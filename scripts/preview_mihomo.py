#!/usr/bin/env python3
"""Value-free offline YAML inspection. Never writes, downloads or runs a core."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.mihomo_preview import MAX_INPUT_BYTES, MihomoPreviewError, preview_mihomo


class InputError(ValueError):
    pass


class Arguments(argparse.ArgumentParser):
    def error(self, message):
        raise InputError("CLI_ARGUMENTS")


def _read_file(path):
    descriptor = None
    try:
        # Require a regular final component, not a symlink/FIFO/device. Parent
        # directory symlinks remain allowed: this is not a path sandbox.
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise InputError("REGULAR_FILE_REQUIRED")
        if before.st_size > MAX_INPUT_BYTES:
            raise InputError("INPUT_LIMIT")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            raw = stream.read(MAX_INPUT_BYTES + 1)
            after = os.fstat(stream.fileno())
        if len(raw) > MAX_INPUT_BYTES:
            raise InputError("INPUT_LIMIT")
        identity = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
        if identity(before) != identity(after):
            raise InputError("INPUT_CHANGED")
        return raw
    except (OSError, AttributeError):
        raise InputError("INPUT_UNREADABLE") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def main(argv=None):
    parser = Arguments(description=__doc__, allow_abbrev=False)
    parser.add_argument("input", help="Bounded regular YAML file; source values are never printed")
    parser.add_argument("--baseline", help="Optional regular YAML file for structural section comparison")
    source = "pair"
    try:
        args = parser.parse_args(argv)
        source = "candidate"
        candidate = _read_file(args.input)
        source = "baseline"
        baseline = None if args.baseline is None else _read_file(args.baseline)
        report = preview_mihomo(candidate, baseline)
    except MihomoPreviewError as error:
        print(json.dumps({"ok": False, "error": {"code": error.code, "source": error.source}}))
        return 2
    except InputError as error:
        print(json.dumps({"ok": False, "error": {"code": str(error), "source": source}}))
        return 2
    print(json.dumps({"ok": True, "report": report}, ensure_ascii=True, allow_nan=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
