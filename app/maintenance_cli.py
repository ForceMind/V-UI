"""Local grant issuance, invoked by the fixed root wrapper as the service user.

This module has no HTTP endpoint. Its stdout contains the one-time grant link
only for an explicit create command; never log stdout or retain shell history.
"""
from __future__ import annotations

import argparse
import json


def main(argv=None):
    from app.models import database
    from app.services import maintenance_auth
    from app.middleware.auth import configured_origin
    parser = argparse.ArgumentParser(description='V-UI 本机临时维护授权')
    parser.add_argument('operation', choices=['create', 'list', 'revoke'])
    parser.add_argument('--scopes', default='diagnose')
    parser.add_argument('--seconds', type=int, default=1800)
    parser.add_argument('--id', type=int)
    args = parser.parse_args(argv)
    database.init_db()
    with database.SessionLocal() as db:
        user = db.query(database.User).filter_by(is_active=True).one_or_none()
        if user is None:
            parser.error('需要已有且启用的管理员账户')
        owner_id = user.id
    if args.operation == 'create':
        origin = configured_origin()
        if not origin:
            parser.error('请先配置准确的 VUI_PUBLIC_ORIGIN')
        record, token = maintenance_auth.create_grant(owner_id, args.scopes.split(','), args.seconds)
        result = {**record, 'link': origin + '/maintenance#grant=' + token}
    elif args.operation == 'revoke':
        if args.id is None or args.id <= 0:
            parser.error('撤销必须提供有效 --id')
        result = maintenance_auth.revoke_grant(owner_id, args.id)
    else:
        result = maintenance_auth.list_grants(owner_id)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
