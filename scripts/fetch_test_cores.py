"""Download fixed official Linux amd64 test binaries; verify archives before extraction."""
import hashlib
import io
import json
from pathlib import Path
import tarfile
import urllib.request
import zipfile

PINS = {
    'sing-box': {'version': '1.14.2',
        'url': 'https://github.com/SagerNet/sing-box/releases/download/v1.14.2/sing-box-1.14.2-linux-amd64.tar.gz',
        'sha256': '738a57c9f31e8c417c6b7e0c7aacd0dbd1b6df4a6fbfa63e6d9b0776ee82beb8',
        'member': 'sing-box-1.14.2-linux-amd64/sing-box'},
    'xray': {'version': '26.3.27',
        'url': 'https://github.com/XTLS/Xray-core/releases/download/v26.3.27/Xray-linux-64.zip',
        'sha256': '23cd9af937744d97776ee35ecad4972cf4b2109d1e0fe6be9930467608f7c8ae',
        'member': 'xray'},
}


def fetch(destination: Path):
    destination.mkdir(parents=True, exist_ok=True)
    evidence = {}
    for name, pin in PINS.items():
        with urllib.request.urlopen(pin['url'], timeout=90) as response:
            data = response.read(100_000_001)
        if len(data) > 100_000_000 or hashlib.sha256(data).hexdigest() != pin['sha256']:
            raise ValueError('Archive checksum mismatch: ' + name)
        if pin['url'].endswith('.zip'):
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                binary = archive.read(pin['member'])
        else:
            with tarfile.open(fileobj=io.BytesIO(data)) as archive:
                member = archive.getmember(pin['member'])
                if not member.isfile(): raise ValueError('Not a regular core binary')
                binary = archive.extractfile(member).read()
        path = destination / name
        path.write_bytes(binary)
        path.chmod(0o700)
        evidence[name] = {**pin, 'binary_sha256': hashlib.sha256(binary).hexdigest()}
    (destination / 'provenance.json').write_text(json.dumps(evidence, indent=2))


if __name__ == '__main__':
    import sys
    fetch(Path(sys.argv[1]))
