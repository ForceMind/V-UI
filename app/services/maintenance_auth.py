"""Expiring, one-use maintenance grants. Stored authority is hash-only."""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import time

from fastapi import HTTPException
from sqlalchemy import Boolean, Column, Integer, String, Text, text

from app.models import database

SCOPES = frozenset({'diagnose', 'nodes', 'share', 'relay', 'certificates', 'operate', 'upgrade', 'destructive'})
INVALID = '维护授权已失效，请联系管理员重新授权'
TOKEN = re.compile(r'^vui_m[gs]_[A-Za-z0-9_-]{43}$')
DEFAULT_SECONDS = 1800
MAX_SECONDS = 180 * 24 * 60 * 60


class MaintenanceGrant(database.Base):
    __tablename__ = 'maintenance_grants'
    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, nullable=False, index=True)
    token_hash = Column(String(64), nullable=False, unique=True)
    credential_stamp = Column(String(64), nullable=False)
    scopes_json = Column(Text, nullable=False)
    created_at = Column(Integer, nullable=False)
    expires_at = Column(Integer, nullable=False, index=True)
    redeemed_at = Column(Integer, nullable=True)
    revoked = Column(Boolean, nullable=False, default=False)


class MaintenanceSession(database.Base):
    __tablename__ = 'maintenance_sessions'
    token_hash = Column(String(64), primary_key=True)
    grant_id = Column(Integer, nullable=False, index=True)
    expires_at = Column(Integer, nullable=False, index=True)


class MaintenanceBucket(database.Base):
    __tablename__ = 'maintenance_exchange_buckets'
    key = Column(String(64), primary_key=True)
    attempts = Column(Integer, nullable=False)
    resets_at = Column(Integer, nullable=False)


class MaintenanceAudit(database.Base):
    __tablename__ = 'maintenance_audit'
    id = Column(Integer, primary_key=True)
    grant_id = Column(Integer, nullable=True)
    operation = Column(String(96), nullable=False)
    created_at = Column(Integer, nullable=False, index=True)
    status = Column(Integer, nullable=False)
    task_id = Column(String(64), nullable=True)


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def normalize_scopes(values):
    if (type(values) is not list or not values or len(values) > len(SCOPES)
            or any(type(value) is not str or value not in SCOPES for value in values)
            or len(set(values)) != len(values)):
        raise HTTPException(422, '维护权限无效')
    return sorted(values)


def _scopes(grant):
    try:
        return normalize_scopes(json.loads(grant.scopes_json))
    except (ValueError, TypeError, HTTPException):
        return None


def _owner(db, grant):
    user = db.get(database.User, grant.owner_id)
    if (not user or not user.is_active or type(user.password_hash) is not str
            or not user.password_hash.startswith('$argon2id$')
            or type(grant.credential_stamp) is not str
            or not re.fullmatch('[0-9a-f]{64}', grant.credential_stamp)
            or not hmac.compare_digest(grant.credential_stamp, digest(user.password_hash))):
        return None
    return user


def _view(grant):
    return {'id': grant.id, 'scopes': _scopes(grant) or [], 'created_at': grant.created_at,
            'expires_at': grant.expires_at, 'redeemed': grant.redeemed_at is not None,
            'revoked': grant.revoked, 'expired': grant.expires_at <= int(time.time())}


def create_grant(owner_id, scopes=None, expires_seconds=DEFAULT_SECONDS):
    scopes = normalize_scopes(['diagnose'] if scopes is None else scopes)
    if type(expires_seconds) is not int or not 60 <= expires_seconds <= MAX_SECONDS:
        raise HTTPException(422, '有效期须为 60 至 15552000 秒（最长 180 天）')
    now = int(time.time())
    with database.SessionLocal() as db:
        db.execute(text('BEGIN IMMEDIATE'))
        user = db.get(database.User, owner_id)
        if (not user or not user.is_active or type(user.password_hash) is not str
                or not user.password_hash.startswith('$argon2id$')):
            raise HTTPException(401, INVALID)
        # Expired grant/session rows contain no recoverable credential.
        db.query(MaintenanceSession).filter(MaintenanceSession.expires_at <= now).delete()
        db.query(MaintenanceGrant).filter(MaintenanceGrant.expires_at < now - 86400).delete()
        if db.query(MaintenanceGrant).filter(MaintenanceGrant.expires_at > now,
                                           MaintenanceGrant.revoked == False).count() >= 100:  # noqa: E712
            raise HTTPException(429, '有效维护授权数量已达上限')
        token = 'vui_mg_' + secrets.token_urlsafe(32)
        grant = MaintenanceGrant(owner_id=user.id, token_hash=digest(token),
            credential_stamp=digest(user.password_hash), scopes_json=json.dumps(scopes),
            created_at=now, expires_at=now + expires_seconds, revoked=False)
        db.add(grant)
        db.flush()
        result = _view(grant)
        db.commit()
    audit(result['id'], 'grant.create', 201)
    return result, token


