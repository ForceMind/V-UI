"""Offline Actions-only candidate component pack producer; never a publisher.

Inputs are independently reviewed official archive/source facts, not authority.
This tool performs no downloads, native execution, release/tag/catalog writes or
qualification. A private CI workspace and JSON/path arguments do not establish
where material came from. Existing product pins and code are never modified.
"""
from contextlib import contextmanager
import argparse
from copy import deepcopy
import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import struct
import sys
import tarfile
import zipfile
import zlib

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core_updates import artifacts as a, store as fs

MAX_MEMBERS = 10000
MAX_CENTRAL = 2 * 1024 * 1024
FONTS = ('.ttf', '.otf', '.woff', '.woff2')


class CoreComponentBuildError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _need(value, code):
    if not value:
        raise CoreComponentBuildError(code)


def _keys(value, fields):
    _need(type(value) is dict and set(value) == set(fields), 'INPUT_FIELDS')


def _canonical(value):
    return fs._canonical(value)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _metadata(raw):
    value = a._json(raw)
    _keys(value, a.COMMON | {'kind', 'upstream'})
    _need(type(value['schema']) is int and value['schema'] == 1
          and value['kind'] == 'core-component-build-input-v1' and value['repository'] == a.REPOSITORY
          and a._hex(value['builder_source'], 40), 'INPUT_CONTEXT')
    core, version, revision = a._tag(value['tag'])
    _need((value['core'], value['version'], value['revision']) == (core, version, revision)
          and type(value['revision']) is int, 'INPUT_CONTEXT')
    compatibility = value['compatibility']
    _keys(compatibility, ('contract', 'app_sources'))
    sources = compatibility['app_sources']
    _need(type(sources) is list and 0 < len(sources) <= 64
          and all(a._hex(source, 40) for source in sources)
          and sources == sorted(set(sources)) and compatibility['contract'] == a.CONTRACT, 'INPUT_COMPATIBILITY')
    upstream = value['upstream']
    _keys(upstream, ('repository', 'tag', 'source_commit', 'source_size', 'source_sha256', 'archives'))
    _need(upstream['repository'] == a.UPSTREAMS[core] and upstream['tag'] == 'v' + version
          and a._hex(upstream['source_commit'], 40) and a._positive(upstream['source_size'], a.MAX_SOURCE)
          and a._hex(upstream['source_sha256']), 'INPUT_UPSTREAM')
    _need(type(upstream['archives']) is list and len(upstream['archives']) == 4, 'INPUT_TARGETS')
    targets, shared = set(), {}
    for row in upstream['archives']:
        _keys(row, ('target', 'archive_name', 'archive_size', 'archive_sha256', 'member'))
        target = row['target']
        _need(type(target) is str and target in a.TARGETS and target not in targets, 'INPUT_TARGETS')
        targets.add(target)
        _, name, member = a._upstream(core, version, target)
        _need(row['archive_name'] == name and row['member'] == member
              and a._positive(row['archive_size'], a.MAX_PACK) and a._hex(row['archive_sha256']), 'INPUT_ARCHIVE')
        identity = tuple(row[key] for key in ('archive_size', 'archive_sha256', 'member'))
        _need(name not in shared or shared[name] == identity, 'INPUT_ARCHIVE_DRIFT')
        shared[name] = identity
    _need(targets == set(a.TARGETS), 'INPUT_TARGETS')
    return value


def _common(meta):
    return {key: deepcopy(meta[key]) for key in sorted(a.COMMON)}


def _row(meta, target):
    _need(type(target) is str and target in a.TARGETS, 'INPUT_TARGET')
    return next(row for row in meta['upstream']['archives'] if row['target'] == target)


