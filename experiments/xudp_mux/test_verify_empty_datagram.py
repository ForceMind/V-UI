"""Synthetic verifier-only unit tests. Passing these is not a Go/runtime pass."""
import copy
import json
from pathlib import Path
import re
import tempfile
import unittest

from verify_empty_datagram import (CIPHERS, EVENTS, LEAVES, PREFIX, SCOPE, PACKAGE,
                                    case_expectations, verify_empty_datagram)


def synthetic_rows():
    rows = []
    for top, cipher in CIPHERS.items():
        encrypted = cipher != 'raw'
        for leaf in LEAVES:
            name = top + '/' + leaf
            fields = [
                dict(actual_mux=True, real_client_service=encrypted, command_mux=encrypted,
                     global_padding=encrypted, authenticated_length=encrypted),
                case_expectations(leaf),
                dict(end_count=1, close_calls=2, each_end_once=True, parent_alive=True,
                     streams=0, queued=0, active_transport_writes=0),
                dict(real_peer_close=True, outer_context_alive=True),
                dict(service_joined=encrypted, mux_handler_joined=True, writer_joined=True, watcher_joined=True,
                     all_test_workers_joined=True, handler_callbacks_finished=1,
                     handler_callbacks_expected=1, active_callers=0,
                     active_transport_writes=0, retained_streams=0, retained_queue=0,
                     handler_callbacks_active=0, no_extra_decoded_bytes=True,
                     outer_context_alive=True, service_error='mux connection closed: read frame header: EOF'),
            ]
            for seq, (event, data) in enumerate(zip(EVENTS, fields), 1):
                value = dict(data, event=event, case=name, cipher=cipher, seq=seq,
                             scope=SCOPE, product_qualified=False)
                rows.append(dict(Action='output', Test=name, Output=PREFIX+json.dumps(value)+'\n'))
            rows.append(dict(Action='pass', Test=name))
        rows.append(dict(Action='pass', Test=top))
    rows.append(dict(Action='pass'))
    for row in rows:
        row['Package'] = PACKAGE
    return rows


def modify(rows, event, key, value, top=None, leaf=None):
    for row in rows:
        if row.get('Action') != 'output':
            continue
        data = json.loads(row['Output'].split(PREFIX, 1)[1])
        if data['event'] == event and (top is None or data['case'].startswith(top+'/')) and (leaf is None or data['case'].endswith('/'+leaf)):
            data[key] = value
            row['Output'] = PREFIX+json.dumps(data)+'\n'
            return rows
    raise AssertionError('Synthetic mutation target missing')


