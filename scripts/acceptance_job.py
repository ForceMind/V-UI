"""Fixed Actions acceptance roles; receipts record identity, never green jobs.

Official download-artifact selects the external producer ID. Inner kit bytes
are verified here; the publisher still re-downloads and checks outer digests
and each role's actual jobs/steps. No arbitrary command or fake install root.
"""
from __future__ import annotations
import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

from scripts import release_acceptance as core

WORK = Path('/tmp/vui-oneclick')
KIT = WORK / 'kit'
BUNDLE = WORK / 'vui-linux.zip'
MIHOMO = WORK / 'client' / 'mihomo'
ROOT = Path(__file__).resolve().parents[1]
TEST_COMMAND = [sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_oneclick_system.py', '-v']
FETCH_COMMAND = [sys.executable, '-B', 'scripts/fetch_mihomo.py', str(MIHOMO.parent)]
MAX_LOG = 16_000_000
HEADER = 'VUI_ACCEPTANCE_CONTEXT '
FOOTER = 'VUI_ACCEPTANCE_EXIT '
UPGRADE_23_MARKER = 'VUI_23_TO_24_UPGRADE_ACCEPTANCE:'
UPGRADE_24_MARKER = 'VUI_24_TO_25_UPGRADE_ACCEPTANCE:'
UPGRADE_MARKERS = {
    ('0.4.24', 'upgrade-0.4.23'): UPGRADE_23_MARKER,
    ('0.4.25', 'upgrade-0.4.24'): UPGRADE_24_MARKER,
}


CORE_SWITCH_FIXTURE = WORK / 'core-switch-fixture'
CORE_SWITCH_CORES = ('sing-box', 'xray')
CORE_SWITCH_MARKER = b'VUI_INSTALLED_CORE_SWITCH_ACCEPTANCE:'
CORE_INTERRUPT_MARKER = b'VUI_INSTALLED_CORE_INTERRUPT_ACCEPTANCE:'
CORE_RESTORE_MARKER = b'VUI_INSTALLED_CORE_RESTORE_ACCEPTANCE:'
CORE_REMOTE_MARKERS = (
    (b'VUI_INSTALLED_CORE_REMOTE_TASK_ACCEPTANCE:', 'CORE_REMOTE_TASK_EVIDENCE_REQUIRED'),
    (b'VUI_INSTALLED_CORE_BACKUP_RECOVERY_ACCEPTANCE:', 'CORE_BACKUP_RECOVERY_EVIDENCE_REQUIRED'),
)

def matrix_for_version(version):
    number = core._version(version)
    core._need(number >= (0, 4, 22), 'PARALLEL_VERSION_NOT_SUPPORTED')
    return {'include': [{'version': old, 'source': source, 'path': '.published-v' + old.replace('.', ''),
                         'netlink': core._version(old) >= (0, 4, 19)}
                        for old, source in core.BASELINES.items() if core._version(old) < number]}


def _role(version, role):
    rows = matrix_for_version(version)['include']
    allowed = {'build', 'fresh'} | {'upgrade-' + row['version'] for row in rows}
    core._need(type(role) is str and role in allowed, 'ROLE_NOT_IN_VERSION_MATRIX')
    return next((row for row in rows if role == 'upgrade-' + row['version']), None)


def artifact_name(kind, *, source, version, run_id, run_attempt, role):
    core._source_version(source, version); _role(version, role)
    core._need(core._positive(run_id) and core._positive(run_attempt), 'ARTIFACT_RUN_IDENTITY')
    core._need(kind in ('kit', 'receipt', 'log'), 'ARTIFACT_KIND')
    if kind == 'kit':
        core._need(role == 'build', 'KIT_ROLE_MUST_BUILD')
        return f'vui-release-kit-{source}-{run_id}-{run_attempt}'
    prefix = 'vui-oneclick-receipt' if kind == 'receipt' else 'vui-oneclick-evidence'
    return f'{prefix}-{role}-{source}-{run_id}-{run_attempt}'


def role_recipe(version, role, *, old_source=None):
    """Pure exact equivalent of the original per-role environment and commands."""
    row = _role(version, role)
    core._need(role != 'build', 'BUILD_HAS_NO_INSTALL_RECIPE')
    env = {'VUI_SYSTEM_INSTALL_TEST': '1', 'VUI_REMOTE_MAINTENANCE_TEST': '1',
           'VUI_RELEASE_BUNDLE': str(BUNDLE), 'VUI_RELEASE_KIT': str(KIT)}
    if role == 'fresh':
        core._need(old_source is None, 'FRESH_HAS_NO_OLD_SOURCE')
        env.update(VUI_DOMAIN_RETRY_TEST='1', VUI_INSTALLER_YN_TEST='1', VUI_EXISTING_INSTALL_TEST='1')
        netlink = True
    else:
        core._need(old_source is not None, 'UPGRADE_SOURCE_DIRECTORY_REQUIRED')
        env.update(VUI_PUBLISHED_INSTALL_VERSION=row['version'], VUI_RELEASE_COMMIT=row['source'],
                   VUI_PUBLISHED_UPGRADE_SOURCE=str(Path(old_source).absolute()))
        netlink = row['netlink']
    commands = []
    if netlink:
        env.update(VUI_NETLINK_SYSTEMD_TEST='1', VUI_TEST_MIHOMO=str(MIHOMO))
        commands.append(list(FETCH_COMMAND))
    commands.append(list(TEST_COMMAND))
    return {'env': env, 'commands': commands}


def role_environment(base, version, role, *, old_source=None):
    # Empty strings and especially the truthy string '0' cannot enable another
    # test family, fake transport, low-resource gate or live installation mode.
    result = {key: value for key, value in base.items() if not key.startswith('VUI_')}
    result.update(role_recipe(version, role, old_source=old_source)['env'])
    result['PYTHONDONTWRITEBYTECODE'] = '1'
    return result


def _context(environ):
    core._need(environ.get('GITHUB_REPOSITORY') == core.REPOSITORY
               and environ.get('GITHUB_ACTIONS') == 'true', 'TRUSTED_ACTIONS_CONTEXT_REQUIRED')
    values = []
    for key in ('GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT'):
        raw = environ.get(key)
        core._need(type(raw) is str and re.fullmatch(r'[1-9][0-9]{0,18}', raw), 'RUN_CONTEXT_INVALID')
        value = int(raw); core._need(core._positive(value), 'RUN_CONTEXT_INVALID'); values.append(value)
    return {'repository': core.REPOSITORY, 'run_id': values[0], 'run_attempt': values[1]}


def _read_file(path, limit):
    absolute = core._absolute(path)
    with core._directory(absolute.parent) as directory:
        fd = os.open(absolute.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        try:
            info = os.fstat(fd)
            core._need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= limit, 'UNSAFE_JOB_FILE')
            raw = core._read(fd, limit)
            core._need(core._file_identity(os.fstat(fd)) == core._file_identity(info), 'JOB_FILE_CHANGED')
            return raw
        finally:
            os.close(fd)


def _identity(path, source, version, scope, *, table=None, bundle=None):
    value = core.load_identity(_read_file(path, core.MAX_JSON))
    core._need(value['source_sha'] == source and value['version'] == version and value['validation_scope'] == scope,
               'JOB_IDENTITY_MISMATCH')
    core._need(table is None or value['checksums_sha256'] == table, 'JOB_TABLE_MISMATCH')
    core._need(bundle is None or value['bundle_sha256'] == bundle, 'JOB_BUNDLE_MISMATCH')
    return value


def _new_output(path, kit):
    """Caller owns the returned fd; any failed final output stays uncertain."""
    path = core._absolute(path)
    with core._directory(kit) as kit_fd:
        info = os.fstat(kit_fd); forbidden = (info.st_dev, info.st_ino)
    with core._directory(path.parent, forbidden=forbidden) as directory:
        info = os.fstat(directory)
        core._need(info.st_uid == os.geteuid() and not info.st_mode & 0o022, 'JOB_OUTPUT_PARENT_UNSAFE')
        return os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)


