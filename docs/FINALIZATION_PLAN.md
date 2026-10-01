# Finalization after rc.2

The owner's latest request adds production-release preparation, a one-command installer, automatic certificate issuance and a graphical certificate manager. It does not authorize accessing a real VPS or issuing a real domain certificate without domain ownership inputs.

## rc.3 — certificate lifecycle

Fixed Certbot 5.8.0, real HTTP-01, separate staging/production records, durable jobs and renewal policy, expiry/error metadata, immutable validated key/certificate material, panel TLS hot reload and existing sing-box VLESS/TLS node binding. No private-key API. Domain control/TOS consent is explicit. Pending queue, renewal failure and consumer apply failure are separate states; stopped cores are not silently restarted.

Local validation: 15 certificate/API/bootstrap cases, four HTTP-01 isolation cases, real serving-context replacement/rejection, and two Certbot/Pebble issuance/failure cases. The larger suite is split locally to avoid the execution tool timeout. CI must execute the actual Chromium certificate workflow and all prior release gates on the final head before acceptance.

The standalone certificate page is linked from the account and routing workspace. Final one-click deployment and the existing new-node form's managed-certificate selector are completed in rc.4, not claimed as finished by the rc.3 prerequisite PR.

## rc.4 — install and release preparation

Guarded one-command Ubuntu24.04 amd64 deployment with a dedicated non-root user, verified offline package, systemd service/socket units, initial certificate/bootstrap and admin setup, conservative update/recovery behavior. Port80 belongs only to a challenge responder, never the administrator site. Preserve existing sites and databases, do not silently stop unrelated services.

Complete README, installation, certificate, update/recovery, security, API, compatibility, contribution and release documentation. Prepare a manual gated official release workflow with explicit tested commit; do not publish a failed candidate or silently merge dependent PRs.

HTTP-01 needs public DNS pointing to this VPS and reachable TCP80. Wildcards, DNS-provider credentials, more proxy protocols and alternate deployment architectures remain outside this finalization scope.
