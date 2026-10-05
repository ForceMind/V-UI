"""Build/test discovery only: collect pinned dependency and Pebble provenance."""
import email.parser
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import urllib.request
import zipfile

root=Path(sys.argv[1])
requirements=[]
for wheel in sorted((root/'wheels').glob('*.whl')):
    with zipfile.ZipFile(wheel) as archive:
        metadata=next(n for n in archive.namelist() if n.endswith('.dist-info/METADATA'))
        parsed=email.parser.Parser().parsestr(archive.read(metadata).decode())
    requirements.append(parsed['Name']+'=='+parsed['Version'])
(root/'resolved.txt').write_text('\n'.join(sorted(requirements,key=str.lower))+'\n')
request=urllib.request.Request('https://api.github.com/repos/letsencrypt/pebble/releases/tags/v2.10.1',headers={'User-Agent':'VUI-ACME-tests'})
with urllib.request.urlopen(request,timeout=30) as response: release=json.load(response)
asset=next(a for a in release['assets'] if a['name']=='pebble-linux-amd64.tar.gz')
with urllib.request.urlopen(asset['browser_download_url'],timeout=60) as response: data=response.read(20_000_001)
if len(data)>20_000_000 or 'sha256:'+hashlib.sha256(data).hexdigest()!=asset['digest']:raise ValueError('Pebble checksum mismatch')
with tarfile.open(fileobj=io.BytesIO(data)) as archive:
    member=next(m for m in archive if m.isfile() and Path(m.name).name=='pebble')
    (root/'pebble').write_bytes(archive.extractfile(member).read())
(root/'pebble').chmod(0o700)
proof={'version':'v2.10.1','url':asset['browser_download_url'],'digest':asset['digest']}
(root/'pebble-provenance.json').write_text(json.dumps(proof,indent=2))
print((root/'resolved.txt').read_text());print(json.dumps(proof))
