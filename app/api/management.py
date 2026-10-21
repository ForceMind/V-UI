"""Separate authenticated read-only management observations."""
import asyncio

from fastapi import APIRouter, HTTPException, Request

from app.services.tailscale_status import status

router = APIRouter()


@router.get("/tailscale")
async def tailscale_status(request: Request):
    # No caller-controlled command, target, refresh override, or body.
    if request.url.query:
        raise HTTPException(400, "This observation accepts no parameters")
    if request.headers.get("transfer-encoding") or request.headers.get("content-length", "0") != "0":
        raise HTTPException(400, "This observation accepts no body")
    try:
        message = await asyncio.wait_for(request.receive(), timeout=1.0)
    except TimeoutError:
        raise HTTPException(400, "This observation accepts no body") from None
    if message.get("body") or message.get("more_body") or message["type"] != "http.request":
        raise HTTPException(400, "This observation accepts no body")
    from starlette.concurrency import run_in_threadpool
    return await run_in_threadpool(status.read)
