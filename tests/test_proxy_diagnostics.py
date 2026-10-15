"""M3 orchestration and fake-transport safety tests, not real overseas evidence."""
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from app.services import proxy_diagnostics as service
from app.services import proxy_diagnostic_worker as worker
from app.services.proxy_policy import DNS_TAG, LOCAL_TAG, UPSTREAM_TAG, VerifiedEndpoint
from app.services.proxy_store import ProxyConflict
from app.services.upstream_profile import UpstreamProfile

ID = "up_" + "1" * 32
REVISION = "a" * 64
UUID = "11111111-1111-1111-1111-111111111111"
PROFILE = {"schema_version": 1, "protocol": "vless", "name": "fixture", "server": "8.8.8.8",
           "server_port": 443, "uuid": UUID, "transport": "tcp", "encryption": "none", "flow": "",
           "tls": {"mode": "tls", "server_name": "vpn.example.test", "alpn": [],
                   "fingerprint": "", "public_key": "", "short_id": ""}}


class Store:
    def __init__(self):
        self.revision = REVISION
        self.reads = 0
    def load_draft(self):
        self.reads += 1
        return {"revision": self.revision, "upstreams": {ID: deepcopy(PROFILE)}}
    def read_snapshot(self):
        return {"revision": self.revision}


def successful(payload, deadline, cancel, emit, directory):
    for stage, code in (("configuration", "CORE_CONFIG_VALID"), ("tcp", "TCP_CONNECTED"),
            ("tls_or_reality", "TLS_CERTIFICATE_VERIFIED"),
            ("proxy_forwarding", "PROXY_FORWARDING_PROVEN_BY_HTTPS"), ("https_exit", "HTTPS_EXIT_OBSERVED")):
        emit({"event": "stage", "stage": stage, "status": "passed", "code": code})
    emit({"event": "endpoint", "address": "8.8.8.8", "resolved_at": 123})
    emit({"event": "stage", "stage": "dns", "status": "insufficient_evidence", "code": "DNS_PATH_BOUND_TO_HTTPS"})
    return {"code": "DIAGNOSTIC_COMPLETE", "exit_ip": "9.9.9.9"}


