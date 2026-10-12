from contextlib import ExitStack
import http.client
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from scripts.low_resource_service_tree import identity, observed_allocator
from scripts.low_resource_certificates import write, read, wait_file, wait_terminal, observed_handler
from scripts.low_resource_sustained import cleanup_process_group

SOURCE=Path(__file__).resolve().parents[1]


def start_responder(stack, root, *, port=0, fail_thread_start=False):
    request=dict(payload=str(SOURCE),root=str(root),unit='local-fixture',source_commit='b'*40,
                 manager_identity=identity(os.getpid()),http_port=port)
    phase=dict(phase=None,directory=None,manager_pid=os.getpid(),
               manager_starttime_ticks=identity(os.getpid())['starttime_ticks'],
               manager_allocator=observed_allocator(os.getpid()))
    write(root/'phase.json',phase);write(root/'responder-request.json',request)
    log=stack.enter_context((root/'responder.log').open('w'))
    code='from pathlib import Path; from scripts.low_resource_responder import serve; from scripts.low_resource_certificates import read; import sys; raise SystemExit(serve(read(Path(sys.argv[1])),Path(sys.argv[2])))'
    if fail_thread_start:
        code='import threading; threading.Thread.start=lambda self: (_ for _ in ()).throw(RuntimeError("synthetic thread start failure")); '+code
    env=dict(PATH=os.environ.get('PATH','/usr/bin:/bin'),HOME=str(root),VUI_DATA_DIR=str(root/'data'),PYTHONDONTWRITEBYTECODE='1')
    process=subprocess.Popen([sys.executable,'-B','-c',code,str(root/'responder-request.json'),str(root/'responder-report.json')],
        cwd=SOURCE,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
    stack.callback(cleanup_process_group,process,grace_seconds=12,kill_seconds=3)
    def alive():
        if process.poll() is not None:raise RuntimeError('Local responder exited: '+(root/'responder.log').read_text())
    ready=wait_file(root/'responder.json',time.monotonic()+10,alive)
    return process,ready,phase


class ResponderTests(unittest.TestCase):
    def test_real_independent_responder_readiness_policy_and_drained_stop(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            with ExitStack() as stack:
                process,ready,_=start_responder(stack,root)
                self.assertEqual(ready['identity']['ppid'],os.getpid())
                self.assertEqual(ready['allocator'],dict(mmap_threshold=None,malloc_tunable_present=False))
                client=http.client.HTTPConnection('127.0.0.1',ready['http_port'],timeout=3)
                client.request('GET','/missing');response=client.getresponse();self.assertEqual(response.status,404);response.read();client.close()
            result=read(root/'responder-report.json')
            self.assertEqual(process.returncode,0)
            self.assertEqual(result['outcome'],'passed');self.assertTrue(result['drained'])
            self.assertEqual(result['active_requests'],0)

    def test_bad_phase_and_checkpoint_do_not_change_real_response(self):
        from app.certificates.http01 import ChallengeServer,handler_for
        for fail in ('phase','checkpoint'):
            with tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary);webroot=root/'web';token='a'*24
                path=webroot/'.well-known/acme-challenge'/token;path.parent.mkdir(parents=True);path.write_text(token+'.fake')
                report=dict(challenges=[],certbot_processes=[],observation_errors=[])
                def phase():
                    if fail=='phase':raise ValueError('invalid fixture phase')
                    return dict(phase='issue',directory='https://127.0.0.1:1234/dir')
                def flush():raise OSError('unwritable fixture report')
                handler=observed_handler(handler_for(webroot),root,report,phase,threading.Lock(),on_change=flush)
                server=ChallengeServer(('127.0.0.1',0),handler)
                thread=threading.Thread(target=server.serve_forever);thread.start()
                try:
                    with patch('scripts.low_resource_certificates.observe_certbot',return_value=[dict(pid=1)]):
                        client=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
                        client.request('GET','/.well-known/acme-challenge/'+token);response=client.getresponse()
                        self.assertEqual(response.status,200);self.assertEqual(response.read(),(token+'.fake').encode());client.close()
                finally:server.shutdown();server.server_close();thread.join(3)
                self.assertTrue(report['observation_errors'])

    def test_terminal_wait_requires_successful_cleanup(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'report.json'
            for outcome in ('running','passed','failed'):
                write(path,dict(outcome=outcome,cleanup_complete=False))
                with self.assertRaises(RuntimeError):wait_terminal(path,time.monotonic())
            write(path,dict(outcome='passed',cleanup_complete=True))
            self.assertTrue(wait_terminal(path,time.monotonic()+1)['cleanup_complete'])

    def test_thread_start_failure_closes_listener_without_shutdown_deadlock(self):
        with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
            root=Path(temporary);started=time.monotonic()
            with self.assertRaisesRegex(RuntimeError,'Local responder exited'):
                start_responder(stack,root,fail_thread_start=True)
            self.assertLess(time.monotonic()-started,5)
            self.assertEqual(read(root/'responder-report.json')['outcome'],'failed')
