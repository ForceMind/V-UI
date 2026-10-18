"""Explicit offline proxy recovery. No DNS, sockets, core checks or core launch.

Preview prints a redacted content-bound plan. Execution requires that exact plan
hash and re-reads the source while holding all supported writer leases. Unknown
state is replaceable only by an explicitly selected, fully validated backup;
reconciliation only aborts a valid prepared transaction to its recorded baseline.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
import tempfile
from types import SimpleNamespace
import uuid

from app import release_tools as release
from app.services.proxy_store import ProxyStore, ProxyStorageError

_FLAGS = ('local_proxy_enabled', 'relay_enabled')
_HEX = re.compile(r'[a-f0-9]{64}\Z')


class ProxyRecoveryError(release.ReleaseError):
    pass


def _require(condition):
    if not condition:
        raise ProxyRecoveryError('Offline recovery validation failed; no policy defaults were applied')


def _json(path, limit=2_000_000):
    def pairs(items):
        result = {}
        for key, value in items:
            _require(key not in result)
            result[key] = value
        return result
    with path.open('rb') as handle:
        raw = handle.read(limit + 1)
    _require(len(raw) <= limit)
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: _require(False))


def _hash(value):
    return release.digest(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode())


def _database(data, *, revoke=False):
    path = data / 'v-ui.db'
    _require(path.is_file() and not path.is_symlink())
    with sqlite3.connect(path.as_uri() + f'?mode={"rw" if revoke else "ro"}', uri=True) as db:
        _require(db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok')
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        _require({'users', 'inbounds', 'admin_sessions', 'subscription_grants'} <= tables)
        if revoke:
            db.execute('DELETE FROM admin_sessions')
            db.execute('UPDATE subscription_grants SET revoked=1')
            db.commit()
    if revoke:
        with path.open('rb') as handle: os.fsync(handle.fileno())


def _rows(data):
    try:
        return ProxyStore(data).policy_rows()
    except ProxyStorageError:
        _require(False)


def validate_mode_fence(data, rows=None):
    """Reject new SQL policy without the capability marker, never bootstrap it."""
    from app.services.inbound_policy import policy_plan
    from app.services.proxy_store import _defaults
    data = Path(data)
    rows = _rows(data) if rows is None else rows
    plan = policy_plan(rows, _defaults())
    names = ('proxy-state', '.proxy-state-initialized', '.proxy-state.lock')
    schema = (ProxyStore(data).policy_schema_version()
              if any(os.path.lexists(data / name) for name in names) else 1)
    _require(plan.legacy or schema == 2)
    return rows


def _runtime(data, core):
    directory = data / 'runtime' / core
    path = directory / 'state.json'
    if not path.exists():
        return {'revision': None, 'enabled': False}
    state = _json(path, 4096)
    _require(type(state) is dict and set(state) == {'revision', 'enabled'}
             and type(state['enabled']) is bool and type(state['revision']) is str
             and _HEX.fullmatch(state['revision']))
    with (directory / (state['revision'] + '.json')).open('rb') as handle:
        raw = handle.read(16 * 1024 * 1024 + 1)
    _require(len(raw) <= 16 * 1024 * 1024 and release.digest(raw) == state['revision'])
    _require(type(_json(directory / (state['revision'] + '.json'), 16 * 1024 * 1024)) is dict)
    return state


def _validate(data):
    """Reconstruct the exact saved policy without resolving or starting it."""
    _database(data)
    rows = validate_mode_fence(data)
    store = ProxyStore(data)
    with store.backup_snapshot() as entries:
        for _ in entries: pass
    snapshot = store.read_snapshot(initialize=False)
    _require(snapshot['pending'] is None)
    active = store.load_active(reject_pending=True)
    runtime = _runtime(data, 'sing-box')
    xray = _runtime(data, 'xray')
    if active is not None:
        _require(active['core_revision'] == runtime['revision'])
        context = active['runtime_context']
        # Legacy snapshots lacking compiler provenance remain explicit blockers.
        _require(context is not None)
        from app.services.core_manager import SingBoxAdapter
        from app.services.proxy_policy import VerifiedEndpoint, compile_proxy_policy
        base = SingBoxAdapter().build_config(row for row in rows if row.core == 'sing-box')
        endpoint = context['endpoint']
        endpoint = (VerifiedEndpoint(**{**endpoint, 'addresses': tuple(endpoint['addresses'])})
                    if endpoint is not None else None)
        compiled = compile_proxy_policy(base, rows, active, endpoint=endpoint,
                                        self_addresses=tuple(context['self_addresses']))
        _require(_hash(compiled.config) == active['core_revision']
                 and context['candidate_core_revision'] == active['core_revision']
                 and context['policy_identity'] == compiled.identity)
        # A manually stopped core keeps its committed proxy policy. Preserve
        # that false intent, but an empty configuration cannot be enabled.
        _require(not runtime['enabled'] or bool(compiled.config.get('inbounds')))
        from app.services.inbound_policy import policy_plan
        plan = policy_plan(rows, active['settings'])
        if context['schema_version'] == 2:
            _require(context['plan_manifest'] == plan.manifest
                     and context['source_fingerprint'] == plan.source_fingerprint)
        else:
            _require(plan.legacy)
        if active['settings']['relay_enabled'] or plan.relay_ids:
            _require(not xray['enabled'])
    else:
        from app.services.inbound_policy import policy_plan
        from app.services.proxy_store import _defaults
        # An unapplied direct or disabled-relay draft is preservable. It has
        # no applied mode claim; startup's active-none guard still blocks new
        # modes from the ordinary auto-start path. Enabled relay needs active.
        _require(not policy_plan(rows, _defaults()).relay_ids)
    from app.services.inbound_policy import policy_plan
    draft_plan = policy_plan(rows, store.load_draft()['settings'])
    applied_plan = policy_plan(rows, active['settings']) if active is not None else None
    def summary(plan):
        return None if plan is None else [{'id': item['id'], 'routing_mode': item['mode'],
            'effective_mode': item['effective'], 'enabled': item['enable']} for item in plan.manifest]
    return {'saved_inbound_policy': summary(draft_plan),
            'applied_inbound_policy': summary(applied_plan),
            'protected_inbound_ids': [] if applied_plan is None else list(applied_plan.relay_ids),
            'requires_upstream': False if applied_plan is None else applied_plan.requires_upstream,
            'draft_revision': snapshot['revision'],
            'draft_switches': {key: snapshot['settings'][key] for key in _FLAGS},
            'active_revision': None if active is None else active['revision'],
            'active_switches': None if active is None else {key: active['settings'][key] for key in _FLAGS},
            'core_revision': None if active is None else active['core_revision'],
            'core_enabled': runtime['enabled']}


def migrate_legacy_modes(data):
    try:
        return release._migrate_legacy_modes(Path(data))
    except release.ReleaseError:
        _require(False)


def committed_protection(data):
    return release._committed_policy_protection(Path(data))


def _normalize_extracted(work):
    _require(not os.path.lexists(work / '.proxy-state.lock'))
    for relative in ('proxy-state', 'proxy-state/revisions', 'proxy-state/secrets'):
        path = work / relative
        if relative == 'proxy-state/secrets' and not path.exists(): path.mkdir(mode=0o700)
        _require(path.is_dir() and not path.is_symlink())
        path.chmod(0o700)
    # Archive extraction owns all these fresh directories; never chmod live data.
    for current, directories, _ in os.walk(work):
        for name in directories: (Path(current) / name).chmod(0o700)


def _reconcile(work):
    store = ProxyStore(work)
    pending = store.recoverable_pending()
    _require(pending is not None and pending.get('phase') == 'prepared')
    # Exhaust the full immutable history validation before resolving anything.
    with store.backup_snapshot() as entries:
        for _ in entries: pass
    context = pending.get('runtime_context')
    _require(context is not None)
    previous = context['previous_core_revision']
    current = _runtime(work, 'sing-box')
    _require(current['revision'] in (previous, context['candidate_core_revision']))
    _require(pending['base_active_revision'] is None or previous == pending['base_core_revision'])
    directory = work / 'runtime' / 'sing-box'
    if previous is not None:
        _require(_HEX.fullmatch(previous) is not None)
        with (directory / (previous + '.json')).open('rb') as handle:
            raw = handle.read(16 * 1024 * 1024 + 1)
        _require(len(raw) <= 16 * 1024 * 1024 and release.digest(raw) == previous)
        release.atomic_json(directory / 'state.json', {'revision': previous,
                                                       'enabled': context['previous_enabled']})
    else:
        _require(not context['previous_enabled'])
        if (directory / 'state.json').exists():
            (directory / 'state.json').unlink(); release.sync_directory(directory)
    store.abort_apply(pending['transaction_id'])
    return pending['transaction_id']


@contextmanager
def _prepared(root, action, archive=None, archive_sha256=None):
    with release._maintenance_directory(Path(root).absolute()): pass
    root = release.private_root(root)
    _require(action in ('restore', 'reconcile'))
    with release.stopped(root) as guard:
        data = root / 'data'
        _require(data.is_dir() and not data.is_symlink())
        source = release.recovery_tree_digest(data)
        source_schema = None
        try:
            source_schema = ProxyStore(data).policy_schema_version()
        except ProxyStorageError:
            # Dedicated recovery may replace corrupt current evidence using
            # the explicit reviewed archive. The source digest binds it.
            pass
        with tempfile.TemporaryDirectory(prefix='.proxy-recovery-', dir=root) as temporary:
            work = Path(temporary) / 'data'
            transaction = None
            if action == 'restore':
                _require(archive is not None and type(archive_sha256) is str and _HEX.fullmatch(archive_sha256))
                meta = release.unpack_verified(Path(archive), archive_sha256, work)
                _require(meta.get('kind') == 'backup' and meta.get('data_path') == str(data))
                (work / release.MANIFEST).unlink()
                _normalize_extracted(work)
                # Validate before changing access records as well as afterwards.
                release.recovery_tree_digest(work)
                _validate(work)
                _database(work, revoke=True)
                if source_schema == 2:
                    migrate_legacy_modes(work)
                    ProxyStore(work).ensure_policy_v2()
                if (root / 'CURRENT.json').exists():
                    release._policy_release_compatible(work, release.active(root)[0] / 'payload')
            else:
                _require(archive is None and archive_sha256 is None)
                _require(release.recovery_tree_digest(data, copy_to=work) == source)
                transaction = _reconcile(work)
            policy = _validate(work)
            target = release.recovery_tree_digest(work)
            plan = {'schema_version': 1, 'operation': action, 'data_path': str(data),
                    'source_sha256': source, 'archive_sha256': archive_sha256,
                    'target_sha256': target, 'transaction_id': transaction,
                    'policy': policy, 'starts_core': False,
                    'revokes_restored_access': action == 'restore'}
            plan['plan_sha256'] = _hash(plan)
            _require(release.recovery_tree_digest(data) == source)
            yield guard, work, plan


def preview(root: Path, action: str, *, archive=None, archive_sha256=None) -> dict:
    try:
        with _prepared(root, action, archive, archive_sha256) as (_, _, plan):
            return plan
    except (ValueError, OSError, sqlite3.Error, KeyError, TypeError) as exc:
        if isinstance(exc, release.ReleaseError): raise
        raise ProxyRecoveryError('Recovery preview refused; source evidence retained') from None


def _sync_stage(work):
    """Persist every prepared byte and directory before publishing its journal."""
    with release._maintenance_directory(work):
        for _, _, files, directory in os.fwalk(work, topdown=False, follow_symlinks=False):
            for name in files:
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
                try:
                    entry = os.fstat(fd)
                    linked = os.stat(name, dir_fd=directory, follow_symlinks=False)
                    _require(stat.S_ISREG(entry.st_mode) and entry.st_uid == os.geteuid()
                             and entry.st_nlink == 1 and not entry.st_mode & 0o077
                             and (entry.st_dev, entry.st_ino) == (linked.st_dev, linked.st_ino))
                    os.fsync(fd)
                finally:
                    os.close(fd)
            os.fsync(directory)
    release.sync_directory(work.parent)


def execute(root: Path, action: str, plan_sha256: str, *, archive=None, archive_sha256=None) -> dict:
    try:
        _require(type(plan_sha256) is str and _HEX.fullmatch(plan_sha256))
        with _prepared(root, action, archive, archive_sha256) as (guard, work, plan):
            _require(plan['plan_sha256'] == plan_sha256)
            root = guard.root
            _sync_stage(work)
            _require(release.recovery_tree_digest(work) == plan['target_sha256'])
            guard._prepare_replacement(work)
            recovery = root / 'recovery'
            with release._maintenance_directory(recovery, create=True): pass
            old = 'recovery/before-' + uuid.uuid4().hex
            journal = {'schema_version': 2, 'kind': 'proxy-recovery', 'old': old,
                       **{key: plan[key] for key in ('source_sha256', 'target_sha256', 'plan_sha256')}}
            release.atomic_json(root / 'RESTORE_PENDING.json', journal)
            # Never auto-clear on an exception. recover-restore validates and
            # restores the exact predecessor, including its original corruption.
            os.replace(root / 'data', root / old)
            release.sync_directory(recovery); release.sync_directory(root)
            os.replace(work, root / 'data')
            release.sync_directory(work.parent); release.sync_directory(root)
            _require(release.recovery_tree_digest(root / 'data') == plan['target_sha256'])
            _require(_validate(root / 'data') == plan['policy'])
            _require(release.recovery_tree_digest(root / old) == plan['source_sha256'])
            (root / 'RESTORE_PENDING.json').unlink(); release.sync_directory(root)
            return {**plan, 'retained_predecessor': old, 'completed': True}
    except (ValueError, OSError, sqlite3.Error, KeyError, TypeError) as exc:
        if isinstance(exc, release.ReleaseError): raise
        raise ProxyRecoveryError('Offline recovery interrupted or refused; retained data and journal require inspection') from None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('operation', choices=('restore', 'reconcile'))
    parser.add_argument('--archive', type=Path)
    parser.add_argument('--archive-sha256')
    parser.add_argument('--execute-plan-sha256')
    args = parser.parse_args(argv)
    release.supported_environment(); os.umask(0o077)
    kwargs = {'archive': args.archive, 'archive_sha256': args.archive_sha256}
    result = (execute(args.root, args.operation, args.execute_plan_sha256, **kwargs)
              if args.execute_plan_sha256 else preview(args.root, args.operation, **kwargs))
    print(json.dumps(result, sort_keys=True, indent=2))


if __name__ == '__main__':
    try: main()
    except (release.ReleaseError, ValueError, OSError):
        print('Offline proxy recovery refused; no network or core was started. Preserve the data and recovery journal.',
              file=sys.stderr)
        raise SystemExit(1)
