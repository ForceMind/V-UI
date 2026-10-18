# Bounded owner runtime candidate

This is a reconstructed source candidate after the 2026-10-18 filesystem rollback. The lost runtime patch digest `0e9209d9155655d8419bcf7ce07d05690ad0122f589a1478ca74643398e8b817` is historical only. These Python sources have new digests and require fresh review and exact-head testing. The regenerated `OFF_OUTCOMES.json` alone reproduces its old SHA256 `77a17333e64cc75055bf6a4ee6e8eeae50c286434b2503a44a67a529738451b3` from independently restored 6e artifacts. No lost Go/mechanism/runtime result is inherited.

Design basis: reconstructed `DESIGN.md`, SHA256 `35b4a5b7fdfdfeb93d8ca2d4934f802acbc34eadf906a82d24c62b828691d3a4`, especially §§8–9. This document describes preparation, not a runtime execution or product qualification.

## Integration and exact source

The parent integrates these files under `experiments/socks_idle_recovery` and commits them with the complete owner mechanism. All old packet modules, observer, helper, packet plan/bytes, business assess, coordinator, process-group cleanup and safety verifier remain unchanged. The adapter checks the five frozen module hashes before import. It explicitly imports a fresh copy and overrides only experiment identity, input validation, fixture/config selection and the finite coordinator stage tuple. It does not forge a five-patch identity for an eight-patch executable.

Real execution refuses a scratch adapter: its own directory must be the reviewed repository's owner experiment directory. `harness.identity()` requires every source/test/document byte to match HEAD. The candidate version is `1.14.2-vui-socks-owner-experiment-<commit12>`; the same-head base version remains `1.14.2-vui-complete-packet-experiment-<commit12>`. Both retain fixed sing/sing-vmess/sing-box revisions. Product pins and defaults are unchanged.

The distinct input schema has exactly `schema_version`, `kind`, `source_identity`, `official`, `candidate`, `arm`, and `build_evidence`. Kind is `vui-socks-owner-source-experiment-input`. The stock tuple is the original fixed 1.14.2 binary. Candidate records path/hash/version/provenance path/hash; build evidence records its directory and exact member digests. Provenance contains the ordered five original patches followed by sing_owner, vmess_session, box_owner. It explicitly requires final_sources_equal_base=false, same_head_base_validated=true, and unchanged shared server writer. It never asserts old source equivalence.

Before any stage, the adapter re-verifies the complete final mechanism evidence and its 23 original regression checks, actual same compiler/graph, all contract-selected new owner groups, cross-compiles and source manifests using the new build verifier. Build evidence must report passed=true, errors=[], runtime_executed=false, product_qualified=false. Snapshot digests must remain unchanged after verification. Actual executable SHA and version are checked. Version subprocesses run within the original owned stage process group, without creating a detached child group.

## Finite matrix and continuation

Exactly one off arm executes C, D, A, B, U, V using the final eight-patch candidate. A/U/V retain stock device; B retains stock overseas. All roles record owner_recovery_enabled=false and owner_recovery_outbound=null, and receive no owner option.

Exactly one on arm executes C and D. C remains 78 cases / 198 leaves, including original local EMSGSIZE cases. D remains six cases / twelve leaves. Only the candidate device's sole mixed inbound receives `experimental_socks_udp_idle_recovery: {outbound: DOMESTIC_ONLY}`. The selected outbound and final route must match that exact tag. Domestic and overseas stay off. No stock binary ever receives the option. Each role binds binary SHA/version, configuration SHA, launch PID/birth and its fixed log section to an explicit mode/tag ledger.

The original coordinator owns process groups, timeout/kill/reap behavior and stop-on-unknown-cleanup. Business failure and resource safety remain separate. A safe RED stage may allow the next fixed diagnostic stage; an unsafe/incomplete stage stops the remaining matrix. Additional mode/role validation is included in the continuation safety wrapper. The new driver never overwrites stage_gate_passed to turn off D or stock RED green.

Off results must reproduce the complete frozen per-case/per-leaf classification projection and original gate map (C/B/U true, D/A/V false). Every applicable original failure-observation checker and the original D SOCKS observation checker must independently verify, with no checker error; a matching RED projection cannot conceal failed raw-evidence verification. In particular, D preserves three close passes and three actual single-send idle failures: PEEK EOF, device control/relay absence, one 32768-byte send, zero target delivery over the unchanged four-second receive window and no reply-window invention. The six-second resource window and initial 60000-byte business packet remain original. On C/D must pass the original strict assess and all packet hash/tuple/target-ledger/zero-delivery/cleanup assertions.

