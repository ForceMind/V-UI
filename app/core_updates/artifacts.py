"""Read-only stdlib validation of an externally authorized core component.

The caller obtains authority through the fixed official Release API (or an
independently trusted cache), and resolves expected_builder_source from the
trusted tag ref. decode_authority parses that normalized snapshot; it cannot
prove where its bytes came from. A backup/ZIP-supplied snapshot is NOT authority.

This module proves publisher-authorized bytes and internal provenance consistency,
not upstream extraction, ELF/native compatibility, test execution or installation.
The future producer must verify the original upstream archive/extraction itself.
No network, environment switches, extraction, store writes or binary execution.
"""
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import struct
import zipfile
import zlib

REPOSITORY = 'ForceMind/V-UI'
REPOSITORY_ID = 1110299182
CONTRACT = 'core-selection-v1'
TARGETS = ('x86_64-gnu', 'x86_64-musl', 'aarch64-gnu', 'aarch64-musl')
UPSTREAMS = {'sing-box': 'SagerNet/sing-box', 'xray': 'XTLS/Xray-core'}
MAX_JSON = 64_000
MAX_PACK = 100_000_000
MAX_EXPANDED = 256_000_000
MAX_SOURCE = 128_000_000
MAX_LICENSE = 1_000_000
MAX_METADATA = 16_000
CHUNK = 1024 * 1024
COMMON = {'schema', 'repository', 'tag', 'core', 'version', 'revision', 'builder_source', 'compatibility'}


class CoreArtifactError(ValueError):
    """Only a fixed code: never include untrusted paths, payloads or exceptions."""
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _need(value, code):
    if not value:
        raise CoreArtifactError(code)


def _hex(value, length=64):
    return type(value) is str and re.fullmatch('[0-9a-f]{'+str(length)+'}', value) is not None


def _positive(value, maximum=2**63-1):
    return type(value) is int and 0 < value <= maximum


def _keys(value, keys):
    _need(type(value) is dict and set(value) == set(keys), 'FIELDS')


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        _need(key not in result, 'JSON_DUPLICATE')
        result[key] = value
    return result


def _number(_):
    raise CoreArtifactError('JSON_NUMBER')


def _integer(raw):
    _need(len(raw) <= 20, 'JSON_NUMBER')
    return int(raw)


def _unicode(value, depth=0):
    _need(depth <= 16, 'JSON_DEPTH')
    if type(value) is str:
        value.encode('utf-8')
    elif type(value) is dict:
        for key, child in value.items():
            _unicode(key, depth+1); _unicode(child, depth+1)
    elif type(value) is list:
        for child in value:
            _unicode(child, depth+1)


def _json(raw):
    _need(type(raw) is bytes and 0 < len(raw) <= MAX_JSON, 'JSON_LIMIT')
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs,
                           parse_float=_number, parse_constant=_number, parse_int=_integer)
        _unicode(value)
    except CoreArtifactError:
        raise
    except (ValueError, UnicodeError, RecursionError):
        raise CoreArtifactError('JSON_INVALID') from None
    _need(type(value) is dict, 'JSON_OBJECT')
    return value


def _tag(value):
    _need(type(value) is str and len(value) <= 100, 'TAG')
    match = re.fullmatch(r'core-(sing-box|xray)-v((?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*))-r([1-9][0-9]{0,8})', value)
    _need(match is not None, 'TAG')
    core, version, revision = match.groups()
    return core, version, int(revision)


def _pack_name(core, version, target):
    return 'vui-core-'+core+'-'+version+'-'+target+'.zip'


@dataclass(frozen=True)
class Asset:
    id: int
    name: str
    size: int
    sha256: str


@dataclass(frozen=True)
class Authority:
    release_id: int
    tag: str
    published_at: str
    assets: tuple


