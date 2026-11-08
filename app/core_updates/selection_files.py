"""Root metadata transactions only; no service, writer, backup or runtime action.

All observations are records supplied by a trusted coordinator, not host proofs
or permission grants. Production writes require UID0. The explicit Layout seam
is for private temp tests, never a user path/environment option. A trial remains
private until validation and a durable confirmed journal. Native no-replace
parking detects interference; it is not a kernel compare-and-swap against root.
"""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
import fcntl
import hashlib
import os
from pathlib import Path
import re
import secrets

from . import artifacts as a, selection as s, store as fs

SELECTION = 'core-selection.json'
FENCE = 'core-selection.fence.json'
PENDING = 'CORE_UPDATE_PENDING.json'
LOCK = 'core-selection.lock'
ACTIVE = '.core-update-active'
HISTORY = '.core-update-history'
PHASES = ('admitting', 'prepared', 'quiesced', 'backed_up', 'trial', 'validated',
          'confirming', 'confirmed', 'cleanup', 'rollback_required', 'recovery')
MAX_JOURNALS = 96


@dataclass(frozen=True)
class Layout:
    root: Path = Path('/etc/v-ui')
    owner: int = 0
    anchor: Path = Path('/')


PRODUCTION_LAYOUT = Layout()


class CoreSelectionFilesError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _need(value, code):
    if not value:
        raise CoreSelectionFilesError(code)


def _keys(value, keys):
    _need(type(value) is dict and set(value) == set(keys), 'FIELDS')


def _raw(value):
    return fs._canonical(value) + b'\n'


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _validate_state(state):
    try:
        s.encode_selection(state)
    except s.CoreSelectionError as exc:
        raise CoreSelectionFilesError(exc.code) from None


def _selection(value, app_source, target):
    return s.decode_selection(_raw(value), app_source=app_source, target=target)


def _selection_map(state):
    return a._json(s.encode_selection(state))


def _intent(value, state):
    _keys(value, s.CORES)
    for core in s.CORES:
        row = value[core]
        _keys(row, ('artifact_id', 'revision', 'enabled'))
        _need(row['artifact_id'] == s.reference_identity(state.slot(core).current)
              and type(row['enabled']) is bool
              and (row['revision'] is None or a._hex(row['revision']))
              and (row['revision'] is not None or not row['enabled']), 'INTENT')
    return deepcopy(value)


def _snapshot(value, state):
    _keys(value, ('panel_running', 'cores'))
    _need(type(value['panel_running']) is bool, 'SERVICE_SNAPSHOT')
    return dict(panel_running=value['panel_running'], cores=_intent(value['cores'], state))


def _validation(value, state, intents, panel_running):
    _keys(value, ('selection_revision', 'panel_running', 'panel_health_verified', 'cores'))
    _need(value['selection_revision'] == state.revision
          and type(value['panel_running']) is bool and value['panel_running'] == panel_running
          and type(value['panel_health_verified']) is bool
          and value['panel_health_verified'] == panel_running, 'VALIDATION')
    _keys(value['cores'], s.CORES)
    for core in s.CORES:
        row = value['cores'][core]
        _keys(row, ('artifact_id', 'binary_sha256', 'revision', 'enabled', 'owned_listeners_verified'))
        reference = state.slot(core).current
        _need(row['artifact_id'] == s.reference_identity(reference)
              and row['binary_sha256'] == reference.binary_sha256
              and row['revision'] == intents[core]['revision']
              and type(row['enabled']) is bool and row['enabled'] == intents[core]['enabled']
              and type(row['owned_listeners_verified']) is bool
              and row['owned_listeners_verified'] == (panel_running and intents[core]['enabled']), 'VALIDATION')
    return deepcopy(value)


def _rebind_intents(intents, state):
    return {core: dict(intents[core], artifact_id=s.reference_identity(state.slot(core).current))
            for core in s.CORES}


def _after_intents(journal):
    # Updating a binary preserves the final live configuration/stop intent.
    # Restoring data instead adopts the independently checked incoming state,
    # while quiesced remains the original live intent for backup and rollback.
    if journal['operation'] == 'restore':
        return journal['restore_target']['intents']
    return journal['quiesced']['cores']


@contextmanager
def _root(layout, *, create=False, allow_missing=False):
    scope = fs._Scope(layout.owner)
    try:
        root, anchor = layout.root, layout.anchor
        _need(type(layout) is Layout and type(layout.owner) is int and layout.owner >= 0
              and isinstance(root, Path) and isinstance(anchor, Path)
              and root.is_absolute() and anchor.is_absolute() and '..' not in root.parts
              and '..' not in anchor.parts and root != anchor, 'LAYOUT')
        try:
            relative = root.relative_to(anchor)
        except ValueError:
            raise CoreSelectionFilesError('LAYOUT') from None
        fd = scope.own(os.open(anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW))
        info = os.fstat(fd)
        fs._directory(info, 0 if anchor == Path('/') else layout.owner,
                      None if anchor == Path('/') else 0o700)
        anchor_identity = fs._dir_identity(info)
        missing = None
        for index, name in enumerate(relative.parts):
            if index == len(relative.parts) - 1:
                try:
                    fd = scope.directory(fd, name, create=create)
                except FileNotFoundError:
                    if not allow_missing or create:
                        raise
                    missing = (fd, name)
                    fd = None
            else:
                child = scope.own(os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd))
                info = os.fstat(child)
                fs._directory(info, 0 if anchor == Path('/') else layout.owner)
                scope.directories.append((fd, name, child, fs._dir_identity(info)))
                fd = child
        yield scope, fd
        if missing is not None:
            _need(not _exists(*missing), 'DIRECTORY_CHANGED')
        _need(fs._dir_identity(os.fstat(scope.fds[0])) == anchor_identity
              and fs._dir_identity(os.stat(anchor, follow_symlinks=False)) == anchor_identity, 'ANCHOR_CHANGED')
        scope.stable()
    finally:
        scope.close()


