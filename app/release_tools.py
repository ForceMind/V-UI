"""Offline, per-user release control. No root, daemon installation, or network calls."""
from __future__ import annotations
from contextlib import contextmanager
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import uuid
import tarfile
from urllib.error import HTTPError
from urllib.request import ProxyHandler, build_opener
import zipfile

PLATFORM = 'linux-multi-cpython312'
ARCHES={'x86_64':'x86_64','amd64':'x86_64','aarch64':'aarch64','arm64':'aarch64'}

def target_key() -> str:
    if platform.system()!='Linux': raise ReleaseError('V-UI server packages support Linux only')
    arch=ARCHES.get(platform.machine().lower())
    if not arch: raise ReleaseError('Unsupported CPU architecture: '+platform.machine())
    musl=bool(list(Path('/lib').glob('ld-musl-*.so.1')) or list(Path('/usr/lib').glob('ld-musl-*.so.1')))
    if not musl:
        name,_=platform.libc_ver(); musl=name.lower()=='musl'
    return arch+('-musl' if musl else '-gnu')

def target_arch() -> str:
    return target_key().split('-',1)[0]

def runtime_python(release: Path) -> Path:
    value=release/'runtime'/'python'/'bin'/'python3'
    if value.is_symlink():
        resolved=value.resolve()
        if release.resolve() not in resolved.parents: raise ReleaseError('Runtime Python symlink escapes release')
    if not value.exists(): raise ReleaseError('Portable Python runtime is missing')
    return value

def runtime_tree_digest(root: Path) -> str:
    digestor=hashlib.sha256()
    for path in sorted(root.rglob('*')):
        rel=path.relative_to(root).as_posix().encode()
        digestor.update(rel+b'\0')
        if path.is_symlink():
            digestor.update(b'L'+os.readlink(path).encode()+b'\0')
        elif path.is_file():
            digestor.update(b'F'+str(path.stat().st_mode & 0o777).encode()+b'\0')
            with path.open('rb') as handle:
                for chunk in iter(lambda:handle.read(1024*1024),b''):digestor.update(chunk)
        elif path.is_dir(): digestor.update(b'D\0')
        else: raise ReleaseError('Unsupported runtime file type')
    return digestor.hexdigest()

def _normalized_archive_path(value: PurePosixPath) -> tuple[str, ...]:
    if value.is_absolute():
        raise ReleaseError('Unsafe portable Python link')
    stack=[]
    for part in value.parts:
        if part in ('','.'): continue
        if part=='..':
            if not stack: raise ReleaseError('Unsafe portable Python link')
            stack.pop()
        else:
            if '\\' in part or any(ord(ch)<32 or ord(ch)==127 for ch in part):
                raise ReleaseError('Unsafe portable Python link')
            stack.append(part)
    if not stack or stack[0]!='python':
        raise ReleaseError('Portable Python link escapes runtime root')
    return tuple(stack)


def extract_runtime(archive_path: Path, destination: Path) -> None:
    destination.mkdir(parents=True,mode=0o700)
    try:
        with tarfile.open(archive_path,'r:gz') as archive:
            members=archive.getmembers()
            if len(members)>50000 or sum(max(0,m.size) for m in members)>650_000_000:
                raise ReleaseError('Portable Python archive exceeds limits')
            for member in members:
                name=PurePosixPath(member.name)
                parts=name.parts
                if (not parts or parts[0]!='python' or member.name.startswith('/')
                        or any(part in ('','..') for part in parts)):
                    raise ReleaseError('Unsafe portable Python archive path')
                if not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
                    raise ReleaseError('Portable Python archive contains unsupported entry')
                if member.issym():
                    _normalized_archive_path(name.parent / PurePosixPath(member.linkname))
                elif member.islnk():
                    _normalized_archive_path(PurePosixPath(member.linkname))
            archive.extractall(destination,filter='data')
    except (ReleaseError,tarfile.TarError,OSError,ValueError,KeyError) as exc:
        shutil.rmtree(destination,ignore_errors=True)
        if isinstance(exc,ReleaseError): raise
        raise ReleaseError('Portable Python extraction failed') from None
MAX_ARCHIVE = 850_000_000
MAX_EXPANDED = 1_100_000_000
MANIFEST = 'MANIFEST.json'

class ReleaseError(RuntimeError):
    pass

