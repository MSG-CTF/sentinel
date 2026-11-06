#!/usr/bin/env python3
"""Fixed Runtime telemetry installer, reached only through the existing SLA SSH key."""
from contextlib import contextmanager
from datetime import datetime, timezone
try:
    import fcntl
except ImportError:
    fcntl = None
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request
from urllib.parse import urlsplit

REPOSITORY = 'MSG-CTF/sentinel'
WORKFLOW_ID = 382911619
ROOT = Path('/opt/sentinel-telemetry')
STATE = Path('/var/lib/sentinel-telemetry-deploy')
FILES = {
    'report_fleet.py': ROOT / 'report_fleet.py',
    'report_clusters.py': ROOT / 'report_clusters.py',
    'report_service.py': ROOT / 'report_service.py',
    'sentinel-fleet-report.service': Path('/etc/systemd/system/sentinel-fleet-report.service'),
    'sentinel-fleet-report.timer': Path('/etc/systemd/system/sentinel-fleet-report.timer'),
    'runtime_deploy.py': Path('/usr/local/libexec/sentinel-telemetry-deploy'),
}
CLIENT_VERSION = 'v1.36.5'
CLIENT_SHA256 = '33bc88a24c3b09cf55bfd59e6ca977881b61fc3f25ab65bc80c65b430248648e'
CLIENT_URL = 'https://dl.k8s.io/release/' + CLIENT_VERSION + '/bin/linux/amd64/kubectl'
CLIENT = ROOT / 'bin' / ('kubectl-' + CLIENT_VERSION)
STAGE = 'request'


def unique_json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate deployment field')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique)


def validate_request(value):
    if (not isinstance(value, dict) or set(value) != {'revision', 'run_id', 'run_attempt'}
            or not isinstance(value['revision'], str) or not re.fullmatch('[0-9a-f]{40}', value['revision'])
            or any(type(value[k]) is not int or not 1 <= value[k] <= 2**63-1 for k in ['run_id', 'run_attempt'])):
        raise ValueError('Invalid telemetry release identity')
    return value


def fetch(url, limit=262144):
    allowed = {'api.github.com', 'raw.githubusercontent.com', 'dl.k8s.io', 'cdn.dl.k8s.io'}
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or parsed.hostname not in allowed or parsed.username or parsed.password:
        raise ValueError('Invalid telemetry source origin')
    request = urllib.request.Request(url, headers={'User-Agent': 'MSG-Sentinel-Telemetry-Deploy/1.0',
        'Accept': 'application/vnd.github+json' if parsed.hostname == 'api.github.com' else '*/*'})
    with urllib.request.urlopen(request, timeout=15) as response:
        final = urlsplit(response.url)
        if final.scheme != 'https' or final.hostname not in allowed:
            raise ValueError('Telemetry source redirected outside its allowed origins')
        raw = response.read(limit+1)
    if len(raw) > limit:
        raise ValueError('Telemetry source exceeded its size limit')
    return raw


def approved_source(identity):
    prefix = 'https://api.github.com/repos/' + REPOSITORY + '/'
    latest = unique_json(fetch(prefix + 'git/ref/heads/dev'))
    run = unique_json(fetch(prefix + 'actions/runs/' + str(identity['run_id'])))
    if (latest.get('object', {}).get('sha') != identity['revision']
            or run.get('head_sha') != identity['revision'] or run.get('head_branch') != 'dev'
            or run.get('head_repository', {}).get('full_name') != REPOSITORY
            or run.get('workflow_id') != WORKFLOW_ID or run.get('run_attempt') != identity['run_attempt']
            or run.get('event') not in {'push', 'workflow_dispatch'}
            or (run.get('status'), run.get('conclusion')) not in {('in_progress', None), ('completed', 'success')}):
        raise ValueError('Telemetry source is not the current approved production run')
    path = 'actions/runs/{run_id}/attempts/{run_attempt}/jobs?per_page=100'.format(**identity)
    document = unique_json(fetch(prefix + path, 1048576))
    jobs = document.get('jobs', [])
    verified = [j for j in jobs if j.get('name') == 'verify']
    deploying = [j for j in jobs if j.get('name') == 'deploy']
    if (document.get('total_count') != len(jobs) or len(verified) != 1 or len(deploying) != 1
            or verified[0].get('status') != 'completed' or verified[0].get('conclusion') != 'success'
            or (deploying[0].get('status'), deploying[0].get('conclusion')) not in {
                ('in_progress', None), ('completed', 'success')}):
        raise ValueError('The exact production attempt has not passed its verification gate')


