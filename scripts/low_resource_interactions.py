"""Disposable browser/export diagnostics, with clients outside service accounting.

This verifies visible-only polling with native document visibility. It never
fakes hidden state. Only a local synthetic panel is used.
"""
from __future__ import annotations

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import resource
import shutil
import re
import ssl
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener

SOURCE = Path(__file__).resolve().parents[1]
SECONDS = 60
POLL_PATHS = ('/api/system/status', '/api/cores/status')
EXPORT_PATHS = ('/api/subscription/raw', '/api/subscription/sing-box.json',
                '/api/subscription/mihomo.yaml', '/api/routing/mihomo/preview')
PHASES = ('visible_tab', 'hidden_tab', 'two_tabs', *('export_' + str(n) for n in range(4)), 'logout')


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, url):
        raise RuntimeError('Export redirects are forbidden; synthetic Cookie stays on exact origin')


def atomic_json(path, value):
    from scripts.low_resource_sustained import atomic_json as write
    write(path, value)


def validate_request(value, work, unit, commit):
    origin = urlsplit(value.get('origin', ''))
    if (value.get('unit') != unit or value.get('source_commit') != commit
            or origin.scheme != 'https' or origin.hostname != '127.0.0.1'
            or not origin.port or origin.path or origin.query or origin.fragment or origin.username or origin.password
            or value.get('node_count') != 100 or value.get('phase_seconds') != SECONDS):
        raise RuntimeError('Invalid scoped browser/export request')
    for name in ('ca', 'cert'):
        if not Path(value[name]).resolve(strict=True).is_relative_to(work.resolve()):
            raise RuntimeError('Interaction fixture escaped disposable work')
    cookie = value.get('cookie', '')
    if not isinstance(cookie, str) or not re.fullmatch(r'__Host-vui_session=[A-Za-z0-9_-]{43}', cookie):
        raise RuntimeError('Expected one synthetic administrator session')


class InteractionBroker:
    def __init__(self, work, output, unit, commit):
        self.work, self.output, self.unit, self.commit = work, output, unit, commit
        self.client_unit = unit.removesuffix(".service") + "-browser.service"
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.serve, daemon=True)
    def __enter__(self):
        from scripts.low_resource_sustained import outside_server, membership
        outside_server(self.unit, membership())
        self.thread.start()
        return self
    def __exit__(self, *_):
        self.stop.set(); self.thread.join(timeout=60)
        if self.thread.is_alive(): raise RuntimeError('Interaction broker did not stop')
    def serve(self):
        request = self.work / 'interactions.request.json'
        response = self.work / 'interactions.response.json'
        while not request.exists():
            if self.stop.wait(.1): return
        evidence = self.output.with_name(self.output.stem + '-interactions.json')
        value, process = {'outcome': 'failed'}, None
        try:
            data = json.loads(request.read_text())
            validate_request(data, self.work, self.unit, self.commit)
            with evidence.with_suffix('.log').open('w') as log:
                command = ['sudo', '-n', 'systemd-run', '--wait', '--pipe', '--unit', self.client_unit,
                    '--uid', str(os.getuid()), '--gid', str(os.getgid()),
                    '--property', 'RuntimeMaxSec=900', '--property', 'KillMode=control-group',
                    '--property', 'TimeoutStopSec=5s',
                    '--property', 'OOMPolicy=stop', '--setenv=GITHUB_ACTIONS=true',
                    '--setenv=RUNNER_ENVIRONMENT=github-hosted',
                    '--setenv=HOME='+str(Path.home()),
                    '/usr/bin/xvfb-run', '--auto-servernum', sys.executable, '-B',
                    str(Path(__file__).resolve()), '--request', str(request), '--output', str(evidence)]
                process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
                deadline = time.monotonic() + 900
                while process.poll() is None:
                    if self.stop.wait(.1) or time.monotonic() >= deadline:
                        raise RuntimeError('Interaction helper stopped or timed out')
                value = json.loads(evidence.read_text())
                if process.returncode != 0 or value.get('outcome') != 'passed':
                    raise RuntimeError('Interaction helper failed')
        except Exception as exc:
            if evidence.exists():
                try: value = json.loads(evidence.read_text())
                except (ValueError, OSError): pass
            value.update(outcome='failed', broker_error_type=type(exc).__name__)
        finally:
            value['helper_outcome'] = value.get('outcome')
            try:
                if process is not None:
                    cleanup_browser_unit(self.client_unit, process)
                value['broker_cleanup_complete'] = True
            except Exception as exc:
                value.update(outcome='failed', broker_cleanup_complete=False, broker_cleanup_error=type(exc).__name__)
            atomic_json(evidence, value)
            atomic_json(response, value)


