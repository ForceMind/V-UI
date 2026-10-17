# Two-arm runtime acceptance preparation

This is isolated experimental test code, not a product UDP feature or a change to any product pin. It runs after the shared-mux mechanism controls, on the **same exact committed V-UI HEAD**. The gated runtime CI job starts only after the same-head mechanism job succeeds. These scripts never upload binaries. No release, merge, deployment, upstream communication, or host network/trust change is included.

## What is tested

Both arms receive the frozen address and route-lifecycle patches. `route-only` receives no shared-mux change; `mux` additionally receives `mux.patch` and `route_cancel.patch`. Each candidate has a distinct experimental version and SHA-256. Only the fixture's `domestic` and `domestic-reconfigured` roles use a candidate. Overseas and device processes continue using these official Linux amd64 binaries:

- sing-box 1.14.2: `07d2866a6c908281a56053f0e7d454918501592f2f258526f2ebe826dab58793`
- Mihomo 1.19.32: `3122d100e8177501776109f1a6253a694611627cf4d7c7ec82705855cf8626a8`

Each arm runs:

1. The unchanged 17 original D2 methods, two existing actual HTTPS/TCP double-hop methods, and the existing same-domain/same-port DNS-rebinding method: **20 original methods**. The exact test13 `raw_echo` allowlist failure remains a real failure. The accepted *diagnostic* result is 19/20, never an original-suite pass, an expected-failure conversion, or product qualification. Unexpected all-green also fails the diagnostic identity check.
2. The unchanged six independent lifecycle probes: full-domain/initial-port/payload equivalence, two six-second idle/recovery probes, a shared-parent active/idle/new-session sibling probe, and three domestic/device restart cycles for each official client. They require actual session End, the exact sockets' disappearance, official-peer EOF evidence, exact target/port/payload/source-peer and DNS ledgers, and joined cleanup. Same-association destination changes remain explicitly bound to the initial target; they are not described as per-frame rejection or rerouting.
3. Two new real VMess/XUDP compatibility cases with the **official sing-box device**: explicit `aes-128-gcm` and `chacha20-poly1305`, `alter_id=0`, `global_padding=true`, `authenticated_length=true`, and verified TLS. Each sends 1, 8,192, 32,768 and 60,000 bytes on one SOCKS association, one attempt per packet, with exact reply address/port/payload and overseas-source delivery accounting. The old fixture's `UDPServer` has an 8,192-byte default receive cap, so these cases add a separate 65,535-byte recorder; the frozen helper and its original target are untouched and still audited.
4. A new verified-TLS raw VLESS CommandMux parent carrying two TCP sessions of **196,742 application bytes each**, sent as four mux request frames per session. Responses must span at least four intact frames per session, reproduce exact payloads, and have exactly one End each. A new UDP sibling then succeeds on that same still-open TLS parent. The application TCP server accepts at most two connections, requires the official overseas source address and exact payloads, and joins all workers at final cleanup.

The compatibility cases do not replace the mechanism tests. The large TCP runtime case proves real framing across multiple frames; it cannot prove that a particular application `sendall` becomes one >65,535-byte Go `Write` call. The Go mechanism controls own that single-call split/partial-write assertion. Likewise these runtime cases do not measure route-copy goroutines under forced parent backpressure; the actual-ConnectionManager mechanism tests own those observations.

These new compatibility probes may expose a pre-existing route-only defect. If they do, preserve the actual failing arm, payload/ledger and exact attempt; the matrix remains failed. Do not assume that route-only must be green, weaken a check, or repeat unchanged code until an inconvenient result disappears. Any revised control expectation needs separately reviewed evidence and test changes.

### Why the VMess case reaches the changed writer

In fixed sing-box source `protocol/vmess/outbound.go`, `packet_encoding=xudp` reaches `DialEarlyXUDPPacketConn`; in fixed sing-vmess `client.go`, this uses `CommandMux`. The service dispatches that command through `HandleMuxConnection` with its encrypted/chunked `serverConn`. Explicit encryption is necessary because sing-box `security=auto` under TLS selects `zero`. Ordinary `multiplex=true` selects a different multiplexing layer and is deliberately absent. Existing `tests/test_vmess_loopback.py` proves ordinary HTTP/TCP, not this encrypted CommandMux path. Parser success alone is never accepted: all four real datagrams must return and their ledgers must match.

Successful business traffic, the exact configuration digest/selected settings and the pinned source dispatch establish use of this encrypted CommandMux path. This runner does not independently capture/decrypt VMess ciphertext and does not claim an independent cryptographic verification.

## Identity and build contract

`common.source_identity()` refuses tracked changes and requires every frozen gate, reused runner, patch, and new runtime Python file to equal its bytes in HEAD. It records the exact commit, tree, and SHA-256 of each. Therefore the new files must first be reviewed and committed in the authorized isolated branch; merely copying them beside an earlier commit cannot produce labelled acceptance.

`build_candidates.py` independently copies pristine fixed source checkouts, applies the exact committed patch chain, and verifies that only the declared upstream source files changed. It uses existing Go 1.25.14, linux/amd64, CGO=0, GOTOOLCHAIN=local, GOWORK=off, no tags, trimpath, buildvcs=false, and explicit distinct version ldflags. Both arms must have the same compiler digest and normalized fixed dependency graph. `go mod verify` runs; fixed go.sum and all other source files must remain unchanged. No compiler is fetched and no product-core path is overwritten.

