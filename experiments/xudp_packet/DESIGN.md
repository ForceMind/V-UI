# Isolated complete-packet receive experiment (2026-10-18)

This extends the research at `95aef379`, not the product. The earlier original
19/20 gate and official-device 32768 -> 15992 failures remain unchanged. No
product pin, UDP default, released binary, deployment or merge is changed.
Only source patches, tests and evidence text are public. Experimental binaries
exist temporarily on the CI runner and are excluded from uploaded artifacts.

## Roles and questions

The unchanged official-device/official-overseas matrix still runs separately.
The additional fixed-source packet candidate may occupy device, domestic and
overseas roles, always identified by its experimental version and hash. Root
confirmed this isolated role extension on 2026-10-18. No existing user device
or server is upgraded. Sing remains fixed at
`87c33f17688fe3dd80eeabef7c3f7a69152ccd42`, sing-box and sing-vmess retain their
previous fixed commits. The validated route/address patches remain unchanged. After the measured
`a066c767` zero-datagram RED, mux receive adds only the distinction between
OptionData with zero UDP bytes and an absent data frame. TCP empty input stays
a no-op; the bounded writer is unchanged. Raw/AES/ChaCha tests traverse actual
pipe delivery and real XUDP for New/Keep, empty/fallback destinations, all UDP
write entries and subsequent short packets. The patched-sing candidate reruns
the same 24 leaves in race and race+debug; runtime C keeps all original leaves.

A: keep only the device stock, fix downstream packet-copy roles, and measure
fresh/warmed SOCKS input boundaries with both explicit VMess ciphers and VLESS.
B: keep only overseas stock, send small requests producing independently recorded
large replies, and distinguish the reverse receive boundary.
C: all three isolated roles use the fixed-source packet candidate; require exact
uplink and downlink payloads/tuples/source ledgers at the original 32768/60000
thresholds and at legal IPv4 SOCKS boundaries. Never accept truncated prefixes.
The stock observations are explicitly failures of complete-packet delivery; they
are not rewritten as a passing original compatibility gate.

## Packet contract

- `65535` is a conservative ordinary UDP wire admission bound; IPv4 socket UDP
  payload is at most 65507 and a 10-byte IPv4 SOCKS header leaves 65497 application
  bytes. IPv6 jumbograms are not supported. Mux's independent uint16 payload limit
  is not an IP datagram promise.
- Packet-copy buffers reserve front/rear headroom in addition to the read budget.
  Default read budget is 65535, not 16384 minus destination headroom. Explicit
  positive MTU budgets include reader overhead, clamp to 65535, and are not
  silently increased. Invalid negative/oversized option fields are rejected as
  programmer errors before allocation; no untrusted packet controls them.
- Generic fallback `ReadPacket` first receives into a bounded 65536-byte scratch
  (maximum wire + one probe byte). It returns an explicit error for data exceeding
  the admission/caller budget, without exposing a successful prefix. The probe
  byte is never delivered. Exact-fit and zero-length packets are valid.
- POSIX single and Linux/NetBSD/Darwin batch receive inspect truncation flags.
  Any truncated batch is rejected as a whole with an explicit error; its already
  consumed packets are not claimed as delivered. All retained buffers are
  released on truncation, transport error and parent close. Windows keeps its
  explicit message-too-large handling and resets readiness state on terminal errors; WSAEWOULDBLOCK retries retain readiness.
- SOCKS first-packet allocation uses the same full-wire budget and releases on
  error. Connected UDP packet-copy must not fall back to stream `unix.Read`;
  bare *net.UDPConn extended read/wait adapters preserve packet boundaries and
  short-buffer errors. Arbitrary wrappers hiding the UDP type are not claimed
  repaired; actual candidate connected roles and wrapper tests are identified. Public net.PacketConn `ReadFrom` semantics are unchanged.
- Ordinary packet-copy error ends that association/copy; no arbitrary parent
  mux shutdown is introduced. Full buffers, close, errors and following packets
  are tested separately. Batch-size options are bounded to the fixed maximum 64.

## Memory and compatibility

The fixed allocator rounds a default 65535-byte packet with VMess/XUDP headroom
(286 front + 96 rear) to 73728 backing bytes. A default 64-packet batch is at most
4,718,592 backing bytes for those options, plus bounded metadata and at most one
65536 fallback scratch for an active fallback call. Each option field is at most
65535, so general buffer storage is at most 196605 bytes; tested real options are
much smaller (general batch ceiling 12,582,720 backing bytes before metadata).
A default first-packet allocation uses the 65536-byte pool class.
CachedPacketConn.ReadPacket itself retains its historical short-buffer behavior;
this experiment tests CopyPacket/ReadCachedPacket whole-packet first-packet
transfer and does not claim all N.PacketReader implementations are repaired.
Buffers kept over EAGAIN remain part of that batch budget, not a
per-attempt allocation. Returned/caller-owned packets and the previous mux queue
budget are accounted separately; there is no claim of whole-process RSS or a
single global cap over arbitrary concurrent associations. The throughput/memory
tradeoff affects packet-copy users beyond SOCKS and requires review.

Linux runtime/race/debug are the actual execution target. Affected Darwin and
Windows source must cross-compile; this does not qualify their runtime behavior.
Tests enumerate exact leaves, reject skips/missing tests, track live allocator
objects, and join all test-owned workers. First/warmed, both directions, exact
fit/overbudget/zero packet/recovery, syscall/fallback/batch, close/error and
unmodified controls are separately recorded. Source hashes, ordered patches,
fixed normalized module graph and compiler digest bind the runtime to the tests.

## Independent encrypted backpressure gate

The unchanged shared writer is separately tested through actual fixed encrypted
Client CommandMux -> Service for AES and ChaCha, padding and authenticated
lengths. A warmup completes the handshake. A controlled underlying pipe then
consumes a ciphertext prefix in the next length/body segment and stops reading.
Queued/in-flight cancellation must return before reading is restored, with the
parent alive and exactly one bounded writer. Restoring the exact captured prefix
to the real decoder must yield the complete frame, one End, correct TCP/UDP
siblings and new association, then all workers join after real parent close.
Parent transport failure and completion-before-close have separate assertions.
This replaces no earlier route snapshot or normal-flow encrypted test.
