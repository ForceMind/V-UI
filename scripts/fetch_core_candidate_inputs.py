"""Anonymous official upstream acquisition for Actions candidate builds only.

Fixed first-candidate pins are independent of product defaults. No native run,
selection, Release/tag/catalog write or binary extraction occurs. Source archive
SHA is an observation of the fixed verified-HTTPS commit resource, NOT an
independently published upstream digest. Inputs/receipts do not grant authority.
Socket timeouts are not hard wall-clock bounds; Actions imposes a job timeout.
"""
import argparse
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.error import HTTPError
from urllib.parse import urljoin
from urllib.request import Request

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core_updates import artifacts as a, github as g
from scripts import build_core_component as producer

MAX_METADATA = 1_000_000
ROOT = Path(__file__).resolve().parents[1]
CANDIDATES = {'sing-box': {'core': 'sing-box',
              'repository': 'SagerNet/sing-box',
              'tag': 'v1.14.3',
              'source_commit': '7054cac5124465ffdb145870d7c01885566711b1',
              'release_id': 407496210,
              'draft': False,
              'prerelease': False,
              'assets': [{'id': 623965138,
                          'name': 'sing-box-1.14.3-linux-amd64-glibc.tar.gz',
                          'size': 32493273,
                          'digest': 'sha256:cadbaded70a635d0c37bf52614d8e3116542243247211e0b441328595c62acb6',
                          'state': 'uploaded',
                          'browser_download_url': 'https://github.com/SagerNet/sing-box/releases/download/v1.14.3/sing-box-1.14.3-linux-amd64-glibc.tar.gz'},
                         {'id': 623965135,
                          'name': 'sing-box-1.14.3-linux-amd64-musl.tar.gz',
                          'size': 32521387,
                          'digest': 'sha256:41fc81fd041134d4d7d850a928d7d2cde17a7d6563953f406b22d32a68bfab1f',
                          'state': 'uploaded',
                          'browser_download_url': 'https://github.com/SagerNet/sing-box/releases/download/v1.14.3/sing-box-1.14.3-linux-amd64-musl.tar.gz'},
                         {'id': 623965232,
                          'name': 'sing-box-1.14.3-linux-arm64-glibc.tar.gz',
                          'size': 29696397,
                          'digest': 'sha256:1612ea46765912efda931a817cf31d830a6e94065a80e3fde9094fa49ab7cd54',
                          'state': 'uploaded',
                          'browser_download_url': 'https://github.com/SagerNet/sing-box/releases/download/v1.14.3/sing-box-1.14.3-linux-arm64-glibc.tar.gz'},
                         {'id': 623965242,
                          'name': 'sing-box-1.14.3-linux-arm64-musl.tar.gz',
                          'size': 29733009,
                          'digest': 'sha256:12d6d67a4edf0dd54590865b1f4f79cd09ae043a02d648e9d1b5fccc8446ee07',
                          'state': 'uploaded',
                          'browser_download_url': 'https://github.com/SagerNet/sing-box/releases/download/v1.14.3/sing-box-1.14.3-linux-arm64-musl.tar.gz'}]},
 'xray': {'core': 'xray',
          'repository': 'XTLS/Xray-core',
          'tag': 'v26.10.21',
          'source_commit': '1b1647cd9ddf0abc34b981d4611fc03bcac1ca74',
          'release_id': 419510937,
          'draft': False,
          'prerelease': False,
          'assets': [{'id': 662392749,
                      'name': 'Xray-linux-64.zip',
                      'size': 15100223,
                      'digest': 'sha256:0eec3ed4163c273d8c8bf821f7d219e257921cf584aa3fb970715e458e631978',
                      'state': 'uploaded',
                      'browser_download_url': 'https://github.com/XTLS/Xray-core/releases/download/v26.10.21/Xray-linux-64.zip'},
                     {'id': 662394710,
                      'name': 'Xray-linux-arm64-v8a.zip',
                      'size': 13587069,
                      'digest': 'sha256:d1304946c486d98e6b886e9b931eaaaebb23835b30e217638037b7f2721d4e9c',
                      'state': 'uploaded',
                      'browser_download_url': 'https://github.com/XTLS/Xray-core/releases/download/v26.10.21/Xray-linux-arm64-v8a.zip'}]}}


