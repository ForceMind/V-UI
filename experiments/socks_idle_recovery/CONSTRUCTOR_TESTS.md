# Owner constructor cancellation supplement

Status: source-only proposal, not gofmt-verified, compiled, or executed. This supplement is not part of the first mechanism candidate and grants no runtime or product qualification. Four new Go test files; thirty exact leaves. No production changes, repository publication, runtime deployment, or binary artifact.

## Rebuilt dependencies

The original draft was interrupted by an execution-workspace rollback. Its pre-rollback interfaces and helpers cannot provide qualification for the rebuilt candidate. The current files are self-contained test helpers and use the rebuilt APIs:

- PacketSessionPolicy{Outbound, Target, Lifecycle}; WithPacketSessionPolicy.
- OwnedPacketSessionDialer.ValidatePacketSession; OpenPacketSession returns concrete *adapter.PacketSessionLease.
- PacketSessionLease.IsValid, AbortTransport, Finalize; NewPacketSessionLease.
- DialPacketSessionTLS and actual DefaultDialer.trackConn. There is no independent DialPacketSessionTCP in this candidate.
- Finalize aborts raw transport, closes the packet, then performs the explicit post-join cache release.

The exact inspected patch/design input hashes are recorded in TEST_CASES.json. Test hashes describe the checked-in source bytes before any future gofmt. Compilation must resolve the full patched local sing and sing-vmess together from the final sing-box module. Never compile against an unpatched module-cache replacement.

## Exact added coverage

1. TestOwnerConcreteTLSConstructionCancellation: 6 leaves, AES/ChaCha/VLESS × actual admission deadline/control EOF. Real SOCKS owner, Router, ConnectionManager, concrete outbound, DefaultDialer, TCP, and Go TLS. Peer reads actual ClientHello but sends no ServerHello. Require terminal join, zero application-copy calls, zero admission, retained-then-released owner slot, one owner abort invocation, TCP peer EOF, and allocator baseline. TLS context and owner supervision can race at the deadline; the result records admission_timeout or deadline. These leaves do not prove outer-protocol TCP write backpressure.

2. TestOwnerRealOuterConstructorPipeCancellation: 6 leaves, real AES/ChaCha/VLESS Client constructor over verified Go TLS and net.Pipe, deadline/control EOF. Handshake completes before the outer write is armed. The peer has no application reader; assert actual encrypted Write entry and nonreturn, then actual owner raw abort releases it. VLESS must expose its actual partial packet result; VMess must return nil on this constructor error. Raw close is audited once; real constructor, provider, route invocation, peer and owner all join with zero copies. The provider is test-only; no concrete-outbound TCP blocking claim.

3. TestOwnerLateLeaseCancellationPermutations: 8 leaves, pre-close/late Attach × successful/error late lease result × deadline/control EOF. Real owner/Router/ConnectionManager, synthetic provider and packet. Provider and finalizer barriers keep the owner slot occupied. Late attach raw-aborts; no copies/admission/new generation; raw abort may precede constructor return, but finalization is fenced after constructor return, raw/packet/cache teardown counts remain once. This models late return; it is not an actual slow TCP dial result.

4. TestOwnerRealConstructedLateLeaseCancellation: 6 leaves, real AES/ChaCha/VLESS Client and real Service over verified Go TLS/net.Pipe. Constructor succeeds first, then a test-only provider-return barrier keeps its actual lease unpublished. Owner deadline/control EOF wins before that return. Require raw abort while the owner slot remains held, zero copies, no new generation, and actual late packet/cache finalization after constructor completion. Outer CommandMux alone may not dispatch an application session. This supplements the synthetic late-return cases without claiming real TCP dial lateness.

5. TestOwnerConcreteAdmittedSurvivesOldDeadline: 3 leaves. Actual owner → Router → concrete outbound → TCP/TLS → fixed Service. Await actual Admitted first; a measured 3.2-second interval starting after that event passes the old deadline. The same generation then completes a second unique application packet and SOCKS reply. Both actual upload acknowledgements precede remote canonical End. Require one admission/transport, exact original target/payload ledgers, preserved control/relay object identities, Dormant only after route/protocol cleanup, and final owner slot release. This is a one-generation timer-order test, not hundreds of persistent-owner generations.

6. TestOwnerActualTrackedPowerCounterComposition: 1 leaf. Actual DefaultDialer/DialPacketSessionTLS with real ConnectionManager and powerreport.Manager. Prove the accepted chain TLS → PacketSessionOnceConn → CounterConn → ExtendedConnWrapper → trackedConn → TCP. After this proof, interpose transparent audit callbacks that retain the original power callbacks and a transparent Close audit immediately below the same once wrapper. Actual TLS encrypted byte totals must equal the independently observed peer-wire totals. Sixteen concurrent lease teardown callers plus repeated attached abort/TLS Close produce one raw, packet, and cache close and no tracked entry. The real joined power report must count exactly one opened connection. Audit probes are test instrumentation after admission; they do not qualify an unknown production wrapper.