def _name(value, *, directory=False):
    _need(type(value) is str and 0 < len(value) <= 512
          and not any(ord(char) < 32 or ord(char) == 127 for char in value)
          and '\\' not in value and ':' not in value and not value.startswith('/'), 'ARCHIVE_PATH')
    normalized = value[:-1] if directory and value.endswith('/') else value
    path = PurePosixPath(normalized)
    _need(normalized and str(path) == normalized
          and all(part not in ('', '.', '..') for part in path.parts), 'ARCHIVE_PATH')
    _need(not normalized.lower().endswith(FONTS), 'ARCHIVE_FONT')
    return normalized


class _Budget:
    def __init__(self, stream):
        self.stream, self.count = stream, 0
    def read(self, size=-1):
        _need(type(size) is int, 'ARCHIVE_READ')
        size = a.CHUNK if size < 0 else min(size, a.CHUNK)
        raw = self.stream.read(min(size, a.MAX_EXPANDED + 1 - self.count))
        self.count += len(raw)
        _need(self.count <= a.MAX_EXPANDED, 'ARCHIVE_EXPANDED_LIMIT')
        return raw


def _stream(stream, expected_size, *, writer=None, retain=False):
    digest, count, pieces = hashlib.sha256(), 0, []
    while True:
        raw = stream.read(a.CHUNK)
        if not raw:
            break
        count += len(raw)
        _need(count <= expected_size, 'ARCHIVE_MEMBER_SIZE')
        digest.update(raw)
        if writer is not None:
            written = writer.write(raw)
            _need(written is None or written == len(raw), 'WRITE_FAILED')
        if retain:
            pieces.append(raw)
    _need(count == expected_size, 'ARCHIVE_MEMBER_SIZE')
    return digest.hexdigest(), count, b''.join(pieces) if retain else None


def _tar(stream, selected, *, size_limit, writer=None, root=None, retain=False):
    """Scan every header, bound expanded input, and never extract paths."""
    seen, total, found = set(), 0, None
    with gzip.GzipFile(fileobj=stream, mode='rb') as compressed:
        budget = _Budget(compressed)
        with tarfile.open(fileobj=budget, mode='r|') as archive:
            for index, info in enumerate(archive):
                _need(index < MAX_MEMBERS, 'ARCHIVE_MEMBER_LIMIT')
                name = _name(info.name, directory=info.isdir())
                _need(name not in seen and info.type in (tarfile.REGTYPE, tarfile.AREGTYPE, tarfile.DIRTYPE) and info.sparse is None
                      and not info.mode & 0o6000, 'ARCHIVE_MEMBER_TYPE')
                seen.add(name)
                if root is not None:
                    _need(name == root or name.startswith(root + '/'), 'SOURCE_ROOT')
                _need(type(info.size) is int and info.size >= 0, 'ARCHIVE_MEMBER_SIZE')
                total += info.size
                _need(total <= a.MAX_EXPANDED, 'ARCHIVE_EXPANDED_LIMIT')
                if name == selected:
                    _need(info.isfile() and 0 < info.size <= size_limit, 'ARCHIVE_SELECTED_MEMBER')
                    with archive.extractfile(info) as content:
                        found = _stream(content, info.size, writer=writer, retain=retain)
            # Tar iteration stops at its EOF marker; drain the stream including
            # its read-ahead buffer to reject hidden appended records and verify
            # gzip CRC/concatenated members within the same expansion budget.
            trailing = 0
            while True:
                raw = archive.fileobj.read(a.CHUNK)
                if not raw:
                    break
                trailing += len(raw)
                _need(not any(raw), 'ARCHIVE_TRAILING_DATA')
            _need(trailing >= 512, 'ARCHIVE_TRUNCATED')
    _need(found is not None, 'ARCHIVE_SELECTED_MEMBER')
    return found


