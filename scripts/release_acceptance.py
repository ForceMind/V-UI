"""Offline release-kit identities; file validation never claims installation passed.

Consumer verification requires an independently obtained SHA256SUMS file digest.
Producer inspection computes that digest but cannot issue a consumer receipt.
Nothing in this module executes, extracts, rebuilds or changes kit content.
Archive metadata and bound control bytes are checked here; runtime payload and
actual installation/job/step outcomes remain their existing independent gates.
After publication starts, failure preserves public output paths as uncertain:
do not use them or retry over them. Automatic cleanup only touches a newly
created private staging namespace, never a final bundle or sidecar pathname.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import struct
import sys
import uuid
import zipfile

from scripts.build_release_kit import SOURCE_ASSETS

REPOSITORY = 'ForceMind/V-UI'
TARGET = 'x86_64-gnu'
PLATFORM = 'linux-multi-cpython312'
BUNDLE = 'vui-linux-' + TARGET + '.zip'
SOURCE_MEMBERS = {name: source for source, name in (*SOURCE_ASSETS, ('docs/RELEASE_NOTES.md', 'RELEASE_NOTES.md'))}
KIT_FILES = frozenset(SOURCE_MEMBERS) | {'vui-source.zip', 'RELEASE.json', BUNDLE, 'SHA256SUMS'}
BASELINES = {
    '0.4.14': 'b0cc174d04d05e61277075412586f0ba0dd231db',
    '0.4.15': '65b1e75d90de733408101ec280a92be935731baa',
    '0.4.16': '18fe631bcf69b68425b454db1795a101e0378842',
    '0.4.17': '95c201c6e26ed54960f964c675e515890f2eea2a',
    '0.4.19': 'a2b9c188658f082b189e77d237787a96eb9cf921',
    '0.4.20': '05173798de1baed5d67a866c4125e72f74fe3f4c',
    '0.4.21': '2aa5e11e3f09c03d048018f033ae888e7c775604',
}
ROLES = frozenset({'build', 'fresh'} | {'upgrade-' + version for version in BASELINES})
PRODUCER_SCOPE = 'producer-inspection'
CONSUMER_SCOPE = 'consumer-files'
IDENTITY_KEYS = frozenset({'schema', 'repository', 'source_sha', 'version', 'target', 'release_id',
                           'checksums_sha256', 'bundle_sha256', 'validation_scope'})
RECEIPT_KEYS = (IDENTITY_KEYS - {'release_id'}) | {
    'run_id', 'run_attempt', 'role', 'producer_artifact_id', 'producer_artifact_digest'}
MAX_KIT = 900_000_000
MAX_BUNDLE = 850_000_000
MAX_SOURCE = 64_000_000
MAX_CODE = 8_000_000
MAX_MANIFEST = 4_000_000
MAX_JSON = 16_384
MAX_TABLE = 64_000
MAX_EXPANDED = 1_100_000_000
MAX_ZIP_ENTRIES = 10_000
MAX_ZIP_METADATA = 16_000_000
CHUNK = 1024 * 1024


class ReleaseAcceptanceError(ValueError):
    """A fixed diagnostic code; untrusted material is never included."""


def _need(value, code):
    if not value:
        raise ReleaseAcceptanceError(code)


def _hex(value, length):
    return type(value) is str and re.fullmatch('[a-f0-9]{' + str(length) + '}', value) is not None


def _version(value):
    _need(type(value) is str and len(value) <= 64 and
          re.fullmatch(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)', value), 'STABLE_VERSION_REQUIRED')
    return tuple(map(int, value.split('.')))


def _source_version(source, version):
    _need(_hex(source, 40), 'EXACT_SOURCE_REQUIRED')
    _version(version)


def _pairs(values):
    result = {}
    for key, value in values:
        _need(key not in result, 'DUPLICATE_JSON_KEY')
        result[key] = value
    return result


def _constant(_):
    raise ReleaseAcceptanceError('NONFINITE_JSON')


def _float(value):
    number = float(value)
    _need(math.isfinite(number), 'NONFINITE_JSON')
    return number


def _unicode(value):
    if isinstance(value, str):
        value.encode('utf-8')
    elif isinstance(value, dict):
        for key, child in value.items():
            _unicode(key); _unicode(child)
    elif isinstance(value, list):
        for child in value:
            _unicode(child)


def _json(raw, limit):
    _need(isinstance(raw, (bytes, str)), 'JSON_BYTES_REQUIRED')
    try:
        raw = raw.encode('utf-8') if isinstance(raw, str) else raw
        _need(len(raw) <= limit, 'JSON_LIMIT')
        result = json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs, parse_constant=_constant, parse_float=_float)
        _unicode(result)
    except (UnicodeError, ValueError, RecursionError) as error:
        if isinstance(error, ReleaseAcceptanceError):
            raise
        raise ReleaseAcceptanceError('INVALID_JSON') from error
    _need(type(result) is dict, 'JSON_OBJECT_REQUIRED')
    return result


def _absolute(path):
    return Path(os.path.abspath(os.fspath(path)))


@contextmanager
def _directory(path, *, forbidden=None):
    """Walk with directory FDs so no symlink component is traversed."""
    path = _absolute(path)
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for component in path.parts[1:]:
            next_fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
            info = os.fstat(fd)
            _need((info.st_dev, info.st_ino) != forbidden, 'OUTPUT_INSIDE_KIT')
        yield fd
    finally:
        os.close(fd)


def _file_identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_nlink, info.st_mtime_ns, info.st_ctime_ns)


def _member(directory, name, limit, stack):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    stack.callback(os.close, fd)
    info = os.fstat(fd)
    _need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and 0 <= info.st_size <= limit, 'UNSAFE_KIT_FILE')
    return fd, _file_identity(info)


def _digest(fd, limit):
    os.lseek(fd, 0, os.SEEK_SET)
    total = 0
    hasher = hashlib.sha256()
    while True:
        chunk = os.read(fd, CHUNK)
        if not chunk:
            return hasher.hexdigest()
        total += len(chunk)
        _need(total <= limit, 'FILE_LIMIT')
        hasher.update(chunk)


def _read(fd, limit):
    os.lseek(fd, 0, os.SEEK_SET)
    with os.fdopen(os.dup(fd), 'rb') as stream:
        raw = stream.read(limit + 1)
    _need(len(raw) <= limit, 'FILE_LIMIT')
    return raw


def _unchanged(directory, members):
    _need(set(os.listdir(directory)) == KIT_FILES, 'KIT_MEMBER_SET_CHANGED')
    for name, (fd, expected) in members.items():
        current = os.stat(name, dir_fd=directory, follow_symlinks=False)
        _need(_file_identity(os.fstat(fd)) == expected and _file_identity(current) == expected
              and stat.S_ISREG(current.st_mode), 'KIT_CHANGED_DURING_VERIFICATION')


def _table(raw):
    result = {}
    try:
        lines = raw.decode('ascii').splitlines()
    except UnicodeError as error:
        raise ReleaseAcceptanceError('INVALID_CHECKSUM_TABLE') from error
    for line in lines:
        match = re.fullmatch(r'([a-f0-9]{64})  ([A-Za-z0-9._-]+)', line)
        _need(match is not None, 'INVALID_CHECKSUM_TABLE')
        checksum, name = match.groups()
        _need(name not in result, 'DUPLICATE_CHECKSUM')
        result[name] = checksum
    _need(set(result) == KIT_FILES - {'SHA256SUMS'}, 'CHECKSUM_MEMBER_SET')
    return result


def _zip_bound(stream):
    """Bound the central-directory allocation before constructing ZipFile."""
    stream.seek(0, os.SEEK_END)
    size = stream.tell()
    stream.seek(max(0, size - 65557))
    tail = stream.read(65557)
    position = tail.rfind(b'PK\x05\x06')
    _need(position >= 0 and len(tail) - position >= 22, 'ZIP_END_RECORD')
    fields = struct.unpack_from('<4s4H2LH', tail, position)
    _, disk, central_disk, disk_count, count, central_size, central_offset, comment = fields
    absolute = size - len(tail) + position
    _need(disk == central_disk == 0 and disk_count == count and 0 < count <= MAX_ZIP_ENTRIES
          and 46 * count <= central_size <= MAX_ZIP_METADATA
          and central_offset + central_size == absolute
          and absolute + 22 + comment == size, 'ZIP_METADATA_LIMIT_OR_LAYOUT')
    stream.seek(0)
    return count


def _safe_zip_name(name, *, directory):
    _need(type(name) is str and 0 < len(name) <= 1024 and '\x00' not in name
          and '\\' not in name and ':' not in name and not name.startswith('/'), 'UNSAFE_ZIP_PATH')
    value = name[:-1] if directory else name
    _need(value and all(part not in ('', '.', '..') for part in value.split('/')), 'UNSAFE_ZIP_PATH')
    return value


@contextmanager
def _archive(fd, *, directories):
    with os.fdopen(os.dup(fd), 'rb') as stream:
        count = _zip_bound(stream)
        with zipfile.ZipFile(stream) as archive:
            entries = archive.infolist()
            _need(len(entries) == count and sum(entry.file_size for entry in entries) <= MAX_EXPANDED,
                  'ZIP_ENTRY_OR_EXPANDED_LIMIT')
            names, files = set(), set()
            for entry in entries:
                is_dir = entry.is_dir()
                name = _safe_zip_name(entry.filename, directory=is_dir)
                _need(entry.orig_filename == entry.filename and name not in names, 'DUPLICATE_OR_TRUNCATED_ZIP_PATH')
                names.add(name)
                kind = stat.S_IFMT(entry.external_attr >> 16)
                _need(not entry.flag_bits & (1 | 64) and entry.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED),
                      'ENCRYPTED_OR_UNSUPPORTED_ZIP')
                if is_dir:
                    _need(directories and kind in (0, stat.S_IFDIR) and entry.file_size == 0, 'UNSAFE_ZIP_DIRECTORY')
                else:
                    _need(kind in (0, stat.S_IFREG), 'NONREGULAR_ZIP_ENTRY')
                    files.add(name)
            for name in names:
                parts = name.split('/')
                _need(not any('/'.join(parts[:index]) in files for index in range(1, len(parts))), 'ZIP_PARENT_IS_FILE')
            yield archive


def _zip_read(archive, name, limit):
    entry = archive.getinfo(name)
    _need(not entry.is_dir() and entry.file_size <= limit, 'ZIP_MATERIAL_LIMIT')
    with archive.open(entry) as stream:
        raw = stream.read(limit + 1)
    _need(len(raw) == entry.file_size and len(raw) <= limit, 'ZIP_MATERIAL_SIZE')
    return raw


def _metadata(record, source, version, *, schema):
    _need(record.get('source_commit') == source and record.get('version') == version
          and record.get('platform') == PLATFORM and record.get('targets') == [TARGET]
          and record.get('release_id') == version + '-' + source[:12], 'RELEASE_METADATA_MISMATCH')
    if schema:
        _need(type(record.get('schema')) is int and record['schema'] == 1, 'RELEASE_SCHEMA')


def _source_binding(source_fd, members, source):
    with _archive(source_fd, directories=True) as archive:
        _need(archive.comment == source.encode('ascii'), 'SOURCE_ARCHIVE_COMMIT')
        for target, member in SOURCE_MEMBERS.items():
            content = _zip_read(archive, member, MAX_CODE)
            _need(content == _read(members[target][0], MAX_CODE), 'SOURCE_CONTROL_BYTES_MISMATCH')


def _cleanup_stage(parent, namespace, directory, stages):
    """Best-effort every cleanup action within our exclusive 0700 namespace.

    This namespace is private to this operation. Compromise by another process
    with the same UID is a compromised-host boundary. No public output path is
    deleted, even if a preceding stat appeared to show an inode we created.
    """
    errors = []
    for name, fd, _ in stages:
        try:
            os.unlink(name, dir_fd=directory)
        except BaseException as error:
            errors.append(error)
        try:
            os.close(fd)
        except BaseException as error:
            errors.append(error)
    if directory is not None:
        try:
            os.close(directory)
        except BaseException as error:
            errors.append(error)
    try:
        os.rmdir(namespace, dir_fd=parent)
    except BaseException as error:
        errors.append(error)
    return errors


def _copy_bundle(source_fd, output, expected_sha, kit_identity):
    output = _absolute(output)
    _need(re.fullmatch(r'[A-Za-z0-9._-]+', output.name) and output.name not in ('.', '..'), 'OUTPUT_BASENAME')
    with _directory(output.parent, forbidden=kit_identity) as parent:
        info = os.fstat(parent)
        _need(info.st_uid == os.geteuid() and not info.st_mode & 0o022, 'OUTPUT_DIRECTORY_NOT_PRIVATE_WRITABLE')
        names = (output.name, output.name + '.sha256')
        for name in names:
            try:
                os.stat(name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                continue
            raise ReleaseAcceptanceError('OUTPUT_ALREADY_EXISTS')
        namespace = '.release-acceptance-' + uuid.uuid4().hex
        os.mkdir(namespace, mode=0o700, dir_fd=parent)
        directory, stages, operation_error = None, [], None
        publication_started = False
        try:
            directory = os.open(namespace, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            info = os.fstat(directory)
            _need(info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o700, 'PRIVATE_STAGE_DIRECTORY')
            for index, temp in enumerate(('bundle', 'checksums')):
                fd = os.open(temp, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
                # Register the descriptor before any further operation can fail.
                stages.append((temp, fd, None))
                info = os.fstat(fd)
                stages[-1] = (temp, fd, (info.st_dev, info.st_ino))
                with os.fdopen(os.dup(fd), 'wb') as stream:
                    if index == 0:
                        source_size = os.fstat(source_fd).st_size
                        _need(source_size <= MAX_BUNDLE, 'COPY_SOURCE_LIMIT')
                        copied = 0
                        os.lseek(source_fd, 0, os.SEEK_SET)
                        while True:
                            data = os.read(source_fd, CHUNK)
                            if not data:
                                break
                            copied += len(data)
                            _need(copied <= source_size, 'COPY_SOURCE_CHANGED')
                            stream.write(data)
                        _need(copied == source_size, 'COPY_SOURCE_CHANGED')
                    else:
                        stream.write((expected_sha + '  ' + output.name + '\n').encode('ascii'))
                    stream.flush()
                    os.fsync(stream.fileno())
                if index == 0:
                    _need(_digest(fd, MAX_BUNDLE) == expected_sha, 'COPIED_BUNDLE_DIGEST_MISMATCH')
            for name, (temp, fd, identity) in zip(names, stages):
                current = os.stat(temp, dir_fd=directory, follow_symlinks=False)
                _need((current.st_dev, current.st_ino) == identity, 'OUTPUT_STAGE_CHANGED')
                publication_started = True
                os.link(temp, name, src_dir_fd=directory, dst_dir_fd=parent, follow_symlinks=False)
                current = os.stat(name, dir_fd=parent, follow_symlinks=False)
                _need((current.st_dev, current.st_ino) == identity and stat.S_ISREG(current.st_mode), 'OUTPUT_CHANGED')
            _need(_digest(stages[0][1], MAX_BUNDLE) == expected_sha, 'COPIED_BUNDLE_DIGEST_MISMATCH')
            os.fsync(parent)
        except BaseException as error:
            operation_error = error
        cleanup_errors = _cleanup_stage(parent, namespace, directory, stages)
        if cleanup_errors:
            failure = ReleaseAcceptanceError('PRIVATE_STAGE_CLEANUP_FAILED_OUTPUTS_UNCERTAIN')
            failure.cleanup_errors = tuple(cleanup_errors)
            raise failure from (operation_error if operation_error is not None else cleanup_errors[0])
        if operation_error is not None:
            if publication_started:
                raise ReleaseAcceptanceError('OUTPUT_PUBLICATION_UNCERTAIN_NO_SUCCESS') from operation_error
            raise operation_error


def _verify(kit, source, version, expected_table, scope, output):
    _source_version(source, version)
    try:
        with _directory(kit) as directory, ExitStack() as stack:
            _need(set(os.listdir(directory)) == KIT_FILES, 'EXACT_KIT_MEMBERS_REQUIRED')
            limits = {BUNDLE: MAX_BUNDLE, 'vui-source.zip': MAX_SOURCE,
                      'RELEASE.json': MAX_JSON, 'SHA256SUMS': MAX_TABLE}
            members = {name: _member(directory, name, limits.get(name, MAX_CODE), stack) for name in sorted(KIT_FILES)}
            _need(sum(info[2] for _, info in members.values()) <= MAX_KIT, 'KIT_SIZE_LIMIT')
            raw = _read(members['SHA256SUMS'][0], MAX_TABLE)
            table_sha = hashlib.sha256(raw).hexdigest()
            _need(expected_table is None or table_sha == expected_table, 'TRUSTED_CHECKSUM_TABLE_MISMATCH')
            values = _table(raw)
            for name, expected in values.items():
                _need(_digest(members[name][0], limits.get(name, MAX_CODE)) == expected, 'KIT_FILE_DIGEST_MISMATCH')
            record = _json(_read(members['RELEASE.json'][0], MAX_JSON), MAX_JSON)
            _metadata(record, source, version, schema=True)
            with _archive(members[BUNDLE][0], directories=False) as archive:
                manifest = _json(_zip_read(archive, 'MANIFEST.json', MAX_MANIFEST), MAX_MANIFEST)
                _metadata(manifest, source, version, schema=False)
            _source_binding(members['vui-source.zip'][0], members, source)
            _unchanged(directory, members)
            if output is not None:
                info = os.fstat(directory)
                _copy_bundle(members[BUNDLE][0], output, values[BUNDLE], (info.st_dev, info.st_ino))
                try:
                    _unchanged(directory, members)
                except BaseException as error:
                    raise ReleaseAcceptanceError('KIT_CHANGED_AFTER_COPY_OUTPUTS_UNCERTAIN') from error
            return {'schema': 1, 'repository': REPOSITORY, 'source_sha': source, 'version': version,
                    'target': TARGET, 'release_id': record['release_id'], 'checksums_sha256': table_sha,
                    'bundle_sha256': values[BUNDLE], 'validation_scope': scope}
    except (OSError, zipfile.BadZipFile, KeyError, RuntimeError, EOFError, UnicodeError) as error:
        raise ReleaseAcceptanceError('KIT_OR_OUTPUT_UNAVAILABLE_OR_INVALID') from error


def verify_kit(kit, *, expected_source, candidate_version, expected_checksums_sha256, bundle_output=None):
    """Verify file identity against an external table digest; never run a test."""
    _need(_hex(expected_checksums_sha256, 64), 'EXTERNAL_CHECKSUM_TABLE_DIGEST_REQUIRED')
    return _verify(kit, expected_source, candidate_version, expected_checksums_sha256, CONSUMER_SCOPE, bundle_output)


def inspect_producer_kit(kit, *, expected_source, candidate_version):
    """Producer-only inspection; its calculated digest is not a consumer anchor."""
    return _verify(kit, expected_source, candidate_version, None, PRODUCER_SCOPE, None)


def _identity(value):
    _need(type(value) is dict and set(value) == IDENTITY_KEYS, 'IDENTITY_KEY_SET')
    _source_version(value['source_sha'], value['version'])
    _need(type(value['schema']) is int and value['schema'] == 1 and value['repository'] == REPOSITORY
          and value['target'] == TARGET and value['release_id'] == value['version'] + '-' + value['source_sha'][:12]
          and _hex(value['checksums_sha256'], 64) and _hex(value['bundle_sha256'], 64)
          and value['validation_scope'] in (PRODUCER_SCOPE, CONSUMER_SCOPE), 'IDENTITY_FIELDS')


def _positive(value):
    return type(value) is int and 0 < value <= 2**63 - 1


def create_receipt(identity, *, run_id, run_attempt, role, producer_artifact_id,
                   producer_artifact_digest, old_version=None, old_source_sha=None):
    """Record file consumption only. CI must separately prove jobs/steps/transport."""
    _identity(identity)
    _need(type(role) is str and role in ROLES, 'RECEIPT_ROLE')
    scope = PRODUCER_SCOPE if role == 'build' else CONSUMER_SCOPE
    _need(identity['validation_scope'] == scope, 'RECEIPT_ROLE_SCOPE')
    _need(all(_positive(value) for value in (run_id, run_attempt, producer_artifact_id)), 'RECEIPT_POSITIVE_IDS')
    _need(type(producer_artifact_digest) is str and re.fullmatch(r'sha256:[a-f0-9]{64}', producer_artifact_digest),
          'RECEIPT_ARTIFACT_DIGEST')
    receipt = {key: value for key, value in identity.items() if key != 'release_id'}
    receipt.update(run_id=run_id, run_attempt=run_attempt, role=role, producer_artifact_id=producer_artifact_id,
                   producer_artifact_digest=producer_artifact_digest)
    if role.startswith('upgrade-'):
        baseline = role[len('upgrade-'):]
        _need(_version(identity['version']) > _version(baseline), 'UPGRADE_MUST_ADVANCE_VERSION')
        _need(old_version in (None, baseline) and old_source_sha in (None, BASELINES[baseline]), 'RECEIPT_OLD_SOURCE')
        receipt.update(old_version=baseline, old_source_sha=BASELINES[baseline])
    else:
        _need(old_version is None and old_source_sha is None, 'UNEXPECTED_OLD_SOURCE')
    return receipt


def validate_receipt(receipt, *, expected_source, candidate_version, run_id, run_attempt, role,
                     producer_artifact_id, producer_artifact_digest, expected_checksums_sha256,
                     expected_bundle_sha256):
    """Strict-load receipt bytes, or accept an already strict-loaded dict.

    Do not predecode untrusted bytes with the default json.loads: duplicate keys
    would already be lost. This validates identity, never green CI or execution.
    """
    _source_version(expected_source, candidate_version)
    if isinstance(receipt, (str, bytes)):
        receipt = _json(receipt, MAX_JSON)
    _need(type(receipt) is dict, 'RECEIPT_OBJECT')
    identity = {'schema': 1, 'repository': REPOSITORY, 'source_sha': expected_source, 'version': candidate_version,
                'target': TARGET, 'release_id': candidate_version + '-' + str(expected_source)[:12],
                'checksums_sha256': expected_checksums_sha256, 'bundle_sha256': expected_bundle_sha256,
                'validation_scope': PRODUCER_SCOPE if role == 'build' else CONSUMER_SCOPE}
    expected = create_receipt(identity, run_id=run_id, run_attempt=run_attempt, role=role,
                              producer_artifact_id=producer_artifact_id, producer_artifact_digest=producer_artifact_digest)
    _need(set(receipt) == set(expected) and all(type(receipt[key]) is type(value) and receipt[key] == value
                                               for key, value in expected.items()), 'RECEIPT_IDENTITY_MISMATCH')
    return dict(receipt)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    consumer = commands.add_parser('verify-kit')
    producer = commands.add_parser('inspect-producer')
    for command in (consumer, producer):
        command.add_argument('--kit', required=True)
        command.add_argument('--source', required=True)
        command.add_argument('--version', required=True)
    consumer.add_argument('--expected-checksums-sha256', required=True)
    consumer.add_argument('--bundle-output')
    args = parser.parse_args(argv)
    try:
        if args.command == 'verify-kit':
            result = verify_kit(args.kit, expected_source=args.source, candidate_version=args.version,
                                expected_checksums_sha256=args.expected_checksums_sha256, bundle_output=args.bundle_output)
        else:
            result = inspect_producer_kit(args.kit, expected_source=args.source, candidate_version=args.version)
    except ReleaseAcceptanceError as error:
        parser.exit(1, 'Release acceptance refused: ' + str(error) + '\n')
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
