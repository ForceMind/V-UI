"""Linux platform detection shared by installer tests and release staging."""
from __future__ import annotations
import os
from pathlib import Path
import platform
import shutil
import subprocess

ARCHES={"x86_64":"x86_64","amd64":"x86_64","aarch64":"aarch64","arm64":"aarch64"}

def read_os_release(path: Path=Path("/etc/os-release")) -> dict:
    result={}
    try:
        for line in path.read_text(errors="replace").splitlines():
            if "=" not in line or line.lstrip().startswith("#"): continue
            key,value=line.split("=",1)
            result[key]=value.strip().strip('"').strip("'")
    except OSError:
        pass
    return result

def architecture() -> str:
    value=ARCHES.get(platform.machine().lower())
    if not value:
        raise RuntimeError("Unsupported CPU architecture: "+platform.machine())
    return value

def glibc_version() -> tuple[int,int] | None:
    name,version=platform.libc_ver()
    if name.lower() in {'glibc','gnu libc'} and re_match_version(version):
        parts=version.split('.')
        return int(parts[0]),int(parts[1])
    ldd=shutil.which('ldd')
    if ldd:
        result=subprocess.run([ldd,'--version'],capture_output=True,text=True)
        text=result.stdout+result.stderr
        import re
        match=re.search(r'(?:glibc|gnu libc|ldd \(.*\))[^0-9]*([0-9]+)\.([0-9]+)',text,re.I)
        if match:return int(match.group(1)),int(match.group(2))
    return None

def re_match_version(value: str) -> bool:
    import re
    return bool(re.fullmatch(r'[0-9]+\.[0-9]+(?:\.[0-9]+)?',value or ''))

def musl_version() -> tuple[int,int] | None:
    ldd=shutil.which('ldd')
    if not ldd:return None
    result=subprocess.run([ldd,'--version'],capture_output=True,text=True)
    import re
    match=re.search(r'Version\s+([0-9]+)\.([0-9]+)',result.stdout+result.stderr,re.I)
    return (int(match.group(1)),int(match.group(2))) if match else None

def libc_family() -> str:
    if list(Path("/lib").glob("ld-musl-*.so.1")) or list(Path("/usr/lib").glob("ld-musl-*.so.1")):
        return "musl"
    name,_=platform.libc_ver()
    if name.lower()=="musl":
        return "musl"
    if name.lower() in {"glibc","gnu libc"}:
        return "gnu"
    ldd=shutil.which("ldd")
    if ldd:
        result=subprocess.run([ldd,"--version"],capture_output=True,text=True)
        text=(result.stdout+result.stderr).lower()
        if "musl" in text:return "musl"
        if "glibc" in text or "gnu libc" in text:return "gnu"
    # Linux distributions overwhelmingly use glibc or musl; never guess another libc.
    raise RuntimeError("Unsupported or undetected Linux libc")

def target_key() -> str:
    if platform.system()!="Linux":
        raise RuntimeError("V-UI server installer supports Linux only")
    return architecture()+"-"+libc_family()

def init_system() -> str:
    if Path("/run/systemd/system").is_dir() and shutil.which("systemctl"):
        return "systemd"
    if shutil.which("rc-service") and shutil.which("rc-update"):
        return "openrc"
    return "unknown"

def package_manager() -> str:
    for name in ("apt-get","dnf","yum","zypper","pacman","apk","xbps-install","emerge"):
        if shutil.which(name):
            return name
    return "unknown"

def distro() -> dict:
    value=read_os_release()
    libc=libc_family()
    glibc=glibc_version() if libc=='gnu' else None
    musl=musl_version() if libc=='musl' else None
    compatible=(glibc is not None and glibc >= (2,34)) if libc=='gnu' else (musl is not None and musl >= (1,2))
    return {
        "id":value.get("ID","unknown"),
        "name":value.get("PRETTY_NAME") or value.get("NAME") or "Unknown Linux",
        "version":value.get("VERSION_ID",""),
        "id_like":value.get("ID_LIKE",""),
        "arch":architecture(),
        "libc":libc,
        "glibc_version":'.'.join(map(str,glibc)) if glibc else None,
        "musl_version":'.'.join(map(str,musl)) if musl else None,
        "runtime_compatible":compatible,
        "target":target_key(),
        "init":init_system(),
        "package_manager":package_manager(),
    }
