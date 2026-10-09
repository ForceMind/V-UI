"""Linux mincore observations of two newly unpacked private payload files.

Mappings use PROT_NONE: no file-content read, page fault, cache advice or eviction.
These file residency bits are not a cgroup page-ownership accounting measure.
"""
import ctypes
import errno
import mmap
import os
from pathlib import Path
import platform
import stat
import time


class CoreResidency:
    def __init__(self):
        self.files = []
        self.unavailable = None
        self.libc = None
        if platform.system() != 'Linux':
            self.unavailable = 'non_linux'
            return
        self.libc = ctypes.CDLL(None, use_errno=True)
        if not all(hasattr(self.libc, name) for name in ('mmap', 'mincore', 'munmap')):
            self.unavailable = 'missing_mincore_interface'
            return
        self.libc.mmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_long]
        self.libc.mmap.restype = ctypes.c_void_p
        self.libc.mincore.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_ubyte)]
        self.libc.mincore.restype = ctypes.c_int
        self.libc.munmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        self.libc.munmap.restype = ctypes.c_int

    def capture(self, payload: Path, arch: str, manifest: dict):
        if self.unavailable:
            return
        directories = []
        try:
            root = os.open(payload, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            directories.append(root)
            for component in ('cores', arch):
                root = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
                directories.append(root)
            for name in ('sing-box', 'xray'):
                relative = f'cores/{arch}/{name}'
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=root)
                try:
                    entry = os.fstat(fd)
                    if (not stat.S_ISREG(entry.st_mode) or entry.st_uid != os.geteuid()
                            or entry.st_nlink != 1 or entry.st_size <= 0
                            or entry.st_size != manifest['files'][relative]['size']):
                        raise RuntimeError('Residency observation requires a new owned regular payload file')
                    identity = (entry.st_dev, entry.st_ino, entry.st_size, entry.st_mtime_ns, entry.st_ctime_ns)
                    self.files.append((relative, fd, identity))
                    fd = None
                finally:
                    if fd is not None:
                        os.close(fd)
        except BaseException:
            self.close()
            raise
        finally:
            for directory in reversed(directories):
                os.close(directory)

    def observe(self):
        started = time.monotonic()
        result = dict(started_monotonic=started, outcome='unavailable' if self.unavailable else 'passed',
            scope='PROT_NONE mincore, no content read; residency is not memcg ownership', files=[])
        if self.unavailable:
            result['reason'] = self.unavailable
        else:
            if len(self.files) != 2:
                raise RuntimeError('Both new payload core descriptors are required')
            page = os.sysconf('SC_PAGESIZE')
            for name, fd, identity in self.files:
                entry = os.fstat(fd)
                if (entry.st_dev, entry.st_ino, entry.st_size, entry.st_mtime_ns, entry.st_ctime_ns) != identity:
                    raise RuntimeError('Payload core identity changed during observation')
                count = (entry.st_size + page - 1) // page
                vector = (ctypes.c_ubyte * count)()
                address = self.libc.mmap(None, entry.st_size, 0, mmap.MAP_SHARED, fd, 0)
                if address == ctypes.c_void_p(-1).value:
                    code = ctypes.get_errno()
                    raise OSError(code, os.strerror(code))
                try:
                    if self.libc.mincore(address, entry.st_size, vector) != 0:
                        code = ctypes.get_errno()
                        if code in (errno.ENOSYS, errno.EPERM, errno.EACCES):
                            result.update(outcome='unavailable', reason='mincore_errno_' + str(code))
                            break
                        raise OSError(code, os.strerror(code))
                    resident = sum(value & 1 for value in vector)
                    result['files'].append(dict(path=name, device=entry.st_dev, inode=entry.st_ino,
                        size_bytes=entry.st_size, page_size_bytes=page, mapped_pages=count,
                        resident_pages=resident, resident_page_bytes=resident * page))
                finally:
                    if self.libc.munmap(address, entry.st_size) != 0:
                        code = ctypes.get_errno()
                        raise OSError(code, os.strerror(code))
        result['finished_monotonic'] = time.monotonic()
        return result

    def close(self):
        for _, fd, _ in self.files:
            os.close(fd)
        self.files.clear()


def validate_observations(rows):
    expected = ('verify_and_unpack_release', 'ensurepip', 'offline_pip_install')
    observed = [row for row in rows if 'payload_core_residency' in row]
    if tuple(row['name'] for row in observed) != expected:
        raise RuntimeError('Missing exact core residency boundary coverage')
    identities = None
    for row in observed:
        value = row['payload_core_residency']
        if value.get('outcome') != 'passed':
            raise RuntimeError('Core residency unavailable: ' + str(value.get('reason', 'unknown')))
        start, end = value.get('started_monotonic'), value.get('finished_monotonic')
        if (type(start) not in (int, float) or type(end) not in (int, float)
                or not row['after']['finished_monotonic'] <= start <= end <= row['finished_monotonic']):
            raise RuntimeError('Core residency observation outside its bound phase')
        files = value.get('files')
        if not isinstance(files, list) or len(files) != 2:
            raise RuntimeError('Missing both payload core observations')
        current = []
        for file, name in zip(files, ('sing-box', 'xray')):
            path = file.get('path', '').split('/')
            if len(path) != 3 or path[0] != 'cores' or path[1] not in ('x86_64', 'aarch64') or path[2] != name:
                raise RuntimeError('Unexpected observed payload core path')
            keys = ('device', 'inode', 'size_bytes', 'page_size_bytes', 'mapped_pages', 'resident_pages', 'resident_page_bytes')
            if any(type(file.get(key)) is not int or file[key] < 0 for key in keys):
                raise RuntimeError('Invalid residency integer counters')
            page, size = file['page_size_bytes'], file['size_bytes']
            if (page < 1024 or page & (page - 1) or size == 0
                    or file['mapped_pages'] != (size + page - 1) // page
                    or file['resident_pages'] > file['mapped_pages']
                    or file['resident_page_bytes'] != file['resident_pages'] * page):
                raise RuntimeError('Inconsistent residency page accounting')
            current.append(tuple(file[key] for key in ('path', 'device', 'inode', 'size_bytes', 'page_size_bytes', 'mapped_pages')))
        if current[0][0].split('/')[1] != current[1][0].split('/')[1]:
            raise RuntimeError('Mixed core architectures')
        if identities is not None and current != identities:
            raise RuntimeError('Core file identity changed between boundaries')
        identities = current
