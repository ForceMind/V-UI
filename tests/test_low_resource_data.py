"""Large fixture semantics and actual reversible archive controls."""
import base64
import copy
from contextlib import ExitStack
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
from scripts import low_resource_data as data
from scripts import low_resource_acceptance as gate
from app import release_tools as release
from app.services.routing_validation import build_rule_plan


UNIT='vui-low-resource-'+('a'*32)+'.service'
COMMIT='b'*40

# Original data-backup sequence, fixed independently of the validator's subset.
DATA_STAGE_NAMES=(
    'offline_stage_including_wheels',
    'activate',
    'fake_admin_provision',
    'https_startup',
    'anonymous_denied',
    'untrusted_ca_rejected',
    'login',
    'authenticated_read',
    '100_authenticated_reads_concurrency_10',
    'panel_only_idle_60_seconds',
    'seed_1000_synthetic_nodes',
    'large_synthetic_nodes_not_running',
    'large_rules_and_real_conditional_write_rejections',
    'large_anonymous_export_denied',
    'create_256_node_scoped_grant',
    'synthetic_public_grant_before_backup',
    'large_four_format_exports_serial_and_concurrent',
    'large_live_backup_rejected_without_stopping_panel',
    'proxy_server_start',
    'panel_single_proxy_idle_60_seconds',
    'proxy_client_start',
    'proxy_10_requests_concurrency_1',
    'proxy_100_requests_concurrency_10',
    'proxy_wrong_uuid_rejected',
    'proxy_wrong_ca_rejected',
    'logout',
    'logged_out_replayed_cookie_denied',
    'login_before_backup',
    'stopped_panel',
    'generate_256MiB_and_64_small_logs_inside_service',
    'stopped_backup',
    'mutate_large_data_after_backup',
    'large_bad_digest_restore_preserves_current_data',
    'stopped_restore',
    'large_business_logs_revocation_and_original_data_retained',
    'restore_data_and_session_revocation',
    'restored_https_startup',
    'old_session_after_restore_denied',
    'old_subscription_after_large_restore_denied',
    'restored_login',
    'restored_large_nodes_not_running',
    'restored_large_exports_and_rules',
    'restored_logout',
    'final_panel_stop',
    'large_subscription_token_absent_from_logs',
)


