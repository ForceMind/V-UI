"""Assemble the already built/accepted bundle with audited one-command entrypoints."""
import hashlib
import json
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import zipfile

SOURCE_ASSETS = (
    ('install.sh', 'install.sh'),
    ('scripts/install_system.py', 'install_system.py'),
    ('scripts/platform_support.py', 'platform_support.py'),
    ('scripts/firewall_support.py', 'firewall_support.py'),
    ('scripts/service_support.py', 'service_support.py'),
    ('scripts/install_ops_control.py', 'install_ops_control.py'),
    ('deploy/system_launcher.py', 'system_launcher.py'),
    ('deploy/system_ops.py', 'system_ops.py'),
    ('deploy/ops_worker.py', 'ops_worker.py'),
    ('app/release_tools.py', 'release_tools.py'),
    ('deploy/v-ui', 'v-ui'),
)


def build(bundle, destination, commit):
    root=Path(__file__).resolve().parents[1]
    bundle,destination=Path(bundle),Path(destination)
    destination.mkdir(parents=True,exist_ok=True)
    if any(destination.iterdir()):
        raise ValueError('Release kit destination must be empty')
    if not re.fullmatch(r'[a-f0-9]{40}',commit):
        raise ValueError('Exact source commit is required')
    with zipfile.ZipFile(bundle) as archive:manifest=json.loads(archive.read('MANIFEST.json'))
    targets=manifest.get('targets') or []
    if len(targets)!=1 or targets[0] not in {'x86_64-gnu','aarch64-gnu','x86_64-musl','aarch64-musl'}:
        raise ValueError('Release bundle must contain exactly one supported target')
    if manifest['source_commit'] != commit:raise ValueError('Bundle was built from another commit')
    target=destination/('vui-linux-'+targets[0]+'.zip');shutil.copyfile(bundle,target)
    subprocess.run(['git','archive','--format=zip','--output='+str(destination/'vui-source.zip'),commit],cwd=root,check=True)
    # Every entry point, including root code and its service templates, comes
    # from this one commit snapshot. Dirty working-tree bytes are never copied.
    with zipfile.ZipFile(destination/'vui-source.zip') as source_archive:
        if source_archive.comment != commit.encode():
            raise ValueError('Source archive does not identify the exact commit')
        names=source_archive.namelist()
        if len(set(names))!=len(names):
            raise ValueError('Source archive has duplicate members')
        for source,name in (*SOURCE_ASSETS,('docs/RELEASE_NOTES.md','RELEASE_NOTES.md')):
            entry=source_archive.getinfo(source)
            if (entry.is_dir() or entry.flag_bits & 1 or entry.file_size>8_000_000
                    or stat.S_IFMT(entry.external_attr >> 16) not in (0,stat.S_IFREG)):
                raise ValueError('Unsafe source entry: '+source)
            (destination/name).write_bytes(source_archive.read(entry))
    (destination/'RELEASE.json').write_text(json.dumps({'schema':1,'version':manifest['version'],
        'source_commit':commit,'release_id':manifest['release_id'],'platform':manifest['platform'],
        'targets':targets},indent=2))
    entries=[]
    for path in sorted(destination.iterdir()):
        if path.is_file() and path.name!='SHA256SUMS':
            entries.append(hashlib.sha256(path.read_bytes()).hexdigest()+'  '+path.name)
    (destination/'SHA256SUMS').write_text('\n'.join(entries)+'\n')

if __name__=='__main__':build(*sys.argv[1:])
