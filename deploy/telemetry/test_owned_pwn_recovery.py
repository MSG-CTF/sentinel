import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
import uuid

import owned_pwn_recovery as recovery
import test_owned_challenge_reporter as reporter_fixture


def fixture():
    source = reporter_fixture.ReporterTests(); source.setUp()
    reporter = copy.deepcopy(source.config)
    reporter['binding']['profile'] = 'pwn-random6'
    reporter['service_url'] = 'tcp://8.8.8.8:30080'
    reporter['target_id'] = 'owned-pwn'
    spec = {'type': 'NodePort', 'selector': {'app': 'random6'},
            'ports': [{'port': 6666, 'targetPort': 6666, 'nodePort': 30080, 'protocol': 'TCP'}]}
    config = {'reporter': reporter, 'workload': {'namespace_uid': str(uuid.uuid4()),
        'deployment_name': 'random6', 'deployment_uid': str(uuid.uuid4()), 'deployment_spec_sha256': 'a'*64,
        'service_name': 'random6', 'service_uid': str(uuid.uuid4()), 'service_spec': spec,
        'images': {'random6': 'ghcr.io/msg-ctf/challenges/pwn-random6/random6@sha256:'+'b'*64}}}
    return source.now, config


class StateTests(unittest.TestCase):
    def setUp(self):
        self.now, self.config = fixture()
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name)/'state.sqlite'
        self.journal = recovery.Journal(self.path, epoch='boot1')
        self.addCleanup(self.directory.cleanup); self.addCleanup(self.journal.close)

    def observation(self, status='UP', reason=None):
        b = self.config['reporter']['binding']
        result = {k:v for k,v in b.items() if k != 'expires_at'}
        result.update(scope='owned_deployment_test', status=status, checked_at=self.now.isoformat(), elapsed_ms=1,
                      reason_code=reason or ('VM_NONCE_ARITHMETIC_CONFIRMED' if status=='UP' else 'FUNCTION_TIMEOUT'))
        return result

    def feed(self, status, seconds=15):
        self.now += timedelta(seconds=seconds)
        return self.journal.observe(self.config, self.observation(status), self.now)

    def eligible(self):
        self.assertFalse(self.feed('UP')); self.assertFalse(self.feed('UP'))
        self.assertFalse(self.feed('DOWN')); self.assertTrue(self.feed('DOWN'))

    def test_requires_two_healthy_baselines_and_two_distinct_failures(self):
        self.feed('DOWN'); self.feed('DOWN')
        self.assertFalse(self.journal.claim(self.config,self.now,'RECREATE_OWNED_POD'))
        self.eligible()
        self.assertTrue(self.journal.claim(self.config,self.now,'RECREATE_OWNED_POD'))

    def test_duplicate_out_of_order_and_unknown_cannot_confirm_or_recover(self):
        self.feed('UP')
        same = self.observation()
        self.assertFalse(self.journal.observe(self.config,same,self.now))
        self.assertFalse(self.feed('DOWN'))
        self.feed('CHECKER_ERROR'); self.assertFalse(self.feed('DOWN'))
        self.assertFalse(self.journal.snapshot(self.config)['armed'])

    def test_gap_discards_stale_baseline(self):
        self.feed('UP'); self.feed('UP')
        self.assertFalse(self.feed('DOWN',seconds=46)); self.assertFalse(self.feed('DOWN'))

    def test_single_claim_survives_restart_and_further_incidents(self):
        self.eligible(); self.assertTrue(self.journal.claim(self.config,self.now,'RECREATE_OWNED_POD'))
        restart = recovery.Journal(self.path,epoch='boot2'); self.addCleanup(restart.close)
        self.assertFalse(restart.claim(self.config,self.now,'RECREATE_OWNED_POD'))
        self.journal.result(self.config,'INDETERMINATE')
        self.feed('UP'); self.feed('UP')
        self.assertEqual(self.journal.snapshot(self.config)['status'],'SUCCEEDED')
        self.feed('DOWN'); self.feed('DOWN')
        self.assertFalse(self.journal.claim(self.config,self.now,'RECREATE_OWNED_POD'))
        self.assertEqual(self.journal.snapshot(self.config)['attempts'],1)

    def test_configuration_change_cannot_reset_consumed_budget(self):
        self.eligible(); self.journal.claim(self.config,self.now,'RESTORE_SERVICE_PORT')
        changed=copy.deepcopy(self.config); changed['reporter']['target_id']='another-target'
        with self.assertRaises(ValueError):self.journal.snapshot(changed)

    def test_old_or_near_expiry_measurement_prevents_claim(self):
        self.eligible()
        self.now += timedelta(seconds=31)
        self.assertFalse(self.journal.claim(self.config,self.now,'RESTORE_SERVICE_PORT'))
        self.config['reporter']['binding']['expires_at']=(self.now+timedelta(seconds=59)).isoformat()
        fresh=recovery.Journal(':memory:'); self.addCleanup(fresh.close)
        self.assertFalse(fresh.observe(self.config,self.observation(),self.now))

    def test_one_success_does_not_verify_and_deadline_failure_stays_failed(self):
        self.eligible(); self.journal.claim(self.config,self.now,'RECREATE_OWNED_POD')
        self.journal.result(self.config,'VERIFYING')
        self.feed('UP'); self.assertEqual(self.journal.snapshot(self.config)['status'],'VERIFYING')
        self.feed('UP'); self.assertEqual(self.journal.snapshot(self.config)['status'],'SUCCEEDED')

    def test_late_success_does_not_claim_repair_verified_within_deadline(self):
        self.eligible(); self.journal.claim(self.config,self.now,'RECREATE_OWNED_POD')
        self.feed('DOWN',seconds=181)
        self.assertEqual(self.journal.snapshot(self.config)['status'],'FAILED')

    def test_unknown_after_verification_deadline_marks_failure(self):
        self.eligible();self.journal.claim(self.config,self.now,'RECREATE_OWNED_POD')
        self.feed('CHECKER_ERROR',seconds=181)
        self.assertEqual(self.journal.snapshot(self.config)['status'],'FAILED')
        self.feed('UP'); self.feed('UP')
        self.assertEqual(self.journal.snapshot(self.config)['status'],'FAILED')

    def test_submission_failure_disarms_before_any_mutation(self):
        self.eligible(); adapter=Mock()
        with self.assertRaises(ValueError):
            recovery.tick(self.config,self.journal,observe=lambda r:self.observation('DOWN'),
                          publish=Mock(side_effect=ValueError()),adapter=adapter,clock=lambda:self.now)
        adapter.assert_not_called();self.assertFalse(self.journal.snapshot(self.config)['armed'])

    def test_read_only_journal_cannot_start_a_mutation(self):
        self.eligible();adapter=Mock();self.now+=timedelta(seconds=1)
        self.journal.db.execute('PRAGMA query_only=ON')
        with self.assertRaises(Exception):
            recovery.tick(self.config,self.journal,observe=lambda r:self.observation('DOWN'),
                publish=lambda *a,**kw:{'accepted':True},adapter=adapter,clock=lambda:self.now)
        adapter.assert_not_called()

    def test_execution_timeout_is_not_retried(self):
        self.eligible()
        def observe(r):
            self.now += timedelta(seconds=1)
            return self.observation('DOWN')
        kube=Mock();kube.plan.return_value={'action':'RECREATE_OWNED_POD'};kube.execute.side_effect=TimeoutError()
        result=recovery.tick(self.config,self.journal,observe=observe,publish=lambda *a,**kw:{'accepted':True},
                             adapter=lambda c:kube,clock=lambda:self.now)
        self.assertEqual(result['recovery_status'],'INDETERMINATE')
        recovery.tick(self.config,self.journal,observe=observe,publish=lambda *a,**kw:{'accepted':True},
                      adapter=lambda c:kube,clock=lambda:self.now)
        self.assertEqual(kube.execute.call_count,1)

    def test_control_ownership_change_at_execution_blocks_action(self):
        self.eligible()
        def observe(r):
            self.now += timedelta(seconds=1)
            return self.observation('DOWN')
        kube=Mock();kube.plan.return_value={'action':'RECREATE_OWNED_POD'}
        kube.execute.side_effect=lambda plan,guard:recovery.require(guard() is None)
        result=recovery.tick(self.config,self.journal,observe=observe,publish=lambda *a,**kw:{'accepted':True},
            adapter=lambda c:kube,clock=lambda:self.now,read=Mock(return_value={'data':{}}))
        self.assertEqual(result['recovery_status'],'INDETERMINATE')

    def test_new_process_requires_new_healthy_baselines(self):
        self.feed('UP');self.feed('UP')
        restarted=recovery.Journal(self.path,epoch='boot2');self.addCleanup(restarted.close)
        self.now += timedelta(seconds=15)
        self.assertFalse(restarted.observe(self.config,self.observation('DOWN'),self.now))

    def test_global_claim_serializes_different_instances(self):
        self.eligible();first=self.config
        self.assertTrue(self.journal.claim(first,self.now,'RECREATE_OWNED_POD'))
        second=copy.deepcopy(first);second['reporter']['binding']['instance_id']=str(uuid.uuid4())
        self.config=second;self.eligible()
        self.assertFalse(self.journal.claim(second,self.now,'RECREATE_OWNED_POD'))


