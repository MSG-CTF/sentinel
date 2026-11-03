"""Read-only KoTH host and workload readiness; never send gameplay requests."""
from datetime import datetime, timezone
import http.client
import ipaddress
import json
import os
from pathlib import Path
import platform
import re
import subprocess

import report_service as core

HEALTH_CHECKS = {
    'dependency-hell': [{'port': 9090, 'path': '/healthz', 'status': 200, 'kind': 'dependency_health'}],
    'card-battle-arena': [{'port': 9090, 'path': '/healthz', 'status': 200, 'kind': 'ok_text'}],
    'jenga': [{'port': 8080, 'path': '/healthz', 'status': 200, 'kind': 'ok_json'}],
    'swanopoly': [{'port': 8080, 'path': '/', 'status': 200, 'kind': 'html'},
                 {'port': 8080, 'path': '/api/me', 'status': 401, 'kind': 'auth_json'}],
}
NAMES = {'pixel-smuggler', 'grand-tour-duel', 'dependency-hell', 'dependency-hell-runner',
         'card-battle-arena', 'jenga', 'swanopoly'}
NAMESPACES = {name: 'koth-' + name for name in NAMES}
NAMESPACES['dependency-hell'] = 'dependency-hell'
DEPENDENCY_WORKLOADS = ('frontend', 'backend', 'backend-internal')


def stamp():
    return datetime.now(timezone.utc).isoformat()


def validate(config):
    assert config['name'] in NAMES and config['hostname'] == platform.node()
    name = config['name']
    assert config['service'] == ('ssh.service' if name == 'dependency-hell-runner' else 'k3s.service')
    assert config['host']['endpoint'] == 'https://sla.msgctf.kr/ingest/heartbeat/koth-host-' + name
    assert isinstance(config['host']['token'], str) and len(config['host']['token']) >= 32
    if name == 'dependency-hell-runner':
        assert config['application'] is None
    else:
        assert config['namespace'] == NAMESPACES[name]
        assert config['application']['endpoint'] == 'https://sla.msgctf.kr/ingest/heartbeat/koth-app-' + name
        assert isinstance(config['application']['token'], str) and len(config['application']['token']) >= 32
        assert config['application']['token'] != config['host']['token']


def kube(kind, namespace=None):
    args = ['/usr/local/bin/k3s', 'kubectl', '--request-timeout=6s',
            '--cache-dir=/run/sentinel-koth-telemetry/kube-cache', 'get', kind]
    if namespace:
        assert re.fullmatch(r'koth-[a-z0-9-]+|dependency-hell', namespace)
        args += ['-n', namespace]
    result = subprocess.run(args + ['-o', 'json'], capture_output=True, timeout=10)
    if result.returncode:
        # Missing namespaces are expected on not-yet-deployed hosts. Do not
        # turn absence into fabricated healthy workload evidence.
        if b'notfound' in result.stderr.lower() or b'not found' in result.stderr.lower():
            return []
        raise RuntimeError('Kubernetes read did not complete')
    assert len(result.stdout) <= 1048576
    body = json.loads(result.stdout)
    return body['items']


def response_matches(check, status, content_type, body):
    if status != check['status'] or len(body) > 16384:
        return False
    content_type = (content_type or '').lower()
    if check['kind'] == 'ok_text':
        return 'text/plain' in content_type and body.strip().lower() == b'ok'
    if check['kind'] == 'html':
        return 'text/html' in content_type and (b'<!doctype html' in body.lower() or b'<html' in body.lower())
    try:
        value = json.loads(body)
    except (ValueError, UnicodeError):
        return False
    if 'application/json' not in content_type or not isinstance(value, dict):
        return False
    if check['kind'] == 'ok_json':
        return value.get('status') == 'ok'
    if check['kind'] == 'dependency_health':
        return value == {'code': 'SUCCESS', 'message': 'healthy', 'data': None}
    if check['kind'] == 'auth_json':
        return set(value) == {'code', 'message', 'data'} and isinstance(value['code'], str)
    return False


