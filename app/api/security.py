"""Firewall and host access remain outside the unprivileged release profile."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
router=APIRouter()

class FirewallRule(BaseModel):
    port: int
    action: str
    protocol: str

@router.get('/status')
def firewall_status():
    return {'managed':False,'status':'unavailable','reason':'Host firewall is managed outside V-UI'}

@router.post('/ban_ip')
def ban_ip(ip: str):
    raise HTTPException(501,'Firewall management is not supported; no host rule was changed')

@router.post('/open_port')
def open_port(rule: FirewallRule):
    raise HTTPException(501,'Firewall management is not supported; no host rule was changed')

@router.get('/ssh_log')
def ssh_log():
    raise HTTPException(501,'Host authentication logs are not collected by this release')
