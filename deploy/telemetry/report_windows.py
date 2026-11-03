"""Read registered Windows RDP listeners from Runtime; never log in, reserve or execute."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import http.client
import ipaddress
import json
import os
from pathlib import Path
import socket
import uuid

RUNTIME_HOST = '34.9.255.41'
RUNTIME_PATH = '/internal/v1/windows-vms?limit=200'
INGEST_HOST = 'sla.msgctf.kr'
INGEST_PATH = '/ingest/heartbeat/windows'


def validate(config):
    ids = config['expected_target_ids']
    if (not isinstance(ids, list) or not 1 <= len(ids) <= 32 or len(set(ids)) != len(ids)
            or any(str(uuid.UUID(value)) != value for value in ids)
            or any(not isinstance(config.get(key), str) or len(config[key]) < 32
                   for key in ('runtime_token', 'ingest_token'))):
        raise ValueError('Invalid fixed Windows reporting binding')


def inventory(config):
    validate(config)
    connection = http.client.HTTPSConnection(RUNTIME_HOST, timeout=10)
    try:
        connection.request('GET', RUNTIME_PATH, headers={'Authorization': 'Bearer ' + config['runtime_token'],
            'Accept': 'application/json', 'Cache-Control': 'no-cache', 'User-Agent': 'MSG-Sentinel-Windows-Readiness/1.0'})
        response = connection.getresponse(); raw = response.read(1048577)
        if response.status != 200 or len(raw) > 1048576 or (response.getheader('Content-Type') or '').split(';')[0].lower() != 'application/json':
            raise ValueError('Windows inventory unavailable')
        return parse_inventory(json.loads(raw), config['expected_target_ids'])
    finally:
        connection.close()


def parse_inventory(value, expected):
    if (not isinstance(value, dict) or value.get('next_cursor', 'missing') is not None
            or not isinstance(value.get('items'), list) or len(value['items']) != len(expected)):
        raise ValueError('Incomplete Windows inventory')
    targets = []
    for row in value['items']:
        identity = row['resource_target_id']; host = row['host']; port = row['port']
        address = ipaddress.ip_address(host)
        if (str(uuid.UUID(identity)) != identity or row['protocol'] != 'rdp' or type(port) is not int
                or not 1 <= port <= 65535 or address.is_loopback or address.is_multicast or address.is_unspecified):
            raise ValueError('Invalid Windows listener binding')
        # Discard RDP profiles, usernames and certificates before probing or saving any evidence
        targets.append({'resource_target_id': identity, 'host': host, 'port': port})
    if len({row['resource_target_id'] for row in targets}) != len(expected) or {row['resource_target_id'] for row in targets} != set(expected):
        raise ValueError('Windows inventory identity changed')
    return targets


def probe(target):
    ready = False
    try:
        with socket.create_connection((target['host'], target['port']), timeout=4):
            ready = True
    except OSError:
        pass
    return {'resource_target_id': target['resource_target_id'], 'listener_reachable': ready}


def collect(config):
    targets = inventory(config)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(probe, targets))
    ready = sum(row['listener_reachable'] for row in results)
    report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'service_state': 'active' if ready == len(targets) else 'failed',
        'gateway_state': 'unverified', 'connection_target_count': len(targets), 'connection_ready_count': ready,
        'restart_count': None, 'memory_used_percent': None, 'disk_used_percent': None, 'load_one_minute': None}
    return report, results


def send(config, report):
    connection = http.client.HTTPSConnection(INGEST_HOST, timeout=10)
    try:
        connection.request('POST', INGEST_PATH, json.dumps(report).encode(),
            {'Authorization': 'Bearer ' + config['ingest_token'], 'Content-Type': 'application/json'})
        response = connection.getresponse(); raw = response.read(8193)
        if response.status != 200 or len(raw) > 8192 or json.loads(raw).get('accepted') is not True:
            raise ValueError('Windows report was not accepted')
    finally:
        connection.close()


def run(config, dry_run=False):
    report, results = collect(config)
    if not dry_run:
        send(config, report)
    return {'observation': report, 'targets': results, 'accepted': not dry_run, 'dry_run': dry_run,
        'authenticated_sessions_opened': 0, 'executions_submitted': 0, 'resources_reserved': 0,
        'guest_changes': 0, 'host_metrics_reported': False}


if __name__ == '__main__':
    try:
        config = json.loads((Path(os.environ['CREDENTIALS_DIRECTORY']) / 'windows.json').read_text())
        result = run(config, dry_run='--dry-run' in __import__('sys').argv)
        if not result['dry_run']:
            path = Path('/var/lib/sentinel-windows-report/last-observation.json')
            temporary = path.with_suffix('.tmp'); temporary.write_text(json.dumps(result) + '\n'); temporary.chmod(0o600)
            os.replace(temporary, path)
        print(json.dumps(result), flush=True)
    except Exception as error:
        print(json.dumps({'collector_failed': True, 'error_type': type(error).__name__}), flush=True)
        raise SystemExit(1) from None
