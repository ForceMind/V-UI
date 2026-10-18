# D: passive SOCKS association ownership evidence

This is a diagnostic-only addition after accurate `6a829dc69317a26c6e924b9d05673838e0002bb1`.
That candidate measured C 78 cases / 198 packet leaves passing, D three explicit
close cases passing, and D three same-association idle recoveries failing. The
new observer has not yet supplied runtime evidence. Source reasoning predicts
remote XUDP End eventually closes the device SOCKS control and UDP relay; the
old hop snapshots alone did not measure those two device sockets or control EOF.

## Unchanged experiment

All A/B/C/D/U/V plans, five source patches, existing complete-packet assertions,
original lifecycle verifier and failed lifecycle terminal behavior are frozen.
In D the original 60000-byte exchange is followed by the original six-second
window, measured from its last successful reply, and one original 32768-byte
recovery send with its original four-second target/reply bounds. Idle retains
the same association. Close still closes it and creates exactly its previously
planned replacement. There is no additional packet, association, keepalive,
retry, timeout adjustment, route change or product qualification. The earlier
D failure and its missing-proof top-level KeyError are not repaired or relabelled.
The official-device/overseas and original 19/20 RED results remain separate.

## Observation only

`socks_observation.py` defines a D-only subclass of the frozen SOCKS helper.
Its initializer has exactly one semantic addition, retaining the existing
control object. A normalized AST control compares the full initializer to the
frozen helper; send, receive, close and context methods are inherited unchanged,
including authentication and construction-error cleanup. It never inspects an
ExitStack's private callbacks and does not change the frozen helper bytes.

Each existing hop release sample gets a corresponding new device sample. The
observer also captures after the complete initial exchange, after explicit
application close when applicable, the completed six-second window, immediately
before the original recovery call, after recovery or its failure, and after local
cleanup. All sampling and close occur on the same owner thread. Start/end times
are recorded; passive I/O overhead never moves the original deadline. This is
sampled ordering, not a packet capture or proof of the exact FIN timestamp.

The original `observe_runtime.py` only accepts domestic/overseas roles. It is
unchanged. The new sampler names `device-singbox` explicitly and only reuses its
address and owned `/proc` socket-table parsers. Every sample binds the actual
process PID and birth before/after, exact descriptor-to-inode maps before/after,
owned TCP/UDP rows cross-checked with psutil, and executable stat identity.
Warm and final samples also hash the actual `/proc/PID/exe` bytes and compare to
the role's exact launched binary. Device role, full source identity and raw files
are bound to the existing input identity, role ledger and evidence manifest.

A warm sample must own both the accepted SOCKS control TCP socket, matched by
its reciprocal application-control four-tuple, and the advertised UDP relay,
matched by its exact local tuple. Device-to-domestic TCP is not the SOCKS
control. Subsequent absence refers to the original inode's ownership in the
exact process fd table, not the absence of kernel TIME_WAIT. Duplicates,
read errors or changed PID/birth/executable make the diagnostic inconclusive.
Known descriptor-race samples remain explicitly inconclusive, with strict
kind/field and before/after-fd validation. No sample is discarded and no sampling
retry is added. Stable warm and stable before-recovery samples are mandatory.
The latter can establish the joint EOF/absence fact at recovery time even when
an intermediate sample has a known fd race. This never claims continuous device
absence/no reappearance over the whole window. `first_verified_device_absence_completed`
comes only from a stable sample; intermediate races remain in the raw record and
reported count. The old domestic/overseas full-window gate is unchanged.

PEEK calls Linux libc `recv(fd, 1, MSG_PEEK | MSG_DONTWAIT)` through
`ctypes.CDLL(None, use_errno=True)` with fixed C argument/return types. Calling
Python `socket.recv` on the original three-second-timeout socket could perform
a blocking preselect despite MSG_DONTWAIT; the C call avoids that without
changing timeout or descriptor flags. Only count/errno are saved, never the
peeked byte. Successful calls ignore stale errno. The observer distinguishes:

- `eof`: zero return on the still-owned original local control descriptor
- `would_block`: EAGAIN/EWOULDBLOCK; only no EOF observed at that instant
- `data`: one queued byte remains unconsumed; it may hide a later FIN
- `error`: explicit errno, including reset/EBADF; never inferred EOF
- `local_closed`: the caller already closed its descriptor; never peer EOF

## Independent verification and cleanup

Each D case writes `socks-association-observation.json`, including failed
business cases. `verify_socks_observation.py` checks phase completeness, exact
role/source/manifest binding, original hop-sample correspondence, warm socket
ownership, local fd/tuple stability around PEEK, deadline/recovery ordering,
original association identity, final control/application closure and process
reaping. It reports `peer_eof_and_device_control_relay_absent` only when both
conditions hold before the original recovery. That result neither asserts
RFC violation nor changes the business recovery result. Other actual states
remain separately reported; missing/contradictory observations fail closed.

The coordinator records this diagnostic in its own field, after the existing
resource/provenance safety check. A complete diagnostic cannot promote a failed
D gate. A diagnostic failure cannot allow continuation without the existing
resource gate. Runtime allowlists, manifest requirements and text-only evidence
preparation cover the new JSON; new source/doc files are included automatically
by both existing recursive source-identity functions. No binary is uploaded.

Control tests cover real nonblocking socketpair PEEK, repeated nonconsumption,
EOF/local close/error, unchanged timeout/flags, unsupported platforms, frozen
helper parity and construction cleanup, and an independent real Python child
whose TCP/UDP fd/inode ownership is measured before and after closure, with
explicit process join. Synthetic negative controls exercise role/PID/birth,
source/executable identity, wrong control/relay tuples, duplicate fd/inode,
races/permission errors, time/phase/association tampering, false EOF and cleanup.
They are mechanism controls, not proxy runtime or new D business cases.

The prepared patch is not a runtime pass, merge, release, deployment, fixed-pin
change or an upgrade of an existing user's device. Exact-candidate CI and
independent artifact verification remain required.