def decode_authority(raw):
    """Strictly decode a caller-trusted API snapshot; no trust is created here."""
    value = _json(raw)
    _keys(value, {'schema', 'repository', 'repository_id', 'release_id', 'tag', 'draft', 'prerelease', 'published_at', 'assets'})
    _need(type(value['schema']) is int and value['schema'] == 1 and value['repository'] == REPOSITORY
          and type(value['repository_id']) is int and value['repository_id'] == REPOSITORY_ID
          and _positive(value['release_id']) and value['draft'] is False and value['prerelease'] is False, 'AUTHORITY')
    _need(type(value['published_at']) is str and re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z', value['published_at']), 'AUTHORITY')
    try:
        datetime.strptime(value['published_at'], '%Y-%m-%dT%H:%M:%SZ')
    except ValueError:
        raise CoreArtifactError('AUTHORITY') from None
    core, version, _ = _tag(value['tag'])
    expected = {'CORE-RELEASE.json', 'SHA256SUMS'} | {_pack_name(core, version, t) for t in TARGETS}
    _need(type(value['assets']) is list and len(value['assets']) == 6, 'ASSET_SET')
    assets = []
    for item in value['assets']:
        _keys(item, {'id', 'name', 'size', 'digest'})
        _need(_positive(item['id']) and type(item['name']) is str and item['name'] in expected
              and _positive(item['size'], MAX_PACK) and type(item['digest']) is str
              and re.fullmatch('sha256:[0-9a-f]{64}', item['digest']) is not None, 'ASSET')
        if item['name'] in ('CORE-RELEASE.json', 'SHA256SUMS'):
            _need(item['size'] <= MAX_JSON, 'ASSET_SIZE')
        assets.append(Asset(item['id'], item['name'], item['size'], item['digest'][7:]))
    _need({a.name for a in assets} == expected and len({a.id for a in assets}) == 6, 'ASSET_SET')
    return Authority(value['release_id'], value['tag'], value['published_at'], tuple(sorted(assets, key=lambda a: a.name)))


def _compat(value, app_source):
    _keys(value, {'contract', 'app_sources'})
    sources = value['app_sources']
    _need(value['contract'] == CONTRACT and type(sources) is list and 0 < len(sources) <= 64
          and all(_hex(s, 40) for s in sources) and len(set(sources)) == len(sources)
          and sources == sorted(sources) and app_source in sources, 'COMPATIBILITY')
    return tuple(sources)


def _identity(value, authority, builder_source, app_source):
    core, version, revision = _tag(authority.tag)
    _need(type(value['schema']) is int and value['schema'] == 1 and value['repository'] == REPOSITORY
          and value['tag'] == authority.tag and value['core'] == core and value['version'] == version
          and type(value['revision']) is int and value['revision'] == revision
          and value['builder_source'] == builder_source, 'IDENTITY')
    return _compat(value['compatibility'], app_source)


@dataclass(frozen=True)
class Pack:
    target: str
    name: str
    size: int
    sha256: str
    manifest_sha256: str


@dataclass(frozen=True)
class VerifiedRelease:
    authority: Authority
    builder_source: str
    app_source: str
    app_sources: tuple
    manifest_sha256: str
    checksums_sha256: str
    packs: tuple


def verify_release(manifest, checksums, *, authority, expected_tag, expected_builder_source, app_source):
    """Bind small metadata to all six external API assets before accepting it."""
    _need(type(authority) is Authority and authority.tag == expected_tag, 'AUTHORITY_CONTEXT')
    _need(_hex(expected_builder_source, 40) and _hex(app_source, 40), 'SOURCE')
    assets = {a.name: a for a in authority.assets}
    for name, raw in (('CORE-RELEASE.json', manifest), ('SHA256SUMS', checksums)):
        _need(type(raw) is bytes and 0 < len(raw) <= MAX_JSON and len(raw) == assets[name].size
              and hashlib.sha256(raw).hexdigest() == assets[name].sha256, 'EXTERNAL_DIGEST')
    record = _json(manifest)
    _keys(record, COMMON | {'kind', 'packs'})
    _need(record['kind'] == 'core-release-v1', 'KIND')
    sources = _identity(record, authority, expected_builder_source, app_source)
    expected = set(assets) - {'SHA256SUMS'}
    table = {}
    try:
        lines = checksums.decode('ascii').splitlines()
    except UnicodeError:
        raise CoreArtifactError('CHECKSUMS') from None
    _need(len(lines) == 5, 'CHECKSUMS')
    for line in lines:
        match = re.fullmatch(r'([0-9a-f]{64})  ([A-Za-z0-9._-]+)', line)
        _need(match is not None, 'CHECKSUMS')
        checksum, name = match.groups()
        _need(name not in table, 'CHECKSUMS_DUPLICATE')
        table[name] = checksum
    _need(set(table) == expected and all(table[n] == assets[n].sha256 for n in table), 'CHECKSUMS_IDENTITY')
    _need(type(record['packs']) is list and len(record['packs']) == 4, 'PACK_SET')
    packs = []
    core, version, _ = _tag(authority.tag)
    for entry in record['packs']:
        _keys(entry, {'target', 'name', 'size', 'sha256', 'manifest_sha256'})
        _need(type(entry['target']) is str and entry['target'] in TARGETS, 'TARGET')
        name = _pack_name(core, version, entry['target'])
        _need(entry['name'] == name and type(entry['size']) is int and entry['size'] == assets[name].size
              and entry['sha256'] == assets[name].sha256 and _hex(entry['manifest_sha256']), 'PACK_IDENTITY')
        packs.append(Pack(entry['target'], name, entry['size'], entry['sha256'], entry['manifest_sha256']))
    _need({p.target for p in packs} == set(TARGETS), 'PACK_SET')
    return VerifiedRelease(authority, expected_builder_source, app_source, sources,
        assets['CORE-RELEASE.json'].sha256, assets['SHA256SUMS'].sha256, tuple(packs))


def _file_identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _directory_identity(info):
    return info.st_dev, info.st_ino


@contextmanager
def _opened(source):
    """Own a duplicate FD; FD inputs cannot attest any pathname or parents."""
    opened = []
    bindings = []
    file_binding = None
    try:
        if type(source) is int:
            _need(source >= 0, 'FILE')
            fd = os.dup(source); opened.append(fd)
        else:
            _need(isinstance(source, (str, Path)), 'PATH')
            path = Path(source)
            _need(path.is_absolute() and '..' not in path.parts, 'PATH')
            parent = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            opened.append(parent)
            for part in path.parts[1:-1]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                opened.append(child)
                bindings.append((parent, part, child, _directory_identity(os.fstat(child))))
                parent = child
            fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            opened.append(fd); file_binding = (parent, path.name)
        info = os.fstat(fd)
        _need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and 0 < info.st_size <= MAX_PACK, 'FILE_TYPE_OR_SIZE')
        before = _file_identity(info)
        def stable():
            _need(_file_identity(os.fstat(fd)) == before, 'FILE_CHANGED')
            if file_binding is not None:
                _need(_file_identity(os.stat(file_binding[1], dir_fd=file_binding[0], follow_symlinks=False)) == before, 'PATH_CHANGED')
            for parent_fd, name, child_fd, identity in bindings:
                current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                _need(stat.S_ISDIR(current.st_mode) and _directory_identity(current) == identity
                      and _directory_identity(os.fstat(child_fd)) == identity, 'PARENT_CHANGED')
        stable()
        yield fd, info.st_size, stable
        stable()
    finally:
        # Always attempt every owned close; never close the caller's descriptor.
        error = False
        for descriptor in reversed(opened):
            try: os.close(descriptor)
            except OSError: error = True
        if error:
            raise CoreArtifactError('FILE_CLOSE') from None


