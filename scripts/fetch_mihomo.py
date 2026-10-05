"""Exact official client binary for configuration and loopback integration checks."""
import gzip
import hashlib
import json
from pathlib import Path
import sys
import urllib.request

PIN = {'version':'1.19.32',
       'url':'https://github.com/MetaCubeX/mihomo/releases/download/v1.19.32/mihomo-linux-amd64-v1.19.32.gz',
       'sha256':'8451100836c9eda194331c2babfad490b2faf30cebc1a04b0e76fd8ac2d35d10',
       'source':'https://api.github.com/repos/MetaCubeX/mihomo/releases/assets/601415750'}


def fetch(destination: Path):
    with urllib.request.urlopen(PIN['url'], timeout=90) as response:
        data = response.read(100_000_001)
    if len(data) > 100_000_000 or hashlib.sha256(data).hexdigest() != PIN['sha256']:
        raise ValueError('Mihomo archive checksum mismatch')
    binary = gzip.decompress(data)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / 'mihomo').write_bytes(binary)
    (destination / 'mihomo').chmod(0o700)
    (destination / 'mihomo-provenance.json').write_text(json.dumps({**PIN,'binary_sha256':hashlib.sha256(binary).hexdigest()},indent=2))


if __name__ == '__main__': fetch(Path(sys.argv[1]))
