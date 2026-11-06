import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import runtime_deploy as installer

IDENTITY = {'revision': 'a'*40, 'run_id': 12345, 'run_attempt': 2}


def approval():
    return [
        {'object': {'sha': IDENTITY['revision']}},
        {'head_sha': IDENTITY['revision'], 'head_branch': 'dev', 'head_repository': {'full_name': installer.REPOSITORY},
         'workflow_id': installer.WORKFLOW_ID, 'run_attempt': 2, 'event': 'push', 'status': 'in_progress', 'conclusion': None},
        {'total_count': 2, 'jobs': [
            {'name': 'verify', 'status': 'completed', 'conclusion': 'success'},
            {'name': 'deploy', 'status': 'in_progress', 'conclusion': None}]},
    ]


class ReleaseAuthorityTests(unittest.TestCase):
    def test_request_has_only_a_complete_workflow_identity(self):
        self.assertEqual(installer.validate_request(IDENTITY), IDENTITY)
        for changed in [{'revision': '../dev'}, {'run_id': True}, {'run_attempt': 0}, {'path': '/etc/passwd'}]:
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                installer.validate_request({**IDENTITY, **changed})
        with self.assertRaises(ValueError):
            installer.unique_json('{"revision":"a","revision":"b"}')

    def test_current_dev_and_exact_successful_attempt_are_required(self):
        with patch.object(installer, 'fetch', side_effect=[json.dumps(v).encode() for v in approval()]) as fetch:
            installer.approved_source(IDENTITY)
            self.assertIn('/attempts/2/jobs?', fetch.call_args.args[0])
        changes = [(0, lambda v: v['object'].update(sha='b'*40)),
            (1, lambda v: v.update(event='pull_request')),
            (1, lambda v: v.update(head_branch='other')),
            (1, lambda v: v.update(run_attempt=1)),
            (1, lambda v: v.update(workflow_id=1)),
            (1, lambda v: v.update(head_repository={'full_name': 'other/repository'})),
            (1, lambda v: v.update(status='completed', conclusion='failure')),
            (2, lambda v: v['jobs'][0].update(conclusion='failure')),
            (2, lambda v: v['jobs'][1].update(status='queued')),
            (2, lambda v: v.update(total_count=101)),
            (2, lambda v: v['jobs'].append(copy.deepcopy(v['jobs'][0])))]
        for index, change in changes:
            values=approval();change(values[index])
            with self.subTest(index=index, change=change), patch.object(installer, 'fetch', side_effect=[json.dumps(v).encode() for v in values]), self.assertRaises(ValueError):
                installer.approved_source(IDENTITY)

    def test_manifest_matches_every_committed_source_and_official_client_pin(self):
        root=Path(__file__).parent
        manifest=json.loads((root/'runtime-release.json').read_text())
        self.assertEqual(set(manifest['files']),set(installer.FILES))
        for name, digest in manifest['files'].items():
            self.assertEqual(hashlib.sha256((root/name).read_bytes().replace(b'\r\n',b'\n')).hexdigest(),digest)
        self.assertEqual(manifest['kubectl'],{'version':installer.CLIENT_VERSION,'sha256':installer.CLIENT_SHA256,'url':installer.CLIENT_URL})

    def test_source_corruption_traversal_and_unpinned_downloads_are_rejected(self):
        sources={name:b'pass\n' if name.endswith('.py') else b'[Unit]\n' for name in installer.FILES}
        manifest={'schema_version':1,'files':{n:hashlib.sha256(v).hexdigest() for n,v in sources.items()},
            'kubectl':{'version':installer.CLIENT_VERSION,'sha256':installer.CLIENT_SHA256,'url':installer.CLIENT_URL}}
        def fetch(url, limit=262144):
            name=url.rsplit('/',1)[1]
            return json.dumps(manifest).encode() if name=='runtime-release.json' else sources[name]
        with patch.object(installer,'fetch',side_effect=fetch):
            self.assertEqual(installer.source_files(IDENTITY),sources)
        original=copy.deepcopy(manifest)
        for corrupt in ['digest','path','client']:
            manifest=copy.deepcopy(original)
            if corrupt=='digest':manifest['files']['report_fleet.py']='0'*64
            if corrupt=='path':manifest['files']['../../etc/passwd']='0'*64
            if corrupt=='client':manifest['kubectl']['url']='https://example.com/kubectl'
            with self.subTest(corrupt=corrupt),patch.object(installer,'fetch',side_effect=fetch),self.assertRaises(ValueError):
                installer.source_files(IDENTITY)

    def test_untrusted_paths_and_redirect_origins_are_rejected(self):
        metadata=SimpleNamespace(st_uid=0,st_mode=stat.S_IFREG|0o755)
        path=Mock();path.lstat.return_value=metadata;path.parents=[]
        installer.trusted(path)
        for owner,mode in [(1001,stat.S_IFREG|0o755),(0,stat.S_IFLNK|0o777),(0,stat.S_IFREG|0o775)]:
            path.lstat.return_value=SimpleNamespace(st_uid=owner,st_mode=mode)
            with self.subTest(owner=owner,mode=mode),self.assertRaises(ValueError):installer.trusted(path)
        for url in ['http://raw.githubusercontent.com/file','https://raw.githubusercontent.com.evil.test/file','https://user@api.github.com/file']:
            with self.subTest(url=url),patch.object(installer.urllib.request,'urlopen') as request,self.assertRaises(ValueError):installer.fetch(url)
            request.assert_not_called()
        response=Mock();response.url='http://api.github.com/file'
        response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        with patch.object(installer.urllib.request,'urlopen',return_value=response),self.assertRaises(ValueError):
            installer.fetch('https://api.github.com/file')

    def test_acceptance_must_belong_to_a_process_started_after_installation(self):
        value={'accepted':True,'checked_at':datetime.fromtimestamp(997,timezone.utc).isoformat(),'node_count':10}
        # Monotonic 15 corresponds to epoch 995 at the sampled clock pair.
        self.assertTrue(installer.fresh_native_observation(value,15,10,1000,20))
        # An old process can finish after installation; its acceptance is insufficient.
        self.assertFalse(installer.fresh_native_observation(value,9,10,1000,20))
        # The next invocation may already have started while the file still contains its predecessor's report.
        self.assertFalse(installer.fresh_native_observation(value,19,10,1000,20))
        for changed in [{'accepted':False},{'node_count':True},{'node_count':0},
            {'checked_at':datetime.fromtimestamp(800,timezone.utc).isoformat()},
            {'checked_at':datetime.fromtimestamp(1005,timezone.utc).isoformat()},
            {'checked_at':'2026-11-06T00:00:00'}]:
            with self.subTest(changed=changed):
                self.assertFalse(installer.fresh_native_observation({**value,**changed},15,10,1000,20))

    @unittest.skipUnless(shutil.which('sh'), 'requires a POSIX shell')
    def test_forced_entry_rejects_shell_syntax_and_deployer_arguments(self):
        script=Path(__file__).with_name('ssh_entry.sh')
        for command in ['telemetry-deploy extra','telemetry-deploy; id','$(id)','scp -t /tmp/file']:
            with self.subTest(command=command):
                result=subprocess.run(['sh',str(script)],env={**os.environ,'SSH_ORIGINAL_COMMAND':command},capture_output=True,timeout=5)
                self.assertEqual(result.returncode,1)


