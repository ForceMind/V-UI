"""Resource interaction isolation, evidence and real temporary-DB export contracts."""
import argparse
import ast
import base64
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import low_resource_acceptance as gate
from scripts import low_resource_interactions as interaction

UNIT='vui-low-resource-'+'a'*32+'.service'
COMMIT='b'*40


def logout_evidence():
    requests=[];ready=[];nav=[]
    def request(page,path,status,start,end,method='GET',generation=1,document='/ui/'):
        row=dict(id=len(requests)+1,page_id=page,tab=f'tab{page}',document_generation=generation,document_path=document,
            path=path,method=method,started_monotonic=start,response_monotonic=(start+end)/2,finished_monotonic=end,
            completed=True,failed=False,status=status,phase_at_start=None,phase_at_finish='logout')
        requests.append(row);return row
    for page in (1,2):
        for path in interaction.INITIAL_PATHS:request(page,path,200,1,1.5)
        ready.append(dict(page_id=page,tab=f'tab{page}',document_generation=1,document_path='/ui/',ready_monotonic=1.8,
                          completed_paths=list(interaction.INITIAL_PATHS)))
        nav.append(dict(page_id=page,tab=f'tab{page}',document_generation=2,document_path='/login',monotonic=4))
    request(1,'/api/auth/logout',200,2.1,2.3,'POST')
    request(2,'/api/system/status',401,2.5,3)
    request(2,'/api/auth/me',401,3,3.3);request(1,'/api/auth/me',401,3.4,3.8)
    row=dict(started_monotonic=2,logout_completed_monotonic=2.4,stopped_from=4.1,stopped_until=15.2,
             prepared_pages=ready,pre_logout_visibility={'tab1':'hidden','tab2':'visible'})
    return row,dict(requests=requests,navigations=nav)


