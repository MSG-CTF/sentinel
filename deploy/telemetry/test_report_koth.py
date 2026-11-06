import copy
import sys
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import report_koth as reporter


class KothReadinessTests(unittest.TestCase):
    def test_internal_and_external_routes_are_distinct_and_runner_uses_its_actual_agent(self):
        config={'name':'jenga','hostname':'expected','namespace':'koth-jenga','service':'k3s.service','track':'INTERNAL',
            'host':{'endpoint':'https://sla.msgctf.kr/ingest/heartbeat/internal-koth-host-jenga','token':'h'*48},
            'application':{'endpoint':'https://sla.msgctf.kr/ingest/heartbeat/internal-koth-app-jenga','token':'a'*48}}
        with patch.object(reporter.platform,'node',return_value='expected'):
            reporter.validate(config)
            for mutation in [{'track':'KOTH'},{'track':'PUBLIC'},{'hostname':'other'},
                {'host':{**config['host'],'endpoint':'https://sla.msgctf.kr/ingest/heartbeat/koth-host-jenga'}},
                {'application':{**config['application'],'token':'h'*48}}]:
                with self.subTest(mutation=mutation),self.assertRaises(AssertionError):reporter.validate({**config,**mutation})
            runner={**config,'name':'dependency-hell-runner','service':'k3s-agent.service','application':None,
                'host':{'endpoint':'https://sla.msgctf.kr/ingest/heartbeat/internal-koth-host-dependency-hell-runner','token':'r'*48}}
            reporter.validate(runner)
            with self.assertRaises(AssertionError):reporter.validate({**runner,'service':'ssh.service'})

    def test_new_internal_health_contracts_require_the_actual_empty_response_and_jenga_envelope(self):
        pixel=reporter.HEALTH_CHECKS['pixel-smuggler'][0]
        self.assertTrue(reporter.response_matches(pixel,200,'text/html; charset=utf-8',b''))
        for status,media,body in [(500,'text/html',b''),(200,'text/html',b'<html>error</html>'),(200,'application/json',b'{}')]:
            self.assertFalse(reporter.response_matches(pixel,status,media,body))
        jenga=reporter.HEALTH_CHECKS['jenga'][0]
        healthy=b'{"code":"SUCCESS","message":"healthy","data":{"status":"ok","match_id":"test-only"}}'
        self.assertTrue(reporter.response_matches(jenga,200,'application/json',healthy))
        for body in [healthy.replace(b'SUCCESS',b'ERROR'),healthy.replace(b'"ok"',b'"down"'),
            b'{"code":"SUCCESS","message":"healthy","data":{"status":"ok"}}',b'{}']:
            self.assertFalse(reporter.response_matches(jenga,200,'application/json',body))

    def dependency(self):
        items = []; nodes = self.fixtures()[1]
        for name in reporter.DEPENDENCY_WORKLOADS:
            deployment, pod = copy.deepcopy(self.fixtures()[0])
            deployment['metadata'].update(name=name, namespace='dependency-hell')
            deployment['spec']['selector']['matchLabels']={'app':name}
            pod['metadata'].update(namespace='dependency-hell', name=name+'-pod', uid=name+'-uid', labels={'app':name})
            items.extend([deployment,pod])
        return items,nodes

    def test_dependency_binds_all_three_real_workloads_and_frontend_health_only(self):
        items,nodes=self.dependency();check=Mock(return_value=(True,[{'contract_passed':True}]))
        summary,state=reporter.evaluate('dependency-hell','dependency-hell',items,nodes,check)
        self.assertEqual(state,'active');self.assertTrue(summary['ready'])
        self.assertEqual({w['name'] for w in summary['workloads']},set(reporter.DEPENDENCY_WORKLOADS))
        check.assert_called_once_with('dependency-hell','10.244.0.2','10.244.0.0/24')

    def test_dependency_cannot_ignore_a_missing_unready_or_misbound_backend(self):
        for fault in ('missing','unready','namespace','labels','pressure','http','zero_replicas','empty_containers'):
            items,nodes=self.dependency();check=Mock(return_value=(fault!='http',[]))
            if fault=='missing':items=items[:4]
            if fault=='unready':items[5]['status']['containerStatuses'][0]['ready']=False
            if fault=='namespace':items[5]['metadata']['namespace']='koth-dependency-hell'
            if fault=='labels':items[5]['metadata']['labels']={'app':'unrelated'}
            if fault=='pressure':nodes[0]['status']['conditions'][1]['status']='True'
            if fault=='zero_replicas':items[4]['spec']['replicas']=0
            if fault=='empty_containers':items[5]['spec']['containers']=[];items[5]['status']['containerStatuses']=[]
            summary,state=reporter.evaluate('dependency-hell','dependency-hell',items,nodes,check)
            self.assertFalse(summary['ready'],fault);self.assertEqual(state,'failed',fault)

    def test_dependency_does_not_accept_legacy_namespace_or_unrelated_deployments(self):
        items,nodes=self.dependency()
        with self.assertRaises(AssertionError):reporter.evaluate('dependency-hell','koth-dependency-hell',items,nodes)
        summary,state=reporter.evaluate('dependency-hell','dependency-hell',[{'kind':'Deployment','metadata':{'namespace':'other','name':'frontend'}}],nodes)
        self.assertFalse(summary['deployed']);self.assertIsNone(state)

    def test_dependency_health_is_fixed_to_the_documented_non_gameplay_endpoint(self):
        self.assertEqual(reporter.HEALTH_CHECKS['dependency-hell'],[{'port':9090,'path':'/healthz','status':200,'kind':'dependency_health'}])
        check=reporter.HEALTH_CHECKS['dependency-hell'][0]
        healthy=b'{"code":"SUCCESS","message":"healthy","data":null}'
        self.assertTrue(reporter.response_matches(check,200,'application/json',healthy))
        for code,media,body in [(500,'application/json',healthy),(200,'text/html',healthy),
                                (200,'application/json',b'{"code":"ERROR","message":"healthy","data":null}'),
                                (200,'application/json',b'{"code":"SUCCESS","message":"healthy","data":{"private":"value"}}')]:
            self.assertFalse(reporter.response_matches(check,code,media,body))
    def fixtures(self):
        deployment = {'kind': 'Deployment', 'metadata': {'name': 'game', 'namespace': 'koth-jenga', 'uid': 'deployment'},
                      'spec': {'replicas': 1, 'selector': {'matchLabels': {'app': 'game'}}},
                      'status': {'readyReplicas': 1}}
        pod = {'kind': 'Pod', 'metadata': {'name': 'pod', 'namespace': 'koth-jenga', 'uid': 'pod', 'labels': {'app': 'game'}},
               'spec': {'nodeName': 'node', 'containers': [{'name': 'game'}]},
               'status': {'phase': 'Running', 'podIP': '10.244.0.2',
                          'containerStatuses': [{'ready': True, 'state': {'running': {}}, 'restartCount': 0}]}}
        node = {'metadata': {'name': 'node'}, 'spec': {'podCIDR': '10.244.0.0/24'},
                'status': {'conditions': [{'type': 'Ready', 'status': 'True'}] +
                                        [{'type': key, 'status': 'False'} for key in ('MemoryPressure','DiskPressure','PIDPressure')]}}
        return [deployment, pod], [node]

    def test_an_empty_host_never_proves_a_workload(self):
        for items in ([], [{'kind': 'Service', 'metadata': {'name': 'kubernetes'}}]):
            result, state = reporter.evaluate('jenga', 'koth-jenga', items, [])
            self.assertFalse(result['deployed']);self.assertIsNone(state)

    def test_pod_and_node_readiness_and_actual_http_are_all_required(self):
        items, nodes = self.fixtures()
        check = Mock(return_value=(True, [{'contract_passed': True}]))
        result, state = reporter.evaluate('jenga', 'koth-jenga', items, nodes, check)
        self.assertTrue(result['ready']);self.assertEqual(state, 'active')
        check.assert_called_once_with('jenga', '10.244.0.2', '10.244.0.0/24')
        for mutate in ('not-ready','pressure','http-down','not-running'):
            a,b = self.fixtures();checker=Mock(return_value=(mutate!='http-down',[]))
            if mutate=='not-ready':a[1]['status']['containerStatuses'][0]['ready']=False
            if mutate=='pressure':b[0]['status']['conditions'][1]['status']='True'
            if mutate=='not-running':a[1]['status']['phase']='Pending'
            result,state=reporter.evaluate('jenga','koth-jenga',a,b,checker)
            self.assertFalse(result['ready'],mutate);self.assertEqual(state,'failed',mutate)

    def test_pods_in_another_namespace_cannot_satisfy_readiness(self):
        items,nodes=self.fixtures();items[1]['metadata']['namespace']='someone-else'
        result,state=reporter.evaluate('jenga','koth-jenga',items,nodes,Mock())
        self.assertFalse(result['ready']);self.assertEqual(state,'failed')

    def test_missing_container_status_is_not_ready(self):
        items,nodes=self.fixtures();items[1]['status']['containerStatuses']=[]
        result,state=reporter.evaluate('jenga','koth-jenga',items,nodes,Mock())
        self.assertFalse(result['ready']);self.assertEqual(state,'failed')

    def test_http_200_html_does_not_satisfy_json_or_health_contract(self):
        check=reporter.HEALTH_CHECKS['jenga'][0]
        self.assertFalse(reporter.response_matches(check,200,'text/html',b'<html>ok</html>'))
        self.assertFalse(reporter.response_matches(check,200,'application/json',b'{"status":"down"}'))
        self.assertTrue(reporter.response_matches(check,200,'application/json',b'{"status":"ok"}'))
        self.assertFalse(reporter.response_matches(check,302,'application/json',b'{"status":"ok"}'))
        self.assertFalse(reporter.response_matches(check,200,'application/json',b'a'*16385))

    def test_card_health_is_exact_and_swan_auth_boundary_is_json(self):
        card=reporter.HEALTH_CHECKS['card-battle-arena'][0]
        self.assertTrue(reporter.response_matches(card,200,'text/plain',b'ok\n'))
        self.assertFalse(reporter.response_matches(card,200,'text/plain',b'something ok'))
        auth=reporter.HEALTH_CHECKS['swanopoly'][1]
        self.assertTrue(reporter.response_matches(auth,401,'application/json',b'{"code":"unauthorized","message":"login","data":null}'))
        self.assertFalse(reporter.response_matches(auth,200,'application/json',b'{"code":"unauthorized","message":"login","data":null}'))

    def test_http_cannot_probe_unrelated_or_public_addresses(self):
        with patch.object(reporter.http.client,'HTTPConnection') as connection:
            for ip,cidr in [('1.2.3.4','10.244.0.0/24'),('127.0.0.1','127.0.0.0/24'),
                            ('10.244.1.2','10.244.0.0/24'),('10.244.0.2','0.0.0.0/0')]:
                with self.assertRaises(AssertionError):reporter.http_readiness('jenga',ip,cidr)
            connection.assert_not_called()

    def test_query_failure_does_not_fabricate_healthy_game(self):
        config={'name':'jenga','hostname':'expected','namespace':'koth-jenga','service':'k3s.service',
                'host':{'endpoint':'https://sla.msgctf.kr/ingest/heartbeat/koth-host-jenga','token':'h'*48},
                'application':{'endpoint':'https://sla.msgctf.kr/ingest/heartbeat/koth-app-jenga','token':'a'*48}}
        with patch.object(reporter.platform,'node',return_value='expected'), \
             patch.object(reporter.core,'collect',return_value={'service_state':'active'}), \
             patch.object(reporter,'kube',side_effect=RuntimeError('private detail')), \
             patch.object(reporter.core,'send') as send:
            value=reporter.run(config,dry_run=True)
            self.assertEqual(value['application']['reason'],'KOTH_QUERY_UNVERIFIED')
            self.assertNotIn('private detail',str(value));send.assert_not_called()
            config['hostname']='other'
            with self.assertRaises(AssertionError):reporter.run(config,dry_run=True)


if __name__=='__main__':unittest.main()
