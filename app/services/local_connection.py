"""Purpose-specific delivery of the active loopback proxy's credentials.

Ordinary snapshots stay redacted. This does not execute a program, change a
system proxy or return the overseas UUID. It requires an explicit POST and
owner authorization at the API boundary (relay AND share for maintenance).
"""
from dataclasses import dataclass, field
import re

from app.services.core_runtime import revision
from app.services.proxy_store import ProxyConflict, ProxyStorageError


class LocalConnectionError(ValueError):
    pass


@dataclass(frozen=True)
class LocalConnection:
    port: int
    username: str = field(repr=False)
    password: str = field(repr=False)
    revision: str

    def delivery(self):
        return {'host': '127.0.0.1', 'port': self.port, 'username': self.username,
                'password': self.password, 'revision': self.revision,
                'protocols': ['http', 'socks5'], 'system_proxy': False}


class LocalConnectionExporter:
    def __init__(self, manager):
        self.manager = manager

    def connection(self, expected_revision):
        if type(expected_revision) is not str or not re.fullmatch('[0-9a-f]{64}', expected_revision):
            raise LocalConnectionError('INVALID_LOCAL_CONNECTION_REQUEST')
        with self.manager.lock:
            coordinator = self.manager.proxy_coordinator
            active = coordinator.store.load_active(reject_pending=True)
            if active is None or not active['settings']['local_proxy_enabled']:
                raise LocalConnectionError('LOCAL_PROXY_NOT_ACTIVE')
            if active['revision'] != expected_revision:
                raise ProxyConflict('Active revision changed')
            try:
                compiled = coordinator.compile_active(self.manager._all_rows())
                context = active.get('runtime_context')
                runtime = self.manager.get('sing-box').runtime
                state, status = runtime.state(), runtime.status()
                if (compiled is None or context is None or compiled.identity != context['policy_identity']
                        or revision(compiled.config) != active['core_revision']
                        or state != {'revision': active['core_revision'], 'enabled': True}
                        or status.get('running') is not True or status.get('enabled') is not True
                        or status.get('binary_exists') is not True or status.get('config_exists') is not True
                        or status.get('version_required') != '1.14.2'
                        or status.get('applied_revision') != active['core_revision']
                        or revision(runtime.active_config()) != active['core_revision']):
                    raise LocalConnectionError('LOCAL_PROXY_STATE_MISMATCH')
                # Re-read under the same manager lock, detecting other store
                # writers before delivering a no-longer-active secret.
                latest = coordinator.store.load_active(reject_pending=True)
                if latest is None or latest['revision'] != expected_revision:
                    raise ProxyConflict('Active revision changed')
                values, password = active['settings'], active['local_password']
                if (type(password) is not str or not 12 <= len(password) <= 255
                        or any(not 33 <= ord(c) <= 126 for c in password)
                        or not re.fullmatch('[A-Za-z0-9._-]{1,64}', values['local_username'])
                        or type(values['local_port']) is not int or not 1024 <= values['local_port'] <= 65535):
                    raise LocalConnectionError('LOCAL_PROXY_STATE_MISMATCH')
                return LocalConnection(values['local_port'], values['local_username'], password, expected_revision)
            except (LocalConnectionError, ProxyConflict, ProxyStorageError):
                raise
            except Exception:
                # Never stringify a runtime error, config or private value.
                raise LocalConnectionError('LOCAL_PROXY_STATE_MISMATCH') from None
