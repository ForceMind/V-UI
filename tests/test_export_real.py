"""Load generated client configurations with checksum-pinned real cores."""
import json
from itertools import product
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from types import SimpleNamespace

from app.services.validated_export import singbox_client_config
from app.services.mihomo_subscription import mihomo_config
from app.services.core_manager import SingBoxAdapter
from loopback_helpers import certificate_files


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


def vless_websocket(*, fingerprint=False, alpn=False, with_host=True):
    item=vless(fingerprint)
    item.stream_settings['tls'].pop('alpn',None)
    item.remark='vless-ws-test'
    item.stream_settings['transport']={'type':'ws','path':'/vless/ws-0_4.3'}
    if with_host:
        item.stream_settings['transport']['headers']={'Host':'ws.example.test'}
    if alpn:
        # WS uses the HTTP/1.1 upgrade path; h2-only is not a verified profile.
        item.stream_settings['tls']['alpn']=['http/1.1']
    return item


def vless_grpc(*, fingerprint=False, alpn=False, service_name='vless.grpc_0-4.4'):
    item=vless(fingerprint)
    item.stream_settings['tls'].pop('alpn',None)
    item.remark='vless-grpc-test'
    item.stream_settings['transport']={'type':'grpc','service_name':service_name}
    if alpn:
        item.stream_settings['tls']['alpn']=['h2']
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


