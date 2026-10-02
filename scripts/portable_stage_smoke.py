"""Stage and boot the exact target bundle without relying on the distro Python runtime."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import subprocess
from app import release_tools

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--bundle",type=Path,required=True)
    p.add_argument("--sha256",required=True)
    p.add_argument("--root",type=Path,required=True)
    args=p.parse_args()
    args.root.mkdir(parents=True,exist_ok=True,mode=0o700)
    identity=release_tools.stage(args.bundle,args.sha256,args.root)
    release=args.root/"releases"/identity
    ready=release_tools.verify_runtime(release)
    python=release_tools.runtime_python(release)
    env=release_tools.child_env(release/"payload",args.root/"data")
    probe=subprocess.run([str(python),"-B","-c",
        "import fastapi,pydantic_core,cryptography,sqlalchemy,certbot;print('runtime-imports-ok')"],
        cwd=release/"payload",env=env,capture_output=True,text=True,timeout=30)
    if probe.returncode:
        raise SystemExit(probe.stderr)
    arch=ready["runtime_key"].split("-",1)[0]
    versions={}
    for name,args_ in (("sing-box",["version"]),("xray",["version"])):
        binary=release/"payload"/"cores"/arch/name
        result=subprocess.run([str(binary),*args_],capture_output=True,text=True,timeout=10)
        if result.returncode:
            raise SystemExit((result.stdout or "")+(result.stderr or ""))
        versions[name]=((result.stdout or "")+(result.stderr or "")).splitlines()[0]
    print(json.dumps({"target":ready["runtime_key"],"release_id":identity,
                      "runtime":probe.stdout.strip(),"cores":versions}))

if __name__=="__main__":main()
