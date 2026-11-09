"""Private authority primitive on an empty GitHub-hosted managed test host.

No application or core mutation, grant DB access, real credentials, network or
remote-write API. Called as Root only by the installed core-switch CI fixture.
"""
from contextlib import ExitStack
import hashlib
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import types

CONTROL=Path('/usr/local/lib/v-ui')
AUTHORITY=Path('/var/lib/v-ui-maintenance/core-authority')


def require(value):
    if not value:
        raise ValueError('INSTALLED_AUTHORITY_ASSERTION')


def main():
    require(os.geteuid()==0 and os.getenv('GITHUB_ACTIONS')=='true'
            and os.getenv('RUNNER_ENVIRONMENT')=='github-hosted'
            and os.getenv('GITHUB_REPOSITORY')=='ForceMind/V-UI')
    require(len(sys.argv)==3 and re.fullmatch(r'[a-f0-9]{64}',sys.argv[1]) is not None
            and re.fullmatch(r'[a-f0-9]{40}',sys.argv[2]) is not None)
    require(AUTHORITY.is_dir() and not AUTHORITY.is_symlink())
    # Reuse the independent, receipt-before-compile bootstrap from the trusted
    # Root-owned CI fixture. Never ask an unverified system_ops to self-verify.
    bootstrap_path=Path(__file__).with_name('runner.py')
    require(bootstrap_path==Path('/run/vui-core-switch-fixture/runner.py'))
    parent=bootstrap_path.parent.lstat()
    require(stat.S_ISDIR(parent.st_mode) and parent.st_uid==0 and stat.S_IMODE(parent.st_mode)==0o700)
    with ExitStack() as stack:
        fd=os.open(bootstrap_path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        stack.callback(os.close,fd)
        def identity(info):
            return (info.st_dev,info.st_ino,info.st_mode,info.st_uid,info.st_gid,
                    info.st_nlink,info.st_size,info.st_mtime_ns,info.st_ctime_ns)
        before=os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_uid==0 and before.st_nlink==1
                and stat.S_IMODE(before.st_mode)==0o600 and 0<before.st_size<=4_000_000)
        raw=os.pread(fd,before.st_size+1,0)
        require(len(raw)==before.st_size and identity(os.fstat(fd))==identity(before)
                and identity(bootstrap_path.lstat())==identity(before))
        bootstrap=types.ModuleType('_vui_ci_authority_bootstrap')
        bootstrap.__file__=str(bootstrap_path)
        exec(compile(raw,str(bootstrap_path),'exec'),bootstrap.__dict__)
        bootstrap.fixed_directory(stack,bootstrap.FIXTURE,private=True)
        ops,_=bootstrap.installed_modules(stack)
        probe(ops)


def probe(ops):
    expected=hashlib.sha256(ops.trusted_bytes(ops.RECEIPT,ops.MAX_JSON)).hexdigest()
    module=ops._load_core_authority(expected)
    require(hashlib.sha256(ops.trusted_bytes(CONTROL/'core_authority.py')).hexdigest()==sys.argv[1])
    require(module.ROOT==AUTHORITY)
    manager,account=ops.verify_installation();require(manager=='systemd')
    authority=module.CoreAuthority()
    config=ops.decode_json(ops.trusted_bytes(ops.CONFIG,ops.MAX_JSON))
    accepted=authority.application(release_id=config['release_id'],target=config['target'])
    require(accepted is not None and accepted['app_source']==sys.argv[2])
    require(not authority.grants())
    fake=dict(main_id=2147483647,main_digest='a'*64)
    record,token=authority.issue(**fake,scopes=['core_operate'],seconds=60)
    parameters=dict(core='sing-box',release_id=1,selection_revision=None)
    intent,created=authority.reserve(**fake,token=token,request_id='1'*32,action='update',parameters=parameters)
    require(created)
    replay,created=authority.reserve(**fake,token=token,request_id='1'*32,action='update',parameters=parameters)
    require(not created and replay==intent)
    consumed=authority.enter_mutation(task_id=intent['id'],plan_sha256='b'*64)
    require(consumed['phase']=='mutating')
    try:
        authority.enter_mutation(task_id=intent['id'],plan_sha256='b'*64)
    except module.CoreAuthorityError as error:
        require(error.code=='CONSUMED')
    else:
        raise ValueError('REPEATED_CONSUMPTION')
    authority.revoke_main(**fake)
    try:
        authority.issue(**fake,scopes=['core_operate'],seconds=60)
    except module.CoreAuthorityError as error:
        require(error.code=='REVOKED_BINDING')
    else:
        raise ValueError('LATE_ISSUE')
    authority.advance_epoch()
    require(authority.application(release_id=config['release_id'],target=config['target'])==accepted)
    for path,mode in ((AUTHORITY,0o700),(AUTHORITY/'.lock',0o600),(AUTHORITY/'authority.json',0o600)):
        info=path.lstat()
        require(info.st_uid==0 and stat.S_IMODE(info.st_mode)==mode and not path.is_symlink())
    raw=ops.trusted_bytes(AUTHORITY/'authority.json',module.MAX_BYTES)
    require(token.encode('ascii') not in raw)
    # A real service-UID child receives neither token nor application code.
    child_code="""import importlib.util,os
from pathlib import Path
p=Path('/usr/local/lib/v-ui/core_authority.py')
s=importlib.util.spec_from_file_location('_denied_core_authority',p)
m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
try:m.CoreAuthority().grants()
except m.CoreAuthorityError as error:
 assert error.code=='OWNER'
else:raise AssertionError('owner guard absent')
try:fd=os.open('/var/lib/v-ui-maintenance/core-authority/authority.json',os.O_RDONLY|os.O_NOFOLLOW)
except PermissionError:pass
else:
 os.close(fd);raise AssertionError('private state readable')
print('OWNER_DENIED')
"""
    child=subprocess.run([str(ops.PYTHON),'-B','-I','-S','-c',child_code],
        user=account.pw_uid,group=account.pw_gid,extra_groups=[],cwd='/',env=ops.ENV,
        stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=15)
    require(child.returncode==0 and child.stdout==b'OWNER_DENIED\n')
    print('VUI_CORE_AUTHORITY_INSTALLED_ACCEPTANCE: Root receipt, accepted SDK app origin, private modes, real service-UID denial, durable replay, single consumption and revoke/late-issue refusal passed')


if __name__=='__main__':
    try:
        main()
    except Exception:
        print('VUI_CORE_AUTHORITY_INSTALLED_ACCEPTANCE_FAILED',file=sys.stderr)
        raise SystemExit(1)
