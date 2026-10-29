"""Manual, gated promotion of exact-head accepted artifacts; never rebuild during publication."""
from __future__ import annotations
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import urllib.error
import urllib.parse
import urllib.request
import zipfile

REPO='ForceMind/V-UI'
TARGETS=('x86_64-gnu','aarch64-gnu','x86_64-musl','aarch64-musl')
REQUIRED={'test.yml','toclash.yml','loopback.yml','release.yml','acme.yml',
          'oneclick.yml','portable.yml','docs.yml'}
REQUIRED_ACCEPTANCE_STEPS = {
    'test.yml': {'Browser authentication smoke', 'Browser routing and subscription workflow',
                 'Browser proxy import, drafts and runtime UI lifecycle',
                 'Browser per-inbound direct activation through actual API and core',
                 'Browser two-instance TCP upstream export and import',
                 'Browser managed-certificate editor transitions and restore'},
    'release.yml': {'Verify non-root install HTTPS browser and recovery'},
    'acme.yml': {'Test graphical certificate lifecycle against Pebble'},
}
INSTALLER_ONLY_BASELINE = '586d8a2dbd0be6d7ba8d5c5823df8fe9a1f73c95'
INSTALLER_ONLY_VERSION = '0.4.11'
INSTALLER_ONLY_FILES = {
    'VERSION', 'main.py', 'setup.sh', 'install.sh',
    'scripts/install_system.py', 'scripts/install_ops_control.py', 'scripts/publish_release.py',
    'scripts/installer_certificate_state.py', 'scripts/build_bundle.py',
    'README.md', 'CHANGELOG.md', 'AGENTS.md', 'ROADMAP.md', 'CONTRIBUTING.md',
    '.github/workflows/publish.yml', '.github/workflows/short-install.yml',
    '.github/workflows/oneclick.yml',
    '.github/workflows/online-release-acceptance.yml',
}
BROWSER_SKIP_STEPS = {
    name: steps | {'Install explicit test browser'} for name, steps in REQUIRED_ACCEPTANCE_STEPS.items()
}
BROWSER_SKIP_STEPS['test.yml'] = REQUIRED_ACCEPTANCE_STEPS['test.yml'] | {
    'Install test browser', 'Prepare checksum-pinned browser frontend'}
COMMON_ASSETS={'install.sh','install_system.py','platform_support.py','firewall_support.py',
               'service_support.py','install_ops_control.py','system_launcher.py',
               'system_ops.py','ops_worker.py','release_tools.py','v-ui',
               'vui-source.zip','RELEASE.json','RELEASE_NOTES.md'}
BUNDLE_ASSETS={'vui-linux-'+target+'.zip' for target in TARGETS}
ASSETS=COMMON_ASSETS|BUNDLE_ASSETS|{'SHA256SUMS'}
KIT_ASSETS=COMMON_ASSETS|{'vui-linux-x86_64-gnu.zip','SHA256SUMS'}


class ReleaseGateError(RuntimeError):
    pass


