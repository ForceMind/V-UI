"""Bounded anonymous client for the official V-UI core-component channel.

Only fixed official API routes are constructed here. Metadata never redirects;
asset redirects are bounded and HTTPS/host constrained. No auth/environment URL,
cache/store/selection writes, global urllib changes or binary execution.

Socket timeout and between-block deadline/cancel checks are NOT a slowloris hard
wall-clock bound: an active blocking read may outlive them. The independent caller
worker must impose its own hard lifetime and kill/reap policy.
"""
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
import fcntl
import hashlib
import http.client
import json
import math
import os
import re
import socket
import ssl
import stat
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin, urlsplit
from urllib.request import Request, HTTPRedirectHandler, HTTPSHandler, ProxyHandler, build_opener

from . import artifacts as a

API = 'https://api.github.com/repos/ForceMind/V-UI'
WEB = 'https://github.com/ForceMind/V-UI'
PER_PAGE = 20
MAX_PAGES = 5
MAX_CANDIDATES = 32
MAX_METADATA = 2_000_000
MAX_LINK = 8000
IO_TIMEOUT = 10
TRANSIENT = frozenset({502, 503, 504})
REDIRECTS = frozenset({301, 302, 303, 307, 308})
ASSET_HOSTS = frozenset({'release-assets.githubusercontent.com', 'objects.githubusercontent.com'})


class CoreGitHubError(ValueError):
    """Fixed diagnostic only; never echo URLs, API bodies or signed locations."""
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _need(condition, code):
    if not condition:
        raise CoreGitHubError(code)


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode()


def _float(raw):
    value = float(raw)
    _need(math.isfinite(value), 'JSON_NUMBER')
    return value


def _json(raw):
    _need(type(raw) is bytes and 0 < len(raw) <= MAX_METADATA, 'JSON_LIMIT')
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=a._pairs, parse_int=a._integer,
                           parse_constant=a._number, parse_float=_float)
        a._unicode(value)
        return value
    except CoreGitHubError:
        raise
    except (a.CoreArtifactError, ValueError, UnicodeError, RecursionError):
        raise CoreGitHubError('JSON_INVALID') from None


def _object(value):
    _need(type(value) is dict, 'API_SCHEMA')
    return value


def _header(headers, name):
    values = [value for key, value in headers.items() if key.lower() == name.lower()]
    _need(len(values) <= 1, 'HTTP_HEADERS')
    return values[0] if values else None


def _timestamp(value):
    _need(type(value) is str and re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z', value), 'API_SCHEMA')
    try: datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ')
    except ValueError: raise CoreGitHubError('API_SCHEMA') from None
    return value


def _url(value):
    _need(type(value) is str and 0 < len(value) <= 16384 and not re.search(r'[\x00-\x20\x7f\\]', value), 'URL_REFUSED')
    try:
        parts = urlsplit(value)
        valid = (parts.scheme == 'https' and parts.netloc == parts.hostname and not parts.username
                 and not parts.password and parts.port is None and not parts.fragment)
    except ValueError:
        raise CoreGitHubError('URL_REFUSED') from None
    _need(valid, 'URL_REFUSED')
    return parts


def _asset_redirect(value, original):
    parts = _url(value)
    _need(value == original or parts.hostname in ASSET_HOSTS, 'ASSET_REDIRECT_REFUSED')
    return value


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, target):
        return None


def _opener():
    context = ssl.create_default_context()
    _need(context.check_hostname is True and context.verify_mode == ssl.CERT_REQUIRED, 'TLS_REQUIRED')
    return build_opener(ProxyHandler({}), HTTPSHandler(context=context), _NoRedirect())


class _HTTPFailure(Exception):
    def __init__(self, code):
        self.code = code


def _transient(error):
    if isinstance(error, _HTTPFailure):
        return error.code in TRANSIENT
    return isinstance(error, (TimeoutError, socket.timeout)) or (
        isinstance(error, URLError) and isinstance(error.reason, (TimeoutError, socket.timeout)))


def _transport_error(error):
    if isinstance(error, _HTTPFailure):
        return CoreGitHubError('HTTP_'+str(error.code))
    if isinstance(error, (TimeoutError, socket.timeout)) or (
            isinstance(error, URLError) and isinstance(error.reason, (TimeoutError, socket.timeout))):
        return CoreGitHubError('IO_TIMEOUT')
    return CoreGitHubError('TRANSPORT_FAILED')


