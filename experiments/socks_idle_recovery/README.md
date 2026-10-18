# Opt-in SOCKS owner mechanism candidate

This is an isolated source experiment. The first reconstructed owner mechanism
run at 398ef8a failed; successful partial Go groups do not grant owner qualification.
The next candidate requires fresh complete mechanism and finite runtime validation. Do not deploy,
release, merge into product code, or infer product UDP support from this branch.

The last verified public baseline is commit
`6e73827babcab15356e2b13f48b0eb3111bb2774`, tree
`a53e0fc95bef8f5087cb274d1e7c85da11b839c1`.
[Its run](https://github.com/ForceMind/V-UI/actions/runs/39134100708)
retains all original failures. The full experimental packet C matrix passed
78 cases / 198 leaves. D still failed same-association idle recovery in three
protocols. Passive observations directly recorded the device control EOF and
absence of its original relay/control descriptors before recovery.

On 2026-10-18 the cloud workspace disconnected and resumed from an older local
snapshot. Unpublished owner work disappeared. The exact public baseline and
its three archived evidence sets have been restored and integrity-checked.
Reconstructed files are new candidates unless their bytes are independently
shown to match the former hashes. Previous static review and pure-Python test
results do not qualify reconstructed files.

The bounded experiment retains the original SOCKS association only on an
explicitly opted-in experimental device. Each genuinely new datagram gets at
most one admission attempt. A private route child must fully join before another
generation starts. No replay, keepalive, shared-parent closure, silent target
change, or direct fallback is permitted. Default product pins and UDP gates stay
unchanged. Only source, tests, and documents are stored here; binaries remain
runner-local.

Recovery checkpoints use a separate WIP branch. Once the complete source,
strict evidence verifier, tests and independent review are ready, an exact
candidate will run the full mechanism and bounded runtime gates. Incomplete
checkpoints are not acceptance results.

## Recovery checkpoint 2

All three source layers are present for WIP backup. The VMess patch and its
three tests match their former hashes. The sing/box owner implementations and
design are reconstructed new bytes; independent source review is in progress,
including fail-closed metadata, outbound identity and nil-lease checks. No
reconstructed owner test result is inherited. The complete test manifest,
Python controls and workflow are not wired at this checkpoint.

Recovered checker controls now pass 47 Python tests. The unchanged address,
mux, packet and original runtime control suites separately pass 122, 38, 179
and 26 tests. These are pure harness checks, not Go or core runtime results.
The sing reconstruction restores strict IPv4 association hints and the original
neutral dormant event cause. Its source remains a newly reviewed candidate.

## First reconstructed mechanism candidate

The strict manifest selects 807 Go leaves in eight groups, each in race and
race+debug modes, plus 22 Darwin/Windows compile-only checks. It runs the
unchanged 23 packet/mux checks both on the five-patch base and on the complete
eight-patch dependency tree. Base and final source identities are distinct.

Of the 648 box leaves, 600 use real TLS/concrete outbound/Service/ConnectionManager
with a fresh mock lifecycle child for each cycle. They do not prove one real
SOCKS owner surviving 200 generations. Separate real-owner two-generation tests
verify actual route metadata, server packet target and unique payloads before
actual Router rejection. The initial target remap and no-replay boundary remain.

At that first candidate, pending drafts were excluded from injection and execution.
The subsequent candidate replaces those drafts with reviewed constructor, late
lease, Admitted-deadline, power-counter and finite owner-off/on gates. See
RUNTIME_PREP.md and the run ledger. Partial mechanism success never grants
product or transparent-recovery qualification.

## Next complete candidate

The follow-on candidate removes the box import cycle without changing its exact
ordinary-Go-TLS policy and moves one actual dial test to the route package.
The VMess corruption negative now authenticates the original wire record, mutates
one payload-ciphertext byte at offset 18, and requires the real client's precise
AEAD authentication error. Random trailing padding is left unchanged. Neither
fix revises the first-run failure ledger.

The strict selection now has 16 injected test files and 837 unique Go leaves in
nine groups. The original route group has 632 leaves after the one-test package
move; its exact top-level regex is disjoint from the new 30-leaf constructor group.
Each group retains its own 180-second timeout, race and race+debug runs. The new
30 leaves include 48.6 seconds of deliberate timer windows before I/O overhead;
this is a source budget, not an observed runtime bound.

The final eight-patch mechanism must pass before the finite runtime starts.
Owner-off runs the original six stages and preserves their exact success/RED
classes with independent raw-evidence checkers. Owner-on runs the unchanged
C 78-case/198-leaf matrix and D six-case same-association lifecycle plan. All
new controls and selected evidence are strict; the 159 Python checks are harness
results only. Original stock failures and product qualification remain separate.
