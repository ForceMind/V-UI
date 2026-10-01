"""Independent TypeScript oracle is generated from a pinned ToClash checkout."""
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
import yaml
from app.services.mihomo_subscription import mihomo_config
from app.services.mihomo_routing import RULE_PRESETS
from app.services.routing_validation import build_rule_plan

@unittest.skipUnless(os.getenv('VUI_TOCLASH_FIXTURES'),'fixed ToClash oracle not provided')
class ToClashParityTests(unittest.TestCase):
    def test_100_reference_cases_semantics_and_order(self):
        data=json.loads(Path(os.environ['VUI_TOCLASH_FIXTURES']).read_text())
        self.assertEqual(data['commit'],'95a5c71a516c10f97f47bfb771018ce890b2b570')
        self.assertEqual(len(data['cases']),100)
        for case in data['cases']:
            with self.subTest(case=case['id']):
                settings={ {'directDomains':'direct_domains','proxyDomains':'proxy_domains','bypassCgnat':'bypass_cgnat'}.get(k,k):v for k,v in case['routing'].items() }
                rows=[SimpleNamespace(id=i+1,core='sing-box',protocol='vless',port=10001+i,remark=name,enable=True,
                    settings={'users':[{'uuid':'11111111-1111-1111-1111-111111111111'}]},
                    stream_settings={'tls':{'enabled':True,'server_name':'vpn.example.test'}}) for i,name in enumerate(case['names'])]
                if case.get('error'):
                    with self.assertRaises(ValueError): mihomo_config(rows,'vpn.example.test',settings)
                    continue
                output=yaml.safe_load(mihomo_config(rows,'vpn.example.test',settings))
                self.assertEqual(output,case['output'])
                for key in ('nameserver-policy','proxy-server-nameserver-policy'):
                    self.assertEqual(list(output['dns'][key].items()),list(case['output']['dns'][key].items()))
                self.assertEqual(build_rule_plan(settings)['warnings'],case['warnings'])
        print('ToClash parity: all 100 fixed reference cases matched')

    def test_entire_catalog_matches_reference(self):
        data=json.loads(Path(os.environ['VUI_TOCLASH_FIXTURES']).read_text())
        normalized=[{'id':p['id'],'category':p['category'],'nameZh':p['name_zh'],'nameEn':p['name_en'],
            'defaultEnabled':p['default_enabled'],'rules':p['rules']} for p in RULE_PRESETS]
        self.assertEqual(normalized,data['catalog'])
        self.assertEqual(len(normalized),40)

if __name__=='__main__': unittest.main()