def list_grants(owner_id):
    with database.SessionLocal() as db:
        return [_view(row) for row in db.query(MaintenanceGrant).filter_by(owner_id=owner_id)
                .order_by(MaintenanceGrant.id.desc()).limit(200)]


def revoke_grant(owner_id, grant_id):
    with database.SessionLocal() as db:
        db.execute(text('BEGIN IMMEDIATE'))
        row = db.query(MaintenanceGrant).filter_by(id=grant_id, owner_id=owner_id).first()
        if row is None:
            raise HTTPException(404, '维护授权不存在')
        row.revoked = True
        db.query(MaintenanceSession).filter_by(grant_id=row.id).delete()
        value = _view(row)
        db.commit()
    audit(grant_id, 'grant.revoke', 200)
    return value


def take_exchange_budget(address):
    now = int(time.time())
    with database.SessionLocal() as db:
        db.execute(text('BEGIN IMMEDIATE'))
        db.query(MaintenanceBucket).filter(MaintenanceBucket.resets_at <= now).delete()
        for name, limit, window in [('global', 60, 60), ('ip:' + address, 12, 300)]:
            key = digest(name)
            row = db.get(MaintenanceBucket, key)
            if row is not None and row.attempts >= limit:
                raise HTTPException(429, '兑换请求过多，请稍后重试', headers={'Retry-After': str(max(1, row.resets_at - now))})
            if row is None:
                row = MaintenanceBucket(key=key, attempts=0, resets_at=now + window)
                db.add(row)
            row.attempts += 1
        db.commit()


def exchange(token, address):
    take_exchange_budget(address)
    if type(token) is not str or not TOKEN.fullmatch(token) or not token.startswith('vui_mg_'):
        raise HTTPException(401, INVALID)
    now = int(time.time())
    with database.SessionLocal() as db:
        # Serializes concurrent exchanges, including workers in other processes.
        db.execute(text('BEGIN IMMEDIATE'))
        grant = db.query(MaintenanceGrant).filter_by(token_hash=digest(token)).first()
        if (grant is None or grant.revoked or grant.redeemed_at is not None
                or type(grant.expires_at) is not int or grant.expires_at <= now
                or not _owner(db, grant) or not _scopes(grant)):
            raise HTTPException(401, INVALID)
        session = 'vui_ms_' + secrets.token_urlsafe(32)
        grant.redeemed_at = now
        db.add(MaintenanceSession(token_hash=digest(session), grant_id=grant.id,
                                  expires_at=grant.expires_at))
        result = _view(grant)
        db.commit()
    audit(result['id'], 'exchange', 200)
    return {**result, 'token': session, 'token_type': 'Bearer'}


def authenticate(token):
    if type(token) is not str or not TOKEN.fullmatch(token) or not token.startswith('vui_ms_'):
        return None
    now = int(time.time())
    with database.SessionLocal() as db:
        session = db.get(MaintenanceSession, digest(token))
        if session is None or type(session.expires_at) is not int or session.expires_at <= now:
            return None
        grant = db.get(MaintenanceGrant, session.grant_id)
        if (grant is None or grant.revoked or type(grant.expires_at) is not int
                or grant.expires_at <= now or type(grant.redeemed_at) is not int
                or not _owner(db, grant)):
            return None
        scopes = _scopes(grant)
        if not scopes:
            return None
        return {'grant_id': grant.id, 'owner_id': grant.owner_id,
                'scopes': scopes, 'expires_at': min(session.expires_at, grant.expires_at)}


# Explicit route templates; never grant an API namespace by prefix.
# revision denotes the existing authoritative precondition the client must send.
CAPABILITIES = []


def _cap(method, path, scope, purpose, revision=None):
    CAPABILITIES.append({'method': method, 'path': path, 'scope': scope,
                         'purpose': purpose, 'revision': revision})


