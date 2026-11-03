import copy
import json
import unittest
from unittest.mock import patch
import report_windows as reporter


class WindowsReadinessTests(unittest.TestCase):
    ID='11111111-1111-4111-8111-111111111111'

    def config(self):
        return {'expected_target_ids':[self.ID], 'runtime_token':'r'*48, 'ingest_token':'i'*48}

    def inventory(self):
        return {'items':[{'resource_target_id':self.ID,'host':'10.128.0.3','port':3389,'protocol':'rdp',
                          'username':'private-user','rdp_file':'private-profile','executor_certificate_pem':'private-certificate'}],
                'next_cursor':None}

    def test_parsed_inventory_preserves_identity_without_exporting_connection_material(self):
        targets=reporter.parse_inventory(self.inventory(),[self.ID])
        self.assertEqual(set(targets[0]),{'resource_target_id','host','port'})
        self.assertNotIn('private-',json.dumps(targets))

    def test_partial_duplicate_missing_or_unexpected_targets_cannot_claim_readiness(self):
        for fault in ('cursor','missing','extra','other','duplicate','loopback','protocol','port'):
            value=self.inventory()
            if fault=='cursor':value['next_cursor']='next'
            if fault=='missing':value['items']=[]
            if fault in ('extra','duplicate'):value['items'].append(copy.deepcopy(value['items'][0]))
            if fault=='other':value['items'][0]['resource_target_id']='22222222-2222-4222-8222-222222222222'
            if fault=='loopback':value['items'][0]['host']='127.0.0.1'
            if fault=='protocol':value['items'][0]['protocol']='ssh'
            if fault=='port':value['items'][0]['port']=True
            with self.assertRaises((ValueError,KeyError)):reporter.parse_inventory(value,[self.ID])

    def test_only_tcp_connection_is_used_and_no_host_metrics_are_invented(self):
        target=reporter.parse_inventory(self.inventory(),[self.ID])
        with patch.object(reporter,'inventory',return_value=target),patch.object(reporter.socket,'create_connection') as connect:
            report,results=reporter.collect(self.config())
        connect.assert_called_once_with(('10.128.0.3',3389),timeout=4)
        self.assertEqual(report['service_state'],'active');self.assertEqual(report['connection_ready_count'],1)
        self.assertTrue(results[0]['listener_reachable'])
        for name in ('restart_count','memory_used_percent','disk_used_percent','load_one_minute'):self.assertIsNone(report[name])
        self.assertNotIn('cpu_used_percent',report)

    def test_unreachable_listener_reports_failed_instead_of_normal(self):
        target=reporter.parse_inventory(self.inventory(),[self.ID])
        with patch.object(reporter,'inventory',return_value=target),patch.object(reporter.socket,'create_connection',side_effect=OSError):
            report,_=reporter.collect(self.config())
        self.assertEqual(report['service_state'],'failed');self.assertEqual(report['connection_ready_count'],0)

    def test_inventory_failure_never_emits_a_fabricated_guest_outage_or_healthy_report(self):
        with patch.object(reporter,'inventory',side_effect=ValueError),patch.object(reporter,'send') as send:
            with self.assertRaises(ValueError):reporter.run(self.config())
            send.assert_not_called()

    def test_duplicate_bound_ids_are_rejected_before_network_access(self):
        config=self.config();config['expected_target_ids']*=2
        with self.assertRaises(ValueError):reporter.validate(config)

    def test_report_requires_the_real_ingest_200_accepted_contract_without_redirects(self):
        with patch.object(reporter.http.client,'HTTPSConnection') as connection:
            response=connection.return_value.getresponse.return_value
            response.status=200;response.read.return_value=b'{"accepted":true}'
            reporter.send(self.config(),{'service_state':'active'})
            connection.assert_called_with('sla.msgctf.kr',timeout=10)
            self.assertEqual(connection.return_value.request.call_args.args[:2],('POST','/ingest/heartbeat/windows'))
            for code,body in [(302,b'{"accepted":true}'),(200,b'{"accepted":false}'),(200,b'x'*8193)]:
                response.status=code;response.read.return_value=body
                with self.assertRaises(ValueError):reporter.send(self.config(),{})