def complete_data_report():
    """Independent ordered fixture; snapshot hashing is in the unstaged gap."""
    unit=UNIT;commit=COMMIT
    clocks={name:(10+index*10,1) for index,name in enumerate(data.REQUIRED_STAGES)}
    # Keep readable lifecycle clocks while including all 45 historical stages.
    pending=[];previous_end=0
    for name in DATA_STAGE_NAMES:
        if name not in clocks:
            pending.append(name);continue
        start,wall=clocks[name];step=(start-previous_end)/(len(pending)+1)
        for offset,item in enumerate(pending,1):clocks[item]=(previous_end+offset*step,min(.5,step/2))
        pending=[];previous_end=start+wall
    metrics={'memory.current':1000,'memory.peak':2000,'memory.stat':{'anon':100,'file':100},
             'memory.events':{'oom':0,'oom_kill':0,'oom_group_kill':0,'max':3},'cpu.stat':{'usage_usec':1000,'core_sched.force_idle_usec':0}}
    export=dict(outcome='passed',unit=unit,source_commit=commit,node_count=1000,cleanup_complete=True,broker_cleanup_complete=True,
        client_cgroup='0::/system.slice/'+unit.removesuffix('.service')+'-data.service',external_metrics=metrics,
        phases=[dict(path=p,outcome='passed',completed=40,baseline=1,serial=30,concurrent=10,concurrency=10,errors=0,
                     semantics_verified=True,body_bytes=50000,body_sha256='a'*64,wall_seconds=2,max_response_seconds=.2) for p in data.PATHS])
    large=dict(exports=export,routing_rejections=[428,409,422],live_backup_rejected=True,bad_digest_preserved_data=True,
        restored_exports_verified=True,synthetic_token_not_logged=True,
        logs=dict(large_bytes=data.LOG_BYTES,allocated_bytes=data.LOG_BYTES,disk_free_before_bytes=8*1024**3,
                  data_allocated_file_bytes=data.LOG_BYTES+64*4096,large_sha256='a'*64,small_file_count=64,small_file_bytes=4096,
                  small_sha256={f'small-{n:03d}.log':'a'*64 for n in range(64)}),
        archive=dict(archive_contains_log_files=True,archive_source='installed app.release_tools',archive_bytes=100000000,
                     expanded_bytes=data.LOG_BYTES+64*4096,sha256='b'*64,compression_ratio=100000000/(data.LOG_BYTES+64*4096),installed_root_allocated_file_bytes=500000000),
        recovery={k:True for k in ('business_restored','routing_restored','log_files_restored','database_integrity',
            'old_sessions_revoked','old_grant_revoked','pre_restore_data_preserved','selected_code_unchanged','temporary_cleanup_complete')})
    report=dict(outcome='passed',complete=True,unit=unit,source_commit=commit,requested_memory_mib=512,
        limits={'memory.max':512*1024*1024,'memory.swap.max':0,'cpu.max':'100000 100000'},
        metrics=copy.deepcopy(metrics),duration_profile='data-backup',service_cgroup='0::/system.slice/'+unit,large_data=large,stages=[dict(name=n,outcome='passed',started_monotonic=clocks[n][0],wall_seconds=clocks[n][1],
        memory_current_bytes=1000,memory_peak_bytes=2000,cpu_usage_usec=1000,memory_stat=copy.deepcopy(metrics['memory.stat']),memory_events=copy.deepcopy(metrics['memory.events']))
        for n in DATA_STAGE_NAMES])
    large['backup_boundaries']=[dict(name=name,unit=unit,source_commit=commit,service_cgroup=report['service_cgroup'],
        started_monotonic=110+i*.2,finished_monotonic=110.1+i*.2,metrics=copy.deepcopy(metrics))
        for i,name in enumerate(data.BACKUP_BOUNDARIES)]
    large.update(lifecycle_boundary_schema=1,lifecycle_boundary_limits=dict(data.LIFECYCLE_LIMITS),
        lifecycle_boundary_scope=data.LIFECYCLE_SCOPE,lifecycle_boundaries=[])
    stamps=(122,124,130.1,130.3,130.5,140.1,140.4,150.1,150.5)
    for index,(name,stamp) in enumerate(zip(data.LIFECYCLE_BOUNDARIES,stamps)):
        metric=copy.deepcopy(metrics);metric['cpu.stat']['usage_usec']=1400+index*100
        large['lifecycle_boundaries'].append(dict(name=name,unit=unit,source_commit=commit,
            service_cgroup=report['service_cgroup'],started_monotonic=stamp,finished_monotonic=stamp+.01,
            metrics=metric,snapshot_observer_self_cpu_seconds=.0001,snapshot_observer_wall_seconds=.01))
    report['metrics']['cpu.stat']['usage_usec']=5000
    return report


