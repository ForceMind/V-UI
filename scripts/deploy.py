"""Offline per-user deployment controller. Read docs/DEPLOYMENT_RC2.md first."""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import subprocess
import sys
sys.dont_write_bytecode=True
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.release_tools import (ReleaseError,active,activate,backup,child_env,private_root,
    recover_restore,restore,stage,stopped,supported_environment)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True)
    commands=p.add_subparsers(dest='command',required=True)
    install=commands.add_parser('stage');install.add_argument('archive',type=Path);install.add_argument('--sha256',required=True)
    switch=commands.add_parser('activate');switch.add_argument('release_id')
    commands.add_parser('rollback');commands.add_parser('status');commands.add_parser('recover-restore')
    copy=commands.add_parser('backup');copy.add_argument('destination',type=Path)
    undo=commands.add_parser('restore');undo.add_argument('archive',type=Path);undo.add_argument('--sha256',required=True)
    admin=commands.add_parser('admin');admin.add_argument('action',choices=['create','set-password']);admin.add_argument('username')
    launch=commands.add_parser('run')
    launch.add_argument('--origin',required=True);launch.add_argument('--cert');launch.add_argument('--key')
    launch.add_argument('--bind',default='127.0.0.1');launch.add_argument('--port',type=int,default=8443)
    args=p.parse_args();supported_environment();os.umask(0o077);root=private_root(args.root)
    if args.command=='stage': print(stage(args.archive,args.sha256,root))
    elif args.command=='activate': activate(root,args.release_id);print('Activated verified release; start explicitly after checking configuration')
    elif args.command=='rollback':
        previous=active(root)[1].get('previous_id')
        if not previous: raise ReleaseError('No prepared previous release')
        activate(root,previous);print('Previous verified code selected; data was not rolled back')
    elif args.command=='status': print(active(root)[1])
    elif args.command=='backup': print('Backup SHA-256: '+backup(root,args.destination))
    elif args.command=='restore': restore(root,args.archive,args.sha256);print('Data restored; all old sessions and subscription grants revoked')
    elif args.command=='recover-restore': recover_restore(root);print('Interrupted restore recovery checked')
    else:
        release,_=active(root);payload=release/'payload';python=release/'venv'/'bin'/'python'
        env=child_env(payload,root/'data')
        if args.command=='admin':
            with stopped(root):
                result=subprocess.run([str(python),'-B','-m','app.admin',args.action,args.username],cwd=payload,env=env)
                raise SystemExit(result.returncode)
        else:
            command=[str(python),'-B','-m','app.serve','--root',str(root),'--origin',args.origin,
                     '--bind',args.bind,'--port',str(args.port)]
            if args.cert or args.key:
                if not (args.cert and args.key):raise ReleaseError('Provide both certificate and private key')
                command += ['--cert',str(Path(args.cert).absolute()),'--key',str(Path(args.key).absolute())]
            os.chdir(payload);os.execve(python,command,env)


if __name__=='__main__':
    try: main()
    except (ReleaseError,ValueError,OSError,subprocess.SubprocessError) as exc:
        print('Operation refused: '+str(exc),file=sys.stderr);raise SystemExit(1)
