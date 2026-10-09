# Root installation and new release-directory resource gate

Status: contract reviewed; implementation and actual CI qualification pending.
The accepted `bc24c55` CPU recovery and ordinary gates remain separate evidence.
Product bytes remain at `5f44a2f`; this fixture must not change them.

## Bounded contract

Use only an explicitly disposable GitHub-hosted Ubuntu 24.04 runner with
systemd and cgroup v2. Before registering any destructive cleanup, refuse an
existing v-ui user/group, installer lock, reserved state/config/controller tree,
unit, enablement link or drop-in in the effective systemd search paths (including
dangling links), or an already loaded/transient unit. Refuse occupied HTTP-01
port 80. Record ownership of everything the fixture subsequently creates.

A unique parent slice must read back `memory.max=536870912`,
`memory.swap.max=0`, and `cpu.max=100000 100000` before the test starts.
The worker, sudo/root installer, staging/pip/backup/restore children and both
PID1-launched application services belong beneath that same parent. Install
owned temporary `Slice=` drop-ins before the first service start. Check root
entry membership and effective service Slice/DropInPaths/ControlGroup, UID and
PID/start identity. Descendants inherit membership; sampling does not pretend
to enumerate every short-lived process. Never reset the cumulative peak.
The coordinator, package construction and PID1/socket host overhead remain
outside and are explicitly disclosed; this is not a whole-host 512 MiB test.

Build candidate A once from the exact test head. B changes only release_id in
its manifest: compare the complete member set, modes and streamed file hashes,
plus every other manifest field, and record both archive SHA256 values. This
is synthetic new-directory staging/activation, not historical-version migration.
Preserve the ordinary oneclick gate and its existing same-bundle upgrade.

The measured sequence retains real sudo installation, verified HTTPS, non-root
services, actual socket-activated HTTP-01, refusal of an implicit repeat, and
same-bundle upgrade/restart. For A to B, prove CURRENT=A, B directory and READY
absent initially, and the same live A panel generation while B is being staged.
Bind the unique newly created pre-upgrade backup from this particular upgrade.
Normal upgrade must preserve the old login session. Compare known service
configuration fields semantically; release_id/bootstrap_python/ready change
as expected. Do not insert an unsupported config key as a preservation marker.

Create a bounded synthetic marker in data before backup, then change it after
backup. Stop owned services; reject a wrong archive digest without changing
data; restore the verified backup and prove the marker and database integrity
return. Restore revokes old sessions, requires a new login, and leaves CURRENT
at B. Assert the intended private 0600/0700 permissions and non-root ownership.
All copies, hashes and verification reads remain in the same resource total.
This fixture does not repeat the separate 256 MiB log/1000-node data profile.

An external supervisor imposes an absolute total deadline. Cleanup stops the
owned socket first, then services and worker with bounded stop/KILL handling.
It captures final parent metrics while the slice still exists, then removes
only verified owned resources. Cleanup failure cannot pass. Preserve partial
JSON and safe diagnostics on timeout, OOM or worker failure. Keep the existing
one-base-page peak allowance and OOM assertions; max events are reported and
are not silently treated as OOM.

Stop this development batch after one exact-head bounded qualification and
independent artifact review, or a concrete blocker. No additional 90-minute
run is part of this fixture loop. Final same-head profile consolidation is a
later batch. Real whole-host headroom, the 160 MiB engineering budget and
24-hour continuous stability remain separate requirements.