def shadowsocks(method='aes-128-gcm'):
    return SimpleNamespace(
        id=3,core='sing-box',protocol='shadowsocks',port=12443,remark='ss-test',
        enable=True,expiry_time=0,
        settings={'method':method,'password':'shadowsocks-password-123'},
        stream_settings={},
    )


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

    def test_hysteria2_server_and_exports_load_in_real_pinned_binaries(self):
        from test_hysteria2_profile import hy2_node
        with tempfile.TemporaryDirectory(prefix='vui-hy2-real-export-') as tmp:
            root = Path(tmp)
            item = hy2_node()
            self.check_item(item, root, 'hy2-native-defaults')
            _, cert, key = certificate_files(root)
            item.stream_settings['tls'].update(certificate_path=str(cert), key_path=str(key))
            config = SingBoxAdapter().build_config([item])
            self.assertNotIn('transport', config['inbounds'][0])
            path = root/'server.json'; path.write_text(json.dumps(config))
            result = subprocess.run([str(Path(os.environ['VUI_TEST_CORES'])/'sing-box'),
                                     'check', '-c', str(path)], capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout.decode(errors='replace') + result.stderr.decode(errors='replace'))
            print('Hysteria2 server and both exported clients accepted by unchanged pinned binaries')

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
            for method in ('aes-128-gcm','aes-256-gcm','chacha20-ietf-poly1305'):
                with self.subTest(protocol='shadowsocks',method=method):
                    self.check_item(shadowsocks(method),root,'ss-'+method)
            print('Generated VLESS/TLS, Trojan/TLS and Shadowsocks configs accepted by Mihomo and sing-box')

    def test_vless_ws_tls_configs_load_in_both_real_clients(self):
        with tempfile.TemporaryDirectory(prefix='vui-ws-real-export-') as tmp:
            root=Path(tmp)
            passed=0
            for fingerprint,alpn,with_host in product((False,True),repeat=3):
                with self.subTest(fingerprint=fingerprint,alpn=alpn,with_host=with_host):
                    self.check_item(
                        vless_websocket(fingerprint=fingerprint,alpn=alpn,with_host=with_host),root,
                        f'vless-ws-fp-{fingerprint}-alpn-{alpn}-host-{with_host}',
                    )
                    passed+=1
            if passed==8:
                print('VLESS/WS/TLS independent Chrome/http1.1 settings, with/without Host, accepted by Mihomo and sing-box')

    def test_vless_ws_tls_server_configs_load_in_real_singbox(self):
        with tempfile.TemporaryDirectory(prefix='vui-ws-real-server-') as tmp:
            root=Path(tmp)
            _,cert,key=certificate_files(root)
            passed=0
            for fingerprint,alpn,with_host in product((False,True),repeat=3):
                with self.subTest(fingerprint=fingerprint,alpn=alpn,with_host=with_host):
                    item=vless_websocket(fingerprint=fingerprint,alpn=alpn,with_host=with_host)
                    item.tag='vless-ws-server'
                    item.stream_settings['tls'].update(
                        certificate_path=str(cert),key_path=str(key),
                    )
                    config=SingBoxAdapter().build_config([item])
                    inbound=config['inbounds'][0]
                    self.assertEqual(inbound['transport'],{
                        'type':'ws','path':'/vless/ws-0_4.3',
                    })
                    path=root/f'server-fp-{fingerprint}-alpn-{alpn}-host-{with_host}.json'
                    path.write_text(json.dumps(config))
                    result=subprocess.run(
                        [str(Path(os.environ['VUI_TEST_CORES'])/'sing-box'),
                         'check','-c',str(path)],
                        capture_output=True,timeout=15,
                    )
                    self.assertEqual(result.returncode,0,
                        result.stdout.decode(errors='replace')+
                        result.stderr.decode(errors='replace'))
                    passed+=1
            if passed==8:
                print('VLESS/WS/TLS server configs accepted by sing-box; Host remains client-only metadata')

    def test_vless_grpc_tls_configs_load_in_both_real_clients(self):
        with tempfile.TemporaryDirectory(prefix='vui-grpc-real-export-') as tmp:
            root=Path(tmp)
            passed=0
            services=('vless.grpc_0-4.4','.', '..', 'A'+('a_.-'*32)[:127])
            for fingerprint,alpn,service_name in product((False,True),(False,True),services):
                with self.subTest(fingerprint=fingerprint,alpn=alpn,service_name=service_name):
                    self.check_item(
                        vless_grpc(fingerprint=fingerprint,alpn=alpn,service_name=service_name),root,
                        f'vless-grpc-fp-{fingerprint}-alpn-{alpn}-service-{services.index(service_name)}',
                    )
                    passed+=1
            if passed==16:
                print('VLESS/gRPC/TLS exports accepted by both real clients: independent Chrome/h2 and literal service names')

    def test_vless_grpc_tls_server_configs_load_in_real_singbox(self):
        with tempfile.TemporaryDirectory(prefix='vui-grpc-real-server-') as tmp:
            root=Path(tmp)
            _,cert,key=certificate_files(root)
            passed=0
            services=('vless.grpc_0-4.4','.', '..', 'A'+('a_.-'*32)[:127])
            for fingerprint,alpn,service_name in product((False,True),(False,True),services):
                with self.subTest(fingerprint=fingerprint,alpn=alpn,service_name=service_name):
                    item=vless_grpc(fingerprint=fingerprint,alpn=alpn,service_name=service_name)
                    item.tag='vless-grpc-server'
                    item.stream_settings['tls'].update(
                        certificate_path=str(cert),key_path=str(key),
                    )
                    config=SingBoxAdapter().build_config([item])
                    self.assertEqual(config['inbounds'][0]['transport'],{
                        'type':'grpc','service_name':service_name,
                    })
                    path=root/f'server-{passed}.json'
                    path.write_text(json.dumps(config))
                    result=subprocess.run(
                        [str(Path(os.environ['VUI_TEST_CORES'])/'sing-box'),
                         'check','-c',str(path)],
                        capture_output=True,timeout=15,
                    )
                    self.assertEqual(result.returncode,0,
                        result.stdout.decode(errors='replace')+
                        result.stderr.decode(errors='replace'))
                    passed+=1
            if passed==16:
                print('VLESS/gRPC/TLS server configs accepted by real sing-box with all Chrome/h2 and literal-service variants')


if __name__=='__main__':
    unittest.main()
