"""Pure selection-state/CAS rules with explicit synthetic material identities."""
import dataclasses
import hashlib
import json
import unittest

from app.core_updates import selection as s
from app.core_updates.artifacts import PackIdentity, REPOSITORY, TARGETS

APP = 'a' * 40
OTHER_APP = 'b' * 40
TARGET = 'x86_64-gnu'


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def bundle(core, app=APP, target=TARGET):
    return s.bundle_reference(core=core,
        version='1.14.2' if core == 'sing-box' else '26.3.27',
        target=target, app_source=app, payload_manifest_sha256=digest(app),
        binary_sha256=digest(core + target), binary_size=99)


def material(core='sing-box', app=APP, target=TARGET, version=None, seed='new'):
    version = version or ('1.14.3' if core == 'sing-box' else '26.10.21')
    return PackIdentity(repository=REPOSITORY, release_id=123,
        tag='core-' + core + '-v' + version + '-r1', core=core, version=version,
        target=target, builder_source='c' * 40, app_source=app,
        release_manifest_sha256=digest(seed + 'release'), checksums_sha256=digest(seed + 'table'),
        pack_sha256=digest(seed + 'pack'), pack_manifest_sha256=digest(seed + 'inner'),
        binary_sha256=digest(seed + 'binary'), binary_size=101)


def initial(app=APP, target=TARGET):
    return s.initial_selection(app_source=app, target=target,
        bundles={core: bundle(core, app, target) for core in s.CORES})


def raw(state):
    return json.loads(s.encode_selection(state))


def seal(value):
    value = dict(value); value.pop('revision', None)
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(',', ':')).encode()
    value['revision'] = hashlib.sha256(encoded).hexdigest()
    return json.dumps(value).encode()


