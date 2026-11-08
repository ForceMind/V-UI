"""Pure selection-state format and compare-and-swap plans, not authority.

These functions do not read/write files, grant permission, resolve executable
paths, check native compatibility, or run a core. A structurally valid state or
its public checksum does NOT authenticate component/bundle material. The caller
must verify the root-owned selection, active application/bundled material and
component catalog/store independently, then persist plans through the managed
stop/backup/recovery transaction. Pending candidates and authority are never
part of this state format. Encoding a plan's after-value for a journal does not
prove it was committed. Backups must obtain the last confirmed live value (or
the transaction's original confirmed snapshot), never treat plan.after as one.
"""
from dataclasses import dataclass
import hashlib
import json
from typing import Optional, Tuple, Union

from .artifacts import (CoreArtifactError, PackIdentity, REPOSITORY, TARGETS,
                        MAX_EXPANDED, _hex, _json, _positive, _tag)

CORES = ('sing-box', 'xray')
MAX_GENERATION = 2**63 - 1
BUNDLE_FIELDS = ('core', 'version', 'target', 'app_source',
                 'payload_manifest_sha256', 'binary_sha256', 'binary_size')
COMPONENT_FIELDS = ('repository', 'release_id', 'tag', 'core', 'version',
                    'target', 'builder_source', 'release_manifest_sha256',
                    'checksums_sha256', 'pack_sha256', 'pack_manifest_sha256',
                    'binary_sha256', 'binary_size')


class CoreSelectionError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _need(condition, code):
    if not condition:
        raise CoreSelectionError(code)


def _keys(value, fields):
    _need(type(value) is dict and set(value) == set(fields), 'SELECTION_FIELDS')


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True,
                      separators=(',', ':')).encode('ascii')


@dataclass(frozen=True)
class BundleReference:
    core: str
    version: str
    target: str
    app_source: str
    payload_manifest_sha256: str
    binary_sha256: str
    binary_size: int


@dataclass(frozen=True)
class ComponentReference:
    repository: str
    release_id: int
    tag: str
    core: str
    version: str
    target: str
    builder_source: str
    release_manifest_sha256: str
    checksums_sha256: str
    pack_sha256: str
    pack_manifest_sha256: str
    binary_sha256: str
    binary_size: int


Reference = Union[BundleReference, ComponentReference]


def _reference_map(reference):
    if type(reference) is BundleReference:
        return dict(kind='bundle', **{f: getattr(reference, f) for f in BUNDLE_FIELDS})
    _need(type(reference) is ComponentReference, 'REFERENCE_TYPE')
    return dict(kind='component', **{f: getattr(reference, f) for f in COMPONENT_FIELDS})


def _decode_reference(value):
    _need(type(value) is dict, 'REFERENCE_FIELDS')
    kind = value.get('kind')
    fields = BUNDLE_FIELDS if kind == 'bundle' else COMPONENT_FIELDS
    _need(kind in ('bundle', 'component'), 'REFERENCE_KIND')
    _keys(value, ('kind', *fields))
    core, version, target = value['core'], value['version'], value['target']
    _need(type(core) is str and core in CORES and type(version) is str
          and type(target) is str and target in TARGETS, 'REFERENCE_IDENTITY')
    try:
        _tag('core-' + core + '-v' + version + '-r1')
        if kind == 'component':
            tagged_core, tagged_version, _ = _tag(value['tag'])
            _need((tagged_core, tagged_version) == (core, version), 'REFERENCE_TAG')
    except CoreArtifactError:
        raise CoreSelectionError('REFERENCE_TAG') from None
    _need(_hex(value['binary_sha256']) and _positive(value['binary_size'], MAX_EXPANDED),
          'REFERENCE_BINARY')
    if kind == 'bundle':
        _need(_hex(value['app_source'], 40) and _hex(value['payload_manifest_sha256']),
              'REFERENCE_BUNDLE')
        return BundleReference(**{f: value[f] for f in fields})
    _need(value['repository'] == REPOSITORY and type(value['repository']) is str
          and _positive(value['release_id']) and _hex(value['builder_source'], 40),
          'REFERENCE_COMPONENT')
    _need(all(_hex(value[field]) for field in ('release_manifest_sha256', 'checksums_sha256',
              'pack_sha256', 'pack_manifest_sha256')), 'REFERENCE_COMPONENT')
    return ComponentReference(**{f: value[f] for f in fields})