def installation_command(files, version, *, installer_args=(), interactive=True):
    """One physical line: root-private bootstrap and independently pinned kit.

    Extra arguments are for existing installer options, such as the temporary
    provided-certificate fixture used by the published Linux acceptance gate.
    No credential is generated or passed by this formatter.
    """
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', version):
        raise ReleaseGateError('Stable installation version required')
    if '--dry-run' in installer_args:
        raise ReleaseGateError('The managed installation command cannot be a dry run')
    script_sha = hashlib.sha256(files['install.sh']).hexdigest()
    kit_sha = hashlib.sha256(files['SHA256SUMS']).hexdigest()
    url = 'https://github.com/' + REPO + '/releases/download/v' + version + '/install.sh'
    prerequisites = (
        'if ! command -v curl >/dev/null 2>&1 || ! command -v bash >/dev/null 2>&1 || ! command -v sha256sum >/dev/null 2>&1; then '
        'if command -v apt-get >/dev/null 2>&1; then apt-get update; DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates curl bash coreutils; '
        'elif command -v apk >/dev/null 2>&1; then apk add --no-cache ca-certificates curl bash coreutils; '
        'elif command -v dnf >/dev/null 2>&1; then dnf install -y ca-certificates curl bash coreutils; '
        'elif command -v yum >/dev/null 2>&1; then yum install -y ca-certificates curl bash coreutils; '
        'elif command -v zypper >/dev/null 2>&1; then zypper --non-interactive install ca-certificates curl bash coreutils; '
        'elif command -v pacman >/dev/null 2>&1; then pacman -Sy --noconfirm --needed ca-certificates curl bash coreutils; '
        'elif command -v xbps-install >/dev/null 2>&1; then xbps-install -Sy ca-certificates curl bash coreutils; '
        'else printf "%s\\n" "Install curl, Bash and sha256sum, then rerun this command." >&2; exit 1; fi; fi'
    )
    options = ['--version', 'v' + version, '--with-global-cli', '--kit-sha256', kit_sha]
    if interactive:
        options.append('--interactive')
    arguments = shlex.join([*options, *installer_args])
    script = '; '.join([
        'set -eu', 'umask 077', prerequisites,
        'setup_dir=$(mktemp -d /root/vui-bootstrap.XXXXXXXX)',
        'curl --proto =https --proto-redir =https --tlsv1.2 --fail --location --retry 2 --connect-timeout 15 --max-time 300 '
        + shlex.quote(url) + ' -o "$setup_dir/install.sh"',
        'printf "%s  %s\\n" ' + script_sha + ' "$setup_dir/install.sh" | sha256sum -c -',
        'exec /bin/bash "$setup_dir/install.sh" ' + arguments,
    ])
    dispatch = ('PATH=/usr/sbin:/usr/bin:/sbin:/bin; export PATH; '
                'if [ "$(id -u)" -eq 0 ]; then exec "$@"; else exec sudo -- "$@"; fi')
    return shlex.join(['/bin/sh', '-c', dispatch, 'v-ui-setup', '/usr/bin/env', '-i',
                       'PATH=/usr/sbin:/usr/bin:/sbin:/bin', 'HOME=/root', 'LANG=C.UTF-8',
                       'PYTHONDONTWRITEBYTECODE=1', '/bin/sh', '-c', script])


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request(path,method='GET',data=None,binary=False):
    url=path if path.startswith('https://uploads.github.com/') else \
        'https://api.github.com/repos/'+REPO+('/'+path if path else '')
    body=data if binary else None if data is None else json.dumps(data).encode()
    headers={'Authorization':'Bearer '+os.environ['GH_TOKEN'],'User-Agent':'V-UI-release-gate',
             'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2026-03-10'}
    if body is not None:
        headers['Content-Type']='application/octet-stream' if binary else 'application/json'
    req=urllib.request.Request(url,method=method,data=body,headers=headers)
    with urllib.request.build_opener(NoRedirect()).open(req,timeout=120) as response:
        return json.load(response)


def select_runs(runs,commit):
    latest={}
    for run in runs:
        if run.get('head_sha')!=commit:
            continue
        path=run.get('path','').split('@')[0].rsplit('/',1)[-1]
        if path in REQUIRED and (path not in latest or run['id']>latest[path]['id']):
            latest[path]=run
    for path in sorted(REQUIRED):
        run=latest.get(path)
        if not run or run.get('status')!='completed' or run.get('conclusion')!='success':
            raise ReleaseGateError('Latest exact-commit check is not successful: '+path)
    return latest


