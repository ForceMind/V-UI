"""Shared Mihomo lifecycle and fixed call-site coverage, without real cores."""
import ast
from collections import Counter
from contextlib import ExitStack
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from loopback_helpers import CoreProcess
from proxy_loopback_support import BoundCoreProcess, ReadyMihomoProcess, fresh_lines
from test_proxy_double_hop import DoubleHopFixture


# Audited persistent-client construction sites. Parser/version subprocess.run
# and the HY2/TUIC sing-box-only UDP-server Popen paths are intentionally absent.
SITES = {
    ("restricted_udp_preflight_helpers.py", "RestrictedUdpFixture.process"): "bool-factory",
    ("test_loopback_chain.py", "LoopbackChainTests.start"): "direct",
    ("test_trojan_loopback.py", "TrojanLoopbackTests.start_mihomo"): "direct",
    ("test_vmess_loopback.py", "VMessLoopbackTests.start_mihomo"): "direct",
    ("test_shadowsocks_loopback.py", "ShadowsocksLoopbackTests.start_mihomo"): "direct",
    ("test_vless_ws_loopback.py", "VLESSWebSocketLoopbackTests.start_client"): "mixed",
    ("test_vless_grpc_preflight_loopback.py", "VLESSGRPCPreflightLoopbackTests.verify_both_clients"): "mixed",
    ("test_vless_grpc_loopback.py", "VLESSGRPCLoopbackTests.verify_both_clients"): "mixed",
    ("test_hysteria2_preflight_loopback.py", "Hysteria2PreflightLoopbackTests.verify_both_clients"): "mixed",
    ("test_tuic_preflight_loopback.py", "TUICPreflightLoopbackTests.verify_both_clients"): "mixed",
    ("test_reality_preflight_loopback.py", "RealityPreflightLoopbackTests.verify_clients"): "mixed",
    ("test_xhttp_preflight_loopback.py", "XHTTPPreflightLoopbackTests.verify_clients"): "direct",
    ("test_xhttp_preflight_loopback.py", "XHTTPPreflightLoopbackTests.observe_httpupgrade_protocols"): "direct",
    ("test_xhttp_preflight_loopback.py", "XHTTPPreflightLoopbackTests.test_httpupgrade_actual_uri_wrong_transport_control"): "direct",
    ("test_hysteria2_managed_certificate.py", "Hysteria2ManagedRealCoreTests.forward_both"): "mixed",
    ("test_tuic_managed_certificate.py", "TUICManagedRealCoreTests.forward_both"): "mixed",
    ("test_proxy_double_hop.py", "DoubleHopFixture.process"): "bound-factory",
    ("test_udp_dns_capability_loopback.py", "ShadowsocksFixture.core"): "udp-factory",
    ("test_udp_dns_capability_loopback.py", "MihomoStartupLoopbackTests.test_listener_and_socks_reply_precede_running_but_fresh_marker_gates_request"): "direct",
}


