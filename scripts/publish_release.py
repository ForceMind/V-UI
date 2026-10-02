"""Manual, gated promotion of an accepted artifact; never rebuild during publication."""
from __future__ import annotations
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
import zipfile

REPO = 'ForceMind/V-UI'
REQUIRED = {'test.yml','toclash.yml','loopback.yml','release.yml','acme.yml','oneclick.yml','docs.yml'}
ASSETS = {'install.sh','install_system.py','platform_support.py','firewall_support.py','service_support.py','vui-linux.zip','vui-source.zip',
          'SHA256SUMS','RELEASE.json','RELEASE_NOTES.md'}


class ReleaseGateError(RuntimeError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request(path, method='GET', data=None, binary=False):
    url = path if path.startswith('https://uploads.github.com/') else 'https://api.github.com/repos/'+REPO+('/'+path if path else '')
    body = data if binary else None if data is None else json.dumps(data).encode()
    headers = {'Authorization':'Bearer '+os.environ['GH_TOKEN'], 'User-Agent':'V-UI-release-gate',
               'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2026-03-10'}
    if body is not None: headers['Content-Type']='application/octet-stream' if binary else 'application/json'
    req=urllib.request.Request(url,method=method,data=body,headers=headers)
    with urllib.request.build_opener(NoRedirect()).open(req,timeout=120) as response:
        return json.load(response)


def select_runs(runs, commit):
    latest={}
    for run in runs:
        if run.get('head_sha') != commit: continue
        path=run.get('path','').split('@')[0].rsplit('/',1)[-1]
        if path in REQUIRED and (path not in latest or run['id'] > latest[path]['id']):
            latest[path]=run
    for path in sorted(REQUIRED):
        run=latest.get(path)
        if not run or run.get('status')!='completed' or run.get('conclusion')!='success':
            raise ReleaseGateError('Latest exact-commit check is not successful: '+path)
    return latest


def validate_kit(raw, commit, version):
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        names=archive.namelist()
        if set(names)!=ASSETS or len(names)!=len(ASSETS):
            raise ReleaseGateError('Unexpected release artifact entries')
        if sum(x.file_size for x in archive.infolist())>900_000_000:
            raise ReleaseGateError('Release artifact exceeds size limit')
        files={name:archive.read(name) for name in names}
    hashes={}
    for line in files['SHA256SUMS'].decode().splitlines():
        match=re.fullmatch(r'([a-f0-9]{64})  ([A-Za-z0-9._-]+)',line)
        if not match or match[2] in hashes:raise ReleaseGateError('Invalid release checksums')
        hashes[match[2]]=match[1]
    if set(hashes)!=ASSETS-{'SHA256SUMS'}:raise ReleaseGateError('Missing asset checksum')
    for name,expected in hashes.items():
        if hashlib.sha256(files[name]).hexdigest()!=expected:raise ReleaseGateError('Asset checksum mismatch')
    record=json.loads(files['RELEASE.json'])
    if record.get('source_commit')!=commit or record.get('version')!=version:
        raise ReleaseGateError('Artifact metadata does not match requested release')
    with zipfile.ZipFile(io.BytesIO(files['vui-linux.zip'])) as archive:
        manifest=json.loads(archive.read('MANIFEST.json'))
        if manifest['source_commit']!=commit or manifest['version']!=version:
            raise ReleaseGateError('Bundle belongs to a different source or version')
    return files


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--commit',required=True);p.add_argument('--version',required=True)
    p.add_argument('--confirm',required=True);p.add_argument('--publish',action='store_true')
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
    if Path('VERSION').read_text().strip()!=args.version:raise ReleaseGateError('VERSION differs')
    runs=[]
    for page in range(1,11):
        batch=request('actions/runs?head_sha='+args.commit+'&per_page=100&page='+str(page))['workflow_runs']
        runs.extend(batch)
        if len(batch)<100:break
    checks=select_runs(runs,args.commit)
    tag='v'+args.version
    for path in ('git/ref/tags/'+tag,'releases/tags/'+tag):
        try:request(path)
        except urllib.error.HTTPError as error:
            if error.code!=404:raise
        else:raise ReleaseGateError('Refusing to overwrite an existing tag or release')
    artifacts=request('actions/runs/'+str(checks['oneclick.yml']['id'])+'/artifacts?per_page=100')['artifacts']
    selected=[a for a in artifacts if a['name']=='vui-release-kit-'+args.commit and not a['expired']]
    if len(selected)!=1:raise ReleaseGateError('No unique accepted release kit')
    artifact=selected[0]
    try:request('actions/artifacts/'+str(artifact['id'])+'/zip')
    except urllib.error.HTTPError as redirect:
        if redirect.code!=302:raise
        location=redirect.headers['Location']
        if urllib.parse.urlsplit(location).scheme!='https':raise ReleaseGateError('Insecure artifact redirect')
    else:raise ReleaseGateError('Expected signed artifact redirect')
    # The signed download request intentionally carries no GitHub authorization header.
    with urllib.request.urlopen(location,timeout=120) as response:raw=response.read(900_000_001)
    if len(raw)>900_000_000 or artifact.get('digest')!='sha256:'+hashlib.sha256(raw).hexdigest():
        raise ReleaseGateError('Artifact transport digest mismatch')
    files=validate_kit(raw,args.commit,args.version)
    evidence='\n'.join('- '+path+': '+str(run['html_url']) for path,run in checks.items())
    body=files['RELEASE_NOTES.md'].decode()+'\n\nVerified commit: `'+args.commit+'`\n\n'+evidence
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
    print(json.dumps({'release':release['html_url'],'draft':release['draft'],'commit':args.commit}))


if __name__=='__main__':
    main()
