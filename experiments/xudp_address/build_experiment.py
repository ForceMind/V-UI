"""Build isolated source controls; never used by an installer or release packager."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

SING_BOX = "af6e64c3b69e6132ebaee0e1a3d24e93903f6709"
SING_VMESS = "31ec11e8790c48a4bf11711806a61e929ee6b89d"
GO_VERSION = "go1.25.14"
KIND = "vui-xudp-address-source-experiment"
SING_VERSION = "v0.9.6-0.20260922013354-87c33f17688f"


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def command(args, cwd, env=None, *, timeout=1200, capture=False):
    print("experiment command:", json.dumps([str(x) for x in args]), flush=True)
    return subprocess.run([str(x) for x in args], cwd=cwd, env=env, check=True,
                          text=True, capture_output=capture, timeout=timeout)


def fixed_checkout(path, expected):
    path = Path(path).resolve(strict=True)
    actual = command(["git", "rev-parse", "HEAD"], path, capture=True).stdout.strip()
    dirty = command(["git", "status", "--porcelain"], path, capture=True).stdout
    if actual != expected or dirty:
        raise ValueError("Official checkout is wrong or dirty: " + str(path))
    return path


def verify_module_graph(text, local_vmess):
    decoder, rows = json.JSONDecoder(), []
    remaining = text.strip()
    while remaining:
        row, end = decoder.raw_decode(remaining)
        rows.append(row)
        remaining = remaining[end:].lstrip()
    by_path = {row["Path"]: row for row in rows}
    if not rows or len(rows) != len(by_path) or any(row.get("Error") for row in rows):
        raise ValueError("Empty, duplicate or errored module graph")
    if by_path.get("github.com/sagernet/sing", {}).get("Version") != SING_VERSION:
        raise ValueError("Packet adapter version changed")
    vmess = by_path.get("github.com/sagernet/sing-vmess", {})
    if vmess.get("Version") != "v0.2.8" or vmess.get("Replace", {}).get("Path") != str(local_vmess):
        raise ValueError("Unexpected XUDP dependency replacement")
    if vmess.get("Replace", {}).get("Version") or Path(vmess.get("Dir", "")).resolve() != local_vmess:
        raise ValueError("XUDP module did not resolve to the exact experiment source")
    if [row["Path"] for row in rows if row.get("Main")] != ["github.com/sagernet/sing-box"]:
        raise ValueError("Unexpected main module or workspace injection")
    if {row["Path"] for row in rows if row.get("Replace")} != {"github.com/sagernet/sing-vmess"}:
        raise ValueError("Unexpected dependency replacement")
    return sorted([{"path": row["Path"], "version": row.get("Version"),
                    "main": bool(row.get("Main")),
                    "replace": "<fixed-local-sing-vmess>" if row.get("Replace") else None}
                   for row in rows], key=lambda row: row["path"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sing-box", required=True, type=Path)
    parser.add_argument("--sing-vmess", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    commit = command(["git", "rev-parse", "HEAD"], root, capture=True).stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Missing exact V-UI experiment identity")
    box = fixed_checkout(args.sing_box, SING_BOX)
    vmess = fixed_checkout(args.sing_vmess, SING_VMESS)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    evidence = output / "evidence"
    evidence.mkdir()
    env = {**os.environ, "GOTOOLCHAIN": "local", "GOWORK": "off", "CGO_ENABLED": "0",
           "GOOS": "linux", "GOARCH": "amd64", "GOFLAGS": "-mod=readonly",
           "GOPROXY": "https://proxy.golang.org", "GOSUMDB": "sum.golang.org",
           "GOMODCACHE": str(output / "module-cache"), "GOCACHE": str(output / "build-cache")}
    go = shutil.which("go", path=env.get("PATH"))
    if go is None:
        raise ValueError("Existing official runner Go compiler is required")
    version = command([go, "version"], root, env, capture=True).stdout.strip()
    if version != "go version " + GO_VERSION + " linux/amd64":
        raise ValueError("Unexpected existing Go toolchain: " + version)
    mod = (box / "go.mod").read_text()
    if ("github.com/sagernet/sing-vmess v0.2.8\n" not in mod or
            "github.com/sagernet/sing v0.9.6-0.20260922013354-87c33f17688f\n" not in mod):
        raise ValueError("Official core dependency pins differ")
    patch = Path(__file__).with_name("remoteaddr.patch").resolve(strict=True)
    patch_text = patch.read_text()
    changed = re.findall(r"^\+\+\+ b/(.+)$", patch_text, re.M)
    if changed != ["xudp.go"] or re.findall(r"^--- a/(.+)$", patch_text, re.M) != ["xudp.go"]:
        raise ValueError("Experiment patch must change exactly sing-vmess/xudp.go")
    test = Path(__file__).with_name("xudp_remoteaddr_experiment_test.go").resolve(strict=True)
    source_hashes = {"sing_box_go_mod": sha256(box / "go.mod"),
                     "sing_box_go_sum": sha256(box / "go.sum"),
                     "sing_vmess_xudp": sha256(vmess / "xudp.go"),
                     "sing_vmess_go_mod": sha256(vmess / "go.mod"),
                     "test_sha256": sha256(test), "patch_sha256": sha256(patch)}
    expected_graph = None
    for phase in ("baseline", "patched"):
        phase_dir = output / phase
        phase_dir.mkdir()
        box_copy = output / (phase + "-sing-box")
        vmess_copy = output / (phase + "-sing-vmess")
        shutil.copytree(box, box_copy, ignore=shutil.ignore_patterns(".git"))
        shutil.copytree(vmess, vmess_copy, ignore=shutil.ignore_patterns(".git"))
        shutil.copyfile(test, vmess_copy / test.name)
        command([Path(go).with_name("gofmt"), "-w", vmess_copy / test.name], vmess_copy, env)
        if phase == "patched":
            command(["git", "apply", "--check", patch], vmess_copy, env)
            command(["git", "apply", patch], vmess_copy, env)
        # Both controls use the exact same local dependency source identity.
        # Only the patched copy changes xudp.go; no module version is upgraded.
        command([go, "mod", "edit", "-replace=github.com/sagernet/sing-vmess=" + str(vmess_copy)], box_copy, env)
        binary_version = "1.14.2-vui-xudp-" + phase + "-experiment-" + commit[:12]
        flags = "-X github.com/sagernet/sing-box/constant.Version=" + binary_version
        command([go, "build", "-trimpath", "-buildvcs=false", "-ldflags=" + flags,
                 "-o", phase_dir / "sing-box", "./cmd/sing-box"], box_copy, env)
        actual = command([phase_dir / "sing-box", "version"], root, env, capture=True).stdout
        if not actual.startswith("sing-box version " + binary_version + "\n"):
            raise ValueError("Experimental build label is absent")
        command([go, "mod", "verify"], box_copy, env)
        if sha256(box_copy / "go.sum") != source_hashes["sing_box_go_sum"]:
            raise ValueError("Experimental build changed the fixed core go.sum")
        module_graph = command([go, "list", "-m", "-json", "all"], box_copy, env, capture=True).stdout
        (evidence / (phase + "-module-graph.jsonl")).write_text(module_graph)
        normalized_graph = verify_module_graph(module_graph, vmess_copy)
        if expected_graph is not None and normalized_graph != expected_graph:
            raise ValueError("Baseline and patched selected dependency graphs differ")
        expected_graph = normalized_graph
        (evidence / (phase + "-verified-module-graph.json")).write_text(json.dumps(normalized_graph, indent=2) + "\n")
        # Test under the core's actual module graph, not sing-vmess's older
        # standalone development graph: the packet adapter must be the pin.
        unit = subprocess.run([go, "test", "-json", "-count=1", "-run", "^TestExperimentXUDP",
                               "github.com/sagernet/sing-vmess"],
                              cwd=box_copy, env=env, text=True, capture_output=True, timeout=300)
        (evidence / (phase + "-go-tests.jsonl")).write_text(unit.stdout)
        (evidence / (phase + "-go-tests.stderr")).write_text(unit.stderr)
        # The independent verifier checks named RED/GREEN assertions, not just
        # a nonzero exit. Keep both controls even when a later gate fails.
        (evidence / (phase + "-go-test-exit.json")).write_text(json.dumps({"returncode": unit.returncode}))
        if sha256(box_copy / "go.sum") != source_hashes["sing_box_go_sum"]:
            raise ValueError("Experimental unit tests changed the fixed core go.sum")
        provenance = {"schema_version": 1, "kind": KIND, "phase": phase,
                      "vui_source_commit": commit, "version": binary_version,
                      "binary_sha256": sha256(phase_dir / "sing-box"),
                      "sing_box": {"version": "1.14.2", "commit": SING_BOX},
                      "sing_vmess": {"version": "0.2.8", "commit": SING_VMESS},
                      "build": {"go_version": GO_VERSION, "goos": "linux", "goarch": "amd64",
                                "tags": [], "cgo_enabled": "0", "ldflags": flags,
                                "go_binary_sha256": sha256(go), "gotoolchain": "local", "gowork": "off",
                                "trimpath": True, "buildvcs": False},
                      "patch": {"applied": phase == "patched",
                                "sha256": sha256(patch) if phase == "patched" else None,
                                "path": str(patch) if phase == "patched" else None},
                      "source_hashes": source_hashes, "version_output": actual,
                      "phase_source_hashes": {"go_mod": sha256(box_copy / "go.mod"),
                                              "go_sum": sha256(box_copy / "go.sum"),
                                              "xudp": sha256(vmess_copy / "xudp.go"),
                                              "formatted_test": sha256(vmess_copy / test.name)},
                      "go_test_returncode": unit.returncode}
        (phase_dir / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
        shutil.copyfile(phase_dir / "provenance.json", evidence / (phase + "-provenance.json"))
    if sha256(output / "baseline/sing-box") == sha256(output / "patched/sing-box"):
        raise ValueError("Experimental controls unexpectedly have identical binaries")
    print("Source experiment built; no release or installer paths changed.", flush=True)


if __name__ == "__main__":
    main()
