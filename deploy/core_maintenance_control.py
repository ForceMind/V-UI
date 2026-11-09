"""Private Root coordinator for a single official core switch.

This entry is not a public daemon action. It consumes a fixed non-root worker
and Root-owned metadata; it never imports or opens service-owned application
files or data. Native systemd qualification is separate from pipe contracts.
"""
from contextlib import contextmanager
import hashlib
import json
import os
import re
import sys
import tempfile
import threading
import time
import types
import uuid


class CoreControlError(RuntimeError):
    """Fixed, non-sensitive failure at the Root task boundary."""


def _need(value, code):
    if not value:
        raise CoreControlError(code)


_CONTROL_MODULES = (
    ('artifacts', 'core_artifacts.py'),
    ('selection', 'core_selection.py'),
    ('store', 'core_store.py'),
    ('github', 'core_github.py'),
    ('catalog', 'core_catalog.py'),
    ('selection_files', 'core_selection_files.py'),
    ('broker', 'core_boot_broker.py'),
)


@contextmanager
def _modules(ops):
    """Compile only the required Root receipt-bound bytes, in a pathless package."""
    _need(os.geteuid() == 0, 'ROOT_REQUIRED')
    package_name = '_vui_fixed_core_control'
    names = [package_name] + [package_name + '.' + name for name, _ in _CONTROL_MODULES]
    _need(not any(name in sys.modules for name in names), 'CORE_NAMESPACE')
    receipt = ops.decode_json(ops.trusted_bytes(ops.RECEIPT, ops.MAX_JSON))
    content = {}
    for name, filename in _CONTROL_MODULES:
        path = ops.CONTROL / filename
        raw = ops.trusted_bytes(path, ops.MAX_FILE)
        _need(hashlib.sha256(raw).hexdigest() == receipt['files'].get(str(path)), 'CORE_CONTROL_CHANGED')
        content[name] = (path, raw)
    package = types.ModuleType(package_name)
    package.__path__ = []
    sys.modules[package_name] = package
    try:
        for name, (path, raw) in content.items():
            full_name = package_name + '.' + name
            module = types.ModuleType(full_name)
            module.__package__ = package_name
            module.__file__ = str(path)
            sys.modules[full_name] = module
            setattr(package, name, module)
            exec(compile(raw, str(path), 'exec'), module.__dict__)
        yield package
    finally:
        for name in reversed(names):
            sys.modules.pop(name, None)


class _Worker:
    """Serialize the entire pipe roundtrip, including broker-thread proof."""
    def __init__(self, child):
        self.child = child
        self._mutex = threading.Lock()
        self._closed = False

    def exchange(self, request, event, fields=()):
        with self._mutex:
            _need(not self._closed, 'WORKER_CLOSED')
            self.child.send(request)
            value = self.child.receive()
            _need(type(value) is dict and set(value) == {'event', *fields}
                  and value['event'] == event, 'CORE_WORKER_PROTOCOL')
            return {key: value[key] for key in fields}

    def command(self, command, event, fields=(), **values):
        return self.exchange(dict(command=command, **values), event, fields)

    def proof(self):
        self.command('prove_operation', 'operation_held')

    def finish(self):
        with self._mutex:
            _need(not self._closed, 'WORKER_CLOSED')
            self.child.send({'command': 'finish'})
            self.child.exit_zero()

    def close(self):
        with self._mutex:
            if self._closed:
                return
            self._closed = True
            self.child.close()


def _start_worker(ops, account, deadline, cancelled):
    child = ops.Child([str(ops.PYTHON), '-B', '-I', '-S', str(ops.WORKER)],
                      max(0, deadline-time.monotonic()), account=account, cancelled=cancelled)
    return _Worker(child)


