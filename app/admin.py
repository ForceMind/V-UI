"""Local administration: python -m app.admin create USER / set-password USER."""
from __future__ import annotations

import argparse
import getpass
import json
import os
import stat
import sys


def installer_request(command):
    """Local installer only: bounded anonymous pipes, never a password argument."""
    if not all(stat.S_ISFIFO(os.fstat(stream.fileno()).st_mode)
               for stream in (sys.stdin, sys.stdout, sys.stderr)):
        raise ValueError('安装器账户操作只接受私有管道')
    raw = sys.stdin.buffer.read(4097)
    if len(raw) > 4096:
        raise ValueError('安装器账户请求无效')
    request = json.loads(raw)
    expected = {'version'} if command == 'installer-status' else {'version', 'username', 'password'}
    if (type(request) is not dict or set(request) != expected
            or type(request['version']) is not int or request['version'] != 1):
        raise ValueError('安装器账户请求无效')
    from app.models import database
    from app.services.auth_service import provision_admin
    if command == 'installer-create':
        if type(request['username']) is not str or type(request['password']) is not str:
            raise ValueError('安装器账户请求无效')
        # provision_admin takes BEGIN IMMEDIATE and checks the entire users
        # table in that transaction. An existing/inactive account is preserved.
        provision_admin(request['username'], request['password'])
        return {'version': 1, 'created': True}
    database.init_db()
    with database.SessionLocal() as db:
        return {'version': 1, 'has_account': db.query(database.User).first() is not None}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["create", "set-password", "installer-status", "installer-create"])
    parser.add_argument("username", nargs='?')
    args = parser.parse_args()
    if args.command.startswith('installer-'):
        if args.username is not None:
            parser.error('安装器账户操作不接受命令行凭据')
        os.umask(0o077)
        try:
            result = installer_request(args.command)
        except Exception:
            # No exception or request bytes may echo a password through stderr.
            print('安装器账户操作失败；已有账户未被重置。', file=sys.stderr)
            return 1
        print(json.dumps(result, separators=(',', ':')))
        return 0
    if args.username is None:
        parser.error('请提供管理员用户名')
    # No command-line password flag: avoid shell history and process-list leaks.
    if not sys.stdin.isatty():
        parser.error('请从交互终端运行；密码不能通过命令行参数传入')
    os.umask(0o077)
    from app.services.auth_service import provision_admin
    password = getpass.getpass('密码（15–128 个字符）: ')
    confirm = getpass.getpass('确认密码: ')
    if password != confirm:
        print('两次密码不一致', file=sys.stderr)
        return 1
    try:
        provision_admin(args.username, password, reset=args.command == "set-password")
    except ValueError as exc:
        messages = {'Username must be 3–64 ASCII letters, digits, _, . or -': '用户名须为 3–64 位字母、数字或 _.-',
                    'Password must contain 15–128 characters': '密码须为 15–128 个字符',
                    'Administrator does not exist': '管理员不存在',
                    'An account already exists; use set-password for its username': '管理员已存在；请使用 set-password 修改其密码'}
        print(messages.get(str(exc), '账户操作失败'), file=sys.stderr)
        return 1
    print('管理员已保存；修改密码时旧会话已撤销。')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