# Separately audited exceptions: sing-box/Xray servers/clients, two shared
# launch implementations, fake unit processes, and non-core app/worker/benchmark
# subprocesses. These entries do not grant Mihomo permission to use the old gate.
OTHER_CONSTRUCTIONS = {
    ('acme_helpers.py', 'PebbleFixture.__init__', 'Popen'),
    ('benchmark_archive_memory.py', 'main', 'Popen'),
    ('benchmark_panel_allocator.py', 'run', 'Popen'),
    ('loopback_helpers.py', 'CoreProcess.start', 'Popen'),
    ('proxy_loopback_support.py', 'BoundCoreProcess.start', 'Popen'),
    ('test_certificate_browser.py', 'CertificateBrowserTests.test_actual_issuance_status_and_renewal_policy_ui', 'Popen'),
    ('test_core_runtime.py', 'SafeCoreRuntimeTests.test_panel_process_death_terminates_watchdog_child', 'Popen'),
    ('test_data_owner.py', 'DataOwnerTests.test_database_connection_blocks_offline_until_closed', 'Popen'),
    ('test_data_owner.py', 'DataOwnerTests.test_source_startup_holds_owner_without_any_core_state', 'Popen'),
    ('test_hysteria2_preflight_loopback.py', 'Hysteria2PreflightLoopbackTests.start_udp_server', 'Popen'),
    ('test_hysteria2_preflight_loopback.py', 'Hysteria2PreflightLoopbackTests.verify_both_clients', 'CoreProcess'),
    ('test_inbound_editor_browser.py', 'InboundEditorBrowserTests.test_managed_security_transitions_refresh_export_and_restore.start', 'Popen'),
    ('test_inbound_editor_browser.py', 'SecurityCenterBrowserTests.test_scope_notice_navigation_without_security_requests', 'Popen'),
    ('test_loopback_chain.py', 'LoopbackChainTests.setUp', 'CoreProcess'),
    ('test_low_resource_accounting.py', 'AccountingTests.test_real_child_and_permission_failure_are_not_omitted', 'Popen'),
    ('test_low_resource_responder.py', 'start_responder', 'Popen'),
    ('test_low_resource_sustained.py', 'SustainedTests.test_actual_persistent_connect_preflight', 'CoreProcess'),
    ('test_maintenance_worker.py', 'MaintenanceWorkerTests.start_worker', 'Popen'),
    ('test_mihomo_readiness.py', 'SharedMihomoReadinessTests.test_plain_and_bound_call_styles_recheck_each_restart_generation', 'ReadyMihomoProcess'),
    ('test_mihomo_readiness.py', 'SharedMihomoReadinessTests.test_singbox_and_xray_keep_original_commands_without_post_up', 'process_class'),
    ('test_proxy_browser.py', 'ProxyBrowserTests.setUp', 'Popen'),
    ('test_proxy_diagnostics.py', 'DiagnosticWorkerTests.test_parent_cancellation_kills_and_reaps_process_group', 'Popen'),
    ('test_proxy_diagnostics.py', 'DiagnosticWorkerTests.test_sigterm_resistant_worker_is_killed_and_reaped', 'Popen'),
    ('test_proxy_diagnostics_real.py', 'DiagnosticRealWorkerTests.start_upstream', 'BoundCoreProcess'),
    ('test_proxy_policy_real.py', 'ProxyPolicyRealTests.core', 'CoreProcess'),
    ('test_proxy_store.py', 'ProxyStoreTests.test_concurrent_processes_have_exactly_one_cas_winner', 'Popen'),
    ('test_reality_preflight_loopback.py', 'RealityPreflightLoopbackTests.verify_clients', 'CoreProcess'),
    ('test_release_deployment.py', 'ReleaseDeploymentTests.test_nonroot_offline_https_browser_restore_and_code_rollback.start', 'Popen'),
    ('test_shadowsocks_loopback.py', 'ShadowsocksLoopbackTests.setUp', 'CoreProcess'),
    ('test_shadowsocks_loopback.py', 'ShadowsocksLoopbackTests.start_singbox_client', 'CoreProcess'),
    ('test_startup.py', 'ApplicationStartupTests.test_clean_install_starts_serves_ui_and_initializes_database', 'Popen'),
    ('test_trojan_loopback.py', 'TrojanLoopbackTests.setUp', 'CoreProcess'),
    ('test_trojan_loopback.py', 'TrojanLoopbackTests.start_singbox_client', 'CoreProcess'),
    ('test_tuic_preflight_loopback.py', 'TUICPreflightLoopbackTests.start_udp_server', 'Popen'),
    ('test_tuic_preflight_loopback.py', 'TUICPreflightLoopbackTests.verify_both_clients', 'CoreProcess'),
    ('test_udp_dns_capability_loopback.py', 'DatagramFixtureTests.test_mihomo_ready_partial_and_previous_generation_never_qualify', 'ReadyMihomoProcess'),
    ('test_udp_dns_capability_loopback.py', 'DatagramFixtureTests.test_mihomo_ready_waits_after_tcp_listener_without_request_retry', 'ReadyMihomoProcess'),
    ('test_vless_grpc_loopback.py', 'VLESSGRPCLoopbackTests.verify_both_clients', 'CoreProcess'),
    ('test_vless_grpc_preflight_loopback.py', 'VLESSGRPCPreflightLoopbackTests.verify_both_clients', 'CoreProcess'),
    ('test_vless_ws_loopback.py', 'VLESSWebSocketLoopbackTests.start_server', 'CoreProcess'),
    ('test_vmess_loopback.py', 'VMessLoopbackTests.setUp', 'CoreProcess'),
    ('test_vmess_loopback.py', 'VMessLoopbackTests.start_singbox_client', 'CoreProcess'),
    # D1 launches two real uvicorn panels, not client cores. Their sing-box
    # children remain under production CoreRuntime; Mihomo uses DoubleHopFixture.
    ('tcp_upstream_export_helpers.py', 'HTTPPanel.start', 'Popen'),
    ('test_tcp_upstream_export_browser.py', 'TCPUpstreamExportBrowserTests.start_panel', 'Popen'),
    ('test_workspace_browser.py', 'WorkspaceBrowserTests.test_drafts_persistence_grants_rotation_and_revocation', 'Popen'),
    ('test_xhttp_preflight_loopback.py', 'XHTTPPreflightLoopbackTests.test_httpupgrade_actual_uri_wrong_transport_control', 'CoreProcess'),
    ('test_xhttp_preflight_loopback.py', 'XHTTPPreflightLoopbackTests.verify_clients', 'CoreProcess'),
}


