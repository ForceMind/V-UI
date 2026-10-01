"""Developer-only pin discovery; no package code is executed or installed."""
import io
import json
from pathlib import Path
import tarfile
import urllib.parse
import urllib.request

PACKAGES = {'vue':'3.5.43','element-plus':'2.14.6','@element-plus/icons-vue':'2.3.2','axios':'1.20.0','qrcodejs':'1.0.0'}

if __name__ == '__main__':
    result = {}
    for name, version in PACKAGES.items():
        url = 'https://registry.npmjs.org/' + urllib.parse.quote(name, safe='') + '/' + version
        with urllib.request.urlopen(url, timeout=30) as response:
            meta = json.load(response)
        assert meta['version'] == version and meta['name'] == name
        dist = meta['dist']
        with urllib.request.urlopen(dist['tarball'], timeout=60) as response:
            archive = response.read(50_000_001)
        assert len(archive) <= 50_000_000
        with tarfile.open(fileobj=io.BytesIO(archive)) as tf:
            candidates = [m.name for m in tf if m.isfile() and
                (m.name.endswith(('.js','.css')) and ('dist/' in m.name or name == 'qrcodejs') or 'LICENSE' in m.name.upper())]
        result[name] = {'version':version,'url':dist['tarball'],'integrity':dist['integrity'],'license':meta.get('license'),'candidates':candidates}
    target = Path('/tmp/vui-frontend-pins.json')
    target.write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2))
