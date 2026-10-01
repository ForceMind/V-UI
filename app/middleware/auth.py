"""Default-deny ASGI boundary for every management and subscription endpoint."""
from __future__ import annotations

import asyncio
import os
import time
from urllib.parse import urlsplit

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
    _ = parsed.port  # Reject invalid/non-numeric ports at startup.
    return value


def cookie_name() -> str:
    return "__Host-vui_session" if configured_origin().startswith("https://") else "vui_local_session"


def expected_origin(request: Request) -> str | None:
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

        async def secure_send(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                # Applies to error responses as well as successful exports.
                headers += [(b"cache-control", b"no-store"),
                            (b"x-content-type-options", b"nosniff"),
                            (b"referrer-policy", b"no-referrer"),
                            (b"x-frame-options", b"DENY")]
                message = {**message, "headers": headers}
            await send(message)

        async def reject(status, detail):
            await JSONResponse({"detail": detail}, status_code=status)(scope, receive, secure_send)

        if not sensitive:
            await self.app(scope, receive, secure_send)
            return
        origin = expected_origin(request)
        if origin is None:
            await reject(403, "Panel origin is not configured for this request")
            return
        public_login = path == "/api/auth/login" and request.method == "POST"
        if not public_login:
            token = request.cookies.get(cookie_name(), "")
            user = await run_in_threadpool(authenticate, token)
            if user is None:
                if ui_document:
                    await RedirectResponse("/login", status_code=303)(scope, receive, secure_send)
                else:
                    await reject(401, "Authentication required")
                return
            scope.setdefault("state", {})["admin"] = user
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            # A custom header plus exact-origin check protects login and all writes.
            # No wildcard CORS, no query tokens, and SameSite is defense in depth.
            if (request.headers.get("origin") != origin
                    or request.headers.get("x-vui-request") != "1"):
                await reject(403, "Same-origin request header required")
                return
        if path == "/api/files" or path.startswith("/api/files/"):
            await reject(503, "Site hosting is disabled until it has an isolated origin")
            return
        if path.startswith("/api/auth/") and request.method == "POST":
            # Bound actual bytes, not just an attacker-controlled Content-Length.
            body = bytearray()
            try:
                deadline = time.monotonic() + 10
                while True:
                    message = await asyncio.wait_for(receive(), max(0.001, deadline - time.monotonic()))
                    if message["type"] == "http.disconnect":
                        return
                    body.extend(message.get("body", b""))
                    if len(body) > 8192:
                        await reject(413, "Authentication request is too large")
                        return
                    if not message.get("more_body", False):
                        break
            except asyncio.TimeoutError:
                await reject(408, "Authentication request timed out")
                return
            original_receive, delivered = receive, False

            async def body_receive():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": bytes(body), "more_body": False}
                return await original_receive()

            receive = body_receive
        await self.app(scope, receive, secure_send)