@unittest.skipUnless(hasattr(os,'fchmod'), 'requires POSIX atomic file modes')
class InstallationRollbackTests(unittest.TestCase):
    def test_bad_binary_is_rejected_before_any_runtime_mutation(self):
        with patch.object(installer,'app_state') as state,patch.object(installer,'atomic_file') as write,self.assertRaises(ValueError):
            installer.install(IDENTITY,{},b'corrupted download')
        state.assert_not_called();write.assert_not_called()

    def test_failed_native_acceptance_restores_sources_and_the_previous_client(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);sources={};targets={}
            for name in installer.FILES:
                path=root/name;path.write_bytes(b'previous '+name.encode());targets[name]=path
                sources[name]=b'new '+name.encode()
            client=root/'bin'/'kubectl-v1.36.5';client.parent.mkdir();client.write_bytes(b'previous client')
            registry=root/'registry.json';registry.write_bytes(b'unchanged registry')
            original_path=installer.Path
            def path(value):return registry if value=='/etc/secure-provisioner/registry.json' else original_path(value)
            def command(args,timeout=15):
                if args[0]==str(client):return json.dumps({'clientVersion':{'gitVersion':installer.CLIENT_VERSION}})
                if '--property=CPUQuotaPerSecUSec' in args:return '300ms'
                return ''
            binary=b'verified test-only client';digest=hashlib.sha256(binary).hexdigest()
            with patch.object(installer,'ROOT',root),patch.object(installer,'FILES',targets),patch.object(installer,'CLIENT',client),patch.object(installer,'CLIENT_SHA256',digest),patch.object(installer,'trusted'),patch.object(installer,'Path',side_effect=path),patch.object(installer,'app_state',return_value={'runtime':'ActiveState=active'}),patch.object(installer,'command',side_effect=command),patch.object(installer,'wait_for_native_observation',side_effect=RuntimeError('no accepted report')),self.assertRaises(RuntimeError):
                installer.install(IDENTITY,sources,binary)
            self.assertEqual(client.read_bytes(),b'previous client')
            for name,target in targets.items():self.assertEqual(target.read_bytes(),b'previous '+name.encode())
            self.assertEqual(list(root.glob('.*.deploy-*')),[])


if __name__=='__main__':unittest.main()
