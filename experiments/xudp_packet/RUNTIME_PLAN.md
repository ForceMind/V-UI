# Isolated packet receive runtime plan

Baseline: V-UI `95aef3791ba0fcf63c23d0f0e4fb60935ea6c9fc`. Read-only baseline;
new scripts will need review and an exact committed identity before labelled CI.
No Go compiler or real core runtime was available during preparation.

## Frozen boundaries

- Original two-arm 20-method assertions and their exact 19/20 RED remain unchanged.
  A diagnostic result is never `original_suite_passed` or `product_qualified`.
- Existing verified mux domestic source is kept separate from the fixed-sing
  packet-receive experiment. Device and overseas binary roles must say whether
  each is official or experimental. Runner-local binaries are never uploaded.
- Only IPv4 SOCKS UDP, VLESS/XUDP and explicit VMess AES-128-GCM / ChaCha20-Poly1305
  under verified TLS are in this experiment. No product pin changes, UDP enabling,
  release, production deployment or upstream communication.

## Independent bounded stages (separate capacity and execution-safety gates)

A. Locate the stock device ingress boundary, with patched overseas and patched
mux domestic so an unrelated stock overseas downlink cap cannot hide the request.
For each VMess cipher use one warmed association: warm 1, then 15991/15992/15993,
then short recovery 7. VLESS analog: 1,16105/16106/16107,7. New first-packet
associations individually send the three relevant boundary values, plus
16373/16374/16375. Each has a 7-byte recovery packet. This is 21 cases and 51
business packets, not a cross-product of every size and lifecycle action.
The 15992 warmed VMess prefix is an observed baseline and must reproduce.
At exact 011184d, warmed VLESS 16107 became a 16106-byte prefix; first 16373/16374
reached the target completely, and 16375 became a 16374-byte prefix in all
three protocols. All nine large first-packet echoes failed on return: stock
device logged `packet download closed: short buffer`. Stage A remains RED.
The original plan and strict full-reply/recovery assertions remain unchanged;
the next runner records failure terminals and actually attempts the original
same-association recovery rather than stopping after the first failed leaf.
No timeout is rewritten as prefix delivery or explicit rejection. Stage A remains capacity RED,
even when its diagnostic observations are complete. First-packet observations
must not silently become accepted production limits.

U. Separate stock-device first-packet **uplink** observations: 16373/16374/16375
for each protocol, with an independent deterministic 32-byte ACK rather than
echo, then a real 7-byte same-association recovery. Nine cases/eighteen leaves.
The short ACK isolates input from the stock device's smaller download buffer;
it does not replace or repair any failed A echo. Target request and ACK hashes
are independently verified. Actual prefixes remain capacity failures.

V. Separate stock-device **downlink** observations: for each protocol, fresh and
warmed associations send a 32-byte request for a 16121/16122/16123-byte target
response, then same-association recovery. Eighteen cases/forty-five leaves.
16122 is a source-budget prediction, not a pre-accepted limit. Missing replies,
explicit short-buffer failures and failed recoveries remain failures. A/U/V
record each single send, separate four-second target and reply windows, new
device-log intervals bound to the launched PID/birth, and real zero-delivery
windows. They never synthesize an actual target row or an empty reply.

B. Isolate stock overseas receive behavior with patched device and patched mux
domestic. For each protocol, 32-byte non-echo requests ask the independent target
for 8192,32768,60000-byte responses, followed by a 7-byte recovery. Record both
complete target `sendto` return length and actual device reply. This is 3 cases,
12 packets. The two large responses must exhibit a concrete shorter prefix or
other explicit rejection; timeout alone is not rejection or truncation proof.
Stock large-response capacity remains RED. No response must be sourced from a
local/direct bypass. Device-stock and overseas-stock faults are separate.

