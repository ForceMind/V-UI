# Ordered implementation ledger

User authorization: complete scheduled alpha.3 through rc.2 sequentially. Optional candidate features and real deployment are excluded. Every stage has an explicit dependent PR; older PRs remain unmerged.

| Stage | Scope | Evidence/status |
| --- | --- | --- |
| alpha.3 | Scoped read-only subscription grants | PR #4, 1c4724e; CI 61, 53 tests + Chromium passed |
| alpha.4 | Safe config apply and recovery | PR #5, 1d4fc3e; CI 63, 66 tests with separate fixed-core checks + Chromium passed |
| alpha.5 | ToClash parity and validated exports | PR #6, 698d098; CI 65 + ToClash reference run 2 passed; 77 tests and 100 independent cases |
| alpha.6 | Saved routing and subscription UI | PR #7, ab641a2; CI 66 + reference run 3 passed; 87 unique cases across tasks plus both Chromium workflows |
| rc.1 | Real loopback proxy/DNS chain | PR #8 / 8e577dc; CI67 + reference4 + real loopback1 passed, five real TLS/DNS paths |
| rc.2 | Selected deployment release gates | PR #9: offline pinned package / HTTPS / non-root / local UI / stopped backups and rollback; final head Checks are the acceptance record |

alpha.3 uses explicit node IDs/formats and an independent public server address. Tokens are SHA-256 stored, shown once, expiring, revocable and rotatable; they never grant management access. App logs redact subscription paths; external proxy logs require separate configuration.

alpha.4 uses immutable configuration revisions and commit-last state pointers. Desired and applied revisions are distinct, invalid candidates preserve the previous runtime, failed starts roll back, manual stop persists and process death cleans up children. Linux with pidfd support and one worker are required. Checksum-pinned sing-box 1.14.2 and Xray 26.3.27 are configuration-tested; Xray rendering targets that exact tag's clients/network schema. Initial bad sing-box expected digest was caught before execution and corrected from the precise immutable official asset.

alpha.5 fixes ToClash 0.3.8 / 95a5c71a516c10f97f47bfb771018ce890b2b570 and Mihomo 1.19.32. Full configuration semantics, ordered DNS policies/rules, warnings and all 40 presets are compared independently. First public export profile is explicitly sing-box VLESS/TCP/TLS; unknown or unverified profiles fail rather than silently losing parameters or turning DIRECT. See EXPORT_ALPHA5.md. Node selection, creation, and profile drafts beyond this verified export profile do not imply end-to-end support.

No master merges, tags, Releases, user databases or VPS deployments have been performed. CI acceptance must reference the final stage head, not earlier results.

rc.2 uses Ubuntu24.04 amd64 / CPython3.12, one web worker, a dedicated unprivileged user and direct HTTPS. Candidate artifacts are uploaded only after the deployment job succeeds. It does not exercise Docker, ARM64, arbitrary historic migrations, actual user VPS networks or optional candidate-pool features. The second-version rollback test uses a synthetic candidate of this schema. No automatic Release or master merge.


## Certificate and production-readiness extension

rc.3: PR #10 / 2b24e7fe382d6529b6223d02f67fbdda72e5832a. Test72, reference9, loopback6, deployment5 and ACME2 succeeded. Real Certbot/Pebble issuance, renewal and failure checks plus the certificate browser workflow are distinct from actual user-domain issuance.

rc.4: one-command systemd setup, managed-certificate selector, unified user/operations/API/security/release docs and gated manual promotion. Final status is recorded on the exact final PR/CI commit, not inferred from this ledger. No automatic master merge, public Release or user VPS deployment.


## v0.3.1 Linux installation review repair (PR #12)

This bounded repair starts from `06c0b8653f7b8d27a43d0ac0bb0bf61561fb7e95` on the existing PR #12 branch. It does not merge dependent PRs or change release/protocol scope.

- OpenRC HTTP-01 selects IPv6-only before binding, while systemd keeps inherited socket activation.
- New installations test IPv4 and available IPv6 for HTTP-01 and the default sing-box node before firewall/account/service writes. Existing conflicting listeners remain live.
- Firewalld uses a single active interface-bound zone, never its default as a proxy for ingress. Multiple zones, source bindings, malformed/failed queries require manual handling. A changed zone after confirmation is refused before writes.
- Real temporary-socket regressions reproduce the pre-fix collision and IPv6-only conflict cases. Mocked command tests cover non-default zone selection, runtime/permanent writes, manual fallback, explicit `yes`, and no-write dry runs. They do not operate the host firewall or service accounts.

Run the focused regression group with `python -m unittest discover -s tests -p test_linux_installation_regressions.py -v` and `python -m unittest discover -s tests -p test_firewall_support.py -v`, alongside existing HTTP-01 and installer tests. The final exact-head acceptance record is PR #12's eight separate workflows: Test V-UI, ToClash, loopback, ACME, one-command installation, portable matrix, selected release deployment, and documentation. Local tests do not replace those gates; no public Release or user VPS deployment is implied.


## v0.3.2 node editor review repair (PR #13)

This bounded repair begins at `7afc1e1f2993159aa249fa864001dce08415da62` and normally merge-forwards the reviewed PR #12 head `719b2b138d681e1b08370c4c769d7d73d47be43f`; no history rewrite, main merge or release is implied.

- Changing managed TLS to none/REALITY clears the hidden certificate selection and binding, without requiring unused file paths. Conflicting explicit managed IDs are rejected rather than changing security silently.
- Staying on TLS retains the manual replacement-path guard. Empty profiles remain no-ops; validation failures preserve binding; saved desired transitions unbind even when core application fails.
- New API and actual app.js regression tests reproduce the reviewed defects before the fix. The separate real Chromium/Uvicorn/pinned-core gate uses synthetic local CA material for create, cancel, edit, reload/re-edit, TLS export and stopped restore. Existing secret redaction/preservation, core/protocol immutability and stopped-core renewal checks remain required.
- Acceptance requires all eight distinct workflows on the final exact head. Local passes and environment skips do not replace binary, loopback, ACME, one-command system installation, portable, deployment or documentation acceptance. REALITY/none edit coverage is not new export/connectivity support.

Browser acceptance fixture restart closes its old page before opening a new random localhost origin, so retired-page polling cannot be mistaken for a third-party request. The no-external-request assertion remains strict.
