"""Private non-root maintenance worker for the root-owned managed controller.

The installed copy loads only its trusted sibling modules. It never
controls services and never accepts commands that skip ownership or leases.
The root controller must prove service quiescence before sending proceed, then
keep the worker alive until service recovery is verified and finish is sent.
"""
from __future__ import annotations

import importlib.util
from contextlib import contextmanager, ExitStack
import json
import os
from pathlib import Path
import re
import selectors
import signal
import sys
import time
import types


ROOT = Path('/var/lib/v-ui')
TIMEOUT = 600
LINE_LIMIT = 8192
RECOVERY_REQUEST_LIMIT = 16384  # Before/after/confirmed selections plus the retained switch context.
PATH_LIMIT = 4096


class WorkerError(RuntimeError):
    pass


class _Deadline(BaseException):
    pass


def _object(pairs):
    value = {}
    for name, item in pairs:
        if name in value:
            raise WorkerError('Duplicate protocol field')
        value[name] = item
    return value


class _Channel:
    def __init__(self, input_fd, output, deadline):
        self.input_fd, self.output, self.deadline = input_fd, output, deadline
        self.buffer = bytearray()

    def read(self, *, initial=False, restore_stage=False):
        limit = RECOVERY_REQUEST_LIMIT if initial or restore_stage else LINE_LIMIT
        while b'\n' not in self.buffer:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise WorkerError('Worker deadline reached')
            with selectors.DefaultSelector() as selector:
                selector.register(self.input_fd, selectors.EVENT_READ)
                if not selector.select(remaining):
                    raise WorkerError('Worker deadline reached')
            part = os.read(self.input_fd, limit + 1 - len(self.buffer))
            if not part:
                raise WorkerError('Controller disconnected')
            self.buffer.extend(part)
            if len(self.buffer) > limit and b'\n' not in self.buffer:
                raise WorkerError('Protocol line exceeded limit')
        line, _, rest = self.buffer.partition(b'\n')
        if len(line) + 1 > limit:
            raise WorkerError('Protocol line exceeded limit')
        self.buffer = bytearray(rest)
        value = json.loads(line.decode('utf-8'), object_pairs_hook=_object)
        if not isinstance(value, dict):
            raise WorkerError('Protocol object required')
        if initial and len(line) + 1 > LINE_LIMIT and value.get('action') != 'core_recover':
            raise WorkerError('Protocol line exceeded limit')
        return value

    def expect(self, command):
        if self.read() != {'command': command}:
            raise WorkerError('Unexpected controller command')

    def send(self, event, **values):
        self.output.write(json.dumps({'event': event, **values}, separators=(',', ':')) + '\n')
        self.output.flush()



def _command(value, allowed):
    if (set(value) != {'command'} or type(value.get('command')) is not str
            or value['command'] not in allowed):
        raise WorkerError('Unexpected controller command')
    return value['command']


def _prove_released_operation(session):
    # The phase is enforced by the private wire state machine. This is M9's
    # actual held-FD/closed-latch/PID proof, never an alive/approved bool.
    proof = getattr(session, '_operation_proof', None)
    if not callable(proof) or proof() is not None:
        raise WorkerError('Maintenance operation proof unavailable')


def _complete_writer_handshake(channel, session, action):
    if action == 'stage':
        channel.expect('finish')
        return
    command = _command(channel.read(), ('release_writers', 'release_writers_for_trial'))
    if command == 'release_writers':
        session.release_writers()
        channel.send('writers_released')
        channel.expect('finish')
        return
    session.release_writers_for_trial()
    channel.send('trial_writers_released')
    phase = 'trial_writers_released'
    while True:
        allowed = (('prove_operation', 'reacquire_writers', 'finish') if phase == 'trial_writers_released'
                   else ('release_reacquired_writers',) if phase == 'writers_reacquired'
                   else ('prove_operation', 'finish'))
        command = _command(channel.read(), allowed)
        if command == 'finish':
            # Root must have independently confirmed stable files/managed
            # released before this internal cleanup. finish grants nothing.
            return
        if command == 'prove_operation':
            _prove_released_operation(session)
            channel.send('operation_held')
        elif command == 'reacquire_writers':
            session.reacquire_writers()
            session._check_reacquired_writers()
            phase = 'writers_reacquired'
            channel.send(phase)
        else:
            session.release_reacquired_writers()
            phase = 'recovery_writers_released'
            channel.send(phase)



