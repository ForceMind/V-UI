"""Verify isolated lifecycle observations, never qualify the product idle gate."""
import argparse
from collections import Counter
import json
from pathlib import Path

PREFIX = 'XUDP_TIMEOUT_OBSERVATION '
CASES = {
    'TestObservationXUDPTimerContextDoesNotUnblockRead': False,
    'TestObservationXUDPReadDeadlineUnblocksRead': True,
}


def need(ok, message):
    if not ok:
        raise ValueError(message)


def verify_events(name, events):
    supported = CASES[name]
    required_once = ('setup', 'wrapper_selected', 'last_activity', 'read_enter', 'write_packet',
                     'context_done', 'observation_snapshot', 'cleanup_start', 'cleanup_joined',
                     'read_packet_return', 'copy_packet_return', 'harness_on_close')
    counts = Counter(x.get('event') for x in events)
    need(set(counts) == set(required_once) | {'close', 'set_read_deadline'}, 'Unexpected or missing observation events')
    need(all(counts[x] == 1 for x in required_once) and counts['close'] == 2, 'Duplicated or missing event identity')
    need(counts['set_read_deadline'] >= (2 if supported else 1), 'Missing deadline branch probes')
    previous = -1
    for i, row in enumerate(events, 1):
        need(type(row.get('schema')) is int and row['schema'] == 1 and row.get('case') == name
             and type(row.get('seq')) is int and row['seq'] == i, 'Wrong observation identity or sequence')
        elapsed = row.get('elapsed_ns')
        need(type(elapsed) is int and previous <= elapsed <= 12_000_000_000, 'Non-monotonic or unbounded observation')
        previous = elapsed
    by_event = {x['event']: x for x in events}
    setup = by_event['setup']
    for key, expected in {'deadline_supported': supported, 'idle_ns': 200_000_000,
                          'post_cancel_hold_ns': 200_000_000, 'wait_bound_ns': 2_000_000_000,
                          'on_close_scope': 'harness_after_real_CopyPacket',
                          'activity_clock': 'before_successful_wrapped_WritePacket'}.items():
        need(type(setup.get(key)) is type(expected) and setup[key] == expected, 'Changed observation setup: ' + key)
    need(events[0]['event'] == 'setup' and events[-1]['event'] == 'cleanup_joined', 'Incomplete observation lifetime')
    need(by_event['wrapper_selected'].get('wrapper') == ('TimeoutPacketConn' if supported else 'TimerPacketConn'), 'Wrong actual wrapper')
    need(by_event['context_done'].get('error') == 'canceled'
         and by_event['context_done'].get('cause') == 'deadline_exceeded', 'Wrong context cancellation evidence')
    need(by_event['last_activity'].get('source_writes') == 1, 'Unexpected last-activity writes')
    started = by_event['last_activity'].get('started_elapsed_ns')
    since = by_event['context_done'].get('since_last_activity_ns')
    need(type(started) is int and 0 <= started <= by_event['last_activity']['elapsed_ns'],
         'Missing initial activity monotonic boundary')
    need(type(since) is int and 200_000_000 <= since <= 2_200_000_000,
         'Context observed before the bounded idle duration')
    need(by_event['read_enter'].get('context_done_at_entry') is False,
         'Read did not block before context cancellation')
    need(by_event['write_packet'].get('role') == 'source' and by_event['write_packet'].get('bytes') == 13,
         'Unexpected observed traffic')
    snapshot = by_event['observation_snapshot']
    expected = {'context_done': True, 'parent_context_done': False,
                'read_returns': int(supported), 'on_close_calls': int(supported),
                'source_closed': int(supported), 'sink_closed': int(supported),
                'source_writes': 1, 'sink_writes': 0, 'copy_bytes': 0, 'idle_gate_qualified': False}
    for key, value in expected.items():
        need(type(snapshot.get(key)) is type(value) and snapshot[key] == value, 'Wrong lifecycle snapshot: ' + key)
    need(by_event['last_activity']['seq'] < by_event['context_done']['seq'] < snapshot['seq']
         < by_event['cleanup_start']['seq'] < by_event['cleanup_joined']['seq'], 'Wrong observation boundary ordering')
    need(by_event['context_done']['elapsed_ns'] - started >= since,
         'Context event disagrees with the measured activity clock')
    if not supported:
        need(snapshot['elapsed_ns'] - by_event['context_done']['elapsed_ns'] >= 200_000_000,
             'Timer branch was not observed blocked after cancellation')
    boundary = snapshot['seq'] if supported else by_event['cleanup_start']['seq']
    error = 'deadline_exceeded' if supported else 'closed'
    for name in ('read_packet_return', 'copy_packet_return', 'harness_on_close'):
        row = by_event[name]
        need(row.get('error') == error, 'Unexpected copy/close cause')
        need(row['seq'] < boundary if supported else row['seq'] > boundary, 'Copy unblocked in wrong phase')
    need(by_event['read_enter']['seq'] < by_event['read_packet_return']['seq']
         < by_event['copy_packet_return']['seq'] < by_event['harness_on_close']['seq'],
         'Impossible read/copy/callback causal order')
    need(by_event['copy_packet_return'].get('bytes') == 0, 'Idle copy transferred data')
    closes = [x for x in events if x['event'] == 'close']
    need(Counter(x.get('role') for x in closes) == Counter(source=1, sink=1), 'Missing exact close identity')
    need(all(x['seq'] < boundary if supported else x['seq'] > boundary for x in closes), 'Close occurred in wrong phase')
    for row in (x for x in events if x['event'] == 'set_read_deadline'):
        need(row.get('role') == 'source' and row.get('error') == ('nil' if supported else 'invalid'), 'Wrong deadline support evidence')
    for key, value in {'source_closed': 1, 'sink_closed': 1, 'on_close_calls': 1, 'workers_started': True}.items():
        row = by_event['cleanup_joined']
        need(type(row.get(key)) is type(value) and row[key] == value, 'Unjoined or unclosed observation workers')
    return {'case': setup['case'], 'deadline_supported': supported, 'events': len(events),
            'context_done_ns': by_event['context_done']['elapsed_ns'],
            'snapshot_ns': snapshot['elapsed_ns'], 'cleanup_joined_ns': events[-1]['elapsed_ns'],
            'idle_gate_qualified': False,
            'classification': 'deadline_released_read' if supported else 'context_cancel_did_not_release_read'}


