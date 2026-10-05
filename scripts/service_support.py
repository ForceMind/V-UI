"""Generate and control V-UI services for systemd or OpenRC."""
from __future__ import annotations
from pathlib import Path
import subprocess

SYSTEMD_DIR=Path("/etc/systemd/system")
OPENRC_DIR=Path("/etc/init.d")
MARKER="# Managed by V-UI guarded installer v2\n"

def service_paths(manager: str) -> dict:
    if manager=="systemd":
        return {name:SYSTEMD_DIR/name for name in ("v-ui.service","v-ui-http01.service","v-ui-http01.socket")}
    if manager=="openrc":
        return {name:OPENRC_DIR/name for name in ("v-ui","v-ui-http01")}
    raise RuntimeError("Unsupported service manager")

def service_files(config: dict) -> dict:
    manager=config["service_manager"]
    python=config["bootstrap_python"]
    prefix=python+" -B /usr/local/lib/v-ui/launcher.py "
    if manager=="systemd":
        common=("User=v-ui\nGroup=v-ui\nUMask=0077\nNoNewPrivileges=true\n"
                "ProtectSystem=strict\nProtectHome=true\nPrivateTmp=true\nPrivateDevices=true\n"
                "CapabilityBoundingSet=\nRestrictSUIDSGID=true\nRestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX\n")
        panel=(MARKER+"[Unit]\nDescription=V-UI managed HTTPS panel\nAfter=network-online.target\nWants=network-online.target\n\n"
               "[Service]\nType=simple\n"+common+"WorkingDirectory=/var/lib/v-ui\nReadWritePaths=/var/lib/v-ui\n"
               "ExecStart="+prefix+"panel\nRestart=on-failure\nRestartSec=5\nTimeoutStopSec=30\n\n"
               "[Install]\nWantedBy=multi-user.target\n")
        challenge=(MARKER+"[Unit]\nDescription=V-UI HTTP-01 challenge responder\nRequires=v-ui-http01.socket\n\n"
                   "[Service]\nType=simple\n"+common+"WorkingDirectory=/var/lib/v-ui\n"
                   "ExecStart="+prefix+"http01\nTimeoutStopSec=15\n")
        socket=(MARKER+"[Unit]\nDescription=V-UI ACME validation socket (not panel HTTP)\n\n"
                "[Socket]\nListenStream=0.0.0.0:80\n"+
                ("ListenStream=[::]:80\nBindIPv6Only=ipv6-only\n" if config.get("ipv6") else "")+
                "Accept=no\nService=v-ui-http01.service\n\n[Install]\nWantedBy=sockets.target\n")
        return {
            SYSTEMD_DIR/"v-ui.service":(panel,0o644),
            SYSTEMD_DIR/"v-ui-http01.service":(challenge,0o644),
            SYSTEMD_DIR/"v-ui-http01.socket":(socket,0o644),
        }
    if manager=="openrc":
        panel=(MARKER+"#!/sbin/openrc-run\ndescription=\"V-UI managed HTTPS panel\"\n"
               "supervisor=supervise-daemon\ncommand=\""+python+"\"\n"
               "command_args=\"-B /usr/local/lib/v-ui/launcher.py panel\"\ncommand_user=\"v-ui:v-ui\"\n"
               "directory=\"/var/lib/v-ui\"\numask=0077\nno_new_privs=yes\nrespawn_delay=5\n"
               "depend() { need net; }\n")
        challenge=(MARKER+"#!/sbin/openrc-run\ndescription=\"V-UI HTTP-01 challenge responder\"\n"
                   "supervisor=supervise-daemon\ncommand=\""+python+"\"\n"
                   "command_args=\"-B /usr/local/lib/v-ui/launcher.py http01-direct\"\ncommand_user=\"v-ui:v-ui\"\n"
                   "directory=\"/var/lib/v-ui\"\numask=0077\nno_new_privs=yes\n"
                   "capabilities=\"^cap_net_bind_service\"\nrespawn_delay=5\n"
                   "depend() { need net; }\n")
        return {OPENRC_DIR/"v-ui":(panel,0o755),OPENRC_DIR/"v-ui-http01":(challenge,0o755)}
    raise RuntimeError("Unsupported service manager")

def reload(manager: str) -> None:
    if manager=="systemd": subprocess.run(["systemctl","daemon-reload"],check=True)

def stop(manager: str, names: list[str]) -> None:
    if not names:return
    if manager=="systemd":
        subprocess.run(["systemctl","stop",*names],check=True)
    else:
        for name in names: subprocess.run(["rc-service",name,"stop"],check=False)

def enable_start(manager: str, names: list[str]) -> None:
    if manager=="systemd":
        subprocess.run(["systemctl","enable","--now",*names],check=True)
    else:
        for name in names:
            subprocess.run(["rc-update","add",name,"default"],check=True)
            subprocess.run(["rc-service",name,"start"],check=True)

def start(manager: str, names: list[str]) -> None:
    if manager=="systemd": subprocess.run(["systemctl","start",*names],check=True)
    else:
        for name in names: subprocess.run(["rc-service",name,"start"],check=True)

def is_active(manager: str, name: str) -> bool:
    if manager=="systemd":
        return subprocess.run(["systemctl","is-active","--quiet",name]).returncode==0
    return subprocess.run(["rc-service",name,"status"],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0

def is_enabled(manager: str, name: str) -> bool:
    if manager=="systemd":
        return subprocess.run(["systemctl","is-enabled","--quiet",name]).returncode==0
    result=subprocess.run(["rc-update","show","default"],capture_output=True,text=True)
    return result.returncode==0 and any(line.split() and line.split()[0]==name for line in result.stdout.splitlines())