for path, purpose in [('/api/maintenance/session', '当前维护权限'), ('/api/maintenance/catalog', '维护操作目录'),
        ('/api/maintenance/audit', '脱敏审计'), ('/api/maintenance/tasks', '维护任务列表'),
        ('/api/maintenance/tasks/{task_id}', '维护任务状态'), ('/api/maintenance/status', '固定服务状态'), ('/api/maintenance/logs', '固定服务脱敏日志分类摘要'),
        ('/api/system/status', '系统资源状态'), ('/api/cores/status', '核心状态'),
        ('/api/inbounds', '节点公开摘要'), ('/api/inbounds/profiles', '协议资格'),
        ('/api/proxy/capabilities', '中转能力'), ('/api/proxy/settings', '代理与中转状态'),
        ('/api/proxy/diagnostics/{job_id}', '诊断结果'), ('/api/certificates', '证书摘要'),
        ('/api/certificates/capabilities', '证书能力'), ('/api/certificates/jobs/{job_id}', '证书任务'),
        ('/api/routing/mihomo', '路由状态'), ('/api/routing/mihomo/snapshot', '路由修订'),
        ('/api/routing/mihomo/catalog', '路由目录')]:
    _cap('GET', path, 'diagnose', purpose)
_cap('POST', '/api/proxy/diagnostics', 'diagnose', '启动有界脱敏诊断', 'base_revision')
_cap('DELETE', '/api/proxy/diagnostics/{job_id}', 'diagnose', '取消诊断任务')
_cap('GET', '/api/inbounds/{inbound_id}/editor', 'nodes', '特权节点编辑读取')
_cap('POST', '/api/inbounds', 'nodes', '创建并应用节点；仅 profile 输入')
_cap('PUT', '/api/inbounds/{inbound_id}', 'nodes', '修改启停并应用节点', 'expected_instance_revision')
_cap('POST', '/api/inbounds/{inbound_id}/delete', 'destructive', '删除准确节点实例', 'expected_instance_revision')
for path in ['/raw', '/mihomo.yaml', '/mihomo-warnings', '/sing-box.json', '/link/{inbound_id}']:
    _cap('GET', '/api/subscription' + path, 'share', '显式分享导出')
_cap('POST', '/api/subscription/upstream/{inbound_id}', 'share', '仅 TCP 中转上游导出')
_cap('GET', '/api/subscriptions', 'share', '订阅授权摘要')
_cap('POST', '/api/subscriptions', 'share', '创建订阅', 'expected_node_revision')
_cap('POST', '/api/subscriptions/{grant_id}/rotate', 'share', '轮换订阅凭据')
_cap('DELETE', '/api/subscriptions/{grant_id}', 'destructive', '撤销订阅')
for method, path, revision in [('POST', '/import/preview', None), ('POST', '/profiles', 'If-Match'),
        ('PUT', '/settings', 'If-Match'), ('POST', '/check', 'base_revision'), ('POST', '/apply', 'base_revision')]:
    _cap(method, '/api/proxy' + path, 'relay', '保存检查或应用代理中转策略', revision)
_cap('PUT', '/api/routing/mihomo', 'relay', '保存路由规则', 'If-Match')
for action in ['apply', 'restart', 'stop']:
    _cap('POST', '/api/cores/{core}/' + action, 'nodes', '固定核心操作')
for method, path in [('POST', '/{certificate_id}/renew'), ('PUT', '/{certificate_id}/auto-renew'),
        ('POST', '/{certificate_id}/bind-panel'), ('POST', '/{certificate_id}/apply')]:
    _cap(method, '/api/certificates' + path, 'certificates', '已有证书续期绑定和重试')
_cap('POST', '/api/certificates', 'certificates', '首次 CA 条款需管理员授权；此入口固定拒绝新申请')
_cap('POST', '/api/maintenance/tasks', 'operate', '固定系统任务；status需diagnose，upgrade需upgrade，restore需destructive和成功备份ID')


def capability(method, path):
    kinds = {'inbound_id': '[1-9][0-9]{0,18}', 'grant_id': '[1-9][0-9]{0,18}',
             'core': '(?:xray|sing-box)', 'certificate_id': '[0-9a-f]{32}',
             'job_id': '(?:diag_)?[0-9a-f]{32}', 'task_id': '[0-9a-f]{32}'}
    for item in CAPABILITIES:
        pattern = re.escape(item['path'])
        for key, value in kinds.items():
            pattern = pattern.replace(r'\{' + key + r'\}', value)
        if item['method'] == method and re.fullmatch(pattern, path):
            return item
    return None


