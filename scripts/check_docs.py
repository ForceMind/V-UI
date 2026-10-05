"""Validate current documentation links and version/installer contracts offline."""
from pathlib import Path
import re
import subprocess

ROOT=Path(__file__).resolve().parents[1]
REQUIRED=['README.md','CHANGELOG.md','SECURITY.md','CONTRIBUTING.md','LICENSE','VERSION',
          *['docs/'+name+'.md' for name in ('README','INSTALLATION','CERTIFICATES','CONFIGURATION',
                    'OPERATIONS','TROUBLESHOOTING','COMPATIBILITY','API','RELEASING','RELEASE_NOTES')]]


# These are current entry points; historical guides intentionally retain old versions.
CURRENT_INSTALL_GUIDES = ('README.md', 'docs/INSTALLATION.md', 'docs/RELEASING.md', 'install.sh')

def check_current_versions(root, version):
    expected = 'v' + version
    for name in CURRENT_INSTALL_GUIDES:
        examples = re.findall(r'--version\s+(v\d+\.\d+\.\d+)', (root / name).read_text())
        if not examples or any(value != expected for value in examples):
            raise ValueError('Current installation version mismatch: ' + name)
    if '**' + expected + '**' not in (root / 'docs/README.md').read_text():
        raise ValueError('Current documentation index version mismatch')
    if not (root / 'docs/RELEASE_NOTES.md').read_text().startswith('# V-UI ' + version + '\n'):
        raise ValueError('Current release notes version mismatch')


def check():
    for name in REQUIRED:
        if not (ROOT/name).is_file() or not (ROOT/name).read_text().strip():raise ValueError('Missing required document: '+name)
    version=(ROOT/'VERSION').read_text().strip()
    if not re.fullmatch(r'\d+\.\d+\.\d+',version):raise ValueError('Invalid VERSION')
    if "version='"+version+"'" not in (ROOT/'main.py').read_text():raise ValueError('Application version mismatch')
    check_current_versions(ROOT, version)
    for path in [ROOT/name for name in REQUIRED if name.endswith('.md')]+list((ROOT/'docs').glob('*.md')):
        content=path.read_text()
        for target in re.findall(r'\[[^\]]*\]\(([^)\s]+)\)',content):
            if '://' in target or target.startswith(('#','mailto:')):continue
            location=target.split('#',1)[0]
            if location and not (path.parent/location).exists():raise ValueError(f'Broken local link: {path.name} -> {target}')
    subprocess.run(['bash','-n',str(ROOT/'install.sh')],check=True)
    subprocess.run(['bash','-n',str(ROOT/'install-bin.sh')],check=True)
    print('Documentation: required guides, local links, version and installer shell syntax OK')

if __name__=='__main__':check()
