"""Pure controls: no core starts, real network sockets, compiler or sleeps."""
from collections import Counter
from copy import deepcopy
import importlib.util
import inspect
from pathlib import Path
import struct
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import common as c
import compatibility as compat
import run_matrix as runner
import prepare_evidence


def identity():
    return {"vui_commit": "1" * 40, "vui_tree": "2" * 40,
            "files": {name: str(index + 3) * 64 for index, name in enumerate(c.PATCHES)}}


def provenance(arm):
    current = identity()
    label = c.version(arm, current["vui_commit"])
    return {"schema_version": c.SCHEMA, "kind": c.KIND, "arm": arm,
            "source_identity": current, "vui_source_commit": current["vui_commit"],
            "version": label, "patches": c.expected_patches(arm, current), "product_qualified": False,
            "binary_sha256": "a" * 64 if arm == "route-only" else "b" * 64,
            "fixed_sources": {"sing_box": c.builder.SING_BOX, "sing_vmess": c.builder.SING_VMESS},
            "module_graph_sha256": "c" * 64,
            "build": {"go_version": c.builder.GO_VERSION, "goos": "linux", "goarch": "amd64", "tags": [],
                      "cgo_enabled": "0", "gotoolchain": "local", "gowork": "off", "trimpath": True,
                      "buildvcs": False, "go_binary_sha256": "d" * 64,
                      "ldflags": "-X github.com/sagernet/sing-box/constant.Version=" + label},
            "source_files": {
                "sing-vmess/xudp.go": {"before": c.life.SOURCE_FILE_HASHES["sing_vmess_xudp"],
                                       "after": c.life.PATCH_IDENTITIES["remoteaddr"]["after"]},
                "sing-box/route/conn.go": {"before": c.builder.ROUTE_SOURCE_SHA256,
                                           "after": c.builder.ROUTE_PATCHED_SHA256 if arm == "route-only" else "e" * 64},
                "sing-vmess/mux.go": {"before": "f" * 64, "after": "f" * 64 if arm == "route-only" else "9" * 64},
                "sing-vmess/mux_writer.go": {"before": None, "after": None if arm == "route-only" else "8" * 64}}}


def summary(methods, failures=()):
    failed = {row["method"] for row in failures}
    return {"tests_run": len(methods), "methods": [{"method": name, "status": "failed" if name in failed else "passed"}
                                                   for name in methods], "failures": list(failures), "nonpass": [],
            "passed_methods": len(methods) - len(failed), "failed_methods": len(failed), "failure_records": len(failures)}


def groups():
    return {"original": summary(c.base.METHODS, [{"method": c.base.METHODS[12], "exception": "AssertionError",
                "known_failure": {"kind": "frozen_domain_reply_excluded_by_original_allowlist"}}]),
            "tcp": summary(c.base.TCP_METHODS), "dns_rebinding": summary(c.base.DNS_METHODS)}


def data_frame(sid, body, address=b""):
    header = struct.pack("!HBB", sid, 2, 1) + (b"\x02" + address if address else b"")
    return struct.pack("!H", len(header)) + header + struct.pack("!H", len(body)) + body


