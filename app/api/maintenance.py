"""Administrative issuance and scoped maintenance self-discovery."""
from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services import maintenance_auth as auth

router = APIRouter()


class GrantPayload(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    scopes: list[str] = Field(default_factory=lambda: ['diagnose'], min_length=1, max_length=8)
    expires_seconds: int = Field(default=1800, ge=60, le=7200)


class ExchangePayload(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    grant: str = Field(min_length=1, max_length=512)


class TaskPayload(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    action: str = Field(pattern=r'^(status|start|stop|restart|backup|upgrade|restore)$')
    backup_id: str | None = Field(default=None, pattern=r'^[0-9a-f]{32}$', min_length=32, max_length=32)

    @model_validator(mode='after')
    def restore_backup(self):
        if (self.action == 'restore') != (self.backup_id is not None):
            raise ValueError('Restore alone requires a backup id')
        return self


def _admin(request):
    # Even when legacy business APIs receive an owner-id compatibility identity,
    # maintenance principals never acquire grant administration authority.
    if getattr(request.state, 'maintenance', None) is not None:
        raise HTTPException(403, '维护会话不能管理授权')
    user = getattr(request.state, 'admin', None)
    if type(user) is not dict or type(user.get('id')) is not int:
        raise HTTPException(401, '需要管理员登录')
    return user['id']


def _principal(request):
    value = getattr(request.state, 'maintenance', None)
    if type(value) is not dict:
        raise HTTPException(403, '请使用临时维护会话')
    return value


@router.get('/grants')
def grants(request: Request):
    return auth.list_grants(_admin(request))


@router.post('/grants', status_code=201)
def create_grant(payload: GrantPayload, request: Request):
    from app.middleware.auth import expected_origin
    record, token = auth.create_grant(_admin(request), payload.scopes, payload.expires_seconds)
    origin = expected_origin(request)
    if origin is None:
        raise HTTPException(403, '面板来源无效')
    return {**record, 'link': origin + '/maintenance#grant=' + token}


@router.delete('/grants/{grant_id}')
def revoke_grant(grant_id: int, request: Request):
    return auth.revoke_grant(_admin(request), grant_id)


@router.post('/exchange')
def exchange(payload: ExchangePayload, request: Request):
    try:
        result = auth.exchange(payload.grant, request.client.host if request.client else 'unknown')
    except HTTPException as error:
        auth.audit(None, 'exchange', error.status_code)
        raise
    principal = {'grant_id': result['id'], 'scopes': result['scopes']}
    return {**result, 'catalog': auth.catalog(principal)}


@router.get('/session')
def session(request: Request):
    principal = _principal(request)
    return {'grant_id': principal['grant_id'], 'scopes': principal['scopes'],
            'expires_at': principal['expires_at']}


@router.get('/catalog')
def catalog(request: Request):
    principal = getattr(request.state, 'maintenance', None)
    if principal is None:
        _admin(request)
        principal = {'scopes': sorted(auth.SCOPES)}
    return {'operations': auth.catalog(principal), 'task_actions': {
        'status': 'diagnose', 'start': 'operate', 'stop': 'operate', 'restart': 'operate',
        'backup': 'operate', 'upgrade': 'upgrade', 'restore': 'destructive'}}


@router.get('/audit')
def audit(request: Request):
    principal = getattr(request.state, 'maintenance', None)
    if principal is None:
        _admin(request)
    return auth.audit_rows(principal)


def _bridge(operation, *args, **kwargs):
    try:
        from app.services import maintenance_tasks
        return getattr(maintenance_tasks, operation)(*args, **kwargs)
    except HTTPException:
        raise
    except Exception:
        # Never expose socket paths, protocol bytes or root worker exceptions.
        raise HTTPException(503, '系统维护组件暂不可用，请稍后重试或使用终端恢复') from None


@router.get('/status')
def status():
    return _bridge('system_status')


@router.get('/logs')
def logs():
    return _bridge('system_logs')


@router.get('/tasks')
def tasks():
    return _bridge('list_tasks')


@router.get('/tasks/{task_id}')
def task(task_id: str):
    if not re.fullmatch('[0-9a-f]{32}', task_id):
        raise HTTPException(422, '任务编号无效')
    return _bridge('get_task', task_id)


@router.post('/tasks', status_code=202)
def create_task(payload: TaskPayload, request: Request):
    principal = getattr(request.state, 'maintenance', None)
    if principal is not None:
        auth.authorize(principal, 'POST', '/api/maintenance/tasks', payload.model_dump())
    result = _bridge('submit_task', payload.action, **({'backup_id': payload.backup_id} if payload.backup_id is not None else {}))
    task_id = result.get('id') if type(result) is dict else None
    if type(task_id) is str and re.fullmatch('[0-9a-f]{32}', task_id):
        request.scope.setdefault('state', {})['maintenance_task_id'] = task_id
    if principal is None:
        auth.audit(None, 'POST /api/maintenance/tasks', 202, task_id)
    return result
