from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional
import json
import os
import subprocess

router = APIRouter()

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
    # In a real environment, this would use systemctl or supervisor
    # subprocess.run(["systemctl", "restart", "xray"])
    return {"message": "Xray core restart command sent"}

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
    
    # Save to file (Mock path)
    config_path = "data/config.json"
    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)
    
    print("Config regenerated at", config_path)
    return True
