# Candidate-device SOCKS owner / joined XUDP child recovery

Date: 2026-10-18. Revision: reconstructed v2, for renewed independent comparison after filesystem rollback.

## 0. Reconstruction status and authority

The previously frozen design had SHA256 `2416b5a8cea469ec813464ff2f1af00a92cdbe4000d9da10c1fd44fb75b449cd`. Its file disappeared when the execution workspace reverted to an older filesystem snapshot. The retained conversation contains substantial original text and the full technical contract, but not enough to establish byte-for-byte reconstruction. **This is a new document with a new digest, not a recovery of those exact bytes.** Never assign the old digest to this file or to reconstructed source patches.

This revision preserves the frozen source-level contract and incorporates the subsequent reviewer hardening recorded in `vui-mux-review-20261017/OWNER_REVIEW_RECOVERY_20261018.md`. It distinguishes those historical review findings from fresh verification. It is a complete standalone implementation/review contract, not a claim that lost code, tests or artifacts have been recovered or have passed again. The design author writes only this independent scratch deliverable. The parent alone owns the development checkout and remote writes.

Restored read-only baseline checkout: `vui-owner-recovery-20261018`, locally checked at `6e73827babcab15356e2b13f48b0eb3111bb2774`, tree `a53e0fc95bef8f5087cb274d1e7c85da11b839c1`. The checked checkout was clean before this reconstruction. Fixed dependency identities remain:

- sing `87c33f17688fe3dd80eeabef7c3f7a69152ccd42`.
- sing-box `af6e64c3b69e6132ebaee0e1a3d24e93903f6709`.
- sing-vmess `31ec11e8790c48a4bf11711806a61e929ee6b89d`.

Historical baseline run: https://github.com/ForceMind/V-UI/actions/runs/39134100708, attempt 1. Earlier stage reference: https://github.com/ForceMind/V-UI/pull/48#issuecomment-6250579779. The exact 6e artifacts and their hashes are preserved in the reviewer recovery handoff. They were independently checked before rollback; newly downloaded artifacts require fresh archive/member/hash checks. The prior 3fa controls attempt failed for missing psutil before runtime and remains a failure, not device EOF evidence.

## 1. Recommendation and qualification boundary

Put the persistent association owner in **sing/protocol/socks**, at the actual UDP ASSOCIATE creation point. Opt into it only from the experimental sing-box mixed inbound. Give each routed generation a queue-backed packet connection that cannot close, read, unwrap to, or take ownership of the real SOCKS relay or TCP control. Use a **joined private-transport route child**, not the current first-copy callback, as the generation-completion boundary.

The minimum candidate supports the frozen device path: local loopback IPv4 SOCKS UDP ASSOCIATE over an actual raw TCP control connection, literal IPv4 application destination, one selected outbound tag, direct TCP to a fixed literal-IPv4 upstream, certificate-verified ordinary Go TLS, XUDP, and VMess AES-128-GCM / ChaCha20-Poly1305 with alter_id=0 and the existing padding/authenticated-length options, or VLESS with empty flow. Unknown wrappers, shared multiplexDialer, dialer detours, V2Ray transports, REALITY, uTLS, kernel TLS, TFO, multipath, parallel/fallback dial paths, network namespaces, domain/resolver policy, TUN, inbound detours and sniff/rewrite/hijack actions do not qualify for this opt-in capability. All unopted paths preserve their old behavior; these exclusions are not global refusals of existing features.

The private transport is disposable. A complete canonical no-error-bit End retires that generation's private TLS/TCP, all synchronous protocol I/O, both copies, its route invocation and buffers. Only a subsequent new application datagram may cause a fresh private connection. The device's actual SOCKS TCP control and UDP relay remain owned by the original owner. The server's existing 2-second End and resource release stay unchanged. No dormant server session, keepalive, synthetic empty packet, replay or automatic retry is permitted. Independent shared raw-mux/sibling/resource gates remain mandatory and separate.

### Verified baseline diagnosis, not recovery qualification

At exact 6e, all three idle leaves first observed EAGAIN on the same application's Linux MSG_PEEK control fd. They then observed actual EOF and stable absence of the device's original control/relay inodes, approximately 2.129449176 seconds for AES, 2.124861106 seconds for ChaCha, and 2.114730999 seconds for VLESS after the last reply. Before recovery after the existing 6-second window, the original local fd/tuple still reported EOF and original device inodes remained absent. Each leaf made its one 32768-byte recovery send; target delivery was zero. Original D remains three explicit-close successes and three same-association idle failures.

Original packet C has 78 cases and 198 leaves: 195 complete request/reply checks and three local EMSGSIZE checks. Preserve every original RED, stock-device/stock-overseas characterization, C complete/zero-packet assertion and `product_qualified=false`. The passive observer does not prove continuous whole-window OS ownership and has inconclusive child-close samples; preserve its explicit false whole-window-continuity flag. None of these baseline facts prove the proposed owner works.

## 2. Why this insertion layer is the minimum defensible choice

| Layer | Exact fixed-source constraint | Decision |
|---|---|---|
| sing SOCKS owner | `protocol/socks/handshake.go` creates the UDP relay/control pair and invokes its packet handler once. Existing `packet.go` Close owns both. The handshake sees the actual TCP peer and UDP relay before adapters hide sender identity. | Select the explicit opt-in branch here; share auth/handshake parsing and preserve legacy paths. |
| mixed-only wrapper around an existing packet conn | `bufio/serverPacketConn.ReadBuffer` updates its reply peer from each receive. The mixed handler receives logical target addresses rather than every actual UDP sender, after existing timer/cached-first-packet wrapping. | mixed supplies activation and policy; it cannot safely recover raw ownership from this wrapper alone. |
| XUDP-only restart | XUDP has one requestWritten flag, session ID 0 and one transport. Reusing that transport would require session generations/tombstones, stale-frame isolation and shared-writer retirement. | Reject. XUDP only gains opted-in precise terminal classification; each generation gets a new private connection. |
| udpnat2 substitution | A 1024-entry cache and 64-frame channel are not this budget; NewPacket silently drops on full queue. | Reject the owner/key/queue policy; do not copy silent overflow. |

