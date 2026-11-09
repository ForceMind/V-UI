#!/usr/bin/python3
"""GitHub-hosted system fixture for the installed private core coordinator.

The transport emulates one release using the same job's staged candidate.
It never contacts GitHub or grants publication/native qualification. The
installed coordinator, worker, store, service and kernel paths remain real.
"""
from contextlib import ExitStack, contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import sys
import types


FIXTURE = Path('/run/vui-core-switch-fixture')
CONTROL = Path('/usr/local/lib/v-ui')
REPOSITORY = 'ForceMind/V-UI'
REPOSITORY_ID = 1110299182
API = 'https://api.github.com/repos/' + REPOSITORY
WEB = 'https://github.com/' + REPOSITORY
RELEASE_ID = 7_000_000_000_000_000_001
TARGETS = ('x86_64-gnu', 'x86_64-musl', 'aarch64-gnu', 'aarch64-musl')
MAX_SMALL = 64_000
MAX_PACK = 100_000_000
CHUNK = 1024 * 1024


class Refused(RuntimeError):
    pass


def require(condition, code):
    if not condition:
        raise Refused(code)


def encoded(value):
    return json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(',', ':')).encode('ascii')


def decode(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, 'FIXTURE_JSON')
            result[key] = value
        return result

    def invalid(_):
        raise Refused('FIXTURE_JSON')

    value = json.loads(raw.decode('utf-8'), object_pairs_hook=unique, parse_constant=invalid)
    require(type(value) is dict, 'FIXTURE_JSON')
    return value


def identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def directory(stack, parent, name, *, private=False):
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open(name, flags, dir_fd=parent)
    stack.callback(os.close, fd)
    info = os.fstat(fd)
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022
            and (not private or stat.S_IMODE(info.st_mode) == 0o700 and info.st_gid == 0),
            'FIXTURE_DIRECTORY')
    return fd


def fixed_directory(stack, path, *, private=False):
    fd = directory(stack, None, '/')
    for index, name in enumerate(path.parts[1:], 2):
        fd = directory(stack, fd, name, private=private and index == len(path.parts))
    return fd


class File:
    def __init__(self, stack, parent, name, limit, *, fixture=False):
        self.parent, self.name = parent, name
        self.fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        stack.callback(os.close, self.fd)
        info = os.fstat(self.fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1
                and not info.st_mode & 0o022 and 0 < info.st_size <= limit
                and (not fixture or info.st_gid == 0 and stat.S_IMODE(info.st_mode) in (0o600, 0o644)),
                'FIXTURE_FILE')
        self.size = info.st_size
        self.record = identity(info)

    def stable(self):
        require(identity(os.fstat(self.fd)) == self.record
                and identity(os.stat(self.name, dir_fd=self.parent, follow_symlinks=False)) == self.record,
                'FIXTURE_FILE_CHANGED')

    def small(self):
        value = os.pread(self.fd, self.size + 1, 0)
        require(len(value) == self.size, 'FIXTURE_FILE_CHANGED')
        self.stable()
        return value

    def digest(self):
        hasher = hashlib.sha256()
        offset = 0
        while offset < self.size:
            block = os.pread(self.fd, min(CHUNK, self.size-offset), offset)
            require(bool(block), 'FIXTURE_FILE_CHANGED')
            hasher.update(block)
            offset += len(block)
        self.stable()
        return hasher.hexdigest()


def installed_modules(stack):
    root = fixed_directory(stack, CONTROL)
    receipt = decode(File(stack, root, 'ops-control.json', MAX_SMALL).small())
    require(type(receipt.get('schema')) is int and receipt['schema'] == 1
            and receipt.get('service_manager') == 'systemd'
            and receipt.get('system_python') == '/usr/bin/python3'
            and type(receipt.get('files')) is dict, 'FIXTURE_RECEIPT')
    sources = []
    for filename in ('system_ops.py', 'core_maintenance_control.py'):
        path = str(CONTROL/filename)
        raw = File(stack, root, filename, 4_000_000).small()
        require(receipt['files'].get(path) == hashlib.sha256(raw).hexdigest(), 'FIXTURE_RECEIPT')
        sources.append((path, raw))
    modules = []
    for index, (path, raw) in enumerate(sources):
        name = f'_vui_ci_installed_core_{index}'
        require(name not in sys.modules, 'FIXTURE_NAMESPACE')
        module = types.ModuleType(name)
        module.__file__ = path
        sys.modules[name] = module
        stack.callback(sys.modules.pop, name, None)
        exec(compile(raw, path, 'exec'), module.__dict__)
        modules.append(module)
    return modules


