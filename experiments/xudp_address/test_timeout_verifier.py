from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from verify_timeout_observations import verify, verify_events

NAMES = ('TestObservationXUDPTimerContextDoesNotUnblockRead', 'TestObservationXUDPReadDeadlineUnblocksRead')


def recorded_shape(name, supported):
    events = []
    def add(event, **values):
        events.append(dict(schema=1, case=name, event=event, seq=len(events) + 1,
                           elapsed_ns=len(events) * 1000000, **values))
    add('setup', deadline_supported=supported, idle_ns=200000000, post_cancel_hold_ns=200000000,
        wait_bound_ns=2000000000, on_close_scope='harness_after_real_CopyPacket',
        activity_clock='before_successful_wrapped_WritePacket')
    add('set_read_deadline', role='source', error='nil' if supported else 'invalid', zero=True)
    add('wrapper_selected', wrapper='TimeoutPacketConn' if supported else 'TimerPacketConn')
    add('write_packet', role='source', bytes=13)
    add('last_activity', source_writes=1)
    add('read_enter', role='source', context_done_at_entry=False)
    if supported:
        add('set_read_deadline', role='source', error='nil', zero=False)
    add('context_done', error='canceled', cause='deadline_exceeded')
    def closed(error):
        add('read_packet_return', error=error)
        add('copy_packet_return', error=error, bytes=0)
        add('harness_on_close', error=error)
        add('close', role='source')
        add('close', role='sink')
    if supported:
        closed('deadline_exceeded')
    add('observation_snapshot', context_done=True, parent_context_done=False, read_returns=int(supported),
        on_close_calls=int(supported), source_closed=int(supported), sink_closed=int(supported),
        source_writes=1, sink_writes=0, copy_bytes=0, idle_gate_qualified=False)
    add('cleanup_start')
    if not supported:
        closed('closed')
    add('cleanup_joined', source_closed=1, sink_closed=1, on_close_calls=1, workers_started=True)
    elapsed = 0
    for row in events:
        elapsed += 1_000_000
        if row['event'] == 'context_done': elapsed += 200_000_000
        if row['event'] == 'observation_snapshot' and not supported: elapsed += 200_000_000
        row['elapsed_ns'] = elapsed
    by = {row['event']: row for row in events}
    by['last_activity']['started_elapsed_ns'] = by['last_activity']['elapsed_ns'] - 100000
    by['context_done']['since_last_activity_ns'] = by['context_done']['elapsed_ns'] - by['last_activity']['started_elapsed_ns']
    return events


class TimeoutObservationVerifierTests(unittest.TestCase):
    def fixture(self, root):
        for phase in ('baseline', 'patched'):
            rows = []
            for name, supported in zip(NAMES, (False, True)):
                rows += [dict(Action='output', Test=name,
                              Output='test.go:99: XUDP_TIMEOUT_OBSERVATION ' + json.dumps(event) + '\n')
                         for event in recorded_shape(name, supported)]
                rows.append(dict(Action='pass', Test=name))
            rows.append(dict(Action='pass', Elapsed=.7))
            for row in rows: row['Package'] = 'github.com/sagernet/sing-vmess'
            (root / (phase + '-timeout-observations.jsonl')).write_text('\n'.join(json.dumps(row) for row in rows))
            (root / (phase + '-timeout-observation-exit.json')).write_text('{"returncode": 0}')

    def test_both_fixed_dependency_observations_are_distinct_from_qualification(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            result = verify(root)
            self.assertEqual(set(result['phases']), {'baseline', 'patched'})
            for cases in result['phases'].values():
                self.assertEqual([x['classification'] for x in cases],
                                 ['context_cancel_did_not_release_read', 'deadline_released_read'])
                self.assertTrue(all(x['idle_gate_qualified'] is False for x in cases))

    def test_missing_extra_failed_or_skipped_go_test_is_rejected(self):
        for kind in ('empty', 'missing', 'extra', 'skip', 'failure', 'exit', 'package'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self.fixture(root)
                path = root / 'patched-timeout-observations.jsonl'
                rows = [json.loads(line) for line in path.read_text().splitlines()]
                if kind == 'empty': rows = []
                elif kind == 'missing': rows = [row for row in rows if row.get('Test') != NAMES[0]]
                elif kind == 'extra': rows.append(dict(Action='pass', Test='TestObservationXUDPUnexpected'))
                elif kind in ('skip', 'failure'): rows.append(dict(Action='skip' if kind == 'skip' else 'fail', Test=NAMES[0]))
                elif kind == 'package': rows[0]['Package'] = 'example.invalid/wrong-module'
                else: (root / 'patched-timeout-observation-exit.json').write_text('{"returncode": 1}')
                path.write_text('\n'.join(json.dumps(row) for row in rows))
                with self.assertRaises(ValueError): verify(root)

    def test_identity_bounds_and_early_timer_cleanup_cannot_be_forged(self):
        for kind in ('case', 'schema', 'seq', 'time', 'extra', 'cause', 'wrapper', 'qualified', 'closed', 'traffic', 'early_timer', 'missing_hold', 'causal_order', 'late_read', 'activity_missing', 'activity_short', 'activity_inconsistent'):
            with self.subTest(kind=kind):
                events = recorded_shape(NAMES[0], False)
                by = {x['event']: x for x in events}
                if kind == 'case': events[2]['case'] = NAMES[1]
                if kind == 'schema': events[2]['schema'] = 9
                if kind == 'seq': events[2]['seq'] = 1
                if kind == 'time': events[2]['elapsed_ns'] = -1
                if kind == 'extra': events.append(deepcopy(events[-1]))
                if kind == 'cause': by['context_done']['cause'] = 'closed'
                if kind == 'wrapper': by['wrapper_selected']['wrapper'] = 'TimeoutPacketConn'
                if kind == 'qualified': by['observation_snapshot']['idle_gate_qualified'] = True
                if kind == 'closed': by['observation_snapshot']['source_closed'] = 1
                if kind == 'traffic': by['observation_snapshot']['source_writes'] = 2
                if kind == 'early_timer':
                    for row in events:
                        if row['seq'] >= by['context_done']['seq']: row['elapsed_ns'] -= 200000000
                if kind == 'late_read': by['read_enter']['context_done_at_entry'] = True
                if kind == 'activity_missing': del by['last_activity']['started_elapsed_ns']
                if kind == 'activity_short': by['context_done']['since_last_activity_ns'] = 100000000
                if kind == 'activity_inconsistent': by['context_done']['since_last_activity_ns'] = 2000000000
                if kind == 'causal_order':
                    a, b = by['read_packet_return'], by['copy_packet_return']
                    a['event'], b['event'] = b['event'], a['event']
                    a['bytes'] = 0
                if kind == 'missing_hold':
                    for row in events:
                        if row['seq'] >= by['observation_snapshot']['seq']: row['elapsed_ns'] -= 200000000
                with self.assertRaises(ValueError): verify_events(NAMES[0], events)

    def test_missing_cleanup_or_wrong_deadline_error_is_rejected(self):
        for kind in ('close', 'return', 'joined', 'callback'):
            with self.subTest(kind=kind):
                events = recorded_shape(NAMES[1], True)
                by = {x['event']: x for x in events}
                if kind == 'close': events.remove(next(x for x in events if x['event'] == 'close'))
                if kind == 'return': by['read_packet_return']['error'] = 'closed'
                if kind == 'joined': by['cleanup_joined']['workers_started'] = False
                if kind == 'callback': by['harness_on_close']['error'] = 'nil'
                with self.assertRaises(ValueError): verify_events(NAMES[1], events)


if __name__ == '__main__':
    unittest.main()
