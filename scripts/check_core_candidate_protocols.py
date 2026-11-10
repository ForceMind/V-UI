"""Run the existing x86_64 GNU protocol gates on same-run CI candidates."""
import argparse
from contextlib import ExitStack, contextmanager
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import tempfile
import unittest

from app import release_tools
from app.core_updates import artifacts
from scripts import check_core_candidate_native as native
from scripts import fetch_mihomo, fetch_test_cores
from tests.core_candidate_context import CORE_ROOT

ROOT = Path(__file__).resolve().parents[1]
TARGET = 'x86_64-gnu'
# Real test classes from the existing protocol gates. Fixed counts detect
# dropped tests, including inherited transport cases, before any evidence.
SUITES = {
    'test_real_cores': ({'PinnedCoreTests': 1, 'XrayManagedTLSRealTests': 1},
        '7a9223e01719d566853b833e0f1a06ee0f2f3684e2cbb7fc35c3404639f3478f'),
    'test_loopback_chain': ({'LoopbackChainTests': 5},
        'ac304536ef4e5a9b822ff0e3b7507f9bc15ab61a0104ba18601b49ef3eaaaf2d'),
    'test_vmess_loopback': ({'VMessLoopbackTests': 7},
        '1bddb07f47d46665fa61773332d1aa8c04f8507dba453816537eb9d14ad70e27'),
    'test_trojan_loopback': ({'TrojanLoopbackTests': 4},
        '2e4c8629ccddda6b1171bdd75af55ea4be2f84e82ac881efa74d0721fa50da1e'),
    'test_shadowsocks_loopback': ({'ShadowsocksLoopbackTests': 3, 'ShadowsocksAES256LoopbackTests': 3, 'ShadowsocksChaCha20LoopbackTests': 3},
        'a9513dea54294c552b76b8952da845b14f749f6bb3e9e16f475c2465ab319dff'),
    'test_vless_ws_loopback': ({'VLESSWebSocketLoopbackTests': 12},
        'd96c5a38a6f8ca07143e9b7ab47958f17fa293d28aec21331f7473952a7bdd38'),
    'test_vless_grpc_preflight_loopback': ({'VLESSGRPCPreflightLoopbackTests': 8},
        '39b18d085d1ff7fcf3636834f5fee37a71aaa172c7f49bf9fc0542f098c8199d'),
    'test_vless_grpc_loopback': ({'VLESSGRPCLoopbackTests': 8},
        '8f4339bf74632a483d2984072334c6f0ed1555ac6935aed3e289be54bb7a5ab4'),
    'test_hysteria2_preflight_loopback': ({'Hysteria2PreflightLoopbackTests': 5},
        '7a586a2358ff25fc05b9c5915ff39d8a1f2f3d1dc763abada382195972735ac8'),
    'test_hysteria2_loopback': ({'Hysteria2PublicLoopbackTests': 5},
        '5b9f6624a03c7b887b89b7510e2503455e99390dde2b2ad47394c61e8acd82a2'),
    'test_hysteria2_managed_certificate': ({'Hysteria2ManagedRealCoreTests': 1},
        'ddbb722db22e7f5fe2d5684f174e4e3d50556edbcefb87b0a65033af9bf2ec78'),
    'test_tuic_preflight_loopback': ({'TUICPreflightLoopbackTests': 6},
        '95596f3b68dda9d1a7a5a438460388b2c3ea5cd3ac754cef8891353dae269735'),
    'test_tuic_loopback': ({'TUICPublicLoopbackTests': 6},
        'df5e87d167dff895bb4a607ced15ce6f78dabd520e747f54cac0032869c774a5'),
    'test_tuic_managed_certificate': ({'TUICManagedRealCoreTests': 1},
        'b1e58b876181476f4f2176ae2d1111c2197b44fc357d7c852e1ec47ed523c0b3'),
    'test_reality_preflight_loopback': ({'RealityPreflightLoopbackTests': 7},
        'e734fcb3a485c2a73ad6cfa40543a59942be908a71b15753ad0668d491c7465e'),
    'test_reality_loopback': ({'RealityPublicLoopbackTests': 7},
        '2ae6c184e1e4a6a176bea0a76c4bbd499686ff6a36b3ad30d4f99c3d373702d3'),
    'test_xhttp_preflight_loopback': ({'XHTTPPreflightLoopbackTests': 12},
        'd4141d9b67d4dace5034bc6bf5c8a026f3a1145c2992ac4069f50d1804515439'),
    'test_xray_share_loopback': ({'XrayShareLoopbackTests': 1},
        '04766558490d598bd5524d1184d79f33bbf87a4aa3d82e66f4b60a6cd1af58fa'),
    'test_udp_dns_capability_loopback': ({'ShadowsocksDatagramParserTests': 1, 'ShadowsocksDatagramLoopbackTests': 5, 'MihomoStartupLoopbackTests': 1, 'DualSwitchDatagramRejectionTests': 2},
        'e2059f959144b8fefa7ab5fcb9aad8bf9c7c5efef936904c92824c0ed58cf976'),
    'test_proxy_policy_real': ({'ProxyPolicyRealTests': 4},
        '2c23eeb6a0af5002e17c61fbf7f458b0fcdd8ffbbd34d8db9e8821990abcd56c'),
    'test_proxy_double_hop': ({'ProxyDoubleHopTests': 13},
        '3ab8ffc8eb75bc0859463972a46cd61701de3380ca1ebb117c0f5b24babb319b'),
    'test_proxy_apply_real': ({'ProxyApplyRealTests': 4},
        '87a01378915c92a27a66d32846f1ccc647069af8b2e212abdb1edc5b3310f996'),
    'test_proxy_diagnostics_real': ({'DiagnosticRealWorkerTests': 12},
        '15bb415a5035ad69c6178982a0a6ced923a7afe7c5cddb607244321e7d5a5ffb'),
    'test_proxy_exports_real': ({'StrictExportParserTests': 1, 'StrictExportLoopbackTests': 6},
        '17afb2c16e8e594fd1f71d8a543f82a861cb65bb9b24284287956f8c615957b7'),
    'test_tcp_upstream_export_real': ({'TCPUpstreamExportRealTests': 4},
        'b3e90ac48028a115632e849bf6ca33b2394863600fdd5ecb0447c2efcc0df900'),
    'test_inbound_routing_real': ({'InboundRoutingRealTests': 4},
        'e43bc1ef19cefc67377a59fd9afb9ecf83863f1a0c88da619de34e45c8908c84'),
    'test_dns_core_tls': ({'DNSCoreTLSTests': 2},
        '1a3f862bd4589fecd56593434ff1343a9707070659885e006e3c849d789572ad'),
}
FLAGS = ('VUI_UDP_DNS_LOOPBACK', 'VUI_UDP_DNS_PARSER', 'VUI_PROXY_LOOPBACK',
         'VUI_TCP_UPSTREAM_LOOPBACK', 'VUI_INBOUND_ROUTING_LOOPBACK', 'VUI_DNS_CORE_TLS')