def verify(directory):
    summary = {'scope': 'Fixed dependency observation only; product idle qualification remains false', 'phases': {}}
    for phase in ('baseline', 'patched'):
        rows = [json.loads(line) for line in (directory / (phase + '-timeout-observations.jsonl')).read_text().splitlines()]
        need(rows and not any(x.get('Action') in ('skip', 'fail') for x in rows), 'Failed, empty or skipped lifecycle observation')
        need(all(x.get('Package') == 'github.com/sagernet/sing-vmess' for x in rows),
             'Observation used an unexpected Go package')
        terminal = [x for x in rows if x.get('Action') == 'pass' and x.get('Test')]
        need(Counter(x['Test'] for x in terminal) == Counter(CASES.keys()), 'Missing or extra observation tests')
        package = [x for x in rows if 'Test' not in x and x.get('Action') in ('pass', 'fail')]
        need(len(package) == 1 and package[0]['Action'] == 'pass', 'Missing package completion')
        need(json.loads((directory / (phase + '-timeout-observation-exit.json')).read_text()) == {'returncode': 0}, 'Nonzero lifecycle observation exit')
        events = {name: [] for name in CASES}
        for row in rows:
            for line in row.get('Output', '').splitlines():
                if PREFIX in line:
                    name = row.get('Test')
                    need(name in CASES, 'Event outside named observation')
                    events[name].append(json.loads(line.split(PREFIX, 1)[1]))
        summary['phases'][phase] = [verify_events(name, values) for name, values in events.items()]
    (directory / 'timeout-observation-verification.json').write_text(json.dumps(summary, indent=2) + '\n')
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    print(json.dumps(verify(parser.parse_args().directory), indent=2))
