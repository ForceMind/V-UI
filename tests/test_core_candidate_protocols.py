"""Offline checks for the candidate protocol harness. No core or sudo executes."""
from contextlib import ExitStack, nullcontext
import ast
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from app.core_updates import artifacts, binary
from app.core_updates.selection import ComponentReference
from scripts import check_core_candidate_protocols as protocols
from tests import core_candidate_context as fixtures
from tests.core_switch_fixture_runner import RELEASE_IDS


class CandidateProtocolGateTests(unittest.TestCase):
    def run_tests(self, case):
        return unittest.TextTestRunner(stream=io.StringIO(), resultclass=protocols.EvidenceResult).run(
            unittest.defaultTestLoader.loadTestsFromTestCase(case))

    def test_success_requires_every_exact_test_id(self):
        class Valid(unittest.TestCase):
            def test_success(self):
                pass
        result = self.run_tests(Valid)
        protocols.require_suite_result(result.successful_ids, result)
        with self.assertRaises(AssertionError):
            protocols.require_suite_result(['same-count-different-method'], result)

    def test_skip_and_expected_failure_block_receipt(self):
        class Invalid(unittest.TestCase):
            @unittest.skip('fixture skip')
            def test_skip(self):
                pass
            @unittest.expectedFailure
            def test_expected_failure(self):
                self.fail()
        result = self.run_tests(Invalid)
        self.assertTrue(result.wasSuccessful())
        with self.assertRaises(AssertionError):
            protocols.require_suite_result([test.id() for test, _ in result.skipped + result.expectedFailures], result)

    def test_legacy_runtime_factory_does_not_supply_candidate_artifact(self):
        runtime = Mock()
        with patch.dict(os.environ, {}, clear=True):
            fixtures.runtime_for_fixture(runtime, 'sing-box', Path('/old/sing-box'), Path('/private/runtime'))
            self.assertEqual(fixtures.server_version('sing-box', '1.14.2'), '1.14.2')
            self.assertEqual(fixtures.singbox_client('/old/sing-box'), '/old/sing-box')
        runtime.assert_called_once_with('sing-box', Path('/old/sing-box'), Path('/private/runtime'))

    def test_candidate_context_rejects_other_attempt_and_uses_same_root_descriptor(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            config = root/'fixture.json'
            source = 'a'*40
            context = dict(repository=artifacts.REPOSITORY, repository_id=artifacts.REPOSITORY_ID,
                           run_id=42, run_attempt=2)
            materials = {}
            for core, version in (('sing-box', '1.14.3'), ('xray', '26.10.21')):
                materials[core] = dict(version=version, tag=f'core-{core}-v{version}-r1', binary_size=128,
                    binary_sha256='b'*64, pack_sha256='c'*64, pack_manifest_sha256='d'*64,
                    release_manifest_sha256='e'*64, checksums_sha256='f'*64)
            config.write_text(json.dumps(dict(schema=1, kind='core-candidate-protocol-fixture-v1',
                source=source, target='x86_64-gnu', context=context,
                baseline=str(root/'legacy/sing-box'), cores=materials)))
            config.chmod(0o600)
            env = dict(GITHUB_ACTIONS='true', RUNNER_ENVIRONMENT='github-hosted',
                GITHUB_REPOSITORY=artifacts.REPOSITORY, GITHUB_RUN_ID='42', GITHUB_RUN_ATTEMPT='1',
                EXPECTED_SOURCE=source, RUNNER_TEMP=str(root), VUI_TEST_CORES=str(fixtures.CORE_ROOT),
                VUI_TEST_CANDIDATE_CONTEXT=str(config))
            fixtures.candidate_context.cache_clear()
            self.addCleanup(fixtures.candidate_context.cache_clear)
            with patch.dict(os.environ, env, clear=True), patch.object(binary, 'open_runtime_artifact',
                    return_value=nullcontext()) as opened:
                with self.assertRaises(AssertionError):
                    fixtures.artifacts_for_fixture()
                opened.assert_not_called()
                os.environ['GITHUB_RUN_ATTEMPT'] = '2'
                candidate = fixtures.artifacts_for_fixture()['sing-box']
                self.assertIs(type(candidate.reference), ComponentReference)
                self.assertEqual((candidate.reference.release_id, candidate.owner, candidate.mode, candidate.anchor),
                                 (RELEASE_IDS['sing-box'], 0, 0o555, Path('/')))
                self.assertIs(fixtures.runtime_artifact('sing-box', fixtures.CORE_ROOT/'sing-box'), candidate)
                with self.assertRaises(AssertionError):
                    fixtures.runtime_artifact('sing-box', root/'legacy/sing-box')
                self.assertEqual(fixtures.singbox_client(str(candidate.path)), str(root/'legacy/sing-box'))
                self.assertEqual(opened.call_count, 2)

    def test_staging_passes_verified_fd_and_records_exact_cleanup_on_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp).resolve()
            payload = {name:(name+' fixture').encode() for name in ('sing-box','xray')}
            cores = {}
            for name, data in payload.items():
                path = directory/name
                path.write_bytes(data)
                path.chmod(0o555)
                cores[name] = dict(binary_sha256=hashlib.sha256(data).hexdigest(), binary_size=len(data))
            commands = []
            verified_fds = []
            hash_fd = artifacts._digest_fd

            def verify(fd, size):
                verified_fds.append(fd)
                return hash_fd(fd, size)

            def invoke(command, *, stdin, stderr, timeout):
                self.assertEqual(stderr, subprocess.PIPE)
                self.assertEqual(command[:8], ['/usr/bin/sudo','-n','/usr/bin/python3','-B','-I','-S',
                                               '-c', protocols.ROOT_STAGE])
                args = command[8:]
                commands.append(args)
                if args == ['create']:
                    return b'[4,5]'
                if args[0] == 'cleanup':
                    return b'null'
                name = args[3]
                data = payload[name]
                self.assertEqual(args[:3], ['stage','4','5'])
                self.assertEqual(stdin, verified_fds[-1])
                self.assertEqual(args[4:6], [str(os.fstat(stdin).st_dev),str(os.fstat(stdin).st_ino)])
                self.assertEqual(args[6:], [str(len(data)), hashlib.sha256(data).hexdigest()])
                self.assertEqual(os.pread(stdin, len(data), 0), data)
                self.assertNotIn(str(directory/name), command)
                return b'[6,7]' if name == 'sing-box' else b'[8,9]'

            def installed_digest(path):
                return cores[path.name]['binary_sha256']

            with patch.object(protocols, 'secure_workspace', return_value=nullcontext((4,5))), \
                    patch.object(protocols, 'digest_file', side_effect=installed_digest), \
                    patch.object(artifacts, '_digest_fd', side_effect=verify), \
                    patch.object(protocols.subprocess, 'check_output', side_effect=invoke):
                with self.assertRaisesRegex(RuntimeError, 'fixture failure'):
                    with protocols.installed_candidates(directory, cores):
                        raise RuntimeError('fixture failure')
            self.assertEqual(commands[-1], ['cleanup','4','5','sing-box','6','7','xray','8','9'])

    def test_stage_refuses_existing_directory_without_sudo(self):
        cores = {name:{} for name in ('sing-box','xray')}
        with patch.object(protocols.os.path, 'lexists', return_value=True), \
                patch.object(protocols.subprocess, 'check_output') as call:
            with self.assertRaises(AssertionError):
                with protocols.installed_candidates(Path('/private/staging'), cores):
                    self.fail()
        call.assert_not_called()

    def test_root_failure_reports_only_a_fixed_action_and_phase(self):
        hidden = 'private arguments and embedded source'
        for stderr, phase in ((b'VUI_ROOT_STAGE_FAILED:LIB_DIRECTORY\n','LIB_DIRECTORY'),
                              (b'VUI_ROOT_STAGE_FAILED:UNEXPECTED_LABEL\n','UNKNOWN'),
                              (b'VUI_ROOT_STAGE_FAILED:LIB_DIRECTORY\nprivate stderr','UNKNOWN')):
            with self.subTest(phase=phase):
                refused = subprocess.CalledProcessError(1, [hidden], output=b'private stdout', stderr=stderr)
                with patch.object(protocols.subprocess, 'check_output', side_effect=refused):
                    with self.assertRaisesRegex(RuntimeError, r'\(create/'+phase+r'\)$') as error:
                        protocols.root_stage('create')
                self.assertTrue(error.exception.__suppress_context__)
                self.assertNotIn(hidden, str(error.exception))

    def test_root_timeout_and_invalid_ack_do_not_expose_invocation_or_result(self):
        timeout = subprocess.TimeoutExpired(['private argv'], 30)
        with patch.object(protocols.subprocess, 'check_output', side_effect=timeout):
            with self.assertRaisesRegex(RuntimeError, r'\(stage/TIMEOUT\)$'):
                protocols.root_stage('stage')
        with patch.object(protocols.subprocess, 'check_output', return_value=b'["private ack", 3]'):
            with self.assertRaisesRegex(RuntimeError, r'\(stage/ACK\)$'):
                protocols.root_stage('stage')

    def test_only_candidate_xray_uses_the_documentation_target(self):
        with patch.object(fixtures, 'artifacts_for_fixture', return_value=None):
            self.assertEqual(fixtures.application_host('xray'), '127.0.0.1')
        with patch.object(fixtures, 'artifacts_for_fixture', return_value={'xray': object()}) as load:
            self.assertEqual(fixtures.application_host('sing-box'), '127.0.0.1')
            load.assert_not_called()
            self.assertEqual(fixtures.application_host('xray'), fixtures.XRAY_APPLICATION_TARGET)

    def test_application_loopback_requires_local_route_and_removes_only_added_address(self):
        host = fixtures.XRAY_APPLICATION_TARGET
        add = ['/usr/bin/sudo','-n','/usr/sbin/ip','-6','addr','add',host+'/128','dev','lo','nodad']
        remove = ['/usr/bin/sudo','-n','/usr/sbin/ip','-6','addr','del',host+'/128','dev','lo']
        for route, expected in ((f'local {host} dev lo src {host}', RuntimeError),
                                (f'local {host} dev eth0 src {host}', AssertionError),
                                (f'{host} via 2001:db8::1 dev lo', AssertionError)):
            with self.subTest(route=route), patch.object(protocols.subprocess, 'run') as call, \
                    patch.object(protocols.subprocess, 'check_output', return_value=route) as query:
                entered = False
                with self.assertRaises(expected):
                    with protocols.candidate_application_loopback():
                        entered = True
                        raise RuntimeError('inert suite failure')
                self.assertIs(entered, expected is RuntimeError)
                self.assertEqual([row.args[0] for row in call.call_args_list], [add, remove])
                query.assert_called_once_with(['/usr/sbin/ip','-6','route','get',host],text=True,timeout=10)
        occupied = subprocess.CalledProcessError(2, add)
        with patch.object(protocols.subprocess, 'run', side_effect=occupied) as call:
            with self.assertRaises(subprocess.CalledProcessError):
                with protocols.candidate_application_loopback():
                    self.fail('address add failure entered the suite')
        call.assert_called_once_with(add, check=True, timeout=10)

    def test_proxy_http_keeps_legacy_authority_and_brackets_ipv6(self):
        helpers = protocols.ROOT/'tests/loopback_helpers.py'
        tree = ast.parse(helpers.read_text(), filename=str(helpers))
        names = ('http_authority', 'http_through')
        declarations = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
        connection = Mock()
        connection.getresponse.return_value = SimpleNamespace(status=200, read=lambda:b'inert body')
        http = SimpleNamespace(client=SimpleNamespace(HTTPConnection=Mock(return_value=connection)))
        namespace = {'http':http}
        exec(compile(ast.Module(body=declarations,type_ignores=[]),str(helpers),'exec'), namespace)
        for host, authority in (('127.0.0.1','127.0.0.1:4567'), ('example.test','example.test:4567'),
                                (fixtures.XRAY_APPLICATION_TARGET, '['+fixtures.XRAY_APPLICATION_TARGET+']:4567')):
            with self.subTest(host=host):
                connection.reset_mock()
                self.assertEqual(namespace['http_through'](1234, host, 4567), (200,b'inert body'))
                connection.request.assert_called_once_with('GET','http://'+authority+'/probe',
                                                           headers={'Connection':'close'})
                connection.close.assert_called_once_with()

    def test_xray_private_control_rejects_timeout_stale_reason_and_target_delivery(self):
        cases = (('fresh', True), ('timeout', False), ('stale', False), ('delivered', False))
        fixture = protocols.ROOT/'tests/test_xray_share_loopback.py'
        helpers = protocols.ROOT/'tests/loopback_helpers.py'

        def declarations(path, names):
            tree = ast.parse(path.read_text(), filename=str(path))
            return [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name in names]

        functions = declarations(fixture, {'require_private_target_rejection'})
        functions += declarations(helpers, {'http_authority', 'require_rejection_evidence'})
        for outcome, accepted in cases:
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                client_log, server_log = root/'client.log', root/'xray.log'
                client_log.write_text('')
                reason = 'freedom: blocked target: tcp:127.0.0.1:4567\n'
                server_log.write_text(reason if outcome == 'stale' else 'Xray ready\n')
                deliveries = []
                control = Mock()
                control.request.side_effect = lambda _, path: deliveries.append(('127.0.0.1:4567', path))
                control.getresponse.return_value = SimpleNamespace(status=200, read=lambda:b'VUI-LOOPBACK-TARGET')

                def attempt(proxy, host, port):
                    self.assertEqual((proxy, host, port), (1234, '127.0.0.1', 4567))
                    if outcome in ('fresh', 'delivered'):
                        with server_log.open('a') as log:
                            log.write(reason)
                    if outcome == 'delivered':
                        deliveries.append(('127.0.0.1:4567', '/probe'))
                    raise TimeoutError('inert request')

                namespace = dict(ExitStack=ExitStack, start_http_target=lambda *_:(4567,deliveries),
                    http=SimpleNamespace(client=SimpleNamespace(HTTPConnection=Mock(return_value=control),
                                                               HTTPException=RuntimeError)),
                    http_through=attempt,
                    time=SimpleNamespace(monotonic=Mock(side_effect=[0,0,13]), sleep=Mock()), print=Mock())
                exec(compile(ast.Module(body=functions,type_ignores=[]),str(fixture),'exec'), namespace)
                check = namespace['require_private_target_rejection']
                client = Mock()
                if accepted:
                    check(self, 1234, client, client_log, server_log)
                    client.stop.assert_called_once_with()
                else:
                    with self.assertRaises(AssertionError):
                        check(self, 1234, client, client_log, server_log)
                    client.stop.assert_not_called()

    def test_workspace_rechecks_identity_even_when_body_raises(self):
        def directory(inode):
            return SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=0, st_gid=0,
                                   st_dev=4, st_ino=inode)

        with patch.object(protocols.os, 'open', side_effect=[10,11]), \
                patch.object(protocols.os, 'fstat', side_effect=lambda fd:directory(fd)), \
                patch.object(protocols.os, 'stat', return_value=directory(12)) as named, \
                patch.object(protocols.os, 'close'):
            with self.assertRaises(AssertionError) as error:
                with protocols.secure_workspace(Path('/fixture'), 0):
                    raise RuntimeError('operation failed before directory rebound was noticed')
        named.assert_called_once_with('fixture', dir_fd=10, follow_symlinks=False)
        self.assertIsInstance(error.exception.__context__, RuntimeError)

    def test_exported_client_check_and_launch_use_baseline_binary(self):
        # Execute the actual fixture methods with inert process and export
        # doubles. This also keeps the test independent of app/HTTP dependencies.
        roles = {'vmess':'start_singbox_client','trojan':'start_singbox_client',
                 'shadowsocks':'start_singbox_client','vless_ws':'start_client'}
        candidate = str(fixtures.CORE_ROOT/'sing-box')
        old = '/pinned/sing-box'
        for protocol, method in roles.items():
            with self.subTest(protocol=protocol):
                path = protocols.ROOT/'tests'/f'test_{protocol}_loopback.py'
                tree = ast.parse(path.read_text(), filename=str(path))
                node, = [item for item in ast.walk(tree)
                         if isinstance(item, ast.FunctionDef) and item.name == method]
                process = Mock()
                fixture = SimpleNamespace(root=Path('/private'), singbox=candidate, stack=object(), env={},
                    server=SimpleNamespace(process=object()), start_server=Mock(),
                    assert_uri=Mock(), check_command=Mock(),
                    exported_singbox=Mock(return_value=Path('/private/client.json')))
                client = Mock(return_value=old)
                namespace = dict(os=os, Path=Path, unused_port=lambda:1234, CoreProcess=process,
                                 singbox_client=client)
                exec(compile(ast.Module(body=[node],type_ignores=[]),str(path),'exec'), namespace)
                args = ('singbox', {}) if protocol == 'vless_ws' else (object(),)
                with patch.dict(os.environ, {'VUI_TEST_CORES':str(fixtures.CORE_ROOT)}):
                    namespace[method](fixture, *args)
                client.assert_called_once_with(candidate)
                self.assertEqual(process.call_args.args[1], [old,'run','-c','/private/client.json'])
                if protocol == 'vless_ws':
                    self.assertEqual(fixture.check_command.call_args.args[0], [old,'check','-c','/private/client.json'])