def _write_json(path, value, kit):
    raw = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode() + b'\n'
    core._need(len(raw) <= core.MAX_JSON, 'JOB_JSON_LIMIT')
    with os.fdopen(_new_output(path, kit), 'wb') as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())


def producer(kit, source, version, identity_output):
    identity = core.inspect_producer_kit(kit, expected_source=source, candidate_version=version)
    _write_json(identity_output, identity, kit)
    return identity


def prepare(kit, source, version, table, bundle, identity_output, *, bundle_output=BUNDLE):
    core._need(core._hex(bundle, 64), 'EXTERNAL_BUNDLE_DIGEST_REQUIRED')
    identity = core.verify_kit(kit, expected_source=source, candidate_version=version,
                               expected_checksums_sha256=table, expected_bundle_sha256=bundle,
                               bundle_output=bundle_output)
    _write_json(identity_output, identity, kit)
    return identity


def _checkout(path, source):
    actual = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=path, text=True).strip()
    core._need(actual == source, 'CHECKOUT_SOURCE_MISMATCH')


def _log_context(identity, role, environ):
    return {'identity': identity, 'role': role, **_context(environ)}


def validate_test_log(raw, expected_context):
    core._need(type(raw) is bytes and len(raw) <= MAX_LOG, 'TEST_LOG_LIMIT')
    try:
        text = raw.decode('utf-8')
    except UnicodeError as error:
        raise core.ReleaseAcceptanceError('TEST_LOG_UTF8') from error
    lines = text.splitlines()
    core._need(lines and lines[0].startswith(HEADER) and lines[-1] == FOOTER + '0', 'TEST_LOG_INCOMPLETE_OR_FAILED')
    core._need(sum(line.startswith(HEADER) for line in lines) == 1
               and sum(line.startswith(FOOTER) for line in lines) == 1, 'TEST_LOG_CONTEXT_DUPLICATED')
    context = core._json(lines[0][len(HEADER):], core.MAX_JSON)
    core._identity(context.get('identity'))
    core._need(context == expected_context and all(type(context[key]) is type(value)
                                                for key, value in expected_context.items()), 'TEST_LOG_CONTEXT_MISMATCH')
    core._need(len(re.findall(r'^Ran 1 test in [0-9]+(?:\.[0-9]+)?s$', text, re.M)) == 1
               and len(re.findall(r'^Ran [0-9]+ tests? in ', text, re.M)) == 1
               and len(re.findall(r'^OK$', text, re.M)) == 1
               and not re.search(r'\b(?:SKIP|SKIPPED|skipped|FAILED|ERROR)\b', text), 'TEST_LOG_NOT_ONE_UNSKIPPED_SUCCESS')
    marker = UPGRADE_MARKERS.get((context['identity']['version'], context['role']))
    if marker is not None:
        matches = [line for line in lines if marker in line]
        core._need(len(matches) == text.count(marker) == 1 and matches[0].startswith(marker + ' ')
                   and matches[0][len(marker):].strip(), f"UPGRADE_{context['role'][-2:]}_EVIDENCE_REQUIRED")