def _exists(directory, name):
    try:
        os.stat(name, dir_fd=directory, follow_symlinks=False)
        return True
    except FileNotFoundError:
        return False


def _read(scope, parent, name, *, mode=0o644, optional=False):
    try:
        fd, size = scope.file(parent, name, mode=mode, limit=a.MAX_JSON)
    except FileNotFoundError:
        if optional:
            return None
        raise
    raw = os.pread(fd, size + 1, 0)
    _need(len(raw) == size and a._file_identity(os.fstat(fd)) == scope.files[-1][3], 'FILE_CHANGED')
    _need(a._file_identity(os.stat(name, dir_fd=parent, follow_symlinks=False)) == scope.files[-1][3],
          'FILE_CHANGED')
    return raw, dict(identity=list(a._file_identity(os.fstat(fd))), sha256=_digest(raw), mode=mode)


def _record(value):
    _keys(value, ('identity', 'sha256', 'mode'))
    _need(type(value['identity']) is list and len(value['identity']) == 9
          and all(type(part) is int and part >= 0 for part in value['identity'])
          and a._hex(value['sha256']) and type(value['mode']) is int
          and value['mode'] in (0o600, 0o644), 'FILE_RECORD')


def _matches(actual, expected):
    # Native rename changes ctime; all other original attributes and raw hash
    # must match. The no-replace operation itself is not inode-conditional CAS.
    return (actual['identity'][:8] == expected['identity'][:8]
            and actual['sha256'] == expected['sha256'] and actual['mode'] == expected['mode'])


def _detach(scope, parent, name):
    scope.files[:] = [row for row in scope.files if row[:2] != (parent, name)]


def _return_foreign(scope, parent, name, destination, restored_name):
    # Best effort, never overwrite a newly appeared live name and never unlink
    # the foreign inode. The caller always reports failure, even after return.
    _detach(scope, parent, name)
    try:
        fs._rename_new(parent, name, destination, restored_name)
    except (fs.CoreStoreError, OSError):
        pass


def _new(scope, parent, name, raw, *, mode=0o600):
    _need(type(raw) is bytes and 0 < len(raw) <= a.MAX_JSON, 'METADATA_LIMIT')
    fd = scope.own(os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                           0o600, dir_fd=parent))
    view = memoryview(raw)
    while view:
        written = os.write(fd, view)
        _need(written > 0, 'WRITE_FAILED')
        view = view[written:]
    os.fchmod(fd, mode)
    os.fsync(fd)
    fs._regular(os.fstat(fd), scope.owner, mode, a.MAX_JSON)
    _need(os.pread(fd, len(raw) + 1, 0) == raw, 'WRITE_CHANGED')
    info = os.fstat(fd)
    scope.files.append((parent, name, fd, a._file_identity(info)))
    _need(a._file_identity(os.stat(name, dir_fd=parent, follow_symlinks=False)) == a._file_identity(info),
          'FILE_CHANGED')
    os.fsync(parent)
    return dict(identity=list(a._file_identity(info)), sha256=_digest(raw), mode=mode)


def _lock(scope, root):
    try:
        fd = scope.own(os.open(LOCK, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                               0o600, dir_fd=root))
        os.fchmod(fd, 0o600)
        os.fsync(fd)
        os.fsync(root)
    except FileExistsError:
        fd = scope.own(os.open(LOCK, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=root))
    info = os.fstat(fd)
    fs._regular(info, scope.owner, 0o600, 0)
    scope.files.append((root, LOCK, fd, a._file_identity(info)))
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise CoreSelectionFilesError('SELECTION_BUSY') from None


def _fence(raw):
    value = a._json(raw)
    _keys(value, ('schema', 'initialized', 'highest_generation', 'confirmed_revision', 'settled', 'transaction'))
    _need(type(value['schema']) is int and value['schema'] == 1
          and value['initialized'] is True and type(value['highest_generation']) is int
          and 0 <= value['highest_generation'] <= s.MAX_GENERATION
          and a._hex(value['confirmed_revision']) and type(value['settled']) is bool
          and a._hex(value['transaction'], 32), 'FENCE')
    return value


def _fence_for(state, transaction, *, settled):
    return dict(schema=1, initialized=True, highest_generation=state.generation,
                confirmed_revision=state.revision, settled=settled, transaction=transaction)


def _pending(raw):
    value = a._json(raw)
    _keys(value, ('schema', 'kind', 'transaction'))
    _need(type(value['schema']) is int and value['schema'] == 1
          and value['kind'] == 'core-update-pending-v1' and a._hex(value['transaction'], 32), 'PENDING')
    return value


