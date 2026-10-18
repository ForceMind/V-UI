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