class _Reader:
    """pread keeps the caller FD's offset unchanged; no whole-archive read."""
    def __init__(self, fd, size):
        self.fd, self.size, self.position = fd, size, 0
    def seekable(self): return True
    def tell(self): return self.position
    def seek(self, offset, whence=os.SEEK_SET):
        position = offset if whence == os.SEEK_SET else self.position+offset if whence == os.SEEK_CUR else self.size+offset if whence == os.SEEK_END else -1
        _need(type(position) is int and 0 <= position <= self.size, 'ZIP_SEEK')
        self.position = position
        return position
    def read(self, size=-1):
        # ZipFile's end-record reader requests the small bounded tail with -1.
        if size == -1: size = self.size-self.position
        _need(type(size) is int and 0 <= size <= CHUNK, 'READ_LIMIT')
        raw = os.pread(self.fd, min(size, self.size-self.position), self.position)
        self.position += len(raw)
        return raw


def _digest_fd(fd, size):
    hasher = hashlib.sha256(); count = 0
    while count < size:
        raw = os.pread(fd, min(CHUNK, size-count), count)
        _need(bool(raw), 'FILE_CHANGED')
        count += len(raw); hasher.update(raw)
    return hasher.hexdigest()


def _zip_metadata(reader):
    size = reader.size
    reader.seek(max(0, size-65557))
    tail = reader.read(min(size, 65557)); position = tail.rfind(b'PK\x05\x06')
    _need(position >= 0 and len(tail)-position == 22, 'ZIP_END')
    _, disk, central_disk, disk_count, count, central_size, offset, comment = struct.unpack_from('<4s4H2LH', tail, position)
    absolute = size-len(tail)+position
    _need(disk == central_disk == comment == 0 and disk_count == count == 5
          and 46*count <= central_size <= MAX_METADATA and offset+central_size == absolute, 'ZIP_METADATA')
    return offset


