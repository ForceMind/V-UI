"""Owned synthetic runtime archive residency, not cgroup/VPS qualification.

Run as non-root on a disk-backed work directory. Both paths retain the complete
runtime pin and extraction checks. PROT_NONE/mincore does not read mapped pages;
residency is not cgroup ownership, total peak memory, or a timing benchmark.
"""
import argparse
import ctypes
import hashlib
import io
import json
import mmap
import os
from pathlib import Path
import random
import shutil
import stat
import sys
import tarfile
import tempfile
import time


def residency(handle):
    entry = os.fstat(handle.fileno())
    if not stat.S_ISREG(entry.st_mode) or not entry.st_size:
        raise RuntimeError('Residency requires a nonempty fixture file')
    libc = ctypes.CDLL(None, use_errno=True)
    libc.mmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int,
                         ctypes.c_int, ctypes.c_int, ctypes.c_long]
    libc.mmap.restype = ctypes.c_void_p
    libc.mincore.argtypes = [ctypes.c_void_p, ctypes.c_size_t,
                            ctypes.POINTER(ctypes.c_ubyte)]
    libc.munmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
    page = os.sysconf('SC_PAGESIZE')
    count = (entry.st_size + page - 1) // page
    bits = (ctypes.c_ubyte * count)()
    address = libc.mmap(None, entry.st_size, 0, mmap.MAP_SHARED, handle.fileno(), 0)
    if address == ctypes.c_void_p(-1).value:
        raise OSError(ctypes.get_errno(), 'PROT_NONE mmap failed')
    try:
        if libc.mincore(address, entry.st_size, bits):
            raise OSError(ctypes.get_errno(), 'mincore failed')
        return dict(device=entry.st_dev, inode=entry.st_ino, size_bytes=entry.st_size,
                    page_size_bytes=page, mapped_pages=count,
                    resident_pages=sum(value & 1 for value in bits))
    finally:
        if libc.munmap(address, entry.st_size):
            raise OSError(ctypes.get_errno(), 'munmap failed')


def tree_contents(root):
    result = {}
    for path in sorted(root.rglob('*')):
        entry = path.lstat()
        row = {'mode': stat.S_IMODE(entry.st_mode)}
        if path.is_symlink():
            row['link'] = os.readlink(path)
        elif path.is_file():
            with path.open('rb') as handle:
                row['sha256'] = hashlib.file_digest(handle, 'sha256').hexdigest()
        else:
            row['directory'] = True
        result[path.relative_to(root).as_posix()] = row
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--work-dir', type=Path, required=True)
    parser.add_argument('--member-mib', type=int, default=32)
    args = parser.parse_args()
    if not 8 <= args.member_mib <= 128:
        parser.error('member-mib must be 8..128')
    if sys.platform != 'linux' or os.geteuid() == 0:
        parser.error('Use a non-root Linux account')
    with (args.source_root / 'app/release_tools.py').open('rb') as handle:
        source_sha = hashlib.file_digest(handle, 'sha256').hexdigest()
    sys.path.insert(0, str(args.source_root.resolve()))
    from app import release_tools as tools
    key = tools.target_key()
    rows = []
    with tempfile.TemporaryDirectory(prefix='vui-runtime-cache-', dir=args.work_dir) as directory:
        root = Path(directory)
        block = random.Random(0).randbytes(1024 * 1024)
        data = root / 'synthetic-data'
        with data.open('xb') as output:
            for _ in range(args.member_mib): output.write(block)
        template = root / 'template.tar.gz'
        with tarfile.open(template, 'w:gz') as archive:
            archive.add(data, arcname='python/lib/data', recursive=False)
            executable = tarfile.TarInfo('python/bin/python3.12')
            executable.size = 7; executable.mode = 0o755
            archive.addfile(executable, io.BytesIO(b'fixture'))
            link = tarfile.TarInfo('python/bin/python3')
            link.type = tarfile.SYMTYPE; link.linkname = 'python3.12'
            archive.addfile(link)
        checksum = tools.file_digest(template)
        expected = None
        for index, mode in enumerate(('plain', 'private', 'private', 'plain')):
            with tempfile.TemporaryDirectory(prefix=mode+'-', dir=root) as attempt:
                payload = Path(attempt) / 'payload'; payload.mkdir(mode=0o700)
                runtimes = payload / 'runtimes'; runtimes.mkdir(mode=0o700)
                source = runtimes / (key + '.tar.gz')
                with template.open('rb') as original, source.open('xb') as raw:
                    output = tools._PrivateArchiveFile(raw)
                    shutil.copyfileobj(original, output, tools.STREAM_CHUNK)
                    output.finish_writes()
                source.chmod(0o600)
                entry = payload.lstat(); destination = Path(attempt) / 'runtime'
                with source.open('rb') as observed:
                    before = residency(observed)
                    start = time.monotonic()
                    if mode == 'plain':
                        if tools.file_digest(source, cold=True) != checksum:
                            raise RuntimeError('Fixture pin changed')
                        tools.extract_runtime(source, destination)
                    else:
                        context = tools._private_runtime_tar(payload, key, checksum,
                            (entry.st_dev, entry.st_ino))
                        tools.extract_runtime(source, destination, _private_archive=context)
                    elapsed = time.monotonic() - start
                    after = residency(observed)
                contents = tree_contents(destination)
                if expected is None: expected = contents
                if contents != expected or tools.file_digest(source) != checksum:
                    raise RuntimeError('Source bytes or extracted tree changed')
                rows.append(dict(order=index, mode=mode, before=before, after=after,
                                 seconds=elapsed, tree=contents))
    with (args.source_root / 'app/release_tools.py').open('rb') as handle:
        if hashlib.file_digest(handle, 'sha256').hexdigest() != source_sha:
            raise RuntimeError('Product source changed during the benchmark')
    print(json.dumps(dict(scope=__doc__, uid=os.geteuid(), member_mib=args.member_mib,
                         source_file_sha256=source_sha, archive_sha256=checksum,
                         results=rows), indent=2))


if __name__ == '__main__': main()
