# Owner runtime text evidence archive

## Scope and non-qualification

`prepare_runtime_evidence.py` prepares a fresh local **directory of evidence
text**, not an executable bundle. Its input is the owner builder's `evidence`
directory, never its build/source root, plus an optional finite runtime evidence
root. It neither executes Go/cores nor builds, downloads, runs CI, publishes or
uploads anything. Recorded binary/provenance paths are inert metadata: the
archive helper never opens them. Source clones, binaries, raw role configs,
certificates and key material are outside the archive.

The helper preserves byte-identical allowed evidence after failed or incomplete
runs. It does not replace the mechanism verifier, runtime adapter, frozen
business/safety gates or strict owner runtime verifier. A copied success flag is
an unassessed source assertion. Archive success establishes file preservation and
this text-boundary contract only. It cannot grant runtime success, complete
whole-window OS continuity, official-client support or product qualification.
The independent official/off RED diagnoses remain unchanged. No retry,
keepalive, new association, new case or broader runtime role is introduced.

## Authoritative paths and formats

The archive root contains owner build evidence directly; there is no extra
`build/` prefix. Allowed root filenames come from
`prepare_evidence.owner_files(harness.read_contract())`. `base/` and
`regression/` filenames come from `harness.packet_prepare.allowed_build()`.
Neither authoritative function is changed or replicated here.

Runtime evidence is placed under `runtime/`, limited to:

- `off-input.json`, `on-input.json`, `RUNTIME_RESULT.json`
- `off/stages.json` and `on/stages.json`
- `off/{C,D,A,B,U,V}/` and `on/{C,D}/`, using exactly
  `packet_boundary.expected_artifacts(packet_boundary.plan(stage))` plus the
  original filename `manifest.json`

`evidence-manifest.json`, new stages, unknown cases, arbitrary nested folders,
raw process logs outside the original frozen-fixture paths and extra empty
folders are rejected. Only directories derived from the exact allowed paths are
accepted. No new runtime artifacts are added to the frozen packet boundary.

Formats are bound to exact paths:

- Every `.json` member is strict JSON
- JSONL applies only to original `GO_JSONL` filenames below `base/` or
  `regression/`, and owner contract group/mode `.stdout` files at the root
- All remaining allowed paths are UTF-8 text. In particular module graph
  `.stdout`, compiler/formatter output, `.stderr` and `.log` are not reclassified
  as Go JSONL merely because their content resembles JSON

Blank Go JSONL lines are retained. Nonblank lines must be individual objects.
The existing structured sanitizer operates on decoded values, avoiding the
historical false-positive interpretation of a test name ending `/b` followed by
JSON punctuation as a Python byte literal.

## Distinct owner metadata

Owner runtime inputs retain schema version 1 and kind
`vui-socks-owner-source-experiment-input`; they must explicitly identify their
`off` or `on` arm. The exact fields are `schema_version`, `kind`,
`source_identity`, `official`, `candidate`, `arm`, `build_evidence`.

- `official`: `path`, `sha256`, `version`
- `candidate`: those three plus `provenance_path`, `provenance_sha256`
- `build_evidence`: `path`, `files`

The recorded paths remain inert. Basic descriptor shapes and digest syntax are
checked. Source identity must have exact commit/tree digest shapes and all eight
expected patch paths with digest values; the candidate version must use the
owner suffix matching the commit's first 12 characters. Root owner provenance
and stage input-identity build provenance must retain schema/kind and the ordered
eight patch path/hash records matching their source identity. These are
structural checks, without a success/green requirement. Actual source/binary
bytes and execution are assessed only by the separate owner runtime adapter. The old five-patch
`packet_boundary.validate_manifest` is never called. Final owner build
provenance remains the distinct bare
`vui-socks-owner-source-experiment` kind; the archive does not reinterpret it as
a five-patch base provenance.

Runtime root and stage result kinds are
`vui-socks-owner-source-experiment-runtime`, with schema version 1 and
`product_qualified:false`. `RUNTIME_RESULT.json` must preserve
`device_whole_window_continuity_proven:false`. Stage `manifest.json` has exactly
`schema_version`, `kind`, `files`, with kind
`vui-socks-owner-source-experiment-runtime-evidence`. No success, completeness or
content-hash agreement of these embedded runtime records is inferred from their
structural acceptance.

## Filesystem and content boundary

Preparation validates all source bytes before creating output. Inputs are never
rewritten or automatically redacted. Unsafe input causes rejection, rather than
quietly dropping a file, weakening a verdict or copying a sanitized substitute.

- Roots and every ancestor component are opened with no-follow directory
  descriptors. Symlinks, including linked/dangling roots and directory links,
  are rejected, as are FIFOs, devices and other nonregular files
- Regular files require link count one; inode aliases are checked across both
  input roots. The helper does not follow hardlinks to outside evidence
- Exact member paths must be canonical relative POSIX paths; absolute paths,
  `..`, backslashes, dot aliases and repeated separators are rejected