class DiagnosticServiceTests(unittest.TestCase):
    def new(self, runner=successful, **kwargs):
        store = Store()
        diagnostic = service.ProxyDiagnostics(store, _runner=runner, _admission=kwargs.pop("_admission", service._Admission()), **kwargs)
        self.addCleanup(diagnostic.close)
        return diagnostic, store

    def finish(self, diagnostic, job, owner="alice"):
        self.assertTrue(diagnostic._jobs[job["job_id"]].done.wait(3))
        return diagnostic.get(owner, job["job_id"])

    def test_saved_profile_exact_revision_and_safe_completed_result(self):
        diagnostic, store = self.new()
        before = deepcopy(PROFILE)
        job = self.finish(diagnostic, diagnostic.start("alice", ID, REVISION))
        self.assertEqual(job["state"], "completed")
        self.assertEqual(job["exit_ip"], "9.9.9.9")
        self.assertEqual(job["pinned_address"], "8.8.8.8")
        self.assertEqual(PROFILE, before)
        self.assertNotIn(UUID, json.dumps(job))
        self.assertNotIn("profile", vars(diagnostic._jobs[job["job_id"]]))
        by_stage = {row["stage"]: row for row in job["stages"]}
        self.assertEqual(by_stage["dns"]["status"], "insufficient_evidence")
        self.assertEqual(by_stage["external_client"]["status"], "not_tested")

    def test_invalid_shape_unknown_profile_and_revision_have_no_worker(self):
        calls = []
        diagnostic, store = self.new(lambda *args: calls.append(args))
        for owner, profile, revision in ((None, ID, REVISION), (True, ID, REVISION),
                ("alice", "https://evil.example", REVISION), ("alice", ID, "*"),
                ("alice", "up_" + "2" * 32, REVISION)):
            with self.assertRaises(service.DiagnosticsError):
                diagnostic.start(owner, profile, revision)
        with self.assertRaises(ProxyConflict):
            diagnostic.start("alice", ID, "b" * 64)
        self.assertEqual(calls, [])

    def test_owner_required_for_read_and_cancel(self):
        diagnostic, _ = self.new()
        job = self.finish(diagnostic, diagnostic.start("alice", ID, REVISION))
        for action in (diagnostic.get, diagnostic.cancel):
            with self.assertRaises(service.DiagnosticsError) as caught:
                action("bob", job["job_id"])
            self.assertEqual(caught.exception.code, "DIAGNOSTIC_NOT_FOUND")
        self.assertEqual(diagnostic.get("alice", job["job_id"])["state"], "completed")

    def test_global_two_and_per_admin_one_across_service_instances(self):
        entered = threading.Event()
        def blocked(payload, deadline, cancel, emit, directory):
            entered.set()
            cancel.wait(3)
            return {"code": "DIAGNOSTIC_CANCELLED", "exit_ip": None}
        admission = service._Admission()
        first, _ = self.new(blocked, _admission=admission)
        second, _ = self.new(blocked, _admission=admission)
        alice = first.start("alice", ID, REVISION)
        self.assertTrue(entered.wait(1))
        with self.assertRaises(service.DiagnosticsError) as caught:
            second.start("alice", ID, REVISION)
        self.assertEqual(caught.exception.code, "DIAGNOSTIC_BUSY")
        bob = second.start("bob", ID, REVISION)
        with self.assertRaises(service.DiagnosticsError) as caught:
            first.start("carol", ID, REVISION)
        self.assertEqual(caught.exception.code, "DIAGNOSTIC_BUSY")
        self.assertEqual(first.cancel("alice", alice["job_id"])["state"], "cancelled")
        self.assertEqual(second.cancel("bob", bob["job_id"])["state"], "cancelled")
        self.assertFalse(admission.active)

    def test_thirty_second_start_limit_survives_completion_and_cancellation(self):
        now = [100.0]
        diagnostic, _ = self.new(_clock=lambda: now[0])
        job = self.finish(diagnostic, diagnostic.start("alice", ID, REVISION))
        for value in (100.0, 129.999):
            now[0] = value
            with self.assertRaises(service.DiagnosticsError) as caught:
                diagnostic.start("alice", ID, REVISION)
            self.assertEqual(caught.exception.code, "DIAGNOSTIC_RATE_LIMITED")
        now[0] = 130.0
        next_job = diagnostic.start("alice", ID, REVISION)
        self.assertNotEqual(job["job_id"], next_job["job_id"])
        self.finish(diagnostic, next_job)

    def test_revision_change_and_expiry_invalidate_observations(self):
        now = [1000.0]
        diagnostic, store = self.new(_wall_clock=lambda: now[0])
        job = self.finish(diagnostic, diagnostic.start("alice", ID, REVISION))
        store.revision = "b" * 64
        stale = diagnostic.get("alice", job["job_id"])
        self.assertTrue(stale["stale"])
        self.assertFalse(any(row["status"] == "passed" for row in stale["stages"]))
        now[0] += service.RESULT_TTL_SECONDS
        self.assertEqual(diagnostic.get("alice", job["job_id"])["state"], "expired")
        now[0] += 61
        with self.assertRaises(service.DiagnosticsError):
            diagnostic.get("alice", job["job_id"])

    def test_completed_cache_and_rate_limiter_are_bounded(self):
        diagnostic, _ = self.new()
        with patch.object(service, "MAX_RETAINED_JOBS", 4):
            for index in range(10):
                self.finish(diagnostic, diagnostic.start(str(index), ID, REVISION), str(index))
            self.assertLessEqual(len(diagnostic._jobs), 4)
        admission = service._Admission()
        for index in range(256):
            admission.acquire(index, 0)
            admission.release(index)
        with self.assertRaises(service.DiagnosticsError):
            admission.acquire("next", 0)
        admission.acquire("next", 30)
        self.assertEqual(len(admission.last_start), 1)

    def test_exception_text_and_malformed_child_data_never_leak(self):
        def crash(*_):
            raise ValueError("secret credential " + UUID)
        diagnostic, _ = self.new(crash)
        job = self.finish(diagnostic, diagnostic.start("alice", ID, REVISION))
        self.assertEqual(job["code"], "DIAGNOSTIC_WORKER_FAILED")
        self.assertNotIn(UUID, json.dumps(job))
        for event in ({"event": "stage", "stage": "tcp", "status": "passed", "code": UUID},
                      {"event": "endpoint", "address": "127.0.0.1", "resolved_at": 123},
                      {"event": "stage", "stage": "dns", "status": "passed", "code": "TCP_CONNECTED"},
                      {"event": "stage", "stage": "external_client", "status": "passed", "code": "TCP_CONNECTED"}):
            with self.assertRaises(service.DiagnosticsError):
                diagnostic._emit(diagnostic._jobs[job["job_id"]], event)

    def test_success_without_observations_is_not_success(self):
        diagnostic, _ = self.new(lambda *_: {"code": "DIAGNOSTIC_COMPLETE", "exit_ip": "9.9.9.9"})
        job = self.finish(diagnostic, diagnostic.start("alice", ID, REVISION))
        self.assertEqual(job["state"], "failed")
        self.assertEqual(job["code"], "DIAGNOSTIC_INVALID_OUTPUT")

    def test_deadline_includes_startup_and_cleanup(self):
        now = [0.0]
        def slow(*args):
            result = successful(*args)
            now[0] = 31.0
            return result
        diagnostic, _ = self.new(slow, _clock=lambda: now[0])
        job = self.finish(diagnostic, diagnostic.start("alice", ID, REVISION))
        self.assertEqual(job["state"], "failed")
        self.assertEqual(job["code"], "DIAGNOSTIC_TIMEOUT")

    def test_ephemeral_directory_private_and_removed_after_failure(self):
        paths = []
        def fail(payload, deadline, cancel, emit, directory):
            path = Path(directory)
            paths.append(path)
            self.assertEqual(path.stat().st_mode & 0o777, 0o700)
            worker._write_config(directory, {"uuid": UUID})
            self.assertEqual((path / "diagnostic.json").stat().st_mode & 0o777, 0o600)
            raise ValueError("not exposed")
        diagnostic, _ = self.new(fail)
        self.finish(diagnostic, diagnostic.start("alice", ID, REVISION))
        self.assertFalse(paths[0].exists())