class IdentityTests(unittest.TestCase):
    def test_distinct_arm_identity_and_fixed_dependency_pair(self):
        values = {arm: provenance(arm) for arm in c.ARMS}
        runner.validate_pair(values, identity())
        self.assertNotEqual(values["mux"]["version"], values["route-only"]["version"])

    def test_cross_arm_schema_product_and_official_binary_rejected(self):
        for arm in c.ARMS:
            for key, value in (("schema_version", 2), ("arm", "old"), ("version", "1.14.2"),
                               ("product_qualified", True), ("binary_sha256", c.base.STOCK_HASHES["sing-box"])):
                with self.subTest(arm=arm, key=key), self.assertRaises(ValueError):
                    row = provenance(arm)
                    row[key] = value
                    c.validate_provenance(row, arm, identity())

    def test_reordered_patch_and_changed_commit_rejected(self):
        for action in (lambda row: row["patches"].reverse(),
                       lambda row: row["source_identity"].update(vui_tree="0" * 40)):
            row = provenance("mux")
            action(row)
            with self.assertRaises(ValueError):
                c.validate_provenance(row, "mux", identity())

    def test_compiler_graph_and_original_source_pair_drift_rejected(self):
        for action in (lambda row: row.update(module_graph_sha256="7" * 64),
                       lambda row: row["build"].update(go_binary_sha256="7" * 64),
                       lambda row: row["source_files"]["sing-vmess/mux.go"].update(before="7" * 64)):
            rows = {arm: provenance(arm) for arm in c.ARMS}
            action(rows["mux"])
            with self.assertRaises(ValueError):
                runner.validate_pair(rows, identity())

    def test_route_control_may_not_contain_mux_patch(self):
        row = provenance("route-only")
        row["source_files"]["sing-vmess/mux.go"]["after"] = "0" * 64
        with self.assertRaises(ValueError):
            c.validate_provenance(row, "route-only", identity())

    def test_missing_mux_delta_rejected(self):
        row = provenance("mux")
        row["source_files"]["sing-vmess/mux.go"]["after"] = row["source_files"]["sing-vmess/mux.go"]["before"]
        with self.assertRaises(ValueError):
            c.validate_provenance(row, "mux", identity())

    def test_clean_source_requires_tracked_exact_head(self):
        with patch.object(c, "git", side_effect=[b"1" * 40, b"2" * 40, b"changed-file"]):
            with self.assertRaisesRegex(ValueError, "differs from exact HEAD"):
                c.source_identity()

    def test_original_frozen_bytes_still_match(self):
        self.assertEqual({name: c.sha256(c.ROOT / name) for name in c.base.FROZEN_FILES}, c.base.FROZEN_FILES)


class OriginalResultTests(unittest.TestCase):
    def test_nineteen_of_twenty_is_red_separate_diagnostic(self):
        row = runner.assess_original(groups())
        self.assertEqual((row["executed"], row["passed"], row["failed"]), (20, 19, 1))
        self.assertIs(row["original_suite_passed"], False)

    def test_all_green_is_not_promoted(self):
        rows = groups()
        rows["original"] = summary(c.base.METHODS)
        with self.assertRaises(ValueError):
            runner.assess_original(rows)

    def test_skip_error_expected_failure_or_wrong_domain_reason_rejected(self):
        for action in (lambda rows: rows["original"]["nonpass"].append({"kind": "skip"}),
                       lambda rows: rows["original"].update(tests_run=16),
                       lambda rows: rows["original"]["failures"][0].update(known_failure=None),
                       lambda rows: rows["tcp"]["methods"][0].update(status="skipped")):
            rows = groups()
            action(rows)
            with self.assertRaises(ValueError):
                runner.assess_original(rows)

    def test_original_functions_not_reassigned(self):
        source = inspect.getsource(runner)
        self.assertEqual(source.count("patch.object("), 1)
        self.assertIn('patch.object(module, "RestrictedUdpFixture", fixture)', source)
        self.assertNotIn('patch.object(module.RestrictedUdpPreflightTests', source)