def source_files(identity):
    prefix = 'https://raw.githubusercontent.com/' + REPOSITORY + '/' + identity['revision'] + '/deploy/telemetry/'
    manifest = unique_json(fetch(prefix + 'runtime-release.json'))
    if (not isinstance(manifest, dict) or set(manifest) != {'schema_version', 'files', 'kubectl'}
            or manifest['schema_version'] != 1 or not isinstance(manifest['files'], dict)
            or set(manifest['files']) != set(FILES)
            or manifest['kubectl'] != {'version': CLIENT_VERSION, 'sha256': CLIENT_SHA256, 'url': CLIENT_URL}):
        raise ValueError('Invalid telemetry file manifest')
    result = {}
    for name, expected in manifest['files'].items():
        if not isinstance(expected, str) or not re.fullmatch('[0-9a-f]{64}', expected):
            raise ValueError('Invalid telemetry source digest')
        raw = fetch(prefix + name)
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError('Telemetry source digest differs from the committed manifest')
        if name.endswith('.py'):
            compile(raw, name, 'exec')
        result[name] = raw
    return result


def trusted(path, directory=False):
    metadata = path.lstat()
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected(metadata.st_mode) or metadata.st_uid != 0 or metadata.st_mode & 0o022:
        raise ValueError('Unsafe telemetry deployment path')
    for parent in path.parents:
        metadata = parent.lstat()
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != 0 or metadata.st_mode & 0o022:
            raise ValueError('Unsafe telemetry deployment parent')


def command(args, timeout=15):
    return subprocess.check_output(args, text=True, stderr=subprocess.PIPE, timeout=timeout).strip()


def app_state():
    return {unit: command(['systemctl', 'show', unit, '--property=MainPID', '--property=NRestarts',
        '--property=ActiveState']) for unit in ['secure-provisioner.service', 'k3s.service']}