CORE_IMPLEMENTATIONS = (
    ('app.core_updates.artifacts', 'core_artifacts.py'),
    ('app.core_updates.selection', 'core_selection.py'),
    ('app.core_updates.binary', 'core_binary.py'),
    ('app.core_updates.store', 'core_store.py'),
    ('app.core_updates.github', 'core_github.py'),
    ('app.core_updates.catalog', 'core_catalog.py'),
    ('app.core_updates.resolver', 'core_resolver.py'),
    ('app.core_updates.application', 'core_application.py'),
    ('app.core_updates.selection_files', 'core_selection_files.py'),
    ('app.core_updates.boot', 'core_boot.py'),
    ('app.core_updates.backup', 'core_backup.py'),
    ('app.core_updates.backup_archive', 'core_backup_archive.py'),
    ('app.core_updates.maintenance', 'core_maintenance.py'),
    ('app.core_updates.maintenance_recovery', 'core_maintenance_recovery.py'),
)


def _load_core_session(release_tools, *, recovery=False, restore=False):
    """Only private requests need this fixed CONTROL closure, never payload."""
    directory = Path(__file__).resolve().parent
    names = ('app.core_updates', 'app.release_tools') + tuple(name for name, _ in CORE_IMPLEMENTATIONS)
    if any(name in sys.modules for name in names):
        raise WorkerError('Unexpected core maintenance namespace')
    if any(name not in sys.modules or getattr(sys.modules[name], '__path__', None) != []
           for name in ('app', 'app.services', 'app.certificates')):
        raise WorkerError('Core maintenance requires isolated control namespaces')
    loaded = []
    try:
        package = types.ModuleType('app.core_updates'); package.__path__ = []
        sys.modules['app.core_updates'] = package; loaded.append('app.core_updates')
        sys.modules['app.release_tools'] = release_tools; loaded.append('app.release_tools')
        for name, filename in CORE_IMPLEMENTATIONS:
            spec = importlib.util.spec_from_file_location(name, directory / filename)
            if spec is None or spec.loader is None:
                raise WorkerError('Core maintenance implementation unavailable')
            module = importlib.util.module_from_spec(spec); sys.modules[name] = module; loaded.append(name)
            spec.loader.exec_module(module)
        if recovery:
            return sys.modules['app.core_updates.maintenance_recovery'].CoreRecoverySession
        if restore:
            return sys.modules['app.core_updates.maintenance'].CoreRestorePreparation
        return sys.modules['app.core_updates.maintenance'].CoreMaintenanceSession
    except BaseException:
        for name in reversed(loaded):sys.modules.pop(name, None)
        raise


def _core_request(value):
    if (set(value) != {'protocol', 'action', 'core', 'expected_selection_revision'}
            or type(value.get('protocol')) is not int or value['protocol'] != 1
            or value.get('action') != 'core_prepare' or value.get('core') not in ('sing-box', 'xray')
            or not (value['expected_selection_revision'] is None or type(value['expected_selection_revision']) is str
                    and re.fullmatch('[a-f0-9]{64}', value['expected_selection_revision']))):
        raise WorkerError('Invalid private core maintenance request')
    return dict(core=value['core'], expected_selection_revision=value['expected_selection_revision'])


def _core_recovery_request(value):
    fields = ('transaction', 'context', 'before', 'after', 'final_intents', 'backup', 'allow_after_ids')
    if 'confirmed_selection' in value:
        fields += ('confirmed_selection',)
        if type(value['confirmed_selection']) is not dict or type(value.get('final_intents')) is not dict:
            raise WorkerError('Invalid confirmed core recovery request')
    if (set(value) != {'protocol', 'action', *fields}
            or type(value.get('protocol')) is not int or value['protocol'] != 1
            or value['action'] != 'core_recover'
            or type(value['transaction']) is not str or not re.fullmatch('[a-f0-9]{32}', value['transaction'])
            or any(type(value[key]) is not dict for key in ('context', 'before', 'after'))
            or value['final_intents'] is not None and type(value['final_intents']) is not dict
            or type(value['allow_after_ids']) is not bool
            or value['allow_after_ids'] and (value['backup'] is None or value['final_intents'] is None)):
        raise WorkerError('Invalid private core recovery request')
    backup = value['backup']
    if backup is not None:
        digests = ('backup_id', 'sha256', 'selection_revision', 'intents_sha256')
        if (type(backup) is not dict or set(backup) != {'kind', 'name', *digests}
                or backup['kind'] != 'backup-components-v1'
                or type(backup['name']) is not str or not re.fullmatch(r'core-maintenance-[a-f0-9]{32}\.zip', backup['name'])
                or any(type(backup[key]) is not str or not re.fullmatch('[a-f0-9]{64}', backup[key]) for key in digests)):
            raise WorkerError('Invalid private core recovery receipt')
    return {key: value[key] for key in fields}


