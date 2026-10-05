# Ordered implementation ledger

The user authorized completion of the scheduled roadmap on 2026-10-01. Work proceeds sequentially from verified alpha.2 commit 7118bd6ca67b8f996f2359db7921b95ca560cd89. Older PRs remain unmerged; each new PR explicitly depends on its predecessor. Candidate-pool features are not part of this authorization.

| Stage | Scope | Status |
| --- | --- | --- |
| alpha.3 | Read-only scoped subscription grants | Implementation and regression added; awaiting this PR's CI |
| alpha.4 | Safe core config apply/recovery | Not started |
| alpha.5 | Fixed ToClash parity and validated exports | Not started |
| alpha.6 | Saved routing and subscription UI | Not started |
| rc.1 | Real loopback proxy/DNS chain | Not started |
| rc.2 | Selected deployment release gates | Not started |

## alpha.3 contract

Authenticated administrators create a grant at POST /api/subscriptions with label, public node server, explicit inbound_ids, formats and expires_days. Create/rotate responses show the bearer URL once. Lists never return token hashes or cleartext tokens. GET/HEAD /sub/{token}/{format} is the only non-admin export path; formats are mihomo.yaml, raw and sing-box.json. Query overrides are rejected.

A grant selects existing enabled node IDs, not all present/future nodes. It cannot access /api/*, administrative cookies cannot be substituted for subscription tokens, and a deleted/disabled selected node blocks export instead of silently generating DIRECT. Expired/revoked grants return the same generic 404 as invalid tokens. Password reset or account disable invalidates associated grants. Rotation intentionally extends expiry to the supplied number of days.

Connection server is independent of the panel Host. It is stored in the grant, not taken from an untrusted request header. Actual protocol combinations remain subject to alpha.5 verification.

Application access logging redacts the complete /sub/ URL, including query strings. The standard main.py entry disables access logs. A separate reverse proxy must also disable/redact logging for /sub/; application code cannot sanitize logs from external proxies or the user's client. All returned configs use no-store and no-referrer. Never paste real subscription URLs into issues or command history.

No master merges, tags, releases or VPS deployment have been performed. This ledger does not claim unrun CI results.