FIELDS = ('version', 'tag', 'pack_manifest_sha256', 'pack_sha256', 'checksums_sha256',
          'release_manifest_sha256', 'binary_sha256', 'binary_size')


@contextmanager
def secure_workspace(path, owner):
    """Check from / without resolving away unsafe ancestors."""
    assert path.is_absolute() and '..' not in path.parts and path != Path('/')
    with ExitStack() as stack:
        parent = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        stack.callback(os.close, parent)
        bindings = []
        def directory_identity(info):
            assert stat.S_ISDIR(info.st_mode) and info.st_uid in (0, owner) and not info.st_mode & 0o022
            return info.st_dev, info.st_ino, info.st_uid, info.st_gid, info.st_mode
        directory_identity(os.fstat(parent))
        for name in path.parts[1:]:
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            stack.callback(os.close, child)
            identity = directory_identity(os.fstat(child))
            bindings.append((parent, name, identity))
            parent = child
        assert os.fstat(parent).st_uid == owner
        try:
            yield directory_identity(os.fstat(parent))[:2]
        finally:
            for parent, name, identity in bindings:
                assert directory_identity(os.stat(name, dir_fd=parent, follow_symlinks=False)) == identity


def bounded_json(path):
    with artifacts._opened(path) as (fd, size, stable):
        assert 0 < size <= 64_000
        value = artifacts._json(os.pread(fd, size+1, 0))
        stable()
    return value


def digest_file(path):
    with artifacts._opened(path) as (fd, size, stable):
        digest = artifacts._digest_fd(fd, size)
        stable()
    return digest


