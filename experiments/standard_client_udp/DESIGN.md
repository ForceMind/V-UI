# Stock Mihomo UDP two-hop comparison

This is isolated research based on product master `1489bf5218fced9b42c57e06715478dc323dc782`. No product code, core pins, export protocol fields, VERSION, binary distribution, deployment or phone app changes are allowed.

## Fixed inputs and separate arms

- Device: unchanged official Mihomo 1.19.32, SHA256 `3122d100e8177501776109f1a6253a694611627cf4d7c7ec82705855cf8626a8`.
- Official sing-box 1.14.2: SHA256 `07d2866a6c908281a56053f0e7d454918501592f2f258526f2ebe826dab58793`.
- Frozen framework and server candidate: exact experimental checkout `afbdba100c5f0e3dc6888684fdbab31980720df3`, tree `996d37d925be8edd18cbf921d3cc580f43c0730d`. It remains outside product source. Every runtime explicitly rejects any owner option.
- Product negative control: unchanged current product per-inbound real test, including actual ordinary API export, real relay UDP rejection and ordinary direct small UDP. This is distinct from research forwarding.
- `stock`: official domestic and overseas, stock device, research guarded server configuration. Run C and D VLESS subsets with strict original expectations. Wrong address is failure, not a supported baseline.
- `candidate`: existing eight-patch server candidate at both servers, stock device, all owner options absent. Run C and D.
- `overseas-stock`: candidate domestic, official overseas, stock device. Run B VLESS response diagnostic. Report measured capacity without the old three-protocol must-fail assertion.

## Immutable gates

C is exactly 26 cases / 66 leaves: first and warmed uplink/downlink, 0/1/8192/32768/60000/65497 bytes, full-short-empty recovery, and one local IPv4 EMSGSIZE at 65498 payload + 10 SOCKS bytes. D is exactly 2 cases / 4 leaves: 60000-byte prerequisite, close or idle, 32768-byte recovery. B is 1 case / 4 leaves: 8192/32768/60000 downlink and 7-byte recovery.

Preserve the frozen byte generator, target ledger, exact source/address/port, full payload, single send, no replay/keepalive, fail-fast C/D semantics, cleanup and time limits. Domestic udp_timeout remains 2s and the entire release observation window remains 6s from the last complete business reply. Idle retains the same application SOCKS control object, UDP source socket and relay tuple; close legitimately creates a new application association. Record PID/birth/executable and socket identities. If the 60000 prerequisite fails, record idle/close window and recovery as unexecuted; process teardown is not idle proof. Stock's missing short idle release remains red.

Small-packet lifecycle diagnostics may be added only as separately named, nonqualifying observations; they never replace C/D or turn its red into green. No claim is made about all mobile clients, TUN/VPN, public networks, UDP/53/443, native UDP, packetaddr or new protocol combinations.

## Adapter and identity

Keep frozen afbdba1 framework in a separate checkout, verified before and after execution. Add only a narrow adapter in this branch: select stock Mihomo as the device, preserve official/candidate server role selection and expose accurate role names. Generate a normal VLESS device node with the current product exporter in an isolated process; copy its fields without changes into a fixture-only listener/selector shell. Store hashes and structural equality, no private material. The server research fixture is not a supported product UDP mode. The real product API export negative control remains separate.

The adapter owns a new evidence schema and verifier: complete versus failed versus never-started leaves, target observations, role origin/hash/version, cleanup, source manifests and exact plan counts. It must not invoke the original assessment's hard-coded B failure expectation, or claim the original sing-device suite passed. Missing/extra/duplicate evidence, changed binaries/source, leaked fixture materials or unsafe cleanup fail closed. Strict capacity failure makes the research workflow fail even when observation and cleanup succeeded.

## Build and publication boundary

Reuse local fixed binaries and downloaded evidence. Experimental binaries were deliberately not uploaded. If no matching temporary binary exists, rebuild the exact old candidate on an empty CI runner using the unchanged old builder, fixed upstream checkouts and fixed Go 1.25.14. Reuse its old verified mux mechanism text artifact where available; do not lower its original build checks. Binary is used only on that runner, never uploaded. Publish only this branch's reviewed adapter, tests, docs, workflow and strictly allowlisted sanitized text evidence. Preserve all prior failed runs.

## Review and closure

Design review precedes implementation. Commit recoverable WIP before running long experiments. Final review binds exact new head/tree, run/attempt, immutable old-source identity, stock and candidate binary identities, actual leaf status/counts, cleanup and artifact hashes. Update DEVELOPMENT_HANDOFF.md and STANDARD_CLIENT_COMPATIBILITY.md with evidence and remaining work; no merge or product qualification is inferred from research completion.
