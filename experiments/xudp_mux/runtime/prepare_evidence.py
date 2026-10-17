#!/usr/bin/env python3
"""Copy only explicit sanitized text evidence; never traverse candidate source/binaries."""
import argparse
from pathlib import Path

import common as c
import run_matrix as runner

BUILD_FILES = frozenset({"compiler.json", "candidates.json", "mux-gofmt.json",
    *(f"{arm}-{kind}.json" for arm in c.ARMS for kind in ("replace", "modules", "build", "module-verify")),
    *(f"{arm}-patch-{index}{suffix}.json" for arm in c.ARMS
      for index in range(2 if arm == "route-only" else 4) for suffix in ("", "-check"))})
RUNTIME_FILES = frozenset({"result.json", "runner-error.log",
    *(arm + "/" + path for arm in c.ARMS for path in runner.ARTIFACTS)})


def prepare(runtime, build, output):
    output.mkdir(parents=True, exist_ok=False)
    entries = []
    for label, directory, allowed in (("runtime", runtime, RUNTIME_FILES), ("build", build, BUILD_FILES)):
        if not directory.exists():
            continue  # Preserve other completed evidence after a failed early step.
        c.need(directory.is_dir() and not directory.is_symlink(), "Linked/non-directory evidence root")
        for path in sorted(directory.rglob("*")):
            c.need(not path.is_symlink(), "Linked evidence is forbidden")
            if not path.is_file():
                continue
            relative = str(path.relative_to(directory))
            c.need(relative in allowed and path.suffix in (".json", ".log"), "Unexpected evidence path: " + relative)
            destination = output / label / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if path.suffix == ".json":
                c.life.save(destination, c.read_json(path))
            else:
                destination.write_text(c.life.clean(path.read_text()))
            c.need(c.life.clean(destination.read_text()) == destination.read_text(), "Unredacted prepared evidence")
            entries.append({"path": str(destination.relative_to(output)), "source_bytes": path.stat().st_size, "sanitized_bytes": destination.stat().st_size,
                            "source_sha256": c.sha256(path),
                            "sanitized_sha256": c.sha256(destination)})
    c.life.save(output / "MANIFEST.json", {"files": entries, "product_qualified": False,
                 "note": "Allowlisted text preservation only; missing, failed and unrun checks are not passing."})
    c.need(entries, "No evidence exists; manifest records an empty attempt")
    return entries


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", required=True, type=Path)
    parser.add_argument("--build", required=True, type=Path, help="Only build_candidates output/evidence")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    prepare(args.runtime, args.build, args.output)


if __name__ == "__main__":
    main()