def _core_fields(channel, command, fields=()):
    value = channel.read()
    if set(value) != {'command', *fields} or value.get('command') != command:
        raise WorkerError('Unexpected private core command')
    return value


def _probe_panel_tls(channel, session, value, *, before_stop=False):
    if (set(value) != {'command', 'require_login'} or value['command'] != 'probe_panel_tls'
            or type(value['require_login']) is not bool or before_stop and not value['require_login']):
        raise WorkerError('Unexpected private panel probe command')
    result = session.probe_panel_tls(require_login=value['require_login'])
    channel.send('panel_tls', status=result['status'], trust_sha256=result['trust_sha256'])


def _observe_restore_data(channel, session, journal, value):
    if value != {'command': 'observe_restore_data', 'transaction': journal['transaction']}:
        raise WorkerError('Unexpected private restore observation command')
    from app.core_updates.backup_archive import observe_restore_trial
    _verify_restore_offline(session, journal)
    data = observe_restore_trial(session.root, journal, lambda: _prove_released_operation(session))
    channel.send('restore_data_observed', transaction=journal['transaction'], data=data)


def _restore_observer_request(value):
    from app.core_updates import selection_files as files
    from app.core_updates import artifacts
    fields = {'protocol', 'action', 'transaction', 'slot', 'pending', 'original', 'incoming'}
    tls = {'trust_sha256', 'config_sha256'}
    has_tls = bool(tls & set(value))
    early = value.get('action') == 'core_restore_observe_early'
    if has_tls:
        fields |= tls
    if 'offline' in value:
        fields.add('offline')
    if early:
        fields.add('panel_running')
    if (set(value) != fields or type(value['protocol']) is not int or value['protocol'] != 1
            or value['action'] not in ('core_restore_observe', 'core_restore_observe_old', 'core_restore_observe_early')
            or any(type(value[key]) is not str or not re.fullmatch('[a-f0-9]{32}', value[key])
                   for key in ('transaction', 'slot'))
            or has_tls and any(not artifacts._hex(value[key]) for key in tls)
            or 'offline' in value and (type(value['offline']) is not dict
                or set(value['offline']) != {'application', 'selection', 'intents'}
                or not early and (has_tls or value['offline']['intents'] is None))
            or early and ('offline' not in value or type(value['panel_running']) is not bool
                          or has_tls and not value['panel_running'])):
        raise WorkerError('Invalid private restore observer request')
    files._record(value['pending'])
    original = files._data_generation(value['original'])
    incoming = files._data_generation(value['incoming'])
    if value['pending']['mode'] != 0o644 or original['dev'] != incoming['dev'] or original == incoming:
        raise WorkerError('Invalid private restore observer identity')
    # The Root controller checks the durable phase and complete journal. This
    # projection grants only read-only observations against the pinned marker.
    phase = 'rollback_required' if value['action'] != 'core_restore_observe' else 'backed_up'
    result = dict(operation='restore', phase=phase, transaction=value['transaction'],
                original_files=dict(pending=value['pending']),
                switch_context=dict(data_generation=original),
                restore_target=dict(kind='core-restore-target-v2',
                    data=dict(slot=value['slot'], incoming_generation=incoming)))
    if has_tls:
        result['tls'] = {key: value[key] for key in tls}
    if 'offline' in value:
        result['offline'] = value['offline']
    if early:
        result.update(early=True, panel_running=value['panel_running'])
    return result