def fixed_clients(directory, mihomo):
    singbox = directory / 'sing-box'
    baseline = bounded_json(directory / 'provenance.json')['sing-box']
    mihomo_row = bounded_json(mihomo.with_name('mihomo-provenance.json'))
    assert mihomo.name == 'mihomo'
    witnesses = {}
    for key, binary, provenance, pin, command, prefix in (
            ('sing-box', singbox, baseline, fetch_test_cores.PINS[TARGET]['sing-box'],
             ('version',), b'sing-box version 1.14.2\n'),
            ('mihomo', mihomo, mihomo_row, fetch_mihomo.PIN, ('-v',), b'Mihomo Meta v1.19.32 ')):
        assert all(provenance.get(key) == value for key, value in pin.items())
        if key == 'sing-box':
            assert provenance['target'] == TARGET
        digest = digest_file(binary)
        assert digest == provenance['binary_sha256']
        result = subprocess.run([str(binary), *command], check=True, capture_output=True, timeout=15)
        assert (result.stdout + result.stderr).startswith(prefix)
        witnesses[key] = dict(version=pin['version'], archive_sha256=pin['sha256'], binary_sha256=digest)
    return singbox, witnesses


def candidate_cores(inputs, destination, source, target, context):
    result = {}
    for core in native.CORES:
        name = f"core-candidate-{core}-{source}-{context['run_id']}-{context['run_attempt']}"
        root = inputs / name
        receipt_path = root / 'production-receipt.json'
        acquisition_path = root / 'inputs/acquisition-receipt.json'
        expected = bounded_json(root / 'inputs/expected.json')
        receipt = bounded_json(receipt_path)
        bounded_json(root / 'candidate/CORE-RELEASE.json')
        with artifacts._opened(root / 'candidate/SHA256SUMS') as (_, size, stable):
            assert size <= 64_000
            stable()
        acquisition = bounded_json(acquisition_path)
        acquisition_digest = digest_file(acquisition_path)
        assert type(receipt['schema']) is int and receipt['context'] == context
        assert acquisition_digest == receipt['acquisition_receipt_sha256']
        assert acquisition['kind'] == 'core-candidate-acquisition-v1'
        assert type(acquisition['schema']) is int and acquisition['schema'] == 1
        assert acquisition['qualified'] is False and acquisition['published'] is False
        assert acquisition['builder_source'] == source and acquisition['app_sources'] == [source]
        path, version, digest, pack = native.native_binary(root, core, source, target, context, destination)
        row, = [row for row in receipt['targets'] if row['target'] == target]
        assert (row['sha256'], row['binary_sha256']) == (pack, digest)
        assert row['binary_size'] == path.stat().st_size
        path.chmod(0o555)
        assert digest_file(path) == digest
        assert row['name'] == artifacts._pack_name(core, version, target)
        assert (expected['core'], expected['version'], expected['builder_source']) == (core, version, source)
        assert version != fetch_test_cores.PINS[target][core]['version']
        result[core] = dict(version=version, tag=expected['tag'], binary_sha256=digest, binary_size=row['binary_size'],
            pack_sha256=pack, pack_manifest_sha256=row['manifest_sha256'],
            release_manifest_sha256=receipt['aggregate']['manifest_sha256'],
            checksums_sha256=receipt['aggregate']['checksums_sha256'],
            production_receipt_sha256=digest_file(receipt_path), acquisition_receipt_sha256=acquisition_digest,
            artifact=name)
    return result


