"""Non-root recovery of core identity bindings from a retained Root switch."""
from copy import deepcopy
import hashlib
import os
from pathlib import Path
import re
import stat

from app import release_tools as tools
from . import artifacts as a, selection as s, selection_files as sf
from .application import bundle_artifacts
from .backup_archive import MAX_ARCHIVE
from .boot import _data_root
from .maintenance import CoreMaintenanceSession, _need, _state_map, _versions
from .resolver import CoreResolver


class CoreRecoverySession(CoreMaintenanceSession):
    """Recheck real local data under the ordinary operation and writer leases."""

    ACTIONS = frozenset(('core_recover',))

    def __init__(self, root, *, transaction, context, before, after, final_intents,
                 allow_after_ids, backup=None, **test_layout):
        _need(a._hex(transaction, 32) and type(allow_after_ids) is bool, 'RECOVERY_CONTEXT')
        app = context['application']
        source = dict(app_source=app['app_source'], target=app['target'])
        self._old = s.decode_selection(sf._raw(before), **source)
        self._candidate_state = s.decode_selection(sf._raw(after), **source)
        changes = [core for core in s.CORES if self._old.slot(core) != self._candidate_state.slot(core)]
        _need(len(changes) == 1 and self._candidate_state.generation == self._old.generation + 1
              and self._candidate_state.slot(changes[0]).previous == self._old.slot(changes[0]).current,
              'RECOVERY_CONTEXT')
        super().__init__(root, core=changes[0], expected_selection_revision=self._old.revision, **test_layout)
        self._transaction = transaction
        self._recovery_context = sf._switch_context(context, self._old,
            context['services']['v-ui.service'] == 'active')
        self._saved_intents = None if final_intents is None else sf._intent(final_intents, self._old)
        self._allow_after = allow_after_ids
        self._recovery_backup = deepcopy(backup)
        _need(not allow_after_ids or (backup is not None and self._saved_intents is not None),
              'RECOVERY_CONTEXT')

    def _pending(self):
        with self._files._io() as (scope, root):
            raw, _ = sf._read(scope, root, sf.PENDING)
            _need(sf._pending(raw)['transaction'] == self._transaction, 'RECOVERY_PENDING_CHANGED')

    def _check_panel_material(self):
        expected = self._recovery_context['trust_sha256']
        if expected is None:
            return
        material = self._read_panel_tls_material()
        _need(material[1][0] == 'provided' and material[2] is not None
              and hashlib.sha256(material[2][1]).hexdigest() == expected
              and self._recovery_context['config_sha256'] ==
                  sf._digest(sf._raw(tools._maintenance_read_config())), 'RECOVERY_TRUST_CHANGED')
        if self._panel_tls_material is None:
            self._panel_tls_material = material
        _need(self._panel_tls_material == material, 'RECOVERY_TRUST_CHANGED')

    def prepare(self):
        _need(os.geteuid() != 0, 'NONROOT_REQUIRED')
        try:
            tools._MaintenanceSession.prepare(self, 'core_recover')
            self._pending()
            release, _, manifest, ready = self._active_application
            self._source, self._target = manifest['source_commit'], ready['runtime_key']
            self._app_facts = self._application_facts(self._active_application)
            self._application_identity = self._observed_application_files
            info = (self.root/'data').lstat()
            self._data_generation = dict(dev=info.st_dev, ino=info.st_ino)
            _need(stat.S_ISDIR(info.st_mode)
                  and self._app_facts == self._recovery_context['application']
                  and self._data_generation == self._recovery_context['data_generation'], 'RECOVERY_SOURCE_CHANGED')
            self._before, self._after = self._old, self._candidate_state
            self._bundle_map = bundle_artifacts(release/'payload', manifest, ready,
                versions=_versions(release/'payload', manifest), owner=os.geteuid(), anchor=self._bundle_anchor)
            self._resolver = CoreResolver(app_source=self._source, target=self._target,
                bundles=self._bundle_map, catalog=self._catalog, store=self._store)
            for core in s.CORES:
                self._resolver.current(self._before, core)
            self._check_panel_material()
            self._core_phase = 'recovery_prepared'
            return dict(transaction=self._transaction, application=deepcopy(self._app_facts),
                        data_generation=deepcopy(self._data_generation))
        except BaseException:
            self.close()
            raise

    def _check_backup(self):
        backup = self._recovery_backup
        _need(type(backup) is dict and backup.get('kind') == 'backup-components-v1'
              and type(backup.get('name')) is str
              and re.fullmatch(r'core-maintenance-[a-f0-9]{32}\.zip', backup['name'])
              and a._hex(backup.get('sha256')) and backup.get('selection_revision') == self._before.revision
              and backup.get('intents_sha256') == sf._digest(sf._raw(self._final_intents)),
              'RECOVERY_BACKUP_REQUIRED')
        with _data_root(self.root/'backups', os.geteuid(), Path('/')) as (scope, directory):
            fd, size = scope.file(directory, backup['name'], mode=0o600, limit=MAX_ARCHIVE)
            _need(a._digest_fd(fd, size) == backup['sha256'], 'RECOVERY_BACKUP_CHANGED')
            scope.stable()

    def execute(self):
        _need(self._core_phase == 'recovery_prepared', 'CORE_PHASE')
        self._pending()
        tools._MaintenanceSession.execute(self)
        self._check()
        self._pending()
        states = self._observe_states()
        old_ids = {core:s.reference_identity(self._before.slot(core).current) for core in s.CORES}
        intents, actual_ids, changed = {}, {}, []
        for core in s.CORES:
            item = states.get(core)
            intents[core] = dict(artifact_id=old_ids[core], revision=item['revision'] if item else None,
                                 enabled=item['enabled'] if item else False)
            actual_ids[core] = old_ids[core]
            if item is None:
                continue
            if item['_schema'] == 1:
                _need(type(self._before.slot(core).current) is s.BundleReference, 'RECOVERY_RUNTIME_ID')
                changed.append(core)
            elif item['artifact_id'] != old_ids[core]:
                after = s.reference_identity(self._after.slot(core).current)
                _need(self._allow_after and item['artifact_id'] == after, 'RECOVERY_RUNTIME_ID')
                actual_ids[core] = after
                changed.append(core)
        _need(self._saved_intents is None or intents == self._saved_intents, 'RECOVERY_INTENT_CHANGED')
        self._final_intents = intents
        self._final_states = states
        reader = tools._readonly_proxy_store_type()
        data = self.root/'data'
        tools._database_mode_fence(data, _existing_store_type=reader)
        if os.path.lexists(data/'proxy-state') or os.path.lexists(data/'.proxy-state-initialized'):
            tools._validate_restore_policy(data, _artifact_ids=actual_ids, _existing_store_type=reader)
        if changed:
            self._check_backup()
        self._pending()
        self._check_panel_material()
        _need(self._observe_states() == states, 'RECOVERY_INTENT_CHANGED')
        # Reuse the real writer-generation checks and require the exact
        # observed states. Unlike same-session rollback this worker just took
        # exclusive writers and has not run any candidate.
        bound = self._bind(self._before, restore=False)
        self._core_phase = 'recovery_bound'
        return dict(selection=bound['selection'], final_intents=bound['intents'], changed_cores=changed)

    def release_recovery_writers(self):
        _need(self._core_phase == 'recovery_bound', 'CORE_PHASE')
        tools._MaintenanceSession.release_writers_for_trial(self)
        self._core_phase = 'recovery_writers_released'
