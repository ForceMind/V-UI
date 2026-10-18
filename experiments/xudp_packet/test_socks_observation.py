"""Synthetic verifier negatives and real local syscall controls; no core/CI run."""
import ast
from contextlib import ExitStack
from copy import deepcopy
import ctypes
import errno
import fcntl
import inspect
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import select
import sys
import tempfile
import textwrap
import time
import types
import unittest
from unittest.mock import Mock, patch

import packet_boundary as p
import socks_observation as o
import verify_socks_observation as v

ROOT = Path(__file__).resolve().parents[2]


def fixture(mode='idle'):
    case = next(c for c in p.plan('D') if c['protocol'] == 'aes-128-gcm' and c['mode'] == mode)
    role = {'case': case['id'], 'role': o.ROLE, 'origin': 'candidate', 'classification': 'experimental',
            'parser_passed': True, 'sha256': 'a'*64, 'version': 'experiment',
            'launches': [{'pid': 123, 'birth_time': 1, 'ready': True, 'sha256': 'a'*64}]}
    source = {'vui_commit': '1'*40, 'vui_tree': '2'*40, 'files': {'fixture': '3'*64}}
    def desc(fd, inode, port, peer=None):
        return {'fd': fd, 'inode': inode, 'local': ['127.0.0.1', port], 'peer': peer}
    initial = {'label': 'initial', 'control': desc(5, 500, 55000, ['127.0.0.1', 1080]),
               'application': desc(6, 600, 56000), 'relay': ['127.0.0.1', 57000], 'created': 9}
    recovery = {'label': 'recovery', 'control': desc(7, 700, 55001, ['127.0.0.1', 1080]),
                'application': desc(8, 800, 56001), 'relay': ['127.0.0.1', 57001], 'created': 16.05}
    associations = [initial] + ([recovery] if mode == 'close' else [])
    def sock(fd, inode, protocol, local, remote, state):
        return {'fd': fd, 'inode': inode, 'protocol': protocol, 'family': 'ipv4',
                'local': v.endpoint(local), 'remote': remote, 'proc_state': state, 'psutil_state': 'NONE'}
    controls = [sock(11, 1001, 'tcp', initial['control']['peer'], v.endpoint(initial['control']['local']), '01'),
                sock(12, 1002, 'udp', initial['relay'], {'host': '0.0.0.0', 'port': 0}, '07')]
    phases = [('after_large_business', 10.01)] + ([('after_explicit_close', 10.03)] if mode == 'close' else [])
    phases += [('release_window', 11), ('release_window', 12), ('window_complete', 16.01),
               ('before_recover', 16.1), ('after_recover' if mode == 'close' else 'failure', 20.31), ('after_local_cleanup', 20.4)]
    samples, events = [], []
    closed_desc = {'fd': -1, 'inode': None, 'local': None, 'peer': None}
    exe = {'device': 1, 'inode': 2, 'size': 3, 'mtime_ns': 4, 'ctime_ns': 5}
    for phase, start in phases:
        sample = {'phase': phase, 'started': start, 'completed': start+.01, 'associations': []}
        if phase in ('after_large_business', 'release_window', 'window_complete'):
            hp = phase if phase == 'after_large_business' else 'large_then_'+mode+('_window_complete' if phase == 'window_complete' else '')
            sample['hop_sample'] = {'started': start-.005, 'completed': start-.001}
            events.append({'kind': 'socket_snapshot', 'phase': hp, 'snapshot': {
                'monotonic': start-.005, 'sample_completed_monotonic': start-.001}})
        rows = deepcopy(controls) if phase == 'after_large_business' or (mode == 'idle' and start < 12) else []
        fds = [{'fd': r['fd'], 'inode': r['inode']} for r in rows]
        sample['device'] = {'role': o.ROLE, 'pid': 123, 'birth_time': 1, 'birth_time_after': 1,
                            'started': start+.001, 'completed': start+.002, 'sockets': rows,
                            'fd_before': fds, 'fd_after': deepcopy(fds), 'races': [],
                            'executable': exe, 'executable_after': exe}
        if phase in ('after_large_business', 'after_local_cleanup'):
            sample['executable_sha256'] = role['sha256']
        current = associations if phase in ('before_recover', 'after_recover', 'failure', 'after_local_cleanup') else [initial]
        for a in current:
            closed = phase == 'after_local_cleanup' or (mode == 'close' and a['label'] == 'initial' and phase != 'after_large_business')
            descriptor = closed_desc if closed else a['control']
            state = 'local_closed' if closed else 'eof' if mode == 'idle' and start >= 12 else 'would_block'
            sample['associations'].append({'label': a['label'], 'control_before': deepcopy(descriptor),
                'control_after': deepcopy(descriptor), 'application': deepcopy(closed_desc if closed else a['application']),
                'relay': a['relay'], 'peek': {'fd': descriptor['fd'], 'state': state,
                    'count': None if closed else 0 if state == 'eof' else -1,
                    'errno': errno.EAGAIN if state == 'would_block' else None}})
        samples.append(sample)
    data = {'schema_version': 1, 'kind': o.KIND, 'case': case['id'], 'mode': mode, 'source_identity': source,
            'device_role': role, 'status': 'complete', 'errors': [], 'product_qualified': False,
            'capacity_evaluated': False, 'associations': associations, 'samples': samples,
            'last_business_reply': 10, 'deadline': 16,
            'cleanup': [{'label': a['label'], 'control_fd': -1, 'application_fd': -1} for a in associations]}
    if mode == 'close': data['explicit_close'] = {'started': 10.021, 'completed': 10.025}
    packets = []
    for index, spec in enumerate(case['packets']):
        a = associations[index if mode == 'close' else 0]
        packets.append({'leaf': case['id']+'/'+spec['id'], 'first_udp': index == 0 or mode == 'close',
            'send_state': 'sent', 'application_socket': a['application']['local'], 'socks_relay': a['relay'],
            'full_delivery_passed': index == 0 or mode == 'close',
            'reply_monotonic': 10 if index == 0 else 20.3, 'terminal_monotonic': 10 if index == 0 else 20.3,
            'target_window': {'started': 9 if index == 0 else 16.2}})
    result = {'process_identity': [{'pid': 123, 'birth_time': 1}], 'packets': packets, 'cleanup': {
        'processes': [{'pid': 123, 'reaped': True, 'returncode': 0}],
        'threads_joined': True, 'application_sockets_closed': True}}
    return data, case, result, [role], source, {'events': events}