Fixed-source facts that constrain the design:

1. `sing/common/bufio/copy.go`'s packet loop directly returns ReadPacket errors, including EOF. Stream CopyExtended's EOF-to-nil normalization does not apply. A plain EOF still cannot distinguish a complete XUDP End from transport EOF.
2. `sing-box/route/conn.go:packetConnectionCopy` invokes onClose on the first completed copy and closes endpoints. That callback is not a two-copy or protocol-worker join.
3. The bounded shared **server** mux writer can retain an already-started frame after both calling copies return. Copy completion is therefore not a universal transport/writer fence. The new candidate excludes shared client parents and admits only private synchronous protocol transports. Existing server writer bounds remain independently tested.
4. `mixed.NewConnection` calls CloseOnHandshakeFailure even after newConnection returns nil, which can invoke its parent onClose. The owner must run synchronously in the existing connection-handler invocation and return only after its own join; it cannot launch a background owner then return success early.
5. CopyPacket may unwrap count/reader/writer adapters, consume cached packets, select read-wait/batch or other optimized paths. The owner child must expose no escape hatch that bypasses its generation, target or close guards. Tests must cover these mechanisms, not only ordinary ReadPacket.
6. Server `serverMuxPacketConn.Close` calls `closeStream(..., true, nil)`, and its handler callback is nil. A remote route rejection may therefore produce the same canonical flags=0 End as idle. Wire End is an opaque normal terminator, not proof of idle or successful delivery.

## 3. Explicit activation, immutable policy and admission limits

Add a candidate-only optional object to `sing-box/option/simple.go`'s HTTPMixedInboundOptions:

`experimental_socks_udp_idle_recovery: { outbound: "DOMESTIC_ONLY" }`

Absence chooses the exact legacy path. Do not add the object to V-UI product import/export/UI schemas. Only the candidate-device mixed role receives it. Validate its selected tag and the narrow local IPv4 inlet shape. For opted UDP ASSOCIATE, require the actual raw TCP control capability before acquiring an owner; a TLS/unknown control wrapper with potentially blocking Close does not qualify. Do not make that predicate reject legacy CONNECT, SOCKS4, HTTP or unopted associations.

For each generation build fresh immutable route metadata from the original owner facts, rerun applicable route/reject policy for the original target, require the selected outbound to equal the configured tag, and demand its concrete private-lease capability. Revalidate metadata after prepareMatchMetadata. Never reuse route-mutated InboundContext or a previous positive route decision. Reject unsupported action semantics before applying them, including route UDPTimeout override, domain unmapping, TLS fragmentation/spoof options, destination rewrite, sniff, inbound detour and DNS actions the candidate cannot faithfully preserve. The normal new XUDP request exercises downstream policy and applicable DNS/private-address gates again. A domain/DNS policy outside this literal-IPv4 capability fails closed; it is not skipped. No DIRECT fallback, including through a default outbound.

Fixed experiment constants:

- Eight owners per opted mixed inbound. Acquire a slot before allocating a UDP relay or returning SOCKS success. On saturation send SOCKS ReplyCodeFailure, close control, allocate no owner worker and evict nobody. Release only after full owner join and the final observer callback has returned.
- One queued complete application datagram, one raw construction/read slot, one upload actually in flight, and one downlink reply buffer. No per-packet worker, unbounded queue, batch ingestion or hidden retry slot.
- At most one generation among Admitting, Active and Retiring. No next dial or retired-child collection until the prior generation has completely joined.
- At most six owner-created workers while active: existing mixed connection handler as supervisor, one raw UDP reader, one control reader, one generation-handler invocation, two route copies. Dormant keeps the first three. Standard library/runtime internals are separate; this is not a whole-process goroutine count.
- Admission ceiling three seconds, additionally bounded by owner cancellation. It covers route/dial/TLS/outer-protocol construction only, not an active child's lifetime. The supervisor enforces it by aborting registered raw TCP, not merely canceling context.
- Relay reply write ceiling one second from that reply's beginning, additionally bounded by earlier child deadline and owner cancellation. Repeated SetWriteDeadline cannot extend this absolute ceiling. Retirement wakes a blocked relay reply immediately and prevents a later generation until that write has returned.
- Owner idle timer starts at successful ASSOCIATE response. Keep mixed's configured UDPTimeout or fixed five-minute default. Refresh only accepted legitimate-source application activity or successful responses; rejected sources, internal events, dialing and End do not refresh it. This does not change the domestic two-second child timeout.

### Source and destination identity

Bind source IP from the authenticated SOCKS TCP peer. Honor a valid nonzero port hint in UDP ASSOCIATE; otherwise select the UDP source port from the first **valid** packet from that peer IP. Before port binding, malformed packets from an eligible IP do not bind, refresh or kill the owner. Once bound, malformed packets from that bound source are terminal. A wrong IP/port is explicitly rejected and counted, cannot rebind the reply peer, and neither refreshes nor kills the legitimate owner. Require RSV=0, FRAG=0 and IPv4 ATYP; domains, fragments and IPv6 are outside the candidate.

Freeze the first allowed application target and preserve the established **initial-target remap**. Later valid IPv4 SOCKS target requests are recorded as requested, while the actual target remains the original before and after idle. Canonicalize each queued frame and each generation's route metadata to that original target. This is neither changed-target rejection nor per-packet rerouting. Never use a changed requested target for re-admission. Tests must show actual client route metadata, server-decoded target and unique payload delivery, not only self-reported owner events. If preservation proves impossible, stop and report; do not silently change semantics.

## 4. Exact source and API proposal

The following are the candidate APIs and source deltas. Names may be reconciled with restored implementation only through explicit review; no code may compile against an undefined N interface in an unpatched dependency.

### 4.1 sing: persistent owner and child

