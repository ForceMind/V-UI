"""Panel-only allocator defaults preserve authentication and environment boundaries."""
import json
import os
import subprocess
import sys
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from deploy import system_launcher as launcher
from scripts import deploy
from app.certificates.provider import CertbotProvider


class PanelAllocatorTests(unittest.TestCase):
    def test_exact_runtime_and_explicit_overrides_without_mutation(self):
        for target in ('x86_64-gnu','aarch64-gnu','x86_64-musl','aarch64-musl'):
            original={'PATH':'/safe'}
            result=launcher.panel_environment(original,target)
            self.assertEqual(original,{'PATH':'/safe'})
            self.assertEqual(result.get('MALLOC_MMAP_THRESHOLD_'),'131072' if target.endswith('-gnu') else None)
            configured=dict(original,MALLOC_MMAP_THRESHOLD_='262144',GLIBC_TUNABLES='glibc.malloc.mmap_threshold=524288')
            self.assertEqual(launcher.panel_environment(configured,target),configured)
        for target in ('x86_64','unknown-gnu',None):
            with self.assertRaises(RuntimeError):launcher.panel_environment({},target)

    def test_system_launch_defaults_and_strict_filter_in_all_modes(self):
        config={'origin':'https://127.0.0.1:8443','bind':'127.0.0.1','port':8443,'certificate_mode':'provided'}
        for target in ('x86_64-gnu','aarch64-gnu','x86_64-musl','aarch64-musl'):
            for mode in ('panel','http01','http01-direct'):
                with self.subTest(target=target,mode=mode), patch.object(launcher,'load_selected',return_value=(config,Path('/release'),Path('/payload'),Path('/runtime/python'),target)),patch.object(launcher.sys,'argv',['launcher',mode]),patch.object(launcher.os,'chdir'),patch.object(launcher.os,'execve') as execute,patch.dict(os.environ,{'MALLOC_MMAP_THRESHOLD_':'262144','GLIBC_TUNABLES':'glibc.malloc.mmap_threshold=524288','LD_PRELOAD':'/not/allowed','PYTHONPATH':'/not/allowed','LISTEN_PID':'123'},clear=True):
                    launcher.main();environment=execute.call_args.args[2]
                    self.assertEqual(environment.get('MALLOC_MMAP_THRESHOLD_'),'262144' if mode=='panel' and target.endswith('-gnu') else None)
                    self.assertNotIn('GLIBC_TUNABLES',environment);self.assertNotIn('LD_PRELOAD',environment);self.assertNotIn('PYTHONPATH',environment)
                    self.assertEqual(environment['LISTEN_PID'],'123')
                    self.assertEqual(environment['VUI_BIN_DIR'],'/payload/cores/'+target.split('-')[0])
        self.assertEqual(launcher.system_panel_environment({},'x86_64-gnu',{})['MALLOC_MMAP_THRESHOLD_'],'131072')

    def test_new_system_input_is_bounded_ascii_decimal_and_never_silently_replaced(self):
        for value in ('0','000131072',str(2**64-1)):
            self.assertEqual(launcher.system_panel_environment({},'x86_64-gnu',{'MALLOC_MMAP_THRESHOLD_':value})['MALLOC_MMAP_THRESHOLD_'],value)
        for value in ('','-1','+1','0x20000','1.5',' 131072','１２３',str(2**64),'1'*100):
            with self.subTest(value=value),self.assertRaises(RuntimeError):launcher.system_panel_environment({},'x86_64-gnu',{'MALLOC_MMAP_THRESHOLD_':value})

    def test_nonroot_run_uses_selected_runtime_before_exec(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);release=root/'release';release.mkdir()
            for target in ('x86_64-gnu','aarch64-musl'):
                (release/'READY.json').write_text(json.dumps({'runtime_key':target}))
                with self.subTest(target=target),patch.object(deploy,'supported_environment'),patch.object(deploy,'active',return_value=(release,{})),patch.object(deploy.sys,'argv',['deploy','--root',str(root),'run','--origin','https://127.0.0.1:8443']),patch.object(deploy.os,'chdir'),patch.object(deploy.os,'execve') as execute,patch.dict(os.environ,{},clear=True):
                    deploy.main();environment=execute.call_args.args[2]
                    self.assertEqual(environment.get('MALLOC_MMAP_THRESHOLD_'),'131072' if target.endswith('-gnu') else None)
                    self.assertIn('app.serve',execute.call_args.args[1])

    def test_direct_controller_entrypoint_resolves_deploy_package(self):
        source=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);release=root/'release';release.mkdir()
            (release/'READY.json').write_text(json.dumps({'runtime_key':'x86_64-gnu'}))
            program="""
import json,os,runpy,sys
from pathlib import Path
from unittest.mock import patch
source,root=map(Path,sys.argv[1:])
sys.path.insert(0,str(source/'scripts'))
sys.path.insert(0,str(source))
from app import release_tools
sys.argv=['deploy','--root',str(root),'run','--origin','https://127.0.0.1:8443']
with patch.object(release_tools,'supported_environment'),patch.object(release_tools,'active',return_value=(root/'release',{})),patch.object(os,'chdir'),patch.object(os,'execve') as execute:
    runpy.run_path(str(source/'scripts/deploy.py'),run_name='__main__')
    print(json.dumps({'threshold':execute.call_args.args[2].get('MALLOC_MMAP_THRESHOLD_')}))
"""
            environment={key:value for key,value in os.environ.items() if key not in ('MALLOC_MMAP_THRESHOLD_','GLIBC_TUNABLES')}
            result=subprocess.run([sys.executable,'-c',program,str(source),str(root)],cwd=root,env=environment,text=True,capture_output=True,timeout=15,check=True)
            self.assertEqual(json.loads(result.stdout),{'threshold':'131072'})

    def test_certbot_inherits_panel_allocator_but_not_proxy_or_python_injection(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);(root/'fullchain.pem').write_bytes(b'synthetic-provider-output')
            provider=CertbotProvider(root/'managed',test_directory='https://127.0.0.1:12345/dir',test_ca=root/'fake-ca')
            process=Mock(returncode=0);process.poll.return_value=0
            environment=launcher.panel_environment({'HTTPS_PROXY':'http://invalid','PYTHONPATH':'/invalid'},'x86_64-gnu')
            with patch.dict(os.environ,environment,clear=True),patch('app.certificates.provider.subprocess.Popen',return_value=process) as popen:
                self.assertEqual(provider.issue({'environment':'production','email':'synthetic@example.test'},root,threading.Event()),b'synthetic-provider-output')
                actual=popen.call_args.kwargs['env']
                self.assertEqual(actual['MALLOC_MMAP_THRESHOLD_'],'131072')
                self.assertNotIn('HTTPS_PROXY',actual);self.assertNotIn('PYTHONPATH',actual)