class CandidateInputError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _need(value, code):
    if not value:
        raise CandidateInputError(code)


def _head():
    return subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True,
                                   stderr=subprocess.DEVNULL, timeout=10).strip()


class _Fetch:
    def __init__(self, opener=None):
        self.opener = g._opener() if opener is None else opener

    @contextmanager
    def response(self, original, *, asset, hops):
        url = original
        while True:
            g._url(url)
            headers = {'User-Agent': 'V-UI-core-candidate-inputs-v1', 'Accept-Encoding': 'identity',
                       'Accept': ('application/octet-stream' if asset or url.startswith('https://codeload.github.com/')
                                  else 'application/vnd.github+json')}
            if url.startswith('https://api.github.com/'):
                headers['X-GitHub-Api-Version'] = '2022-11-28'
            request = Request(url, headers=headers, method='GET')
            try:
                response = self.opener.open(request, timeout=10)
            except HTTPError as error:
                response = error
            try:
                _need(response.geturl() == url, 'RESPONSE_URL_CHANGED')
                code = response.getcode()
                _need(type(code) is int and 100 <= code <= 599, 'HTTP_STATUS')
                if code in g.REDIRECTS:
                    _need(asset and hops[0] > 0, 'REDIRECT_REFUSED')
                    location = g._header(response.headers, 'Location')
                    _need(type(location) is str, 'REDIRECT_REFUSED')
                    url = g._asset_redirect(urljoin(url, location), original)
                    hops[0] -= 1
                elif code != 200:
                    raise g._HTTPFailure(code)
                else:
                    yield response
                    return
            finally:
                try:
                    response.close()
                except Exception:
                    raise CandidateInputError('RESPONSE_CLOSE_FAILED') from None

    def transfer(self, url, *, limit, asset=False, expected=None, fd=None):
        hops = [3]
        for attempt in range(2):
            if fd is not None:
                os.ftruncate(fd, 0)
                os.lseek(fd, 0, os.SEEK_SET)
            try:
                with self.response(url, asset=asset, hops=hops) as response:
                    length = g._header(response.headers, 'Content-Length')
                    encoding = g._header(response.headers, 'Content-Encoding')
                    transfer = g._header(response.headers, 'Transfer-Encoding')
                    _need(encoding in (None, 'identity') and transfer in (None, 'chunked')
                          and not (length is not None and transfer is not None),
                          'HTTP_HEADERS')
                    if length is not None:
                        _need(type(length) is str and re.fullmatch('[0-9]{1,10}', length) is not None
                              and int(length) <= limit, 'HTTP_LENGTH')
                        length = int(length)
                    if expected is not None:
                        _need(length is None or length == expected[0], 'HTTP_LENGTH')
                    count, digest, pieces = 0, hashlib.sha256(), []
                    while True:
                        raw = response.read(min(a.CHUNK, limit + 1 - count))
                        _need(type(raw) is bytes, 'HTTP_BODY')
                        if not raw:
                            break
                        count += len(raw)
                        _need(count <= limit, 'HTTP_LIMIT')
                        digest.update(raw)
                        if fd is None:
                            pieces.append(raw)
                        else:
                            view = memoryview(raw)
                            while view:
                                written = os.write(fd, view)
                                _need(written > 0, 'WRITE_FAILED')
                                view = view[written:]
                    checksum = digest.hexdigest()
                    _need(count > 0 and (length is None or count == length), 'HTTP_TRUNCATED')
                    _need(expected is None or (count, checksum) == expected, 'HTTP_DIGEST')
                return count, checksum, b''.join(pieces) if fd is None else None
            except CandidateInputError:
                raise
            except Exception as error:
                if attempt == 0 and g._transient(error):
                    continue
                if isinstance(error, g._HTTPFailure):
                    raise CandidateInputError('HTTP_' + str(error.code)) from None
                raise CandidateInputError('FETCH_FAILED') from None
        raise CandidateInputError('FETCH_FAILED')

    def json(self, url):
        _, _, raw = self.transfer(url, limit=MAX_METADATA)
        value = g._json(raw)
        _need(type(value) is dict, 'API_SCHEMA')
        return value