class CoreSelectionTests(unittest.TestCase):
    def decode(self, value, app=APP, target=TARGET):
        return s.decode_selection(value, app_source=app, target=target)

    def test_all_targets_initial_roundtrip_is_canonical_and_has_no_pending_authority_or_path(self):
        for target in TARGETS:
            with self.subTest(target=target):
                state = initial(target=target)
                self.assertEqual(self.decode(s.encode_selection(state), target=target), state)
                self.assertEqual(state.generation, 0)
                self.assertEqual(tuple(slot.core for slot in state.slots), s.CORES)
                self.assertTrue(all(slot.previous is None for slot in state.slots))
                encoded = s.encode_selection(state)
                for field in ('pending', 'candidate', 'authority', 'token', 'path', 'enabled'):
                    self.assertNotIn('"' + field + '"', encoded.decode())
                reordered = dict(reversed(list(raw(state).items())))
                self.assertEqual(s.encode_selection(self.decode(json.dumps(reordered).encode(), target=target)), encoded)

    def test_upgrade_changes_only_one_slot_and_retains_exact_last_current(self):
        state = initial()
        for core in s.CORES:
            with self.subTest(core=core):
                before = s.encode_selection(state)
                change = s.plan_component(state, core=core, identity=material(core), expected_revision=state.revision)
                self.assertTrue(change.changed)
                self.assertIs(change.before, state)
                self.assertEqual(change.after.generation, state.generation + 1)
                self.assertEqual(change.after.slot(core).previous, state.slot(core).current)
                self.assertEqual(change.after.slot(core).current, s.component_reference(material(core)))
                other = next(name for name in s.CORES if name != core)
                self.assertEqual(change.after.slot(other), state.slot(other))
                self.assertEqual(s.encode_selection(state), before)

    def test_rollback_rechecks_previous_material_and_keeps_current_for_forward_recovery(self):
        state = initial()
        update = s.plan_component(state, core='sing-box', identity=material(), expected_revision=state.revision).after
        rollback = s.plan_rollback(update, core='sing-box', material=bundle('sing-box'), expected_revision=update.revision)
        self.assertEqual(rollback.after.slot('sing-box').current, state.slot('sing-box').current)
        self.assertEqual(rollback.after.slot('sing-box').previous, update.slot('sing-box').current)
        forward = s.plan_rollback(rollback.after, core='sing-box', material=material(), expected_revision=rollback.after.revision)
        self.assertEqual(forward.after.slot('sing-box'), update.slot('sing-box'))
        self.assertNotEqual(forward.after.revision, update.revision)
        self.assertEqual(forward.after.generation, 3)

    def test_aba_stale_revision_conflicts_even_when_requested_artifact_is_current(self):
        a = initial()
        b = s.plan_component(a, core='sing-box', identity=material(), expected_revision=a.revision).after
        c = s.plan_rollback(b, core='sing-box', material=bundle('sing-box'), expected_revision=b.revision).after
        d = s.plan_rollback(c, core='sing-box', material=material(), expected_revision=c.revision).after
        self.assertEqual(d.slots, b.slots)
        with self.assertRaisesRegex(s.CoreSelectionError, '^SELECTION_CONFLICT$'):
            s.plan_component(d, core='sing-box', identity=material(), expected_revision=b.revision)

    def test_exact_current_is_noop_after_cas_and_never_erases_previous_or_increments_generation(self):
        first = initial()
        state = s.plan_component(first, core='xray', identity=material('xray'), expected_revision=first.revision).after
        change = s.plan_component(state, core='xray', identity=material('xray'), expected_revision=state.revision)
        self.assertFalse(change.changed); self.assertIs(change.after, state)
        self.assertEqual(change.after.slot('xray').previous, first.slot('xray').current)
        with self.assertRaisesRegex(s.CoreSelectionError, 'SELECTION_CONFLICT'):
            s.plan_component(state, core='xray', identity=material('xray'), expected_revision=first.revision)

    def test_explicit_return_to_bundle_is_planned_and_preserves_current_as_previous(self):
        first = initial()
        same = s.plan_bundle(first, core='sing-box', bundle=bundle('sing-box'), expected_revision=first.revision)
        self.assertFalse(same.changed)
        state = s.plan_component(first, core='sing-box', identity=material(), expected_revision=first.revision).after
        change = s.plan_bundle(state, core='sing-box', bundle=bundle('sing-box'), expected_revision=state.revision)
        self.assertEqual(change.after.slot('sing-box').current, first.slot('sing-box').current)
        self.assertEqual(change.after.slot('sing-box').previous, state.slot('sing-box').current)

    def test_component_reference_is_app_independent_but_planning_requires_current_app_context(self):
        self.assertEqual(s.component_reference(material()), s.component_reference(material(app=OTHER_APP)))
        state = initial()
        for bad in (material(app=OTHER_APP), material(target='aarch64-gnu'), material(core='xray')):
            with self.subTest(bad=bad), self.assertRaisesRegex(s.CoreSelectionError, 'MATERIAL_CONTEXT'):
                s.plan_component(state, core='sing-box', identity=bad, expected_revision=state.revision)
        other_state = initial(app=OTHER_APP)
        self.assertTrue(s.plan_component(other_state, core='sing-box', identity=material(app=OTHER_APP),
                                        expected_revision=other_state.revision).changed)

    def test_previous_is_exact_identity_not_just_core_name_or_version(self):
        first = initial()
        state = s.plan_component(first, core='sing-box', identity=material(), expected_revision=first.revision).after
        for wrong in (bundle('xray'), dataclasses.replace(bundle('sing-box'), binary_sha256='d' * 64), material()):
            with self.subTest(wrong=wrong), self.assertRaises(s.CoreSelectionError):
                s.plan_rollback(state, core='sing-box', material=wrong, expected_revision=state.revision)
        with self.assertRaisesRegex(s.CoreSelectionError, 'NO_PREVIOUS'):
            s.plan_rollback(first, core='sing-box', material=bundle('sing-box'), expected_revision=first.revision)

    def test_old_bundle_previous_can_be_archived_but_not_silently_rebound_to_new_app(self):
        state = initial()
        value = raw(state)
        value['cores']['sing-box']['previous'] = dict(value['cores']['sing-box']['current'], app_source=OTHER_APP)
        archived = self.decode(seal(value))
        self.assertEqual(archived.slot('sing-box').previous.app_source, OTHER_APP)
        with self.assertRaisesRegex(s.CoreSelectionError, 'MATERIAL_CONTEXT'):
            s.plan_rollback(archived, core='sing-box', material=bundle('sing-box', app=OTHER_APP),
                            expected_revision=archived.revision)

    def test_unknown_missing_duplicate_nonfinite_and_untrusted_fields_never_decode(self):
        state = raw(initial())
        changes = []
        for field in tuple(state):
            d = json.loads(json.dumps(state)); del d[field]; changes.append(json.dumps(d).encode())
        for field in ('candidate', 'pending', 'authority', 'url', 'approved'):
            d = dict(state); d[field] = True; changes.append(seal(d))
        changes += [b'{"schema":1,"schema":1}', b'{"schema":NaN}', b'{"schema":1.0}',
                    b'\xff', b'[]', b'{}', b'{"value":"\xed\xa0\x80"}', b' ' * 64001]
        for data in changes:
            with self.subTest(data=data[:70]), self.assertRaises(s.CoreSelectionError):
                self.decode(data)

    def test_context_generation_digest_and_full_core_set_are_required(self):
        state = raw(initial())
        for field, bads in (('app_source', [OTHER_APP, 'A' * 40, None]),
                            ('target', ['aarch64-gnu', None]),
                            ('schema', [True, 2, '1']),
                            ('generation', [True, -1, s.MAX_GENERATION + 1]),
                            ('revision', ['f' * 64, None])):
            for bad in bads:
                value = json.loads(json.dumps(state)); value[field] = bad
                data = json.dumps(value).encode() if field == 'revision' else seal(value)
                with self.subTest(field=field, bad=bad), self.assertRaises(s.CoreSelectionError):
                    self.decode(data)
        for cores in ({}, {'sing-box': state['cores']['sing-box']}, {**state['cores'], 'third': {}}):
            with self.assertRaises(s.CoreSelectionError): self.decode(seal(dict(state, cores=cores)))

    def test_slot_and_reference_fields_all_have_closed_shapes_and_context(self):
        state = raw(initial())
        for name in ('current', 'previous'):
            value = json.loads(json.dumps(state)); del value['cores']['sing-box'][name]
            with self.assertRaises(s.CoreSelectionError): self.decode(seal(value))
        ref = state['cores']['sing-box']['current']
        variants = [dict(ref, core='xray'), dict(ref, target='aarch64-gnu'), dict(ref, app_source=OTHER_APP),
                    dict(ref, binary_size=True), dict(ref, binary_size=0), dict(ref, binary_sha256='secret-marker'),
                    dict(ref, kind='custom'), dict(ref, path='/private/user-secret'), dict(ref, version='1.2.3;evil')]
        for key in ref:
            value = dict(ref); del value[key]; variants.append(value)
        for bad in variants:
            value = json.loads(json.dumps(state)); value['cores']['sing-box']['current'] = bad
            with self.subTest(bad=bad), self.assertRaises(s.CoreSelectionError) as error:
                self.decode(seal(value))
            self.assertNotIn('secret-marker', str(error.exception))
            self.assertNotIn('/private/user-secret', str(error.exception))
        value = json.loads(json.dumps(state)); value['cores']['sing-box']['previous'] = dict(ref)
        with self.assertRaises(s.CoreSelectionError): self.decode(seal(value))

    def test_component_all_material_hashes_repository_tag_and_release_id_are_required(self):
        first = initial()
        state = s.plan_component(first, core='sing-box', identity=material(), expected_revision=first.revision).after
        original = raw(state)
        ref = original['cores']['sing-box']['current']
        variants = []
        for field in ref:
            d = dict(ref); del d[field]; variants.append(d)
        for field in ('builder_source', 'release_manifest_sha256', 'checksums_sha256',
                      'pack_sha256', 'pack_manifest_sha256', 'binary_sha256'):
            variants.append(dict(ref, **{field: 'bad'}))
        variants += [dict(ref, release_id=True), dict(ref, release_id=0), dict(ref, repository='attacker/fork'),
                     dict(ref, tag='core-xray-v1.14.3-r1'), dict(ref, tag='core-sing-box-v1.14.4-r1')]
        for bad in variants:
            value = json.loads(json.dumps(original)); value['cores']['sing-box']['current'] = bad
            with self.subTest(bad=bad), self.assertRaises(s.CoreSelectionError): self.decode(seal(value))

    def test_generation_exhaustion_is_fail_closed_and_noop_stays_possible(self):
        value = raw(initial()); value['generation'] = s.MAX_GENERATION
        full = self.decode(seal(value))
        with self.assertRaisesRegex(s.CoreSelectionError, 'GENERATION_EXHAUSTED'):
            s.plan_component(full, core='sing-box', identity=material(), expected_revision=full.revision)
        self.assertFalse(s.plan_bundle(full, core='sing-box', bundle=bundle('sing-box'),
                                       expected_revision=full.revision).changed)

    def test_initial_requires_both_verified_context_bundle_records(self):
        for bundles in ({}, {'sing-box': bundle('sing-box')},
                        {'sing-box': bundle('sing-box'), 'xray': bundle('xray', app=OTHER_APP)}):
            with self.assertRaises(s.CoreSelectionError):
                s.initial_selection(app_source=APP, target=TARGET, bundles=bundles)

    def test_structural_parse_does_not_authenticate_material_or_execute_anything(self):
        state = initial()
        self.assertFalse(any(hasattr(state, name) for name in ('approved', 'authorized', 'binary_path')))
        value = raw(state); value['cores']['sing-box']['current']['binary_sha256'] = 'd' * 64
        forged = self.decode(seal(value))
        self.assertNotEqual(forged.slot('sing-box').current, state.slot('sing-box').current)
        # Root ownership plus independent bundle/catalog/store/native verification
        # is still mandatory before a caller may persist or run either reference.
        self.assertNotEqual(s.reference_identity(forged.slot('sing-box').current),
                            s.reference_identity(state.slot('sing-box').current))

    def test_pending_plan_and_untyped_material_are_not_confirmed_backup_or_execution_proof(self):
        state = initial()
        plan = s.plan_component(state, core='sing-box', identity=material(), expected_revision=state.revision)
        with self.assertRaisesRegex(s.CoreSelectionError, 'SELECTION_TYPE'):
            s.encode_selection(plan)
        # The after-value is serializable for journals. This gives it no
        # committed/backup status; that boundary belongs to the root transaction.
        self.assertEqual(self.decode(s.encode_selection(plan.after)), plan.after)
        self.assertFalse(hasattr(plan.after, 'committed'))
        with self.assertRaisesRegex(s.CoreSelectionError, 'MATERIAL_IDENTITY'):
            s.plan_component(state, core='sing-box', identity=dataclasses.asdict(material()),
                             expected_revision=state.revision)
        with self.assertRaisesRegex(s.CoreSelectionError, 'BUNDLE_BASELINE'):
            s.plan_bundle(state, core='sing-box', bundle=dataclasses.asdict(bundle('sing-box')),
                          expected_revision=state.revision)


if __name__ == '__main__': unittest.main()