class _Switch:
    def __init__(self, ops, modules, services, account, worker, installation_proof,
                 config, *, deadline, cancelled):
        self.ops, self.modules, self.services, self.account = ops, modules, services, account
        self.worker, self.installation_proof, self.config = worker, installation_proof, config
        self.deadline, self.cancelled = deadline, cancelled
        self.a, self.s, self.m, self.wire = (modules.artifacts, modules.selection,
                                           modules.selection_files, modules.broker)
        self.files = self.m.SelectionFiles()
        self.catalog = modules.catalog.CoreCatalog()
        self.store = modules.store.CoreArtifactStore()
        self.transaction = None
        self.stop_attempted = False
        self.released = False
        self.confirming = False
        self.broker_closed = True
        self.broker = None
        self.trust_sha256 = None

    def _time(self):
        remaining = self.deadline - time.monotonic()
        _need(remaining > 0 and not self.cancelled, 'CORE_TASK_CANCELLED')
        return remaining

    def _proof(self):
        self._time()
        _need(self.installation_proof() is None, 'INSTALLATION_PROOF')
        self.worker.proof()
        _need(self.installation_proof() is None, 'INSTALLATION_PROOF')

    def _state(self, value):
        return self.s.decode_selection(self.m._raw(value), **self.source)

    def _intents(self, value, state):
        return self.m._intent(value, state)

    def _context(self, core, expected_revision):
        facts = self.worker.exchange(dict(protocol=1, action='core_prepare', core=core,
            expected_selection_revision=expected_revision), 'core_context',
            ('application', 'data_generation', 'selection', 'selection_initialized',
             'bundles', 'intents', 'runtime_schemas'))
        self._accept_context(facts, core, expected_revision)

    def _accept_context(self, facts, core, expected_revision):
        _need(type(facts) is dict and set(facts) == {'application', 'data_generation', 'selection',
            'selection_initialized', 'bundles', 'intents', 'runtime_schemas'}, 'CORE_APPLICATION_CONTEXT')
        app = facts['application']
        _need(type(app) is dict and set(app) == {'app_source', 'target', 'release_id',
            'current_sha256', 'manifest_sha256', 'ready_sha256'}
            and self.a._hex(app['app_source'], 40) and app['target'] in self.a.TARGETS
            and app['target'] == self.config.get('target')
            and type(app['release_id']) is str and self.ops.RELEASE.fullmatch(app['release_id'])
            and all(self.a._hex(app[name]) for name in ('current_sha256', 'manifest_sha256', 'ready_sha256')),
            'CORE_APPLICATION_CONTEXT')
        self.source = dict(app_source=app['app_source'], target=app['target'])
        self.application = app
        self.generation = facts['data_generation']
        self.wire._generation(self.generation)
        self.before = self._state(facts['selection'])
        self.initialized = facts['selection_initialized']
        _need(type(self.initialized) is bool, 'CORE_SELECTION_CONTEXT')
        self.s._base(self.before, core, self.before.revision)
        self._intents(facts['intents'], self.before)
        _need(type(facts['bundles']) is dict and set(facts['bundles']) == set(self.s.CORES),
              'CORE_APPLICATION_CONTEXT')
        self.bundles = {key:self.s._decode_reference(value) for key, value in facts['bundles'].items()}
        _need(all(type(ref) is self.s.BundleReference and ref.core == name
                  and ref.app_source == app['app_source'] and ref.target == app['target']
                  and ref.payload_manifest_sha256 == app['manifest_sha256']
                  for name, ref in self.bundles.items()), 'CORE_APPLICATION_CONTEXT')
        self.initial_schemas = facts['runtime_schemas']
        current = self.files.read_boot(**self.source)
        if self.initialized:
            _need(current == self.before and expected_revision == self.before.revision, 'CORE_SELECTION_CONTEXT')
        else:
            _need(current is None and expected_revision is None
                  and self.before == self.s.initial_selection(bundles=self.bundles, **self.source),
                  'CORE_SELECTION_CONTEXT')

    def _plan(self, core, action, release_id):
        previous = self.before.slot(core).previous
        if action == 'rollback':
            _need(self.initialized and previous is not None, 'NO_PREVIOUS_CORE')
            if type(previous) is self.s.BundleReference:
                _need(previous == self.bundles[core], 'PREVIOUS_BUNDLE_UNAVAILABLE')
                identity = previous
            else:
                entry = self.catalog.read(previous.release_manifest_sha256, app_source=self.source['app_source'])
                identity = self.store.read(previous.release_manifest_sha256, authority=entry.authority,
                    expected_tag=entry.authority.tag, expected_builder_source=entry.tag_source, **self.source).identity
            return self.s.plan_rollback(self.before, core=core, material=identity,
                                        expected_revision=self.before.revision)
        client = self.modules.github.GitHubCoreClient()
        kwargs = dict(deadline=self.deadline, stop=lambda: bool(self.cancelled))
        candidate = client.inspect(release_id, app_source=self.source['app_source'], **kwargs)
        _need(self.a._tag(candidate.authority.tag)[0] == core, 'CORE_CANDIDATE_CONTEXT')
        current = self.before.slot(core).current
        if (type(current) is self.s.ComponentReference
                and current.release_manifest_sha256 == candidate.verified_release.manifest_sha256):
            return None
        _need(self.installation_proof() is None, 'INSTALLATION_PROOF')
        with tempfile.NamedTemporaryFile(prefix='vui-core-candidate-', dir='/var/tmp') as pack:
            client.download_pack(candidate, self.source['target'], pack.fileno(), **kwargs)
            entry = self.catalog.record(candidate, app_source=self.source['app_source'], client=client, **kwargs)
            identity = self.store.prepare(pack.fileno(), candidate.manifest, candidate.checksums,
                authority=entry.authority, expected_tag=entry.authority.tag,
                expected_builder_source=entry.tag_source, **self.source).identity
        return self.s.plan_component(self.before, core=core, identity=identity,
                                     expected_revision=self.before.revision)

    def _barriers(self):
        # The private worker refuses RESTORE_PENDING before/under writers, and
        # keeps its operation lease. Never open that service-owned path as root.
        values = {'restore_pending_sha256': self.wire.file_digest(None, None)}
        with self.files._io() as (scope, directory):
            for key, name in (('core_pending_sha256', self.m.PENDING),
                              ('core_selection_sha256', self.m.SELECTION),
                              ('core_selection_fence_sha256', self.m.FENCE)):
                result = self.m._read(scope, directory, name, optional=True)
                values[key] = (self.wire.file_digest(tuple(result[1]['identity']), result[0])
                               if result is not None else self.wire.file_digest(None, None))
        return values

    def _identity(self):
        return self.wire.PeerIdentity(**self.services.managed_identity(self.account.pw_uid))

    def _await_identity(self):
        # Type=simple can report the exact executor before its UID drop.
        # Only this typed transient is retried, and only before arming.
        deadline = time.monotonic() + min(10, self._time())
        previous = self.services.deadline
        # Before arm there is no broker identity callback using Services yet.
        self.services.deadline = min(previous, deadline) if previous is not None else deadline
        try:
            while time.monotonic() < deadline:
                self._time()
                try:
                    identity = self._identity()
                except self.ops.IdentityStarting:
                    time.sleep(min(.05, max(0, deadline-time.monotonic())))
                else:
                    _need(time.monotonic() < deadline, 'CORE_PANEL_IDENTITY_TIMEOUT')
                    self._time()
                    return identity
            raise CoreControlError('CORE_PANEL_IDENTITY_TIMEOUT')
        finally:
            self.services.deadline = previous

    def _tls(self, identity, *, login=False):
        deadline = time.monotonic() + min(30, self._time())
        while time.monotonic() < deadline:
            self._time()
            _need(self._identity() == identity, 'CORE_PANEL_IDENTITY_CHANGED')
            if self.config.get('certificate_mode') == 'provided':
                response = self.worker.command('probe_panel_tls', 'panel_tls',
                                               ('status', 'trust_sha256'), require_login=login)
                status = response['status']
                if status == 'ready':
                    digest = response['trust_sha256']
                    _need(self.a._hex(digest) and self.trust_sha256 in (None, digest),
                          'CORE_PANEL_TRUST_CHANGED')
                    self.trust_sha256 = digest
                else:
                    _need(status in ('retry', 'pending'), 'CORE_PANEL_TLS_REJECTED')
            else:
                result = self.ops.local_panel_tls(self.config, require_login=login)
                status = 'ready' if result.startswith('ready（') else 'retry'
                _need(status == 'ready' or result in (
                    'unknown（本机 TLS 握手未完成）', 'pending（面板仍在维护）'), 'CORE_PANEL_TLS_REJECTED')
            if status == 'ready':
                _need(self._identity() == identity, 'CORE_PANEL_IDENTITY_CHANGED')
                _need(time.monotonic() < deadline, 'CORE_PANEL_TLS_TIMEOUT')
                self._time()
                return
            time.sleep(.1)
        raise CoreControlError('CORE_PANEL_TLS_TIMEOUT')

    def _validation(self, state, intents, observed):
        # Running rows come from the exact authenticated app's real private
        # probe. Stopped rows attest the already verified material only.
        running = observed is not None
        rows = {}
        for core in self.s.CORES:
            ref = state.slot(core).current
            row = observed['cores'][core] if running else None
            live = row is not None and row['running']
            rows[core] = dict(artifact_id=row['artifact_id'] if live else self.s.reference_identity(ref),
                binary_sha256=row['binary_sha256'] if live else ref.binary_sha256,
                revision=row['revision'] if running else intents[core]['revision'],
                enabled=row['enabled'] if running else intents[core]['enabled'],
                owned_listeners_verified=row['owned_listeners_ready'] if running else False)
        result = dict(selection_revision=state.revision, panel_running=running,
                      panel_health_verified=running, cores=rows)
        return self.m._validation(result, state, intents, self.initial['v-ui.service'] == 'active')

    def _close_broker(self):
        broker = self.broker
        if broker is None:
            return
        try:
            broker.close()
        except BaseException as error:
            unfinished = not broker.callbacks_finished
            timed_out = (isinstance(error, self.wire.CoreBootBrokerError)
                         and error.code == 'THREAD_STILL_RUNNING')
            if unfinished or timed_out:
                # A join timeout occurs before endpoint/FD cleanup, even if
                # the thread exits immediately afterwards. Always finish it.
                if unfinished:
                    self.cancelled.append(True)
                try:
                    broker.close(_join_timeout=15)
                except BaseException as second:
                    if not broker.callbacks_finished:
                        # The isolated daemon task exits with the lock still
                        # held. Kernel exit terminates all its callbacks.
                        os._exit(1)
                    if (isinstance(second, self.wire.CoreBootBrokerError)
                            and second.code == 'THREAD_STILL_RUNNING'):
                        # The thread ended after the second join check. Finish
                        # the endpoint/FD cleanup directly, with no new join.
                        try:
                            broker._finish_close()
                        except BaseException:
                            pass
                        else:
                            self.broker_closed = True
                else:
                    self.broker_closed = True
            self.broker = None
            raise
        self.broker = None
        self.broker_closed = True

    def _restore_trial_journal(self):
        journal = self.files.read_restore_recovery()
        target = journal['restore_target']
        context = journal['switch_context']
        _need(journal['transaction'] == self.transaction and journal['phase'] == 'backed_up'
              and target['kind'] == 'core-restore-target-v2'
              and context['application'] == self.application
              and context['services'] == self.initial
              and self.m._digest(self.m._raw(self.config)) == context['config_sha256']
              and self.config.get('certificate_mode', 'managed') == context['certificate_mode'],
              'CORE_RESTORE_CONTEXT')
        _need(context['certificate_mode'] != 'provided', 'CORE_RESTORE_TLS_PROBE_REQUIRED')
        return journal

    def _restore_worker_request(self, journal, action):
        plan = journal['restore_target']['data']
        return dict(protocol=1, action=action, transaction=self.transaction, slot=plan['slot'],
                    pending=journal['original_files']['pending'],
                    original=journal['switch_context']['data_generation'],
                    incoming=plan['incoming_generation'])

    def _start_restore_observer(self):
        """Hand a stopped, exchanged restore to its read-only worker."""
        self.services.prove_stopped()
        _need(self.installation_proof() is None, 'INSTALLATION_PROOF')
        journal = self._restore_trial_journal()
        response = self.worker.exchange(self._restore_worker_request(journal, 'core_restore_observe'),
            'restore_observer_ready', ('transaction',))
        _need(response['transaction'] == self.transaction, 'CORE_RESTORE_CONTEXT')
        self._proof()

    def _backup_before(self):
        snapshot = self.files.backup_snapshot(self.transaction, **self.source)
        snapshot = dict(snapshot, schema=1, kind='core-backup-closure-v1',
                        selection=self.m._selection_map(snapshot['selection']))
        self.backup = self.worker.command('backup_core', 'core_backed_up',
            ('backup_name', 'backup_sha256', 'snapshot_sha256'), snapshot=snapshot)
        name, digest = self.backup['backup_name'], self.backup['backup_sha256']
        _need(type(name) is str and re.fullmatch(r'core-maintenance-[a-f0-9]{32}\.zip', name)
              and self.a._hex(digest) and self.backup['snapshot_sha256'] ==
                  hashlib.sha256(self.modules.store._canonical(snapshot)).hexdigest(), 'CORE_BACKUP_RECEIPT')
        self.files.record_backup(self.transaction, dict(kind='backup-components-v1',
            backup_id=hashlib.sha256((name+':'+digest).encode()).hexdigest(), sha256=digest, name=name,
            selection_revision=self.before.revision,
            intents_sha256=self.m._digest(self.m._raw(self.final))), **self.source)
        return digest

    def run_restore(self, archive_name, archive_sha256, expected_revision):
        """Private managed-certificate restore. The caller owns the Root installation lock."""
        _need(self.config.get('certificate_mode', 'managed') != 'provided', 'CORE_RESTORE_TLS_PROBE_REQUIRED')
        prepared = self.worker.exchange(dict(protocol=1, action='core_restore_prepare', archive_name=archive_name,
            archive_sha256=archive_sha256, expected_selection_revision=expected_revision),
            'restore_preflight', ('context', 'incoming_selection', 'incoming_intents',
                                  'incoming_generation', 'preflight_tree_sha256'))
        facts = prepared['context']
        self._accept_context(facts, 'sing-box', expected_revision)
        _need(self.initialized, 'CORE_RESTORE_CONTEXT')
        incoming = self._state(prepared['incoming_selection'])
        incoming_intents = self._intents(prepared['incoming_intents'], incoming)
        self.initial = self.services.snapshot()
        self._time()
        _need(self.installation_proof() is None, 'INSTALLATION_PROOF')
        self.transaction = self.files.begin_restore(incoming, expected_revision=expected_revision,
            service_snapshot=dict(panel_running=self.initial['v-ui.service'] == 'active', cores=facts['intents']),
            source_backup_sha256=archive_sha256, restored_intents=incoming_intents,
            switch_context=dict(application=self.application, data_generation=self.generation,
                services=self.initial, config_sha256=self.m._digest(self.m._raw(self.config)),
                certificate_mode='managed', trust_sha256=None),
            data_plan=dict(slot=uuid.uuid4().hex, incoming_generation=prepared['incoming_generation'],
                preflight_tree_sha256=prepared['preflight_tree_sha256'], incoming_trust_sha256=None), **self.source)
        try:
            self.services.stop_all()
            self.services.prove_stopped()
            with self.files._io() as (scope, root):
                _, journal = self.files._active(scope, root, self.transaction, **self.source, phases=('prepared',))
            staged = self.worker.command('stage_restore', 'restore_staged',
                ('final_intents', 'restore_data', 'original_tree_sha256', 'prepared_tree_sha256'), journal=journal)
            self.final = self._intents(staged['final_intents'], self.before)
            self.services.prove_stopped()
            self.files.record_quiesced(self.transaction, dict(panel_stopped=True, writers_exclusive=True,
                cores=self.final), restore_data=staged['restore_data'],
                original_tree_sha256=staged['original_tree_sha256'],
                prepared_tree_sha256=staged['prepared_tree_sha256'], **self.source)
            self._backup_before()
            self.worker.finish()
            self.worker.close()
            _need(self.installation_proof() is None, 'INSTALLATION_PROOF')
            self.worker = _start_worker(self.ops, self.account, self.deadline, self.cancelled)
            state = self._exchange_and_activate_restore_trial()
            return self._result('restored_backup', state)
        except BaseException:
            self.services.stop_all(emergency=True)
            raise

    def _exchange_and_activate_restore_trial(self, *, _new_worker=None):
        """Run the private exchange for an already staged and backed-up restore."""
        self._time()
        _need(self.installation_proof() is None, 'INSTALLATION_PROOF')
        self.services.prove_stopped()
        journal = self._restore_trial_journal()
        request = self._restore_worker_request(journal, 'core_restore_exchange')
        request.update(sequence=journal['sequence'],
                       original_tree_sha256=journal['quiesced']['original_tree_sha256'],
                       prepared_tree_sha256=journal['quiesced']['prepared_tree_sha256'])
        _need(self.installation_proof() is None, 'INSTALLATION_PROOF')
        response = self.worker.exchange(request, 'restore_data_exchanged', ('receipt',))
        return self._handoff_restore_trial(response['receipt'], _new_worker=_new_worker)

    def _handoff_restore_trial(self, receipt, *, _new_worker=None):
        """Finish the writer before starting the observer under the same Root lock."""
        self._proof()
        self.services.prove_stopped()
        journal = self._restore_trial_journal()
        self.files._require_trial_receipt(journal, receipt)
        self.worker.finish()
        self.worker.close()
        self._time()
        _need(self.installation_proof() is None, 'INSTALLATION_PROOF')
        self.services.prove_stopped()
        self._time()
        _need(self.installation_proof() is None, 'INSTALLATION_PROOF')
        self.worker = (_start_worker(self.ops, self.account, self.deadline, self.cancelled)
                       if _new_worker is None else _new_worker())
        try:
            self._start_restore_observer()
            state = self._activate_restore_trial(receipt)
            self.worker.finish()
            return state
        finally:
            self.worker.close()

    def _activate_restore_trial(self, receipt, observe=None):
        """Start a private restore trial after the worker exchanged the data."""
        self._proof()
        journal = self._restore_trial_journal()
        target = journal['restore_target']
        _need(observe is None or callable(observe), 'CORE_RESTORE_CONTEXT')
        self.services.prove_stopped()

        def current_data():
            if observe is None:
                response = self.worker.command('observe_restore_data', 'restore_data_observed',
                    ('transaction', 'data'), transaction=self.transaction)
                _need(response['transaction'] == self.transaction, 'CORE_RESTORE_CONTEXT')
                data = response['data']
            else:
                data = observe()
            _need(self.m.restore_data_position(journal, data) == 'trial', 'CORE_RESTORE_DATA_CHANGED')
            self._proof()
            return data

        current_data()
        state = self.files.provision(self.transaction, restore_receipt=receipt, **self.source)
        self.generation = target['data']['incoming_generation']
        self.trust_sha256 = target['data']['incoming_trust_sha256']
        try:
            self._activate(state, self._intents(target['intents'], state),
                           recovery=False, restore_observe=current_data)
        except BaseException:
            self.services.stop_all(emergency=True)
            raise
        return state

    def _activate(self, state, intents, *, recovery, cleanup=False, restore_observe=None):
        self.services.prove_stopped()
        self._proof()
        broker = None
        try:
            observed = None
            if self.initial['v-ui.service'] == 'active':
                context = dict(**self.source, purpose='old_recovery' if recovery else 'trial',
                    data_generation=self.generation, selection=self.m._selection_map(state),
                    intents=intents, barriers=self._barriers(),
                    application={name:self.application[name] for name in self.wire.APPLICATION_FIELDS})
                _need(context['barriers']['core_pending_sha256'] != self.wire.file_digest(None, None),
                      'CORE_PENDING_MISSING')
                broker = self.wire.CoreBootBroker(context, service_uid=self.account.pw_uid,
                    service_gid=self.account.pw_gid, proof=self._proof, identity_query=self._identity,
                    timeout=min(300, self._time()))
                self.broker_closed = False
                self.broker = broker
                broker.listen(recover_stale=recovery,
                              rename_noreplace=self.modules.store._rename_new if recovery else None)
                self.services.restore(self.initial)
                identity = self._await_identity()
                broker.arm(identity); broker.wait_ready()
                result = broker.request_probe()
                _need(result['ok'] is True, 'CORE_PROBE_REJECTED')
                observed = result['observed']
                self._tls(identity)
            else:
                self.services.restore(self.initial)
                _need(self.services.state('v-ui.service') == 'inactive', 'STOPPED_PANEL_STARTED')
            observation = self._validation(state, intents, observed)
            self._proof()
            if cleanup:
                self.confirming = True
                confirmed = self.files.finish_cleanup(self.transaction, **self.source)
            elif recovery:
                self.confirming = True
                confirmed = self.files.rollback_metadata(self.transaction, observation,
                                                         final_intents=self.final, **self.source)
            else:
                restore = {} if restore_observe is None else {'restore_data': restore_observe()}
                self.files.record_validation(self.transaction, observation, **self.source, **restore)
                if restore_observe is not None:
                    restore['restore_data'] = restore_observe()
                self.confirming = True
                confirmed = self.files.confirm(self.transaction, **self.source, **restore)
            _need(confirmed == state and self.files.read_stable(**self.source) == state, 'CORE_CONFIRMATION_CHANGED')
            if broker is not None:
                broker.release(self._barriers())
                _need(broker.state == 'released', 'CORE_RELEASE_UNCONFIRMED')
                # A sent final frame is not proof that the app actually opened
                # its gate. Observe the same process serving its public login.
                self._tls(identity, login=True)
        finally:
            self._close_broker()

    def _restore_before(self):
        self.files.mark_failed(self.transaction, **self.source)
        self.worker.command('reacquire_writers', 'writers_reacquired')
        restored = self.worker.command('restore_core_ids', 'core_ids_restored', ('selection', 'intents'))
        _need(self._state(restored['selection']) == self.before
              and self._intents(restored['intents'], self.before) == self.final, 'OLD_INTENT_CHANGED')
        self.worker.command('release_reacquired_writers', 'recovery_writers_released')
        state = self.s._build(self.before.app_source, self.before.target,
                              self.after.generation + 1, self.before.slots)
        self._activate(state, self.final, recovery=True)
        self.worker.finish()
        return self._result('restored_previous', state)

    def _result(self, outcome, state):
        return dict(outcome=outcome, selection_revision=state.revision,
                    panel_running=self.initial['v-ui.service'] == 'active',
                    backup_name=self.backup['backup_name'], backup_sha256=self.backup['backup_sha256'])

    def run_recovery(self):
        journal = self.files.read_switch_recovery()
        cleanup = 'confirmed_selection' in journal
        context = journal['switch_context']
        self.application = context['application']
        self.source = {key:self.application[key] for key in ('app_source', 'target')}
        _need(self.config.get('target') == self.source['target']
              and self.m._digest(self.m._raw(self.config)) == context['config_sha256'],
              'CORE_RECOVERY_CONFIG_CHANGED')
        self.generation = context['data_generation']
        self.initial = context['services']
        self.trust_sha256 = context['trust_sha256']
        self.transaction = journal['transaction']
        self.before = self._state(journal['before'])
        self.after = self._state(journal['after'])
        restored = self._state(journal['confirmed_selection'] if cleanup else journal['recovery_selection'])
        saved_intents = (journal['rollback']['intents'] if journal['rollback'] is not None else
                         journal['quiesced']['cores'] if journal['quiesced'] is not None else None)
        backup = journal['backup']
        allow_after = backup is not None and journal['phase'] in (
            'trial', 'validated', 'confirming', 'rollback_required', 'recovery')
        request = dict(protocol=1, action='core_recover', transaction=self.transaction,
            context=context, before=journal['before'], after=journal['after'], final_intents=saved_intents,
            backup=backup, allow_after_ids=allow_after)
        if cleanup:
            _need(saved_intents is not None, 'CORE_RECOVERY_CONTEXT')
            request['confirmed_selection'] = journal['confirmed_selection']
        ready = self.worker.exchange(request, 'core_recovery_prepared',
            ('transaction', 'application', 'data_generation'))
        _need(ready['transaction'] == self.transaction and ready['application'] == self.application
              and ready['data_generation'] == self.generation, 'CORE_RECOVERY_CONTEXT')
        self._time()
        _need(self.installation_proof() is None, 'INSTALLATION_PROOF')
        self.stop_attempted = True
        try:
            self.services.stop_all()
            result = self.worker.command('proceed', 'core_recovery_bound',
                                         ('selection', 'final_intents', 'changed_cores'))
            expected_state = restored if cleanup else self.before
            expected_intents = self.m._rebind_intents(saved_intents, restored) if cleanup else saved_intents
            self.final = self._intents(result['final_intents'], expected_state)
            changed = result['changed_cores']
            _need(self._state(result['selection']) == expected_state
                  and (expected_intents is None or self.final == expected_intents)
                  and type(changed) is list and all(core in self.s.CORES for core in changed)
                  and len(changed) == len(set(changed)) and (not cleanup or not changed),
                  'CORE_RECOVERY_CONTEXT')
            self.services.prove_stopped()
            self.worker.command('release_recovery_writers', 'recovery_writers_released')
            self._activate(restored, self.final, recovery=True, cleanup=cleanup)
            self.worker.finish()
            outcome = 'resumed_confirmed' if cleanup else 'restored_previous'
            result = dict(outcome=outcome, selection_revision=restored.revision,
                          panel_running=self.initial['v-ui.service'] == 'active')
            if backup is not None:
                result.update(backup_name=backup['name'], backup_sha256=backup['sha256'])
            return result
        except BaseException:
            self.services.stop_all(emergency=True)
            raise CoreControlError('CORE_RECOVERY_REQUIRED') from None

    def run(self, core, action, expected_revision, release_id):
        try:
            self._context(core, expected_revision)
            plan = self._plan(core, action, release_id)
            if plan is None or not plan.changed:
                self.worker.finish()
                return dict(outcome='already_current', selection_revision=self.before.revision)
            self.after = plan.after
            _need(self.after.generation < self.s.MAX_GENERATION, 'CORE_GENERATION_EXHAUSTED')
            candidate = self.s._reference_map(self.after.slot(core).current)
            prepared = self.worker.command('check_core', 'prepared',
                ('application', 'data_generation', 'selection', 'candidate', 'intents', 'source_token'),
                candidate=candidate)
            _need(prepared['application'] == self.application and prepared['data_generation'] == self.generation
                  and self._state(prepared['selection']) == self.before and prepared['candidate'] == candidate
                  and self.a._hex(prepared['source_token']), 'CORE_PREPARED_CONTEXT')
            self.initial = self.services.snapshot()
            pre = self._intents(prepared['intents'], self.before)
            if self.initial['v-ui.service'] == 'active':
                self._tls(self._identity(), login=True)
            self._time()
            _need(self.installation_proof() is None, 'INSTALLATION_PROOF')
            self.transaction = self.files.begin(plan, expected_revision=self.before.revision,
                service_snapshot=dict(panel_running=self.initial['v-ui.service'] == 'active', cores=pre),
                initial_schemas=None if self.initialized else self.initial_schemas,
                switch_context=dict(application=self.application, data_generation=self.generation,
                    services=self.initial, config_sha256=self.m._digest(self.m._raw(self.config)),
                    certificate_mode=self.config.get('certificate_mode', 'managed'),
                    trust_sha256=self.trust_sha256))
            _need(self.a._hex(self.transaction, 32), 'CORE_TRANSACTION')
            self.stop_attempted = True
            self.services.stop_all()
            quiesced = self.worker.command('proceed', 'core_quiesced', ('final_intents', 'source_token'))
            _need(quiesced['source_token'] == prepared['source_token'], 'CORE_PREPARED_CONTEXT')
            self.final = self._intents(quiesced['final_intents'], self.before)
            _need(all(self.final[core]['revision'] == pre[core]['revision'] for core in self.s.CORES),
                  'CORE_FINAL_CONFIGURATION_CHANGED')
            self.services.prove_stopped()
            self.files.record_quiesced(self.transaction,
                dict(panel_stopped=True, writers_exclusive=True, cores=self.final), **self.source)
            digest = self._backup_before()
            _need(self.files.provision(self.transaction, **self.source) == self.after, 'CORE_TRIAL_CHANGED')
            bound = self.worker.command('bind_core_ids', 'core_ids_bound', ('selection', 'intents'),
                                        backup_sha256=digest)
            intents = self.m._rebind_intents(self.final, self.after)
            _need(self._state(bound['selection']) == self.after
                  and self._intents(bound['intents'], self.after) == intents, 'CORE_BOUND_CONTEXT')
            self.worker.command('release_writers_for_trial', 'trial_writers_released')
            self.released = True
            self._activate(self.after, intents, recovery=False)
            self.worker.finish()
            return self._result('switched', self.after)
        except BaseException:
            if not self.stop_attempted:
                raise
            try:
                self.services.stop_all(emergency=True)
                if self.released and self.broker_closed and not self.confirming:
                    return self._restore_before()
            except BaseException:
                # An unsuccessful old trial may have started the old panel.
                self.services.stop_all(emergency=True)
            raise CoreControlError('CORE_RECOVERY_REQUIRED') from None