def process_constructions(filename, source):
    """Inventory explicit core factories and every raw Popen in tests.

    This is a fixed-call-site regression guard, not arbitrary Python dataflow
    analysis. New factories/aliases need an explicit review and inventory entry.
    """
    found = Counter()
    class Visitor(ast.NodeVisitor):
        def __init__(self):
            self.scope = []
        def visit_FunctionDef(self, node):
            self.scope.append(node.name)
            self.generic_visit(node)
            self.scope.pop()
        visit_AsyncFunctionDef = visit_FunctionDef
        visit_ClassDef = visit_FunctionDef
        def visit_Call(self, node):
            names = {item.id for item in ast.walk(node.func) if isinstance(item, ast.Name)}
            if isinstance(node.func, ast.Attribute) and node.func.attr == "Popen":
                signature = "Popen"
            elif names & {"CoreProcess", "BoundCoreProcess", "ReadyMihomoProcess", "process_class"}:
                signature = "mixed" if isinstance(node.func, ast.IfExp) else ast.unparse(node.func)
            else:
                signature = None
            if signature:
                found[(filename, ".".join(self.scope), signature)] += 1
            self.generic_visit(node)
    Visitor().visit(ast.parse(source))
    return found


def scoped_functions(tree, scope=()):
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            name = (*scope, node.name)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                yield ".".join(name), node
            yield from scoped_functions(node, name)
        else:
            yield from scoped_functions(node, scope)


