import copy
from datetime import datetime,timezone
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import Mock,patch
import uuid

import report_broker_fleet as source
import report_fleet as observer


def resource(**changes):
    return {'resource_target_id':str(uuid.uuid4()),'enabled':True,'retired_at':None,'account_enabled':True,
        'provider_instance_state':'RUNNING','instance_name':'node-one','runtime_type':'KUBERNETES',
        'provider_scope_id':'project','target_id':str(uuid.uuid4()),'public_ip':'34.135.180.218','private_ip':'10.0.0.2',**changes}


class BrokerFleetSourceTests(unittest.TestCase):
    def test_current_registration_flags_not_a_fixed_count_control_discovery(self):
        node=resource();windows=resource(runtime_type=None,target_id=None)
        connections={windows['resource_target_id']:{'enabled':True,'port':3389,'address_source':'public_ip'}}
        value=source.build_roster([node,windows],connections,{})
        self.assertTrue(value['complete']);self.assertEqual(len(value['nodes']),1);self.assertEqual(len(value['windows']),1)
        for changed in ({'enabled':False},{'retired_at':'retired'},{'account_enabled':False},{'provider_instance_state':'TERMINATED'}):
            with self.subTest(changed=changed):
                self.assertEqual(source.build_roster([{**node,**changed}],{}, {})['nodes'],[])
        connections[windows['resource_target_id']]['enabled']=False
        self.assertEqual(source.build_roster([windows],connections,{})['windows'],[])
        self.assertEqual(source.build_roster([],{}, {})['nodes'],[])

    def test_unbound_new_nodes_remain_visible_and_private_rdp_is_explicit(self):
        node=resource(target_id=None,public_ip=None,private_ip=None)
        value=source.build_roster([node],{}, {})['nodes'][0]
        self.assertIsNone(value['node_uid']);self.assertIsNone(value['server_url'])
        windows=resource(runtime_type=None)
        connection={'enabled':True,'port':3390,'address_source':'private_ip'}
        value=source.build_roster([windows],{windows['resource_target_id']:connection},{})['windows'][0]
        self.assertEqual(value['host'],'10.0.0.2');self.assertEqual(value['port'],3390)

    def test_ambiguous_invalid_or_oversized_input_cannot_be_published_as_a_partial_roster(self):
        node=resource();other=resource(target_id=node['target_id'])
        for rows in ([node,node],[node,other],[resource() for _ in range(65)]):
            with self.assertRaises(ValueError):source.build_roster(rows,{}, {})
        windows=resource(runtime_type=None)
        for host in ('127.0.0.1','169.254.169.254','0.0.0.0','224.0.0.1'):
            with self.assertRaises(ValueError):
                source.build_roster([{**windows,'public_ip':host}],{windows['resource_target_id']:{'enabled':True,'port':3389,'address_source':'public_ip'}},{})

    def test_only_allowlisted_metadata_is_returned(self):
        node=resource(secret='never-export',username='never-export')
        value=source.build_roster([node],{}, {node['resource_target_id']:{'server_url':'https://34.135.180.218:6443','kubeconfig_base64':'never-export'}})
        self.assertNotIn('never-export',json.dumps(value))
        with patch.object(source.subprocess,'run',return_value=Mock(returncode=1,stdout=b'{"complete":true}')):
            with self.assertRaises(RuntimeError):source.collect_container()


class FleetObserverTests(unittest.TestCase):
    def desired(self):
        node=resource()
        roster=source.build_roster([node],{}, {})
        return {**roster,'roster_version':'a'*64,'storage_healthy':True}

    def test_changed_roster_or_metadata_outage_never_reuses_an_old_registered_list(self):
        good=self.desired();self.assertEqual(observer.validate_desired(good),good)
        for changed in ({'complete':False},{'storage_healthy':False},{'checked_at':'2020-01-01T00:00:00+00:00'},
                        {'roster_version':None},{'nodes':[good['nodes'][0],good['nodes'][0]]}):
            with self.subTest(changed=changed),self.assertRaises(ValueError):observer.validate_desired({**good,**changed})
        for endpoint in ('http://10.0.0.2:6443','https://127.0.0.1:6443','https://169.254.169.254:6443','https://10.0.0.2:443','https://10.0.0.2:6443/?token=x'):
            invalid=copy.deepcopy(good);invalid['nodes'][0]['server_url']=endpoint
            with self.assertRaises(ValueError):observer.validate_desired(invalid)

    def test_absent_credentials_and_unbound_nodes_are_explicit_unknown_reports(self):
        member=self.desired()['nodes'][0]
        with patch.object(observer,'request',side_effect=ValueError('upstream unavailable')):
            value=observer.node_report(member,{'broker_runtime_token':'x'*48})
        self.assertEqual(value['resource_target_id'],member['resource_target_id'])
        self.assertEqual(value['node']['query_state'],'query_failed');self.assertEqual(value['node']['node_count'],0)
        with patch.object(observer,'request') as fetch:
            value=observer.node_report({**member,'node_uid':None,'server_url':None},{})
            fetch.assert_not_called();self.assertEqual(value['node']['query_state'],'not_registered')

    def test_real_tcp_probe_is_payload_free_and_failures_do_not_become_healthy(self):
        member={'resource_target_id':str(uuid.uuid4()),'host':'10.0.0.1','port':3389}
        with patch.object(observer.socket,'create_connection',side_effect=socket.timeout()):
            self.assertFalse(observer.windows_report(member)['windows']['reachable'])
        connection=Mock();connection.__enter__=Mock(return_value=connection);connection.__exit__=Mock(return_value=False)
        with patch.object(observer.socket,'create_connection',return_value=connection) as connect:
            self.assertTrue(observer.windows_report(member)['windows']['reachable'])
            connect.assert_called_once_with(('10.0.0.1',3389),timeout=4);connection.send.assert_not_called()

    def test_roster_race_returns_retry_without_resubmitting_or_registering_targets(self):
        desired=self.desired();config={'observer_token':'o'*48,'broker_runtime_token':'b'*48}
        report={'resource_target_id':desired['nodes'][0]['resource_target_id'],'node':{}}
        with patch.object(observer,'request',side_effect=[(200,desired),(409,{'code':'ROSTER_CHANGED'})]) as request,patch.object(observer,'node_report',return_value=report):
            result=observer.run(config)
        self.assertFalse(result['accepted']);self.assertTrue(result['retry_next_cycle']);self.assertEqual(request.call_count,2)

    def test_kubeconfig_plugins_and_identity_changes_are_rejected_before_node_access(self):
        member=self.desired()['nodes'][0]
        credential={'schema_version':1,'resource_target_id':member['resource_target_id'],'target_id':member['node_uid'],
            'server_url':member['server_url'],'kubeconfig_base64':'eA=='}
        bad={'clusters':[{'name':'x','cluster':{'server':member['server_url'],'insecure-skip-tls-verify':True}}],
            'users':[{'name':'x','user':{'exec':{'command':'evil'}}}],
            'contexts':[{'name':'x','context':{'cluster':'x','user':'x'}}],'current-context':'x'}
        with patch.object(observer,'command',return_value=json.dumps(bad)) as command:
            with self.assertRaises(ValueError):observer.verified_kubeconfig(credential,member)
            self.assertEqual(command.call_count,1)
        with patch.object(observer,'command') as command:
            with self.assertRaises(ValueError):observer.verified_kubeconfig({**credential,'target_id':str(uuid.uuid4())},member)
            command.assert_not_called()
        with self.assertRaises(ValueError):observer.unique_json('{"key":1,"key":2}')


if __name__=='__main__':unittest.main()