def switch(ops, *, core, action, expected_revision, release_id=None, cancelled=None):
    """Fixed internal operation; the caller still needs a scoped daemon action."""
    _need(os.geteuid() == 0, 'ROOT_REQUIRED')
    _need(os.getpid() == os.getpgrp() == os.getsid(0), 'CORE_TASK_ISOLATION')
    _need(type(core) is str and core in ('sing-box', 'xray') and action in ('update', 'rollback')
          and (expected_revision is None or type(expected_revision) is str and ops.HEX.fullmatch(expected_revision))
          and (action == 'rollback' and release_id is None
               or action == 'update' and type(release_id) is int and 0 < release_id < 2**63), 'CORE_REQUEST')
    return _run_task(ops, cancelled, (core, action, expected_revision, release_id))


def recover(ops, *, cancelled=None):
    """Explicit Root-only recovery of the retained transaction."""
    _need(os.geteuid() == 0, 'ROOT_REQUIRED')
    _need(os.getpid() == os.getpgrp() == os.getsid(0), 'CORE_TASK_ISOLATION')
    return _run_task(ops, cancelled, None)


def restore_backup(ops, *, archive_name, archive_sha256, expected_revision, cancelled=None):
    """Fixed private restore entry for a scoped caller in an isolated session."""
    _need(os.geteuid() == 0, 'ROOT_REQUIRED')
    _need(os.getpid() == os.getpgrp() == os.getsid(0), 'CORE_TASK_ISOLATION')
    _need(type(archive_name) is str and re.fullmatch(r'core-maintenance-[a-f0-9]{32}\.zip', archive_name)
          and all(type(value) is str and ops.HEX.fullmatch(value)
                  for value in (archive_sha256, expected_revision)), 'CORE_RESTORE_REQUEST')
    return _run_task(ops, cancelled, (archive_name, archive_sha256, expected_revision), restore=True)