def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def safe_name(value: str) -> bool:
    return (isinstance(value, str) and bool(value) and len(value) < 512
        and not any(ord(c) < 32 or ord(c) == 127 or c == '\\' for c in value)
        and not value.startswith('/') and all(p not in ('', '.', '..') for p in value.split('/'))
        and str(PurePosixPath(value)) == value)

def private_root(root: Path) -> Path:
    root = Path(root).absolute()
    if root.is_symlink(): raise ReleaseError('Root must not be a symbolic link')
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if root.stat().st_uid != os.geteuid() or root.stat().st_mode & 0o077:
        raise ReleaseError('Root must be owned by the current user with mode 0700')
    return root.resolve()

def sync_directory(path: Path):
    directory = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(directory)
    finally: os.close(directory)

def atomic_json(path: Path, value: dict):
    raw = json.dumps(value, sort_keys=True, indent=2).encode()
    fd, name = tempfile.mkstemp(prefix='.commit-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(raw); handle.flush(); os.fsync(handle.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        if os.path.exists(name): os.unlink(name)

@contextmanager
def lease(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink(): raise ReleaseError('Refusing symbolic lock file')
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'a+b') as handle:
        try: fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise ReleaseError('Service or another operation is active; stop it first') from None
        try: yield
        finally: fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

@contextmanager
def stopped(root: Path):
    with lease(root / '.operation.lock'), lease(root / '.panel.lease'):
        if (root / 'RESTORE_PENDING.json').exists():
            raise ReleaseError('An interrupted restore requires recover-restore before other operations')
        yield

def supported_environment():
    key=target_key()
    if os.geteuid()==0: raise ReleaseError('Run release operations as the unprivileged service user, not root')
    if not hasattr(os,'pidfd_open'): raise ReleaseError('Linux pidfd support is required')
    fd=os.pidfd_open(os.getpid());os.close(fd)
    return key

def files_in(root: Path) -> dict:
    result = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink(): raise ReleaseError('Symbolic links are not permitted in payloads or backups')
        if path.is_dir(): continue
        if not path.is_file(): raise ReleaseError('Only regular files are permitted')
        name = path.relative_to(root).as_posix()
        if name == MANIFEST: continue
        if not safe_name(name): raise ReleaseError('Unsafe payload path')
        result[name] = {'sha256':digest(path.read_bytes()), 'size':path.stat().st_size,
                       'mode':0o700 if name.startswith('cores/') and path.name in ('xray','sing-box') else 0o600}
    return result

def create_archive(payload: Path, destination: Path, metadata: dict) -> str:
    if destination.exists(): raise ReleaseError('Destination already exists')
    manifest = {**metadata, 'schema':1, 'files':files_in(payload)}
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as output, zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(MANIFEST, json.dumps(manifest, sort_keys=True, indent=2))
        for name, info in manifest['files'].items():
            entry = zipfile.ZipInfo(name); entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = (stat.S_IFREG | info['mode']) << 16
            archive.writestr(entry, (payload / name).read_bytes())
    with destination.open('rb') as handle: os.fsync(handle.fileno())
    sync_directory(destination.parent)
    return digest(destination.read_bytes())

def unpack_verified(archive_path: Path, expected_sha: str, destination: Path) -> dict:
    if not re.fullmatch(r'[a-f0-9]{64}', expected_sha): raise ReleaseError('Expected archive SHA-256 is required')
    with archive_path.open('rb') as handle: raw = handle.read(MAX_ARCHIVE + 1)
    if len(raw) > MAX_ARCHIVE or digest(raw) != expected_sha: raise ReleaseError('Archive checksum mismatch')
    if destination.exists(): raise ReleaseError('Extraction requires a new directory')
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = archive.infolist()
            names = [entry.filename for entry in entries]
            if len(names) > 10000 or len(set(names)) != len(names) or not all(safe_name(n) for n in names):
                raise ReleaseError('Duplicate or unsafe archive paths')
            if MANIFEST not in names or sum(e.file_size for e in entries) > MAX_EXPANDED:
                raise ReleaseError('Missing manifest or archive exceeds expanded size limit')
            for entry in entries:
                kind = stat.S_IFMT(entry.external_attr >> 16)
                if entry.is_dir() or kind not in (0, stat.S_IFREG) or entry.flag_bits & 1:
                    raise ReleaseError('Archive contains a non-regular or encrypted entry')
            if archive.getinfo(MANIFEST).file_size > 4_000_000: raise ReleaseError('Manifest exceeds size limit')
            manifest = json.loads(archive.read(MANIFEST))
            if manifest.get('schema') != 1 or not isinstance(manifest.get('files'),dict): raise ReleaseError('Invalid manifest')
            if set(names) != set(manifest['files']) | {MANIFEST}: raise ReleaseError('Manifest file set mismatch')
            destination.mkdir(mode=0o700, parents=True)
            for name, info in manifest['files'].items():
                if info['mode'] not in (0o600, 0o700) or not re.fullmatch(r'[a-f0-9]{64}', info['sha256']):
                    raise ReleaseError('Invalid manifest entry')
                data = archive.read(name)
                if len(data) != info['size'] or digest(data) != info['sha256']: raise ReleaseError('Payload checksum mismatch')
                target = destination / name; target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                target.write_bytes(data); target.chmod(info['mode'])
            (destination / MANIFEST).write_text(json.dumps(manifest,sort_keys=True,indent=2))
            (destination / MANIFEST).chmod(0o600)
            return manifest
    except (OSError, ValueError, KeyError, TypeError, AttributeError, zipfile.BadZipFile, RuntimeError) as exc:
        if destination.exists(): shutil.rmtree(destination)
        if isinstance(exc, ReleaseError): raise
        raise ReleaseError('Archive validation or extraction failed') from None

def verify_payload(payload: Path) -> dict:
    try:
        manifest = json.loads((payload / MANIFEST).read_text())
        if manifest.get('schema') != 1 or files_in(payload) != manifest['files']:
            raise ReleaseError('Installed payload differs from its manifest')
        for name, info in manifest['files'].items():
            path = payload / name
            if stat.S_IMODE(path.stat().st_mode) != info['mode'] or path.stat().st_uid != os.geteuid():
                raise ReleaseError('Payload file ownership or mode changed')
        if (payload / MANIFEST).stat().st_mode & 0o077:
            raise ReleaseError('Manifest must be private')
        return manifest
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        raise ReleaseError('Installed payload cannot be verified') from None

def child_env(payload: Path, data: Path) -> dict:
    env = {k:v for k,v in os.environ.items() if not k.startswith(('PIP_', 'PYTHON'))}
    env.update(PYTHONPATH=str(payload), PYTHONDONTWRITEBYTECODE='1', VUI_DATA_DIR=str(data),
               VUI_BIN_DIR=str(payload/'cores'/target_arch()), VUI_PUBLIC_ORIGIN='', VUI_RELEASE_ROOT='')
    return env

def health_check(release: Path):
    payload = release / 'payload'; verify_payload(payload)
    verify_runtime(release)
    python = runtime_python(release)
    with tempfile.TemporaryDirectory(prefix='vui-candidate-') as directory:
        data = Path(directory)
        env = child_env(payload, data/'data'); env['VUI_BIN_DIR']=str(data/'no-cores')
        log_path = data/'health.log'
        with log_path.open('w') as log:
            process = subprocess.Popen([str(python),'-B','-m','uvicorn','main:app','--host','127.0.0.1','--port','0',
                '--no-proxy-headers','--no-use-colors'],cwd=payload,env=env,stdout=log,stderr=subprocess.STDOUT)
            try:
                deadline = time.monotonic()+20; match = None
                while time.monotonic() < deadline:
                    if process.poll() is not None: raise ReleaseError('Candidate failed startup health check')
                    match = re.search(r'Uvicorn running on http://127\.0\.0\.1:(\d+)', log_path.read_text())
                    if match: break
                    time.sleep(.05)
                if not match: raise ReleaseError('Candidate startup health check timed out')
                base='http://127.0.0.1:'+match.group(1); opener=build_opener(ProxyHandler({}))
                with opener.open(base+'/login',timeout=3) as response:
                    if response.status != 200: raise ReleaseError('Candidate login page unavailable')
                try: opener.open(base+'/api/auth/me',timeout=3)
                except HTTPError as exc:
                    if exc.code != 401: raise ReleaseError('Candidate authentication boundary failed') from None
                else: raise ReleaseError('Candidate allowed an anonymous administrator request')
            finally:
                process.terminate()
                try: process.wait(timeout=10)
                except subprocess.TimeoutExpired: process.kill();process.wait(timeout=5)

def release_id(value: str) -> str:
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,100}',value):
        raise ReleaseError('Invalid release identifier')
    return value

