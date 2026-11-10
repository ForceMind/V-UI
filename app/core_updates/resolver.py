"""Shared read-only material resolution, not selection/action authority.

The caller authenticates the live confirmed selection or an authorized trial,
and obtains bundles from verified application payloads. Parsed state/ref values
alone grant neither choice nor execution permission. Component resolution is
offline: an independent root catalog supplies M1's authority; artifact TRUST or
backup declarations cannot replace it. No service, runtime, write or network.
"""
from . import artifacts as a
from .binary import RuntimeArtifact, RuntimeArtifactError, open_runtime_artifact
from .catalog import CoreCatalog, CoreCatalogError
from .selection import (BundleReference, ComponentReference, CoreSelectionError,
                        SelectionState, component_reference, encode_selection,
                        reference_identity, CORES)
from .store import CoreArtifactStore, CoreStoreError


class CoreResolutionError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _need(condition, code):
    if not condition:
        raise CoreResolutionError(code)


class CoreResolver:
    def __init__(self, *, app_source, target, bundles, catalog=None, store=None):
        _need(a._hex(app_source, 40) and type(target) is str and target in a.TARGETS,
              'RESOLUTION_CONTEXT')
        _need(type(bundles) is dict and set(bundles) == set(CORES), 'BUNDLE_CONTEXT')
        for core in CORES:
            value = bundles[core]
            _need(type(value) is RuntimeArtifact and type(value.reference) is BundleReference,
                  'BUNDLE_CONTEXT')
            _need(value.core == core and value.reference.app_source == app_source
                  and value.reference.target == target, 'BUNDLE_CONTEXT')
        _need(catalog is None or type(catalog) is CoreCatalog, 'CATALOG_CONTEXT')
        _need(store is None or type(store) is CoreArtifactStore, 'STORE_CONTEXT')
        self.app_source, self.target = app_source, target
        # Freeze the caller's map; replacement by a later UI/trial update must
        # create a new resolver, not silently change an existing runtime.
        self._bundles = tuple((core, bundles[core]) for core in CORES)
        self._catalog = catalog
        self._store = store

    def resolve(self, reference):
        try:
            reference_identity(reference)
            _need(reference.target == self.target, 'RESOLUTION_CONTEXT')
            if type(reference) is BundleReference:
                _need(reference.app_source == self.app_source, 'BUNDLE_CONTEXT')
                descriptor = dict(self._bundles)[reference.core]
                _need(descriptor.reference == reference, 'BUNDLE_IDENTITY')
            else:
                _need(type(reference) is ComponentReference, 'RESOLUTION_REFERENCE')
                catalog = self._catalog if self._catalog is not None else CoreCatalog()
                store = self._store if self._store is not None else CoreArtifactStore()
                entry = catalog.read(reference.release_manifest_sha256, app_source=self.app_source)
                _need(entry.authority.release_id == reference.release_id
                      and entry.authority.tag == reference.tag
                      and entry.tag_source == reference.builder_source
                      and entry.verified_release.manifest_sha256 == reference.release_manifest_sha256
                      and entry.verified_release.checksums_sha256 == reference.checksums_sha256,
                      'CATALOG_IDENTITY')
                material = store.read(reference.release_manifest_sha256,
                    authority=entry.authority, expected_tag=reference.tag,
                    expected_builder_source=entry.tag_source,
                    app_source=self.app_source, target=self.target)
                _need(material.identity.app_source == self.app_source
                      and component_reference(material.identity) == reference, 'STORE_IDENTITY')
                descriptor = RuntimeArtifact(reference, material.binary_path,
                    owner=store.layout.owner, anchor=store.layout.anchor, mode=0o555)
            # Verify the actual immutable component/private bundled material.
            # CoreRuntime still holds and rechecks its own FD before executing.
            with open_runtime_artifact(descriptor):
                pass
            return descriptor
        except CoreResolutionError:
            raise
        except (CoreCatalogError, CoreStoreError, CoreSelectionError, RuntimeArtifactError):
            raise CoreResolutionError('MATERIAL_UNAVAILABLE_OR_CHANGED') from None

    def current(self, state, core):
        """Resolve a caller-authenticated confirmed/trial state, never raw JSON."""
        try:
            _need(type(state) is SelectionState and state.app_source == self.app_source
                  and state.target == self.target, 'RESOLUTION_CONTEXT')
            encode_selection(state)
            _need(type(core) is str and core in CORES, 'RESOLUTION_CORE')
            return self.resolve(state.slot(core).current)
        except CoreSelectionError:
            raise CoreResolutionError('RESOLUTION_STATE') from None
