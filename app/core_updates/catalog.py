"""Root-owned independent component-authority snapshots for offline consumers.

A Candidate dataclass is not provenance. record() must freshly recheck through the
fixed official M2 client before any catalog creation, then revalidate app_source
with M0. Production callers use the default client; trusted tests may inject a
real M2 instance with its fake opener. No JSON/backup import or fresh=True API.

read() is entirely offline and never constructs a network client. Trust derives
from this separate root-controlled immutable catalog and its supported record
path, not artifact/TRUST, backups, a checksum in input, or an action grant.
Snapshots are not a live revocation/status proof. No tokens, nonce, selection,
service, artifact-store, user-data, GC or runtime operations are implemented.
"""
from contextlib import contextmanager
from dataclasses import dataclass, field
import os
from pathlib import Path

from . import artifacts as a
from . import store as fs
from .github import Candidate, GitHubCoreClient, CoreGitHubError

PRODUCTION_LAYOUT = fs.Layout(Path('/etc/v-ui/core-catalog'))
FILES = frozenset({'CONTEXT.json', 'CORE-RELEASE.json', 'SHA256SUMS'})


class CoreCatalogError(ValueError):
    """Fixed code only; untrusted body/path/URL never forms the diagnostic."""
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _need(condition, code):
    if not condition:
        raise CoreCatalogError(code)


@contextmanager
def _root(layout, *, create=False):
    """Catalog-specific root walk using the audited M1 FD/binding scope.

    M1's _root additionally requires artifacts/, so it is deliberately not used
    for this distinct three-file catalog layout. No M1 abstraction is changed.
    """
    scope = fs._Scope(layout.owner)
    try:
        root, anchor = layout.root, layout.anchor
        _need(type(layout.owner) is int and layout.owner >= 0 and isinstance(root, Path) and isinstance(anchor, Path)
              and root.is_absolute() and anchor.is_absolute() and '..' not in root.parts
              and '..' not in anchor.parts and root != anchor, 'LAYOUT')
        try: relative = root.relative_to(anchor)
        except ValueError: raise CoreCatalogError('LAYOUT') from None
        fd = scope.own(os.open(anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW))
        info = os.fstat(fd)
        fs._directory(info, 0 if anchor == Path('/') else layout.owner, None if anchor == Path('/') else 0o700)
        anchor_identity = fs._dir_identity(info)
        for index, name in enumerate(relative.parts):
            if index == len(relative.parts)-1:
                fd = scope.directory(fd, name, create=create)
            else:
                child = scope.own(os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd))
                info = os.fstat(child); fs._directory(info, 0 if anchor == Path('/') else layout.owner)
                scope.directories.append((fd, name, child, fs._dir_identity(info))); fd = child
        yield scope, fd
        _need(fs._dir_identity(os.fstat(scope.fds[0])) == anchor_identity
              and fs._dir_identity(os.stat(anchor, follow_symlinks=False)) == anchor_identity, 'ANCHOR_CHANGED')
        scope.stable()
    finally:
        scope.close()


def _context(authority, tag_source):
    _need(a._hex(tag_source, 40), 'TAG_SOURCE')
    return dict(schema=1, kind='core-catalog-v1', authority=fs._authority(authority), tag_source=tag_source)


@dataclass(frozen=True)
class CatalogEntry:
    authority: a.Authority
    tag_source: str
    verified_release: a.VerifiedRelease
    manifest: bytes = field(repr=False)
    checksums: bytes = field(repr=False)
    reused: bool = True

    @property
    def manifest_sha256(self):
        return self.verified_release.manifest_sha256