def verify_runtime(release: Path) -> dict:
    ready=json.loads((release/'READY.json').read_text())
    if ready.get('runtime_key')!=target_key(): raise ReleaseError('Prepared release runtime does not match this host')
    if runtime_tree_digest(release/'runtime')!=ready.get('runtime_tree_sha256'):
        raise ReleaseError('Portable runtime changed after preparation')
    return ready

def stage(archive: Path, expected_sha: str, root: Path) -> str:
    key=supported_environment(); root=private_root(root)
    with lease(root/'.operation.lock'), tempfile.TemporaryDirectory(prefix='.stage-',dir=root) as temporary:
        payload=Path(temporary)/'payload'; meta=unpack_verified(archive,expected_sha,payload)
        if meta.get('kind')!='release' or meta.get('platform')!=PLATFORM or key not in meta.get('targets',[]):
            raise ReleaseError('Release archive does not support this Linux target: '+key)
        identity=release_id(meta.get('release_id'))
        releases=root/'releases'
        if releases.is_symlink(): raise ReleaseError('Release parent cannot be a symbolic link')
        releases.mkdir(mode=0o700,exist_ok=True)
        final=releases/identity
        if final.exists() or final.is_symlink(): raise ReleaseError('Release already exists; never overwrite a prepared version')
        final.mkdir(parents=True,mode=0o700)
        try:
            os.replace(payload,final/'payload')
            runtime_archive=final/'payload'/'runtimes'/(key+'.tar.gz')
            pin=(meta.get('portable_runtime_pins') or {}).get(key,{})
            if not runtime_archive.is_file() or digest(runtime_archive.read_bytes())!=pin.get('sha256'):
                raise ReleaseError('Portable Python runtime pin mismatch')
            extract_runtime(runtime_archive,final/'runtime')
            python=runtime_python(final)
            env=child_env(final/'payload',root/'data')
            subprocess.run([str(python),'-m','ensurepip','--upgrade'],env=env,check=True,timeout=60,stdout=subprocess.DEVNULL)
            lock=final/'payload'/('requirements.'+key+'.lock')
            wheels=final/'payload'/'wheels'/key
            subprocess.run([str(python),'-m','pip','--isolated','--disable-pip-version-check','install',
                '--no-index','--only-binary=:all:','--require-hashes','--find-links',str(wheels),'-r',str(lock)],
                env=env,check=True,timeout=180)
            subprocess.run([str(python),'-m','pip','--isolated','check'],env=env,check=True,timeout=20)
            ready={'archive_sha256':expected_sha,'manifest_sha256':digest((final/'payload'/MANIFEST).read_bytes()),
                   'runtime_key':key,'runtime_archive_sha256':pin['sha256'],
                   'runtime_tree_sha256':runtime_tree_digest(final/'runtime')}
            atomic_json(final/'READY.json',ready)
            health_check(final)
            return identity
        except Exception:
            shutil.rmtree(final); raise