# Only fixed stdlib code crosses sudo. Candidate bytes enter on the same
# already-open read-only FD as the runner's verifier, never on a root path.
ROOT_STAGE = r"""
from contextlib import contextmanager
import hashlib
import json
import os
import re
import stat
import sys

DIRECTORY = 'vui-ci-core-protocols'
NAMES = ('sing-box', 'xray')
ROOT_UID = ROOT_GID = 0
DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
MAX_BINARY = 100_000_000
CHUNK = 1024 * 1024


def identity(info):
    return info.st_dev, info.st_ino


def directory_entry(parent, name, expected):
    info = os.stat(name, dir_fd=parent, follow_symlinks=False)
    assert stat.S_ISDIR(info.st_mode) and info.st_uid == ROOT_UID and not info.st_mode & 0o022
    assert identity(info) == expected


@contextmanager
def folder(parent, name, expected=None):
    fd = os.open(name, DIR_FLAGS, dir_fd=parent)
    try:
        actual = identity(os.fstat(fd))
        assert expected is None or actual == expected
        directory_entry(parent, name, actual)
        yield fd, lambda: directory_entry(parent, name, actual)
    finally:
        try:
            directory_entry(parent, name, identity(os.fstat(fd)))
        finally:
            os.close(fd)


def fingerprint(info):
    return (*identity(info), info.st_mode, info.st_nlink, info.st_uid, info.st_gid,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def leaf(parent, name, expected, mode, links=1):
    info = os.stat(name, dir_fd=parent, follow_symlinks=False)
    assert identity(info) == expected and info.st_uid == ROOT_UID and info.st_gid == ROOT_GID
    assert stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == mode and info.st_nlink == links


def hash_bytes(fd, size, copy_to=None):
    digest = hashlib.sha256()
    offset = 0
    while offset < size:
        block = os.pread(fd, min(CHUNK, size-offset), offset)
        assert block
        digest.update(block)
        offset += len(block)
        if copy_to is not None:
            pending = memoryview(block)
            while pending:
                written = os.write(copy_to, pending)
                assert written > 0
                pending = pending[written:]
    assert not os.pread(fd, 1, size)
    return digest.hexdigest()


def stage(directory, stable, name, source, expected_source, uid, size, digest):
    assert name in NAMES and 0 < size <= MAX_BINARY and re.fullmatch('[a-f0-9]{64}', digest)
    before = os.fstat(source)
    assert stat.S_ISREG(before.st_mode) and stat.S_IMODE(before.st_mode) == 0o555
    assert identity(before) == expected_source
    assert before.st_nlink == 1 and before.st_uid == uid and before.st_size == size
    stable()
    temp = '.stage-' + name
    target = os.open(temp, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
    created = identity(os.fstat(target))
    mode = 0o600
    links = 1
    try:
        leaf(directory, temp, created, mode)
        assert hash_bytes(source, size, target) == digest
        assert fingerprint(os.fstat(source)) == fingerprint(before)
        os.fsync(target)
        output = os.fstat(target)
        assert output.st_size == size and hash_bytes(target, size) == digest
        assert fingerprint(os.fstat(target)) == fingerprint(output)
        stable()
        leaf(directory, temp, created, mode)
        os.fchmod(target, 0o555)
        mode = 0o555
        leaf(directory, temp, created, mode)
        # linkat publishes an absent leaf atomically and never replaces a name.
        os.link(temp, name, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
        links = 2
        leaf(directory, name, created, mode, links)
        stable()
    finally:
        try:
            stable()
            leaf(directory, temp, created, mode, links)
            os.unlink(temp, dir_fd=directory)
        finally:
            os.close(target)
    leaf(directory, name, created, 0o555)
    stable()
    return created


def cleanup(parent, expected, known):
    # Verify the entire closure before any delete. Unknown entries are retained.
    with folder(parent, DIRECTORY, expected) as (directory, stable):
        assert set(os.listdir(directory)) == set(known)
        for name, recorded in known.items():
            leaf(directory, name, recorded, 0o555)
        for name, recorded in known.items():
            stable()
            leaf(directory, name, recorded, 0o555)
            os.unlink(name, dir_fd=directory)
        stable()
    directory_entry(parent, DIRECTORY, expected)
    os.rmdir(DIRECTORY, dir_fd=parent)


def main():
    assert sys.platform == 'linux' and os.geteuid() == 0 and __debug__
    uid = int(os.environ['SUDO_UID'])
    assert uid > 0
    os.umask(0o077)
    op, *args = sys.argv[1:]
    root = os.open('/', DIR_FLAGS)
    try:
        info = os.fstat(root)
        assert info.st_uid == 0 and not info.st_mode & 0o022
        with folder(root, 'opt') as (parent, opt_stable):
            opt_stable()
            if op == 'create':
                assert not args
                os.mkdir(DIRECTORY, 0o700, dir_fd=parent)
                with folder(parent, DIRECTORY) as (directory, stable):
                    os.fchown(directory, 0, 0)
                    os.fchmod(directory, 0o755)
                    stable()
                    result = identity(os.fstat(directory))
            elif op == 'stage':
                dev, ino, name, source_dev, source_ino, size, digest = args
                with folder(parent, DIRECTORY, (int(dev), int(ino))) as (directory, stable):
                    result = stage(directory, stable, name, 0, (int(source_dev), int(source_ino)),
                                   uid, int(size), digest)
            else:
                assert op == 'cleanup'
                dev, ino, *entries = args
                assert len(entries) % 3 == 0
                known = {}
                for index in range(0, len(entries), 3):
                    name, leaf_dev, leaf_ino = entries[index:index+3]
                    assert name in NAMES and name not in known
                    known[name] = (int(leaf_dev), int(leaf_ino))
                cleanup(parent, (int(dev), int(ino)), known)
                result = None
    finally:
        os.close(root)
    print(json.dumps(result, separators=(',', ':')))


if __name__ == '__main__':
    main()
"""