For the mux arm only, it applies the same toolchain's `gofmt` to `mux.go` and `mux_writer.go` before recording hashes, matching the mechanism builder. It never formats route-only or route source. `compare_mechanism.py` verifies the downloaded mechanism artifact manifest, then requires the same exact commit, formatted source bytes, ordered patches, compiler digest and normalized module graph before the runtime job can start its matrix.

This builder is separate from `../build_and_test.py`: the latter creates instrumented mechanism-test source trees, while this builder creates minimal runtime source trees from pristine checkouts. The fixed source commits and repository patch bytes are identical, but runtime builds contain no injected Go test files. Do not relabel a mechanism tree or previous lifecycle candidate as a new runtime arm.

## Exact integration commands

Only execute the real stages after the publication/CI permission blocker is resolved and the accurate candidate is committed. These commands do not create a workflow or publish anything. Run on the empty approved Linux runner with Python 3.12 and the existing official Go 1.25.14 compiler selected in PATH. Use the repository's existing test dependencies and official-core fetch scripts, without changing their pins:

```sh
export PYTHONDONTWRITEBYTECODE=1
python3 -m unittest discover -s experiments/xudp_mux -p 'test_*.py' -v
python3 -m unittest discover -s experiments/xudp_mux/runtime -p 'test_*.py' -v
python3 -m unittest discover -s experiments/xudp_address -p 'test_*.py' -v

# Existing mechanism stage first, at this same HEAD.
python3 experiments/xudp_mux/build_and_test.py \
  --sing-box .experiment-source/sing-box \
  --sing-vmess .experiment-source/sing-vmess \
  --output /tmp/vui-xudp-mux-mechanism

# Existing requirements-test.txt and official fetch contracts.
python3 scripts/fetch_test_cores.py /tmp/vui-xudp-mux-official/cores
python3 scripts/fetch_mihomo.py /tmp/vui-xudp-mux-official/client

python3 experiments/xudp_mux/runtime/build_candidates.py \
  --sing-box .experiment-source/sing-box \
  --sing-vmess .experiment-source/sing-vmess \
  --output /tmp/vui-xudp-mux-candidates

export VUI_TEST_CORES=/tmp/vui-xudp-mux-official/cores
export VUI_TEST_MIHOMO=/tmp/vui-xudp-mux-official/client/mihomo
export VUI_DATA_DIR="$(mktemp -d)"
python3 experiments/xudp_mux/runtime/compare_mechanism.py \
  --mechanism /tmp/vui-xudp-mux-mechanism/evidence \
  --candidates /tmp/vui-xudp-mux-candidates

python3 experiments/xudp_mux/runtime/run_matrix.py \
  --candidates /tmp/vui-xudp-mux-candidates \
  --output /tmp/vui-xudp-mux-runtime

# Run preservation even after a failed build/runtime attempt, before any upload.
python3 experiments/xudp_mux/runtime/prepare_evidence.py \
  --runtime /tmp/vui-xudp-mux-runtime \
  --build /tmp/vui-xudp-mux-candidates/evidence \
  --output /tmp/vui-xudp-mux-runtime-upload
```

The `.experiment-source` checkouts must be sing-box `af6e64c3b69e6132ebaee0e1a3d24e93903f6709` and sing-vmess `31ec11e8790c48a4bf11711806a61e929ee6b89d`, clean and unmodified. Each output path must be new. Use new attempt paths for unchanged-code retries and preserve earlier failed evidence. Do not append a successful run over a failed directory.

The isolated workflow includes separate runtime unittest discovery explicitly; the parent directory's discovery does not recurse into this non-package directory. Provision the same existing Python dependencies as the old runtime workflow. Keep mechanism and runtime results separately named. Allow roughly 10 minutes for the two sequential runtime arms in addition to build/test time; the original negative-case windows and cleanup bounds are intentionally unchanged.

Upload **only** `/tmp/vui-xudp-mux-runtime-upload` after successful evidence preparation. Its allowlist contains JSON/logs and a hash manifest, never candidate binaries, full process configs, certificates, private keys, caches, temporary data directories or source trees. No permission to upload experimental binaries is implied. Sanitization keeps failures, missing observations and incomplete attempts visible.

## Result interpretation and current verification

- Runtime exit 0 means both *diagnostic* contracts completed: all original methods executed with the exact preserved 19/20 RED and all nine supplemental cases passed, with complete process and lifetime ledgers.
- `original_suite_passed` and `product_qualified` remain false at every level. Errors, skips, missing methods/roles/artifacts, changed identities, boundary-inconclusive idle evidence, cleanup failures or unexpected original outcomes cause a nonzero result.
- Missing compiler, dependencies, official binaries or process/socket permissions are blockers, not skips or passing results.
- Preparation validation on 2026-10-17: 26 pure runtime controls (including exact mechanism/runtime identity checks), 12 parent mechanism Python controls, and 122 unchanged address/lifecycle Python controls passed. No candidate compilation, real TLS/device runtime or CI was executed for these new files. The available `/usr/bin/go` is not the required Go compiler (`go version` returns `Go: Unknown option: version`); uncommitted runtime files are also deliberately ineligible for exact-HEAD execution. The subsequent exact-commit CI result is recorded separately; this preparation record is not a runtime pass.