def _restore_exchange_request(value):
    from app.core_updates import artifacts
    from app.core_updates.selection_files import MAX_JOURNALS
    fields = {'sequence', 'original_tree_sha256', 'prepared_tree_sha256'}
    rollback = value.get('action') == 'core_restore_rollback'
    digests = fields - {'sequence'}
    if (value.get('action') not in ('core_restore_exchange', 'core_restore_rollback')
            or not fields <= set(value) or type(value['sequence']) is not int
            or not 0 <= value['sequence'] < MAX_JOURNALS
            or not (all(artifacts._hex(value[key]) for key in digests)
                    or rollback and all(value[key] is None for key in digests))):
        raise WorkerError('Invalid private restore exchange request')
    projection = {key: item for key, item in value.items() if key not in fields}
    if {'trust_sha256', 'config_sha256', 'offline', 'panel_running'} & set(projection):
        raise WorkerError('Unexpected private restore exchange fields')
    projection['action'] = 'core_restore_observe'
    journal = _restore_observer_request(projection)
    journal['sequence'] = value['sequence']
    journal['quiesced'] = ({key: value[key] for key in digests}
                           if value['original_tree_sha256'] is not None else None)
    if rollback:
        journal['phase'] = 'rollback_required'
    return journal


def _complete_restore_exchange(channel, root, release_tools, journal):
    from app.data_owner import data_owner
    from app.core_updates.backup_archive import (SelectionFiles, exchange_restore_data,
                                                  _inspect_restore_slots, _restore_slot_names)
    with release_tools._maintenance_directory(root):
        pass
    with ExitStack() as leases:
        proof = leases.enter_context(release_tools._maintenance_lease(root, '.operation.lock', _proof=True))
        leases.enter_context(release_tools._maintenance_lease(root, '.panel.lease'))
        leases.enter_context(data_owner(root/'data', exclusive=True))
        files = SelectionFiles()
        pending = leases.enter_context(files.hold_restore_pending(journal))
        names = _restore_slot_names(journal['restore_target']['data']['slot'])
        recovery = root/'recovery'
        rollback = journal['phase'] == 'rollback_required'
        try:
            recovery_fd = leases.enter_context(release_tools._maintenance_directory(recovery))
        except FileNotFoundError:
            if not rollback or journal['quiesced'] is not None:
                raise
            recovery_fd = None
        if recovery_fd is not None:
            root_fd = leases.enter_context(release_tools._maintenance_directory(root))
            parents = {key: root_fd if key == 'live' else recovery_fd for key in names}
            position, observed = _inspect_restore_slots(root, journal, names, parents, root_fd, recovery_fd)
            allowed = (('unplaced', 'staged') if journal['quiesced'] is None else
                       ('staged', 'parked', 'trial', 'rollback_gap', 'restored') if rollback else
                       ('staged', 'parked', 'trial'))
            if position not in allowed:
                raise WorkerError('Unexpected private restore exchange layout')
            for key in ('live', 'incoming', 'original', 'rejected'):
                if observed[key] is not None:
                    pending()
                    path = root/names[key] if key == 'live' else recovery/names[key]
                    leases.enter_context(release_tools._offline_writers(path, stable=False))
        session = types.SimpleNamespace(root=root, _operation_proof=proof)
        guard = release_tools._StoppedGuard(root)
        try:
            _prove_released_operation(session)
            receipt = exchange_restore_data(guard, journal, rollback=rollback, _selection_files=files)
            _prove_released_operation(session)
            channel.send('restore_data_exchanged', receipt=receipt)
            while _command(channel.read(), ('prove_operation', 'finish')) != 'finish':
                _prove_released_operation(session)
                channel.send('operation_held')
        finally:
            guard._valid = False


def _verify_restore_offline(session, journal):
    if 'offline' not in journal:
        return
    from app.core_updates.maintenance import verify_stopped_restore
    generation = (journal['switch_context']['data_generation'] if journal['phase'] == 'rollback_required'
                  else journal['restore_target']['data']['incoming_generation'])
    return verify_stopped_restore(session.root, generation, journal['offline'],
                                  lambda: _prove_released_operation(session))


