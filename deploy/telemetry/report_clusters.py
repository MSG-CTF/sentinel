#!/usr/bin/env python3
"""Report explicitly bound Kubernetes nodes using GET-only kubectl operations."""
import datetime
from decimal import Decimal
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
from urllib.parse import urlsplit

from report_service import send

REGISTRY = Path('/etc/secure-provisioner/registry.json')
KUBECTL = '/usr/local/bin/kubectl'
COUNTERS = ('node_count', 'ready_node_count', 'schedulable_node_count', 'memory_pressure_node_count',
            'disk_pressure_node_count', 'pid_pressure_node_count', 'unknown_condition_node_count')


def run_json(config_path, args):
    result = subprocess.run([KUBECTL, '--cache-dir=/run/sentinel-cluster-report/cache',
                             '--kubeconfig=' + config_path, '--request-timeout=6s', 'get'] + args,
                            capture_output=True, text=True, check=True, timeout=9)
    if len(result.stdout) > 2 * 1024 * 1024:
        raise ValueError('Kubernetes response exceeds the inspection limit')
    return json.loads(result.stdout)


def quantity(text):
    match = re.fullmatch(r'([0-9]+(?:\.[0-9]+)?)(n|u|m|Ki|Mi|Gi|Ti|Pi|Ei|k|M|G|T|P|E)?', str(text))
    if not match:
        raise ValueError('Unsupported resource quantity')
    scales = {'n': Decimal('1e-9'), 'u': Decimal('1e-6'), 'm': Decimal('1e-3'),
              **{unit + 'i': Decimal(1024) ** power for power, unit in enumerate('KMGTPE', 1)},
              **{unit: Decimal(1000) ** power for power, unit in enumerate('kMGTPE', 1)}}
    return Decimal(match[1]) * scales.get(match[2], 1)


def empty(target, state):
    return {'cluster_target_id': target, 'query_state': state, **dict.fromkeys(COUNTERS, 0)}


def observe(cluster, now):
    target = cluster['target_id']
    config_path = PurePosixPath(cluster['kubeconfig_path'])
    if (not config_path.is_absolute() or config_path.parent != PurePosixPath('/etc/secure-provisioner/kubeconfigs')
            or config_path.suffix not in {'.yaml', '.yml', '.json'}):
        raise ValueError('Kubeconfig is outside the fixed configuration directory')
    document = run_json(str(config_path), ['nodes', '-o', 'json'])
    nodes = document['items']
    if not isinstance(nodes, list) or len(nodes) > 100 or len({node['metadata']['name'] for node in nodes}) != len(nodes):
        raise ValueError('Invalid node inventory')
    observation = empty(target, 'ok')
    observation['node_count'] = len(nodes)
    for node in nodes:
        conditions = node['status']['conditions']
        if not isinstance(conditions, list) or len({c['type'] for c in conditions}) != len(conditions):
            raise ValueError('Ambiguous node conditions')
        states = {item['type']: item['status'] for item in conditions}
        required = ('Ready', 'MemoryPressure', 'DiskPressure', 'PIDPressure')
        if any(states.get(name) not in {'True', 'False'} for name in required):
            observation['unknown_condition_node_count'] += 1
        ready = states.get('Ready') == 'True'
        unschedulable = node.get('spec', {}).get('unschedulable', False)
        if type(unschedulable) is not bool:
            raise ValueError('Invalid node scheduling flag')
        observation['ready_node_count'] += int(ready)
        observation['schedulable_node_count'] += int(ready and not unschedulable)
        for condition, counter in [('MemoryPressure', 'memory_pressure_node_count'),
                                   ('DiskPressure', 'disk_pressure_node_count'), ('PIDPressure', 'pid_pressure_node_count')]:
            observation[counter] += int(states.get(condition) == 'True')
    if len(nodes) != 1:
        return observation
    node = nodes[0]
    info = node['status'].get('nodeInfo', {})
    for field, value, maximum in [('hostname', node['metadata']['name'], 128),
                                  ('os_name', info.get('osImage'), 160), ('architecture', info.get('architecture'), 32)]:
        if isinstance(value, str) and 0 < len(value) <= maximum and all(ord(char) >= 32 and ord(char) != 127 for char in value):
            observation[field] = value
    capacity = node['status'].get('capacity', {})
    cpu = quantity(capacity['cpu'])
    memory = quantity(capacity['memory'])
    if cpu != int(cpu) or not 1 <= cpu <= 65536 or memory != int(memory) or not 0 < memory <= 9007199254740991:
        raise ValueError('Invalid node capacity')
    observation.update(cpu_logical_count=int(cpu), memory_total_bytes=int(memory))
    try:
        metrics = run_json(str(config_path), ['--raw', '/apis/metrics.k8s.io/v1beta1/nodes'])
        items = metrics['items']
        if not isinstance(items, list) or len(items) != 1 or items[0]['metadata']['name'] != node['metadata']['name']:
            raise ValueError('Metric identity differs from the node')
        metric = items[0]
        at = datetime.datetime.fromisoformat(metric['timestamp'].replace('Z', '+00:00'))
        if at.tzinfo is None or not -30 <= (now - at).total_seconds() <= 60:
            raise ValueError('Node metrics are stale')
        used_cpu = quantity(metric['usage']['cpu'])
        used_memory = quantity(metric['usage']['memory'])
        if used_cpu > cpu or used_memory > memory or used_memory != int(used_memory):
            raise ValueError('Node usage exceeds capacity')
        observation.update(cpu_used_percent=round(float(100 * used_cpu / cpu), 1),
                           memory_used_percent=round(float(100 * used_memory / memory), 1),
                           memory_used_bytes=int(used_memory))
    except (KeyError, TypeError, ValueError, subprocess.SubprocessError, OSError):
        # Missing metrics do not become artificial zero resource readings.
        pass
    return observation