def reference_identity(reference):
    """Public artifact identity, not an authenticity or compatibility proof."""
    value = _reference_map(reference)
    _decode_reference(value)
    return hashlib.sha256(_canonical(value)).hexdigest()


def bundle_reference(*, core, version, target, app_source, payload_manifest_sha256,
                     binary_sha256, binary_size):
    """Wrap facts obtained from an independently verified application payload."""
    return _decode_reference(dict(kind='bundle', core=core, version=version,
        target=target, app_source=app_source, payload_manifest_sha256=payload_manifest_sha256,
        binary_sha256=binary_sha256, binary_size=binary_size))


def component_reference(identity):
    """Wrap a verified material result; never derive authority from state JSON."""
    _need(type(identity) is PackIdentity and _hex(identity.app_source, 40), 'MATERIAL_IDENTITY')
    return _decode_reference(dict(kind='component', **{
        field: getattr(identity, field) for field in COMPONENT_FIELDS}))


@dataclass(frozen=True)
class CoreSlot:
    core: str
    current: Reference
    previous: Optional[Reference]


@dataclass(frozen=True)
class SelectionState:
    app_source: str
    target: str
    generation: int
    revision: str
    slots: Tuple[CoreSlot, ...]

    def slot(self, core):
        _need(type(core) is str and core in CORES, 'CORE')
        return next(slot for slot in self.slots if slot.core == core)


def _content(app_source, target, generation, slots):
    _need(_hex(app_source, 40) and type(target) is str and target in TARGETS, 'SELECTION_CONTEXT')
    _need(type(generation) is int and 0 <= generation <= MAX_GENERATION, 'SELECTION_GENERATION')
    _need(type(slots) is tuple and len(slots) == len(CORES)
          and all(type(slot) is CoreSlot for slot in slots)
          and tuple(slot.core for slot in slots) == CORES, 'SELECTION_CORES')
    cores = {}
    for slot in slots:
        current = _decode_reference(_reference_map(slot.current))
        previous = None if slot.previous is None else _decode_reference(_reference_map(slot.previous))
        _need(current.core == slot.core and current.target == target, 'SELECTION_CURRENT')
        if type(current) is BundleReference:
            _need(current.app_source == app_source, 'SELECTION_CURRENT_SOURCE')
        if previous is not None:
            _need(previous.core == slot.core and previous.target == target
                  and previous != current, 'SELECTION_PREVIOUS')
        cores[slot.core] = {'current': _reference_map(current),
                            'previous': None if previous is None else _reference_map(previous)}
    return dict(schema=1, app_source=app_source, target=target, generation=generation, cores=cores)


def _build(app_source, target, generation, slots):
    content = _content(app_source, target, generation, slots)
    revision = hashlib.sha256(_canonical(content)).hexdigest()
    return SelectionState(app_source, target, generation, revision, slots)


def encode_selection(state):
    _need(type(state) is SelectionState, 'SELECTION_TYPE')
    content = _content(state.app_source, state.target, state.generation, state.slots)
    digest = hashlib.sha256(_canonical(content)).hexdigest()
    _need(_hex(state.revision) and state.revision == digest, 'SELECTION_REVISION')
    return _canonical(dict(content, revision=digest)) + b'\n'


