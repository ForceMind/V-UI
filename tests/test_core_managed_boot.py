"""Fake authenticated transport only; no data permit, service or native core."""
from copy import copy, deepcopy
from contextlib import ExitStack
import io
import json
import os
from pathlib import Path
import pickle
import socket
import stat
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from deploy import core_boot_broker as w
from app.core_updates import managed_boot as m, selection as s
from test_core_selection import initial, APP, TARGET


def context():
    state=initial()
    return dict(protocol=1,op='context',session_id='1'*32,purpose='trial',app_source=APP,target=TARGET,
        data_generation=dict(dev=1,ino=2),application={key:'2'*64 for key in w.APPLICATION_FIELDS},
        barriers={key:'3'*64 for key in w.BARRIER_FIELDS},selection=json.loads(s.encode_selection(state)),
        intents={core:dict(artifact_id=s.reference_identity(state.slot(core).current),
                          revision='4'*64 if core=='sing-box' else None,enabled=core=='sing-box')for core in s.CORES})


def observed(value):
    return {**{key:deepcopy(value[key])for key in ('app_source','target','data_generation','application','barriers')},
            'selection_revision':value['selection']['revision'],'cores':{core:dict(
                artifact_id=value['intents'][core]['artifact_id'],
                binary_sha256=value['selection']['cores'][core]['current']['binary_sha256'],
                revision=value['intents'][core]['revision'],enabled=value['intents'][core]['enabled'],
                running=value['intents'][core]['enabled'],owned_listeners_ready=value['intents'][core]['enabled'])for core in s.CORES}}


class Socket:
    def __init__(self,messages,close_error=False):
        self.input=io.BytesIO(b''.join(w.encode_frame(message)for message in messages))
        self.sent=bytearray();self.closed=False;self.close_calls=0;self.close_error=close_error
    def settimeout(self,value):pass
    def connect(self,path):self.path=path
    def send(self,raw):self.sent.extend(raw);return len(raw)
    def recv(self,size):return self.input.read(min(size,7))
    def close(self):
        self.closed=True;self.close_calls+=1
        if self.close_error:raise OSError('private close details')
    def messages(self):
        raw=bytes(self.sent);values=[]
        while raw:
            size=int.from_bytes(raw[:4],'big');values.append(w.decode_frame(raw[:4+size]));raw=raw[4+size:]
        return values


