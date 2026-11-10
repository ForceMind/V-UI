"""Convert independently verified active application facts to bundle references.

Only trusted boot/maintenance code supplies these observations, after the
archive/READY/full-payload/runtime chain is verified. Dictionaries, versions
or this function do not grant source trust, selection or execution permission.
No filesystem, environment, network, core execution or persistence occurs here.
The material resolver/runtime rechecks actual files at their own boundaries.
"""
from pathlib import Path

from . import artifacts as a
from .binary import RuntimeArtifact
from .selection import CORES, CoreSelectionError, bundle_reference


class ApplicationCoreError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _need(value, code):
    if not value:
        raise ApplicationCoreError(code)


def bundle_artifacts(payload, manifest, ready, *, versions, owner, anchor=Path('/')):
    """Wrap the results of active_verified without rehashing the whole payload.

    versions comes from the already verified application's runtime constants,
    never the application's own version or user/upstream-latest input. The
    caller's ready result has already been matched to the actual host target.
    owner is the service UID verified for every file by verify_payload.
    """
    _need(isinstance(payload, Path) and payload.is_absolute(), 'APPLICATION_PATH')
    _need(type(manifest) is dict and type(manifest.get('schema')) is int
          and manifest['schema'] == 1 and manifest.get('kind') == 'release'
          and manifest.get('platform') == 'linux-multi-cpython312'
          and a._hex(manifest.get('source_commit'), 40), 'APPLICATION_CONTEXT')
    _need(type(ready) is dict and type(ready.get('runtime_key')) is str
          and ready['runtime_key'] in a.TARGETS
          and a._hex(ready.get('manifest_sha256')), 'APPLICATION_CONTEXT')
    target = ready['runtime_key']
    targets = manifest.get('targets')
    _need(type(targets) is list and targets
          and all(type(value) is str and value in a.TARGETS for value in targets)
          and len(set(targets)) == len(targets) and target in targets, 'APPLICATION_TARGET')
    _need(type(owner) is int and owner >= 0 and type(versions) is dict
          and set(versions) == set(CORES), 'APPLICATION_CORES')
    files = manifest.get('files')
    _need(type(files) is dict, 'APPLICATION_CORES')
    result = {}
    for core in CORES:
        name = 'cores/' + target.split('-', 1)[0] + '/' + core
        item = files.get(name)
        _need(type(item) is dict and set(item) == {'sha256', 'size', 'mode'}
              and a._hex(item['sha256']) and a._positive(item['size'], a.MAX_EXPANDED)
              and type(item['mode']) is int and item['mode'] == 0o700,
              'APPLICATION_BINARY')
        # bundle_reference validates canonical core versions and complete IDs.
        try:
            reference = bundle_reference(core=core, version=versions[core],
                target=target, app_source=manifest['source_commit'],
                payload_manifest_sha256=ready['manifest_sha256'],
                binary_sha256=item['sha256'], binary_size=item['size'])
            result[core] = RuntimeArtifact(reference, payload / name, owner=owner,
                                          anchor=anchor, mode=item['mode'])
        except CoreSelectionError:
            raise ApplicationCoreError('APPLICATION_CORES') from None
    return result
