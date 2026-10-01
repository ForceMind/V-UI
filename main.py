from __future__ import annotations

import os
import sys

# New credential/database files should not be group/world readable.
os.umask(0o077)

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.staticfiles import StaticFiles

from app.api import (
    auth,
    cores,
    files,
    inbounds,
    routing,
    security,
    singbox,
    subscription,
    system,
    xray,
)
from app.models.database import DB_PATH, init_db
from app.middleware.auth import AdminAuthMiddleware, configured_origin
from app.services.core_manager import core_manager

app = FastAPI(
    title="V-UI",
    description="Lightweight Xray + sing-box management panel",
    version="0.3.0-alpha.2",
)

# No permissive CORS. The default-deny boundary also covers legacy aliases,
# subscription exports and OpenAPI; alpha.3 will add separate subscription tokens.
app.add_middleware(AdminAuthMiddleware)

app.include_router(system.router, prefix="/api/system", tags=["System"])
app.include_router(inbounds.router, prefix="/api/inbounds", tags=["Inbounds"])
app.include_router(cores.router, prefix="/api/cores", tags=["Cores"])
app.include_router(routing.router, prefix="/api/routing", tags=["Routing"])
app.include_router(subscription.router, prefix="/api/subscription", tags=["Subscription"])
app.include_router(xray.router, prefix="/api/xray", tags=["Xray"])
app.include_router(singbox.router, prefix="/api/singbox", tags=["Sing-box"])
app.include_router(auth.router, prefix="/api/auth", tags=["Auth"])
app.include_router(security.router, prefix="/api/security", tags=["Security"])
app.include_router(files.router, prefix="/api/files", tags=["Files"])


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    if request.url.path.startswith("/api/auth/"):
        # FastAPI's default detail can echo input. Never echo a password payload.
        return JSONResponse({"detail": "Invalid authentication request"}, status_code=422)
    return await request_validation_exception_handler(request, exc)


@app.on_event("startup")
async def startup_event():
    configured_origin()  # Invalid deployment settings fail closed at startup.
    init_db()
    if os.name == "posix" and DB_PATH.is_file():
        DB_PATH.chmod(0o600)


@app.on_event("shutdown")
async def shutdown_event():
    core_manager.stop_all()


def get_web_path() -> str:
    if getattr(sys, "frozen", False):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))

    web_path = os.path.join(base_path, "web")
    if not os.path.exists(web_path):
        os.makedirs(web_path, exist_ok=True)
        with open(os.path.join(web_path, "index.html"), "w", encoding="utf-8") as handle:
            handle.write(
                "<h1>V-UI Panel is running</h1>"
                "<p>Please upload frontend files to the web directory.</p>"
            )
    return web_path


@app.get("/login", include_in_schema=False)
@app.get("/account", include_in_schema=False)
def account_page():
    return FileResponse(os.path.join(get_web_path(), "account.html"), headers={
        "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
    })


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/login", status_code=303)


app.mount("/ui", StaticFiles(directory=get_web_path(), html=True), name="ui")
# Deliberately do not serve uploaded wwwroot files on the administrator origin.
# Existing files remain on disk. Upload/list routes are quarantined by middleware.


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=os.getenv("VUI_HOST", "127.0.0.1"),
                port=int(os.getenv("VUI_PORT", "2053")), proxy_headers=False)
