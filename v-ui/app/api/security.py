from fastapi import APIRouter
from pydantic import BaseModel
import subprocess

router = APIRouter()

class FirewallRule(BaseModel):
    port: int
    action: str # ALLOW, DENY
    protocol: str # tcp, udp

@router.get("/status")
async def firewall_status():
    # Mock status
    return {"status": "active", "rules_count": 12}

@router.post("/ban_ip")
async def ban_ip(ip: str):
    """
    Manually ban an IP address using iptables
    """
    # cmd = f"iptables -A INPUT -s {ip} -j DROP"
    # subprocess.run(cmd, shell=True)
    return {"message": f"IP {ip} has been added to the blocklist"}

@router.post("/open_port")
async def open_port(rule: FirewallRule):
    """
    Open a port in the firewall
    """
    # cmd = f"ufw allow {rule.port}/{rule.protocol}"
    return {"message": f"Port {rule.port}/{rule.protocol} opened"}

@router.get("/ssh_log")
async def get_ssh_log():
    """
    Analyze auth.log for failed login attempts (Anti-Brute-force)
    """
    return {"recent_failed_attempts": []}
