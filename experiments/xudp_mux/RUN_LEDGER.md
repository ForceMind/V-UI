# Shared mux experiment run ledger

This isolated experiment does not qualify product UDP. Preserve each failed
attempt; source changes require a new exact-head run rather than retrying for green.

## 2026-10-17 first mechanism run: compile failure

- Commit: `f009350f7c8ad554b8ed86fbc8131bcdadd9bb2f`
- Tree: `83d5456b4017e89b6a7999ddc7dd2dd4d1c110bb`
- [Run 39006786929, attempt 1](https://github.com/ForceMind/V-UI/actions/runs/39006786929): failure.
- Fixed Go 1.25.14 and all 12 Python controls passed.
- Both actual-route `route-only` backpressure controls passed and reproduced
  the expected retained route copy workers (reply: 2; StatusEnd: 1).
- The new mux unit race, unit debug, and actual-route groups failed to build:
  `mux.go` retained an unused `N` network import after removing the direct writer.
  None of those tests executed. The dependent real-runtime matrix was skipped.
- Follow-up change: remove that unused import only from the mux implementation.
  Assertions, limits, source pins, and prior failures remain unchanged.
- [Evidence artifact 12091461961](https://github.com/ForceMind/V-UI/actions/runs/39006786929/artifacts/12091461961):
  ZIP 86,596 bytes, SHA-256
  `161746df3543cf858fdf3df2c1f65b0df810633cd42f3d9a818703c9ab54ed78`.
  All 30 manifest entries were verified by size and SHA-256 after download.
  `VERIFICATION_FAILED.json` correctly rejects the absent executed mux tests.

## 2026-10-17 mechanism green, real large-UDP compatibility red

- Commit: `a2f91cb707bd5a63484e82d22c3de09b03bbb125`, tree
  `c8c5a6fb7c2bbbeab0a83017875e8b2344f51bdf`.
- [Run 39007081471, attempt 1](https://github.com/ForceMind/V-UI/actions/runs/39007081471):
  mechanism job success, runtime job failure. No unchanged-code retry.
- Mechanism: race/debug each passed all 17 top-level and 3 child terminals.
  Actual route controls retained reply/End copy counts 2/1; the mux arm had zero
  copy/callback/canceler workers before reads were restored, an open parent and
  exactly one parent writer. Parent shutdown joined all recorded work.
- Same exact commit, formatted implementation bytes, ordered patch chain,
  compiler digest and normalized module graph matched the runtime build.
- Both runtime arms retained original 19/20 RED, passed all six independent
  lifecycle cases, and passed the real verified-TLS shared-parent TCP case:
  two 196,742-byte streams, complete framing and a later UDP sibling.
- In both arms, both encrypted VMess cases passed 1/8,192 bytes, then delivered
  only the exact 15,992-byte prefix of 32,768 bytes to the target. The 60,000-byte
  requests did not run. All four cases remain failures; no truncated payload is
  accepted as a success. [Fixed-source buffer analysis](SOCKS_BUFFER_BOUNDARY.md)
  is distinct from actual syscall observation.
- Mechanism artifact `12090903218`: 90,789 bytes, SHA-256
  `b753a5e0741d94b6930e118399a5af16c6eef7c79d1b66244c06685e80def330`,
  all 30 manifest members independently verified.
- Runtime artifact `12091353576`: 399,085 bytes, SHA-256
  `f9902b8c8e646d6b5b93ae47a84c9d1d1a4e68531b38cff870969231cf99d362`,
  all 277 sanitized text members independently verified.
- Next diagnostic adds real encrypted Client-to-Service normal-flow tests to
  both arms and records actual runtime replies before the unchanged assertions.
  It does not relax the original device compatibility gate.

## 2026-10-18 reconstructed owner first mechanism run (398ef8a)

Exact commit `398ef8a93fefc2f751b8cd3c7390fb80396ea7dc`, tree
`5430117fab9c74127089c59153c27cb4e310bb59`,
[run 39140791475 attempt 1](https://github.com/ForceMind/V-UI/actions/runs/39140791475).
Owner job `117714974285` failed. All 47 Python controls passed first.

The final box source had an import cycle: `common/dialer` → `common/tls` →
`dns` → `common/dialer`. Dialer, mixed and route owner groups executed zero Go
leaves, and the candidate binary did not build. Ten affected Darwin/Windows
compile-only checks failed. The final-tree old regression ledger has 21 command
successes, with `mux-route-race` and `build` failing; this is not a 23-check pass.
The separately constructed five-patch base retained its complete strict gate.

Actual new Go leaf terminals per mode were sing owner 62/62, chunk finalizer
1/1, adapter 2/2, and VLESS 4/4. VMess race passed 90/92, debug passed 91/92:
both race ciphers and debug ChaCha failed `corrupt_aead_terminal` because the
injected mutation produced a nil reader error. These original failures remain
RED. Source inspection subsequently identified that the test flipped the final
wire byte, which can be unauthenticated random padding; a deterministic
ciphertext-region correction requires a new exact-head run and does not alter
these results. No owner runtime or owner qualification was obtained.

Owner artifact `12141099997`: ZIP 599754 bytes, SHA256
`9c1dac5fb97dc259b669d082dddf8961e7f5ac97d06e153fdfa4c277fc62f021`;
399 unique evidence members had exact size/SHA matches. The result's first
strict error, `Missing/mislabeled binary identity`, does not replace the raw
compile and test failure details above.

The separate unchanged mux job passed its strict route, unit, normal encrypted,
encrypted cancellation and zero-datagram gates. Artifact `12141149246`: ZIP
123901 bytes, SHA256
`6cfba6c773760199d5157bc1074b31b37aa818fada1851d417c4ac8e64c5db0c`;
54 members independently verified. Original runtime and packet results are
recorded separately when their complete accurate-head evidence is checked.

## 2026-10-18 owner second mechanism run (99706f8)

Exact commit `99706f82eb85099b1697cdf914eb7d97fe3b39e2`, tree
`69420b32794ea0d54786ebc5d9910d7e4c0013bc`,
[run 39142231773 attempt 1](https://github.com/ForceMind/V-UI/actions/runs/39142231773).
Owner job `117719171168` failed after 159 Python controls passed.

The import-cycle repair compiled the complete candidate. Both the independently
built five-patch base and final eight-patch tree completed all 23 unchanged
regression commands. All 22 owner Darwin/Windows compile-only checks succeeded.
The repaired VMess authenticated-ciphertext mutation passed in both ciphers and
both modes; all 92 VMess and four VLESS leaves passed in each mode. Sing owner
62, chunk finalizer one, adapter two, dialer 13 and mixed limiter one also passed
in each mode. This establishes the recorded mechanism subsets, not whole-owner
acceptance or non-Linux runtime qualification.

The original route group ran 16 passing leaves before
`TestOwnerActualRouterLocalReject` panicked with `unknown reject method:`.
Its fixture directly constructed an empty reject method and bypassed the normal
JSON default normalization. The failed leaf and 615 unexecuted leaves remain
RED/incomplete in each mode.

The constructor group passed the real power-counter leaf. Five first owner
leaves failed during SOCKS setup with `request rejected, code=1`; fail-fast left
24 leaves unexecuted in each mode. Fixed `ClientHandshake5` rewrites an
unspecified IPv4 hint to `[::]:0`, which the intentionally IPv4-only owner rejects.
A fixture correction must explicitly encode the supported IPv4 association
request; it must not relax the source admission policy or skip any leaf.
No timing or late-lease success is inferred from these setup failures.

Owner artifact `12141967041`: ZIP 614860 bytes, SHA256
`187809f13488e40a9c6104d054bd316af68fdec26bff00c5ad9a4debbb1a92cd`;
418 unique members matched exact sizes and hashes. Strict owner verification
failed and both owner-off and owner-on runtime stages were not started.
The original stock runtime/packet jobs remain independent and retain their
accurate-head failures. No product or same-association recovery qualification.
