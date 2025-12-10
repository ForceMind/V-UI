import uuid
import subprocess
import json
import os
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List, Optional

router = APIRouter()

# Global Xray Process
xray_process = None
# Mock Database for Inbounds (In real app, use SQLite)
inbounds_db = []

class StreamSettings(BaseModel):
    network: str = "tcp"
    security: str = "none"
    externalProxy: Optional[List[str]] = []
    realitySettings: Optional[dict] = None
    wsSettings: Optional[dict] = None
    tcpSettings: Optional[dict] = None

class Inbound(BaseModel):
    id: Optional[int] = None
    remark: str = ""
    port: int
    protocol: str
    settings: dict
    stream_settings: StreamSettings
    enable: bool = True
    expiry_time: Optional[int] = 0
    total_traffic: Optional[int] = 0
    tag: Optional[str] = None

@router.get("/inbounds")
async def get_inbounds():
    return inbounds_db

@router.post("/inbounds")
async def add_inbound(inbound: Inbound):
    # Auto-generate ID
    inbound.id = int(subprocess.check_output(["date", "+%s"]).strip()) * 1000 + len(inbounds_db)
    
    # Auto-generate UUID for VMess/VLESS if missing
    if inbound.protocol in ["vmess", "vless"]:
        if "clients" not in inbound.settings or not inbound.settings["clients"]:
             inbound.settings["clients"] = [{"id": str(uuid.uuid4()), "alterId": 0}]
        elif not inbound.settings["clients"][0].get("id"):
             inbound.settings["clients"][0]["id"] = str(uuid.uuid4())

    inbounds_db.append(inbound)
    await reload_xray_config()
    return {"message": "Inbound added", "inbound": inbound}

@router.put("/inbounds/{inbound_id}")
async def update_inbound(inbound_id: int, inbound: Inbound):
    for i, item in enumerate(inbounds_db):
        if item.id == inbound_id:
            inbound.id = inbound_id # Ensure ID doesn't change
            inbounds_db[i] = inbound
            await reload_xray_config()
            return {"message": "Inbound updated", "inbound": inbound}
    raise HTTPException(status_code=404, detail="Inbound not found")

@router.delete("/inbounds/{inbound_id}")
async def delete_inbound(inbound_id: int):
    global inbounds_db
    inbounds_db = [i for i in inbounds_db if i.id != inbound_id]
    await reload_xray_config()
    return {"message": "Inbound deleted"}

@router.post("/restart")
async def restart_xray():
    if start_xray_core():
        return {"message": "Xray core restarted successfully"}
    else:
        raise HTTPException(status_code=500, detail="Failed to restart Xray core")

def get_xray_path():
    bin_name = "xray.exe" if os.name == 'nt' else "xray"
    return os.path.join("bin", bin_name)

def start_xray_core():
    global xray_process
    xray_path = get_xray_path()
    config_path = "data/config.json"
    
    if not os.path.exists(xray_path):
        print(f"Error: Xray binary not found at {xray_path}")
        return False

    # Ensure config exists
    if not os.path.exists(config_path):
        # Create a dummy config if missing to allow startup
        pass 

    try:
        stop_xray_core()
        print(f"Starting Xray core: {xray_path} -c {config_path}")
        # Use list for args
        xray_process = subprocess.Popen(
            [xray_path, "run", "-c", config_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        return True
    except Exception as e:
        print(f"Failed to start Xray: {e}")
        return False

def stop_xray_core():
    global xray_process
    if xray_process:
        try:
            xray_process.terminate()
            xray_process.wait(timeout=2)
        except:
            if xray_process:
                xray_process.kill()
        xray_process = None

async def reload_xray_config():
    # Convert Pydantic models to dict for JSON serialization
    inbounds_config = []
    for i in inbounds_db:
        if i.enable:
            ib_dict = i.dict(exclude={'id', 'remark', 'enable', 'expiry_time', 'total_traffic', 'stream_settings'})
            # Map stream_settings to streamSettings (camelCase)
            ib_dict['streamSettings'] = i.stream_settings.dict(exclude_none=True)
            inbounds_config.append(ib_dict)

    config = {
        "log": {"loglevel": "warning"},
        "inbounds": inbounds_config,
        "outbounds": [
            {"protocol": "freedom", "settings": {}},
            {"protocol": "blackhole", "settings": {}, "tag": "blocked"}
        ]
    }
    
    config_path = "data/config.json"
    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)
    
    if xray_process:
        start_xray_core()
    return True

@router.on_event("startup")
async def startup_event():
    start_xray_core()

@router.on_event("shutdown")
async def shutdown_event():
    stop_xray_core()
