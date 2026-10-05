"""Explicit build-time download. Exact archive integrity is checked before extraction."""
import base64
import hashlib
import io
import json
from pathlib import Path
import tarfile
import urllib.request

ROOT=Path(__file__).resolve().parents[1]

def extract_selected(data: bytes, pin: dict, target: Path) -> dict:
    if pin['integrity'] != 'sha512-'+base64.b64encode(hashlib.sha512(data).digest()).decode():
        raise ValueError('Frontend archive integrity mismatch')
    files={}
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        for member_name, name in pin['files'].items():
            if Path(name).suffix.lower() in ('.ttf','.otf','.woff','.woff2'):
                raise ValueError('Font files are not included in this package')
            member=archive.getmember(member_name)
            if not member.isfile() or member.size > 8_000_000: raise ValueError('Invalid asset member')
            content=archive.extractfile(member).read()
            path=target/name;path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            path.write_bytes(content);path.chmod(0o600)
            files[name]=hashlib.sha256(content).hexdigest()
    return files

def fetch(target: Path):
    if target.exists(): raise ValueError('Use an empty asset destination')
    pins=json.loads((ROOT/'deploy/frontend-pins.json').read_text())
    target.mkdir(parents=True,mode=0o700)
    evidence={}
    for name,pin in pins.items():
        with urllib.request.urlopen(pin['url'],timeout=90) as response:data=response.read(50_000_001)
        if len(data)>50_000_000:raise ValueError('Archive exceeds size limit')
        evidence[name]={**pin,'selected_sha256':extract_selected(data,pin,target)}
    (target/'PROVENANCE.json').write_text(json.dumps(evidence,indent=2))

if __name__=='__main__':
    import sys
    fetch(Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/'web/vendor')