def _original_zip(fd, size, selected, *, writer=None):
    _need(size >= 22, 'ARCHIVE_ZIP')
    end = os.pread(fd, 22, size - 22)
    signature, disk, central_disk, disk_count, count, central_size, central, comment = struct.unpack('<4s4H2LH', end)
    _need(signature == b'PK\x05\x06' and disk == central_disk == comment == 0
          and disk_count == count and 0 < count <= MAX_MEMBERS
          and 46 * count <= central_size <= MAX_CENTRAL and central + central_size == size - 22,
          'ARCHIVE_ZIP')
    seen, total, found, cursor = set(), 0, None, 0
    with zipfile.ZipFile(a._Reader(fd, size)) as archive:
        entries = archive.infolist()
        _need(len(entries) == count, 'ARCHIVE_ZIP')
        for info in sorted(entries, key=lambda entry: entry.header_offset):
            name = _name(info.filename, directory=info.is_dir())
            kind = stat.S_IFMT(info.external_attr >> 16)
            _need(info.orig_filename == info.filename and name not in seen
                  and kind in ((0, stat.S_IFDIR) if info.is_dir() else (0, stat.S_IFREG))
                  and not info.flag_bits & (1 | 64) and not info.volume
                  and info.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                  and not (info.external_attr >> 16) & 0o6000, 'ARCHIVE_MEMBER_TYPE')
            seen.add(name)
            total += info.file_size
            _need(total <= a.MAX_EXPANDED and 0 <= info.file_size <= a.MAX_EXPANDED
                  and info.header_offset == cursor, 'ARCHIVE_ZIP_LAYOUT')
            raw = os.pread(fd, 30, cursor)
            _need(len(raw) == 30, 'ARCHIVE_ZIP_LAYOUT')
            fields = struct.unpack('<4s5H3L2H', raw)
            sig, _, flags, method, _, _, crc, packed, expanded, name_size, extra_size = fields
            encoded = info.filename.encode('utf-8' if flags & 0x800 else 'cp437')
            _need(sig == b'PK\x03\x04' and flags == info.flag_bits and method == info.compress_type
                  and name_size == len(encoded) and os.pread(fd, name_size, cursor + 30) == encoded
                  and packed in ((0, info.compress_size) if flags & 8 else (info.compress_size,))
                  and expanded in ((0, info.file_size) if flags & 8 else (info.file_size,))
                  and crc in ((0, info.CRC) if flags & 8 else (info.CRC,)), 'ARCHIVE_ZIP_LAYOUT')
            cursor += 30 + name_size + extra_size + info.compress_size
            if flags & 8:
                raw = os.pread(fd, 16, cursor)
                signed = raw[:4] == b'PK\x07\x08'
                value = raw[4:16] if signed else raw[:12]
                _need(len(value) == 12 and struct.unpack('<LLL', value) == (
                    info.CRC, info.compress_size, info.file_size), 'ARCHIVE_ZIP_DESCRIPTOR')
                cursor += 16 if signed else 12
            _need(cursor <= central, 'ARCHIVE_ZIP_LAYOUT')
            if info.is_dir():
                _need(info.file_size == 0, 'ARCHIVE_MEMBER_SIZE')
            else:
                with archive.open(info) as content:
                    value = _stream(content, info.file_size, writer=writer if name == selected else None)
                if name == selected:
                    _need(info.file_size > 0, 'ARCHIVE_SELECTED_MEMBER')
                    found = value
        _need(cursor == central and found is not None, 'ARCHIVE_ZIP_LAYOUT')
    return found


def _binary(fd, size, meta, target, *, writer=None):
    row = _row(meta, target)
    if meta['core'] == 'xray':
        value = _original_zip(fd, size, row['member'], writer=writer)
    else:
        value = _tar(a._Reader(fd, size), row['member'], size_limit=a.MAX_EXPANDED, writer=writer)
    return value


def _license(stream, meta):
    upstream = meta['upstream']
    root = upstream['repository'].split('/')[1] + '-' + upstream['source_commit']
    value = _tar(stream, root + '/LICENSE', root=root, size_limit=a.MAX_LICENSE, retain=True)
    return value[2]


