"""Private runtime archive advice is fd-bound and never weakens stage integrity.

Synthetic unit fixtures only: no downloaded runtime, install, network, or claim
about real kernel residency or a measured installation memory peak.
"""
from contextlib import contextmanager, ExitStack
import errno
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import stat
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app import release_tools as tools


class RuntimeArchiveCacheTests(unittest.TestCase):
    key = 'x86_64-gnu'

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(
            prefix='.runtime-archive-unit-', dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.ancestor = self.root / 'owned'
        self.ancestor.mkdir(mode=0o700)
        self.payload = self.ancestor / 'payload'
        self.payload.mkdir(mode=0o700)
        (self.payload / 'runtimes').mkdir(mode=0o700)
        self.archive = self.payload / 'runtimes' / (self.key + '.tar.gz')
        self.content = b'all synthetic runtime bytes\0' * 4097 + b'tail'
        with tarfile.open(self.archive, 'w:gz') as archive:
            for name, data, mode in (
                    ('python/bin/python3.12', b'synthetic executable', 0o755),
                    ('python/lib/data', self.content, 0o644),
                    ('python/lib/empty', b'', 0o600)):
                entry = tarfile.TarInfo(name)
                entry.size = len(data); entry.mode = mode
                archive.addfile(entry, io.BytesIO(data))
            for name, target, kind in (
                    ('python/bin/python3', 'python3.12', tarfile.SYMTYPE),
                    ('python/lib/alias', 'python/lib/data', tarfile.LNKTYPE),
                    ('python/lib-link', 'lib', tarfile.SYMTYPE)):
                entry = tarfile.TarInfo(name)
                entry.type = kind; entry.linkname = target
                archive.addfile(entry)
        self.archive.chmod(0o600)
        self.archive_bytes = self.archive.read_bytes()
        self.sha = hashlib.sha256(self.archive_bytes).hexdigest()
        self.identity = self.inode(self.payload)
        self.archive_identity = self.inode(self.archive)
        self.destination = self.root / 'new-runtime'
        self.external = self.root / 'external'
        self.external.write_bytes(b'caller-owned source must stay untouched')
        self.external_bytes = self.external.read_bytes()

    @staticmethod
    def inode(path):
        entry = path.stat()
        return entry.st_dev, entry.st_ino

    @staticmethod
    def fd_inode(fd):
        entry = os.fstat(fd)
        return entry.st_dev, entry.st_ino

    def private(self, **changes):
        options = dict(payload=self.payload, key=self.key, expected_sha=self.sha,
                       payload_identity=self.identity)
        options.update(changes)
        return tools._private_runtime_tar(**options)

    def extract(self, **changes):
        tools.extract_runtime(self.archive, self.destination,
                              _private_archive=self.private(**changes))

    def assert_runtime(self, path=None):
        path = path or self.destination
        self.assertEqual((path / 'python/lib/data').read_bytes(), self.content)
        self.assertEqual((path / 'python/lib/empty').read_bytes(), b'')
        self.assertEqual((path / 'python/bin/python3.12').read_bytes(), b'synthetic executable')
        self.assertEqual(stat.S_IMODE((path / 'python/bin/python3.12').stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE((path / 'python/lib/data').stat().st_mode), 0o644)
        self.assertEqual(os.readlink(path / 'python/bin/python3'), 'python3.12')
        self.assertEqual(os.readlink(path / 'python/lib-link'), 'lib')
        self.assertEqual(self.inode(path / 'python/lib/data'), self.inode(path / 'python/lib/alias'))

    def assert_external_unchanged(self):
        self.assertEqual(self.external.read_bytes(), self.external_bytes)

    @contextmanager
    def capture_descriptors(self):
        """Track helper opens even when its context never reaches yield."""
        opened = []
        raw_handles = []
        original_open, original_fdopen = os.open, os.fdopen
        def open_fd(*args, **kwargs):
            fd = original_open(*args, **kwargs)
            opened.append(fd)
            return fd
        def fdopen(*args, **kwargs):
            handle = original_fdopen(*args, **kwargs)
            raw_handles.append(handle)
            return handle
        with patch.object(tools.os, 'open', side_effect=open_fd), \
             patch.object(tools.os, 'fdopen', side_effect=fdopen):
            try:
                yield opened, raw_handles
            finally:
                self.assertTrue(opened)
                self.assertTrue(all(handle.closed for handle in raw_handles))
                for fd in set(opened):
                    with self.assertRaises(OSError) as failure:
                        os.fstat(fd)
                    self.assertEqual(failure.exception.errno, errno.EBADF)

    def test_default_extraction_never_advises_caller_source(self):
        events = []
        source_before = self.archive.stat()
        def record(fd, *args):
            events.append(self.fd_inode(fd))
        with patch.object(tools.os, 'fsync', side_effect=record), \
             patch.object(tools.os, 'posix_fadvise', side_effect=record), \
             patch.object(tools, '_private_runtime_tar', side_effect=AssertionError('private opt-in')):
            tools.extract_runtime(self.archive, self.destination)
        self.assertNotIn(self.archive_identity, events)
        self.assertEqual(self.archive.read_bytes(), self.archive_bytes)
        self.assertEqual(self.archive.stat().st_mode, source_before.st_mode)
        self.assertEqual(self.archive.stat().st_nlink, source_before.st_nlink)
        self.assert_runtime()

    def test_private_hash_and_tar_use_same_fd_with_writeback_before_advice(self):
        events = []; hashed = []; tar_readers = []; reads = []
        original_digest, original_tar_open = tools.stream_digest, tarfile.open
        original_read = tools._PrivateArchiveFile.read
        def read(reader, size=-1):
            data = original_read(reader, size)
            reads.append((reader.fileno(), size, len(data)))
            return data
        def digest(reader, *args, **kwargs):
            fd = reader.fileno()
            self.assertEqual(self.fd_inode(fd), self.archive_identity)
            result = original_digest(reader, *args, **kwargs)
            self.assertEqual(result, (self.sha, len(self.archive_bytes)))
            hashed.append((reader, fd, result))
            events.append(('hashed', fd))
            return result
        def open_tar(*args, **kwargs):
            self.assertFalse(args)
            reader = kwargs['fileobj']; tar_readers.append(reader)
            self.assertEqual(reader.tell(), 0)
            self.assertTrue(hashed)
            self.assertIs(reader, hashed[-1][0])
            self.assertEqual(reader.fileno(), hashed[-1][1])
            events.append(('tar', reader.fileno()))
            return original_tar_open(*args, **kwargs)
        def sync(fd): events.append(('sync', fd))
        def advice(fd, offset, length, kind):
            self.assertEqual(self.fd_inode(fd), self.archive_identity)
            self.assertIn(('sync', fd), events)
            self.assertEqual(kind, os.POSIX_FADV_DONTNEED)
            events.append(('advice', fd))
        with self.capture_descriptors(), \
             patch.object(tools, 'stream_digest', side_effect=digest), \
             patch.object(tools.tarfile, 'open', side_effect=open_tar), \
             patch.object(tools._PrivateArchiveFile, 'read', read), \
             patch.object(tools.os, 'fsync', side_effect=sync), \
             patch.object(tools.os, 'posix_fadvise', side_effect=advice):
            with self.private() as archive:
                self.assertEqual(archive.extractfile('python/lib/data').read(), self.content)
                archive.getmembers()
        self.assertEqual(len(hashed), 1)
        self.assertEqual(len(tar_readers), 1)
        self.assertTrue(reads)
        self.assertTrue(all(0 <= size <= tools.STREAM_CHUNK for _, size, _ in reads))
        self.assertLess(events.index(('sync', hashed[0][1])), events.index(('hashed', hashed[0][1])))
        self.assertLess(events.index(('hashed', hashed[0][1])), events.index(('tar', hashed[0][1])))
        self.assertTrue(any(kind == 'advice' for kind, _ in events))

    def test_private_extraction_preserves_all_bytes_modes_and_internal_links(self):
        self.extract()
        self.assert_runtime()
        self.assertEqual(self.archive.read_bytes(), self.archive_bytes)
        self.assertEqual(stat.S_IMODE(self.archive.stat().st_mode), 0o600)
        self.assertEqual(self.archive.stat().st_nlink, 1)
        self.assert_external_unchanged()

    def test_existing_destination_never_enters_private_archive(self):
        self.destination.mkdir()
        marker = self.destination / 'existing'; marker.write_bytes(b'keep')
        with patch.object(tools.os, 'posix_fadvise') as advice, \
             patch.object(tools.os, 'open', side_effect=AssertionError('must not open')):
            with self.assertRaises(FileExistsError): self.extract()
        advice.assert_not_called()
        self.assertEqual(marker.read_bytes(), b'keep')

    def test_invalid_pin_or_target_fails_before_open_or_advice(self):
        for changes in ({'expected_sha': None}, {'expected_sha': 'A' * 64},
                        {'expected_sha': '0' * 63}, {'key': '../outside'}, {'key': 'other'}):
            with self.subTest(changes=changes), \
                 patch.object(tools.os, 'open') as opened, \
                 patch.object(tools.os, 'posix_fadvise') as advice:
                with self.assertRaises(tools.ReleaseError):
                    with self.private(**changes): pass
                opened.assert_not_called(); advice.assert_not_called()
                self.assertFalse(self.destination.exists())

    def test_wrong_complete_pin_fails_before_tar_and_closes_all_fds(self):
        # A suffix outside useful tar content must still participate in the pin.
        with self.archive.open('ab') as handle: handle.write(b'full-pin-tail' * 100)
        with self.capture_descriptors(), patch.object(tools.tarfile, 'open') as opened:
            with self.assertRaisesRegex(tools.ReleaseError, 'pin mismatch'): self.extract()
            opened.assert_not_called()
        self.assertFalse(self.destination.exists())

    def test_payload_identity_mode_and_owner_are_required_before_advice(self):
        for guard in ('identity', 'mode', 'owner'):
            with self.subTest(guard=guard), ExitStack() as stack:
                kwargs = {}
                if guard == 'identity': kwargs['payload_identity'] = (self.identity[0], self.identity[1] + 1)
                if guard == 'mode':
                    self.payload.chmod(0o755)
                    stack.callback(self.payload.chmod, 0o700)
                if guard == 'owner': stack.enter_context(patch.object(tools.os, 'geteuid', return_value=os.geteuid() + 1))
                advice = stack.enter_context(patch.object(tools.os, 'posix_fadvise'))
                with self.capture_descriptors():
                    with self.assertRaises(tools.ReleaseError): self.extract(**kwargs)
                advice.assert_not_called()
                self.assertFalse(self.destination.exists())

    def test_symlinked_ancestor_payload_runtimes_and_leaf_are_rejected(self):
        for target in (self.ancestor, self.payload, self.archive.parent, self.archive):
            with self.subTest(target=target):
                held = target.with_name(target.name + '-held')
                target.rename(held); target.symlink_to(held)
                try:
                    with self.capture_descriptors(), patch.object(tools.os, 'posix_fadvise') as advice:
                        with self.assertRaises(tools.ReleaseError): self.extract()
                        advice.assert_not_called()
                    self.assertFalse(self.destination.exists())
                finally:
                    target.unlink(); held.rename(target)
        self.assert_external_unchanged()

    def test_archive_mode_link_count_empty_size_and_type_are_rejected(self):
        for guard in ('mode', 'hardlink', 'empty', 'size', 'directory', 'fifo'):
            with self.subTest(guard=guard), ExitStack() as stack:
                if guard == 'mode': self.archive.chmod(0o644)
                elif guard == 'hardlink':
                    other = self.root / 'hardlink'; os.link(self.archive, other)
                    stack.callback(other.unlink)
                elif guard == 'empty': self.archive.write_bytes(b'')
                elif guard == 'size': stack.enter_context(patch.object(tools, 'MAX_ARCHIVE', len(self.archive_bytes) - 1))
                elif guard == 'directory': self.archive.unlink(); self.archive.mkdir(mode=0o700)
                elif guard == 'fifo': self.archive.unlink(); os.mkfifo(self.archive, 0o600)
                try:
                    with self.capture_descriptors(), patch.object(tools.os, 'posix_fadvise') as advice:
                        with self.assertRaises(tools.ReleaseError): self.extract()
                        advice.assert_not_called()
                    self.assertFalse(self.destination.exists())
                finally:
                    if self.archive.is_dir(): self.archive.rmdir()
                    else: self.archive.unlink()
                    self.archive.write_bytes(self.archive_bytes); self.archive.chmod(0o600)
        self.assert_external_unchanged()

    def test_archive_owner_is_required_independently_of_payload_owner(self):
        original = os.fstat
        def fstat(fd):
            entry = original(fd)
            if (entry.st_dev, entry.st_ino) == self.archive_identity:
                fields = {name: getattr(entry, name) for name in dir(entry) if name.startswith('st_')}
                fields['st_uid'] = entry.st_uid + 1
                return SimpleNamespace(**fields)
            return entry
        with self.capture_descriptors(), patch.object(tools.os, 'fstat', side_effect=fstat), \
             patch.object(tools.os, 'posix_fadvise') as advice:
            with self.assertRaises(tools.ReleaseError): self.extract()
            advice.assert_not_called()
        self.assertFalse(self.destination.exists())

    def test_replacement_after_open_is_rejected_before_hash_or_advice(self):
        original = os.open
        replacement = self.root / 'replacement'; replacement.write_bytes(self.archive_bytes); replacement.chmod(0o600)
        replacement_identity = self.inode(replacement)
        replaced = []
        def open_fd(name, flags, *args, **kwargs):
            fd = original(name, flags, *args, **kwargs)
            if name == self.archive.name and kwargs.get('dir_fd') is not None:
                replaced.append(fd); os.replace(replacement, self.archive)
            return fd
        with patch.object(tools.os, 'open', side_effect=open_fd), self.capture_descriptors(), \
             patch.object(tools, 'stream_digest') as digest, \
             patch.object(tools.os, 'posix_fadvise') as advice:
            with self.assertRaises(tools.ReleaseError): self.extract()
            digest.assert_not_called(); advice.assert_not_called()
        self.assertTrue(replaced)
        self.assertEqual(self.inode(self.archive), replacement_identity)
        self.assertEqual(self.archive.read_bytes(), self.archive_bytes)
        self.assertFalse(self.destination.exists())

    def test_path_replacement_during_read_never_advises_replacement_and_cleans_tree(self):
        original = tools._PrivateArchiveFile.read
        replacement = self.root / 'replacement'; replacement.write_bytes(self.archive_bytes); replacement.chmod(0o600)
        replacement_identity = self.inode(replacement)
        replaced = []; advised = []
        def read(reader, size=-1):
            data = original(reader, size)
            if data and not replaced:
                replaced.append(True); os.replace(replacement, self.archive)
            return data
        def advise(fd, *args): advised.append(self.fd_inode(fd))
        with self.capture_descriptors(), patch.object(tools._PrivateArchiveFile, 'read', read), \
             patch.object(tools.os, 'posix_fadvise', side_effect=advise):
            with self.assertRaisesRegex(tools.ReleaseError, 'identity changed'): self.extract()
        self.assertTrue(replaced)
        self.assertTrue(advised)
        self.assertTrue(all(identity == self.archive_identity for identity in advised))
        self.assertNotIn(replacement_identity, advised)
        self.assertFalse(self.destination.exists())
        self.assertEqual(self.archive.read_bytes(), self.archive_bytes)

    def test_retained_ancestor_bindings_detect_directory_replacement_during_read(self):
        for target in (self.ancestor, self.payload, self.archive.parent):
            with self.subTest(target=target):
                original = tools._PrivateArchiveFile.read
                held = target.with_name(target.name + '-held')
                changed = []; advised = []
                def read(reader, size=-1):
                    data = original(reader, size)
                    if data and not changed:
                        changed.append(True)
                        target.rename(held); target.mkdir(mode=0o700)
                        (target / 'sentinel').write_bytes(b'concurrent directory')
                    return data
                def advise(fd, *args): advised.append(self.fd_inode(fd))
                try:
                    with self.capture_descriptors(), patch.object(tools._PrivateArchiveFile, 'read', read), \
                         patch.object(tools.os, 'posix_fadvise', side_effect=advise):
                        with self.assertRaisesRegex(tools.ReleaseError, 'identity changed'): self.extract()
                    self.assertTrue(changed)
                    self.assertTrue(all(identity == self.archive_identity for identity in advised))
                    self.assertFalse(self.destination.exists())
                    self.assertEqual((target / 'sentinel').read_bytes(), b'concurrent directory')
                finally:
                    if changed: shutil.rmtree(target); held.rename(target)
        self.assert_external_unchanged()

    def test_in_place_mode_and_content_changes_after_pin_are_rejected(self):
        original = tools.stream_digest
        for mutation in ('mode', 'content'):
            with self.subTest(mutation=mutation):
                def digest(reader, *args, **kwargs):
                    result = original(reader, *args, **kwargs)
                    if mutation == 'mode': self.archive.chmod(0o640)
                    else:
                        with self.archive.open('ab') as handle: handle.write(b'post-pin-change')
                    return result
                try:
                    with self.capture_descriptors(), patch.object(tools, 'stream_digest', side_effect=digest):
                        with self.assertRaises(tools.ReleaseError): self.extract()
                    self.assertFalse(self.destination.exists())
                finally:
                    self.archive.write_bytes(self.archive_bytes); self.archive.chmod(0o600)

    def test_missing_or_rejected_advice_preserves_pin_and_extraction(self):
        for interface in ('posix_fadvise', 'POSIX_FADV_DONTNEED', 'rejected'):
            with self.subTest(interface=interface), ExitStack() as stack:
                if interface == 'rejected':
                    stack.enter_context(patch.object(tools.os, 'posix_fadvise', side_effect=OSError('unsupported')))
                else: stack.enter_context(patch.object(tools.os, interface, None))
                sync = stack.enter_context(patch.object(tools.os, 'fsync'))
                self.extract(); self.assert_runtime()
                if interface != 'rejected': sync.assert_not_called()
            shutil.rmtree(self.destination)
        self.assertEqual(self.archive.read_bytes(), self.archive_bytes)

    def test_missing_safe_traversal_uses_one_plain_fd_without_source_advice(self):
        original_digest, original_tar_open = tools.stream_digest, tarfile.open
        for interface in ('O_DIRECTORY', 'O_NOFOLLOW', 'O_NONBLOCK'):
            with self.subTest(interface=interface):
                hashed = []; tar_handles = []
                def digest(handle, *args, **kwargs):
                    self.assertNotIsInstance(handle, tools._PrivateArchiveFile)
                    result = original_digest(handle, *args, **kwargs)
                    self.assertEqual(result, (self.sha, len(self.archive_bytes)))
                    hashed.append(handle)
                    return result
                def open_tar(*args, **kwargs):
                    self.assertFalse(args)
                    handle = kwargs['fileobj']; tar_handles.append(handle)
                    self.assertIs(handle, hashed[-1])
                    self.assertEqual(handle.tell(), 0)
                    return original_tar_open(*args, **kwargs)
                with patch.object(tools.os, interface, None), \
                     patch.object(tools, 'stream_digest', side_effect=digest), \
                     patch.object(tools.tarfile, 'open', side_effect=open_tar), \
                     patch.object(tools.os, 'fsync') as sync, \
                     patch.object(tools.os, 'posix_fadvise') as advice:
                    with self.private() as archive:
                        self.assertEqual(archive.extractfile('python/lib/data').read(), self.content)
                    sync.assert_not_called(); advice.assert_not_called()
                self.assertEqual(len(hashed), 1)
                self.assertEqual(len(tar_handles), 1)
                self.assertTrue(hashed[0].closed)
        self.assertEqual(self.archive.read_bytes(), self.archive_bytes)

    def test_missing_safe_traversal_preserves_extraction_without_source_advice(self):
        for interface in ('O_DIRECTORY', 'O_NOFOLLOW', 'O_NONBLOCK'):
            with self.subTest(interface=interface):
                advised = []
                def advise(fd, *args): advised.append(self.fd_inode(fd))
                with patch.object(tools.os, interface, None), \
                     patch.object(tools.os, 'posix_fadvise', side_effect=advise):
                    self.extract()
                self.assertNotIn(self.archive_identity, advised)
                self.assert_runtime()
                shutil.rmtree(self.destination)

    def test_plain_fd_fallback_does_not_reopen_replacement_after_hash(self):
        original_digest = tools.stream_digest
        replacement = self.root / 'replacement'
        replacement.write_bytes(b'caller replacement is not the pinned runtime')
        replacement_identity = self.inode(replacement)
        handles = []
        def digest(handle, *args, **kwargs):
            result = original_digest(handle, *args, **kwargs)
            self.assertEqual(result, (self.sha, len(self.archive_bytes)))
            handles.append(handle)
            os.replace(replacement, self.archive)
            return result
        with patch.object(tools.os, 'O_DIRECTORY', None), \
             patch.object(tools, 'stream_digest', side_effect=digest), \
             patch.object(tools.os, 'posix_fadvise') as advice:
            with self.private() as archive:
                self.assertEqual(archive.extractfile('python/lib/data').read(), self.content)
            advice.assert_not_called()
        self.assertEqual(len(handles), 1)
        self.assertTrue(handles[0].closed)
        self.assertEqual(self.inode(self.archive), replacement_identity)
        self.assertEqual(self.archive.read_bytes(), b'caller replacement is not the pinned runtime')

    def test_plain_fd_fallback_keeps_pin_failure_and_read_errors_closed(self):
        original_open = Path.open
        for failure in ('pin', 'read', 'tar'):
            with self.subTest(failure=failure), ExitStack() as stack:
                handles = []
                def open_file(path, *args, **kwargs):
                    handle = original_open(path, *args, **kwargs)
                    if path == self.archive: handles.append(handle)
                    return handle
                stack.enter_context(patch.object(tools.os, 'O_DIRECTORY', None))
                stack.enter_context(patch.object(Path, 'open', open_file))
                advice = stack.enter_context(patch.object(tools.os, 'posix_fadvise'))
                if failure == 'read': stack.enter_context(patch.object(tools, 'stream_digest', side_effect=OSError('read error')))
                if failure == 'tar': stack.enter_context(patch.object(tools.tarfile, 'open', side_effect=tarfile.ReadError('tar error')))
                with self.assertRaises(tools.ReleaseError):
                    self.extract(expected_sha='0' * 64 if failure == 'pin' else self.sha)
                self.assertEqual(len(handles), 1)
                self.assertTrue(handles[0].closed)
                advice.assert_not_called()
                self.assertFalse(self.destination.exists())

    def test_tar_extraction_error_cleans_partial_tree_and_closes_fds(self):
        def fail_extract(archive, destination, **kwargs):
            (destination / 'partial').write_bytes(b'partial new runtime')
            raise OSError('injected extraction failure')
        with self.capture_descriptors(), patch.object(tools.tarfile.TarFile, 'extractall', fail_extract):
            with self.assertRaisesRegex(tools.ReleaseError, 'extraction failed'): self.extract()
        self.assertFalse(self.destination.exists())
        self.assertEqual(self.archive.read_bytes(), self.archive_bytes)
        self.assert_external_unchanged()

    def test_hash_read_tar_and_writeback_errors_close_fds_and_remove_new_tree(self):
        for failure in ('hash-read', 'tar-open', 'tar-members', 'fsync', 'fdopen'):
            with self.subTest(failure=failure), ExitStack() as stack:
                if failure == 'hash-read': stack.enter_context(patch.object(tools, 'stream_digest', side_effect=OSError('read error')))
                elif failure == 'tar-open': stack.enter_context(patch.object(tools.tarfile, 'open', side_effect=tarfile.ReadError('bad tar')))
                elif failure == 'tar-members': stack.enter_context(patch.object(tools.tarfile.TarFile, 'getmembers', side_effect=tarfile.ReadError('bad members')))
                elif failure == 'fsync': stack.enter_context(patch.object(tools.os, 'fsync', side_effect=OSError('sync error')))
                elif failure == 'fdopen': stack.enter_context(patch.object(tools.os, 'fdopen', side_effect=OSError('fdopen error')))
                with self.capture_descriptors():
                    with self.assertRaisesRegex(tools.ReleaseError, 'extraction failed'): self.extract()
                self.assertFalse(self.destination.exists())
        self.assertEqual(self.archive.read_bytes(), self.archive_bytes)
        self.assert_external_unchanged()

    def test_valid_pin_for_invalid_tar_still_fails_and_closes_fds(self):
        self.archive.write_bytes(b'not a gzip or tar archive')
        sha = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        with self.capture_descriptors():
            with self.assertRaisesRegex(tools.ReleaseError, 'extraction failed'):
                self.extract(expected_sha=sha)
        self.assertFalse(self.destination.exists())

    def test_post_validation_close_error_does_not_leak_other_fds_or_partial_tree(self):
        original = os.close
        failed = []
        def close(fd):
            original(fd)
            if not failed:
                failed.append(fd); raise OSError('injected close error')
        with self.capture_descriptors(), patch.object(tools.os, 'close', side_effect=close):
            with self.assertRaisesRegex(tools.ReleaseError, 'extraction failed'): self.extract()
        self.assertTrue(failed)
        self.assertFalse(self.destination.exists())

    def test_cleanup_close_error_preserves_primary_pin_failure(self):
        original = os.close
        def close(fd):
            original(fd); raise OSError('secondary close error')
        with self.capture_descriptors(), patch.object(tools.os, 'close', side_effect=close):
            with self.assertRaisesRegex(tools.ReleaseError, 'pin mismatch'):
                self.extract(expected_sha='0' * 64)
        self.assertFalse(self.destination.exists())

    def stage_fixture(self):
        root = self.root / 'installation'; root.mkdir(mode=0o700)
        old = root / 'releases' / 'version-a'; old.mkdir(parents=True, mode=0o700)
        (old / 'runtime-sentinel').write_bytes(b'old prepared A')
        data = root / 'data'; data.mkdir(mode=0o700)
        (data / 'sentinel').write_bytes(b'live data')
        current = root / 'CURRENT.json'; current.write_text('{"release_id":"version-a"}\n')
        bundle = self.root / 'caller-release.zip'; bundle.write_bytes(b'synthetic outer source')
        metadata = {'kind': 'release', 'platform': tools.PLATFORM, 'release_id': 'version-b',
                    'targets': [self.key], 'portable_runtime_pins': {self.key: {'sha256': self.sha}}}
        unpacked = []
        def unpack(archive, expected_sha, destination):
            self.assertEqual(archive, bundle)
            shutil.copytree(self.payload, destination)
            destination.chmod(0o700)
            (destination / tools.MANIFEST).write_text('{}')
            unpacked.append(self.inode(destination))
            return metadata
        return root, old, current, bundle, metadata, unpack, unpacked

    def assert_stage_preserved_a(self, root, old, current, bundle):
        self.assertEqual((old / 'runtime-sentinel').read_bytes(), b'old prepared A')
        self.assertEqual((root / 'data/sentinel').read_bytes(), b'live data')
        self.assertEqual(current.read_text(), '{"release_id":"version-a"}\n')
        self.assertEqual(bundle.read_bytes(), b'synthetic outer source')
        self.assertFalse(list(root.glob('.stage-*')))
        with tools.lease(root / '.operation.lock'): pass

    def test_stage_passes_pre_rename_payload_identity_and_exact_pin(self):
        root, old, current, bundle, metadata, unpack, unpacked = self.stage_fixture()
        original = tools._private_runtime_tar
        calls = []
        def private(payload, key, sha, identity):
            calls.append((payload, key, sha, identity))
            self.assertEqual(identity, unpacked[0])
            self.assertEqual(identity, self.inode(payload))
            return original(payload, key, sha, identity)
        with patch.object(tools, 'supported_environment', return_value=self.key), \
             patch.object(tools, 'unpack_verified', side_effect=unpack), \
             patch.object(tools, '_private_runtime_tar', side_effect=private), \
             patch.object(tools.subprocess, 'run') as run, patch.object(tools, 'health_check'):
            self.assertEqual(tools.stage(bundle, 'a' * 64, root), 'version-b')
        self.assertEqual(calls, [(root / 'releases/version-b/payload', self.key, self.sha, unpacked[0])])
        self.assertEqual(run.call_args_list[0].args[0][1:3], ['-m', 'ensurepip'])
        self.assert_runtime(root / 'releases/version-b/runtime')
        ready = json.loads((root / 'releases/version-b/READY.json').read_text())
        self.assertEqual(ready['runtime_archive_sha256'], self.sha)
        self.assert_stage_preserved_a(root, old, current, bundle)

    def test_stage_private_failures_skip_ensurepip_and_remove_only_new_b(self):
        for failure in ('pin', 'tar', 'fsync', 'replacement', 'payload-swap-before-rename'):
            with self.subTest(failure=failure):
                root, old, current, bundle, metadata, unpack, unpacked = self.stage_fixture()
                original_read, original_replace = tools._PrivateArchiveFile.read, os.replace
                replaced = []
                if failure == 'pin': metadata['portable_runtime_pins'][self.key]['sha256'] = '0' * 64
                if failure == 'tar':
                    self.archive.write_bytes(b'bad runtime tar')
                    metadata['portable_runtime_pins'][self.key]['sha256'] = hashlib.sha256(self.archive.read_bytes()).hexdigest()
                def read(reader, size=-1):
                    data = original_read(reader, size)
                    if data and not replaced:
                        replaced.append(True)
                        path = root / 'releases/version-b/payload/runtimes' / self.archive.name
                        replacement = path.with_suffix('.replacement')
                        replacement.write_bytes(self.archive_bytes); replacement.chmod(0o600)
                        os.replace(replacement, path)
                    return data
                def replace(source, destination):
                    if Path(source).name == 'payload' and Path(destination) == root / 'releases/version-b/payload':
                        held = Path(source).with_name('swapped-payload')
                        original_replace(source, held)
                        shutil.copytree(held, source)
                        Path(source).chmod(0o700)
                        replaced.append(True)
                    return original_replace(source, destination)
                try:
                    with ExitStack() as stack:
                        stack.enter_context(patch.object(tools, 'supported_environment', return_value=self.key))
                        stack.enter_context(patch.object(tools, 'unpack_verified', side_effect=unpack))
                        run = stack.enter_context(patch.object(tools.subprocess, 'run'))
                        health = stack.enter_context(patch.object(tools, 'health_check'))
                        if failure == 'fsync': stack.enter_context(patch.object(tools.os, 'fsync', side_effect=OSError('archive sync failed')))
                        if failure == 'replacement': stack.enter_context(patch.object(tools._PrivateArchiveFile, 'read', read))
                        if failure == 'payload-swap-before-rename': stack.enter_context(patch.object(tools.os, 'replace', side_effect=replace))
                        with self.assertRaises(tools.ReleaseError): tools.stage(bundle, 'a' * 64, root)
                        run.assert_not_called(); health.assert_not_called()
                    if failure in ('replacement', 'payload-swap-before-rename'): self.assertTrue(replaced)
                    self.assertFalse((root / 'releases/version-b').exists())
                    self.assert_stage_preserved_a(root, old, current, bundle)
                finally:
                    shutil.rmtree(root); bundle.unlink()
                    self.archive.write_bytes(self.archive_bytes); self.archive.chmod(0o600)


if __name__ == '__main__': unittest.main()