def _complete_restore_observer(channel, root, release_tools, journal):
    from app.core_updates.backup_archive import observe_restore_trial
    with release_tools._maintenance_directory(root):
        pass
    with release_tools._maintenance_lease(root, '.operation.lock', _proof=True) as proof:
        session = types.SimpleNamespace(root=root, _operation_proof=proof)
        observe_restore_trial(root, journal, lambda: _prove_released_operation(session))
        intents = _verify_restore_offline(session, journal)
        if 'tls' in journal:
            from app.core_updates.maintenance import RestorePanelProbe
            generation = (journal['switch_context']['data_generation']
                          if journal['phase'] == 'rollback_required' else
                          journal['restore_target']['data']['incoming_generation'])
            session.probe_panel_tls = RestorePanelProbe(root, generation, journal['tls']['trust_sha256'],
                journal['tls']['config_sha256'], lambda: _prove_released_operation(session))
        ready = dict(transaction=journal['transaction'])
        if journal.get('early'):
            ready['final_intents'] = intents
            if journal['panel_running']:
                journal.pop('offline')
            else:
                journal['offline'] = dict(journal['offline'], intents=intents)
            observe_restore_trial(root, journal, lambda: _prove_released_operation(session))
        channel.send('restore_observer_ready', **ready)
        while True:
            value = channel.read()
            if value.get('command') == 'probe_panel_tls' and 'tls' in journal:
                _probe_panel_tls(channel, session, value)
                continue
            if value.get('command') == 'observe_restore_data':
                _observe_restore_data(channel, session, journal, value)
                continue
            if _command(value, ('prove_operation', 'finish')) == 'finish':
                return
            _prove_released_operation(session)
            channel.send('operation_held')


def _complete_restore_preparation(channel, session):
    with _quiet_candidate_io():
        prepared = session.prepare()
    channel.send('restore_preflight', **prepared)
    value = channel.read(restore_stage=True)
    if value == {'command': 'finish'}:
        return
    if set(value) != {'command', 'journal'} or value['command'] != 'stage_restore':
        raise WorkerError('Unexpected private restore staging command')
    with _quiet_candidate_io():
        staged = session.stage_restore(value['journal'])
    channel.send('restore_staged', **staged)
    value = _core_fields(channel, 'backup_core', ('snapshot',))
    with _quiet_candidate_io():
        backup = session.backup_core(value['snapshot'])
    channel.send('core_backed_up', **backup)
    channel.expect('finish')


def _complete_core_handshake(channel, session):
    with _quiet_candidate_io():facts = session.prepare()
    channel.send('core_context', **facts)
    value = channel.read()
    if value == {'command': 'finish'}:return
    if set(value) != {'command', 'candidate'} or value.get('command') != 'check_core':
        raise WorkerError('Unexpected private core command')
    with _quiet_candidate_io():facts = session.check_core(value['candidate'])
    channel.send('prepared', **facts)
    while True:
        value = channel.read()
        if value == {'command': 'proceed'}:
            break
        _probe_panel_tls(channel, session, value, before_stop=True)
    with _quiet_candidate_io():facts = session.execute()
    channel.send('core_quiesced', **facts)
    value = _core_fields(channel, 'backup_core', ('snapshot',))
    with _quiet_candidate_io():facts = session.backup_core(value['snapshot'])
    channel.send('core_backed_up', **facts)
    value = _core_fields(channel, 'bind_core_ids', ('backup_sha256',))
    with _quiet_candidate_io():facts = session.bind_core_ids(value['backup_sha256'])
    channel.send('core_ids_bound', **facts)
    channel.expect('release_writers_for_trial')
    session.release_writers_for_trial();channel.send('trial_writers_released')
    phase = 'trial_writers_released'
    while True:
        allowed = (('prove_operation', 'reacquire_writers', 'finish') if phase == 'trial_writers_released'
                   else ('restore_core_ids',) if phase == 'writers_reacquired'
                   else ('release_reacquired_writers',) if phase == 'core_ids_restored'
                   else ('prove_operation', 'finish'))
        value = channel.read()
        if value.get('command') == 'probe_panel_tls' and phase in ('trial_writers_released', 'recovery_writers_released'):
            _probe_panel_tls(channel, session, value)
            continue
        command = _command(value, allowed)
        if command == 'finish':return
        if command == 'prove_operation':
            _prove_released_operation(session);channel.send('operation_held')
        elif command == 'reacquire_writers':
            session.reacquire_writers();session._check_reacquired_writers()
            phase = 'writers_reacquired';channel.send(phase)
        elif command == 'restore_core_ids':
            with _quiet_candidate_io():facts = session.restore_core_ids()
            phase = 'core_ids_restored';channel.send(phase, **facts)
        else:
            session.release_reacquired_writers();phase = 'recovery_writers_released';channel.send(phase)


