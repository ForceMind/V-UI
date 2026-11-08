"""Temporary fake material and AST startup wiring; no core/process/service run."""
import argparse
import ast
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from app.core_updates import boot as b, selection as s, selection_files as sf, store as fs
from app.core_updates import artifacts as a, catalog as c, github as g
from app.core_updates.application import bundle_artifacts
from test_core_artifacts import APP, BUILD, Fixture
from test_core_github import Transport
from test_core_manager_artifacts import load_manager

VERSIONS = {'sing-box': '1.14.2', 'xray': '26.3.27'}
SOURCE = Path(__file__).resolve().parents[1] / 'app/serve.py'


class CoreBootTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='vui-core-boot-')
        self.addCleanup(temp.cleanup)
        self.anchor = Path(temp.name).resolve()
        self.payload = self.anchor / 'release' / 'payload'
        self.data = self.anchor / 'data'
        self.data.mkdir(mode=0o700)
        self.meta_root = self.anchor / 'metadata'
        self.meta_root.mkdir(mode=0o755)
        # Match the actual root metadata contract regardless of a previous
        # test's owner-only umask; do not weaken the production mode check.
        self.meta_root.chmod(0o755)
        self.files = sf.SelectionFiles(sf.Layout(self.meta_root, os.geteuid(), self.anchor))
        files = {}
        for core in s.CORES:
            path = self.payload / 'cores' / 'x86_64' / core
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            raw = ('fake bundle ' + core).encode()
            path.write_bytes(raw)
            path.chmod(0o700)
            files['cores/x86_64/' + core] = dict(sha256=hashlib.sha256(raw).hexdigest(),
                                                size=len(raw), mode=0o700)
        self.manifest = dict(schema=1, kind='release', platform='linux-multi-cpython312',
                             source_commit=APP, targets=['x86_64-gnu'], files=files)
        self.ready = dict(runtime_key='x86_64-gnu', manifest_sha256='d' * 64)
        self.bundles = bundle_artifacts(self.payload, self.manifest, self.ready, versions=VERSIONS,
                                        owner=os.geteuid(), anchor=self.anchor)
        self.initial = s.initial_selection(app_source=APP, target='x86_64-gnu',
                                           bundles={core: value.reference for core, value in self.bundles.items()})
        self.manager = types.SimpleNamespace(configure_artifacts=Mock())
        self.context = dict(app_source=APP, target='x86_64-gnu')
        self.config = b'{"inbounds":[]}'
        self.revision = hashlib.sha256(self.config).hexdigest()

    def run_boot(self, **kwargs):
        values = dict(payload=self.payload, manifest=self.manifest, ready=self.ready,
                      data=self.data, versions=VERSIONS, selection_files=self.files,
                      data_anchor=self.anchor, bundle_anchor=self.anchor)
        values.update(kwargs)
        return b.configure_boot(self.manager, **values)

    def state(self, core, value):
        (self.data / 'runtime').mkdir(mode=0o700, exist_ok=True)
        directory = self.data / 'runtime' / core
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        (directory / (self.revision + '.json')).write_bytes(self.config)
        (directory / (self.revision + '.json')).chmod(0o600)
        path = directory / 'state.json'
        path.write_bytes(json.dumps(value).encode())
        path.chmod(0o600)
        return path

    def modern(self, state=None):
        state = self.initial if state is None else state
        for core in s.CORES:
            self.state(core, dict(schema=2, revision=self.revision, enabled=False,
                                 artifact_id=s.reference_identity(state.slot(core).current)))

    def confirmed(self, state=None):
        state = self.initial if state is None else state
        (self.meta_root / sf.SELECTION).write_bytes(s.encode_selection(state))
        (self.meta_root / sf.SELECTION).chmod(0o644)
        (self.meta_root / sf.FENCE).write_bytes(sf._raw(sf._fence_for(state, 'f' * 32, settled=True)))
        (self.meta_root / sf.FENCE).chmod(0o644)

    def test_uninitialized_missing_or_legacy_two_key_state_preserves_three_argument_runtime(self):
        self.assertIsNone(self.run_boot())
        self.state('sing-box', dict(revision=self.revision, enabled=True))
        self.assertIsNone(self.run_boot())
        self.manager.configure_artifacts.assert_not_called()
        module, runtime = load_manager()
        self.manager = module.CoreManager()
        self.assertIsNone(self.run_boot())
        legacy = self.manager.get('sing-box').runtime
        self.assertEqual(legacy.args, ('sing-box', Path('/legacy/sing-box'), Path('/data/runtime/sing-box')))
        self.assertEqual(legacy.kwargs, {})

    def test_entire_root_absent_is_readonly_legacy_and_not_a_caught_stable_error(self):
        self.meta_root.rmdir()
        with patch.object(sf.os, 'write') as write:
            self.assertIsNone(self.run_boot())
        write.assert_not_called()
        self.assertFalse(self.meta_root.exists())
        with patch.object(sf.SelectionFiles, 'read_boot', side_effect=sf.CoreSelectionFilesError('METADATA_IO')):
            with self.assertRaisesRegex(b.CoreBootError, 'CORE_SELECTION_UNAVAILABLE'):
                self.run_boot()
        self.manager.configure_artifacts.assert_not_called()

    def test_schema2_without_root_choice_rejected_and_never_bootstrapped(self):
        self.modern()
        with self.assertRaisesRegex(b.CoreBootError, 'CORE_SELECTION_REQUIRED'):
            self.run_boot()
        self.manager.configure_artifacts.assert_not_called()
        self.assertEqual(list(self.meta_root.iterdir()), [])

    def test_unknown_legacy_extra_duplicate_json_or_damaged_state_rejected(self):
        for value in (dict(schema=1, revision=self.revision, enabled=False),
                      dict(revision=self.revision, enabled=False, extra=True),
                      dict(revision=None, enabled=False), dict(revision=self.revision, enabled=0)):
            with self.subTest(value=value):
                self.state('sing-box', value)
                with self.assertRaises(b.CoreBootError): self.run_boot()
        path = self.state('sing-box', dict(revision=self.revision, enabled=False))
        path.write_bytes(b'{"enabled":false,"enabled":false}')
        with self.assertRaises(b.CoreBootError): self.run_boot()
        path.write_bytes(b' ' * 4097)
        with self.assertRaises(b.CoreBootError): self.run_boot()
        self.manager.configure_artifacts.assert_not_called()

    def test_state_symlink_hardlink_mode_and_config_hash_are_checked_without_repair(self):
        path = self.state('sing-box', dict(revision=self.revision, enabled=False))
        raw = path.read_bytes()
        for mode in (0o644, 0o666):
            path.chmod(mode)
            with self.subTest(mode=mode), self.assertRaises(b.CoreBootError): self.run_boot()
            self.assertEqual(path.stat().st_mode & 0o777, mode)
        path.chmod(0o600)
        link = self.anchor / 'hardlink'
        os.link(path, link)
        with self.assertRaises(b.CoreBootError): self.run_boot()
        link.unlink()
        path.unlink()
        target = self.anchor / 'state-source'
        target.write_bytes(raw)
        target.chmod(0o600)
        path.symlink_to(target)
        with self.assertRaises(b.CoreBootError): self.run_boot()
        path.unlink()
        self.state('sing-box', dict(revision=self.revision, enabled=False))
        config_path = path.parent / (self.revision + '.json')
        config_path.write_bytes(b'changed private config')
        with self.assertRaisesRegex(b.CoreBootError, 'RUNTIME_STATE_DAMAGED'): self.run_boot()
        self.assertEqual(config_path.read_bytes(), b'changed private config')

    def test_each_root_trace_and_corruption_refuse_no_fallback(self):
        for name in (sf.LOCK, sf.HISTORY, sf.PENDING, sf.ACTIVE, sf.FENCE, sf.SELECTION):
            with self.subTest(name=name):
                path = self.meta_root / name
                if name in (sf.HISTORY, sf.ACTIVE): path.mkdir(mode=0o700)
                else:
                    path.write_bytes(b'{}')
                    path.chmod(0o600 if name == sf.LOCK else 0o644)
                with self.assertRaisesRegex(b.CoreBootError, 'CORE_SELECTION_UNAVAILABLE'): self.run_boot()
                if path.is_dir(): path.rmdir()
                else: path.unlink()
        self.manager.configure_artifacts.assert_not_called()

    def test_confirmed_bundle_snapshot_only_configures_complete_map_once_without_runtime_construction(self):
        self.confirmed()
        self.modern()
        snapshot = self.run_boot()
        self.assertEqual(set(snapshot), set(s.CORES))
        self.assertEqual(snapshot, self.bundles)
        self.manager.configure_artifacts.assert_called_once_with(snapshot)

    def test_confirmed_missing_unused_core_keeps_stopped_and_legacy_state_requires_migration(self):
        self.confirmed()
        self.assertEqual(self.run_boot(), self.bundles)
        self.manager.configure_artifacts.reset_mock()
        self.state('sing-box', dict(revision=self.revision, enabled=False))
        with self.assertRaisesRegex(b.CoreBootError, 'RUNTIME_SELECTION_MISMATCH'): self.run_boot()
        self.manager.configure_artifacts.assert_not_called()
        self.modern()
        self.state('xray', dict(schema=2, revision=self.revision, enabled=False, artifact_id='0' * 64))
        with self.assertRaisesRegex(b.CoreBootError, 'RUNTIME_SELECTION_MISMATCH'): self.run_boot()

    def test_material_missing_or_changed_after_one_resolution_never_partially_configures(self):
        self.confirmed()
        self.modern()
        self.bundles['xray'].path.write_bytes(b'changed bytes')
        with self.assertRaisesRegex(b.CoreBootError, 'CORE_MATERIAL_UNAVAILABLE'): self.run_boot()
        self.manager.configure_artifacts.assert_not_called()

    def test_manager_late_configuration_refusal_is_fixed_startup_failure_not_fallback(self):
        self.confirmed()
        self.modern()
        self.manager.configure_artifacts.side_effect = RuntimeError('synthetic late runtime cache')
        with self.assertRaisesRegex(b.CoreBootError, '^CORE_MANAGER_CONFIGURATION_REFUSED$'):
            self.run_boot()
        self.manager.configure_artifacts.assert_called_once()
        self.assertEqual(self.files.read_boot(**self.context), self.initial)

    def test_selection_race_during_scan_refuses_instead_of_using_legacy_or_old_snapshot(self):
        read_boot = sf.SelectionFiles.read_boot
        reads = 0
        def barrier(files, **kwargs):
            nonlocal reads
            reads += 1
            if reads == 2:
                path = self.meta_root / sf.PENDING
                path.write_bytes(b'{}')
                path.chmod(0o644)
            return read_boot(files, **kwargs)
        with patch.object(sf.SelectionFiles, 'read_boot', new=barrier):
            with self.assertRaisesRegex(b.CoreBootError, 'CORE_SELECTION_UNAVAILABLE'): self.run_boot()
        self.assertTrue((self.meta_root / sf.PENDING).exists())
        self.manager.configure_artifacts.assert_not_called()

    def test_root_symlink_or_unsettled_or_mismatched_fence_is_not_uninitialized(self):
        self.meta_root.rmdir()
        elsewhere = self.anchor / 'foreign-metadata'
        elsewhere.mkdir(mode=0o755)
        self.meta_root.symlink_to(elsewhere, target_is_directory=True)
        with self.assertRaisesRegex(b.CoreBootError, 'CORE_SELECTION_UNAVAILABLE'): self.run_boot()
        self.assertTrue(self.meta_root.is_symlink())
        self.meta_root.unlink()
        self.meta_root.mkdir(mode=0o755)
        self.confirmed()
        fence = self.meta_root / sf.FENCE
        original = fence.read_bytes()
        for changes in (dict(settled=False), dict(highest_generation=1), dict(confirmed_revision='0' * 64)):
            value = json.loads(original)
            value.update(changes)
            fence.write_bytes(sf._raw(value))
            with self.subTest(changes=changes), self.assertRaisesRegex(b.CoreBootError, 'CORE_SELECTION_UNAVAILABLE'):
                self.run_boot()
        self.manager.configure_artifacts.assert_not_called()

    def test_missing_root_appearing_during_absence_check_refuses_legacy(self):
        self.meta_root.rmdir()
        exists = sf._exists
        fired = False
        def appear(parent, name):
            nonlocal fired
            if name == self.meta_root.name and not fired:
                fired = True
                self.meta_root.mkdir(mode=0o755)
                (self.meta_root / sf.PENDING).write_bytes(b'{}')
                (self.meta_root / sf.PENDING).chmod(0o644)
            return exists(parent, name)
        with patch.object(sf, '_exists', side_effect=appear):
            with self.assertRaisesRegex(b.CoreBootError, 'CORE_SELECTION_UNAVAILABLE'): self.run_boot()
        self.assertTrue(fired)
        self.assertTrue((self.meta_root / sf.PENDING).exists())
        self.manager.configure_artifacts.assert_not_called()

    def test_read_boot_close_error_attempts_every_owned_fd_and_never_means_legacy(self):
        open_fd, close = os.open, os.close
        opened, closed = [], []
        def observe_open(*args, **kwargs):
            fd = open_fd(*args, **kwargs)
            opened.append(fd)
            return fd
        def fail_after_close(fd):
            close(fd)
            closed.append(fd)
            if len(closed) == 1:
                raise OSError('synthetic close acknowledgement lost')
        with patch.object(sf.os, 'open', side_effect=observe_open), patch.object(sf.os, 'close', side_effect=fail_after_close):
            with self.assertRaisesRegex(b.CoreBootError, 'CORE_SELECTION_UNAVAILABLE'): self.run_boot()
        self.assertEqual(sorted(opened), sorted(closed))
        for fd in opened:
            with self.assertRaises(OSError): os.fstat(fd)
        self.assertEqual(list(self.meta_root.iterdir()), [])
        self.manager.configure_artifacts.assert_not_called()

    def test_unsafe_runtime_parent_and_wrong_owner_are_refused_without_creating_state(self):
        foreign = self.anchor / 'foreign-runtime'
        foreign.mkdir(mode=0o700)
        (self.data / 'runtime').symlink_to(foreign, target_is_directory=True)
        with self.assertRaisesRegex(b.CoreBootError, 'RUNTIME_STATE_UNAVAILABLE'): self.run_boot()
        self.assertTrue((self.data / 'runtime').is_symlink())
        (self.data / 'runtime').unlink()
        with self.assertRaisesRegex(b.CoreBootError, 'RUNTIME_STATE_UNAVAILABLE'):
            self.run_boot(owner=os.geteuid() + 1)
        self.assertEqual(list(self.data.iterdir()), [])
        self.manager.configure_artifacts.assert_not_called()

    def test_component_and_bundle_share_exact_offline_resolver_snapshot(self):
        fixture = Fixture('sing-box', '1.14.3')
        transport = Transport(fixture)
        client = g.GitHubCoreClient(opener=transport)
        candidate = client.inspect(31, app_source=APP)
        catalog = c.CoreCatalog(fs.Layout(self.anchor / 'catalog', os.geteuid(), self.anchor))
        store = fs.CoreArtifactStore(fs.Layout(self.anchor / 'store', os.geteuid(), self.anchor))
        catalog.record(candidate, app_source=APP, client=client)
        pack = self.anchor / 'pack.zip'
        pack.write_bytes(fixture.pack_bytes['x86_64-gnu'])
        material = store.prepare(pack, candidate.manifest, candidate.checksums,
            authority=candidate.authority, expected_tag=fixture.tag, expected_builder_source=BUILD,
            app_source=APP, target='x86_64-gnu')
        state = s.plan_component(self.initial, core='sing-box', identity=material.identity,
                                 expected_revision=self.initial.revision).after
        self.confirmed(state)
        self.modern(state)
        calls = list(transport.calls)
        snapshot = self.run_boot(catalog=catalog, store=store)
        self.assertEqual(snapshot['sing-box'].reference, s.component_reference(material.identity))
        self.assertEqual(snapshot['xray'], self.bundles['xray'])
        self.assertEqual(transport.calls, calls)
        transport.closed(self)
        self.manager.configure_artifacts.assert_called_once_with(snapshot)
        self.manager.configure_artifacts.reset_mock()
        missing = c.CoreCatalog(fs.Layout(self.anchor / 'missing-catalog', os.geteuid(), self.anchor))
        with self.assertRaisesRegex(b.CoreBootError, 'CORE_MATERIAL_UNAVAILABLE'):
            self.run_boot(catalog=missing, store=store)
        self.manager.configure_artifacts.assert_not_called()