@dataclass(frozen=True)
class ReleaseHint:
    release_id: int
    tag: str
    core: str
    version: str
    revision: int
    published_at: str


@dataclass(frozen=True)
class Discovery:
    hints: tuple
    truncated: bool
    pages: int
    examined: int


@dataclass(frozen=True)
class Candidate:
    authority: a.Authority
    verified_release: a.VerifiedRelease
    tag_source: str
    manifest: bytes = field(repr=False)
    checksums: bytes = field(repr=False)
    repository_metadata: bytes = field(repr=False)
    release_metadata: bytes = field(repr=False)
    ref_metadata: tuple = field(repr=False)


class GitHubCoreClient:
    def __init__(self, *, opener=None):
        # The injection is trusted Python test code, never a user/baseURL switch.
        self._opener = _opener() if opener is None else opener

    def _check(self, deadline, stop):
        if stop is not None:
            _need(callable(stop), 'CANCEL_INPUT')
            try: cancelled = stop()
            except Exception: raise CoreGitHubError('CANCEL_CHECK_FAILED') from None
            _need(type(cancelled) is bool, 'CANCEL_INPUT')
            _need(not cancelled, 'CANCELLED')
        if deadline is None:
            return IO_TIMEOUT
        _need(type(deadline) in (int, float) and -(2**63) <= deadline <= 2**63-1
              and math.isfinite(deadline), 'DEADLINE_INPUT')
        remaining = deadline-time.monotonic()
        _need(remaining > 0, 'DEADLINE')
        return min(IO_TIMEOUT, remaining)

    @contextmanager
    def _response(self, url, *, asset, hops, deadline, stop):
        original = url
        while True:
            timeout = self._check(deadline, stop)
            parts = _url(url)
            headers = {'User-Agent': 'V-UI-core-component-v1',
                       'Accept': 'application/octet-stream' if asset else 'application/vnd.github+json'}
            if parts.hostname == 'api.github.com':
                headers['X-GitHub-Api-Version'] = '2022-11-28'
            request = Request(url, headers=headers, method='GET')
            try: response = self._opener.open(request, timeout=timeout)
            except HTTPError as error: response = error
            try:
                _need(response.geturl() == url, 'RESPONSE_URL_CHANGED')
                code = response.getcode()
                _need(type(code) is int and 100 <= code <= 599, 'HTTP_STATUS')
                if code in REDIRECTS:
                    _need(asset, 'METADATA_REDIRECT')
                    _need(hops[0] > 0, 'REDIRECT_LIMIT')
                    location = _header(response.headers, 'Location')
                    _need(type(location) is str and 0 < len(location) <= 16384
                          and not re.search(r'[\x00-\x20\x7f\\]', location), 'ASSET_REDIRECT_REFUSED')
                    url = _asset_redirect(urljoin(url, location), original)
                    hops[0] -= 1
                elif code != 200:
                    raise _HTTPFailure(code)
                else:
                    yield response
                    return
            finally:
                response.close()

    def _transfer(self, url, *, limit, asset=False, expected=None, reset=None, sink=None, deadline=None, stop=None):
        # Two original-URL attempts total, at most three asset redirects combined.
        _need(type(url) is str and (url == API or url.startswith(API+'/')), 'API_ROUTE')
        if asset:
            _need(re.fullmatch(re.escape(API)+r'/releases/assets/[1-9][0-9]{0,18}', url), 'API_ROUTE')
        hops = [3]
        for attempt in range(2):
            self._check(deadline, stop)
            if reset is not None: reset()
            try:
                with self._response(url, asset=asset, hops=hops, deadline=deadline, stop=stop) as response:
                    length = _header(response.headers, 'Content-Length')
                    if length is not None:
                        _need(type(length) is str and length.isascii() and length.isdigit() and len(length) <= 12
                              and int(length) <= limit and (expected is None or int(length) == expected.size), 'HTTP_SIZE')
                    total = 0; digest = hashlib.sha256(); chunks = []
                    while True:
                        self._check(deadline, stop)
                        asked = min(a.CHUNK, limit+1-total)
                        reader = getattr(response, 'read1', None)
                        if not callable(reader):
                            reader = response.read
                            asked = min(65536, asked)
                        raw = reader(asked)
                        self._check(deadline, stop)
                        _need(type(raw) is bytes and len(raw) <= asked, 'RESPONSE_BYTES')
                        if not raw: break
                        total += len(raw); _need(total <= limit, 'RESPONSE_SIZE')
                        digest.update(raw)
                        if sink is None: chunks.append(raw)
                        else: sink(raw, total-len(raw))
                    _need(length is None or total == int(length), 'HTTP_LENGTH')
                    _need(expected is None or total == expected.size and digest.hexdigest() == expected.sha256, 'ASSET_DIGEST')
                    return (b''.join(chunks), {} if asset else {'link': _header(response.headers, 'Link')}) if sink is None else total
            except (OSError, URLError, _HTTPFailure, http.client.HTTPException, EOFError) as error:
                if attempt == 0 and _transient(error):
                    continue
                raise _transport_error(error) from None

    def _get(self, suffix, *, deadline, stop):
        raw, headers = self._transfer(API+suffix, limit=MAX_METADATA, deadline=deadline, stop=stop)
        return _json(raw), raw, headers

    def _repository(self, *, deadline, stop):
        value, raw, _ = self._get('', deadline=deadline, stop=stop)
        _object(value)
        _need(type(value.get('id')) is int and value['id'] == a.REPOSITORY_ID
              and value.get('full_name') == a.REPOSITORY and value.get('private') is False, 'REPOSITORY_IDENTITY')
        return raw

    def _release(self, release_id, *, deadline, stop):
        _need(a._positive(release_id), 'RELEASE_ID')
        value, raw, _ = self._get('/releases/'+str(release_id), deadline=deadline, stop=stop)
        _object(value)
        _need(type(value.get('id')) is int and value['id'] == release_id
              and value.get('url') == API+'/releases/'+str(release_id)
              and value.get('draft') is False and value.get('prerelease') is False, 'RELEASE_IDENTITY')
        tag = value.get('tag_name')
        a._tag(tag)
        _need(value.get('html_url') == WEB+'/releases/tag/'+tag, 'RELEASE_IDENTITY')
        assets = value.get('assets')
        _need(type(assets) is list and len(assets) == 6, 'RELEASE_ASSETS')
        selected = []
        for item in assets:
            _object(item)
            _need(a._positive(item.get('id')) and type(item.get('name')) is str and item.get('state') == 'uploaded'
                  and item.get('url') == API+'/releases/assets/'+str(item['id'])
                  and item.get('browser_download_url') == WEB+'/releases/download/'+tag+'/'+item['name'], 'ASSET_IDENTITY')
            selected.append({key: item.get(key) for key in ('id', 'name', 'size', 'digest')})
        authority = a.decode_authority(_canonical(dict(schema=1, repository=a.REPOSITORY,
            repository_id=a.REPOSITORY_ID, release_id=release_id, tag=tag, draft=False, prerelease=False,
            published_at=value.get('published_at'), assets=selected)))
        return authority, raw

    def _tag_source(self, tag, *, deadline, stop):
        a._tag(tag)
        path = '/git/ref/tags/'+quote(tag, safe='')
        value, raw, _ = self._get(path, deadline=deadline, stop=stop)
        _object(value)
        _need(value.get('ref') == 'refs/tags/'+tag
              and value.get('url') == API+'/git/refs/tags/'+quote(tag, safe=''), 'TAG_REF')
        records = [raw]; seen = set(); hops = 0
        while True:
            obj = _object(value.get('object'))
            kind, source = obj.get('type'), obj.get('sha')
            _need(kind in ('commit', 'tag') and a._hex(source, 40), 'TAG_OBJECT')
            expected = API+'/git/'+('commits/' if kind == 'commit' else 'tags/')+source
            _need(obj.get('url') == expected, 'TAG_OBJECT')
            if kind == 'commit': return source, tuple(records)
            _need(source not in seen and hops < 4, 'TAG_DEPTH_OR_CYCLE')
            seen.add(source); hops += 1
            value, raw, _ = self._get('/git/tags/'+source, deadline=deadline, stop=stop)
            _object(value); _need(value.get('sha') == source and value.get('url') == expected, 'TAG_OBJECT')
            records.append(raw)

    def _context(self, release_id, *, deadline, stop):
        _need(a._positive(release_id), 'RELEASE_ID')
        repo = self._repository(deadline=deadline, stop=stop)
        authority, metadata = self._release(release_id, deadline=deadline, stop=stop)
        source, refs = self._tag_source(authority.tag, deadline=deadline, stop=stop)
        return authority, source, repo, metadata, refs

    def discover(self, core, *, deadline=None, stop=None):
        """Bounded hints with explicit truncation, never a complete-history claim."""
        try:
            _need(type(core) is str and core in a.UPSTREAMS, 'CORE')
            self._repository(deadline=deadline, stop=stop)
            hints = []; ids = set(); tags = set()
            for page in range(1, MAX_PAGES+1):
                value, _, headers = self._get('/releases?per_page='+str(PER_PAGE)+'&page='+str(page), deadline=deadline, stop=stop)
                _need(type(value) is list and len(value) <= PER_PAGE, 'DISCOVERY_SCHEMA')
                limited = False
                for item in value:
                    _object(item)
                    identifier, tag = item.get('id'), item.get('tag_name')
                    _need(a._positive(identifier) and type(tag) is str and 0 < len(tag) <= 200
                          and type(item.get('draft')) is bool and type(item.get('prerelease')) is bool, 'DISCOVERY_SCHEMA')
                    _need(identifier not in ids and tag not in tags, 'DISCOVERY_CHANGED')
                    ids.add(identifier); tags.add(tag)
                    if item['draft'] or item['prerelease'] or not tag.startswith('core-'+core+'-'): continue
                    parsed, version, revision = a._tag(tag)
                    _need(item.get('url') == API+'/releases/'+str(identifier), 'DISCOVERY_SCHEMA')
                    hint = ReleaseHint(identifier, tag, parsed, version, revision, _timestamp(item.get('published_at')))
                    if len(hints) < MAX_CANDIDATES:
                        hints.append(hint)
                    if len(hints) == MAX_CANDIDATES: limited = True
                next_page = _links(headers.get('link'), page)
                if limited: return Discovery(tuple(hints), True, page, len(ids))
                if not next_page and len(value) < PER_PAGE: return Discovery(tuple(hints), False, page, len(ids))
            return Discovery(tuple(hints), True, MAX_PAGES, len(ids))
        except CoreGitHubError:
            raise
        except a.CoreArtifactError:
            raise CoreGitHubError('COMPONENT_REJECTED') from None
        except (ValueError, KeyError, TypeError, AttributeError):
            raise CoreGitHubError('API_SCHEMA') from None

    def inspect(self, release_id, *, app_source, deadline=None, stop=None):
        try:
            _need(a._hex(app_source, 40), 'APP_SOURCE')
            authority, source, repo, raw, refs = self._context(release_id, deadline=deadline, stop=stop)
            files = {}
            for name in ('CORE-RELEASE.json', 'SHA256SUMS'):
                asset = next(item for item in authority.assets if item.name == name)
                files[name], _ = self._transfer(API+'/releases/assets/'+str(asset.id), limit=asset.size,
                    asset=True, expected=asset, deadline=deadline, stop=stop)
            release = a.verify_release(files['CORE-RELEASE.json'], files['SHA256SUMS'], authority=authority,
                expected_tag=authority.tag, expected_builder_source=source, app_source=app_source)
            candidate = Candidate(authority, release, source, files['CORE-RELEASE.json'], files['SHA256SUMS'], repo, raw, refs)
            self.recheck(candidate, deadline=deadline, stop=stop)
            return candidate
        except CoreGitHubError:
            raise
        except a.CoreArtifactError:
            raise CoreGitHubError('COMPONENT_REJECTED') from None
        except (ValueError, KeyError, TypeError, AttributeError):
            raise CoreGitHubError('API_SCHEMA') from None

    def recheck(self, candidate, *, deadline=None, stop=None):
        try:
            _need(type(candidate) is Candidate and type(candidate.authority) is a.Authority
                  and type(candidate.verified_release) is a.VerifiedRelease, 'CANDIDATE')
            authority, source, _, _, _ = self._context(candidate.authority.release_id, deadline=deadline, stop=stop)
            _need(authority == candidate.authority and source == candidate.tag_source, 'CONTEXT_CHANGED')
            verified = a.verify_release(candidate.manifest, candidate.checksums, authority=authority,
                expected_tag=authority.tag, expected_builder_source=source, app_source=candidate.verified_release.app_source)
            _need(verified == candidate.verified_release, 'CONTEXT_CHANGED')
            return verified
        except CoreGitHubError:
            raise
        except a.CoreArtifactError:
            raise CoreGitHubError('COMPONENT_REJECTED') from None
        except (ValueError, KeyError, TypeError, AttributeError):
            raise CoreGitHubError('API_SCHEMA') from None

    def download_pack(self, candidate, target, fd, *, deadline=None, stop=None):
        """The sole write API, to a caller-owned new empty private O_RDWR FD.

        Caller grants exclusive use and must close/unlink its own temporary file
        on failure: partial or fully copied but unconfirmed bytes are NOT success.
        This function neither closes the caller FD nor deletes a pathname. Typed
        timeout retry truncates only this supplied new sink before restarting.
        """
        _need(type(fd) is int and fd >= 0 and type(target) is str and target in a.TARGETS, 'SINK_INPUT')
        duplicate = None
        try:
            duplicate = os.dup(fd); before = os.fstat(duplicate)
            flags = fcntl.fcntl(duplicate, fcntl.F_GETFL)
            _need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_uid == os.geteuid()
                  and stat.S_IMODE(before.st_mode) == 0o600 and before.st_size == 0
                  and os.lseek(duplicate, 0, os.SEEK_CUR) == 0
                  and flags & os.O_ACCMODE == os.O_RDWR and not flags & os.O_APPEND, 'SINK_INPUT')
            identity = (before.st_dev, before.st_ino, before.st_mode, before.st_uid, before.st_gid)
            def stable():
                now = os.fstat(duplicate)
                _need((now.st_dev, now.st_ino, now.st_mode, now.st_uid, now.st_gid) == identity and now.st_nlink == 1, 'SINK_CHANGED')
            verified = self.recheck(candidate, deadline=deadline, stop=stop)
            selected = next(item for item in candidate.authority.assets if item.name == a._pack_name(
                a._tag(candidate.authority.tag)[0], a._tag(candidate.authority.tag)[1], target))
            def reset():
                stable(); os.ftruncate(duplicate, 0)
            def sink(raw, offset):
                stable(); view = memoryview(raw)
                while view:
                    try: written = os.pwrite(duplicate, view, offset)
                    except OSError: raise CoreGitHubError('SINK_WRITE') from None
                    _need(written > 0, 'SINK_WRITE')
                    offset += written; view = view[written:]
            self._transfer(API+'/releases/assets/'+str(selected.id), limit=selected.size, asset=True,
                expected=selected, reset=reset, sink=sink, deadline=deadline, stop=stop)
            stable(); _need(os.fstat(duplicate).st_size == selected.size, 'SINK_SIZE'); os.fsync(duplicate)
            verified_file = a._file_identity(os.fstat(duplicate))
            result = a.verify_pack(duplicate, verified, target=target)
            _need(a._file_identity(os.fstat(duplicate)) == verified_file, 'SINK_CHANGED')
            self.recheck(candidate, deadline=deadline, stop=stop); stable()
            _need(a._file_identity(os.fstat(duplicate)) == verified_file, 'SINK_CHANGED')
            return result
        except CoreGitHubError:
            raise
        except a.CoreArtifactError:
            raise CoreGitHubError('COMPONENT_REJECTED') from None
        except (OSError, ValueError, KeyError, TypeError, AttributeError, RuntimeError):
            raise CoreGitHubError('DOWNLOAD_FAILED') from None
        finally:
            if duplicate is not None:
                try: os.close(duplicate)
                except OSError: raise CoreGitHubError('SINK_CLOSE') from None


