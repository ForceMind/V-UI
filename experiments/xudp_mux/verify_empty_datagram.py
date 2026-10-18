"""Strict supplement for new isolated mux empty-datagram Go evidence.

This verifier requires every named top/leaf, exact scoped event ordering, real
packet delivery assertions, no-data/TCP controls, recovery, End and joins. Its
synthetic unit fixtures are verifier tests only, never runtime evidence.
"""
import argparse
import json
from pathlib import Path

CIPHERS = {
    'TestMuxEmptyDatagramRaw': 'raw',
    'TestMuxEmptyDatagramAES': 'aes-128-gcm',
    'TestMuxEmptyDatagramChaCha': 'chacha20-poly1305',
}
LEAVES = (
    'StatusNewReadFrom', 'StatusNewReadPacket',
    'StatusKeepReadFrom', 'StatusKeepReadPacket',
    'NoDataNewAndKeep', 'XUDPWriteTo', 'UDPOutputEntrypoints',
    'TCPEmptyWriteNoop',
)
EVENTS = ('transport_ready', 'case_verified', 'ends_drained', 'parent_close', 'parent_joined')
PREFIX = 'XUDP_MUX_EMPTY '
SCOPE = 'isolated-mux-empty-datagram'
PACKAGE = 'github.com/sagernet/sing-vmess'


def need(condition, message):
    if not condition:
        raise ValueError(message)


def exact(event, key, expected):
    actual = event.get(key)
    need(type(actual) is type(expected) and actual == expected,
         f"Invalid {event.get('event')} {key}: {actual!r}, expected {expected!r}")


def case_expectations(leaf):
    if leaf.startswith(('StatusNew', 'StatusKeep')):
        new = leaf.startswith('StatusNew')
        api = 'ReadFrom' if leaf.endswith('ReadFrom') else 'ReadPacket'
        return dict(read_api=api, status_new=new, empty_uploads=1,
                    empty_replies=1, short_roundtrips=1, tuple_payload_exact=True,
                    option_data_observed=True, pipe_and_address_completed=True,
                    fallback_destination=not new and api == 'ReadFrom')
    if leaf == 'NoDataNewAndKeep':
        return dict(no_data_frames=2, short_roundtrips=2,
                    first_received_is_short=True, tuple_payload_exact=True,
                    no_spurious_empty=True)
    if leaf == 'XUDPWriteTo':
        return dict(real_xudp=True, request_frames=3, new_empty_frames=1,
                    keep_empty_frames=1, empty_uploads=2, empty_replies=2,
                    short_roundtrips=1, nil_and_empty_slices=True,
                    tuple_payload_exact=True, option_data_observed=True,
                    pipe_and_address_completed=True)
    if leaf == 'UDPOutputEntrypoints':
        return dict(entrypoints=['Write', 'WriteTo', 'WritePacket'],
                    empty_replies=3, short_roundtrips=1,
                    tuple_payload_exact=True, option_data_observed=True,
                    exactly_one_frame_per_call=True)
    if leaf == 'TCPEmptyWriteNoop':
        return dict(empty_input_frames=2, empty_output_calls=3,
                    empty_output_frames=0, spurious_zero_reads=0,
                    short_roundtrips=1,
                    entrypoints=['Write(nil)', 'Write(empty)', 'WriteBuffer(empty)'],
                    tuple_payload_exact=True)
    raise ValueError('Unknown leaf: ' + leaf)


def verify_empty_datagram(path):
    rows = [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
    need(rows and all(isinstance(row, dict) for row in rows), 'Empty/invalid mux empty-datagram evidence')
    need(all(row.get('Package') == PACKAGE for row in rows), 'Wrong/missing Go evidence package')
    leaves = {top + '/' + leaf for top in CIPHERS for leaf in LEAVES}
    expected = set(CIPHERS) | leaves
    terminal = {}
    events = {name: [] for name in leaves}
    package_terminal = []
    pending_output = {}
    for row in rows:
        action, name = row.get('Action'), row.get('Test')
        if action in ('pass', 'skip', 'fail'):
            need(not pending_output.get((PACKAGE, name)), 'Unterminated output before terminal')
            if name:
                need(name in expected, 'Unexpected test terminal: ' + name)
                need(name not in terminal, 'Duplicate test terminal: ' + name)
                terminal[name] = action
            else:
                package_terminal.append(action)
        output = row.get('Output', '')
        need(isinstance(output, str), 'Non-string Go output')
        if output:
            need(action == 'output', 'Evidence event is not output')
            source = (row['Package'], name)
            pending_output[source] = pending_output.get(source, '') + output
            while '\n' in pending_output[source]:
                line, pending_output[source] = pending_output[source].split('\n', 1)
                if PREFIX not in line:
                    continue
                data = json.loads(line.split(PREFIX, 1)[1])
                need(isinstance(data, dict), 'Invalid evidence event')
                need(name in events and data.get('case') == name, 'Wrong evidence leaf/source')
                need(name not in terminal, 'Event after terminal: ' + name)
                events[name].append(data)
    need(not any(pending_output.values()), 'Unterminated split evidence output')
    need(set(terminal) == expected, 'Missing mux empty-datagram top/leaf test')
    need(all(value == 'pass' for value in terminal.values()), 'Failed/skipped mux empty-datagram test')
    need(package_terminal == ['pass'], 'Package did not uniquely pass')
    for name, evidence in events.items():
        top, leaf = name.split('/')
        need([event.get('event') for event in evidence] == list(EVENTS), 'Missing/duplicate/out-of-order event: ' + name)
        for seq, event in enumerate(evidence, 1):
            exact(event, 'seq', seq)
            exact(event, 'cipher', CIPHERS[top])
            exact(event, 'scope', SCOPE)
            exact(event, 'product_qualified', False)
        ready, observed, drained, close, joined = evidence
        encrypted = CIPHERS[top] != 'raw'
        for key, expected_value in dict(actual_mux=True, command_mux=encrypted,
                                        real_client_service=encrypted,
                                        global_padding=encrypted,
                                        authenticated_length=encrypted).items():
            exact(ready, key, expected_value)
        for key, expected_value in case_expectations(leaf).items():
            exact(observed, key, expected_value)
        for key, expected_value in dict(end_count=1, close_calls=2, each_end_once=True,
                                        parent_alive=True, streams=0, queued=0,
                                        active_transport_writes=0).items():
            exact(drained, key, expected_value)
        exact(close, 'real_peer_close', True)
        exact(close, 'outer_context_alive', True)
        exact(joined, 'service_joined', encrypted)
        for key in ('mux_handler_joined', 'writer_joined', 'watcher_joined',
                    'all_test_workers_joined', 'no_extra_decoded_bytes',
                    'outer_context_alive'):
            exact(joined, key, True)
        for key in ('handler_callbacks_finished', 'handler_callbacks_expected'):
            exact(joined, key, 1)
        for key in ('active_callers', 'active_transport_writes', 'retained_streams',
                    'retained_queue', 'handler_callbacks_active'):
            exact(joined, key, 0)
        error = joined.get('service_error')
        need(isinstance(error, str) and error not in ('', '<nil>') and
             not any(word in error.lower() for word in ('timeout', 'deadline')),
             'Missing real peer-close parent error')
    return {'top_level_cases': len(CIPHERS), 'leaf_cases': len(leaves),
            'events': len(leaves) * len(EVENTS), 'scope': SCOPE,
            'product_qualified': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('evidence', type=Path)
    args = parser.parse_args()
    print(json.dumps(verify_empty_datagram(args.evidence), sort_keys=True))


if __name__ == '__main__':
    main()
