"""Drop-privilege terminal grant launcher. This executable refuses root."""
from __future__ import annotations
import importlib.util
import json
import os
from pathlib import Path
import pwd
import re
import sys

CONTROL = Path('/usr/local/lib/v-ui')
ROOT = Path('/var/lib/v-ui')
SCOPES = {'diagnose', 'nodes', 'share', 'relay', 'certificates', 'operate', 'upgrade', 'destructive'}


def arguments(args):
    if args == ['list']:
        return args
    if len(args) == 2 and args[0] == 'revoke' and re.fullmatch(r'[1-9][0-9]{0,18}', args[1]):
        return ['revoke', '--id', args[1]]
    if len(args) == 3 and args[0] == 'create':
        scopes = args[1].split(',')
        if (1 <= len(scopes) <= len(SCOPES) and len(set(scopes)) == len(scopes)
                and set(scopes) <= SCOPES and re.fullmatch(r'[0-9]{1,8}', args[2])
                and 60 <= int(args[2]) <= 15552000):
            return ['create', '--scopes', args[1], '--seconds', args[2]]
    raise ValueError('fixed terminal grant input required')


def main():
    account = pwd.getpwnam('v-ui')
    if os.getuid() == 0 or os.geteuid() != account.pw_uid:
        raise ValueError('dedicated service user required')
    args = arguments(sys.argv[1:])
    spec = importlib.util.spec_from_file_location('_vui_fixed_grant_worker', CONTROL / 'ops_worker.py')
    worker = importlib.util.module_from_spec(spec); spec.loader.exec_module(worker)
    release, _ = worker._load_release_tools().active(ROOT)
    config = json.loads((Path('/etc/v-ui/service.json')).read_text())
    payload = release / 'payload'
    runtime = release / 'runtime/python/bin/python3'
    environment = {'PATH': '/usr/bin:/bin', 'HOME': str(ROOT), 'LANG': 'C.UTF-8',
                   'PYTHONDONTWRITEBYTECODE': '1', 'VUI_DATA_DIR': str(ROOT / 'data'),
                   'VUI_PUBLIC_ORIGIN': config['origin']}
    os.chdir(payload)
    os.execve(runtime, [str(runtime), '-B', '-m', 'app.maintenance_cli', *args], environment)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('终端授权入口未完成；请检查受管安装。', file=sys.stderr)
        raise SystemExit(1)
