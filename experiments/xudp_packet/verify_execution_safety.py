"""Resource/provenance-only continuation gate. This never grants capacity PASS."""
from collections import Counter
from pathlib import Path
import packet_boundary as p
import build_and_test as b


def validate_live_input(expected_input):
    # Re-read committed source bytes and both actual executables after each
    # stage. Historical launch records alone cannot detect later replacement.
    current = p.source_identity(b.ROOT, b.c)
    p.validate_manifest(expected_input, current, b.ROOT, execute=False)


def verify(directory, stage, execution, expected_input, expected_manifest_sha256):
    directory = Path(directory)
    p.need(execution.get('timeout') is False and type(execution.get('returncode')) is int and
           execution['returncode'] in (0, 1) and execution.get('cleanup') == {
               'signals': [], 'leader_joined': True, 'process_group_gone': True},
           'Unknown termination, timeout, signal or surviving process group')
    validate_live_input(expected_input)
    cases = p.plan(stage)
    p.need(p.json_read(directory/'plan.json') == cases, 'Continuation plan mismatch')
    manifest = p.json_read(directory/'manifest.json')
    p.need(manifest.get('schema_version') == 1 and manifest.get('kind') == p.KIND+'-evidence',
           'Unknown stage evidence manifest')
    members = manifest['files']
    p.need(isinstance(members, dict) and members, 'Missing stage evidence members')
    actual = set()
    for path in directory.rglob('*'):
        p.need(not path.is_symlink(), 'Linked continuation evidence')
        if path.is_file(): actual.add(str(path.relative_to(directory)))
    p.need(actual == set(members) | {'manifest.json'} and
           set(members) <= p.expected_artifacts(cases), 'Stage evidence set mismatch')
    required = {'result.json', 'plan.json', 'input-identity.json', 'process-binaries.json', 'suite.log'}
    for case in cases:
        required.update({case['id']+'/result.json', case['id']+'/packets.json',
                         'frozen-fixture/'+case['id']+'.json', 'frozen-fixture/'+case['id']+'.log'})
        if case['mode'] in ('close', 'idle'):
            required.add(case['id']+'/lifecycle-observation.json')
            required.add(case['id']+'/socks-association-observation.json')
    p.need(required <= set(members), 'Missing raw case/fixture evidence')
    for name, digest in members.items():
        p.need(p.digest(digest) and p.sha(directory/name) == digest, 'Stage evidence hash mismatch')
    identity = p.json_read(directory/'input-identity.json')
    p.need(identity['manifest_sha256'] == expected_manifest_sha256 and
           identity['source_identity'] == expected_input['source_identity'] and
           identity['official'] == expected_input['official'] and
           identity['candidate'] == expected_input['candidate'], 'Stage HEAD or role binary identity differs')
    provenance = expected_input['candidate']['provenance_path']
    p.need(p.sha(provenance) == expected_input['candidate']['provenance_sha256'] and
           identity['build_provenance'] == p.json_read(provenance), 'Stage build identity differs')
    report = p.json_read(directory/'result.json')
    p.need(report.get('stage') == stage and report.get('kind') == p.KIND and
           report.get('product_qualified') is False and report.get('original_suite_passed') is False,
           'Stage result identity or qualification changed')
    results = report['cases']
    p.verify_leaf_set([case['id'] for case in cases], [row['case'] for row in results], 'safe case')
    processes = p.json_read(directory/'process-binaries.json')
    expected_roles = Counter((case['id'], role) for case in cases for role in case['roles'])
    p.need(Counter((row['case'], row['role']) for row in processes) == expected_roles,
           'Missing or duplicate launched role')
    by_case = {case['id']: case for case in cases}
    for row in processes:
        origin = by_case[row['case']]['roles'][row['role']]
        binary = expected_input[origin]
        p.need(row['origin'] == origin and row['binary'] == binary['path'] and
               row['sha256'] == binary['sha256'] and row['version'] == binary['version'] and
               row['classification'] == ('official' if origin == 'official' else 'experimental') and
               row['parser_passed'] is True and p.digest(row['config_sha256']), 'Role provenance incomplete')
        launches = row['launches']
        p.need(len(launches) == 1 and launches[0]['ready'] is True and
               type(launches[0]['pid']) is int and launches[0]['pid'] > 0 and
               launches[0]['sha256'] == binary['sha256'], 'Unknown startup or launch identity')
    for result in results:
        case = by_case[result['case']]
        p.need(result == p.json_read(directory/case['id']/'result.json') and
               result['packets'] == p.json_read(directory/case['id']/'packets.json'), 'Aggregate/raw case differ')
        p.need(result.get('status') in ('observed', 'error') and result.get('roles') == case['roles'] and
               result.get('stage') == stage and result.get('protocol') == case['protocol'], 'Unfinished case')
        owned = [row for row in processes if row['case'] == case['id']]
        p.verify_cleanup(result, owned)  # exact launch/live/birth/reaped PID correspondence + frozen traps
        p.need(all(row['returncode'] == 0 for row in result['cleanup']['processes']),
               'A core crashed or exited by signal/nonzero code')
        p.need(not result['dns']['ordinary'] and not result['dns']['business'] and not result['dns']['expected'],
               'Unexpected DNS activity during failed stage')
        specs = {case['id']+'/'+spec['id']: spec for spec in case['packets']}
        packet_rows = result['packets']
        seen = [row['leaf'] for row in packet_rows]
        ordered = list(specs)
        p.need(seen and seen == ordered[:len(seen)] and
               result.get('unexecuted_leaves') == ordered[len(seen):] and
               all(row.get('status') in ('error', 'full_delivery_passed', 'capacity_failed_exact_prefix',
                                        'local_emsgsize_verified') for row in packet_rows),
               'Unstarted, duplicate or unterminated packet evidence')
        p.need((result['status'] == 'error' and bool(result.get('errors'))) or
               (result['status'] == 'observed' and seen == ordered and not result.get('errors')),
               'Missing case failure or unexecuted-leaf accounting')
        for row in packet_rows:
            if row['status'] == 'error':
                p.need(isinstance(row.get('error'), dict) and row['error'].get('type') and row['error'].get('message'),
                       'Packet error terminal has no actual cause')
        if case['mode'] in ('close', 'idle'):
            lifecycle = p.json_read(directory/case['id']/'lifecycle-observation.json')
            p.need(lifecycle.get('status') in ('observed', 'error') and
                   (lifecycle['status'] != 'error' or bool(lifecycle.get('error'))),
                   'Lifecycle observation lacks a terminal result')
        ledger = result['target_ledger']
        p.need(ledger['closed'] is True and ledger['thread_joined'] is True and ledger['socket_closed'] is True and
               not ledger['errors'] and ledger['host'] == '127.0.0.3' and type(ledger['port']) is int and
               0 < ledger['port'] <= 65535, 'Target cleanup/source errors')
        fixture = p.json_read(directory/'frozen-fixture'/(case['id']+'.json'))
        p.need(fixture.get('status') in ('passed', 'failed') and fixture.get('deliveries') == [
                   {'host': '127.0.0.3', 'count': 0},
                   {'host': '127.0.0.3', 'count': ledger['received_count']}],
               'Raw fixture lifetime deliveries disagree')
        target_rows = ledger['packets']
        leaves = [row['leaf'] for row in target_rows]
        p.need(len(leaves) == len(set(leaves)) and set(leaves) <= set(seen) and
               ledger['received_count'] == len(target_rows) == len(ledger['actual_datagrams']) and
               type(ledger['planned_count']) is int and len(target_rows) <= ledger['planned_count'] <= len(seen),
               'Unaccounted target packet or unfinished recorder')
        for event, target in zip(ledger['actual_datagrams'], target_rows):
            request, expected_reply = p.packet_bodies(target['leaf'], specs[target['leaf']])
            size = target['request_actual']['bytes']
            p.need(type(size) is int and 0 <= size <= len(request) and
                   target['request_expected'] == p.ident(request) and target['request_actual'] == p.ident(request[:size]) and
                   target['request_full'] is (size == len(request)) and target['request_exact_prefix'] is True,
                   'Target bytes changed outside capacity observation')
            sent = expected_reply if specs[target['leaf']]['direction'] in ('downlink', 'uplink_ack') else request[:size]
            p.need(target['reply_sent'] == p.ident(sent) and target['sendto_return'] == len(sent),
                   'Target did not finish its declared UDP send')
            p.need(target['target'] == [ledger['host'], ledger['port']] and
                   len(target['source']) == 2 and target['source'][0] == '127.0.0.4' and
                   type(target['source'][1]) is int and 0 < target['source'][1] <= 65535 and
                   event['target'] == target['target'] and event['source'] == target['source'] and
                   event['payload'] == target['request_actual'] and event['flags'] == 0 and event['ancillary_count'] == 0,
                   'Unaccounted target receive, truncation, or DIRECT traffic')
    return {'stage_execution_safe': True, 'cases': len(cases), 'roles': len(processes),
            'capacity_evaluated': False, 'scope': 'resource_cleanup_and_identity_only'}
