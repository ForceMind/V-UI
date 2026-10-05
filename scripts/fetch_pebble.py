"""Test-only immutable Pebble binary. Never used as a production CA."""
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import urllib.request
URL='https://github.com/letsencrypt/pebble/releases/download/v2.10.1/pebble-linux-amd64.tar.gz'
SHA256='4f2fcb5bca8c85c9cf73ad140fccfc0d2be40bd81ab99879c79b7b8a0b4f70ed'

def fetch(root):
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    with urllib.request.urlopen(URL,timeout=90) as response:data=response.read(20_000_001)
    if len(data)>20_000_000 or hashlib.sha256(data).hexdigest()!=SHA256:raise ValueError('Pebble archive checksum mismatch')
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        member=next(m for m in archive if m.isfile() and Path(m.name).name=='pebble')
        (root/'pebble').write_bytes(archive.extractfile(member).read())
    (root/'pebble').chmod(0o700)
    (root/'provenance.json').write_text(json.dumps({'version':'2.10.1','url':URL,'sha256':SHA256},indent=2))
if __name__=='__main__':fetch(sys.argv[1])