def browser_group(unit):
    if not re.fullmatch(r'vui-low-resource-[0-9a-f]{32}-browser\.service', unit):
        raise RuntimeError('Invalid scoped browser unit')
    return Path('/sys/fs/cgroup/system.slice') / unit


def cleanup_browser_unit(unit, process):
    directory = browser_group(unit)
    # This is a separate disposable systemd cgroup, not merely a process group:
    # detached Chromium sessions and Xvfb inherit it and cannot escape by setsid.
    try:
        subprocess.run(['sudo','-n','systemctl','stop',unit],capture_output=True,timeout=20)
    except subprocess.TimeoutExpired:
        subprocess.run(['sudo','-n','systemctl','kill','--kill-whom=all','--signal=KILL',unit],
                       capture_output=True,timeout=10)
    deadline=time.monotonic()+5
    while directory.exists():
        try:
            events=dict(line.split() for line in (directory/'cgroup.events').read_text().splitlines())
        except FileNotFoundError:
            break
        if events.get('populated')=='0':break
        if time.monotonic()>=deadline:raise RuntimeError('Browser cgroup remained populated after stop')
        time.sleep(.1)
    if process.poll() is None:
        process.terminate()
        try:process.wait(timeout=3)
        except subprocess.TimeoutExpired:process.kill();process.wait(timeout=3)
    subprocess.run(['sudo','-n','systemctl','reset-failed',unit],capture_output=True,timeout=10)


def validate_result(value, unit, commit):
    from scripts.low_resource_sustained import outside_server
    if (value.get('outcome') != 'passed' or value.get('source_commit') != commit
            or value.get('unit') != unit or value.get('node_count') != 100
            or value.get('polling_contract') != 'visible-only-state-status'
            or value.get('broker_cleanup_complete') is not True or value.get('cleanup_complete') is not True
            or [p.get('name') for p in value.get('phases', [])] != list(PHASES)):
        raise RuntimeError('Incomplete interaction evidence')
    outside_server(unit, value.get('client_cgroup', ''))
    expected_unit=unit.removesuffix('.service')+'-browser.service'
    if value.get('client_cgroup') != '0::/system.slice/'+expected_unit:
        raise RuntimeError('Browser did not run in its separate scoped unit')
    driver=value.get('native_focus_driver',{})
    if (driver.get('playwright_version')!='1.57.0' or driver.get('changes')!=1
            or driver.get('enabled') is not False or driver.get('installed_driver_modified') is not False
            or not re.fullmatch('[0-9a-f]{64}',driver.get('source_sha256',''))
            or not re.fullmatch('[0-9a-f]{64}',driver.get('copy_sha256',''))
            or driver['source_sha256']==driver['copy_sha256']):
        raise RuntimeError('Native visibility driver provenance missing')
    from scripts.low_resource_acceptance import assert_no_oom
    external=value.get('external_metrics', {})
    if (type(external.get('memory.peak')) is not int or external['memory.peak'] <= 0
            or type(external.get('cpu.stat',{}).get('usage_usec')) is not int
            or external['cpu.stat']['usage_usec'] < 0
            or not {'oom','oom_kill'} <= external.get('memory.events',{}).keys()):
        raise RuntimeError('Missing complete external cgroup accounting')
    assert_no_oom(external)
    for phase in value['phases']:
        elapsed = phase.get('wall_seconds')
        if (phase.get('outcome') != 'passed' or type(elapsed) not in (int, float)
                or not math.isfinite(elapsed) or elapsed <= 0
                or phase['name'] != 'logout' and elapsed < SECONDS):
            raise RuntimeError('Missing or short interaction phase')
    for row in value['phases'][:3]:
        expected = ({'tab1':'visible'} if row['name']=='visible_tab' else
                    {'tab1':'hidden'} if row['name']=='hidden_tab' else {'tab1':'hidden','tab2':'visible'})
        if (row.get('visibility') != expected or row.get('visibility_samples', 0) < 50
                or row.get('request_failures') != 0 or row.get('pending_requests') != 0
                or any(status != '200' for status in row.get('statuses', {}))
                or row['name'] != 'hidden_tab' and sum(row.get('requests', {}).values()) <= 0):
            raise RuntimeError('Invalid actual visibility or polling evidence')
        requests=row.get('requests',{})
        statuses=row.get('statuses',{})
        if (any(type(v) is not int or v < 0 for v in (*requests.values(),*statuses.values()))
                or sum(requests.values()) != sum(statuses.values())):
            raise RuntimeError('Polling requests and completed responses do not reconcile')
        visible='tab1' if row['name']=='visible_tab' else 'tab2' if row['name']=='two_tabs' else None
        if visible and any(requests.get(visible+':'+path,0) <= 0 for path in POLL_PATHS):
            raise RuntimeError('Visible tab did not poll both status endpoints')
        if row['name'] in ('hidden_tab','two_tabs') and any(key.startswith('tab1:') and count for key,count in requests.items()):
            raise RuntimeError('Hidden tab continued periodic status polling')
        peaks=row.get('max_inflight_by_endpoint',{})
        if set(peaks)!=set(requests) or any(type(n) is not int or n!=1 for n in peaks.values()):
            raise RuntimeError('Status endpoint requests overlapped')
    for index,row in enumerate(value['phases'][3:7]):
        elapsed=row.get('active_seconds')
        if (row.get('path') != EXPORT_PATHS[index] or row.get('requests_completed') != 600
                or row.get('concurrency') != 10 or row.get('errors') != 0
                or type(elapsed) not in (int,float) or not math.isfinite(elapsed) or elapsed < SECONDS
                or type(row.get('body_bytes')) is not int or row['body_bytes'] <= 0
                or not re.fullmatch(r'[0-9a-f]{64}', row.get('body_sha256',''))):
            raise RuntimeError('Incomplete fixed export evidence')
    logout=value['phases'][-1]
    if (logout.get('both_tabs_redirected') is not True or logout.get('polling_stopped') is not True
            or logout.get('statuses',{}).get('401',0)<1):
        raise RuntimeError('Logout did not stop both tabs')
    validate_logout_trace(logout,value.get('session_trace',{}))
    if value.get('external_requests') != 0 or value.get('browser_exceptions') != 0:
        raise RuntimeError('Browser isolation or runtime exception failed')
    usage = value.get('client_usage', {})
    for field in ('self_user_seconds', 'self_system_seconds', 'children_user_seconds', 'children_system_seconds'):
        number = usage.get(field)
        if type(number) not in (int, float) or not math.isfinite(number) or number < 0:
            raise RuntimeError('Missing client CPU accounting')
    for field in ('self_max_rss_kib', 'children_max_rss_kib'):
        if type(usage.get(field)) is not int or usage[field] <= 0:
            raise RuntimeError('Missing client RSS accounting')


