"""Assemble the already built/accepted bundle with audited one-command entrypoints."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile


def build(bundle, destination, commit):
    root=Path(__file__).resolve().parents[1]
    bundle,destination=Path(bundle),Path(destination)
    destination.mkdir(parents=True,exist_ok=True)
    target=destination/'vui-linux-amd64.zip';shutil.copyfile(bundle,target)
    with zipfile.ZipFile(target) as archive:manifest=json.loads(archive.read('MANIFEST.json'))
    if manifest['source_commit'] != commit:raise ValueError('Bundle was built from another commit')
    for source,name in ((root/'install.sh','install.sh'),(root/'scripts/install_system.py','install_system.py')):
        shutil.copyfile(source,destination/name)
    subprocess.run(['git','archive','--format=zip','--output='+str(destination/'vui-source.zip'),commit],cwd=root,check=True)
    shutil.copyfile(root/'docs/RELEASE_NOTES.md',destination/'RELEASE_NOTES.md')
    (destination/'RELEASE.json').write_text(json.dumps({'schema':1,'version':manifest['version'],
        'source_commit':commit,'release_id':manifest['release_id'],'platform':manifest['platform']},indent=2))
    entries=[]
    for path in sorted(destination.iterdir()):
        if path.is_file() and path.name!='SHA256SUMS':
            entries.append(hashlib.sha256(path.read_bytes()).hexdigest()+'  '+path.name)
    (destination/'SHA256SUMS').write_text('\n'.join(entries)+'\n')

if __name__=='__main__':build(*sys.argv[1:])
