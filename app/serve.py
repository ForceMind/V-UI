"""The selected deployment entrypoint: unprivileged, verified package, direct HTTPS."""
from __future__ import annotations
import argparse
import ipaddress
import os
from pathlib import Path
import ssl
import sys
from urllib.parse import urlsplit
from app.release_tools import ReleaseError, active, lease, private_root, supported_environment


def private_file(value: str) -> Path:
    path=Path(value).absolute()
    if path.is_symlink() or not path.is_file() or path.stat().st_uid != os.geteuid() or path.stat().st_mode & 0o077:
        raise ReleaseError('Certificate/key must be a regular owner-only file owned by the service user')
    return path


def run() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--origin',required=True)
    parser.add_argument('--cert')
    parser.add_argument('--key')
    parser.add_argument('--bind',default='127.0.0.1')
    parser.add_argument('--port',type=int,default=8443)
    args=parser.parse_args()
    supported_environment(); os.umask(0o077)
    root=private_root(args.root)
    if (root/'RESTORE_PENDING.json').exists(): raise ReleaseError('Interrupted restore requires recover-restore')
    if not 1024 <= args.port <= 65535: raise ReleaseError('Use an unprivileged port in 1024–65535')
    ipaddress.ip_address(args.bind)
    os.environ['VUI_PUBLIC_ORIGIN']=args.origin
    os.environ.update(VUI_DATA_DIR=str(root/'data'))
    from app.middleware.auth import configured_origin
    origin=configured_origin()
    if not origin.startswith('https://') or (urlsplit(origin).port or 443) != args.port:
        raise ReleaseError('Direct HTTPS origin and listening port must match')
    with lease(root/'.panel.lease'):
        release,_=active(root); payload=release/'payload'
        if Path(__file__).resolve().parents[1] != payload.resolve():
            raise ReleaseError('Selected release changed; rerun the active release command')
        data=root/'data'
        if not data.is_dir() or data.is_symlink() or data.stat().st_uid != os.geteuid() or data.stat().st_mode & 0o077:
            raise ReleaseError('Data directory must be owned by service user with mode 0700')
        os.environ.update(VUI_DATA_DIR=str(data),VUI_BIN_DIR=str(payload/'cores'/active(root)[1]['runtime_key'].split('-',1)[0]),VUI_RELEASE_ROOT=str(root))
        # Only initialize the data store after taking the instance lease and
        # verifying the selected release. Bootstrap paths are not API inputs.
        from app.certificates.manager import get_manager
        from app.models.database import init_db
        from app.certificates.tls import LiveTLS
        init_db(); manager=get_manager()
        material=manager.panel_material()
        if material is None and not (args.cert and args.key):
            raise ReleaseError('No panel certificate. Complete certificate bootstrap or provide both --cert and --key')
        cert,key=(private_file(str(path)) for path in (material or (args.cert,args.key)))
        live_tls=LiveTLS(cert,key)
        os.environ['VUI_CERTIFICATES_ENABLED']='1'
        manager.panel_reloader=live_tls.reload
        import uvicorn
        import main
        print('V-UI verified release listening with HTTPS; no privileged ports or access-log tokens',flush=True)
        uvicorn.run(main.app,host=args.bind,port=args.port,workers=1,proxy_headers=False,
            access_log=False,server_header=False,ssl_certfile=str(cert),ssl_keyfile=str(key),
            limit_concurrency=64,timeout_keep_alive=5,ssl_context_factory=lambda config, default: live_tls.context)


if __name__=='__main__':
    try: run()
    except (ReleaseError,ValueError,OSError,ssl.SSLError) as exc:
        print('Startup refused: '+str(exc),file=sys.stderr);raise SystemExit(1)