class CoreCatalog:
    def __init__(self, layout=PRODUCTION_LAYOUT):
        _need(type(layout) is fs.Layout, 'LAYOUT')
        self.layout = layout

    def _read_at(self, scope, directory, manifest_sha, app_source, *, expected=None, reused=True):
        try:
            fs._directory(os.fstat(directory), self.layout.owner, 0o755)
            _need(set(os.listdir(directory)) == FILES, 'CATALOG_MEMBERS')
            value = a._json(scope.bytes(directory, 'CONTEXT.json'))
            a._keys(value, {'schema', 'kind', 'authority', 'tag_source'})
            _need(type(value['schema']) is int and value['schema'] == 1
                  and value['kind'] == 'core-catalog-v1' and a._hex(value['tag_source'], 40), 'CATALOG_CONTEXT')
            authority = a.decode_authority(fs._canonical(value['authority']))
            manifest = scope.bytes(directory, 'CORE-RELEASE.json')
            checksums = scope.bytes(directory, 'SHA256SUMS')
            verified = a.verify_release(manifest, checksums, authority=authority, expected_tag=authority.tag,
                expected_builder_source=value['tag_source'], app_source=app_source)
            _need(verified.manifest_sha256 == manifest_sha, 'CATALOG_ID')
            if expected is not None:
                _need(authority == expected.authority and value['tag_source'] == expected.tag_source
                      and verified == expected.verified_release and manifest == expected.manifest
                      and checksums == expected.checksums, 'CATALOG_IDENTITY_CHANGED')
            _need(set(os.listdir(directory)) == FILES, 'CATALOG_MEMBERS')
            scope.stable()
            return CatalogEntry(authority, value['tag_source'], verified, manifest, checksums, reused)
        except FileNotFoundError:
            raise CoreCatalogError('CATALOG_INVALID') from None

    def read(self, manifest_sha, *, app_source):
        """Offline trusted-root read only. Missing records are never initialized."""
        _need(a._hex(manifest_sha) and a._hex(app_source, 40), 'INPUT')
        try:
            with _root(self.layout) as (scope, root):
                directory = scope.directory(root, manifest_sha)
                return self._read_at(scope, directory, manifest_sha, app_source)
        except CoreCatalogError:
            raise
        except FileNotFoundError:
            raise CoreCatalogError('CATALOG_MISSING') from None
        except a.CoreArtifactError:
            raise CoreCatalogError('METADATA_REJECTED') from None
        except fs.CoreStoreError as error:
            raise CoreCatalogError(error.code) from None
        except (OSError, ValueError, KeyError, TypeError, AttributeError, RuntimeError):
            raise CoreCatalogError('CATALOG_INVALID') from None

    def record(self, candidate, *, app_source, client=None, deadline=None, stop=None):
        """Record only after live M2 reauthentication; never seed from a backup.

        The caller's trusted production code supplies no custom transport. Python
        object types alone do not authenticate origin; fresh fixed-API recheck is
        mandatory even on same-ID reuse. Root already has raw filesystem power:
        this module does not claim to defend against a malicious root caller.
        """
        _need(os.geteuid() == self.layout.owner, 'ROOT_REQUIRED')
        _need(type(candidate) is Candidate and a._hex(app_source, 40), 'INPUT')
        stage = None
        try:
            # Construct no client in __init__/read; only this explicit record path.
            client = GitHubCoreClient() if client is None else client
            _need(type(client) is GitHubCoreClient, 'CLIENT')
            fresh = client.recheck(candidate, deadline=deadline, stop=stop)
            _need(type(fresh) is a.VerifiedRelease and fresh.authority == candidate.authority
                  and fresh.builder_source == candidate.tag_source, 'AUTHORITY_REJECTED')
            # The given current app source is independent from the Candidate's
            # prior consumer context. Re-run M0, never just trust its dataclass.
            verified = a.verify_release(candidate.manifest, candidate.checksums,
                authority=fresh.authority, expected_tag=fresh.authority.tag,
                expected_builder_source=fresh.builder_source, app_source=app_source)
            expected = CatalogEntry(fresh.authority, fresh.builder_source, verified,
                                    candidate.manifest, candidate.checksums, False)
            context = fs._canonical(_context(expected.authority, expected.tag_source))
            _need(len(context) <= a.MAX_JSON, 'METADATA_LIMIT')
            manifest_sha = verified.manifest_sha256
            with _root(self.layout, create=True) as (scope, root):
                fs._lock(scope, root)
                try: existing = os.stat(manifest_sha, dir_fd=root, follow_symlinks=False)
                except FileNotFoundError: existing = None
                if existing is not None:
                    directory = scope.directory(root, manifest_sha)
                    result = self._read_at(scope, directory, manifest_sha, app_source, expected=expected)
                    os.fsync(directory); os.fsync(root)
                    return result
                staging = scope.directory(root, '.staging', mode=0o700, create=True)
                try:
                    stage = fs._Stage(scope, staging)
                    stage.create()
                    stage.small('CONTEXT.json', context)
                    stage.small('CORE-RELEASE.json', candidate.manifest)
                    stage.small('SHA256SUMS', candidate.checksums)
                    os.fchmod(stage.fd, 0o755); os.fsync(stage.fd)
                    self._read_at(scope, stage.fd, manifest_sha, app_source, expected=expected, reused=False)
                    stage.bound(); scope.stable()
                    fs._rename_new(staging, stage.name, root, manifest_sha)
                    os.fsync(root); os.fsync(staging)
                    directory = scope.directory(root, manifest_sha)
                    _need(fs._dir_identity(os.fstat(directory))[:2] == stage.identity, 'PUBLISH_CHANGED')
                    return self._read_at(scope, directory, manifest_sha, app_source, expected=expected, reused=False)
                finally:
                    if stage is not None: stage.cleanup()
        except CoreCatalogError:
            raise
        except CoreGitHubError:
            raise CoreCatalogError('AUTHORITY_REJECTED') from None
        except a.CoreArtifactError:
            raise CoreCatalogError('METADATA_REJECTED') from None
        except fs.CoreStoreError as error:
            raise CoreCatalogError(error.code) from None
        except (OSError, ValueError, KeyError, TypeError, AttributeError, RuntimeError):
            raise CoreCatalogError('CATALOG_INVALID') from None