def _links(raw, page):
    if raw is None:
        return False
    _need(type(raw) is str and 0 < len(raw) <= MAX_LINK, 'PAGINATION')
    relations = set(); next_page = False
    for part in raw.split(','):
        match = re.fullmatch(r'\s*<([^>]+)>; rel="(next|prev|first|last)"\s*', part)
        _need(match is not None, 'PAGINATION')
        url, relation = match.groups()
        _need(relation not in relations, 'PAGINATION'); relations.add(relation)
        parsed = _url(url)
        fields = parsed.query.split('&')
        _need(len(fields) == 2 and all('=' in item for item in fields), 'PAGINATION')
        query = dict(item.split('=', 1) for item in fields)
        _need(parsed.scheme+'://'+parsed.netloc+parsed.path == API+'/releases'
              and set(query) == {'per_page', 'page'} and query['per_page'] == '20'
              and re.fullmatch(r'[1-9][0-9]{0,5}', query['page']), 'PAGINATION')
        number = int(query['page'])
        if relation == 'next':
            _need(number == page+1, 'PAGINATION'); next_page = True
        elif relation == 'prev': _need(page > 1 and number == page-1, 'PAGINATION')
        elif relation == 'first': _need(number == 1, 'PAGINATION')
        else: _need(number >= page, 'PAGINATION')
    return next_page