C. Paired repaired endpoint capacity. For each protocol, test echo uplink and
non-echo downlink independently on fresh and warmed associations for payloads
0/1/8192/32768/60000/65497. First-packet means there was no prior UDP request on that
SOCKS association; a TCP/SOCKS handshake alone is not a UDP warmup. Each full-size
probe is followed by 7 bytes on the same association. Each protocol adds the
same-association sequence 65497,7,0,7, proving full/short/empty/recovery. Empty
uplink must deliver exactly one empty target datagram; empty downlink must
actually return an empty SOCKS payload. Do not turn a missing empty reply into
an expected skip. Each protocol also sends a 65498-byte application datagram
once: its 10-byte IPv4 SOCKS wrapper is 65508 bytes, so Linux AF_INET SOCK_DGRAM
must return local EMSGSIZE, no target packet may arrive, and a subsequent
7-byte packet must work. This proves local OS refusal only, not patched receive
MSG_TRUNC/MTU rejection. Use one fresh attempt per leaf, no resend. This is 78
cases, 195 business packets plus 3 failing local send attempts (198 fixed leaves).
This stage may run independently after earlier capacity failures only when the
separate execution-safety verifier proves their complete resource cleanup and
identity. Earlier failed gates stay failed. All three roles use the fixed-sing
packet candidate, retaining the previously verified mux/route source bytes.

D. Additional repaired endpoint lifecycle smoke. For each of the three
protocols, send 60000 bytes, then either explicitly close the SOCKS association
or leave it idle, require the exact identified domestic forwarding TCP,
overseas accepted TCP and overseas UDP socket inodes to disappear within six
seconds, and recover with 32768 bytes. Close uses a fresh SOCKS association;
idle uses the still-open association. Six cases and twelve business packets.
This SOCKS-facing addition does not observe raw XUDP Session End. The old
six-case lifecycle suite, including shared-parent sibling cancellation and
three domestic/device restart cycles, remains an independent unchanged gate.
A new large-packet shared-parent/restart stress expansion is not implemented
here and is not claimed by D.

Each stage has a twenty-minute between-case execution budget. If it expires,
remaining leaves stay unexecuted and the stage fails; the budget never shrinks
the planned leaf set or changes an assertion. Process startup/stop calls and
packet windows also retain their existing finite bounds. Stage C currently
uses independent fixtures per case for strict failure and cleanup isolation;
real runner timing has not yet been measured.

## Size accounting

- IPv4 UDP payload socket maximum: 65507 bytes (normal 20-byte IPv4 header).
- IPv4 SOCKS UDP wrapper: 10 bytes, hence application maximum 65497.
- A 65535-byte mux length field does not make a 65535-byte UDP datagram legal.
- Domain SOCKS overhead is 7 + ASCII hostname bytes; IPv6 SOCKS overhead is 22.
  Unit-test budget arithmetic and header encode/decode only. DNS/domain and
  IPv6 real-traffic qualification are excluded here. IPv6 normal UDP payload
  maximum is 65527 without extension headers; jumbograms are excluded.

## Evidence and anti-false-green rules

The expected stage case IDs and packet IDs are generated by a frozen plan,
not discovered from output. Exact non-empty Counter equality is required for
both. Missing, duplicate, skipped, unexpected, unlaunched or unjoined cases fail.
Every payload uses counter-mode SHA-256 blocks so it is nonuniform and does not
repeat a 32-byte block. Each row records full expected and actual hashes/lengths,
prefix identity, source IP and port, requested host and port, SOCKS relay tuple,
application socket tuple, direction and first/warmed state. The independent
target receives via recvmsg(65535) and rejects MSG_TRUNC/MSG_CTRUNC in the recorder.
Target send returns, DNS traps, DIRECT traps, PID births/exits, binary hashes,
configuration digests and every socket/worker close are retained.

Manifest provenance must bind exact repository commit/tree, frozen fixture
hashes, all new runtime source hashes, fixed upstream commits, ordered patch
identities, compiler digest/flags, normalized dependency graph and binary
version/hash. A supplied manifest is not an excuse to trust arbitrary binaries:
compare actual file hashes and version command output and require the expected
role-to-build map. Byte-identical role reuse is allowed only when explicitly the
same experimental build; it must not claim separate official provenance.

