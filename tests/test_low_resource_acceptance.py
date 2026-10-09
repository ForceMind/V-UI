"""Offline structural/control tests. These never invoke sudo or host systemd."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/low_resource_acceptance.py"
spec = importlib.util.spec_from_file_location("low_resource_acceptance", SCRIPT)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)
UNIT = "vui-low-resource-" + "a" * 32 + ".service"
COMMIT = "b" * 40


class LowResourceAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="vui-cgroup-unit-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.cg = self.root / "system.slice" / UNIT
        self.cg.mkdir(parents=True)
        for name, text in {"memory.max": str(gate.MEMORY_BYTES), "memory.swap.max": "0",
                           "cpu.max": "100000 100000", "memory.current": "12000000",
                           "memory.peak": "16000000", "memory.events": "low 0\nhigh 0\nmax 2\noom 0\noom_kill 0\n",
                           "memory.stat": "anon 8000000\nfile 4000000\nslab 123456\n",
                           "cpu.stat": "usage_usec 12345\nuser_usec 12000\nsystem_usec 345\n"}.items():
            (self.cg / name).write_text(text)
        self.args = argparse.Namespace(bundle=self.root / "candidate.zip", source_commit=COMMIT,
                                       output=self.root / "acceptance.json", unit=UNIT, work_dir=self.root, memory_mib=512)

    def test_requires_both_explicit_hosted_markers_and_nonroot_linux(self):
        with patch.object(gate.os, "geteuid", return_value=1001), patch.object(gate.platform, "system", return_value="Linux"):
            for env in ({}, {"GITHUB_ACTIONS": "true"}, {"GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "self-hosted"}):
                with patch.dict(os.environ, env, clear=True), self.assertRaises(RuntimeError):
                    gate.require_hosted_runner()
            with patch.dict(os.environ, {"GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "github-hosted"}, clear=True):
                gate.require_hosted_runner()
                with patch.object(gate.os, "geteuid", return_value=0), self.assertRaises(RuntimeError):
                    gate.require_hosted_runner()
                with patch.object(gate.platform, "system", return_value="Darwin"), self.assertRaises(RuntimeError):
                    gate.require_hosted_runner()

    def test_cgroup_membership_requires_exact_unique_unit(self):
        self.assertEqual(gate.cgroup_directory(UNIT, f"0::/system.slice/{UNIT}\n", self.root), self.cg)
        for membership in ("0::/\n", "1:memory:/test\n", f"0::/../{UNIT}\n", f"0::/missing/{UNIT}\n",
                           f"0::/system.slice/{UNIT}\n0::/system.slice/{UNIT}\n"):
            with self.subTest(membership=membership), self.assertRaises(RuntimeError):
                gate.cgroup_directory(UNIT, membership, self.root)
        with self.assertRaises(RuntimeError):
            gate.cgroup_directory("ssh.service", "0::/system.slice/ssh.service", self.root)

    def test_exact_cpu_memory_swap_limits_required(self):
        self.assertEqual(gate.verify_limits(self.cg)["memory.max"], gate.MEMORY_BYTES)
        for name, value in (("memory.max", "max"), ("memory.max", str(gate.MEMORY_BYTES * 2)),
                            ("memory.swap.max", "1"), ("cpu.max", "max 100000"),
                            ("cpu.max", "200000 100000"), ("cpu.max", "50000 100000"), ("cpu.max", "0 0")):
            original = (self.cg / name).read_text()
            (self.cg / name).write_text(value)
            with self.subTest(name=name, value=value), self.assertRaises(RuntimeError):
                gate.verify_limits(self.cg)
            (self.cg / name).write_text(original)

    def test_all_memory_profiles_exactly_validated_and_forwarded(self):
        for profile in (320, 384, 512):
            with self.subTest(profile=profile):
                self.args.memory_mib = profile
                (self.cg / "memory.max").write_text(str(profile * 1024 * 1024))
                self.assertEqual(gate.verify_limits(self.cg, profile)["memory.max"], profile * 1024 * 1024)
                other = 512 if profile != 512 else 384
                with self.assertRaises(RuntimeError): gate.verify_limits(self.cg, other)
                command = gate.systemd_command(self.args, UNIT, self.root, self.args.output)
                self.assertIn(f"MemoryMax={profile}M", command)
                self.assertEqual(command[command.index("--memory-mib") + 1], str(profile))
        with self.assertRaises(RuntimeError): gate.memory_bytes(1024)

    def test_memory_stat_requires_anon_and_file_and_preserves_other_counters(self):
        self.assertEqual(gate.metrics(self.cg)["memory.stat"], {"anon": 8000000, "file": 4000000, "slab": 123456})
        for value in ("anon 1\n", "file 1\n", "anon -1\nfile 2\n"):
            (self.cg / "memory.stat").write_text(value)
            with self.subTest(value=value), self.assertRaises(RuntimeError): gate.metrics(self.cg)
        (self.cg / "memory.stat").unlink()
        with self.assertRaises(FileNotFoundError): gate.metrics(self.cg)

    def test_coordinator_smaller_profiles_preserve_requested_budget(self):
        for profile in (320, 384):
            with self.subTest(profile=profile):
                self.args.memory_mib = profile
                self.args.output = self.root / f"acceptance-{profile}.json"
                status, summary, calls = self.run_coordinator("passed")
                self.assertEqual(status, 0)
                self.assertEqual(summary["requested_memory_mib"], profile)
                self.assertIn(f"{profile} MiB cgroup", summary["scope"])
                self.assertIn(f"MemoryMax={profile}M", calls[0])

    def test_peak_and_cpu_evidence_mandatory(self):
        value = gate.metrics(self.cg)
        self.assertEqual(value["memory.peak"], 16000000)
        gate.assert_no_oom(value)  # memory.events max alone is not an OOM.
        (self.cg / "memory.peak").unlink()
        with self.assertRaises(FileNotFoundError):
            gate.metrics(self.cg)
        (self.cg / "memory.peak").write_text("0")
        with self.assertRaises(RuntimeError):
            gate.metrics(self.cg)
        (self.cg / "memory.peak").write_text("10")
        (self.cg / "cpu.stat").write_text("user_usec 1\n")
        with self.assertRaises(RuntimeError):
            gate.metrics(self.cg)

    def test_any_oom_counter_fails_even_without_oom_kill(self):
        for key in ("oom", "oom_kill", "oom_group_kill"):
            value = gate.metrics(self.cg)
            value["memory.events"][key] = 1
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                gate.assert_no_oom(value)

    def test_missing_oom_counters_fail(self):
        (self.cg / "memory.events").write_text("high 0\nmax 0\n")
        with self.assertRaises(RuntimeError):
            gate.metrics(self.cg)

    def test_scoped_systemd_command_accounts_entire_worker(self):
        command = gate.systemd_command(self.args, UNIT, self.root, self.args.output)
        self.assertEqual(command[:5], ["sudo", "-n", "systemd-run", "--wait", "--pipe"])
        for option in ("MemoryMax=512M", "MemorySwapMax=0", "CPUQuota=100%", "RuntimeMaxSec=600",
                       "KillMode=control-group", "OOMPolicy=stop", "--setenv=TMPDIR=" + str(self.root)):
            self.assertIn(option, command)
        self.assertEqual(command[command.index("--unit") + 1], UNIT)
        self.assertIn(str(SCRIPT), command)
        self.assertIn(COMMIT, command)
        self.assertNotIn("--scope", command)
        self.assertNotIn("--user", command)

    def run_coordinator(self, mode):
        calls = []
        def fake_run(command, **kwargs):
            calls.append(command)
            if "systemd-run" in command:
                unit = command[command.index("--unit") + 1]
                report_path = Path(command[command.index("--output") + 1])
                if mode == "timeout":
                    raise subprocess.TimeoutExpired(command, 660)
                if mode != "missing":
                    report = {"unit": unit, "source_commit": COMMIT, "outcome": "passed", "complete": True,
                              "duration_profile": getattr(self.args, "duration_profile", "smoke"),
                              "metrics": gate.metrics(self.cg), "requested_memory_mib": self.args.memory_mib,
                              "limits": {"memory.max": gate.memory_bytes(self.args.memory_mib)},
                              "base_page_size_bytes": os.sysconf("SC_PAGE_SIZE")}
                    from app.release_tools import target_key
                    from deploy.system_launcher import panel_environment
                    from scripts.low_resource_service_tree import allocator_fields
                    policy = allocator_fields(panel_environment({}, target_key()))
                    group = '0::/system.slice/' + unit
                    report.update(worker_pid=10, service_cgroup=group)
                    report['proxy_workload'] = {'server_tree': dict(
                        parent_role='resource_fixture_worker', worker_pid=10, runtime_key=target_key(),
                        policy=policy, cleanup_complete=True, roles={
                            'watchdog':dict(pid=20,ppid=10,starttime_ticks=1,process_group=20,cgroup=group,allocator=policy),
                            'core':dict(pid=21,ppid=20,starttime_ticks=2,process_group=20,cgroup=group,allocator=policy)})}
                    if mode == "wrong_duration_profile": report["duration_profile"] = "sustained"
                    if mode == "partial": report["complete"] = False
                    if mode == "wrong_source": report["source_commit"] = "c" * 40
                    if mode == "wrong_profile": report["requested_memory_mib"] = 384 if self.args.memory_mib == 512 else 512
                    if mode == "wrong_limit": report["limits"]["memory.max"] = 123
                    if mode == "oom": report["metrics"]["memory.events"]["oom"] = 1
                    if mode == "over_peak": report["metrics"]["memory.peak"] = gate.memory_bytes(self.args.memory_mib) + os.sysconf("SC_PAGE_SIZE") + 1
                    if mode == "one_page_peak": report["metrics"]["memory.peak"] = gate.memory_bytes(self.args.memory_mib) + os.sysconf("SC_PAGE_SIZE")
                    if mode == "wrong_page": report["base_page_size_bytes"] = os.sysconf("SC_PAGE_SIZE") * 2
                    if mode == "failed": report["outcome"] = "failed"
                    report_path.write_text(json.dumps(report))
                return subprocess.CompletedProcess(command, 1 if mode == "nonzero" else 0)
            return subprocess.CompletedProcess(command, 0, "ActiveState=inactive\n", "")
        with patch.object(gate, "require_hosted_runner"), patch.object(gate.subprocess, "run", side_effect=fake_run):
            status = gate.coordinator(self.args)
        return status, json.loads(self.args.output.read_text()), calls

    def test_coordinator_success_keeps_exact_evidence_and_scoped_cleanup(self):
        status, summary, calls = self.run_coordinator("passed")
        self.assertEqual(status, 0)
        self.assertEqual(summary["outcome"], "passed")
        self.assertTrue((self.root / "acceptance-worker.json").exists())
        for command in calls[1:]:
            self.assertEqual(command[-1], summary["unit"])
            self.assertIn(command[3], ("show", "stop", "reset-failed"))
        self.assertFalse(list(self.root.glob("low-resource-work-*")))

    def test_coordinator_failures_never_silently_skip(self):
        for mode in ("missing", "partial", "wrong_duration_profile", "wrong_source", "wrong_profile", "wrong_limit", "wrong_page", "oom", "over_peak", "failed", "nonzero", "timeout"):
            with self.subTest(mode=mode):
                self.args.output = self.root / (mode + ".json")
                status, summary, calls = self.run_coordinator(mode)
                self.assertEqual(status, 1)
                self.assertEqual(summary["outcome"], "failed")
                self.assertIn("error", summary)
                self.assertEqual(calls[-1][3], "reset-failed")

    def test_fixed_one_page_allowance_is_disclosed_and_never_expanded(self):
        for page in (4096, 16384, 65536):
            at = gate.peak_assessment(gate.MEMORY_BYTES + page, gate.MEMORY_BYTES, page)
            self.assertFalse(at["within_nominal_budget"])
            self.assertEqual(at["nominal_overage_bytes"], page)
            self.assertTrue(at["within_fixed_page_allowance"])
            self.assertFalse(gate.peak_assessment(gate.MEMORY_BYTES + page + 1,
                gate.MEMORY_BYTES, page)["within_fixed_page_allowance"])
        for page in (0, -1, 8192, 2 * 1024 * 1024, True):
            with self.assertRaises(RuntimeError): gate.peak_assessment(100, gate.MEMORY_BYTES, page)
        status, summary, _ = self.run_coordinator("one_page_peak")
        self.assertEqual(status, 0)
        self.assertFalse(summary["peak_budget_assessment"]["within_nominal_budget"])
        self.assertEqual(summary["peak_budget_assessment"]["nominal_overage_bytes"], os.sysconf("SC_PAGE_SIZE"))

    def test_existing_evidence_not_overwritten(self):
        self.args.output.write_text("existing")
        with patch.object(gate, "require_hosted_runner"), patch.object(gate.subprocess, "run") as run:
            with self.assertRaises(RuntimeError): gate.coordinator(self.args)
            run.assert_not_called()
        self.assertEqual(self.args.output.read_text(), "existing")

    def test_worker_failure_retains_initial_limits_and_final_counters(self):
        self.args.bundle.with_suffix(".zip.sha256").write_text("a" * 64 + " candidate.zip\n")
        from app import release_tools
        def stage_failure(*_):
            initial = json.loads(self.args.output.read_text())
            self.assertEqual(initial["limits"]["memory.max"], gate.MEMORY_BYTES)
            self.assertEqual(initial["source_commit"], COMMIT)
            self.assertFalse(initial["complete"])
            raise RuntimeError("Synthetic staging failure")
        with patch.object(gate, "require_hosted_runner"), patch.object(gate, "cgroup_directory", return_value=self.cg), \
             patch.object(gate, "filesystem_type", return_value="ext4"), \
             patch.object(release_tools, "supported_environment"), patch.object(release_tools, "stage", side_effect=stage_failure):
            self.assertEqual(gate.worker(self.args), 1)
        report = json.loads(self.args.output.read_text())
        self.assertTrue(report["complete"])
        self.assertEqual(report["outcome"], "failed")
        self.assertEqual(report["stages"][0]["outcome"], "failed")
        self.assertEqual(report["metrics"]["memory.peak"], 16000000)
        self.assertEqual(report["metrics"]["memory.stat"]["anon"], 8000000)
        self.assertEqual(report["stages"][0]["memory_stat"]["file"], 4000000)
        self.assertEqual(report["stages"][0]["memory_events"]["max"], 2)
        self.assertFalse(list(self.root.glob("installed-*")))

    def test_wrong_bundle_source_fails_before_provision_or_launch(self):
        self.args.bundle.with_suffix(".zip.sha256").write_text("a" * 64 + " candidate.zip\n")
        from app import release_tools
        with patch.object(gate, "require_hosted_runner"), patch.object(gate, "cgroup_directory", return_value=self.cg), \
             patch.object(gate, "filesystem_type", return_value="ext4"), \
             patch.object(release_tools, "supported_environment"), patch.object(release_tools, "stage", return_value="fake"), \
             patch.object(release_tools, "activate"), patch.object(release_tools, "active", return_value=(self.root, {})), \
             patch.object(release_tools, "verify_payload", return_value={"source_commit": "c" * 40}), \
             patch.object(gate.subprocess, "run") as run, patch.object(gate.subprocess, "Popen") as launch:
            self.assertEqual(gate.worker(self.args), 1)
            run.assert_not_called()
            launch.assert_not_called()
        self.assertEqual(json.loads(self.args.output.read_text())["outcome"], "failed")

    def test_tmpfs_work_rejected_before_stage(self):
        from app import release_tools
        with patch.object(gate, "require_hosted_runner"), patch.object(gate, "cgroup_directory", return_value=self.cg), \
             patch.object(gate, "filesystem_type", return_value="tmpfs"), patch.object(release_tools, "stage") as stage:
            self.assertEqual(gate.worker(self.args), 1)
            stage.assert_not_called()
        report = json.loads(self.args.output.read_text())
        self.assertTrue(report["complete"])
        self.assertEqual(report["outcome"], "failed")

    def test_workflow_uses_actual_bundle_before_existing_deployment_and_preserves_json(self):
        workflow = (SCRIPT.parents[1] / ".github/workflows/release.yml").read_text()
        self.assertIn("scripts/low_resource_acceptance.py", workflow)
        self.assertIn("--bundle /tmp/vui-release/vui-linux.zip", workflow)
        self.assertIn("--source-commit", workflow)
        self.assertLess(workflow.index("scripts/low_resource_acceptance.py"), workflow.index("test_release_deployment.py"))
        self.assertIn("/tmp/vui-release/low-resource*.json", workflow)
        for profile in (320, 384, 512):
            self.assertIn(f"--memory-mib {profile}", workflow)
            self.assertIn(f"--output /tmp/vui-release/low-resource-{profile}.json", workflow)
        self.assertEqual(workflow.count("python -B scripts/low_resource_acceptance.py"), 3)
        self.assertIn("if: always()", workflow)


if __name__ == "__main__":
    unittest.main()