def validate_service_samples(samples):
    from scripts.low_resource_acceptance import assert_no_oom
    if not isinstance(samples,list) or not samples:raise RuntimeError('Service samples missing')
    previous=-1
    grouped={name:[] for name in PHASES}
    for sample in samples:
        timestamp=sample.get('observed_monotonic')
        if type(timestamp) not in (int,float) or not math.isfinite(timestamp) or timestamp < previous:
            raise RuntimeError('Invalid service sample timestamp')
        previous=timestamp
        if (type(sample.get('memory.peak')) is not int or sample['memory.peak'] <= 0
                or type(sample.get('memory.current')) is not int or sample['memory.current'] < 0
                or type(sample.get('cpu.stat',{}).get('usage_usec')) is not int
                or not {'anon','file'} <= sample.get('memory.stat',{}).keys()
                or not {'oom','oom_kill'} <= sample.get('memory.events',{}).keys()
                or not sample.get('processes')):
            raise RuntimeError('Incomplete service sample accounting')
        assert_no_oom(sample)
        if sample.get('driver_phase') in grouped:grouped[sample['driver_phase']].append(timestamp)
    for name,times in grouped.items():
        if not times or name!='logout' and (len(times)<5 or times[-1]-times[0]<40):
            raise RuntimeError('Service sampling did not cover every required phase')


def request_interactions(work, output, unit, commit, origin, ca, cert, cookie, observe):
    request, response = work/'interactions.request.json', work/'interactions.response.json'
    if request.exists() or response.exists(): raise RuntimeError('Refusing reused interaction evidence')
    atomic_json(request, dict(unit=unit, source_commit=commit, origin=origin, ca=str(ca), cert=str(cert),
        cookie=cookie, node_count=100, phase_seconds=SECONDS))
    start = time.monotonic()
    while not response.exists():
        if time.monotonic()-start > 930: raise RuntimeError('Interaction response deadline exceeded')
        phase = 'starting'
        progress = work/'interactions.progress.json'
        if progress.exists(): phase = json.loads(progress.read_text()).get('phase', 'unknown')
        observe(phase)
        time.sleep(1)
    value = json.loads(response.read_text())
    validate_result(value, unit, commit)
    return value