def _complete_core_recovery_handshake(channel, session):
    with _quiet_candidate_io():
        prepared = session.prepare()
    channel.send('core_recovery_prepared', **prepared)
    channel.expect('proceed')
    with _quiet_candidate_io():
        bound = session.execute()
    channel.send('core_recovery_bound', **bound)
    channel.expect('release_recovery_writers')
    session.release_recovery_writers()
    channel.send('recovery_writers_released')
    while True:
        value = channel.read()
        if value.get('command') == 'probe_panel_tls':
            _probe_panel_tls(channel, session, value)
            continue
        if _command(value, ('prove_operation', 'finish')) == 'finish':
            return
        _prove_released_operation(session)
        channel.send('operation_held')


def _request(value):
    action = value.get('action')
    if type(value.get('protocol')) is not int or value['protocol'] != 1 or not isinstance(action, str):
        raise WorkerError('Unsupported maintenance protocol')
    fields = {'protocol', 'action'}
    if action in ('restore', 'stage'):
        fields |= {'path', 'sha256'}
    elif action == 'activate':
        fields.add('release_id')
    elif action not in ('start', 'stop', 'restart', 'backup', 'recover-restore'):
        raise WorkerError('Unknown maintenance action')
    if set(value) != fields:
        raise WorkerError('Unexpected maintenance fields')
    result = {}
    if action in ('restore', 'stage'):
        path, checksum = value['path'], value['sha256']
        if (not isinstance(path, str) or not 0 < len(path) <= PATH_LIMIT
                or any(ord(char) < 32 or ord(char) == 127 for char in path)
                or not Path(path).is_absolute() or '..' in Path(path).parts
                or not isinstance(checksum, str) or not re.fullmatch(r'[a-f0-9]{64}', checksum)):
            raise WorkerError('Invalid restore input')
        result.update(archive=Path(path), sha256=checksum)
    if action == 'activate':
        identity = value['release_id']
        if not isinstance(identity, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,100}', identity):
            raise WorkerError('Invalid release identifier')
        result['identity'] = identity
    return action, result


@contextmanager
def _quiet_candidate_io():
    """Candidate subprocesses get neither protocol input nor event output."""
    sys.stdout.flush(); sys.stderr.flush()
    saved = [os.dup(fd) for fd in (0, 1, 2)]
    for fd in saved:
        os.set_inheritable(fd, False)
    sink = os.open(os.devnull, os.O_RDWR)
    try:
        for fd in (0, 1, 2):
            os.dup2(sink, fd)
        yield
    finally:
        sys.stdout.flush(); sys.stderr.flush()
        for fd, original in zip((0, 1, 2), saved):
            os.dup2(original, fd); os.close(original)
        os.close(sink)


