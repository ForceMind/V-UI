import base64
from copy import deepcopy
import json
from types import SimpleNamespace
import unittest
from urllib.parse import urlsplit,parse_qs
import yaml
from app.services.validated_export import ExportError,validated_nodes,share_link,base64_subscription,singbox_client_config
from app.services.mihomo_subscription import mihomo_config

def node(**changes):
    value=dict(id=1,core='sing-box',protocol='vless',port=10443,remark='demo / 中文',enable=True,
        settings={'users':[{'uuid':'11111111-1111-1111-1111-111111111111'}]},
        stream_settings={'tls':{'enabled':True,'server_name':'vpn.example.test','certificate_path':'/private/server/cert.pem','key_path':'/private/server/key.pem'}})
    value.update(changes)
    return SimpleNamespace(**value)

def trojan_node(**changes):
    value=dict(id=2,core='sing-box',protocol='trojan',port=10444,remark='trojan / 中文',enable=True,
        settings={'users':[{'password':'Tr0jan-pass:/?#[]@!def node(**changes):
    value=dict(id=1,core='sing-box',protocol='vless',port=10443,remark='demo / 中文',enable=True,
        settings={'users':[{'uuid':'11111111-1111-1111-1111-111111111111'}]},
        stream_settings={'tls':{'enabled':True,'server_name':'vpn.example.test','certificate_path':'/private/server/cert.pem','key_path':'/private/server/key.pem'}})
    value.update(changes)
    return SimpleNamespace(**value)
()*+,;='}]},
        stream_settings={'tls':{'enabled':True,'server_name':'vpn.example.test',
            'certificate_path':'/private/server/cert.pem','key_path':'/private/server/key.pem'}})
    value.update(changes)
    return SimpleNamespace(**value)


def trojan_node(**changes):
    value=dict(id=2,core='sing-box',protocol='trojan',port=11443,remark='trojan demo / 中文',enable=True,
        settings={'users':[{'password':'trojan-password-123'}]},
        stream_settings={'tls':{'enabled':True,'server_name':'trojan.example.test',
            'certificate_path':'/private/server/trojan-cert.pem','key_path':'/private/server/trojan-key.pem'}})
    value.update(changes)
    return SimpleNamespace(**value)

class ValidatedExportTests(unittest.TestCase):
    def test_three_outputs_preserve_connection_fields_without_server_paths(self):
        item=node()
        for output in (mihomo_config([item],'vpn.example.test',{'mode':'direct'}),json.dumps(singbox_client_config([item],'vpn.example.test')),base64.b64decode(base64_subscription([item],'vpn.example.test')).decode()):
            self.assertIn('11111111-1111-1111-1111-111111111111',output)
            self.assertNotIn('/private/server',output)
        config=yaml.safe_load(mihomo_config([item],'vpn.example.test',{'mode':'direct'}))
        self.assertTrue(config['proxies'][0]['tls'])
        self.assertFalse(config['proxies'][0]['skip-cert-verify'])
        self.assertEqual(config['proxies'][0]['servername'],'vpn.example.test')
    def test_trojan_three_outputs_preserve_password_tls_and_hide_server_paths(self):
        item=trojan_node()
        outputs=(
            mihomo_config([item],'vpn.example.test',{'mode':'direct'}),
            json.dumps(singbox_client_config([item],'vpn.example.test')),
            base64.b64decode(base64_subscription([item],'vpn.example.test')).decode(),
        )
        for output in outputs:
            self.assertIn('Tr0jan-pass',output)
            self.assertNotIn('/private/server',output)
        config=yaml.safe_load(outputs[0])
        proxy=config['proxies'][0]
        self.assertEqual(proxy['type'],'trojan')
        self.assertEqual(proxy['password'],'Tr0jan-pass:/?#[]@!    def test_trojan_three_outputs_preserve_password_and_hide_server_paths(self):
        item=trojan_node()
        outputs=(
            mihomo_config([item],'trojan.example.test',{'mode':'direct'}),
            json.dumps(singbox_client_config([item],'trojan.example.test')),
            base64.b64decode(base64_subscription([item],'trojan.example.test')).decode(),
        )
        for output in outputs:
            self.assertIn('trojan-password-123',output)
            self.assertNotIn('/private/server',output)
        config=yaml.safe_load(outputs[0])
        proxy=config['proxies'][0]
        self.assertEqual(proxy['type'],'trojan')
        self.assertEqual(proxy['password'],'trojan-password-123')
        self.assertTrue(proxy['tls'])
        self.assertFalse(proxy['skip-cert-verify'])
        self.assertEqual(proxy['sni'],'trojan.example.test')
        client=singbox_client_config([item],'trojan.example.test')['outbounds'][0]
        self.assertEqual(client['type'],'trojan')
        self.assertEqual(client['password'],'trojan-password-123')
        self.assertEqual(client['tls']['server_name'],'trojan.example.test')
        uri=base64.b64decode(base64_subscription([item],'trojan.example.test')).decode()
        self.assertTrue(uri.startswith('trojan://trojan-password-123@'))
        self.assertIn('sni=trojan.example.test',uri)

    def test_trojan_rejects_multiuser_unknown_fields_non_tcp_and_insecure_tls(self):
        candidates=[]
        x=trojan_node();x.settings['users']*=2;candidates.append(x)
        x=trojan_node();x.settings['users'][0]['unknown']='secret';candidates.append(x)
        x=trojan_node();x.settings['users'][0]['password']='';candidates.append(x)
        x=trojan_node();x.stream_settings['transport']={'type':'ws','path':'/trojan'};candidates.append(x)
        x=trojan_node();x.stream_settings['tls']['enabled']=False;candidates.append(x)
        x=trojan_node();x.stream_settings['_vui']={'security':'tls','skip_cert_verify':True};candidates.append(x)
        for item in candidates:
            with self.subTest(item=item.__dict__),self.assertRaises(ExportError):
                validated_nodes([item],'trojan.example.test')

    def test_ipv6_uri_and_name_encoding(self):
()*+,;=')
        self.assertEqual(proxy['sni'],'vpn.example.test')
        self.assertTrue(proxy['tls'])
        self.assertFalse(proxy['skip-cert-verify'])
        client=singbox_client_config([item],'vpn.example.test')['outbounds'][0]
        self.assertEqual(client['type'],'trojan')
        self.assertEqual(client['tls']['server_name'],'vpn.example.test')

    def test_trojan_uri_encodes_password_ipv6_and_name(self):
        uri=share_link(trojan_node(),'2001:db8::2')
        parsed=urlsplit(uri)
        self.assertEqual(parsed.scheme,'trojan')
        self.assertEqual(parsed.hostname,'2001:db8::2')
        self.assertEqual(parsed.port,10444)
        self.assertIn('[2001:db8::2]',uri)
        self.assertNotIn('中文',uri)
        self.assertIn('sni=vpn.example.test',uri)
        self.assertIn('Tr0jan-pass%3A%2F%3F%23%5B%5D%40%21%24%26%28%29%2A%2B%2C%3B%3D',uri)

    def test_trojan_rejects_multiuser_missing_password_transport_and_insecure_tls(self):
        candidates=[]
        x=trojan_node();x.settings['users']*=2;candidates.append(x)
        x=trojan_node();x.settings['users'][0].pop('password');candidates.append(x)
        x=trojan_node();x.settings['users'][0]['unknown']='x';candidates.append(x)
        x=trojan_node();x.stream_settings['transport']={'type':'ws','path':'/x'};candidates.append(x)
        x=trojan_node();x.stream_settings['_vui']={'skip_cert_verify':True};candidates.append(x)
        for item in candidates:
            with self.assertRaises(ExportError):
                validated_nodes([item],'vpn.example.test')

    def test_ipv6_uri_and_name_encoding(self):
        uri=share_link(node(),'2001:db8::1');parsed=urlsplit(uri)
        self.assertEqual(parsed.hostname,'2001:db8::1');self.assertEqual(parsed.port,10443)
        self.assertIn('[2001:db8::1]',uri);self.assertNotIn('中文',uri)
        self.assertEqual(parse_qs(parsed.query)['security'],['tls'])
    def test_empty_disabled_and_expiring_never_fallback_to_direct(self):
        for rows in ([],[node(enable=False)],[node(expiry_time=10)]):
            for exporter in (mihomo_config,base64_subscription,singbox_client_config):
                with self.subTest(exporter=exporter.__name__),self.assertRaises(ExportError): exporter(rows,'vpn.example.test')
    def test_unsupported_protocol_or_core_is_explicitly_rejected(self):
        for change in ({'core':'xray'},{'protocol':'tuic'},{'protocol':'hysteria2'},{'protocol':'vmess'},{'protocol':'shadowsocks'}):
            with self.subTest(change=change),self.assertRaises(ExportError): validated_nodes([node(**change)],'vpn.example.test')
    def test_unknown_fields_multiuser_and_flow_are_rejected(self):
        candidates=[]
        x=node();x.settings['unknown']='secret';candidates.append(x)
        x=node();x.settings['users']*=2;candidates.append(x)
        x=node();x.settings['users'][0]['flow']='xtls-rprx-vision';candidates.append(x)
        x=node();x.stream_settings['transport']={'type':'ws','path':'/x'};candidates.append(x)
        x=node();x.stream_settings['tls']['reality']={'private_key':'never-export'};candidates.append(x)
        for item in candidates:
            with self.assertRaises(ExportError): validated_nodes([item],'vpn.example.test')
    def test_tls_verification_missing_sni_and_conflicts_fail(self):
        candidates=[]
        x=node();x.stream_settings['tls']['enabled']=False;candidates.append(x)
        x=node();x.stream_settings['tls'].pop('server_name');candidates.append(x)
        x=node();x.stream_settings['_vui']={'skip_cert_verify':True};candidates.append(x)
        x=node();x.stream_settings['_vui']={'server_name':'other.test'};candidates.append(x)
        for item in candidates:
            with self.assertRaises(ExportError): validated_nodes([item],'vpn.example.test')
    def test_supported_optional_tls_fields_preserved(self):
        item=node();item.stream_settings['_vui']={'client_fingerprint':'chrome','security':'tls','skip_cert_verify':False}
        item.stream_settings['tls']['alpn']=['h2','http/1.1']
        proxy=validated_nodes([item],'vpn.example.test')[0]
        self.assertEqual(proxy['client-fingerprint'],'chrome');self.assertEqual(proxy['alpn'],['h2','http/1.1'])
        client=singbox_client_config([item],'vpn.example.test')['outbounds'][0]
        self.assertEqual(client['tls']['utls']['fingerprint'],'chrome');self.assertEqual(client['tls']['alpn'],['h2','http/1.1'])
    def test_reserved_names_and_credentials_do_not_mutate_input(self):
        item=node(remark='DIRECT');before=deepcopy(item.__dict__)
        result=validated_nodes([item,item],'vpn.example.test')
        self.assertEqual([x['name'] for x in result],['DIRECT 2','DIRECT 3']);self.assertEqual(item.__dict__,before)

if __name__=='__main__': unittest.main()