class EmptyDatagramVerifierTests(unittest.TestCase):
    def verify(self, rows):
        with tempfile.TemporaryDirectory(prefix='synthetic-empty-verifier-') as directory:
            path = Path(directory)/'synthetic-only.jsonl'
            path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
            return verify_empty_datagram(path)

    def reject(self, rows):
        with self.assertRaises((ValueError, TypeError)):
            self.verify(rows)

    def test_complete_synthetic_fixture(self):
        result = self.verify(synthetic_rows())
        self.assertEqual((result['top_level_cases'], result['leaf_cases'], result['events']), (3, 24, 120))
        self.assertFalse(result['product_qualified'])

    def test_expected_go_names_and_explicit_leaves(self):
        text = (Path(__file__).parent/'mux_empty_datagram_test.go').read_text()
        self.assertEqual(set(re.findall(r'^func (TestMuxEmptyDatagram\w+)\(', text, re.M)), set(CIPHERS))
        self.assertEqual(re.findall(r't.Run\("([^"]+)"', text), list(LEAVES))
        self.assertIn('NewXUDPConn(capture,muxEmptyA)', text)
        self.assertIn('c.ReadFrom(p)', text)
        self.assertIn('c.ReadPacket(p)', text)
        self.assertIn('AddressSerializer.ReadAddrPort', text)
        self.assertIn('h.service.NewConnection', text)

    def test_missing_top_or_leaf(self):
        for target in (next(iter(CIPHERS)), next(iter(CIPHERS))+'/'+LEAVES[0]):
            with self.subTest(target=target):
                self.reject([row for row in synthetic_rows() if row.get('Test') != target])

    def test_skip_fail_missing_duplicate_package_and_terminal(self):
        for action in ('skip', 'fail'):
            rows = synthetic_rows()
            next(row for row in rows if row['Action'] == 'pass' and '/' in row.get('Test', ''))['Action'] = action
            self.reject(rows)
        rows = synthetic_rows()
        self.reject(rows[:-1])
        self.reject(rows + [dict(Action='pass', Package=PACKAGE)])
        self.reject(rows + [dict(Action='pass', Test=next(iter(CIPHERS)), Package=PACKAGE)])
        self.reject(rows + [dict(Action='pass', Test='TestUnexpected', Package=PACKAGE)])
        self.reject(rows[:-1] + [dict(Action='fail', Package=PACKAGE)])

    def test_every_event_required_once_in_order(self):
        base = synthetic_rows()
        for i in range(len(EVENTS)):
            with self.subTest(event=EVENTS[i]):
                self.reject(base[:i] + base[i+1:])
                self.reject(base[:i] + [base[i]] + base[i:])
        rows = copy.deepcopy(base); rows[0], rows[1] = rows[1], rows[0]; self.reject(rows)
        rows = copy.deepcopy(base); rows[4], rows[5] = rows[5], rows[4]; self.reject(rows)

    def test_split_go_output_reassembled_by_package_and_test(self):
        for cut in (5, 41, 1024):
            with self.subTest(cut=cut):
                rows = []
                for row in synthetic_rows():
                    if row.get('Action') == 'output':
                        output = row['Output']
                        rows.extend(dict(row, Output=output[i:i+cut]) for i in range(0, len(output), cut))
                    else:
                        rows.append(row)
                self.assertEqual(self.verify(rows)['leaf_cases'], 24)

    def test_split_go_output_interleaved_sources(self):
        rows = synthetic_rows()
        first, second = rows[0], rows[6]
        output1, output2 = first['Output'], second['Output']
        rows[6] = dict(second, Output=output2[17:])
        rows[:1] = [dict(first, Output=output1[:11]), dict(second, Output=output2[:17]), dict(first, Output=output1[11:])]
        self.assertEqual(self.verify(rows)['leaf_cases'], 24)

    def test_wrong_missing_package_or_cross_leaf_fragment(self):
        rows = synthetic_rows(); rows[0]['Package'] = 'different-package'; self.reject(rows)
        rows = synthetic_rows(); rows[0].pop('Package'); self.reject(rows)
        rows = synthetic_rows(); original = rows[0]; output = original['Output']
        rows[:1] = [dict(original, Output=output[:40]), dict(original, Test='TestMuxEmptyDatagramAES/StatusNewReadFrom', Output=output[40:])]
        self.reject(rows)

    def test_unterminated_or_missing_continuation_rejected(self):
        rows = synthetic_rows(); rows[0]['Output'] = rows[0]['Output'][:40]; self.reject(rows)
        rows = synthetic_rows(); rows[4]['Output'] = rows[4]['Output'].rstrip('\n'); self.reject(rows)
        rows = synthetic_rows(); rows.insert(-1, dict(Action='output', Package=PACKAGE, Output='unterminated package output')); self.reject(rows)

    def test_common_negative_mutations(self):
        changes = [
            ('transport_ready', 'actual_mux', False),
            ('transport_ready', 'command_mux', True),
            ('transport_ready', 'real_client_service', True),
            ('case_verified', 'pipe_and_address_completed', False),
            ('case_verified', 'option_data_observed', False),
            ('case_verified', 'empty_uploads', 0),
            ('case_verified', 'empty_replies', 0),
            ('case_verified', 'tuple_payload_exact', False),
            ('case_verified', 'short_roundtrips', 0),
            ('case_verified', 'status_new', False),
            ('case_verified', 'read_api', 'Read'),
            ('ends_drained', 'end_count', 0),
            ('ends_drained', 'end_count', 2),
            ('ends_drained', 'close_calls', 1),
            ('ends_drained', 'each_end_once', False),
            ('ends_drained', 'parent_alive', False),
            ('ends_drained', 'streams', 1),
            ('ends_drained', 'queued', 1),
            ('ends_drained', 'active_transport_writes', 1),
            ('parent_close', 'real_peer_close', False),
            ('parent_close', 'outer_context_alive', False),
            ('parent_joined', 'service_joined', True),
            ('parent_joined', 'mux_handler_joined', False),
            ('parent_joined', 'writer_joined', False),
            ('parent_joined', 'watcher_joined', False),
            ('parent_joined', 'all_test_workers_joined', False),
            ('parent_joined', 'handler_callbacks_finished', 0),
            ('parent_joined', 'handler_callbacks_expected', 0),
            ('parent_joined', 'active_callers', 1),
            ('parent_joined', 'active_transport_writes', 1),
            ('parent_joined', 'retained_streams', 1),
            ('parent_joined', 'retained_queue', 1),
            ('parent_joined', 'handler_callbacks_active', 1),
            ('parent_joined', 'no_extra_decoded_bytes', False),
            ('parent_joined', 'outer_context_alive', False),
            ('parent_joined', 'service_error', ''),
            ('parent_joined', 'service_error', '<nil>'),
            ('parent_joined', 'service_error', 'i/o timeout'),
            ('parent_joined', 'service_error', 'deadline exceeded'),
            ('parent_joined', 'scope', 'product-qualified'),
            ('parent_joined', 'product_qualified', True),
            ('parent_joined', 'cipher', 'none'),
            ('parent_joined', 'case', 'TestMuxEmptyDatagramAES/StatusNewReadFrom'),
            ('parent_joined', 'seq', 1),
            ('parent_joined', 'seq', True),
            ('parent_joined', 'active_callers', False),
        ]
        for event, key, value in changes:
            with self.subTest(event=event, key=key, value=value):
                self.reject(modify(synthetic_rows(), event, key, value))

    def test_each_semantic_leaf_rejects_mutated_delivery_control(self):
        changes = [
            ('StatusKeepReadFrom', 'fallback_destination', False),
            ('StatusKeepReadPacket', 'fallback_destination', True),
            ('NoDataNewAndKeep', 'no_data_frames', 1),
            ('NoDataNewAndKeep', 'first_received_is_short', False),
            ('NoDataNewAndKeep', 'no_spurious_empty', False),
            ('XUDPWriteTo', 'real_xudp', False),
            ('XUDPWriteTo', 'request_frames', 2),
            ('XUDPWriteTo', 'new_empty_frames', 0),
            ('XUDPWriteTo', 'keep_empty_frames', 0),
            ('XUDPWriteTo', 'nil_and_empty_slices', False),
            ('UDPOutputEntrypoints', 'entrypoints', ['WriteTo']),
            ('UDPOutputEntrypoints', 'exactly_one_frame_per_call', False),
            ('TCPEmptyWriteNoop', 'empty_input_frames', 1),
            ('TCPEmptyWriteNoop', 'empty_output_calls', 2),
            ('TCPEmptyWriteNoop', 'empty_output_frames', 1),
            ('TCPEmptyWriteNoop', 'spurious_zero_reads', 1),
            ('TCPEmptyWriteNoop', 'entrypoints', ['Write(nil)']),
        ]
        for leaf, key, value in changes:
            with self.subTest(leaf=leaf, key=key):
                self.reject(modify(synthetic_rows(), 'case_verified', key, value, leaf=leaf))
        for top in ('TestMuxEmptyDatagramAES', 'TestMuxEmptyDatagramChaCha'):
            self.reject(modify(synthetic_rows(), 'parent_joined', 'service_joined', False, top=top))
            for key in ('real_client_service', 'command_mux', 'global_padding', 'authenticated_length'):
                with self.subTest(top=top, key=key):
                    self.reject(modify(synthetic_rows(), 'transport_ready', key, False, top=top))

    def test_wrong_row_source_action_and_event_after_terminal(self):
        rows = synthetic_rows(); rows[0]['Test'] = 'TestMuxEmptyDatagramAES/StatusNewReadFrom'; self.reject(rows)
        rows = synthetic_rows(); rows[0]['Action'] = 'run'; self.reject(rows)
        rows = synthetic_rows(); rows[0]['Test'] = 'TestMuxEmptyDatagramRaw'; self.reject(rows)


if __name__ == '__main__':
    unittest.main()
