"""Read the current Broker roster and observe its nodes and RDP listeners without guest actions."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import base64
import hashlib
import http.client
import ipaddress
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import tempfile
import uuid
from urllib.parse import urlsplit

import report_clusters

RUNTIME = Path('/run/sentinel-fleet-report')
MAX_BYTES = 262144


def unique_json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate JSON field')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique)


def request(host, path, token, body=None, limit=MAX_BYTES):
    connection = http.client.HTTPSConnection(host, timeout=10)
    try:
        connection.request('POST' if body is not None else 'GET', path,
            json.dumps(body) if body is not None else None,
            headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/json', 'Content-Type': 'application/json',
                     'Cache-Control': 'no-cache', 'User-Agent': 'MSG-Sentinel-Fleet/1.0'})
        response = connection.getresponse(); raw = response.read(limit + 1)
        if len(raw) > limit or (response.getheader('Content-Type') or '').split(';')[0] != 'application/json':
            raise ValueError('Invalid fleet response')
        if response.status not in (200, 409):
            raise ValueError('Fleet request unavailable')
        return response.status, unique_json(raw)
    finally:
        connection.close()


def uuid_value(value):
    if not isinstance(value, str) or str(uuid.UUID(value)) != value or uuid.UUID(value).int == 0:
        raise ValueError('Invalid fleet identity')
    return value


def ip_value(value):
    address = ipaddress.ip_address(value)
    if address.is_loopback or address.is_link_local or address.is_multicast or address.is_unspecified or address.is_reserved:
        raise ValueError('Invalid fleet address')
    return value


def validate_desired(value):
    if (value.get('schema_version') != 1 or value.get('complete') is not True or value.get('storage_healthy') is not True
            or not isinstance(value.get('roster_version'), str) or len(value['roster_version']) != 64
            or any(c not in '0123456789abcdef' for c in value['roster_version'])
            or not isinstance(value.get('nodes'), list) or not isinstance(value.get('windows'), list)
            or len(value['nodes']) + len(value['windows']) > 64):
        raise ValueError('No complete current Broker roster')
    at = datetime.fromisoformat(value['checked_at'].replace('Z', '+00:00'))
    if at.tzinfo is None or not -30 <= (datetime.now(timezone.utc) - at).total_seconds() <= 180:
        raise ValueError('Broker roster is stale')
    ids = []
    for member in value['nodes'] + value['windows']:
        ids.append(uuid_value(member['resource_target_id']))
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate roster identity')
    for node in value['nodes']:
        if node['node_uid'] is not None:
            uuid_value(node['node_uid'])
        if node['server_url'] is not None:
            url = urlsplit(node['server_url'])
            if (url.scheme != 'https' or url.port != 6443 or url.path not in ('','/') or url.username or url.password or url.query or url.fragment):
                raise ValueError('Invalid Kubernetes endpoint')
            ip_value(url.hostname)
    for windows in value['windows']:
        ip_value(windows['host'])
        if type(windows['port']) is not int or not 1 <= windows['port'] <= 65535:
            raise ValueError('Invalid RDP port')
    return value


def command(args, raw=None):
    process = subprocess.run(args, input=raw, capture_output=True, text=True, timeout=9)
    if process.returncode or len(process.stdout) > 1048576:
        raise ValueError('Credential verification failed')
    return process.stdout


def decoded(value):
    return base64.b64decode(value, validate=True).decode('utf-8')


def verified_kubeconfig(credential, member):
    if (credential.get('schema_version') != 1 or credential.get('resource_target_id') != member['resource_target_id']
            or credential.get('target_id') != member['node_uid'] or credential.get('server_url') != member['server_url']):
        raise ValueError('Broker credential identity differs from roster')
    raw = decoded(credential['kubeconfig_base64'])
    config = unique_json(command([report_clusters.KUBECTL, '--kubeconfig=/dev/stdin', 'config', 'view', '--raw', '-o', 'json'], raw))
    if any(len(config.get(key, [])) != 1 for key in ('clusters', 'users', 'contexts')):
        raise ValueError('Ambiguous kubeconfig')
    cluster = config['clusters'][0]['cluster']; user = config['users'][0]['user']; context = config['contexts'][0]
    if (set(cluster) != {'server', 'certificate-authority-data'} or cluster['server'] != member['server_url']
            or set(user) != {'client-certificate-data', 'client-key-data'} or config['current-context'] != context['name']
            or context['context']['cluster'] != config['clusters'][0]['name'] or context['context']['user'] != config['users'][0]['name']):
        raise ValueError('Unsafe kubeconfig authentication or TLS settings')
    decoded(cluster['certificate-authority-data']); decoded(user['client-key-data'])
    certificate = decoded(user['client-certificate-data'])
    leaf = certificate.split('-----END CERTIFICATE-----', 1)[0] + '-----END CERTIFICATE-----\n'
    fingerprint = hashlib.sha256(ssl.PEM_cert_to_DER_cert(leaf)).hexdigest()
    if fingerprint != credential.get('client_certificate_fingerprint_sha256'):
        raise ValueError('Client certificate fingerprint mismatch')
    expiry = command(['openssl', 'x509', '-noout', '-enddate', '-checkend', '300'], certificate)
    actual = ssl.cert_time_to_seconds(expiry.splitlines()[0].removeprefix('notAfter='))
    advertised = datetime.fromisoformat(credential['client_certificate_not_after'].replace('Z', '+00:00'))
    if advertised.tzinfo is None or advertised.timestamp() != actual:
        raise ValueError('Certificate validity differs from Broker')
    # Keep only the validated, embedded fields. Plugins, files, proxy URLs and extensions are never used
    return json.dumps({'apiVersion': 'v1', 'kind': 'Config', 'current-context': 'fleet',
        'clusters': [{'name': 'fleet', 'cluster': cluster}], 'users': [{'name': 'fleet', 'user': user}],
        'contexts': [{'name': 'fleet', 'context': {'cluster': 'fleet', 'user': 'fleet'}}]})


def node_report(member, config):
    now = datetime.now(timezone.utc)
    binding = member['node_uid'] or 'unbound-' + member['resource_target_id'].replace('-', '')
    observation = report_clusters.empty(binding, 'not_registered' if not member['node_uid'] or not member['server_url'] else 'query_failed')
    temporary = None
    try:
        if member['node_uid'] and member['server_url']:
            status, credential = request('broker.mjsec.kr', '/api/v1/runtime/k3s-credentials/' + member['resource_target_id'],
                                         config['broker_runtime_token'], limit=1048576)
            if status != 200:
                raise ValueError('Broker credential unavailable')
            raw = verified_kubeconfig(credential, member)
            directory = RUNTIME / 'kubeconfigs'; directory.mkdir(mode=0o700, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode='w', suffix='.json', dir=directory, delete=False) as stream:
                temporary = Path(stream.name); os.chmod(temporary, 0o600); stream.write(raw)
            report_clusters.CACHE_DIRECTORY = str(RUNTIME / 'cache')
            observation = report_clusters.observe({'target_id': binding, 'expected_node_uid': member['node_uid'],
                'enabled': True, 'kubeconfig_path': str(temporary)}, now)
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError):
        pass
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    observation['checked_at'] = now.isoformat()
    return {'resource_target_id': member['resource_target_id'], 'node': observation}


def windows_report(member):
    reachable = False
    try:
        with socket.create_connection((member['host'], member['port']), timeout=4):
            reachable = True
    except OSError:
        pass
    return {'resource_target_id': member['resource_target_id'],
            'windows': {'checked_at': datetime.now(timezone.utc).isoformat(), 'reachable': reachable}}


def run(config):
    if set(config) != {'observer_token', 'broker_runtime_token'} or any(not isinstance(v, str) or len(v) < 32 for v in config.values()):
        raise ValueError('Invalid fleet observer credentials')
    status, desired = request('sla.msgctf.kr', '/ingest/fleet/desired', config['observer_token'])
    if status != 200:
        raise ValueError('Fleet discovery unavailable')
    validate_desired(desired)
    with ThreadPoolExecutor(max_workers=4) as pool:
        nodes = [pool.submit(node_report, member, config) for member in desired['nodes']]
        windows = [pool.submit(windows_report, member) for member in desired['windows']]
        reports = [future.result() for future in nodes + windows]
    body = {'schema_version': 1, 'roster_version': desired['roster_version'],
            'checked_at': datetime.now(timezone.utc).isoformat(), 'reports': reports}
    status, acknowledgement = request('sla.msgctf.kr', '/ingest/fleet/observations', config['observer_token'], body)
    if status == 409:
        return {'accepted': False, 'retry_next_cycle': True, 'code': acknowledgement.get('code')}
    if acknowledgement.get('accepted') is not True:
        raise ValueError('Fleet observations were not accepted')
    return {'accepted': True, 'checked_at': body['checked_at'], 'roster_version': body['roster_version'],
            'node_count': len(nodes), 'windows_count': len(windows), 'guest_changes': 0}


if __name__ == '__main__':
    try:
        directory = Path(os.environ['CREDENTIALS_DIRECTORY'])
        config = unique_json((directory / 'fleet.json').read_text())
        if set(config) != {'observer_token'}:
            raise ValueError('Invalid observer configuration')
        config['broker_runtime_token'] = (directory / 'broker-token').read_text().strip()
        result = run(config)
        path = Path('/var/lib/sentinel-fleet-report/last-observation.json')
        temporary = path.with_suffix('.tmp'); temporary.write_text(json.dumps(result) + '\n'); temporary.chmod(0o600)
        os.replace(temporary, path); print(json.dumps(result), flush=True)
    except Exception as error:
        print(json.dumps({'observer_failed': True, 'error_type': type(error).__name__}), flush=True)
        raise SystemExit(1) from None
