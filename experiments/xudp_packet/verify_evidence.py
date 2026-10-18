"""Exact test-name coverage; zero tests/skips/extra tests never mean success."""
import json
from pathlib import Path


def need(value, message):
    if not value:
        raise ValueError(message)


def expected_terminals(leaves):
    result = set()
    for leaf in leaves:
        parts = leaf.split('/')
        result.update('/'.join(parts[:i]) for i in range(1, len(parts) + 1))
    return result


def verify_go(path, leaves, package):
    rows = [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
    need(rows, 'Empty Go log')
    names, package_passes = {}, 0
    for row in rows:
        need(row.get('Package') == package, 'Unexpected Go package')
        if row.get('Action') in ('pass', 'fail', 'skip'):
            if 'Test' in row:
                name = row['Test']
                need(name not in names, 'Duplicate terminal: ' + name)
                names[name] = row['Action']
            else:
                need(row['Action'] == 'pass', 'Package failed/skipped')
                package_passes += 1
    need(set(names) == expected_terminals(leaves), 'Missing or extra actual test terminals')
    need(all(value == 'pass' for value in names.values()), 'Failed or skipped test')
    need(package_passes == 1, 'Missing unique package PASS')
    return {'leaves': len(leaves), 'terminals': len(names)}


def verify_build(directory, contract):
    directory = Path(directory)
    need(contract['test_run_regex'] == '^TestVUICompletePacket', 'Changed test selection')
    required = contract['linux_required_leaf_tests']
    need(set(required) == {'github.com/sagernet/sing/' + p for p in
                          ('common/network', 'common/bufio', 'protocol/socks')}, 'Wrong packages')
    need(sum(len(v) for v in required.values()) == contract['linux_required_leaf_count'],
         'Wrong fixed leaf count')
    results = {}
    for package, leaves in required.items():
        need(len(leaves) == len(set(leaves)) and bool(leaves), 'Duplicate/empty test list')
        label = package.removeprefix('github.com/sagernet/sing/').replace('/', '-')
        for mode in ('race', 'debug'):
            name = label + '-' + mode
            exit_row = json.loads((directory / (name + '.exit.json')).read_text())
            need(exit_row['returncode'] == 0, 'Go command failed: ' + name)
            need(exit_row.get('environment') == {'GOOS':'linux','GOARCH':'amd64','CGO_ENABLED':'1','GOTOOLCHAIN':'local','GOWORK':'off'}, 'Incorrect native test environment')
            args = exit_row['args']
            need('-race' in args and (('-tags=debug' in args) == (mode == 'debug')),
                 'Missing race/debug flags')
            need(args[args.index('-run') + 1] == '^TestVUICompletePacket' and args[-1] == package,
                 'Changed command selection')
            results[name] = verify_go(directory / (name + '.stdout'), leaves, package)
        for goos in ('darwin', 'windows'):
            name = label + '-' + goos + '-compile'
            exit_row = json.loads((directory / (name + '.exit.json')).read_text())
            need(exit_row['returncode'] == 0 and '-c' in exit_row['args'] and
                 exit_row['args'][-1] == package, 'Cross compilation failed: ' + name)
            need(exit_row.get('environment') == {'GOOS':goos,'GOARCH':'amd64','CGO_ENABLED':'0','GOTOOLCHAIN':'local','GOWORK':'off'}, 'Incorrect cross-compile environment')
    provenance = json.loads((directory / 'provenance.json').read_text())
    expected_checks = {p.replace('github.com/sagernet/sing/','').replace('/','-')+'-'+mode
                       for p in required for mode in ('race','debug','darwin-compile','windows-compile')} | {'build','module-verify'}
    checks = provenance['checks']
    need(len(checks) == len(expected_checks) and {row['label'] for row in checks} == expected_checks,
         'Missing, duplicate or unexpected build check')
    for label in ('build','module-verify'):
        exit_row = json.loads((directory / (label + '.exit.json')).read_text())
        need(exit_row['returncode'] == 0 and exit_row.get('environment') ==
             {'GOOS':'linux','GOARCH':'amd64','CGO_ENABLED':'0' if label=='build' else '1','GOTOOLCHAIN':'local','GOWORK':'off'},
             'Missing/failed build or module verification')
        need(('build' in exit_row['args']) if label=='build' else exit_row['args'][-2:] == ['mod','verify'], 'Wrong build/verify command')
    need(provenance['product_qualified'] is False and
         all(row['returncode'] == 0 for row in provenance['checks']), 'Incomplete build checks')
    report = {'linux': results, 'cross_compile_only': ['darwin/amd64', 'windows/amd64'],
              'product_qualified': False}
    (directory / 'VERIFICATION.json').write_text(json.dumps(report, indent=2) + '\n')
    return report