def _provenance(meta, target, license_bytes, binary_sha256):
    source, row = meta['upstream'], _row(meta, target)
    upstream = dict(repository=source['repository'], tag=source['tag'],
        archive_name=row['archive_name'], archive_size=row['archive_size'], archive_sha256=row['archive_sha256'],
        member=row['member'], source_commit=source['source_commit'], source_size=source['source_size'],
        source_sha256=source['source_sha256'],
        license_member=source['repository'].split('/')[1] + '-' + source['source_commit'] + '/LICENSE')
    return dict(schema=1, upstream=upstream, binary_sha256=binary_sha256,
                license_sha256=_sha(license_bytes), source_sha256=source['source_sha256'])


@contextmanager
def _workspace(path):
    scope = fs._Scope(os.geteuid())
    try:
        _need(isinstance(path, Path) and path.is_absolute() and '..' not in path.parts
              and path != Path('/'), 'WORKSPACE')
        fd = scope.own(os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW))
        for index, name in enumerate(path.parts[1:]):
            info = os.fstat(fd)
            _need(stat.S_ISDIR(info.st_mode) and info.st_uid in (0, scope.owner)
                  and (not info.st_mode & 0o022 or info.st_mode & stat.S_ISVTX), 'WORKSPACE')
            child = scope.own(os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd))
            scope.directories.append((fd, name, child, fs._dir_identity(os.fstat(child))))
            fd = child
        fs._directory(os.fstat(fd), scope.owner, 0o700)
        yield scope, fd
        scope.stable()
    finally:
        scope.close()


def _relative(workspace, path):
    _need(isinstance(path, Path) and '..' not in path.parts, 'WORKSPACE_PATH')
    if not path.is_absolute():
        path = workspace / path
    try:
        parts = path.relative_to(workspace).parts
    except ValueError:
        raise CoreComponentBuildError('WORKSPACE_PATH') from None
    _need(parts and all(part not in ('', '.', '..') for part in parts), 'WORKSPACE_PATH')
    return parts


def _parent(scope, root, workspace, path):
    parts = _relative(workspace, path)
    for part in parts[:-1]:
        root = scope.directory(root, part, mode=None)
    return root, parts[-1]


def _input(scope, root, workspace, path, limit):
    parent, name = _parent(scope, root, workspace, path)
    return _at(scope, parent, name, limit)


def _at(scope, parent, name, limit):
    fd = scope.own(os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent))
    info = os.fstat(fd)
    mode = stat.S_IMODE(info.st_mode)
    _need(stat.S_ISREG(info.st_mode) and info.st_uid == scope.owner and info.st_nlink == 1
          and mode in (0o400, 0o444, 0o600, 0o644) and 0 < info.st_size <= limit, 'INPUT_FILE')
    scope.files.append((parent, name, fd, a._file_identity(info)))
    scope.stable()
    return fd, info.st_size


def _expected(fd, size, expected_size, expected_sha):
    _need(size == expected_size and a._digest_fd(fd, size) == expected_sha, 'INPUT_DIGEST')


def _zip_entry(name, size):
    entry = zipfile.ZipInfo(name)
    entry.create_system = 3
    entry.external_attr = (stat.S_IFREG | (0o555 if name.startswith('bin/') else 0o644)) << 16
    entry.compress_type = zipfile.ZIP_DEFLATED
    entry.file_size = size
    return entry


def _fd_chunks(fd, size):
    offset = 0
    while offset < size:
        raw = os.pread(fd, min(a.CHUNK, size - offset), offset)
        _need(bool(raw), 'INPUT_CHANGED')
        offset += len(raw)
        yield raw


def _copy_into(writer, fd, size):
    for raw in _fd_chunks(fd, size):
        _need(writer.write(raw) == len(raw), 'WRITE_FAILED')