def _allowed_scopes(item):
    if item['path'] in {'/api/maintenance/session', '/api/maintenance/catalog'}:
        return sorted(SCOPES)
    if item['path'] in {'/api/maintenance/tasks', '/api/maintenance/tasks/{task_id}', '/api/maintenance/status'}:
        return ['diagnose', 'operate', 'upgrade', 'destructive']
    return [item['scope']]


def catalog(principal):
    scopes = set(principal['scopes'])
    return [{**item, 'any_scopes': _allowed_scopes(item)} for item in CAPABILITIES
            if scopes.intersection(_allowed_scopes(item))]


def authorize(principal, method, path, payload=None):
    item = capability(method, path)
    scopes = set(principal['scopes'])
    if item is None or not scopes.intersection(_allowed_scopes(item)):
        raise HTTPException(403, '此维护授权不允许该操作')
    if method in {'POST', 'PUT', 'PATCH', 'DELETE'}:
        if type(payload) is not dict:
            raise HTTPException(422, '维护请求格式无效')
        if path.startswith('/api/inbounds'):
            if 'routing_mode' in payload and 'relay' not in scopes:
                raise HTTPException(403, '修改入口出口需要中转权限')
            if 'certificate_id' in payload and 'certificates' not in scopes:
                raise HTTPException(403, '绑定证书需要证书权限')
            if any(key in payload for key in ('settings', 'stream_settings', 'user_id')):
                raise HTTPException(403, '远程维护仅允许结构化节点配置')
            profile = payload.get('profile')
            if profile is not None and (type(profile) is not dict or any(key in profile for key in (
                    'certificate_path', 'key_path', 'certificateFile', 'keyFile'))):
                raise HTTPException(403, '远程维护不能指定文件材料路径')
            if method == 'PUT' or path.endswith('/delete'):
                if type(payload.get('expected_instance_revision')) is not str or not re.fullmatch('[0-9a-f]{64}', payload['expected_instance_revision']):
                    raise HTTPException(428, '请先刷新节点并携带实例修订')
        if path == '/api/subscriptions' and method == 'POST':
            if type(payload.get('expected_node_revision')) is not str or not re.fullmatch('[0-9a-f]{64}', payload['expected_node_revision']):
                raise HTTPException(428, '请先刷新节点并携带完整列表修订')
        if path == '/api/certificates' and method == 'POST':
            raise HTTPException(409, '新证书须由管理员首次授权 CA 条款；维护会话可续期已有证书')
        if path == '/api/maintenance/tasks' and method == 'POST':
            action = payload.get('action')
            needed = ('diagnose' if action == 'status' else 'upgrade' if action == 'upgrade'
                      else 'destructive' if action == 'restore' else 'operate')
            if needed not in scopes:
                raise HTTPException(403, '此维护授权不允许该系统任务')
    return item


def audit(grant_id, operation, status, task_id=None):
    # Only server-defined operation templates; never persist a URL or user body.
    allowed = {'grant.create', 'grant.revoke', 'exchange'} | {
        item['method'] + ' ' + item['path'] for item in CAPABILITIES}
    if operation not in allowed:
        operation = 'denied'
    if task_id is not None and (type(task_id) is not str or not re.fullmatch('[0-9a-f]{32}', task_id)):
        task_id = None
    if type(grant_id) is not int or grant_id <= 0:
        grant_id = None
    if type(status) is not int or not 100 <= status <= 599:
        status = 500
    now = int(time.time())
    with database.SessionLocal() as db:
        db.execute(text('BEGIN IMMEDIATE'))
        db.add(MaintenanceAudit(grant_id=grant_id, operation=operation,
            created_at=now, status=status, task_id=task_id))
        db.flush()
        cutoff = db.query(MaintenanceAudit.id).order_by(MaintenanceAudit.id.desc()).offset(1999).limit(1).scalar()
        if cutoff is not None:
            db.query(MaintenanceAudit).filter(MaintenanceAudit.id < cutoff).delete(synchronize_session=False)
        db.query(MaintenanceAudit).filter(MaintenanceAudit.created_at < now - 7 * 86400).delete(synchronize_session=False)
        db.commit()


def audit_rows(principal=None):
    with database.SessionLocal() as db:
        rows = db.query(MaintenanceAudit)
        if principal is not None:
            rows = rows.filter_by(grant_id=principal['grant_id'])
        return [{'id': row.id, 'grant_id': row.grant_id, 'operation': row.operation,
                 'created_at': row.created_at, 'status': row.status, 'task_id': row.task_id}
                for row in rows.order_by(MaintenanceAudit.id.desc()).limit(200)]