1. `protocol/socks/handshake.go`: retain HandleConnectionEx as the legacy entrypoint. Add HandleConnectionExWithUDPAssociation with the same existing arguments plus explicit `*UDPAssociationOptions`; share parser logic internally. In the opted UDP branch validate raw inlet, reserve slot, allocate raw UDP, send normal success, then call `udpAssociation.Run()` synchronously. Close an allocated relay if response writing fails. Do not construct legacy AssociatePacketConn, canceler.PacketConn or CachedPacketConn in this branch. Existing TCP/SOCKS4/HTTP/unopted paths stay unchanged.
2. New `protocol/socks/association.go`: the owner, single complete-packet raw read loop, immutable source/original target, single pending slot, state machine, idle/admission clocks, control watcher and synchronous supervisor/join. The sole control reader uses the handshake's existing buffered reader so buffered unexpected bytes are not lost. The raw UDP reader uses the previously patched complete-packet `bufio.NewPacketConn(rawUDP).ReadPacket`; parse only complete wire packets.
3. New `protocol/socks/association_packet.go`: generation-local `N.PacketConn` implements ReadPacket, WritePacket, Close, LocalAddr and local deadline methods. It cannot close physical owner sockets. It exposes no physical conn, Upstream/UpstreamReader/UpstreamWriter, replaceable reader/writer, cached packet, batch, syscall or direct-handler interface. ReaderOverhead=0; FrontHeadroom=10 for validated IPv4 SOCKS reply headers. It copies a complete queued packet only after checking caller FreeLen; short buffers return `N.ErrPacketTooLarge`, never a successful prefix. WritePacket owns/releases its supplied buffer on every outcome and checks generation before parsing/validating reply data or touching the real relay. A stale malformed reply cannot terminate a newer generation.
4. New `common/network/packet_session.go`: distinct `ErrPacketSessionEnded`, not aliased to io.EOF, net.ErrClosed or context.Canceled. Shared narrow lifecycle interface:
   - `AttachPacketSessionAbort(func() error) error`.
   - `PacketSessionAdmitted() error`.
   - `BeginPacketSessionRetire(error)`.
   - `PacketSessionWriteFinished(error)`.

Attach atomically installs the sole private raw abort or rejects and invokes it immediately if already closing. Admitted linearizes admission against deadline/owner Close, stops admission timing and enters Active only while current and open. BeginRetire records the first cause and disables that generation's reads/replies; it is not permission for a new child. WriteFinished acknowledges the sole upload lease exactly once, including error; late acknowledgements are generation-local. The route's selected-outbound policy remains in a typed sing-box context, not mutable globals or user-controlled packet metadata.

`UDPAssociationOptions` contains `Acquire func() (release func(), ok bool)` supplied by mixed's limiter. Existing handler/source/context/timeout arguments and fixed constants supply the remaining runtime inputs. A bounded optional synchronous Observe callback may be supplied for exact owner lifecycle evidence as specified in §7; it must not introduce a queue or worker.

### 4.2 Explicit post-join cache finalization

5. `sing/common/bufio/chunk.go`: add idempotent `ChunkReader.ReleaseCacheAfterJoin()`, explicitly **not Close and not io.Closer**. Release retained read cache only after the sole reader is joined. Preserve the legacy io.Closer method set. Existing rawClientConn.Close already calls common.Close(c.Conn, c.reader), while legacy callers do not guarantee that Read is joined; globally adding ChunkReader.Close would create a concurrent-release hazard and affect service/Vision too.
6. `sing-vmess/client.go`: add `rawClientConn.ReleasePacketSessionReadCacheAfterJoin()` for the known fixed reader stack. Only the opted-in private lease finalizer calls it after all I/O has joined. Do not walk arbitrary Upstream chains or alter rawClientConn.Close timing. VLESS has no corresponding VMess chunk cache. Legacy non-io.Closer behavior is a required regression test.

### 4.3 sing-vmess: explicit wire terminal mode

7. `sing-vmess/xudp.go`: add an opt-in terminal-reporting mode set before publishing the candidate XUDPConn. Default parser behavior remains historic, including legacy End→io.EOF. Only a complete canonical End with header length 4, wire session ID 0 and flags 0 yields `N.ErrPacketSessionEnded`. Validate flags and complete framing before positive classification; the fixed reader currently branches on StatusEnd before checking OptionError. Wrong SID, End+OptionError, End+data, illegal length/flags, partial header, raw EOF, truncation, cipher/authentication failures are terminal errors.

Bound header arithmetic and reads before allocating; use an iterative no-data control loop instead of recursion. Keep/KeepAlive must obey permitted protocol shapes; they do not create an empty application datagram. Valid OptionData of length zero remains a real packet. No session-ID reuse or in-stream restart is added: each generation uses ID 0 on its own new connection.

### 4.4 sing-box: policy, exclusive lease and effective dialer

8. `option/simple.go`, `protocol/mixed/inbound.go`: parse the optional object, construct the eight-slot limiter, select the owner handshake only when enabled, and inject immutable selected-outbound context and bounded observation. Keep owner Run in the original NewConnection invocation. The original parent onClose runs only after owner cleanup and return; child callbacks are separate. Snapshot auth identity, inbound tag/type, source, relay and original destination into a fresh metadata value per generation.
9. New `adapter/packet_session.go`: immutable context helpers and narrow lease capability:
   - `OwnedPacketSessionDialer.OpenPacketSession(ctx, target, attachAbort) (OwnedPacketSessionLease, error)`.
   - Lease `PacketConn() N.PacketConn`, `AbortTransport() error`, and explicit `Finalize()` after join (returning/reporting any cleanup failure).