class LargeDataTests(unittest.TestCase):
    def test_all_1000_nodes_and_saved_rule_semantics(self):
        uuids=[f'11111111-1111-1111-1111-{n+1:012d}' for n in range(1000)]
        raw=base64.b64encode('\n'.join(f'vless://{u}@vpn.example.test:443#node-{n}' for n,u in enumerate(uuids)).encode())
        data.check_body(data.PATHS[0],raw)
        leaked=base64.b64encode(base64.b64decode(raw).replace(b'#node-0',b'?certificate_path=/tmp/server.pem#node-0'))
        with self.assertRaises(RuntimeError):data.check_body(data.PATHS[0],leaked)
        singbox={'outbounds':[dict(type='vless',tag=str(n),uuid=u) for n,u in enumerate(uuids)]}
        data.check_body(data.PATHS[1],json.dumps(singbox).encode())
        plan=build_rule_plan(data.routing_fixture());preview={**plan,'source':'saved'}
        data.check_body(data.PATHS[3],json.dumps(preview).encode())
        import yaml
        mihomo=dict(proxies=[dict(name=str(n),uuid=u) for n,u in enumerate(uuids)],
                    rules=[rule for section in plan['sections'] for rule in section['rules']],dns=plan['dns'])
        data.check_body(data.PATHS[2],yaml.safe_dump(mihomo).encode())
        bad=copy.deepcopy(singbox);bad['outbounds'][-1]['uuid']=uuids[0]
        with self.assertRaises(RuntimeError):data.check_body(data.PATHS[1],json.dumps(bad).encode())
        bad=copy.deepcopy(preview)
        for section in bad['sections']:section['rules']=[r.replace('FORCE_PROXY','DIRECT') for r in section['rules']]
        with self.assertRaises(RuntimeError):data.check_body(data.PATHS[3],json.dumps(bad).encode())
        with self.assertRaises(RuntimeError):data.check_body(data.PATHS[0],b'PRIVATE KEY')

    def test_real_installed_archive_control_flow_on_small_unit_fixture(self):
        # Size is small only for this unit; the hosted profile contract stays256MiB.
        with tempfile.TemporaryDirectory() as directory,patch.object(data,'LOG_BYTES',1024*1024):
            root=Path(directory);root.chmod(0o700);folder=root/'data';folder.mkdir(mode=0o700)
            # Ordinary restore verifies the selected release's policy reader.
            # Build a synthetic, locally verified selection rather than a bare
            # invalid CURRENT sentinel; no interpreter/core is executed here.
            source=root/'selected-source';source.mkdir(mode=0o700)
            (source/'main.py').write_text('# synthetic selected code\n')
            selected=root/'releases'/'small-fixture';selected.mkdir(parents=True,mode=0o700)
            selected_archive=root/'selected.zip'
            selected_digest=release.create_archive(source,selected_archive,{
                'kind':'release','platform':release.PLATFORM,'release_id':'small-fixture',
                'targets':[release.target_key()]})
            release.unpack_verified(selected_archive,selected_digest,selected/'payload')
            runtime=selected/'runtime'/'python'/'bin';runtime.mkdir(parents=True)
            python=runtime/'python3';python.write_text('#! /bin/sh\n');python.chmod(0o700)
            release.atomic_json(selected/'READY.json',{
                'manifest_sha256':release.digest((selected/'payload'/release.MANIFEST).read_bytes()),
                'runtime_key':release.target_key(),
                'runtime_tree_sha256':release.runtime_tree_digest(selected/'runtime')})
            release.atomic_json(root/'CURRENT.json',{'release_id':'small-fixture'})
            (folder/'mihomo-routing.json').write_text(json.dumps(data.routing_fixture()))
            with sqlite3.connect(folder/'v-ui.db') as db:
                db.executescript('CREATE TABLE users(username TEXT); INSERT INTO users VALUES ("resource-admin");'
                    'CREATE TABLE admin_sessions(id INTEGER); INSERT INTO admin_sessions VALUES (1);'
                    'CREATE TABLE subscription_grants(revoked INTEGER); INSERT INTO subscription_grants VALUES (0);'
                    'CREATE TABLE inbounds(id INTEGER PRIMARY KEY,core TEXT,remark TEXT,enable INTEGER,port INTEGER,protocol TEXT,settings TEXT,stream_settings TEXT,tag TEXT);')
                db.executemany('INSERT INTO inbounds VALUES (?,?,?,?,?,?,?,?,?)',[(n+1,'sing-box',str(n),1,20000+n,'vless','{}','{}',str(n)) for n in range(1000)])
            payload=Path(__file__).resolve().parents[1]
            original=dict(business=data.business_digest(folder),routing=data.digest(folder/'mihomo-routing.json'))
            with release.lease(root/'.panel.lease'):
                result=data.installed_operation(sys.executable,payload,folder,root,'reject_live_backup',root/'live.zip')
                self.assertTrue(result['rejected']);self.assertFalse((root/'live.zip').exists())
            logs=data.generate_log_fixture(folder);data.verify_log_fixture(folder,logs)
            archive=root/'backup.zip';result=data.installed_operation(sys.executable,payload,folder,root,'backup',archive)
            self.assertEqual(data.verify_archive(root,folder,archive,logs)['sha256'],result['digest'])
            data.mutate_after_backup(folder);changed=data.current_snapshot(root,folder)
            rejected=data.installed_operation(sys.executable,payload,folder,root,'reject_bad_digest',archive,'0'*64)
            self.assertTrue(rejected['rejected']);self.assertEqual(data.current_snapshot(root,folder),changed)
            data.installed_operation(sys.executable,payload,folder,root,'restore',archive,result['digest'])
            verified=data.verify_recovery(root,folder,original,changed,logs)
            self.assertTrue(all(verified.values()))

    def test_complete_report_rejects_missing_security_data_and_resource_evidence(self):
        unit=UNIT;commit=COMMIT;report=complete_data_report()
        data.validate_complete(report,unit,commit)
        for kind in ('missing','order','source','cgroup','clock','nan','metrics','peak','events','cpu','decreased'):
            bad=copy.deepcopy(report);rows=bad['large_data']['backup_boundaries']
            if kind=='missing':rows.pop()
            elif kind=='order':rows.reverse()
            elif kind=='source':rows[1]['source_commit']='c'*40
            elif kind=='cgroup':rows[1]['service_cgroup']='0::/another.service'
            elif kind=='clock':rows[1]['finished_monotonic']=112
            elif kind=='nan':rows[1]['started_monotonic']=float('nan')
            elif kind=='metrics':del rows[1]['metrics']['memory.stat']['file']
            elif kind=='peak':rows[1]['metrics']['memory.peak']=3000
            elif kind=='events':rows[1]['metrics']['memory.events']['max']=4
            elif kind=='cpu':rows[-1]['metrics']['cpu.stat']['usage_usec']=2001
            elif kind=='decreased':rows[1]['metrics']['cpu.stat']['usage_usec']=999
            with self.subTest(boundary=kind),self.assertRaises(RuntimeError):data.validate_complete(bad,unit,commit)
        for kind in ('stage','order','stage_cpu','oom','nan','cleanup','grant','before','digest','token','log_size','missing_file','source','short_count','rule_semantics','allocated_nan','archive_nan','ratio_nan','body_overlimit'):
            bad=copy.deepcopy(report);value=bad['large_data']
            if kind=='stage':bad['stages'].pop()
            elif kind=='order':bad['stages'].reverse()
            elif kind=='stage_cpu':del bad['stages'][0]['cpu_usage_usec']
            elif kind=='oom':bad['stages'][0]['memory_events']['oom']=1
            elif kind=='nan':value['exports']['phases'][0]['wall_seconds']=float('nan')
            elif kind=='cleanup':value['exports']['broker_cleanup_complete']=False
            elif kind=='grant':value['recovery']['old_grant_revoked']=False
            elif kind=='before':value['recovery']['pre_restore_data_preserved']=False
            elif kind=='digest':value['bad_digest_preserved_data']=False
            elif kind=='token':value['synthetic_token_not_logged']=False
            elif kind=='log_size':value['logs']['large_bytes']=1
            elif kind=='missing_file':value['logs']['small_sha256'].pop('small-001.log')
            elif kind=='source':value['exports']['source_commit']='c'*40
            elif kind=='short_count':value['exports']['phases'][0]['serial']=29
            elif kind=='allocated_nan':value['logs']['allocated_bytes']=float('nan')
            elif kind=='archive_nan':value['archive']['archive_bytes']=float('nan')
            elif kind=='ratio_nan':value['archive']['compression_ratio']=float('nan')
            elif kind=='body_overlimit':value['exports']['phases'][0]['body_bytes']=8*1024*1024+1
            else:value['exports']['phases'][0]['semantics_verified']=False
            with self.subTest(kind=kind),self.assertRaises(RuntimeError):data.validate_complete(bad,unit,commit)

    def test_profile_limit_and_unknown_client_unit(self):
        from types import SimpleNamespace
        self.assertEqual(gate.runtime_seconds(SimpleNamespace(memory_mib=512,duration_profile='data-backup')),1800)
        with self.assertRaises(RuntimeError):gate.runtime_seconds(SimpleNamespace(memory_mib=384,duration_profile='data-backup'))
        from scripts.low_resource_certificates import external_group
        self.assertTrue(external_group('vui-low-resource-'+('a'*32)+'-data.service').name.endswith('-data.service'))
        with self.assertRaises(RuntimeError):external_group('ssh.service')

if __name__=='__main__':unittest.main()
