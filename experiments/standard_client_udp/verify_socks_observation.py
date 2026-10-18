"""Independent D-only diagnostic verifier. Never promotes the original D gate."""
from collections import Counter
import errno
import math
from pathlib import Path

import packet_boundary as p
from socks_observation import FILE, KIND, ROLE


def moment(value):
    p.need(type(value) in (int, float) and math.isfinite(value) and value >= 0, 'Invalid passive sample time')
    return value


def endpoint(value, host='127.0.0.1'):
    p.need(isinstance(value, list) and len(value) == 2 and value[0] == host and
           type(value[1]) is int and 0 < value[1] <= 65535, 'Wrong passive endpoint tuple')
    return {'host': value[0], 'port': value[1]}


def descriptor(value, *, closed=False):
    p.need(set(value) == {'fd', 'inode', 'local', 'peer'}, 'Wrong local descriptor fields')
    if closed:
        p.need(value == {'fd': -1, 'inode': None, 'local': None, 'peer': None}, 'Local socket not closed')
    else:
        p.need(type(value['fd']) is int and value['fd'] >= 0 and
               type(value['inode']) is int and value['inode'] > 0, 'Missing real local fd/inode')
        endpoint(value['local'])
        if value['peer'] is not None:
            endpoint(value['peer'])


def peek_state(value, fd):
    p.need(set(value) == {'fd', 'state', 'count', 'errno'} and value['fd'] == fd, 'PEEK descriptor mismatch')
    state, count, error = value['state'], value['count'], value['errno']
    valid = ((state == 'local_closed' and fd == -1 and count is None and error is None) or
             (fd >= 0 and state == 'eof' and type(count) is int and count == 0 and error is None) or
             (fd >= 0 and state == 'data' and type(count) is int and count == 1 and error is None) or
             (fd >= 0 and type(count) is int and count == -1 and type(error) is int and error > 0 and
              state == ('would_block' if error in (errno.EAGAIN, errno.EWOULDBLOCK) else 'error')))
    p.need(valid, 'Impossible or relabelled PEEK result')
    return state


def device_rows(sample, role):
    d = sample['device']
    launch = role['launches'][0]
    p.need(d['role'] == ROLE and type(d['pid']) is int and d['pid'] == launch['pid'] and
           moment(d['birth_time']) == d['birth_time_after'] == launch['birth_time'], 'Wrong sampled device PID/birth/role')
    p.need(moment(sample['started']) <= moment(d['started']) <= moment(d['completed']) <= moment(sample['completed']),
           'Device sample escaped timing envelope')
    p.need(d['executable'] == d['executable_after'] and set(d['executable']) ==
           {'device', 'inode', 'size', 'mtime_ns', 'ctime_ns'} and
           all(type(v) is int and v >= 0 for v in d['executable'].values()) and
           d['executable']['inode'] > 0 and d['executable']['size'] > 0, 'Device executable identity changed')
    maps = []
    for key in ('fd_before', 'fd_after'):
        rows = d[key]
        p.need(all(set(v) == {'fd', 'inode'} and type(v['fd']) is int and v['fd'] >= 0 and
                   type(v['inode']) is int and v['inode'] > 0 for v in rows) and
               len({v['fd'] for v in rows}) == len(rows) and len({v['inode'] for v in rows}) == len(rows),
               'Duplicate or invalid sampled device fd/inode')
        maps.append({v['fd']: v['inode'] for v in rows})
    races = d['races']
    p.need(isinstance(races, list), 'Malformed device race list')
    changed = set()
    for race in races:
        p.need(isinstance(race, dict), 'Malformed device race row')
        kind = race.get('kind')
        if kind == 'fd_changed_during_snapshot':
            p.need(set(race) == {'kind', 'fd', 'before_inode', 'after_inode'} and
                   type(race['fd']) is int and race['fd'] >= 0 and
                   race['before_inode'] == maps[0].get(race['fd']) and
                   race['after_inode'] == maps[1].get(race['fd']) and
                   race['before_inode'] != race['after_inode'], 'Forged fd-change race')
            p.need(race['fd'] not in changed, 'Duplicate fd-change race')
            changed.add(race['fd'])
        elif kind in ('fd_removed_during_readlink', 'connection_unresolved_during_snapshot'):
            p.need(set(race) == {'kind', 'fd'} and type(race['fd']) is int and
                   race['fd'] >= (-1 if kind == 'connection_unresolved_during_snapshot' else 0),
                   'Malformed unresolved descriptor race')
        else:
            p.need(race == {'kind': 'stable_proc_socket_omitted_by_psutil'},
                   'Unknown race cannot conceal a read/identity failure')
    expected_changed = {fd for fd in maps[0].keys() | maps[1].keys() if maps[0].get(fd) != maps[1].get(fd)}
    p.need(changed == expected_changed, 'Changed fd set lacks exact explicit race evidence')
    rows = d['sockets']
    p.need(len({r['inode'] for r in rows}) == len(rows) and len({r['fd'] for r in rows}) == len(rows),
           'Duplicate device socket identity')
    for r in rows:
        p.need(maps[0].get(r['fd']) == maps[1].get(r['fd']) == r['inode'] and r['protocol'] in ('tcp', 'udp') and
               r['family'] in ('ipv4', 'ipv6'), 'Device socket is not owned by sampled fd')
    return rows, set(maps[0].values()), not races


