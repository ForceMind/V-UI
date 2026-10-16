"""Verify actual ConnectionManager observations without qualifying mux lifecycle."""
import argparse
from collections import Counter
import json
from pathlib import Path

PHASES = ('baseline', 'patched', 'lifecycle')
PREFIX = 'XUDP_ROUTE_LIFECYCLE '
TEST_PREFIX = 'TestLifecycleXUDP'
TOP = tuple(TEST_PREFIX + name for name in (
    'IdleCancellation', 'ActivityUpload', 'ActivityDownload', 'NormalUploadEnd', 'NormalDownloadEnd',
    'ParentCancel', 'CancelEOFCompetition', 'BlockedOnClose', 'BlockedInboundClose',
    'BlockedInboundWrite', 'GuardScoping', 'AlreadyCanceledContext', 'NilOnClose',
    'RealMuxReplyBackpressure', 'RealMuxStatusEndBackpressure'))
GUARDS = tuple(TEST_PREFIX + 'GuardScoping/' + name for name in
               ('native_deadline', 'no_explicit_timeout', 'inferred_timeout', 'not_udp_connect', 'reused_timer'))
LEAVES = tuple(name for name in TOP if not name.endswith('GuardScoping')) + GUARDS
LABELS = dict(zip(TOP, (
    ('idle',), ('activity_alive', 'after_activity_idle'), ('activity_alive', 'after_activity_idle'),
    ('normal_end_no_stale_hook',), ('normal_end_no_stale_hook',), ('parent_cancel',),
    ('cancel_eof_competition',), ('blocked_actual_on_close',), ('blocked_inbound_close',),
    ('blocked_inbound_write_incomplete',), (), ('already_canceled_context',), ('nil_original_on_close',),
    ('real_mux_reply_backpressure',), ('real_mux_status_end_backpressure',))))
LABELS.update({name: ('guard_' + name.split('/')[-1],) for name in GUARDS})


def need(condition, message):
    if not condition:
        raise ValueError(message)


def exact(row, key, expected):
    need(type(row.get(key)) is type(expected) and row[key] == expected, 'Unexpected ' + key)


