"""Pinned parser and narrowly scoped real policy rejection tests.

These tests do not establish all four-state transitions, external-client
qualification, real overseas routing, or the complete double-hop matrix.
Only temporary loopback listeners, fake credentials and temporary CA files are
used. No system networking, firewall, resolver or trust configuration changes.
"""
from contextlib import ExitStack
from copy import deepcopy
import base64
import http.client
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import tempfile
import time
import unittest

from app.services.core_manager import SingBoxAdapter
from app.services.proxy_policy import AddressPolicy, LOCAL_TAG, UPSTREAM_TAG, compile_proxy_policy
from loopback_helpers import (CoreProcess, certificate_files, start_http_target,
                              start_udp_echo, unused_port, _recv_exact,
                              _socks_address_from_stream, _socks_udp_target)
from test_proxy_policy import PASSWORD, PROFILE_ID, endpoint, inbound, snapshot


@unittest.skipUnless(os.environ.get("VUI_TEST_CORES"), "Set VUI_TEST_CORES for pinned real-core policy evidence")
class ProxyPolicyRealTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.binary = Path(os.environ["VUI_TEST_CORES"]) / "sing-box"
        self.assertTrue(self.binary.is_file(), "Pinned sing-box is missing")
        version = subprocess.run([str(self.binary), "version"], capture_output=True, text=True, timeout=10)
        self.assertEqual(version.returncode, 0)
        self.assertIn("sing-box version 1.14.2", version.stdout)
        self.ca, self.cert, self.key = certificate_files(self.root, "relay-policy")
        self.empty_certs = self.root / "empty-certs"
        self.empty_certs.mkdir()
        self.env = {**os.environ, "SSL_CERT_FILE": str(self.ca), "SSL_CERT_DIR": str(self.empty_certs)}
        self.count = 0

    def core(self, config, port):
        self.count += 1
        path = self.root / f"core-{self.count}.json"
        path.write_text(json.dumps(config)); path.chmod(0o600)
        log = self.root / f"core-{self.count}.log"
        process = CoreProcess(self.stack, [str(self.binary), "run", "-c", str(path)], log, self.env)
        process.start(port)
        return process, log

    def configuration(self, local=True, relay=False, reality=False, rows=None, **kwargs):
        value = snapshot(local, relay, reality=reality)
        value["settings"]["local_port"] = unused_port()
        if rows is None:
            rows = [inbound(port=unused_port())] if relay else []
        for row in rows:
            row.stream_settings["tls"].update(certificate_path=str(self.cert), key_path=str(self.key))
        base = SingBoxAdapter().build_config(rows)
        base["log"] = {"level": "debug", "timestamp": False}
        kwargs.setdefault("endpoint", endpoint(value))
        kwargs.setdefault("self_addresses", ("127.0.0.1",))
        return compile_proxy_policy(base, rows, value, **kwargs), value

    def request(self, proxy_port, target_port, *, username="fixture-user", password=PASSWORD, host="127.0.0.1"):
        connection = http.client.HTTPConnection("127.0.0.1", proxy_port, timeout=3)
        credentials = base64.b64encode((username + ":" + password).encode()).decode()
        try:
            connection.request("GET", f"http://{host}:{target_port}/policy-test",
                               headers={"Proxy-Authorization": "Basic " + credentials, "Connection": "close"})
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def assert_failure(self, proxy_port, target_port, **kwargs):
        try:
            status, _ = self.request(proxy_port, target_port, **kwargs)
            self.assertGreaterEqual(status, 400)
        except (OSError, http.client.HTTPException):
            pass

    def wait_reason(self, log, reasons, offset=0):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            text = log.read_text(errors="replace")[offset:].lower()
            if any(all(reason in line for reason in reasons) for line in text.splitlines()):
                return text
            time.sleep(.03)
        self.fail("Missing actual rejection reason: " + repr(reasons) + "\n" + log.read_text())

    def test_fixed_parser_accepts_all_four_states_and_tls_reality_upstreams(self):
        for local in (False, True):
            for relay in (False, True):
                for reality in (False, True):
                    with self.subTest(local=local, relay=relay, reality=reality):
                        result, _ = self.configuration(local, relay, reality)
                        path = self.root / "check.json"
                        path.write_text(json.dumps(result.config)); path.chmod(0o600)
                        check = subprocess.run([str(self.binary), "check", "-c", str(path)],
                                               capture_output=True, text=True, timeout=10, env=self.env)
                        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)

    def test_real_mixed_auth_and_literal_self_target_reject_without_upstream(self):
        target_port, requests = start_http_target(self.stack)
        direct = http.client.HTTPConnection("127.0.0.1", target_port, timeout=3)
        direct.request("GET", "/control")
        self.assertEqual(direct.getresponse().read(), b"VUI-LOOPBACK-TARGET"); direct.close()
        self.assertEqual(len(requests), 1)
        result, value = self.configuration()
        process, log = self.core(result.config, value["settings"]["local_port"])
        port = value["settings"]["local_port"]
        status, _ = self.request(port, target_port, password="wrong-fixture-password")
        self.assertEqual(status, 407)
        self.assertEqual(len(requests), 1)
        offset = log.stat().st_size
        self.assert_failure(port, target_port)
        text = self.wait_reason(log, ("reject",), offset)
        self.assertEqual(len(requests), 1)
        self.assertNotIn("outbound/vless", text)
        self.assertNotIn("outbound/direct", text)
        self.assertIsNone(process.process.poll())
        self.assertEqual(result.config["inbounds"][0]["listen"], "127.0.0.1")
        self.assertFalse(result.config["inbounds"][0]["set_system_proxy"])

    def test_real_socks_udp_reject_has_target_zero_delivery(self):
        target_port, packets = start_udp_echo(self.stack)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as direct:
            direct.settimeout(3); direct.sendto(b"control", ("127.0.0.1", target_port))
            self.assertEqual(direct.recv(1024), b"VUI-UDP-ECHO:control")
        self.assertEqual(len(packets), 1)
        result, value = self.configuration()
        _, log = self.core(result.config, value["settings"]["local_port"])
        with socket.create_connection(("127.0.0.1", value["settings"]["local_port"]), timeout=3) as control:
            control.settimeout(3); control.sendall(b"\x05\x01\x02")
            self.assertEqual(_recv_exact(control, 2), b"\x05\x02")
            user, password = b"fixture-user", PASSWORD.encode()
            control.sendall(b"\x01" + bytes([len(user)]) + user + bytes([len(password)]) + password)
            self.assertEqual(_recv_exact(control, 2), b"\x01\x00")
            control.sendall(b"\x05\x03\x00\x01\x00\x00\x00\x00\x00\x00")
            reply = _recv_exact(control, 4)
            self.assertEqual(reply[:3], b"\x05\x00\x00")
            host, port = _socks_address_from_stream(control, reply[3])
            host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
            offset = log.stat().st_size
            with socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET, socket.SOCK_DGRAM) as udp:
                udp.settimeout(.4)
                udp.sendto(b"\x00\x00\x00" + _socks_udp_target("127.0.0.1", target_port) + b"must-reject", (host, port))
                try:
                    response = udp.recv(4096)
                    self.fail("Rejected UDP received application data: " + repr(response))
                except (socket.timeout, ConnectionRefusedError):
                    pass
            text = self.wait_reason(log, ("network=udp", "reject"), offset)
            self.assertNotIn("outbound/vless", text)
            self.assertNotIn("outbound/direct", text)
        self.assertEqual(len(packets), 1)

    def test_real_closed_upstream_does_not_fall_back_to_reachable_target(self):
        target_port, requests = start_http_target(self.stack)
        direct = http.client.HTTPConnection("127.0.0.1", target_port, timeout=3)
        direct.request("GET", "/control")
        self.assertEqual(direct.getresponse().status, 200); direct.close()
        value = snapshot(True, False, server="127.0.0.2")
        value["settings"]["local_port"] = unused_port()
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.2", 0))
            # Bound but not listening: deterministic refused upstream while the
            # application target remains demonstrably reachable on loopback.
            value["upstreams"][PROFILE_ID]["server_port"] = reservation.getsockname()[1]
            policy = AddressPolicy._for_tests(allowed_addresses=("127.0.0.1", "127.0.0.2"))
            base = SingBoxAdapter().build_config([])
            base["log"] = {"level": "debug", "timestamp": False}
            result = compile_proxy_policy(base, [], value, endpoint=endpoint(value, ("127.0.0.2",)),
                                          self_addresses=("127.0.0.3",), policy=policy)
            _, log = self.core(result.config, value["settings"]["local_port"])
            offset = log.stat().st_size
            self.assert_failure(value["settings"]["local_port"], target_port)
            text = self.wait_reason(log, ("connection refused",), offset)
            self.assertEqual(len(requests), 1)
            self.assertNotIn("outbound/direct", text)
            self.assertIn(UPSTREAM_TAG, text)
            self.assertIn(LOCAL_TAG, text)


if __name__ == "__main__":
    unittest.main()