A/B record capacity failures as failures while permitting a separate
`diagnostic_complete` summary. C/D require all packet semantics, runtime cleanup
and manifests. No stage can override original gate RED. A strict leaf artifact
allowlist plus content hashes excludes binaries, source trees, credentials,
config files, certificates, private keys and caches from any evidence package.

## Runtime/API and preparation status

The runner supplies A/B/C, the D endpoint smoke, and separate U/V observations. It imports the original
fixture without patching any test method or assertion. Preparation has run only
29 matrix-only pure Python controls at its initial preparation point. Actual
later runs and their failures are recorded chronologically in RUN_LEDGER.md.

Copy reviewed files to `experiments/xudp_packet/` in the isolated candidate.
`source_identity()` deliberately refuses a standalone preparation path, dirty
checkout, untracked test text, or source bytes unequal to exact HEAD. It matches
the packet builder's complete identity, including .py/.go/.patch/.md/.json.
The parent builder produces runtime-input.json and provenance.json. Runtime
uses candidate.provenance_path and verifies its SHA-256, exact binary versions,
source commit/tree, ordered five-patch chain, compiler flags/digest and fixed
module graph identity. Runtime never builds or uploads binaries.

Commands after review, commit and same-head mechanism/build gates:

    export PYTHONDONTWRITEBYTECODE=1
    python3 -m unittest discover -s experiments/xudp_packet -p 'test_*.py' -v
    python3 experiments/xudp_packet/packet_boundary.py --stage A --describe
    python3 experiments/xudp_packet/packet_boundary.py --stage A \
      --repository "$PWD" --binary-manifest /tmp/packet-build/runtime-input.json \
      --output /tmp/packet-runtime-A-attempt1

The reviewed coordinator executes C/D/A/B/U/V in separate fresh directories
under one exact identity. `stage_gate_passed` retains the original strict
capacity/diagnostic result; it is never changed by safe continuation. A separate
`stage_execution_safe` permits the next independent observation only after
rechecking the exact plan, source commit/tree, actual binary hashes, build
provenance, every raw evidence member/hash, complete case/role coverage, launched
PID/birth/live/reaped correspondence, core exit codes zero, target socket/thread
closure, complete target receive/send accounting, empty DNS/DIRECT traps, and
fixture/application cleanup. Failed cases must list actual terminal errors and
all unexecuted leaves. D must retain its terminal lifecycle observation even
on failure. Each stage has an owned process group and a 1250-second outer bound.
Timeout, cleanup signals, group remnants, startup uncertainty, changed source or
binary, incomplete evidence, or a crashed core stop subsequent stages.
An ordinary stage exit 1 caused by failed capacity may safely continue; it stays
exit 1 and failed. `all_stages_executed` is separate from `all_gates_passed`, and
the overall command returns 1 whenever any gate failed. No stage is retried.
Candidate C/D run first to expose repaired-path blockers promptly; this does
not skip any remaining stock diagnostic group or infer its outcome.
The separate failed-observation verifier authenticates A/U/V error facts against
raw target receive/send records, the same application/relay tuple, four-second
observation windows, the exact official device PID/birth, and fresh intervals
of the original frozen device log. Consistent repeated fixture log snapshots
must be prefix-monotonic. Missing observations remain unverified; contradictory
windows/logs are rejected. This verifier never changes an error to success and
never substitutes for either the capacity gate or the resource-safety gate.
A/B exit zero means diagnostic evidence complete while stage_capacity_passed
remains false and concrete capacity_failed_leaves remain present. C/D exit zero
requires their strictly complete cases and cleanup. original_suite_passed and
product_qualified are always false. Zero tests, partial stages, errors, absent
observations or insufficient cleanup cannot pass.

The output manifest enumerates every allowed text artifact with SHA-256. Before
upload, the parent must independently verify the manifest. Failure output is
preserved even when strict manifest assembly fails; do not upload an unchecked
folder as successful evidence. Never include runner-local executables, source
build trees, configs, keys, certificates, caches or unrelated files.