def http_readiness(name, pod_ip, cidr):
    address = ipaddress.ip_address(pod_ip)
    network = ipaddress.ip_network(cidr)
    assert address.version == 4 and address.is_private and not address.is_loopback
    assert address in network and network.prefixlen >= 16
    evidence = []
    for check in HEALTH_CHECKS.get(name, []):
        connection = http.client.HTTPConnection(str(address), check['port'], timeout=4)
        try:
            connection.request('GET', check['path'], headers={'User-Agent': 'MSG-Sentinel-Koth-Readiness/1.0',
                                                            'Accept': 'application/json,text/html,text/plain'})
            response = connection.getresponse()
            body = response.read(16385)
            passed = response_matches(check, response.status, response.getheader('Content-Type'), body)
            evidence.append({'path': check['path'], 'port': check['port'],
                             'http_status': response.status, 'contract_passed': passed})
        except (OSError, http.client.HTTPException) as error:
            evidence.append({'path': check['path'], 'port': check['port'], 'http_status': None,
                             'contract_passed': False, 'error_type': type(error).__name__})
        finally:
            connection.close()
    return all(row['contract_passed'] for row in evidence), evidence


def evaluate(name, namespace, items, nodes, check=http_readiness):
    assert name in NAMES and name != 'dependency-hell-runner'
    assert namespace == NAMESPACES[name]
    if name == 'dependency-hell':
        return evaluate_dependency(namespace, items, nodes, check)
    deployments = [row for row in items if row['kind'] == 'Deployment' and row['metadata']['name'] == 'game']
    if not deployments:
        return {'deployed': False, 'reason': 'WORKLOAD_NOT_DEPLOYED'}, None
    assert len(deployments) == 1
    deployment = deployments[0]
    assert deployment['metadata']['namespace'] == namespace
    selector = deployment['spec']['selector']['matchLabels']
    pods = [row for row in items if row['kind'] == 'Pod' and not row['metadata'].get('deletionTimestamp')
            and row['metadata'].get('namespace') == namespace
            and all(row['metadata'].get('labels', {}).get(k) == value for k, value in selector.items())]
    desired = deployment['spec'].get('replicas', 1)
    ready = desired > 0 and deployment['status'].get('readyReplicas', 0) >= desired and len(pods) >= desired
    summaries = []
    node_map = {node['metadata']['name']: node for node in nodes}
    for pod in pods:
        statuses = pod['status'].get('containerStatuses', [])
        pod_ready = (pod['status'].get('phase') == 'Running'
                     and len(statuses) == len(pod['spec']['containers'])
                     and all(c.get('ready') and 'running' in c.get('state', {}) for c in statuses))
        node = node_map.get(pod['spec'].get('nodeName'))
        conditions = {c['type']: c['status'] for c in (node or {}).get('status', {}).get('conditions', [])}
        node_ready = conditions.get('Ready') == 'True' and all(conditions.get(k) == 'False'
                           for k in ('MemoryPressure', 'DiskPressure', 'PIDPressure'))
        http_ok, http_evidence = False, []
        if pod_ready and node_ready:
            http_ok, http_evidence = check(name, pod['status']['podIP'], node['spec']['podCIDR'])
        ready = ready and pod_ready and node_ready and http_ok
        summaries.append({'pod_uid': pod['metadata']['uid'], 'pod_ready': pod_ready,
                          'node_ready': node_ready, 'http_checks': http_evidence,
                          'container_count': len(statuses),
                          'restart_count': sum(c.get('restartCount', 0) for c in statuses)})
    summary = {'deployed': True, 'deployment_uid': deployment['metadata']['uid'],
               'ready': ready, 'reason': 'KOTH_READINESS_CONFIRMED' if ready else 'KOTH_READINESS_FAILED',
               'http_contract_configured': name in HEALTH_CHECKS, 'pods': summaries}
    return summary, 'active' if ready else 'failed'


