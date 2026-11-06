import base64
import hashlib
import json
import os
from pathlib import Path
import tempfile
import subprocess
import unittest
from unittest.mock import Mock, patch

import deploy_host as host
import release


class RuntimeTelemetryDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.public=base64.b64encode(b'test-only-ed25519-host-key').decode()
        self.binding={'asset_id':'runtime','host':'runtime.example.test','port':22,'username':'sentinel-ops',
            'service':'secure-provisioner.service','private_key_env':'SENTINEL_SSH_RUNTIME_B64',
            'host_key_sha256':base64.b64encode(hashlib.sha256(base64.b64decode(self.public)).digest()).decode()}
        self.private=b'-----BEGIN OPENSSH PRIVATE KEY-----\n'+b'test-only-key'*12+b'\n-----END OPENSSH PRIVATE KEY-----\n'
        self.manifest={'runtime_telemetry':True,'revision':'a'*40,'release_id':'a'*40+'-12345-2'}
        self.write_environment()

    def write_environment(self):
        (self.root/'probe.env').write_text('SENTINEL_OPERATIONS_JSON='+json.dumps([self.binding])+'\nSENTINEL_SSH_RUNTIME_B64='+base64.b64encode(self.private).decode()+'\n')

    def receipt(self):
        return {'accepted':True,'revision':'a'*40,'run_id':12345,'run_attempt':2,
            'kubectl_sha256':'33bc88a24c3b09cf55bfd59e6ca977881b61fc3f25ab65bc80c65b430248648e',
            'cpu_quota_percent':30,'native_observation':{'accepted':True},'application_restarts':0,'participant_resource_changes':0}

    def scan(self):return Mock(stdout='runtime.example.test ssh-ed25519 '+self.public+'\n')

    def test_existing_pinned_key_is_ephemeral_and_remote_command_is_fixed(self):
        paths=[]
        def ssh(args,**kwargs):
            self.assertEqual(args[-1],'telemetry-deploy')
            self.assertIn('StrictHostKeyChecking=yes',args)
            key=Path(args[args.index('-i')+1]);paths.append(key.parent)
            self.assertEqual(key.read_bytes(),self.private)
            self.assertEqual(json.loads(kwargs['input']),{'revision':'a'*40,'run_id':12345,'run_attempt':2})
            self.assertEqual(kwargs['timeout'],270)
            return Mock(returncode=0,stdout=json.dumps(self.receipt()))
        with patch.object(host,'command',return_value=self.scan()),patch.object(host.subprocess,'run',side_effect=ssh):
            receipt=host.deploy_runtime_telemetry(self.root,self.manifest)
        self.assertTrue(receipt['accepted'])
        self.assertTrue(all(not p.exists() for p in paths))
        self.assertTrue((self.root/'runtime-telemetry.json').exists())

    def test_wrong_host_pin_does_not_attempt_authentication(self):
        self.binding['host_key_sha256']=base64.b64encode(bytes(32)).decode();self.write_environment()
        with patch.object(host,'command',return_value=self.scan()),patch.object(host.subprocess,'run') as ssh,self.assertRaises(host.DeploymentError):
            host.deploy_runtime_telemetry(self.root,self.manifest)
        ssh.assert_not_called()

    def test_remote_failure_and_stale_or_incomplete_receipts_fail_deployment_and_remove_key(self):
        changes=[{'run_attempt':1},{'revision':'b'*40},{'cpu_quota_percent':60},
            {'native_observation':{'accepted':False}},{'application_restarts':1},{'participant_resource_changes':1}]
        for change in changes:
            paths=[]
            def ssh(args,**kwargs):
                paths.append(Path(args[args.index('-i')+1]).parent)
                return Mock(returncode=0,stdout=json.dumps({**self.receipt(),**change}))
            with self.subTest(change=change),patch.object(host,'command',return_value=self.scan()),patch.object(host.subprocess,'run',side_effect=ssh),self.assertRaises(host.DeploymentError):
                host.deploy_runtime_telemetry(self.root,self.manifest)
            self.assertTrue(all(not p.exists() for p in paths))
        with patch.object(host,'command',return_value=self.scan()),patch.object(host.subprocess,'run',return_value=Mock(returncode=1,stdout='private error')),self.assertRaisesRegex(host.DeploymentError,'^Runtime telemetry installation failed$'):
            host.deploy_runtime_telemetry(self.root,self.manifest)
        paths=[]
        def timed_out(args,**kwargs):
            paths.append(Path(args[args.index('-i')+1]).parent)
            raise subprocess.TimeoutExpired(args,270)
        with patch.object(host,'command',return_value=self.scan()),patch.object(host.subprocess,'run',side_effect=timed_out),self.assertRaisesRegex(host.DeploymentError,'^Runtime telemetry installation did not complete$'):
            host.deploy_runtime_telemetry(self.root,self.manifest)
        self.assertTrue(all(not p.exists() for p in paths))

    def test_other_assets_credentials_and_arbitrary_host_arguments_are_rejected(self):
        for changed in [{'asset_id':'other'},{'private_key_env':'SENTINEL_SSH_BACKEND_B64'},
            {'host':'-oProxyCommand=anything'},{'username':'root'},{'service':'other.service'}]:
            original=self.binding.copy();self.binding.update(changed);self.write_environment()
            with self.subTest(changed=changed),patch.object(host,'command') as command,self.assertRaises(host.DeploymentError):
                host.deploy_runtime_telemetry(self.root,self.manifest)
            command.assert_not_called();self.binding=original

    def test_manifest_enables_runtime_only_for_explicit_production_build(self):
        for flag in ['false','true','invalid']:
            with self.subTest(flag=flag),patch.dict(os.environ,{'SENTINEL_RUNTIME_TELEMETRY_DEPLOY_ENABLED':flag}),patch.object(release,'run',return_value=Mock(stdout='[{"Id":"sha256:test-only"}]')):
                if flag=='invalid':
                    with self.assertRaises(ValueError):release.build_manifest(self.root,'a'*40,'12345','2')
                else:
                    release.build_manifest(self.root,'a'*40,'12345','2')
                    self.assertEqual(json.loads((self.root/'release.json').read_text()).get('runtime_telemetry',False),flag=='true')
        with patch.object(host,'command') as command:
            self.assertIsNone(host.deploy_runtime_telemetry(self.root,{'revision':'a'*40}))
        command.assert_not_called()


if __name__=='__main__':unittest.main()
