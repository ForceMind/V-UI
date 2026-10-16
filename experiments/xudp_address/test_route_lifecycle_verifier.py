from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from verify_route_lifecycle import GUARDS, LABELS, LEAVES, PHASES, TOP, verify, verify_events


def synthetic_events(phase, name):
    events = []
    def add(event, at=None, **fields):
        elapsed = (events[-1]['elapsed_ns'] + 1_000_000 if events else 0) if at is None else at
        row = dict(schema=1, phase=phase, case=name, event=event, seq=len(events) + 1, elapsed_ns=elapsed, **fields)
        events.append(row)
        return row
    add('setup', idle_ns=200_000_000, wait_bound_ns=2_000_000_000, window_ns=450_000_000,
        worker_count_source='runtime.Stack', worker_categories_overlap=False,
        actual_entrypoint='ConnectionManager.NewPacketConnection', stack_classifier_checks=5, full_lifecycle_qualified=False)
    activity = name.endswith(('ActivityUpload', 'ActivityDownload'))
    mux = 'RealMux' in name
    if activity:
        for index in range(15):
            now = 10_000_000 + index * 50_000_000
            add('activity_success', at=now + 1_000_000, index=index, successful_write_ns=now,
                gap_ns=0 if index == 0 else 50_000_000, idle_ns=200_000_000)
        add('activity_window', packet_count=15, max_gap_ns=50_000_000, activity_span_ns=700_000_000,
            direction='upload' if name.endswith('Upload') else 'download')
    callback_emitted = False
    for label in LABELS[name]:
        closed = phase == 'lifecycle'
        if label == 'activity_alive': closed = False
        elif label in ('normal_end_no_stale_hook', 'cancel_eof_competition', 'guard_native_deadline'): closed = True
        elif label.startswith('guard_'): closed = False
        copies = 0 if closed else 2
        if label in ('blocked_actual_on_close', 'blocked_inbound_close'): copies = 1 if phase == 'lifecycle' else 2
        if label in ('blocked_inbound_write_incomplete', 'real_mux_reply_backpressure'): copies = 2
        if label == 'real_mux_status_end_backpressure': copies = 1 if phase == 'lifecycle' else 2
        callbacks = int(closed)
        if label in ('blocked_actual_on_close', 'blocked_inbound_close'): callbacks = 1
        if label in ('blocked_inbound_write_incomplete', 'real_mux_reply_backpressure', 'nil_original_on_close'): callbacks = 0
        if callbacks and not callback_emitted:
            add('actual_on_close', error='closed')
            callback_emitted = True
        if phase == 'lifecycle' and name.endswith(('IdleCancellation', 'ActivityUpload', 'ActivityDownload', 'NilOnClose')) and label != 'activity_alive':
            add('route_log', context_cause_sentinel='os.ErrDeadlineExceeded', context_cause='deadline_exceeded', context_error='canceled')
        if phase == 'lifecycle' and name.endswith(('ParentCancel', 'AlreadyCanceledContext')):
            add('route_log', context_cause_sentinel='context.Canceled', context_cause='canceled', context_error='canceled')
        if label == 'blocked_inbound_close': add('fixture_close_blocked', role='inbound')
        if label == 'blocked_inbound_write_incomplete': add('fixture_write_blocked', role='inbound')
        if label == 'cancel_eof_competition':
            add('fixture_pre_close_blocked', role='outbound')
            add('race_window', pre_close_gate_entered=True, outbound_closed=False, on_close_calls=1,
                parent_context_done=True, route_copy_workers=1, route_callback_workers=int(phase == 'lifecycle'),
                actual_timer_callback_held=phase == 'lifecycle', full_lifecycle_qualified=False)
        parent_canceled = label in ('parent_cancel', 'already_canceled_context', 'cancel_eof_competition',
                                    'guard_no_explicit_timeout', 'guard_inferred_timeout')
        fields = dict(label=label, outbound_closed=closed, inbound_closed=closed, on_close_calls=callbacks,
                      route_copy_workers=copies, route_callback_workers=0, canceler_workers=0,
                      outbound_close_calls=3 if phase == 'lifecycle' else 2, full_lifecycle_qualified=False,
                      parent_context_done=parent_canceled, inbound_reads=0, inbound_writes=0, outbound_reads=0, outbound_writes=0)
        if mux:
            reply = name.endswith('ReplyBackpressure')
            fields.update(wire_session_id=0, outer_context_done=False, reply_write_starts=int(reply), reply_write_active=int(reply),
                          status_end_write_starts=int(not reply and phase == 'lifecycle'),
                          status_end_write_active=int(not reply and phase == 'lifecycle'))
        add('snapshot', at=events[-1]['elapsed_ns'] + 450_000_000, **fields)
    if mux:
        add('restore_reads', explicit_cleanup=True, parent_transport_open=True)
        if not callback_emitted:
            add('actual_on_close', error='closed')
            callback_emitted = True
        add('drained_before_parent_close', parent_context_done=False, outer_context_done=False, on_close_calls=1,
            active_transport_writes=0, full_lifecycle_qualified=False)
    add('cleanup_start')
    if not callback_emitted and not name.endswith('NilOnClose'): add('actual_on_close', error='closed')
    add('cleanup_joined', route_copy_workers=0, route_callback_workers=0, canceler_workers=0, mux_cleanup_workers=0,
        test_owned_workers_joined=True, actual_route_workers_joined=True, full_lifecycle_qualified=False,
        on_close_calls=0 if name.endswith('NilOnClose') else 1, active_transport_writes=0,
        inbound_reads=0, inbound_writes=0, outbound_reads=0, outbound_writes=0)
    return events


