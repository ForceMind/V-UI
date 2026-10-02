"""Download fixed official Linux core binaries for x86_64 or aarch64."""
import hashlib
import io
import json
from pathlib import Path
import tarfile
import urllib.request
import zipfile

PINS = {
    "x86_64": {
        "sing-box": {"version":"1.14.2","url":"https://github.com/SagerNet/sing-box/releases/download/v1.14.2/sing-box-1.14.2-linux-amd64.tar.gz","sha256":"a684484d7477d1437282ee411f4d131d0340aaad60a7868841ebd5d87dd8a0c6","member":"sing-box-1.14.2-linux-amd64/sing-box"},
        "xray": {"version":"26.3.27","url":"https://github.com/XTLS/Xray-core/releases/download/v26.3.27/Xray-linux-64.zip","sha256":"23cd9af937744d97776ee35ecad4972cf4b2109d1e0fe6be9930467608f7c8ae","member":"xray"},
    },
    "aarch64": {
        "sing-box": {"version":"1.14.2","url":"https://github.com/SagerNet/sing-box/releases/download/v1.14.2/sing-box-1.14.2-linux-arm64.tar.gz","sha256":"b43a1fb1bda131c6653576741ce527eb2bdeab7c9308ca90ee8b972abb7e4a7f","member":"sing-box-1.14.2-linux-arm64/sing-box"},
        "xray": {"version":"26.3.27","url":"https://github.com/XTLS/Xray-core/releases/download/v26.3.27/Xray-linux-arm64-v8a.zip","sha256":"4d30283ae614e3057f730f67cd088a42be6fdf91f8639d82cb69e48cde80413c","member":"xray"},
    },
}

def fetch(destination: Path, arch: str="x86_64"):
    if arch not in PINS: raise ValueError("Unsupported core architecture: "+arch)
    destination.mkdir(parents=True, exist_ok=True)
    evidence={}
    for name,pin in PINS[arch].items():
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
        evidence[name]={**pin,"binary_sha256":hashlib.sha256(binary).hexdigest(),"arch":arch}
    (destination/"provenance.json").write_text(json.dumps(evidence,indent=2))

if __name__=="__main__":
    import sys
    fetch(Path(sys.argv[1]),sys.argv[2] if len(sys.argv)>2 else "x86_64")