def verify_events(phase, name, events):
    need(phase in PHASES and name in LEAVES and events, 'Unknown or empty route observation')
    previous = -1
    for index, row in enumerate(events, 1):
        for key, value in {'schema': 1, 'phase': phase, 'case': name, 'seq': index}.items(): exact(row, key, value)
        elapsed = row.get('elapsed_ns')
        need(type(elapsed) is int and previous <= elapsed <= 15_000_000_000, 'Unbounded/nonmonotonic route observation')
        previous = elapsed
        if 'full_lifecycle_qualified' in row: exact(row, 'full_lifecycle_qualified', False)
    counts = Counter(row.get('event') for row in events)
    need(not counts['harness_precondition_failed'], 'Route fixture preconditions failed')
    for key in ('setup', 'cleanup_start', 'cleanup_joined'):
        need(counts[key] == 1, 'Missing or duplicate route lifetime boundary: ' + key)
    need(events[0]['event'] == 'setup' and events[-1]['event'] == 'cleanup_joined', 'Incomplete route event lifetime')
    setup, joined = events[0], events[-1]
    for key, value in {'idle_ns': 200_000_000, 'wait_bound_ns': 2_000_000_000, 'window_ns': 450_000_000,
                       'worker_count_source': 'runtime.Stack', 'worker_categories_overlap': False,
                       'actual_entrypoint': 'ConnectionManager.NewPacketConnection',
                       'stack_classifier_checks': 5, 'full_lifecycle_qualified': False}.items(): exact(setup, key, value)
    for key in ('route_copy_workers', 'route_callback_workers', 'canceler_workers'): exact(joined, key, 0)
    exact(joined, 'mux_cleanup_workers', 0)
    for key in ('test_owned_workers_joined', 'actual_route_workers_joined'): exact(joined, key, True)
    exact(joined, 'full_lifecycle_qualified', False)
    exact(joined, 'on_close_calls', 0 if name.endswith('NilOnClose') else 1)
    snapshots = [row for row in events if row['event'] == 'snapshot']
    need(tuple(row.get('label') for row in snapshots) == LABELS[name], 'Wrong or missing route snapshots')
    cleanup = next(row for row in events if row['event'] == 'cleanup_start')
    need(all(row['seq'] < cleanup['seq'] for row in snapshots), 'Snapshot was taken after explicit cleanup')
    callbacks = [row for row in events if row['event'] == 'actual_on_close']
    need(len(callbacks) == (0 if name.endswith('NilOnClose') else 1), 'Original handler invocation count differs')
    for row in snapshots:
        exact(row, 'full_lifecycle_qualified', False)
        for key, maximum in (('route_copy_workers', 2), ('route_callback_workers', 1), ('canceler_workers', 1), ('on_close_calls', 1)):
            need(type(row.get(key)) is int and 0 <= row[key] <= maximum, 'Invalid actual worker/callback count: ' + key)
        label = row['label']
        if label not in ('activity_alive', 'cancel_eof_competition'):
            need(row['elapsed_ns'] >= 450_000_000, 'Snapshot preceded the actual observation window')
        parent_canceled = label in ('parent_cancel', 'already_canceled_context', 'cancel_eof_competition',
                                    'guard_no_explicit_timeout', 'guard_inferred_timeout')
        exact(row, 'parent_context_done', parent_canceled)
        expected_closed = phase == 'lifecycle'
        if label == 'activity_alive': expected_closed = False
        elif label in ('normal_end_no_stale_hook', 'cancel_eof_competition', 'guard_native_deadline'): expected_closed = True
        elif label.startswith('guard_'): expected_closed = False
        exact(row, 'outbound_closed', expected_closed)
        if label in ('blocked_inbound_write_incomplete', 'real_mux_reply_backpressure'):
            exact(row, 'route_copy_workers', 2)
            exact(row, 'on_close_calls', 0)
        elif label == 'real_mux_status_end_backpressure':
            need(row['route_copy_workers'] >= 1, 'Real StatusEnd backpressure did not retain any copy')
        elif label in ('blocked_actual_on_close', 'blocked_inbound_close'):
            need(row['route_copy_workers'] >= 1, 'Blocked close did not retain any actual copy')
            exact(row, 'on_close_calls', 1)
        else:
            exact(row, 'route_copy_workers', 0 if expected_closed else 2)
        if label.startswith('guard_'):
            exact(row, 'route_callback_workers', 0)
        if label in ('normal_end_no_stale_hook',):
            exact(row, 'outbound_close_calls', 3 if phase == 'lifecycle' else 2)
        if expected_closed and label not in ('blocked_actual_on_close', 'blocked_inbound_close',
                                             'blocked_inbound_write_incomplete', 'real_mux_reply_backpressure',
                                             'real_mux_status_end_backpressure'):
            # This is BEFORE cleanup_start. Forced teardown counts cannot stand
            # in for autonomous ownership/copy completion in positive cases.
            exact(row, 'inbound_closed', True)
            for key in ('route_callback_workers', 'canceler_workers', 'inbound_reads',
                        'inbound_writes', 'outbound_reads', 'outbound_writes'): exact(row, key, 0)
            exact(row, 'on_close_calls', 0 if name.endswith('NilOnClose') else 1)
    blocking = {'blocked_actual_on_close': ('actual_on_close', None),
                'blocked_inbound_close': ('fixture_close_blocked', 'inbound'),
                'blocked_inbound_write_incomplete': ('fixture_write_blocked', 'inbound')}
    for snapshot in snapshots:
        if snapshot['label'] in blocking:
            event, role = blocking[snapshot['label']]
            witnesses = [row for row in events if row['event'] == event and (role is None or row.get('role') == role)]
            need(len(witnesses) == 1 and witnesses[0]['seq'] < snapshot['seq'] and
                 snapshot['elapsed_ns'] - witnesses[0]['elapsed_ns'] >= 450_000_000,
                 'Missing actual blocked-operation observation window')
    if name.endswith('CancelEOFCompetition'):
        races = [row for row in events if row['event'] == 'race_window']
        need(len(races) == 1, 'Missing actual cancellation/EOF overlap')
        race = races[0]
        for key, value in {'pre_close_gate_entered': True, 'outbound_closed': False, 'on_close_calls': 1,
                           'parent_context_done': True, 'route_callback_workers': int(phase == 'lifecycle'),
                           'actual_timer_callback_held': phase == 'lifecycle',
                           'full_lifecycle_qualified': False}.items(): exact(race, key, value)
        need(type(race.get('route_copy_workers')) is int and 1 <= race['route_copy_workers'] <= 2,
             'Competition did not retain a live copy')
        gate = [row for row in events if row['event'] == 'fixture_pre_close_blocked' and row.get('role') == 'outbound']
        need(len(gate) == 1 and gate[0]['seq'] < race['seq'] < snapshots[0]['seq'] < cleanup['seq'],
             'Race evidence did not precede gate release and cleanup')
        need(callbacks[0]['seq'] < race['seq'], 'Original handler did not overlap actual held callback')
    else:
        need(not counts['race_window'], 'Race event in unrelated test')
    if name.endswith(('ActivityUpload', 'ActivityDownload')):
        need(snapshots[1]['elapsed_ns'] - snapshots[0]['elapsed_ns'] >= 450_000_000,
             'Activity cessation observation window was shortened')
        activity = [row for row in events if row['event'] == 'activity_window']
        need(len(activity) == 1, 'Missing actual activity timing evidence')
        row = activity[0]
        exact(row, 'packet_count', 15)
        exact(row, 'direction', 'upload' if name.endswith('Upload') else 'download')
        need(type(row.get('max_gap_ns')) is int and 0 <= row['max_gap_ns'] < 200_000_000, 'Activity had an idle-sized scheduling gap')
        need(type(row.get('activity_span_ns')) is int and 600_000_000 <= row['activity_span_ns'] <= 3_000_000_000,
             'Activity did not span three idle periods within its bound')
        need(row['seq'] < snapshots[0]['seq'], 'Activity timing emitted after snapshot')
        successful = [event for event in events if event['event'] == 'activity_success']
        need(len(successful) == 15, 'Missing per-packet activity evidence')
        previous = None
        gaps, times = [], []
        for index, event in enumerate(successful):
            exact(event, 'index', index)
            exact(event, 'idle_ns', 200_000_000)
            now = event.get('successful_write_ns')
            need(type(now) is int and 0 <= now <= event['elapsed_ns'], 'Impossible successful activity timestamp')
            gap = 0 if previous is None else now - previous
            need(0 <= gap < 200_000_000, 'Activity interval violated the idle premise')
            exact(event, 'gap_ns', gap)
            need(event['seq'] < row['seq'], 'Activity summary preceded a packet')
            gaps.append(gap)
            times.append(now)
            previous = now
        exact(row, 'max_gap_ns', max(gaps))
        exact(row, 'activity_span_ns', times[-1] - times[0])
    if phase == 'lifecycle' and name.endswith(('IdleCancellation', 'ActivityUpload', 'ActivityDownload', 'NilOnClose')):
        causal = [row for row in events if row['event'] == 'route_log'
                  and row['seq'] < snapshots[-1]['seq']
                  and row.get('context_cause_sentinel') == 'os.ErrDeadlineExceeded']
        need(causal, 'Missing actual route-context deadline evidence before cleanup')
        need(all(row.get('context_cause') == 'deadline_exceeded' and row.get('context_error') == 'canceled'
                 for row in causal), 'Actual route ctx.Err/cause evidence disagrees')
    if phase == 'lifecycle' and name.endswith(('ParentCancel', 'AlreadyCanceledContext')):
        causal = [row for row in events if row['event'] == 'route_log' and row['seq'] < snapshots[-1]['seq']
                  and row.get('context_cause_sentinel') == 'context.Canceled']
        need(causal and all(row.get('context_cause') == row.get('context_error') == 'canceled' for row in causal),
             'Missing actual route parent/pre-canceled context evidence')
    real_mux = 'RealMux' in name
    if real_mux:
        exact(joined, 'mux_cleanup_workers', 0)
        exact(joined, 'active_transport_writes', 0)
        snapshot = snapshots[0]
        exact(snapshot, 'parent_context_done', False)
        exact(snapshot, 'outer_context_done', False)
        exact(snapshot, 'wire_session_id', 0)
        if name.endswith('ReplyBackpressure'):
            exact(snapshot, 'reply_write_starts', 1)
            exact(snapshot, 'reply_write_active', 1)
        else:
            exact(snapshot, 'reply_write_starts', 0)
            exact(snapshot, 'reply_write_active', 0)
            exact(snapshot, 'status_end_write_active', int(phase == 'lifecycle'))
            exact(snapshot, 'status_end_write_starts', int(phase == 'lifecycle'))
        for event in ('restore_reads', 'drained_before_parent_close'): need(counts[event] == 1, 'Missing real mux recovery boundary')
        restore = next(row for row in events if row['event'] == 'restore_reads')
        drain = next(row for row in events if row['event'] == 'drained_before_parent_close')
        exact(restore, 'explicit_cleanup', True)
        exact(restore, 'parent_transport_open', True)
        exact(drain, 'parent_context_done', False)
        exact(drain, 'outer_context_done', False)
        exact(drain, 'on_close_calls', 1)
        exact(drain, 'active_transport_writes', 0)
        need(snapshot['seq'] < restore['seq'] < drain['seq'] < cleanup['seq'], 'Mux cleanup disguised as natural lifecycle')
    else:
        for key in ('inbound_reads', 'inbound_writes', 'outbound_reads', 'outbound_writes'): exact(joined, key, 0)
    return {'case': name, 'phase': phase, 'events': len(events), 'snapshots': snapshots,
            'cleanup_joined_ns': joined['elapsed_ns'], 'full_lifecycle_qualified': False}


