import copy
from contextlib import ExitStack
import os
from pathlib import Path
import shutil
import socket
import sys
import tempfile
import unittest

from scripts.low_resource_service_tree import (WatchedCore, allocator_fields,
    canonical_cgroup, identity, observed_allocator, validate_tree)
from deploy.system_launcher import panel_environment


class ServiceTreeTests(unittest.TestCase):
    def test_real_installed_watchdog_inherits_policy_and_cleans_group(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'app/services').mkdir(parents=True)
            shutil.copy(Path(__file__).resolve().parents[1] / 'app/services/core_child.py',
                        root / 'app/services/core_child.py')
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
            (root / 'run').write_text('import socket,time\ns=socket.socket()\ns.bind(("127.0.0.1",'+str(port)+'))\ns.listen()\ntime.sleep(60)\n')
            config = root / 'config.json'; config.write_text('{}')
            environment = panel_environment(dict(os.environ), 'x86_64-gnu')
            environment.pop('GLIBC_TUNABLES', None)
            with ExitStack() as stack:
                core = WatchedCore(stack, sys.executable, root, sys.executable, config,
                                   root / 'core.log', environment, 'x86_64-gnu')
                core.start(port)
                core.check()
                self.assertNotEqual(core.process.pid, core.core_pid)
                self.assertEqual(observed_allocator(core.core_pid), allocator_fields(environment))
                original = copy.deepcopy(core.roles)
                core.roles['core']['starttime_ticks'] += 1
                with self.assertRaisesRegex(RuntimeError, 'identity'): core.check()
                core.roles = original
            value = core.evidence()
            validate_tree(value, 'x86_64-gnu', os.getpid(), identity(os.getpid())['cgroup'])
            for path in ('policy', 'roles'):
                invalid = copy.deepcopy(value); invalid[path] = {}
                with self.assertRaises(RuntimeError):
                    validate_tree(invalid, 'x86_64-gnu', os.getpid(), identity(os.getpid())['cgroup'])
            for row in value['roles'].values():
                self.assertFalse(Path('/proc', str(row['pid'])).exists())

    def test_allowed_environment_fields_and_exact_cgroup(self):
        value = allocator_fields({'MALLOC_MMAP_THRESHOLD_':'131072', 'SECRET':'never record'})
        self.assertEqual(value, dict(mmap_threshold='131072', malloc_tunable_present=False))
        for bad in ('-1','١','18446744073709551616','1\n'):
            with self.assertRaises(RuntimeError): allocator_fields({'MALLOC_MMAP_THRESHOLD_':bad})
        self.assertEqual(canonical_cgroup('0::/system.slice/test.service','test.service'), '/sys/fs/cgroup/system.slice/test.service')
        for bad in ('0::/a/../test.service','0::/a//test.service','0::/wrong','1::/test.service','0::/test.service\n0::/test.service'):
            with self.assertRaises(RuntimeError): canonical_cgroup(bad,'test.service')
