"""Manual hosted-runner experiment; never an installer or production repair.

Two isolated systemd variants differ only in AF_NETLINK. The fake direct VLESS
TLS inlet is narrowed to loopback; its direct outbound and route come literally
from SingBoxAdapter, including an UNSET auto_detect_interface when unset there.
No target requests, DNS, external upstream, capabilities or host rules are used.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import signal
import socket
import subprocess
import tempfile
import time

from fetch_test_cores import PINS
from service_support import service_files

ROOT = Path(__file__).resolve().parents[1]
FAKE_UUID = '11111111-1111-4111-8111-111111111111'
SANDBOX_KEYS = ('UMask', 'NoNewPrivileges', 'ProtectSystem', 'ProtectHome',
                'PrivateTmp', 'PrivateDevices', 'CapabilityBoundingSet',
                'RestrictSUIDSGID', 'RestrictAddressFamilies')
STATE_KEYS = ('LoadState', 'ActiveState', 'SubState', 'Result', 'MainPID',
              'ExecMainCode', 'ExecMainStatus', 'User', 'Group',
              'AmbientCapabilities', *SANDBOX_KEYS)


def run(command, *, timeout=20, check=True):
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout,
                            env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8'})
    if check and result.returncode:
        raise RuntimeError('Command failed: ' + command[0] + ' (exit ' + str(result.returncode) + ')')
    return result


def guard():
    if (os.environ.get('GITHUB_ACTIONS') != 'true' or os.uname().sysname != 'Linux'
            or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted'
            or os.environ.get('GITHUB_REPOSITORY') != 'ForceMind/V-UI'
            or not re.fullmatch(r'[0-9]+', os.environ.get('GITHUB_RUN_ID', ''))
            or os.geteuid() != 0 or not Path('/run/systemd/system').is_dir()
            or Path('/proc/1/comm').read_text().strip() != 'systemd'):
        raise RuntimeError('Only a root orchestrator on an explicitly opted-in hosted Linux systemd runner is allowed')
    for name in ('/etc/v-ui/service.json', '/var/lib/v-ui/CURRENT.json',
                 '/var/lib/v-ui/CURRENT', '/usr/local/lib/v-ui/ops-control.json'):
        if os.path.lexists(name):
            raise RuntimeError('Refusing a runner containing an existing V-UI installation')
    # Never borrow a production service, even if its metadata is missing.
    absent = run(['systemctl', 'show', 'v-ui.service', '--property=LoadState', '--value'], check=False)
    if absent.stdout.strip() != 'not-found':
        raise RuntimeError('Refusing a runner with a pre-existing v-ui.service')


def canonical_sandbox():
    panel = next(text for path, (text, _) in service_files({
        'service_manager': 'systemd', 'bootstrap_python': '/usr/bin/python3',
        'ipv6': False}).items() if path.name == 'v-ui.service')
    values = dict(line.split('=', 1) for line in panel.splitlines() if '=' in line)
    selected = {key: values[key] for key in SANDBOX_KEYS}
    if (set(selected['RestrictAddressFamilies'].split()) != {'AF_INET', 'AF_INET6', 'AF_UNIX'}
            or selected['CapabilityBoundingSet'] != '' or selected['NoNewPrivileges'] != 'true'):
        raise RuntimeError('Canonical sandbox changed; this experiment must be reviewed again')
    return selected


def adapter_basis():
    """Read literal route/outbounds without importing the app or opening its DB."""
    tree = ast.parse((ROOT / 'app/services/core_manager.py').read_text())
    adapter = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'SingBoxAdapter')
    method = next(node for node in adapter.body if isinstance(node, ast.FunctionDef) and node.name == 'build_config')
    returned = next(node.value for node in reversed(method.body) if isinstance(node, ast.Return))
    values = {ast.literal_eval(key): value for key, value in zip(returned.keys, returned.values)}
    return {key: ast.literal_eval(values[key]) for key in ('route', 'outbounds')}


def show(unit):
    result = run(['systemctl', 'show', unit, '--all', '--property=' + ','.join(STATE_KEYS)], check=False)
    values = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    if result.returncode and values.get('LoadState') != 'not-found':
        raise RuntimeError('Cannot read owned probe unit state')
    return values


def journal(unit):
    raw = run(['journalctl', '--unit=' + unit, '--no-pager', '--output=cat', '--lines=100']).stdout
    # All inputs are invented, but retain no UUID or PEM material in evidence.
    raw = raw.replace(FAKE_UUID, '[fake-uuid]')
    raw = re.sub(r'-----BEGIN .*?-----.*?-----END .*?-----', '[fake-pem-redacted]', raw, flags=re.S)
    return raw[-24000:]


def netlink_reason(log):
    return [line for line in log.splitlines()
            if re.search(r'netlink|route monitor|network monitor', line, re.I)
            and re.search(r'not permitted|permission denied|address family not supported|protocol not supported', line, re.I)]


def listener_owned(pid, port, uid, expected_core):
    if pid <= 0:
        return False
    try:
        # Type=simple may expose systemd-executor's PID before exec/drop-UID.
        # It is not a core sample until the kernel reports our verified binary.
        if Path(os.readlink('/proc/' + str(pid) + '/exe')) != expected_core:
            return False
        status = Path('/proc/' + str(pid) + '/status').read_text()
        identity = next(line for line in status.splitlines() if line.startswith('Uid:'))
        if any(int(value) != uid for value in identity.split()[1:]):
            raise RuntimeError('Probe main process did not retain the requested non-root identity')
        descriptors = Path('/proc/' + str(pid) + '/fd')
        sockets = {os.readlink(path) for path in descriptors.iterdir()}
        for line in Path('/proc/net/tcp').read_text().splitlines()[1:]:
            fields = line.split()
            if (fields[1] == '0100007F:' + format(port, '04X') and fields[3] == '0A'
                    and 'socket:[' + fields[9] + ']' in sockets):
                return True
    except (FileNotFoundError, ProcessLookupError):
        return False
    return False


def validate_properties(actual, expected, user, group):
    if actual.get('User') != user or actual.get('Group') != group:
        raise RuntimeError('systemd did not retain the requested non-root identity')
    if actual.get('CapabilityBoundingSet') or actual.get('AmbientCapabilities'):
        raise RuntimeError('Probe must have no bounding or ambient capabilities')
    for key, value in expected.items():
        observed = actual.get(key)
        if key == 'RestrictAddressFamilies':
            matches = set((observed or '').split()) == set(value.split())
        elif value == 'true':
            matches = observed == 'yes'
        else:
            matches = observed == value
        if not matches:
            raise RuntimeError('Effective systemd sandbox differs: ' + key)


def start_variant(unit, phase, properties, work, core, config, account):
    options = {**properties, 'User': account.pw_name, 'Group': str(account.pw_gid),
               'WorkingDirectory': str(work), 'ReadWritePaths': str(work),
               'Restart': 'no', 'TimeoutStopSec': '5s', 'RuntimeMaxSec': '30s'}
    if phase == 'check':
        options['RemainAfterExit'] = 'yes'
    command = ['systemd-run', '--quiet', '--unit=' + unit,
               '--service-type=' + ('oneshot' if phase == 'check' else 'simple')]
    for key, value in options.items():
        command.append('--property=' + key + '=' + value)
    command += [str(core), phase, '-c', str(config)]
    return run(command, check=False).returncode


def observe(unit, phase, properties, port, account, core):
    deadline = time.monotonic() + 10
    listening = False
    while True:
        state = show(unit)
        validate_properties(state, properties, account.pw_name, str(account.pw_gid))
        if phase == 'check' and state.get('ActiveState') == 'active' and state.get('SubState') == 'exited':
            outcome = 'check_passed'
            break
        if phase == 'run' and listener_owned(int(state.get('MainPID') or '0'), port, account.pw_uid, core):
            time.sleep(.3)
            state = show(unit)
            listening = listener_owned(int(state.get('MainPID') or '0'), port, account.pw_uid, core)
            outcome = 'owned_loopback_listener' if listening else 'listener_disappeared'
            break
        if state.get('ActiveState') in {'failed', 'inactive'}:
            outcome = 'process_exited'
            break
        if time.monotonic() >= deadline:
            outcome = 'unresolved_timeout'
            break
        time.sleep(.1)
    log = journal(unit)
    return {'outcome': outcome, 'listener_owned': listening, 'properties': state,
            'explicit_netlink_reasons': netlink_reason(log), 'fake_fixture_log': log}


def identity_self_tests():
    """Pure mocked /proc regressions; no real process, file or systemd access."""
    import unittest
    from unittest.mock import patch

    class IdentitySamplingTests(unittest.TestCase):
        core = Path('/run/vui-fake/sing-box')
        descriptor = Path('/proc/1234/fd/7')

        def sample(self, executable, uid, inode='4242'):
            def read(path, *args, **kwargs):
                if path == Path('/proc/1234/status'):
                    return 'Uid: ' + ' '.join([str(uid)] * 4) + '\n'
                self.assertEqual(path, Path('/proc/net/tcp'))
                return ('header\n0: 0100007F:28CB 00000000:0000 0A '
                        '00000000:00000000 00:00000000 00000000 65534 0 ' + inode + '\n')
            def link(path):
                if str(path) == '/proc/1234/exe':
                    return executable
                self.assertEqual(path, self.descriptor)
                return 'socket:[4242]'
            with patch.object(Path, 'read_text', autospec=True, side_effect=read) as reads, \
                    patch.object(Path, 'iterdir', return_value=iter([self.descriptor])), \
                    patch.object(os, 'readlink', side_effect=link):
                outcome = listener_owned(1234, 10443, 65534, self.core)
                if executable != str(self.core):
                    reads.assert_not_called()
                return outcome

        def test_pre_exec_root_executor_is_not_ready(self):
            self.assertFalse(self.sample('/usr/lib/systemd/systemd-executor', 0))

        def test_actual_core_running_as_root_is_rejected(self):
            with self.assertRaisesRegex(RuntimeError, 'non-root identity'):
                self.sample(str(self.core), 0)

        def test_actual_nonroot_core_requires_its_owned_listener(self):
            self.assertTrue(self.sample(str(self.core), 65534))
            self.assertFalse(self.sample(str(self.core), 65534, inode='9999'))

        def test_other_executable_with_matching_uid_is_not_ready(self):
            self.assertFalse(self.sample('/usr/bin/unrelated', 65534))

    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(IdentitySamplingTests))
    return 0 if result.wasSuccessful() else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--core', type=Path)
    parser.add_argument('--self-test', action='store_true', help='Run only four pure mocked identity-sampling regressions')
    args = parser.parse_args()
    if args.self_test:
        return identity_self_tests()
    if args.core is None:
        parser.error('--core is required for the hosted systemd experiment')
    guard()
    canonical = canonical_sandbox()
    original = args.core.resolve(strict=True)
    provenance = json.loads((original.parent / 'provenance.json').read_text())['sing-box']
    pin = PINS['x86_64-gnu']['sing-box']
    digest = hashlib.sha256(original.read_bytes()).hexdigest()
    if (any(provenance.get(key) != value for key, value in pin.items())
            or provenance.get('target') != 'x86_64-gnu' or provenance.get('binary_sha256') != digest):
        raise RuntimeError('Core provenance differs from the fixed official x86_64-gnu 1.14.2 pin')
    account = pwd.getpwnam('nobody')
    if account.pw_uid == 0:
        raise RuntimeError('Probe account must not be root')
    work = Path(tempfile.mkdtemp(prefix='vui-sbox-af-', dir='/run'))
    units, report = [], {'canonical_sandbox': canonical, 'core_binary_sha256': digest,
                         'fake_only': True, 'vps_cause_proven': False, 'variants': {}}
    def interrupted(signum, _frame):
        raise InterruptedError('Probe interrupted by signal ' + str(signum))
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        os.chown(work, account.pw_uid, account.pw_gid)
        work.chmod(0o700)
        core = work / 'sing-box'
        shutil.copyfile(original, core)
        core.chmod(0o555)
        version = run([str(core), 'version']).stdout
        if not version.startswith('sing-box version 1.14.2\n'):
            raise RuntimeError('Copied fixed-core version mismatch')
        certificate, key = work / 'fake-cert.pem', work / 'fake-key.pem'
        run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
             '-subj', '/CN=fake-node.example.test', '-keyout', str(key), '-out', str(certificate)])
        os.chown(key, account.pw_uid, account.pw_gid)
        key.chmod(0o400)
        certificate.chmod(0o444)
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1', 0))
            port = reservation.getsockname()[1]
        basis = adapter_basis()
        fixture = {**basis, 'log': {'level': 'debug', 'timestamp': False},
                   'inbounds': [{'type': 'vless', 'tag': 'fake-direct', 'listen': '127.0.0.1',
                                 'listen_port': port, 'users': [{'uuid': FAKE_UUID}],
                                 'tls': {'enabled': True, 'server_name': 'fake-node.example.test',
                                         'certificate_path': str(certificate), 'key_path': str(key)}}]}
        config = work / 'fake-config.json'
        config.write_text(json.dumps(fixture))
        config.chmod(0o444)
        report.update(route=basis['route'], outbounds=basis['outbounds'],
                      auto_detect_interface=basis['route'].get('auto_detect_interface', 'UNSET: identical to adapter'),
                      config_sha256=hashlib.sha256(config.read_bytes()).hexdigest(), loopback_port=port,
                      fixture_scope='Same direct dual-OFF adapter route/outbound; fake VLESS TLS, loopback-only inlet, debug log; no relay/upstream runtime claimed')
        variants = {'canonical': canonical,
                    'with_netlink': {**canonical, 'RestrictAddressFamilies': canonical['RestrictAddressFamilies'] + ' AF_NETLINK'}}
        # Both variants validate the exact same config before either run.
        for phase in ('check', 'run'):
            for name, properties in variants.items():
                unit = work.name + '-' + name.replace('_', '-') + '-' + phase + '.service'
                units.append(unit)
                start_exit = start_variant(unit, phase, properties, work, core, config, account)
                result = observe(unit, phase, properties, port, account, core)
                result['systemd_run_exit'] = start_exit
                report['variants'].setdefault(name, {})[phase] = result
                run(['systemctl', 'stop', unit], check=False)
                result['after_stop'] = show(unit)
                if phase == 'check' and result['outcome'] != 'check_passed':
                    raise RuntimeError('Configuration check did not pass; startup comparison is inconclusive')
        base = report['variants']['canonical']['run']
        added = report['variants']['with_netlink']['run']
        if (base['outcome'] == 'process_exited' and base['explicit_netlink_reasons']
                and added['outcome'] == 'owned_loopback_listener'):
            report['hypothesis'] = 'supported_in_this_isolated_fixture_only'
        elif base['outcome'] == added['outcome'] == 'owned_loopback_listener':
            report['hypothesis'] = 'not_reproduced_for_same_direct_adapter_route'
        else:
            report['hypothesis'] = 'inconclusive: inspect explicit reasons; timeout is not a cause'
    except Exception as exc:
        report['hypothesis'] = 'inconclusive'
        report['error'] = type(exc).__name__ + ': ' + str(exc)
    finally:
        cleanup = []
        for unit in units:
            try:
                stopped = run(['systemctl', 'stop', unit], check=False)
                state = show(unit)
                if state.get('ActiveState') not in {'inactive', 'failed'} or state.get('MainPID', '0') != '0':
                    raise RuntimeError('Owned probe unit still running')
                run(['systemctl', 'reset-failed', unit], check=False)
                cleanup.append({'unit': unit, 'stopped': True, 'stop_exit': stopped.returncode})
            except Exception as exc:
                cleanup.append({'unit': unit, 'stopped': False, 'error': type(exc).__name__})
        report['cleanup'] = cleanup
        if all(item['stopped'] for item in cleanup):
            shutil.rmtree(work)
            report['temporary_directory_removed'] = True
        else:
            report['temporary_directory_removed'] = False
            report['hypothesis'] = 'inconclusive: cleanup failed'
        print(json.dumps(report, indent=2, sort_keys=True), flush=True)
    return 1 if report['hypothesis'].startswith('inconclusive') else 0


if __name__ == '__main__':
    raise SystemExit(main())