class KubernetesTests(unittest.TestCase):
    def setUp(self):
        self.at,self.config=fixture();self.w=self.config['workload'];self.b=self.config['reporter']['binding']
        self.namespace='ctf-'+uuid.UUID(self.b['instance_id']).hex
        def meta(resource,uid=None,namespaced=True):
            return {'name':resource,'uid':uid or str(uuid.uuid4()),'resourceVersion':'9',
                **({'namespace':self.namespace} if namespaced else {}),'labels':{
                'msgctf.io/instance-id':self.b['instance_id'],'msgctf.io/team-id':self.b['owner_team_id'],
                'app.kubernetes.io/managed-by':'secure-provisioner'}}
        pod_spec={'containers':[{'name':k,'image':v} for k,v in self.w['images'].items()],'nodeName':'owned-node'}
        deployment_spec={'replicas':1,'template':{'spec':pod_spec}}
        self.w['deployment_spec_sha256']=recovery.digest(deployment_spec)
        self.objects={
            'namespace':{'metadata':meta(self.namespace,self.w['namespace_uid'],False)},
            'deployment':{'metadata':meta('random6',self.w['deployment_uid']),'spec':deployment_spec},
            'service':{'metadata':meta('random6',self.w['service_uid']),'spec':copy.deepcopy(self.w['service_spec'])},
            'pods':{'items':[{'metadata':meta('random6-abc-pod'),'spec':pod_spec}]},
            'replicaset':{'metadata':meta('random6-abc')},
            'node':{'status':{'conditions':[{'type':k,'status':v} for k,v in
                {'Ready':'True','MemoryPressure':'False','DiskPressure':'False','PIDPressure':'False'}.items()]}}}
        self.objects['replicaset']['metadata']['ownerReferences']=[{'kind':'Deployment','uid':self.w['deployment_uid'],'controller':True}]
        self.objects['pods']['items'][0]['metadata']['ownerReferences']=[{'kind':'ReplicaSet','name':'random6-abc',
            'uid':self.objects['replicaset']['metadata']['uid'],'controller':True}]
        self.calls=[]
        def run(args,body=None):
            self.calls.append((args,body))
            if args[:2]==['get','pod']:return copy.deepcopy(self.objects['pods']['items'][0])
            return copy.deepcopy(self.objects[args[1]]) if args[0]=='get' else {'kind':'Status'}
        self.kube=recovery.Kubernetes(self.config,registry={'clusters':[{'target_id':'owned-cluster','enabled':True,
            'kubeconfig_path':'/etc/secure-provisioner/kubeconfigs/owned.yaml'}]},run=run)

    def test_routes_only_expected_target_port_with_uid_and_version_tests(self):
        self.objects['service']['spec']['ports'][0]['targetPort']=6667
        plan=self.kube.plan();self.assertEqual(plan['action'],'RESTORE_SERVICE_PORT')
        self.kube.execute(plan,lambda:None)
        args,body=self.calls[-1];self.assertEqual(args[:3],['patch','service','random6'])
        patch=json.loads(args[args.index('-p')+1])
        self.assertEqual([p['op'] for p in patch],['test','test','test','replace'])
        self.assertEqual(patch[-1]['path'],'/spec/ports/0/targetPort');self.assertEqual(patch[-1]['value'],6666)

    def test_recreates_only_owned_pod_with_uid_and_version_preconditions(self):
        plan=self.kube.plan();self.assertEqual(plan['action'],'RECREATE_OWNED_POD')
        self.kube.execute(plan,lambda:None)
        args,body=self.calls[-1];meta=self.objects['pods']['items'][0]['metadata']
        self.assertEqual(args,['delete','--raw','/api/v1/namespaces/'+self.namespace+'/pods/'+meta['name'],'-f','-'])
        self.assertEqual(body['preconditions'],{'uid':meta['uid'],'resourceVersion':meta['resourceVersion']})

    def test_foreign_replaced_or_modified_resources_never_produce_plan(self):
        mutations=[lambda o:o['namespace']['metadata'].update(uid=str(uuid.uuid4())),
            lambda o:o['deployment']['metadata']['labels'].update({'msgctf.io/team-id':str(uuid.uuid4())}),
            lambda o:o['deployment']['spec'].update(replicas=2),
            lambda o:o['service']['metadata'].update(uid=str(uuid.uuid4())),
            lambda o:o['service']['spec'].update(selector={'app':'foreign'}),
            lambda o:o['pods']['items'][0]['metadata'].update(deletionTimestamp='now'),
            lambda o:o['replicaset']['metadata']['ownerReferences'][0].update(uid=str(uuid.uuid4())),
            lambda o:o['node']['status']['conditions'][0].update(status='False')]
        for mutate in mutations:
            before=copy.deepcopy(self.objects);mutate(self.objects)
            with self.subTest(mutate=mutate),self.assertRaises(ValueError):self.kube.plan()
            self.objects=before
        self.assertFalse(any(args[0]!='get' for args,body in self.calls))

    def test_last_control_guard_failure_never_sends_kubernetes_mutation(self):
        plan=self.kube.plan();count=len(self.calls)
        with self.assertRaises(ValueError):self.kube.execute(plan,lambda:'OWNERSHIP_BINDING_REJECTED')
        self.assertEqual(len(self.calls),count)

    def test_replaced_pod_after_confirmation_cannot_be_deleted(self):
        plan=self.kube.plan();self.objects['pods']['items'][0]['metadata']['uid']=str(uuid.uuid4())
        with self.assertRaises(ValueError):self.kube.execute(plan,lambda:None)
        self.assertFalse(any(args[0]!='get' for args,body in self.calls))

    def test_web_tag_images_and_ambiguous_service_are_rejected(self):
        for mutate in [lambda c:c['reporter']['binding'].update(profile='web-notebook'),
                       lambda c:c['workload']['images'].update(random6='image:latest'),
                       lambda c:c['workload']['service_spec']['ports'].append({'port':6667})]:
            invalid=copy.deepcopy(self.config);mutate(invalid)
            with self.assertRaises(ValueError):recovery.validate(invalid)


