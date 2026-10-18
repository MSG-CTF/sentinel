#!/usr/bin/env python3
"""Inspect or start one fixed backend container without exposing Docker access."""
import json
import http.client
from pathlib import Path
import re
import stat
import subprocess
import sys

CONTAINER = 'msg-dev-backend-1'
SERVICE = 'docker:' + CONTAINER
DOCKER = ['/usr/bin/docker', '--host=unix:///var/run/docker.sock',
          '--config=/etc/sentinel-telemetry/docker-client']
BRIDGE_STATE = Path('/opt/msg-dev/deploy-state/backend-bridge.json')


def verified_bridge():
    """A stopped primary is covered only by a measured, serving deployment bridge."""
    connection = None
    try:
        metadata = BRIDGE_STATE.lstat()
        parent = BRIDGE_STATE.parent.lstat()
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0
                or metadata.st_mode & 0o022 or metadata.st_size > 4096
                or not stat.S_ISDIR(parent.st_mode) or parent.st_uid != 0 or parent.st_mode & 0o022):
            return None
        bridge = json.loads(BRIDGE_STATE.read_text())
        if (bridge.get('phase') != 'serving'
                or not re.fullmatch(r'[a-f0-9]{64}', bridge.get('candidate', ''))
                or not re.fullmatch(r'msg-dev-backend-candidate-[A-Za-z0-9._-]{1,100}', bridge.get('candidate_name', ''))):
            return None
        result = subprocess.run(DOCKER + ['inspect', '--format',
            '{"id":{{json .Id}},"name":{{json .Name}},"running":{{.State.Running}},"restart_count":{{.RestartCount}}}',
            bridge['candidate']], capture_output=True, text=True, check=True, timeout=2)
        candidate = json.loads(result.stdout)
        if (candidate['id'] != bridge['candidate']
                or candidate['name'] != '/' + bridge['candidate_name']
                or candidate['running'] is not True):
            return None
        connection = http.client.HTTPConnection('127.0.0.1', 8080, timeout=1.5)
        connection.request('GET', '/healthz', headers={'Host': 'msgctf.kr',
            'X-Forwarded-Proto': 'https', 'Accept': 'application/json'})
        response = connection.getresponse()
        if (response.status != 200
                or response.getheader('Content-Type', '').split(';')[0] != 'application/json'):
            return None
        raw = response.read(4097)
        if len(raw) > 4096:
            return None
        health = json.loads(raw)
        if not isinstance(health, dict) or any(health.get(key) != 'ok' for key in ('status', 'database', 'cache')):
            return None
        return {'ActiveState': 'active', 'NRestarts': int(candidate['restart_count'])}
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, http.client.HTTPException):
        return None
    finally:
        if connection is not None:
            connection.close()


def container_state():
    result = subprocess.run(DOCKER + ['inspect', '--format',
        '{"state":{{json .State}},"restart_count":{{.RestartCount}}}', CONTAINER],
        capture_output=True, text=True, check=True, timeout=5)
    document = json.loads(result.stdout)
    state = document['state']
    status = state['Status']
    if status not in {'created', 'running', 'paused', 'restarting', 'removing', 'exited', 'dead'}:
        raise ValueError('Unknown container state')
    health = state.get('Health', {}).get('Status')
    active = ('activating' if status == 'restarting' else
              'active' if status == 'running' and health != 'unhealthy' else
              'inactive' if status in {'created', 'exited'} else 'failed')
    return {'service': SERVICE, 'container': CONTAINER, 'ActiveState': active,
            'SubState': status, 'NRestarts': int(document['restart_count']),
            'MainPID': int(state.get('Pid', 0)), 'health': health or 'unverified'}


def resources():
    sys.path.insert(0, '/opt/sentinel-telemetry')
    from report_service import collect
    observation = collect('docker.service')
    try:
        state = container_state()
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        state = verified_bridge()
        if state is None:
            raise
    else:
        if state['ActiveState'] != 'active':
            state = verified_bridge() or state
    observation.update(service_state=state['ActiveState'], restart_count=state['NRestarts'])
    return observation


def execute(action):
    if action == 'status':
        return container_state()
    if action == 'resources':
        return resources()
    if action == 'start':
        state = container_state()
        if state['SubState'] not in {'created', 'exited'}:
            raise ValueError('Container is not stopped')
        subprocess.run(DOCKER + ['start', CONTAINER], check=True, timeout=12,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {'result': 'start_requested', **container_state()}
    raise ValueError('Action is not allowed')


if __name__ == '__main__':
    try:
        if len(sys.argv) != 2:
            raise ValueError('One fixed action required')
        print(json.dumps(execute(sys.argv[1]), ensure_ascii=False))
    except Exception:
        print(json.dumps({'error': 'ACTION_REJECTED_OR_UNCONFIRMED'}))
        raise SystemExit(1)