class ServeBootOrderTests(unittest.TestCase):
    def run_serve(self, *, boot_error=None):
        tree = ast.parse(SOURCE.read_text(), filename=str(SOURCE))
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'run')
        events = []
        with tempfile.TemporaryDirectory(prefix='vui-serve-order-') as directory:
            root = Path(directory).resolve()
            (root / 'data').mkdir(mode=0o700)
            release = root / 'release'
            payload = release / 'payload'
            (payload / 'app').mkdir(parents=True)
            manager = object()
            versions = dict(VERSIONS)
            manifest = {'observed': 'full payload verified once'}
            ready = {'observed': 'runtime verified once'}
            certificate = types.SimpleNamespace(panel_material=lambda: ('fixture-cert', 'fixture-key'))
            def configure(value, **kwargs):
                events.append('configure')
                self.assertIs(value, manager)
                self.assertIs(kwargs['manifest'], manifest)
                self.assertIs(kwargs['ready'], ready)
                self.assertIs(kwargs['versions'], versions)
                self.assertEqual(kwargs['data'], root / 'data')
                self.assertEqual(os.environ['VUI_BIN_DIR'], str(payload / 'cores/x86_64'))
                if boot_error:
                    raise boot_error
            @contextmanager
            def held(*args):
                events.append('lease-enter')
                yield
                events.append('lease-exit')
            def active(root_value):
                events.append('active-verified')
                self.assertEqual(root_value, root)
                return release, {'release_id': 'fixture'}, manifest, ready
            modules = {
                'app.middleware.auth': types.SimpleNamespace(configured_origin=lambda: 'https://localhost:8443'),
                'app.core_updates.boot': types.SimpleNamespace(configure_boot=configure),
                'app.services.core_manager': types.SimpleNamespace(core_manager=manager),
                'app.services.core_runtime': types.SimpleNamespace(VERSIONS=versions),
                'app.certificates.manager': types.SimpleNamespace(get_manager=lambda: certificate),
                'app.models.database': types.SimpleNamespace(init_db=lambda: events.append('init-db')),
                'app.certificates.tls': types.SimpleNamespace(LiveTLS=lambda *args: types.SimpleNamespace(
                    reload=lambda: None, context=object())),
                'uvicorn': types.SimpleNamespace(run=lambda *args, **kwargs: events.append('uvicorn-run')),
                'main': types.SimpleNamespace(app=object())}
            importer = __import__
            def fake_import(name, *args, **kwargs):
                if name in modules:
                    events.append('import:' + name)
                    return modules[name]
                return importer(name, *args, **kwargs)
            builtins = dict(vars(__import__('builtins')), __import__=fake_import,
                            print=lambda *args, **kwargs: events.append('startup-message'))
            fake_os = types.SimpleNamespace(umask=lambda value: None, path=os.path,
                                            environ=os.environ, geteuid=os.geteuid)
            namespace = dict(argparse=argparse, Path=Path, os=fake_os, ipaddress=__import__('ipaddress'),
                urlsplit=__import__('urllib.parse', fromlist=['urlsplit']).urlsplit,
                supported_environment=lambda: None, private_root=lambda value: root,
                lease=held, data_owner=held, active_verified=active, target_arch=lambda: 'x86_64',
                private_file=lambda value: Path(value), ReleaseError=ValueError,
                __file__=str(payload / 'app/serve.py'), __builtins__=builtins)
            exec(compile(ast.Module(body=[function], type_ignores=[]), str(SOURCE), 'exec'), namespace)
            with patch.dict(os.environ, {}, clear=False), patch.object(sys, 'argv',
                    ['serve', '--root', str(root), '--origin', 'https://localhost:8443']):
                if boot_error:
                    with self.assertRaises(type(boot_error)):
                        namespace['run']()
                else:
                    namespace['run']()
            return events

    def test_original_cli_uses_one_verified_pass_and_configures_before_db_certificates_main(self):
        events = self.run_serve()
        self.assertEqual(events.count('active-verified'), 1)
        configure = events.index('configure')
        self.assertEqual(events[:configure].count('lease-enter'), 2)
        self.assertNotIn('lease-exit', events[:configure])
        for name in ('import:app.certificates.manager', 'import:app.models.database', 'init-db',
                     'import:main', 'uvicorn-run'):
            self.assertLess(configure, events.index(name))

    def test_boot_failure_prevents_db_certificate_main_or_runtime_start(self):
        events = self.run_serve(boot_error=b.CoreBootError('CORE_SELECTION_UNAVAILABLE'))
        self.assertIn('configure', events)
        self.assertNotIn('init-db', events)
        self.assertNotIn('import:app.certificates.manager', events)
        self.assertNotIn('import:main', events)
        self.assertNotIn('uvicorn-run', events)

    def test_serve_entry_imports_verified_helper_without_changing_cli(self):
        tree = ast.parse(SOURCE.read_text())
        imports = [node for node in tree.body if isinstance(node, ast.ImportFrom)
                   and node.module == 'app.release_tools']
        self.assertEqual(len(imports), 1)
        names = {value.name for value in imports[0].names}
        self.assertIn('active_verified', names)
        self.assertNotIn('active', names)
        self.assertFalse(any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                             and node.func.attr == 'bootstrap' for node in ast.walk(tree)))


if __name__ == '__main__':
    unittest.main()
