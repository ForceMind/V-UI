// Run inside the separately checked-out, fixed ToClash repository.
// This oracle never imports V-UI's Python implementation.
import { execFileSync } from 'node:child_process'
import { copyFileSync, existsSync, mkdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { buildMihomoConfig } from './src/core/transformer/mihomo'
import { buildRulePlan } from './src/core/rules/plan'
import { RULE_PRESETS } from './src/core/rules/presets'

const pin = '95a5c71a516c10f97f47bfb771018ce890b2b570'
if (execFileSync('git', ['rev-parse', 'HEAD'], {encoding:'utf8'}).trim() !== pin) throw new Error('Wrong ToClash commit')
const out = process.env.VUI_REFERENCE_OUT!
mkdirSync(out, {recursive:true})
const disabled = Object.fromEntries(RULE_PRESETS.map(p => [p.id, false]))
const enabled = Object.fromEntries(RULE_PRESETS.map(p => [p.id, true]))
const cases: any[] = [
  {id:'standard-default', routing:{}},
  {id:'direct-default', routing:{mode:'direct'}},
  {id:'direct-none', routing:{mode:'direct',presets:disabled}},
  {id:'standard-all', routing:{presets:enabled}},
  {id:'direct-all', routing:{mode:'direct',presets:enabled}},
  {id:'direct-parent', routing:{directDomains:['example.com'],proxyDomains:['api.example.com']}},
  {id:'direct-child', routing:{directDomains:['api.example.com'],proxyDomains:['example.com']}},
  {id:'intranet', routing:{intranet:[{suffix:'corp.example',nameservers:['192.0.2.53']},{suffix:'api.corp.example',nameservers:['192.0.2.54:5353']}],proxyDomains:['example']}},
  {id:'intranet-system', routing:{mode:'direct',intranet:[{suffix:'corp.example',nameservers:['system']}]}},
  {id:'local-overlap', routing:{directDomains:['localhost'],proxyDomains:['local','api.local','10.0.0.1','::1','lan']}},
  {id:'cgnat-on', routing:{bypassCgnat:true,proxyDomains:['100.64.0.1','100.128.0.1']}},
  {id:'cgnat-off', routing:{bypassCgnat:false,proxyDomains:['100.64.0.1']}},
  {id:'ip-and-url', routing:{directDomains:['https://192.0.2.10/path','[2001:db8::1]'],proxyDomains:['wss://app.example.net/path','https://[2001:db8::2]:443/']}},
  {id:'normalization', routing:{directDomains:['EXAMPLE.COM.','*.example.com','https://example.com/path'],proxyDomains:['例子.测试','198.51.100.1']}},
  {id:'reserved-names', names:['PROXY','demo','demo','DIRECT'],routing:{mode:'direct'}},
]
for (const preset of RULE_PRESETS) for (const mode of ['standard','direct']) {
  cases.push({id:`${mode}-${preset.id}`,routing:{mode,presets:{...disabled,[preset.id]:true}}})
}
for (const value of ['bad domain','x.com,DIRECT','https://user:password@example.com','bad\\host','https://example.com:99999']) {
  cases.push({id:`invalid-${cases.length}`,routing:{proxyDomains:[value]}})
}
for (const fixture of cases) {
  fixture.names ??= ['demo']
  const nodes = fixture.names.map((name: string, i: number) => ({name,type:'vless',server:'vpn.example.test',port:10001+i,
    uuid:'11111111-1111-1111-1111-111111111111',udp:true,tls:true,servername:'vpn.example.test',
    network:'tcp',packetEncoding:'xudp',skipCertVerify:false}))
  try {
    fixture.output = buildMihomoConfig(nodes as any, true, fixture.routing)
    fixture.warnings = buildRulePlan(fixture.routing).warnings
  } catch {
    fixture.error = true
  }
}
writeFileSync(join(out,'fixtures.json'), JSON.stringify({commit:pin,version:'0.3.8',catalog:RULE_PRESETS,cases},null,2))
copyFileSync('LICENSE',join(out,'ToClash-LICENSE'))
if (existsSync('NOTICE')) copyFileSync('NOTICE',join(out,'ToClash-NOTICE'))
execFileSync('git',['archive','--format=zip','HEAD','--output='+join(out,'toclash-source.zip')])
console.log(`ToClash fixed reference ${pin}: ${cases.length} cases, ${RULE_PRESETS.length} presets`)