The root result reports original_suite_passed=false and product_qualified=false even if finite_experiment_contract_passed becomes true. It records both arms, original per-stage gate states, additional verification, exact unexecuted stages and resource safety. It does not claim official clients or production lines are repaired.

## Typed lifecycle evidence

The existing device log carries one bounded JSON object after `socks_udp_owner `. Required keys are pid, owner_id, sequence, transition, generation, packet_sequence, accepted_packets, rejected_sources, remapped_packets, cause, source, relay, target. JSON duplicate keys, nonfinite numbers, invalid uint64/bool counters, unknown fields/enums, noncanonical IPv4 tuples, incomplete lines and oversized records are rejected. The PID is the exact device launch PID. Event callbacks may arrive out of order, so authoritative per-owner sequence is sorted, then checked contiguous and unique.

Expected transitions are create → admitting → admitted, optionally retire(session_end) → join(session_end) → dormant(none), then a later fresh packet admits the next generation. Idle D requires exactly one original owner and two admitted generations, with complete g1 join/dormant before g2. No extra owner, replay, synthetic packet, target remap or rejected-source count is accepted in this fixed runtime. Every accepted packet count is bound to a real original successful send, including empty application datagrams. C requires one generation. Close D uses separate owner IDs 1 and 2 and one generation each. Every case uses a new fixture process, so process-wide owner IDs begin at 1.

Final closing/closed must carry EOF and the exact accepted packet/generation totals. Closed is the typed whole-owner join boundary after its workers, route copies, lease finalizer and invocation/callback complete. It is not necessary to invent a separate final-generation join event on control EOF. A naturally idle g2 may have its own canonical retire/join/dormant suffix before final control close.

Raw fixture logs have the frozen three role sections. Repeated snapshots are accepted only if each later section extends the exact earlier bytes. Domestic/overseas or any off-role owner event fails verification. Missing/extra/truncated owner events fail even if packets passed.

## Existing passive samples and honest scope

The original verify_socks_observation.verify_case first checks all timing, shape, original descriptors, PID/birth, binary identity, recorded races and lifecycle references. Strict owner-on checking adds:

- Stable warm and before-recover samples retain the original device control TCP and relay UDP inodes, tuples and owned rows; all other adjudicable pre-cleanup samples must retain them too.
- Every original pre-cleanup control PEEK reports would_block/EAGAIN. EOF, queued data, syscall errors or owner socket replacement fail. Raced intermediate samples remain explicitly inconclusive.
- Existing after_large_business, before_recover and after_recover samples must stably show one original private TCP connection to the domestic ingress, its old inode absent before recovery, then a new private TCP inode under the same sampled PID/birth. Reused ephemeral ports are not automatically a failure: the new inode is the allocation proof, and both tuples are recorded.
- Close cases require distinct new local control/application socket inodes and the original full cleanup checks.

No extra sampling window is added. `device_whole_window_continuity_proven` remains false. Typed events support owner invariants; they cannot retroactively turn a raced OS snapshot into whole-window continuity proof. Missing or raced decisive transport samples fail instead of being silently treated as green.

## Running after mechanism approval

Linux with /proc, Python and the original psutil/runtime fixture dependencies is required. The final mechanism builder must already have completed for this exact HEAD. Candidate and official binaries remain runner-local. The parent alone chooses when the reviewed candidate is ready; preparing these sources does not authorize or claim runtime completion.

```
python3 experiments/socks_idle_recovery/runtime_adapter.py --repository "$PWD" --arm on --stage C --describe
python3 experiments/socks_idle_recovery/run_owner_runtime.py --repository "$PWD" --build /runner/owner-build --official /runner/official/sing-box --output /runner/owner-runtime
```

The output must be fresh. Stage and case residual evidence is retained on failure. Evidence packaging is a separate text-only operation documented in `RUNTIME_ARCHIVE_CONTRACT.md`; it neither evaluates runtime business gates nor uploads anything by itself. Only its explicit finite allowlist is eligible for a later authorized artifact upload. Source clones, binaries, configs, PEM and secrets are excluded.

## Preparation verification and limits

Synthetic controls exercise typed-event parser/FSM, source and generation identity, role activation, eight-patch provenance, compiler/graph/regression negative controls, existing passive sample checks and finite summary classification. Historical 6e D verification establishes only preservation of the old close/idle diagnosis. Original coordinator and safety Python controls are rerun unchanged. No core process, Go test, mechanism CI, runtime matrix, upload, release or product qualification is performed by this preparation task. New runtime qualification must be attached to its exact final commit, compiler, build evidence and both off/on arms.
