"""Linux synthetic per-inode cache measurement, not cgroup/VPS qualification.

Uses mincore without touching mapped pages. Compares an ordinary private file
with the product's private archive wrapper. No global cache drops or quota edits.
"""
import argparse
import ctypes
import hashlib
import json
import mmap
import os
from pathlib import Path
import sys
import tempfile
import time


def resident_bytes(handle):
    size = os.fstat(handle.fileno()).st_size
    if not size: return 0
    page = os.sysconf('SC_PAGESIZE')
    with mmap.mmap(handle.fileno(), size, access=mmap.ACCESS_COPY) as mapping:
        address = ctypes.addressof(ctypes.c_char.from_buffer(mapping))
        pages = (size + page - 1) // page
        vector = (ctypes.c_ubyte * pages)()
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.mincore(ctypes.c_void_p(address), ctypes.c_size_t(size), vector):
            raise OSError(ctypes.get_errno(), 'mincore failed')
        return sum(bool(value & 1) for value in vector) * page


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--work-dir', type=Path, required=True)
    parser.add_argument('--member-mib', type=int, default=128)
    args = parser.parse_args()
    if not 1 <= args.member_mib <= 700: parser.error('member-mib must be 1..700')
    sys.path.insert(0, str(args.source_root.resolve()))
    from app.release_tools import _PrivateArchiveFile
    block = bytes(range(256)) * 4096
    results = {}
    for mode in ('ordinary_private_file', 'bounded_private_archive'):
        with tempfile.TemporaryFile(dir=args.work_dir) as raw:
            handle = raw if mode == 'ordinary_private_file' else _PrivateArchiveFile(raw)
            start = time.monotonic(); peak = 0
            for _ in range(args.member_mib):
                handle.write(block)
                peak = max(peak, resident_bytes(raw))
            if mode == 'ordinary_private_file': handle.flush()
            else: handle.finish_writes()
            after_write = resident_bytes(raw)
            handle.seek(0); checksum = hashlib.sha256()
            while chunk := handle.read(1024 * 1024):
                checksum.update(chunk); peak = max(peak, resident_bytes(raw))
            results[mode] = {'sampled_inode_cache_peak_bytes': peak,
                'after_write_cache_bytes': after_write, 'after_read_cache_bytes': resident_bytes(raw),
                'seconds_including_residency_sampling': time.monotonic() - start,
                'sha256': checksum.hexdigest()}
    assert len({row['sha256'] for row in results.values()}) == 1
    print(json.dumps({'member_mib': args.member_mib, 'results': results}, indent=2))


if __name__ == '__main__': main()
