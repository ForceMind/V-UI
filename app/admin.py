"""Local administration: python -m app.admin create USER / set-password USER."""
from __future__ import annotations

import argparse
import getpass
import os
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["create", "set-password"])
    parser.add_argument("username")
    args = parser.parse_args()
    # No command-line password flag: avoid shell history and process-list leaks.
    if not sys.stdin.isatty():
        parser.error("Run from an interactive terminal; passwords are never accepted in arguments")
    os.umask(0o077)
    from app.services.auth_service import provision_admin
    password = getpass.getpass("Password (15–128 characters): ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        print("Passwords do not match", file=sys.stderr)
        return 1
    try:
        provision_admin(args.username, password, reset=args.command == "set-password")
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print("Administrator saved. Previous sessions were revoked when resetting.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
