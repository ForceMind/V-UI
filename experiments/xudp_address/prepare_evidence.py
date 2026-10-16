"""Allowlist and sanitize experiment evidence before any external upload."""
import argparse
import hashlib
import json
from pathlib import Path
import re

from run_experiment import expected_cases

FIXTURE_IDS = ("11111111-1111-4111-8111-111111111111",
               "22222222-2222-4222-8222-222222222222",
               "33333333-3333-4333-8333-333333333333")
SECRET = re.compile(r"-----BEGIN [^-\r\n]*PRIVATE KEY-----|\bgh[pousr]_[A-Za-z0-9_]{20,}|"
                    r"\bgithub_pat_[A-Za-z0-9_]{20,}|\bAKIA[0-9A-Z]{16}\b")
MAX_FILE = 4 * 1024 * 1024
MAX_TOTAL = 24 * 1024 * 1024


def digest(data):
    return hashlib.sha256(data).hexdigest()


def allowed_names():
    logs = {"build.log", "baseline.log", "patched.log", "observations.log"}
    for phase in ("baseline", "patched"):
        logs.update(phase + "/" + name for name in ("result.json", "process-binaries.json", "suite.log", "runner-error.log"))
        logs.update(phase + "/cases/" + name + suffix for name in expected_cases(phase) for suffix in (".json", ".log"))
        logs.update(phase + "/delivery-ledgers/" + name + ".json" for name in expected_cases(phase))
    logs.update("observations/" + name for name in
                ("result.json", "process-binaries.json", "suite.log", "runner-error.log",
                 "observations/domain-freeze.json", "observations/idle-sockets.json"))
    for name in ("observe_domain_freeze", "observe_idle_sockets"):
        logs.update("observations/cases/" + name + suffix for suffix in (".json", ".log"))
        logs.add("observations/delivery-ledgers/" + name + ".json")
    source = {"go-control-verification.json", "timeout-observation-verification.json"}
    for phase in ("baseline", "patched"):
        source.update(phase + suffix for suffix in ("-go-tests.jsonl", "-go-tests.stderr", "-go-test-exit.json",
                      "-module-graph.jsonl", "-verified-module-graph.json", "-provenance.json",
                      "-timeout-observations.jsonl", "-timeout-observations.stderr", "-timeout-observation-exit.json"))
    return {"logs": logs, "source": source,
            "official": {"cores/provenance.json", "client/mihomo-provenance.json"}}


def stage(logs, source, official, output):
    output = Path(output)
    if output.exists():
        raise ValueError("Evidence output already exists; preserve the prior attempt")
    allowed, prepared, manifest = allowed_names(), [], []
    total = 0
    for category, root in (("logs", logs), ("source", source), ("official", official)):
        root = Path(root)
        if not root.exists():
            continue
        if root.is_symlink():
            raise ValueError("Evidence root is a symlink")
        # Official directories contain the known binaries too: do not scan or
        # stage them. Their two explicitly named public provenance files only.
        paths = [root / name for name in sorted(allowed[category])] if category == "official" else sorted(root.rglob("*"))
        for path in paths:
            if path.is_symlink():
                raise ValueError("Evidence symlink is not allowed")
            if not path.exists() or path.is_dir():
                continue
            relative = path.relative_to(root).as_posix()
            if relative not in allowed[category] or not path.is_file():
                raise ValueError("Unlisted evidence file: " + category + "/" + relative)
            size = path.stat().st_size
            total += size
            if size > MAX_FILE or total > MAX_TOTAL:
                raise ValueError("Evidence resource limit exceeded")
            raw = path.read_bytes()
            if len(raw) != size or b"\x00" in raw or raw.startswith((b"\x7fELF", b"MZ", b"PK\x03\x04", b"\x1f\x8b")):
                raise ValueError("Binary or changing content in text evidence")
            text = raw.decode("utf-8-sig", errors="strict")
            if SECRET.search(text):
                raise ValueError("Potential credential material in evidence: " + category + "/" + relative)
            redactions = []
            for number, value in enumerate(FIXTURE_IDS):
                if value in text:
                    redactions.append("fixed-fixture-id-" + str(number + 1))
                    text = text.replace(value, "<fixture>")
            if re.search(r"/tmp/vui-d2-[A-Za-z0-9_-]+", text):
                redactions.append("ephemeral-fixture-path")
                text = re.sub(r"/tmp/vui-d2-[A-Za-z0-9_-]+", "<fixture-directory>", text)
            if path.suffix == ".json":
                json.loads(text)
            elif path.suffix == ".jsonl":
                # Go module graph is concatenated pretty JSON, not one object per line.
                remaining = text.strip()
                while remaining:
                    _, end = json.JSONDecoder().raw_decode(remaining)
                    remaining = remaining[end:].lstrip()
            data = text.encode("utf-8")
            name = category + "/" + relative
            prepared.append((name, data))
            manifest.append({"path": name, "original_bytes": size, "original_sha256": digest(raw),
                             "staged_bytes": len(data), "staged_sha256": digest(data), "redactions": redactions})
    # Validate every file before creating anything uploadable. On rejection no
    # partially populated staging directory can be accidentally published.
    output.mkdir(parents=True, exist_ok=False)
    for name, data in prepared:
        path = output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    (output / "EVIDENCE_MANIFEST.json").write_text(json.dumps({
        "schema_version": 1, "scope": "Source experiment evidence only; no binaries, configs or keys",
        "file_count": len(manifest), "original_total_bytes": total, "files": manifest}, indent=2) + "\n")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("logs", "source", "official", "output"):
        parser.add_argument("--" + name, required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps({"staged_text_files": len(stage(args.logs, args.source, args.official, args.output))}))