def verify_case(data, case, result, roles, source, lifecycle):
    p.need(data.get('schema_version') == 1 and type(data['schema_version']) is int and
           data.get('kind') == KIND and data.get('case') == case['id'] and data.get('mode') == case['mode'] and
           data.get('status') in ('complete', 'complete_with_inconclusive_samples') and data.get('errors') == [],
           'Incomplete passive SOCKS observation')
    p.need(data.get('source_identity') == source and data.get('product_qualified') is False and
           data.get('capacity_evaluated') is False, 'Passive source identity or qualification mismatch')
    owned = [r for r in roles if r['case'] == case['id'] and r['role'] == ROLE]
    p.need(len(owned) == 1 and data['device_role'] == owned[0], 'Passive device role not exact launch record')
    role = owned[0]
    p.need(role['origin'] == 'mihomo' and role['classification'] == 'official' and
           role['parser_passed'] is True and p.digest(role['sha256']) and
           len(role['launches']) == 1 and role['launches'][0]['ready'] is True and
           role['launches'][0]['sha256'] == role['sha256'], 'Device is not the declared stock Mihomo')
    launch = role['launches'][0]
    p.need([r for r in result['process_identity'] if r['pid'] == launch['pid']] ==
           [{'pid': launch['pid'], 'birth_time': launch['birth_time']}], 'Case live process identity mismatch')
    p.need(any(r['pid'] == launch['pid'] and r['reaped'] is True and r['returncode'] == 0
               for r in result['cleanup']['processes']), 'Device not cleanly joined')
    p.need(result['cleanup']['threads_joined'] is True and result['cleanup']['application_sockets_closed'] is True,
           'Application cleanup not verified')
    labels = ['initial', 'recovery'] if case['mode'] == 'close' else ['initial']
    initial = data['associations']
    p.need([a['label'] for a in initial] == labels, 'SOCKS association count/identity changed')
    for a in initial:
        descriptor(a['control']); descriptor(a['application'])
        p.need(a['control']['peer'] is not None and a['application']['peer'] is None and
               a['control']['inode'] != a['application']['inode'], 'Wrong initial SOCKS socket roles')
        endpoint(a['relay']); moment(a['created'])
    packets = result['packets']
    p.need(len(packets) == 2 and [r['leaf'] for r in packets] == p.leaf_ids([case]), 'Original D packet leaves changed')
    for index, packet in enumerate(packets):
        a = initial[index if case['mode'] == 'close' else 0]
        p.need(packet['application_socket'] == a['application']['local'] and packet['socks_relay'] == a['relay'] and
               packet['first_udp'] is (index == 0 or case['mode'] == 'close') and packet['send_state'] == 'sent',
               'Packet does not use the original planned association')
    p.need(packets[0]['full_delivery_passed'] is True, 'No initial complete large packet')
    last, deadline = moment(data['last_business_reply']), moment(data['deadline'])
    p.need(last == packets[0]['reply_monotonic'] and deadline == last + 6, 'Original six-second deadline changed')
    samples = data['samples']
    phases = [s['phase'] for s in samples]
    end_phase = 'after_recover' if packets[1]['full_delivery_passed'] else 'failure'
    required = ['after_large_business'] + (['after_explicit_close'] if case['mode'] == 'close' else [])
    n = phases.count('release_window')
    p.need(n > 0 and phases == required + ['release_window'] * n + ['window_complete', 'before_recover', end_phase, 'after_local_cleanup'],
           'Missing/reordered passive observation phase')
    p.need(moment(samples[0]['started']) >= last and moment(initial[0]['created']) <= samples[0]['started'],
           'Warm observation precedes initial business')
    if case['mode'] == 'close':
        close = data['explicit_close']
        p.need(samples[0]['completed'] <= moment(close['started']) <= moment(close['completed']) <= samples[1]['started'],
               'Explicit application close timing changed')
    else:
        p.need('explicit_close' not in data, 'Idle case explicitly closed the original association')
    hop_events = [e for e in lifecycle['events'] if e['kind'] == 'socket_snapshot' and
                  e['phase'] in ('after_large_business', 'large_then_' + case['mode'],
                                 'large_then_' + case['mode'] + '_window_complete')]
    references = [{'started': e['snapshot']['monotonic'], 'completed': e['snapshot']['sample_completed_monotonic']} for e in hop_events]
    p.need([s['hop_sample'] for s in samples if 'hop_sample' in s] == references,
           'Passive observation omitted or invented an original release sample')
    control_tuple, relay_tuple = initial[0]['control'], endpoint(initial[0]['relay'])
    warm_rows, _, warm_stable = device_rows(samples[0], role)
    p.need(warm_stable, 'Warm device identity sample must be stable')
    controls = [r for r in warm_rows if r['protocol'] == 'tcp' and r['family'] == 'ipv4' and
                r['local'] == endpoint(control_tuple['peer']) and r['remote'] == endpoint(control_tuple['local'])]
    relays = [r for r in warm_rows if r['protocol'] == 'udp' and r['family'] == 'ipv4' and r['local'] == relay_tuple]
    p.need(len(controls) == len(relays) == 1 and controls[0]['proc_state'] == '01' and
           relays[0]['remote'] == {'host': '0.0.0.0', 'port': 0}, 'Warm device control/relay ownership not exact')
    keys = {controls[0]['inode'], relays[0]['inode']}
    p.need(len(keys) == 2, 'Control and relay share an inode')
    previous, first_eof, first_absent, inconclusive = None, None, None, 0
    before = None
    for s in samples:
        p.need('error' not in s and (previous is None or moment(s['started']) >= previous), 'Passive sample error/overlap')
        previous = moment(s['completed'])
        if 'hop_sample' in s:
            p.need(s['hop_sample']['started'] <= s['hop_sample']['completed'] <= s['started'], 'Passive sample before corresponding hop snapshot')
        rows, inodes, stable = device_rows(s, role)
        inconclusive += int(not stable)
        p.need(s['device']['executable'] == samples[0]['device']['executable'], 'Device executable changed between samples')
        if s['phase'] in ('after_large_business', 'after_local_cleanup'):
            p.need(s.get('executable_sha256') == role['sha256'], 'Sampled actual device executable hash mismatch')
        expected_labels = labels if s['phase'] in ('before_recover', end_phase, 'after_local_cleanup') else ['initial']
        p.need([a['label'] for a in s['associations']] == expected_labels, 'Unexpected sampled association')
        for a in s['associations']:
            identity = initial[labels.index(a['label'])]
            closed = s['phase'] == 'after_local_cleanup' or (a['label'] == 'initial' and case['mode'] == 'close' and s['phase'] != 'after_large_business')
            descriptor(a['control_before'], closed=closed); descriptor(a['application'], closed=closed)
            p.need(a['control_before'] == a['control_after'] and a['relay'] == identity['relay'], 'Control identity changed around PEEK')
            if not closed:
                p.need(a['control_before'] == identity['control'] and a['application'] == identity['application'], 'Local association descriptor/tuple changed')
            state = peek_state(a['peek'], a['control_before']['fd'])
            if a['label'] == 'initial' and state == 'eof':
                first_eof = first_eof or s['completed']
        # Only a stable sample can establish an absence at that instant. An
        # intermediate race remains raw/inconclusive; it is never erased or
        # converted to a claim of whole-window continuous device absence.
        if stable and not (keys & inodes):
            first_absent = first_absent or s['completed']
        if s['phase'] == 'before_recover':
            before = s
    window = next(s for s in samples if s['phase'] == 'window_complete')
    p.need(window['hop_sample']['started'] >= deadline and window['completed'] <= before['started'] and
           before['completed'] <= packets[1]['target_window']['started'], 'Recovery precedes unchanged release window/observation')
    terminal = packets[1].get('terminal_monotonic', packets[1].get('reply_monotonic'))
    p.need(samples[-2]['started'] >= moment(terminal), 'Terminal observation precedes actual recovery result')
    p.need(data['cleanup'] == [{'label': label, 'control_fd': -1, 'application_fd': -1} for label in labels],
           'Original control or application socket survived cleanup')
    before_state = before['associations'][0]['peek']['state']
    _, before_inodes, before_stable = device_rows(before, role)
    p.need(before_stable, 'Before-recovery device sample must be stable')
    p.need(data['status'] == ('complete_with_inconclusive_samples' if inconclusive else 'complete'),
           'Raw status hides or invents inconclusive device samples')
    ended = before_state == 'eof' and not keys & before_inodes
    # Data queued ahead of FIN hides EOF; EAGAIN means only that EOF was not
    # observed at this instant. A syscall error is not EOF. Never promote either.
    state = ('peer_eof_and_device_control_relay_absent' if ended else
             'explicit_application_close' if case['mode'] == 'close' else 'no_joint_peer_eof_and_device_absence')
    return {'case': case['id'], 'observations_verified': True, 'association_state_before_recovery': state,
            'peek_before_recovery': before_state, 'initial_control_inode': controls[0]['inode'],
            'initial_relay_inode': relays[0]['inode'], 'first_eof_completed': first_eof,
            'first_verified_device_absence_completed': first_absent,
            'inconclusive_device_samples': inconclusive, 'device_whole_window_continuity_proven': False,
            'business_recovery_passed': packets[1]['full_delivery_passed'] is True,
            'capacity_evaluated': False, 'product_qualified': False}

