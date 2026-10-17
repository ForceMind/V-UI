"""Immutable identities shared by the two-arm runtime builder and runner."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
ARMS = ("route-only", "mux")
KIND = "vui-xudp-shared-mux-runtime-experiment"
SCHEMA = 1


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


life = load("mux_runtime_lifecycle", ROOT / "experiments/xudp_address/lifecycle_runtime.py")
base, observer = life.base, life.observer
builder = load("mux_runtime_builder", ROOT / "experiments/xudp_address/build_experiment.py")
need, sha256, read_json = base.require, base.sha256, base.read_json
REUSED = (
    "experiments/xudp_address/build_experiment.py",
    "experiments/xudp_address/run_experiment.py",
    "experiments/xudp_address/lifecycle_runtime.py",
    "experiments/xudp_address/observe_runtime.py",
)
PATCHES = (
    "experiments/xudp_address/remoteaddr.patch",
    "experiments/xudp_address/lifecycle.patch",
    "experiments/xudp_mux/mux.patch",
    "experiments/xudp_mux/route_cancel.patch",
)
SOURCE_FILES = ("sing-box/route/conn.go", "sing-vmess/xudp.go", "sing-vmess/mux.go", "sing-vmess/mux_writer.go")


def digest(value, length=64):
    return isinstance(value, str) and re.fullmatch("[0-9a-f]{%d}" % length, value) is not None


def version(arm, commit):
    need(arm in ARMS and digest(commit, 40), "Invalid runtime arm or commit")
    return "1.14.2-vui-xudp-shared-" + arm + "-experiment-" + commit[:12]


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, timeout=30)


def source_identity():
    """Refuse uncommitted test/patch code; no HEAD label on dirty working bytes."""
    commit = git("rev-parse", "HEAD").decode().strip()
    tree = git("rev-parse", "HEAD^{tree}").decode().strip()
    need(digest(commit, 40) and digest(tree, 40), "Missing exact repository identity")
    need(not git("diff", "--name-only", "HEAD", "--").strip(), "Tracked checkout differs from exact HEAD")
    files = sorted(set(base.FROZEN_FILES) | set(REUSED) | set(PATCHES) |
                   {str(path.relative_to(ROOT)) for path in HERE.glob("*.py")})
    hashes = {}
    for name in files:
        path = ROOT / name
        need(path.is_file() and not path.is_symlink(), "Missing/linked experiment source: " + name)
        committed = git("show", "HEAD:" + name)
        need(path.read_bytes() == committed, "Runtime source is not committed at HEAD: " + name)
        hashes[name] = sha256(path)
    need({name: hashes[name] for name in base.FROZEN_FILES} == base.FROZEN_FILES,
         "Original gate, helper, policy or product pin changed")
    return {"vui_commit": commit, "vui_tree": tree, "files": hashes}


def expected_patches(arm, identity):
    need(arm in ARMS, "Unknown arm")
    return [{"path": name, "sha256": identity["files"][name]}
            for name in PATCHES[:2 if arm == "route-only" else 4]]


def validate_provenance(data, arm, identity):
    need(isinstance(data, dict), "Runtime provenance must be an object")
    need(type(data.get("schema_version")) is int and data["schema_version"] == SCHEMA and
         data.get("kind") == KIND and data.get("arm") == arm, "Wrong runtime schema, kind or arm")
    need(data.get("source_identity") == identity, "Runtime source identity differs from committed checkout")
    need(data.get("vui_source_commit") == identity["vui_commit"], "Wrong source commit")
    label = version(arm, identity["vui_commit"])
    need(data.get("version") == label, "Wrong unique experimental version")
    need(data.get("patches") == expected_patches(arm, identity), "Changed or reordered patch chain")
    need(data.get("product_qualified") is False, "Runtime provenance cannot qualify product")
    need(digest(data.get("binary_sha256")) and data["binary_sha256"] not in base.STOCK_HASHES.values(),
         "Invalid or official candidate hash")
    need(data.get("fixed_sources") == {"sing_box": builder.SING_BOX, "sing_vmess": builder.SING_VMESS},
         "Wrong immutable upstream sources")
    build = data.get("build", {})
    expected = {"go_version": builder.GO_VERSION, "goos": "linux", "goarch": "amd64", "tags": [],
                "cgo_enabled": "0", "gotoolchain": "local", "gowork": "off", "trimpath": True,
                "buildvcs": False, "ldflags": "-X github.com/sagernet/sing-box/constant.Version=" + label}
    need(all(build.get(key) == value for key, value in expected.items()), "Changed build flags/toolchain")
    need(digest(build.get("go_binary_sha256")), "Missing compiler hash")
    need(digest(data.get("module_graph_sha256")), "Missing fixed dependency graph digest")
    sources = data.get("source_files", {})
    need(set(sources) == set(SOURCE_FILES), "Incomplete source-file identities")
    for name, pair in sources.items():
        need(set(pair) == {"before", "after"}, "Malformed source identity")
        need((pair["before"] is None if name.endswith("mux_writer.go") else digest(pair["before"])),
             "Missing original source hash")
        need((pair["after"] is None if arm == "route-only" and name.endswith("mux_writer.go") else digest(pair["after"])),
             "Missing arm source hash")
    need(sources["sing-vmess/xudp.go"] == {"before": life.SOURCE_FILE_HASHES["sing_vmess_xudp"],
                                         "after": life.PATCH_IDENTITIES["remoteaddr"]["after"]},
         "Address delta differs from frozen source experiment")
    need(sources["sing-box/route/conn.go"]["before"] == builder.ROUTE_SOURCE_SHA256,
         "Route baseline changed")
    if arm == "route-only":
        need(sources["sing-box/route/conn.go"]["after"] == builder.ROUTE_PATCHED_SHA256 and
             sources["sing-vmess/mux.go"]["before"] == sources["sing-vmess/mux.go"]["after"],
             "Route-only control contains a mux delta")
    else:
        need(sources["sing-box/route/conn.go"]["after"] != builder.ROUTE_PATCHED_SHA256 and
             sources["sing-vmess/mux.go"]["before"] != sources["sing-vmess/mux.go"]["after"],
             "Mux arm is missing its deltas")
    return label


def verify_candidate(path, data, arm, identity, stock):
    label = validate_provenance(data, arm, identity)
    path = Path(path).resolve(strict=True)
    need(path.is_file() and os.access(path, os.X_OK), "Candidate is not executable")
    need(not any(path.samefile(item["path"]) for item in stock.values()), "Candidate aliases official peer/device")
    need(sha256(path) == data["binary_sha256"], "Candidate binary hash mismatch")
    result = subprocess.run([str(path), "version"], text=True, capture_output=True, timeout=15)
    output = result.stdout + result.stderr
    need(result.returncode == 0 and output.splitlines() and output.splitlines()[0] == "sing-box version " + label,
         "Candidate does not report the exact experimental version")
    return {"path": str(path), "sha256": sha256(path), "version_output": output}


def graph_digest(graph):
    return hashlib.sha256(json.dumps(graph, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
