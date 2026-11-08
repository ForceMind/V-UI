"""Pure protocol and private temporary Unix IO with explicit fake Root proofs.

Only the Linux SO_PEERCRED case exercises actual kernel peer credentials. No
systemd, daemon installation, core process, Root operation or user data is used.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import socket
import struct
import stat
import queue
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from deploy import core_boot_broker as b
from app.core_updates import selection as s
from test_core_selection import initial, APP, TARGET


def context():
    selected=initial()
    return dict(purpose='trial',app_source=APP,target=TARGET,data_generation=dict(dev=1,ino=2),
        application={key:'a'*64 for key in b.APPLICATION_FIELDS},selection=json.loads(s.encode_selection(selected)),
        intents={core:dict(artifact_id=s.reference_identity(selected.slot(core).current),
                          revision=hashlib.sha256(core.encode()).hexdigest(),enabled=core=='sing-box') for core in s.CORES},
        barriers={key:'b'*64 for key in b.BARRIER_FIELDS})


def observation(ctx):
    result={key:deepcopy(ctx[key]) for key in ('app_source','target','data_generation','application','barriers')}
    result['selection_revision']=ctx['selection']['revision']
    result['cores']={}
    for core in b.CORES:
        intent=ctx['intents'][core];running=intent['enabled']
        result['cores'][core]=dict(artifact_id=intent['artifact_id'] if running else None,
            binary_sha256=ctx['selection']['cores'][core]['current']['binary_sha256'] if running else None,
            revision=intent['revision'],enabled=running,running=running,owned_listeners_ready=running)
    return result


class ProtocolTests(unittest.TestCase):
    def test_closed_shapes_context_binding_integer_duplicate_nan_and_frame_bounds(self):
        ctx=context();grant=dict(protocol=1,op='context',session_id='f'*32,**ctx)
        self.assertEqual(b.decode_frame(b.encode_frame(grant)),grant)
        claim={key:deepcopy(ctx[key]) for key in ('app_source','target','data_generation')}
        claim.update(protocol=1,op='claim')
        for update in (dict(pid=123),dict(protocol=True),dict(target='x86_64'),dict(data_generation=dict(dev=True,ino=2))):
            with self.subTest(update=update),self.assertRaises(b.CoreBootBrokerError):b.encode_frame(dict(claim,**update))
        for raw in (b'{"protocol":1,"protocol":1}',b'{"protocol":NaN}',b'{"protocol":1.0}',
                    b'{"protocol":-1}',b'{"protocol":18446744073709551616}',b'\xff'):
            with self.assertRaises(b.CoreBootBrokerError):b.decode_frame(struct.pack('!I',len(raw))+raw)
        for raw in (b'',struct.pack('!I',64001),struct.pack('!I',2)+b'{}'+b'extra'):
            with self.assertRaises(b.CoreBootBrokerError):b.decode_frame(raw)
        changed=deepcopy(grant);changed['selection']['app_source']='f'*40
        with self.assertRaises(b.CoreBootBrokerError):b.encode_frame(changed)
        changed=deepcopy(grant);changed['barriers']['data_owner_sha256']='0'*64
        with self.assertRaises(b.CoreBootBrokerError):b.encode_frame(changed)
    def test_periodic_stop_proof_exception_keeps_exact_failure_reason(self):
        left,right=socket.socketpair();self.addCleanup(left.close);self.addCleanup(right.close)
        def failed():raise b.CoreBootBrokerError('PROOF_FAILED')
        with self.assertRaisesRegex(b.CoreBootBrokerError,'^PROOF_FAILED$'):
            b.recv_frame(left,time.monotonic()+1,failed)

    def test_released_final_frame_and_exact_file_absence_identity_digest(self):
        message=dict(protocol=1,op='released',session_id='f'*32,selection_revision='a'*64)
        self.assertEqual(b.decode_frame(b.encode_frame(message)),message)
        self.assertNotEqual(b.file_digest(None,None),b.file_digest((1,2,0o100600,0,0,1,0,1,1),b''))
        self.assertNotEqual(b.file_digest((1,2,0o100600,0,0,1,1,1,1),b'a'),
                            b.file_digest((1,3,0o100600,0,0,1,1,1,1),b'a'))
        with self.assertRaises(b.CoreBootBrokerError):b.file_digest(None,b'')
    def test_partial_oversize_eof_and_deadline_use_one_total_budget(self):
        left,right=socket.socketpair();self.addCleanup(left.close);self.addCleanup(right.close)
        right.sendall(struct.pack('!I',64001))
        with self.assertRaisesRegex(b.CoreBootBrokerError,'FRAME_LIMIT'):b.recv_frame(left,time.monotonic()+1)
        right.sendall(b'\x00\x00')
        start=time.monotonic()
        with self.assertRaisesRegex(b.CoreBootBrokerError,'DEADLINE'):b.recv_frame(left,start+0.05)
        self.assertLess(time.monotonic()-start,0.3)
        right.close()
        with self.assertRaisesRegex(b.CoreBootBrokerError,'EOF'):b.recv_frame(left,time.monotonic()+1)
        with self.assertRaisesRegex(b.CoreBootBrokerError,'CANCELLED'):
            b.send_frame(left,dict(protocol=1,op='startup_ready',session_id='a'*32),time.monotonic()+1,lambda:True)


class FakeUnixNetwork:
    """Darwin sandbox seam: real socketpair bytes, no Unix bind/peer proof."""
    def __init__(self):
        self.socket=socket.socket;self.stat=os.stat;self.unlink=os.unlink;self.listeners={};self.nodes=set()
    def create(self,family=socket.AF_INET,type=socket.SOCK_STREAM,proto=0,fileno=None):
        if fileno is not None:return self.socket(family,type,proto,fileno=fileno)
        if family!=socket.AF_UNIX:return self.socket(family,type,proto)
        return FakeUnixSocket(self)
    def node_stat(self,*args,**kwargs):
        value=self.stat(*args,**kwargs)
        if (value.st_dev,value.st_ino) in self.nodes:
            fields=list(value);fields[0]=stat.S_IFSOCK|stat.S_IMODE(value.st_mode)
            return os.stat_result(fields)
        return value

    def node_unlink(self,*args,**kwargs):
        info=self.stat(*args,follow_symlinks=False,**kwargs)
        self.unlink(*args,**kwargs)
        self.nodes.discard((info.st_dev,info.st_ino))


class FakeUnixSocket:
    def __init__(self,network,stream=None):
        self.network=network;self.stream=stream;self.timeout=0.1;self.queue=queue.Queue()
        self.bound=None;self.node_fd=None;self.closed=False
    def bind(self,path):
        self.node_fd=os.open(path,os.O_RDWR|os.O_CREAT|os.O_EXCL,0o600)
        info=os.fstat(self.node_fd);self.network.nodes.add((info.st_dev,info.st_ino))
        self.bound=path;self.network.listeners[path]=self
    def listen(self,size):pass
    def connect(self,path):
        listener=self.network.listeners.get(path)
        if listener is None or listener.closed:raise OSError('fake listener unavailable')
        left,right=socket.socketpair();self.stream=left
        listener.queue.put(FakeUnixSocket(self.network,right))
    def accept(self):
        try:return self.queue.get(timeout=self.timeout),None
        except queue.Empty:raise socket.timeout()
    def settimeout(self,value):
        self.timeout=value
        if self.stream is not None:self.stream.settimeout(value)
    def fileno(self):return self.stream.fileno()
    def send(self,value):return self.stream.send(value)
    def recv(self,size):return self.stream.recv(size)
    def sendall(self,value):return self.stream.sendall(value)
    def close(self):
        if self.closed:return
        self.closed=True
        if self.stream is not None:self.stream.close()
        if self.node_fd is not None:
            os.close(self.node_fd);self.node_fd=None
        if self.bound:self.network.listeners.pop(self.bound,None)


class BrokerTests(unittest.TestCase):
    def setUp(self):
        # Short private path is necessary for AF_UNIX sun_path on Darwin too.
        temp=tempfile.TemporaryDirectory(prefix='vui-broker-',dir='/private/tmp' if Path('/private/tmp').is_dir() else '/tmp')
        self.addCleanup(temp.cleanup);self.root=Path(temp.name).resolve();self.root.chmod(0o700)
        if not sys.platform.startswith('linux'):
            network=FakeUnixNetwork()
            sockets=patch.object(b.socket,'socket',side_effect=network.create);sockets.start();self.addCleanup(sockets.stop)
            stats=patch.object(b.os,'stat',side_effect=network.node_stat);stats.start();self.addCleanup(stats.stop)
            unlinks=patch.object(b.os,'unlink',side_effect=network.node_unlink);unlinks.start();self.addCleanup(unlinks.stop)
        self.ctx=context();self.identity=b.PeerIdentity(12345,501,'start-17','/system.slice/v-ui.service')
        self.current=self.identity;self.proofs=0;self.proof_error=False
        self.gid=os.getegid() or 1;self.brokers=[];self.clients=[];self.workers=[]
        self.addCleanup(self.cleanup)
    def cleanup(self):
        for broker in self.brokers:
            try:broker.close()
            except b.CoreBootBrokerError:pass
        for sock in self.clients:
            try:sock.close()
            except OSError:pass
        for thread in self.workers:thread.join(timeout=1)
    def proof(self):
        self.proofs+=1
        if self.proof_error:raise RuntimeError('untrusted private detail')
    def broker(self,timeout=2,credentials=None):
        layout=b.BrokerLayout(self.root/'broker',os.geteuid(),self.root)
        broker=b.CoreBootBroker(self.ctx,service_uid=501,service_gid=self.gid,proof=self.proof,
            identity_query=lambda:self.current,timeout=timeout,layout=layout,
            credentials=(lambda sock:(self.identity.pid,self.identity.uid,self.gid)) if credentials is None else credentials)
        self.brokers.append(broker);broker.listen();return broker
    def connect(self,broker):
        sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);self.clients.append(sock);sock.connect(str(broker.endpoint));return sock
    def claim(self,sock,deadline,ctx=None):
        ctx=self.ctx if ctx is None else ctx
        request=dict(protocol=1,op='claim',**{key:deepcopy(ctx[key]) for key in ('app_source','target','data_generation')})
        b.send_frame(sock,request,deadline)
        return b.recv_frame(sock,deadline)
    def client(self,broker,*,grant=True,mutate_probe=None,ack=True):
        events=[];result={}
        def run():
            try:
                sock=self.connect(broker);deadline=time.monotonic()+2
                message=self.claim(sock,deadline);events.append(message['op']);result['grant']=message
                if not grant:return
                sid=message['session_id'];b.send_frame(sock,dict(protocol=1,op='startup_ready',session_id=sid),deadline)
                while True:
                    message=b.recv_frame(sock,deadline);events.append(message['op'])
                    if message['op']=='request_probe':
                        seen=observation(self.ctx)
                        if mutate_probe:mutate_probe(seen)
                        b.send_frame(sock,dict(protocol=1,op='probe_result',session_id=sid,
                            probe_id=message['probe_id'],ok=True,observed=seen),deadline)
                    elif message['op']=='release':
                        result['release']=message
                        if not ack:return
                        b.send_frame(sock,dict(protocol=1,op='release_ack',session_id=sid),deadline)
                    elif message['op'] in ('released','revoke'):
                        result['last']=message;return
            except BaseException as error:result['error']=error
        thread=threading.Thread(target=run);self.workers.append(thread);thread.start()
        return thread,events,result
    def test_same_connection_probe_release_final_frame_and_once_claim(self):
        broker=self.broker();broker.arm(self.identity)
        self.assertEqual(stat.S_IMODE(broker.endpoint.stat().st_mode),0o660)
        self.assertEqual(stat.S_IMODE(broker.endpoint.parent.stat().st_mode),0o750)
        self.assertEqual(broker.endpoint.stat().st_gid,self.gid)
        thread,events,result=self.client(broker);broker.wait_ready()
        with self.assertRaises(b.CoreBootBrokerError):broker.release(self.ctx['barriers'])
        answer=broker.request_probe();self.assertTrue(answer['ok'])
        final={key:'e'*64 for key in b.BARRIER_FIELDS}
        broker.release(final);thread.join(timeout=1)
        self.assertEqual(events,['context','request_probe','release','released'])
        self.assertEqual(result['release']['barriers'],final)
        self.assertEqual(result['last']['selection_revision'],self.ctx['selection']['revision'])
        self.assertGreaterEqual(self.proofs,7);self.assertEqual(broker.state,'released')
        with self.assertRaises(b.CoreBootBrokerError):broker.arm(self.identity)
        with self.assertRaises(b.CoreBootBrokerError):broker.request_probe()
        broker.close();self.assertFalse(broker.endpoint.exists());self.assertEqual(broker.state,'released')
    def test_unarmed_connection_waits_without_grant_or_claim_consumption(self):
        broker=self.broker();sock=self.connect(broker)
        request=dict(protocol=1,op='claim',**{key:self.ctx[key] for key in ('app_source','target','data_generation')})
        b.send_frame(sock,request,time.monotonic()+1);sock.settimeout(0.05)
        with self.assertRaises(socket.timeout):sock.recv(1)
        self.assertFalse(broker._claimed);broker.arm(self.identity)
        response=b.recv_frame(sock,time.monotonic()+1);self.assertEqual(response['op'],'context')
        broker.revoke();broker.close()
    def test_wrong_peer_cannot_claim_and_does_not_consume_expected_scope(self):
        first=[True]
        def credentials(sock):
            if first[0]:first[0]=False;return (999,999,self.gid)
            return self.identity.pid,self.identity.uid,self.gid
        broker=self.broker(credentials=credentials);broker.arm(self.identity)
        bad=self.connect(broker)
        with self.assertRaises(b.CoreBootBrokerError):self.claim(bad,time.monotonic()+0.5)
        self.assertFalse(broker._claimed)
        thread,events,result=self.client(broker);broker.wait_ready();broker.revoke();broker.close();thread.join(timeout=1)
        self.assertEqual(events[0],'context')
    def test_changed_pid_start_cgroup_or_failed_live_proof_revoke(self):
        for change in ('pid','start','cgroup','proof'):
            with self.subTest(change=change):
                broker=self.broker();broker.arm(self.identity);thread,events,result=self.client(broker);broker.wait_ready()
                if change=='pid':self.current=b.PeerIdentity(12346,501,'start-17',self.identity.cgroup)
                elif change=='start':self.current=b.PeerIdentity(12345,501,'new-start',self.identity.cgroup)
                elif change=='cgroup':self.current=b.PeerIdentity(12345,501,'start-17','/foreign')
                else:self.proof_error=True
                with self.assertRaises(b.CoreBootBrokerError):broker.request_probe()
                broker.close();thread.join(timeout=1)
                self.assertNotIn('released',events);self.assertEqual(result.get('last',{}).get('op'),'revoke')
                self.current=self.identity;self.proof_error=False
    def test_claim_source_target_and_data_drift_do_not_grant_context(self):
        for key,value in (('app_source','f'*40),('target','aarch64-gnu'),('data_generation',dict(dev=1,ino=3))):
            broker=self.broker();broker.arm(self.identity);sock=self.connect(broker);ctx=deepcopy(self.ctx);ctx[key]=value
            message=self.claim(sock,time.monotonic()+1,ctx)
            self.assertEqual(message['op'],'revoke');self.assertFalse(broker._claimed);broker.close()
    def test_probe_barrier_mismatch_and_wrong_sequence_never_release(self):
        broker=self.broker();broker.arm(self.identity)
        thread,events,result=self.client(broker,mutate_probe=lambda value:value['barriers'].update(core_pending_sha256='f'*64))
        broker.wait_ready()
        with self.assertRaises(b.CoreBootBrokerError):broker.request_probe()
        broker.close();thread.join(timeout=1);self.assertNotIn('released',events)
        broker=self.broker();broker.arm(self.identity);sock=self.connect(broker);grant=self.claim(sock,time.monotonic()+1)
        b.send_frame(sock,dict(protocol=1,op='release_ack',session_id=grant['session_id']),time.monotonic()+1)
        self.assertEqual(b.recv_frame(sock,time.monotonic()+1)['op'],'revoke');broker.close()
    def test_deadline_partial_frame_eof_and_cancel_keep_closed_gate(self):
        broker=self.broker(timeout=0.12);broker.arm(self.identity);sock=self.connect(broker);sock.sendall(b'\x00\x00')
        with self.assertRaises(b.CoreBootBrokerError):broker.wait_ready()
        self.assertNotEqual(broker.state,'released');broker.close();self.assertFalse(broker.endpoint.exists())
        broker=self.broker();broker.arm(self.identity);sock=self.connect(broker);self.claim(sock,time.monotonic()+1);sock.close()
        with self.assertRaises(b.CoreBootBrokerError):broker.wait_ready()
        broker.close()
    def test_root_gate_before_filesystem_and_existing_socket_foreign_cleanup(self):
        production=b.CoreBootBroker(self.ctx,service_uid=501,service_gid=501,proof=self.proof,identity_query=lambda:self.identity)
        with patch.object(b.os,'geteuid',return_value=501),patch.object(b.os,'open') as opened:
            with self.assertRaisesRegex(b.CoreBootBrokerError,'ROOT_REQUIRED'):production.listen()
        opened.assert_not_called()
        directory=self.root/'broker';directory.mkdir(mode=0o750);os.chown(directory,os.geteuid(),self.gid);directory.chmod(0o750)
        foreign=directory/'core-boot.sock';foreign.write_bytes(b'preserve foreign')
        with self.assertRaises(b.CoreBootBrokerError):self.broker()
        self.assertEqual(foreign.read_bytes(),b'preserve foreign');foreign.unlink()
        broker=self.broker();foreign.unlink();foreign.write_bytes(b'foreign replacement')
        with self.assertRaisesRegex(b.CoreBootBrokerError,'CLEANUP_FAILED'):broker.close()
        self.assertEqual(foreign.read_bytes(),b'foreign replacement')
    def test_idle_peer_eof_revokes_before_another_control_command(self):
        broker=self.broker();broker.arm(self.identity);sock=self.connect(broker)
        grant=self.claim(sock,time.monotonic()+1)
        b.send_frame(sock,dict(protocol=1,op='startup_ready',session_id=grant['session_id']),time.monotonic()+1)
        broker.wait_ready();sock.close()
        self.assertTrue(broker._finished.wait(0.5));self.assertEqual(broker.state,'revoked')
        with self.assertRaises(b.CoreBootBrokerError):broker.request_probe()
        broker.close()
    def test_post_ack_proof_failure_never_sends_released_completion(self):
        broker=self.broker();broker.arm(self.identity)
        thread,events,result=self.client(broker);broker.wait_ready();broker.request_probe()
        receive=b.recv_frame
        def after_ack(*args,**kwargs):
            answer=receive(*args,**kwargs)
            if answer['op']=='release_ack':self.proof_error=True
            return answer
        with patch.object(b,'recv_frame',side_effect=after_ack):
            with self.assertRaises(b.CoreBootBrokerError):broker.release(self.ctx['barriers'])
        broker.close();thread.join(timeout=1)
        self.assertIn('release',events);self.assertIn('revoke',events);self.assertNotIn('released',events)

    def test_thread_start_failure_cleans_owned_endpoint_and_all_opened_fds(self):
        opened=[];open_fd=os.open
        def recorded(*args,**kwargs):
            fd=open_fd(*args,**kwargs);opened.append(fd);return fd
        with patch.object(b.os,'open',side_effect=recorded), \
                patch.object(b.threading.Thread,'start',side_effect=RuntimeError('private thread failure')):
            with self.assertRaisesRegex(b.CoreBootBrokerError,'LISTEN_FAILED'):self.broker()
        self.assertFalse((self.root/'broker/core-boot.sock').exists())
        for fd in opened:
            with self.assertRaises(OSError):os.fstat(fd)
    def test_background_socket_close_failure_is_reported_not_silently_lost(self):
        broker=self.broker();listener=broker._listener;calls=[]
        class CloseFailure:
            def __getattr__(self,name):return getattr(listener,name)
            def close(self):
                calls.append('close');listener.close();raise OSError('close acknowledgement lost')
        broker._listener=CloseFailure()
        with self.assertRaisesRegex(b.CoreBootBrokerError,'CLEANUP_FAILED'):broker.close()
        self.assertEqual(calls,['close']);self.assertFalse(broker.endpoint.exists())
        self.assertFalse(broker._thread.is_alive())

    def test_startup_wait_and_ready_idle_periodically_revoke_without_root_commands(self):
        for phase,failure in (('startup','proof'),('ready','proof'),('ready','identity')):
            with self.subTest(phase=phase,failure=failure):
                broker=self.broker(timeout=2);broker.arm(self.identity);sock=self.connect(broker)
                try:
                    grant=self.claim(sock,time.monotonic()+1)
                    if phase=='ready':
                        b.send_frame(sock,dict(protocol=1,op='startup_ready',session_id=grant['session_id']),time.monotonic()+1)
                        broker.wait_ready()
                    if failure=='proof':self.proof_error=True
                    else:self.current=b.PeerIdentity(12346,501,'changed-start','/changed-cgroup')
                    # No probe/release command, client EOF or total timeout may
                    # trigger this revocation; the live observer must do so.
                    self.assertTrue(broker._finished.wait(0.65),'idle live proof must revoke before session deadline')
                    message=b.recv_frame(sock,time.monotonic()+0.5)
                    self.assertEqual(message['op'],'revoke')
                    self.assertEqual(message['reason'],'proof_failed' if failure=='proof' else 'identity_changed')
                    self.assertEqual(broker._error,'PROOF_FAILED' if failure=='proof' else 'IDENTITY_CHANGED')
                    self.assertEqual(broker.state,'revoked');self.assertFalse(broker._released)
                finally:
                    broker.close();sock.close();self.proof_error=False;self.current=self.identity

    def test_proof_flag_does_not_grant_scope_and_close_terminates_thread(self):
        broker=self.broker();broker._proof=lambda:True
        with self.assertRaisesRegex(b.CoreBootBrokerError,'PROOF_FAILED'):broker.arm(self.identity)
        broker.close();self.assertFalse(broker._thread.is_alive())


@unittest.skipUnless(hasattr(socket,'SO_PEERCRED'),'Linux Actions required for actual SO_PEERCRED')
class KernelPeerTests(unittest.TestCase):
    def test_kernel_credentials_are_not_request_pid_and_root_client_uid_is_checked(self):
        left,right=socket.socketpair();self.addCleanup(left.close);self.addCleanup(right.close)
        self.assertEqual(b.peer_credentials(left)[:2],(os.getpid(),os.geteuid()))
        self.assertEqual(b.check_root_peer(left,expected_uid=os.geteuid())[:2],(os.getpid(),os.geteuid()))
        with self.assertRaises(b.CoreBootBrokerError):b.check_root_peer(left,expected_uid=os.geteuid()+1)
        if os.geteuid()==0:
            # The production service must be non-root; do not forge a UID in
            # the positive real-peer case when this test runner itself is root.
            with self.assertRaises(b.CoreBootBrokerError):
                b.CoreBootBroker(context(),service_uid=0,service_gid=1,proof=lambda:None,identity_query=lambda:None)
            return
        with tempfile.TemporaryDirectory(prefix='vui-kernel-broker-',dir='/tmp') as temporary:
            anchor=Path(temporary).resolve();anchor.chmod(0o700);ctx=context()
            identity=b.PeerIdentity(os.getpid(),os.geteuid(),'fake-start','/fake-test-cgroup')
            broker=b.CoreBootBroker(ctx,service_uid=os.geteuid(),service_gid=os.getegid() or 1,
                proof=lambda:None,identity_query=lambda:identity,timeout=2,
                layout=b.BrokerLayout(anchor/'broker',os.geteuid(),anchor))
            self.assertIs(broker._credentials,b.peer_credentials)
            broker.listen();broker.arm(identity);errors=[]
            def client():
                try:
                    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as sock:
                        sock.connect(str(broker.endpoint));b.check_root_peer(sock,expected_uid=os.geteuid())
                        deadline=time.monotonic()+2
                        b.send_frame(sock,dict(protocol=1,op='claim',**{key:ctx[key]
                            for key in ('app_source','target','data_generation')}),deadline)
                        grant=b.recv_frame(sock,deadline);sid=grant['session_id']
                        b.send_frame(sock,dict(protocol=1,op='startup_ready',session_id=sid),deadline)
                        probe=b.recv_frame(sock,deadline)
                        b.send_frame(sock,dict(protocol=1,op='probe_result',session_id=sid,
                            probe_id=probe['probe_id'],ok=True,observed=observation(ctx)),deadline)
                        release=b.recv_frame(sock,deadline);self.assertEqual(release['op'],'release')
                        b.send_frame(sock,dict(protocol=1,op='release_ack',session_id=sid),deadline)
                        self.assertEqual(b.recv_frame(sock,deadline)['op'],'released')
                except BaseException as error:errors.append(error)
            thread=threading.Thread(target=client);thread.start()
            try:
                broker.wait_ready();broker.request_probe();broker.release(ctx['barriers'])
                thread.join(timeout=1);self.assertFalse(thread.is_alive());self.assertEqual(errors,[])
            finally:broker.close();thread.join(timeout=1)


if __name__=='__main__':unittest.main()