def active(root: Path) -> tuple[Path,dict]:
    try:
        current=json.loads((root/'CURRENT.json').read_text()); identity=release_id(current['release_id'])
        release=root/'releases'/identity
        if release.is_symlink(): raise ReleaseError('Release must not be a symbolic link')
        meta=verify_payload(release/'payload'); ready=verify_runtime(release)
        if digest((release/'payload'/MANIFEST).read_bytes()) != ready['manifest_sha256']:
            raise ReleaseError('Release manifest changed after health check')
        if meta['release_id'] != identity: raise ReleaseError('Release identity mismatch')
        return release,current
    except (OSError,ValueError,KeyError,TypeError): raise ReleaseError('No verified active release') from None

def activate(root: Path, identity: str):
    root=private_root(root); identity=release_id(identity)
    with stopped(root):
        release=root/'releases'/identity
        if release.is_symlink() or (root/'releases').is_symlink(): raise ReleaseError('Symbolic release is not permitted')
        meta=verify_payload(release/'payload')
        ready=verify_runtime(release)
        if digest((release/'payload'/MANIFEST).read_bytes()) != ready['manifest_sha256'] or meta['release_id'] != identity:
            raise ReleaseError('Release not prepared')
        health_check(release)
        previous=active(root)[1]['release_id'] if (root/'CURRENT.json').exists() else None
        (root/'data').mkdir(mode=0o700,exist_ok=True)
        atomic_json(root/'CURRENT.json',{'release_id':identity,'previous_id':previous})