class DegradedTests(unittest.TestCase):
    def setUp(self):
        self.source=reporter_fixture.ReporterTests();self.source.setUp()
        self.source.config['binding']['profile']='pwn-random6'
        self.source.config['service_url']='tcp://8.8.8.8:30080'
        self.source.row['endpoints']=[{'service_url':self.source.config['service_url']}]

    def test_verified_degraded_can_establish_real_failure_but_never_recovery(self):
        s=self.source;s.runtime['phase']='DEGRADED'
        s.probe.return_value={'status':'DOWN','reason_code':'FUNCTION_TIMEOUT','elapsed_ms':1}
        self.assertEqual(recovery.observe_owned(s.config,s.read,s.probe,lambda:s.now)['status'],'DOWN')
        s.probe.return_value={'status':'UP','reason_code':'VM_NONCE_ARITHMETIC_CONFIRMED','elapsed_ms':1}
        self.assertEqual(recovery.observe_owned(s.config,s.read,s.probe,lambda:s.now)['status'],'CHECKER_ERROR')
        self.assertEqual(recovery.reporter.observe(s.config,s.read,s.probe,lambda:s.now)['status'],'CHECKER_ERROR')

    def test_unknown_provisioning_deleting_and_stale_degraded_cannot_probe(self):
        s=self.source
        for phase in ['UNKNOWN','PROVISIONING','TERMINATING','TERMINATED']:
            s.runtime['phase']=phase;s.probe.reset_mock()
            self.assertEqual(recovery.observe_owned(s.config,s.read,s.probe,lambda:s.now)['status'],'CHECKER_ERROR')
            s.probe.assert_not_called()
        s.runtime['phase']='DEGRADED';s.runtime['observed_at']=(s.now-timedelta(seconds=61)).isoformat()
        self.assertEqual(recovery.observe_owned(s.config,s.read,s.probe,lambda:s.now)['status'],'CHECKER_ERROR')
        s.probe.assert_not_called()


if __name__=='__main__':unittest.main()
