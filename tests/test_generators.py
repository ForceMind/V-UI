import unittest
from types import SimpleNamespace
from app.services.core_manager import SingBoxAdapter, XrayAdapter
from app.services.mihomo_subscription import mihomo_config
from app.services.validated_export import share_link


def inbound(**kwargs):
    data={'id':1,'core':'sing-box','remark':'demo','port':443,'protocol':'vless',
          'settings':{'users':[{'uuid':'11111111-1111-1111-1111-111111111111'}]},
          'stream_settings':{'tls':{'enabled':True,'server_name':'example.com'}},'enable':True,'tag':'demo'}
    data.update(kwargs)
    return SimpleNamespace(**data)

class ConfigGeneratorTests(unittest.TestCase):
    def test_xray_config(self):
        config=XrayAdapter().build_config([inbound(core='xray',settings={'clients':[{'id':'11111111-1111-1111-1111-111111111111'}],'decryption':'none'},stream_settings={})])
        self.assertEqual(config['inbounds'][0]['protocol'],'vless')
        self.assertEqual(config['inbounds'][0]['port'],443)
        self.assertIn('clients',config['inbounds'][0]['settings'])
    def test_singbox_config(self):
        item=inbound(core='sing-box',protocol='hysteria2',settings={'users':[{'password':'secret'}]},
            stream_settings={'tls':{'enabled':True,'certificate_path':'/tmp/cert.pem','key_path':'/tmp/key.pem'}})
        config=SingBoxAdapter().build_config([item])
        self.assertEqual(config['inbounds'][0]['type'],'hysteria2')
        self.assertEqual(config['inbounds'][0]['users'][0]['password'],'secret')
    def test_vless_share_link(self):
        link=share_link(inbound(),'example.com')
        self.assertTrue(link.startswith('vless://'));self.assertIn('@example.com:443',link)
    def test_mihomo_output(self):
        output=mihomo_config([inbound()],'example.com')
        self.assertIn('vless',output);self.assertIn('example.com',output);self.assertIn('FORCE_PROXY',output)

if __name__=='__main__': unittest.main()