def root_stage(action, *args, stdin=subprocess.DEVNULL):
    command = ['/usr/bin/sudo', '-n', '/usr/bin/python3', '-B', '-I', '-S',
               '-c', ROOT_STAGE, action, *map(str, args)]
    output = subprocess.check_output(command, stdin=stdin, timeout=30)
    result = json.loads(output)
    if action == 'cleanup':
        assert result is None
        return None
    assert type(result) is list and len(result) == 2
    assert all(type(value) is int and value > 0 for value in result)
    return tuple(result)


@contextmanager
def installed_candidates(staging, cores):
    """Use the real ComponentReference / anchor with a fresh root-only directory."""
    assert set(cores) == set(native.CORES) and not os.path.lexists(CORE_ROOT)
    directory = root_stage('create')
    installed = {}
    try:
        with secure_workspace(CORE_ROOT, 0) as current:
            assert current == directory
            for name in native.CORES:
                row = cores[name]
                with artifacts._opened(staging/name) as (fd, size, stable):
                    info = os.fstat(fd)
                    assert info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o555
                    assert size == row['binary_size'] and artifacts._digest_fd(fd, size) == row['binary_sha256']
                    stable()
                    installed[name] = root_stage('stage', *directory, name, info.st_dev, info.st_ino,
                                                 size, row['binary_sha256'], stdin=fd)
                    stable()
                assert digest_file(CORE_ROOT/name) == row['binary_sha256']
            yield
            assert all(digest_file(CORE_ROOT/name) == row['binary_sha256'] for name, row in cores.items())
    finally:
        leaves = [value for name, identity in installed.items() for value in (name, *identity)]
        root_stage('cleanup', *directory, *leaves)

class EvidenceResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.successful_ids = []

    def addSuccess(self, test):
        super().addSuccess(test)
        self.successful_ids.append(test.id())


def ids_digest(ids):
    return hashlib.sha256("\n".join(sorted(ids)).encode("ascii")).hexdigest()


def require_suite_result(expected, result):
    if (not result.wasSuccessful() or result.skipped or result.expectedFailures
            or result.unexpectedSuccesses or result.testsRun != len(expected)
            or sorted(result.successful_ids) != sorted(expected)):
        raise AssertionError('Every required protocol test must execute and pass')


def suite_worker(name, evidence):
    assert name in SUITES and os.environ.get('VUI_TEST_CANDIDATE_CONTEXT')
    sys.path.insert(0, str(ROOT / 'tests'))
    from tests.core_candidate_context import artifacts_for_fixture
    assert artifacts_for_fixture()
    module = importlib.import_module(name)
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    ids = []
    classes, expected_digest = SUITES[name]
    for class_name, count in classes.items():
        test_class = getattr(module, class_name)
        assert test_class.__module__ == name and issubclass(test_class, unittest.TestCase)
        tests = loader.loadTestsFromTestCase(test_class)
        assert not loader.errors and tests.countTestCases() == count
        ids.extend(test.id() for test in tests)
        suite.addTests(tests)
    assert len(set(ids)) == len(ids) and ids_digest(ids) == expected_digest
    result = unittest.TextTestRunner(verbosity=2, resultclass=EvidenceResult).run(suite)
    require_suite_result(ids, result)
    evidence.write_text(json.dumps(dict(count=len(ids), ids=sorted(ids))), encoding='ascii')


