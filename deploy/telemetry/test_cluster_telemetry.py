import copy
import datetime
import json
import subprocess
import unittest
from unittest.mock import Mock, patch

import report_clusters as reporter

NOW = datetime.datetime(2026, 10, 18, 14, 0, tzinfo=datetime.timezone.utc)
BINDING = {'endpoint': 'https://sla.msgctf.kr/ingest/cluster/runtime-cluster',
           'cluster_target_id': 'cluster-a', 'token': 't' * 48}
CLUSTER = {'target_id': 'cluster-a', 'enabled': True,
           'kubeconfig_path': '/etc/secure-provisioner/kubeconfigs/cluster-a.json'}


def node():
    return {'metadata': {'name': 'node-one'}, 'spec': {}, 'status': {
        'conditions': [{'type': name, 'status': 'True' if name == 'Ready' else 'False'}
                       for name in ['Ready', 'MemoryPressure', 'DiskPressure', 'PIDPressure']],
        'capacity': {'cpu': '2', 'memory': '4Gi'},
        'nodeInfo': {'osImage': 'Debian GNU/Linux 12', 'architecture': 'amd64'},
    }}


def metric():
    return {'metadata': {'name': 'node-one'}, 'timestamp': NOW.isoformat(),
            'usage': {'cpu': '250000000n', 'memory': '1Gi'}}


class ClusterTelemetryTests(unittest.TestCase):
    def test_one_failed_delivery_does_not_prevent_the_other_cluster_report(self):
        first = (BINDING, {'query_state': 'ok'})
        second = ({**BINDING, 'endpoint': 'https://sla.msgctf.kr/ingest/cluster/test-cluster'}, {'query_state': 'ok'})
        with patch.object(reporter, 'collect_reports', return_value=[first, second]), patch.object(
                reporter, 'send', side_effect=[RuntimeError('private response'), None]) as send:
            with self.assertRaisesRegex(RuntimeError, '^One or more cluster reports were not accepted$'):
                reporter.report([], {})
            self.assertEqual(send.call_count, 2)

    def collect(self, nodes=None, metrics=None):
        nodes = [node()] if nodes is None else nodes
        metrics = [metric()] if metrics is None else metrics
        with patch.object(reporter, 'run_json', side_effect=[{'items': nodes}, {'items': metrics}]):
            return reporter.collect_reports([BINDING], {'clusters': [CLUSTER]}, NOW)[0][1]

    def test_resources_use_verified_node_identity_and_kubernetes_units(self):
        observed = self.collect()
        self.assertEqual(observed['node_count'], 1)
        self.assertEqual(observed['ready_node_count'], 1)
        self.assertEqual(observed['cpu_used_percent'], 12.5)
        self.assertEqual(observed['memory_used_percent'], 25)
        self.assertEqual(observed['memory_total_bytes'], 4 * 1024 ** 3)
        self.assertNotIn('disk_used_percent', observed)
        self.assertNotIn('gateway_state', observed)

    def test_missing_stale_and_wrong_node_metrics_never_become_zero(self):
        stale = metric(); stale['timestamp'] = (NOW - datetime.timedelta(seconds=61)).isoformat()
        wrong = metric(); wrong['metadata']['name'] = 'other-node'
        future = metric(); future['timestamp'] = (NOW + datetime.timedelta(seconds=31)).isoformat()
        too_large = metric(); too_large['usage']['memory'] = '100Gi'
        for metrics in [[], [stale], [wrong], [future], [too_large]]:
            with self.subTest(metrics=metrics):
                result = self.collect(metrics=metrics)
                self.assertEqual(result['query_state'], 'ok')
                self.assertNotIn('cpu_used_percent', result)
                self.assertNotIn('memory_used_bytes', result)

    def test_node_failures_pressure_unknown_and_cordon_remain_distinct(self):
        for condition, status, field in [('Ready', 'False', 'ready_node_count'),
                                         ('Ready', 'Unknown', 'unknown_condition_node_count'),
                                         ('MemoryPressure', 'True', 'memory_pressure_node_count'),
                                         ('DiskPressure', 'True', 'disk_pressure_node_count'),
                                         ('PIDPressure', 'True', 'pid_pressure_node_count')]:
            changed = node()
            next(item for item in changed['status']['conditions'] if item['type'] == condition)['status'] = status
            observed = self.collect(nodes=[changed])
            self.assertEqual(observed[field], 0 if status == 'False' else 1)
        changed = node(); changed['spec']['unschedulable'] = True
        observed = self.collect(nodes=[changed])
        self.assertEqual(observed['ready_node_count'], 1)
        self.assertEqual(observed['schedulable_node_count'], 0)
        changed = node(); changed['status']['conditions'].pop()
        self.assertEqual(self.collect(nodes=[changed])['unknown_condition_node_count'], 1)

    def test_cluster_absence_or_query_failure_cannot_reuse_previous_nodes(self):
        for registry, state in [({'clusters': []}, 'not_registered'),
                                ({'clusters': [{**CLUSTER, 'enabled': False}]}, 'disabled')]:
            with patch.object(reporter, 'run_json') as query:
                result = reporter.collect_reports([BINDING], registry, NOW)[0][1]
                self.assertEqual(result['query_state'], state)
                self.assertEqual(result['node_count'], 0)
                query.assert_not_called()
        with patch.object(reporter, 'run_json', side_effect=subprocess.TimeoutExpired('kubectl', 9)):
            result = reporter.collect_reports([BINDING], {'clusters': [CLUSTER]}, NOW)[0][1]
            self.assertEqual(result['query_state'], 'query_failed')
            self.assertEqual(result['node_count'], 0)

    def test_ambiguous_registries_and_duplicate_bindings_are_rejected(self):
        with self.assertRaises(ValueError):
            reporter.collect_reports([BINDING], {'clusters': [CLUSTER, CLUSTER]}, NOW)
        with self.assertRaises(ValueError):
            reporter.collect_reports([BINDING, BINDING], {'clusters': [CLUSTER]}, NOW)
        duplicate = node()
        self.assertEqual(self.collect(nodes=[duplicate, duplicate])['query_state'], 'query_failed')

    def test_only_get_commands_and_fixed_config_paths_are_used(self):
        with patch.object(reporter.subprocess, 'run', return_value=Mock(stdout='{"items":[]}')) as command:
            reporter.observe(CLUSTER, NOW)
            args = command.call_args.args[0]
            self.assertEqual(args[0], reporter.KUBECTL)
            self.assertEqual(args[-4:], ['get', 'nodes', '-o', 'json'])
            self.assertNotIn('shell', command.call_args.kwargs)
        for path in ['/tmp/untrusted.yaml', '../kubeconfig', '/etc/secure-provisioner/kubeconfigs/../../bad.json']:
            with patch.object(reporter, 'run_json') as query, self.assertRaises(ValueError):
                reporter.observe({**CLUSTER, 'kubeconfig_path': path}, NOW)
                query.assert_not_called()
        with self.assertRaises(ValueError):
            reporter.collect_reports([{**BINDING, 'endpoint': 'https://example.com/ingest/cluster/runtime-cluster'}], {'clusters': []}, NOW)

    def test_multiple_nodes_have_no_single_node_resources(self):
        second = copy.deepcopy(node()); second['metadata']['name'] = 'node-two'
        observed = self.collect(nodes=[node(), second])
        self.assertEqual(observed['node_count'], 2)
        self.assertNotIn('hostname', observed)
        self.assertNotIn('cpu_used_percent', observed)


if __name__ == '__main__':
    unittest.main()
