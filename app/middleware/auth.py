"""Default-deny ASGI boundary for every management and subscription endpoint."""
from __future__ import annotations

import asyncio
import os
import json
import time
from urllib.parse import urlsplit

from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse

from app.services.auth_service import authenticate

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def configured_origin() -> str:
    value = os.getenv("VUI_PUBLIC_ORIGIN", "").rstrip("/")
    if not value:
        return ""
    parsed = urlsplit(value)
    if (not parsed.hostname or parsed.username or parsed.password or parsed.path
            or parsed.query or parsed.fragment or parsed.scheme not in {"http", "https"}):
        raise ValueError("VUI_PUBLIC_ORIGIN must be an origin, without path or credentials")
    if parsed.scheme == "http" and parsed.hostname not in LOCAL_HOSTS:
        raise ValueError("A non-loopback panel origin must use HTTPS")
    port = parsed.port  # Reject invalid/non-numeric ports at startup.
    # Browsers serialize origins with lowercase scheme/host and no default port.
    # Canonicalization must also precede the HTTPS/Secure-cookie decision.
    host = parsed.hostname
    authority = f"[{host}]" if ":" in host else host.encode("idna").decode("ascii")
    if port is not None and port != (443 if parsed.scheme == "https" else 80):
        authority += f":{port}"
    return f"{parsed.scheme}://{authority}"


def cookie_name() -> str:
    return "__Host-vui_session" if configured_origin().startswith("https://") else "vui_local_session"


def expected_origin(request: Request) -> str | None:
    if len(request.headers.getlist('host')) != 1:
        return None
    configured = configured_origin()
    if configured:
        if request.headers.get("host", "").lower() != urlsplit(configured).netloc.lower():
            return None
        return configured
    # Unconfigured installations only work over a loopback connection/SSH tunnel.
    if (request.url.hostname not in LOCAL_HOSTS or not request.client
            or request.client.host not in LOCAL_HOSTS):
        return None
    return f"{request.url.scheme}://{request.url.netloc}"


class AdminAuthMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        path = scope["path"].rstrip("/")
        ui_document = path in {"/ui", "/ui/index.html"}
        sensitive = ui_document or path == "/api" or path.startswith("/api/") or path in {
            "/docs", "/docs/oauth2-redirect", "/openapi.json", "/redoc"}
        principal = None
        operation = "denied"
        error_body = None
        error_delivered = False
        response_started = False

        async def secure_send(message):
            nonlocal error_body, error_delivered, response_started
            if message["type"] == "http.response.start":
                response_started = True
                headers = list(message.get("headers", []))
                if principal is not None:
                    from app.services.maintenance_auth import audit
                    # Audit has a strict fixed-field schema, with bounded retention.
                    await run_in_threadpool(audit, principal['grant_id'], operation, message['status'],
                                            scope.get('state', {}).get('maintenance_task_id'))
                    if message['status'] >= 400:
                        detail = {401: '维护授权已失效，请联系管理员重新授权',
                                  403: '此维护授权不允许该操作',
                                  404: '请求的维护对象不存在',
                                  409: '配置或状态冲突，请刷新并核对协议、证书和应用资格',
                                  428: '请先刷新对象并携带所需修订',
                                  429: '请求过多或任务处理中，请稍后重试',
                                  503: '维护组件暂不可用，请稍后重试或使用终端恢复'}.get(
                                      message['status'], '维护请求无效，请核对操作目录与输入')
                        if path == '/api/certificates' and request.method == 'POST' and message['status'] == 409:
                            detail = '新证书须由管理员首次授权 CA 条款；维护会话可续期已有证书'
                        error_body = json.dumps({'detail': detail}, ensure_ascii=False).encode()
                        headers = [(k, v) for k, v in headers if k.lower() not in {b'content-length', b'content-type'}]
                        headers += [(b'content-type', b'application/json'),
                                    (b'content-length', str(len(error_body)).encode())]
                headers += [(b"cache-control", b"no-store"),
                            (b"x-content-type-options", b"nosniff"),
                            (b"referrer-policy", b"no-referrer"),
                            (b"x-frame-options", b"DENY")]
                message = {**message, "headers": headers}
            elif message["type"] == "http.response.body" and error_body is not None:
                if error_delivered:
                    return
                error_delivered = True
                message = {"type": "http.response.body", "body": error_body, "more_body": False}
            await send(message)

        async def reject(status, detail, headers=None):
            await JSONResponse({"detail": detail}, status_code=status, headers=headers)(scope, receive, secure_send)

        if not sensitive:
            await self.app(scope, receive, secure_send)
            return
        origin = expected_origin(request)
        if origin is None:
            await reject(403, "Panel origin is not configured for this request")
            return
        public_login = path == "/api/auth/login" and request.method == "POST"
        public_exchange = path == "/api/maintenance/exchange" and request.method == "POST"
        authorization = request.headers.get("authorization")
        if public_exchange:
            # A duplicate bearer must never turn the exchange into another API.
            if authorization is not None or request.query_params:
                await reject(422, "维护兑换请求格式无效")
                return
        elif authorization is not None:
            from app.services import maintenance_auth
            invalid_detail = (maintenance_auth.INVALID if authorization.startswith(
                ('Bearer vui_ms_', 'Bearer vui_mg_')) else 'Authentication required')
            if (len(request.headers.getlist('authorization')) != 1
                    or not authorization.startswith('Bearer ')):
                await reject(401, invalid_detail)
                return
            principal = await run_in_threadpool(maintenance_auth.authenticate, authorization[7:])
            if principal is None:
                await reject(401, invalid_detail)
                return
            # Never fall back to an administrator cookie when Bearer is present.
            scope.setdefault('state', {})['maintenance'] = principal
            # Existing owner-scoped business services require an owner id. This
            # is never used as the middleware/admin-grants authorization source.
            scope['state']['admin'] = {'id': principal['owner_id'], 'role': 'maintenance'}
            entry = maintenance_auth.capability(request.method, path)
            operation = entry['method'] + ' ' + entry['path'] if entry else 'denied'
            if (request.headers.get('x-vui-request') != '1'
                    or len(request.headers.getlist('x-vui-request')) != 1
                    or (request.headers.get('origin') is not None and request.headers.get('origin') != origin)
                    or len(request.headers.getlist('origin')) > 1):
                await reject(403, '维护请求来源无效')
                return
            # Keep revision queries confined to the existing single-link GET
            # capability; other exports retain only their host/core selectors.
            allowed_query = {'core'} if path == '/api/inbounds' else {'core', 'host'} if path.startswith('/api/subscription/') else set()
            if entry is not None and entry['method'] == 'GET' and entry['path'] == '/api/subscription/link/{inbound_id}':
                allowed_query |= {'expected_instance_revision', 'expected_content_revision'}
            if (any(key not in allowed_query for key in request.query_params)
                    or any(len(request.query_params.getlist(key)) != 1 for key in request.query_params)):
                await reject(422, '维护请求不能使用 query 凭据或未知参数')
                return
        elif not public_login:
            token = request.cookies.get(cookie_name(), "")
            user = await run_in_threadpool(authenticate, token)
            if user is None:
                if ui_document:
                    await RedirectResponse("/login", status_code=303)(scope, receive, secure_send)
                else:
                    await reject(401, "Authentication required")
                return
            scope.setdefault("state", {})["admin"] = user
        if principal is None and request.method not in {"GET", "HEAD", "OPTIONS"}:
            # Cookie requests always require browser CSRF, even without Origin.
            # Exchange is explicit one-use JSON authority, never cookie authority.
            exchange_client = public_exchange and request.headers.get('origin') is None
            if ((not exchange_client and request.headers.get("origin") != origin)
                    or request.headers.get("x-vui-request") != "1"
                    or len(request.headers.getlist('origin')) > 1):
                await reject(403, "Same-origin request header required")
                return
        if path == "/api/files" or path.startswith("/api/files/"):
            await reject(403 if principal is not None else 503,
                         "Site hosting is disabled until it has an isolated origin")
            return
        bounded_auth = path.startswith("/api/auth/") and request.method == "POST"
        bounded_proxy = path.startswith("/api/proxy/") and request.method in {"POST", "PUT", "PATCH"}
        bounded_maintenance = path.startswith('/api/maintenance/') and request.method in {'POST', 'PUT', 'PATCH', 'DELETE'}
        bounded_dns = path.startswith('/api/certificates') and request.method in {'POST', 'PUT', 'PATCH', 'DELETE'}
        payload = {}
        if bounded_auth or bounded_proxy or bounded_maintenance or bounded_dns or principal is not None:
            byte_limit = 2_097_152 if bounded_proxy else 65_536 if principal is not None else 8192
            if principal is not None and request.method in {'GET', 'HEAD', 'OPTIONS'}:
                byte_limit = 0
            body = bytearray()
            try:
                deadline = time.monotonic() + 10
                while True:
                    message = await asyncio.wait_for(receive(), max(0.001, deadline - time.monotonic()))
                    if message["type"] == "http.disconnect":
                        return
                    chunk = message.get("body", b"")
                    if len(body) + len(chunk) > byte_limit:
                        await reject(413, "Request is too large")
                        return
                    body.extend(chunk)
                    if not message.get("more_body", False):
                        break
            except asyncio.TimeoutError:
                await reject(408, "Request timed out")
                return
            if principal is not None or bounded_maintenance or bounded_dns:
                def unique_object(pairs):
                    result = {}
                    for key, value in pairs:
                        if key in result:
                            raise ValueError()
                        result[key] = value
                    return result
                try:
                    if body:
                        if request.headers.get('content-type', '').split(';', 1)[0].strip().lower() != 'application/json':
                            raise ValueError()
                        payload = json.loads(bytes(body), object_pairs_hook=unique_object,
                            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                    if type(payload) is not dict:
                        raise ValueError()
                except (ValueError, TypeError, UnicodeError, RecursionError):
                    await reject(422, '维护请求格式无效')
                    return
            original_receive, delivered = receive, False

            async def body_receive():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": bytes(body), "more_body": False}
                return await original_receive()

            receive = body_receive
        if principal is not None:
            from app.services.maintenance_auth import authorize
            try:
                authorize(principal, request.method, path, payload)
            except HTTPException as exc:
                await reject(exc.status_code, exc.detail, exc.headers)
                return
        try:
            await self.app(scope, receive, secure_send)
        except Exception:
            if principal is None or response_started:
                raise
            # Suppress exception text from both responses and server trace logs.
            await reject(503, '维护请求暂不可用')