def _candidate(fd, size, meta, target):
    """M0 format checks without inventing a Release Authority/asset ID."""
    _need(size <= a.MAX_PACK, 'PACK_LIMIT')
    reader = a._Reader(fd, size)
    central = a._zip_metadata(reader)
    core = meta['core']
    names = {'bin/' + core, 'LICENSE', 'SOURCE.tar.gz', 'PROVENANCE.json', 'CORE-PACK.json'}
    with zipfile.ZipFile(reader) as archive:
        entries = archive.infolist()
        _need(len(entries) == len({entry.filename for entry in entries}) == 5
              and {entry.filename for entry in entries} == names
              and sum(entry.file_size for entry in entries) <= a.MAX_EXPANDED, 'PACK_MEMBERS')
        for entry in entries:
            mode = 0o555 if entry.filename == 'bin/' + core else 0o644
            _need(entry.orig_filename == entry.filename and not entry.is_dir() and entry.volume == 0
                  and entry.external_attr >> 16 == stat.S_IFREG | mode and entry.create_system == 3
                  and not entry.flag_bits & (1 | 64) and not entry.extra and not entry.comment
                  and entry.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                  and 0 <= entry.header_offset < central and 0 <= entry.compress_size <= a.MAX_PACK, 'PACK_ENTRY')
        a._local_records(reader, entries, central)
        raw = a._read_member(archive, 'CORE-PACK.json', limit=a.MAX_JSON, retain=True)
        record = a._json(raw)
        a._keys(record, a.COMMON | {'kind', 'target', 'files'})
        # JSON booleans compare equal to integers in Python. Compare the
        # canonical common bytes so schema/revision True cannot pass M0's
        # strict integer identity through a self-consistent candidate pack.
        _need(_canonical({key: record[key] for key in a.COMMON}) == _canonical(_common(meta))
              and record['kind'] == 'core-pack-v1' and record['target'] == target
              and raw == _canonical(record), 'PACK_CONTEXT')
        files = record['files']
        a._keys(files, names - {'CORE-PACK.json'})
        for name, info in files.items():
            a._keys(info, {'size', 'mode', 'sha256'})
            maximum = (a.MAX_SOURCE if name == 'SOURCE.tar.gz' else a.MAX_LICENSE if name == 'LICENSE'
                       else a.MAX_JSON if name == 'PROVENANCE.json' else a.MAX_EXPANDED)
            mode = 0o555 if name == 'bin/' + core else 0o644
            _need(a._positive(info['size'], maximum) and type(info['mode']) is int and info['mode'] == mode
                  and a._hex(info['sha256']) and archive.getinfo(name).file_size == info['size'], 'PACK_FILE')
            a._read_member(archive, name, limit=maximum, expected=info['sha256'])
        row, source = _row(meta, target), meta['upstream']
        _need((files['SOURCE.tar.gz']['size'], files['SOURCE.tar.gz']['sha256']) == (
            source['source_size'], source['source_sha256']), 'PACK_SOURCE')
        with archive.open('SOURCE.tar.gz') as content:
            license_bytes = _license(content, meta)
        _need(files['LICENSE']['size'] == len(license_bytes)
              and files['LICENSE']['sha256'] == _sha(license_bytes), 'LICENSE_SOURCE')
        provenance = a._read_member(archive, 'PROVENANCE.json', limit=a.MAX_JSON, retain=True)
        _need(provenance == _canonical(_provenance(meta, target, license_bytes, files['bin/' + core]['sha256'])), 'PACK_PROVENANCE')
        a._provenance(a._json(provenance), core, meta['version'], target, files)
    return dict(target=target, name=a._pack_name(core, meta['version'], target), size=size,
                sha256=a._digest_fd(fd, size), manifest_sha256=_sha(raw)), (files['bin/' + core]['sha256'], files['bin/' + core]['size'])


@contextmanager
def _output(scope, root, workspace, destination):
    parent, name = _parent(scope, root, workspace, destination)
    try:
        os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        pass
    else:
        raise CoreComponentBuildError('OUTPUT_EXISTS')
    stage = fs._Stage(scope, parent)
    try:
        stage.create()
        os.fsync(stage.fd)
        os.fsync(parent)
        yield stage, parent, name
        scope.stable()
        stage.bound()
        fs._rename_new(parent, stage.name, parent, name)
        _need(fs._dir_identity(os.stat(name, dir_fd=parent, follow_symlinks=False))[:2] == stage.identity,
              'OUTPUT_CHANGED')
        os.fsync(parent)
    finally:
        stage.cleanup()


