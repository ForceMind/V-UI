# Packet receive experiment run ledger

## 2026-10-18 receive-only first candidate

Exact public commit `15a4e49a31dbe61348a0bc174eaebec620605c7b`, tree
`6c2659ae6b94ba4f0a4116cb02e84ee48c7de033`.
[Run 39125198825, attempt 1](https://github.com/ForceMind/V-UI/actions/runs/39125198825)
completed **failure**: mux mechanism and packet receive mechanism succeeded;
the unchanged official-device/overseas runtime matrix failed.

- Receive Linux: all 58 fixed leaves passed with race, and all 58 with race+debug.
  Darwin/Windows amd64 each compiled three package test binaries; these are
  compile results, not runtime qualification.
- Default 64-slot batch recorded logical peak 4,218,688 and final live zero.
  Its allocator backing is 64 × 73,728 = 4,718,592 bytes; this is not total RSS.
- Old mux: race/debug each 20 terminals; actual route controls preserve RED
  2/1 before recovery and candidate GREEN 0; normal encrypted library flow
  has two terminals in each of four groups. New encrypted cancellation was
  not in this commit and did not execute.
- Original runtime: both arms preserve 19/20 RED; four VMess device cases
  (two arms × two ciphers) still fail the unchanged large-packet assertion.
  Their 32768-byte request is truncated to a 15992-byte prefix at the target;
  60000 is not reached. Existing six lifecycle cases and shared TCP pass.
  The packet-patched candidate is not used in this old runtime matrix.
- Independent review verified exact source/test/compiler/module identities and
  all archive members, sizes and hashes. No product qualification is granted.

| Artifact | Members | ZIP bytes | ZIP SHA-256 |
|---|---:|---:|---|
| 12135902528 packet | 101 | 76981 | ea1bfbf872cce6c06c08ae8b810ebe111e020a8535650e02f098055c860a07ef |
| 12136275324 mux | 42 | 97618 | 15ef7644b4fa9102a0f525add8e91cc0d39075c6e19fabf061e6f861327a50ff |
| 12135757203 original runtime | 277 | 400094 | dc245f1d807925e161e515bcff74ca28c5f32a97945b7eb5c75a2772b77ba3d7 |

## Next exact candidate: prepared, not yet run

The receive/source patches remain byte-identical. Add real fixed Client/Service
ciphertext partial-write cancellation, and rerun those tests plus all existing
mux/route mechanisms with the patched sing dependency. Add separately recorded
stock-role boundary diagnostics and all-experimental-role IPv4 packet capacity
and endpoint lifecycle stages. See [runtime contract](RUNTIME_PLAN.md).

The new coordinator halts after incomplete diagnostic evidence, failed capacity
in C/D, timeout, or process-group cleanup failure. Later stages stay explicitly
unexecuted. A/B retain their measured capacity RED even when diagnostic gates
allow progression. Linux actual runtime and new encrypted tests remain pending
until a new exact-head run. Encrypted library-callers do not qualify an actual
ConnectionManager-plus-encrypted partial-write combination. Product UDP stays off.

## 2026-10-18 first encrypted-cancellation candidate

Exact commit `e9d22fc4627337f39f982d7405430ee8845b1c99`, tree
`b2cac45e73af53229e6aa56b705507349dc118f1`.
[Run 39126400709, attempt 1](https://github.com/ForceMind/V-UI/actions/runs/39126400709)
failed in the mux mechanism job. Both dependent runtime/packet jobs were skipped.
Artifact `12135963826`: 48 members, ZIP 112358 bytes, SHA-256
`7492f5f128b092b9bc251dd4c22793582a91ae866ae04a0205ab5ba246fb38fe`.
Independent member/size/hash checks match.

New race tests actually ran: eight leaves passed; both cipher
`ParentTransportError` leaves failed because the helper incorrectly required
`net.ErrClosed` when the real pipe returned `io.ErrClosedPipe`. No DATA RACE was
reported. Debug ran all ten leaves successfully. Existing route/unit/normal
cipher checks passed. The strict parser also rejected a real long stack event
split into multiple Go `Output` records; this is a verifier framing defect.
These failures remain failures and do not qualify the new encrypted path.

The next correction is test/verifier-only: preserve the real closed-pipe error
class for parent transport failure, retain strict `net.ErrClosed` for local
association cancellation, record every failed caller, and assemble complete
output lines only within the same Go package/test identity. Missing fragments,
wrong identities and incomplete terminals still fail. No core patch, payload,
capacity, timing or original runtime assertion changes in this correction.
A–D and patched-dependency combined checks have not yet run.

## 2026-10-18 corrected encrypted mechanism, runtime evidence obstruction

Exact commit `7d76372eb573fe4e1b0f2923801571f8aac4446a`, tree
`11e4018a08ec142d14a4b8aa950424d670f228f7`.
[Run 39126841416, attempt 1](https://github.com/ForceMind/V-UI/actions/runs/39126841416)
completed **failure**. The mux mechanism succeeded and its strict verifier and
archive were independently checked: 2 top-level/10 encrypted-cancel leaves each
in race and debug, plus all old unit/route/normal encrypted checks.
Artifact `12136387209`: 48 members, ZIP 112479 bytes, SHA-256
`d23eb7f635d1dd1fd22105a53a9f369f35db7d41ed300e4ef90150cd9eb9245a`.

The packet build/strict combined-check step succeeded according to GitHub job
117673905656, but the runtime command exited 1 and artifact preparation also
failed. No packet archive was uploaded. Thus patched-dependency leaf-level
qualification and the A–D failure cause cannot be independently established
from that run. Do not call this a measured C/zero-packet failure. The old
runtime job also failed; archive `12136332789` has 277 members, ZIP 400762
bytes and SHA-256 `f97a48e300393c2b414943e2638046805faa777508721b0ae863a69fb1b2424f`.
Member/size/hash checks match; both original 19/20 and the four official-device
large-packet failures remain, with no new packet role qualification.

The available job log shows the evidence-copy gate misclassified JSON syntax:
its whole-text Python byte-literal regex treats a Go test name ending `/b`
followed by a JSON quote as byte text. The problem reproduces on the retained
mechanism JSONL. The next test-only change validates JSON/known Go JSONL by
parsed values, rejects duplicate keys, NUL and key/certificate material, and
keeps the path allowlist and raw bytes unchanged. Stage failures will also emit
a sanitized diagnostic summary to the job log. No core patch or runtime
assertion changes. This is a repaired evidence path, not a waived runtime gate.
