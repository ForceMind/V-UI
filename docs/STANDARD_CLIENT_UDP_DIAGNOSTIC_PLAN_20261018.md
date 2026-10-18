# Stock-client UDP: finite boundary and 1200-byte idle diagnosis

Status: design reviewed; implementation and actual evidence pending. Continue the isolated `experiment/standard-client-udp-20261018` branch from `4ef6f0392e5349acf51298e24aa32322407d57bf`. Prior [C/D/B results](STANDARD_CLIENT_UDP_RESULTS_20261018.md) remain unchanged and red. This is not product relay UDP qualification or a phone/TUN test.

## Fixed matrix: seven cases, ten packet leaves

| Servers: domestic / overseas | Group | Cases / leaves | Exact traffic |
| --- | --- | --- | --- |
| Existing candidate / existing candidate | E: boundary | 4 / 4 | Four separate first-packet associations: uplink 16374, uplink 16375, downlink 16384, downlink 16385 bytes |
| Official / official | I: small idle | 1 / 2 | 1200-byte complete echo, full six-second quiet window, one new 1200-byte echo on the original association |
| Existing candidate / existing candidate | I: small idle | 1 / 2 | Same fixed sequence |
| Existing candidate / official | I: small idle | 1 / 2 | Same fixed sequence; only overseas role differs from candidate arm |

Every device is unmodified official Mihomo 1.19.32 (`3122d100e8177501776109f1a6253a694611627cf4d7c7ec82705855cf8626a8`), using its mixed/SOCKS interface and the unchanged ordinary VLESS exporter node in the same fixture shell. Official servers remain sing-box 1.14.2 (`07d2866a6c908281a56053f0e7d454918501592f2f258526f2ebe826dab58793`). The candidate remains the exact old eight-patch `afbdba100c5f0e3dc6888684fdbab31980720df3` source, rebuilt only on the temporary runner if its old binary is unavailable, with complete unchanged build verification. All owner options must be absent.

## Measurements and bounded continuation

- Reuse the frozen packet generator, target ledger, exact payload/address/port and original single-send/receive checks. E uses four separate strict cases, with no hard-coded expected failure or accepted prefix. Uplink records actual target bytes and target send; the application echo is attempted only when the original strict target prerequisite passes, otherwise it is explicitly unexecuted; downlink sends the frozen small probe and measures the specified response size. Failed capacity remains red even when the evidence is complete.
- I uses distinct deterministic `first` and `recover` 1200-byte payloads/hashes. A failed initial full-payload/tuple prerequisite leaves window and recovery unexecuted. No timeout is called explicit rejection.
- After a complete initial reply, keep the original application control and UDP objects, descriptors/inodes, local tuple and SOCKS relay. Domestic `udp_timeout` stays 2s. Observe the entire six-second window from the last complete business reply, with passive PID/birth, socket and control-PEEK samples and zero application sends. Do not lengthen the timer or insert keepalive, probes, reconnect, retry or replay.
- Record server-leg release and next-packet availability separately. After the entire window, send exactly one new 1200-byte packet on that same association, even if the measured leg did not release. This limited diagnostic continuation differs intentionally from strict D; it never changes D's 60000-byte prerequisite, fail-fast behavior, six-second gate or failed result. Identity or unsafe observation failures stop continuation.
- Retain all raw samples, including device snapshot races as inconclusive rather than absence. No OS-level continuous observation claim is inferred from periodic samples. Release requires verified absence and no reappearance through the final sample; availability alone cannot green a failed release gate.
- Each independent case/arm may continue after a capacity failure only after original resource cleanup and independent raw identity/FD/thread/process-group verification succeed. Retain the established 20-minute stage and 1250-second owned-group boundaries. Unsafe predecessors stop later groups. No second capacity/boundary collection is planned; only recoverable fixture/evidence faults can justify a targeted repair, with the failed run retained.

## Source, archive, CI and review

Commit a recoverable design checkpoint first. Add a separate diagnostic entry point/schema and verifier using the reviewed adapter and text-only archive safety boundary. Do not modify the frozen source or strict C/D lifecycle. Recompute the raw-to-verdict proof, ordinary-export-to-device-config binding, exact plan, before/after source/binary identities, complete build provenance, cleanup and archive hashes. Include positive and one-field-negative controls for the new window/recovery/release distinctions.

A separate finite workflow runs this seven-case set, current pure controls and the product P4 ten-method negative-control group. Disable the old research workflow's automatic push trigger; preserve manual invocation and every old run. The new workflow is enabled for the isolated branch only after exact source review. It must not accidentally rerun old C/D/B/auth. Old authentication evidence remains tied to its old source and is not inherited as new-head authentication qualification.

After one bounded runtime collection, independently read all raw records and sanitized text members, bind exact SHA/tree/run/attempt/artifact hashes, and update the result/compatibility/handoff documents on the same branch. No binary upload, pin/VERSION change, client modification, production merge, release, deployment or security-setting change is included.

## Decision scope

The desired answer is whether these two ordinary-sized packets can use the same stock-client association across the six-second interval, and whether replacing only the overseas candidate with official sing-box changes that measured outcome. This can constrain the tested server-role requirement; it cannot establish the minimum of eight individual patches or general mobile compatibility. The four boundary points can strengthen or contradict the current source-supported truncation inference, not establish every large-packet failure's cause. Native mobile import/TUN, public networks, real MTU behavior and long-duration traffic remain separate future gates. Product relay UDP rejection remains enforced.
