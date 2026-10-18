#!/usr/bin/env python3
"""Preserve only named owner build/runtime UTF-8 evidence, without qualifying it.

The archive is a directory, never a source tree or a binary bundle. Verification
is self-contained: it neither opens recorded binary paths nor executes a core,
Go, git, a network request, or the old five-patch runtime input validator.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat

import harness as h

owner_prepare = h.load('owner_runtime_archive_build_allowlist', h.HERE / 'prepare_evidence.py')
boundary = h.load('owner_runtime_archive_packet_allowlist', h.HERE.parent / 'xudp_packet/packet_boundary.py')
SCHEMA = 1
KIND = h.KIND
ARCHIVE_KIND = KIND + '-text-evidence-archive'
RUNTIME_KIND = KIND + '-runtime'
ARMS = {'off': ('C', 'D', 'A', 'B', 'U', 'V'), 'on': ('C', 'D')}
MANIFEST = 'MANIFEST.json'
SENSITIVE_KEYS = frozenset({'password', 'passwd', 'passphrase', 'secret', 'clientsecret',
    'token', 'accesstoken', 'refreshtoken', 'apikey', 'privatekey', 'authorization',
    'credential', 'credentials', 'uuid', 'certificate', 'certificatepath', 'keypath'})
SECRET_ASSIGNMENT = re.compile(
    r'''(?ix)(?:["']?\b(?:password|passwd|passphrase|client[_-]?secret|secret|access[_-]?token|refresh[_-]?token|api[_-]?key|private[_-]?key|authorization|credentials?)["']?\s*[:=]\s*[^\s,}]+|\bBearer\s+[A-Za-z0-9._~+/-]+=*)''')
OWNER_MARKER = 'socks_udp_owner '



def need(condition, message):
    if not condition:
        raise ValueError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(data):
    return (json.dumps(data, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8')


def member_name(name):
    need(isinstance(name, str) and name and '\\' not in name and '\x00' not in name,
         'Invalid evidence member path')
    path = PurePosixPath(name)
    need(not path.is_absolute() and '..' not in path.parts and str(path) == name and name != '.',
         'Noncanonical or traversing evidence member: ' + name)
    return name


def allowed_files(contract=None):
    """Reuse, rather than widen or duplicate, both authoritative build lists."""
    if contract is None:
        contract = h.read_contract()
    owner = owner_prepare.owner_files(contract)
    old = h.packet_prepare.allowed_build()
    owner_jsonl = {g['label'] + '-' + mode + '.stdout'
                   for g in contract['groups'] for mode in h.MODES}
    files = {}

    def add(name, jsonl=False):
        member_name(name)
        need(name not in files, 'Duplicate allowlist path: ' + name)
        files[name] = 'jsonl' if jsonl else ('json' if name.endswith('.json') else 'text')

    for name in sorted(owner):
        add(name, name in owner_jsonl)
    for prefix in ('base', 'regression'):
        for name in sorted(old):
            add(prefix + '/' + name, name in h.packet_prepare.GO_JSONL)
    for name in ('off-input.json', 'on-input.json', 'RUNTIME_RESULT.json'):
        add('runtime/' + name)
    for arm, stages in ARMS.items():
        add('runtime/' + arm + '/stages.json')
        for stage in stages:
            for name in sorted(boundary.expected_artifacts(boundary.plan(stage)) | {'manifest.json'}):
                add('runtime/' + arm + '/' + stage + '/' + name)
    need(MANIFEST not in files, 'Archive manifest collides with evidence')
    return files


def allowed_directories(files):
    return {str(parent) for name in files for parent in PurePosixPath(name).parents if str(parent) != '.'}


def allowlist_sha256(files):
    return digest(canonical({'schema_version': SCHEMA, 'kind': ARCHIVE_KIND,
                             'files': [{'path': name, 'format': files[name]} for name in sorted(files)],
                             'directories': sorted(allowed_directories(files))}))


def strict_json(text):
    # The shared parser rejects duplicate keys and NUL, including escaped NUL.
    value = h.packet_prepare.strict_json(text)

    def finite(item):
        if isinstance(item, float):
            need(math.isfinite(item), 'Nonfinite JSON number in evidence')
        elif isinstance(item, dict):
            for key, child in item.items():
                finite(key)
                finite(child)
        elif isinstance(item, list):
            for child in item:
                finite(child)
    finite(value)
    return value


def check_owner_logs(text):
    for line in text.splitlines():
        if OWNER_MARKER not in line:
            continue
        need(line.count(OWNER_MARKER) == 1, 'Ambiguous owner event log')
        event = strict_json(line.split(OWNER_MARKER, 1)[1])
        need(isinstance(event, dict), 'Nonobject owner event log')
        check_clean(event)


def check_clean(value):
    """Apply the established sanitizer to values, never JSON punctuation."""
    need(h.c.life.scrub(value) == value, 'Unsanitized structured evidence')
    if isinstance(value, str):
        need('-----BEGIN ' not in value and 'PRIVATE KEY-----' not in value and
             'BEGIN CERTIFICATE' not in value, 'PEM/key material in evidence')
        need(SECRET_ASSIGNMENT.search(value) is None, 'Secret-bearing text in evidence')
        check_owner_logs(value)
    elif isinstance(value, list):
        for item in value:
            check_clean(item)
    elif isinstance(value, dict):
        for key, item in value.items():
            normalized = re.sub('[^a-z0-9]', '', key.lower())
            need(normalized not in SENSITIVE_KEYS and not normalized.endswith(('password', 'privatekey', 'accesstoken', 'refreshtoken', 'clientsecret')),
                 'Sensitive field in evidence: ' + key)
            check_clean(key)
            check_clean(item)


def input_shape(value, arm):
    """Archive metadata shape only; eight-patch provenance is assessed elsewhere.

    Paths are retained as inert strings. In particular this does not invoke
    packet_boundary.validate_manifest, which expects the old five-patch input.
    """
    need(isinstance(value, dict) and set(value) == {
        'schema_version', 'kind', 'source_identity', 'official', 'candidate', 'arm', 'build_evidence'},
        'Wrong owner runtime input fields')
    need(type(value['schema_version']) is int and value['schema_version'] == SCHEMA and
         value['kind'] == KIND and value['arm'] == arm, 'Wrong owner runtime input schema/kind/arm')
    need(isinstance(value['source_identity'], dict) and isinstance(value['build_evidence'], dict),
         'Missing owner runtime source/build evidence metadata')
    for label, fields in (('official', {'path', 'sha256', 'version'}),
                          ('candidate', {'path', 'sha256', 'version', 'provenance_path', 'provenance_sha256'})):
        row = value[label]
        need(isinstance(row, dict) and set(row) == fields and
             all(isinstance(row[key], str) and row[key] for key in fields),
             'Wrong owner runtime binary descriptor: ' + label)
        for key in fields:
            if key.endswith('sha256'):
                need(re.fullmatch('[0-9a-f]{64}', row[key]) is not None, 'Invalid recorded digest')
    build = value['build_evidence']
    need(set(build) == {'path', 'files'} and isinstance(build['path'], str) and build['path'] and
         isinstance(build['files'], dict), 'Wrong owner runtime build evidence descriptor')


def validate_bytes(name, data, format):
    text = data.decode('utf-8', errors='strict')
    need('\x00' not in text and '-----BEGIN ' not in text and 'PRIVATE KEY-----' not in text and
         'BEGIN CERTIFICATE' not in text, 'Binary or PEM/key material in evidence: ' + name)
    if format == 'json':
        value = strict_json(text)
        check_clean(value)
        if name in ('runtime/off-input.json', 'runtime/on-input.json'):
            input_shape(value, name.split('/')[1].split('-')[0])
        elif name == 'runtime/RUNTIME_RESULT.json':
            need(isinstance(value, dict) and type(value.get('schema_version')) is int and
                 value['schema_version'] == SCHEMA and value.get('kind') == RUNTIME_KIND and
                 value.get('product_qualified') is False and value.get('continuity_claim') is False,
                 'Wrong owner runtime result scope')
    elif format == 'jsonl':
        for line in text.splitlines():
            if line.strip():
                value = strict_json(line)
                need(isinstance(value, dict), 'Nonobject Go test JSONL: ' + name)
                check_clean(value)
    else:
        need(format == 'text' and h.c.life.clean(text) == text, 'Unsanitized text evidence: ' + name)
        need(SECRET_ASSIGNMENT.search(text) is None, 'Secret-bearing text in evidence: ' + name)
        check_owner_logs(text)
    return data


def absolute_path(path):
    path = Path(path)
    need('..' not in path.parts, 'Traversing evidence root')
    return Path(os.path.abspath(path))


def open_directory(path, missing_ok=False):
    """Open each component without following root or ancestor symlinks."""
    need(hasattr(os, 'O_NOFOLLOW') and hasattr(os, 'O_DIRECTORY'), 'No safe no-follow directory support')
    path = absolute_path(path)
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[1:]:
            try:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            except FileNotFoundError:
                if missing_ok:
                    os.close(fd)
                    return None
                raise
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def collect(root, files, *, missing_ok=False, inodes=None):
    """Read a fixed-name tree using no-follow file descriptors, not rglob."""
    inodes = set() if inodes is None else inodes
    directories = allowed_directories(files)
    root_fd = open_directory(root, missing_ok)
    if root_fd is None:
        return {}
    found = {}
    seen_dirs = set()

    def visit(fd, prefix=''):
        info = os.fstat(fd)
        identity = (info.st_dev, info.st_ino)
        need(identity not in seen_dirs, 'Duplicate evidence directory')
        seen_dirs.add(identity)
        for name in sorted(os.listdir(fd)):
            relative = member_name(prefix + name)
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            need(not stat.S_ISLNK(info.st_mode), 'Linked evidence member: ' + relative)
            if stat.S_ISDIR(info.st_mode):
                need(relative in directories, 'Unexpected evidence directory: ' + relative)
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                try:
                    now = os.fstat(child)
                    need((now.st_dev, now.st_ino) == (info.st_dev, info.st_ino), 'Changed evidence directory')
                    visit(child, relative + '/')
                finally:
                    os.close(child)
                continue
            need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, 'Nonregular or hardlinked evidence: ' + relative)
            need(relative in files and relative not in found, 'Unexpected/duplicate evidence path: ' + relative)
            child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            try:
                before = os.fstat(child)
                identity = (before.st_dev, before.st_ino)
                need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and
                     identity == (info.st_dev, info.st_ino) and identity not in inodes,
                     'Changed, aliased or nonregular evidence: ' + relative)
                inodes.add(identity)
                with os.fdopen(child, 'rb', closefd=False) as stream:
                    data = stream.read()
                after = os.fstat(child)
                need((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns, before.st_nlink) ==
                     (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns, after.st_nlink) and
                     len(data) == before.st_size, 'Evidence changed while reading: ' + relative)
                found[relative] = data
            finally:
                os.close(child)
    try:
        visit(root_fd)
    finally:
        os.close(root_fd)
    return found


def manifest_for(members, files):
    entries = [{'path': name, 'sha256': digest(data), 'bytes': len(data), 'format': files[name]}
               for name, data in sorted(members.items())]
    return {'schema_version': SCHEMA, 'kind': ARCHIVE_KIND, 'files': entries, 'count': len(entries),
            'allowlist_sha256': allowlist_sha256(files), 'packaged_only': True,
            'runtime_verdict': 'not_assessed', 'product_qualified': False}


def verify_archive(archive, contract=None):
    """Independently recompute every file, format, count, hash and scope flag."""
    files = allowed_files(contract)
    members = collect(archive, {**files, MANIFEST: 'json'})
    need(MANIFEST in members, 'Missing archive manifest')
    raw_manifest = members.pop(MANIFEST)
    validate_bytes(MANIFEST, raw_manifest, 'json')
    manifest = strict_json(raw_manifest.decode('utf-8'))
    need(isinstance(manifest, dict) and type(manifest.get('schema_version')) is int and
         type(manifest.get('count')) is int and manifest.get('packaged_only') is True and
         manifest.get('product_qualified') is False and isinstance(manifest.get('files'), list),
         'Invalid archive manifest types/scope')
    paths = []
    for row in manifest['files']:
        need(isinstance(row, dict) and set(row) == {'path', 'sha256', 'bytes', 'format'} and
             type(row.get('bytes')) is int and row['bytes'] >= 0, 'Invalid archive file entry')
        paths.append(member_name(row['path']))
    need(paths == sorted(paths) and len(paths) == len(set(paths)), 'Unsorted/duplicate archive paths')
    need(members, 'Empty evidence archive')
    for name, data in members.items():
        validate_bytes(name, data, files[name])
    need(manifest == manifest_for(members, files), 'Archive manifest/files/allowlist mismatch')
    return manifest


def prepare(build, output, contract=None, runtime=None):
    """Preserve partial/failed evidence verbatim, requiring at least one file."""
    files = allowed_files(contract)
    output = absolute_path(output)
    roots = [absolute_path(root) for root in (build, runtime) if root is not None]
    for root in roots:
        need(root != output and root not in output.parents and output not in root.parents,
             'Archive and evidence roots overlap')
    need(len(roots) == len(set(roots)), 'Duplicate evidence roots')
    inodes, members = set(), {}
    if build is not None:
        build_files = {name: format for name, format in files.items() if not name.startswith('runtime/')}
        members.update(collect(build, build_files, missing_ok=True, inodes=inodes))
    if runtime is not None:
        runtime_files = {name.removeprefix('runtime/'): format for name, format in files.items()
                         if name.startswith('runtime/')}
        members.update({'runtime/' + name: data for name, data in
                        collect(runtime, runtime_files, missing_ok=True, inodes=inodes).items()})
    need(members, 'No evidence members to preserve')
    for name, data in members.items():
        validate_bytes(name, data, files[name])
    manifest = manifest_for(members, files)
    # All source checks finish before output creation. The output must be fresh;
    # its existing parent is opened without following any symlink component.
    parent = open_directory(output.parent)
    try:
        os.mkdir(output.name, mode=0o700, dir_fd=parent)
        out = os.open(output.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    finally:
        os.close(parent)
    try:
        for name, data in sorted({**members, MANIFEST: canonical(manifest)}.items()):
            fd = os.dup(out)
            try:
                parts = PurePosixPath(name).parts
                for part in parts[:-1]:
                    try:
                        os.mkdir(part, mode=0o700, dir_fd=fd)
                    except FileExistsError:
                        pass
                    child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    os.close(fd)
                    fd = child
                child = os.open(parts[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
                with os.fdopen(child, 'wb') as stream:
                    stream.write(data)
            finally:
                os.close(fd)
    finally:
        os.close(out)
    return verify_archive(output, contract)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', type=Path, help='Owner build evidence directory, never the source/build root')
    parser.add_argument('--runtime', type=Path, help='Finite off/on runtime evidence root')
    parser.add_argument('--output', type=Path, help='Fresh directory with an existing parent')
    parser.add_argument('--verify', type=Path, help='Independently verify a previously prepared archive')
    args = parser.parse_args(argv)
    if args.verify is not None:
        need(args.build is None and args.runtime is None and args.output is None, 'Do not mix verify and prepare')
        report = verify_archive(args.verify)
    else:
        need(args.output is not None and (args.build is not None or args.runtime is not None),
             'Preparation requires output and at least one evidence root')
        report = prepare(args.build, args.output, runtime=args.runtime)
    print(json.dumps({key: report[key] for key in ('count', 'packaged_only', 'runtime_verdict', 'product_qualified')},
                     sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