## Harness and limits

All server endpoints and SOCKS clients are local; credentials and temporary certificate authority material are fixtures. No user or production network is contacted. Datagrams are sent once; there is no retry/replay loop.

All explicitly started workers register independent returned channels before launch. Control dial/handshake and direct test I/O have bounded waits/deadlines. Sync.Once gates are owned by the fixture; failure cleanup cancels the fixture, opens/closes owned gates and raw sockets, closes the manager, and joins registered workers under one eight-second cleanup timer. If a worker does not join, the test panics and refuses allocator restoration; it cannot silently pass or continue into later cycles. Protocol handler join is registered before the test sends its first actual application packet. Service completion is published only after secure/raw deferred closes. Allocator get/release ownership is checked only after complete joins and service cleanup.

Remaining limits: no actual intentionally late TCP DialContext return; no claim that a tiny concrete VMess/VLESS outer request filled a real TCP send buffer; no new shared-parent writer qualification; no complete actual-owner hundreds-of-generations qualification; no runtime C/D, product UDP, release or deployment qualification. Previously frozen tests and failure evidence remain independent and unchanged.

## Timing and platform boundary

The thirty leaves contain thirteen actual three-second owner/admission-deadline windows and three 3.2-second post-Admitted windows: 48.6 seconds of configured intentional timer windows. This is source-derived budget accounting, not a measured duration or worst-case elapsed upper bound. TLS setup, I/O, scheduling and power-report shutdown remain unmeasured; some construction is already inside the three-second window. Each phase has an eight-second watchdog; failure cleanup shares one eight-second join timer. Failed subtests return from their parent loop immediately, so a regression does not continue later protocols or permutations.

Keep this supplement in its own exact-name group with a 180-second package timeout in each mode. Do not concatenate it with the existing 631 owner-route leaves under one broad ^TestOwner selector until exact-head measurements establish a justified combined budget. The existing 631-leaf selector and this 30-leaf selector are full-name anchored, disjoint, and recorded in TEST_CASES.json; splitting preserves 661 route leaves and does not increase either group timeout. The original 600 real-cycle timing is not available here, so combined completion within 180 seconds is not certified.

The four added files have no platform build tags, syscall/unix imports, or Linux-only symbols. They use generic Go/net/TLS APIs and the existing platform-split powerreport implementation, so static inspection found no new Darwin/Windows compile-only blocker. Actual cross-compilation is still required and does not provide non-Linux runtime qualification.

## Applying and verifying

constructor_cancellation_tests.patch adds only four new route/vui_*_supplement_test.go paths. The manifest contains exact source paths, target paths, SHA256 values and all thirty full leaf names. Do not overlay an existing upstream test file. The current supplement contains no references to helpers from the first mechanism test overlay.

In the final sing-box module after applying all eight production patches and resolving both patched local dependencies, run separately:

- go test -json -race ./route -run '^TestOwner(ConcreteTLSConstructionCancellation|RealOuterConstructorPipeCancellation|LateLeaseCancellationPermutations|RealConstructedLateLeaseCancellation|ConcreteAdmittedSurvivesOldDeadline|ActualTrackedPowerCounterComposition)$' -count=1 -timeout=180s
- The same command with -tags debug.

Save unmodified JSON outputs and process exit codes. Run verify_test_json.py with --json LOG --mode race or race_debug. The validator rejects missing, duplicate, skipped, failed, unexpected or unfinished leaves and requires all top-level and package PASS records. Its source-only mode only verifies file hashes and expected leaf counts; it explicitly reports go_tests_executed:false. Synthetic validator fixtures are not Go test evidence.

A real Go compiler/gofmt is unavailable in this workspace. The system /usr/bin/go is a different program and rejects "go version". Only static interface/source inspection, lexical-delimiter checks, source hashes, patch-application checks and Python-validator tests can be performed here. Exact source formatting, compilation, race/race+debug execution and final dependency provenance remain mandatory future gates.


## Integration provenance

The standalone manifest preserves its preparation-time production inputs. The
integrated candidate uses box_owner.patch SHA256
`22862e45c16114491c0a7b82d0c717610bfa956e4ff138cf29bf4167691ed2bf`,
which removes an import cycle while retaining the same low-level TLS Config alias
and exact concrete TLS policy. All source/test hashes and the full eight-patch
chain must be verified again on the final committed dependency tree. The 632
original route leaves and these 30 leaves use disjoint exact top-level regexes,
each with an unchanged 180-second timeout and both race modes.
