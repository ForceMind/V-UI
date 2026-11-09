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


def _bundle_baseline(state):
    return (type(state) is s.SelectionState and state.generation == 0
            and all(type(slot.current) is s.BundleReference and slot.previous is None for slot in state.slots))


def _first_component_change(before, after):
    if not _bundle_baseline(before) or after.generation != 1:
        return False
    changed = [core for core in s.CORES if before.slot(core) != after.slot(core)]
    if len(changed) != 1:
        return False
    old, new = before.slot(changed[0]), after.slot(changed[0])
    return type(new.current) is s.ComponentReference and new.previous == old.current


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


def _data_generation(value):
    _keys(value, ('dev', 'ino'))
    _need(all(type(number) is int and 0 <= number < 2**64 for number in value.values())
          and value['ino'] > 0, 'SWITCH_DATA')
    return value


def _restore_data_plan(value, context):
    _keys(value, ('slot', 'incoming_generation', 'preflight_tree_sha256', 'incoming_trust_sha256'))
    _need(a._hex(value['slot'], 32) and a._hex(value['preflight_tree_sha256']), 'RESTORE_DATA_PLAN')
    original = context['data_generation']
    incoming = _data_generation(value['incoming_generation'])
    _need(incoming['dev'] == original['dev'] and incoming['ino'] != original['ino'], 'RESTORE_DATA_PLAN')
    trust = value['incoming_trust_sha256']
    _need(a._hex(trust) if context['trust_sha256'] is not None else trust is None, 'RESTORE_TRUST')
    return deepcopy(value)


def restore_data_position(journal, observed):
    """Classify no-follow observations from a checked journal. Never move data."""
    target = journal['restore_target']
    _need(target is not None and target['kind'] == 'core-restore-target-v2', 'RESTORE_DATA_PLAN')
    _keys(observed, ('live', 'original', 'incoming', 'rejected'))
    original = journal['switch_context']['data_generation']
    incoming = target['data']['incoming_generation']
    positions = {
        ('old', None, None, None): 'unplaced',
        ('old', None, 'new', None): 'staged',
        (None, 'old', 'new', None): 'parked',
        ('new', 'old', None, None): 'trial',
        (None, 'old', None, 'new'): 'rollback_gap',
        ('old', None, None, 'new'): 'restored',
    }
    identities = []
    for name in ('live', 'original', 'incoming', 'rejected'):
        actual = observed[name]
        if actual is None:
            identities.append(None)
            continue
        _data_generation(actual)
        _need(actual in (original, incoming), 'RESTORE_DATA_CHANGED')
        identities.append('old' if actual == original else 'new')
    position = positions.get(tuple(identities))
    _need(position is not None, 'RESTORE_DATA_CHANGED')
    return position


def _require_restore_data(journal, observed, positions):
    target = journal['restore_target']
    if target is None or target['kind'] != 'core-restore-target-v2':
        _need(observed is None, 'RESTORE_DATA_PLAN')
        return
    _need(restore_data_position(journal, observed) in positions, 'RESTORE_DATA_POSITION')


def _switch_context(value, state, panel_running):
    """Durable observations for explicit recovery, never a writer permit."""
    _keys(value, ('application', 'data_generation', 'services', 'config_sha256',
                  'certificate_mode', 'trust_sha256'))
    app = value['application']
    _keys(app, ('app_source', 'target', 'release_id', 'current_sha256',
                'manifest_sha256', 'ready_sha256'))
    _need(app['app_source'] == state.app_source and app['target'] == state.target
          and type(app['release_id']) is str
          and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,100}', app['release_id'])
          and all(a._hex(app[key]) for key in ('current_sha256', 'manifest_sha256', 'ready_sha256')),
          'SWITCH_APPLICATION')
    _data_generation(value['data_generation'])
    services = value['services']
    _keys(services, ('v-ui.service', 'v-ui-http01.service', 'v-ui-http01.socket'))
    _need(all(status in ('active', 'inactive') for status in services.values())
          and (services['v-ui.service'] == 'active') == panel_running, 'SWITCH_SERVICES')
    _need(a._hex(value['config_sha256']) and value['certificate_mode'] in ('managed', 'provided'),
          'SWITCH_CONFIG')
    requires_trust = value['certificate_mode'] == 'provided' and panel_running
    _need(a._hex(value['trust_sha256']) if requires_trust else value['trust_sha256'] is None,
          'SWITCH_TRUST')
    return deepcopy(value)


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