class PeekTests(unittest.TestCase):
    def pair(self):
        a, b = socket.socketpair()
        self.addCleanup(a.close); self.addCleanup(b.close)
        a.settimeout(3)
        return a, b

    def test_would_block_is_prompt_and_preserves_timeout_flags_fd(self):
        a, _ = self.pair(); before = fcntl.fcntl(a.fileno(), fcntl.F_GETFL)
        fd = a.fileno(); start = time.monotonic()
        self.assertEqual(o.peek(a), {'fd': fd, 'state': 'would_block', 'count': -1, 'errno': errno.EAGAIN})
        self.assertLess(time.monotonic()-start, .25)
        self.assertEqual(a.gettimeout(), 3)
        self.assertEqual(fcntl.fcntl(fd, fcntl.F_GETFL), before)
        self.assertEqual(a.fileno(), fd)

    def test_repeated_data_peek_does_not_consume_or_copy_payload(self):
        a, b = self.pair(); b.sendall(b'private-test-byte')
        for _ in range(3):
            ctypes.set_errno(errno.EBADF)
            self.assertEqual(o.peek(a), {'fd': a.fileno(), 'state': 'data', 'count': 1, 'errno': None})
        self.assertEqual(a.recv(32), b'private-test-byte')
        self.assertEqual(o.peek(a)['state'], 'would_block')

    def test_eof_differs_from_local_closed(self):
        a, b = self.pair(); b.close()
        self.assertEqual(o.peek(a), {'fd': a.fileno(), 'state': 'eof', 'count': 0, 'errno': None})
        a.close()
        self.assertEqual(o.peek(a), {'fd': -1, 'state': 'local_closed', 'count': None, 'errno': None})

    def test_ebadf_is_error_never_peer_eof(self):
        fd = os.dup(0); os.close(fd)
        self.assertEqual(o.peek(types.SimpleNamespace(fileno=lambda: fd)),
                         {'fd': fd, 'state': 'error', 'count': -1, 'errno': errno.EBADF})

    def test_unsupported_platform_fails_without_python_recv_fallback(self):
        with patch.object(o.sys, 'platform', 'win32'):
            with self.assertRaisesRegex(ValueError, 'requires Linux'): o.peek(Mock())


