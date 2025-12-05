from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional
import json
import os
import subprocess
import signal
import sys

router = APIRouter()

# Global Xray Process
xray_process = None

# Mock Database for Inbounds
inbounds_db = []

class Inbound(BaseModel):
    id: int
    remark: str
    port: int
    protocol: str
    settings: dict
    stream_settings: dict
    enable: bool = True
    expiry_time: Optional[int] = 0
    total_traffic: Optional[int] = 0

def get_xray_path():
    # Determine binary path based on OS
    bin_name = "xray.exe" if os.name == 'nt' else "xray"
    return os.path.join("bin", bin_name)

def start_xray_core():
    global xray_process
    xray_path = get_xray_path()
    config_path = "data/config.json"
    
    if not os.path.exists(xray_path):
        print(f"Error: Xray binary not found at {xray_path}")
        return False

    if not os.path.exists(config_path):
        print(f"Error: Config not found at {config_path}")
        return False

    try:
        # Stop existing if any
        stop_xray_core()
        
        print(f"Starting Xray core: {xray_path} -c {config_path}")
        xray_process = subprocess.Popen(
            [xray_path, "run", "-c", config_path],
            stdout=subprocess.DEVNULL, # Redirect logs to avoid clutter, or handle properly
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

@router.on_event("startup")
async def startup_event():
    # Generate initial config if needed and start core
    if not os.path.exists("data/config.json"):
        await reload_xray_config()
    start_xray_core()

@router.on_event("shutdown")
async def shutdown_event():
    stop_xray_core()

@router.get("/inbounds")
async def get_inbounds():
    return inbounds_db

@router.post("/inbounds")
async def add_inbound(inbound: Inbound):
    inbounds_db.append(inbound)
    # Trigger config reload
    await reload_xray_config()
    return {"message": "Inbound added", "inbound": inbound}

@router.post("/restart")
async def restart_xray():
    """
    Restart the Xray core service
    """
    if start_xray_core():
        return {"message": "Xray core restarted successfully"}
    else:
        raise HTTPException(status_code=500, detail="Failed to restart Xray core")

async def reload_xray_config():
    """
    Generate config.json from database and reload core
    """
    config = {
        "log": {
            "loglevel": "warning"
        },
        "inbounds": [i.dict(exclude={'id', 'remark', 'enable', 'expiry_time', 'total_traffic'}) for i in inbounds_db if i.enable],
        "outbounds": [
            {
                "protocol": "freedom",
                "settings": {}
            },
            {
                "protocol": "blackhole",
                "settings": {},
                "tag": "blocked"
            }
        ]
    }
    
    # Save to file
    config_path = "data/config.json"
    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)
    
    print("Config regenerated at", config_path)
    
    # Auto restart if process is running
    if xray_process:
        start_xray_core()
        
    return True