def _lock(scope, root, *, require_new=False):
    try:
        fd = scope.own(os.open(LOCK, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                               0o600, dir_fd=root))
        os.fchmod(fd, 0o600)
        os.fsync(fd)
        os.fsync(root)
    except FileExistsError:
        _need(not require_new, 'RECOVERY_REQUIRED')
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
    fields = ('schema', 'kind', 'transaction', 'sequence', 'previous_sha256', 'phase', 'operation',
              'before', 'after', 'pre_stop', 'quiesced', 'backup', 'validation', 'original_files',
              'actions', 'rollback', 'known_generations', 'restore_target')
    _keys(value, (*fields, 'switch_context') if type(value) is dict and 'switch_context' in value else fields)
    _need(type(value['schema']) is int and value['schema'] == 1
          and value['kind'] == 'core-selection-journal-v1' and a._hex(value['transaction'], 32)
          and type(value['sequence']) is int and 0 <= value['sequence'] < MAX_JOURNALS
          and (value['previous_sha256'] is None or a._hex(value['previous_sha256']))
          and value['phase'] in PHASES
          and value['operation'] in ('bootstrap', 'initial_component', 'component', 'bundle', 'rollback', 'restore'), 'JOURNAL')
    before = None if value['before'] is None else _selection(value['before'], app_source, target)
    after = _selection(value['after'], app_source, target)
    _need((before is None) == (value['operation'] == 'bootstrap'), 'JOURNAL_BASELINE')
    first_switch = value['operation'] == 'initial_component'
    if first_switch:
        _need(_first_component_change(before, after), 'JOURNAL_BASELINE')
    _snapshot(value['pre_stop'], after if before is None else before)
    if 'switch_context' in value:
        _need(value['operation'] != 'bootstrap', 'SWITCH_CONTEXT')
        _switch_context(value['switch_context'], before, value['pre_stop']['panel_running'])
    if value['operation'] == 'restore':
        restored = value['restore_target']
        current_format = 'switch_context' in value
        fields = ('kind', 'source_backup_sha256', 'intents')
        _keys(restored, (*fields, 'data') if current_format else fields)
        _need(restored['kind'] == ('core-restore-target-v2' if current_format else 'core-restore-target-v1')
              and a._hex(restored['source_backup_sha256']), 'RESTORE_TARGET')
        _intent(restored['intents'], after)
        if current_format:
            _restore_data_plan(restored['data'], value['switch_context'])
    else:
        _need(value['restore_target'] is None, 'RESTORE_TARGET')
    dual_restore = value['operation'] == 'restore' and 'switch_context' in value
    _keys(value['original_files'], ('selection', 'fence', 'pending'))
    for key, record in value['original_files'].items():
        # The first actual switch has a logical bundle baseline for recovery,
        # but both original public records really were absent on disk.
        _need((record is None) == (key != 'pending' and (before is None or first_switch)), 'JOURNAL_BASELINE')
        if record is not None:
            _record(record)
    if value['quiesced'] is not None:
        fields = ('panel_stopped', 'writers_exclusive', 'cores')
        digests = ('original_tree_sha256', 'prepared_tree_sha256')
        _keys(value['quiesced'], (*fields, *digests) if dual_restore else fields)
        _need(value['quiesced']['panel_stopped'] is True and value['quiesced']['writers_exclusive'] is True,
              'QUIESCENCE')
        _intent(value['quiesced']['cores'], before)
        if dual_restore:
            _need(all(a._hex(value['quiesced'][name]) for name in digests), 'RESTORE_DATA_DIGEST')
    if value['backup'] is not None:
        backup_fields = ('kind', 'backup_id', 'sha256', 'selection_revision', 'intents_sha256')
        _keys(value['backup'], (*backup_fields, 'name') if 'switch_context' in value else backup_fields)
        if 'switch_context' in value:
            name = value['backup']['name']
            digest = value['backup']['sha256']
            _need(type(name) is str and re.fullmatch(r'core-maintenance-[a-f0-9]{32}\.zip', name)
                  and a._hex(digest) and value['backup']['backup_id'] == _digest((name+':'+digest).encode()),
                  'BACKUP')
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
    _need(previous.get('switch_context') == current.get('switch_context'), 'JOURNAL_REWRITTEN')
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
    def _io(self, *, write=False, create=False, allow_missing=False, new_lock=False):
        _need(not (write and allow_missing) and (not new_lock or write and create), 'LAYOUT')
        if write:
            _need(os.geteuid() == (0 if self.layout.anchor == Path('/') else self.layout.owner), 'ROOT_REQUIRED')
        try:
            with _root(self.layout, create=create, allow_missing=allow_missing) as (scope, root):
                if write:
                    _lock(scope, root, require_new=new_lock)
                yield scope, root
        except (fs.CoreStoreError, a.CoreArtifactError, s.CoreSelectionError) as exc:
            raise CoreSelectionFilesError(exc.code) from None
        except OSError:
            raise CoreSelectionFilesError('METADATA_IO') from None

    @contextmanager
    def hold_restore_pending(self, journal):
        """Pin the public admission barrier for an already checked Root journal."""
        _need(journal['operation'] == 'restore', 'RECOVERY_OPERATION')
        with _root(self.layout) as (scope, root):
            raw, record = _read(scope, root, PENDING)
            _need(_pending(raw)['transaction'] == journal['transaction']
                  and record == journal['original_files']['pending'], 'PENDING_CHANGED')
            scope.stable()
            yield scope.stable

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
                         expected_files=None, restore_target=None, switch_context=None):
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
            expected = None if before is None else expected_files[key]
            result = _read(scope, root, name, optional=expected is None)
            observed = None if result is None else result[1]
            _need(observed == expected, 'PUBLIC_CHANGED')
            originals[key] = observed
        journal = dict(schema=1, kind='core-selection-journal-v1', transaction=transaction,
            sequence=0, previous_sha256=None, phase='admitting', operation=operation,
            before=None if before is None else _selection_map(before), after=_selection_map(after),
            pre_stop=_snapshot(snapshot, after if before is None else before), quiesced=None,
            backup=None, validation=None, original_files=originals, actions=[], rollback=None,
            known_generations=list(known_generations), restore_target=deepcopy(restore_target))
        if switch_context is not None:
            journal['switch_context'] = deepcopy(switch_context)
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

    def begin(self, plan, *, expected_revision, service_snapshot, initial_schemas=None, switch_context=None):
        """Begin an ordinary switch, or the first component switch from legacy bundles.

        The supplied schemas are observations from the live private worker,
        not proof of service stop or writers. Nothing publishes a selection
        until the same backup/validation/confirmation gates as later switches.
        """
        _need(type(plan) is s.SelectionPlan, 'PLAN')
        _validate_state(plan.before)
        _validate_state(plan.after)
        _need(expected_revision == plan.before.revision and a._hex(expected_revision), 'SELECTION_CONFLICT')
        _snapshot(service_snapshot, plan.before)
        if switch_context is not None:
            switch_context = _switch_context(switch_context, plan.before, service_snapshot['panel_running'])
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
        if initial_schemas is not None:
            _need(plan.operation == 'component' and plan.changed and _first_component_change(plan.before, plan.after),
                  'BOOTSTRAP_BASELINE')
            _keys(initial_schemas, s.CORES)
            for core in s.CORES:
                schema = initial_schemas[core]
                revision = service_snapshot['cores'][core]['revision']
                _need(schema is None and revision is None
                      or type(schema) is int and schema == 1 and revision is not None, 'WRITER_OBSERVATION')
            with self._io(write=True, create=True, new_lock=True) as (scope, root):
                _need(not any(_exists(root, name) for name in (SELECTION, FENCE, PENDING, ACTIVE, HISTORY)),
                      'ALREADY_INITIALIZED_OR_DAMAGED')
                _, journal = self._new_transaction(scope, root, before=plan.before, after=plan.after,
                    operation='initial_component', snapshot=service_snapshot, known_generations=[],
                    expected_files=dict(selection=None, fence=None), switch_context=switch_context)
                return journal['transaction']
        with self._io(write=True) as (scope, root):
            current, expected_files = self._stable(scope, root, plan.before.app_source, plan.before.target,
                                                   with_records=True)
            _need(current == plan.before and current.revision == expected_revision, 'SELECTION_CONFLICT')
            if not plan.changed:
                return None
            _, journal = self._new_transaction(scope, root, before=current, after=plan.after,
                operation=plan.operation, snapshot=service_snapshot, known_generations=[], expected_files=expected_files,
                switch_context=switch_context)
            return journal['transaction']

    def begin_restore(self, snapshot, *, app_source, target, expected_revision,
                      service_snapshot, source_backup_sha256, restored_intents, known_generations=(),
                      switch_context=None, data_plan=None):
        """Bind separately checked incoming intents; no data restore occurs.

        Source SHA and intents are trusted coordinator observations after its
        backup/material/state2/config checks, never authority from this API.
        The live quiesced intent remains separate for backup/failure rollback.
        """
        _validate_state(snapshot)
        _need(snapshot.app_source == app_source and snapshot.target == target, 'RESTORE_CONTEXT')
        _need(a._hex(source_backup_sha256) and (switch_context is None) == (data_plan is None),
              'RESTORE_TARGET')
        restore_target = dict(kind='core-restore-target-v1', source_backup_sha256=source_backup_sha256,
                              intents=_intent(restored_intents, snapshot))
        known = list(known_generations)
        _need(len(known) <= 14 and all(type(item) is int and 0 <= item <= s.MAX_GENERATION for item in known),
              'GENERATION')
        with self._io(write=True) as (scope, root):
            before, expected_files = self._stable(scope, root, app_source, target, with_records=True)
            _need(before.revision == expected_revision, 'SELECTION_CONFLICT')
            service_snapshot = _snapshot(service_snapshot, before)
            if switch_context is not None:
                switch_context = _switch_context(switch_context, before, service_snapshot['panel_running'])
                restore_target['kind'] = 'core-restore-target-v2'
                restore_target['data'] = _restore_data_plan(data_plan, switch_context)
            maximum = max([before.generation, snapshot.generation] + known)
            highest = s.MAX_GENERATION - 1 if switch_context is not None else s.MAX_GENERATION
            _need(maximum < highest, 'GENERATION_EXHAUSTED')
            after = s._build(app_source, target, maximum + 1, snapshot.slots)
            _, journal = self._new_transaction(scope, root, before=before, after=after,
                operation='restore', snapshot=service_snapshot,
                known_generations=known + [snapshot.generation], expected_files=expected_files,
                restore_target=restore_target, switch_context=switch_context)
            return journal['transaction']

    def _active(self, scope, root, transaction, app_source, target, phases):
        pending_raw, record = _read(scope, root, PENDING)
        _need(_pending(pending_raw)['transaction'] == transaction, 'TRANSACTION_CONFLICT')
        directory, journal, location = self._load(scope, root, transaction, app_source, target)
        _need(location == 'active' and _matches(record, journal['original_files']['pending']), 'PENDING_CHANGED')
        _need(journal['phase'] in phases, 'PHASE')
        return directory, journal

    def record_quiesced(self, transaction, observation, *, app_source, target, restore_data=None,
                        original_tree_sha256=None, prepared_tree_sha256=None):
        with self._io(write=True) as (scope, root):
            directory, journal = self._active(scope, root, transaction, app_source, target, ('prepared',))
            _require_restore_data(journal, restore_data, ('staged',))
            before = _selection(journal['before'], app_source, target)
            _keys(observation, ('panel_stopped', 'writers_exclusive', 'cores'))
            _need(observation['panel_stopped'] is True and observation['writers_exclusive'] is True, 'QUIESCENCE')
            _intent(observation['cores'], before)
            journal['quiesced'] = deepcopy(observation)
            if restore_data is not None:
                _need(a._hex(original_tree_sha256) and a._hex(prepared_tree_sha256), 'RESTORE_DATA_DIGEST')
                journal['quiesced']['original_tree_sha256'] = original_tree_sha256
                journal['quiesced']['prepared_tree_sha256'] = prepared_tree_sha256
            else:
                _need(original_tree_sha256 is None and prepared_tree_sha256 is None, 'RESTORE_DATA_PLAN')
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

    def provision(self, transaction, *, app_source, target, restore_data=None, restore_tree_sha256=None):
        with self._io(write=True) as (scope, root):
            directory, journal = self._active(scope, root, transaction, app_source, target, ('backed_up',))
            _require_restore_data(journal, restore_data, ('trial',))
            expected_digest = journal['quiesced'].get('prepared_tree_sha256')
            _need(restore_tree_sha256 == expected_digest, 'RESTORE_DATA_DIGEST')
            self._append(scope, directory, journal, phase='trial')
            return _selection(journal['after'], app_source, target)

    def trial_view(self, transaction, *, app_source, target):
        with self._io(write=True) as (scope, root):
            _, journal = self._active(scope, root, transaction, app_source, target, ('trial', 'validated'))
            return _selection(journal['after'], app_source, target)

    def record_validation(self, transaction, observation, *, app_source, target, restore_data=None):
        with self._io(write=True) as (scope, root):
            directory, journal = self._active(scope, root, transaction, app_source, target, ('trial',))
            _require_restore_data(journal, restore_data, ('trial',))
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

    def _settle_fence(self, scope, root, directory, journal, state):
        if journal['phase'] != 'cleanup':
            self._append(scope, directory, journal, phase='cleanup')
        current = _read(scope, root, FENCE, optional=True)
        planned = [action['new'] for action in journal['actions'] if action['public'] == FENCE]
        _need(current is None or any(_matches(current[1], record) for record in planned), 'PUBLIC_CHANGED')
        self._replace(scope, root, directory, journal, key='settle-fence', name=FENCE,
                      expected=None if current is None else current[1],
                      raw=_raw(_fence_for(state, journal['transaction'], settled=True)))

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
        if 'switch_context' in journal:
            if pending is None:
                archived_pending = _read(scope, directory, 'public-pending.json')
                _need(_matches(archived_pending[1], journal['original_files']['pending']), 'PENDING_CHANGED')
            # Retain the transaction locator and admission barrier throughout
            # the two-renames fence publication. Older journals keep their
            # original cleanup order for replay compatibility.
            self._settle_fence(scope, root, directory, journal, state)
        if pending is not None:
            scope.stable()
            _detach(scope, root, PENDING)
            fs._rename_new(root, PENDING, directory, 'public-pending.json')
        archived_pending = _read(scope, directory, 'public-pending.json')
        if not _matches(archived_pending[1], journal['original_files']['pending']):
            _return_foreign(scope, directory, 'public-pending.json', root, PENDING)
            raise CoreSelectionFilesError('PENDING_CHANGED')
        os.fsync(directory)
        os.fsync(root)
        if 'switch_context' not in journal:
            self._settle_fence(scope, root, directory, journal, state)

    def _confirmed_state(self, scope, root, directory, journal, app_source, target):
        _need(journal['phase'] in ('confirmed', 'cleanup'), 'PHASE')
        state = _selection(journal['after'] if journal['rollback'] is None
                           else journal['rollback']['selection'], app_source, target)
        current = _read(scope, root, SELECTION)
        planned = [action['new'] for action in journal['actions'] if action['public'] == SELECTION]
        _need(current[0] == s.encode_selection(state)
              and any(_matches(current[1], record) for record in planned), 'PUBLIC_CHANGED')
        if 'switch_context' in journal:
            actions = [action for action in journal['actions'] if action['public'] == FENCE]
            fence = _read(scope, root, FENCE, optional=True)
            if fence is None:
                # Legitimate absence is only the exact settle-fence rename gap.
                _need(any(action['key'] == 'settle-fence' and action['old'] is not None
                          and _exists(directory, action['old_slot'])
                          and _exists(directory, action['new_slot']) for action in actions),
                      'PUBLIC_CHANGED')
            else:
                _need(any(_matches(fence[1], action['new']) for action in actions), 'PUBLIC_CHANGED')
                value = _fence(fence[0])
                _need(value['transaction'] == journal['transaction']
                      and value['confirmed_revision'] == state.revision
                      and value['highest_generation'] == state.generation, 'FENCE_CONFLICT')
        return state

    def finish_cleanup(self, transaction, *, app_source, target, restore_data=None):
        """Explicitly finish metadata of an already durably confirmed journal.

        Never resumes a trial or records new host validation. A candidate without
        a durable confirmed journal must instead be externally restored first.
        """
        with self._io(write=True) as (scope, root):
            directory, journal, _ = self._load(scope, root, transaction, app_source, target)
            state = self._confirmed_state(scope, root, directory, journal, app_source, target)
            positions = (('trial',) if journal['rollback'] is None else
                         ('unplaced', 'staged') if journal['quiesced'] is None else ('staged', 'restored'))
            _require_restore_data(journal, restore_data, positions)
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

    def confirm(self, transaction, *, app_source, target, restore_data=None):
        with self._io(write=True) as (scope, root):
            directory, journal = self._active(scope, root, transaction, app_source, target, ('validated',))
            _require_restore_data(journal, restore_data, ('trial',))
            after = _selection(journal['after'], app_source, target)
            self._append(scope, directory, journal, phase='confirming')
            self._publish_confirmed(scope, root, directory, journal, after)
            return after

    def _rollback_highwater(self, scope, root, directory, journal, before, after, known):
        maximum = max([before.generation, after.generation] + journal['known_generations'] + list(known))
        for key, name in (('selection', SELECTION), ('fence', FENCE)):
            current = _read(scope, root, name, optional=True)
            original = journal['original_files'][key]
            actions = [action for action in journal['actions'] if action['public'] == name]
            if current is not None:
                allowed = [original, *(action['new'] for action in actions)]
                _need(any(record is not None and _matches(current[1], record) for record in allowed),
                      'PUBLIC_CHANGED')
                generation = (s.decode_selection(current[0], app_source=after.app_source,
                              target=after.target).generation if key == 'selection'
                              else _fence(current[0])['highest_generation'])
                maximum = max(maximum, generation)
            elif 'switch_context' in journal:
                # _load already checked the identity of every present private
                # slot. A missing original without a parked inode is drift.
                gap = any(_exists(directory, action['new_slot']) and
                          (action['old'] is None and original is None or
                           action['old'] is not None and _exists(directory, action['old_slot']))
                          for action in actions)
                _need(gap or original is None and not actions, 'PUBLIC_CHANGED')
        return maximum

    def read_switch_recovery(self):
        """Read durable switch facts without creating or clearing any barrier."""
        return self._read_recovery(restore=False)

    def read_restore_recovery(self):
        """Read the desired data disposition from the journal."""
        return self._read_recovery(restore=True)

    def _read_recovery(self, *, restore):
        with self._io() as (scope, root):
            pending = _read(scope, root, PENDING, optional=True)
            fence = _read(scope, root, FENCE, optional=True)
            if pending is not None:
                transaction = _pending(pending[0])['transaction']
            elif fence is not None:
                transaction = _fence(fence[0])['transaction']
            else:
                raise CoreSelectionFilesError('RECOVERY_IDENTITY_MISSING')
            if _exists(root, ACTIVE):
                directory = scope.directory(root, ACTIVE, mode=0o700)
            else:
                history = scope.directory(root, HISTORY, mode=0o700)
                directory = scope.directory(history, transaction, mode=0o700)
            first, _ = _read(scope, directory, 'journal-000.json', mode=0o600)
            after = a._json(first).get('after')
            _need(type(after) is dict and a._hex(after.get('app_source'), 40)
                  and type(after.get('target')) is str and after['target'] in a.TARGETS,
                  'SWITCH_CONTEXT')
            directory, journal, location = self._load(scope, root, transaction, after['app_source'], after['target'])
            _need('switch_context' in journal, 'SWITCH_CONTEXT_REQUIRED')
            _need((journal['operation'] == 'restore') == restore, 'RECOVERY_OPERATION')
            if pending is not None:
                _need(_matches(pending[1], journal['original_files']['pending']), 'PENDING_CHANGED')
            elif location == 'archive' and fence is not None and _fence(fence[0])['settled']:
                # The latest completed history is evidence, not a recovery task.
                raise CoreSelectionFilesError('NO_SWITCH_RECOVERY')
            else:
                _need(journal['phase'] in ('confirmed', 'cleanup'), 'RECOVERY_REQUIRED')
            result = deepcopy(journal)
            if journal['phase'] not in ('confirmed', 'cleanup'):
                before = _selection(journal['before'], after['app_source'], after['target'])
                proposed = _selection(journal['after'], after['app_source'], after['target'])
                maximum = self._rollback_highwater(scope, root, directory, journal, before, proposed, ())
                if journal['rollback'] is None:
                    _need(maximum < s.MAX_GENERATION, 'GENERATION_EXHAUSTED')
                    restored = s._build(before.app_source, before.target, maximum + 1, before.slots)
                else:
                    restored = _selection(journal['rollback']['selection'], after['app_source'], after['target'])
                    _need(maximum <= restored.generation, 'RECOVERY_CONFLICT')
                result['recovery_selection'] = _selection_map(restored)
            else:
                _need(pending is not None, 'NO_SWITCH_RECOVERY')
                confirmed = self._confirmed_state(scope, root, directory, journal,
                                                  after['app_source'], after['target'])
                result['confirmed_selection'] = _selection_map(confirmed)
            if restore:
                result['data_disposition'] = ('retain_incoming' if 'confirmed_selection' in result
                                              and journal['rollback'] is None else 'restore_original')
            return result

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
                          final_intents=None, restore_data=None, original_tree_sha256=None):
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
            quiesced = journal['quiesced']
            positions = ('staged', 'restored') if quiesced is not None else ('unplaced', 'staged')
            _require_restore_data(journal, restore_data, positions)
            expected_digest = quiesced.get('original_tree_sha256') if quiesced is not None else None
            _need(original_tree_sha256 == expected_digest, 'RESTORE_DATA_DIGEST')
            before = _selection(journal['before'], app_source, target)
            after = _selection(journal['after'], app_source, target)
            known = list(known_generations)
            _need(len(known) <= 14 and all(type(item) is int and 0 <= item <= s.MAX_GENERATION for item in known),
                  'GENERATION')
            maximum = self._rollback_highwater(scope, root, directory, journal, before, after, known)
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