def _local_records(reader, entries, central):
    """Cover the entire local-record area, without hidden/prefixed ZIP data.

    Accept normal local headers, force_zip64 local headers and bounded streaming
    data descriptors; the small component never needs a ZIP64 central directory.
    """
    cursor = 0
    for entry in sorted(entries, key=lambda item: item.header_offset):
        _need(entry.header_offset == cursor, 'ZIP_LOCAL_LAYOUT')
        reader.seek(cursor); raw = reader.read(30)
        _need(len(raw) == 30, 'ZIP_LOCAL_LAYOUT')
        signature, _, flags, compression, _, _, crc, compressed, size, name_size, extra_size = struct.unpack('<4s5H3L2H', raw)
        _need(signature == b'PK\x03\x04' and flags == entry.flag_bits
              and compression == entry.compress_type and name_size == len(entry.filename.encode('ascii'))
              and extra_size in (0, 20), 'ZIP_LOCAL_LAYOUT')
        _need(reader.read(name_size) == entry.filename.encode('ascii'), 'ZIP_LOCAL_NAME')
        extra = reader.read(extra_size)
        wide = bool(extra_size)
        if wide:
            kind, length, size, compressed = struct.unpack('<HHQQ', extra)
            _need(kind == 1 and length == 16, 'ZIP_LOCAL_EXTRA')
        if flags & 8:
            _need(crc in (0, entry.CRC) and size in (0, entry.file_size)
                  and compressed in (0, entry.compress_size), 'ZIP_LOCAL_SIZE')
        else:
            _need(crc == entry.CRC and size == entry.file_size and compressed == entry.compress_size, 'ZIP_LOCAL_SIZE')
        cursor = entry.header_offset+30+name_size+extra_size+entry.compress_size
        _need(cursor <= central, 'ZIP_LOCAL_LAYOUT')
        if flags & 8:
            reader.seek(cursor); signature = reader.read(4)
            signed = signature == b'PK\x07\x08'
            reader.seek(cursor+4 if signed else cursor)
            fmt = '<LQQ' if wide else '<LLL'
            length = struct.calcsize(fmt); descriptor = reader.read(length)
            _need(len(descriptor) == length and struct.unpack(fmt, descriptor)
                  == (entry.CRC, entry.compress_size, entry.file_size), 'ZIP_DESCRIPTOR')
            cursor += length+(4 if signed else 0)
    _need(cursor == central, 'ZIP_LOCAL_LAYOUT')


def _read_member(archive, name, *, limit, expected=None, retain=False):
    entry = archive.getinfo(name)
    _need(0 < entry.file_size <= limit, 'MEMBER_LIMIT')
    hasher = hashlib.sha256(); count = 0; chunks = []
    with archive.open(entry) as stream:
        while True:
            chunk = stream.read(CHUNK)
            if not chunk: break
            count += len(chunk); _need(count <= limit and count <= entry.file_size, 'MEMBER_LIMIT')
            hasher.update(chunk)
            if retain: chunks.append(chunk)
    _need(count == entry.file_size and (expected is None or hasher.hexdigest() == expected), 'MEMBER_DIGEST')
    return b''.join(chunks) if retain else hasher.hexdigest()


def _upstream(core, version, target):
    repository = UPSTREAMS[core]
    arch = 'amd64' if target.startswith('x86_64-') else 'arm64'
    if core == 'sing-box':
        platform = arch+('-glibc' if target.endswith('-gnu') else '-musl')
        base = 'sing-box-'+version+'-linux-'+platform
        return repository, base+'.tar.gz', base+'/sing-box'
    return repository, 'Xray-linux-64.zip' if arch == 'amd64' else 'Xray-linux-arm64-v8a.zip', 'xray'


def _provenance(value, core, version, target, files):
    _keys(value, {'schema', 'upstream', 'binary_sha256', 'license_sha256', 'source_sha256'})
    upstream = value['upstream']
    _keys(upstream, {'repository', 'tag', 'archive_name', 'archive_size', 'archive_sha256', 'member',
                     'source_commit', 'source_size', 'source_sha256', 'license_member'})
    repository, archive, member = _upstream(core, version, target)
    _need(type(value['schema']) is int and value['schema'] == 1 and upstream['repository'] == repository
          and upstream['tag'] == 'v'+version and upstream['archive_name'] == archive and upstream['member'] == member
          and _positive(upstream['archive_size'], MAX_PACK) and _hex(upstream['archive_sha256'])
          and _hex(upstream['source_commit'], 40), 'UPSTREAM')
    _need(upstream['license_member'] == repository.split('/')[1]+'-'+upstream['source_commit']+'/LICENSE', 'LICENSE_SOURCE')
    _need(type(upstream['source_size']) is int and upstream['source_size'] == files['SOURCE.tar.gz']['size']
          and upstream['source_sha256'] == files['SOURCE.tar.gz']['sha256']
          and value['source_sha256'] == files['SOURCE.tar.gz']['sha256']
          and value['license_sha256'] == files['LICENSE']['sha256']
          and value['binary_sha256'] == files['bin/'+core]['sha256'], 'PROVENANCE')