class WireTests(unittest.TestCase):
    def decoder(self, data):
        self.assertEqual(data, b"\x12\x34\x01\x7f\0\0\x03")
        return "127.0.0.3", 0x1234

    def test_split_response_header_and_tcp_payload(self):
        reader = compat.FrameReader(self.decoder)
        body = compat.payload(65535, "maximum")
        wire = b"\0\0" + data_frame(0, body)
        rows = []
        for offset in range(0, len(wire), 17):
            rows.extend(reader.feed(wire[offset:offset + 17]))
        self.assertEqual(rows, [(0, 2, None, body)])
        self.assertFalse(reader.buffer)

    def test_coalesced_tcp_end_and_udp_address(self):
        reader = compat.FrameReader(self.decoder)
        address = b"\x12\x34\x01\x7f\0\0\x03"
        wire = b"\0\0" + data_frame(1, b"abc") + b"\0\x04\0\x01\x03\0" + data_frame(2, b"udp", address)
        self.assertEqual(reader.feed(wire), [(1, 2, None, b"abc"), (1, 3, None, None),
                                            (2, 2, ("127.0.0.3", 0x1234), b"udp")])

    def test_unknown_session_error_end_bad_header_and_unbounded_input(self):
        for wire in (b"\0\0" + data_frame(3, b"bad"), b"\0\0\0\x04\0\0\x03\x02",
                     b"\0\0\0\x03", b"\0\0" + bytes(200000)):
            with self.subTest(size=len(wire)), self.assertRaises(ValueError):
                compat.FrameReader(self.decoder).feed(wire)

    def test_frame_count_bounded(self):
        reader = compat.FrameReader(self.decoder)
        with self.assertRaises(ValueError):
            reader.feed(b"\0\0" + data_frame(0, b"x") * 257)

    def test_tcp_requests_split_at_uint16_without_mutating_old_helper(self):
        module = SimpleNamespace(xudp_address=lambda host, port: b"address")
        body = compat.payload(compat.TCP_BYTES, "request")
        message = struct.pack("!I", len(body)) + body
        frames = [compat.tcp_frame(module, 1, "127.0.0.3", 3000, message[pos:pos + 65535], pos == 0)
                  for pos in range(0, len(message), 65535)]
        self.assertEqual(len(frames), 4)
        self.assertEqual(frames[0][2:7], b"\0\x01\x01\x01\x01")
        self.assertEqual(frames[1][2:6], b"\0\x01\x02\x01")
        with self.assertRaises(ValueError):
            compat.tcp_frame(module, 1, "127.0.0.3", 3000, bytes(65536), True)


class CompatibilityTests(unittest.TestCase):
    def test_explicit_encrypted_xudp_configuration(self):
        def domestic():
            return {"inbounds": [{"type": "vless", "users": [{"uuid": "fake", "flow": ""}],
                                  "tls": {"enabled": True}}]}
        def client(kind):
            self.assertEqual(kind, "singbox")
            return {"outbounds": [{"type": "vless", "flow": "", "packet_encoding": "xudp",
                                   "tls": {"enabled": True, "server_name": "vpn.example.test"}}]}
        fixture = SimpleNamespace(guarded_config=domestic, client_config=client)
        for security in compat.VMESS.values():
            inbound, outbound = compat.vmess_configs(fixture, security)
            node = outbound["outbounds"][0]
            self.assertEqual(inbound["inbounds"][0]["type"], "vmess")
            self.assertEqual(node["security"], security)
            self.assertTrue(node["global_padding"] and node["authenticated_length"])
            self.assertNotIn("multiplex", node)
            self.assertNotIn("flow", node)
            self.assertNotIn("insecure", node["tls"])

    def test_independent_large_udp_recorder_does_not_truncate(self):
        target = compat.LargeDatagramTarget.__new__(compat.LargeDatagramTarget)
        target.lock, target.stop = threading.Lock(), threading.Event()
        target.received, target.errors = [], []
        body = compat.payload(60000, "large")
        target.stream = Mock()
        target.stream.recvfrom.return_value = (body, ("127.0.0.4", 12000))
        target.stream.sendto.side_effect = lambda *args: target.stop.set()
        target.run()
        target.stream.recvfrom.assert_called_once_with(65535)
        target.stream.sendto.assert_called_once_with(body, ("127.0.0.4", 12000))
        self.assertEqual(target.received, [("127.0.0.4", body)])
        self.assertEqual(target.errors, [])

    def test_tcp_ledger_rejects_wrong_peer_duplicate_missing_and_extra(self):
        target = compat.TcpTarget.__new__(compat.TcpTarget)
        target.peer, target.bodies, target.errors = "127.0.0.4", [b"a", b"b"], []
        for rows in ([('127.0.0.5', b'a'), ('127.0.0.4', b'b')], [],
                     [('127.0.0.4', b'a')] * 2,
                     [('127.0.0.4', b'a'), ('127.0.0.4', b'b'), ('127.0.0.4', b'c')]):
            target.received = rows
            with self.assertRaises(ValueError):
                target.audit()
        target.received = [(target.peer, body) for body in target.bodies]
        target.audit()

    def test_artifact_allowlist_excludes_binary_config_and_material(self):
        for name in ("candidate-sing-box", "candidate.json", "config.json", "ca.pem", "server.key",
                     "cases/compat_vmess_aes128gcm.yaml"):
            self.assertNotIn(name, runner.ARTIFACTS)
        for case in compat.CASES:
            self.assertIn("observations/" + case + ".json", runner.ARTIFACTS)