A lease proves concrete exclusive ownership; interface presence or `options.Detour == ""` alone does not. It is not offered by arbitrary embedded/wrapped dialers or shared mux. Abort must close the actual private socket, not first wait for TLS close-notify or a protocol write lock. Finalize must be idempotent and may touch protocol reader state only after it is quiescent. A once-close wrapper around the known raw connection is also passed into TLS so subsequent upper-layer Close cannot double-run tracked/counter cleanup.
10. `protocol/vmess/outbound.go`, `protocol/vless/outbound.go`: implement the capability only for the narrow fixed direct/TLS/XUDP form. Validate config and effective runtime objects. Register raw abort immediately after the one TCP dial succeeds, before TLS or outer constructor writes. TLS verification is mandatory; use the existing ordinary Go TLS path and allowed policy, no insecure bypass. VMess uses fixed `Client.DialXUDPPacketConn` with nil outer-handshake payload so the synchronous writer is initialized before copies, and no early whole-payload buffer is retained. VLESS uses fixed `Client.DialEarlyXUDPPacketConn` with empty flow because fixed non-early DialXUDPPacketConn uses CommandTCP rather than CommandMux. **Despite its name, this fixed Early method synchronously calls remoteConn.Write(nil); it is not deferred until first copy.** Do not modify the fixed vless/client.go command path to broaden scope. Turn on precise End mode before lease publication. No application data is consumed during constructor admission.
11. New `common/dialer/packet_session.go`: narrow `(*DefaultDialer).DialPacketSessionTCP(ctx, literalIPv4)` validates the actual effective constructed dialer. Require literal IPv4 upstream, TFO off, multipath off, empty netns, networkStrategy nil, defaultNetworkStrategy false, no interface fallback/parallel resolver/detour or inherited NetworkManager strategy. Inspect constructed defaults, not only JSON. Use the existing underlying net.Dialer synchronously with tcp4 and the bounded context, preserving already permitted per-socket policy. Do not substitute an unrelated bare net.Dialer to bypass unsupported configuration. Known trackedConn and CounterConn wrappers created by the fixed trackConn path may be explicitly recognized only with proof they own this one socket and add no I/O worker; arbitrary Upstream unwrapping is forbidden. Abort/upper Close must be once-only for tracking/power counters. If exclusive socket or bounded worker ownership cannot be proven, opt-in admission fails.

### 4.5 sing-box: joined route path

12. `route/route.go`: detect explicit owner lifecycle/context before selecting unsupported handoff/fast paths. Run normal fresh route policy, rejecting unsupported actions before effects; recheck metadata after matching/preparation. Require unchanged original target and configured outbound. Local rejection immediately closes the owner via the generation-specific callback and is never re-admitted. Do not globally alter legacy routing.
13. `route/conn.go`: select a new joined packet helper only for the opted lifecycle + policy contract. Legacy `NewPacketConnection` and its first-copy semantics remain unchanged for everyone else. Use lease.PacketConn directly rather than coercing it through a generic net.PacketConn path that adds storage or bypasses lifecycle. Do not wrap the owner in a route canceler or attach the admission timeout to active copies.
14. New `route/packet_session.go`: acquire the narrow lease and own both copy invocations. Call Admitted before starting them; failure after a late admission decision closes/finalizes the lease and starts zero copies. First terminal result invokes BeginRetire, cancels/wakes the child and aborts raw transport. Both copies must return before touching read-cache finalizers or publishing completion. Close upper wrappers only after raw abort has unblocked their synchronous I/O. Record upload acknowledgement through PacketSessionWriteFinished before it can be treated as safely finished. Suppress only the expected sibling-cancellation result associated with an already-recorded canonical terminator; a genuine local error cannot be laundered into a recoverable End.

The route helper's callback is once-only and occurs after complete joined cleanup. A finalizer error is terminal and must notify the owner immediately, but owner slot release still waits for the generation invocation's final return marker. Cancel, abort, failure notification and owned cleanup must precede the invocation's workers.Done/returned publication; there must be no owned work after the join signal. A callback is not a substitute for invocation return, and a returned invocation without the required callback/completion record is a lifecycle failure.

These grouped changes touch exactly 16 production paths; the manifest in §10 enumerates them individually.

## 5. State machine, generation fence and close ordering

Owner states: AwaitFirst, Admitting(g), Active(g), Retiring(g), Dormant; terminal Closing, Closed. Store a checked monotonically increasing generation ID and generation object identity. Never wrap IDs or reuse owner identity. Old callbacks, replies or write acknowledgements must not mutate current state through a reused integer or pointer.

| State/event | Required action and next state |
|---|---|
| Successful ASSOCIATE | Own the same control and relay, start owner timer/readers, AwaitFirst. |
| First valid datagram in AwaitFirst or Dormant | Assign packet sequence once, retain one complete frame, start one generation invocation with fresh original-target metadata, Admitting(g). |
| Admission success wins lock | Stop admission timer, publish lease and Active(g), then start two copies. |
| Admission error/deadline/owner cancellation | Closing; abort raw if registered, join any late result/invocation, release pending data with explicit failure; no retry or fallback. |
| Active receives valid datagram | Canonicalize original target and enqueue if the single pending slot is free; overflow is explicit terminal overload. |
| Canonical End and no old queued/in-flight upload | BeginRetire fixes the boundary, disables g reads/replies, aborts private transport and joins all of g; only then Dormant. |
| End while an old pending or unacknowledged upload exists | Terminal uncertainty, Closing. Never replay or silently migrate that data. |
| New datagram after retire linearization | May occupy the one new pending slot, explicitly assigned after that boundary; it cannot start g+1 until g has fully joined. A second pending frame is fatal overload. |
| Other wire/local error, owner timeout, control EOF/unexpected byte or explicit Close | Closing, close owner sockets, cancel/abort generation, join all resources, then Closed. |

Every accepted datagram has one sequence and at most one transmission attempt. Receiving its bytes into the owner does not imply remote delivery. A failed or uncertain datagram is never retried, including after a canonical End. Frame handoff/upload acknowledgement and End are ordered under the state lock; a write still unacknowledged when End wins is conservatively uncertain even if the transport might have delivered it. Concurrency tests must exercise both orders. Construction completion and packet admission must be explicitly linearized so no pre-boundary queued frame is silently reclassified as a fresh recovery trigger.

The sole raw reader owns construction, transfers a complete buffer to the single pending slot, then allocates/uses its next construction slot. The sole upload copy takes a frame once and holds it until WritePacket has returned and the acknowledgement is recorded. A canceled child cannot steal a next-generation queued frame. Owner overload releases all retained frames during terminal cleanup with an explicit reason; there is no default/select drop path. Rejected wrong-source packets are a separate declared security rejection, not silent accepted-traffic loss.

### Complete retirement fence

All of the following must hold before g+1 admission, Dormant completion, or terminal slot release as applicable:

