"""Pure fake ZIPs and temporary files; never build or execute a product package."""
from __future__ import annotations
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import warnings
import zipfile

from scripts import release_acceptance as acceptance
from scripts.build_release_kit import SOURCE_ASSETS

SHA = 'a' * 40
VERSION = '0.4.22'
ARTIFACT = 'sha256:' + 'c' * 64


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def zipped(entries, *, comment=b'', force_zip64=False):
    stream = io.BytesIO()
    with warnings.catch_warnings(), zipfile.ZipFile(stream, 'w') as archive:
        warnings.simplefilter('ignore', UserWarning)
        archive.comment = comment
        for name, content in entries:
            entry = name if isinstance(name, zipfile.ZipInfo) else zipfile.ZipInfo(name)
            if force_zip64:
                with archive.open(entry, 'w', force_zip64=True) as output:
                    output.write(content)
            else:
                archive.writestr(entry, content)
    return stream.getvalue()


class KitTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='vui-acceptance-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.kit = self.root / 'kit'
        self.kit.mkdir()
        self.controls = {name: ('fake inert ' + source + '\n').encode()
                         for source, name in (*SOURCE_ASSETS, ('docs/RELEASE_NOTES.md', 'RELEASE_NOTES.md'))}
        self.manifest = {'schema': 1, 'kind': 'release', 'version': VERSION, 'source_commit': SHA,
                         'release_id': VERSION + '-' + SHA[:12], 'platform': acceptance.PLATFORM,
                         'targets': [acceptance.TARGET], 'files': {},
                         'python_runtime_version': 'fake-runtime-never-executed'}
        self.record = {key: self.manifest[key] for key in
                       ('schema', 'version', 'source_commit', 'release_id', 'platform', 'targets')}
        for name, raw in self.controls.items():
            (self.kit / name).write_bytes(raw)
        self.write_bundle()
        self.write_source()
        (self.kit / 'RELEASE.json').write_text(json.dumps(self.record))
        self.rehash()

    def write_bundle(self, *, raw=None, entries=None, force_zip64=False):
        (self.kit / acceptance.BUNDLE).write_bytes(raw if raw is not None else zipped(
            entries if entries is not None else [('MANIFEST.json', json.dumps(self.manifest).encode())],
            force_zip64=force_zip64))

    def write_source(self, *, comment=None, extra=(), overrides=None, force_zip64=False):
        entries = [('scripts/', b''), ('deploy/', b''), ('docs/', b'')]
        for name, source in acceptance.SOURCE_MEMBERS.items():
            entries.append((source, (overrides or {}).get(name, (self.kit / name).read_bytes())))
        entries.extend(extra)
        (self.kit / 'vui-source.zip').write_bytes(zipped(entries, comment=SHA.encode() if comment is None else comment,
                                                       force_zip64=force_zip64))

    def rehash(self, *, raw=None):
        if raw is None:
            raw = ''.join(digest((self.kit / name).read_bytes()) + '  ' + name + '\n'
                          for name in sorted(acceptance.KIT_FILES - {'SHA256SUMS'})).encode()
        (self.kit / 'SHA256SUMS').write_bytes(raw)
        self.anchor = digest(raw)

    def verify(self, **changes):
        args = {'expected_source': SHA, 'candidate_version': VERSION, 'expected_checksums_sha256': self.anchor}
        args.update(changes)
        return acceptance.verify_kit(self.kit, **args)

    def test_real_producer_member_set_and_valid_fake_kit_are_immutable(self):
        self.assertEqual(len(acceptance.KIT_FILES), 16)
        self.assertEqual(acceptance.SOURCE_MEMBERS,
                         {name: source for source, name in (*SOURCE_ASSETS, ('docs/RELEASE_NOTES.md', 'RELEASE_NOTES.md'))})
        before = {name: (self.kit / name).read_bytes() for name in acceptance.KIT_FILES}
        with patch('subprocess.Popen', side_effect=AssertionError('must not execute')):
            result = self.verify()
        self.assertEqual(result['validation_scope'], 'consumer-files')
        self.assertEqual(result['checksums_sha256'], self.anchor)
        self.assertEqual(result['bundle_sha256'], digest(before[acceptance.BUNDLE]))
        self.assertNotIn('success', result)
        self.assertEqual(before, {name: (self.kit / name).read_bytes() for name in acceptance.KIT_FILES})

    def test_external_anchor_required_and_never_inferred_for_consumer(self):
        for value in (None, '', 'A' * 64, '0' * 64, True):
            with self.subTest(value=value), self.assertRaises(acceptance.ReleaseAcceptanceError):
                self.verify(expected_checksums_sha256=value)
        with self.assertRaises(TypeError):
            acceptance.verify_kit(self.kit, expected_source=SHA, candidate_version=VERSION)
        inspected = acceptance.inspect_producer_kit(self.kit, expected_source=SHA, candidate_version=VERSION)
        self.assertEqual(inspected['validation_scope'], 'producer-inspection')
        self.assertEqual(inspected['checksums_sha256'], self.anchor)

    def test_external_bundle_digest_is_checked_before_any_copy(self):
        output = self.root / 'not-created.zip'
        for invalid in ('0' * 64, 'A' * 64, True):
            with self.subTest(invalid=invalid), self.assertRaises(acceptance.ReleaseAcceptanceError):
                self.verify(expected_bundle_sha256=invalid, bundle_output=output)
            self.assertFalse(output.exists())
        self.verify(expected_bundle_sha256=digest((self.kit / acceptance.BUNDLE).read_bytes()))

    def test_input_source_and_canonical_stable_version(self):
        for source in ('b' * 40, 'A' * 40, 'a' * 39, None, True):
            with self.subTest(source=source), self.assertRaises(acceptance.ReleaseAcceptanceError):
                self.verify(expected_source=source)
        for version in ('0.4.23', 'v0.4.22', '00.4.22', '0.04.22', '0.4.22-rc.1', 'latest', True, None):
            with self.subTest(version=version), self.assertRaises(acceptance.ReleaseAcceptanceError):
                self.verify(candidate_version=version)

    def test_changed_control_bytes_are_rejected_with_or_without_rehashed_table(self):
        name = 'install_system.py'
        (self.kit / name).write_bytes(b'changed inert script\n')
        with self.assertRaisesRegex(acceptance.ReleaseAcceptanceError, 'DIGEST'): self.verify()
        self.rehash()
        with self.assertRaisesRegex(acceptance.ReleaseAcceptanceError, 'SOURCE_CONTROL'): self.verify()

    def test_source_comment_and_all_control_bindings(self):
        self.write_source(comment=b'b' * 40); self.rehash()
        with self.assertRaisesRegex(acceptance.ReleaseAcceptanceError, 'SOURCE_ARCHIVE_COMMIT'): self.verify()
        for name in acceptance.SOURCE_MEMBERS:
            self.write_source(overrides={name: b'wrong inert bytes'})
            self.rehash()
            with self.subTest(name=name), self.assertRaisesRegex(acceptance.ReleaseAcceptanceError, 'SOURCE_CONTROL'):
                self.verify()

    def test_six_megabyte_producer_control_and_local_zip64_headers_remain_legal(self):
        (self.kit / 'install_system.py').write_bytes(b'x' * 6_000_000)
        self.write_source(force_zip64=True)
        self.write_bundle(force_zip64=True)
        self.rehash()
        self.verify()

    def test_release_and_bundle_metadata_mismatch(self):
        for key, value in (('source_commit', 'b' * 40), ('version', '0.4.23'), ('platform', 'other'),
                           ('targets', ['aarch64-gnu']), ('targets', [acceptance.TARGET, 'aarch64-gnu']),
                           ('release_id', VERSION + '-bbbbbbbbbbbb')):
            for target in ('record', 'bundle'):
                with self.subTest(key=key, target=target):
                    if target == 'record':
                        (self.kit / 'RELEASE.json').write_text(json.dumps({**self.record, key: value}))
                    else:
                        self.write_bundle(entries=[('MANIFEST.json', json.dumps({**self.manifest, key: value}).encode())])
                    self.rehash()
                    with self.assertRaisesRegex(acceptance.ReleaseAcceptanceError, 'METADATA'): self.verify()
                    (self.kit / 'RELEASE.json').write_text(json.dumps(self.record)); self.write_bundle()
        (self.kit / 'RELEASE.json').write_text(json.dumps({**self.record, 'schema': True})); self.rehash()
        with self.assertRaisesRegex(acceptance.ReleaseAcceptanceError, 'SCHEMA'): self.verify()

    def test_manifest_extensions_and_absent_unrequired_runtime_fields_are_allowed(self):
        required = {key: self.manifest[key] for key in ('source_commit', 'version', 'platform', 'targets', 'release_id')}
        self.write_bundle(entries=[('MANIFEST.json', json.dumps({**required, 'future_metadata': {'safe': 1}}).encode())])
        self.rehash(); self.verify()

    def test_json_rejects_duplicate_nonfinite_overflow_bad_utf8_and_oversize(self):
        bad = (b'{"version":"x","version":"y"}', b'{"extra":NaN}', b'{"extra":Infinity}',
               b'{"extra":1e9999}', b'{"extra":"\xff"}', b'{"extra":"\\ud800"}',
               b' ' * (acceptance.MAX_JSON + 1), b'[]')
        original = (self.kit / 'RELEASE.json').read_bytes()
        for raw in bad:
            (self.kit / 'RELEASE.json').write_bytes(raw); self.rehash()
            with self.subTest(raw=raw[:30]), self.assertRaises(acceptance.ReleaseAcceptanceError): self.verify()
        (self.kit / 'RELEASE.json').write_bytes(original)
        for raw in bad[:6]:
            self.write_bundle(entries=[('MANIFEST.json', raw)]); self.rehash()
            with self.subTest(manifest=raw[:30]), self.assertRaises(acceptance.ReleaseAcceptanceError): self.verify()

    def test_exact_directory_members_missing_extra_directory_link_hardlink_and_fifo(self):
        extra = self.kit / 'extra'; extra.write_text('no')
        with self.assertRaises(acceptance.ReleaseAcceptanceError): self.verify()
        extra.unlink(); extra.mkdir()
        with self.assertRaises(acceptance.ReleaseAcceptanceError): self.verify()
        extra.rmdir()
        name = 'install.sh'; original = (self.kit / name).read_bytes(); (self.kit / name).unlink()
        with self.assertRaises(acceptance.ReleaseAcceptanceError): self.verify()
        external = self.root / 'external'; external.write_bytes(original)
        (self.kit / name).symlink_to(external)
        with self.assertRaises(acceptance.ReleaseAcceptanceError): self.verify()
        (self.kit / name).unlink(); os.link(external, self.kit / name)
        with self.assertRaises(acceptance.ReleaseAcceptanceError): self.verify()
        (self.kit / name).unlink(); os.mkfifo(self.kit / name)
        with self.assertRaises(acceptance.ReleaseAcceptanceError): self.verify()
        alias = self.root / 'alias'; alias.symlink_to(self.kit, target_is_directory=True)
        with self.assertRaises(acceptance.ReleaseAcceptanceError):
            acceptance.verify_kit(alias, expected_source=SHA, candidate_version=VERSION, expected_checksums_sha256=self.anchor)

    def test_checksum_duplicates_illegal_names_and_digest(self):
        original = (self.kit / 'SHA256SUMS').read_bytes()
        for raw in (original + original.splitlines(keepends=True)[0], original.replace(b'  install.sh', b'  ../install.sh'),
                    original.replace(b'  install.sh', b'  sub/install.sh'), b'A' + original[1:],
                    original.replace(b'  install.sh', b'  SHA256SUMS')):
            self.rehash(raw=raw)
            with self.subTest(raw=raw[:30]), self.assertRaises(acceptance.ReleaseAcceptanceError): self.verify()

    def test_zip_unsafe_or_duplicate_names_encryption_nonregular_and_directory(self):
        base = [('MANIFEST.json', json.dumps(self.manifest).encode())]
        symbolic = zipfile.ZipInfo('unsafe'); symbolic.external_attr = (stat.S_IFLNK | 0o777) << 16
        for extra in ([('MANIFEST.json', b'{}')], [('../unsafe', b'x')], [('a\\b', b'x')],
                      [('/absolute', b'x')], [('a//b', b'x')], [('dir/', b'')], [(symbolic, b'target')],
                      [('a', b'x'), ('a/b', b'x')]):
            self.write_bundle(entries=base + extra); self.rehash()
            with self.subTest(extra=str(extra)[:70]), self.assertRaises(acceptance.ReleaseAcceptanceError): self.verify()
        raw = bytearray(zipped(base)); central = raw.index(b'PK\x01\x02')
        struct.pack_into('<H', raw, central + 8, 1)
        self.write_bundle(raw=bytes(raw)); self.rehash()
        with self.assertRaises(acceptance.ReleaseAcceptanceError): self.verify()
        self.write_bundle()
        for extra in ([('install.sh', b'duplicate')], [('../unsafe', b'x')], [(symbolic, b'target')]):
            self.write_source(extra=extra); self.rehash()
            with self.subTest(source=str(extra)[:70]), self.assertRaises(acceptance.ReleaseAcceptanceError): self.verify()

    def test_zip_metadata_is_bounded_before_zipfile_parsing_and_materials_are_bounded(self):
        raw = bytearray((self.kit / acceptance.BUNDLE).read_bytes())
        end = raw.rindex(b'PK\x05\x06')
        struct.pack_into('<H', raw, end + 10, 65535)
        self.write_bundle(raw=bytes(raw)); self.rehash()
        with patch.object(acceptance.zipfile, 'ZipFile') as constructor, self.assertRaises(acceptance.ReleaseAcceptanceError):
            self.verify()
        constructor.assert_not_called()
        self.write_bundle(); self.rehash()
        with patch.object(acceptance, 'MAX_MANIFEST', 8), self.assertRaises(acceptance.ReleaseAcceptanceError): self.verify()
        with patch.object(acceptance, 'MAX_CODE', 8), self.assertRaises(acceptance.ReleaseAcceptanceError): self.verify()

    def test_copy_outside_kit_with_sidecar_and_no_overwrite(self):
        destination = self.root / 'candidate.zip'
        self.verify(bundle_output=destination)
        self.assertEqual(destination.read_bytes(), (self.kit / acceptance.BUNDLE).read_bytes())
        sidecar = destination.with_suffix('.zip.sha256')
        self.assertEqual(sidecar.read_text(), digest(destination.read_bytes()) + '  candidate.zip\n')
        self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o600)
        self.assertEqual((destination.stat().st_nlink, sidecar.stat().st_nlink), (1, 1))
        self.assertEqual(set(path.name for path in self.kit.iterdir()), acceptance.KIT_FILES)
        before = destination.read_bytes(), sidecar.read_bytes()
        with self.assertRaises(acceptance.ReleaseAcceptanceError): self.verify(bundle_output=destination)
        self.assertEqual(before, (destination.read_bytes(), sidecar.read_bytes()))
        with self.assertRaises(acceptance.ReleaseAcceptanceError): self.verify(bundle_output=self.kit / 'extra.zip')

    def test_copy_refuses_existing_or_symbolic_sidecar_and_unsafe_parent(self):
        destination = self.root / 'candidate.zip'; sidecar = self.root / 'candidate.zip.sha256'
        existing = self.root / 'keep'; existing.write_text('preserved')
        sidecar.symlink_to(existing)
        with self.assertRaises(acceptance.ReleaseAcceptanceError): self.verify(bundle_output=destination)
        self.assertTrue(sidecar.is_symlink()); self.assertFalse(destination.exists()); self.assertEqual(existing.read_text(), 'preserved')
        sidecar.unlink(); destination.symlink_to(existing)
        with self.assertRaises(acceptance.ReleaseAcceptanceError): self.verify(bundle_output=destination)
        self.assertTrue(destination.is_symlink()); destination.unlink()
        unsafe = self.root / 'unsafe'; unsafe.mkdir(mode=0o777); unsafe.chmod(0o777)
        with self.assertRaises(acceptance.ReleaseAcceptanceError): self.verify(bundle_output=unsafe / 'x.zip')

    def test_invalid_kit_never_creates_output_and_partial_publication_is_retained(self):
        destination = self.root / 'candidate.zip'
        with self.assertRaises(acceptance.ReleaseAcceptanceError):
            self.verify(expected_checksums_sha256='0' * 64, bundle_output=destination)
        self.assertFalse(destination.exists())
        link = os.link; counter = 0
        def second_fails(*args, **kwargs):
            nonlocal counter
            counter += 1
            if counter == 2:
                raise OSError('synthetic second publication failure')
            return link(*args, **kwargs)
        with patch.object(acceptance.os, 'link', side_effect=second_fails), \
                self.assertRaisesRegex(acceptance.ReleaseAcceptanceError, 'PUBLICATION_UNCERTAIN'):
            self.verify(bundle_output=destination)
        # Design correction: a public pathname cannot be atomically unlinked
        # conditional on its inode. Retain uncertain outputs; never claim success.
        self.assertEqual(destination.read_bytes(), (self.kit / acceptance.BUNDLE).read_bytes())
        self.assertFalse((self.root / 'candidate.zip.sha256').exists())
        self.assertEqual(list(self.root.glob('.release-acceptance-*')), [])
        with self.assertRaisesRegex(acceptance.ReleaseAcceptanceError, 'OUTPUT_ALREADY_EXISTS'):
            self.verify(bundle_output=destination)
        competing = self.root / 'competing.zip'
        def competing_sidecar(*args, **kwargs):
            if args[1] == 'competing.zip.sha256':
                (self.root / args[1]).write_text('someone else\n')
            return link(*args, **kwargs)
        with patch.object(acceptance.os, 'link', side_effect=competing_sidecar), \
                self.assertRaisesRegex(acceptance.ReleaseAcceptanceError, 'PUBLICATION_UNCERTAIN'):
            self.verify(bundle_output=competing)
        self.assertTrue(competing.exists())
        self.assertEqual((self.root / 'competing.zip.sha256').read_text(), 'someone else\n')
        self.assertEqual(list(self.root.glob('.release-acceptance-*')), [])

    def test_copied_bytes_are_rehashed_before_and_after_publication(self):
        original_copy = acceptance._copy_bundle
        original_digest = acceptance._digest
        for failure_at in (1, 2):
            destination = self.root / ('bad-copy-' + str(failure_at) + '.zip')
            calls = []
            def bad_stage(*args, **kwargs):
                def digest_stage(fd, limit):
                    calls.append(fd)
                    return '0' * 64 if len(calls) == failure_at else original_digest(fd, limit)
                with patch.object(acceptance, '_digest', side_effect=digest_stage):
                    return original_copy(*args, **kwargs)
            code = 'COPIED_BUNDLE_DIGEST_MISMATCH' if failure_at == 1 else 'PUBLICATION_UNCERTAIN'
            with self.subTest(failure_at=failure_at), patch.object(acceptance, '_copy_bundle', side_effect=bad_stage), \
                    self.assertRaisesRegex(acceptance.ReleaseAcceptanceError, code):
                self.verify(bundle_output=destination)
            self.assertEqual(len(calls), failure_at)
            self.assertEqual(destination.exists(), failure_at == 2)
            self.assertEqual(destination.with_suffix('.zip.sha256').exists(), failure_at == 2)
            self.assertEqual(list(self.root.glob('.release-acceptance-*')), [])

    def test_fsync_publication_failure_retains_both_outputs_and_refuses_success(self):
        destination = self.root / 'candidate.zip'
        original = os.fsync
        def directory_failure(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError('synthetic directory sync failure')
            return original(fd)
        with patch.object(acceptance.os, 'fsync', side_effect=directory_failure), \
                self.assertRaisesRegex(acceptance.ReleaseAcceptanceError, 'PUBLICATION_UNCERTAIN'):
            self.verify(bundle_output=destination)
        self.assertTrue(destination.exists()); self.assertTrue((self.root / 'candidate.zip.sha256').exists())
        self.assertEqual(list(self.root.glob('.release-acceptance-*')), [])

    def test_public_cleanup_never_unlinks_even_with_competitor_path_replacement(self):
        original_link, original_unlink = os.link, os.unlink
        for replace_before_failure in (False, True):
            name = 'race-' + str(int(replace_before_failure)) + '.zip'
            destination = self.root / name
            foreign = self.root / ('foreign-' + name); foreign.write_bytes(b'foreign inode must survive')
            public_unlinks = []
            def dangerous_unlink(path, *args, **kwargs):
                if path in (name, name + '.sha256'):
                    # This is the old stat->unlink exploit window. It must never
                    # be reached, even when the earlier publication stat matched.
                    public_unlinks.append(path)
                    os.replace(foreign, destination)
                return original_unlink(path, *args, **kwargs)
            def fail_after_first_link(source, target, **kwargs):
                if target == name + '.sha256':
                    if replace_before_failure:
                        os.replace(foreign, destination)
                    raise OSError('synthetic publication failure')
                return original_link(source, target, **kwargs)
            with self.subTest(replaced=replace_before_failure), \
                    patch.object(acceptance.os, 'unlink', side_effect=dangerous_unlink), \
                    patch.object(acceptance.os, 'link', side_effect=fail_after_first_link), \
                    self.assertRaisesRegex(acceptance.ReleaseAcceptanceError, 'PUBLICATION_UNCERTAIN'):
                self.verify(bundle_output=destination)
            self.assertEqual(public_unlinks, [])
            if replace_before_failure:
                self.assertEqual(destination.read_bytes(), b'foreign inode must survive')
            else:
                self.assertEqual(foreign.read_bytes(), b'foreign inode must survive')
                self.assertEqual(destination.read_bytes(), (self.kit / acceptance.BUNDLE).read_bytes())
            self.assertFalse(destination.with_suffix('.zip.sha256').exists())
            self.assertEqual(list(self.root.glob('.release-acceptance-*')), [])
        self.assertFalse(hasattr(acceptance, '_owned_unlink'))

    def test_entire_kit_is_rechecked_after_copy_before_returning_consumer_identity(self):
        destination = self.root / 'candidate.zip'
        original_copy = acceptance._copy_bundle
        def change_control(*args, **kwargs):
            original_copy(*args, **kwargs)
            (self.kit / 'install.sh').write_bytes(b'changed after original verification')
        stdout, stderr = io.StringIO(), io.StringIO()
        args = ['verify-kit', '--kit', str(self.kit), '--source', SHA, '--version', VERSION,
                '--expected-checksums-sha256', self.anchor, '--bundle-output', str(destination)]
        with patch.object(acceptance, '_copy_bundle', side_effect=change_control), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as refused:
            acceptance.main(args)
        self.assertEqual(refused.exception.code, 1)
        self.assertEqual(stdout.getvalue(), '')  # No successful identity/receipt.
        self.assertIn('KIT_CHANGED_AFTER_COPY_OUTPUTS_UNCERTAIN', stderr.getvalue())
        self.assertTrue(destination.exists()); self.assertTrue(destination.with_suffix('.zip.sha256').exists())
        self.assertEqual(list(self.root.glob('.release-acceptance-*')), [])

    def test_cleanup_attempts_all_objects_and_fds_despite_first_unlink_and_close_failure(self):
        destination = self.root / 'candidate.zip'
        real_open, real_close, real_unlink, real_fsync = os.open, os.close, os.unlink, os.fsync
        opened, closed, unlinked = {}, [], []
        primary = OSError('original publication operation failed')
        unlink_error, close_error = OSError('first private unlink failed'), OSError('first close failed')
        def track_open(path, *args, **kwargs):
            fd = real_open(path, *args, **kwargs)
            if str(path).startswith('.release-acceptance-'):
                opened['directory'] = fd
            elif path in ('bundle', 'checksums') and kwargs.get('dir_fd') == opened.get('directory'):
                opened[path] = fd
            return fd
        def fail_first_unlink(path, *args, **kwargs):
            if kwargs.get('dir_fd') == opened.get('directory'):
                unlinked.append(path)
                if path == 'bundle':
                    raise unlink_error
            return real_unlink(path, *args, **kwargs)
        def fail_first_close(fd):
            if fd in opened.values():
                closed.append(fd)
            result = real_close(fd)
            if fd == opened.get('bundle'):
                raise close_error
            return result
        def fail_public_sync(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                raise primary
            return real_fsync(fd)
        with patch.object(acceptance.os, 'open', side_effect=track_open), \
                patch.object(acceptance.os, 'close', side_effect=fail_first_close), \
                patch.object(acceptance.os, 'unlink', side_effect=fail_first_unlink), \
                patch.object(acceptance.os, 'fsync', side_effect=fail_public_sync), \
                self.assertRaisesRegex(acceptance.ReleaseAcceptanceError, 'PRIVATE_STAGE_CLEANUP_FAILED') as refused:
            self.verify(bundle_output=destination)
        self.assertIs(refused.exception.__cause__, primary)
        self.assertIn(unlink_error, refused.exception.cleanup_errors)
        self.assertIn(close_error, refused.exception.cleanup_errors)
        self.assertEqual(unlinked, ['bundle', 'checksums'])
        self.assertEqual(set(closed), set(opened.values()))
        self.assertEqual(len(closed), 3)
        for fd in opened.values():
            with self.assertRaises(OSError): os.fstat(fd)
        namespaces = list(self.root.glob('.release-acceptance-*'))
        self.assertEqual(len(namespaces), 1)  # The injected unlink refusal is honest.
        self.assertEqual(stat.S_IMODE(namespaces[0].stat().st_mode), 0o700)
        self.assertEqual([path.name for path in namespaces[0].iterdir()], ['bundle'])
        self.assertTrue(destination.exists()); self.assertTrue(destination.with_suffix('.zip.sha256').exists())

    def test_json_rejections_do_not_depend_on_unrelated_identity_mismatch(self):
        good = json.dumps(self.record).encode()
        original = (self.kit / 'RELEASE.json').read_bytes()
        for suffix, code in ((b',"version":"0.4.22"}', 'DUPLICATE_JSON_KEY'),
                             (b',"extra":{"nested":1,"nested":1}}', 'DUPLICATE_JSON_KEY'),
                             (b',"extra":NaN}', 'NONFINITE_JSON'),
                             (b',"extra":Infinity}', 'NONFINITE_JSON'),
                             (b',"extra":1e9999}', 'NONFINITE_JSON')):
            (self.kit / 'RELEASE.json').write_bytes(good[:-1] + suffix); self.rehash()
            with self.subTest(suffix=suffix), self.assertRaisesRegex(acceptance.ReleaseAcceptanceError, code): self.verify()
        (self.kit / 'RELEASE.json').write_bytes(original)
        manifest = json.dumps(self.manifest).encode()
        self.write_bundle(entries=[('MANIFEST.json', manifest[:-1] + b',"version":"0.4.22"}')]); self.rehash()
        with self.assertRaisesRegex(acceptance.ReleaseAcceptanceError, 'DUPLICATE_JSON_KEY'): self.verify()

    def test_cli_requires_consumer_anchor_and_prints_only_file_identity(self):
        command = [sys.executable, '-B', '-m', 'scripts.release_acceptance', 'verify-kit',
                   '--kit', str(self.kit), '--source', SHA, '--version', VERSION]
        missing = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(missing.returncode, 2)
        result = subprocess.run(command + ['--expected-checksums-sha256', self.anchor], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        identity = json.loads(result.stdout)
        self.assertEqual(identity['validation_scope'], acceptance.CONSUMER_SCOPE)
        self.assertEqual(set(identity), acceptance.IDENTITY_KEYS)
        self.assertNotIn('fake inert', result.stdout)


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        self.identity = {'schema': 1, 'repository': acceptance.REPOSITORY, 'source_sha': SHA, 'version': VERSION,
                         'target': acceptance.TARGET, 'release_id': VERSION + '-' + SHA[:12],
                         'checksums_sha256': 'd' * 64, 'bundle_sha256': 'e' * 64,
                         'validation_scope': acceptance.CONSUMER_SCOPE}
        self.expected = {'expected_source': SHA, 'candidate_version': VERSION, 'run_id': 42, 'run_attempt': 2,
                         'role': 'fresh', 'producer_artifact_id': 123, 'producer_artifact_digest': ARTIFACT,
                         'expected_checksums_sha256': 'd' * 64, 'expected_bundle_sha256': 'e' * 64}

    def create(self, *, role='fresh', identity=None, **kwargs):
        values = {'run_id': 42, 'run_attempt': 2, 'role': role,
                  'producer_artifact_id': 123, 'producer_artifact_digest': ARTIFACT}
        values.update(kwargs)
        return acceptance.create_receipt(identity or self.identity, **values)

    def test_fresh_build_and_each_seven_upgrade_identity(self):
        result = self.create()
        self.assertEqual(set(result), acceptance.RECEIPT_KEYS)
        self.assertEqual(acceptance.validate_receipt(json.dumps(result).encode(), **self.expected), result)
        producer = {**self.identity, 'validation_scope': acceptance.PRODUCER_SCOPE}
        built = self.create(role='build', identity=producer)
        acceptance.validate_receipt(built, **{**self.expected, 'role': 'build'})
        self.assertEqual(acceptance.BASELINES, {
            '0.4.14': 'b0cc174d04d05e61277075412586f0ba0dd231db',
            '0.4.15': '65b1e75d90de733408101ec280a92be935731baa',
            '0.4.16': '18fe631bcf69b68425b454db1795a101e0378842',
            '0.4.17': '95c201c6e26ed54960f964c675e515890f2eea2a',
            '0.4.19': 'a2b9c188658f082b189e77d237787a96eb9cf921',
            '0.4.20': '05173798de1baed5d67a866c4125e72f74fe3f4c',
            '0.4.21': '2aa5e11e3f09c03d048018f033ae888e7c775604',
            '0.4.22': '558687a76cd29f4e4735284926eb05bf6664472e',
            '0.4.23': 'be5d8459cdd3eeedce73f2ff6e4663e5eb781e42',
            '0.4.24': '8b8d7cf82e55609a205455b351f659eb9de91974'})
        for version, source in acceptance.BASELINES.items():
            role = 'upgrade-' + version
            candidate = {'0.4.22':'0.4.23','0.4.23':'0.4.24','0.4.24':'0.4.25'}.get(version, VERSION)
            identity = {**self.identity, 'version': candidate, 'release_id': candidate + '-' + SHA[:12]}
            result = self.create(role=role, identity=identity)
            self.assertEqual((result['old_version'], result['old_source_sha']), (version, source))
            acceptance.validate_receipt(result, **{**self.expected, 'role': role, 'candidate_version': candidate})
            with self.assertRaises(acceptance.ReleaseAcceptanceError): self.create(role=role, old_source_sha='f' * 40)

    def test_role_scope_cannot_substitute_producer_inspection_for_consumer_anchor(self):
        for role in acceptance.ROLES:
            wrong = acceptance.CONSUMER_SCOPE if role == 'build' else acceptance.PRODUCER_SCOPE
            with self.subTest(role=role), self.assertRaises(acceptance.ReleaseAcceptanceError):
                self.create(role=role, identity={**self.identity, 'validation_scope': wrong})
        with self.assertRaises(acceptance.ReleaseAcceptanceError): self.create(role='upgrade-0.4.22')
        with self.assertRaises(acceptance.ReleaseAcceptanceError): self.create(old_version='0.4.21')
        with self.assertRaises(acceptance.ReleaseAcceptanceError):
            self.create(role='upgrade-0.4.21', identity={**self.identity, 'version': '0.4.21',
                                                        'release_id': '0.4.21-' + SHA[:12]})

    def test_bool_bad_ids_digest_and_unknown_identity_fields_are_rejected(self):
        for key in ('run_id', 'run_attempt', 'producer_artifact_id'):
            for value in (True, False, 0, -1, '2', 1.0, 2**63):
                with self.subTest(key=key, value=value), self.assertRaises(acceptance.ReleaseAcceptanceError):
                    self.create(**{key: value})
        for value in ('a' * 64, 'sha256:' + 'A' * 64, None):
            with self.assertRaises(acceptance.ReleaseAcceptanceError): self.create(producer_artifact_digest=value)
        with self.assertRaises(acceptance.ReleaseAcceptanceError): self.create(identity={**self.identity, 'installed': True})

    def test_every_field_is_bound_to_external_expectation_and_no_unknown_or_missing_keys(self):
        receipt = self.create()
        for key in receipt:
            bad = dict(receipt)
            bad[key] = True if type(bad[key]) is int else str(bad[key]) + '-changed'
            with self.subTest(key=key), self.assertRaises(acceptance.ReleaseAcceptanceError):
                acceptance.validate_receipt(bad, **self.expected)
            bad = dict(receipt); del bad[key]
            with self.subTest(missing=key), self.assertRaises(acceptance.ReleaseAcceptanceError):
                acceptance.validate_receipt(bad, **self.expected)
        with self.assertRaises(acceptance.ReleaseAcceptanceError):
            acceptance.validate_receipt({**receipt, 'old_version': '0.4.21'}, **self.expected)
        upgrade = self.create(role='upgrade-0.4.21'); upgrade['old_source_sha'] = 'f' * 40
        with self.assertRaises(acceptance.ReleaseAcceptanceError):
            acceptance.validate_receipt(upgrade, **{**self.expected, 'role': 'upgrade-0.4.21'})

    def test_receipt_raw_json_rejects_duplicate_keys_nan_overflow_bad_utf8_and_limits(self):
        receipt = json.dumps(self.create()).encode()
        bad = (receipt[:-1] + b',"run_attempt":2}', receipt[:-1] + b',"extra":NaN}',
               receipt[:-1] + b',"extra":Infinity}', receipt[:-1] + b',"extra":1e9999}',
               b'{"bad":"\xff"}', b' ' * (acceptance.MAX_JSON + 1))
        for index, raw in enumerate(bad):
            expected = 'DUPLICATE_JSON_KEY' if index == 0 else 'NONFINITE_JSON' if index < 4 else 'INVALID_JSON|JSON_LIMIT'
            with self.subTest(raw=raw[-30:]), self.assertRaisesRegex(acceptance.ReleaseAcceptanceError, expected):
                acceptance.validate_receipt(raw, **self.expected)


if __name__ == '__main__':
    unittest.main()
