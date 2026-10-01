from __future__ import annotations

import os
import sys

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
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
from app.models.database import init_db
from app.services.core_manager import core_manager

app = FastAPI(
    title="V-UI",
    description="Lightweight Xray + sing-box management panel",
    version="0.3.0-alpha.1",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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


@app.on_event("startup")
async def startup_event():
    init_db()


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


app.mount("/ui", StaticFiles(directory=get_web_path(), html=True), name="ui")

www_root = "wwwroot"
if not os.path.exists(www_root):
    os.makedirs(www_root)
    with open(os.path.join(www_root, "index.html"), "w", encoding="utf-8") as handle:
        handle.write("<h1>Welcome</h1>")

app.mount("/", StaticFiles(directory=www_root, html=True), name="site")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=2053)