NATIVE_BACKGROUND_ARGS = (
    '--disable-background-timer-throttling',
    '--disable-backgrounding-occluded-windows',
    '--disable-renderer-backgrounding',
)


_FOCUS_ENABLE = 'this._client.send("Emulation.setFocusEmulationEnabled", { enabled: true })'
_FOCUS_DISABLE = _FOCUS_ENABLE.replace('enabled: true', 'enabled: false')


@contextmanager
def native_playwright_driver(work, report):
    """Do not enable focus capture in Playwright's original CDP session.

    A second CDP session cannot release the first session's browser-side capture
    handle. Use an isolated copy of the pinned automation driver, with exactly
    one boolean changed; never modify the installed package or Chromium binary.
    """
    from importlib.metadata import version
    from playwright._impl._driver import compute_driver_executable
    from playwright._impl import _transport
    from unittest.mock import patch
    if version('playwright') != '1.57.0':raise RuntimeError('Native visibility driver requires exact Playwright 1.57.0')
    node, cli = compute_driver_executable()
    source=Path(cli).parent
    copied=work/'native-playwright-package'
    if copied.exists():raise RuntimeError('Refusing to overwrite a driver fixture')
    shutil.copytree(source,copied,symlinks=True)  # Includes upstream license files.
    try:
        path=copied/'lib/server/chromium/crPage.js'
        original=path.read_bytes();text=original.decode('utf-8')
        if text.count(_FOCUS_ENABLE)!=1:raise RuntimeError('Pinned focus initialization is not uniquely identified')
        changed=text.replace(_FOCUS_ENABLE,_FOCUS_DISABLE).encode('utf-8')
        path.write_bytes(changed)
        report['native_focus_driver']=dict(playwright_version='1.57.0',changes=1,
            source_sha256=hashlib.sha256(original).hexdigest(),copy_sha256=hashlib.sha256(changed).hexdigest(),
            enabled=False,installed_driver_modified=False)
        with patch.object(_transport,'compute_driver_executable',return_value=(node,str(copied/Path(cli).name))):
            yield
    finally:
        shutil.rmtree(copied)


def wait_native_visibility(pages, expected, *, timeout=5):
    deadline=time.monotonic()+timeout
    while True:
        states={name:page.evaluate('document.visibilityState') for name,page in pages.items()}
        if states==expected:return states
        if time.monotonic()>=deadline:raise RuntimeError('Native tab visibility transition failed: '+repr(states))
        next(iter(pages.values())).wait_for_timeout(100)


INITIAL_PATHS=('/api/auth/me',*POLL_PATHS,'/api/inbounds','/api/subscription/mihomo-warnings',
               '/api/routing/mihomo/catalog','/api/routing/mihomo','/api/routing/mihomo/preview')
TRACE_PATHS=(*INITIAL_PATHS,'/api/auth/logout')


class SessionTrace:
    """Safe lifecycle evidence from page creation, including phase boundaries."""
    def __init__(self,current):
        self.current=current;self.requests=[];self.navigations=[];self.pending={};self.pages={}
    def attach(self,page,tab):
        key=len(self.pages)+1;state={'tab':tab,'generation':0,'path':'other'};self.pages[key]=state
        def phase():return self.current['row']['name'] if self.current['row'] else None
        def navigated(frame):
            if frame!=page.main_frame:return
            path=urlsplit(frame.url).path
            state.update(generation=state['generation']+1,path=path if path in ('/ui/','/login') else 'other')
            self.navigations.append(dict(page_id=key,tab=tab,document_generation=state['generation'],
                document_path=state['path'],monotonic=time.monotonic()))
        def requested(request):
            path=urlsplit(request.url).path
            if path not in TRACE_PATHS:return
            row=dict(id=len(self.requests)+1,page_id=key,tab=tab,document_generation=state['generation'],
                document_path=state['path'],path=path,method=request.method,started_monotonic=time.monotonic(),
                phase_at_start=phase(),completed=False,failed=False)
            self.requests.append(row);self.pending[request]=row
        def responded(response):
            row=self.pending.get(response.request)
            if row is not None:row.update(status=response.status,response_monotonic=time.monotonic())
        def finished(request):
            row=self.pending.pop(request,None)
            if row is not None:row.update(completed=True,finished_monotonic=time.monotonic(),phase_at_finish=phase())
        def failed(request):
            row=self.pending.pop(request,None)
            if row is not None:row.update(failed=True,failed_monotonic=time.monotonic(),phase_at_finish=phase())
        page.on('framenavigated',navigated);page.on('request',requested);page.on('response',responded)
        page.on('requestfinished',finished);page.on('requestfailed',failed)
        return key
    def ready(self,key):
        state=self.pages[key]
        rows=[r for r in self.requests if r['page_id']==key and r['document_generation']==state['generation']]
        if (state['path']!='/ui/' or any(r['page_id']==key for r in self.pending.values())
                or any(r['failed'] for r in rows)):
            return None
        complete={r['path'] for r in rows if r['completed'] and r.get('status')==200 and r.get('method')=='GET'}
        if not set(INITIAL_PATHS)<=complete:return None
        return dict(page_id=key,tab=state['tab'],document_generation=state['generation'],document_path='/ui/',
                    ready_monotonic=time.monotonic(),completed_paths=list(INITIAL_PATHS))
    def wait_ready(self,page,key):
        from playwright.sync_api import expect
        expect(page.locator('.el-dropdown-link')).to_contain_text('resource-admin',timeout=15000)
        deadline=time.monotonic()+15
        while True:
            ready=self.ready(key)
            if ready:return ready
            if time.monotonic()>=deadline:raise RuntimeError('Dashboard authentication/initial requests not complete')
            page.wait_for_timeout(50)
    def snapshot(self):return dict(requests=self.requests,navigations=self.navigations)