class RouteLifecycleEvidenceTests(unittest.TestCase):
    def fixture(self, root):
        for phase in PHASES:
            rows = []
            for name in LEAVES:
                rows += [dict(Action='output', Test=name, Output='test.go:99: XUDP_ROUTE_LIFECYCLE ' + json.dumps(event) + '\n')
                         for event in synthetic_events(phase, name)]
            rows += [dict(Action='pass', Test=name) for name in TOP + GUARDS]
            rows.append(dict(Action='pass', Elapsed=12.0))
            for row in rows: row['Package'] = 'github.com/sagernet/sing-box/route'
            (root / (phase + '-route-lifecycle-tests.jsonl')).write_text('\n'.join(json.dumps(row) for row in rows))
            (root / (phase + '-route-lifecycle-exit.json')).write_text('{"returncode": 0}')

    def test_three_complete_control_matrices_do_not_grant_full_lifecycle_qualification(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); self.fixture(root)
            result = verify(root)
            self.assertEqual(tuple(result['phases']), PHASES)
            self.assertFalse(result['full_lifecycle_qualified'])
            self.assertTrue(all(len(cases) == 19 for cases in result['phases'].values()))

    def test_wrong_package_failure_skip_missing_duplicate_and_wrong_exit_are_rejected(self):
        for mode in ('package', 'failure', 'skip', 'missing', 'duplicate', 'exit', 'unknown_test'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); self.fixture(root)
                path = root / 'lifecycle-route-lifecycle-tests.jsonl'
                rows = [json.loads(line) for line in path.read_text().splitlines()]
                if mode == 'package': rows[0]['Package'] = 'example.invalid/not-route'
                elif mode in ('failure', 'skip'): rows[-1]['Action'] = 'fail' if mode == 'failure' else 'skip'
                elif mode == 'missing': rows = [row for row in rows if row.get('Test') != LEAVES[0]]
                elif mode == 'duplicate': rows.insert(0, next(row for row in rows if row.get('Test') and row['Action'] == 'pass'))
                elif mode == 'exit': (root / 'lifecycle-route-lifecycle-exit.json').write_text('{"returncode": 2}')
                else: rows[0]['Test'] = 'TestLifecycleXUDPUnknown'
                path.write_text('\n'.join(json.dumps(row) for row in rows))
                with self.assertRaises(ValueError): verify(root)

    def test_event_identity_cleanup_and_actual_context_are_strict(self):
        name = 'TestLifecycleXUDPIdleCancellation'
        for mode in ('phase', 'seq', 'time', 'qualified', 'workers', 'callbacks', 'cleanup', 'outbound', 'cause', 'source', 'precondition', 'snapshot_io', 'snapshot_inbound', 'classifier', 'short_window', 'parent'):
            with self.subTest(mode=mode):
                events = synthetic_events('lifecycle', name)
                snapshot = next(row for row in events if row['event'] == 'snapshot')
                if mode == 'phase': events[0]['phase'] = 'patched'
                elif mode == 'seq': events[1]['seq'] += 1
                elif mode == 'time': events[-1]['elapsed_ns'] = 20_000_000_000
                elif mode == 'qualified': snapshot['full_lifecycle_qualified'] = True
                elif mode == 'workers': events[-1]['route_copy_workers'] = 1
                elif mode == 'callbacks': events[-1]['on_close_calls'] = 2
                elif mode == 'cleanup': events[-1]['actual_route_workers_joined'] = False
                elif mode == 'outbound': snapshot['outbound_closed'] = False
                elif mode == 'cause': next(row for row in events if row['event'] == 'route_log')['context_cause_sentinel'] = 'context.Canceled'
                elif mode == 'source': events[0]['worker_count_source'] = 'guessed from onClose'
                elif mode == 'snapshot_io': snapshot['inbound_reads'] = 1
                elif mode == 'snapshot_inbound': snapshot['inbound_closed'] = False
                elif mode == 'classifier': events[0]['stack_classifier_checks'] = 4
                elif mode == 'short_window': snapshot['elapsed_ns'] = 10_000_000
                elif mode == 'parent': snapshot['parent_context_done'] = True
                else: events[1]['event'] = 'harness_precondition_failed'
                with self.assertRaises(ValueError): verify_events('lifecycle', name, events)

    def test_activity_cannot_claim_renewal_from_a_summary_or_idle_sized_gap(self):
        name = 'TestLifecycleXUDPActivityUpload'
        for mode in ('count', 'gap', 'span', 'direction', 'packet', 'timestamp', 'summary', 'short_idle_window'):
            with self.subTest(mode=mode):
                events = synthetic_events('lifecycle', name)
                window = next(row for row in events if row['event'] == 'activity_window')
                success = [row for row in events if row['event'] == 'activity_success']
                if mode == 'count': window['packet_count'] = 9
                elif mode == 'gap': window['max_gap_ns'] = 200_000_000
                elif mode == 'span': window['activity_span_ns'] = 400_000_000
                elif mode == 'direction': window['direction'] = 'download'
                elif mode == 'packet': success[-1]['index'] = 13
                elif mode == 'timestamp': success[5]['successful_write_ns'] += 100
                elif mode == 'short_idle_window':
                    snapshots = [row for row in events if row['event'] == 'snapshot']
                    snapshots[-1]['elapsed_ns'] = snapshots[0]['elapsed_ns'] + 449_000_000
                else: window['max_gap_ns'] -= 1
                with self.assertRaises(ValueError): verify_events('lifecycle', name, events)

    def test_blocked_operations_require_live_copy_and_full_actual_blocking_window(self):
        for suffix in ('BlockedOnClose', 'BlockedInboundClose', 'BlockedInboundWrite'):
            name = 'TestLifecycleXUDP' + suffix
            for mode in ('copy', 'window', 'missing_witness', 'parent'):
                with self.subTest(name=name, mode=mode):
                    events = synthetic_events('lifecycle', name)
                    snapshot = next(row for row in events if row['event'] == 'snapshot')
                    event = {'BlockedOnClose': 'actual_on_close', 'BlockedInboundClose': 'fixture_close_blocked',
                             'BlockedInboundWrite': 'fixture_write_blocked'}[suffix]
                    witness = next(row for row in events if row['event'] == event)
                    if mode == 'copy': snapshot['route_copy_workers'] = 0
                    elif mode == 'window': snapshot['elapsed_ns'] = witness['elapsed_ns'] + 449_000_000
                    elif mode == 'missing_witness': witness['event'] = 'unrelated_event'
                    else: snapshot['parent_context_done'] = True
                    with self.assertRaises(ValueError): verify_events('lifecycle', name, events)

    def test_competition_requires_real_held_callback_overlap_before_cleanup(self):
        name = 'TestLifecycleXUDPCancelEOFCompetition'
        for phase in PHASES:
            for mode in ('missing', 'closed', 'copy', 'callback', 'gate', 'parent', 'handler', 'held'):
                with self.subTest(phase=phase, mode=mode):
                    events = synthetic_events(phase, name)
                    race = next(row for row in events if row['event'] == 'race_window')
                    if mode == 'missing': race['event'] = 'unrelated_event'
                    elif mode == 'closed': race['outbound_closed'] = True
                    elif mode == 'copy': race['route_copy_workers'] = 0
                    elif mode == 'callback': race['route_callback_workers'] = 1 - int(phase == 'lifecycle')
                    elif mode == 'gate': next(row for row in events if row['event'] == 'fixture_pre_close_blocked')['role'] = 'inbound'
                    elif mode == 'parent': race['parent_context_done'] = False
                    elif mode == 'handler': race['on_close_calls'] = 0
                    else: race['actual_timer_callback_held'] = phase != 'lifecycle'
                    with self.assertRaises(ValueError): verify_events(phase, name, events)

    def test_real_mux_resource_close_does_not_erase_copy_backpressure(self):
        for name in ('TestLifecycleXUDPRealMuxReplyBackpressure', 'TestLifecycleXUDPRealMuxStatusEndBackpressure'):
            for mode in ('parent', 'copy', 'write', 'restore', 'drain', 'mux_cleanup'):
                with self.subTest(name=name, mode=mode):
                    events = synthetic_events('lifecycle', name)
                    snapshot = next(row for row in events if row['event'] == 'snapshot')
                    if mode == 'parent': snapshot['parent_context_done'] = True
                    elif mode == 'copy': snapshot['route_copy_workers'] = 0
                    elif mode == 'write': snapshot['reply_write_active' if 'Reply' in name else 'status_end_write_active'] = 0
                    elif mode == 'restore': next(row for row in events if row['event'] == 'restore_reads')['parent_transport_open'] = False
                    elif mode == 'drain': next(row for row in events if row['event'] == 'drained_before_parent_close')['active_transport_writes'] = 1
                    else: events[-1]['mux_cleanup_workers'] = 1
                    with self.assertRaises(ValueError): verify_events('lifecycle', name, events)


if __name__ == '__main__':
    unittest.main()