class WorkerProtocolTests(unittest.TestCase):
    def fake_worker(self, program, *, timeout=3, cancelled=None):
        real_popen = subprocess.Popen
        processes = []
        def launch(_command, **kwargs):
            child = real_popen([sys.executable, "-u", "-c", program], **kwargs)
            processes.append(child)
            return child
        with tempfile.TemporaryDirectory() as directory, patch.object(service.subprocess, "Popen", launch):
            try:
                return service._run_worker({"profile": PROFILE}, time.monotonic() + timeout,
                                           cancelled or threading.Event(), lambda event: None, directory)
            finally:
                self.assertTrue(all(child.poll() is not None for child in processes))

    def test_protocol_rejects_over_limit_truncated_duplicate_and_raw_secret_output(self):
        for program, code in (
            ("import sys;sys.stdin.read();print('x'*65537)", "DIAGNOSTIC_OUTPUT_LIMIT"),
            ("import sys;sys.stdin.read();sys.stdout.write('{')", "DIAGNOSTIC_INVALID_OUTPUT"),
            ("import sys;sys.stdin.read();print('{\"event\":\"result\",\"event\":\"bad\"}')", "DIAGNOSTIC_INVALID_OUTPUT"),
            ("import sys;sys.stdin.read();print('private uuid credential')", "DIAGNOSTIC_INVALID_OUTPUT"),
        ):
            with self.subTest(program=program), self.assertRaises(service.DiagnosticsError) as caught:
                self.fake_worker(program)
            self.assertEqual(caught.exception.code, code)
            self.assertNotIn("private", str(caught.exception))

    def test_worker_timeout_has_cleanup_reserve_inside_deadline(self):
        started = time.monotonic()
        with self.assertRaises(service.DiagnosticsError) as caught:
            self.fake_worker("import sys,time;sys.stdin.read();time.sleep(10)", timeout=1)
        self.assertEqual(caught.exception.code, "DIAGNOSTIC_TIMEOUT")
        self.assertLess(time.monotonic() - started, 1)

    def test_worker_cancellation_terminates_before_time_limit(self):
        cancel = threading.Event()
        timer = threading.Timer(.1, cancel.set)
        timer.start()
        self.addCleanup(timer.cancel)
        with self.assertRaises(service.DiagnosticsError) as caught:
            self.fake_worker("import sys,time;sys.stdin.read();time.sleep(10)", cancelled=cancel)
        self.assertEqual(caught.exception.code, "DIAGNOSTIC_CANCELLED")

    def test_terminal_output_followed_by_extra_data_is_rejected(self):
        result = json.dumps({"event": "result", "code": "CORE_START_FAILED", "exit_ip": None})
        program = f"import sys;sys.stdin.read();print({result!r});print('extra')"
        with self.assertRaises(service.DiagnosticsError) as caught:
            self.fake_worker(program)
        self.assertEqual(caught.exception.code, "DIAGNOSTIC_INVALID_OUTPUT")

    def test_cancelled_job_still_uses_start_rate_limit(self):
        def waiting(payload, deadline, cancel, emit, directory):
            cancel.wait(2)
            return {"code": "DIAGNOSTIC_CANCELLED", "exit_ip": None}
        diagnostic = service.ProxyDiagnostics(Store(), _runner=waiting, _admission=service._Admission())
        self.addCleanup(diagnostic.close)
        job = diagnostic.start("alice", ID, REVISION)
        self.assertEqual(diagnostic.cancel("alice", job["job_id"])["state"], "cancelled")
        with self.assertRaises(service.DiagnosticsError) as caught:
            diagnostic.start("alice", ID, REVISION)
        self.assertEqual(caught.exception.code, "DIAGNOSTIC_RATE_LIMITED")


