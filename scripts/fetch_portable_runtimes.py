"""Fetch fixed CPython 3.12 runtimes for supported Linux libc/architectures."""
from __future__ import annotations
import hashlib
from pathlib import Path
import urllib.request

PINS = {
    "x86_64-gnu": {
        "url": "https://github.com/astral-sh/python-build-standalone/releases/download/20260901/cpython-3.12.14%2B20260901-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz",
        "sha256": "72748da13197c1fb161e3afeef20a6a385ff24f2165e6e2758e47008e7faba4c",
    },
    "x86_64-musl": {
        "url": "https://github.com/astral-sh/python-build-standalone/releases/download/20260901/cpython-3.12.14%2B20260901-x86_64-unknown-linux-musl-install_only_stripped.tar.gz",
        "sha256": "1f37044c8cdbd74d5ee112a753c65ef209fedd169c98f3e4e748a93e27eb27a4",
    },
    "aarch64-gnu": {
        "url": "https://github.com/astral-sh/python-build-standalone/releases/download/20260901/cpython-3.12.14%2B20260901-aarch64-unknown-linux-gnu-install_only_stripped.tar.gz",
        "sha256": "577b4bec0793ad1ff0cbff9adbd0df078eddde38a4c41bf5d83ad381a85ee39d",
    },
    "aarch64-musl": {
        "url": "https://github.com/astral-sh/python-build-standalone/releases/download/20260901/cpython-3.12.14%2B20260901-aarch64-unknown-linux-musl-install_only_stripped.tar.gz",
        "sha256": "a0ad6f01b9204eba573a08927097b78143c564f98bea68a41ea1e172f041da3a",
    },
}

def fetch(destination: Path, keys=None) -> None:
    destination=Path(destination)
    destination.mkdir(parents=True,exist_ok=True)
    selected=list(PINS) if keys is None else list(keys)
    for key in selected:
        if key not in PINS:raise ValueError('Unknown portable runtime target: '+key)
        pin=PINS[key]
        path=destination/(key+".tar.gz")
        with urllib.request.urlopen(pin["url"],timeout=120) as response:
            raw=response.read(160_000_001)
        if len(raw)>160_000_000 or hashlib.sha256(raw).hexdigest()!=pin["sha256"]:
            raise ValueError("Portable Python checksum mismatch: "+key)
        path.write_bytes(raw)
        path.chmod(0o600)

if __name__=="__main__":
    import sys
    fetch(Path(sys.argv[1]))