def atomic_file(path, raw, mode):
    trusted(path.parent, directory=True)
    if path.exists() or path.is_symlink():
        trusted(path)
    descriptor, temporary = tempfile.mkstemp(prefix='.' + path.name + '.deploy-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), mode)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextmanager
def deployment_lock():
    if fcntl is None:
        raise ValueError('Runtime telemetry deployment requires Linux file locking')
    path = Path('/run/lock/sentinel-telemetry-deploy.lock')
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        metadata = os.fstat(descriptor)
        if metadata.st_uid != 0 or not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077:
            raise ValueError('Unsafe telemetry deployment lock')
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(descriptor)


def fresh_native_observation(value, installed_after, epoch, monotonic):
    at = datetime.fromisoformat(value['checked_at'].replace('Z', '+00:00'))
    started = value.get('collector_started_monotonic')
    invocation = value.get('collector_invocation_id')
    if (type(started) not in (int, float) or not installed_after <= started <= monotonic
            or not isinstance(invocation, str) or not re.fullmatch('[0-9a-f]{32}', invocation)):
        return False
    unit_epoch = epoch - (monotonic - started)
    return (value.get('accepted') is True and at.tzinfo is not None
        and unit_epoch <= at.timestamp() <= epoch+1
        and 0 <= epoch-at.timestamp() < 150
        and type(value.get('node_count')) is int and value['node_count'] > 0)


def wait_for_native_observation(installed_after, timeout=180):
    deadline = time.monotonic()+timeout
    while time.monotonic() < deadline:
        try:
            value = unique_json(Path('/var/lib/sentinel-fleet-report/last-observation.json').read_bytes())
            if fresh_native_observation(value, installed_after, time.time(), time.monotonic()):
                return {k: value[k] for k in ['accepted', 'checked_at', 'roster_version', 'node_count', 'windows_count', 'guest_changes',
                    'collector_started_monotonic', 'collector_invocation_id']}
        except (OSError, ValueError, KeyError, TypeError):
            pass
        time.sleep(2)
    raise RuntimeError('Fresh native telemetry acceptance was not observed')


def install(identity, sources, binary):
    if hashlib.sha256(binary).hexdigest() != CLIENT_SHA256:
        raise ValueError('Official Kubernetes client checksum verification failed')
    before = app_state()
    if any('ActiveState=active' not in value for value in before.values()):
        raise ValueError('The existing runtime services must be active')
    registry = Path('/etc/secure-provisioner/registry.json')
    registry_digest = hashlib.sha256(registry.read_bytes()).hexdigest()
    for path in FILES.values():
        trusted(path)
    (ROOT / 'bin').mkdir(mode=0o755, exist_ok=True)
    trusted(ROOT / 'bin', directory=True)
    backups = {path: (path.read_bytes(), stat.S_IMODE(path.stat().st_mode)) for path in FILES.values()}
    if CLIENT.exists() or CLIENT.is_symlink():
        trusted(CLIENT)
        backups[CLIENT] = (CLIENT.read_bytes(), stat.S_IMODE(CLIENT.stat().st_mode))
    try:
        # Fixed module interfaces remain compatible across these atomic replacements.
        # No timer, application service, Pod or participant workload is stopped.
        atomic_file(CLIENT, binary, 0o755)
        version = unique_json(command([str(CLIENT), 'version', '--client=true', '--output=json'], 9))
        if version.get('clientVersion', {}).get('gitVersion') != CLIENT_VERSION:
            raise ValueError('Unexpected installed Kubernetes client version')
        for name, raw in sources.items():
            atomic_file(FILES[name], raw, 0o644 if name.endswith(('.service', '.timer')) else 0o755)
        command(['systemd-analyze', 'verify', str(FILES['sentinel-fleet-report.service']), str(FILES['sentinel-fleet-report.timer'])])
        command(['systemctl', 'daemon-reload'])
        command(['systemctl', 'enable', '--now', 'sentinel-fleet-report.timer'])
        if command(['systemctl', 'show', 'sentinel-fleet-report.service', '--property=CPUQuotaPerSecUSec', '--value']) != '300ms':
            raise ValueError('Unexpected telemetry CPU quota override')
        observation = wait_for_native_observation(time.monotonic())
        if before != app_state() or hashlib.sha256(registry.read_bytes()).hexdigest() != registry_digest:
            raise ValueError('Runtime application or registry changed during deployment')
    except BaseException:
        for path, (raw, mode) in backups.items():
            atomic_file(path, raw, mode)
        if CLIENT not in backups:
            CLIENT.unlink(missing_ok=True)
        command(['systemctl', 'daemon-reload'])
        raise
    result = {**identity, 'accepted': True, 'installed_at': datetime.now(timezone.utc).isoformat(),
        'source_sha256': {name: hashlib.sha256(raw).hexdigest() for name, raw in sources.items()},
        'kubectl_version': CLIENT_VERSION, 'kubectl_sha256': CLIENT_SHA256, 'kubectl_reinstalled': True,
        'cpu_quota_percent': 30, 'native_observation': observation, 'application_restarts': 0,
        'participant_resource_changes': 0, 'runtime_registry_unchanged': True}
    STATE.mkdir(mode=0o700, exist_ok=True)
    trusted(STATE, directory=True)
    atomic_file(STATE / 'current.json', (json.dumps(result)+'\n').encode(), 0o600)
    return result


def interrupted(*_):
    raise RuntimeError('Telemetry deployment interrupted')


def main():
    global STAGE
    if os.geteuid() != 0 or len(sys.argv) != 1:
        raise ValueError('The fixed telemetry installer requires root and no arguments')
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGHUP, interrupted)
    signal.signal(signal.SIGALRM, interrupted)
    signal.alarm(15)
    request = sys.stdin.buffer.read(1025)
    if len(request) > 1024:
        raise ValueError('Telemetry deployment request is too large')
    identity = validate_request(unique_json(request))
    signal.alarm(240)
    with deployment_lock():
        STAGE = 'approval'
        approved_source(identity)
        STAGE = 'source_manifest'
        sources = source_files(identity)
        STAGE = 'client_download'
        binary = fetch(CLIENT_URL, 120*1024*1024)
        STAGE = 'activation'
        result = install(identity, sources, binary)
    signal.alarm(0)
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        failure = {'accepted': False, 'stage': STAGE, 'error_type': type(error).__name__}
        code = getattr(error, 'code', None)
        if type(code) is int and 100 <= code <= 599:
            failure['http_status'] = code
        print(json.dumps(failure), flush=True)
        raise SystemExit(1) from None
