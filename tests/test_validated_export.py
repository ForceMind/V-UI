import base64
from copy import deepcopy
import json
from types import SimpleNamespace
import unittest
from urllib.parse import parse_qs, unquote, urlsplit

import yaml

from app.services.validated_export import (
    ExportError,
    base64_subscription,
    share_link,
    singbox_client_config,
    validated_nodes,
)
from app.services.mihomo_subscription import mihomo_config


def vless_node(**changes):
    value=dict(
        id=1,core='sing-box',protocol='vless',port=10443,
        remark='demo / 中文',enable=True,expiry_time=0,
        settings={'users':[{'uuid':'11111111-1111-1111-1111-111111111111'}]},
        stream_settings={'tls':{
            'enabled':True,
            'server_name':'vpn.example.test',
            'certificate_path':'/private/server/cert.pem',
            'key_path':'/private/server/key.pem',
        }},
    )
    value.update(changes)
    return SimpleNamespace(**value)


def trojan_node(**changes):
    value=dict(
        id=2,core='sing-box',protocol='trojan',port=11443,
        remark='trojan / 中文',enable=True,expiry_time=0,
        settings={'users':[{'password':'Tr0jan-pass:/?#[]@!$&()*+,;='}]},
        stream_settings={'tls':{
            'enabled':True,
            'server_name':'trojan.example.test',
            'certificate_path':'/private/server/trojan-cert.pem',
            'key_path':'/private/server/trojan-key.pem',
        }},
    )
    value.update(changes)
    return SimpleNamespace(**value)


def ss_node(method="aes-128-gcm", **changes):
    value=dict(
        id=3,core='sing-box',protocol='shadowsocks',port=12443,
        remark='ss / 中文',enable=True,expiry_time=0,
        settings={'method':method,'password':'shadowsocks-password-123'},
        stream_settings={},
    )
    value.update(changes)
    return SimpleNamespace(**value)