class ManagedBootTests(unittest.TestCase):
    def setUp(self):self.value=context()
    def claim(self,messages=None,**kwargs):
        self.socket=Socket([self.value]+(messages or []),kwargs.pop('close_error',False))
        self.stack=[]
        for item in (patch.object(m,'_socket_path',return_value=(1,2,0,0,0)),
                     patch.object(w,'check_root_peer',return_value=(123,0,0)),
                     patch.object(w,'peer_credentials',return_value=(123,0,0))):
            item.start();self.addCleanup(item.stop)
        result=m.claim(app_source=APP,target=TARGET,data_generation=self.value['data_generation'],
            application=self.value['application'],barriers=self.value['barriers'],
            _socket_factory=lambda *args:self.socket,**kwargs)
        self.addCleanup(result.close)
        return result

    def release_messages(self):
        final={key:'5'*64 for key in w.BARRIER_FIELDS}
        return [dict(protocol=1,op='release',session_id=self.value['session_id'],
                     selection_revision=self.value['selection']['revision'],barriers=final),
                dict(protocol=1,op='released',session_id=self.value['session_id'],
                     selection_revision=self.value['selection']['revision'])]

    def test_claim_binds_observations_and_full_m3_selection_without_mutable_context_alias(self):
        result=self.claim();self.assertEqual(result.selection,initial())
        copy_of_context=result.context;copy_of_context['intents'].clear()
        self.assertEqual(set(result.context['intents']),set(s.CORES))
        self.assertEqual(self.socket.messages()[0],dict(protocol=1,op='claim',app_source=APP,
            target=TARGET,data_generation=self.value['data_generation']))
        self.assertFalse(result.released);self.assertFalse(result.revoked)

    def test_connection_cannot_be_created_from_json_or_copied_or_pickled(self):
        with self.assertRaisesRegex(m.ManagedBootError,'CONNECTION_REQUIRED'):
            m.ManagedBootConnection(None,None,None,None,None,None,None)
        result=self.claim()
        for operation in (copy,deepcopy,pickle.dumps):
            with self.assertRaisesRegex(m.ManagedBootError,'CONNECTION_REQUIRED'):operation(result)

    def test_observed_application_or_data_or_barriers_mismatch_closes_before_context_return(self):
        for key,changed in (('data_generation',dict(dev=1,ino=3)),
                            ('application',{key:'f'*64 for key in w.APPLICATION_FIELDS}),
                            ('barriers',{key:'f'*64 for key in w.BARRIER_FIELDS})):
            with self.subTest(key=key):
                original=self.value;self.value=deepcopy(original);self.value[key]=changed
                fake=Socket([self.value])
                with patch.object(m,'_socket_path',return_value=(1,2,0,0,0)), \
                        patch.object(w,'check_root_peer'),patch.object(w,'peer_credentials',return_value=(123,0,0)), \
                        self.assertRaisesRegex(m.ManagedBootError,'BOOT_FACTS_CHANGED'):
                    m.claim(app_source=APP,target=TARGET,data_generation=original['data_generation'],
                        application=original['application'],barriers=original['barriers'],_socket_factory=lambda *args:fake)
                self.assertTrue(fake.closed);self.value=original

    def test_coarse_protocol_context_cannot_bypass_full_m3_revision_validation(self):
        self.value['selection']['revision']='f'*64
        with self.assertRaises(m.ManagedBootError):self.claim()
        self.assertTrue(self.socket.closed)

    def test_wrong_root_peer_rejects_before_sending_claim(self):
        fake=Socket([self.value])
        with patch.object(m,'_socket_path',return_value=(1,2,0,0,0)), \
                patch.object(w,'check_root_peer',side_effect=w.CoreBootBrokerError('ROOT_PEER_REQUIRED')), \
                self.assertRaises(m.ManagedBootError):
            m.claim(app_source=APP,target=TARGET,data_generation=self.value['data_generation'],
                application=self.value['application'],barriers=self.value['barriers'],_socket_factory=lambda *args:fake)
        self.assertEqual(fake.sent,b'');self.assertTrue(fake.closed)

    def test_socket_replacement_between_connect_and_send_rejects_without_claim(self):
        fake=Socket([self.value])
        with patch.object(m,'_socket_path',side_effect=[(1,2,0,0,0),(1,3,0,0,0)]), \
                patch.object(w,'check_root_peer'),patch.object(w,'peer_credentials',return_value=(123,0,0)), \
                self.assertRaisesRegex(m.ManagedBootError,'SOCKET_CHANGED'):
            m.claim(app_source=APP,target=TARGET,data_generation=self.value['data_generation'],
                application=self.value['application'],barriers=self.value['barriers'],_socket_factory=lambda *args:fake)
        self.assertEqual(fake.sent,b'');self.assertTrue(fake.closed)

    def test_root_released_before_request_ack_is_not_a_release_grant(self):
        result=self.claim(self.release_messages()[1:]);events=[];result.startup_ready()
        with self.assertRaisesRegex(m.ManagedBootError,'UNEXPECTED_CONTROL'):
            result.run_controls(probe=Mock(),settle=lambda _:None,on_released=lambda:events.append('released'),
                                on_revoked=lambda:events.append('revoked'))
        self.assertEqual(events,['revoked']);self.assertFalse(result.released)

    def test_startup_ready_cannot_be_replayed_and_closes_once(self):
        result=self.claim();result.startup_ready()
        with self.assertRaises(m.ManagedBootError):result.startup_ready()
        self.assertTrue(result.revoked);self.assertTrue(self.socket.closed)
        result.close();self.assertEqual(self.socket.close_calls,1)

    def test_probe_then_three_way_release_only_ungates_after_released(self):
        request=dict(protocol=1,op='request_probe',session_id=self.value['session_id'],probe_id='6'*32)
        messages=[request]+self.release_messages();result=self.claim(messages);events=[]
        result.startup_ready()
        def settle(barriers):
            self.assertFalse(result.released);self.assertEqual(barriers,messages[1]['barriers']);events.append('settle')
        result.run_controls(probe=lambda:(observed(self.value),True),settle=settle,
            on_released=lambda:events.append('released'),on_revoked=lambda:events.append('revoked'))
        self.assertEqual(events,['settle','released']);self.assertTrue(result.released)
        self.assertFalse(result.revoked);self.assertTrue(self.socket.closed)
        self.assertEqual([value['op']for value in self.socket.messages()],['claim','startup_ready','probe_result','release_ack'])

    def test_eof_after_release_ack_still_revokes_and_never_ungates(self):
        result=self.claim(self.release_messages()[:1]);events=[];result.startup_ready()
        with self.assertRaises(w.CoreBootBrokerError):
            result.run_controls(probe=Mock(),settle=lambda _:None,on_released=lambda:events.append('released'),
                                on_revoked=lambda:events.append('revoked'))
        self.assertEqual(events,['revoked']);self.assertTrue(result.revoked);self.assertFalse(result.released)
        self.assertEqual(self.socket.messages()[-1]['op'],'release_ack')

    def test_failed_actual_settlement_never_acknowledges_or_releases(self):
        result=self.claim(self.release_messages());events=[];result.startup_ready()
        def settle(_):raise ValueError('private filesystem failure')
        with self.assertRaises(ValueError):
            result.run_controls(probe=Mock(),settle=settle,on_released=lambda:events.append('released'),
                                on_revoked=lambda:events.append('revoked'))
        self.assertEqual(events,['revoked'])
        self.assertNotIn('release_ack',[message['op']for message in self.socket.messages()])

    def test_wrong_session_or_unexpected_control_or_revoke_fails_closed(self):
        for message in (dict(protocol=1,op='released',session_id='9'*32,selection_revision=initial().revision),
                        dict(protocol=1,op='startup_ready',session_id=self.value['session_id']),
                        dict(protocol=1,op='revoke',session_id=self.value['session_id'],reason='cancelled')):
            with self.subTest(message=message):
                result=self.claim([message]);events=[];result.startup_ready()
                with self.assertRaises(Exception):
                    result.run_controls(probe=Mock(),settle=lambda _:None,on_released=lambda:events.append('released'),
                                        on_revoked=lambda:events.append('revoked'))
                self.assertEqual(events,['revoked']);self.assertTrue(self.socket.closed)

    def test_cancel_during_probe_revokes_before_sending_observation(self):
        stop=[False];request=dict(protocol=1,op='request_probe',session_id=self.value['session_id'],probe_id='6'*32)
        result=self.claim([request],_stop=lambda:stop[0]);events=[];result.startup_ready()
        def probe():stop[0]=True;return observed(self.value),True
        with self.assertRaises(m.ManagedBootError):
            result.run_controls(probe=probe,settle=lambda _:None,on_released=Mock(),on_revoked=lambda:events.append('revoked'))
        self.assertEqual(events,['revoked'])
        self.assertNotIn('probe_result',[message['op']for message in self.socket.messages()])

    def test_close_error_releases_owned_socket_reference_and_never_calls_release_callback(self):
        result=self.claim(self.release_messages(),close_error=True);result.startup_ready();events=[]
        with self.assertRaises(m.ManagedBootError):
            result.run_controls(probe=Mock(),settle=lambda _:None,on_released=lambda:events.append('released'),
                                on_revoked=lambda:events.append('revoked'))
        self.assertEqual(events,['revoked']);self.assertEqual(self.socket.close_calls,1)

    def test_invalid_callback_rejects_with_cleanup_instead_of_leaving_ready_session_live(self):
        result=self.claim();result.startup_ready()
        with self.assertRaisesRegex(m.ManagedBootError,'CALLBACK_REQUIRED'):
            result.run_controls(probe=None,settle=lambda _:None,on_released=Mock(),on_revoked=Mock())
        self.assertTrue(result.revoked);self.assertTrue(self.socket.closed)

    def test_socket_path_permissions_and_symlink_rejection_use_private_temp_node(self):
        with tempfile.TemporaryDirectory(prefix='vui-managed-socket-') as temporary:
            root=Path(temporary).resolve();root.chmod(0o700);path=root/'core.sock'
            with ExitStack() as stack:
                if sys.platform.startswith('linux'):
                    server=stack.enter_context(socket.socket(socket.AF_UNIX,socket.SOCK_STREAM))
                    server.bind(str(path))  # Mandatory actual bind in Linux Actions.
                else:
                    # Desktop sandbox forbids AF_UNIX bind. Only fake the leaf
                    # node type; ancestor/mode/uid/link stats remain real here.
                    path.write_bytes(b'fake Unix socket node')
                    real_lstat=Path.lstat
                    def node_lstat(value):
                        info=real_lstat(value)
                        if value==path:
                            return SimpleNamespace(st_mode=stat.S_IFSOCK|stat.S_IMODE(info.st_mode),
                                st_uid=info.st_uid,st_gid=info.st_gid,st_nlink=info.st_nlink,
                                st_dev=info.st_dev,st_ino=info.st_ino)
                        return info
                    stack.enter_context(patch.object(Path,'lstat',node_lstat))
                path.chmod(0o660)
                identity=m._socket_path(path,os.geteuid(),root)
                self.assertEqual(identity[:2],(path.stat().st_dev,path.stat().st_ino))
                path.chmod(0o666)
                with self.assertRaises(m.ManagedBootError):m._socket_path(path,os.geteuid(),root)
                path.chmod(0o660);alias=root/'link';alias.symlink_to(path)
                with self.assertRaises(m.ManagedBootError):m._socket_path(alias,os.geteuid(),root)


if __name__=='__main__':unittest.main()