class HelperTests(unittest.TestCase):
    def helper(self):
        # Execute just the frozen class with fake dependencies, never import app
        # startup or run a proxy/core. This also pins construction cleanup.
        tree = ast.parse((ROOT/'tests/udp_dns_capability_helpers.py').read_text())
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'SocksDatagrams')
        control, udp = Mock(), Mock()
        control.__enter__ = Mock(return_value=control); control.__exit__ = Mock()
        udp.__enter__ = Mock(return_value=udp); udp.__exit__ = Mock()
        sock = types.SimpleNamespace(create_connection=Mock(return_value=control), socket=Mock(return_value=udp),
                                     AF_INET=socket.AF_INET, SOCK_DGRAM=socket.SOCK_DGRAM)
        ns = {'ExitStack': ExitStack, 'socket': sock, 'CLIENT': '127.0.0.1',
              '_recv_exact': Mock(side_effect=[b'\x05\x00', b'\x05\x00\x00\x01']),
              '_socks_address_from_stream': Mock(return_value=('127.0.0.1', 12345))}
        exec(compile(ast.Module(body=[cls], type_ignores=[]), 'frozen-helper-test', 'exec'), ns)
        return types.SimpleNamespace(**ns), cls, control, udp

    def test_subclass_is_only_control_reference_delta(self):
        helper, original, _, _ = self.helper()
        cls = o.observable_socks_class(helper)
        candidate = ast.parse(textwrap.dedent(inspect.getsource(cls))).body[0]
        init = next(n for n in candidate.body if isinstance(n, ast.FunctionDef) and n.name == '__init__')
        class Normalize(ast.NodeTransformer):
            def visit_Attribute(self, n):
                n = self.generic_visit(n)
                return ast.copy_location(ast.Name(id=n.attr, ctx=n.ctx), n) if isinstance(n.value, ast.Name) and n.value.id == 'helper' else n
            def visit_Assign(self, n):
                if len(n.targets) == 1 and isinstance(n.targets[0], ast.Attribute) and n.targets[0].attr == 'control': return None
                return self.generic_visit(n)
        init = Normalize().visit(init)
        frozen = next(n for n in original.body if isinstance(n, ast.FunctionDef) and n.name == '__init__')
        self.assertEqual(ast.dump(init), ast.dump(frozen))
        for name in ('send', 'receive', 'close', '__enter__', '__exit__'):
            self.assertIs(getattr(cls, name), getattr(helper.SocksDatagrams, name))

    def test_success_retains_control_and_close_cleans_both(self):
        h, _, control, udp = self.helper(); assoc=o.observable_socks_class(h)(1080)
        self.assertIs(assoc.control, control)
        h.socket.create_connection.assert_called_once_with(('127.0.0.1', 1080), timeout=3)
        self.assertEqual(control.sendall.call_count, 2)
        self.assertEqual(assoc.sent, [])
        assoc.close(); control.__exit__.assert_called_once(); udp.__exit__.assert_called_once()

    def test_negotiation_failure_closes_control(self):
        h, _, control, udp = self.helper(); h._recv_exact.side_effect=[b'bad']
        with self.assertRaises(AssertionError): o.observable_socks_class(h)(1080)
        control.__exit__.assert_called_once(); h.socket.socket.assert_not_called()

    def test_udp_bind_failure_closes_both(self):
        h, _, control, udp = self.helper(); udp.bind.side_effect=OSError('synthetic bind failure')
        with self.assertRaises(OSError): o.observable_socks_class(h)(1080)
        control.__exit__.assert_called_once(); udp.__exit__.assert_called_once()