class Response:
    def __init__(self, url, payload, *, corrupt=False):
        self.url, self.payload, self.corrupt = url, payload, corrupt
        self.size = len(payload) if isinstance(payload, bytes) else payload.size
        self.headers = {'Content-Length': str(self.size)}
        self.offset = 0
        self.closed = False

    def geturl(self):
        return self.url

    def getcode(self):
        return 200

    def read1(self, count):
        require(not self.closed and 0 < count <= CHUNK, 'FIXTURE_RESPONSE')
        end = min(self.offset + count, self.size)
        if isinstance(self.payload, bytes):
            block = self.payload[self.offset:end]
        else:
            block = os.pread(self.payload.fd, end-self.offset, self.offset)
            require(len(block) == end-self.offset, 'FIXTURE_FILE_CHANGED')
        if self.corrupt and self.offset == 0 and block:
            block = bytes((block[0] ^ 1,)) + block[1:]
        self.offset = end
        return block

    def close(self):
        self.closed = True
        if isinstance(self.payload, File):
            self.payload.stable()


class FixtureOpener:
    def __init__(self, stack, folder, source, *, bad_digest=False):
        candidate = directory(stack, folder, 'candidate', private=True)
        manifest = File(stack, candidate, 'CORE-RELEASE.json', MAX_SMALL, fixture=True)
        record = decode(manifest.small())
        core, version, tag = (record.get(key) for key in ('core', 'version', 'tag'))
        require(core in ('sing-box', 'xray') and type(version) is str and type(tag) is str
                and len(tag) <= 100 and re.fullmatch(r'(?:0|[1-9][0-9]*)(?:\.(?:0|[1-9][0-9]*)){2}', version)
                and re.fullmatch(re.escape(f'core-{core}-v{version}-r')+r'[1-9][0-9]{0,8}', tag)
                and record.get('kind') == 'core-release-v1' and record.get('builder_source') == source
                and type(record.get('compatibility')) is dict
                and record['compatibility'].get('app_sources') == [source], 'FIXTURE_CANDIDATE')
        self.core = core
        self.corrupted = False
        self.error_type = None
        packs = {f'vui-core-{core}-{version}-{target}.zip' for target in TARGETS}
        require(set(os.listdir(candidate)) == packs | {'CORE-RELEASE.json','SHA256SUMS'}, 'FIXTURE_CANDIDATE')
        files = {'CORE-RELEASE.json': manifest,
                 'SHA256SUMS': File(stack, candidate, 'SHA256SUMS', MAX_SMALL, fixture=True)}
        files.update({name:File(stack, candidate, name, MAX_PACK, fixture=True) for name in sorted(packs)})
        self.assets = {}
        self.bad_digest = bad_digest
        asset_rows = []
        for index, (name, payload) in enumerate(sorted(files.items()), 1):
            identifier = RELEASE_ID + index
            url = API + '/releases/assets/' + str(identifier)
            self.assets[url] = (payload, name in packs)
            asset_rows.append(dict(id=identifier, name=name, size=payload.size, digest='sha256:'+payload.digest(),
                                   state='uploaded', url=url,
                                   browser_download_url=WEB+'/releases/download/'+tag+'/'+name))
        release = API + '/releases/' + str(RELEASE_ID)
        self.metadata = {
            API: encoded(dict(id=REPOSITORY_ID, full_name=REPOSITORY, private=False)),
            release: encoded(dict(id=RELEASE_ID, url=release, html_url=WEB+'/releases/tag/'+tag,
                                  tag_name=tag, draft=False, prerelease=False,
                                  published_at='2026-01-01T00:00:00Z', assets=asset_rows)),
            API+'/git/ref/tags/'+tag: encoded(dict(ref='refs/tags/'+tag,
                url=API+'/git/refs/tags/'+tag, object=dict(type='commit', sha=source,
                                                         url=API+'/git/commits/'+source))),
        }

    def open(self, request, timeout):
        url = request.full_url
        require(request.get_method() == 'GET' and 0 < timeout <= 10, 'FIXTURE_ROUTE')
        if url in self.metadata:
            return Response(url, self.metadata[url])
        require(url in self.assets, 'FIXTURE_ROUTE')
        payload, native_pack = self.assets[url]
        payload.stable()
        corrupt = self.bad_digest and native_pack
        if corrupt:
            require(not self.corrupted, 'FIXTURE_ROUTE')
            self.corrupted = True
        return Response(url, payload, corrupt=corrupt)


