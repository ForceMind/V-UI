"""Explicit opt-in seven-case diagnostic contract; original C/D/B defaults stay frozen."""
from copy import deepcopy
import json
from pathlib import Path
import runtime as r

ARMS = {'stock': ('I',), 'candidate': ('E', 'I'), 'overseas-stock': ('I',)}
KIND = 'vui-stock-client-udp-finite-diagnostic'

def plan(p, arm, stage):
    r.need(arm in ARMS and stage in ARMS[arm], 'Unknown diagnostic arm/stage')
    roles={'domestic':'official' if arm=='stock' else 'candidate',
           'overseas':'candidate' if arm=='candidate' else 'official','device-mihomo':'mihomo'}
    def case(name, packets, mode='packets'):
        return {'id':name,'stage':stage,'protocol':'vless','roles':deepcopy(roles),'mode':mode,'packets':packets}
    if stage=='E':
        return [case(f'E_vless_first_{direction}_{size}',[p.packet('first',size,direction=direction)])
                for direction,sizes in [('uplink',(16374,16375)),('downlink',(16384,16385))] for size in sizes]
    return [case('I_vless_small_idle',[p.packet('first',1200),p.packet('recover',1200)],'idle_diagnostic')]

def verify(p, case, row, roles, known, output, identity, base_verify):
    verdict=base_verify(p,case,row,roles,known,output,identity)
    verdict['original_suite_passed']=False
    if case['stage']=='E': return verdict
    import diagnostic_verify as v
    diagnostic=v.verify(p,case,row,roles,output,identity)
    verdict['diagnostic']=diagnostic
    verdict['case_gate_passed']=bool(verdict['case_gate_passed'] and diagnostic['release_passed'] and diagnostic['next_packet_passed'])
    return verdict

def install():
    """Each diagnostic CLI explicitly installs this fixed contract in its own process."""
    import evidence as e
    r.need(r.KIND != KIND, 'Diagnostic contract installed twice')
    original=r.verify_case
    r.ARMS=ARMS; r.KIND=KIND; r.plan=plan
    r.verify_case=lambda *args: verify(*args,base_verify=original)
    e.KIND=KIND+'-archive'
    original_files=e.stage_files
    def stage_files(p,arm,stage):
        files=original_files(p,arm,stage)
        if stage=='I':
            for c in plan(p,arm,stage):
                for name in ('lifecycle-observation.json','socks-association-observation.json'):
                    files[c['id']+'/'+name]='json'
        return files
    e.stage_files=stage_files