def decode_selection(raw, *, app_source, target):
    """Parse confirmed structure only; caller must authenticate files/materials."""
    try:
        value = _json(raw)
    except CoreArtifactError:
        raise CoreSelectionError('SELECTION_JSON') from None
    _keys(value, ('schema', 'app_source', 'target', 'generation', 'revision', 'cores'))
    _need(type(value['schema']) is int and value['schema'] == 1, 'SELECTION_SCHEMA')
    _need(_hex(app_source, 40) and type(target) is str and target in TARGETS
          and value['app_source'] == app_source and value['target'] == target, 'SELECTION_CONTEXT')
    _keys(value['cores'], CORES)
    slots = []
    for core in CORES:
        slot = value['cores'][core]
        _keys(slot, ('current', 'previous'))
        current = _decode_reference(slot['current'])
        previous = None if slot['previous'] is None else _decode_reference(slot['previous'])
        slots.append(CoreSlot(core, current, previous))
    state = _build(app_source, target, value['generation'], tuple(slots))
    _need(_hex(value['revision']) and value['revision'] == state.revision, 'SELECTION_REVISION')
    return state


def initial_selection(*, app_source, target, bundles):
    """Create a plan baseline from both independently verified bundled cores."""
    _keys(bundles, CORES)
    _need(all(type(bundles[core]) is BundleReference for core in CORES), 'BUNDLE_BASELINE')
    return _build(app_source, target, 0, tuple(CoreSlot(core, bundles[core], None) for core in CORES))


@dataclass(frozen=True)
class SelectionPlan:
    operation: str
    core: str
    before: SelectionState
    after: SelectionState
    changed: bool


def _base(state, core, expected_revision):
    encode_selection(state)
    _need(type(core) is str and core in CORES, 'CORE')
    _need(_hex(expected_revision) and expected_revision == state.revision, 'SELECTION_CONFLICT')
    return state.slot(core)


def _next(state, core, current, previous, operation):
    _need(state.generation < MAX_GENERATION, 'GENERATION_EXHAUSTED')
    slots = tuple(CoreSlot(core, current, previous) if slot.core == core else slot for slot in state.slots)
    after = _build(state.app_source, state.target, state.generation + 1, slots)
    return SelectionPlan(operation, core, state, after, True)


def plan_component(state, *, core, identity, expected_revision):
    slot = _base(state, core, expected_revision)
    candidate = component_reference(identity)
    _need(identity.app_source == state.app_source and candidate.target == state.target
          and candidate.core == core, 'MATERIAL_CONTEXT')
    if candidate == slot.current:
        return SelectionPlan('component', core, state, state, False)
    return _next(state, core, candidate, slot.current, 'component')


def plan_bundle(state, *, core, bundle, expected_revision):
    slot = _base(state, core, expected_revision)
    _need(type(bundle) is BundleReference, 'BUNDLE_BASELINE')
    candidate = _decode_reference(_reference_map(bundle))
    _need(candidate.app_source == state.app_source and candidate.target == state.target
          and candidate.core == core, 'MATERIAL_CONTEXT')
    if candidate == slot.current:
        return SelectionPlan('bundle', core, state, state, False)
    return _next(state, core, candidate, slot.current, 'bundle')


def plan_rollback(state, *, core, material, expected_revision):
    """Caller re-verifies previous material/config before persisting this plan.

    Historical previous references may be retained across app changes. A bundle
    belonging to another app source is not silently rebound or executed here.
    """
    slot = _base(state, core, expected_revision)
    _need(slot.previous is not None, 'NO_PREVIOUS')
    if type(material) is PackIdentity:
        candidate = component_reference(material)
        _need(material.app_source == state.app_source, 'MATERIAL_CONTEXT')
    else:
        _need(type(material) is BundleReference and material.app_source == state.app_source,
              'MATERIAL_CONTEXT')
        candidate = _decode_reference(_reference_map(material))
    _need(candidate == slot.previous, 'PREVIOUS_MATERIAL_MISMATCH')
    return _next(state, core, candidate, slot.current, 'rollback')