- File opens are no-follow and nonblocking. File identity, type, link count,
  byte count, size and modification/change timestamps are checked around a
  bounded read, detecting replacement or modification during collection
- Input/output overlap and duplicate input roots are rejected. Output must be a
  fresh directory with an existing, unlinked parent. Its manifest is written
  only after its evidence files
- UTF-8 decoding is strict. Literal/escaped NUL, nonfinite JSON numbers including
  overflow, unpaired Unicode surrogates, duplicate JSON keys at any depth and
  malformed JSON/JSONL are rejected
- Existing fixture-secret and packet-byte sanitization checks remain mandatory.
  PEM material, sensitive JSON keys (including nested and normalized variants),
  credential assignments, authenticated URLs, bearer strings and recognized
  GitHub/OpenAI/AWS credential prefixes are rejected in decoded content

These checks are a defensive boundary, not permission to put credentials into
logs. Unrecognizable arbitrary secrets cannot be proved absent by pattern
matching; the upstream experiment remains restricted to false credentials and
sanitized evidence. Failure evidence must itself satisfy the same boundary.

## Typed owner event lines

Every string/text line containing `socks_udp_owner ` is independently checked.
It must contain one marker, end with a newline, be at most 4,096 UTF-8 bytes and
contain one strict JSON object after the marker. Its exact fields are:

`pid`, `owner_id`, `sequence`, `transition`, `generation`, `packet_sequence`,
`accepted_packets`, `rejected_sources`, `remapped_packets`, `cause`, `source`,
`relay`, `target`.

Counters are actual integers (not booleans), in `[0, 2^64)`. PID, owner ID and
sequence must be positive. Transition/cause values use the fixed owner enums;
endpoints are canonical IPv4/decimal-port strings, with `target:':0'` allowed
only on `create`. These intentionally small structural constants mirror the
owner logger; they do not import the runtime verifier or execute its success
checks.

Partial event sequences are allowed, including a lone retirement/failure event.
The packager does not require a complete lifecycle, compare PID to a launched
process, require contiguous sequences, infer resource joins, or grant continuity.
Those belong to the runtime verifier. This separation preserves failed-run
records without treating incomplete lifecycle evidence as a pass.

## Resource ceiling and missing evidence

Each member is capped at 64 MiB. Aggregate archived bytes, including
`MANIFEST.json`, are capped at 512 MiB. The limits are checked before reading
oversized members and across both build/runtime roots; reads never request more
than the checked source size plus one byte. Verification applies the same
limits. Exceeding a limit rejects the operation; it never truncates evidence.

For scale only, the restored historical `6e` evidence tree measured during this
reconstruction contained 758 on-disk files totaling 9,200,317 bytes including its
root manifest. Its largest member, `runtime/C/result.json`, was 914,847 bytes.
The ceilings are intentionally generous relative to that old evidence. This
measurement is historical and supplies no claim about the new candidate's
runtime result or required future archive size.

Either input root may be omitted or absent after a failure if the other supplies
at least one safe allowed file. An existing linked, dangling, non-directory or
unsafe root is never treated as a missing root. Empty directories alone do not
produce an archive. Missing stages/results and failed checks do not prevent
preserving other valid evidence. A truncated/malformed individual JSON or owner
event is rejected rather than repaired. No source success marker is required.

## Manifest and independent verification

`MANIFEST.json` has exactly:

- `schema_version:1`
- `kind:"vui-socks-owner-source-experiment-text-evidence-archive"`
- `files`: lexicographically path-sorted entries, each with exact `path`,
  `sha256`, `bytes`, `format`
- `count`: exact entry count, excluding the manifest itself
- `allowlist_sha256`: SHA-256 of compact, key-sorted UTF-8 JSON plus a newline,
  containing schema/kind, the full sorted path/format list and its derived
  sorted directory list
- `packaged_only:true`
- `runtime_verdict:"not_assessed"`
- `product_qualified:false`

The archive manifest does not hash itself. `verify_archive` independently walks
an existing archive with the same no-follow/type/content boundary, re-reads all
bytes, reconstructs every entry and the current authoritative allowlist digest,
and compares the complete manifest. Duplicate/unsorted entries, unknown fields,
missing/extra actual files, stale bytes/hashes/formats/counts and altered scope
flags all reject. Existing output is never overwritten. An I/O failure during
writing may leave a partial safe output, but it cannot pass verification.

Example commands from the reviewed checkout:

```sh
python experiments/socks_idle_recovery/prepare_runtime_evidence.py \
  --build /runner/owner-build/evidence \
  --runtime /runner/owner-runtime \
  --output /runner/owner-text-archive
python experiments/socks_idle_recovery/prepare_runtime_evidence.py \
  --verify /runner/owner-text-archive
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover \
  -s experiments/socks_idle_recovery -p test_runtime_evidence.py -v
```

The output parent must exist. Default preparation/verification reads the current
owner `TEST_CASES.json`; the synthetic Python controls deliberately supply a
small fixture contract and never pretend it is real mechanism execution.
