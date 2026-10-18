"""Verifier unit tests only. Synthetic fixtures are NOT Go/runtime evidence."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from verify_encrypted_cancel import CIPHERS, LEAVES, PREFIX, verify_encrypted_cancel


def synthetic_rows():
    rows = []
    def continuity(response, request):
        data = dict(response_chunks=response, request_chunks=request, physical_bytes_exact=True)
        for key in ('server_data_nonce', 'server_length_nonce', 'client_data_nonce', 'client_length_nonce'):
            data[key] = response
        for key in ('upload_data_nonce', 'upload_length_nonce', 'service_data_nonce', 'service_length_nonce'):
            data[key] = request
        return ('cipher_continuity', data)
    for top, cipher in CIPHERS.items():
        for leaf in LEAVES:
            name = top + '/' + leaf
            real_error = leaf == 'ParentTransportError'
            prefix = 1 if leaf in ('PartialAuthenticatedLength', 'CompletionBeforeClose') else 55
            events = [continuity(1, 3), ('warmup_complete', dict(command_mux=True, global_padding=True, authenticated_length=True, decoder_reads_serial=True, ciphertext_fully_consumed=True, response_chunks=1)),
                      ('wire_blocked', dict(prefix_bytes=prefix, auth_length_bytes=18, completed_write_bytes_since_capture=0, active_write_consumed_bytes=prefix, active_write_length=15050, parent_writers=1, blocked_pipe_writers=1, active_transport_writes=1, maximum_transport_writes=1,
                                           blocked_stack='goroutine 7 [select]:\nnet.(*pipe).write(\n(*encCancelWire).Write(\n(*serverConn).Write(\n(*serverSession).writeLoop('))]
            if real_error:
                events.append(('parent_error_snapshot', dict(parent_alive=True, no_context_cancel=True, pending_callers=3, queued_callers=2, blocked_pipe_writers=1)))
            else:
                restored = ('restore_reads', dict(prefix_bytes=prefix, unchanged_prefix_replay=True, parent_alive=True))
                if leaf == 'CompletionBeforeClose':
                    events.extend([restored, ('completion_before_close', dict(caller_success=True, operation_done=True, parent_alive=True, caller_bytes=65535))])
                else:
                    queued = leaf == 'QueuedCancel'
                    events.extend([('cancel_snapshot', dict(parent_deadline_changes=0, parent_close_calls=0, queued=queued, operation_done=queued, caller_closed=True, caller_joined=True, operation_canceled=True, parent_alive=True, caller_bytes=0, active_callers=int(queued), cancel_bound_ns=3_000_000_000, cancel_elapsed_ns=1000, parent_writers=1, blocked_pipe_writers=1, active_transport_writes=1, streams=3, owned_frames=2, owned_bytes=65557, handler_callbacks_finished=3, handler_callbacks_active=0)), restored])
                events.extend([continuity(18, 11), ('siblings_recovered', dict(same_parent=True, tuple_payload_exact=True, parent_alive=True, udp_upload=60000, udp_reply=60000, tcp_upload=32768, tcp_reply=70007)), continuity(22, 13), ('fresh_session_recovered', dict(session_id=4, actual_encrypted_new=True, same_parent=True, tuple_payload_exact=True, parent_alive=True, udp_upload=4097, udp_reply=32768, end_count=1, handler_callbacks_finished=4)), continuity(24, 13), ('drained_before_parent_close', dict(parent_alive=True, each_end_once=True, end_count=4, active_transport_writes=0, active_callers=0, handler_callbacks_active=0))])
            events.extend([('parent_close', dict(real_peer_close=True, outer_context_alive=True, while_write_blocked=real_error)), ('parent_joined', dict(service_joined=True, writer_joined=True, watcher_joined=True, all_test_workers_joined=True, outer_context_alive=True, handler_callbacks_finished=3 if real_error else 4, handler_callbacks_expected=3 if real_error else 4, active_transport_writes=0, active_callers=0, retained_streams=0, retained_queue=0, service_error='read frame header: EOF', real_write_errors=int(real_error), parent_error_results=[dict(caller=i,bytes=0,**{'class':'net.ErrClosed','error':'use of closed network connection'}) for i in range(3)] if real_error else [], parent_error_callers_closed=3 if real_error else 0))])
            for seq, (event, fields) in enumerate(events, 1):
                value = dict(fields, event=event, case=name, cipher=cipher, seq=seq, scope='encrypted-library-callers', product_qualified=False)
                rows.append(dict(Action='output', Test=name, Output=PREFIX+json.dumps(value)+'\n'))
            rows.append(dict(Action='pass', Test=name))
        rows.append(dict(Action='pass', Test=top))
    rows.append(dict(Action='pass'))
    for row in rows: row['Package'] = 'github.com/sagernet/sing-vmess'
    return rows


class VerifyEncryptedCancellation(unittest.TestCase):
    def verify(self, rows):
        with tempfile.TemporaryDirectory(prefix='synthetic-verifier-unit-', dir=Path(__file__).parent) as d:
            p = Path(d)/'synthetic-only.jsonl'
            p.write_text(''.join(json.dumps(row)+'\n' for row in rows))
            return verify_encrypted_cancel(p)

    def mutate(self, event, key, value, leaf=None):
        rows = synthetic_rows()
        for row in rows:
            if row.get('Action') != 'output':
                continue
            data = json.loads(row['Output'].split(PREFIX, 1)[1])
            if data['event'] == event and (leaf is None or data['case'].endswith('/'+leaf)):
                data[key] = value
                row['Output'] = PREFIX+json.dumps(data)+'\n'
                return rows
        raise AssertionError('fixture mutation target missing')

    def test_complete_synthetic_fixture(self):
        # Passing this fixture validates verifier plumbing, not Go behavior.
        self.assertEqual(self.verify(synthetic_rows())['leaf_cases'], 10)
        actual_pipe = [{'caller': i, 'bytes': 0, 'class': 'io.ErrClosedPipe', 'error': 'io: read/write on closed pipe'} for i in range(3)]
        self.assertEqual(self.verify(self.mutate('parent_joined', 'parent_error_results', actual_pipe, 'ParentTransportError'))['leaf_cases'], 10)

    def test_default_and_debug_write_layouts(self):
        rows = self.mutate('wire_blocked', 'completed_write_bytes_since_capture', 18, 'PartialCiphertextPayload')
        for row in rows:
            if row.get('Action') == 'output':
                data = json.loads(row['Output'].split(PREFIX, 1)[1])
                if data['event'] == 'wire_blocked' and data['case'] == 'TestMuxEncryptedCancelAES/PartialCiphertextPayload':
                    data['active_write_consumed_bytes'] = 37
                    row['Output'] = PREFIX+json.dumps(data)+'\n'
        self.assertEqual(self.verify(rows)['leaf_cases'], 10)

    def test_split_actual_go_output_reassembled(self):
        rows = synthetic_rows(); split = []
        for row in rows:
            if row.get('Action') == 'output':
                data = row['Output']
                split.extend([dict(row, Output=data[:41]), dict(row, Output=data[41:])])
            else: split.append(row)
        self.assertEqual(self.verify(split)['leaf_cases'], 10)

    def test_wrong_package_rejected(self):
        rows = synthetic_rows(); rows[0]['Package'] = 'different-package'
        with self.assertRaises(ValueError): self.verify(rows)

    def test_cross_leaf_fragment_rejected(self):
        rows = synthetic_rows(); row = rows[0]; data = row['Output']
        rows[:1] = [dict(row, Output=data[:60]), dict(row, Test='TestMuxEncryptedCancelChaCha/PartialAuthenticatedLength', Output=data[60:])]
        with self.assertRaises(ValueError): self.verify(rows)

    def test_missing_output_continuation_rejected(self):
        rows = synthetic_rows()
        row = next(r for r in rows if r.get('Action') == 'output')
        row['Output'] = row['Output'][:60]
        with self.assertRaises(ValueError): self.verify(rows)

    def test_parent_error_detail_rejected(self):
        for value in ([], [{'caller':0,'bytes':0,'class':'io.EOF','error':'EOF'}]*3):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.verify(self.mutate('parent_joined','parent_error_results',value,'ParentTransportError'))

    def test_missing_leaf(self):
        rows = [r for r in synthetic_rows() if not r.get('Test', '').endswith('/QueuedCancel')]
        with self.assertRaises(ValueError): self.verify(rows)

    def test_skipped_leaf(self):
        rows = synthetic_rows()
        next(r for r in rows if r.get('Action') == 'pass' and '/' in r.get('Test', ''))['Action'] = 'skip'
        with self.assertRaises(ValueError): self.verify(rows)

    def test_duplicate_terminal(self):
        rows = synthetic_rows(); rows.append(dict(Action='pass', Test='TestMuxEncryptedCancelAES', Package='github.com/sagernet/sing-vmess'))
        with self.assertRaises(ValueError): self.verify(rows)

    def test_incomplete_package(self):
        with self.assertRaises(ValueError): self.verify(synthetic_rows()[:-1])

    def test_negative_field_mutations(self):
        cases = [
            ('warmup_complete', 'authenticated_length', False),
            ('wire_blocked', 'prefix_bytes', 0),
            ('wire_blocked', 'blocked_stack', 'synthetic mock writer'),
            ('wire_blocked', 'maximum_transport_writes', 2),
            ('wire_blocked', 'active_write_consumed_bytes', 15050),
            ('cancel_snapshot', 'caller_closed', False),
            ('cancel_snapshot', 'caller_joined', False),
            ('cancel_snapshot', 'parent_alive', False),
            ('cancel_snapshot', 'parent_deadline_changes', 1),
            ('cancel_snapshot', 'parent_close_calls', 1),
            ('cancel_snapshot', 'cancel_elapsed_ns', 3_000_000_000),
            ('cancel_snapshot', 'owned_bytes', 99999999),
            ('cancel_snapshot', 'operation_done', True),
            ('restore_reads', 'unchanged_prefix_replay', False),
            ('siblings_recovered', 'same_parent', False),
            ('siblings_recovered', 'tcp_reply', 4096),
            ('fresh_session_recovered', 'actual_encrypted_new', False),
            ('fresh_session_recovered', 'session_id', 2),
            ('fresh_session_recovered', 'same_parent', False),
            ('fresh_session_recovered', 'end_count', 2),
            ('fresh_session_recovered', 'handler_callbacks_finished', 3),
            ('cipher_continuity', 'server_data_nonce', 99),
            ('cipher_continuity', 'physical_bytes_exact', False),
            ('drained_before_parent_close', 'each_end_once', False),
            ('parent_close', 'real_peer_close', False),
            ('parent_joined', 'all_test_workers_joined', False),
            ('parent_joined', 'retained_streams', 1),
            ('parent_joined', 'service_error', 'i/o timeout'),
            ('parent_joined', 'seq', 1),
            ('parent_joined', 'product_qualified', True),
        ]
        for event, key, value in cases:
            with self.subTest(event=event, key=key), self.assertRaises(ValueError):
                self.verify(self.mutate(event, key, value))
        for key, value in [('real_write_errors', 0), ('parent_error_callers_closed', 2)]:
            with self.subTest(parent_error=key), self.assertRaises(ValueError):
                self.verify(self.mutate('parent_joined', key, value, 'ParentTransportError'))

    def test_restored_before_cancel_snapshot(self):
        rows = synthetic_rows()
        wanted = []
        for i, row in enumerate(rows):
            if row.get('Test') == 'TestMuxEncryptedCancelAES/PartialAuthenticatedLength' and row.get('Action') == 'output':
                event = json.loads(row['Output'].split(PREFIX, 1)[1])['event']
                if event in ('cancel_snapshot', 'restore_reads'): wanted.append(i)
        a, b = wanted; rows[a], rows[b] = rows[b], rows[a]
        with self.assertRaises(ValueError): self.verify(rows)


if __name__ == '__main__':
    unittest.main()
