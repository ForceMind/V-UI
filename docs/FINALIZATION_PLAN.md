# Finalization after rc.2

The owner's latest request explicitly adds production-release preparation, a one-command installer, automatic certificate issuance and a graphical certificate manager. This extends the earlier scope; it does not authorize accessing a real VPS or issuing a real domain certificate without domain ownership inputs.

1. rc.3: certificate management using fixed Certbot 5.8.0 and HTTP-01. Persist job/status and renewal policy, use staging versus production explicitly, validate material before activating, never return private keys, and bind certificates to panel/node consumers. Add real Pebble ACME and browser acceptance, not just mocked successful issuance.
2. rc.4: a guarded one-command deployment path and comprehensive public documentation. Preserve existing verified offline packages, create a dedicated service account/systemd units, avoid root web processes, guide initial admin/certificate setup, retain recovery and update checks. Prepare a manual, gated official release workflow; do not create an untested release or silently merge the dependent PR chain.

Default certificate validation is HTTP-01: public DNS must reach the VPS and port 80 must be reachable. Wildcards/DNS provider credentials are not implied. Missing prerequisites must be diagnosed, never bypassed with disabled TLS verification.

Existing ToClash and strict export guarantees must remain covered. No unrelated protocol expansion, no changes to real user data. CI queued/running is not acceptance. All completion claims must name the final tested commit and any untested external conditions.
