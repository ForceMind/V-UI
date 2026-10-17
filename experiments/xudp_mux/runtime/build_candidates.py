#!/usr/bin/env python3
"""Build two explicitly labelled runtime candidates; never upload these binaries."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess

import common as c


def manifest(directory):
    result = {}
    for path in sorted(directory.rglob("*")):
        c.need(not path.is_symlink(), "Unexpected symlink in fixed source tree")
        if path.is_file():
            result[str(path.relative_to(directory))] = c.sha256(path)
    return result


def command(args, cwd, env, log):
    result = subprocess.run([str(arg) for arg in args], cwd=cwd, env=env, text=True,
                            capture_output=True, timeout=1200)
    c.life.save(log, {"args": [str(arg) for arg in args], "returncode": result.returncode,
                      "stdout": result.stdout, "stderr": result.stderr})
    c.need(result.returncode == 0, "Build command failed; see " + str(log))
    return result.stdout


def build(args):
    identity = c.source_identity()
    official = {"sing-box": c.builder.fixed_checkout(args.sing_box, c.builder.SING_BOX),
                "sing-vmess": c.builder.fixed_checkout(args.sing_vmess, c.builder.SING_VMESS)}
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    evidence = output / "evidence"
    evidence.mkdir()
    env = {**os.environ, "GOTOOLCHAIN": "local", "GOWORK": "off", "GOENV": "off", "CGO_ENABLED": "0",
           "GOOS": "linux", "GOARCH": "amd64", "GOAMD64": "v1", "GOEXPERIMENT": "", "GOFLAGS": "-mod=readonly",
           "GOPROXY": "https://proxy.golang.org", "GOSUMDB": "sum.golang.org", "GONOSUMDB": "",
           "GONOPROXY": "", "GOPRIVATE": "", "GOMODCACHE": str(output / "modules"), "GOCACHE": str(output / "cache")}
    go = shutil.which("go", path=env.get("PATH"))
    c.need(go is not None, "Existing fixed runner compiler is required")
    compiler = command([go, "version"], c.ROOT, env, evidence / "compiler.json").strip()
    c.need(compiler == "go version " + c.builder.GO_VERSION + " linux/amd64", "Wrong compiler: " + compiler)
    graph = None
    candidates = []
    for arm in c.ARMS:
        directory = output / arm
        directory.mkdir()
        trees = {}
        for family, source in official.items():
            trees[family] = directory / family
            shutil.copytree(source, trees[family], ignore=shutil.ignore_patterns(".git"))
        before = {family + "/" + path: value for family, tree in trees.items() for path, value in manifest(tree).items()}
        for index, change in enumerate(c.expected_patches(arm, identity)):
            target = trees["sing-vmess" if index in (0, 2) else "sing-box"]
            path = c.ROOT / change["path"]
            command(["git", "apply", "--check", path], target, env, evidence / f"{arm}-patch-{index}-check.json")
            command(["git", "apply", path], target, env, evidence / f"{arm}-patch-{index}.json")
        if arm == "mux":
            # Match build_and_test.py's post-patch product-source formatting.
            # Never format the frozen route-only or route source files.
            command([Path(go).with_name("gofmt"), "-w", trees["sing-vmess"] / "mux.go",
                     trees["sing-vmess"] / "mux_writer.go"], trees["sing-vmess"], env,
                    evidence / "mux-gofmt.json")
        after = {family + "/" + path: value for family, tree in trees.items() for path, value in manifest(tree).items()}
        changed = {name for name in before.keys() | after.keys() if before.get(name) != after.get(name)}
        expected = set(c.SOURCE_FILES[:2] if arm == "route-only" else c.SOURCE_FILES)
        c.need(changed == expected, "Patch changed an unexpected upstream source file")
        source_files = {name: {"before": before.get(name), "after": after.get(name)} for name in c.SOURCE_FILES}
        box, vmess = trees["sing-box"], trees["sing-vmess"]
        command([go, "mod", "edit", "-replace=github.com/sagernet/sing-vmess=" + str(vmess)], box, env,
                evidence / f"{arm}-replace.json")
        graph_text = command([go, "list", "-m", "-json", "all"], box, env, evidence / f"{arm}-modules.json")
        normalized = c.builder.verify_module_graph(graph_text, vmess)
        c.need(graph is None or graph == normalized, "Runtime arms selected different dependencies")
        graph = normalized
        label = c.version(arm, identity["vui_commit"])
        flags = "-X github.com/sagernet/sing-box/constant.Version=" + label
        binary = directory / "candidate-sing-box"
        command([go, "build", "-trimpath", "-buildvcs=false", "-ldflags=" + flags,
                 "-o", binary, "./cmd/sing-box"], box, env, evidence / f"{arm}-build.json")
        command([go, "mod", "verify"], box, env, evidence / f"{arm}-module-verify.json")
        final = {family + "/" + path: value for family, tree in trees.items() for path, value in manifest(tree).items()}
        c.need({name for name in final.keys() | after.keys() if final.get(name) != after.get(name)} == {"sing-box/go.mod"},
               "Build changed source or fixed go.sum")
        c.need(c.source_identity() == identity, "Repository changed during candidate build")
        data = {"schema_version": c.SCHEMA, "kind": c.KIND, "arm": arm, "version": label,
                "vui_source_commit": identity["vui_commit"], "source_identity": identity,
                "fixed_sources": {"sing_box": c.builder.SING_BOX, "sing_vmess": c.builder.SING_VMESS},
                "patches": c.expected_patches(arm, identity), "source_files": source_files,
                "binary_sha256": c.sha256(binary), "module_graph_sha256": c.graph_digest(normalized),
                "build": {"go_version": c.builder.GO_VERSION, "goos": "linux", "goarch": "amd64", "tags": [],
                          "cgo_enabled": "0", "gotoolchain": "local", "gowork": "off", "trimpath": True,
                          "buildvcs": False, "go_binary_sha256": c.sha256(go), "ldflags": flags},
                "product_qualified": False}
        c.verify_candidate(binary, data, arm, identity, {})
        c.life.save(directory / "provenance.json", data)
        candidates.append({"arm": arm, "binary": str(binary), "provenance": str(directory / "provenance.json"),
                           "binary_sha256": c.sha256(binary)})
    c.need(len({item["binary_sha256"] for item in candidates}) == 2, "Both arms have the same binary")
    c.life.save(evidence / "candidates.json", {"candidates": candidates, "source_identity": identity,
                                              "product_qualified": False})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sing-box", required=True, type=Path)
    parser.add_argument("--sing-vmess", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    build(parser.parse_args())


if __name__ == "__main__":
    main()