def _journal(value, app_source, target):
    _keys(value, ('schema', 'kind', 'transaction', 'sequence', 'previous_sha256', 'phase', 'operation',
                  'before', 'after', 'pre_stop', 'quiesced', 'backup', 'validation', 'original_files',
                  'actions', 'rollback', 'known_generations', 'restore_target'))
    _need(type(value['schema']) is int and value['schema'] == 1
          and value['kind'] == 'core-selection-journal-v1' and a._hex(value['transaction'], 32)
          and type(value['sequence']) is int and 0 <= value['sequence'] < MAX_JOURNALS
          and (value['previous_sha256'] is None or a._hex(value['previous_sha256']))
          and value['phase'] in PHASES
          and value['operation'] in ('bootstrap', 'component', 'bundle', 'rollback', 'restore'), 'JOURNAL')
    before = None if value['before'] is None else _selection(value['before'], app_source, target)
    after = _selection(value['after'], app_source, target)
    _need((before is None) == (value['operation'] == 'bootstrap'), 'JOURNAL_BASELINE')
    _snapshot(value['pre_stop'], after if before is None else before)
    if value['operation'] == 'restore':
        _keys(value['restore_target'], ('kind', 'source_backup_sha256', 'intents'))
        _need(value['restore_target']['kind'] == 'core-restore-target-v1'
              and a._hex(value['restore_target']['source_backup_sha256']), 'RESTORE_TARGET')
        _intent(value['restore_target']['intents'], after)
    else:
        _need(value['restore_target'] is None, 'RESTORE_TARGET')
    _keys(value['original_files'], ('selection', 'fence', 'pending'))
    for key, record in value['original_files'].items():
        if record is not None:
            _record(record)
        else:
            _need(key != 'pending' and before is None, 'JOURNAL_BASELINE')
    if value['quiesced'] is not None:
        _keys(value['quiesced'], ('panel_stopped', 'writers_exclusive', 'cores'))
        _need(value['quiesced']['panel_stopped'] is True and value['quiesced']['writers_exclusive'] is True,
              'QUIESCENCE')
        _intent(value['quiesced']['cores'], before)
    if value['backup'] is not None:
        _keys(value['backup'], ('kind', 'backup_id', 'sha256', 'selection_revision', 'intents_sha256'))
        _need(value['backup']['kind'] == 'backup-components-v1' and before is not None
              and value['quiesced'] is not None and a._hex(value['backup']['backup_id'])
              and a._hex(value['backup']['sha256'])
              and value['backup']['selection_revision'] == before.revision
              and value['backup']['intents_sha256'] == _digest(_raw(value['quiesced']['cores'])), 'BACKUP')
    if value['validation'] is not None:
        _need(value['quiesced'] is not None and value['backup'] is not None, 'VALIDATION')
        _validation(value['validation'], after, _after_intents(value), value['pre_stop']['panel_running'])
    _need(type(value['actions']) is list and len(value['actions']) <= 12, 'ACTIONS')
    names = set()
    for action in value['actions']:
        _keys(action, ('key', 'public', 'old', 'new', 'old_slot', 'new_slot'))
        _need(action['key'] in ('confirm-selection', 'confirm-fence', 'settle-fence',
                               'rollback-selection', 'rollback-fence')
              and action['key'] not in names and action['public'] in (SELECTION, FENCE)
              and action['old_slot'] == action['key'] + '.old'
              and type(action['new_slot']) is str
              and re.fullmatch(re.escape(action['key']) + r'\.new-[a-f0-9]{32}', action['new_slot']), 'ACTIONS')
        names.add(action['key'])
        if action['old'] is not None:
            _record(action['old'])
        _record(action['new'])
    _need(type(value['known_generations']) is list and len(value['known_generations']) <= 64
          and all(type(item) is int and 0 <= item <= s.MAX_GENERATION
                  for item in value['known_generations']), 'GENERATION')
    if value['rollback'] is not None:
        _keys(value['rollback'], ('selection', 'validation', 'intents'))
        restored = _selection(value['rollback']['selection'], app_source, target)
        _need(before is not None and restored.slots == before.slots
              and restored.generation > max([before.generation, after.generation] + value['known_generations']),
              'ROLLBACK')
        intents = _intent(value['rollback']['intents'], before)
        _need(value['quiesced'] is None or intents == value['quiesced']['cores'], 'RECOVERY_INTENT')
        _validation(value['rollback']['validation'], restored, intents, value['pre_stop']['panel_running'])
    if value['operation'] == 'bootstrap':
        _need(value['phase'] in ('admitting', 'prepared', 'confirming', 'confirmed', 'cleanup')
              and all(value[field] is None for field in ('quiesced', 'backup', 'validation', 'rollback')),
              'PHASE_EVIDENCE')
    else:
        phase = value['phase']
        if phase in ('admitting', 'prepared'):
            _need(all(value[field] is None for field in ('quiesced', 'backup', 'validation', 'rollback'))
                  and not value['actions'], 'PHASE_EVIDENCE')
        if phase == 'quiesced':
            _need(value['quiesced'] is not None and value['backup'] is None
                  and value['validation'] is None, 'PHASE_EVIDENCE')
        if phase in ('backed_up', 'trial'):
            _need(value['quiesced'] is not None and value['backup'] is not None
                  and value['validation'] is None, 'PHASE_EVIDENCE')
        if phase in ('validated', 'confirming', 'confirmed', 'cleanup') and value['rollback'] is None:
            _need(all(value[field] is not None for field in ('quiesced', 'backup', 'validation')),
                  'PHASE_EVIDENCE')
        if phase == 'recovery':
            _need(value['rollback'] is not None, 'PHASE_EVIDENCE')
        if value['actions']:
            _need(phase in ('confirming', 'confirmed', 'cleanup', 'rollback_required', 'recovery'),
                  'PHASE_EVIDENCE')
    return value


def _follows(previous, current):
    for key in ('schema', 'kind', 'transaction', 'operation', 'before', 'after', 'pre_stop', 'original_files',
                'restore_target'):
        _need(previous[key] == current[key], 'JOURNAL_REWRITTEN')
    transitions = {
        'admitting': ('prepared', 'recovery'), 'prepared': ('quiesced', 'rollback_required', 'recovery'),
        'quiesced': ('backed_up', 'rollback_required', 'recovery'),
        'backed_up': ('trial', 'rollback_required', 'recovery'),
        'trial': ('validated', 'rollback_required', 'recovery'),
        'validated': ('confirming', 'rollback_required', 'recovery'),
        'confirming': ('confirmed', 'rollback_required', 'recovery'),
        'confirmed': ('cleanup',), 'cleanup': (),
        'rollback_required': ('recovery',), 'recovery': ('confirmed',)}
    if current['operation'] == 'bootstrap':
        transitions['prepared'] = ('confirming',)
    _need(current['phase'] == previous['phase'] or current['phase'] in transitions[previous['phase']],
          'JOURNAL_PHASE')
    for key in ('quiesced', 'backup', 'validation', 'rollback'):
        if previous[key] is not None:
            _need(current[key] == previous[key], 'JOURNAL_REWRITTEN')
    _need(current['actions'][:len(previous['actions'])] == previous['actions']
          and current['known_generations'][:len(previous['known_generations'])] == previous['known_generations'],
          'JOURNAL_REWRITTEN')


