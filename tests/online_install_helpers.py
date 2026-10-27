"""Explicit CI transport fixture or real public-release installer command.

Only the pre-publication transport is doubled. Both modes execute the real
installer, portable bundle, fixed OS controller, systemd and global v-ui entry.
This module is called only by the opt-in empty-host root integration test.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess
from urllib.request import ProxyHandler, Request, build_opener
from urllib.parse import urlsplit

from scripts.publish_release import ASSETS, installation_command
from scripts.ops_menu_smoke import MenuSession


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
