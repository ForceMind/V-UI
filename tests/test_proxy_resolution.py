"""Fake endpoint answers and isolated helper fixtures; no public DNS queries."""
import copy
import io
import ipaddress
import json
import os
from pathlib import Path
import resource
import socket
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import psutil

from app.services import proxy_resolution as resolver
from app.services.proxy_policy import PolicyError, VerifiedEndpoint
from app.services.upstream_profile import UpstreamProfile

PROFILE_ID = "up_" + "a" * 32
REVISION = "b" * 64
FAKE_UUID = "11111111-1111-4111-8111-111111111111"
SELF = ("127.0.0.1", "::1", "9.9.9.9")
PUBLIC_V6 = "2606:4700:4700::1111"


def profile(host="upstream.example.test"):
    return {"schema_version": 1, "protocol": "vless", "name": "Fake upstream",
            "server": host, "server_port": 443, "uuid": FAKE_UUID,
            "transport": "tcp", "encryption": "none", "flow": "",
            "tls": {"mode": "tls", "server_name": "independent-sni.example.test", "alpn": [],
                    "fingerprint": "", "public_key": "", "short_id": ""}}


def output(addresses):
    return json.dumps({"ok": True, "addresses": addresses}, separators=(",", ":")).encode()


def answer(address):
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    return (socket.AF_INET if ip.version == 4 else socket.AF_INET6, socket.SOCK_STREAM,
            socket.IPPROTO_TCP, "", (address, 0) if ip.version == 4 else (address, 0, 0, 0))


def interfaces(*addresses):
    return {"fake0": [SimpleNamespace(family=socket.AF_INET if ":" not in item else socket.AF_INET6,
                                     address=item) for item in addresses]}