class CandidateRootStageTests(unittest.TestCase):
    def setUp(self):
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name).resolve()
        self.namespace = {'__name__':'inert_root_stage'}
        exec(compile(protocols.ROOT_STAGE, '<fixed-root-stage>', 'exec'), self.namespace)
        self.namespace['ROOT_UID'] = os.geteuid()
        self.namespace['ROOT_GID'] = os.getegid()
        self.parent = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close,self.parent)
        self.path = self.root/self.namespace['DIRECTORY']
        self.path.mkdir(mode=0o700)
        self.directory = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close,self.directory)
        self.identity = self.namespace['identity']
        self.expected = self.identity(os.fstat(self.directory))
        self.source = self.root/'runner-binary'
        self.data = b'inert verified binary bytes'
        self.source.write_bytes(self.data)
        self.source.chmod(0o555)
        self.fd = os.open(self.source, os.O_RDONLY | os.O_NOFOLLOW)
        self.addCleanup(os.close,self.fd)

    def stage(self, digest=None):
        return self.namespace['stage'](self.directory, self.stable, 'sing-box', self.fd,
            self.identity(os.fstat(self.fd)), os.geteuid(), len(self.data),
            digest or hashlib.sha256(self.data).hexdigest())

    def stable(self):
        self.namespace['directory_entry'](self.parent,self.path.name,self.expected)

    def cleanup(self, expected, known):
        self.namespace['cleanup'](self.parent,expected,known)

    def test_fixed_parent_walks_var_lib_and_rejects_unsafe_ancestors(self):
        self.assertEqual(fixtures.CORE_ROOT, Path('/var/lib')/self.namespace['DIRECTORY'])
        var = self.root/'var'
        lib = var/'lib'
        var.mkdir(mode=0o700)
        lib.mkdir(mode=0o700)
        with self.namespace['fixed_parent'](self.parent) as fd:
            self.assertTrue(os.path.samestat(os.fstat(fd), lib.stat()))
        var.chmod(0o770)
        with self.assertRaises(AssertionError):
            with self.namespace['fixed_parent'](self.parent):
                self.fail('group writable var accepted')
        self.assertEqual(self.namespace['PHASE'], 'VAR_DIRECTORY')
        var.chmod(0o700)
        lib.rename(var/'real-lib')
        lib.symlink_to(var/'real-lib')
        with self.assertRaises(OSError):
            with self.namespace['fixed_parent'](self.parent):
                self.fail('symlink lib accepted')
        self.assertEqual(self.namespace['PHASE'], 'LIB_DIRECTORY')

    def test_rebound_source_path_is_not_read_by_root(self):
        self.source.rename(self.root/'original')
        self.source.write_bytes(b'outside the verified FD')
        installed = self.stage()
        self.assertEqual((self.path/'sing-box').read_bytes(), self.data)
        self.assertEqual((self.path/'sing-box').stat().st_mode & 0o777, 0o555)
        self.cleanup(self.expected, {'sing-box':installed})
        self.assertFalse(self.path.exists())

    def test_bad_digest_never_publishes_and_removes_only_own_temporary_leaf(self):
        with self.assertRaises(AssertionError):
            self.stage('0'*64)
        self.assertEqual(list(self.path.iterdir()), [])
        self.cleanup(self.expected, {})

    def test_existing_destination_is_preserved_and_unrelated_leaf_blocks_cleanup(self):
        foreign = self.path/'sing-box'
        foreign.write_bytes(b'unrelated')
        with self.assertRaises(FileExistsError):
            self.stage()
        self.assertEqual(list(self.path.iterdir()), [foreign])
        with self.assertRaises(AssertionError):
            self.cleanup(self.expected, {})
        self.assertEqual(foreign.read_bytes(), b'unrelated')

    def test_replaced_directory_and_leaf_are_never_removed(self):
        installed = self.stage()
        original = self.root/'original-directory'
        self.path.rename(original)
        self.path.mkdir(mode=0o700)
        with self.assertRaises(AssertionError):
            self.cleanup(self.expected, {'sing-box':installed})
        self.assertTrue(self.path.exists())
        self.assertEqual((original/'sing-box').read_bytes(), self.data)
        self.path.rmdir()
        original.rename(self.path)
        (self.path/'sing-box').rename(self.root/'original-leaf')
        foreign = self.path/'sing-box'
        foreign.write_bytes(b'unknown same-name replacement')
        foreign.chmod(0o555)
        with self.assertRaises(AssertionError):
            self.cleanup(self.expected, {'sing-box':installed})
        self.assertEqual(foreign.read_bytes(), b'unknown same-name replacement')


if __name__ == '__main__':
    unittest.main()