@contextmanager
def _errors():
    try:
        yield
    except CoreComponentBuildError:
        raise
    except (a.CoreArtifactError, fs.CoreStoreError) as error:
        raise CoreComponentBuildError(error.code) from None
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RuntimeError,
            tarfile.TarError, zipfile.BadZipFile, zipfile.LargeZipFile, zlib.error, EOFError, OverflowError, struct.error):
        raise CoreComponentBuildError('CANDIDATE_BUILD_FAILED') from None


def build_target(workspace, metadata, *, target, archive, source, destination):
    """Produce one ZIP in a NEW output directory, preserving original bytes."""
    with _errors(), _workspace(workspace) as (scope, root):
        meta_fd, meta_size = _input(scope, root, workspace, metadata, a.MAX_JSON)
        meta = _metadata(os.pread(meta_fd, meta_size + 1, 0))
        row = _row(meta, target)
        archive_fd, archive_size = _input(scope, root, workspace, archive, a.MAX_PACK)
        source_fd, source_size = _input(scope, root, workspace, source, a.MAX_SOURCE)
        _expected(archive_fd, archive_size, row['archive_size'], row['archive_sha256'])
        _expected(source_fd, source_size, meta['upstream']['source_size'], meta['upstream']['source_sha256'])
        binary_sha256, binary_size, _ = _binary(archive_fd, archive_size, meta, target)
        license_bytes = _license(a._Reader(source_fd, source_size), meta)
        provenance = _canonical(_provenance(meta, target, license_bytes, binary_sha256))
        values = {'bin/' + meta['core']: dict(size=binary_size, sha256=binary_sha256, mode=0o555),
                  'SOURCE.tar.gz': dict(size=source_size, sha256=meta['upstream']['source_sha256'], mode=0o644),
                  'LICENSE': dict(size=len(license_bytes), sha256=_sha(license_bytes), mode=0o644),
                  'PROVENANCE.json': dict(size=len(provenance), sha256=_sha(provenance), mode=0o644)}
        record = _canonical(dict(_common(meta), kind='core-pack-v1', target=target, files=values))
        _need(len(record) <= a.MAX_JSON and len(provenance) <= a.MAX_JSON
              and sum(item['size'] for item in values.values()) + len(record) <= a.MAX_EXPANDED, 'PACK_LIMIT')
        with _output(scope, root, workspace, destination) as (stage, _, _):
            name = a._pack_name(meta['core'], meta['version'], target)
            fd = stage.new_file(stage.fd, name)
            with os.fdopen(fd, 'wb', closefd=False) as output:
                with zipfile.ZipFile(output, 'w', allowZip64=False) as packed:
                    with packed.open(_zip_entry('bin/' + meta['core'], binary_size), 'w') as binary:
                        copied = _binary(archive_fd, archive_size, meta, target, writer=binary)
                        _need(copied[:2] == (binary_sha256, binary_size), 'BINARY_CHANGED')
                    with packed.open(_zip_entry('SOURCE.tar.gz', source_size), 'w') as original:
                        _copy_into(original, source_fd, source_size)
                    for label, raw in (('LICENSE', license_bytes), ('PROVENANCE.json', provenance), ('CORE-PACK.json', record)):
                        packed.writestr(_zip_entry(label, len(raw)), raw)
            os.fchmod(fd, 0o644)
            os.fsync(fd)
            checked_fd, checked_size = _at(scope, stage.fd, name, a.MAX_PACK)
            result, binary_identity = _candidate(checked_fd, checked_size, meta, target)
            _need(binary_identity == (binary_sha256, binary_size), 'BINARY_CHANGED')
            scope.stable()
        return dict(scope='candidate-format-only', published=False, qualified=False,
                    binary_sha256=binary_sha256, binary_size=binary_size, **result)


