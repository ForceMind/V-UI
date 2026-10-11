"""Synthetic Linux archive benchmark; no root, real core, VPS or install certification.

Run with --source-root CHECKOUT --work-dir DISK_BACKED_DIR --member-mib 128.
Uses a stored ZIP to exercise resident archive + large-member memory. The parent
retains the installer verification result while a child unpacks the staged copy.
Reports sampled simultaneous process RSS, not page cache/cgroup/total host memory.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile


def rss(pid):
    try:
        for line in Path(f'/proc/{pid}/status').read_text().splitlines():
            if line.startswith('VmRSS:'): return int(line.split()[1])
    except FileNotFoundError: pass
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--work-dir', type=Path, required=True)
    parser.add_argument('--member-mib', type=int, default=128)
    parser.add_argument('--phase', choices=['parent', 'child'])
    parser.add_argument('--bundle', type=Path); parser.add_argument('--sha')
    args = parser.parse_args()
    sys.path.insert(0, str(args.source_root.resolve()))
    from app import release_tools
    from scripts import install_system
    if args.phase == 'child':
        start = time.monotonic()
        release_tools.unpack_verified(args.bundle, args.sha, args.work_dir / 'extract')
        print(json.dumps({'child_peak_rss_kib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                          'child_seconds': time.monotonic() - start}))
        return
    if args.phase == 'parent':
        start = time.monotonic()
        # Baseline has no temp_dir argument; TMPDIR is also set by the driver.
        import inspect
        kwargs = {'temp_dir': args.work_dir} if 'temp_dir' in inspect.signature(install_system.verify_archive).parameters else {}
        snapshot, archive, _ = install_system.verify_archive(args.bundle, args.sha, **kwargs)
        bundle = args.work_dir / 'staged.zip'
        with bundle.open('wb') as target:
            if isinstance(snapshot, bytes): target.write(snapshot)
            else:
                snapshot.seek(0); shutil.copyfileobj(snapshot, target, 1024 * 1024)
        child = subprocess.Popen([sys.executable, __file__, '--source-root', str(args.source_root),
            '--work-dir', str(args.work_dir), '--phase', 'child', '--bundle', str(bundle), '--sha', args.sha],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        peak = 0
        while child.poll() is None:
            peak = max(peak, rss(os.getpid()) + rss(child.pid)); time.sleep(.005)
        output, error = child.communicate()
        if child.returncode: raise RuntimeError(error)
        archive.close()
        if not isinstance(snapshot, bytes): snapshot.close()
        print(json.dumps({**json.loads(output), 'parent_peak_rss_kib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                          'sampled_parent_child_rss_kib': peak, 'wall_seconds': time.monotonic() - start}))
        return
    if not 1 <= args.member_mib <= 700: parser.error('--member-mib must be 1..700')
    args.work_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='archive-bench-', dir=args.work_dir) as folder:
        work = Path(folder); bundle = work / 'fixture.zip'
        files = {'scripts/deploy.py': b'# synthetic controller\n', 'app/release_tools.py': b'# synthetic api\n',
                 'deploy/system_launcher.py': b'# synthetic launcher\n'}
        block = bytes(range(256)) * 4096; checksum = hashlib.sha256()
        for _ in range(args.member_mib): checksum.update(block)
        entries = {name: {'size': len(data), 'sha256': hashlib.sha256(data).hexdigest(), 'mode': 0o600}
                   for name, data in files.items()}
        entries['large.bin'] = {'size': args.member_mib * len(block), 'sha256': checksum.hexdigest(), 'mode': 0o600}
        manifest = {'schema': 1, 'kind': 'release', 'platform': release_tools.PLATFORM,
                    'release_id': 'synthetic-memory', 'targets': [release_tools.target_key()], 'files': entries}
        with zipfile.ZipFile(bundle, 'w', compression=zipfile.ZIP_STORED) as archive:
            archive.writestr('MANIFEST.json', json.dumps(manifest))
            for name, data in files.items(): archive.writestr(name, data)
            with archive.open('large.bin', 'w') as member:
                for _ in range(args.member_mib): member.write(block)
        checksum = hashlib.sha256()
        with bundle.open('rb') as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b''): checksum.update(chunk)
        env = {**os.environ, 'TMPDIR': str(work.resolve())}
        result = subprocess.run([sys.executable, __file__, '--source-root', str(args.source_root.resolve()),
            '--work-dir', str(work.resolve()), '--phase', 'parent', '--bundle', str(bundle.resolve()),
            '--sha', checksum.hexdigest()], env=env, capture_output=True, text=True, check=True)
        print(json.dumps({'member_mib': args.member_mib, 'archive_bytes': bundle.stat().st_size,
                          'source_root': str(args.source_root), **json.loads(result.stdout)}))


if __name__ == '__main__': main()