def validate_installer_only_scope(commit, version):
    """One explicitly approved patch; no web/app/dependency/core changes allowed."""
    if version != INSTALLER_ONLY_VERSION:
        raise ReleaseGateError('Non-browser installer exception is only approved for 0.4.11')
    try:
        subprocess.run(['git','merge-base','--is-ancestor',INSTALLER_ONLY_BASELINE,commit],check=True,
                       stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        changes=subprocess.check_output(['git','diff','--name-status',INSTALLER_ONLY_BASELINE,commit],text=True)
        for line in changes.splitlines():
            fields=line.split('\t')
            if len(fields)!=2 or fields[0] not in ('A','M'):
                raise ReleaseGateError('Installer-only scope refuses deletion, rename or type change')
            name=fields[1]
            allowed=(name in INSTALLER_ONLY_FILES or
                     (name.startswith('tests/') and name.endswith('.py')) or
                     (name.startswith('docs/') and name.endswith('.md')))
            if not allowed:
                raise ReleaseGateError('Non-installer change requires full acceptance: '+name)
            mode=subprocess.check_output(['git','ls-tree',commit,'--',name],text=True).split()[0]
            if mode not in ('100644','100755'):
                raise ReleaseGateError('Installer-only exception requires regular source files')
        previous=subprocess.check_output(['git','show',INSTALLER_ONLY_BASELINE+':main.py'])
        current=subprocess.check_output(['git','show',commit+':main.py'])
        if previous.count(b"version='0.4.10'") != 1 or current != previous.replace(b"version='0.4.10'",b"version='0.4.11'",1):
            raise ReleaseGateError('Application changes exceed the installer-only version bump')
        if subprocess.check_output(['git','show',commit+':VERSION']).strip()!=b'0.4.11':
            raise ReleaseGateError('Installer-only version identity differs')
        # Packaging may add this one installer helper, but must not change
        # application/dependency/core selection or rewrite any payload bytes.
        old_builder=subprocess.check_output(['git','show',INSTALLER_ONLY_BASELINE+':scripts/build_bundle.py'])
        new_builder=subprocess.check_output(['git','show',commit+':scripts/build_bundle.py'])
        old_line=b"'scripts/deploy.py','scripts/install_system.py'}"
        new_line=b"'scripts/deploy.py','scripts/install_system.py',\n            'scripts/installer_certificate_state.py'}"
        if old_builder.count(old_line)!=1 or new_builder!=old_builder.replace(old_line,new_line,1):
            raise ReleaseGateError('Builder changes exceed the single installer helper addition')
    except subprocess.CalledProcessError as exc:
        raise ReleaseGateError('Cannot prove installer-only source ancestry/scope') from exc


def validate_run_jobs(checks, commit, *, installer_only_no_browser=False):
    """A green workflow with skipped acceptance is not release qualification."""
    for path, run in sorted(checks.items()):
        attempt = run.get('run_attempt')
        if type(attempt) is not int or attempt < 1:
            raise ReleaseGateError('Exact workflow attempt required: '+path)
        jobs=[]
        for page in range(1,11):
            batch=request('actions/runs/'+str(run['id'])+'/attempts/'+str(attempt)+
                          '/jobs?per_page=100&page='+str(page))['jobs']
            jobs.extend(batch)
            if len(batch)<100:
                break
        else:
            raise ReleaseGateError('Workflow job pagination exceeds bound: '+path)
        if not jobs or len({job['id'] for job in jobs}) != len(jobs):
            raise ReleaseGateError('Missing or duplicated exact-attempt jobs: '+path)
        names={step.get('name') for job in jobs for step in job.get('steps', [])}
        if not REQUIRED_ACCEPTANCE_STEPS.get(path,set()) <= names:
            raise ReleaseGateError('Missing required acceptance steps: '+path)
        for job in jobs:
            if (job.get('run_id') != run['id'] or job.get('head_sha') != commit
                    or job.get('status') != 'completed' or job.get('conclusion') != 'success'):
                raise ReleaseGateError('Exact workflow job is not successful: '+path)
            steps=job.get('steps', [])
            def accepted(step):
                return (step.get('status') == 'completed' and
                        (step.get('conclusion') == 'success' or
                         (installer_only_no_browser and step.get('conclusion') == 'skipped'
                          and step.get('name') in BROWSER_SKIP_STEPS.get(path,set()))))
            if not steps or not all(accepted(step) for step in steps):
                raise ReleaseGateError('Workflow has unverified, skipped or failed steps: '+path)
        # Refuse a retry or state transition that started while evidence was read.
        current=request('actions/runs/'+str(run['id']))
        if (current.get('head_sha') != commit or current.get('run_attempt') != attempt
                or current.get('status') != 'completed' or current.get('conclusion') != 'success'):
            raise ReleaseGateError('Workflow changed during qualification: '+path)


def _archive_files(raw: bytes, expected: set[str], limit: int) -> dict[str,bytes]:
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        names=archive.namelist()
        if set(names)!=expected or len(names)!=len(expected):
            raise ReleaseGateError('Unexpected artifact entries')
        if sum(item.file_size for item in archive.infolist())>limit:
            raise ReleaseGateError('Artifact exceeds size limit')
        if any(item.is_dir() or item.flag_bits & 1 or
               stat.S_IFMT(item.external_attr >> 16) not in (0, stat.S_IFREG)
               for item in archive.infolist()):
            raise ReleaseGateError('Artifact contains a link, directory or encrypted entry')
        return {name:archive.read(name) for name in names}


def _parse_checksums(raw: bytes) -> dict[str,str]:
    values={}
    for line in raw.decode().splitlines():
        match=re.fullmatch(r'([a-f0-9]{64})  ([A-Za-z0-9._-]+)',line)
        if not match or match[2] in values:
            raise ReleaseGateError('Invalid release checksums')
        values[match[2]]=match[1]
    return values


def validate_bundle(raw: bytes,commit: str,version: str,target: str) -> dict:
    if target not in TARGETS:
        raise ReleaseGateError('Unknown release target')
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        if 'MANIFEST.json' not in archive.namelist():
            raise ReleaseGateError('Target bundle has no manifest')
        manifest=json.loads(archive.read('MANIFEST.json'))
    if (manifest.get('source_commit')!=commit or manifest.get('version')!=version
            or manifest.get('platform')!='linux-multi-cpython312'
            or manifest.get('targets')!=[target]):
        raise ReleaseGateError('Target bundle metadata mismatch: '+target)
    return manifest


def validate_kit(raw,commit,version):
    files=_archive_files(raw,KIT_ASSETS,900_000_000)
    hashes=_parse_checksums(files['SHA256SUMS'])
    if set(hashes)!=KIT_ASSETS-{'SHA256SUMS'}:
        raise ReleaseGateError('Missing kit asset checksum')
    for name,expected in hashes.items():
        if hashlib.sha256(files[name]).hexdigest()!=expected:
            raise ReleaseGateError('Kit asset checksum mismatch: '+name)
    record=json.loads(files['RELEASE.json'])
    if record.get('source_commit')!=commit or record.get('version')!=version:
        raise ReleaseGateError('Artifact metadata does not match requested release')
    validate_bundle(files['vui-linux-x86_64-gnu.zip'],commit,version,'x86_64-gnu')
    return files


def validate_target_artifact(raw,commit,version,target):
    bundle='vui-linux-'+target+'.zip'
    checksum=bundle+'.sha256'
    files=_archive_files(raw,{bundle,checksum},900_000_000)
    line=files[checksum].decode().strip()
    match=re.fullmatch(r'([a-f0-9]{64})  '+re.escape(bundle),line)
    if not match or hashlib.sha256(files[bundle]).hexdigest()!=match.group(1):
        raise ReleaseGateError('Portable target checksum mismatch: '+target)
    validate_bundle(files[bundle],commit,version,target)
    return files[bundle]


def download_artifact(artifact,max_bytes=900_000_000):
    try:
        request('actions/artifacts/'+str(artifact['id'])+'/zip')
    except urllib.error.HTTPError as redirect:
        if redirect.code!=302:
            raise
        location=redirect.headers['Location']
        if urllib.parse.urlsplit(location).scheme!='https':
            raise ReleaseGateError('Insecure artifact redirect')
    else:
        raise ReleaseGateError('Expected signed artifact redirect')
    with urllib.request.urlopen(location,timeout=120) as response:
        raw=response.read(max_bytes+1)
    if len(raw)>max_bytes or artifact.get('digest')!='sha256:'+hashlib.sha256(raw).hexdigest():
        raise ReleaseGateError('Artifact transport digest mismatch')
    return raw


def unique_artifact(items,name):
    selected=[a for a in items if a.get('name')==name and not a.get('expired')]
    if len(selected)!=1:
        raise ReleaseGateError('No unique accepted artifact: '+name)
    return selected[0]


def finalize_assets(files: dict[str,bytes],commit: str,version: str) -> dict[str,bytes]:
    record=json.loads(files['RELEASE.json'])
    record.update(source_commit=commit,version=version,targets=list(TARGETS))
    files['RELEASE.json']=json.dumps(record,indent=2).encode()
    if set(files)-{'SHA256SUMS'}!=ASSETS-{'SHA256SUMS'}:
        raise ReleaseGateError('Final release asset set is incomplete')
    lines=[
        hashlib.sha256(files[name]).hexdigest()+'  '+name
        for name in sorted(files) if name!='SHA256SUMS'
    ]
    files['SHA256SUMS']=('\n'.join(lines)+'\n').encode()
    if set(files)!=ASSETS:
        raise ReleaseGateError('Unexpected final release asset set')
    return files


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--commit',required=True);p.add_argument('--version',required=True)
    p.add_argument('--confirm',required=True);p.add_argument('--publish',action='store_true')
    p.add_argument('--installer-only-no-browser',action='store_true',
                   help='Explicitly approved 0.4.11 installer-only exception; browser evidence stays unverified')
    args=p.parse_args()
    if not re.fullmatch(r'[a-f0-9]{40}',args.commit) or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+',args.version):
        raise ReleaseGateError('Exact commit and stable version required')
    if args.confirm!='RELEASE '+args.version+' '+args.commit:
        raise ReleaseGateError('Explicit release confirmation does not match')
    if os.environ.get('GITHUB_REPOSITORY')!=REPO or os.environ.get('GITHUB_EVENT_NAME')!='workflow_dispatch':
        raise ReleaseGateError('Run only from the trusted manual repository workflow')

    default=request('')['default_branch']
    current=request('git/ref/heads/'+urllib.parse.quote(default,safe=''))['object']['sha']
    local=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()
    if args.commit!=current or local!=current:
        raise ReleaseGateError('Merge and test the exact current default-branch commit first')
    if Path('VERSION').read_text().strip()!=args.version:
        raise ReleaseGateError('VERSION differs')

    runs=[]
    for page in range(1,11):
        batch=request('actions/runs?head_sha='+args.commit+'&per_page=100&page='+str(page))['workflow_runs']
        runs.extend(batch)
        if len(batch)<100:
            break
    checks=select_runs(runs,args.commit)
    if args.installer_only_no_browser:
        validate_installer_only_scope(args.commit,args.version)
    validate_run_jobs(checks,args.commit,installer_only_no_browser=args.installer_only_no_browser)

    tag='v'+args.version
    for path in ('git/ref/tags/'+tag,'releases/tags/'+tag):
        try:
            request(path)
        except urllib.error.HTTPError as error:
            if error.code!=404:
                raise
        else:
            raise ReleaseGateError('Refusing to overwrite an existing tag or release')

    oneclick=request('actions/runs/'+str(checks['oneclick.yml']['id'])+'/artifacts?per_page=100')['artifacts']
    kit=unique_artifact(oneclick,'vui-release-kit-'+args.commit)
    files=validate_kit(download_artifact(kit),args.commit,args.version)

    portable=request('actions/runs/'+str(checks['portable.yml']['id'])+'/artifacts?per_page=100')['artifacts']
    for target in TARGETS[1:]:
        name='vui-linux-'+target+'-'+args.commit
        artifact=unique_artifact(portable,name)
        files['vui-linux-'+target+'.zip']=validate_target_artifact(
            download_artifact(artifact),args.commit,args.version,target
        )

    files=finalize_assets(files,args.commit,args.version)

    evidence='\n'.join('- '+path+': '+str(run['html_url']) for path,run in sorted(checks.items()))
    body=files['RELEASE_NOTES.md'].decode()+'\n\nVerified commit: `'+args.commit+'`\n\n'+evidence
    if args.installer_only_no_browser:
        body+='\n\n验收范围：用户明确批准本次 0.4.11 纯安装交互修复采用非浏览器验收。浏览器未验证；'+\
              '仅跳过明列的浏览器步骤，其余 jobs/steps、四目标制品及实际安装门禁保持。'+\
              '已检查相对 `'+INSTALLER_ONLY_BASELINE+'` 无前端、应用逻辑、运行依赖或固定核心变更；此例外不适用于其他版本。\n'
    body+='\n\n## 一行交互安装并注册 v-ui\n\n```sh\n'+installation_command(files,args.version)+'\n```\n'
    body+='\n安装成功后输入 `v-ui`。普通管理员按已有 sudo 策略认证；不增加免密 sudoers。\n'
    release=request('releases','POST',{'tag_name':tag,'target_commitish':args.commit,'name':'V-UI '+tag,
                                      'body':body,'draft':True,'prerelease':False})
    upload=release['upload_url'].split('{')[0]
    if not upload.startswith('https://uploads.github.com/repos/'+REPO+'/releases/'):
        raise ReleaseGateError('Unexpected upload endpoint; draft retained')
    for name in sorted(files):
        asset=request(upload+'?name='+urllib.parse.quote(name),'POST',files[name],True)
        if asset.get('digest')!='sha256:'+hashlib.sha256(files[name]).hexdigest():
            raise ReleaseGateError('Uploaded asset digest mismatch; release remains draft')
    if args.publish:
        release=request('releases/'+str(release['id']),'PATCH',{'draft':False,'make_latest':'true'})
    print(json.dumps({'release':release['html_url'],'draft':release['draft'],
                      'commit':args.commit,'targets':list(TARGETS)}))


if __name__=='__main__':
    main()
