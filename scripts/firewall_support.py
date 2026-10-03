"""Conservative local-firewall detection and explicit changes."""
from __future__ import annotations
import re
import shutil
import subprocess

def run(args):
    return subprocess.run(args,capture_output=True,text=True)

def detect() -> dict:
    ufw=shutil.which("ufw")
    if ufw:
        status=run([ufw,"status"])
        if status.returncode==0 and re.search(r"^Status:\s+active\s*$",status.stdout,re.M|re.I):
            return {"backend":"ufw","managed":True,"detail":"UFW active"}
    firewall=shutil.which("firewall-cmd")
    if firewall:
        state=run([firewall,"--state"])
        if state.returncode==0 and state.stdout.strip()=="running":
            zone=run([firewall,"--get-default-zone"]).stdout.strip() or "default"
            return {"backend":"firewalld","managed":True,"detail":"firewalld active","zone":zone}
    nft=shutil.which("nft")
    if nft:
        rules=run([nft,"list","ruleset"])
        if rules.returncode==0 and rules.stdout.strip():
            return {"backend":"nftables","managed":False,"detail":"custom nftables rules detected"}
    iptables=shutil.which("iptables-save")
    if iptables:
        rules=run([iptables])
        if rules.returncode==0 and re.search(r"^-A\s+INPUT\b",rules.stdout,re.M):
            return {"backend":"iptables","managed":False,"detail":"custom iptables rules detected"}
    return {"backend":"none","managed":False,"detail":"no active supported local firewall detected"}

def port_open(info: dict, port: int) -> bool | None:
    backend=info["backend"]
    if backend=="ufw":
        value=run(["ufw","status"])
        return any(re.search(r"(^|\s)"+re.escape(str(port))+r"/tcp\s+ALLOW\b",line,re.I) for line in value.stdout.splitlines())
    if backend=="firewalld":
        value=run(["firewall-cmd","--zone",info["zone"],"--query-port",str(port)+"/tcp"])
        return value.returncode==0
    if backend=="none": return True
    return None

def open_ports(info: dict, ports: list[int]) -> None:
    ports=sorted(set(int(p) for p in ports))
    if info["backend"]=="ufw":
        for port in ports:
            result=subprocess.run(["ufw","allow",str(port)+"/tcp"])
            if result.returncode: raise RuntimeError("UFW refused port "+str(port))
        return
    if info["backend"]=="firewalld":
        zone=info["zone"]
        for port in ports:
            for permanent in (False,True):
                command=["firewall-cmd","--zone",zone]
                if permanent:command.append("--permanent")
                command += ["--add-port",str(port)+"/tcp"]
                if subprocess.run(command).returncode:
                    raise RuntimeError("firewalld refused port "+str(port))
        return
    raise RuntimeError("Firewall backend is not safe for automatic modification")