def collect_reports(bindings, registry, now=None):
    now = now or datetime.datetime.now(datetime.timezone.utc)
    clusters = registry['clusters']
    if not isinstance(clusters, list) or len({c['target_id'] for c in clusters}) != len(clusters):
        raise ValueError('Ambiguous cluster registry')
    if len({binding['endpoint'] for binding in bindings}) != len(bindings) or len({binding['cluster_target_id'] for binding in bindings}) != len(bindings):
        raise ValueError('Duplicate reporting destinations or cluster bindings')
    reports = []
    for binding in bindings:
        url = urlsplit(binding['endpoint'])
        if (url.scheme != 'https' or url.hostname != 'sla.msgctf.kr' or url.port not in {None, 443}
                or url.username or url.password or url.query or url.fragment
                or not re.fullmatch(r'/ingest/cluster/[a-z][a-z0-9_-]{0,63}', url.path)
                or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', binding['cluster_target_id'])):
            raise ValueError('Invalid cluster reporting destination or binding')
        cluster = next((c for c in clusters if c['target_id'] == binding['cluster_target_id']), None)
        state = 'not_registered' if cluster is None else 'disabled' if cluster.get('enabled') is not True else 'ok'
        try:
            observation = observe(cluster, now) if state == 'ok' else empty(binding['cluster_target_id'], state)
        except (KeyError, TypeError, ValueError, subprocess.SubprocessError, OSError):
            observation = empty(binding['cluster_target_id'], 'query_failed')
        observation['checked_at'] = now.isoformat().replace('+00:00', 'Z')
        reports.append((binding, observation))
    return reports


def report(bindings, registry):
    failures = 0
    for binding, observation in collect_reports(bindings, registry):
        try:
            send(binding, observation)
        except Exception:
            failures += 1
    if failures:
        raise RuntimeError('One or more cluster reports were not accepted')


if __name__ == '__main__':
    try:
        path = Path(os.environ['CREDENTIALS_DIRECTORY']) / 'clusters.json'
        bindings = json.loads(path.read_text())['bindings']
        if not isinstance(bindings, list) or not 1 <= len(bindings) <= 16:
            raise ValueError('Invalid cluster binding collection')
        report(bindings, json.loads(REGISTRY.read_text()))
        print('Cluster heartbeats accepted')
    except Exception as error:
        print('Cluster heartbeat failed: ' + type(error).__name__)
        raise SystemExit(1)
