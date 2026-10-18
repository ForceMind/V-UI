#!/usr/bin/env python3
"""Finite off six-stage then owner-on C/D driver using the original owned-stage runner."""
from __future__ import annotations
import argparse
from pathlib import Path
import runtime_adapter as a
import verify_runtime as v


def outcome_projection(report):
    return {'diagnostic_complete':report['diagnostic_complete'], 'stage_capacity_passed':report['stage_capacity_passed'],
            'capacity_failed_leaves':report.get('capacity_failed_leaves',[]),
            'cases':[{'case':c['case'],'status':c['status'],'unexecuted_leaves':c['unexecuted_leaves'],
                      'packets':[{k:r[k] for k in ('leaf','status','full_delivery_passed','failure_kind') if k in r}
                                 for r in c['packets']]} for c in report['cases']]}

def make_manifest(rt,build,official,arm,identity):
    p=rt.p; build=Path(build).resolve(); official=Path(official).resolve()
    provenance=build/'provenance.json'; data=p.json_read(provenance); candidate=build/'owner-sing-box'
    return {'schema_version':1,'kind':a.KIND+'-input','source_identity':identity,'arm':arm,
            'official':{'path':str(official),'sha256':p.sha(official),'version':'1.14.2'},
            'candidate':{'path':str(candidate),'sha256':p.sha(candidate),'version':data['version'],
                         'provenance_path':str(provenance),'provenance_sha256':p.sha(provenance)},
            'build_evidence':{'path':str(build/'evidence'),'files':a.evidence_files(build/'evidence')}}

def run_arm(rt,output,manifest_path,arm,*,runner=None,safety_checker=None,failure_checker=None,socks_checker=None,additional_checker=None):
    p=rt.p; output=Path(output); manifest=p.json_read(manifest_path); binaries={k:manifest[k] for k in ('official','candidate')}
    a.need(manifest['arm']==arm and rt.runner.STAGES==a.ARMS[arm],'Arm/input/coordinator mismatch')
    base_runner=runner or rt.runner.run_stage; base_safety=safety_checker or rt.safety.verify
    def execute(command):
        command=list(command); a.need(Path(command[1]).name=='packet_boundary.py','Unexpected legacy stage command')
        command[1]=str(a.HERE/'runtime_adapter.py'); command+=['--arm',arm]
        return base_runner(command)
    def safety(directory,stage,execution,expected_input,expected_sha):
        checked=base_safety(directory,stage,execution,expected_input,expected_sha)
        roles=p.json_read(Path(directory)/'process-binaries.json')
        for case in p.plan(stage): v.verify_roles(case,[r for r in roles if r['case']==case['id']],binaries,arm)
        return checked
    rows=rt.runner.run_stages(output,manifest_path,runner=execute,safety_checker=safety,
            failure_checker=failure_checker or rt.runner.check_failure_observations,
            socks_checker=socks_checker or rt.socks.verify_stage)
    frozen=p.json_read(a.HERE/'OFF_OUTCOMES.json')
    for row in rows:
        if not row['stage_execution_safe']: continue
        stage=row['stage']; directory=output/stage; report=p.json_read(directory/'result.json')
        try:
            row['owner_verification']=(additional_checker or v.verify_stage)(rt,directory,stage,report,arm,binaries)
            if arm=='off':
                a.need(outcome_projection(report)==frozen['stages'][stage]['outcomes'],'Frozen off diagnostic classes changed')
                row['baseline_classification_preserved']=True
                if stage=='D': row['historical_D']=v.verify_off_idle_red(rt,directory,report)
            row['additional_verification_passed']=True
        except (OSError,ValueError,KeyError,TypeError,IndexError) as exc:
            row['additional_verification_passed']=False
            row['additional_verification_error']={'type':type(exc).__name__,'message':str(exc)}
    p.save(output/'stages.json',rows)
    return rows

def summarize(arms,source):
    expected=[(arm,stage) for arm,stages in a.ARMS.items() for stage in stages]
    executed=[(arm,row['stage']) for arm,rows in arms.items() for row in rows]
    complete=executed==expected
    safe=all(row.get('stage_execution_safe') is True for rows in arms.values() for row in rows)
    additional=all(row.get('additional_verification_passed') is True for rows in arms.values() for row in rows)
    baseline=len(arms.get('off',[]))==6 and all(r.get('baseline_classification_preserved') is True for r in arms['off'])
    on=len(arms.get('on',[]))==2 and all(r.get('stage_gate_passed') is True for r in arms['on'])
    return {'schema_version':1,'kind':a.RUNTIME_KIND,'source_identity':source,'runtime_executed':True,
            'all_stages_executed':complete,'all_executed_stages_resource_safe':safe,
            'finite_experiment_contract_passed':complete and safe and additional and baseline and on,
            'candidate_off_baseline_preserved':baseline,'owner_on_original_C_D_passed':on,
            'unexecuted_stages':[{'arm':arm,'stage':stage} for arm,stage in expected if (arm,stage) not in executed],
            'original_suite_passed':False,'product_qualified':False,'device_whole_window_continuity_proven':False,
            'official_original_gate_unchanged':True,'arms':arms}

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository',type=Path,required=True);parser.add_argument('--build',type=Path,required=True)
    parser.add_argument('--official',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(argv); args.output.mkdir(parents=True,exist_ok=False); arms={}; source=None
    for arm in a.ARMS:
        rt=a.install(a.load_runtime(args.repository),arm); current=rt.h.identity()
        a.need(source is None or source==current,'Exact source changed between arms'); source=current
        manifest=make_manifest(rt,args.build,args.official,arm,source); path=args.output/(arm+'-input.json')
        rt.p.save(path,manifest); a.validate_manifest(rt,manifest,source,rt.repo,execute=False)
        directory=args.output/arm;directory.mkdir()
        arms[arm]=run_arm(rt,directory,path,arm)
        result=summarize(arms,source);rt.p.save(args.output/'RUNTIME_RESULT.json',result)
        if len(arms[arm])!=len(a.ARMS[arm]) or not all(r['stage_execution_safe'] for r in arms[arm]): break
    return 0 if result['finite_experiment_contract_passed'] else 1

if __name__=='__main__':raise SystemExit(main())
