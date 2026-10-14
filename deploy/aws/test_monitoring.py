"""Real collector/provisioning checks and monitoring rollback regressions."""
import http.server
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.request
import uuid

import deploy_host as host


class MonitoringRollbackTests(unittest.TestCase):
    def test_partial_start_and_proxy_failure_restore_both_existing_services(self):
        for failure in ('second-start', 'collection', 'proxy'):
            with self.subTest(failure=failure):
                states={name:{'State':{'Running':True},'version':'old'} for name in host.MONITORS}
                def command(args):
                    if args[1]=='rename': states[args[3]]=states.pop(args[2])
                    elif args[1] in ('start','stop'): states[args[-1]]['State']['Running']=args[1]=='start'
                    else: self.fail(str(args))
                def start(name, release):
                    states[name]={'State':{'Running':True},'version':'new'}
                    if name==host.MONITORS[1] and failure=='second-start': raise RuntimeError('fixture')
                def finish():
                    if failure=='proxy': raise RuntimeError('fixture')
                with patch.object(host,'container',states.get), patch.object(host,'command',command), \
                     patch.object(host,'remove_container',lambda name:states.pop(name,None)), \
                     patch.object(host,'start_monitoring',start), \
                     patch.object(host,'monitoring_ready',lambda _:failure!='collection'):
                    with self.assertRaises(RuntimeError): host.activate_monitoring(Path('/fixture'),{'username':'admin'},finish)
                self.assertEqual(set(states),set(host.MONITORS))
                self.assertTrue(all(info['State']['Running'] and info['version']=='old' for info in states.values()))


@unittest.skipUnless(os.environ.get('SENTINEL_DOCKER_TESTS')=='1','Set SENTINEL_DOCKER_TESTS=1 for Grafana integration')
class MonitoringIntegrationTests(unittest.TestCase):
    def test_pinned_images_scrape_private_metrics_and_serve_provisioned_dashboard(self):
        token='fixture-only-'+'m'*30
        class Metrics(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                allowed=self.path=='/internal/metrics' and self.headers.get('Authorization')=='Bearer '+token
                self.send_response(200 if allowed else 401)
                self.send_header('Content-Type','text/plain; version=0.0.4; charset=utf-8')
                self.end_headers()
                if allowed:self.wfile.write(b'# TYPE sentinel_fixture_health gauge\nsentinel_fixture_health 1\n')
            def log_message(self,*args):pass
        server=http.server.ThreadingHTTPServer(('127.0.0.1',8080),Metrics)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        suffix=uuid.uuid4().hex[:10]
        names=('sentinel-prom-test-'+suffix,'sentinel-grafana-test-'+suffix)
        with tempfile.TemporaryDirectory() as temp, patch.object(host,'BASE',Path(temp)), patch.object(host,'MONITORS',names):
            root=Path(temp);release=root/'release';release.mkdir()
            # Exercise the production preparation with a fake docker-cp only;
            # all official images, config validation, listeners and queries are real.
            original=host.command
            def command(args,**kwargs):
                if args[1]=='create':return None
                if args[1]=='cp':
                    shutil.copytree(Path(__file__).resolve().parents[2]/'monitoring',release/'monitoring');return None
                return original(args,**kwargs)
            with patch.object(host,'command',command):
                host.prepare_monitoring('fixture',release,{'domain':'sla.example.com'},token)
            try:
                for name in names:host.start_monitoring(name,release)
                self.assertTrue(host.monitoring_ready('admin',timeout=120),'collector or dashboard failed to start')
                url='http://127.0.0.1:3000/grafana/api/datasources/proxy/uid/Prometheus/api/v1/query?query=sentinel_fixture_health'
                req=urllib.request.Request(url,headers={'X-Sentinel-User':'admin'})
                with urllib.request.urlopen(req,timeout=10) as response:result=json.load(response)
                self.assertEqual(result['data']['result'][0]['value'][1],'1')
                req=urllib.request.Request('http://127.0.0.1:3000/grafana/api/user',headers={'X-Sentinel-User':'admin'})
                with urllib.request.urlopen(req,timeout=10) as response:user=json.load(response)
                self.assertFalse(user['isGrafanaAdmin'])
            finally:
                for name in names:host.remove_container(name)


if __name__=='__main__':unittest.main()