def _snapshot(fetch, pin):
    api = 'https://api.github.com/repos/' + pin['repository']
    repo = fetch.json(api)
    _need(repo.get('full_name') == pin['repository'] and a._positive(repo.get('id'))
          and repo.get('html_url') == 'https://github.com/' + pin['repository'], 'API_REPOSITORY')
    release = fetch.json(api + '/releases/' + str(pin['release_id']))
    _need(type(release.get('id')) is int and release['id'] == pin['release_id']
          and release.get('tag_name') == pin['tag'] and release.get('draft') is False
          and release.get('prerelease') is False
          and release.get('url') == api + '/releases/' + str(pin['release_id'])
          and release.get('html_url') == 'https://github.com/' + pin['repository'] + '/releases/tag/' + pin['tag'],
          'API_RELEASE')
    assets = release.get('assets')
    _need(type(assets) is list and len(assets) <= 1000, 'API_ASSETS')
    names, identifiers = set(), set()
    selected = {}
    expected = {row['name']: row for row in pin['assets']}
    for row in assets:
        _need(type(row) is dict and type(row.get('name')) is str and a._positive(row.get('id'))
              and row['name'] not in names and row['id'] not in identifiers, 'API_ASSETS')
        names.add(row['name']); identifiers.add(row['id'])
        if row['name'] in expected:
            fields = ('id', 'name', 'size', 'digest', 'state', 'browser_download_url')
            projection = {key: row.get(key) for key in fields}
            _need(type(row.get('size')) is int and projection == expected[row['name']], 'API_ASSET_CHANGED')
            selected[row['name']] = projection
    _need(set(selected) == set(expected), 'API_ASSETS')
    ref = fetch.json(api + '/git/ref/tags/' + pin['tag'])
    _need(ref.get('ref') == 'refs/tags/' + pin['tag'], 'API_TAG')
    chain, seen, obj = [], set(), ref.get('object')
    for _ in range(5):
        _need(type(obj) is dict and obj.get('type') in ('commit', 'tag')
              and a._hex(obj.get('sha'), 40) and obj['sha'] not in seen, 'API_TAG')
        kind, commit = obj['type'], obj['sha']
        _need(obj.get('url') == api + '/git/' + ('commits/' if kind == 'commit' else 'tags/') + commit, 'API_TAG')
        seen.add(commit);chain.append(dict(type=kind, sha=commit))
        if kind == 'commit':
            _need(commit == pin['source_commit'], 'API_TAG_CHANGED')
            break
        tag = fetch.json(api + '/git/tags/' + commit)
        _need(tag.get('sha') == commit, 'API_TAG')
        obj = tag.get('object')
    else:
        raise CandidateInputError('API_TAG_DEPTH')
    return dict(repository=dict(id=repo['id'], full_name=repo['full_name'], html_url=repo['html_url']),
                release_id=pin['release_id'], tag=pin['tag'], draft=False, prerelease=False,
                assets=[selected[name] for name in sorted(selected)], tag_chain=chain)


def _small(stage, name, value):
    raw = producer._canonical(value)
    _need(len(raw) <= MAX_METADATA, 'RECEIPT_LIMIT')
    stage.write(stage.fd, name, (raw,), mode=0o600, size=len(raw), checksum=producer._sha(raw))