1. g is atomically disabled for new reads/replies and its context canceled.
2. Its actual registered raw TCP is aborted; late Attach immediately aborts instead of publishing.
3. Both route copy callers have returned; no reply write or upload write remains in flight.
4. All synchronous protocol reader/writer calls reachable from those copies or constructor have returned. No hidden shared-parent writer is admitted by the capability.
5. Upper Close and explicit post-join read-cache Finalize have completed; errors are retained.
6. The once-only child completion callback has run and the generation-handler invocation has published its final return after all its owned work.
7. No pending frame remains owned by g, no stale callback can claim the new generation, and resource counters are at the permitted baseline.

A join that does not complete leaves the owner Closing and retains its slot. It never permits another generation or claims successful resource reclamation. Do not add a detached reaper or accumulate retiring children each cycle. Unknown/private-ownership failure is refused before copies; never close a shared parent to satisfy this fence and damage siblings.

### Contexts and deadlines

Owner context derives from the original inbound context. Each child gets a fresh context derived independently from owner, never from canceled g-1. A separate three-second admission context is used for bounded dial/TLS work and stopped after successful admission; active copies must not inherit its expired deadline. The existing owner supervisor enforces the same absolute admission deadline, calls terminal close, cancels the child and invokes registered raw abort. Merely canceling ctx does not unblock fixed synchronous VMess/VLESS outer Write.

`PacketSessionAdmitted`, deadline expiry and owner Close serialize under one state lock. Deadline/Close winning means a late lease is aborted/finalized and its invocation joined, with zero copies started. Admitted winning means the admission timer is stopped and the active child survives that former deadline. No extra timeout/close goroutine. The supervisor still watches original owner cancellation. Raw TCP must be registered before a possibly blocking TLS/outer write, including when cancellation happens between dial return and Attach.

Physical owner Close is idempotent. It closes raw control and relay to wake their unique readers and a blocked UDP reply, cancels any child and aborts its raw transport. Do not hold the owner mutex while performing socket I/O, callbacks, observers, blocking joins or finalizers. The supervisor must not wait for itself. Its handler return is the final parent-owned completion boundary. Local deadline setters are generation-scoped; late old setters cannot change a current owner's unrelated socket deadline or extend the absolute reply budget.

## 6. Exact complete-packet resource budget

Use full datagram sizes, headroom and pool allocation classes, not just application payload lengths. For the admitted IPv4 wire path, maximum UDP payload is 65507 bytes, SOCKS IPv4 header is 10 bytes, and application payload maximum is 65497. Zero is valid. The existing complete-receive mechanism and its EMSGSIZE behavior remain intact.

| Concurrent storage | Requested capacity / overhead | Maximum backing charge |
|---|---|---:|
| Raw construction/read | 65535-byte complete wire budget | 65536 |
| Existing complete-receive fallback/probe scratch | Complete packet, simultaneously live with construction when used | 65536 |
| One pending frame | Transfer the complete raw backing after parse; reserve before copy/retention | 65536 |
| One upload copy/in-flight packet | 65535 + at most 286 front + 96 rear = 65917 | 73728 |
| One downlink/reply packet | 65535 + 10 front = 65545 | 73728 |
| **Per-owner core datagram backing cap** | 3 × 65536 + 2 × 73728 | **344064** |
| **Eight-owner core cap** | 8 × 344064 | **2752512** |

There are at most three distinct ingress datagrams: construction, pending and upload. The receive fallback scratch is a copy/probe of construction, not another admission slot. An implementation with extra overlapping copies must either eliminate them or obtain review for a revised bound; it cannot count only logical queue length. Reserve budget before allocation/retention, cover the pending→upload overlap, release exactly once, and test failure paths under allocator accounting. A full packet's read budget cannot shrink because front/rear headroom was subtracted from an old small buffer. Unknown or excessive negotiated headroom fails capability admission. Direct lease N.PacketConn use avoids adding an unaccounted generic packet adapter scratch.

Separately bounded protocol memory includes VMess ChunkReader's 16384-byte cache, crypto chunk storage around the fixed 15000-byte chunk plus bounded header/tag/padding, small outer request storage, and bounded response header (at most 65535 + 16 requested, 73728 pool class). The outer handshake uses nil data, so no early whole-application-payload BufferedWriter is retained. Any final implementation must enumerate its actual concurrent protocol buffers and demonstrate joined release. Standard Go TLS/runtime/kernel buffers are separate; 344064 is not a total RSS or process memory claim. Metadata/event records are fixed small bounded state. Per-owner process workers, dial attempts, sockets and protocol caches must not grow across generations.

## 7. Terminal semantics and bounded lifecycle observation

| Observed cause | Owner treatment |
|---|---|
| Complete canonical length=4 / SID=0 / flags=0 End, no old pending/in-flight packet | Retire and join; next new application packet may re-admit original target/policy. |
| Same End while old queued/unacknowledged upload remains | Terminal uncertainty; no replay. |
| Remote route rejection that produces canonical flags=0 End | Indistinguishable at the client from the preceding normal terminator. A later new packet may cause one fresh admission; this is not recognition of idle, success or remote reject. |
| Locally visible route reject, changed selected outbound, unsupported capability, dial/TLS/constructor error, admission deadline | Terminal owner failure; release pending data, no retry and no DIRECT. |
| Explicit wire OptionError, malformed/partial End, wrong SID/length/flags, truncation, authentication error or transport EOF | Terminal owner failure, never typed as recoverable End. |
| Control EOF/unexpected byte, owner Close/context/idle timeout, accepted-source malformed packet or queue overload | Terminal owner failure with full join. |
| Expected second-copy cancellation after already recorded canonical End | Cleanup consequence only; retain original cause. Any genuinely independent error/finalizer failure remains terminal. |

Typed End must survive all permitted count/handshake/copy wrappers without being normalized to success or bare EOF. Opaque remote End is the frozen semantic limit; do not invent a remote idle reason or claim successful packet delivery from it. Re-admission is triggered by a fresh datagram only, and executes normal target/policy gates again. One application packet cannot automatically make multiple attempts.