class SharedMihomoReadinessTests(unittest.TestCase):
    def test_every_fixed_client_construction_selects_the_shared_barrier(self):
        root = Path(__file__).resolve().parent
        choices = {"ReadyMihomoProcess": ReadyMihomoProcess, "CoreProcess": CoreProcess,
                   "BoundCoreProcess": BoundCoreProcess}
        for (filename, scope), mode in SITES.items():
            with self.subTest(file=filename, factory=scope):
                function = dict(scoped_functions(ast.parse((root/filename).read_text())))[scope]
                if mode.endswith("factory"):
                    expressions = [node.value for node in ast.walk(function) if isinstance(node, ast.Assign)
                                   and any(isinstance(target, ast.Name) and target.id == "process_class"
                                           for target in node.targets)]
                else:
                    expressions = [node.func for node in ast.walk(function) if isinstance(node, ast.Call)
                                   and "ReadyMihomoProcess" in ast.unparse(node.func)]
                self.assertEqual(len(expressions), 1, "Missing or duplicate known Mihomo constructor")
                expression = expressions[0]
                if mode == "direct":
                    self.assertEqual(ast.unparse(expression), "ReadyMihomoProcess")
                else:
                    variable = "mihomo" if mode == "bool-factory" else "kind" if mode == "udp-factory" else "client"
                    variants = (True,) if mode == "bool-factory" else ("mihomo", "mihomo-uri") if mode == "mixed" else ("mihomo",)
                    for variant in variants:
                        self.assertIs(eval(compile(ast.Expression(expression), filename, "eval"),
                                           {"__builtins__": {}}, {**choices, variable: variant}), ReadyMihomoProcess)
                    other = False if mode == "bool-factory" else "sing-box" if mode == "udp-factory" else "singbox"
                    self.assertIs(eval(compile(ast.Expression(expression), filename, "eval"),
                                       {"__builtins__": {}}, {**choices, variable: other}),
                                  BoundCoreProcess if mode.endswith("factory") else CoreProcess)

    def test_all_explicit_core_and_popen_sites_are_in_the_reviewed_inventory(self):
        root = Path(__file__).resolve().parent
        found = Counter()
        for path in root.rglob("*.py"):
            found.update(process_constructions(str(path.relative_to(root)), path.read_text()))
        expected = Counter(OTHER_CONSTRUCTIONS)
        expected.update((filename, scope, "ReadyMihomoProcess" if mode == "direct" else
                         "mixed" if mode == "mixed" else "process_class")
                        for (filename, scope), mode in SITES.items())
        self.assertEqual(found, expected, "New/changed process construction requires a startup-barrier audit")

    def test_inventory_detects_new_file_and_extra_core_in_an_existing_ready_factory(self):
        for filename, source in (
                ("new_fixture.py", "def start():\n    CoreProcess(stack, [mihomo], path, env)\n"),
                ("new_fixture.py", "def start():\n    subprocess.Popen([mihomo])\n"),
                ("test_loopback_chain.py", "class LoopbackChainTests:\n    def start(self):\n"
                 "        ReadyMihomoProcess(stack, [mihomo], path, env)\n"
                 "        CoreProcess(stack, [mihomo], path, env)\n")):
            with self.subTest(file=filename, source=source):
                found = process_constructions(filename, source)
                allowed = Counter((file, scope, "ReadyMihomoProcess") for file, scope in SITES)
                self.assertTrue(found - allowed, "An ungated constructor escaped the inventory")

    def test_fresh_lines_requires_complete_new_records(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/"core.log"
            path.write_text("old partial ")
            offset = len(path.read_text())
            with path.open("a") as log:
                log.write("connect: connection refused\nnew connect: connection refused")
            self.assertEqual(fresh_lines(path, offset), [])
            with path.open("a") as log:
                log.write("\ntrailing incomplete connect: connection refused")
            self.assertEqual(fresh_lines(path, offset), ["new connect: connection refused"])
            with path.open("a") as log:
                log.write("\n")
            self.assertEqual(fresh_lines(path, offset), ["new connect: connection refused",
                                                        "trailing incomplete connect: connection refused"])

    def test_plain_and_bound_call_styles_recheck_each_restart_generation(self):
        for explicit_host in (None, "127.0.0.2"):
            with self.subTest(host=explicit_host), tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
                core = ReadyMihomoProcess(stack, ["nonstandard-name", "-f", "fixture.yaml"],
                                         Path(temporary)/"core.log", {})
                endpoints = []
                def listener_ready(owner, port, host):
                    child = SimpleNamespace(pid=4100 + owner.generation, returncode=None)
                    child.poll = lambda: child.returncode
                    child.terminate = lambda: setattr(child, "returncode", -15)
                    child.wait = lambda timeout: child.returncode
                    owner.process = child
                    endpoints.append((host, port))
                    owner.ready_marker.write_text(f"{child.pid}\n")
                with patch.object(BoundCoreProcess, "start", listener_ready):
                    args = (18080,) if explicit_host is None else (18080, explicit_host)
                    core.start(*args)
                    first, first_pid = core.ready_marker, core.process.pid
                    core.stop()
                    core.start(*args)
                    self.assertNotEqual(core.ready_marker, first)
                    self.assertNotEqual(core.process.pid, first_pid)
                    self.assertEqual(first.read_text(), f"{first_pid}\n")
                    self.assertEqual(core.ready_marker.read_text(), f"{core.process.pid}\n")
                    self.assertEqual(core.command.count("-post-up"), 1)
                    self.assertEqual(endpoints, [(explicit_host or "127.0.0.1", 18080)] * 2)

    def test_shared_double_hop_factory_covers_inherited_clients_without_filename_guessing(self):
        with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
            case = DoubleHopFixture()
            case.root, case.stack, case.sequence, case.env = Path(temporary), stack, 0, {}
            case.mihomo, case.singbox = "arbitrary-client-executable", "sing-box"
            case.sanitized = lambda text: text
            with patch("test_proxy_double_hop.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout="", stderr="")):
                self.assertIsInstance(case.process("client", {}, client="mihomo"), ReadyMihomoProcess)
                self.assertIs(type(case.process("server", {}, client="singbox")), BoundCoreProcess)

    def test_singbox_and_xray_keep_original_commands_without_post_up(self):
        for process_class in (CoreProcess, BoundCoreProcess):
            for binary in ("sing-box", "xray"):
                with self.subTest(process=process_class.__name__, binary=binary), \
                     tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
                    command = [binary, "run", "-c", "fixture.json"]
                    core = process_class(stack, command, Path(temporary)/"core.log", {})
                    child = SimpleNamespace(poll=lambda: None)
                    with patch("loopback_helpers.subprocess.Popen", return_value=child) as launch, \
                         patch("loopback_helpers.socket.create_connection"):
                        core.start(18080)
                        self.assertEqual(launch.call_args.args[0], command)
                        self.assertNotIn("-post-up", launch.call_args.args[0])
                    core.process = None


if __name__ == "__main__":
    unittest.main()