class BytesSocket:
    def __init__(self, data):
        self.data = data
    def makefile(self, *args, **kwargs):
        return io.BytesIO(self.data)


def response(body=b'{"ip":"9.9.9.9"}', status=200, headers=None):
    headers = headers if headers is not None else [b"Content-Length: " + str(len(body)).encode()]
    return BytesSocket(b"HTTP/1.1 " + str(status).encode() + b" Fixture\r\n" + b"\r\n".join(headers) + b"\r\n\r\n" + body)


class DiagnosticWorkerTests(unittest.TestCase):
    def test_catalog_is_fixed_in_core_with_no_direct_or_ordinary_listener(self):
        profile = UpstreamProfile.from_dict(PROFILE)
        endpoint = VerifiedEndpoint(ID, REVISION, profile.server, ("8.8.8.8",), 123)
        config = worker._diagnostic_config(profile, ID, REVISION, endpoint, ("127.0.0.1",), 18000,
                                           "diagnostic", "ephemeral-fake-password")
        self.assertEqual(len(config["inbounds"]), 1)
        self.assertEqual(config["inbounds"][0]["listen"], "127.0.0.1")
        self.assertFalse(config["inbounds"][0]["set_system_proxy"])
        self.assertEqual([row["tag"] for row in config["outbounds"]], [UPSTREAM_TAG])
        self.assertEqual(config["dns"]["servers"], [{"type": "https", "tag": DNS_TAG, "server": "1.1.1.1",
            "server_port": 443, "path": "/dns-query", "tls": {"enabled": True, "server_name": "cloudflare-dns.com"},
            "detour": UPSTREAM_TAG}])
        rules = config["route"]["rules"]
        self.assertEqual(rules[0], {"domain": ["api64.ipify.org"], "invert": True, "action": "reject"})
        self.assertEqual(rules[1], {"port": [443], "invert": True, "action": "reject"})
        self.assertEqual(rules[-1], {"inbound": [LOCAL_TAG], "action": "reject"})
        self.assertTrue(any("8.8.8.8/32" in row.get("ip_cidr", []) for row in rules))
        self.assertEqual(config["log"], {"disabled": True})
        self.assertEqual(config["outbounds"][0]["server"], "8.8.8.8")
        self.assertEqual(config["outbounds"][0]["tls"]["server_name"], "vpn.example.test")

    def test_strict_ipify_json_and_http_status(self):
        self.assertEqual(worker._read_exit(response()), "9.9.9.9")
        self.assertEqual(worker._read_exit(response(b'{"ip":"2606:4700:4700::1111"}')), "2606:4700:4700::1111")
        for body in (b'{"ip":"9.9.9.9","ip":"8.8.8.8"}', b'{"ip":"9.9.9.9","other":1}',
                     b'{"ip":"fe80::1%lo"}', b'{"ip":7}', b'[]', b'{"ip":"not-ip"}', b'not json'):
            with self.subTest(body=body), self.assertRaises(service.DiagnosticsError):
                worker._read_exit(response(body))
        for status in (201, 301, 302, 307, 308, 400, 500):
            with self.subTest(status=status), self.assertRaises(service.DiagnosticsError) as caught:
                worker._read_exit(response(status=status))
            self.assertEqual(caught.exception.code, "HTTP_STATUS_REJECTED")

    def test_response_limit_includes_headers_and_no_compressed_or_ambiguous_body(self):
        for item in (
            response(b"x" * 65537),
            response(headers=[b"X-Large: " + b"x" * 65536]),
            response(headers=[b"Content-Length: 16", b"Content-Length: 16"]),
            response(headers=[b"Content-Length: 16", b"Transfer-Encoding: chunked"]),
            response(headers=[b"Content-Length: -1"]),
            response(headers=[b"Content-Length: 500"]),
        ):
            with self.assertRaises(service.DiagnosticsError):
                worker._read_exit(item)
        chunk = b'10\r\n{"ip":"9.9.9.9"}\r\n0\r\n\r\n'
        self.assertEqual(worker._read_exit(response(chunk, headers=[b"Transfer-Encoding: chunked"])), "9.9.9.9")

    def test_normal_tls_uses_pinned_ip_and_original_sni(self):
        profile = UpstreamProfile.from_dict(PROFILE)
        from unittest.mock import MagicMock
        connection = MagicMock()
        context = MagicMock()
        with patch.object(worker.socket, "create_connection", return_value=connection) as connect, \
                patch.object(worker.ssl, "create_default_context", return_value=context), \
                patch.object(worker, "_stage") as stage:
            worker._tcp_tls(profile, "8.8.8.8")
        connect.assert_called_once_with(("8.8.8.8", 443), timeout=3)
        context.wrap_socket.assert_called_once_with(connection.__enter__(), server_hostname="vpn.example.test")
        self.assertIn((("tls_or_reality", "passed", "TLS_CERTIFICATE_VERIFIED"),), stage.call_args_list)

    def test_reality_tcp_never_claims_authentication_or_performs_plain_tls(self):
        profile = deepcopy(PROFILE)
        profile["flow"] = "xtls-rprx-vision"
        profile["tls"].update(mode="reality", fingerprint="chrome", public_key="A" * 43, short_id="0123456789abcdef")
        from unittest.mock import MagicMock
        with patch.object(worker.socket, "create_connection", return_value=MagicMock()), \
                patch.object(worker.ssl, "create_default_context") as tls, patch.object(worker, "_stage") as stage:
            worker._tcp_tls(UpstreamProfile.from_dict(profile), "8.8.8.8")
        tls.assert_not_called()
        self.assertIn((("tls_or_reality", "insufficient_evidence", "REALITY_REQUIRES_PROXY_EVIDENCE"),), stage.call_args_list)
        self.assertFalse(any("AUTHENTICATED" in call.args[2] for call in stage.call_args_list))

    def test_mixed_bootstrap_self_and_upstream_guard_reuse(self):
        profile = UpstreamProfile.from_dict({**PROFILE, "server": "upstream.example.test"})
        for answers in (("8.8.8.8", "127.0.0.1"), ("8.8.8.8", "169.254.169.254"),
                        ("8.8.8.8", "100.100.100.200"), ("8.8.8.8", "::ffff:1.1.1.1")):
            with patch.object(worker, "self_address_snapshot", return_value=("127.0.0.1",)), \
                    patch.object(worker, "_resolve", return_value=answers), self.assertRaises(service.DiagnosticsError):
                worker._address_context(profile, ID, REVISION, None)
        with patch.object(worker, "self_address_snapshot", return_value=("8.8.8.8",)), \
                patch.object(worker, "_resolve", return_value=("8.8.8.8",)), self.assertRaises(service.DiagnosticsError):
            worker._address_context(profile, ID, REVISION, None)

    def test_resolver_subprocess_output_and_timeout_are_bounded_and_reaped(self):
        for program, maximum, timeout in (("print('x'*5000)", 128, 1), ("import time;time.sleep(10)", 128, .1)):
            with self.assertRaises(service.DiagnosticsError):
                worker._capture([sys.executable, "-c", program], maximum=maximum, timeout=timeout)
            self.assertFalse(worker._CHILDREN)

    def test_private_config_refuses_existing_file_or_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "target"
            target.write_text("do not change")
            (Path(directory) / "diagnostic.json").symlink_to(target)
            with self.assertRaises(OSError):
                worker._write_config(directory, {"secret": "fake"})
            self.assertEqual(target.read_text(), "do not change")

    def test_real_isolated_worker_fails_closed_without_network_or_core(self):
        request = {"profile": {**PROFILE, "server": "127.0.0.1"}, "profile_id": ID,
                   "revision": REVISION, "panel_origin": None}
        events = []
        with tempfile.TemporaryDirectory() as directory:
            result = service._run_worker(request, time.monotonic() + 5, threading.Event(), events.append, directory)
            self.assertFalse((Path(directory) / "diagnostic.json").exists())
        self.assertIn(result["code"], {"ADDRESS_CHECK_FAILED", "SELF_ADDRESS_UNAVAILABLE"})
        self.assertIsNone(result["exit_ip"])
        self.assertEqual(events, [])

    def test_parent_cancellation_kills_and_reaps_process_group(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "child.pid"
            program = "\n".join([
                "import subprocess,sys,time,pathlib,signal",
                "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])",
                "def stop(*args): raise SystemExit(0)",
                "signal.signal(signal.SIGTERM,stop)",
                f"pathlib.Path({str(marker)!r}).write_text(str(p.pid))",
                "try: time.sleep(60)",
                "finally:",
                "    p.terminate()",
                "    p.wait(timeout=1)",
            ])
            process = subprocess.Popen([sys.executable, "-c", program], stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, start_new_session=True)
            deadline = time.monotonic() + 2
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertTrue(marker.exists())
            child_pid = int(marker.read_text())
            service._stop_process(process)
            self.assertIsNotNone(process.returncode)
            import psutil
            self.assertFalse(psutil.pid_exists(child_pid))

    def test_sigterm_resistant_worker_is_killed_and_reaped(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "ready"
            program = ("import signal,time,pathlib;signal.signal(signal.SIGTERM,signal.SIG_IGN);"
                       f"pathlib.Path({str(marker)!r}).touch();time.sleep(60)")
            process = subprocess.Popen([sys.executable, "-c", program], stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, start_new_session=True)
            deadline = time.monotonic() + 2
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertTrue(marker.exists())
            service._stop_process(process)
            self.assertEqual(process.returncode, -signal.SIGKILL)


class EvidencePipelineTests(unittest.TestCase):
    def run_fixture(self, reality=False, exit_error=None, owns=True):
        from contextlib import ExitStack
        from unittest.mock import MagicMock
        profile = deepcopy(PROFILE)
        if reality:
            profile["flow"] = "xtls-rprx-vision"
            profile["tls"].update(mode="reality", fingerprint="chrome", public_key="A" * 43,
                                   short_id="0123456789abcdef")
        events = []
        endpoint = VerifiedEndpoint(ID, REVISION, "8.8.8.8", ("8.8.8.8",), 123)
        with ExitStack() as stack:
            directory = stack.enter_context(tempfile.TemporaryDirectory())
            for name, value in (("_address_context", (("127.0.0.1",), endpoint)),
                                ("_free_port", 18000), ("_validate_core", None),
                                ("_start_core", MagicMock()), ("_owns_listener", owns)):
                stack.enter_context(patch.object(worker, name, return_value=value))
            stack.enter_context(patch.object(worker, "_emit", side_effect=events.append))
            stack.enter_context(patch.object(worker.socket, "create_connection", return_value=MagicMock()))
            stack.enter_context(patch.object(worker.ssl, "create_default_context", return_value=MagicMock()))
            stack.enter_context(patch.object(worker, "_https_exit", return_value="9.9.9.9", side_effect=exit_error))
            request = {"profile": profile, "profile_id": ID, "revision": REVISION,
                       "panel_origin": None, "binary": "/fixture/sing-box"}
            try:
                result = worker._run(request, directory)
                code = "DIAGNOSTIC_COMPLETE"
            except service.DiagnosticsError as error:
                code, result = error.code, None
        return code, result, events

    def test_reality_success_comes_only_after_proxy_https_and_dns_stays_limited(self):
        code, result, events = self.run_fixture(reality=True)
        self.assertEqual((code, result), ("DIAGNOSTIC_COMPLETE", "9.9.9.9"))
        stages = [event for event in events if event["event"] == "stage"]
        reality = [event for event in stages if event["stage"] == "tls_or_reality"]
        self.assertEqual([event["status"] for event in reality], ["insufficient_evidence", "passed"])
        self.assertEqual(reality[-1]["code"], "REALITY_AUTHENTICATED_BY_HTTPS")
        dns = [event for event in stages if event["stage"] == "dns"]
        self.assertEqual(dns[0]["status"], "insufficient_evidence")
        self.assertFalse(any(event["stage"] == "external_client" for event in stages))

    def test_https_failure_or_listener_loss_cannot_authenticate_reality(self):
        for error, owns in ((service.DiagnosticsError("HTTPS_FAILED"), True), (None, False)):
            code, result, events = self.run_fixture(reality=True, exit_error=error, owns=owns)
            self.assertIsNone(result)
            self.assertNotEqual(code, "DIAGNOSTIC_COMPLETE")
            self.assertFalse(any(event.get("stage") == "tls_or_reality" and event["status"] == "passed"
                                 for event in events))
            self.assertFalse(any(event.get("stage") == "https_exit" and event["status"] == "passed"
                                 for event in events))

    def test_arbitrary_provider_url_headers_and_network_override_fields_are_rejected(self):
        request = {"profile": PROFILE, "profile_id": ID, "revision": REVISION,
                   "panel_origin": None, "binary": "/fixture/sing-box"}
        for key in ("url", "provider", "headers", "command", "address_policy", "environment", "target_port", "_target_port"):
            with patch.object(worker, "_address_context") as address, self.assertRaises(service.DiagnosticsError):
                worker._run({**request, key: "bad"}, "/unused")
            address.assert_not_called()

    def test_listener_readiness_requires_exact_core_pid_loopback_tcp_owner(self):
        from unittest.mock import MagicMock
        from types import SimpleNamespace
        import socket
        child = SimpleNamespace(pid=12345, poll=lambda: None)
        process = MagicMock()
        with patch("psutil.Process", return_value=process):
            process.net_connections.return_value = [SimpleNamespace(family=socket.AF_INET,
                type=socket.SOCK_STREAM, status="LISTEN", laddr=SimpleNamespace(ip="0.0.0.0", port=18000))]
            self.assertFalse(worker._owns_listener(child, 18000))
            process.net_connections.return_value[0].laddr.ip = "127.0.0.1"
            self.assertTrue(worker._owns_listener(child, 18000))
            process.net_connections.return_value[0].laddr.port = 18001
            self.assertFalse(worker._owns_listener(child, 18000))


@unittest.skipUnless(os.environ.get("VUI_TEST_CORES"), "Set VUI_TEST_CORES for pinned diagnostic parser evidence")
class DiagnosticPinnedParserTests(unittest.TestCase):
    def test_pinned_core_accepts_fixed_catalog_tls_and_reality_configs(self):
        binary = str(Path(os.environ["VUI_TEST_CORES"]) / "sing-box")
        for reality in (False, True):
            with self.subTest(reality=reality), tempfile.TemporaryDirectory() as directory:
                profile = deepcopy(PROFILE)
                if reality:
                    profile["flow"] = "xtls-rprx-vision"
                    profile["tls"].update(mode="reality", fingerprint="chrome", public_key="A" * 43,
                                           short_id="0123456789abcdef")
                profile = UpstreamProfile.from_dict(profile)
                endpoint = VerifiedEndpoint(ID, REVISION, profile.server, ("8.8.8.8",), 123)
                config = worker._diagnostic_config(profile, ID, REVISION, endpoint, ("127.0.0.1",),
                                                    18000, "diagnostic", "ephemeral-fake-password")
                worker._validate_core(binary, worker._write_config(directory, config))


if __name__ == "__main__":
    unittest.main()