def validate_core_switch_evidence(raw_log):
    markers = [line for line in raw_log.splitlines() if CORE_SWITCH_MARKER in line]
    expected = tuple(CORE_SWITCH_MARKER + b' ' + name.encode('ascii') + b' '
                     for name in CORE_SWITCH_CORES)
    core._need(len(markers) == len(expected) == raw_log.count(CORE_SWITCH_MARKER)
               and all(line.startswith(prefix) and line[len(prefix):].strip()
                       for line, prefix in zip(markers, expected)),
               'CORE_SWITCH_EVIDENCE_REQUIRED')
    interrupted = [line for line in raw_log.splitlines() if CORE_INTERRUPT_MARKER in line]
    core._need(len(interrupted) == raw_log.count(CORE_INTERRUPT_MARKER) == 1
               and interrupted[0].startswith(CORE_INTERRUPT_MARKER + b' ')
               and interrupted[0][len(CORE_INTERRUPT_MARKER):].strip()
               and raw_log.find(CORE_INTERRUPT_MARKER) > raw_log.rfind(CORE_SWITCH_MARKER),
               'CORE_INTERRUPT_EVIDENCE_REQUIRED')
    restored = [line for line in raw_log.splitlines() if CORE_RESTORE_MARKER in line]
    core._need(len(restored) == raw_log.count(CORE_RESTORE_MARKER) == 1
               and restored[0].startswith(CORE_RESTORE_MARKER + b' ')
               and restored[0][len(CORE_RESTORE_MARKER):].strip()
               and raw_log.find(CORE_RESTORE_MARKER) > raw_log.find(CORE_INTERRUPT_MARKER),
               'CORE_RESTORE_EVIDENCE_REQUIRED')
    previous = raw_log.find(CORE_RESTORE_MARKER)
    for marker, error in CORE_REMOTE_MARKERS:
        lines = [line for line in raw_log.splitlines() if marker in line]
        position = raw_log.find(marker)
        core._need(len(lines) == raw_log.count(marker) == 1
                   and lines[0].startswith(marker + b' ')
                   and lines[0][len(marker):].strip() and position > previous, error)
        previous = position


