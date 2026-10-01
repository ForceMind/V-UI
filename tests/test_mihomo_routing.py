import unittest
from types import SimpleNamespace
import yaml
from app.services.mihomo_routing import default_routing,unique_proxy_names
from app.services.routing_validation import build_rule_plan,normalize_routing
from app.services.mihomo_subscription import mihomo_config


def inbound():
    return SimpleNamespace(id=1,core='sing-box',protocol='vless',remark='demo',port=443,enable=True,tag='demo',
        settings={'users':[{'uuid':'11111111-1111-1111-1111-111111111111'}]},
        stream_settings={'tls':{'enabled':True,'server_name':'example.com'}})

def flatten_rules(plan): return [r for s in plan['sections'] for r in s['rules']]

class MihomoRoutingTests(unittest.TestCase):
    def test_standard_mode_matches_toclash_group_and_rule_semantics(self):
        routing=default_routing();plan=build_rule_plan(routing);rules=flatten_rules(plan)
        self.assertEqual(rules[0],'DOMAIN-SUFFIX,localhost,DIRECT')
        self.assertIn('DOMAIN-SUFFIX,openai.com,FORCE_PROXY',rules)
        self.assertEqual(rules[rules.index('DOMAIN-SUFFIX,openai.com,FORCE_PROXY')+1],'DOMAIN-SUFFIX,openai.com,REJECT')
        self.assertIn('DOMAIN-SUFFIX,github.com,PROXY',rules);self.assertIn('DOMAIN-SUFFIX,google.com,PROXY',rules)
        self.assertIn('GEOSITE,CN,DIRECT',rules);self.assertIn('GEOIP,CN,DIRECT',rules)
        self.assertEqual(rules[-1],'MATCH,PROXY')
        self.assertEqual(plan['dns']['nameserver-policy']['+.openai.com'],['https://1.1.1.1/dns-query#FORCE_PROXY','https://8.8.8.8/dns-query#FORCE_PROXY'])
        groups={g['name']:g for g in yaml.safe_load(mihomo_config([inbound()],'example.com',routing))['proxy-groups']}
        self.assertEqual(set(groups),{'PROXY','AUTO','FORCE_PROXY'})
        self.assertIn('AUTO',groups['PROXY']['proxies']);self.assertIn('DIRECT',groups['PROXY']['proxies'])
        self.assertNotIn('DIRECT',groups['FORCE_PROXY']['proxies'])
    def test_direct_mode_only_proxies_selected_services(self):
        routing=default_routing();routing['mode']='direct';rules=flatten_rules(build_rule_plan(routing))
        self.assertNotIn('GEOSITE,CN,DIRECT',rules);self.assertNotIn('GEOIP,CN,DIRECT',rules)
        self.assertEqual(rules[-1],'MATCH,DIRECT')
        self.assertIn('DOMAIN-SUFFIX,github.com,FORCE_PROXY',rules);self.assertIn('DOMAIN-SUFFIX,google.com,FORCE_PROXY',rules)
        self.assertIn('DOMAIN,cdn.openaimerge.com,FORCE_PROXY',rules)
        output=yaml.safe_load(mihomo_config([inbound()],'example.com',routing))
        self.assertEqual([g['name'] for g in output['proxy-groups']],['FORCE_PROXY'])
        self.assertEqual(output['dns']['nameserver'],['system'])
    def test_custom_direct_parent_overrides_proxy_child(self):
        plan=build_rule_plan({'direct_domains':['example.com'],'proxy_domains':['api.example.com']});rules=flatten_rules(plan)
        self.assertIn('DOMAIN-SUFFIX,example.com,DIRECT',rules);self.assertNotIn('DOMAIN-SUFFIX,api.example.com,FORCE_PROXY',rules)
        self.assertEqual({w['code']:w['count'] for w in plan['warnings']}.get('DIRECT_OVERRIDE'),1)
    def test_intranet_dns_is_used_for_business_and_proxy_bootstrap(self):
        routing=normalize_routing({'intranet':[{'suffix':'corp.example','nameservers':['192.0.2.53']}]})
        plan=build_rule_plan(routing)
        self.assertIn('DOMAIN-SUFFIX,corp.example,DIRECT',flatten_rules(plan))
        for key in ('nameserver-policy','proxy-server-nameserver-policy'):
            self.assertEqual(plan['dns'][key]['+.corp.example'],['udp://192.0.2.53:53'])
        self.assertIn('+.corp.example',plan['dns']['fake-ip-filter'])
    def test_reserved_and_duplicate_proxy_names_are_stable(self):
        proxies=unique_proxy_names([{'name':'PROXY'},{'name':'demo'},{'name':'demo'}])
        self.assertEqual([p['name'] for p in proxies],['PROXY 2','demo','demo 2'])

if __name__=='__main__': unittest.main()