class InteractionContracts(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        for name in ('ca','cert'):(self.root/name).touch()
        self.request=dict(unit=UNIT,source_commit=COMMIT,origin='https://127.0.0.1:12345',
            ca=str(self.root/'ca'),cert=str(self.root/'cert'),cookie='__Host-vui_session='+'a'*43,
            node_count=100,phase_seconds=60)

    def result(self):
        phases=[]
        for name in interaction.PHASES:
            row=dict(name=name,outcome='passed',wall_seconds=60.1)
            if name in ('visible_tab','hidden_tab','two_tabs'):
                row.update(visibility={'tab1':'visible'} if name=='visible_tab' else {'tab1':'hidden'}
                    if name=='hidden_tab' else {'tab1':'hidden','tab2':'visible'},visibility_samples=60,
                    requests={('tab2' if name=='two_tabs' else 'tab1')+':/api/system/status':20,
                              ('tab2' if name=='two_tabs' else 'tab1')+':/api/cores/status':6},
                    statuses={'200':26},max_inflight=1,request_failures=0,pending_requests=0)
            elif name.startswith('export_'):
                row.update(path=interaction.EXPORT_PATHS[int(name[-1])],requests_completed=600,concurrency=10,
                    errors=0,active_seconds=60.01,body_bytes=10000,body_sha256='a'*64)
            else:row.update(both_tabs_redirected=True,polling_stopped=True)
            if name=='hidden_tab':row.update(requests={},statuses={})
            if name in ('visible_tab','hidden_tab','two_tabs'):row['max_inflight_by_endpoint']={key:1 for key in row['requests']}
            if name=='logout':row['statuses']={'401':1}
            phases.append(row)
        logout,trace=logout_evidence();phases[-1].update(logout)
        return dict(session_trace=trace,outcome='passed',source_commit=COMMIT,unit=UNIT,node_count=100,phases=phases,polling_contract='visible-only-state-status',
            cleanup_complete=True,broker_cleanup_complete=True,
            native_focus_driver=dict(playwright_version='1.57.0',changes=1,enabled=False,installed_driver_modified=False,source_sha256='a'*64,copy_sha256='b'*64),
            client_cgroup='0::/system.slice/'+UNIT.removesuffix('.service')+'-browser.service',
            external_metrics={'memory.peak':100000,'cpu.stat':{'usage_usec':100},'memory.events':{'oom':0,'oom_kill':0}},
            external_requests=0,browser_exceptions=0,
            client_usage=dict(self_user_seconds=1,self_system_seconds=1,children_user_seconds=1,
                children_system_seconds=1,self_max_rss_kib=100,children_max_rss_kib=100))

    def test_only_scoped_loopback_fake_request(self):
        interaction.validate_request(self.request,self.root,UNIT,COMMIT)
        bad=[('origin','https://example.com:12345'),('origin','http://127.0.0.1:12345'),
             ('origin','https://name:pass@127.0.0.1:12345'),('origin','https://127.0.0.1:12345/path'),
             ('cookie','__Host-vui_session=short'),('ca','/etc/passwd'),('unit','other'),
             ('source_commit','c'*40),('node_count',10),('phase_seconds',1)]
        for key,value in bad:
            with self.subTest(key=key,value=value),self.assertRaises((RuntimeError,FileNotFoundError)):
                interaction.validate_request({**self.request,key:value},self.root,UNIT,COMMIT)

    def test_complete_result_and_fixed_interaction_budget(self):
        interaction.validate_result(self.result(),UNIT,COMMIT)
        args=argparse.Namespace(memory_mib=512,duration_profile='interactions')
        self.assertEqual(gate.runtime_seconds(args),1800)
        args.memory_mib=384
        with self.assertRaises(RuntimeError):gate.runtime_seconds(args)

    def test_missing_mixed_or_incomplete_evidence_rejected(self):
        for field,value in [('source_commit','wrong'),('outcome','running'),('node_count',0),
                            ('broker_cleanup_complete',False),('cleanup_complete',False),
                            ('client_usage',{}),('polling_contract','baseline'),('native_focus_driver',{}),('external_metrics',{}),('external_requests',1),('browser_exceptions',1),
                            ('client_cgroup','0::/system.slice/'+UNIT),('phases',[])]:
            row=self.result();row[field]=value
            with self.subTest(field=field),self.assertRaises(RuntimeError):
                interaction.validate_result(row,UNIT,COMMIT)
        for index,field,value in [(0,'wall_seconds',59),(0,'wall_seconds',float('nan')),
            (1,'visibility',{'tab1':'visible'}),(2,'visibility_samples',1),(0,'statuses',{'401':1}),
            (0,'requests',{}),(0,'max_inflight_by_endpoint',{'tab1:/api/system/status':2}),(1,'requests',{'tab1:/api/system/status':1}),(0,'statuses',{}),(2,'requests',{'tab1:/api/system/status':26}),
            (0,'pending_requests',1),(0,'request_failures',1),(3,'requests_completed',599),(4,'errors',1),(5,'active_seconds',59),
            (6,'body_sha256','bad'),(7,'both_tabs_redirected',False),(7,'polling_stopped',False),(7,'statuses',{})]:
            row=self.result();row['phases'][index][field]=value
            with self.subTest(index=index,field=field),self.assertRaises(RuntimeError):
                interaction.validate_result(row,UNIT,COMMIT)

    def test_optimized_hidden_polling_requires_zero_with_native_visibility(self):
        row=self.result();row['phases'][1]['requests']={};row['phases'][1]['statuses']={}
        interaction.validate_result(row,UNIT,COMMIT)
        source=Path(interaction.__file__).read_text()
        self.assertIn('headless=False',source)
        self.assertIn("page.evaluate('document.visibilityState')",source)
        self.assertNotIn('Object.defineProperty',source)
        self.assertEqual(source.count('Emulation.setFocusEmulationEnabled'),1)
        self.assertNotIn('setPageVisibility',source)
        self.assertNotIn('setWebLifecycleState',source)
        self.assertNotIn('context.request.post',source)
        self.assertIn("fetch('/api/auth/logout'",source)
        self.assertNotIn('ignore_https_errors',source)

    def test_browser_cleanup_uses_cgroup_including_detached_children(self):
        class Process:
            def poll(self):return 0
        unit=UNIT.removesuffix('.service')+'-browser.service'
        group=self.root/'group';group.mkdir();(group/'cgroup.events').write_text('populated 0\n')
        with patch.object(interaction,'browser_group',return_value=group),patch.object(interaction.subprocess,'run') as run:
            interaction.cleanup_browser_unit(unit,Process())
            self.assertIn(unittest.mock.call(['sudo','-n','systemctl','stop',unit],capture_output=True,timeout=20),run.call_args_list)
        with self.assertRaises(RuntimeError):interaction.browser_group('ssh.service')
        self.assertIn('KillMode=control-group',Path(interaction.__file__).read_text())

    def test_service_samples_cover_all_phases_and_oom_fails(self):
        samples=[]
        for index,name in enumerate(interaction.PHASES):
            for tick in range(6):
                samples.append({'driver_phase':name,'observed_monotonic':index*70+tick*10,
                    'memory.peak':1000,'memory.current':100,'cpu.stat':{'usage_usec':index*100+tick},
                    'memory.stat':{'anon':50,'file':50},'memory.events':{'oom':0,'oom_kill':0},
                    'processes':[{'pid':123}]})
        interaction.validate_service_samples(samples)
        for bad in (samples[:1],samples[:-6],[],None):
            with self.assertRaises(RuntimeError):interaction.validate_service_samples(bad)
        bad=copy.deepcopy(samples);bad[0]['memory.events']['oom']=1
        with self.assertRaises(RuntimeError):interaction.validate_service_samples(bad)
        bad=copy.deepcopy(samples);bad[0]['observed_monotonic']=float('nan')
        with self.assertRaises(RuntimeError):interaction.validate_service_samples(bad)

    def test_export_redirects_are_never_followed_with_cookie(self):
        for destination in ('https://other.example.test/','https://127.0.0.1:12345/other'):
            with self.assertRaisesRegex(RuntimeError,'redirects are forbidden'):
                interaction.NoRedirect().redirect_request(None,None,302,'redirect',{},destination)
        source=Path(interaction.__file__).read_text()
        self.assertIn('HTTPSHandler(context=ca_context),NoRedirect()',source)
        self.assertIn("with lock:row['requests_completed']+=1",source)
        self.assertIn("with lock:row['errors']+=1",source)

    def test_200_headers_then_body_failure_never_counts_as_completed(self):
        class Page:
            def __init__(self):self.events={}
            def on(self,event,callback):self.events[event]=callback
        class Request:
            url='https://127.0.0.1:12345/api/system/status'
        page=Page();row=dict(requests={},statuses={},request_failures=0,max_inflight=0)
        current={'row':row};inflight={}
        interaction.track_page(page,'tab1',current,inflight,[])
        request=Request()
        page.events['request'](request)
        response=argparse.Namespace(request=request,status=200)
        page.events['response'](response)
        self.assertEqual(row['statuses'],{})
        self.assertEqual(len(inflight),1)
        current['row']=None  # Attribution remains the original phase.
        page.events['requestfailed'](request)
        self.assertEqual(row['request_failures'],1)
        self.assertEqual(row['statuses'],{})
        self.assertFalse(inflight)
        current['row']=row
        page.events['request'](request);page.events['response'](response)
        page.events['requestfinished'](request)
        self.assertEqual(row['statuses'],{'200':1})
        self.assertEqual(row['requests'],{'tab1:/api/system/status':2})
        self.assertEqual(row['request_failures'],1)
        self.assertFalse(inflight)

    def test_logout_trace_requires_original_document_refusal_and_complete_stop_window(self):
        row,trace=logout_evidence();interaction.validate_logout_trace(row,trace)
        for bad in ('after_login','wrong_generation','incomplete','body_failure','no_status','pending_at_ready','deadline','stop_request','short_stop','missing_startup','post_initialization','post_refusal','late_logout'):
            r,t=copy.deepcopy((row,trace))
            denials=[q for q in t['requests'] if q['status']==401]
            if bad=='after_login':
                for q in denials:q.update(document_path='/login',document_generation=2)
            elif bad=='wrong_generation':denials[-1]['document_generation']=2
            elif bad=='incomplete':denials[-1]['completed']=False
            elif bad=='body_failure':denials[-1]['failed']=True
            elif bad=='no_status':t['requests'].remove(denials[0])
            elif bad=='pending_at_ready':t['requests'][0]['finished_monotonic']=2.2
            elif bad=='deadline':t['navigations'][0]['monotonic']=18
            elif bad=='stop_request':
                q=copy.deepcopy(denials[0]);q.update(id=999,started_monotonic=5,response_monotonic=5.1,finished_monotonic=5.2);t['requests'].append(q)
            elif bad=='short_stop':r['stopped_until']=r['stopped_from']+10.9
            elif bad=='post_initialization':
                for q in t['requests']:
                    if q['status']==200 and q['path']!='/api/auth/logout':q['method']='POST'
            elif bad=='post_refusal':
                for q in denials:q['method']='POST'
            elif bad=='late_logout':
                for q in t['requests']:
                    if q['path']=='/api/auth/logout':q.update(response_monotonic=100,finished_monotonic=101)
            else:t['requests'].pop(0)
            with self.subTest(bad=bad),self.assertRaises(RuntimeError):interaction.validate_logout_trace(r,t)

    def test_session_trace_keeps_cross_phase_requests_without_credentials(self):
        class Page:
            def __init__(self):self.events={};self.main_frame=argparse.Namespace(url='https://127.0.0.1:12345/ui/')
            def on(self,event,callback):self.events[event]=callback
        class Request:
            method='GET'
            url='https://127.0.0.1:12345/api/auth/me?secret=not-recorded'
        page=Page();current={'row':None};trace=interaction.SessionTrace(current);key=trace.attach(page,'tab1')
        page.events['framenavigated'](page.main_frame)
        request=Request();page.events['request'](request)
        current['row']={'name':'logout'}
        page.events['response'](argparse.Namespace(request=request,status=401));page.events['requestfinished'](request)
        row=trace.requests[0]
        self.assertIsNone(row['phase_at_start']);self.assertEqual(row['phase_at_finish'],'logout')
        self.assertEqual(row['document_generation'],1);self.assertEqual(row['document_path'],'/ui/')
        self.assertTrue(row['completed']);self.assertEqual(row['status'],401)
        self.assertNotIn('secret',json.dumps(trace.snapshot()));self.assertIsNone(trace.ready(key))
        page.main_frame.url='https://127.0.0.1:12345/login';page.events['framenavigated'](page.main_frame)
        after=Request();page.events['request'](after)
        self.assertEqual(trace.requests[-1]['document_generation'],2)
        self.assertEqual(trace.requests[-1]['document_path'],'/login')
        self.assertIsNone(trace.ready(key))

    def test_native_focus_uses_isolated_exact_driver_copy_and_restores_transport(self):
        from playwright._impl import _transport, _driver
        package=self.root/'package';path=package/'lib/server/chromium/crPage.js';path.parent.mkdir(parents=True)
        text='before; '+interaction._FOCUS_ENABLE+'; after;'
        path.write_text(text);(package/'cli.js').write_text('fixture');(package/'LICENSE').write_text('fixture license')
        real=_transport.compute_driver_executable
        report={}
        with patch.object(_driver,'compute_driver_executable',return_value=('/node',str(package/'cli.js'))):
            with interaction.native_playwright_driver(self.root,report):
                node,cli=_transport.compute_driver_executable()
                self.assertEqual(node,'/node')
                copied=Path(cli).parent
                self.assertEqual((copied/'lib/server/chromium/crPage.js').read_text(),text.replace(interaction._FOCUS_ENABLE,interaction._FOCUS_DISABLE))
                self.assertTrue((copied/'LICENSE').exists())
                self.assertEqual(path.read_text(),text)
            self.assertFalse(copied.exists())
            self.assertIs(_transport.compute_driver_executable,real)
            self.assertFalse(report['native_focus_driver']['enabled'])
            self.assertFalse(report['native_focus_driver']['installed_driver_modified'])
            path.write_text('no exact marker')
            with self.assertRaisesRegex(RuntimeError,'uniquely identified'):
                with interaction.native_playwright_driver(self.root,{}):pass
            self.assertFalse((self.root/'native-playwright-package').exists())

    def test_native_visibility_is_read_not_forced(self):
        self.assertEqual(set(interaction.NATIVE_BACKGROUND_ARGS),{
            '--disable-background-timer-throttling','--disable-backgrounding-occluded-windows','--disable-renderer-backgrounding'})
        class Page:
            def evaluate(self,expression):self.expression=expression;return 'visible'
            def wait_for_timeout(self,ms):pass
        page=Page()
        self.assertEqual(interaction.wait_native_visibility({'tab1':page},{'tab1':'visible'},timeout=0),{'tab1':'visible'})
        with self.assertRaisesRegex(RuntimeError,'Native tab visibility'):
            interaction.wait_native_visibility({'tab1':page},{'tab1':'hidden'},timeout=0)
        self.assertEqual(page.expression,'document.visibilityState')

    def test_workflow_opt_in_and_old_smoke_unchanged(self):
        root=Path(__file__).resolve().parents[1]
        workflow=(root/'.github/workflows/low-resource-interactions.yml').read_text()
        self.assertIn("github.event.label.name == 'run-low-resource-interactions'",workflow)
        self.assertIn('types: [labeled]',workflow)
        self.assertIn('command -v xvfb-run',workflow)
        self.assertIn('--memory-mib 512 --duration-profile interactions',workflow)
        self.assertIn('ref: ${{ github.event.pull_request.head.sha }}',workflow)
        self.assertIn('if: always()',workflow)
        self.assertNotIn('--duration-profile',(root/'.github/workflows/release.yml').read_text())


class RealExportFixtureTests(unittest.TestCase):
    def setUp(self):
        from test_auth import AuthenticationTests
        AuthenticationTests.setUp(self)
        self.login=lambda:AuthenticationTests.login(self)

    def test_exact_seed_code_exports_one_hundred_nodes_without_starting_listeners(self):
        from loopback_helpers import certificate_files
        from app.models import database
        root=Path(self.temp.name);ca,cert,key=certificate_files(root,'export')
        tree=ast.parse(Path(gate.__file__).read_text())
        codes=[node.value for node in ast.walk(tree) if isinstance(node,ast.Constant)
               and isinstance(node.value,str) and 'for n in range(100):' in node.value]
        self.assertEqual(len(codes),1)
        with patch('sys.argv',['fixture',str(cert),str(key)]):
            exec(compile(codes[0],'<scoped-export-fixture>','exec'),{})
        with database.SessionLocal() as db:self.assertEqual(db.query(database.Inbound).count(),100)
        self.assertEqual(self.login().status_code,200)
        raw=self.client.get('/api/subscription/raw')
        self.assertEqual(raw.status_code,200)
        self.assertEqual(len(base64.b64decode(raw.content).decode().splitlines()),100)
        exported=self.client.get('/api/subscription/sing-box.json')
        self.assertEqual(exported.status_code,200)
        self.assertEqual(sum(row.get('type')=='vless' for row in exported.json()['outbounds']),100)
        yaml=self.client.get('/api/subscription/mihomo.yaml')
        self.assertEqual(yaml.status_code,200)
        self.assertEqual(yaml.content,self.client.get('/api/subscription/mihomo.yaml').content)
        self.assertEqual(self.client.get('/api/routing/mihomo/preview').status_code,200)
        with patch('sys.argv',['fixture',str(cert),str(key)]),self.assertRaisesRegex(RuntimeError,'empty node'):
            exec(compile(codes[0],'<scoped-export-fixture>','exec'),{})


if __name__=='__main__':unittest.main()
