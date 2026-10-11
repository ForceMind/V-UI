import copy
import ctypes
import errno
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts.low_resource_core_residency import CoreResidency, validate_observations


class CoreResidencyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.payload = Path(self.temp.name) / 'payload'
        self.core = self.payload / 'cores/x86_64'; self.core.mkdir(parents=True)
        self.manifest = {'files': {}}
        for name in ('sing-box', 'xray'):
            (self.core / name).write_bytes(b'synthetic' * 513)
            self.manifest['files']['cores/x86_64/' + name] = {'size': (self.core / name).stat().st_size}
        self.observer = CoreResidency(); self.addCleanup(self.observer.close)

    def test_real_owned_file_observation_preserves_contents_and_closes_descriptors(self):
        self.observer.capture(self.payload, 'x86_64', self.manifest)
        descriptors = [fd for _, fd, _ in self.observer.files]
        with patch.object(os, 'read', side_effect=AssertionError('content read')), patch.object(os, 'posix_fadvise', side_effect=AssertionError('cache advice')):
            result = self.observer.observe()
        self.assertEqual(result['outcome'], 'passed')
        for row in result['files']:
            self.assertLessEqual(row['resident_pages'], row['mapped_pages'])
        self.observer.close()
        for fd in descriptors:
            with self.assertRaises(OSError): os.fstat(fd)
        self.assertEqual((self.core / 'sing-box').read_bytes(), b'synthetic' * 513)

    def test_prot_none_mapping_and_unmap_on_mincore_failure(self):
        self.observer.capture(self.payload, 'x86_64', self.manifest)
        calls = []
        def mapping(address, size, protection, flags, fd, offset):
            calls.append(('mmap', protection, offset)); return 4096
        def mincore(*args): ctypes.set_errno(errno.EPERM); return -1
        def unmap(*args): calls.append(('munmap',)); return 0
        self.observer.libc = SimpleNamespace(mmap=mapping, mincore=mincore, munmap=unmap)
        result = self.observer.observe()
        self.assertEqual(result['outcome'], 'unavailable'); self.assertEqual(result['reason'], 'mincore_errno_1')
        self.assertEqual(calls, [('mmap', 0, 0), ('munmap',)])

    def test_symlink_hardlink_wrong_size_or_owner_rejected_without_touching_target(self):
        for mode in ('symlink', 'hardlink', 'wrong_size', 'wrong_owner'):
            with self.subTest(mode=mode):
                target = self.core / 'xray'; external = Path(self.temp.name) / 'external'
                external.write_bytes(b'keep'); target.unlink()
                if mode == 'symlink': target.symlink_to(external)
                elif mode == 'hardlink': os.link(external, target)
                else: target.write_bytes(b'synthetic' * 513)
                metadata = copy.deepcopy(self.manifest)
                if mode == 'wrong_size': metadata['files']['cores/x86_64/xray']['size'] = 1
                if mode == 'wrong_owner':
                    with patch.object(os, 'geteuid', return_value=os.geteuid() + 1), self.assertRaises(RuntimeError):
                        self.observer.capture(self.payload, 'x86_64', metadata)
                else:
                    with self.assertRaises((RuntimeError, OSError)): self.observer.capture(self.payload, 'x86_64', metadata)
                self.assertEqual(self.observer.files, []); self.assertEqual(external.read_bytes(), b'keep')
                target.unlink(); target.write_bytes(b'synthetic' * 513)

    def test_observations_keep_descriptor_identity_across_payload_rename_but_reject_content_change(self):
        self.observer.capture(self.payload, 'x86_64', self.manifest)
        moved = self.payload.with_name('prepared-payload'); self.payload.rename(moved)
        self.assertEqual(self.observer.observe()['outcome'], 'passed')
        (moved / 'cores/x86_64/xray').write_bytes(b'changed')
        with self.assertRaisesRegex(RuntimeError, 'identity changed'): self.observer.observe()

    def test_strict_boundary_coverage_times_page_counts_and_identity(self):
        self.observer.capture(self.payload, 'x86_64', self.manifest)
        rows = []
        for name in ('verify_and_unpack_release', 'ensurepip', 'offline_pip_install'):
            observation = self.observer.observe()
            rows.append(dict(name=name, after=dict(finished_monotonic=observation['started_monotonic']),
                             finished_monotonic=observation['finished_monotonic'], payload_core_residency=observation))
        validate_observations(rows, os.sysconf("SC_PAGESIZE"), "x86_64")
        for mutation in ('missing', 'order', 'unavailable', 'outside', 'nan', 'count', 'bool', 'identity', 'path'):
            bad = copy.deepcopy(rows); v = bad[-1]['payload_core_residency']; file = v['files'][0]
            if mutation == 'missing': bad.pop()
            elif mutation == 'order': bad.reverse()
            elif mutation == 'unavailable': v['outcome'] = 'unavailable'
            elif mutation == 'outside': v['finished_monotonic'] += 100
            elif mutation == 'nan': v['started_monotonic'] = float('nan')
            elif mutation == 'count': file['resident_pages'] = file['mapped_pages'] + 1
            elif mutation == 'bool': file['mapped_pages'] = True
            elif mutation == 'identity': file['inode'] += 1
            elif mutation == 'path': file['path'] = '/old/core'
            with self.subTest(mutation=mutation), self.assertRaises(RuntimeError): validate_observations(bad, os.sysconf("SC_PAGESIZE"), "x86_64")

    def test_forged_consistent_page_size_or_architecture_is_rejected(self):
        self.observer.capture(self.payload, 'x86_64', self.manifest)
        for kind in ('page', 'arch'):
            rows=[]
            for name in ('verify_and_unpack_release', 'ensurepip', 'offline_pip_install'):
                value=self.observer.observe()
                for file in value['files']:
                    if kind=='page':
                        file.update(page_size_bytes=1024**3, mapped_pages=1, resident_pages=1, resident_page_bytes=1024**3)
                    else:file['path']=file['path'].replace('x86_64','aarch64')
                rows.append(dict(name=name,after=dict(finished_monotonic=value['started_monotonic']),
                    finished_monotonic=value['finished_monotonic'],payload_core_residency=value))
            with self.subTest(kind=kind),self.assertRaises(RuntimeError):
                validate_observations(rows,os.sysconf('SC_PAGESIZE'),'x86_64')

    def test_mmap_permission_rejection_is_unavailable_without_unmapping_failure_address(self):
        self.observer.capture(self.payload,'x86_64',self.manifest)
        def denied(*args):ctypes.set_errno(errno.EPERM);return ctypes.c_void_p(-1).value
        self.observer.libc=SimpleNamespace(mmap=denied,munmap=lambda *args:self.fail('invalid unmap'))
        result=self.observer.observe()
        self.assertEqual(result['outcome'],'unavailable');self.assertEqual(result['reason'],'mmap_errno_1')

    def test_close_attempts_every_descriptor_and_never_masks_active_product_error(self):
        original=os.close
        for active in (False,True):
            self.observer.capture(self.payload,'x86_64',self.manifest)
            descriptors=[fd for _,fd,_ in self.observer.files];calls=[]
            def close(fd):
                calls.append(fd);original(fd)
                if len(calls)==1:raise InterruptedError(errno.EINTR,'already closed')
            with self.subTest(active=active),patch.object(os,'close',side_effect=close):
                if active:
                    failure=RuntimeError('original product failure')
                    with self.assertRaises(RuntimeError) as caught:
                        try:raise failure
                        finally:self.observer.close()
                    self.assertIs(caught.exception,failure)
                else:
                    with self.assertRaises(InterruptedError):self.observer.close()
            self.assertEqual(calls,descriptors);self.assertEqual(self.observer.files,[])
            self.observer.close()
            for fd in descriptors:
                with self.assertRaises(OSError):os.fstat(fd)

    def test_nonlinux_is_explicitly_unavailable_without_opening_files(self):
        with patch('scripts.low_resource_core_residency.platform.system', return_value='Other'):
            observer = CoreResidency()
        with patch.object(os, 'open', side_effect=AssertionError('unexpected file open')):
            observer.capture(self.payload, 'x86_64', self.manifest)
            self.assertEqual(observer.observe()['outcome'], 'unavailable')
        observer.close()


if __name__ == '__main__': unittest.main()