def validate_logout_trace(row,trace):
    def moment(value):return type(value) in (int,float) and math.isfinite(value) and value>=0
    prepared=row.get('prepared_pages',[]);requests=trace.get('requests',[]);navigations=trace.get('navigations',[])
    if len(prepared)!=2 or {p.get('tab') for p in prepared}!={'tab1','tab2'} or len({p.get('page_id') for p in prepared})!=2:raise RuntimeError('Logout did not start from two authenticated dashboards')
    for key in ('started_monotonic','logout_completed_monotonic','stopped_from','stopped_until'):
        if not moment(row.get(key)):raise RuntimeError('Missing logout timeline boundaries')
    if (row['logout_completed_monotonic']<row['started_monotonic'] or row['stopped_from']<row['logout_completed_monotonic']
            or row['stopped_until']-row['stopped_from']<11):raise RuntimeError('Incomplete logout stop interval')
    if row.get('pre_logout_visibility')!={'tab1':'hidden','tab2':'visible'}:raise RuntimeError('Logout visibility precondition missing')
    ids=[r.get('id') for r in requests]
    if len(ids)!=len(set(ids)):raise RuntimeError('Duplicate request evidence')
    for r in requests:
        if r.get('path') not in TRACE_PATHS or r.get('method') not in ('GET','POST') or not moment(r.get('started_monotonic')):
            raise RuntimeError('Invalid safe request timeline')
        if r.get('completed'):
            if (r.get('failed') or type(r.get('status')) is not int or not moment(r.get('response_monotonic'))
                    or not moment(r.get('finished_monotonic')) or not r['started_monotonic']<=r['response_monotonic']<=r['finished_monotonic']):
                raise RuntimeError('Request completion is not proven')
    def complete(r,status):return r.get('completed') is True and r.get('failed') is False and r.get('status')==status
    originals={(p.get('page_id'),p.get('document_generation')) for p in prepared}
    logout=[r for r in requests if r.get('document_path')=='/ui/' and (r.get('page_id'),r.get('document_generation')) in originals and r['path']=='/api/auth/logout' and r['method']=='POST' and complete(r,200)
            and r['started_monotonic']>=row['started_monotonic']]
    if len(logout)!=1:raise RuntimeError('Real successful logout request missing')
    if logout[0]['response_monotonic']>row['logout_completed_monotonic'] or logout[0]['finished_monotonic']>row['stopped_from']:
        raise RuntimeError('Logout completion contradicts request timeline')
    status_denials=[]
    for p in prepared:
        if (p.get('document_path')!='/ui/' or type(p.get('page_id')) is not int or type(p.get('document_generation')) is not int
                or not moment(p.get('ready_monotonic')) or p['ready_monotonic']>row['started_monotonic']
                or set(p.get('completed_paths',[]))!=set(INITIAL_PATHS)):raise RuntimeError('Invalid dashboard readiness')
        original=[r for r in requests if r.get('page_id')==p['page_id'] and r.get('tab')==p['tab'] and r.get('document_generation')==p['document_generation'] and r.get('document_path')=='/ui/']
        for path in INITIAL_PATHS:
            if not any(r['path']==path and r.get('method')=='GET' and complete(r,200) and r['finished_monotonic']<=p['ready_monotonic'] for r in original):
                raise RuntimeError('Dashboard initialization response missing')
        if any(r['started_monotonic']<=p['ready_monotonic'] and (not r.get('completed') or r.get('finished_monotonic',float('inf'))>p['ready_monotonic']) for r in original):
            raise RuntimeError('Dashboard was marked ready with pending requests')
        nav=[n for n in navigations if n.get('page_id')==p['page_id'] and n.get('tab')==p['tab'] and n.get('document_path')=='/login'
             and n.get('document_generation',0)>p['document_generation'] and moment(n.get('monotonic')) and n['monotonic']>=row['started_monotonic']]
        if not nav:raise RuntimeError('Login navigation missing')
        arrived=min(n['monotonic'] for n in nav)
        if arrived>row['logout_completed_monotonic']+15 or arrived>row['stopped_from']:raise RuntimeError('Logout navigation exceeded common deadline')
        denials=[r for r in original if r.get('method')=='GET' and complete(r,401) and r['response_monotonic']>=logout[0]['started_monotonic'] and r['finished_monotonic']<=arrived]
        if not any(r['path']=='/api/auth/me' for r in denials):raise RuntimeError('Dashboard current-session refusal before navigation missing')
        status_denials.extend(r for r in denials if r['path'] in POLL_PATHS)
    if not status_denials:raise RuntimeError('Original dashboard status refusal missing')
    if any(r['path'] in POLL_PATHS and row['stopped_from']<=r['started_monotonic']<=row['stopped_until'] for r in requests):
        raise RuntimeError('Status request during stopped observation')