def _exit_code(value):
    core._need(type(value) is int, 'TEST_EXIT_CODE')
    return value if value >= 0 else 128 - value


def run_test(source, version, role, identity_path, log, *, old_source=None, environ=None, core_switch=False):
    environ = os.environ if environ is None else environ
    _context(environ)
    recipe = role_recipe(version, role, old_source=old_source)
    identity = _identity(identity_path, source, version, core.CONSUMER_SCOPE)
    checked = core.verify_kit(KIT, expected_source=source, candidate_version=version,
                              expected_checksums_sha256=identity['checksums_sha256'],
                              expected_bundle_sha256=identity['bundle_sha256'])
    core._need(checked == identity, 'PREPARED_IDENTITY_CHANGED')
    with core._directory(BUNDLE.parent) as directory, ExitStack() as stack:
        fd, original = core._member(directory, BUNDLE.name, core.MAX_BUNDLE, stack)
        core._need(core._digest(fd, core.MAX_BUNDLE) == identity['bundle_sha256']
                   and core._file_identity(os.fstat(fd)) == original, 'PREPARED_BUNDLE_CHANGED')
    sidecar = _read_file(BUNDLE.with_suffix(BUNDLE.suffix + '.sha256'), core.MAX_TABLE)
    core._need(sidecar == (identity['bundle_sha256'] + '  ' + BUNDLE.name + '\n').encode('ascii'), 'PREPARED_SIDECAR_CHANGED')
    _checkout(ROOT, source)
    core._need((ROOT / 'VERSION').read_text().strip() == version, 'CHECKOUT_VERSION_MISMATCH')
    if old_source is not None:
        row = _role(version, role)
        _checkout(Path(old_source), row['source'])
    env = role_environment(environ, version, role, old_source=old_source)
    if core_switch:
        core_directories = tuple(CORE_SWITCH_FIXTURE / name for name in CORE_SWITCH_CORES)
        directories = (CORE_SWITCH_FIXTURE, *core_directories, *(path / 'candidate' for path in core_directories))
        core._need(role == 'fresh' and all(path.is_dir() and not path.is_symlink() for path in directories),
                   'CORE_SWITCH_FRESH_FIXTURE_REQUIRED')
        env['VUI_CORE_SWITCH_FIXTURE'] = str(CORE_SWITCH_FIXTURE)
    context = _log_context(identity, role, environ)
    code = 0
    with os.fdopen(_new_output(log, KIT), 'wb') as stream:
        stream.write((HEADER + json.dumps(context, sort_keys=True) + '\n').encode()); stream.flush()
        for command in recipe['commands']:
            try:
                completed = subprocess.run(command, cwd=ROOT, env=env, stdout=stream, stderr=subprocess.STDOUT,
                                           check=command != TEST_COMMAND)
                code = _exit_code(completed.returncode)
            except subprocess.CalledProcessError as error:
                code = _exit_code(error.returncode)
            if code:
                break
        stream.write(('\n' + FOOTER + str(code) + '\n').encode()); stream.flush(); os.fsync(stream.fileno())
    if code == 0:
        raw_log = _read_file(log, MAX_LOG)
        validate_test_log(raw_log, context)
        if core_switch:
            validate_core_switch_evidence(raw_log)
    return code


