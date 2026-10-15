"""Stage and boot the exact target bundle without relying on the distro Python runtime."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app import release_tools
from scripts.ops_menu_smoke import installed_menu_smoke, portable_menu_stage_smoke
from scripts.managed_ops_smoke import installed_managed_ops_smoke
from scripts.proxy_import_smoke import installed_proxy_import_smoke

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
    # This is an already staged, disposable acceptance fixture. Select it only
    # for the menu consumer; the menu itself must never activate or create data.
    release_tools.atomic_json(args.root/"CURRENT.json",{"release_id":identity,"previous_id":None})
    menu=installed_menu_smoke(python,release/"payload",args.root)
    menu.update(installed_proxy_import_smoke(python,release/"payload",args.root))
    menu.update(portable_menu_stage_smoke(python,release/"payload",args.root,args.bundle,args.sha256,
        managed_smoke=installed_managed_ops_smoke))
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
                      "runtime":probe.stdout.strip(),"cores":versions,**menu}))

if __name__=="__main__":main()