Optional Observe is synchronous, called outside owner locks, and transports only bounded enum/count/address/identity fields, never payloads or secrets. It introduces no asynchronous event queue or worker. Owner and event sequence IDs are nonreused and checked against overflow. Sequence allocation, not callback arrival time, is the order authority if delivery from different workers is concurrent. Consumers must not infer order from log arrival alone. The final Closed event is emitted only after all owned work has joined, and the observer must return before releasing the slot. Reserve seven event IDs before admitting a generation: the last complete generation needs five nonterminal and two terminal lifecycle records. Refuse new work before overflow rather than wrap, omit terminal records or reuse IDs. Callback implementations must return promptly and must not recursively wait for their own owner completion.

Lifecycle evidence supports the real ownership invariant but does not replace kernel tuple/inode observations or actual route/server payload evidence. A fresh socket with a reused port, a self-reported target, an absent EOF sample or an inconclusive sample is not proof of unchanged physical association.

## 8. Required strict tests

All tests use false credentials, local endpoints, temporary CA and runner-local binaries. Every launched test goroutine has a join record. No passing test can leave its callers/services alive, restore an allocator early or depend on sleeps as proof that a write has entered/blocking state.

### 8.1 Owner and whole-packet contract

- Actual owner/handshake has exactly one raw reader and one control reader; first and recovered generations receive their first new packet exactly once for zero, 1, 32768, 60000 and 65497 bytes. Distinguish local oversize errors from successful complete packet transport.
- Eight-slot saturation: ninth association fails before relay/worker allocation; no eviction. Closing one fully frees one slot. Stuck admission/Closing retains its slot. Raw inlet restriction affects only explicit candidate UDP.
- Complete backing accounting during raw construction, receive fallback, pending handoff, upload overlap, blocked writer and reply. Single-slot overflow is terminal/countable and releases all frames; no silent accepted-packet drop branch.
- Source binding and valid port hint; before-bind malformed does not bind/refresh/kill; after-bind malformed terminates. Wrong IP/port never rebinds or refreshes. Fragments/domains/IPv6 rejected within the candidate; unopted behavior unchanged.
- Initial-target remap before and after idle, with separate requested/actual ledgers and actual original-target metadata/wire/payload checks; never DIRECT or a new target on re-admission.
- Short caller buffer yields no successful prefix. Max headroom preserves complete receive size. Empty OptionData stays distinct from internal control/no-data.
- Concurrent packets create one generation only. Deterministic owner Close versus first packet/dial return/Attach/Admitted/callback/invocation-return permutations. Late raw/lease closes exactly once. Missing callback or unjoined invocation cannot authorize g+1.
- Old queued or in-flight upload crossing End is explicit terminal uncertainty. Test upload acknowledgement before End and End before acknowledgement with barriers; never resend the same sequence.
- New packet while Retiring may occupy one slot after the boundary but cannot dial before full join. Stale callback/reply/ack/deadline cannot affect a later generation. Stale malformed reply is rejected before parsing can kill the new child.
- Owner idle covers AwaitFirst, Dormant, Admitting and Active; internal/hostile activity cannot extend it. Blocked relay reply wakes on control EOF/End/deadline; repeated SetWriteDeadline cannot exceed one-second absolute ceiling; no lock inversion or stale write after next generation.
- Counter/sequence boundaries including MaxUint64 and final seven-event reservation; no identity wrap. Observe runs outside lock, Closed only after joins, and slot releases only after observer return.
- Hundreds of actual-owner fake-transport cycles demonstrate owner state/storage invariants with that exact scope; they do not alone prove real TLS/route behavior.

### 8.2 Parser and classification

- Canonical six-byte wire End produces the distinct cause only in opted mode; unopted mode keeps legacy behavior.
- End+OptionError, wrong SID, header lengths 0/3/5/65535, illegal flags, End+data, EOF at every header byte, body truncation, transport EOF and authentication error never qualify.
- Empty OptionData then nonempty; Keep/KeepAlive are not application packets; long control sequences use bounded iterative processing rather than recursion.
- Exact error survives ordinary CopyPacket and allowed wrappers. Negative tests exercise attempted Upstream/count/cached/read-wait/batch/syscall/writer escape paths; no close/target/lifecycle bypass.
- **Actual remote Router rejection producing canonical End**: record that rejection; read actual per-generation unique payload and original target at server admission before rejecting; assert zero extra packets/application delivery as appropriate. Explicitly demonstrate client ambiguity, no replay, and only a later fresh packet causing one new admission. Gate reject on actual upload acknowledgement so a race is not mistaken for a recoverable packet. Local rejection and explicit wire error stay terminal.

### 8.3 Real route, protocol, lease and cancellation

- Actual fixed VMess AES/ChaCha CommandMux Client→Service and VLESS CommandMux, with existing padding/authenticated lengths and full packet cases. Assert outer command bytes and parsed End cause; a stub that returns the sentinel is insufficient.
- A private encrypted write paused after a ciphertext prefix: owner Close or End must raw-abort and join both copies and protocol calls, with no hidden continuing writer or replay. Confirm cache and allocator baseline after joined finalization.
- Real TCP/TLS handshake stall, late dial result, owner cancellation before/after raw Attach, tracked/counter once-close and unsupported effective dial policies. Context cancellation alone is not a blocked-I/O proof.
- Actual fixed VMess and VLESS constructor outer Write blocked deterministically over net.Pipe or an equivalent controlled connection, with entered/nonreturned-write or stack evidence and actual raw abort. A TCP peer merely pausing reads may not block the small header because kernel buffers can accept it; do not claim that as constructor-block coverage. Keep these constructor tests distinct from actual TCP/TLS handshake tests.
- For each real constructor, deterministic deadline/Close-first order gives late lease abort/finalize/invocation join and zero copies; Admitted-first order stops the timer and the active child survives the former three-second deadline. Both orders must be tested, not just errors.Is(ctx.Err()).
- No concurrent rawClientConn.reader construction versus upper Close/finalizer; raw abort first, all constructor/read callers joined, then cache release. Legacy ChunkReader is not io.Closer, including service/Vision paths.
- Concrete TLS/VMess/VLESS/Service/ConnectionManager repeated cycles show no growth of fds, caches, buffers, owned workers or retiring children; report whether the lifecycle child is real or mocked. A 600-cycle aggregate with a newly created mock lifecycle child each time is **not** evidence of one real SOCKS owner surviving 200 generations. Require a separate real full-owner two-generation route/reject case, and describe its own exact scope.
- Opted shared multiplexDialer, unknown wrapper, insecure/non-Go TLS, transport, detour, resolver/fallback/namespace/TFO/multipath and unsupported metadata/action fail admission without closing shared siblings. The same unopted configurations retain old behavior.
- Existing shared bounded server-writer tests remain: a caller can return while one bounded parent writer owns a prefix frame; raw sibling/new association recovery, End drain and parent fault join must retain their old strict gates. Device-private recovery does not replace or reinterpret them.