def _run(root, release_tools, input_fd, output, *, timeout=TIMEOUT):
    """Private testable implementation; the executable has no root/path flags."""
    deadline = time.monotonic() + timeout
    channel = _Channel(input_fd, output, deadline)
    session = None
    previous_alarm = signal.getsignal(signal.SIGALRM)
    previous_term = signal.getsignal(signal.SIGTERM)
    previous_int = signal.getsignal(signal.SIGINT)

    deadline_active = True

    def interrupted(number, _frame):
        if number != signal.SIGALRM or deadline_active:
            raise _Deadline()

    signal.signal(signal.SIGALRM, interrupted)
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    signal.setitimer(signal.ITIMER_REAL, timeout)
    try:
        try:
            if os.geteuid() == 0:
                raise WorkerError('Worker must run as the service user')
            os.umask(0o077)
            request = channel.read(initial=True)
            if request.get('action') == 'core_prepare':
                arguments = _core_request(request)
                implementation = _load_core_session(release_tools)
                session = implementation(root, **arguments)
                _complete_core_handshake(channel, session)
            elif request.get('action') == 'core_recover':
                arguments = _core_recovery_request(request)
                implementation = _load_core_session(release_tools, recovery=True)
                session = implementation(root, **arguments)
                _complete_core_recovery_handshake(channel, session)
            elif request.get('action') in ('core_restore_observe', 'core_restore_observe_old', 'core_restore_observe_early'):
                _load_core_session(release_tools)
                journal = _restore_observer_request(request)
                _complete_restore_observer(channel, root, release_tools, journal)
            elif request.get('action') in ('core_restore_exchange', 'core_restore_rollback'):
                _load_core_session(release_tools)
                journal = _restore_exchange_request(request)
                _complete_restore_exchange(channel, root, release_tools, journal)
            elif request.get('action') == 'core_restore_prepare':
                fields = {'archive_name', 'archive_sha256', 'expected_selection_revision'}
                if (set(request) != {'protocol', 'action', *fields}
                        or type(request['protocol']) is not int or request['protocol'] != 1):
                    raise WorkerError('Invalid private restore preparation request')
                implementation = _load_core_session(release_tools, restore=True)
                session = implementation(root, **{key: request[key] for key in fields})
                _complete_restore_preparation(channel, session)
            else:
                action, arguments = _request(request)
                if action in ('start', 'restart'):
                    from app.data_owner import core_selection_present
                    if core_selection_present():
                        _load_core_session(release_tools)
                session = release_tools._MaintenanceSession(root)
                session.prepare(action, **arguments)
                channel.send('prepared')
                channel.expect('proceed')
                with _quiet_candidate_io():
                    result = session.execute()
                channel.send('done', ok=True, **result)
                _complete_writer_handshake(channel, session, action)
            status = 0
        finally:
            # The selector can reach the same deadline before SIGALRM is
            # dispatched. Retire it before encoding the fixed error frame.
            deadline_active = False
            signal.setitimer(signal.ITIMER_REAL, 0)
    except (Exception, _Deadline):
        deadline_active = False
        status = 1
        try: channel.send('error', code='MAINTENANCE_REFUSED')
        except (OSError, ValueError, _Deadline): pass
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        try:
            if session is not None:
                session.close()
        except (Exception, _Deadline):
            # A cleanup failure is still a failed worker transaction, including
            # after finish; do not override it with an earlier success return.
            try: channel.send('error', code='MAINTENANCE_REFUSED')
            except (OSError, ValueError): pass
            status = 1
        finally:
            signal.signal(signal.SIGALRM, previous_alarm)
            signal.signal(signal.SIGTERM, previous_term)
            signal.signal(signal.SIGINT, previous_int)
    return status


def _load_release_tools():
    # The root controller verified these fixed sibling files against its receipt.
    # -I -S excludes cwd/PYTHONPATH/site hooks. Do not add a package payload (or
    # any directory) to sys.path: create inert, pathless namespaces and load only
    # the exact stdlib dependency closure needed by backup/restore. No app startup,
    # parser, core binary, or service-owned installed code is imported here.
    directory = Path(__file__).resolve().parent
    packages = ('app', 'app.services', 'app.certificates')
    implementations = (('app.data_owner', 'data_owner.py'),
                       ('app.services.upstream_profile', 'upstream_profile.py'),
                       ('app.services.inbound_policy', 'inbound_policy.py'),
                       ('app.services.proxy_store', 'proxy_store.py'),
                       ('app.certificates.errors', 'certificate_errors.py'),
                       ('app.certificates.dns_store', 'dns_store.py'),
                       ('app.certificates.cloudflare', 'cloudflare.py'),
                       ('app.certificates.dns_journal', 'dns_journal.py'),
                       ('app.certificates.dns_backup', 'dns_backup.py'),
                       ('_vui_managed_release_tools', 'release_tools.py'))
    names = packages + tuple(name for name, _ in implementations)
    if any(name in sys.modules for name in names):
        raise WorkerError('Unexpected application modules in isolated worker')
    loaded = []
    try:
        for name in packages:
            package = types.ModuleType(name)
            package.__path__ = []
            sys.modules[name] = package
            loaded.append(name)
        for name, filename in implementations:
            spec = importlib.util.spec_from_file_location(name, directory / filename)
            if spec is None or spec.loader is None:
                raise WorkerError('Maintenance implementation unavailable')
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            loaded.append(name)
            spec.loader.exec_module(module)
        return module
    except BaseException:
        for name in reversed(loaded):
            sys.modules.pop(name, None)
        raise


def main():
    if len(sys.argv) != 1 or os.geteuid() == 0:
        return 1
    try:
        return _run(ROOT, _load_release_tools(), sys.stdin.fileno(), sys.stdout)
    except Exception:
        print('{"event":"error","code":"MAINTENANCE_REFUSED"}', flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
