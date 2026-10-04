"""Download fixed official Linux core binaries for glibc/musl and x86_64/aarch64."""
import hashlib
import io
import json
from pathlib import Path
import tarfile
import urllib.request
import zipfile

PINS = {
    "x86_64-gnu": {
        "sing-box": {"version":"1.14.2","url":"https://github.com/SagerNet/sing-box/releases/download/v1.14.2/sing-box-1.14.2-linux-amd64-glibc.tar.gz","sha256":"5c7bc18461827b28d0e5ee7e89d33b276d3ff7c818531104c8e8d26d85b0656e","member":"sing-box-1.14.2-linux-amd64-glibc/sing-box"},
        "xray": {"version":"26.3.27","url":"https://github.com/XTLS/Xray-core/releases/download/v26.3.27/Xray-linux-64.zip","sha256":"23cd9af937744d97776ee35ecad4972cf4b2109d1e0fe6be9930467608f7c8ae","member":"xray"},
    },
    "x86_64-musl": {
        "sing-box": {"version":"1.14.2","url":"https://github.com/SagerNet/sing-box/releases/download/v1.14.2/sing-box-1.14.2-linux-amd64-musl.tar.gz","sha256":"8f6cb4bcf94d2b33c65d52e0d5b142db29a938336f1ff7267f397ac3758fc297","member":"sing-box-1.14.2-linux-amd64-musl/sing-box"},
        "xray": {"version":"26.3.27","url":"https://github.com/XTLS/Xray-core/releases/download/v26.3.27/Xray-linux-64.zip","sha256":"23cd9af937744d97776ee35ecad4972cf4b2109d1e0fe6be9930467608f7c8ae","member":"xray"},
    },
    "aarch64-gnu": {
        "sing-box": {"version":"1.14.2","url":"https://github.com/SagerNet/sing-box/releases/download/v1.14.2/sing-box-1.14.2-linux-arm64-glibc.tar.gz","sha256":"87db5c3a96ebad1c44c0be1fe7955db2f76b8c97bbc0ed62173063d675e078cf","member":"sing-box-1.14.2-linux-arm64-glibc/sing-box"},
        "xray": {"version":"26.3.27","url":"https://github.com/XTLS/Xray-core/releases/download/v26.3.27/Xray-linux-arm64-v8a.zip","sha256":"4d30283ae614e3057f730f67cd088a42be6fdf91f8639d82cb69e48cde80413c","member":"xray"},
    },
    "aarch64-musl": {
        "sing-box": {"version":"1.14.2","url":"https://github.com/SagerNet/sing-box/releases/download/v1.14.2/sing-box-1.14.2-linux-arm64-musl.tar.gz","sha256":"675297394f9430cebb72b3c48ba8bce0d6f7c750a9d68a8f7f88c515c8255cd1","member":"sing-box-1.14.2-linux-arm64-musl/sing-box"},
        "xray": {"version":"26.3.27","url":"https://github.com/XTLS/Xray-core/releases/download/v26.3.27/Xray-linux-arm64-v8a.zip","sha256":"4d30283ae614e3057f730f67cd088a42be6fdf91f8639d82cb69e48cde80413c","member":"xray"},
    },
}

def normalize_target(value: str) -> str:
    aliases={"amd64":"x86_64-gnu","x86_64":"x86_64-gnu","arm64":"aarch64-gnu","aarch64":"aarch64-gnu"}
    return aliases.get(value,value)

def fetch(destination: Path, target: str="x86_64-gnu"):
    target=normalize_target(target)
    if target not in PINS: raise ValueError("Unsupported core target: "+target)
    destination.mkdir(parents=True,exist_ok=True)
    evidence={}
    for name,pin in PINS[target].items():
        with urllib.request.urlopen(pin["url"],timeout=90) as response:
            data=response.read(100_000_001)
        if len(data)>100_000_000 or hashlib.sha256(data).hexdigest()!=pin["sha256"]:
            raise ValueError("Archive checksum mismatch: "+name)
        if pin["url"].endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(data)) as archive:binary=archive.read(pin["member"])
        else:
            with tarfile.open(fileobj=io.BytesIO(data)) as archive:
                member=archive.getmember(pin["member"])
                if not member.isfile():raise ValueError("Not a regular core binary")
                binary=archive.extractfile(member).read()
        path=destination/name;path.write_bytes(binary);path.chmod(0o700)
        evidence[name]={**pin,"binary_sha256":hashlib.sha256(binary).hexdigest(),"target":target}
    (destination/"provenance.json").write_text(json.dumps(evidence,indent=2))

if __name__=="__main__":
    import sys
    fetch(Path(sys.argv[1]),sys.argv[2] if len(sys.argv)>2 else "x86_64-gnu")