@dataclass(frozen=True)
class PackIdentity:
    repository: str
    release_id: int
    tag: str
    core: str
    version: str
    target: str
    builder_source: str
    app_source: str
    release_manifest_sha256: str
    checksums_sha256: str
    pack_sha256: str
    pack_manifest_sha256: str
    binary_sha256: str
    binary_size: int


def verify_pack(source, release, *, target):
    """Read an archive path or borrowed FD; no extraction or execution.

    Path traversal is no-follow and checked again before return. FD inputs prove
    only the stable regular inode, not parent/root ownership. Root store ownership
    and immutable publication belong to the later store module.
    """
    _need(type(release) is VerifiedRelease and type(target) is str and target in TARGETS, 'VERIFIED_CONTEXT')
    expected = next((p for p in release.packs if p.target == target), None)
    _need(expected is not None, 'TARGET')
    core, version, _ = _tag(release.authority.tag)
    try:
        with _opened(source) as (fd, size, stable):
            _need(size == expected.size and _digest_fd(fd, size) == expected.sha256, 'PACK_DIGEST')
            reader = _Reader(fd, size); central = _zip_metadata(reader)
            with zipfile.ZipFile(reader) as archive:
                entries = archive.infolist()
                names = [entry.filename for entry in entries]
                members = {'bin/'+core, 'LICENSE', 'SOURCE.tar.gz', 'PROVENANCE.json', 'CORE-PACK.json'}
                _need(len(entries) == len(set(names)) == 5 and set(names) == members
                      and sum(e.file_size for e in entries) <= MAX_EXPANDED, 'MEMBER_SET')
                for entry in entries:
                    mode = 0o555 if entry.filename == 'bin/'+core else 0o644
                    _need(entry.orig_filename == entry.filename and not entry.is_dir()
                          and entry.volume == 0
                          and entry.external_attr >> 16 == stat.S_IFREG | mode
                          and entry.create_system == 3
                          and not entry.flag_bits & (1 | 64) and not entry.extra and not entry.comment
                          and entry.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                          and 0 <= entry.header_offset < central and 0 <= entry.compress_size <= MAX_PACK, 'ZIP_ENTRY')
                _local_records(reader, entries, central)
                raw = _read_member(archive, 'CORE-PACK.json', limit=MAX_JSON,
                                   expected=expected.manifest_sha256, retain=True)
                record = _json(raw); _keys(record, COMMON | {'kind', 'target', 'files'})
                _need(record['kind'] == 'core-pack-v1' and record['target'] == target, 'PACK_IDENTITY')
                _need(_identity(record, release.authority, release.builder_source, release.app_source) == release.app_sources, 'COMPATIBILITY')
                files = record['files']; _keys(files, members-{'CORE-PACK.json'})
                for name, info in files.items():
                    _keys(info, {'size', 'mode', 'sha256'})
                    maximum = MAX_SOURCE if name == 'SOURCE.tar.gz' else MAX_LICENSE if name == 'LICENSE' else MAX_JSON if name == 'PROVENANCE.json' else MAX_EXPANDED
                    mode = 0o555 if name == 'bin/'+core else 0o644
                    _need(_positive(info['size'], maximum) and type(info['mode']) is int and info['mode'] == mode
                          and _hex(info['sha256']) and archive.getinfo(name).file_size == info['size'], 'FILE_RECORD')
                    _read_member(archive, name, limit=maximum, expected=info['sha256'])
                provenance = _json(_read_member(archive, 'PROVENANCE.json', limit=MAX_JSON, retain=True))
                _provenance(provenance, core, version, target, files)
            _need(_digest_fd(fd, size) == expected.sha256, 'PACK_CHANGED')
            stable()
            binary = files['bin/'+core]
            return PackIdentity(REPOSITORY, release.authority.release_id, release.authority.tag, core, version,
                target, release.builder_source, release.app_source, release.manifest_sha256,
                release.checksums_sha256, expected.sha256, expected.manifest_sha256, binary['sha256'], binary['size'])
    except CoreArtifactError:
        raise
    except (OSError, ValueError, KeyError, TypeError, AttributeError, zipfile.BadZipFile, RuntimeError, EOFError, OverflowError, struct.error, zlib.error):
        raise CoreArtifactError('PACK_INVALID') from None
