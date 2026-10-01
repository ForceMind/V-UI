"""Load generated client configurations with checksum-pinned real cores."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from app.services.validated_export import singbox_client_config
from app.services.mihomo_subscription import mihomo_config

@unittest.skipUnless(os.getenv('VUI_TEST_MIHOMO') and os.getenv('VUI_TEST_CORES'),'pinned binaries not provided')
class RealExportTests(unittest.TestCase):
    def test_plain_tls_and_optional_fingerprint_alpn_load(self):
        with tempfile.TemporaryDirectory(prefix='vui-real-export-') as tmp:
            root=Path(tmp)
            for optional in (False,True):
                item=SimpleNamespace(id=1,core='sing-box',protocol='vless',port=10443,remark='test-node',enable=True,
                    settings={'users':[{'uuid':'11111111-1111-1111-1111-111111111111'}]},
                    stream_settings={'tls':{'enabled':True,'server_name':'vpn.example.test'}})
                if optional:
                    item.stream_settings['tls']['alpn']=['h2','http/1.1']
                    item.stream_settings['_vui']={'client_fingerprint':'chrome'}
                yaml_path=root/'client.yaml';yaml_path.write_text(mihomo_config([item],'127.0.0.1',{'mode':'direct'}))
                result=subprocess.run([os.environ['VUI_TEST_MIHOMO'],'-t','-d',str(root),'-f',str(yaml_path)],capture_output=True,timeout=15)
                self.assertEqual(result.returncode,0,result.stdout.decode(errors='replace')+result.stderr.decode(errors='replace'))
                json_path=root/'client.json';json_path.write_text(json.dumps(singbox_client_config([item],'127.0.0.1')))
                result=subprocess.run([str(Path(os.environ['VUI_TEST_CORES'])/'sing-box'),'check','-c',str(json_path)],capture_output=True,timeout=15)
                self.assertEqual(result.returncode,0,result.stderr.decode(errors='replace'))
            print('Generated VLESS/TCP/TLS configuration accepted by Mihomo and sing-box')

if __name__=='__main__': unittest.main()
