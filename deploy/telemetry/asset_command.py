#!/usr/bin/env python3
"""Root-owned, fixed-service SSH operations. No user-provided shell, paths or unit names."""
import json
import subprocess
import sys
from pathlib import Path

SERVICE = 'msg-ticket-bot.service'


def service_state():
    result = subprocess.run(['/usr/bin/systemctl', 'show', SERVICE,
                             '--property=ActiveState', '--property=SubState', '--property=NRestarts',
                             '--property=MainPID', '--property=ActiveEnterTimestamp'],
                            capture_output=True, text=True, check=True, timeout=5)
    return dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)


def execute(action):
    if action == 'status':
        return {'service': SERVICE, **service_state()}
    if action == 'resources':
        sys.path.insert(0, '/opt/sentinel-telemetry')
        from report_service import collect
        return collect(SERVICE)
    if action == 'start':
        state = service_state()
        if state.get('ActiveState') not in {'inactive', 'failed'}:
            raise ValueError('Service is not stopped')
        subprocess.run(['/usr/bin/systemctl', 'start', SERVICE], check=True, timeout=12,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {'service': SERVICE, 'result': 'start_requested', **service_state()}
    raise ValueError('Action is not allowed')


if __name__ == '__main__':
    try:
        if len(sys.argv) != 2: raise ValueError('One fixed action required')
        print(json.dumps(execute(sys.argv[1]), ensure_ascii=False))
    except Exception:
        print(json.dumps({'error': 'ACTION_REJECTED_OR_UNCONFIRMED'}))
        raise SystemExit(1)
