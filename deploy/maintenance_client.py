"""Bounded stdlib Unix-socket client for the fixed root maintenance service."""
from __future__ import annotations
import json
import os
from pathlib import Path
import socket
import stat

SOCKET = Path('/run/v-ui-maintenance/control.sock')
LIMIT = 65536


class MaintenanceUnavailable(RuntimeError):
    pass


def request(value):
    raw = json.dumps(value, separators=(',', ':'), allow_nan=False).encode() + b'\n'
    if len(raw) > 4096:
        raise MaintenanceUnavailable('维护请求无效')
    try:
        # The service account cannot substitute the peer socket or its parents.
        for parent in reversed(SOCKET.parents):
            info = parent.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
                raise OSError('untrusted socket directory')
        info = SOCKET.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o007:
            raise OSError('untrusted socket')
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(5)
            connection.connect(str(SOCKET))
            connection.sendall(raw)
            connection.shutdown(socket.SHUT_WR)
            data = bytearray()
            while True:
                part = connection.recv(min(8192, LIMIT + 1 - len(data)))
                if not part:
                    break
                data.extend(part)
                if len(data) > LIMIT:
                    raise OSError('oversized response')
        result = json.loads(data)
        if not isinstance(result, dict) or result.get('ok') is not True:
            raise MaintenanceUnavailable('系统维护未接受请求，请检查任务与本机受管组件')
        return result['result']
    except (OSError, ValueError, KeyError, TypeError):
        raise MaintenanceUnavailable('系统维护组件不可用，请通过本机终端检查') from None