class ValidatedExportTests(unittest.TestCase):
    def test_vless_three_outputs_preserve_connection_fields_without_server_paths(self):
        item=vless_node()
        outputs=(
            mihomo_config([item],'vpn.example.test',{'mode':'direct'}),
            json.dumps(singbox_client_config([item],'vpn.example.test')),
            base64.b64decode(base64_subscription([item],'vpn.example.test')).decode(),
        )
        for output in outputs:
            self.assertIn('11111111-1111-1111-1111-111111111111',output)
            self.assertNotIn('/private/server',output)
        config=yaml.safe_load(outputs[0])
        proxy=config['proxies'][0]
        self.assertEqual(proxy['type'],'vless')
        self.assertTrue(proxy['tls'])
        self.assertFalse(proxy['skip-cert-verify'])
        self.assertEqual(proxy['servername'],'vpn.example.test')

    def test_trojan_three_outputs_preserve_password_tls_and_hide_server_paths(self):
        item=trojan_node()
        outputs=(
            mihomo_config([item],'trojan.example.test',{'mode':'direct'}),
            json.dumps(singbox_client_config([item],'trojan.example.test')),
            base64.b64decode(base64_subscription([item],'trojan.example.test')).decode(),
        )
        for output in outputs:
            self.assertIn('Tr0jan-pass',output)
            self.assertNotIn('/private/server',output)
        config=yaml.safe_load(outputs[0])
        proxy=config['proxies'][0]
        self.assertEqual(proxy['type'],'trojan')
        self.assertEqual(proxy['password'],'Tr0jan-pass:/?#[]@!$&()*+,;=')
        self.assertEqual(proxy['sni'],'trojan.example.test')
        self.assertTrue(proxy['tls'])
        self.assertFalse(proxy['skip-cert-verify'])
        client=singbox_client_config([item],'trojan.example.test')['outbounds'][0]
        self.assertEqual(client['type'],'trojan')
        self.assertEqual(client['password'],'Tr0jan-pass:/?#[]@!$&()*+,;=')
        self.assertEqual(client['tls']['server_name'],'trojan.example.test')

    def test_shadowsocks_three_outputs_for_validated_aead_methods(self):
        for method in ("aes-128-gcm","aes-256-gcm","chacha20-ietf-poly1305"):
            with self.subTest(method=method):
                item=ss_node(method)
                outputs=(
                    mihomo_config([item],'ss.example.test',{'mode':'direct'}),
                    json.dumps(singbox_client_config([item],'ss.example.test')),
                    base64.b64decode(base64_subscription([item],'ss.example.test')).decode(),
                )
                for output in outputs:
                    self.assertIn('shadowsocks-password-123',output if method not in output else output)
                    self.assertNotIn('/private/server',output)
                proxy=yaml.safe_load(outputs[0])['proxies'][0]
                self.assertEqual(proxy['type'],'ss')
                self.assertEqual(proxy['cipher'],method)
                self.assertEqual(proxy['password'],'shadowsocks-password-123')
                self.assertTrue(proxy['udp'])
                client=singbox_client_config([item],'ss.example.test')['outbounds'][0]
                self.assertEqual(client['type'],'shadowsocks')
                self.assertEqual(client['method'],method)
                self.assertEqual(client['password'],'shadowsocks-password-123')
                uri=base64.b64decode(base64_subscription([item],'ss.example.test')).decode()
                self.assertTrue(uri.startswith('ss://'))
                self.assertIn('#ss%20%2F%20%E4%B8%AD%E6%96%87',uri)

    def test_shadowsocks_rejects_unknown_method_fields_stream_and_password(self):
        candidates=[]
        candidates.append(ss_node("rc4-md5"))
        x=ss_node();x.settings['unknown']='x';candidates.append(x)
        x=ss_node();x.settings['password']='';candidates.append(x)
        x=ss_node();x.stream_settings={'transport':{'type':'ws'}};candidates.append(x)
        x=ss_node();x.stream_settings={'_vui':{'security':'none'}};candidates.append(x)
        for item in candidates:
            with self.subTest(item=item.__dict__),self.assertRaises(ExportError):
                validated_nodes([item],'ss.example.test')

    def test_vless_ipv6_uri_and_name_encoding(self):
        uri=share_link(vless_node(),'2001:db8::1')
        parsed=urlsplit(uri)
        self.assertEqual(parsed.hostname,'2001:db8::1')
        self.assertEqual(parsed.port,10443)
        self.assertIn('[2001:db8::1]',uri)
        self.assertNotIn('中文',uri)
        self.assertEqual(parse_qs(parsed.query)['security'],['tls'])

    def test_trojan_uri_encodes_password_ipv6_and_name(self):
        item=trojan_node()
        uri=share_link(item,'2001:db8::2')
        parsed=urlsplit(uri)
        self.assertEqual(parsed.scheme,'trojan')
        self.assertEqual(parsed.hostname,'2001:db8::2')
        self.assertEqual(parsed.port,11443)
        self.assertEqual(unquote(parsed.username),'Tr0jan-pass:/?#[]@!$&()*+,;=')
        self.assertEqual(parse_qs(parsed.query)['sni'],['trojan.example.test'])
        self.assertIn('[2001:db8::2]',uri)
        self.assertNotIn('中文',uri)

    def test_empty_disabled_and_expiring_never_fallback_to_direct(self):
        for rows in ([],[vless_node(enable=False)],[vless_node(expiry_time=10)]):
            for exporter in (mihomo_config,base64_subscription,singbox_client_config):
                with self.subTest(exporter=exporter.__name__),self.assertRaises(ExportError):
                    exporter(rows,'vpn.example.test')

    def test_unverified_protocol_or_core_is_explicitly_rejected(self):
        for change in (
            {'core':'xray'},
            {'protocol':'tuic'},
            {'protocol':'hysteria2'},
            {'protocol':'vmess'},
        ):
            with self.subTest(change=change),self.assertRaises(ExportError):
                validated_nodes([vless_node(**change)],'vpn.example.test')

    def test_vless_unknown_fields_multiuser_flow_and_non_tcp_are_rejected(self):
        candidates=[]
        x=vless_node();x.settings['unknown']='secret';candidates.append(x)
        x=vless_node();x.settings['users']*=2;candidates.append(x)
        x=vless_node();x.settings['users'][0]['flow']='xtls-rprx-vision';candidates.append(x)
        x=vless_node();x.stream_settings['transport']={'type':'ws','path':'/x'};candidates.append(x)
        x=vless_node();x.stream_settings['tls']['reality']={'private_key':'never-export'};candidates.append(x)
        for item in candidates:
            with self.subTest(item=item.__dict__),self.assertRaises(ExportError):
                validated_nodes([item],'vpn.example.test')

    def test_trojan_rejects_multiuser_missing_password_unknown_fields_non_tcp_and_insecure_tls(self):
        candidates=[]
        x=trojan_node();x.settings['users']*=2;candidates.append(x)
        x=trojan_node();x.settings['users'][0].pop('password');candidates.append(x)
        x=trojan_node();x.settings['users'][0]['unknown']='x';candidates.append(x)
        x=trojan_node();x.stream_settings['transport']={'type':'ws','path':'/x'};candidates.append(x)
        x=trojan_node();x.stream_settings['tls']['enabled']=False;candidates.append(x)
        x=trojan_node();x.stream_settings['_vui']={'security':'tls','skip_cert_verify':True};candidates.append(x)
        for item in candidates:
            with self.subTest(item=item.__dict__),self.assertRaises(ExportError):
                validated_nodes([item],'trojan.example.test')

    def test_tls_verification_missing_sni_and_conflicts_fail_for_both_profiles(self):
        for factory in (vless_node,trojan_node):
            candidates=[]
            x=factory();x.stream_settings['tls']['enabled']=False;candidates.append(x)
            x=factory();x.stream_settings['tls'].pop('server_name');candidates.append(x)
            x=factory();x.stream_settings['_vui']={'security':'tls','skip_cert_verify':True};candidates.append(x)
            x=factory();x.stream_settings['_vui']={'security':'tls','server_name':'other.test'};candidates.append(x)
            for item in candidates:
                with self.subTest(protocol=item.protocol),self.assertRaises(ExportError):
                    validated_nodes([item],'vpn.example.test')

    def test_supported_optional_tls_fields_preserved_for_vless_and_trojan(self):
        for factory in (vless_node,trojan_node):
            item=factory()
            item.stream_settings['_vui']={
                'client_fingerprint':'chrome',
                'security':'tls',
                'skip_cert_verify':False,
            }
            item.stream_settings['tls']['alpn']=['h2','http/1.1']
            proxy=validated_nodes([item],'vpn.example.test')[0]
            self.assertEqual(proxy['client-fingerprint'],'chrome')
            self.assertEqual(proxy['alpn'],['h2','http/1.1'])
            client=singbox_client_config([item],'vpn.example.test')['outbounds'][0]
            self.assertEqual(client['tls']['utls']['fingerprint'],'chrome')
            self.assertEqual(client['tls']['alpn'],['h2','http/1.1'])

    def test_reserved_names_and_credentials_do_not_mutate_input(self):
        for factory in (vless_node,trojan_node):
            item=factory(remark='DIRECT')
            before=deepcopy(item.__dict__)
            result=validated_nodes([item,item],'vpn.example.test')
            self.assertEqual([x['name'] for x in result],['DIRECT 2','DIRECT 3'])
            self.assertEqual(item.__dict__,before)


if __name__=='__main__':
    unittest.main()
