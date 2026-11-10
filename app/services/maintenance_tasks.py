"""Panel-to-root maintenance bridge; authentication and scopes stay in the API."""
from __future__ import annotations
# The installed panel copy has no privileged imports or caller-configurable path.
import importlib.util
from pathlib import Path

# Source development and packaged deployments both include deploy/ in the payload.
_spec = importlib.util.spec_from_file_location('_vui_task_client', Path(__file__).resolve().parents[2] / 'deploy/maintenance_client.py')
_client = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_client)
MaintenanceUnavailable = _client.MaintenanceUnavailable


def submit_task(action, *, backup_id=None):
    value = {'protocol': 1, 'operation': 'submit', 'action': action}
    if backup_id is not None:
        value['backup_id'] = backup_id
    return _client.request(value)


def get_task(task_id):
    return _client.request({'protocol': 1, 'operation': 'get', 'task_id': task_id})


def list_tasks():
    return _client.request({'protocol': 1, 'operation': 'list'})


def system_status():
    return _client.request({'protocol': 1, 'operation': 'status'})


def system_logs():
    return _client.request({'protocol': 1, 'operation': 'logs'})


def core_selection_status():
    return _client.request({'protocol': 1, 'operation': 'core_status'})


def core_authority_status(root_token, binding):
    return _client.request({'protocol':1,'operation':'core_authority','token':root_token,**binding})


def revoke_core_binding(main_id, main_digest):
    return _client.request({'protocol':1,'operation':'core_revoke','main_id':main_id,'main_digest':main_digest})


def core_observe():
    return _client.request({'protocol': 1, 'operation': 'core_observe'})


def core_list():
    return _client.request({'protocol': 1, 'operation': 'core_list'})


def core_get(task_id):
    return _client.request({'protocol': 1, 'operation': 'core_get', 'task_id': task_id})


def core_lookup(root_token, binding, request_id):
    return _client.request({'protocol': 1, 'operation': 'core_lookup', 'token': root_token,
                            **binding, 'request_id': request_id})


def core_submit(root_token, binding, request_id, action, parameters):
    return _client.request({'protocol': 1, 'operation': 'core_submit', 'token': root_token,
                            **binding, 'request_id': request_id, 'action': action, 'parameters': parameters})
