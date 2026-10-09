"""Explicit private-cgroup smoke entry; host attestation is required before GO.

This is a test harness, not a container deployment mode. Product installation,
clients, init, observer and all descendants stay in the same limited container.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import signal
import sys
import time

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))

from scripts import low_resource_acceptance as gate
from scripts.low_resource_service_tree import identity


def private_directory(args, root=Path('/sys/fs/cgroup'), proc=Path('/proc')):
    if (getattr(args, 'cgroup_backend', None) != 'docker-private'
            or args.duration_profile != 'smoke' or not gate.UNIT_PATTERN.fullmatch(args.unit)):
        raise RuntimeError('Private container accounting is only enabled for explicit smoke')
    if (proc/'self/cgroup').read_text().strip() != '0::/' or (proc/'1/cgroup').read_text().strip() != '0::/':
        raise RuntimeError('Expected private container cgroup root for init and worker')
    mounts=[row.split() for row in (proc/'mounts').read_text().splitlines()]
    if not any(len(row)>=4 and row[1]==str(root) and row[2]=='cgroup2' and 'ro' in row[3].split(',') for row in mounts):
        raise RuntimeError('Container cgroup2 mount must be read-only')
    gate.verify_limits(root, args.memory_mib)
    gate.metrics(root)
    return root


def process_binding(pid):
    value = identity(pid)
    value['pid_namespace'] = os.readlink(f'/proc/{pid}/ns/pid')
    value['cgroup_namespace'] = os.readlink(f'/proc/{pid}/ns/cgroup')
    return value


def wait_marker(path, expected, timeout):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if path.exists():
            if json.loads(path.read_text()) != expected:
                raise RuntimeError('Container handshake identity mismatch')
            return
        time.sleep(.1)
    raise RuntimeError('Container handshake timed out')


def live_members(root):
    return sorted(int(pid) for pid in (root/'cgroup.procs').read_text().split())


def entry(args):
    gate.require_hosted_runner()
    if not re.fullmatch(r'[0-9a-f]{40}', args.source_commit):
        raise RuntimeError('Exact source required')
    from app.release_tools import target_key
    if args.target not in ('x86_64-musl', 'aarch64-musl') or target_key() != args.target:
        raise RuntimeError('Container native musl target differs from declared target')
    args.cgroup_backend = 'docker-private'
    args.duration_profile = 'smoke'
    directory = private_directory(args)
    if args.output.exists():
        raise RuntimeError('Refusing to replace worker evidence')
    prefix = args.output.parent
    token = dict(unit=args.unit, source_commit=args.source_commit, target=args.target)
    ready = dict(**token, worker=process_binding(os.getpid()), init=process_binding(1),
                 uid=os.getuid(), gid=os.getgid(), limits=gate.verify_limits(directory, args.memory_mib),
                 page_size=os.sysconf('SC_PAGE_SIZE'), metrics=gate.metrics(directory),
                 members=live_members(directory),observed_monotonic=time.monotonic())
    if set(ready['members']) != {1, os.getpid()}:
        raise RuntimeError('Unexpected initial container processes')
    gate.write_json(prefix/'ready.json', ready)
    wait_marker(prefix/'go.json', token, 120)
    started = time.monotonic()
    # Independent of the host coordinator's lifetime. Even if that process is
    # lost, measured work cannot run forever while the container stays alive.
    def deadline(signum, frame):
        raise RuntimeError('Container smoke exceeded its fixed 600-second deadline')
    previous=signal.signal(signal.SIGALRM,deadline)
    signal.alarm(gate.runtime_seconds(args))
    try:
        result = gate.worker(args)
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM,previous)
    terminal = dict(**token, returncode=result, worker=process_binding(os.getpid()),
                    init=process_binding(1), members=live_members(directory),
                    started_monotonic=started,finished_monotonic=time.monotonic(),
                    wall_seconds=time.monotonic()-started, metrics=gate.metrics(directory))
    if set(terminal['members']) != {1, os.getpid()}:
        terminal['cleanup_error'] = 'Unexpected processes remain after smoke cleanup'
        result = 1
    terminal['returncode'] = result
    gate.write_json(prefix/'terminal.json', terminal)
    wait_marker(prefix/'ack.json', token, 120)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--source-commit', required=True)
    parser.add_argument('--target', choices=('x86_64-musl', 'aarch64-musl'), required=True)
    parser.add_argument('--unit', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--work-dir', type=Path, required=True)
    parser.add_argument('--memory-mib', type=int, choices=gate.MEMORY_PROFILES, default=512)
    args = parser.parse_args()
    os.umask(0o077)
    # Raising interrupts blocked work and allows context managers to clean up;
    # the independent host still stops/kills the whole container on any failure.
    def terminate(signum, frame):
        raise RuntimeError('Container harness terminated')
    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)
    return entry(args)


if __name__ == '__main__':
    raise SystemExit(main())
