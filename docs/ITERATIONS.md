# Ordered implementation ledger

User authorization: complete scheduled alpha.3 through rc.2 sequentially. Optional candidate features and real deployment are excluded. Every stage has an explicit dependent PR; older PRs remain unmerged.

| Stage | Scope | Evidence/status |
| --- | --- | --- |
| alpha.3 | Scoped read-only subscription grants | PR #4, 1c4724e; CI 61, 53 tests + Chromium passed |
| alpha.4 | Safe config apply and recovery | PR #5, 1d4fc3e; CI 63, 66 tests with separate fixed-core checks + Chromium passed |
| alpha.5 | ToClash parity and validated exports | PR #6, 698d098; CI 65 + ToClash reference run 2 passed; 77 tests and 100 independent cases |
| alpha.6 | Saved routing and subscription UI | PR #7, ab641a2; CI 66 + reference run 3 passed; 87 unique cases across tasks plus both Chromium workflows |
| rc.1 | Real loopback proxy/DNS chain | Implemented; local real-core tests passed; final CI required |
| rc.2 | Selected deployment release gates | Not started |

alpha.3 uses explicit node IDs/formats and an independent public server address. Tokens are SHA-256 stored, shown once, expiring, revocable and rotatable; they never grant management access. App logs redact subscription paths; external proxy logs require separate configuration.

alpha.4 uses immutable configuration revisions and commit-last state pointers. Desired and applied revisions are distinct, invalid candidates preserve the previous runtime, failed starts roll back, manual stop persists and process death cleans up children. Linux with pidfd support and one worker are required. Checksum-pinned sing-box 1.14.2 and Xray 26.3.27 are configuration-tested; Xray rendering targets that exact tag's clients/network schema. Initial bad sing-box expected digest was caught before execution and corrected from the precise immutable official asset.

alpha.5 fixes ToClash 0.3.8 / 95a5c71a516c10f97f47bfb771018ce890b2b570 and Mihomo 1.19.32. Full configuration semantics, ordered DNS policies/rules, warnings and all 40 presets are compared independently. First public export profile is explicitly sing-box VLESS/TCP/TLS; unknown or unverified profiles fail rather than silently losing parameters or turning DIRECT. See EXPORT_ALPHA5.md. Node selection, creation, and profile drafts beyond this verified export profile do not imply end-to-end support.

No master merges, tags, Releases, user databases or VPS deployments have been performed. CI acceptance must reference the final stage head, not earlier results.
