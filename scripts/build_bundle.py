"""Build the one validated deployment package; never run on a production VPS."""
from __future__ import annotations
import argparse
import email.parser
import hashlib
import io
import tarfile
import urllib.request
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.release_tools import PLATFORM,create_archive,supported_environment
from vendor_frontend import fetch as fetch_frontend
from fetch_test_cores import fetch as fetch_cores
from fetch_portable_runtimes import fetch as fetch_runtimes, PINS as RUNTIME_PINS
from platform_support import target_key as detected_target
from prepare_frontend import prepare as prepare_frontend


def core_sources(payload: Path):
    """Retain the upstream release-tag sources and license alongside binaries."""
    output=payload/'sources';output.mkdir(mode=0o700)
    licenses=payload/'third_party/core-licenses';licenses.mkdir(parents=True,exist_ok=True,mode=0o700)
    proof={}
    for name,repo,tag in [('sing-box','SagerNet/sing-box','v1.14.2'),('xray','XTLS/Xray-core','v26.3.27')]:
        request=urllib.request.Request(f'https://api.github.com/repos/{repo}/commits/{tag}',headers={'User-Agent':'V-UI-release-builder'})
        with urllib.request.urlopen(request,timeout=30) as response:commit=json.load(response)['sha']
        if not re.fullmatch('[a-f0-9]{40}',commit):raise ValueError('Invalid upstream source commit')
        url=f'https://codeload.github.com/{repo}/tar.gz/{commit}'
        with urllib.request.urlopen(url,timeout=90) as response:data=response.read(50_000_001)
        if len(data)>50_000_000:raise ValueError('Source archive exceeds limit')
        with tarfile.open(fileobj=io.BytesIO(data)) as archive:
            members=archive.getmembers()
            if any(m.name.lower().endswith(('.ttf','.otf','.woff','.woff2')) for m in members):
                raise ValueError('Do not redistribute font files in the release package')
            license_member=next(m for m in members if m.isfile() and len(m.name.split('/'))==2 and m.name.split('/')[-1].upper() in ('LICENSE','LICENSE.TXT','LICENSE.MD'))
            (licenses/(name+'-LICENSE')).write_bytes(archive.extractfile(license_member).read())
        (output/(name+'-source.tar.gz')).write_bytes(data)
        proof[name]={'repository':repo,'release_tag':tag,'commit':commit,'archive_url':url,'sha256':hashlib.sha256(data).hexdigest()}
    (output/'PROVENANCE.json').write_text(json.dumps(proof,indent=2))


TARGETS = set(RUNTIME_PINS)

def wheel_lock(payload: Path, key: str):
    source=ROOT/'requirements-runtime.txt'
    requirements=[line.strip() for line in source.read_text().splitlines() if line.strip() and not line.startswith('#')]
    expected={re.sub('[-_.]+','-',name).lower():version for name,version in (line.split('==') for line in requirements)}
    wheels=payload/'wheels'/key;wheels.mkdir(parents=True,mode=0o700)
    if key.endswith('-musl'):
        command=[sys.executable,'-m','pip','--isolated','--disable-pip-version-check','wheel',
            '--no-deps','-r',str(source),'--wheel-dir',str(wheels)]
    else:
        command=[sys.executable,'-m','pip','--isolated','--disable-pip-version-check','download',
            '--index-url','https://pypi.org/simple','--only-binary=:all:','--no-deps',
            '-r',str(source),'--dest',str(wheels)]
    subprocess.run(command,check=True,timeout=600)
    found={}
    for wheel in sorted(wheels.glob('*.whl')):
        with zipfile.ZipFile(wheel) as archive:
            names=[name for name in archive.namelist() if name.endswith('.dist-info/METADATA')]
            if len(names)!=1:raise ValueError('Invalid wheel metadata')
            metadata=email.parser.Parser().parsestr(archive.read(names[0]).decode())
        name=re.sub('[-_.]+','-',metadata['Name']).lower();version=metadata['Version']
        if expected.get(name)!=version or name in found:raise ValueError('Wheel does not match the exact runtime lock')
        found[name]=f"{name}=={version} --hash=sha256:{hashlib.sha256(wheel.read_bytes()).hexdigest()}"
    if set(found)!=set(expected):raise ValueError('Missing locked wheel for '+key)
    (payload/('requirements.'+key+'.lock')).write_text('\n'.join(found[name] for name in sorted(found))+'\n')

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('destination',type=Path)
    p.add_argument('--source-commit',required=True);p.add_argument('--target');args=p.parse_args()
    os.umask(0o077)
    if os.name!='posix':raise ValueError('Linux build environment required')
    if not re.fullmatch('[a-f0-9]{40}',args.source_commit):raise ValueError('Exact source commit required')
    actual=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    if actual!=args.source_commit:raise ValueError('Build source does not match the requested commit')
    subprocess.run(['git','diff','--quiet','HEAD','--'],cwd=ROOT,check=True)
    key=args.target or detected_target()
    if key not in TARGETS:raise ValueError('Unsupported build target: '+key)
    if key != detected_target():raise ValueError('Build target must match the native build environment')
    with tempfile.TemporaryDirectory(prefix='vui-build-') as directory:
        payload=Path(directory)/'payload';payload.mkdir(mode=0o700)
        # Only git-tracked application files; never copy local data, keys or tests.
        tracked=subprocess.check_output(['git','ls-files','-z'],cwd=ROOT).decode().split('\x00')
        allowed_files={'main.py','README.md','ROADMAP.md','AGENTS.md','VERSION','CHANGELOG.md','SECURITY.md',
            'CONTRIBUTING.md','LICENSE','install.sh','requirements-runtime.txt','scripts/deploy.py','scripts/install_system.py'}
        for name in filter(None,tracked):
            if not (name in allowed_files or name.startswith(('app/','web/','docs/','third_party/','deploy/'))):continue
            if '__pycache__' in name or name.endswith(('.pyc','.ttf','.otf','.woff','.woff2')):continue
            source=ROOT/name
            if source.is_symlink():raise ValueError('Tracked symbolic links are not packaged')
            target=payload/name;target.parent.mkdir(parents=True,exist_ok=True,mode=0o700);shutil.copyfile(source,target)
        prepare_frontend(payload)
        fetch_frontend(payload/'web/vendor')
        arch=key.split('-',1)[0]
        fetch_cores(payload/'cores'/arch,key)
        fetch_runtimes(payload/'runtimes',[key])
        core_sources(payload)
        wheel_lock(payload,key)
        # Included bin hashes, npm archive integrities and lock files are themselves
        # protected by the final manifest and independent archive SHA-256.
        version=(ROOT/'VERSION').read_text().strip()
        if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+',version):raise ValueError('Invalid release version')
        identity=version+'-'+args.source_commit[:12]
        metadata={'kind':'release','release_id':identity,'platform':PLATFORM,'source_commit':args.source_commit,'version':version,
            'targets':[key],'python_runtime_version':'3.12.14+20260901',
            'portable_runtime_pins':{key:RUNTIME_PINS[key]},
            'protocol_profile':'sing-box VLESS/TCP/TLS single-user verified certificate',
            'runtime_pins':(ROOT/'requirements-runtime.txt').read_text()}
        checksum=create_archive(payload,args.destination,metadata)
        args.destination.with_suffix(args.destination.suffix+'.sha256').write_text(checksum+'  '+args.destination.name+'\n')
        print(json.dumps({'release_id':identity,'sha256':checksum,'source_commit':args.source_commit,'platform':PLATFORM}))

if __name__=='__main__':main()