def _run_task(ops, cancelled, request, *, restore=False):
    cancelled = [] if cancelled is None else cancelled
    deadline = time.monotonic() + ops.TIMEOUT
    with ops.installation_lock(_proof=True) as proof:
        manager, account = ops.verify_installation()
        _need(manager == 'systemd', 'CORE_SYSTEMD_REQUIRED')
        config = ops.decode_json(ops.trusted_bytes(ops.CONFIG, ops.MAX_JSON))
        services = ops.Services(manager, cancelled)
        services.deadline = deadline
        with _modules(ops) as modules:
            def new_task():
                worker = _start_worker(ops, account, deadline, cancelled)
                return _Switch(ops, modules, services, account, worker, proof, config,
                               deadline=deadline, cancelled=cancelled)

            task = new_task()
            try:
                if restore:
                    return task.run_restore(*request)
                if request is None:
                    return task.run_recovery()
                try:
                    return task.run(*request)
                except CoreControlError as error:
                    if (str(error) != 'CORE_RECOVERY_REQUIRED' or not task.stop_attempted
                            or task.released or task.confirming or cancelled):
                        raise
                    services.prove_stopped()
                    task.worker.close()
                    task = new_task()
                    return task.run_recovery()
            finally:
                try:
                    task._close_broker()
                finally:
                    task.worker.close()
