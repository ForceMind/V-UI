"""Linux-only kernel fixture: a fake Python checker leaves a live child."""
import fcntl
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest


@unittest.skipUnless(sys.platform=='linux','Linux child adoption and isolated group signals')
class NativeCheckProcessGroupTests(unittest.TestCase):
    def test_python_fixture_descendant_forces_group_abort_before_clean_return(self):
        # Start a worker fixture as a session/group leader. The fake core
        # leaves a same-group child holding an independent flock after exit.
        core_code = """
import fcntl, os, sys, time
from pathlib import Path
work = Path(sys.argv[1])
if os.fork() == 0:
    with (work/'descendant.lock').open('wb') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        (work/'descendant-ready').touch()
        time.sleep(30)
    os._exit(0)
while not (work/'descendant-ready').exists():
    time.sleep(.01)
"""
        worker_code = """
import sys
from pathlib import Path
from app.core_updates.maintenance import _command
work = Path(sys.argv[1])
_command([sys.executable, '-c', 'pass'], timeout=3)
(work/'clean-command').touch()
_command([sys.executable, '-c', sys.argv[2], str(work)], timeout=3)
(work/'incorrect-success').touch()
"""
        with tempfile.TemporaryDirectory(prefix='vui-core-check-group-') as temporary:
            work=Path(temporary)
            worker=subprocess.Popen([sys.executable,'-c',worker_code,str(work),core_code],
                cwd=Path(__file__).resolve().parents[1], start_new_session=True,
                stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            flags=os.WEXITED|os.WNOHANG|os.WNOWAIT
            try:
                deadline=time.monotonic()+8
                observed=None
                while observed is None and time.monotonic()<deadline:
                    observed=os.waitid(os.P_PID,worker.pid,flags)
                    if observed is None:time.sleep(.01)
                self.assertIsNotNone(observed)
                self.assertTrue((work/'clean-command').exists())
                self.assertTrue((work/'descendant-ready').exists())
                self.assertFalse((work/'incorrect-success').exists())
                self.assertEqual((observed.si_code,observed.si_status),(os.CLD_KILLED,signal.SIGKILL))
                with (work/'descendant.lock').open('rb') as lock:
                    deadline=time.monotonic()+2
                    while True:
                        try:
                            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                            break
                        except BlockingIOError:
                            if time.monotonic()>=deadline:raise
                            time.sleep(.01)
            finally:
                # Keep the original leader unreaped until the last group
                # signal. This also cleans the fixture against the old bug.
                os.waitid(os.P_PID,worker.pid,flags)
                try:os.killpg(worker.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                worker.wait(timeout=3)
