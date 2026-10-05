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


def vless(optional=False):
    item=SimpleNamespace(
        id=1,core='sing-box',protocol='vless',port=10443,remark='vless-test',
        enable=True,expiry_time=0,
        settings={'users':[{'uuid':'11111111-1111-1111-1111-111111111111'}]},
        stream_settings={'tls':{'enabled':True,'server_name':'vpn.example.test'}},
    )
    if optional:
        item.stream_settings['tls']['alpn']=['h2','http/1.1']
        item.stream_settings['_vui']={
            'security':'tls',
            'server_name':'vpn.example.test',
            'client_fingerprint':'chrome',
            'skip_cert_verify':False,
        }
    return item


def trojan(optional=False):
    item=SimpleNamespace(
        id=2,core='sing-box',protocol='trojan',port=11443,remark='trojan-test',
        enable=True,expiry_time=0,
        settings={'users':[{'password':'trojan-password-123'}]},
        stream_settings={'tls':{'enabled':True,'server_name':'trojan.example.test'}},
    )
    if optional:
        item.stream_settings['tls']['alpn']=['h2','http/1.1']
        item.stream_settings['_vui']={
            'security':'tls',
            'server_name':'trojan.example.test',
            'client_fingerprint':'chrome',
            'skip_cert_verify':False,
        }
    return item


@unittest.skipUnless(
    os.getenv('VUI_TEST_MIHOMO') and os.getenv('VUI_TEST_CORES'),
    'pinned binaries not provided',
)
class RealExportTests(unittest.TestCase):
    def check_item(self,item,root,label):
        yaml_path=root/(label+'.yaml')
        yaml_path.write_text(
            mihomo_config([item],'127.0.0.1',{'mode':'direct'})
        )
        result=subprocess.run(
            [os.environ['VUI_TEST_MIHOMO'],'-t','-d',str(root),'-f',str(yaml_path)],
            capture_output=True,timeout=15,
        )
        self.assertEqual(
            result.returncode,0,
            result.stdout.decode(errors='replace')+
            result.stderr.decode(errors='replace'),
        )

        json_path=root/(label+'.json')
        json_path.write_text(
            json.dumps(singbox_client_config([item],'127.0.0.1'))
        )
        result=subprocess.run(
            [str(Path(os.environ['VUI_TEST_CORES'])/'sing-box'),
             'check','-c',str(json_path)],
            capture_output=True,timeout=15,
        )
        self.assertEqual(
            result.returncode,0,
            result.stdout.decode(errors='replace')+
            result.stderr.decode(errors='replace'),
        )

    def test_vless_and_trojan_tls_configs_load_in_real_clients(self):
        with tempfile.TemporaryDirectory(prefix='vui-real-export-') as tmp:
            root=Path(tmp)
            for protocol,factory in (('vless',vless),('trojan',trojan)):
                for optional in (False,True):
                    with self.subTest(protocol=protocol,optional=optional):
                        self.check_item(
                            factory(optional),
                            root,
                            protocol+('-optional' if optional else '-plain'),
                        )
            print('Generated VLESS/TCP/TLS and Trojan/TCP/TLS configs accepted by Mihomo and sing-box')


if __name__=='__main__':
    unittest.main()
