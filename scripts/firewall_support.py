"""Conservative local-firewall detection and explicit changes."""
from __future__ import annotations
import re
import shutil
import subprocess

def run(args):
    return subprocess.run(args,capture_output=True,text=True)

def active_firewalld_zone(output: str) -> str | None:
    """Only one interface-bound zone with no source-specific routing is safe.

    The default zone is not evidence of where inbound traffic arrives. Multiple
    active zones, source bindings and unknown output need operator knowledge;
    never guess which interface/source carries the user's public traffic.
    """
    zones = {}
    current = None
    for line in output.splitlines():
        if not line.strip():
            continue
        if not line[0].isspace():
            name = line.strip()
            if not re.fullmatch(r"[A-Za-z0-9_-]+", name) or name in zones:
                return None
            current = name
            zones[current] = set()
        else:
            match = re.fullmatch(r"\s+(interfaces|sources):\s*(.*)", line)
            if current is None or match is None:
                return None
            kind, values = match.groups()
            if kind in zones[current]:
                return None
            if kind == "sources" and values.strip():
                return None
            if kind == "interfaces" and values.strip():
                zones[current].add(kind)
    if len(zones) != 1:
        return None
    zone, bindings = next(iter(zones.items()))
    return zone if "interfaces" in bindings else None


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
            active=run([firewall,"--get-active-zones"])
            zone=active_firewalld_zone(active.stdout) if active.returncode==0 else None
            if zone is None:
                return {"backend":"firewalld","managed":False,
                        "detail":"firewalld ingress zone is ambiguous or unavailable; manual configuration required"}
            return {"backend":"firewalld","managed":True,
                    "detail":"firewalld active interface zone: "+zone,"zone":zone}
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
        if not info.get("managed") or not info.get("zone"):
            return None
        value=run(["firewall-cmd","--zone",info["zone"],"--query-port",str(port)+"/tcp"])
        if value.returncode==0 and value.stdout.strip()=="yes": return True
        if value.returncode==1 and value.stdout.strip()=="no": return False
        return None
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
        if not info.get("managed") or not info.get("zone"):
            raise RuntimeError("Firewalld ingress zone requires manual configuration")
        # Confirmation may take time. Refuse to apply the approved ports to a
        # different (or now ambiguous) zone if bindings changed in the meantime.
        current=detect()
        if (current.get("backend")!="firewalld" or not current.get("managed")
                or current.get("zone")!=info["zone"]):
            raise RuntimeError("Firewalld ingress zone changed; rerun preflight or configure it manually")
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