def backup(root: Path, destination: Path) -> str:
    root=private_root(root)
    with stopped(root), tempfile.TemporaryDirectory(prefix='.backup-',dir=root) as temporary:
        data=root/'data'; work=Path(temporary)/'data'; work.mkdir(mode=0o700)
        if not data.is_dir(): raise ReleaseError('No data directory to back up')
        for name in files_in(data):
            if name.endswith(('owner.lock','.db-wal','.db-shm')) or name.startswith('.'): continue
            if name=='v-ui.db': continue
            target=work/name; target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            shutil.copyfile(data/name,target);target.chmod(0o600)
        if (data/'v-ui.db').is_file():
            with sqlite3.connect(f'file:{data / "v-ui.db"}?mode=ro',uri=True) as src, sqlite3.connect(work/'v-ui.db') as dst:
                src.backup(dst)
                if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok': raise ReleaseError('Database integrity check failed')
        return create_archive(work,destination,{'kind':'backup','data_path':str(data),'platform':PLATFORM})

def recover_restore(root: Path):
    root=private_root(root)
    with lease(root/'.operation.lock'), lease(root/'.panel.lease'):
        journal=root/'RESTORE_PENDING.json'
        if not journal.exists(): return
        value=json.loads(journal.read_text()); old=value['old']
        if not safe_name(old) or not old.startswith('recovery/before-'):
            raise ReleaseError('Invalid recovery journal; manual inspection required')
        previous=root/old; data=root/'data'
        if previous.is_dir():
            if data.exists(): os.replace(data,root/'recovery'/('interrupted-'+uuid.uuid4().hex))
            os.replace(previous,data)
        elif not data.exists(): raise ReleaseError('Original data is missing; restore from an external backup')
        sync_directory(root/'recovery');sync_directory(root)
        journal.unlink();sync_directory(root)

def restore(root: Path, archive: Path, expected_sha: str):
    root=private_root(root)
    with stopped(root), tempfile.TemporaryDirectory(prefix='.restore-',dir=root) as temporary:
        work=Path(temporary)/'data'; meta=unpack_verified(archive,expected_sha,work)
        if meta.get('kind') != 'backup' or meta.get('data_path') != str(root/'data'):
            raise ReleaseError('Backup belongs to a different data path; automatic path rewriting is not supported')
        (work/MANIFEST).unlink()
        db_path=work/'v-ui.db'
        if not db_path.is_file(): raise ReleaseError('Backup has no database')
        with sqlite3.connect(db_path) as db:
            if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok': raise ReleaseError('Backup database is damaged')
            tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {'users','inbounds','admin_sessions','subscription_grants'} <= tables:
                raise ReleaseError('Unsupported backup database schema')
            # Restoring must never resurrect previously revoked access tokens.
            db.execute('DELETE FROM admin_sessions'); db.execute('UPDATE subscription_grants SET revoked=1'); db.commit()
        with db_path.open('rb') as handle: os.fsync(handle.fileno())
        if not (root/'data').is_dir() or (root/'data').is_symlink():
            raise ReleaseError('Existing data directory is required for a reversible restore')
        old='recovery/before-'+uuid.uuid4().hex; (root/'recovery').mkdir(exist_ok=True,mode=0o700)
        atomic_json(root/'RESTORE_PENDING.json',{'old':old})
        try:
            os.replace(root/'data',root/old)
            sync_directory(root/'recovery');sync_directory(root)
            os.replace(work,root/'data')
            sync_directory(root)
            (root/'RESTORE_PENDING.json').unlink();sync_directory(root)
        except OSError:
            if (root/old).exists() and not (root/'data').exists():
                os.replace(root/old,root/'data');sync_directory(root)
                (root/'RESTORE_PENDING.json').unlink();sync_directory(root)
            raise ReleaseError('Restore interrupted; original data retained. Run recover-restore before service startup.') from None