def inject_transport(control, opener):
    original = control._modules

    @contextmanager
    def modules(ops):
        with original(ops) as loaded:
            require(loaded.github.API == API and loaded.github.WEB == WEB
                    and loaded.artifacts.REPOSITORY_ID == REPOSITORY_ID, 'FIXTURE_REPOSITORY')
            client = loaded.github.GitHubCoreClient
            opener.error_type = loaded.github.CoreGitHubError
            loaded.github.GitHubCoreClient = lambda: client(opener=opener)
            yield loaded

    control._modules = modules


def inject_assertions(control, source, core, port):
    original_context = control._Switch._context

    def context(task, name, revision):
        original_context(task, name, revision)
        require(task.source['app_source'] == source and name == core, 'FIXTURE_SOURCE')

    control._Switch._context = context
    attempts = []
    if port is None:
        return attempts
    original_activate = control._Switch._activate

    def activate(task, state, intents, *, recovery, cleanup=False):
        if recovery:
            return original_activate(task, state, intents, recovery=True, cleanup=cleanup)
        require(not attempts and not cleanup and task.initial['v-ui.service'] == 'active'
                and intents[core]['enabled'] and port != task.config['port'], 'FIXTURE_PORT')
        task.services.prove_stopped()
        task._proof()
        with ExitStack() as stack:
            ipv4 = stack.enter_context(socket.socket(socket.AF_INET, socket.SOCK_STREAM))
            ipv4.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            ipv4.bind(('127.0.0.1', port))
            ipv4.listen(1)
            ipv6 = stack.enter_context(socket.socket(socket.AF_INET6, socket.SOCK_STREAM))
            ipv6.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            ipv6.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            ipv6.bind(('::1', port))
            ipv6.listen(1)
            attempts.append(port)
            return original_activate(task, state, intents, recovery=False, cleanup=False)

    control._Switch._activate = activate
    return attempts


def parse(arguments):
    require(len(arguments) in (3, 4), 'FIXTURE_INPUT')
    action, source, revision = arguments[:3]
    require(action in ('update', 'rollback', 'bad-digest', 'port-conflict')
            and re.fullmatch('[a-f0-9]{40}', source)
            and (revision == 'none' or re.fullmatch('[a-f0-9]{64}', revision))
            and (len(arguments) == 4) == (action == 'port-conflict'), 'FIXTURE_INPUT')
    port = None
    if action == 'port-conflict':
        require(re.fullmatch('[0-9]{4,5}', arguments[3]), 'FIXTURE_INPUT')
        port = int(arguments[3])
        require(1024 <= port <= 65535, 'FIXTURE_INPUT')
    return action, source, None if revision == 'none' else revision, port


def run(arguments):
    require(sys.platform == 'linux' and os.geteuid() == 0
            and os.getpid() == os.getpgrp() == os.getsid(0)
            and sys.executable == '/usr/bin/python3'
            and sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode
            and os.environ.get('GITHUB_ACTIONS') == 'true'
            and os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted'
            and os.environ.get('GITHUB_REPOSITORY') == REPOSITORY
            and Path(__file__) == FIXTURE/'runner.py', 'FIXTURE_SCOPE')
    action, source, revision, port = parse(arguments)
    with ExitStack() as stack:
        folder = fixed_directory(stack, FIXTURE, private=True)
        File(stack, folder, 'runner.py', 4_000_000, fixture=True).stable()
        ops, control = installed_modules(stack)
        opener = FixtureOpener(stack, folder, source, bad_digest=action == 'bad-digest')
        inject_transport(control, opener)
        conflicts = inject_assertions(control, source, opener.core, port)
        switch_action = 'rollback' if action == 'rollback' else 'update'
        try:
            result = control.switch(ops, core=opener.core, action=switch_action,
                expected_revision=revision, release_id=RELEASE_ID if switch_action == 'update' else None)
        except Exception as error:
            if (action == 'bad-digest' and opener.corrupted and type(error) is opener.error_type
                    and str(error) == 'ASSET_DIGEST'):
                raise Refused('ASSET_DIGEST') from None
            raise
        expected = 'restored_previous' if action == 'port-conflict' else 'switched'
        require(action != 'bad-digest' and result.get('outcome') == expected
                and type(result.get('panel_running')) is bool
                and type(result.get('selection_revision')) is str
                and re.fullmatch('[a-f0-9]{64}', result['selection_revision'])
                and (port is None or conflicts == [port]), 'FIXTURE_OUTCOME')
        return dict(ok=True, outcome=expected, selection_revision=result['selection_revision'],
                    panel_running=result['panel_running'])


def main():
    try:
        result = run(sys.argv[1:])
    except Refused as error:
        result = dict(ok=False, error=str(error))
    except BaseException:
        result = dict(ok=False, error='FIXTURE_SWITCH_REFUSED')
    print(encoded(result).decode('ascii'))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
