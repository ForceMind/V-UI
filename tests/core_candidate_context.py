"""Private CI fixture material for candidate protocol tests."""
from functools import lru_cache
import os
import stat
from pathlib import Path

CORE_ROOT = Path("/opt/vui-ci-core-protocols")


@lru_cache(maxsize=1)
def candidate_context(path):
    from app.core_updates import artifacts, selection
    from app.core_updates.binary import RuntimeArtifact, open_runtime_artifact
    from tests.core_switch_fixture_runner import RELEASE_IDS

    assert os.geteuid() != 0 and os.getenv('GITHUB_ACTIONS') == 'true'
    assert os.getenv('RUNNER_ENVIRONMENT') == 'github-hosted'
    assert os.getenv('GITHUB_REPOSITORY') == artifacts.REPOSITORY
    config = Path(path)
    assert Path(os.environ['VUI_TEST_CORES']) == CORE_ROOT
    assert Path(os.environ['RUNNER_TEMP']) in config.parents
    with artifacts._opened(config) as (fd, size, stable):
        info = os.fstat(fd)
        assert 0 < size <= 8192 and info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o600
        value = artifacts._json(os.pread(fd, size + 1, 0))
        stable()
    assert set(value) == {'schema', 'kind', 'source', 'target', 'context', 'cores', 'baseline'}
    assert type(value['schema']) is int and value['schema'] == 1
    assert value['kind'] == 'core-candidate-protocol-fixture-v1'
    assert value['source'] == os.environ['EXPECTED_SOURCE'] and value['target'] == 'x86_64-gnu'
    assert value['context'] == dict(repository=artifacts.REPOSITORY,
        repository_id=artifacts.REPOSITORY_ID, run_id=int(os.environ['GITHUB_RUN_ID']),
        run_attempt=int(os.environ['GITHUB_RUN_ATTEMPT']))
    assert set(value['cores']) == {'sing-box', 'xray'}
    cores = {}
    for name, record in value['cores'].items():
        assert set(record) == {'version', 'tag', 'pack_manifest_sha256', 'pack_sha256',
                               'checksums_sha256', 'release_manifest_sha256', 'binary_sha256', 'binary_size'}
        # This is the private fixed CI identity used by the installed fixture.
        # The verified candidate pack supplies the digests and exact version.
        ref = selection.ComponentReference(artifacts.REPOSITORY, RELEASE_IDS[name], record['tag'],
            name, record['version'], value['target'], value['source'], record['release_manifest_sha256'],
            record['checksums_sha256'], record['pack_sha256'], record['pack_manifest_sha256'],
            record['binary_sha256'], record['binary_size'])
        artifact = RuntimeArtifact(ref, CORE_ROOT / name, owner=0, mode=0o555)
        with open_runtime_artifact(artifact):
            pass
        cores[name] = artifact
    baseline = Path(value['baseline'])
    assert baseline.is_absolute() and baseline.name == 'sing-box'
    assert Path(os.environ['RUNNER_TEMP']) in baseline.parents
    return cores, baseline


def artifacts_for_fixture():
    configured = os.environ.get('VUI_TEST_CANDIDATE_CONTEXT')
    return candidate_context(configured)[0].copy() if configured else None


def runtime_artifact(core, binary):
    material = artifacts_for_fixture()
    if material is None:
        return None
    artifact = material[core]
    assert Path(binary) == artifact.path
    return artifact


def runtime_for_fixture(runtime_type, core, binary, root):
    artifact = runtime_artifact(core, binary)
    if artifact is None:
        return runtime_type(core, binary, root)
    return runtime_type(core, binary, root, artifact=artifact)


def server_version(core, fixed):
    material = artifacts_for_fixture()
    return fixed if material is None else material[core].version


def singbox_client(server):
    configured = os.environ.get('VUI_TEST_CANDIDATE_CONTEXT')
    return str(candidate_context(configured)[1]) if configured else server
