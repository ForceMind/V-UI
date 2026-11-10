"""Exact official client binary for configuration and loopback integration checks."""
import gzip
import hashlib
import json
from pathlib import Path
import platform
import sys

if __package__:
    from .test_asset_download import download_pinned
else:
    from test_asset_download import download_pinned

PIN = {'version':'1.19.32',
       'url':'https://github.com/MetaCubeX/mihomo/releases/download/v1.19.32/mihomo-linux-amd64-v1.19.32.gz',
       'sha256':'8451100836c9eda194331c2babfad490b2faf30cebc1a04b0e76fd8ac2d35d10',
       'source':'https://api.github.com/repos/MetaCubeX/mihomo/releases/assets/601415750'}


ARM64_PIN = {'version':'1.19.32',
             'url':'https://github.com/MetaCubeX/mihomo/releases/download/v1.19.32/mihomo-linux-arm64-v1.19.32.gz',
             'sha256':'9dd862e28b46ff7d775f169cceebc28deccaa0a9e804237d421cd2571e0caba0',
             'source':'https://api.github.com/repos/MetaCubeX/mihomo/releases/assets/601415736'}


def native_pin():
    pins = {'x86_64': PIN, 'amd64': PIN, 'aarch64': ARM64_PIN, 'arm64': ARM64_PIN}
    arch = platform.machine().lower()
    if arch not in pins:
        raise ValueError('Unsupported Mihomo fixture architecture: ' + arch)
    return pins[arch]


def fetch(destination: Path):
    pin = native_pin()
    data = download_pinned(pin['url'], max_bytes=100_000_000, timeout=90)
    if len(data) > 100_000_000 or hashlib.sha256(data).hexdigest() != pin['sha256']:
        raise ValueError('Mihomo archive checksum mismatch')
    binary = gzip.decompress(data)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / 'mihomo').write_bytes(binary)
    (destination / 'mihomo').chmod(0o700)
    (destination / 'mihomo-provenance.json').write_text(json.dumps({**pin,'binary_sha256':hashlib.sha256(binary).hexdigest()},indent=2))


if __name__ == '__main__': fetch(Path(sys.argv[1]))