class SelectionFiles:
    def __init__(self, layout=PRODUCTION_LAYOUT):
        _need(type(layout) is Layout, 'LAYOUT')
        self.layout = layout

    @contextmanager
    def _io(self, *, write=False, create=False, allow_missing=False):
        _need(not (write and allow_missing), 'LAYOUT')
        if write:
            _need(os.geteuid() == (0 if self.layout.anchor == Path('/') else self.layout.owner), 'ROOT_REQUIRED')
        try:
            with _root(self.layout, create=create, allow_missing=allow_missing) as (scope, root):
                if write:
                    _lock(scope, root)
                yield scope, root
        except (fs.CoreStoreError, a.CoreArtifactError, s.CoreSelectionError) as exc:
            raise CoreSelectionFilesError(exc.code) from None
        except OSError:
            raise CoreSelectionFilesError('METADATA_IO') from None

    def _stable(self, scope, root, app_source, target, *, with_records=False):
        _need(not _exists(root, PENDING) and not _exists(root, ACTIVE), 'RECOVERY_REQUIRED')
        raw, selection_record = _read(scope, root, SELECTION)
        state = s.decode_selection(raw, app_source=app_source, target=target)
        fence_raw, fence_record = _read(scope, root, FENCE)
        fence = _fence(fence_raw)
        _need(fence['settled'] and fence['highest_generation'] == state.generation
              and fence['confirmed_revision'] == state.revision, 'FENCE_CONFLICT')
        # Recheck the barrier after both files to reject a concurrently admitted
        # transaction; parent/file FD bindings are checked again on scope exit.
        _need(not _exists(root, PENDING) and not _exists(root, ACTIVE), 'RECOVERY_REQUIRED')
        return (state, dict(selection=selection_record, fence=fence_record)) if with_records else state

    def read_boot(self, *, app_source, target):
        """Return None only for explicitly absent initialization metadata.

        This read never creates a lock/root or interprets a failed stable read
        as legacy. Even a lone lock/history is an initialization trace. Missing
        production /etc/v-ui is accepted only after checking trusted ancestors.
        """
        with self._io(allow_missing=True) as (scope, root):
            if root is None:
                return None
            names = (SELECTION, FENCE, PENDING, ACTIVE, HISTORY, LOCK)
            if not any(_exists(root, name) for name in names):
                # Verify absence again before leaving the held directory scope.
                _need(not any(_exists(root, name) for name in names), 'RECOVERY_REQUIRED')
                return None
            return self._stable(scope, root, app_source, target)

    def read_stable(self, *, app_source, target):
        with self._io() as (scope, root):
            return self._stable(scope, root, app_source, target)

    def backup_stable(self, *, app_source, target):
        """Ordinary backup entry; no pending snapshot is accepted."""
        return self.read_stable(app_source=app_source, target=target)

    def _publish_journal(self, scope, directory, name, raw):
        # A short write can leave an opaque private stage, never a corrupt
        # named tail that hides the last durable valid journal. Stages are
        # preserved, not adopted/removed on retries or lost acknowledgements.
        staged = 'journal-stage-' + secrets.token_hex(16)
        record = _new(scope, directory, staged, raw)
        scope.stable()
        _detach(scope, directory, staged)
        fs._rename_new(directory, staged, directory, name)
        result = _read(scope, directory, name, mode=0o600)
        _need(_matches(result[1], record), 'JOURNAL_CHANGED')
        os.fsync(directory)

    def _append(self, scope, directory, journal, *, phase=None):
        next_value = deepcopy(journal)
        if phase is not None:
            next_value['phase'] = phase
        next_value['sequence'] += 1
        _need(next_value['sequence'] < MAX_JOURNALS, 'JOURNAL_LIMIT')
        previous_raw, _ = _read(scope, directory, 'journal-%03d.json' % journal['sequence'], mode=0o600)
        next_value['previous_sha256'] = _digest(previous_raw)
        after = journal['after']
        previous = _journal(a._json(previous_raw), after['app_source'], after['target'])
        _need(previous['sequence'] == journal['sequence']
              and previous['transaction'] == journal['transaction'], 'JOURNAL_CHAIN')
        _journal(next_value, after['app_source'], after['target'])
        _follows(previous, next_value)
        self._publish_journal(scope, directory, 'journal-%03d.json' % next_value['sequence'], _raw(next_value))
        journal.clear()
        journal.update(next_value)

    def _load(self, scope, root, transaction, app_source, target):
        _need(a._hex(transaction, 32), 'TRANSACTION')
        if _exists(root, ACTIVE):
            directory = scope.directory(root, ACTIVE, mode=0o700)
            location = 'active'
        else:
            history = scope.directory(root, HISTORY, mode=0o700)
            directory = scope.directory(history, transaction, mode=0o700)
            location = 'archive'
        names = os.listdir(directory)
        journals = sorted(name for name in names if re.fullmatch(r'journal-[0-9]{3}\.json', name))
        _need(0 < len(journals) <= MAX_JOURNALS
              and journals == ['journal-%03d.json' % index for index in range(len(journals))], 'JOURNAL_CHAIN')
        latest, previous = None, None
        for index, name in enumerate(journals):
            raw, _ = _read(scope, directory, name, mode=0o600)
            value = _journal(a._json(raw), app_source, target)
            _need(value['transaction'] == transaction and value['sequence'] == index
                  and value['previous_sha256'] == previous, 'JOURNAL_CHAIN')
            if latest is not None:
                _follows(latest, value)
            latest, previous = value, _digest(raw)
        allowed = set(journals) | {'public-pending.json'}
        # A crash may leave an O_EXCL staging file before its action journal
        # append. Preserve it as uninterpreted recovery material, never execute
        # or adopt it as a planned publication.
        for name in names:
            if re.fullmatch(r'journal-stage-[a-f0-9]{32}', name) or re.fullmatch(
                    r'(?:confirm-selection|confirm-fence|settle-fence|rollback-selection|rollback-fence)'
                    r'\.new-[a-f0-9]{32}', name):
                allowed.add(name)
        for action in latest['actions']:
            allowed.update((action['old_slot'], action['new_slot']))
        _need(set(names) <= allowed and len(names) <= MAX_JOURNALS + 25, 'PRIVATE_MEMBERS')
        bound = {action[key]: action['old' if key == 'old_slot' else 'new']
                 for action in latest['actions'] for key in ('old_slot', 'new_slot')}
        for name in set(names) - set(journals):
            # A partially written unbound .new file is opaque recovery evidence;
            # never parse it as a plan or remove it. All files still require safe
            # type/owner/link/size/mode and stable FD/path bindings.
            if name in bound:
                item = _read(scope, directory, name)
                _need(bound[name] is not None and _matches(item[1], bound[name]), 'PRIVATE_CHANGED')
            elif name == 'public-pending.json':
                item = _read(scope, directory, name)
                _need(_pending(item[0])['transaction'] == transaction
                      and _matches(item[1], latest['original_files']['pending']), 'PENDING_CHANGED')
            else:
                mode = os.stat(name, dir_fd=directory, follow_symlinks=False).st_mode & 0o7777
                _need(mode in (0o600, 0o644), 'PRIVATE_CHANGED')
                _read(scope, directory, name, mode=mode)
        _need(set(os.listdir(directory)) == set(names), 'PRIVATE_CHANGED')
        return directory, latest, location

    def _new_transaction(self, scope, root, *, before, after, operation, snapshot, known_generations,
                         expected_files=None, restore_target=None):
        _need(not any(_exists(root, name) for name in (PENDING, ACTIVE)), 'RECOVERY_REQUIRED')
        transaction = secrets.token_hex(16)
        pending_raw = _raw(dict(schema=1, kind='core-update-pending-v1', transaction=transaction))
        pending_record = _new(scope, root, PENDING, pending_raw, mode=0o644)
        # First barrier is durable before preparing any stop-capable journal.
        os.mkdir(ACTIVE, 0o700, dir_fd=root)
        directory = scope.directory(root, ACTIVE, mode=0o700)
        os.fsync(directory)
        os.fsync(root)
        originals = {'pending': pending_record}
        for key, name in (('selection', SELECTION), ('fence', FENCE)):
            result = _read(scope, root, name, optional=before is None)
            if before is not None:
                _need(expected_files is not None and result[1] == expected_files[key], 'PUBLIC_CHANGED')
            originals[key] = None if result is None else result[1]
        journal = dict(schema=1, kind='core-selection-journal-v1', transaction=transaction,
            sequence=0, previous_sha256=None, phase='admitting', operation=operation,
            before=None if before is None else _selection_map(before), after=_selection_map(after),
            pre_stop=_snapshot(snapshot, after if before is None else before), quiesced=None,
            backup=None, validation=None, original_files=originals, actions=[], rollback=None,
            known_generations=list(known_generations), restore_target=deepcopy(restore_target))
        _journal(journal, after.app_source, after.target)
        self._publish_journal(scope, directory, 'journal-000.json', _raw(journal))
        self._append(scope, directory, journal, phase='prepared')
        return directory, journal

    def bootstrap(self, initial, *, writer_observation, service_snapshot):
        """Explicit first initialization; observation structure is not a scan."""
        _validate_state(initial)
        _need(initial.generation == 0 and all(type(slot.current) is s.BundleReference
              and slot.previous is None for slot in initial.slots), 'BOOTSTRAP_BASELINE')
        _keys(writer_observation, s.CORES)
        for core in s.CORES:
            row = writer_observation[core]
            _keys(row, ('schema', 'revision', 'enabled'))
            _need(type(row['enabled']) is bool and (
                  row['schema'] is None and row['revision'] is None and not row['enabled']
                  or type(row['schema']) is int and row['schema'] == 1 and a._hex(row['revision'])),
                  'WRITER_OBSERVATION')
        snapshot = _snapshot(service_snapshot, initial)
        _need(all(snapshot['cores'][core]['revision'] == writer_observation[core]['revision']
                  and snapshot['cores'][core]['enabled'] == writer_observation[core]['enabled']
                  for core in s.CORES), 'WRITER_OBSERVATION')
        with self._io(write=True, create=True) as (scope, root):
            _need(not any(_exists(root, name) for name in (SELECTION, FENCE, PENDING, ACTIVE, HISTORY)),
                  'ALREADY_INITIALIZED_OR_DAMAGED')
            directory, journal = self._new_transaction(scope, root, before=None, after=initial,
                operation='bootstrap', snapshot=snapshot, known_generations=[])
            self._append(scope, directory, journal, phase='confirming')
            self._publish_confirmed(scope, root, directory, journal, initial)
            return initial

    def begin(self, plan, *, expected_revision, service_snapshot):
        _need(type(plan) is s.SelectionPlan, 'PLAN')
        _validate_state(plan.before)
        _validate_state(plan.after)
        _need(expected_revision == plan.before.revision and a._hex(expected_revision), 'SELECTION_CONFLICT')
        _snapshot(service_snapshot, plan.before)
        _need(plan.operation in ('component', 'bundle', 'rollback') and plan.core in s.CORES
              and type(plan.changed) is bool, 'PLAN')
        if plan.changed:
            _need(plan.after.app_source == plan.before.app_source and plan.after.target == plan.before.target
                  and plan.after.generation == plan.before.generation + 1
                  and plan.after.slot(plan.core).previous == plan.before.slot(plan.core).current
                  and plan.after.slot(plan.core).current != plan.before.slot(plan.core).current
                  and all(plan.after.slot(core) == plan.before.slot(core) for core in s.CORES if core != plan.core), 'PLAN')
            if plan.operation == 'rollback':
                _need(plan.after.slot(plan.core).current == plan.before.slot(plan.core).previous, 'PLAN')
            else:
                kind = s.ComponentReference if plan.operation == 'component' else s.BundleReference
                _need(type(plan.after.slot(plan.core).current) is kind, 'PLAN')
        else:
            _need(plan.after == plan.before, 'PLAN')
        with self._io(write=True) as (scope, root):
            current, expected_files = self._stable(scope, root, plan.before.app_source, plan.before.target,
                                                   with_records=True)
            _need(current == plan.before and current.revision == expected_revision, 'SELECTION_CONFLICT')
            if not plan.changed:
                return None
            _, journal = self._new_transaction(scope, root, before=current, after=plan.after,
                operation=plan.operation, snapshot=service_snapshot, known_generations=[], expected_files=expected_files)
            return journal['transaction']

    def begin_restore(self, snapshot, *, app_source, target, expected_revision,
                      service_snapshot, source_backup_sha256, restored_intents, known_generations=()):
        """Bind separately checked incoming intents; no data restore occurs.

        Source SHA and intents are trusted coordinator observations after its
        backup/material/state2/config checks, never authority from this API.
        The live quiesced intent remains separate for backup/failure rollback.
        """
        _validate_state(snapshot)
        _need(snapshot.app_source == app_source and snapshot.target == target, 'RESTORE_CONTEXT')
        _need(a._hex(source_backup_sha256), 'RESTORE_TARGET')
        restore_target = dict(kind='core-restore-target-v1', source_backup_sha256=source_backup_sha256,
                              intents=_intent(restored_intents, snapshot))
        known = list(known_generations)
        _need(len(known) <= 14 and all(type(item) is int and 0 <= item <= s.MAX_GENERATION for item in known),
              'GENERATION')
        with self._io(write=True) as (scope, root):
            before, expected_files = self._stable(scope, root, app_source, target, with_records=True)
            _need(before.revision == expected_revision, 'SELECTION_CONFLICT')
            _snapshot(service_snapshot, before)
            maximum = max([before.generation, snapshot.generation] + known)
            _need(maximum < s.MAX_GENERATION, 'GENERATION_EXHAUSTED')
            after = s._build(app_source, target, maximum + 1, snapshot.slots)
            _, journal = self._new_transaction(scope, root, before=before, after=after,
                operation='restore', snapshot=service_snapshot,
                known_generations=known + [snapshot.generation], expected_files=expected_files,
                restore_target=restore_target)
            return journal['transaction']

    def _active(self, scope, root, transaction, app_source, target, phases):
        pending_raw, record = _read(scope, root, PENDING)
        _need(_pending(pending_raw)['transaction'] == transaction, 'TRANSACTION_CONFLICT')
        directory, journal, location = self._load(scope, root, transaction, app_source, target)
        _need(location == 'active' and _matches(record, journal['original_files']['pending']), 'PENDING_CHANGED')
        _need(journal['phase'] in phases, 'PHASE')
        return directory, journal

    def record_quiesced(self, transaction, observation, *, app_source, target):
        with self._io(write=True) as (scope, root):
            directory, journal = self._active(scope, root, transaction, app_source, target, ('prepared',))
            before = _selection(journal['before'], app_source, target)
            _keys(observation, ('panel_stopped', 'writers_exclusive', 'cores'))
            _need(observation['panel_stopped'] is True and observation['writers_exclusive'] is True, 'QUIESCENCE')
            _intent(observation['cores'], before)
            journal['quiesced'] = deepcopy(observation)
            self._append(scope, directory, journal, phase='quiesced')

    def backup_snapshot(self, transaction, *, app_source, target):
        with self._io(write=True) as (scope, root):
            _, journal = self._active(scope, root, transaction, app_source, target,
                ('quiesced', 'backed_up', 'trial', 'validated', 'confirming', 'rollback_required', 'recovery'))
            _need(journal['quiesced'] is not None, 'QUIESCENCE')
            return dict(selection=_selection(journal['before'], app_source, target),
                        intents=deepcopy(journal['quiesced']['cores']),
                        panel_was_running=journal['pre_stop']['panel_running'])

    def record_backup(self, transaction, receipt, *, app_source, target):
        with self._io(write=True) as (scope, root):
            directory, journal = self._active(scope, root, transaction, app_source, target, ('quiesced',))
            journal['backup'] = deepcopy(receipt)
            self._append(scope, directory, journal, phase='backed_up')

    def provision(self, transaction, *, app_source, target):
        with self._io(write=True) as (scope, root):
            directory, journal = self._active(scope, root, transaction, app_source, target, ('backed_up',))
            self._append(scope, directory, journal, phase='trial')
            return _selection(journal['after'], app_source, target)

    def trial_view(self, transaction, *, app_source, target):
        with self._io(write=True) as (scope, root):
            _, journal = self._active(scope, root, transaction, app_source, target, ('trial', 'validated'))
            return _selection(journal['after'], app_source, target)

    def record_validation(self, transaction, observation, *, app_source, target):
        with self._io(write=True) as (scope, root):
            directory, journal = self._active(scope, root, transaction, app_source, target, ('trial',))
            after = _selection(journal['after'], app_source, target)
            _validation(observation, after, _after_intents(journal), journal['pre_stop']['panel_running'])
            journal['validation'] = deepcopy(observation)
            self._append(scope, directory, journal, phase='validated')

    def mark_failed(self, transaction, *, app_source, target):
        with self._io(write=True) as (scope, root):
            directory, journal = self._active(scope, root, transaction, app_source, target,
                ('prepared', 'quiesced', 'backed_up', 'trial', 'validated', 'confirming'))
            self._append(scope, directory, journal, phase='rollback_required')

    def _replace(self, scope, root, directory, journal, *, key, name, expected, raw):
        action = next((row for row in journal['actions'] if row['key'] == key), None)
        if action is None:
            next_name, old_name = key + '.new-' + secrets.token_hex(16), key + '.old'
            record = _new(scope, directory, next_name, raw, mode=0o644)
            action = dict(key=key, public=name, old=expected, new=record,
                          old_slot=old_name, new_slot=next_name)
            journal['actions'].append(action)
            self._append(scope, directory, journal)
        _need(action['public'] == name and action['new']['sha256'] == _digest(raw), 'ACTION_CONFLICT')
        current = _read(scope, root, name, optional=True)
        parked = _read(scope, directory, action['old_slot'], optional=True)
        staged = _read(scope, directory, action['new_slot'], optional=True)
        if current is not None and _matches(current[1], action['new']):
            _need(staged is None and (parked is not None and _matches(parked[1], action['old'])
                  if action['old'] is not None else parked is None), 'PUBLICATION_CONFLICT')
            os.fsync(root)
            os.fsync(directory)
            return
        if action['old'] is None:
            _need(current is None and parked is None, 'PUBLIC_CHANGED')
        elif parked is None:
            _need(current is not None and _matches(current[1], action['old']), 'PUBLIC_CHANGED')
            scope.stable()
            _detach(scope, root, name)
            fs._rename_new(root, name, directory, action['old_slot'])
            parked = _read(scope, directory, action['old_slot'])
            if not _matches(parked[1], action['old']):
                # A privileged actor replaced the source between stat/rename.
                # Preserve the foreign inode, returning it only with NOREPLACE.
                _return_foreign(scope, directory, action['old_slot'], root, name)
                raise CoreSelectionFilesError('PUBLIC_CHANGED')
            os.fsync(root)
            os.fsync(directory)
        else:
            _need(_matches(parked[1], action['old']) and current is None, 'PUBLIC_CHANGED')
        _need(staged is not None and _matches(staged[1], action['new']), 'STAGING_CHANGED')
        scope.stable()
        _detach(scope, directory, action['new_slot'])
        fs._rename_new(directory, action['new_slot'], root, name)
        published = _read(scope, root, name)
        _need(_matches(published[1], action['new']), 'PUBLIC_CHANGED')
        os.fsync(directory)
        os.fsync(root)

    def _archive_and_clear(self, scope, root, directory, journal, state):
        transaction = journal['transaction']
        history = scope.directory(root, HISTORY, mode=0o700, create=True)
        # Preserve all original snapshots/journal/parked inodes permanently.
        if _exists(root, ACTIVE):
            scope.stable()
            scope.directories[:] = [row for row in scope.directories if row[:2] != (root, ACTIVE)]
            fs._rename_new(root, ACTIVE, history, transaction)
            info = os.fstat(directory)
            if fs._dir_identity(os.stat(transaction, dir_fd=history, follow_symlinks=False)) != fs._dir_identity(info):
                _return_foreign(scope, history, transaction, root, ACTIVE)
                raise CoreSelectionFilesError('PRIVATE_CHANGED')
            scope.directories.append((history, transaction, directory, fs._dir_identity(info)))
        else:
            _need(fs._dir_identity(os.stat(transaction, dir_fd=history, follow_symlinks=False))
                  == fs._dir_identity(os.fstat(directory)), 'PRIVATE_CHANGED')
        os.fsync(root)
        os.fsync(history)
        pending = _read(scope, root, PENDING, optional=True)
        if pending is not None:
            _need(_matches(pending[1], journal['original_files']['pending']), 'PENDING_CHANGED')
            scope.stable()
            _detach(scope, root, PENDING)
            fs._rename_new(root, PENDING, directory, 'public-pending.json')
        archived_pending = _read(scope, directory, 'public-pending.json')
        if not _matches(archived_pending[1], journal['original_files']['pending']):
            _return_foreign(scope, directory, 'public-pending.json', root, PENDING)
            raise CoreSelectionFilesError('PENDING_CHANGED')
        os.fsync(directory)
        os.fsync(root)
        if journal['phase'] != 'cleanup':
            self._append(scope, directory, journal, phase='cleanup')
        current = _read(scope, root, FENCE, optional=True)
        planned = [action['new'] for action in journal['actions'] if action['public'] == FENCE]
        _need(current is None or any(_matches(current[1], record) for record in planned), 'PUBLIC_CHANGED')
        self._replace(scope, root, directory, journal, key='settle-fence', name=FENCE,
                      expected=None if current is None else current[1],
                      raw=_raw(_fence_for(state, transaction, settled=True)))

    def finish_cleanup(self, transaction, *, app_source, target):
        """Explicitly finish metadata of an already durably confirmed journal.

        Never resumes a trial or records new host validation. A candidate without
        a durable confirmed journal must instead be externally restored first.
        """
        with self._io(write=True) as (scope, root):
            directory, journal, _ = self._load(scope, root, transaction, app_source, target)
            _need(journal['phase'] in ('confirmed', 'cleanup'), 'PHASE')
            state = _selection(journal['after'] if journal['rollback'] is None
                               else journal['rollback']['selection'], app_source, target)
            current = _read(scope, root, SELECTION)
            planned = [action['new'] for action in journal['actions'] if action['public'] == SELECTION]
            _need(current[0] == s.encode_selection(state)
                  and any(_matches(current[1], record) for record in planned), 'PUBLIC_CHANGED')
            self._archive_and_clear(scope, root, directory, journal, state)
            return state

    def _publish_confirmed(self, scope, root, directory, journal, state, *, rollback=False):
        prefix = 'rollback' if rollback else 'confirm'
        for name, suffix, raw in ((SELECTION, 'selection', s.encode_selection(state)),
                                  (FENCE, 'fence', _raw(_fence_for(state, journal['transaction'], settled=False)))):
            expected = journal['original_files'][suffix]
            if rollback:
                result = _read(scope, root, name, optional=True)
                expected = None if result is None else result[1]
            self._replace(scope, root, directory, journal, key=prefix+'-'+suffix, name=name,
                          expected=expected, raw=raw)
        self._append(scope, directory, journal, phase='confirmed')
        self._archive_and_clear(scope, root, directory, journal, state)

    def confirm(self, transaction, *, app_source, target):
        with self._io(write=True) as (scope, root):
            directory, journal = self._active(scope, root, transaction, app_source, target, ('validated',))
            after = _selection(journal['after'], app_source, target)
            self._append(scope, directory, journal, phase='confirming')
            self._publish_confirmed(scope, root, directory, journal, after)
            return after

    def recovery_view(self, *, app_source, target):
        """Facts only. Never continue a trial, start a process or infer success."""
        with self._io(write=True) as (scope, root):
            pending = _read(scope, root, PENDING, optional=True)
            fence = _read(scope, root, FENCE, optional=True)
            if pending is not None:
                transaction = _pending(pending[0])['transaction']
            elif fence is not None:
                transaction = _fence(fence[0])['transaction']
            else:
                raise CoreSelectionFilesError('RECOVERY_IDENTITY_MISSING')
            directory, journal, location = self._load(scope, root, transaction, app_source, target)
            files = {}
            for key, name in (('selection', SELECTION), ('fence', FENCE)):
                result = _read(scope, root, name, optional=True)
                files[key] = None if result is None else dict(raw_sha256=result[1]['sha256'],
                                                             identity=result[1]['identity'])
            return dict(transaction=transaction, phase=journal['phase'], location=location,
                        pending=pending is not None, before=deepcopy(journal['before']),
                        after=deepcopy(journal['after']), rollback=deepcopy(journal['rollback']),
                        pre_stop=deepcopy(journal['pre_stop']), quiesced=deepcopy(journal['quiesced']),
                        restore_target=deepcopy(journal['restore_target']),
                        backup=deepcopy(journal['backup']), actual_files=files,
                        requires_recovery=pending is not None or location == 'active'
                            or fence is None or not _fence(fence[0])['settled'])

    def rollback_metadata(self, transaction, observation, *, app_source, target, known_generations=(),
                          final_intents=None):
        """Only after external stop/reacquire/old-binary restoration proof.

        This method restores metadata, not processes, user data or backups. Its
        validation input merely records trusted coordinator observations.
        """
        with self._io(write=True) as (scope, root):
            directory, journal, location = self._load(scope, root, transaction, app_source, target)
            # Durable confirmation is irreversible within this transaction.
            # Cleanup must finish before a separate planned rollback can begin.
            _need(journal['phase'] not in ('confirmed', 'cleanup'), 'PHASE')
            _need(location == 'active' and _exists(root, PENDING), 'RECOVERY_REQUIRED')
            pending = _read(scope, root, PENDING)
            _need(_pending(pending[0])['transaction'] == transaction
                  and _matches(pending[1], journal['original_files']['pending']), 'PENDING_CHANGED')
            _need(journal['before'] is not None, 'BOOTSTRAP_RECOVERY_REQUIRED')
            before = _selection(journal['before'], app_source, target)
            after = _selection(journal['after'], app_source, target)
            known = list(known_generations)
            _need(len(known) <= 14 and all(type(item) is int and 0 <= item <= s.MAX_GENERATION for item in known),
                  'GENERATION')
            actual = {}
            maximum = max([before.generation, after.generation] + journal['known_generations'] + known)
            for name in (SELECTION, FENCE):
                result = _read(scope, root, name, optional=True)
                actual[name] = result
                if result is not None:
                    if name == SELECTION:
                        current = s.decode_selection(result[0], app_source=app_source, target=target)
                        maximum = max(maximum, current.generation)
                    else:
                        maximum = max(maximum, _fence(result[0])['highest_generation'])
                    # Only our original or journal-planned publications may be
                    # parked. A structurally valid foreign file is still foreign.
                    allowed = [journal['original_files']['selection' if name == SELECTION else 'fence']]
                    allowed += [action['new'] for action in journal['actions'] if action['public'] == name]
                    _need(any(record is not None and _matches(result[1], record) for record in allowed), 'PUBLIC_CHANGED')
            if journal['rollback'] is None:
                _need(maximum < s.MAX_GENERATION, 'GENERATION_EXHAUSTED')
                restored = s._build(app_source, target, maximum + 1, before.slots)
                if journal['quiesced'] is None:
                    _need(final_intents is not None, 'FINAL_INTENT_REQUIRED')
                    intents = _intent(final_intents, before)
                else:
                    intents = journal['quiesced']['cores']
                    _need(final_intents is None or _intent(final_intents, before) == intents, 'RECOVERY_INTENT')
                _validation(observation, restored, intents, journal['pre_stop']['panel_running'])
                journal['known_generations'] += known
                journal['rollback'] = dict(selection=_selection_map(restored), validation=deepcopy(observation),
                                           intents=deepcopy(intents))
                self._append(scope, directory, journal, phase='recovery')
            else:
                restored = _selection(journal['rollback']['selection'], app_source, target)
                _need(journal['rollback']['validation'] == observation
                      and (final_intents is None or _intent(final_intents, before) == journal['rollback']['intents'])
                      and max([before.generation, after.generation] + journal['known_generations'] + known)
                          < restored.generation and maximum <= restored.generation, 'RECOVERY_CONFLICT')
            self._publish_confirmed(scope, root, directory, journal, restored, rollback=True)
            return restored