def evaluate_dependency(namespace, items, nodes, check):
    # The service has separate long-lived workloads; only frontend owns the health-only port
    results = []
    all_pods = []
    present = {row['metadata']['name'] for row in items if row['kind'] == 'Deployment'
               and row['metadata'].get('namespace') == namespace}
    if not present.intersection(DEPENDENCY_WORKLOADS):
        return {'deployed': False, 'reason': 'WORKLOAD_NOT_DEPLOYED'}, None
    ready = True
    node_map = {node['metadata']['name']: node for node in nodes}
    for workload in DEPENDENCY_WORKLOADS:
        matches = [row for row in items if row['kind'] == 'Deployment'
                   and row['metadata'].get('namespace') == namespace and row['metadata']['name'] == workload]
        assert len(matches) <= 1
        if not matches:
            ready = False; results.append({'name': workload, 'ready': False, 'reason': 'DEPLOYMENT_MISSING'}); continue
        deployment = matches[0]
        selector = deployment['spec']['selector']['matchLabels']
        assert isinstance(selector, dict) and selector
        pods = [row for row in items if row['kind'] == 'Pod' and row['metadata'].get('namespace') == namespace
                and not row['metadata'].get('deletionTimestamp')
                and all(row['metadata'].get('labels', {}).get(key) == value for key, value in selector.items())]
        desired = deployment['spec'].get('replicas', 1)
        workload_ready = (type(desired) is int and desired > 0
                          and deployment.get('status', {}).get('readyReplicas', 0) >= desired and len(pods) >= desired)
        for pod in pods:
            statuses = pod['status'].get('containerStatuses', [])
            pod_ready = (pod['status'].get('phase') == 'Running' and bool(statuses)
                         and len(statuses) == len(pod['spec']['containers'])
                         and all(c.get('ready') and 'running' in c.get('state', {}) for c in statuses))
            node = node_map.get(pod['spec'].get('nodeName'))
            conditions = {c['type']: c['status'] for c in (node or {}).get('status', {}).get('conditions', [])}
            node_ready = conditions.get('Ready') == 'True' and all(conditions.get(k) == 'False'
                          for k in ('MemoryPressure', 'DiskPressure', 'PIDPressure'))
            http_ok, evidence = True, []
            if workload == 'frontend':
                http_ok = False
                if pod_ready and node_ready:
                    http_ok, evidence = check('dependency-hell', pod['status']['podIP'], node['spec']['podCIDR'])
            workload_ready = workload_ready and pod_ready and node_ready and http_ok
            all_pods.append({'workload': workload, 'pod_uid': pod['metadata']['uid'], 'pod_ready': pod_ready,
                             'node_ready': node_ready, 'http_checks': evidence, 'container_count': len(statuses),
                             'restart_count': sum(c.get('restartCount', 0) for c in statuses)})
        ready = ready and workload_ready
        results.append({'name': workload, 'deployment_uid': deployment['metadata']['uid'],
                        'desired_replicas': desired, 'ready': workload_ready})
    return {'deployed': True, 'ready': ready, 'reason': 'KOTH_READINESS_CONFIRMED' if ready else 'KOTH_READINESS_FAILED',
            'http_contract_configured': True, 'workloads': results, 'pods': all_pods}, 'active' if ready else 'failed'


def run(config, *, dry_run=False):
    validate(config)
    observation = core.collect(config['service'])
    host_observation = dict(observation)
    acknowledgements = {}
    if not dry_run:
        core.send(config['host'], host_observation)
        acknowledgements['host'] = True
    if config['application']:
        try:
            items = kube('deployments,pods,services', config['namespace'])
            nodes = kube('nodes')
            app_summary, state = evaluate(config['name'], config['namespace'], items, nodes)
        except Exception as error:
            app_summary, state = {'deployed': None, 'reason': 'KOTH_QUERY_UNVERIFIED',
                                  'error_type': type(error).__name__}, None
        application = {**observation, 'checked_at': stamp(), 'service_state': state,
                       'restart_count': sum(p['restart_count'] for p in app_summary.get('pods', []))} if state else None
    else:
        app_summary, application = {'deployed': False, 'reason': 'RUNNER_HOST_ONLY'}, None
    if not dry_run:
        if application:
            core.send(config['application'], application)
            acknowledgements['application'] = True
    summary = {'checked_at': stamp(), 'hostname': config['hostname'],
               'host_state': host_observation['service_state'], 'application': app_summary,
               'accepted': acknowledgements, 'dry_run': dry_run,
               'gameplay_requests_sent': 0, 'service_restarts': 0,
               'token_values_logged': False}
    if not dry_run:
        state_dir = Path('/var/lib/sentinel-koth-telemetry')
        path = state_dir / 'last-observation.json'
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(summary) + '\n')
        temporary.chmod(0o600)
        os.replace(temporary, path)
    return summary


if __name__ == '__main__':
    try:
        path = Path(os.environ['CREDENTIALS_DIRECTORY']) / 'observer.json'
        config = json.loads(path.read_text())
        print(json.dumps(run(config, dry_run='--dry-run' in __import__('sys').argv)), flush=True)
    except Exception as error:
        print(json.dumps({'collector_failed': True, 'error_type': type(error).__name__}), flush=True)
        raise SystemExit(1) from None
