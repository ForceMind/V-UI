"""Explicit CI transport fixture or real public-release installer command.

Only the pre-publication transport is doubled. Both modes execute the real
installer, portable bundle, fixed OS controller, systemd and global v-ui entry.
This module is called only by the opt-in empty-host root integration test.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
from pathlib import Path
import re
import select
import shlex
import stat
import subprocess
import time
from contextlib import contextmanager
from urllib.request import ProxyHandler, Request, build_opener
from urllib.parse import urlsplit

from scripts.publish_release import ASSETS, installation_command
from scripts.ops_menu_smoke import MenuSession

COMMAND_DIRECTORY = Path('/usr/local/bin')


def sudo_policy_snapshot():
    """Read-only hashes, not rule contents; inaccessible is an error, not absence."""
    program = '''import hashlib,json,os,stat
from pathlib import Path
records={}
for path in [Path('/etc/sudoers'),*sorted(Path('/etc/sudoers.d').iterdir())]:
    info=path.lstat()
    record={'type':stat.S_IFMT(info.st_mode),'uid':info.st_uid,'gid':info.st_gid,'mode':stat.S_IMODE(info.st_mode)}
    if stat.S_ISREG(info.st_mode):
        with path.open('rb') as stream:raw=stream.read(1000001)
        if len(raw)>1000000:raise RuntimeError('sudo policy snapshot exceeds bound')
        record['sha256']=hashlib.sha256(raw).hexdigest()
    elif stat.S_ISLNK(info.st_mode):record['target']=os.readlink(path)
    records[str(path)]=record
print(json.dumps(records,sort_keys=True))
'''
    result = subprocess.run(['/usr/bin/sudo', '-n', '/usr/bin/python3', '-B', '-I', '-S', '-c', program],
                            check=True, capture_output=True, text=True, timeout=10)
    snapshot = json.loads(result.stdout)
    if not isinstance(snapshot, dict) or '/etc/sudoers' not in snapshot:
        raise AssertionError('Complete sudo policy snapshot required')
    return snapshot


@contextmanager
def hosted_command_directory():
    """Temporary hosted-CI fixture preparation, never production installer policy.

    Hosted images may make /usr/local/bin runner-owned/writable for tool setup.
    Keep the application's root-path rejection intact, prepare a trusted empty-
    product fixture, and restore only this exact directory's recorded metadata.
    """
    if (platform.system() != 'Linux' or os.environ.get('GITHUB_ACTIONS') != 'true'
            or os.environ.get('GITHUB_REPOSITORY') != 'ForceMind/V-UI'
            or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted'
            or os.environ.get('VUI_SYSTEM_INSTALL_TEST') != '1'):
        raise AssertionError('Hosted empty-runner command-directory fixture required')
    path = COMMAND_DIRECTORY
    before = path.lstat()
    if not stat.S_ISDIR(before.st_mode):
        raise AssertionError('Hosted command directory must be a real directory')
    identity = (before.st_dev, before.st_ino)
    original = (before.st_uid, before.st_gid, stat.S_IMODE(before.st_mode))
    expected = original
    failure = None
    def current():
        value = path.lstat()
        if not stat.S_ISDIR(value.st_mode) or (value.st_dev, value.st_ino) != identity:
            raise AssertionError('Hosted command-directory identity changed; do not restore')
        return value.st_uid, value.st_gid, stat.S_IMODE(value.st_mode)
    def run(program, value):
        subprocess.run(['/usr/bin/sudo', '-n', program, value, str(path)],
                       check=True, capture_output=True, timeout=10)
    try:
        print('Hosted command-directory fixture before: ' + json.dumps({'uid':original[0],'gid':original[1],'mode':oct(original[2])}))
        if original[:2] != (0, 0):
            run('/usr/bin/chown', '0:0')
            expected = (0, 0, original[2])
            if current() != expected:
                raise AssertionError('Hosted command-directory ownership preparation failed')
        if expected[2] != 0o755:
            run('/usr/bin/chmod', '0755')
            expected = (0, 0, 0o755)
            if current() != expected:
                raise AssertionError('Hosted command-directory mode preparation failed')
        yield
    except BaseException as error:
        failure = error
        raise
    finally:
        try:
            if current() != expected:
                raise AssertionError('Hosted command-directory metadata changed; do not restore')
            if expected[:2] != original[:2]:
                run('/usr/bin/chown', str(original[0])+':'+str(original[1]))
            if expected[2] != original[2]:
                run('/usr/bin/chmod', format(original[2], '04o'))
            if current() != original:
                raise AssertionError('Hosted command-directory restoration failed')
        except BaseException as cleanup_error:
            if failure is None:
                raise
            failure.add_note('Hosted command-directory cleanup was not confirmed: '+type(cleanup_error).__name__)


def fixture_commands(source, work, kit, certificate_args, configured_args):
    version = (source / 'VERSION').read_text().strip()
    kit = Path(kit).resolve()
    checksum = hashlib.sha256((kit / 'SHA256SUMS').read_bytes()).hexdigest()
    directory = work / 'download-fixture'
    directory.mkdir(mode=0o700)
    log = work / 'fixture-downloads.jsonl'
    log.write_text('')
    script = '''#!/usr/bin/python3
import hashlib,json,pathlib,sys
args=sys.argv[1:]
url=next((value for value in args if value.startswith('https://')), '')
prefix=PREFIX
if not url.startswith(prefix) or '-o' not in args:raise SystemExit(71)
name=url[len(prefix):]
if '/' in name or name not in NAMES:raise SystemExit(72)
destination=pathlib.Path(args[args.index('-o')+1])
if destination.name!=name:raise SystemExit(73)
raw=(pathlib.Path(KIT)/name).read_bytes()
destination.write_bytes(raw)
with pathlib.Path(LOG).open('a') as stream:
    stream.write(json.dumps({'name':name,'sha256':hashlib.sha256(raw).hexdigest()})+'\\n')
'''
    replacements = {'PREFIX': repr('https://github.com/ForceMind/V-UI/releases/download/v' + version + '/'),
                    'NAMES': repr(sorted(path.name for path in kit.iterdir())),
                    'KIT': repr(str(kit)), 'LOG': repr(str(log))}
    for key, value in replacements.items():
        script = script.replace(key, value)
    curl = directory / 'curl'
    curl.write_text(script)
    curl.chmod(0o700)
    base = ['sudo', '/usr/bin/env', 'PATH=' + str(directory) + ':/usr/sbin:/usr/bin:/sbin:/bin',
            'PYTHONDONTWRITEBYTECODE=1', 'bash', str(source / 'install.sh'),
            '--version', 'v' + version, '--with-global-cli', '--kit-sha256', checksum,
            *certificate_args]
    return {'initial': [*base, '--interactive'], 'repeat': [*base, *configured_args],
            'upgrade': [*base, *configured_args, '--upgrade'], 'transport': 'explicit local file adapter',
            'download_log': log}


def published_commands(source, version, commit, certificate_args, configured_args):
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', version) or (source / 'VERSION').read_text().strip() != version:
        raise AssertionError('Published version differs from checked out source')
    if subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=source, text=True).strip() != commit:
        raise AssertionError('Published acceptance checkout is not exact')
    opener = build_opener(ProxyHandler({}))
    def read(url, limit):
        request = Request(url, headers={'User-Agent': 'V-UI-public-install-acceptance'})
        with opener.open(request, timeout=60) as response:
            if urlsplit(response.geturl()).scheme != 'https':
                raise AssertionError('Public acceptance must retain HTTPS')
            data = response.read(limit + 1)
        if len(data) > limit:
            raise AssertionError('Public acceptance metadata exceeds bound')
        return data
    release = json.loads(read('https://api.github.com/repos/ForceMind/V-UI/releases/tags/v' + version, 1_000_000))
    if release.get('draft') or release.get('prerelease') or release.get('tag_name') != 'v' + version:
        raise AssertionError('Stable public release is required')
    assets = {item['name']: item for item in release['assets']}
    if set(assets) != ASSETS:
        raise AssertionError('Public release does not have the complete fixed asset set')
    files = {}
    for name, limit in (('install.sh', 100_000), ('SHA256SUMS', 64_000), ('RELEASE.json', 16_384)):
        item = assets[name]
        expected = 'https://github.com/ForceMind/V-UI/releases/download/v' + version + '/' + name
        if item['browser_download_url'] != expected:
            raise AssertionError('Unexpected public asset URL')
        raw = read(expected, limit)
        if item.get('digest') != 'sha256:' + hashlib.sha256(raw).hexdigest():
            raise AssertionError('Public asset metadata/digest mismatch')
        files[name] = raw
    if files['install.sh'] != (source / 'install.sh').read_bytes():
        raise AssertionError('Published installer differs from exact source')
    record = json.loads(files['RELEASE.json'])
    if record.get('source_commit') != commit or record.get('version') != version:
        raise AssertionError('Published source identity mismatch')
    canonical = installation_command(files, version)
    if canonical not in release.get('body', ''):
        raise AssertionError('Published installation command differs from accepted bytes')
    def command(*, interactive, extra):
        return ['/bin/sh', '-c', installation_command(files, version,
                installer_args=(*certificate_args, *extra), interactive=interactive)]
    return {'initial': command(interactive=True, extra=()),
            'repeat': command(interactive=False, extra=configured_args),
            'upgrade': command(interactive=False, extra=(*configured_args, '--upgrade')),
            'transport': 'anonymous public HTTPS release assets', 'canonical': canonical}


def setup_commands(source, version, release_commit, checkout_commit, ref, certificate_args, configured_args, *, release_source):
    """Exercise the actual short bootstrap, independently of the frozen app release."""
    if ref != 'main' and not re.fullmatch(r'[a-f0-9]{40}', ref):
        raise AssertionError('Short bootstrap requires main or an exact source commit')
    if subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=source, text=True).strip() != checkout_commit:
        raise AssertionError('Short bootstrap checkout is not exact')
    # The new bootstrap may have a newer VERSION and installer. Authenticate the
    # immutable public suite against its own independently checked out source.
    published = published_commands(Path(release_source), version, release_commit,
                                   certificate_args, configured_args)
    url = 'https://raw.githubusercontent.com/ForceMind/V-UI/' + ref + '/setup.sh'
    opener = build_opener(ProxyHandler({}))
    with opener.open(Request(url, headers={'User-Agent':'V-UI-short-install-acceptance'}), timeout=60) as response:
        if urlsplit(response.geturl()).scheme != 'https':
            raise AssertionError('Short bootstrap must retain HTTPS')
        data = response.read(100_001)
    if len(data) > 100_000 or data != (source / 'setup.sh').read_bytes():
        raise AssertionError('Public short bootstrap differs from exact checkout')
    canonical = 'bash <(curl -fsSL ' + url + ')'
    def command(extra):
        return ['/bin/bash', '-c', canonical + ' ' + shlex.join(['--version', 'v'+version,
                                                              *certificate_args, *extra])]
    return {'initial':command(()), 'repeat':command(configured_args),
            'upgrade':command((*configured_args, '--upgrade')),
            'transport':'anonymous public HTTPS short bootstrap and frozen release assets',
            'canonical':canonical, 'release_canonical':published['canonical']}


def global_menu_check(test, env, *, exercise_start_stop):
    entry = Path('/usr/local/sbin/v-ui')
    alias = Path('/usr/local/bin/v-ui')
    test.assertTrue(alias.is_symlink())
    test.assertEqual(alias.readlink(), entry)
    test.assertEqual(entry.stat().st_uid, 0)
    test.assertEqual(entry.stat().st_mode & 0o7777, 0o755)
    with MenuSession(['v-ui'], cwd='/tmp', env=env) as session:
        session.expect('请选择: ')
        session.send('1\n')
        state = session.expect('请选择: ')
        test.assertIn('v-ui.service', state)
        if exercise_start_stop:
            for choice in ('3\n', '2\n'):
                session.send(choice)
                session.expect('确认 [yes/N]: ')
                session.send('yes\n')
                result = session.expect('请选择: ')
                test.assertIn('操作完成；服务状态已核验。', result)
        session.send('0\n')
        session.finish()


def configured_setup_with_tty(command, *, timeout=300, cancel_existing=False):
    """Use a real /dev/tty; optionally cancel only the existing-install menu."""
    with MenuSession(command, cwd='/tmp', env=dict(os.environ), timeout=timeout) as session:
        deadline=time.monotonic()+timeout
        cancelled=False
        while session.process.poll() is None:
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(command,timeout,output=session.text())
            session._read(.1)
            if cancel_existing and not cancelled and '请选择 [1/2/0，回车取消]: ' in session.text():
                session.send('0\n')
                cancelled=True
        while session._read(.02) and select.select([session.fd],[],[],0)[0]:
            pass
        if cancel_existing and not cancelled:
            raise AssertionError('Existing-install menu prompt was not observed; cancellation was not exercised')
        return subprocess.CompletedProcess(command,session.process.returncode,session.text(),'')