def track_page(page, label, current, inflight, errors):
    page.on('pageerror', lambda error: errors.append(type(error).__name__))
    def requested(request):
        path=urlsplit(request.url).path; row=current['row']
        if row is not None and path in POLL_PATHS:
            key=label+':'+path; row['requests'][key]=row['requests'].get(key,0)+1
            inflight[request]={'row':row,'status':None,'endpoint':key}
            peaks=row.setdefault('max_inflight_by_endpoint',{})
            peaks[key]=max(peaks.get(key,0),sum(item['endpoint']==key for item in inflight.values()))
            row['max_inflight']=max(row['max_inflight'],len(inflight))
    def responded(response):
        item=inflight.get(response.request)
        if item is not None:item['status']=str(response.status)
    def finished(request):
        item=inflight.pop(request,None)
        if item is not None:
            row,status=item['row'],item['status']
            if status is None:row['request_failures']+=1
            else:row['statuses'][status]=row['statuses'].get(status,0)+1
    def failed(request):
        item=inflight.pop(request,None)
        if item is not None:item['row']['request_failures']+=1
    page.on('request',requested);page.on('response',responded)
    page.on('requestfinished',finished);page.on('requestfailed',failed)


def run(value, output, work):
    from scripts.low_resource_acceptance import require_hosted_runner
    from scripts.low_resource_sustained import membership, outside_server
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization
    from playwright.sync_api import sync_playwright, expect
    require_hosted_runner(); outside_server(value['unit'], membership())
    client_unit=value['unit'].removesuffix('.service')+'-browser.service'
    if membership() != '0::/system.slice/'+client_unit:
        raise RuntimeError('Interaction helper requires its separate browser cgroup')
    group=browser_group(client_unit)
    report = dict(schema=1, outcome='running', unit=value['unit'], source_commit=value['source_commit'],
        node_count=100, client_cgroup=membership(), phases=[], cleanup_complete=False,
        polling_contract='visible-only-state-status',
        scope='100 synthetic database nodes; actual headed browser visibility and fixed exports; not proxy throughput or certificate renewal',
        visibility_mode='native headed tabs; Playwright focus emulation disabled and background scheduling overrides removed',
        accounting='browser/export generator outside service cgroup; same hosted machine still shared')
    origin = value['origin']
    ca_context = ssl.create_default_context(cafile=value['ca'])
    cookie = value['cookie']
    external, errors = [], []
    current = {'row': None}
    trace=SessionTrace(current);report['session_trace']=trace.snapshot()
    inflight = {}
    def phase(name, action):
        row = dict(name=name, outcome='running', started_monotonic=time.monotonic(), requests={}, statuses={}, max_inflight=0, max_inflight_by_endpoint={}, request_failures=0)
        report['phases'].append(row); current['row'] = row
        atomic_json(work/'interactions.progress.json', {'phase':name})
        atomic_json(output, report)
        try:
            action(row); row['outcome'] = 'passed'
        except Exception as exc:
            row.update(outcome='failed', error_type=type(exc).__name__)
            raise
        finally:
            current['row'] = None
            if name in ('visible_tab','hidden_tab','two_tabs'):
                deadline=time.monotonic()+10
                while any(item['row'] is row for item in inflight.values()) and time.monotonic()<deadline:
                    context.pages[0].wait_for_timeout(50)
            row['pending_requests']=sum(item['row'] is row for item in inflight.values())
            row['wall_seconds'] = time.monotonic()-row['started_monotonic']
            atomic_json(output, report)
            atomic_json(work/'interactions.progress.json', {'phase':'between_phases'})
    def only_local(route):
        if route.request.url.startswith(origin+'/'): route.continue_()
        else:
            external.append(urlsplit(route.request.url).scheme + '://' + (urlsplit(route.request.url).hostname or ''))
            route.abort()
    def observe_pages(row, pages, expected):
        start=time.monotonic();row['visibility']={};row['visibility_samples']=0
        while time.monotonic()-start < SECONDS:
            states={name:page.evaluate('document.visibilityState') for name,page in pages.items()}
            row['visibility']=states;row['visibility_samples']+=1
            if states != expected: raise RuntimeError('Browser did not maintain real requested visibility')
            next(iter(pages.values())).wait_for_timeout(min(1000,max(1,(SECONDS-(time.monotonic()-start))*1000)))
        if external or errors: raise RuntimeError('Unexpected external request or browser exception')
    try:
        leaf=x509.load_pem_x509_certificate(Path(value['cert']).read_bytes())
        spki=leaf.public_key().public_bytes(serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo)
        pin=base64.b64encode(hashlib.sha256(spki).digest()).decode()
        with native_playwright_driver(work,report), sync_playwright() as p:
            browser=p.chromium.launch(headless=False,args=['--ignore-certificate-errors-spki-list='+pin],
                                      ignore_default_args=list(NATIVE_BACKGROUND_ARGS))
            try:
                context=browser.new_context(viewport={'width':1280,'height':900})
                context.route('**/*',only_local)
                context.add_cookies([dict(name='__Host-vui_session',value=cookie.split('=',1)[1],url=origin,
                                         secure=True,httpOnly=True,sameSite='Strict')])
                first=context.new_page();track_page(first,'tab1',current,inflight,errors);first_key=trace.attach(first,'tab1');first.goto(origin+'/ui/')
                expect(first.locator('.logo')).to_contain_text('V-UI')
                first.bring_to_front();wait_native_visibility({'tab1':first},{'tab1':'visible'})
                phase('visible_tab',lambda row:observe_pages(row,{'tab1':first},{'tab1':'visible'}))
                blank=context.new_page();blank.goto('about:blank')
                blank.bring_to_front();wait_native_visibility({'tab1':first},{'tab1':'hidden'})
                phase('hidden_tab',lambda row:observe_pages(row,{'tab1':first},{'tab1':'hidden'}))
                second=context.new_page();track_page(second,'tab2',current,inflight,errors);second_key=trace.attach(second,'tab2');second.goto(origin+'/ui/')
                expect(second.locator('.logo')).to_contain_text('V-UI')
                second.bring_to_front();wait_native_visibility({'tab1':first,'tab2':second},{'tab1':'hidden','tab2':'visible'})
                phase('two_tabs',lambda row:observe_pages(row,{'tab1':first,'tab2':second},{'tab1':'hidden','tab2':'visible'}))
                # Do not mix active browser polling into the standalone export windows.
                first.close();second.close();blank.close()
                for index,path in enumerate(EXPORT_PATHS):
                    def exports(row, path=path):
                        def get():
                            opener=build_opener(ProxyHandler({}),HTTPSHandler(context=ca_context),NoRedirect())
                            with opener.open(Request(origin+path,headers={'Cookie':cookie}),timeout=10) as response:
                                raw=response.read()
                                if response.status!=200:raise RuntimeError('Export status failed')
                                return raw
                        row.update(path=path,concurrency=10,planned_requests=600,requests_completed=0,
                                   errors=0,baseline_requests=0,duration_seconds=SECONDS)
                        try:
                            baseline=get();row['baseline_requests']=1
                        except Exception:
                            row['errors']+=1
                            raise
                        digest=hashlib.sha256(baseline).hexdigest()
                        row.update(body_bytes=len(baseline),body_sha256=digest)
                        if path.endswith('/raw') and len(base64.b64decode(baseline).decode().splitlines())!=100:
                            raise RuntimeError('Raw export omitted fixture nodes')
                        if path.endswith('/sing-box.json') and sum(x.get('type')=='vless' for x in json.loads(baseline)['outbounds'])!=100:
                            raise RuntimeError('Sing-box export omitted fixture nodes')
                        start=time.monotonic();lock=threading.Lock()
                        def one(_):
                            try:
                                if hashlib.sha256(get()).hexdigest()!=digest:raise RuntimeError('Export body changed')
                                with lock:row['requests_completed']+=1
                            except Exception:
                                with lock:row['errors']+=1
                                raise
                        with ThreadPoolExecutor(max_workers=10) as pool:
                            for tick in range(SECONDS):
                                time.sleep(max(0,start+tick-time.monotonic()))
                                if time.monotonic()-(start+tick)>=1:raise RuntimeError('Export missed fixed-rate slot')
                                list(pool.map(one,range(10)))
                                row['active_seconds']=time.monotonic()-start
                                atomic_json(output,report)
                        time.sleep(max(0,start+SECONDS-time.monotonic()))
                        row['active_seconds']=time.monotonic()-start
                    phase('export_'+str(index),exports)
                first=context.new_page();track_page(first,'tab1',current,inflight,errors);first_key=trace.attach(first,'tab1');first.goto(origin+'/ui/')
                second=context.new_page();track_page(second,'tab2',current,inflight,errors);second_key=trace.attach(second,'tab2');second.goto(origin+'/ui/')
                prepared=[trace.wait_ready(first,first_key),trace.wait_ready(second,second_key)]
                second.bring_to_front();wait_native_visibility({'tab1':first,'tab2':second},{'tab1':'hidden','tab2':'visible'})
                prepared=[trace.wait_ready(first,first_key),trace.wait_ready(second,second_key)]
                def logout(row):
                    row.update(prepared_pages=prepared,pre_logout_visibility={'tab1':first.evaluate('document.visibilityState'),'tab2':second.evaluate('document.visibilityState')})
                    status=first.evaluate("""async () => {
                        const response = await fetch('/api/auth/logout', {
                            method: 'POST', credentials: 'same-origin',
                            headers: {'Content-Type': 'application/json', 'X-VUI-Request': '1'},
                            body: '{}'
                        });
                        await response.arrayBuffer();
                        return response.status;
                    }""")
                    if status!=200:raise RuntimeError('Synthetic logout failed')
                    observed_deadline=time.monotonic()+5
                    while True:
                        completed=[r for r in trace.requests if r['page_id']==first_key and r['path']=='/api/auth/logout'
                                   and r['started_monotonic']>=row['started_monotonic'] and r['completed']]
                        if completed:break
                        if time.monotonic()>=observed_deadline:raise RuntimeError('Logout request completion event missing')
                        first.wait_for_timeout(20)
                    row['logout_completed_monotonic']=completed[0]['finished_monotonic']
                    deadline=row['logout_completed_monotonic']+15
                    for page in (first,second):
                        page.wait_for_url('**/login',timeout=max(1,(deadline-time.monotonic())*1000))
                    row['stopped_from']=time.monotonic()
                    count=sum(row['requests'].values());first.wait_for_timeout(11000)
                    row['stopped_until']=time.monotonic()
                    if sum(row['requests'].values())!=count:raise RuntimeError('Polling continued after login redirect')
                    row.update(both_tabs_redirected=True,polling_stopped=True)
                phase('logout',logout)
                if external or errors:raise RuntimeError('Unexpected browser activity')
            finally: browser.close()
        report.update(outcome='passed',cleanup_complete=True,external_requests=0,browser_exceptions=0)
    except Exception as exc:
        report.update(outcome='failed',error_type=type(exc).__name__,error=str(exc))
    finally:
        from scripts.low_resource_acceptance import metrics
        report['external_metrics']=metrics(group)
        report['external_accounting_scope']='Xvfb/browser/helper cgroup at helper exit; final Xvfb wrapper shutdown is not sampled'
        report['external_limits']={name:(group/name).read_text().strip() for name in ('memory.max','memory.swap.max','cpu.max')}
        own,children=resource.getrusage(resource.RUSAGE_SELF),resource.getrusage(resource.RUSAGE_CHILDREN)
        report['client_usage']=dict(self_user_seconds=own.ru_utime,self_system_seconds=own.ru_stime,self_max_rss_kib=own.ru_maxrss,
            children_user_seconds=children.ru_utime,children_system_seconds=children.ru_stime,children_max_rss_kib=children.ru_maxrss,
            rss_scope='Linux getrusage high water, not additive peak; external_metrics includes Xvfb and detached browser children')
        atomic_json(output,report)
    return 0 if report['outcome']=='passed' else 1


if __name__=='__main__':
    sys.path.insert(0,str(SOURCE))
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();os.umask(0o077)
    raise SystemExit(run(json.loads(args.request.read_text()),args.output,args.request.parent))
