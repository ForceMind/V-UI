"""Synthetic status and temporary Python children only; never runs Tailscale."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch

from app.services import tailscale_status as service

RAW = {"Version": "1.104.2-tabcdef012-gabcdef123", "BackendState": "Running",
       "Self": {"Online": True, "HostName": "secret-host", "TailscaleIPs": ["100.64.0.7"]},
       "AuthURL": "https://secret.example.test/auth", "User": {"7": {"LoginName": "private@example.test"}},
       "Peer": {"secret-key": {"DNSName": "secret-peer"}}}


def raw(**changes):
    return json.dumps({**deepcopy(RAW), **changes}).encode()


class ProjectionTests(unittest.TestCase):
    def test_projection_states_and_no_sensitive_fields(self):
        self.assertEqual(service.project(raw()), ("online", "control_connected"))
        self.assertEqual(service.project(raw(Self={"Online": False})), ("offline", "control_disconnected"))
        self.assertEqual(service.project(raw(BackendState="Stopped", Self=None)), ("offline", "stopped"))
        for state in ("NoState", "NeedsLogin", "NeedsMachineAuth", "Starting"):
            self.assertEqual(service.project(raw(BackendState=state)), ("unknown", "not_ready"))
        self.assertNotIn("secret", str(service.project(raw())))

    def test_release_version_whitelist(self):
        for value in ("1.104.2", "1.104.2-tabcdef", "1.104.2-tabcdef-g123456"):
            self.assertEqual(service.project(raw(Version=value))[0], "online")
        for value in (None, 104, "1.104.20", "1.104.3", "1.104.2-dev20261021-tabcdef",
                      "1.104.2-tabc", "1.104.2-tabcdef-dirty", "1.104.2\n", "1.104.2-1-tabcdef"):
            with self.subTest(version=value), self.assertRaisesRegex(service.ObservationError, "unsupported_version"):
                service.project(raw(Version=value))

    def test_invalid_shapes_types_utf8_and_duplicate_fields(self):
        cases = [b"", b"\xff", b"[]", b"null", b"{}", b'{"Version":"1.104.2","Version":"1.104.2"}',
                 b'{"Version":"1.104.2","x":NaN}', b'{"x":' + b'[' * 2000 + b']' * 2000 + b'}',
                 raw(Self=None), raw(Self={}), raw(Self={"Online": "true"}), raw(Self={"Online": 1}),
                 raw(BackendState=True), raw(BackendState=[]), raw(BackendState="FutureState")]
        for value in cases:
            with self.subTest(raw=value[:80]), self.assertRaises(service.ObservationError):
                service.project(value)

    def test_byte_node_and_depth_limits(self):
        for value in (b" " * (service.MAX_BYTES + 1), raw(extra=[0] * 1025)):
            with self.assertRaisesRegex(service.ObservationError, "output_limit"):
                service.project(value)
        nested = 0
        for _ in range(18): nested = [nested]
        with self.assertRaisesRegex(service.ObservationError, "output_limit"):
            service.project(raw(extra=nested))


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.enabled = patch.dict(os.environ, {"VUI_TAILSCALE_STATUS_ENABLED": "1"})
        self.enabled.start()
        self.addCleanup(self.enabled.stop)
        self.now = 100.0
        self.calls = 0
        self.value = raw()
        self.subject = service.TailscaleStatus(_collector=self.collect, _clock=lambda: self.now, _wall=lambda: 1234)

    def collect(self):
        self.calls += 1
        if isinstance(self.value, BaseException): raise self.value
        return self.value

    def test_disabled_has_no_probe(self):
        for value in ("", "0", "true"):
            with patch.dict(os.environ, {"VUI_TAILSCALE_STATUS_ENABLED": value}):
                self.assertEqual(self.subject.read(), {"enabled": False})
        self.assertEqual(self.calls, 0)

    def test_result_allowlist_copy_and_remaining_monotonic_ttl(self):
        first = self.subject.read()
        self.assertEqual(set(first), {"enabled", "state", "reason", "observed_at", "stale", "valid_for_seconds"})
        self.assertEqual(first["state"], "online")
        self.assertEqual(first["observed_at"], 1234)
        first["state"] = "poison"
        self.now += 29
        cached = self.subject.read()
        self.assertEqual(cached["state"], "online")
        self.assertEqual(cached["valid_for_seconds"], 1)
        self.assertEqual(self.calls, 1)
        self.now += 1
        self.value = service.ObservationError("permission_denied")
        self.assertEqual(self.subject.read()["state"], "unknown")
        self.assertEqual(self.calls, 2)
        self.now += 29
        self.subject.read()
        self.assertEqual(self.calls, 2)

    def test_error_text_is_not_exposed(self):
        for error in (RuntimeError("secret-host"), service.ObservationError("secret-host")):
            self.value = error
            self.now += 30
            result = self.subject.read()
            self.assertEqual(result["state"], "unknown")
            self.assertEqual(result["reason"], "unavailable")
            self.assertNotIn("secret", json.dumps(result))

    def test_concurrent_requests_return_unknown_without_second_probe(self):
        entered, release = threading.Event(), threading.Event()
        def collect():
            entered.set(); release.wait(2); return raw()
        subject = service.TailscaleStatus(_collector=collect)
        result = []
        thread = threading.Thread(target=lambda: result.append(subject.read()))
        thread.start()
        try:
            self.assertTrue(entered.wait(1))
            self.assertEqual(subject.read()["reason"], "busy")
        finally:
            release.set(); thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result[0]["state"], "online")

    def test_expired_during_collection_never_caches_online(self):
        def collect(): self.now += 31; return raw()
        self.subject._collector = collect
        self.assertEqual(self.subject.read()["reason"], "expired")
        self.assertIsNone(self.subject._cached)

    def test_uncertain_cleanup_fuses_future_attempts(self):
        self.value = service.CleanupError("cleanup_failed")
        self.assertEqual(self.subject.read()["reason"], "cleanup_failed")
        self.now += 1000
        self.value = raw()
        self.assertEqual(self.subject.read()["reason"], "cleanup_failed")
        self.assertEqual(self.calls, 1)

    def test_base_exception_releases_lock_but_keeps_cooldown(self):
        self.value = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt): self.subject.read()
        self.assertEqual(self.subject.read()["state"], "unknown")
        self.assertEqual(self.calls, 1)


class CollectorTests(unittest.TestCase):
    def run_child(self, code, *, timeout=1.0):
        # Inject only at the process factory for tests. Production COMMAND remains
        # fixed; no API/configuration input can select this Python executable.
        original = subprocess.Popen
        children = []
        def launch(command, **kwargs):
            self.assertEqual(command, service.COMMAND)
            self.assertEqual(kwargs["env"], service.ENVIRONMENT)
            self.assertFalse(kwargs["shell"])
            self.assertTrue(kwargs["start_new_session"])
            self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
            self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
            child = original([sys.executable, "-c", code], **kwargs)
            children.append(child)
            return child
        try:
            with patch.object(service.subprocess, "Popen", side_effect=launch), patch.object(service, "EXECUTION_SECONDS", timeout):
                return service.collect()
        finally:
            for child in children:
                self.assertIsNotNone(child.poll(), "temporary child must be reaped")
                self.assertTrue(child.stdout.closed)

    def test_fixed_command_environment_and_successful_child(self):
        self.assertEqual(service.COMMAND, ("/usr/bin/tailscale", "--socket=/var/run/tailscale/tailscaled.sock",
                                         "status", "--json", "--peers=false"))
        value = self.run_child("import os; os.write(1," + repr(raw()) + ")")
        self.assertEqual(service.project(value)[0], "online")

    def test_group_signal_precedes_leader_reap_even_on_success(self):
        events = []
        original_waitid, original_killpg, original_wait = os.waitid, os.killpg, subprocess.Popen.wait
        def observe(pid_type, pid, flags):
            self.assertTrue(flags & os.WNOWAIT)
            events.append('observe')
            return original_waitid(pid_type, pid, flags)
        def kill(pid, sig):
            self.assertNotIn('reap', events)
            events.append('signal')
            return original_killpg(pid, sig)
        def wait(child, *args, **kwargs):
            self.assertIn('signal', events)
            events.append('reap')
            return original_wait(child, *args, **kwargs)
        with patch.object(os, 'waitid', side_effect=observe), patch.object(os, 'killpg', side_effect=kill), \
                patch.object(subprocess.Popen, 'wait', wait):
            self.run_child("print('{}')")
        self.assertIn('observe', events)
        self.assertLess(events.index('signal'), events.index('reap'))

    def test_timeout_and_output_flood_are_killed_and_reaped(self):
        for code, reason in (("import time; time.sleep(60)", "timeout"),
                             ("import os\nwhile True: os.write(1,b'x'*4096)", "output_limit")):
            start = time.monotonic()
            with self.subTest(reason=reason), self.assertRaisesRegex(service.ObservationError, reason):
                self.run_child(code, timeout=0.2)
            self.assertLess(time.monotonic() - start, 2)

    def test_eof_with_live_child_still_has_deadline(self):
        with self.assertRaisesRegex(service.ObservationError, "timeout"):
            self.run_child("import os,time; os.close(1); time.sleep(60)", timeout=0.2)

    def test_nonzero_and_stderr_are_not_exposed(self):
        with self.assertRaisesRegex(service.ObservationError, "^unavailable$"):
            self.run_child("import sys; print('secret-host',file=sys.stderr); sys.exit(7)")

    def test_exact_limit_is_accepted_and_next_byte_rejected(self):
        self.assertEqual(len(self.run_child("import os; os.write(1,b'x'*65536)")), 65536)
        with self.assertRaisesRegex(service.ObservationError, "output_limit"):
            self.run_child("import os; os.write(1,b'x'*65537)")

    def test_start_failures_have_fixed_codes(self):
        for error, code in ((FileNotFoundError("secret"), "unavailable"),
                            (PermissionError("secret"), "permission_denied"), (OSError("secret"), "unavailable")):
            with patch.object(service.subprocess, "Popen", side_effect=error), self.assertRaisesRegex(service.ObservationError, '^' + code + '$'):
                service.collect()

    def test_lost_child_identity_never_signals_group(self):
        from types import SimpleNamespace
        child = SimpleNamespace(pid=123456, wait=lambda **kwargs: self.fail('must not reap unowned child'))
        with patch.object(service.os, 'waitid', side_effect=ChildProcessError()), \
                patch.object(service.os, 'killpg') as kill:
            with self.assertRaisesRegex(service.CleanupError, '^cleanup_failed$'):
                service._reap(child)
            kill.assert_not_called()

    def test_external_reaper_during_collect_never_signals_recycled_identity(self):
        def externally_reaped(_kind, pid, _flags):
            os.waitpid(pid, 0)
            raise ChildProcessError()
        with patch.object(service.os, 'waitid', side_effect=externally_reaped), \
                patch.object(service.os, 'killpg') as kill:
            with self.assertRaisesRegex(service.CleanupError, '^cleanup_failed$'):
                self.run_child("print('{}')")
            kill.assert_not_called()

    def test_nondefault_sigchld_never_spawns(self):
        with patch.object(service.signal, 'getsignal', return_value=service.signal.SIG_IGN), \
                patch.object(service.subprocess, 'Popen') as launch:
            with self.assertRaisesRegex(service.ObservationError, '^unavailable$'):
                service.collect()
            launch.assert_not_called()

    def test_pipe_close_failure_cannot_hide_cleanup_fuse(self):
        from types import SimpleNamespace
        stream = SimpleNamespace(fileno=lambda: 42,
            close=lambda: (_ for _ in ()).throw(OSError('private close detail')))
        child = SimpleNamespace(pid=123456, stdout=stream)
        with patch.object(service.subprocess, 'Popen', return_value=child), \
                patch.object(service.os, 'set_blocking', side_effect=OSError('private read detail')), \
                patch.object(service, '_reap', side_effect=service.CleanupError('cleanup_failed')):
            with self.assertRaisesRegex(service.CleanupError, '^cleanup_failed$'):
                service.collect()

    def test_unsupported_platform_never_spawns(self):
        with patch.object(service.sys, 'platform', 'darwin'), patch.object(service.subprocess, 'Popen') as launch:
            with self.assertRaisesRegex(service.ObservationError, '^unavailable$'):
                service.collect()
            launch.assert_not_called()

    def test_cleanup_failure_is_distinct(self):
        from types import SimpleNamespace
        child = SimpleNamespace(pid=123456, wait=lambda **kwargs: None)
        with patch.object(service.os, "waitid", return_value=None), patch.object(service.os, "killpg", side_effect=PermissionError("secret")), self.assertRaises(service.CleanupError):
            service._reap(child)
        child.wait = lambda **kwargs: (_ for _ in ()).throw(subprocess.TimeoutExpired("secret", 1))
        with patch.object(service.os, "waitid", return_value=None), patch.object(service.os, "killpg"), self.assertRaises(service.CleanupError):
            service._reap(child)


class ApiTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        from fastapi import FastAPI
        from app.api import management
        from app.middleware.auth import AdminAuthMiddleware
        self.collect = patch.object(management.status, "read", return_value={"enabled": False})
        self.probe = self.collect.start()
        self.addCleanup(self.collect.stop)
        self.env = patch.dict(os.environ, {"VUI_PUBLIC_ORIGIN": "https://panel.example.test"})
        self.env.start(); self.addCleanup(self.env.stop)
        app = FastAPI()
        app.add_middleware(AdminAuthMiddleware)
        app.include_router(management.router, prefix="/api/management")
        self.client = TestClient(app, base_url="https://panel.example.test")

    def test_unauthenticated_and_wrong_host_never_probe(self):
        with patch("app.middleware.auth.authenticate", return_value=None):
            self.assertEqual(self.client.get("/api/management/tailscale").status_code, 401)
        with patch("app.middleware.auth.authenticate", return_value={"id": 1}):
            self.assertEqual(self.client.get("/api/management/tailscale", headers={"Host": "wrong.example.test"}).status_code, 403)
        self.probe.assert_not_called()

    def test_authenticated_read_and_no_store(self):
        with patch("app.middleware.auth.authenticate", return_value={"id": 1}):
            result = self.client.get("/api/management/tailscale")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json(), {"enabled": False})
        self.assertIn("no-store", result.headers["cache-control"])
        self.probe.assert_called_once_with()

    def test_query_body_and_write_methods_never_probe(self):
        with patch("app.middleware.auth.authenticate", return_value={"id": 1}):
            for request in (("GET", "/api/management/tailscale?command=up", {}),
                            ("GET", "/api/management/tailscale", {"content": b"secret"}),
                            ("POST", "/api/management/tailscale", {"headers": {"Origin": "https://panel.example.test", "X-VUI-Request": "1"}})):
                self.assertIn(self.client.request(request[0], request[1], **request[2]).status_code, (400, 405))
        self.probe.assert_not_called()


class JavaScriptTests(unittest.TestCase):
    def test_syntax_and_deterministic_dom_lifecycle(self):
        root = Path(__file__).resolve().parents[1]
        for command in (["node", "--check", "web/js/tailscale-status.js"],
                        ["node", "tests/tailscale_status_dom.js"]):
            result = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_hidden_markup_and_no_secret_storage_or_dynamic_html(self):
        root = Path(__file__).resolve().parents[1]
        html = (root / "web/proxy.html").read_text()
        script = (root / "web/js/tailscale-status.js").read_text()
        self.assertIn('id="tailscale-card" aria-labelledby="tailscale-heading" hidden', html)
        self.assertIn('/ui/js/tailscale-status.js', html)
        for value in ('innerHTML', 'localStorage', 'sessionStorage', 'console.', 'setInterval'):
            self.assertNotIn(value, script)
