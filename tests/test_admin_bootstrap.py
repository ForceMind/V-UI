"""Private installer account protocol against isolated synthetic databases.

No installation, network, host configuration or real credentials are used.
Database-backed cases execute in the ordinary Linux Actions test environment.
"""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest


SOURCE = Path(__file__).resolve().parents[1]
PASSWORD = 'Synthetic-initial-account-password!'


class AdminBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.data = Path(self.work.name) / 'data'
        self.env = {**os.environ, 'VUI_DATA_DIR': str(self.data), 'PYTHONDONTWRITEBYTECODE': '1'}

    def invoke(self, action, request, *, stdin_file=False, stdout_file=False):
        command = [sys.executable, '-B', '-m', 'app.admin', action]
        self.assertNotIn(PASSWORD, ' '.join(command))
        self.assertFalse(any(PASSWORD in value for value in self.env.values()))
        raw = request if isinstance(request, bytes) else json.dumps(request).encode()
        if stdin_file or stdout_file:
            with tempfile.TemporaryFile() as inp, tempfile.TemporaryFile() as out:
                inp.write(raw); inp.seek(0)
                result = subprocess.run(command, cwd=SOURCE, env=self.env, timeout=30,
                    input=None if stdin_file else raw, stdin=inp if stdin_file else None,
                    stdout=out if stdout_file else subprocess.PIPE, stderr=subprocess.PIPE)
                if stdout_file:
                    out.seek(0); result.stdout = out.read()
        else:
            result = subprocess.run(command, cwd=SOURCE, env=self.env, input=raw,
                                    capture_output=True, timeout=30)
        self.assertNotIn(PASSWORD.encode(), result.stdout + result.stderr)
        return result

    def status(self):
        result = self.invoke('installer-status', {'version': 1})
        self.assertEqual(result.returncode, 0, 'Private account status did not complete')
        return json.loads(result.stdout)

    def create(self, username='synthetic-admin', password=PASSWORD):
        return self.invoke('installer-create', {'version': 1, 'username': username, 'password': password})

    def rows(self):
        with sqlite3.connect('file:' + str(self.data / 'v-ui.db') + '?mode=ro', uri=True) as db:
            return db.execute('SELECT id,username,password_hash,is_active FROM users ORDER BY id').fetchall()

    def test_empty_database_creation_and_existing_account_are_not_reinitialized(self):
        self.assertEqual(self.status(), {'version': 1, 'has_account': False})
        result = self.create()
        self.assertEqual(result.returncode, 0, 'Private account creation did not complete')
        self.assertEqual(json.loads(result.stdout), {'version': 1, 'created': True})
        self.assertEqual(result.stderr, b'')
        before = self.rows()
        self.assertEqual(len(before), 1)
        self.assertEqual(before[0][1], 'synthetic-admin')
        self.assertTrue(before[0][2].startswith('$argon2id$'))
        self.assertNotIn(PASSWORD, before[0][2])
        self.assertEqual(self.status(), {'version': 1, 'has_account': True})
        refused = self.create('replacement-admin', 'Different-synthetic-password!')
        self.assertNotEqual(refused.returncode, 0)
        self.assertEqual(refused.stdout, b'')
        self.assertEqual(self.rows(), before)
        for path in self.data.rglob('*'):
            if path.is_file():
                self.assertNotIn(PASSWORD.encode(), path.read_bytes(), 'Plaintext password persisted in fixture data')

    def test_inactive_account_still_blocks_initialization_and_preserves_sessions(self):
        self.assertEqual(self.create().returncode, 0)
        with sqlite3.connect(self.data / 'v-ui.db') as db:
            db.execute('UPDATE users SET is_active=0')
            db.execute('INSERT INTO admin_sessions (token_hash,user_id,credential_stamp,created_at,expires_at) VALUES (?,?,?,?,?)',
                       ('a' * 64, 1, 'b' * 64, 0, 2147483647))
        before = self.rows()
        self.assertTrue(self.status()['has_account'])
        self.assertNotEqual(self.create('another-admin').returncode, 0)
        self.assertEqual(self.rows(), before)
        with sqlite3.connect(self.data / 'v-ui.db') as db:
            self.assertEqual(db.execute('SELECT count(*) FROM admin_sessions').fetchone()[0], 1)

    def test_parallel_initialization_commits_exactly_one_administrator(self):
        self.assertFalse(self.status()['has_account'])  # Complete schema initialization first.
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(self.create, ('synthetic-left', 'synthetic-right')))
        self.assertEqual(sorted(result.returncode for result in results), [0, 1])
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertIn(rows[0][1], ('synthetic-left', 'synthetic-right'))
        self.assertTrue(self.status()['has_account'])

    def test_malformed_overlong_and_wrong_shape_requests_are_safely_refused(self):
        requests = [b'{"password":"' + PASSWORD.encode(), b'x' * 4097,
                    {'version': True}, {'version': 2}, {'version': 1, 'extra': PASSWORD},
                    {'version': 1, 'username': ['bad'], 'password': PASSWORD},
                    {'version': 1, 'username': 'valid-admin', 'password': 12345},
                    {'version': 1, 'username': '--bad user', 'password': PASSWORD},
                    {'version': 1, 'username': 'valid-admin', 'password': 'short'}]
        self.assertFalse(self.status()['has_account'])
        for request in requests:
            with self.subTest(index=requests.index(request)):
                result = self.invoke('installer-create', request)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, b'')
                self.assertIn('安装器账户操作失败'.encode(), result.stderr)
                self.assertNotIn(b'Traceback', result.stderr)
                self.assertEqual(self.rows(), [])

    def test_regular_files_are_not_accepted_as_private_pipe_channels(self):
        for stdin_file, stdout_file in ((True, False), (False, True)):
            with self.subTest(stdin_file=stdin_file, stdout_file=stdout_file):
                result = self.invoke('installer-status', {'version': 1},
                                     stdin_file=stdin_file, stdout_file=stdout_file)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, b'')
                self.assertIn('安装器账户操作失败'.encode(), result.stderr)
                self.assertFalse((self.data / 'v-ui.db').exists())


if __name__ == '__main__':
    unittest.main()
