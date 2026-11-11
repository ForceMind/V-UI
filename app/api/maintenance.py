"""Administrative issuance and scoped maintenance self-discovery."""
from __future__ import annotations

import json
import re

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services import maintenance_auth as auth

router = APIRouter()


class GrantPayload(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    scopes: list[str] = Field(default_factory=lambda: ['diagnose'], min_length=1, max_length=len(auth.SCOPES))
    expires_seconds: int = Field(default=auth.DEFAULT_SECONDS, ge=60, le=auth.MAX_SECONDS)


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


class CoreTaskPayload(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    action: str
    parameters: dict

    @model_validator(mode='after')
    def closed_core_intent(self):
        fields = {
            'update': {'core', 'release_id', 'selection_revision'},
            'rollback': {'core', 'selection_revision'},
            'backup': {'selection_revision'},
            'restore': {'backup_task_id', 'selection_revision'},
            'recover': {'recovery_kind', 'pending_transaction', 'pending_sequence', 'selection_revision'},
        }
        value = self.parameters
        if self.action not in fields or set(value) != fields[self.action]:
            raise ValueError('固定核心操作参数无效')
        revision = value['selection_revision']
        if not (revision is None or type(revision) is str and re.fullmatch('[a-f0-9]{64}', revision)):
            raise ValueError('核心选择修订无效')
        if self.action in {'rollback', 'backup', 'restore'} and revision is None:
            raise ValueError('请先检查当前核心选择')
        if self.action in {'update', 'rollback'} and value['core'] not in ('sing-box', 'xray'):
            raise ValueError('核心名称无效')
        if self.action == 'update' and not (type(value['release_id']) is int and 0 < value['release_id'] < 2**63):
            raise ValueError('候选版本编号无效')
        if self.action == 'restore' and not (type(value['backup_task_id']) is str and re.fullmatch('[a-f0-9]{32}', value['backup_task_id'])):
            raise ValueError('备份任务编号无效')
        if self.action == 'recover' and not (
                value['recovery_kind'] in ('switch', 'restore', 'backup')
                and type(value['pending_transaction']) is str and re.fullmatch('[a-f0-9]{32}', value['pending_transaction'])
                and type(value['pending_sequence']) is int and 0 <= value['pending_sequence'] < 2**63):
            raise ValueError('未完成事务参数无效')
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


@router.get('/core-authority')
def core_authority(request: Request):
    if request.query_params:raise HTTPException(422,'核心授权检查不接受额外参数')
    principal=_principal(request)
    auth.authorize(principal,'GET','/api/maintenance/core-authority')
    root_token=request.headers.get('X-VUI-Core-Authorization','')
    if not re.fullmatch(r'vui_cg_[a-f0-9]{32}_[A-Za-z0-9_-]{43}',root_token):
        raise HTTPException(403,'请使用本机终端创建的核心维护链接')
    return _bridge('core_authority_status',root_token,auth.core_binding(principal))


def _core_access(request, method, path):
    if request.query_params:
        raise HTTPException(422, '核心维护不接受额外查询参数')
    principal = _principal(request)
    auth.authorize(principal, method, path, {} if method == 'POST' else None)
    if method == 'POST':
        from app.middleware.auth import expected_origin
        origin = expected_origin(request)
        if origin is None or request.headers.getlist('origin') != [origin]:
            raise HTTPException(403, '核心维护需要准确的同源请求')
    return principal


@router.post('/core-observations', status_code=202)
async def core_observations(request: Request):
    _core_access(request, 'POST', '/api/maintenance/core-observations')
    raw = await request.body()
    try:
        value = json.loads(raw) if raw.strip() else {}
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise HTTPException(422, '核心检查请求无效') from None
    if type(value) is not dict or value:
        raise HTTPException(422, '核心检查不接受调用者参数')
    from starlette.concurrency import run_in_threadpool
    return await run_in_threadpool(_bridge, 'core_observe')


@router.get('/core-tasks')
def core_tasks(request: Request):
    _core_access(request, 'GET', '/api/maintenance/core-tasks')
    return _bridge('core_list')


@router.get('/core-tasks/{task_id}')
def core_task(task_id: str, request: Request):
    _core_access(request, 'GET', '/api/maintenance/core-tasks/' + task_id)
    if not re.fullmatch('[a-f0-9]{32}', task_id):
        raise HTTPException(422, '核心任务编号无效')
    return _bridge('core_get', task_id)


@router.get('/core-requests/{request_id}')
def core_request(request_id: str, request: Request):
    principal = _core_access(request, 'GET', '/api/maintenance/core-requests/' + request_id)
    if not re.fullmatch('[a-f0-9]{32}', request_id):
        raise HTTPException(422, '核心请求编号无效')
    tokens = request.headers.getlist('x-vui-core-authorization')
    if len(tokens) != 1 or not re.fullmatch(r'vui_cg_[a-f0-9]{32}_[A-Za-z0-9_-]{43}', tokens[0]):
        raise HTTPException(403, '请使用本机终端创建的核心维护链接')
    return _bridge('core_lookup', tokens[0], auth.core_binding(principal), request_id)


@router.post('/core-tasks', status_code=202)
def create_core_task(payload: CoreTaskPayload, request: Request):
    principal = _core_access(request, 'POST', '/api/maintenance/core-tasks')
    root_tokens = request.headers.getlist('x-vui-core-authorization')
    nonces = request.headers.getlist('idempotency-key')
    if len(root_tokens) != 1 or not re.fullmatch(r'vui_cg_[a-f0-9]{32}_[A-Za-z0-9_-]{43}', root_tokens[0]):
        raise HTTPException(403, '请使用本机终端创建的核心维护链接')
    if len(nonces) != 1 or not re.fullmatch('[a-f0-9]{32}', nonces[0]):
        raise HTTPException(422, '核心任务需要一次操作对应的唯一请求编号')
    result = _bridge('core_submit', root_tokens[0], auth.core_binding(principal), nonces[0], payload.action, payload.parameters)
    if type(result) is dict and type(result.get('id')) is str and re.fullmatch('[a-f0-9]{32}', result['id']):
        request.scope.setdefault('state', {})['maintenance_task_id'] = result['id']
    return result


@router.get('/core-selection')
def core_selection(request: Request):
    if request.query_params:
        raise HTTPException(422, '核心选择查询不接受额外参数')
    principal = getattr(request.state, 'maintenance', None)
    if principal is None:
        _admin(request)
    else:
        auth.authorize(principal, 'GET', '/api/maintenance/core-selection')
    return _bridge('core_selection_status')


def _core_candidates(core, request: Request):
    if request.query_params:
        raise HTTPException(422, '官方候选查询不接受额外参数')
    principal = getattr(request.state, 'maintenance', None)
    if principal is None:
        _admin(request)
    else:
        auth.authorize(principal, 'GET', '/api/maintenance/core-candidates/' + core)
    from app.services import core_hints
    try:
        return core_hints.discover(core)
    except core_hints.CoreHintsUnavailable as error:
        raise HTTPException(429 if error.code == 'BUSY' else 503,
                            '官方候选信息暂不可用，请稍后重试；核心选择未改变') from None
    except Exception:
        raise HTTPException(503, '官方候选信息暂不可用，请稍后重试；核心选择未改变') from None


@router.get('/core-candidates/sing-box')
def core_candidates_sing_box(request: Request):
    return _core_candidates('sing-box', request)


@router.get('/core-candidates/xray')
def core_candidates_xray(request: Request):
    return _core_candidates('xray', request)


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
