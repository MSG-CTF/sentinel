#!/usr/bin/env python3
"""Inspect or start one fixed backend container without exposing Docker access."""
import json
import subprocess
import sys

CONTAINER = 'msg-dev-backend-1'
SERVICE = 'docker:' + CONTAINER
DOCKER = ['/usr/bin/docker', '--host=unix:///var/run/docker.sock',
          '--config=/etc/sentinel-telemetry/docker-client']


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
    state = container_state()
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
