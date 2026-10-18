#!/usr/bin/env python3
"""Deliver host resources and the fixed backend container's measured state."""
import json
import os
from pathlib import Path
import subprocess
from urllib.parse import urlsplit

from report_service import send

FIELDS = {'checked_at', 'service_state', 'restart_count', 'memory_used_percent',
          'disk_used_percent', 'load_one_minute', 'cpu_used_percent', 'cpu_logical_count',
          'cpu_model', 'os_name', 'architecture', 'hostname', 'memory_total_bytes',
          'memory_used_bytes', 'disk_total_bytes', 'disk_used_bytes', 'uptime_seconds', 'gateway_state'}


def report(config):
    if (config.get('service') != 'docker:msg-dev-backend-1'
            or urlsplit(config.get('endpoint', '')).path != '/ingest/heartbeat/backend-host'):
        raise ValueError('Invalid backend reporter binding')
    result = subprocess.run(['/usr/local/libexec/sentinel-asset-command', 'resources'],
                            capture_output=True, text=True, check=True, timeout=12)
    observation = json.loads(result.stdout)
    if not isinstance(observation, dict) or observation.keys() != FIELDS:
        raise ValueError('Invalid resource observation')
    send(config, observation)


if __name__ == '__main__':
    try:
        config = json.loads((Path(os.environ['CREDENTIALS_DIRECTORY']) / 'reporter.json').read_text())
        report(config)
        print('Backend heartbeat accepted')
    except Exception as error:
        print('Backend heartbeat failed: ' + type(error).__name__)
        raise SystemExit(1)