def run_suite(name, directory, env):
    evidence = directory / (name + '.json')
    data = directory / (name + '-data')
    data.mkdir(mode=0o700)
    command = [sys.executable, '-B', '-m', 'scripts.check_core_candidate_protocols',
               '--suite-worker', name, str(evidence)]
    with subprocess.Popen(command, cwd=ROOT, env={**env, 'VUI_DATA_DIR': str(data)},
                          start_new_session=True) as child:
        try:
            assert child.wait(timeout=900) == 0, 'Candidate protocol suite failed: ' + name
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGKILL)
            child.wait(timeout=15)
            raise
    value = bounded_json(evidence)
    assert set(value) == {'count', 'ids'} and type(value['count']) is int
    classes, expected_digest = SUITES[name]
    assert value['count'] == len(value['ids']) == sum(classes.values())
    assert len(set(value['ids'])) == value['count'] and all(item.startswith(name + '.') for item in value['ids'])
    assert ids_digest(value['ids']) == expected_digest
    return dict(count=value['count'], ids_sha256=expected_digest)


def main():
    if not __debug__:
        raise SystemExit('Candidate protocol checks require assertions')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path)
    parser.add_argument('--target', choices=(TARGET,))
    parser.add_argument('--source')
    parser.add_argument('--baseline-cores', type=Path)
    parser.add_argument('--mihomo', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--suite-worker', nargs=2, help=argparse.SUPPRESS)
    args = parser.parse_args()
    assert sys.platform == 'linux' and os.geteuid() != 0
    assert os.environ['GITHUB_ACTIONS'] == 'true' and os.environ['RUNNER_ENVIRONMENT'] == 'github-hosted'
    assert os.environ['GITHUB_REPOSITORY'] == artifacts.REPOSITORY
    if args.suite_worker:
        suite_worker(args.suite_worker[0], Path(args.suite_worker[1]))
        return
    assert all((args.inputs, args.target, args.source, args.baseline_cores, args.mihomo, args.output))
    assert args.target == release_tools.target_key() and re.fullmatch('[a-f0-9]{40}', args.source)
    assert subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip() == args.source
    subprocess.run(['git', 'diff', '--quiet', 'HEAD', '--'], cwd=ROOT, check=True)
    context = dict(repository=os.environ['GITHUB_REPOSITORY'], repository_id=int(os.environ['GITHUB_REPOSITORY_ID']),
                   run_id=int(os.environ['GITHUB_RUN_ID']), run_attempt=int(os.environ['GITHUB_RUN_ATTEMPT']))
    assert context['repository_id'] == artifacts.REPOSITORY_ID
    assert context['run_id'] > 0 and context['run_attempt'] > 0
    runner_temp = Path(os.environ['RUNNER_TEMP'])
    assert args.output.parent == runner_temp and not os.path.lexists(args.output)
    assert all(runner_temp in path.parents for path in (args.inputs, args.baseline_cores, args.mihomo))
    os.umask(0o077)
    with secure_workspace(runner_temp, os.geteuid()), tempfile.TemporaryDirectory(
            prefix='vui-candidate-protocols-', dir=runner_temp) as temporary:
        directory = Path(temporary)
        cores = candidate_cores(args.inputs, directory, args.source, args.target, context)
        baseline, clients = fixed_clients(args.baseline_cores, args.mihomo)
        fixture = directory / 'fixture.json'
        fixture.write_text(json.dumps(dict(schema=1, kind='core-candidate-protocol-fixture-v1',
            source=args.source, target=args.target, context=context, baseline=str(baseline),
            cores={name: {key: row[key] for key in FIELDS} for name, row in cores.items()})), encoding='ascii')
        environment = {key: value for key, value in os.environ.items() if not key.startswith('VUI_')}
        environment.update({flag: '1' for flag in FLAGS})
        environment.update(EXPECTED_SOURCE=args.source, VUI_TEST_CANDIDATE_CONTEXT=str(fixture),
                           VUI_TEST_CORES=str(CORE_ROOT), VUI_TEST_MIHOMO=str(args.mihomo))
        with installed_candidates(directory, cores):
            suites = {name: run_suite(name, directory, environment) for name in SUITES}
        assert all(digest_file(directory/name) == row['binary_sha256'] for name, row in cores.items())
        assert digest_file(baseline) == clients['sing-box']['binary_sha256']
        assert digest_file(args.mihomo) == clients['mihomo']['binary_sha256']
    with args.output.open('x', encoding='ascii') as output:
        json.dump(dict(schema=1, kind='core-candidate-protocols-v1', scope='ci-protocol-evidence',
            published=False, qualified=False, source=args.source, target=args.target, context=context,
            cores=cores, clients=clients, suites=suites), output, sort_keys=True)
        output.write('\n')


if __name__ == '__main__':
    main()