def receipt(identity_path, source, version, role, artifact_id, artifact_digest, table, bundle, output,
            *, test_log=None, environ=None):
    environ = os.environ if environ is None else environ
    context = _context(environ); _role(version, role)
    core._need(Path(output).name == 'acceptance-receipt.json', 'FIXED_RECEIPT_BASENAME_REQUIRED')
    scope = core.PRODUCER_SCOPE if role == 'build' else core.CONSUMER_SCOPE
    core._need(core._hex(table, 64) and core._hex(bundle, 64), 'EXTERNAL_DIGESTS_REQUIRED')
    identity = _identity(identity_path, source, version, scope, table=table, bundle=bundle)
    if role == 'build':
        core._need(test_log is None, 'BUILD_RECEIPT_HAS_NO_TEST_CLAIM')
    else:
        core._need(test_log is not None, 'CONSUMER_TEST_LOG_REQUIRED')
        validate_test_log(_read_file(test_log, MAX_LOG), _log_context(identity, role, environ))
    result = core.create_receipt(identity, run_id=context['run_id'], run_attempt=context['run_attempt'], role=role,
                                 producer_artifact_id=artifact_id, producer_artifact_digest=artifact_digest)
    core.validate_receipt(result, expected_source=source, candidate_version=version, run_id=context['run_id'],
                          run_attempt=context['run_attempt'], role=role, producer_artifact_id=artifact_id,
                          producer_artifact_digest=artifact_digest, expected_checksums_sha256=table,
                          expected_bundle_sha256=bundle)
    _write_json(output, result, KIT)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    matrix = commands.add_parser('matrix'); matrix.add_argument('--version', required=True)
    name = commands.add_parser('name'); name.add_argument('--kind', choices=('kit', 'receipt', 'log'), required=True)
    build = commands.add_parser('producer')
    consume = commands.add_parser('prepare')
    test = commands.add_parser('run-test')
    record = commands.add_parser('receipt')
    for command in (name, build, consume, test, record):
        command.add_argument('--source', required=True); command.add_argument('--version', required=True)
    for command in (name, test, record): command.add_argument('--role', required=True)
    for command in (build, consume):
        command.add_argument('--kit', required=True); command.add_argument('--identity-output', required=True)
    for command in (consume, record):
        command.add_argument('--expected-checksums-sha256', required=True)
        command.add_argument('--expected-bundle-sha256', required=True)
    consume.add_argument('--bundle-output', default=str(BUNDLE))
    for command in (test, record): command.add_argument('--identity', required=True)
    test.add_argument('--log', required=True); test.add_argument('--old-source')
    test.add_argument('--core-switch', action='store_true')
    record.add_argument('--producer-artifact-id', type=int, required=True)
    record.add_argument('--producer-artifact-digest', required=True)
    record.add_argument('--output', required=True); record.add_argument('--test-log')
    args = parser.parse_args(argv)
    try:
        if args.command == 'matrix': result = matrix_for_version(args.version)
        elif args.command == 'name':
            context = _context(os.environ)
            result = artifact_name(args.kind, source=args.source, version=args.version, run_id=context['run_id'],
                                   run_attempt=context['run_attempt'], role=args.role)
        elif args.command == 'producer': result = producer(args.kit, args.source, args.version, args.identity_output)
        elif args.command == 'prepare':
            result = prepare(args.kit, args.source, args.version, args.expected_checksums_sha256,
                             args.expected_bundle_sha256, args.identity_output, bundle_output=args.bundle_output)
        elif args.command == 'run-test':
            code = run_test(args.source, args.version, args.role, args.identity, args.log,
                            old_source=args.old_source, core_switch=args.core_switch)
            if code: raise SystemExit(code)
            return
        else:
            result = receipt(args.identity, args.source, args.version, args.role, args.producer_artifact_id,
                             args.producer_artifact_digest, args.expected_checksums_sha256,
                             args.expected_bundle_sha256, args.output, test_log=args.test_log)
    except (core.ReleaseAcceptanceError, OSError, subprocess.SubprocessError) as error:
        code = str(error) if isinstance(error, core.ReleaseAcceptanceError) else 'JOB_IO_OR_COMMAND_REFUSED'
        parser.exit(1, 'Acceptance job refused: ' + code + '\n')
    print(result if isinstance(result, str) else json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