class PreservationTests(unittest.TestCase):
    def test_failed_incomplete_attempt_preserved_and_scrubbed(self):
        with tempfile.TemporaryDirectory(dir=HERE) as temporary:
            root = Path(temporary)
            runtime = root / "runtime"
            runtime.mkdir()
            (runtime / "runner-error.log").write_text("failure 11111111-1111-4111-8111-111111111111 b'packet'")
            entries = prepare_evidence.prepare(runtime, root / "missing-build", root / "upload")
            self.assertEqual(len(entries), 1)
            text = (root / "upload/runtime/runner-error.log").read_text()
            self.assertIn("failure <fixture> <packet-bytes>", text)
            self.assertFalse(c.read_json(root / "upload/MANIFEST.json")["product_qualified"])

    def test_unexpected_file_and_pem_refuse_packaging(self):
        for filename, body in (("config.json", '{}'), ("runner-error.log", "-----BEGIN CERTIFICATE-----")):
            with self.subTest(filename=filename), tempfile.TemporaryDirectory(dir=HERE) as temporary:
                root = Path(temporary)
                runtime = root / "runtime"
                runtime.mkdir()
                (runtime / filename).write_text(body)
                with self.assertRaises(ValueError):
                    prepare_evidence.prepare(runtime, root / "missing-build", root / "upload")

    def test_empty_attempt_is_never_success(self):
        with tempfile.TemporaryDirectory(dir=HERE) as temporary:
            root = Path(temporary)
            with self.assertRaises(ValueError):
                prepare_evidence.prepare(root / "missing-runtime", root / "missing-build", root / "upload")
            self.assertEqual(c.read_json(root / "upload/MANIFEST.json")["files"], [])


if __name__ == "__main__":
    unittest.main()


class MechanismIdentityTests(unittest.TestCase):
    def make_pair(self):
        import compare_mechanism as comparison
        runtime=provenance('mux')
        graph=[{'path':'fixed','version':'fixed'}]
        runtime['module_graph_sha256']=c.graph_digest(graph)
        mechanism={'arm':'mux','vui_commit':runtime['vui_source_commit'],'go_binary_sha256':runtime['build']['go_binary_sha256'],'product_qualified':False,'checks':[{'returncode':0}],
                   'source_sha256':{name:runtime['source_files']['sing-vmess/'+name]['after'] for name in ('mux.go','mux_writer.go','xudp.go')},'route_sha256':runtime['source_files']['sing-box/route/conn.go']['after'],
                   'patches':[{'name':Path(row['path']).name,'sha256':row['sha256']} for row in runtime['patches']]}
        return comparison,mechanism,runtime,graph
    def test_exact_tested_source_pair_passes(self):
        comparison,mechanism,runtime,graph=self.make_pair();comparison.compare_arm(mechanism,runtime,graph)
    def test_different_commit_source_graph_compiler_or_patch_is_rejected(self):
        for action in (lambda m:m.update(vui_commit='0'*40),lambda m:m.update(go_binary_sha256='0'*64),lambda m:m.update(route_sha256='0'*64),lambda m:m['source_sha256'].update(mux_writer_go='0'*64, **{'mux.go':'0'*64}),lambda m:m['patches'].reverse(),lambda m:m['checks'][0].update(returncode=1)):
            comparison,mechanism,runtime,graph=self.make_pair();action(mechanism)
            with self.assertRaises(ValueError):comparison.compare_arm(mechanism,runtime,graph)