### 8.4 Test-harness and failure cleanup gates

Every test-created goroutine, including standalone handshake clients and protocol service handlers, is launched through a registered harness or equivalent independent returned channel. Cleanup first marks closing, opens all sync.Once-controlled barriers (late registration opens immediately), closes raw sockets/owner/child/listener as necessary, then joins every caller under a single bounded cleanup deadline. Only after actual returned signals may it mark joined and restore allocator/global hooks. Buffers receive deferred release on all fatal paths. Handshake clients have bounded deadlines. A serviceDone signal sent before a deferred secure.Close is not complete service join; move publication after all owned cleanup. Register cleanup before any accept/operation that can fail. Stop subsequent cycles on a failed cycle rather than accumulating unjoined work. Close/cancel outer manager/listener before allocator restoration. Pending/timeout/fatal/skip is not success, and test-only force_stdio output is outside default/race/debug qualification unless separately admitted.

## 9. Frozen runtime evidence and role scope

Use the original packet sends, windows, target/reply assertions and two-second server timeout. Add no retry, keepalive, preflight application packet or replacement association to make D pass. The same six D leaves remain the acceptance units. Three idle leaves must preserve the same actual application/SOCKS/control/relay objects, show the original UDP tuple/relay port+inode/control TCP tuple+inode unchanged in every adjudicable existing sample, show no control EOF, and record zero owner recreation/rebind. Old server child resources must disappear within the existing six-second window. At the one 32768-byte new recovery send, show one new route generation/private transport and complete unique request/reply bytes with strict source/target ledgers. The existing 60000-byte pre-idle exchange remains. Three explicit-close leaves still require full original-owner closure and a separate new SOCKS association.

The current passive observer explicitly cannot prove whole-window OS continuity and has inconclusive close-race samples. Preserve those gaps and false proof flags; do not relabel them as successful continuity or substitute a new connection. Exact owner lifecycle events support the invariant but do not change the observer's measured scope. Do not expand a new sampling matrix merely to manufacture a stronger claim.

Bind each device/domestic/overseas PID, birth time, version and binary SHA, role config hash/flag/tag, ordered patch inputs, old/new inode ownership and source manifest to the exact candidate. Separate resource disappearance from new allocation. TIME_WAIT is not an owned-fd leak. Linux is the runtime target; Darwin/Windows compile-only does not establish runtime behavior.

Finite role matrix:

- Preserve old A/B/C/D and U/V/stock baseline arms with the new option absent. Stock device A/U/V must never receive the candidate-only object. Candidate device B remains off for stock-overseas boundary characterization. Every role records explicit `owner_recovery_enabled:false` even if the executable contains optional code.
- Add exactly one owner-on reuse of existing C and six D cases. Only candidate-device mixed gets the selected DOMESTIC_ONLY object. Domestic/overseas retain existing patched role config and no owner option; their two-second behavior and address scope stay unchanged.
- Rerun C on the owner-on executable/mode because queue ownership can regress first/warmed/empty/full-size transport. Do not splice C green from a different binary/flag with D green from owner-on.
- Candidate-off D must retain the original expected failure class and diagnosis. Owner-on D must meet the original strict assertions. Stock/off RED is preserved, not required to turn green.
- Validator rejects owner-on stock/domestic/overseas config, missing explicit mode, role/version/hash mismatch and incompatible reused manifests.

Qualification is tiered: base mechanism, final owner mechanism, owner-on C/D runtime and historical official-client diagnostics are separate. Passing owner mechanism+C/D cannot change official stock RED or claim overall product qualification. Product pin, product UDP defaults, release/deployment and actual external device/line scope remain unchanged. Candidate binaries stay runner-local and are never uploaded or published.

## 10. Eight-patch integration and exact 16-file boundary

The old packet builder's validate_mechanism compares final formatted mux.go, mux_writer.go, xudp.go and route/conn.go to its base mechanism. This owner design intentionally changes xudp.go and route/conn.go and adds sing APIs. It cannot truthfully pass the old same-implementation assertion for the final tree. Preserve the old verifier and five-patch schema unchanged. Do not delete comparison, feed hashes from another tree or inherit old green flags.

Create a distinct `experiments/socks_idle_recovery/` source experiment and evidence kind, for example `vui-socks-owner-source-experiment`. Ordered patches:

1. Existing remoteaddr.patch → sing-vmess.
2. Existing lifecycle.patch → sing-box.
3. Existing mux.patch → sing-vmess.
4. Existing route_cancel.patch → sing-box.
5. Existing receive.patch → sing.
6. New sing_owner.patch → sing.
7. New vmess_session.patch → sing-vmess.
8. New box_owner.patch → sing-box.

After the first five patches, gofmt and freeze a base tree manifest; verify it against a freshly generated same-head legacy mechanism, preserving graph/compiler/patch checks and the existing 23 regression commands. Historical 3fa/6e evidence is not a substitute for a new commit's same-head base run. Then apply all three new patches before compiling any reference to the added N interface. Gofmt and freeze a separate final-owner source manifest. The verifier checks the exact before→base→final delta and never emits `same_mux_route_sources:true` for differing final source.

Exactly 16 allowed production paths:

| Module | Paths |
|---|---|
| sing (5) | `protocol/socks/handshake.go`; new `protocol/socks/association.go`; new `protocol/socks/association_packet.go`; new `common/network/packet_session.go`; `common/bufio/chunk.go` |
| sing-vmess (2) | `client.go`; `xudp.go` |
| sing-box (9) | `option/simple.go`; `protocol/mixed/inbound.go`; new `adapter/packet_session.go`; `protocol/vmess/outbound.go`; `protocol/vless/outbound.go`; new `common/dialer/packet_session.go`; `route/route.go`; `route/conn.go`; new `route/packet_session.go` |

Final mux.go/mux_writer.go remain byte-identical to formatted base. Additional production files require explicit design/allowlist review, never wildcard expansion. Test files are separately enumerated and hashed, injected only into new paths; overlays must not overwrite upstream tests. The once-reported pre-rollback test-file/leaf counts are historical only, not automatic acceptance of reconstructed files. Freeze each new exact test manifest and require unique leaf terminals with no missing/extra/skipped result.

Use local replacements for both complete patched sing and sing-vmess from the final sing-box module. Verify actual `go list -m -json all` outputs resolve the exact directories and normalized fixed versions, not old cache/downloaded interfaces. The full final dependency set reruns:

- All existing complete-packet sing tests, same strict leaf requirements, race and race+debug.
- All 23 existing regression commands, including TestMuxBounded, encrypted AES/ChaCha, encrypted-cancel AES/ChaCha, empty Raw/AES/ChaCha, real TestMuxRoute and lifecycle, against the **same final** dependencies and unchanged assertions.
- New owner, parser, lease, effective-dialer, cancellation, route, observation, old flag-off and non-io.Closer groups with exact test identities, race and race+debug.
- Cross-compile all touched package families, including sing-vmess and sing-box owner/route/dialer, for Darwin/Windows using the reviewed exact compile-only command manifest. Native execution remains Linux.

Capture compiler digest/go version, each patch check/apply result, module graph, go.sum/replacements, actual formatted source/test hashes, final candidate version/binary digest and every command/exit/join record. Required verifier hardening from prior review:

- Require success records for check/apply of overlay patches indexed 5/6/7 with exact args/environment; absence or fabricated partial evidence fails.
- Require exact gofmt records for all 16 changed source paths and separately frozen test targets, using the same approved toolchain.
- Normalize both actual module stdout streams to saved unchanged graph, not just trust a summary field.
- Final regression/new commands record bounded process timeout, leader exit and process-group-gone evidence; do not retroactively change the old base schema.
- Every new native/cross command uses the base compiler path, correct target env and exact selection. No skip/duplicate/extra/missing leaf can pass.
- Test source/target paths are relative, contained and source-under-tests; reject absolute paths or `..` escapes.
- After injection/gofmt freeze the complete source map and formatted test map. After all execution independently rehash the entire map and require identical post-test-source-manifest, including each target hash.
- Parse JSON/JSONL with duplicate-key rejection; enforce text-only UTF-8 allowlist and exact scrub equality. Do not traverse/upload binaries or dependency source clones. Preserve failed partial evidence as failure.

A mechanism-only new job may precede runtime integration, keeping old jobs unchanged. Its success has no owner-on runtime meaning. The runtime adapter/config/evidence validator must be separately restored/reconstructed and reviewed before attaching any owner-on role.

## 11. Bounded implementation/recovery sequence and acceptance

0. **Historical prerequisite complete:** exact 6e passive diagnosis and original architecture were independently reviewed before rollback. **Current reconstruction step:** review this new document/digest against the frozen constraints. It does not requalify lost code. Parent owns the next source-stage decision.
1. Reconstruct isolated sing owner/API/finalizer patch and owner package tests. They compile within complete patched sing without box-only types. Restore all harness cleanup and unbound-source/observation bounds. Hash the new delivery; do not reuse a lost patch digest without byte equality.
2. Reconstruct vmess terminal/finalizer patch and coherent box lease/dialer/route/mixed patch over the complete local dependency graph. Add real constructor/cancellation/opaque-End/metadata tests, fix all cleanup publication ordering, and distinguish real-owner versus mock-child cycle scope. No build with undefined common interfaces.
3. Restore/reconstruct new build/verifier/schema and frozen test manifests. Check all 16 source paths, eight patch identities, base/final graphs and full final regression+new test/cross-compile results. Parent may make an explicit WIP text backup; it is not a reviewed or qualified candidate.
4. Independently review exact restored source, new hashes and all tests. Re-download/reverify baseline artifacts as needed. No previous local test count or reviewer-memory statement substitutes for fresh accurate-tree execution.
5. Restore/review bounded runtime adapter and run original off/stock controls plus the single owner-on C/D arm with unchanged sends/windows/assertions. Verify each exact candidate role and artifact. Report owner-on qualification separately from expected historical RED and never promote stock/product qualification.

Acceptance requires no remaining source/ownership blocker and successful required new final-tree mechanism gates plus strict owner-on runtime gates; diagnostic arms must retain their accurate expected classifications, including RED. Failed stages remain recorded. No code is merged into product support, no product pin or UDP flag is changed, and no binary, release or deployment is authorized by this design. Existing official clients gain no claimed behavior from this isolated candidate.

## 12. Explicit frozen semantic decisions

- Owner layer is sing SOCKS; mixed activates it; XUDP only distinguishes canonical termination.
- Capability is opted candidate-device private verified-TLS transport with effective-dialer proof; unknown/shared capability fails closed only for the opted path.
- One generation and one pending slot; overload and crossed-End ambiguity terminate rather than drop/replay/retry.
- Initial-target remap is retained as established compatibility, not a new question or rejection default.
- Canonical End carries no trustworthy remote idle/success/rejection reason. Only a fresh datagram may trigger one re-admission of original target/policy.
- Actual raw abort enforces three-second constructor admission; both-copy+constructor+protocol-finalizer+callback+invocation join precedes reuse.
- Chunk cache finalization stays explicit and non-io.Closer; no global Close side effect.
- Whole-packet backing and worker limits include construction/in-flight state, with separate protocol/TLS/runtime accounting.
- Exact role/source/flag/test provenance and preservation of official RED are part of qualification, not optional report wording.

No unresolved user-facing target-policy choice remains in this contract. Any implementation inability to satisfy these boundaries is a concrete blocker for parent/reviewer, not permission to silently relax them.
