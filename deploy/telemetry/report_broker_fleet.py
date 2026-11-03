"""Publish a complete, read-only Broker registration snapshot without exporting credentials."""
from datetime import datetime, timezone
import http.client
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

MAX_MEMBERS = 64
CONTAINER = 'resource-broker-app-1'


def address(value):
    ip = ipaddress.ip_address(value)
    if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_reserved:
        raise ValueError('Invalid monitoring address')
    return str(ip)


def identity(value):
    value = str(uuid.UUID(str(value)))
    if uuid.UUID(value).int == 0:
        raise ValueError('Missing resource identity')
    return value


def text(value, maximum=80):
    if not isinstance(value, str) or not 0 < len(value) <= maximum or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError('Invalid resource label')
    return value


def build_roster(resources, connections, credentials, now=None):
    nodes, windows = [], []
    for resource in resources:
        if (not resource['enabled'] or resource['retired_at'] is not None or not resource['account_enabled']
                or resource['provider_instance_state'] != 'RUNNING'):
            continue
        rid = identity(resource['resource_target_id'])
        name = text(resource['instance_name'] or rid)
        connection = connections.get(rid)
        if connection is not None:
            if not connection['enabled']:
                continue
            host = resource['private_ip'] if connection['address_source'] == 'private_ip' else resource['public_ip']
            if connection['address_source'] not in ('public_ip', 'private_ip'):
                raise ValueError('Unknown Windows address source')
            port = connection['port']
            if type(port) is not int or not 1 <= port <= 65535:
                raise ValueError('Invalid Windows listener')
            windows.append({'resource_target_id': rid, 'name': name, 'host': address(host), 'port': port})
        elif resource['runtime_type'] == 'KUBERNETES':
            node_uid = identity(resource['target_id']) if resource['target_id'] else None
            credential = credentials.get(rid)
            if credential:
                server = credential['server_url']
            else:
                host = resource['public_ip'] or resource['private_ip']
                host = address(host) if host else None
                server = ('https://' + ('[' + host + ']' if ':' in host else host) + ':6443') if host else None
            nodes.append({'resource_target_id': rid, 'node_uid': node_uid, 'name': name,
                          'scope_id': text(resource['provider_scope_id'] or 'unassigned'), 'server_url': server})
    ids = [r['resource_target_id'] for r in nodes + windows]
    uids = [n['node_uid'] for n in nodes if n['node_uid']]
    if len(ids) > MAX_MEMBERS or len(set(ids)) != len(ids) or len(set(uids)) != len(uids):
        raise ValueError('Ambiguous or oversized Broker roster')
    return {'schema_version': 1, 'checked_at': (now or datetime.now(timezone.utc)).isoformat(), 'complete': True,
            'nodes': sorted(nodes, key=lambda r: r['resource_target_id']),
            'windows': sorted(windows, key=lambda r: r['resource_target_id'])}


def collect_db():
    from sqlalchemy import select, text as statement
    from sqlalchemy.orm import joinedload
    from app.db.session import get_session_factory
    from app.db.tables import ResourceTargetTable, K3sCredentialTable
    with get_session_factory()() as session:
        session.connection(execution_options={'isolation_level': 'REPEATABLE READ'})
        session.execute(statement('SET TRANSACTION READ ONLY'))
        rows = session.scalars(select(ResourceTargetTable).options(joinedload(ResourceTargetTable.account))).all()
        resources = []
        for row in rows:
            value = {key: getattr(row, key) for key in ('resource_target_id', 'instance_name', 'provider_scope_id',
                'target_id', 'enabled', 'retired_at', 'provider_instance_state', 'public_ip', 'private_ip')}
            value['runtime_type'] = row.runtime_type.value if row.runtime_type else None
            value['account_enabled'] = row.account.enabled
            resources.append(value)
        # Credentials, account identifiers and RDP profiles never leave the Broker database process
        connections = {str(row['resource_target_id']): dict(row) for row in session.execute(statement(
            'SELECT resource_target_id,enabled,port,address_source FROM windows_vm_connections')).mappings()}
        credentials = {str(row.resource_target_id): {'server_url': row.server_url}
                       for row in session.scalars(select(K3sCredentialTable)).all()}
        return build_roster(resources, connections, credentials)


def collect_container():
    source = Path(__file__).read_bytes()
    result = subprocess.run(['docker', 'exec', '-i', CONTAINER, 'python', '-', '--collect-only'],
                            input=source, capture_output=True, timeout=25)
    if result.returncode or len(result.stdout) > 262144:
        raise RuntimeError('Broker snapshot unavailable')
    roster = json.loads(result.stdout)
    if roster.get('complete') is not True:
        raise ValueError('Partial Broker snapshot rejected')
    return roster


def publish(config, roster):
    if set(config) != {'token'} or not isinstance(config['token'], str) or len(config['token']) < 32:
        raise ValueError('Invalid fleet publisher credential')
    connection = http.client.HTTPSConnection('sla.msgctf.kr', timeout=10)
    try:
        connection.request('POST', '/ingest/fleet/roster', json.dumps(roster), headers={
            'Authorization': 'Bearer ' + config['token'], 'Content-Type': 'application/json'})
        response = connection.getresponse(); raw = response.read(8193)
        if response.status != 200 or len(raw) > 8192:
            raise RuntimeError('Roster publication was not accepted')
        value = json.loads(raw)
        if value.get('accepted') is not True:
            raise ValueError('Invalid roster acknowledgement')
        return value
    finally:
        connection.close()


if __name__ == '__main__':
    try:
        if sys.argv[1:] == ['--collect-only']:
            print(json.dumps(collect_db()), flush=True)
        else:
            config = json.loads((Path(os.environ['CREDENTIALS_DIRECTORY']) / 'fleet.json').read_text())
            roster = collect_container(); accepted = publish(config, roster)
            result = {'checked_at': roster['checked_at'], 'accepted': True, 'roster_version': accepted['roster_version'],
                      'node_count': len(roster['nodes']), 'windows_count': len(roster['windows'])}
            path = Path('/var/lib/sentinel-broker-fleet/last-publication.json')
            temporary = path.with_suffix('.tmp'); temporary.write_text(json.dumps(result) + '\n'); temporary.chmod(0o600)
            os.replace(temporary, path); print(json.dumps(result), flush=True)
    except Exception as error:
        print(json.dumps({'publisher_failed': True, 'error_type': type(error).__name__}), flush=True)
        raise SystemExit(1) from None
