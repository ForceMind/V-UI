# WIP: opt-in SOCKS owner recovery experiment

This is an incomplete source-only recovery checkpoint. No owner Go compilation,
race/debug test, runtime matrix, or qualification has completed. Do not deploy,
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