class SamplerTests(unittest.TestCase):
    def test_actual_process_role_fd_inode_and_closed_socket_absence(self):
        import psutil
        c = p.load_reuse(ROOT)
        script = """import socket,sys
listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(1)
relay=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);relay.bind(('127.0.0.1',0))
print(listener.getsockname()[1],flush=True)
accepted,_=listener.accept()
print(accepted.fileno(),relay.fileno(),flush=True)
sys.stdin.readline();accepted.close();relay.close();print('closed',flush=True)
sys.stdin.readline();listener.close()
"""
        proc=subprocess.Popen([sys.executable,'-c',script],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        def line():
            self.assertTrue(select.select([proc.stdout],[],[],3)[0], 'Owned observation fixture did not respond')
            return proc.stdout.readline().strip()
        try:
            port=int(line())
            with socket.create_connection(('127.0.0.1',port),timeout=3):
                fds=list(map(int,line().split()))
                launch={'pid':proc.pid,'birth_time':psutil.Process(proc.pid).create_time()}
                core=types.SimpleNamespace(process=proc)
                sample=o.device_snapshot(core,psutil,c.observer,launch)
                self.assertEqual(sample['role'],'device-singbox');self.assertEqual(sample['races'],[])
                wanted={r['inode'] for r in sample['sockets'] if r['fd'] in fds}
                self.assertEqual(len(wanted),2)
                proc.stdin.write('close\n');proc.stdin.flush();self.assertEqual(line(),'closed')
                after=o.device_snapshot(core,psutil,c.observer,launch)
                self.assertEqual(after['races'],[])
                self.assertFalse(wanted & {r['inode'] for r in after['fd_after']})
                proc.stdin.write('finish\n');proc.stdin.flush()
                self.assertEqual(proc.wait(timeout=3),0)
        finally:
            if proc.poll() is None:proc.kill()
            proc.wait(timeout=3)
            proc.stdin.close();proc.stdout.close();proc.stderr.close()

    def test_identity_read_failure_preserved_without_retry_or_packet_action(self):
        core = types.SimpleNamespace(process=types.SimpleNamespace(pid=123, poll=lambda: None))
        process = types.SimpleNamespace(create_time=lambda: 1)
        psutil = types.SimpleNamespace(Process=lambda pid: process)
        with patch.object(o, 'exe_stat', side_effect=PermissionError('synthetic proc denial')) as read:
            with self.assertRaises(PermissionError):
                o.device_snapshot(core, psutil, Mock(), {'pid': 123, 'birth_time': 1})
        read.assert_called_once_with(123)

    def test_capture_error_and_race_persist_inconclusive_without_retry(self):
        for failure in ('error', 'race'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as name:
                args = fixture(); role=args[3][0]
                obs=o.Observation(Path(name)/o.FILE,args[1],None,None,None,args[4],role)
                sample=args[0]['samples'][0]['device']
                sample['races']=[{'kind':'fd_changed'}]
                effect={'side_effect': PermissionError('synthetic proc denial')} if failure=='error' else {'return_value':sample}
                with patch.object(o,'device_snapshot',**effect) as reader:
                    obs.capture('failure');obs.finish()
                self.assertEqual(reader.call_count,1)
                saved=p.json_read(Path(name)/o.FILE)
                self.assertEqual(saved['status'],'inconclusive' if failure=='error' else 'complete_with_inconclusive_samples')
                self.assertEqual(len(saved['samples']),1)
                if failure=='error':self.assertEqual(saved['samples'][0]['error']['type'],'PermissionError')


class VerifierTests(unittest.TestCase):
    def test_idle_eof_diagnostic_does_not_promote_business_red(self):
        result = v.verify_case(*fixture())
        self.assertEqual(result['association_state_before_recovery'], 'peer_eof_and_device_control_relay_absent')
        self.assertFalse(result['business_recovery_passed'] or result['capacity_evaluated'] or result['product_qualified'])

    def test_explicit_close_is_separate_from_peer_eof(self):
        result = v.verify_case(*fixture('close'))
        self.assertEqual(result['association_state_before_recovery'], 'explicit_application_close')
        self.assertTrue(result['business_recovery_passed'])

    def test_eagain_data_and_error_are_not_eof(self):
        for state, count, error in [('would_block', -1, errno.EAGAIN), ('data', 1, None), ('error', -1, errno.ECONNRESET)]:
            data = fixture()
            for s in data[0]['samples']:
                peek = s['associations'][0]['peek']
                if peek['state'] == 'eof': peek.update(state=state, count=count, errno=error)
            self.assertEqual(v.verify_case(*data)['association_state_before_recovery'], 'no_joint_peer_eof_and_device_absence')

    def test_missing_reordered_or_relabelled_sample_rejected(self):
        for change in [lambda d:d['samples'].pop(1), lambda d:d['samples'].reverse(),
                       lambda d:d['samples'][0].update(phase='failure'), lambda d:d.update(status='collecting'),
                       lambda d:d['errors'].append({'type':'OSError','message':'failure'}),
                       lambda d:d['samples'][1].update(error={'type':'OSError','message':'proc denied'})]:
            args=fixture();change(args[0])
            with self.subTest(change=change), self.assertRaises(ValueError):v.verify_case(*args)

    def test_role_pid_birth_executable_and_source_spoofs_rejected(self):
        changes = [lambda d:d['samples'][0]['device'].update(role='domestic'),
                   lambda d:d['samples'][0]['device'].update(pid=124),
                   lambda d:d['samples'][0]['device'].update(birth_time_after=2),
                   lambda d:d['samples'][0].update(executable_sha256='b'*64),
                   lambda d:d['samples'][1]['device'].update(executable={'inode':99}),
                   lambda d:d.update(source_identity={'fake':True}),
                   lambda d:d.update(device_role={'role':'domestic'}),
                   lambda d:d.update(product_qualified=True)]
        for change in changes:
            args=deepcopy(fixture());change(args[0])
            with self.subTest(change=change), self.assertRaises((ValueError, KeyError)):v.verify_case(*args)

    def test_missing_warm_wrong_control_parent_or_relay_and_dup_rejected(self):
        changes = [lambda d:d['samples'][0]['device']['sockets'].pop(),
                   lambda d:d['samples'][0]['device']['sockets'][0].update(remote={'host':'127.0.0.1','port':4444}),
                   lambda d:d['samples'][0]['device']['sockets'][1].update(local={'host':'127.0.0.1','port':9999}),
                   lambda d:d['samples'][0]['device']['sockets'].append(deepcopy(d['samples'][0]['device']['sockets'][0])),
                   lambda d:d['samples'][0]['device']['fd_before'].append({'fd':11,'inode':1001})]
        for change in changes:
            args=deepcopy(fixture());change(args[0])
            with self.subTest(change=change), self.assertRaises(ValueError):v.verify_case(*args)

    def test_race_fd_reuse_and_pid_read_errors_inconclusive(self):
        changes = [lambda d:d['samples'][2]['device']['races'].append({'kind':'fd_changed'}),
                   lambda d:d['samples'][0]['device']['fd_after'][0].update(inode=9999),
                   lambda d:d['samples'][1].update(error={'type':'PermissionError','message':'proc denied'}),
                   lambda d:d['samples'][1]['device']['races'].append({'kind':'permission_error'})]
        for change in changes:
            args=deepcopy(fixture());change(args[0])
            with self.subTest(change=change), self.assertRaises(ValueError):v.verify_case(*args)

    def test_intermediate_explicit_race_retained_but_joint_before_recovery_fact_verified(self):
        args=deepcopy(fixture());data=args[0];sample=data['samples'][1]['device']
        sample['fd_after']=[];sample['sockets']=[]
        sample['races']=[{'kind':'fd_changed_during_snapshot','fd':r['fd'],'before_inode':r['inode'],'after_inode':None}
                         for r in sample['fd_before']]
        data['status']='complete_with_inconclusive_samples'
        before=deepcopy(data)
        result=v.verify_case(*args)
        self.assertEqual(data,before)  # No discarded sample or recomputed raw evidence.
        self.assertEqual(result['inconclusive_device_samples'],1)
        self.assertEqual(result['first_verified_device_absence_completed'],12.01)
        self.assertEqual(result['association_state_before_recovery'],'peer_eof_and_device_control_relay_absent')
        self.assertFalse(result['device_whole_window_continuity_proven'] or result['business_recovery_passed'])

    def test_warm_and_before_recovery_must_remain_stable(self):
        for phase in ('after_large_business','before_recover'):
            args=deepcopy(fixture());data=args[0]
            sample=next(s for s in data['samples'] if s['phase']==phase)
            sample['device']['races']=[{'kind':'fd_removed_during_readlink','fd':99}]
            data['status']='complete_with_inconclusive_samples'
            with self.subTest(phase=phase),self.assertRaisesRegex(ValueError,'must be stable'):v.verify_case(*args)

    def test_explicit_race_cannot_absorb_arbitrary_error_identity_or_malformed_fields(self):
        changes=[lambda s:s.update(error={'type':'PermissionError','message':'denied'}),
                 lambda s:s['device'].update(birth_time_after=2),
                 lambda s:s['device']['races'][0].update(extra='hidden'),
                 lambda s:s['device']['races'][0].update(kind='permission_denied'),
                 lambda s:s['device'].update(fd_after=[{'fd':99,'inode':999}])]
        for change in changes:
            args=deepcopy(fixture());data=args[0];sample=data['samples'][1]
            sample['device']['races']=[{'kind':'fd_removed_during_readlink','fd':99}]
            data['status']='complete_with_inconclusive_samples';change(sample)
            with self.subTest(change=change),self.assertRaises(ValueError):v.verify_case(*args)

    def test_stable_reappearance_does_not_get_reported_as_continuous_absence(self):
        args=deepcopy(fixture());data=args[0]
        sample=next(s for s in data['samples'] if s['phase']=='window_complete')['device']
        warm=data['samples'][0]['device']
        for key in ('sockets','fd_before','fd_after'):sample[key]=deepcopy(warm[key])
        result=v.verify_case(*args)
        self.assertEqual(result['association_state_before_recovery'],'peer_eof_and_device_control_relay_absent')
        self.assertFalse(result['device_whole_window_continuity_proven'])

    def test_false_eof_local_close_and_peek_flags_rejected(self):
        changes = [lambda d:d['samples'][2]['associations'][0]['peek'].update(count=-1,errno=errno.EBADF),
                   lambda d:d['samples'][2]['associations'][0]['peek'].update(state='local_closed'),
                   lambda d:d['samples'][2]['associations'][0]['control_after'].update(inode=9999),
                   lambda d:d['samples'][-1]['associations'][0]['peek'].update(state='eof',count=0),
                   lambda d:d['cleanup'][0].update(control_fd=5)]
        for change in changes:
            args=deepcopy(fixture());change(args[0])
            with self.subTest(change=change), self.assertRaises(ValueError):v.verify_case(*args)

    def test_deadline_recovery_time_association_and_cleanup_mutations(self):
        for change in [lambda a:a[0].update(deadline=20), lambda a:a[0]['samples'][-3].update(completed=20.4),
                       lambda a:a[2]['packets'][1].update(application_socket=['127.0.0.1',9999]),
                       lambda a:a[2]['packets'][1].update(first_udp=True),
                       lambda a:a[2]['cleanup']['processes'][0].update(reaped=False),
                       lambda a:a[2]['cleanup'].update(threads_joined=False),
                       lambda a:a[0]['associations'].append(deepcopy(a[0]['associations'][0]))]:
            args=deepcopy(fixture());change(args)
            with self.subTest(change=change), self.assertRaises(ValueError):v.verify_case(*args)

    def test_missing_or_modified_raw_manifest_file_rejected(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name)
            p.save(root/'process-binaries.json',[]);p.save(root/'input-identity.json',{'source_identity':{}})
            p.save(root/'manifest.json',{'files':{}})
            report={'stage':'D','product_qualified':False,'original_suite_passed':False,
                    'cases':[{'case':c['id']} for c in p.plan('D')]}
            with self.assertRaisesRegex(ValueError,'raw file'):v.verify_stage(root,'D',report)

    def test_only_D_artifact_plan_and_source_identity_coverage(self):
        for stage in p.ROLES:
            cases=p.plan(stage); files=p.expected_artifacts(cases)
            self.assertEqual(len([x for x in files if x.endswith('/'+o.FILE)]),6 if stage=='D' else 0)
        self.assertEqual((len(p.plan('C')),len(p.leaf_ids(p.plan('C')))),(78,198))
        self.assertEqual((len(p.plan('D')),len(p.leaf_ids(p.plan('D')))),(6,12))
        self.assertIn('HERE.rglob("*")',inspect.getsource(p.source_identity))


class FrozenContractTests(unittest.TestCase):
    # Accurate 6a829dc baselines: these diagnostics must not change business,
    # source patches, frozen helpers, or any existing A/B/C/D/U/V case.
    def test_all_six_stage_plans_unchanged(self):
        expected = {'A': '211010a0823f3c9873484fa0ae715f2529a8d21ed234d83a9002cfbb64fe3b30', 'B': '16f925d8601d654917177b452ce9a827c0cfe51e8685bb93444cb25b18509352', 'C': '74f25916bedca7ab54fe46646518851596ed8acd09fcca01efb0b1e5df6997fa', 'D': '8b1fee3e3afd56b9cd72c1914baecd0b64332e8d89588dd9157b2c9b2c624b8b', 'U': 'e18ad9c22c5a4d56053a548ff2e92772fae819c7084ddbfb48f0f042202eaee3', 'V': 'a6628cc4658d4e2a56f071c2cbd4a9b6e0570006db7d8031e0a5db5bf0b6967d'}
        for stage, digest in expected.items():
            self.assertEqual(hashlib.sha256(json.dumps(p.plan(stage),sort_keys=True).encode()).hexdigest(),digest,stage)

    def test_original_business_and_gate_asts_unchanged(self):
        expected = {'plan': '98f94f14714be185be13c5181ae81e22bfd7c8b068aba129dce0fc6898566485', 'configs': 'd475cd0d716015319a68d8e29e775c5a6f70510d7c9784b4afc6b31ca33b99c1', '_exchange': '0e27ed8a45d63dd85d1a9644fc9179bf6977ea8106beaba818903d5d01c3445e', 'exchange': 'e4e27032489a69cc09af9676eaa146c71b3f52124365bdd171e94874f1eb7972', 'verify_lifecycle': '56d21057cd35cdd30b8c4e708a677b651371f3666a31d113b7c485389055b3f5', 'verify_lifecycle_files': '058ac542c30365db0900ec9c65989e89a3e0d078bb828b7bb2b74b7877aa3501', 'assess': '0987f2928c3ad47d47a072ca5d5079bca0dc2913b88a2787465cd41c67150152'}
        tree=ast.parse((ROOT/'experiments/xudp_packet/packet_boundary.py').read_text())
        actual={n.name:hashlib.sha256(ast.dump(n).encode()).hexdigest() for n in tree.body
                if isinstance(n,ast.FunctionDef) and n.name in expected}
        self.assertEqual(actual,expected)

    def test_five_patches_and_frozen_observer_helpers_unchanged(self):
        expected = {'experiments/xudp_address/remoteaddr.patch': 'ee08c4049b05d0fd72b1609839af574fe9be59b5e6c1ed4a1da54f0ffcb3a8ae', 'experiments/xudp_address/lifecycle.patch': 'b1ddb2c13896d06cf86d401044cafc9cb8f15ebbe66664fbb443f8ad13847010', 'experiments/xudp_mux/mux.patch': 'd9e1684350aedc550f7fb1c38ab690e5b23eafc2e196eddcb6e34e3598f1261a', 'experiments/xudp_mux/route_cancel.patch': '6d7e88d65ec5d463974a03102aceae5ecd15ae1cd009b1abbc1b012e60926070', 'experiments/xudp_packet/receive.patch': '8f0d6726bf96ae743087f23f9e285edf5e99a92d2f64873c3e9232d75774da6e', 'experiments/xudp_address/observe_runtime.py': '318e539cb9ea7d1a205b0aee904956d48824a05bbd349dc11472fe7e653dfd64', 'tests/udp_dns_capability_helpers.py': '7706e422336b60b79e31a959c583b28725c5ed6a3cf632d71652b2f80bee77ea', 'tests/restricted_udp_preflight_helpers.py': '6cf081f447bb4b6c8e6553d4b8bf25051fbef1bb7a2a0b0fc8be730c9545cbe9'}
        for name,digest in expected.items():
            self.assertEqual(p.sha(ROOT/name),digest,name)


if __name__ == '__main__': unittest.main()