def verify(directory):
    summary = {'scope': 'Three-way owned-outbound diagnostic; blocked mux copies remain unqualified',
               'full_lifecycle_qualified': False, 'phases': {}}
    for phase in PHASES:
        rows = [json.loads(line) for line in (directory / (phase + '-route-lifecycle-tests.jsonl')).read_text().splitlines()]
        need(rows and not any(row.get('Action') in ('fail', 'skip') for row in rows), 'Failed/skipped route test or build')
        need(all(row.get('Package') == 'github.com/sagernet/sing-box/route' for row in rows), 'Unexpected route test package')
        terminal = [row for row in rows if row.get('Test') and row.get('Action') == 'pass']
        need(Counter(row['Test'] for row in terminal) == Counter(TOP + GUARDS), 'Route test identity/count mismatch')
        package = [row for row in rows if not row.get('Test') and row.get('Action') == 'pass']
        need(len(package) == 1, 'Missing exact route package completion')
        code = json.loads((directory / (phase + '-route-lifecycle-exit.json')).read_text())
        need(type(code.get('returncode')) is int and code['returncode'] == 0, 'Route tests returned a nonzero code')
        events = {name: [] for name in LEAVES}
        for row in rows:
            for line in row.get('Output', '').splitlines():
                if PREFIX in line:
                    need(row.get('Test') in events, 'Route event outside an expected leaf test')
                    events[row['Test']].append(json.loads(line.split(PREFIX, 1)[1]))
        summary['phases'][phase] = [verify_events(phase, name, events[name]) for name in LEAVES]
    (directory / 'route-lifecycle-verification.json').write_text(json.dumps(summary, indent=2) + '\n')
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    print(json.dumps(verify(parser.parse_args().directory), indent=2))
