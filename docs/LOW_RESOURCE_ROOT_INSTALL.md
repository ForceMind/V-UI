# Root installation and new release-directory resource gate

Status: the original root fixture qualified at `d745c61`; bounded upgrade
accounting below is a new test-only candidate and awaits its own qualification.
The original local suite had 746 tests, 621 passed and 125 environment skips; 32 focused
fixture contracts independently passed. These do not prove root/systemd resources.
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

An external supervisor imposes an absolute total deadline. Cleanup first stops the worker and installer/rollback initiators, then the
owned socket before either product service, with bounded stop/KILL handling.
The parent must report no remaining descendants and populated=0 before files
or accounts are removed.
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

## Original qualification and the remaining attribution gap

Exact `d745c61de1735ad2d47e8aea728aaa2b443bfe61` passed the
[root run 38012745140](https://github.com/ForceMind/V-UI/actions/runs/38012745140)
on 2026-10-10: all nine stages, four actual root entry receipts and cleanup.
Artifact 11655305842 ZIP SHA256 is
`4243b49f6f91d30a132067597100f9e4dccf514bf93b50847f9806bc79067938`;
the downloaded original passed ZIP CRC and digest verification again.

Initial installation reached a cumulative 393228288-byte peak. Just before
fresh-directory upgrade, current was 140795904 bytes with that same peak;
afterwards current was 159494144 and cumulative peak 520445952 bytes
(496.336 MiB). All max/OOM counters remained zero, but the nominal margin was
only 15.664 MiB. The 121.324 MiB peak increase occurred somewhere within the
40.690-second upgrade. Existing retained live-A/not-ready-B observations cover
only about 15.318 seconds of it and have no corresponding resource snapshots.
End-of-stage anon/file values cannot identify the peak's composition or cause.

## Bounded upgrade accounting candidate

This slice changes only the fixture, its tests and documentation. Product
bytes, installer arguments, verification reads, original nine stages, parent
limits and cleanup remain unchanged. No cache advice, peak reset, skipped
validation or process moved outside the measured parent is introduced.

- Take a complete sample before the upgrade command, then wait 0.5 seconds
  after each completed observation, and take a final sample after the command.
  Never catch up missed intervals. Require no more than five seconds between
  consecutive metric-read completions, and bounded gaps to the enclosing
  stage timestamps (those existing timestamps precede their metric reads).
  Long observations or gaps fail qualification rather than being hidden.
- Record the same parent inode/device, limits, cumulative peak/events/CPU and
  current/anon/file/kernel counters with read start/end times. These kernel
  files are read separately; they are not an atomic peak decomposition.
- Keep every sample, including B READY while A remains CURRENT, activation,
  service restart and the final command return. READY precedes staging health
  checks; it does not mean staging has already returned.
- Classify only complete allowlisted command templates beneath the measured
  parent: private controller stage/backup/activate, release-runtime ensurepip,
  offline pip/install/check and candidate health. Managed-service roles mean
  exact service-leaf membership. Persist enum roles plus PID/start/parent/UID/
  cgroup identities, never raw argv, argv hashes or environment. Recheck process
  identity after the bounded cmdline read. Unknown/short-lived processes are
  not invented; the worker and observer must actually occur in every sample.
- State observations can lose a process during an actual stop/start race.
  Record that narrow exception without discarding accounting. Wrong effective
  Slice/drop-ins/UID/membership and other hard failures still fail. Bind the
  original live-A/not-ready-B proof to these exact journal states.
- Append each sample once, flush within the parent, and cap the compact private
  0600 JSONL at 2 MiB, 500 samples and 64 KiB per row. Cmdline reads are also
  capped at 64 KiB. Reserve 1 KiB for a failed terminal record; do not discard
  oldest samples or rewrite a growing document on each observation. Preserve
  complete prefix rows after interruption, but truncated/malformed tails,
  missing terminal records and exhausted budgets cannot qualify.
- Record helper self CPU, reaped-child CPU, observation wall duration, sample
  count and journal bytes. They expose measurement costs; they do not measure
  all overhead from sudo, the worker's thread/writes or kernel work separately.
  All that work remains in the original parent total. The existing outside
  coordinator only reads the retained journal after the worker has stopped.

Interpret new cumulative-peak increases between successive observations, with
the roles present near those intervals. Do not assign a surviving cumulative
peak to the currently visible role. Extraction and repeated integrity hashing
share one controller process and may remain indistinguishable. This candidate
does not promise finer causal attribution or a lower peak, and a sampled total
is not a clean performance comparison with the earlier observer. Only one new
exact-head bounded root qualification is planned after local and independent
review; old long/platform results keep their own source identities.

Local candidate validation: Python 3.12.14, 762 tests, 637 passed and 125
environment skips in 59.821 seconds; 48 root-specific tests independently
passed without skips. Documents, compilation, shell/JavaScript syntax and diff
checks passed; protected product paths remain byte-identical to `5f44a2f`.
These results do not qualify actual root/systemd resources or new CI.
