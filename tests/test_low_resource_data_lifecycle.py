"""Independent, root-free lifecycle accounting and original operation controls."""
import ast
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts import low_resource_acceptance as gate
from scripts import low_resource_data as data
from test_low_resource_data import COMMIT, DATA_STAGE_NAMES, UNIT, complete_data_report


NAMES=(
    'before_current_snapshot','after_current_snapshot','before_installed_bad_digest_restore',
    'after_installed_bad_digest_rejection_before_preservation_check',
    'after_bad_digest_preservation_check','before_installed_restore','after_installed_restore',
    'before_recovery_verification','after_recovery_verification')
MUTATE='mutate_large_data_after_backup'
BAD='large_bad_digest_restore_preserves_current_data'
RESTORE='stopped_restore'
VERIFY='large_business_logs_revocation_and_original_data_retained'


def rows(report):
    return report['large_data']['lifecycle_boundaries']


def stage(report,name):
    return next(item for item in report['stages'] if item['name']==name)


def compact(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


def pad_row(row,target):
    """Reach a byte boundary using only otherwise-valid bounded kernel keys."""
    values=row['metrics']['memory.stat'];index=0;last=None
    while target-len(compact(row))>133:
        last=f'k{index:03d}'+('x'*124);values[last]=0;index+=1
    remaining=target-len(compact(row))
    if 0<remaining<6:
        values.pop(last);last=last[:-6];values[last]=0
        remaining=target-len(compact(row))
    if remaining:values['p'+('x'*(remaining-6))]=0
    if len(compact(row))!=target or len(values)>128:
        raise AssertionError('Invalid bounded padding fixture')


def prefix_report(count):
    """A genuine stopped prefix, with no fictional future stage timestamps."""
    report=complete_data_report();report.update(outcome='failed',complete=False)
    report['large_data']['lifecycle_boundaries']=rows(report)[:count]
    last=MUTATE if count<=2 else BAD if count<=5 else RESTORE if count<=7 else VERIFY
    index=next(i for i,item in enumerate(report['stages']) if item['name']==last)
    report['stages']=report['stages'][:index+1]
    if count not in (1,2):report['stages'][-1]['outcome']='failed'
    return report


class LifecycleValidationTests(unittest.TestCase):
    def assert_invalid(self,report):
        with self.assertRaises(RuntimeError):data.validate_lifecycle_boundaries(report,UNIT,COMMIT)
        with self.assertRaises(RuntimeError):data.validate_complete(report,UNIT,COMMIT)

    def test_fixed_contract_and_nonzero_max_are_valid(self):
        self.assertEqual(data.LIFECYCLE_BOUNDARIES,NAMES)
        self.assertEqual(data.LIFECYCLE_LIMITS,{'max_rows':9,'max_row_bytes':16384,'max_total_bytes':131072})
        self.assertEqual(data.LOG_BYTES,256*1024*1024)
        self.assertEqual(data.NODE_COUNT,1000)
        self.assertEqual(data.BACKUP_BOUNDARIES,('before_installed_backup',
            'after_installed_backup_before_verification','after_archive_verification'))
        report=complete_data_report()
        self.assertEqual(len(report['stages']),45)
        self.assertEqual(tuple(item['name'] for item in report['stages']),DATA_STAGE_NAMES)
        self.assertTrue(all(previous['started_monotonic']+previous['wall_seconds']<=following['started_monotonic']
            for previous,following in zip(report['stages'],report['stages'][1:])))
        self.assertGreater(rows(report)[0]['metrics']['memory.events']['max'],0)
        data.validate_lifecycle_boundaries(report,UNIT,COMMIT)
        data.validate_complete(report,UNIT,COMMIT)

    def test_header_identity_and_contract_cannot_be_relabelled(self):
        changes=(('duration_profile','smoke'),('unit','other.service'),('source_commit','c'*40),
            ('requested_memory_mib',384),('service_cgroup','0::/other/'+UNIT))
        for key,value in changes:
            report=complete_data_report();report[key]=value
            with self.subTest(key=key):self.assert_invalid(report)
        for key,value in (('lifecycle_boundary_schema',2),('lifecycle_boundary_schema',True),
                ('lifecycle_boundary_limits',{'max_rows':10,'max_row_bytes':16384,'max_total_bytes':131072}),
                ('lifecycle_boundary_scope','observer overhead removed')):
            report=complete_data_report();report['large_data'][key]=value
            with self.subTest(key=key,value=value):self.assert_invalid(report)
        for key in ('lifecycle_boundary_schema','lifecycle_boundary_limits','lifecycle_boundary_scope','lifecycle_boundaries'):
            report=complete_data_report();del report['large_data'][key]
            with self.subTest(missing=key):self.assert_invalid(report)

    def test_service_limits_and_bound_header_types_are_exact(self):
        for key,values in (('memory.max',(True,512*1024*1024+1,'536870912')),
                ('memory.swap.max',(True,1,'0')),
                ('cpu.max',('max 100000','200000 100000','0 0','0100000 0100000','1 2',None))):
            for value in values:
                report=complete_data_report();report['limits'][key]=value
                with self.subTest(key=key,value=value):self.assert_invalid(report)
        for key in ('max_rows','max_row_bytes','max_total_bytes'):
            report=complete_data_report();report['large_data']['lifecycle_boundary_limits'][key]=float(data.LIFECYCLE_LIMITS[key])
            with self.subTest(key=key):self.assert_invalid(report)

    def test_complete_needs_nine_unique_ordered_rows(self):
        for value in (None,{},'rows',[],NAMES):
            report=complete_data_report();report['large_data']['lifecycle_boundaries']=value
            with self.subTest(value=repr(value)):self.assert_invalid(report)
        for kind in ('missing_middle','duplicate','reversed','unknown','tenth'):
            report=complete_data_report();value=rows(report)
            if kind=='missing_middle':value.pop(4)
            elif kind=='duplicate':value[4]=copy.deepcopy(value[3])
            elif kind=='reversed':value.reverse()
            elif kind=='unknown':value[4]['name']='after_something_else'
            else:value.append(copy.deepcopy(value[-1]))
            with self.subTest(kind=kind):self.assert_invalid(report)

    def test_every_row_identity_shape_and_canonical_group_is_required(self):
        for index in range(9):
            for key,value in (('unit','ssh.service'),('source_commit','c'*40),
                    ('service_cgroup','0::/other.service'),('name',None)):
                report=complete_data_report();rows(report)[index][key]=value
                with self.subTest(index=index,key=key):self.assert_invalid(report)
        for group in ('0::/system.slice//'+UNIT,'0::/system.slice/../'+UNIT,
                '0::/system.slice/./'+UNIT,'0::/system.slice/'+UNIT+'/',
                '0::/system.slice/'+UNIT+'\n0::/other','1:memory:/system.slice/'+UNIT):
            report=complete_data_report();report['service_cgroup']=group
            for row in rows(report)+report['large_data']['backup_boundaries']:row['service_cgroup']=group
            with self.subTest(group=group):self.assert_invalid(report)
        for value in (None,[],0,'row'):
            report=complete_data_report();rows(report)[4]=value
            with self.subTest(row=value):self.assert_invalid(report)

    def test_clocks_and_observer_costs_reject_nonfinite_bool_and_negative(self):
        keys=('started_monotonic','finished_monotonic','snapshot_observer_self_cpu_seconds',
              'snapshot_observer_wall_seconds')
        for key in keys:
            for value in (None,'0',True,False,float('nan'),float('inf'),-float('inf'),-1):
                report=complete_data_report();rows(report)[3][key]=value
                with self.subTest(key=key,value=value):self.assert_invalid(report)
            report=complete_data_report();del rows(report)[3][key]
            with self.subTest(missing=key):self.assert_invalid(report)
        report=complete_data_report();rows(report)[3]['snapshot_observer_wall_seconds']=.2
        self.assert_invalid(report)
        report=complete_data_report();rows(report)[3]['finished_monotonic']=130.2
        self.assert_invalid(report)

    def test_observer_cpu_is_not_incorrectly_bounded_by_wall_or_cgroup_tick(self):
        report=complete_data_report()
        for row in rows(report):row['snapshot_observer_self_cpu_seconds']=.5
        # Process CPU and memcg counters use different read intervals/tick precision.
        data.validate_lifecycle_boundaries(report,UNIT,COMMIT)
        data.validate_complete(report,UNIT,COMMIT)

    def test_first_snapshot_is_in_actual_unstaged_gap(self):
        for index,stamp in ((0,120.5),(1,120.8),(0,130.01),(1,130.001)):
            report=complete_data_report();row=rows(report)[index]
            row.update(started_monotonic=stamp,finished_monotonic=stamp+.01)
            with self.subTest(index=index,stamp=stamp):self.assert_invalid(report)
        report=complete_data_report()
        for row in rows(report)[1:]:row['metrics']['cpu.stat']['usage_usec']+=100000
        report['metrics']['cpu.stat']['usage_usec']+=100000
        # The gap's CPU delta is not part of mutate or bad-digest stage CPU.
        stage(report,MUTATE)['cpu_usage_usec']=1
        stage(report,BAD)['cpu_usage_usec']=200
        data.validate_lifecycle_boundaries(report,UNIT,COMMIT)

    def test_each_in_stage_boundary_obeys_its_own_window_and_cpu_delta(self):
        for name,indices in ((BAD,(2,3,4)),(RESTORE,(5,6)),(VERIFY,(7,8))):
            for index in indices:
                for stamp in (stage(complete_data_report(),name)['started_monotonic']-.1,
                              stage(complete_data_report(),name)['started_monotonic']+1):
                    report=complete_data_report();rows(report)[index].update(
                        started_monotonic=stamp,finished_monotonic=stamp+.01)
                    with self.subTest(stage=name,index=index,stamp=stamp):self.assert_invalid(report)
            report=complete_data_report();stage(report,name)['cpu_usage_usec']=99
            with self.subTest(stage=name,cpu='too_small'):self.assert_invalid(report)

    def test_rows_cannot_precede_backup_or_decrease_cumulative_counters(self):
        mutations=(('memory.peak',None,1999),('cpu.stat','usage_usec',999),('memory.events','max',2))
        for index in (0,4,8):
            for section,key,value in mutations:
                report=complete_data_report();metric=rows(report)[index]['metrics']
                if key is None:metric[section]=value
                else:metric[section][key]=value
                with self.subTest(index=index,counter=(section,key)):self.assert_invalid(report)
        report=complete_data_report();report['large_data']['backup_boundaries'][-1]['finished_monotonic']=123
        self.assert_invalid(report)

    def test_metric_shapes_types_and_final_upper_bounds(self):
        paths=(('memory.current',),('memory.peak',),('memory.stat','anon'),('memory.stat','file'),
            ('memory.events','max'),('memory.events','oom'),('memory.events','oom_kill'),
            ('memory.events','oom_group_kill'),('cpu.stat','usage_usec'))
        for path in paths:
            for value in (None,True,-1,float('nan'),float('inf'),1.5,'1'):
                report=complete_data_report();target=rows(report)[4]['metrics']
                for key in path[:-1]:target=target[key]
                target[path[-1]]=value
                with self.subTest(path=path,value=value):self.assert_invalid(report)
            report=complete_data_report();target=rows(report)[4]['metrics']
            for key in path[:-1]:target=target[key]
            del target[path[-1]]
            with self.subTest(missing=path):self.assert_invalid(report)
        for section in ('memory.stat','memory.events','cpu.stat'):
            for value in (None,[],0):
                report=complete_data_report();rows(report)[4]['metrics'][section]=value
                with self.subTest(section=section,value=value):self.assert_invalid(report)
        for target in ('stage','final'):
            for counter in ('peak','max','cpu'):
                report=complete_data_report()
                if target=='stage':
                    record=stage(report,VERIFY)
                    record[{'peak':'memory_peak_bytes','cpu':'cpu_usage_usec'}.get(counter,'memory_events')]=(
                        1999 if counter=='peak' else 99 if counter=='cpu' else {'max':2,'oom':0,'oom_kill':0,'oom_group_kill':0})
                elif counter=='peak':report['metrics']['memory.peak']=1999
                elif counter=='max':report['metrics']['memory.events']['max']=2
                else:report['metrics']['cpu.stat']['usage_usec']=2199
                with self.subTest(target=target,counter=counter):self.assert_invalid(report)

    def test_oom_is_diagnostic_only_and_never_qualifies(self):
        for key in ('oom','oom_kill','oom_group_kill'):
            report=prefix_report(9)
            for row in rows(report)[-2:]:row['metrics']['memory.events'][key]=1
            report['stages'][-1]['memory_events']=copy.deepcopy(report['stages'][-1]['memory_events'])
            report['stages'][-1]['memory_events'][key]=1
            report['metrics']['memory.events'][key]=1
            result=data.validate_lifecycle_prefix(report,UNIT,COMMIT)
            self.assertTrue(result['oom_observed']);self.assertIs(result['qualification'],False)
            with self.subTest(key=key):self.assert_invalid(report)

    def test_serialized_row_and_total_caps_are_independent(self):
        report=complete_data_report();value=rows(report)
        value[0]['metrics']['memory.stat'].update({f'k{index:03d}'+('x'*124):0 for index in range(123)})
        self.assertGreater(len(compact(value[0])),16384)
        with self.assertRaisesRegex(RuntimeError,'row byte budget'):data.validate_lifecycle_rows(value,UNIT,COMMIT,report['service_cgroup'])
        self.assert_invalid(report)
        report=complete_data_report();value=rows(report)
        for row in value:row['metrics']['memory.stat'].update({f'k{index:03d}'+('x'*124):0 for index in range(110)})
        self.assertTrue(all(len(compact(row))<16384 for row in value))
        self.assertGreater(len(compact(value)),131072)
        self.assert_invalid(report)
        with self.assertRaisesRegex(RuntimeError,'total byte budget'):data.validate_lifecycle_rows(value,UNIT,COMMIT,report['service_cgroup'])

    def test_exact_row_and_compact_total_caps_accept_then_one_byte_overflows(self):
        report=complete_data_report();value=rows(report)[:1]
        pad_row(value[0],16384)
        self.assertEqual(data.validate_lifecycle_rows(value,UNIT,COMMIT,report['service_cgroup']),16386)
        value[0]['metrics']['memory.stat']['anon']=1000  # Exactly one extra ASCII byte.
        self.assertEqual(len(compact(value[0])),16385)
        with self.assertRaisesRegex(RuntimeError,'row byte budget'):
            data.validate_lifecycle_rows(value,UNIT,COMMIT,report['service_cgroup'])
        report=complete_data_report();value=rows(report)
        for row in value[:-1]:pad_row(row,15000)
        pad_row(value[-1],131072-8*15000-10)  # Nine rows: eight commas, two brackets.
        self.assertEqual(len(compact(value)),131072)
        self.assertEqual(data.validate_lifecycle_rows(value,UNIT,COMMIT,report['service_cgroup']),131072)
        data.validate_complete(report,UNIT,COMMIT)
        value[-1]['metrics']['memory.stat']['anon']=1000
        self.assertEqual(len(compact(value)),131073)
        with self.assertRaisesRegex(RuntimeError,'total byte budget'):
            data.validate_lifecycle_rows(value,UNIT,COMMIT,report['service_cgroup'])

    def test_known_previous_stage_cumulative_counters_cannot_decrease_at_next_boundary(self):
        for name in ('stopped_backup',MUTATE,BAD,RESTORE):
            for counter in ('peak','events'):
                report=complete_data_report();previous=stage(report,name)
                if counter=='peak':previous['memory_peak_bytes']=2001
                else:previous['memory_events']['max']=4
                with self.subTest(stage=name,counter=counter):self.assert_invalid(report)

    def test_final_report_cannot_drop_completed_stage_peak_or_events(self):
        for counter in ('peak','events'):
            report=complete_data_report()
            if counter=='peak':report['stages'][-1]['memory_peak_bytes']=2001
            else:report['stages'][-1]['memory_events']['max']=4
            with self.subTest(counter=counter):self.assert_invalid(report)

    def test_current_anon_and_file_are_gauges_and_may_drop_independently(self):
        report=complete_data_report()
        for index,row in enumerate(rows(report)):
            row['metrics']['memory.current']=1000 if index%2 else 700
            row['metrics']['memory.stat']['anon']=600 if index%2 else 10
            row['metrics']['memory.stat']['file']=10 if index%2 else 600
        report['metrics']['memory.current']=100
        report['metrics']['memory.stat'].update(anon=10,file=10)
        data.validate_lifecycle_boundaries(report,UNIT,COMMIT)
        data.validate_complete(report,UNIT,COMMIT)

    def test_all_memory_event_counters_are_monotonic_and_dotted_cpu_key_is_valid(self):
        report=complete_data_report()
        for row in rows(report)+report['large_data']['backup_boundaries']:
            row['metrics']['memory.events']['high']=10
        for item in report['stages']:item['memory_events']['high']=10
        report['metrics']['memory.events']['high']=10
        self.assertIn('core_sched.force_idle_usec',rows(report)[0]['metrics']['cpu.stat'])
        data.validate_complete(report,UNIT,COMMIT)
        for kind in ('decreased','disappeared','stage_final','report_final'):
            bad=copy.deepcopy(report)
            if kind=='decreased':rows(bad)[4]['metrics']['memory.events']['high']=9
            elif kind=='disappeared':del rows(bad)[4]['metrics']['memory.events']['high']
            elif kind=='stage_final':stage(bad,BAD)['memory_events']['high']=9
            else:bad['metrics']['memory.events']['high']=9
            with self.subTest(kind=kind):self.assert_invalid(bad)

    def test_prefix_uses_available_final_fields_but_does_not_invent_missing_fields(self):
        fields=('wall_seconds','memory_peak_bytes','memory_events','cpu_usage_usec')
        for count in range(3,10):
            report=prefix_report(count)
            for key in fields:report['stages'][-1].pop(key)
            with self.subTest(count=count):
                self.assertEqual(data.validate_lifecycle_prefix(report,UNIT,COMMIT)['observation_count'],count)
            for key in fields:
                bad=copy.deepcopy(report);bad['stages'][-1][key]=None
                with self.subTest(count=count,explicit_null=key),self.assertRaises(RuntimeError):
                    data.validate_lifecycle_prefix(bad,UNIT,COMMIT)
        report=prefix_report(0);report['metrics']['memory.events']['oom']=1
        self.assertTrue(data.validate_lifecycle_prefix(report,UNIT,COMMIT)['oom_observed'])
        report['metrics']['cpu.stat']['usage_usec']=True
        with self.assertRaises(RuntimeError):data.validate_lifecycle_prefix(report,UNIT,COMMIT)

    def test_known_earlier_stage_and_backup_anchor_clocks_are_binding(self):
        for kind in ('anchor_before_stage','anchor_after_stage','overlap','missing_anchor','missing_mutate_clock'):
            report=prefix_report(4)
            if kind=='anchor_before_stage':
                report['large_data']['backup_boundaries'][-1].update(started_monotonic=100,finished_monotonic=100.1)
            elif kind=='anchor_after_stage':
                report['large_data']['backup_boundaries'][-1].update(started_monotonic=119,finished_monotonic=119.1)
            elif kind=='overlap':stage(report,MUTATE)['started_monotonic']=110.5
            elif kind=='missing_anchor':report['large_data']['backup_boundaries'].pop()
            else:del stage(report,MUTATE)['wall_seconds']
            with self.subTest(kind=kind),self.assertRaises(RuntimeError):data.validate_lifecycle_prefix(report,UNIT,COMMIT)

    def test_every_prefix_length_is_diagnostic_and_never_completes_failed_report(self):
        for count in range(10):
            report=prefix_report(count)
            with self.subTest(count=count):
                result=data.validate_lifecycle_prefix(report,UNIT,COMMIT)
                self.assertEqual(result['observation_count'],count)
                self.assertIs(result['qualification'],False)
                self.assertIs(result['oom_observed'],False)
                self.assert_invalid(report)
                if count not in (1,2):
                    report['stages'][-1]['outcome']='running'
                    result=data.validate_lifecycle_prefix(report,UNIT,COMMIT)
                    self.assertEqual(result['observation_count'],count)

    def test_prefix_rejects_holes_duplicates_future_stages_and_known_wrong_bounds(self):
        for count in range(1,10):
            for kind in ('hole','duplicate','wrong_gap','future_passed'):
                report=prefix_report(count)
                if kind=='hole':rows(report)[-1]['name']=NAMES[min(count,8)] if count<9 else 'unknown'
                elif kind=='duplicate':rows(report).append(copy.deepcopy(rows(report)[-1]))
                elif kind=='wrong_gap':rows(report)[0].update(started_monotonic=120.5,finished_monotonic=120.51)
                else:
                    # Never accept a passed downstream stage while its earlier observation is absent.
                    report['stages']=complete_data_report()['stages']
                    if count==9:report['stages'][0]['outcome']='failed'
                with self.subTest(count=count,kind=kind),self.assertRaises(RuntimeError):
                    data.validate_lifecycle_prefix(report,UNIT,COMMIT)

    def test_prefix_does_not_invent_unstarted_future_bound(self):
        report=prefix_report(2)
        rows(report)[1].update(started_monotonic=500,finished_monotonic=500.01)
        result=data.validate_lifecycle_prefix(report,UNIT,COMMIT)
        self.assertEqual(result['observation_count'],2)
        # Once the next stage exists, its real start is a binding upper limit.
        report['stages'].append(copy.deepcopy(stage(complete_data_report(),BAD)))
        report['stages'][-1]['outcome']='running'
        with self.assertRaises(RuntimeError):data.validate_lifecycle_prefix(report,UNIT,COMMIT)



def acceptance_nodes():
    """Extract only the actual recorder, checkpoint, and stopped lifecycle block.

    No worker setup, subprocess, systemd, cgroup write, or real file read executes.
    These are the production acceptance statements, not a copied test algorithm.
    """
    tree=ast.parse(Path(gate.__file__).read_text())
    functions={node.name:node for node in ast.walk(tree) if isinstance(node,ast.FunctionDef)}
    blocks=[node for node in ast.walk(tree) if isinstance(node,ast.If)
        and any(isinstance(child,ast.Call) and isinstance(child.func,ast.Name)
            and child.func.id=='stage' and child.args and isinstance(child.args[0],ast.Constant)
            and child.args[0].value==MUTATE for item in node.body for child in ast.walk(item))]
    # Pick the narrow data-backup branch, excluding enclosing worker conditionals.
    block=min(blocks,key=lambda node:node.end_lineno-node.lineno)
    return functions['lifecycle_boundary'],functions['checkpoint'],block.body


class FakeClock:
    def __init__(self):self.wall=10.;self.cpu=1.
    def monotonic(self):return self.wall
    def process_time(self):return self.cpu
    def spend(self,wall,cpu):self.wall+=wall;self.cpu+=cpu


class LifecycleExecutionTests(unittest.TestCase):
    def scenario(self,*,operation_error=None,checkpoint_error=None,oom_at=None,
                 false_rejection=False,mismatched_snapshot=False,metric_error=None):
        recorder,checkpoint,block=acceptance_nodes()
        clock=FakeClock();events=[];saved=[];attempted=[];calls=[];stages=[];metrics_calls=[]
        report=complete_data_report();report['large_data']['lifecycle_boundaries']=[]
        report['stages']=[]
        for key in ('bad_digest_preserved_data','recovery'):report['large_data'].pop(key,None)
        group=report['service_cgroup'];directory=object();output=object()
        root=object();folder=object();python=object();payload=object();backup=object();digest='d'*64
        original=report['large_data'].setdefault('original',object());logs=report['large_data']['logs']
        changed={'files':{'large.log':'original-full-sha'},'current':'selected','recovery':[]}
        recovered={'whole_recovery':'unchanged return object'}
        failure=OSError('specific operation failure')

        class IdentityPath:
            def __init__(self,path):
                if path!='/proc/self/cgroup':raise AssertionError('Unexpected real path '+str(path))
            def read_text(self):
                events.append('identity');clock.spend(.003,.001)
                return group+'\n'

        def metrics(target):
            self.assertIs(target,directory);metrics_calls.append(target);events.append('metrics')
            if metric_error is not None and len(metrics_calls)==metric_error:raise failure
            clock.spend(.007,.002)
            value=copy.deepcopy(complete_data_report()['metrics'])
            value['cpu.stat']['usage_usec']=1000+len(metrics_calls)
            if oom_at is not None and (len(metrics_calls)+1)//2>=oom_at:value['memory.events']['oom']=1
            return value

        def validate(*args,**kwargs):
            events.append('validate');clock.spend(20,10)
            self.assertIs(kwargs.get('allow_oom'),True)
            return data.validate_lifecycle_rows(*args,**kwargs)

        def write_json(target,value):
            self.assertIs(target,output);events.append('checkpoint_write');clock.spend(30,20)
            attempted.append(copy.deepcopy(value))
            if checkpoint_error==len(attempted):raise failure
            saved.append(copy.deepcopy(value))

        def called(name,args):
            calls.append((name,args));events.append(name)
            if operation_error==name:raise failure

        snapshot_count=0
        def current_snapshot(*args):
            nonlocal snapshot_count
            snapshot_count+=1;called('snapshot_'+str(snapshot_count),args)
            if mismatched_snapshot and snapshot_count==2:return {'different':True}
            return changed

        def installed_operation(*args):
            name=args[4];called(name,args)
            if name=='reject_bad_digest':return {'rejected':not false_rejection}
            return {'restore_completed':True}

        def mutate(*args):called('mutate',args)
        def verify(*args):called('verify_recovery',args);return recovered
        def record_stage(name,action):
            stages.append(name);row={'name':name,'outcome':'running'};report['stages'].append(row)
            try:
                value=action();row['outcome']='passed';return value
            except Exception:
                row['outcome']='failed';raise

        large=SimpleNamespace(LIFECYCLE_BOUNDARIES=data.LIFECYCLE_BOUNDARIES,
            validate_lifecycle_rows=validate,current_snapshot=current_snapshot,
            installed_operation=installed_operation,mutate_after_backup=mutate,verify_recovery=verify)
        namespace=dict(report=report,large=large,args=SimpleNamespace(unit=UNIT,source_commit=COMMIT),
            time=clock,Path=IdentityPath,metrics=metrics,directory=directory,output=output,
            write_json=write_json,assert_no_oom=gate.assert_no_oom,stage=record_stage,
            root=root,data=folder,python=python,payload=payload,backup=backup,digest=digest)
        exec(compile(ast.Module(body=[recorder,checkpoint],type_ignores=[]),gate.__file__,'exec'),namespace)
        execute=lambda:exec(compile(ast.Module(body=block,type_ignores=[]),gate.__file__,'exec'),namespace)
        return SimpleNamespace(execute=execute,namespace=namespace,report=report,calls=calls,
            stages=stages,events=events,saved=saved,attempted=attempted,clock=clock,
            failure=failure,root=root,folder=folder,python=python,payload=payload,backup=backup,
            digest=digest,changed=changed,original=original,logs=logs,recovered=recovered,
            metrics_calls=metrics_calls)

    def test_actual_lifecycle_keeps_exact_operations_arguments_results_and_two_snapshots(self):
        run=self.scenario();run.execute()
        self.assertEqual([name for name,_ in run.calls],
            ['mutate','snapshot_1','reject_bad_digest','snapshot_2','restore','verify_recovery'])
        self.assertEqual(run.stages,[MUTATE,BAD,RESTORE,VERIFY])
        expected=[(run.folder,), (run.root,run.folder),
            (run.python,run.payload,run.folder,run.root,'reject_bad_digest',run.backup,'0'*64),
            (run.root,run.folder),
            (run.python,run.payload,run.folder,run.root,'restore',run.backup,run.digest),
            (run.root,run.folder,run.original,run.changed,run.logs)]
        self.assertEqual([args for _,args in run.calls],expected)
        self.assertIs(run.report['large_data']['recovery'],run.recovered)
        self.assertIs(run.report['large_data']['bad_digest_preserved_data'],True)
        self.assertEqual([row['name'] for row in rows(run.report)],list(NAMES))
        self.assertEqual(len(run.saved),9)
        self.assertEqual([len(rows(item)) for item in run.saved],list(range(1,10)))
        self.assertEqual(len(run.metrics_calls),18)  # Snapshot plus original checkpoint reread.
        self.assertEqual([run.events.index(name) for name in
            ('snapshot_1','reject_bad_digest','snapshot_2','restore','verify_recovery')],sorted(
            run.events.index(name) for name in ('snapshot_1','reject_bad_digest','snapshot_2','restore','verify_recovery')))

    def test_snapshot_cost_excludes_serialization_validation_and_actual_checkpoint(self):
        run=self.scenario();run.execute()
        for row in rows(run.report):
            self.assertAlmostEqual(row['snapshot_observer_wall_seconds'],.010)
            self.assertAlmostEqual(row['snapshot_observer_self_cpu_seconds'],.003)
        self.assertGreater(run.clock.wall,450)
        self.assertGreater(run.clock.cpu,270)
        self.assertEqual(run.events[:6],['mutate','identity','metrics','validate','metrics','checkpoint_write'])
        self.assertGreater(rows(run.report)[1]['started_monotonic']-rows(run.report)[0]['finished_monotonic'],50)

    def test_original_operation_failures_keep_last_before_and_stop_later_work(self):
        cases=(('mutate',0,0),('snapshot_1',1,1),('reject_bad_digest',3,2),
            ('snapshot_2',4,3),('restore',6,4),('verify_recovery',8,5))
        expected=['mutate','snapshot_1','reject_bad_digest','snapshot_2','restore','verify_recovery']
        for name,count,call_index in cases:
            run=self.scenario(operation_error=name)
            with self.subTest(operation=name):
                with self.assertRaises(OSError) as caught:run.execute()
                self.assertIs(caught.exception,run.failure)
                self.assertEqual([item[0] for item in run.calls],expected[:call_index+1])
                self.assertEqual([row['name'] for row in rows(run.report)],list(NAMES[:count]))
                self.assertEqual(len(run.saved),count)
                self.assertNotIn('recovery',run.report['large_data'])

    def test_false_rejection_short_circuits_second_full_snapshot(self):
        run=self.scenario(false_rejection=True)
        with self.assertRaisesRegex(RuntimeError,'Bad digest damaged'):run.execute()
        self.assertEqual([name for name,_ in run.calls],['mutate','snapshot_1','reject_bad_digest'])
        self.assertEqual([row['name'] for row in rows(run.report)],list(NAMES[:3]))
        self.assertNotIn('bad_digest_preserved_data',run.report['large_data'])
        self.assertNotIn(RESTORE,run.stages)

    def test_failed_preservation_comparison_never_emits_success_boundary(self):
        run=self.scenario(mismatched_snapshot=True)
        with self.assertRaisesRegex(RuntimeError,'Bad digest damaged'):run.execute()
        self.assertEqual([name for name,_ in run.calls],['mutate','snapshot_1','reject_bad_digest','snapshot_2'])
        self.assertEqual([row['name'] for row in rows(run.report)],list(NAMES[:4]))
        self.assertNotIn('bad_digest_preserved_data',run.report['large_data'])
        self.assertNotIn(RESTORE,run.stages)

    def test_each_checkpoint_failure_keeps_in_memory_row_and_previous_durable_prefix(self):
        completed_calls=(['mutate'],['mutate','snapshot_1'],['mutate','snapshot_1'],
            ['mutate','snapshot_1','reject_bad_digest'],
            ['mutate','snapshot_1','reject_bad_digest','snapshot_2'],
            ['mutate','snapshot_1','reject_bad_digest','snapshot_2'],
            ['mutate','snapshot_1','reject_bad_digest','snapshot_2','restore'],
            ['mutate','snapshot_1','reject_bad_digest','snapshot_2','restore'],
            ['mutate','snapshot_1','reject_bad_digest','snapshot_2','restore','verify_recovery'])
        for count in range(1,10):
            run=self.scenario(checkpoint_error=count)
            with self.subTest(checkpoint=count):
                with self.assertRaises(OSError) as caught:run.execute()
                self.assertIs(caught.exception,run.failure)
                self.assertEqual(len(rows(run.report)),count)
                self.assertEqual(len(run.saved),count-1)
                self.assertEqual(len(run.attempted),count)
                self.assertEqual([name for name,_ in run.calls],completed_calls[count-1])
                self.assertNotIn('recovery',run.report['large_data'])

    def test_oom_snapshot_is_persisted_before_original_checkpoint_rejects(self):
        for count in range(1,10):
            run=self.scenario(oom_at=count)
            with self.subTest(boundary=count):
                with self.assertRaises(RuntimeError):run.execute()
                self.assertEqual(len(rows(run.report)),count)
                self.assertEqual(len(run.saved),count)
                self.assertEqual(rows(run.saved[-1])[-1]['metrics']['memory.events']['oom'],1)
                self.assertEqual(run.saved[-1]['metrics']['memory.events']['oom'],1)
                self.assertNotIn('recovery',run.report['large_data'])

    def test_failed_snapshot_metric_read_does_not_emit_a_partial_row(self):
        for count in range(1,10):
            run=self.scenario(metric_error=2*count-1)
            with self.subTest(boundary=count):
                with self.assertRaises(OSError) as caught:run.execute()
                self.assertIs(caught.exception,run.failure)
                self.assertEqual(len(rows(run.report)),count-1)
                self.assertEqual(len(run.saved),count-1)

    def test_tenth_row_or_wrong_next_name_has_no_measurement_or_checkpoint(self):
        run=self.scenario();run.execute();events=list(run.events)
        for name in (NAMES[-1],'unknown'):
            with self.subTest(name=name),self.assertRaises(RuntimeError):run.namespace['lifecycle_boundary'](name)
            self.assertEqual(run.events,events)
            self.assertEqual(len(run.saved),9)
        run=self.scenario()
        with self.assertRaises(RuntimeError):run.namespace['lifecycle_boundary'](NAMES[1])
        self.assertEqual(run.events,[])
        self.assertEqual(rows(run.report),[])

    def test_unchanged_hashing_installed_commands_and_stage_identity_expressions(self):
        # Frozen ASTs for deliberately unchanged baseline helpers, including whole-file
        # reads and release tool argv. AST comparison tolerates comments/formatting.
        expected={
            'digest':'50ed353fbdffb488fabd669da1fce08b89c55deb99386278e3efc64e4218f3a1',
            'installed_operation':'293466c64f39227c0c5fe18493882b9ac2a92fdc444dd16623cf0fd5f68c428c',
            'current_snapshot':'9d179d2c29008e3961a210d2d18ab923456c0114df9b9f3182902feb1803ebe6',
            'mutate_after_backup':'ba30f43e3a17925a9c554d69b40663afcc3a7767aca3c3587f3ab8f97b4e84e2',
            'verify_recovery':'b7b45ee9f0c4c06a24cb49ff404e04177171426c1fcb167b9ac2ec39d7f092ed'}
        tree=ast.parse(Path(data.__file__).read_text())
        for node in tree.body:
            if isinstance(node,ast.FunctionDef) and node.name in expected:
                with self.subTest(function=node.name):
                    self.assertEqual(hashlib.sha256(ast.dump(node,include_attributes=False).encode()).hexdigest(),expected.pop(node.name))
        self.assertFalse(expected)
        for name,count,digest in (
                ('low_resource_acceptance.py',55,'9d2636f94466fd54e1e4fda4ac23924e84246e1d43269b2235375f77efd3316a'),
                ('low_resource_proxy.py',6,'6e2a9c4d7a70e93dfa10738b2e66f8c9f5fc82362f116c513c352b776badb168')):
            tree=ast.parse(Path(gate.__file__).with_name(name).read_text())
            identities=sorted(ast.dump(node.args[0],include_attributes=False) for node in ast.walk(tree)
                if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id=='stage')
            with self.subTest(module=name):
                self.assertEqual(len(identities),count)
                self.assertEqual(hashlib.sha256(json.dumps(identities).encode()).hexdigest(),digest)


if __name__=='__main__':unittest.main()