class ProxyResolutionTests(unittest.TestCase):
    def reject(self, code, action, *args, **kwargs):
        with self.assertRaises(resolver.ResolutionError) as caught:
            action(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)
        self.assertNotIn(FAKE_UUID, str(caught.exception))
        return caught.exception

    def resolve(self, value=None, *, self_addresses=SELF):
        return resolver.resolve_upstream(PROFILE_ID, REVISION, value or profile(), self_addresses=self_addresses)

    def test_literal_skips_dns_and_binds_exact_identity_and_time(self):
        value = profile("8.8.8.8")
        before = copy.deepcopy(value)
        with patch.object(resolver, "_bounded_worker_io") as worker, patch.object(resolver.time, "time", return_value=1234.9):
            result = self.resolve(value)
        worker.assert_not_called()
        self.assertEqual(result, VerifiedEndpoint(PROFILE_ID, REVISION, "8.8.8.8", ("8.8.8.8",), 1234))
        self.assertEqual(value, before)
        self.assertNotIn(FAKE_UUID, repr(result))

    def test_hostname_all_answers_validated_then_sorted_and_original_sni_preserved(self):
        value = profile()
        original = copy.deepcopy(value)
        with patch.object(resolver, "_bounded_worker_io", return_value=output([PUBLIC_V6, "8.8.8.8", "1.1.1.1"])) as worker:
            result = self.resolve(UpstreamProfile.from_dict(value))
        self.assertEqual(result.addresses, ("1.1.1.1", "8.8.8.8", PUBLIC_V6))
        self.assertEqual(result.host, "upstream.example.test")
        self.assertEqual(value, original)
        command, raw = worker.call_args.args
        self.assertEqual(command[:3], [sys.executable, "-I", "-B"])
        self.assertTrue(Path(command[3]).is_absolute())
        self.assertEqual(command[-1], "--resolve-worker")
        self.assertEqual(json.loads(raw), {"host": "upstream.example.test"})
        for secret in (FAKE_UUID, "independent-sni.example.test", "upstream.example.test"):
            self.assertNotIn(secret, " ".join(command))
        self.assertNotIn(FAKE_UUID.encode(), raw)

    def test_any_forbidden_answer_rejects_whole_set_including_metadata_and_mapping(self):
        for bad in ("10.0.0.1", "127.0.0.1", "169.254.169.254", "100.100.100.200",
                    "192.0.2.5", "::1", "fe80::1", "::ffff:8.8.8.8", "64:ff9b::808:808", "2001:db8::1"):
            with self.subTest(bad=bad), patch.object(resolver, "_bounded_worker_io", return_value=output(["8.8.8.8", bad])):
                with self.assertRaises(PolicyError) as caught:
                    self.resolve()
                self.assertIn(caught.exception.code, {"PROHIBITED_ADDRESS", "SELF_OR_UPSTREAM_ADDRESS"})

    def test_literal_and_dns_self_addresses_are_denied_regardless_of_port(self):
        for value in (profile("9.9.9.9"), dict(profile("9.9.9.9"), server_port=8443)):
            with self.assertRaises(PolicyError) as caught:
                self.resolve(value)
            self.assertEqual(caught.exception.code, "SELF_OR_UPSTREAM_ADDRESS")
        with patch.object(resolver, "_bounded_worker_io", return_value=output(["8.8.8.8", PUBLIC_V6])):
            with self.assertRaises(PolicyError) as caught:
                self.resolve(self_addresses=SELF + (PUBLIC_V6,))
            self.assertEqual(caught.exception.code, "SELF_OR_UPSTREAM_ADDRESS")

    def test_missing_empty_invalid_and_wildcard_only_self_snapshot_fail_before_dns(self):
        for snapshot in (None, (), [], set(), "127.0.0.1", ("0.0.0.0", "::"), ("bad",), ("fe80::1%eth0",), ["127.0.0.1"] * 4097):
            with self.subTest(snapshot=str(snapshot)[:80]), patch.object(resolver, "_resolve_host") as lookup:
                self.reject("SELF_ADDRESS_UNAVAILABLE", self.resolve, self_addresses=snapshot)
                lookup.assert_not_called()

    def test_invalid_context_and_profile_do_not_start_network_work(self):
        cases = [("wrong", REVISION, profile()), (PROFILE_ID, "wrong", profile()),
                 (PROFILE_ID, REVISION, {"server": "host"}), (PROFILE_ID, REVISION, SimpleNamespace(server="x"))]
        with patch.object(resolver, "_resolve_host") as lookup:
            for args in cases:
                self.reject("INVALID_RESOLUTION_CONTEXT", resolver.resolve_upstream, *args, self_addresses=SELF)
            lookup.assert_not_called()

    def test_each_explicit_resolution_is_fresh_and_previous_endpoint_stays_pinned(self):
        with patch.object(resolver, "_bounded_worker_io", side_effect=[output(["8.8.8.8"]), output(["127.0.0.1"])]) as worker:
            first = self.resolve()
            with self.assertRaises(PolicyError):
                self.resolve()
        self.assertEqual(first.addresses, ("8.8.8.8",))
        self.assertEqual(worker.call_count, 2)

    def test_system_worker_uses_only_current_system_resolver_and_deduplicates(self):
        addresses = [PUBLIC_V6, "8.8.8.8", "1.1.1.1", "8.8.8.8"]
        with patch.object(resolver.socket, "getaddrinfo", return_value=[answer(value) for value in addresses]) as lookup:
            actual = resolver._system_addresses("node.example.test")
        lookup.assert_called_once_with("node.example.test", None, family=socket.AF_UNSPEC,
                                       type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP, flags=0)
        self.assertEqual(actual, ("1.1.1.1", "8.8.8.8", PUBLIC_V6))

    def test_system_worker_never_truncates_a_large_canonical_answer_set(self):
        values = [answer("8.8.8." + str(index)) for index in range(1, 34)]
        with patch.object(resolver.socket, "getaddrinfo", return_value=values[:32]):
            self.assertEqual(len(resolver._system_addresses("node.example.test")), 32)
        with patch.object(resolver.socket, "getaddrinfo", return_value=values):
            self.reject("TOO_MANY_ADDRESSES", resolver._system_addresses, "node.example.test")
        with patch.object(resolver.socket, "getaddrinfo", return_value=[values[0]] * 100):
            self.assertEqual(resolver._system_addresses("node.example.test"), ("8.8.8.1",))

    def test_malformed_family_scope_or_empty_system_answers_fail_closed(self):
        scoped = (socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (PUBLIC_V6, 0, 0, 2))
        malformed = [[], [["8.8.8.8"]], [(999, 1, 6, "", ("8.8.8.8", 0))],
                     [answer("fe80::1%eth0")], [scoped],
                     [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (PUBLIC_V6, 0))]]
        for answers in malformed:
            with self.subTest(answers=answers), patch.object(resolver.socket, "getaddrinfo", return_value=answers):
                self.reject("RESOLUTION_FAILED" if not answers else "INVALID_RESOLUTION",
                            resolver._system_addresses, "node.example.test")

    def test_ipc_malformed_partial_oversized_unknown_and_duplicate_keys_are_rejected(self):
        outputs = [b"", b"{", b"[]", b"null", b'{"ok":true}', b'{"ok":1,"addresses":[]}',
                   b'{"ok":true,"addresses":[]}', b'{"ok":true,"addresses":[1]}',
                   b'{"ok":true,"addresses":["8.8.8.8"],"extra":true}',
                   b'{"ok":true,"ok":true,"addresses":["8.8.8.8"]}',
                   b'{"ok":false,"code":"' + FAKE_UUID.encode() + b'"}',
                   b'{"ok":false,"code":"RESOLUTION_FAILED","message":"private"}',
                   output(["8.8.8.8", "8.8.8.8"]), output(["2606:4700:4700:0:0:0:0:1111"]),
                   output(["fe80::1%eth0"]), b"x" * 4097, b"\xff", b"[" * 2000]
        for raw in outputs:
            with self.subTest(raw=raw[:100]):
                self.reject("INVALID_RESOLUTION", resolver._decode_worker_output, raw)
        self.reject("TOO_MANY_ADDRESSES", resolver._decode_worker_output,
                    output(["8.8.8." + str(index) for index in range(1, 34)]))
        for code in resolver._WORKER_ERROR_CODES:
            self.reject(code, resolver._decode_worker_output, json.dumps({"ok": False, "code": code}).encode())

    def test_local_snapshot_contains_every_interface_address_and_panel_answer(self):
        values = interfaces("127.0.0.1", "::1", "10.1.2.3", "9.9.9.9", "fe80::1%fake0", "0.0.0.0", "::")
        values["fake0"].append(SimpleNamespace(family=psutil.AF_LINK, address="00:00:00:00:00:01"))
        with patch.object(psutil, "net_if_addrs", return_value=values), patch.object(resolver, "_bounded_worker_io", return_value=output(["8.8.8.8", PUBLIC_V6])) as worker:
            actual = resolver.self_address_snapshot("https://panel.example.test:8443")
        expected = ("8.8.8.8", "9.9.9.9", "10.1.2.3", "127.0.0.1", "::1", PUBLIC_V6, "fe80::1")
        self.assertEqual(actual, expected)
        self.assertEqual(json.loads(worker.call_args.args[1]), {"host": "panel.example.test"})

    def test_unconfigured_origin_does_not_lookup_and_literal_panel_origin_skips_worker(self):
        for origin in (None, "", "http://127.0.0.1:8080", "https://[::1]:8443"):
            with self.subTest(origin=origin), patch.object(psutil, "net_if_addrs", return_value=interfaces("127.0.0.1", "::1")), patch.object(resolver, "_bounded_worker_io") as worker:
                self.assertEqual(resolver.self_address_snapshot(origin), ("127.0.0.1", "::1"))
                worker.assert_not_called()

    def test_snapshot_error_or_absent_actual_interfaces_never_passes_even_with_panel_ip(self):
        for fixture in ({}, interfaces("0.0.0.0", "::"), interfaces("invalid"), {"bad": None}):
            with self.subTest(fixture=fixture), patch.object(psutil, "net_if_addrs", return_value=fixture):
                self.reject("SELF_ADDRESS_UNAVAILABLE", resolver.self_address_snapshot, "https://8.8.8.8")
        for error in (OSError(FAKE_UUID), psutil.Error(FAKE_UUID), RuntimeError(FAKE_UUID)):
            with patch.object(psutil, "net_if_addrs", side_effect=error):
                self.reject("SELF_ADDRESS_UNAVAILABLE", resolver.self_address_snapshot)
        with patch.object(psutil, "net_if_addrs", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                resolver.self_address_snapshot()

    def test_panel_resolution_failure_is_not_silently_omitted(self):
        with patch.object(psutil, "net_if_addrs", return_value=interfaces("127.0.0.1")), patch.object(resolver, "_bounded_worker_io", return_value=b'{"ok":false,"code":"RESOLUTION_FAILED"}'):
            self.reject("RESOLUTION_FAILED", resolver.self_address_snapshot, "https://panel.example.test")

    def test_panel_origin_requires_plain_http_origin_without_auth_or_extra_parts(self):
        origins = [False, [], 1, "panel.example.test", "ftp://host", "https://u:p@host", "https://host/", "https://host/path",
                   "https://host?q=1", "https://host#fragment", "https://host?", "https://host#",
                   "https://[::1]ignored.example", "https://host:", "https://host:0", "https://host:65536",
                   "https://host\n", "https://[fe80::1%eth0]", "https://host:wrong", "https://[::1"]
        with patch.object(psutil, "net_if_addrs") as enumerate_interfaces, patch.object(resolver, "_resolve_host") as lookup:
            for origin in origins:
                with self.subTest(origin=origin):
                    self.reject("INVALID_PANEL_ORIGIN", resolver.self_address_snapshot, origin)
            enumerate_interfaces.assert_not_called()
            lookup.assert_not_called()

    def test_scope_is_stripped_only_for_actual_ipv6_interfaces(self):
        for entry in (SimpleNamespace(family=socket.AF_INET, address="8.8.8.8%fake0"),
                      SimpleNamespace(family=socket.AF_INET6, address="fe80::1%"),
                      SimpleNamespace(family=socket.AF_INET6, address="8.8.8.8")):
            with patch.object(psutil, "net_if_addrs", return_value={"fake0": [entry]}):
                self.reject("SELF_ADDRESS_UNAVAILABLE", resolver.self_address_snapshot)

    def test_global_two_worker_cap_shared_by_upstream_and_panel_and_slots_recover(self):
        entered = threading.Barrier(3)
        release = threading.Event()
        results = []
        def io_fixture(*args):
            entered.wait(timeout=2)
            release.wait(timeout=2)
            return output(["8.8.8.8"])
        def invoke(action):
            try:
                results.append(action())
            except BaseException as error:
                results.append(error)
        with patch.object(resolver, "_bounded_worker_io", side_effect=io_fixture), patch.object(psutil, "net_if_addrs", return_value=interfaces("127.0.0.1")):
            workers = [threading.Thread(target=invoke, args=(self.resolve,)),
                       threading.Thread(target=invoke, args=(lambda: resolver.self_address_snapshot("https://panel.example.test"),))]
            for worker in workers:
                worker.start()
            try:
                entered.wait(timeout=2)
                self.reject("RESOLVER_BUSY", self.resolve)
            finally:
                release.set()
                for worker in workers:
                    worker.join(timeout=3)
        self.assertEqual(len(results), 2)
        self.assertFalse(any(isinstance(result, BaseException) for result in results))
        with patch.object(resolver, "_bounded_worker_io", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.resolve()
        with patch.object(resolver, "_bounded_worker_io", return_value=output(["8.8.8.8"])):
            self.assertEqual(self.resolve().addresses, ("8.8.8.8",))

    def test_resource_limits_exact_and_fail_closed(self):
        self.assertEqual(resolver.RESOLUTION_TIMEOUT_SECONDS, 3)
        self.assertEqual(resolver.MAX_CONCURRENT_RESOLVERS, 2)
        self.assertEqual(resolver.MAX_ADDRESSES, 32)
        with patch.object(resource, "setrlimit") as setter:
            resolver._set_worker_limits()
        self.assertEqual(setter.call_args_list[0].args, (resource.RLIMIT_AS, (128 * 1024 * 1024,) * 2))
        self.assertEqual(setter.call_args_list[1].args, (resource.RLIMIT_CPU, (3, 3)))
        self.assertEqual(setter.call_args_list[2].args, (resource.RLIMIT_CORE, (0, 0)))
        with patch.object(resource, "setrlimit", side_effect=OSError(FAKE_UUID)):
            self.reject("RESOLVER_LIMIT_UNAVAILABLE", resolver._set_worker_limits)

    def run_fixture(self, code, *, raw=b"", timeout=None):
        processes = []
        real_popen = subprocess.Popen
        def recording(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            processes.append(process)
            return process
        with patch.object(resolver.subprocess, "Popen", side_effect=recording):
            try:
                if timeout is None:
                    return resolver._bounded_worker_io([sys.executable, "-I", "-c", code], raw)
                with patch.object(resolver, "RESOLUTION_TIMEOUT_SECONDS", timeout):
                    return resolver._bounded_worker_io([sys.executable, "-I", "-c", code], raw)
            finally:
                for process in processes:
                    self.assertIsNotNone(process.returncode)
                    self.assertTrue(process.stdin.closed)
                    self.assertTrue(process.stdout.closed)
                    with self.assertRaises(ChildProcessError):
                        os.waitpid(process.pid, os.WNOHANG)

    def test_real_isolated_helper_works_outside_checkout_without_pythonpath_or_public_dns(self):
        # Numeric getaddrinfo does not issue a DNS query. It exercises the actual
        # entrypoint, resource limits, imports and IPC in the packaged shape.
        command = [sys.executable, "-I", "-B", str(Path(resolver.__file__).resolve()), "--resolve-worker"]
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"PYTHONPATH": directory}):
            previous = os.getcwd()
            try:
                os.chdir(directory)
                raw = resolver._bounded_worker_io(command, b'{"host":"8.8.8.8"}')
            finally:
                os.chdir(previous)
        self.assertEqual(resolver._decode_worker_output(raw), ("8.8.8.8",))

    def test_real_helper_fixture_limits_memory_and_sanitizes_failures(self):
        path = str(Path(resolver.__file__).resolve())
        code = (f"import runpy,sys; sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r}); m = runpy.run_path({path!r}); m['_set_worker_limits'](); "
                "exec('try:\\n bytearray(200 * 1024 * 1024)\\nexcept MemoryError:\\n print(\"bounded\")')")
        self.assertEqual(self.run_fixture(code).strip(), b"bounded")
        with patch.object(resolver.subprocess, "Popen", side_effect=OSError(FAKE_UUID)):
            error = self.reject("RESOLVER_UNAVAILABLE", self.resolve)
        self.assertTrue(error.__suppress_context__)

    def test_deadline_counts_startup_and_partial_ipc_and_waits_for_child_exit(self):
        for code in ("import time; time.sleep(30)",
                     "import sys,time; sys.stdout.write('{'); sys.stdout.flush(); time.sleep(30)",
                     "import os,time; os.close(0); os.close(1); time.sleep(30)"):
            start = time.monotonic()
            self.reject("RESOLUTION_TIMEOUT", self.run_fixture, code, timeout=0.12)
            self.assertLess(time.monotonic() - start, 2)
        with patch.object(resolver.time, "monotonic", side_effect=[10, 14]):
            self.reject("RESOLUTION_TIMEOUT", self.run_fixture, "import time; time.sleep(30)")

    def test_oversized_output_and_nonzero_exit_reap_without_leaking_stderr(self):
        code = "import sys; sys.stdout.buffer.write(b'x' * 20000); sys.stdout.flush()"
        with patch.object(resolver.json, "loads") as loads:
            self.reject("RESOLVER_RESOURCE_LIMIT", self.run_fixture, code)
            loads.assert_not_called()
        self.reject("RESOLVER_RESOURCE_LIMIT", self.run_fixture,
                    "import sys; sys.stderr.write(" + repr(FAKE_UUID) + "); sys.exit(3)")
        self.reject("INVALID_RESOLUTION", resolver._bounded_worker_io, [sys.executable], b"x" * 513)

    def test_cancellation_reaps_child_and_closes_pipes(self):
        with patch.object(resolver.selectors.DefaultSelector, "select", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.run_fixture("import time; time.sleep(30)")

    def test_real_child_fixture_resolves_hostname_without_any_external_dns(self):
        root = str(Path(__file__).resolve().parents[1])
        path = str(Path(resolver.__file__).resolve())
        answers = [(socket.AF_INET.value, socket.SOCK_STREAM.value, socket.IPPROTO_TCP, "", ("8.8.8.8", 0)),
                   (socket.AF_INET6.value, socket.SOCK_STREAM.value, socket.IPPROTO_TCP, "", (PUBLIC_V6, 0, 0, 0))]
        code = (f"import runpy,sys; sys.path.insert(0, {root!r}); m = runpy.run_path({path!r}); "
                f"m['socket'].getaddrinfo = lambda *args, **kwargs: {answers!r}; "
                "raise SystemExit(m['_worker_main']())")
        raw = self.run_fixture(code, raw=b'{"host":"fake-upstream.example.test"}')
        self.assertEqual(resolver._decode_worker_output(raw), ("8.8.8.8", PUBLIC_V6))

    def test_real_helper_rejects_malformed_partial_and_oversized_request_without_dns(self):
        command = [sys.executable, "-I", "-B", str(Path(resolver.__file__).resolve()), "--resolve-worker"]
        for raw in (b"{", b"{}", b'{"host":"8.8.8.8","host":"1.1.1.1"}', b"x" * 512):
            result = json.loads(resolver._bounded_worker_io(command, raw))
            self.assertFalse(result["ok"])
            self.assertIn(result["code"], {"INVALID_RESOLUTION", "RESOLUTION_FAILED"})
        fake_in = SimpleNamespace(buffer=io.BytesIO(b"x" * 513))
        fake_out = io.StringIO()
        with patch.object(resolver, "_set_worker_limits"), patch.object(resolver.sys, "stdin", fake_in), patch.object(resolver.sys, "stdout", fake_out), patch.object(resolver.socket, "getaddrinfo") as lookup:
            resolver._worker_main()
            lookup.assert_not_called()
        self.assertEqual(json.loads(fake_out.getvalue()), {"ok": False, "code": "INVALID_RESOLUTION"})

    def test_worker_only_emits_controlled_errors_and_never_raw_resolver_exception(self):
        for effect, expected in ((socket.gaierror(FAKE_UUID), "RESOLUTION_FAILED"),
                                 (MemoryError(FAKE_UUID), "RESOLVER_RESOURCE_LIMIT")):
            fake_in = SimpleNamespace(buffer=io.BytesIO(b'{"host":"node.example.test"}'))
            fake_out = io.StringIO()
            with patch.object(resolver, "_set_worker_limits"), patch.object(resolver.socket, "getaddrinfo", side_effect=effect), patch.object(resolver.sys, "stdin", fake_in), patch.object(resolver.sys, "stdout", fake_out):
                self.assertEqual(resolver._worker_main(), 0)
            self.assertEqual(json.loads(fake_out.getvalue()), {"ok": False, "code": expected})
            self.assertNotIn(FAKE_UUID, fake_out.getvalue())


if __name__ == "__main__":
    unittest.main()