def assemble(workspace, metadata, *, packs, destination):
    """Validate four complete candidate packs and publish a NEW six-file set.

    This rechecks M0 bytes/context/source/license closure, not the historical
    original-archive extraction. Actions must bind build_target's derived binary
    receipt to its immutable producer output; pack self-hashes are not origin.
    """
    with _errors(), _workspace(workspace) as (scope, root):
        meta_fd, meta_size = _input(scope, root, workspace, metadata, a.MAX_JSON)
        meta = _metadata(os.pread(meta_fd, meta_size + 1, 0))
        _keys(packs, a.TARGETS)
        checked, inputs, shared = [], {}, {}
        for target in a.TARGETS:
            fd, size = _input(scope, root, workspace, packs[target], a.MAX_PACK)
            row, binary_identity = _candidate(fd, size, meta, target)
            origin = _row(meta, target)
            key = (origin['archive_name'], origin['archive_sha256'], origin['member'])
            _need(key not in shared or shared[key] == binary_identity, 'PACK_ORIGIN_DRIFT')
            shared[key] = binary_identity
            checked.append(row)
            inputs[target] = fd, size
        release = _canonical(dict(_common(meta), kind='core-release-v1', packs=checked))
        _need(len(release) <= a.MAX_JSON, 'METADATA_LIMIT')
        values = {row['name']: row['sha256'] for row in checked}
        values['CORE-RELEASE.json'] = _sha(release)
        table = ''.join(values[name] + '  ' + name + '\n' for name in sorted(values)).encode('ascii')
        with _output(scope, root, workspace, destination) as (stage, _, _):
            for row in checked:
                fd, size = inputs[row['target']]
                stage.write(stage.fd, row['name'], _fd_chunks(fd, size), mode=0o644, size=size, checksum=row['sha256'])
            stage.small('CORE-RELEASE.json', release)
            stage.small('SHA256SUMS', table)
            expected = dict(values, SHA256SUMS=_sha(table))
            for name, digest in expected.items():
                fd, size = _at(scope, stage.fd, name, a.MAX_PACK)
                _need(a._digest_fd(fd, size) == digest, 'OUTPUT_CHANGED')
            _need(set(os.listdir(stage.fd)) == set(expected), 'OUTPUT_CHANGED')
            scope.stable()
        return dict(scope='candidate-format-only', published=False, qualified=False,
                    manifest_sha256=_sha(release), checksums_sha256=_sha(table), packs=checked)


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise CoreComponentBuildError('CLI_ARGUMENTS')


def main(argv=None):
    parser = _Parser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    target = commands.add_parser('build-target')
    aggregate = commands.add_parser('assemble')
    for command in (target, aggregate):
        command.add_argument('--workspace', type=Path, required=True)
        command.add_argument('--metadata', type=Path, required=True)
        command.add_argument('--output', type=Path, required=True, help='New directory; never overwritten')
    target.add_argument('--target', choices=a.TARGETS, required=True)
    target.add_argument('--archive', type=Path, required=True)
    target.add_argument('--source', type=Path, required=True)
    aggregate.add_argument('--pack', action='append', required=True, help='Exact target=workspace-relative-path')
    try:
        args = parser.parse_args(argv)
        if args.command == 'build-target':
            result = build_target(args.workspace, args.metadata, target=args.target,
                archive=args.archive, source=args.source, destination=args.output)
        else:
            packs = {}
            for value in args.pack:
                fields = value.split('=', 1)
                _need(len(fields) == 2 and fields[0] in a.TARGETS and fields[0] not in packs, 'INPUT_TARGETS')
                packs[fields[0]] = Path(fields[1])
            result = assemble(args.workspace, args.metadata, packs=packs, destination=args.output)
        print(json.dumps(result, sort_keys=True))
        return 0
    except CoreComponentBuildError as error:
        print('Candidate component rejected: ' + error.code, file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