def acquire(workspace, *, core, builder_source, destination, opener=None, head=None):
    """Trusted opener/head injection is test-only; no CLI network/source overrides."""
    try:
        _need(type(core) is str and core in CANDIDATES and a._hex(builder_source, 40), 'INPUT_CONTEXT')
        actual = _head() if head is None else head()
        _need(actual == builder_source and a._hex(actual, 40), 'BUILDER_SOURCE_MISMATCH')
        pin = deepcopy(CANDIDATES[core])
        fetch = _Fetch(opener)
        with producer._workspace(workspace) as (scope, root):
            with producer._output(scope, root, workspace, destination) as (stage, _, _):
                before = _snapshot(fetch, pin)
                origins = []
                api = 'https://api.github.com/repos/' + pin['repository']
                for row in pin['assets']:
                    fd = stage.new_file(stage.fd, row['name'])
                    size, checksum, _ = fetch.transfer(api + '/releases/assets/' + str(row['id']),
                        limit=a.MAX_PACK, asset=True, expected=(row['size'], row['digest'][7:]), fd=fd)
                    os.fsync(fd)
                    origins.append(dict(name=row['name'], asset_id=row['id'], size=size, sha256=checksum,
                                        digest_basis='official-release-api-digest-and-reviewed-pin'))
                source_url = 'https://codeload.github.com/' + pin['repository'] + '/tar.gz/' + pin['source_commit']
                source_fd = stage.new_file(stage.fd, 'SOURCE.tar.gz')
                source_size, source_sha, _ = fetch.transfer(source_url, limit=a.MAX_SOURCE, fd=source_fd)
                os.fsync(source_fd)
                after = _snapshot(fetch, pin)
                _need(after == before, 'UPSTREAM_METADATA_CHANGED')
                version = pin['tag'][1:]
                archives = []
                assets = {row['name']: row for row in pin['assets']}
                for target in a.TARGETS:
                    _, name, member = a._upstream(core, version, target)
                    row = assets[name]
                    archives.append(dict(target=target, archive_name=name, archive_size=row['size'],
                                         archive_sha256=row['digest'][7:], member=member))
                expected = dict(schema=1, kind='core-component-build-input-v1', repository=a.REPOSITORY,
                    core=core, version=version, revision=1, tag='core-' + core + '-v' + version + '-r1',
                    builder_source=builder_source, compatibility=dict(contract=a.CONTRACT, app_sources=[builder_source]),
                    upstream=dict(repository=pin['repository'], tag=pin['tag'], source_commit=pin['source_commit'],
                                  source_size=source_size, source_sha256=source_sha, archives=archives))
                producer._metadata(producer._canonical(expected))
                receipt = dict(schema=1, kind='core-candidate-acquisition-v1', scope='candidate-inputs-only',
                    published=False, qualified=False, builder_source=builder_source, app_sources=[builder_source],
                    upstream=before, upstream_identity_sha256=producer._sha(producer._canonical(before)),
                    archives=origins, source=dict(url=source_url, commit=pin['source_commit'], size=source_size,
                        sha256=source_sha, digest_basis='observed-fixed-commit-verified-https-response',
                        independently_published_digest=False))
                _small(stage, 'expected.json', expected)
                _small(stage, 'acquisition-receipt.json', receipt)
                for name in [row['name'] for row in pin['assets']] + ['SOURCE.tar.gz', 'expected.json', 'acquisition-receipt.json']:
                    fd, size = producer._at(scope, stage.fd, name, max(a.MAX_SOURCE, MAX_METADATA))
                    _need(a._digest_fd(fd, size) == (source_sha if name == 'SOURCE.tar.gz' else
                          next((row['digest'][7:] for row in pin['assets'] if row['name'] == name),
                               producer._sha(producer._canonical(expected if name == 'expected.json' else receipt)))),
                          'ACQUISITION_CHANGED')
                scope.stable()
            return dict(scope='candidate-inputs-only', published=False, qualified=False,
                        core=core, version=version, source_sha256=source_sha,
                        expected_sha256=producer._sha(producer._canonical(expected)),
                        receipt_sha256=producer._sha(producer._canonical(receipt)))
    except CandidateInputError:
        raise
    except (producer.CoreComponentBuildError, a.CoreArtifactError, g.CoreGitHubError):
        raise CandidateInputError('ACQUISITION_REJECTED') from None
    except Exception:
        raise CandidateInputError('ACQUISITION_FAILED') from None


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise CandidateInputError('CLI_ARGUMENTS')


def main(argv=None):
    parser = _Parser(description=__doc__)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--core', choices=('sing-box', 'xray'), required=True)
    parser.add_argument('--builder-source', required=True)
    parser.add_argument('--output', type=Path, required=True)
    try:
        args = parser.parse_args(argv)
        print(json.dumps(acquire(args.workspace, core=args.core, builder_source=args.builder_source,
                                 destination=args.output), sort_keys=True))
        return 0
    except CandidateInputError as error:
        print('Candidate acquisition rejected: ' + error.code, file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
