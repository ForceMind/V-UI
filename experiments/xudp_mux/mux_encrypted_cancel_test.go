package vmess

// Isolated mux-arm supplement. This exercises the fixed encrypted Client/Service
// and direct library callers, not ConnectionManager or official-device SOCKS.
// Keep mux_encrypted_test.go beside this file for encryptedRequest/encryptedRead.
import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"runtime"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	M "github.com/sagernet/sing/common/metadata"
	N "github.com/sagernet/sing/common/network"
)

const encCancelBound = 3 * time.Second
var encCancelTarget = M.ParseSocksaddr("127.0.0.3:12345")

// No fake Write, early return, injected error, deadline, or cipher implementation:
// every Write below delegates the same bytes to the real net.Pipe endpoint.
type encCancelWire struct {
	net.Conn
	active, maximum atomic.Int32
	starts, completions, completedBytes atomic.Int64
	currentLength atomic.Int64
	closes, failedWrites, deadlineChanges atomic.Int32
}

func (w *encCancelWire) Write(p []byte) (int, error) {
	w.currentLength.Store(int64(len(p)))
	w.starts.Add(1)
	now := w.active.Add(1)
	for old := w.maximum.Load(); now > old; old = w.maximum.Load() {
		if w.maximum.CompareAndSwap(old, now) { break }
	}
	n, err := w.Conn.Write(p)
	w.completedBytes.Add(int64(n))
	w.completions.Add(1)
	if err != nil { w.failedWrites.Add(1) }
	w.active.Add(-1)
	return n, err
}

func (w *encCancelWire) Close() error { w.closes.Add(1); return w.Conn.Close() }
func (w *encCancelWire) SetDeadline(v time.Time) error { w.deadlineChanges.Add(1); return w.Conn.SetDeadline(v) }
func (w *encCancelWire) SetReadDeadline(v time.Time) error { w.deadlineChanges.Add(1); return w.Conn.SetReadDeadline(v) }
func (w *encCancelWire) SetWriteDeadline(v time.Time) error { w.deadlineChanges.Add(1); return w.Conn.SetWriteDeadline(v) }

// Only the test's main goroutine reads this transport. The one captured raw
// prefix is replayed once, unchanged, through the original Client's reader.
// Neither the actual client decoder nor any nonce/cipher state is replaced.
type encCancelClientWire struct {
	net.Conn
	prefix *bytes.Reader
	physicalRead atomic.Int64
}

func (w *encCancelClientWire) Read(p []byte) (int, error) {
	if w.prefix != nil && w.prefix.Len() != 0 { return w.prefix.Read(p) }
	w.prefix = nil
	n, err := w.Conn.Read(p)
	w.physicalRead.Add(int64(n))
	return n, err
}

func (w *encCancelClientWire) capture(n int) ([]byte, error) {
	if w.prefix != nil && w.prefix.Len() != 0 { return nil, errors.New("unconsumed ciphertext replay") }
	p := make([]byte, n)
	_, err := io.ReadFull(w, p)
	return p, err
}

func (w *encCancelClientWire) restore(p []byte) {
	w.prefix = bytes.NewReader(p)
}

type encCancelHandler struct {
	tcp chan net.Conn
	udp chan N.PacketConn
	returned chan struct{}
	active, finished atomic.Int32
}

func (h *encCancelHandler) finish() {
	h.active.Add(-1)
	h.finished.Add(1)
	h.returned <- struct{}{}
}
func (h *encCancelHandler) NewConnectionEx(_ context.Context, c net.Conn, _ M.Socksaddr, _ M.Socksaddr, _ N.CloseHandlerFunc) {
	h.active.Add(1); defer h.finish(); h.tcp <- c
}
func (h *encCancelHandler) NewPacketConnectionEx(_ context.Context, c N.PacketConn, _ M.Socksaddr, _ M.Socksaddr, _ N.CloseHandlerFunc) {
	h.active.Add(1); defer h.finish(); h.udp <- c
}

type encCancelCall struct { done chan struct{}; n int; err error }
type encCancelHarness struct {
	t *testing.T
	cipher string
	ctx context.Context
	cancel context.CancelFunc
	client *clientConn
	clientWire *encCancelClientWire
	serverWire *encCancelWire
	service *Service[string]
	serviceDone chan struct{}
	serviceErr error
	handler *encCancelHandler
	session *serverSession
	parent *serverConn
	workers sync.WaitGroup
	activeCallers atomic.Int32
	admitted, seq int
	responseChunks, requestChunks int
	ends map[uint16]int
	joined bool
	verifiedParentErrorCallers int
}

func encCancelChunks(n int) int { return (n + WriteChunkSize - 1) / WriteChunkSize }
func encCancelPayload(n int, salt byte) []byte {
	p := make([]byte, n)
	for i := range p { p[i] = byte((i*37 + i/251) ^ int(salt)) }
	return p
}

func encCancelReady(ch <-chan struct{}) bool {
	select { case <-ch: return true; default: return false }
}

func encCancelEventually(t *testing.T, check func() bool, label string) {
	t.Helper()
	deadline := time.Now().Add(encCancelBound)
	for !check() {
		if time.Now().After(deadline) { t.Fatalf("bounded observation failed: %s", label) }
		runtime.Gosched() // Read a condition/stack, never infer blockage from a sleep.
	}
}

func (h *encCancelHarness) event(name string, fields map[string]any) {
	h.t.Helper(); h.seq++
	fields["event"], fields["case"], fields["cipher"], fields["seq"] = name, h.t.Name(), h.cipher, h.seq
	fields["scope"], fields["product_qualified"] = "encrypted-library-callers", false
	p, err := json.Marshal(fields); if err != nil { h.t.Fatal(err) }
	h.t.Logf("XUDP_ENCRYPTED_CANCEL %s", p)
}

func (h *encCancelHarness) start(call func() (int, error)) *encCancelCall {
	done := &encCancelCall{done: make(chan struct{})}
	h.workers.Add(1); h.activeCallers.Add(1)
	go h.runCall(done, call)
	return done
}
func (h *encCancelHarness) runCall(done *encCancelCall, call func() (int, error)) {
	defer func() { h.activeCallers.Add(-1); h.workers.Done(); close(done.done) }()
	done.n, done.err = call()
}
func (h *encCancelHarness) wait(call *encCancelCall, wantN int, wantClosed bool) {
	h.t.Helper()
	select {
	case <-call.done:
		if wantClosed {
			if call.n != wantN || !errors.Is(call.err, net.ErrClosed) { h.t.Fatalf("cancel result n=%d err=%v; want %d/net.ErrClosed", call.n, call.err, wantN) }
		} else if call.n != wantN || call.err != nil { h.t.Fatalf("operation n=%d err=%v; want %d/nil", call.n, call.err, wantN) }
	case <-time.After(encCancelBound): h.t.Fatal("encrypted caller exceeded bound")
	}
}
func (h *encCancelHarness) waitChannel(ch <-chan struct{}, label string) {
	h.t.Helper()
	select { case <-ch: case <-time.After(encCancelBound): h.t.Fatal(label+" did not join") }
}

func newEncCancelHarness(t *testing.T, security string) *encCancelHarness {
	t.Helper()
	ctx, cancel := context.WithCancel(context.Background())
	left, right := net.Pipe()
	h := &encCancelHarness{t: t, cipher: security, ctx: ctx, cancel: cancel,
		clientWire: &encCancelClientWire{Conn: left}, serverWire: &encCancelWire{Conn: right}, serviceDone: make(chan struct{}),
		handler: &encCancelHandler{tcp: make(chan net.Conn, 4), udp: make(chan N.PacketConn, 4), returned: make(chan struct{}, 4)}, ends: make(map[uint16]int)}
	t.Cleanup(func() {
		// Error cleanup cannot produce a successful evidence event.
		cancel(); _ = left.Close(); _ = right.Close(); _ = h.service.Close()
		h.waitChannel(h.serviceDone, "Service cleanup")
		all := make(chan struct{}); go func() { h.workers.Wait(); close(all) }()
		h.waitChannel(all, "caller cleanup")
		if !h.joined { t.Error("explicit encrypted parent join was not established") }
	})
	h.service = NewService[string](h.handler)
	const user = "11111111-2222-4333-8444-555555555555"
	if err := h.service.UpdateUsers([]string{"encrypted-cancel-fixture"}, []string{user}, []int{0}); err != nil { t.Fatal(err) }
	go func() { defer close(h.serviceDone); h.serviceErr = h.service.NewConnection(ctx, h.serverWire, M.ParseSocksaddr("127.0.0.1:24443"), nil) }()
	client, err := NewClient(user, security, 0, ClientWithGlobalPadding(), ClientWithAuthenticatedLength()); if err != nil { t.Fatal(err) }
	h.client = &clientConn{client.dialRaw(h.clientWire, CommandMux, MuxDestination)}
	required := byte(RequestOptionChunkStream | RequestOptionChunkMasking | RequestOptionGlobalPadding | RequestOptionAuthenticatedLength)
	if h.client.command != CommandMux || h.client.option != required || h.client.security == SecurityTypeNone { t.Fatal("missing encrypted CommandMux options") }
	return h
}

func (h *encCancelHarness) send(id uint16, status, network byte, data []byte) *encCancelCall {
	frame := encryptedRequest(id, status, network, encCancelTarget, data)
	call := h.start(func() (int, error) { return h.client.Write(frame) })
	// Caller owns the request frame until waitSend has joined this call.
	h.requestChunks += encCancelChunks(len(frame))
	return call
}
func (h *encCancelHarness) waitSend(call *encCancelCall, id uint16, status, network byte, data []byte) {
	h.wait(call, len(encryptedRequest(id, status, network, encCancelTarget, data)), false)
}
func (h *encCancelHarness) opened(session *serverSession) {
	h.t.Helper()
	select { case <-h.handler.returned: case <-time.After(encCancelBound): h.t.Fatal("handler callback did not return") }
	h.admitted++
	if h.session == nil {
		h.session = session
		var ok bool
		h.parent, ok = session.conn.(*serverConn)
		if !ok || h.parent.Conn != h.serverWire || h.parent.option != h.client.option || h.parent.security != h.client.security { h.t.Fatal("Service did not dispatch the actual encrypted parent") }
	} else if h.session != session { h.t.Fatal("sibling escaped shared encrypted parent") }
}
func (h *encCancelHarness) openUDP(id uint16) *serverMuxPacketConn {
	h.t.Helper(); d := h.send(id, StatusNew, NetworkUDP, nil); h.waitSend(d, id, StatusNew, NetworkUDP, nil)
	select { case c := <-h.handler.udp: v, ok := c.(*serverMuxPacketConn); if !ok { h.t.Fatal("unexpected UDP dispatch") }; h.opened(v.session); return v
	case <-time.After(encCancelBound): h.t.Fatal("UDP dispatch absent"); return nil }
}
func (h *encCancelHarness) openTCP(id uint16) *serverMuxConn {
	h.t.Helper(); d := h.send(id, StatusNew, NetworkTCP, nil); h.waitSend(d, id, StatusNew, NetworkTCP, nil)
	select { case c := <-h.handler.tcp: v, ok := c.(*serverMuxConn); if !ok { h.t.Fatal("unexpected TCP dispatch") }; h.opened(v.session); return v
	case <-time.After(encCancelBound): h.t.Fatal("TCP dispatch absent"); return nil }
}
func (h *encCancelHarness) udpWrite(c *serverMuxPacketConn, p []byte) *encCancelCall {
	return h.start(func() (int, error) { return c.WriteTo(p, encCancelTarget) })
}
func (h *encCancelHarness) tcpWrite(c *serverMuxConn, p []byte) *encCancelCall {
	return h.start(func() (int, error) { return c.Write(p) })
}
func (h *encCancelHarness) close(c io.Closer) {
	call := h.start(func() (int, error) { return 0, c.Close() }); h.wait(call, 0, false)
}
func (h *encCancelHarness) read() encryptedFrame {
	h.t.Helper(); _ = h.clientWire.SetReadDeadline(time.Now().Add(encCancelBound)); defer h.clientWire.SetReadDeadline(time.Time{})
	f := encryptedRead(h.t, h.client)
	length := 6
	if f.address.IsValid() { length += 1 + AddressSerializer.AddrPortLen(f.address) }
	if f.data != nil { length += 2 + len(f.data) }
	h.responseChunks += encCancelChunks(length)
	if f.status == StatusEnd { h.ends[f.id]++; if h.ends[f.id] != 1 { h.t.Fatalf("duplicate End for %d", f.id) } }
	return f
}
func (h *encCancelHarness) data(id uint16, p []byte, udp bool) {
	h.t.Helper(); f := h.read()
	if f.id != id || f.status != StatusKeep || !bytes.Equal(f.data, p) { h.t.Fatalf("response frame changed id=%d got=%d status=%d size=%d want=%d", id, f.id, f.status, len(f.data), len(p)) }
	if (udp && f.address != encCancelTarget) || (!udp && f.address.IsValid()) { h.t.Fatal("response tuple/network changed") }
}
func (h *encCancelHarness) end(id uint16) {
	h.t.Helper(); f := h.read()
	if f.id != id || f.status != StatusEnd || len(f.data) != 0 || f.address.IsValid() { h.t.Fatalf("ordered End missing for %d", id) }
}

// The predicate is evaluated under the production state lock. Waiting uses its
// real changed notification, not guessed timing or a product test hook.
func (h *encCancelHarness) stateWait(check func() bool, label string) {
	h.t.Helper(); timer := time.NewTimer(encCancelBound); defer timer.Stop()
	for {
		h.session.streamAccess.Lock(); ok, changed := check(), h.session.changed; h.session.streamAccess.Unlock()
		if ok { return }
		select { case <-changed: case <-timer.C: h.t.Fatal("state condition absent: "+label) }
	}
}
func (h *encCancelHarness) queued(s *serverStream) *muxWrite {
	h.stateWait(func() bool { return s.pending != nil && !s.pending.started }, "queued operation")
	h.session.streamAccess.Lock(); defer h.session.streamAccess.Unlock()
	if s.pending == nil || s.pending.started { h.t.Fatal("queued operation changed under blocked parent") }
	return s.pending
}
func (h *encCancelHarness) pending(s *serverStream) *muxWrite {
	h.session.streamAccess.Lock(); defer h.session.streamAccess.Unlock()
	if s.pending == nil || !s.pending.started { h.t.Fatal("actual in-flight operation missing") }
	return s.pending
}
func (h *encCancelHarness) quiescent() {
	h.stateWait(func() bool {
		if h.session.reject != nil || len(h.session.queue) != 0 { return false }
		for _, s := range h.session.streams { if s.pending != nil || s.endQueued { return false } }
		return h.serverWire.active.Load() == 0
	}, "writer quiescence")
}

func encCancelStacks(t *testing.T) (writers, blocked, cleanup, testWorkers int, evidence string) {
	t.Helper(); p := make([]byte, 2<<20); n := runtime.Stack(p, true); if n == len(p) { t.Fatal("truncated stack evidence") }
	for _, stack := range strings.Split(string(p[:n]), "\n\n") {
		if strings.Contains(stack, "(*serverSession).writeLoop(") {
			writers++
			if strings.Contains(stack, " [select]:") && strings.Contains(stack, "net.(*pipe).write(") && strings.Contains(stack, "(*encCancelWire).Write(") && strings.Contains(stack, "(*serverConn).Write(") { blocked++; evidence = stack }
		}
		if strings.Contains(stack, "HandleMuxConnection.func") { cleanup++ }
		if strings.Contains(stack, "(*encCancelHarness).runCall(") || strings.Contains(stack, "(*encCancelHandler).NewConnectionEx(") || strings.Contains(stack, "(*encCancelHandler).NewPacketConnectionEx(") { testWorkers++ }
	}
	return
}

func (h *encCancelHarness) assertAlive() {
	h.t.Helper()
	if h.ctx.Err() != nil || h.session.ctx.Err() != nil || h.serverWire.closes.Load() != 0 || h.serverWire.deadlineChanges.Load() != 0 || encCancelReady(h.serviceDone) || encCancelReady(h.session.writerDone) { h.t.Fatal("association operation killed encrypted parent") }
}

// prefix=1 is inside the encrypted length header. prefix=18+37 traverses the
// complete 2+CipherOverhead authenticated length and 37 encrypted body bytes.
// debug's STDIO path may use separate raw writes; cumulative bytes identify
// the location without assuming the default WriteBuffer coalescing behavior.
func (h *encCancelHarness) capture(prefix int) []byte {
	h.t.Helper()
	baseBytes := h.serverWire.completedBytes.Load()
	_ = h.clientWire.SetReadDeadline(time.Now().Add(encCancelBound))
	p, err := h.clientWire.capture(prefix)
	_ = h.clientWire.SetReadDeadline(time.Time{})
	if err != nil { h.t.Fatal("ciphertext prefix read failed", err) }
	var writers, blocked int; var stack string
	encCancelEventually(h.t, func() bool {
		writers, blocked, _, _, stack = encCancelStacks(h.t)
		return writers == 1 && blocked == 1 && h.serverWire.active.Load() == 1
	}, "sole writer in actual pipe.write")
	complete := h.serverWire.completedBytes.Load() - baseBytes
	partial := int64(prefix) - complete
	if partial <= 0 || partial >= h.serverWire.currentLength.Load() || h.serverWire.maximum.Load() != 1 { h.t.Fatalf("not a partial actual raw Write: prefix=%d complete=%d partial=%d length=%d", prefix, complete, partial, h.serverWire.currentLength.Load()) }
	h.assertAlive()
	h.event("wire_blocked", map[string]any{"prefix_bytes": prefix, "auth_length_bytes": 2+CipherOverhead, "completed_write_bytes_since_capture": complete, "active_write_consumed_bytes": partial, "active_write_length": h.serverWire.currentLength.Load(), "parent_writers": writers, "blocked_pipe_writers": blocked, "active_transport_writes": h.serverWire.active.Load(), "maximum_transport_writes": h.serverWire.maximum.Load(), "blocked_stack": stack})
	return p
}
func (h *encCancelHarness) restored(prefix []byte) {
	h.assertAlive()
	h.event("restore_reads", map[string]any{"prefix_bytes": len(prefix), "unchanged_prefix_replay": true, "parent_alive": true})
	h.clientWire.restore(prefix)
}
func (h *encCancelHarness) canceledSnapshot(call *encCancelCall, op *muxWrite, queued bool, started time.Time) {
	h.wait(call, 0, true); h.assertAlive()
	elapsed := time.Since(started); if elapsed >= encCancelBound { h.t.Fatal("aggregate association cancellation exceeded bound") }
	h.session.streamAccess.Lock()
	if !op.canceled || (queued && (op.stream.pending != nil || !encCancelReady(op.done))) || (!queued && (op.stream.pending != op || encCancelReady(op.done))) { h.session.streamAccess.Unlock(); h.t.Fatal("wrong queued/in-flight cancellation ownership") }
	owned, frames := 0, 0
	for _, s := range h.session.streams {
		if s.pending != nil { owned += len(s.pending.frame); frames++ }
		if s.endQueued { owned += 6; frames++ }
	}
	streams := len(h.session.streams)
	h.session.streamAccess.Unlock()
	writers, blocked, _, _, _ := encCancelStacks(h.t)
	if writers != 1 || blocked != 1 || h.serverWire.active.Load() != 1 || owned > muxMaxStreams*(muxMaxFrame+6)+6 || streams > muxMaxStreams { h.t.Fatal("unbounded or missing retained writer") }
	h.event("cancel_snapshot", map[string]any{"parent_deadline_changes": h.serverWire.deadlineChanges.Load(), "parent_close_calls": h.serverWire.closes.Load(), "queued": queued, "cancel_elapsed_ns": elapsed.Nanoseconds(), "cancel_bound_ns": encCancelBound.Nanoseconds(), "operation_canceled": true, "operation_done": encCancelReady(op.done), "active_callers": h.activeCallers.Load(), "caller_closed": errors.Is(call.err, net.ErrClosed), "caller_bytes": call.n, "caller_joined": encCancelReady(call.done), "parent_alive": true, "parent_writers": writers, "blocked_pipe_writers": blocked, "active_transport_writes": h.serverWire.active.Load(), "streams": streams, "owned_frames": frames, "owned_bytes": owned, "handler_callbacks_finished": h.handler.finished.Load(), "handler_callbacks_active": h.handler.active.Load()})
}

// Inspect only existing real wrappers. No nonce is modified or copied. This
// supports debug's ExtendedReader/WriterWrapper layers as well as normal mode.
func encCancelNonces(t *testing.T, value any, writer bool) (data, length uint16) {
	t.Helper(); foundData, foundLength := false, false
	for depth := 0; value != nil && depth < 24; depth++ {
		switch v := value.(type) {
		case *AEADWriter: if !writer { t.Fatal("writer in reader chain") }; data, foundData = v.nonceCount, true
		case *AEADChunkWriter: if !writer || v.globalPadding == nil { t.Fatal("missing writer padding/auth length") }; length, foundLength = v.nonceCount, true
		case *AEADReader: if writer { t.Fatal("reader in writer chain") }; data, foundData = v.nonceCount, true
		case *AEADChunkReader: if writer || v.globalPadding == nil { t.Fatal("missing reader padding/auth length") }; length, foundLength = v.nonceCount, true
		}
		if foundData && foundLength { return }
		if up, ok := value.(interface{ UpstreamReader() any }); ok { value = up.UpstreamReader() } else if up, ok := value.(interface{ Upstream() any }); ok { value = up.Upstream() } else { break }
	}
	t.Fatalf("real AEAD data/authenticated-length chain missing: writer=%v", writer); return
}
func (h *encCancelHarness) nonceCheck() {
	h.t.Helper(); h.quiescent()
	sw, sl := encCancelNonces(h.t, h.parent.writer, true)
	cr, cl := encCancelNonces(h.t, h.client.reader, false)
	cw, cwl := encCancelNonces(h.t, h.client.writer, true)
	sr, srl := encCancelNonces(h.t, h.parent.reader, false)
	if int(sw) != h.responseChunks || sw != sl || sw != cr || sw != cl || int(cw) != h.requestChunks || cw != cwl || cw != sr || cw != srl { h.t.Fatalf("nonce/chunk discontinuity response=%d/%d/%d/%d want=%d request=%d/%d/%d/%d want=%d", sw, sl, cr, cl, h.responseChunks, cw, cwl, sr, srl, h.requestChunks) }
	if h.serverWire.completedBytes.Load() != h.clientWire.physicalRead.Load() { h.t.Fatal("unread encrypted output remains") }
	h.event("cipher_continuity", map[string]any{"response_chunks": h.responseChunks, "request_chunks": h.requestChunks, "server_data_nonce": sw, "server_length_nonce": sl, "client_data_nonce": cr, "client_length_nonce": cl, "upload_data_nonce": cw, "upload_length_nonce": cwl, "service_data_nonce": sr, "service_length_nonce": srl, "physical_bytes_exact": true})
}

func (h *encCancelHarness) warmup(c *serverMuxPacketConn) {
	body := []byte("response-handshake-and-full-chunk-warmup")
	d := h.udpWrite(c, body); h.data(c.sessionID, body, true); h.wait(d, len(body), false)
	h.nonceCheck()
	// A warmup is one complete mux frame and one complete cipher chunk, including
	// its padding. All physical bytes have been consumed; no concurrent read exists.
	h.event("warmup_complete", map[string]any{"command_mux": true, "global_padding": true, "authenticated_length": true, "decoder_reads_serial": true, "response_chunks": h.responseChunks, "ciphertext_fully_consumed": true})
}

func (h *encCancelHarness) siblings(udp *serverMuxPacketConn, tcp *serverMuxConn) {
	up := encCancelPayload(60000, 0x32)
	d := h.send(udp.sessionID, StatusKeep, NetworkUDP, up)
	got := make([]byte, 65535)
	readUDP := h.start(func() (int, error) {
		n, address, err := udp.ReadFrom(got)
		if err == nil && (address == nil || address.String() != encCancelTarget.String() || n != len(up) || !bytes.Equal(got[:n], up)) { err = fmt.Errorf("sibling UDP upload changed n=%d addr=%v", n, address) }
		return n, err
	})
	h.wait(readUDP, len(up), false)
	h.waitSend(d, udp.sessionID, StatusKeep, NetworkUDP, up)
	tcpUp := encCancelPayload(32768, 0x76)
	d = h.send(tcp.sessionID, StatusKeep, NetworkTCP, tcpUp)
	got = make([]byte, len(tcpUp))
	readTCP := h.start(func() (int, error) {
		n, err := io.ReadFull(tcp, got)
		if err == nil && !bytes.Equal(got, tcpUp) { err = errors.New("sibling TCP upload changed") }
		return n, err
	})
	h.wait(readTCP, len(tcpUp), false)
	h.waitSend(d, tcp.sessionID, StatusKeep, NetworkTCP, tcpUp)
	udpDown, tcpDown := encCancelPayload(60000, 0x53), encCancelPayload(70007, 0x91)
	u := h.udpWrite(udp, udpDown)
	h.data(udp.sessionID, udpDown, true); h.wait(u, len(udpDown), false)
	tc := h.tcpWrite(tcp, tcpDown)
	for pos := 0; pos < len(tcpDown); { end := pos + muxMaxPayload; if end > len(tcpDown) { end = len(tcpDown) }; h.data(tcp.sessionID, tcpDown[pos:end], false); pos = end }
	h.wait(tc, len(tcpDown), false)
	h.assertAlive(); h.nonceCheck()
	h.event("siblings_recovered", map[string]any{"same_parent": udp.session == h.session && tcp.session == h.session, "udp_upload": len(up), "udp_reply": len(udpDown), "tcp_upload": len(tcpUp), "tcp_reply": len(tcpDown), "tuple_payload_exact": true, "parent_alive": true})
}

// Admit a fresh stream only after the canceled stream's End and the existing
// siblings have fully recovered. This is a real encrypted StatusNew, not reuse
// of a pre-created handle or a call to newStream from the test.
func (h *encCancelHarness) freshSession() {
	h.assertAlive()
	fresh := h.openUDP(4)
	up, down := encCancelPayload(4097, 0x44), encCancelPayload(32768, 0x84)
	d := h.send(4, StatusKeep, NetworkUDP, up)
	got := make([]byte, 65535)
	read := h.start(func() (int, error) {
		n, address, err := fresh.ReadFrom(got)
		if err == nil && (address == nil || address.String() != encCancelTarget.String() || n != len(up) || !bytes.Equal(got[:n], up)) { err = fmt.Errorf("fresh UDP upload changed n=%d addr=%v", n, address) }
		return n, err
	})
	h.wait(read, len(up), false); h.waitSend(d, 4, StatusKeep, NetworkUDP, up)
	reply := h.udpWrite(fresh, down)
	h.data(4, down, true); h.wait(reply, len(down), false)
	h.close(fresh); h.end(4)
	h.stateWait(func() bool { return h.session.streams[4] == nil }, "fresh stream End completion")
	h.assertAlive(); h.nonceCheck()
	h.event("fresh_session_recovered", map[string]any{"session_id": 4, "actual_encrypted_new": true, "same_parent": fresh.session == h.session, "udp_upload": len(up), "udp_reply": len(down), "tuple_payload_exact": true, "end_count": h.ends[4], "handler_callbacks_finished": h.handler.finished.Load(), "parent_alive": true})
}

func (h *encCancelHarness) closeSurvivors(udp *serverMuxPacketConn, tcp *serverMuxConn) {
	h.close(udp); h.end(udp.sessionID)
	h.close(tcp); h.end(tcp.sessionID)
	h.stateWait(func() bool { return len(h.session.streams) == 0 && len(h.session.queue) == 0 }, "all exactly-once End completions")
	h.quiescent(); h.assertAlive(); h.nonceCheck()
	if len(h.ends) != 4 { h.t.Fatalf("wrong End identity count %v", h.ends) }
	for _, n := range h.ends { if n != 1 { h.t.Fatal("nonunique End") } }
	h.event("drained_before_parent_close", map[string]any{"parent_alive": true, "end_count": len(h.ends), "each_end_once": true, "active_transport_writes": h.serverWire.active.Load(), "active_callers": h.activeCallers.Load(), "handler_callbacks_active": h.handler.active.Load()})
}

func (h *encCancelHarness) closeParent(realError bool, callers ...*encCancelCall) {
	h.t.Helper(); h.assertAlive()
	h.event("parent_close", map[string]any{"real_peer_close": true, "while_write_blocked": realError, "outer_context_alive": h.ctx.Err() == nil})
	_ = h.clientWire.Conn.Close() // Actual peer closure; no synthetic error or context cancellation.
	h.waitChannel(h.serviceDone, "Service")
	h.waitChannel(h.session.writerDone, "fixed parent writer")
	if h.serviceErr == nil || h.ctx.Err() != nil { h.t.Fatalf("missing independent transport error: service=%v outer=%v", h.serviceErr, h.ctx.Err()) }
	if realError && h.serverWire.failedWrites.Load() == 0 { h.t.Fatal("blocked real transport Write did not report an error") }
	for _, caller := range callers { h.wait(caller, 0, true); h.verifiedParentErrorCallers++ }
	h.joinAll()
}
func (h *encCancelHarness) joinAll() {
	h.t.Helper()
	all := make(chan struct{}); go func() { h.workers.Wait(); close(all) }()
	h.waitChannel(all, "all test callers")
	encCancelEventually(h.t, func() bool { w, _, c, own, _ := encCancelStacks(h.t); return w == 0 && c == 0 && own == 0 }, "fixed writer, watcher, callback and caller goroutine exit")
	h.session.streamAccess.Lock()
	clean := h.session.closed && len(h.session.streams) == 0 && len(h.session.queue) == 0 && h.session.reject == nil
	h.session.streamAccess.Unlock()
	if !clean || h.serverWire.active.Load() != 0 || h.serverWire.maximum.Load() != 1 || h.activeCallers.Load() != 0 || h.handler.active.Load() != 0 || int(h.handler.finished.Load()) != h.admitted { h.t.Fatal("unjoined encrypted parent ownership") }
	h.joined = true
	h.event("parent_joined", map[string]any{"parent_error_callers_closed": h.verifiedParentErrorCallers, "service_joined": true, "writer_joined": true, "watcher_joined": true, "all_test_workers_joined": true, "handler_callbacks_finished": h.handler.finished.Load(), "handler_callbacks_expected": h.admitted, "active_transport_writes": h.serverWire.active.Load(), "active_callers": h.activeCallers.Load(), "retained_streams": 0, "retained_queue": 0, "real_write_errors": h.serverWire.failedWrites.Load(), "service_error": fmt.Sprint(h.serviceErr), "outer_context_alive": h.ctx.Err() == nil})
}

func encCancelRun(t *testing.T, security, scenario string) {
	h := newEncCancelHarness(t, security)
	a, b, tcp := h.openUDP(1), h.openUDP(2), h.openTCP(3)
	h.warmup(a)
	body := encCancelPayload(65535, 0xa7)
	saved := bytes.Clone(body)
	first := h.udpWrite(a, body)
	prefixBytes := 1
	if scenario == "PartialCiphertextPayload" || scenario == "QueuedCancel" || scenario == "ParentTransportError" { prefixBytes = 2 + CipherOverhead + 37 }
	prefix := h.capture(prefixBytes)
	inflight := h.pending(a.stream)
	if scenario == "ParentTransportError" {
		second := h.udpWrite(b, []byte("queued UDP before real parent error")); h.queued(b.stream)
		third := h.tcpWrite(tcp, []byte("queued TCP before real parent error")); h.queued(tcp.stream)
		h.event("parent_error_snapshot", map[string]any{"parent_alive": true, "pending_callers": h.activeCallers.Load(), "queued_callers": 2, "blocked_pipe_writers": 1, "no_context_cancel": true})
		h.closeParent(true, first, second, third)
		return
	}
	if scenario == "CompletionBeforeClose" {
		h.restored(prefix); h.data(1, saved, true); h.wait(first, len(saved), false)
		if !encCancelReady(inflight.done) || inflight.err != nil { t.Fatal("real operation not complete before Close") }
		h.event("completion_before_close", map[string]any{"caller_success": true, "caller_bytes": first.n, "operation_done": true, "parent_alive": true})
		h.close(a)
		if err := inflight.wait(a.stream.done, h.session.ctx.Done()); err != nil { t.Fatal("later Close changed successful real operation", err) }
		h.end(1); h.siblings(b, tcp); h.freshSession(); h.closeSurvivors(b, tcp); h.closeParent(false); return
	}
	if scenario == "QueuedCancel" {
		queued := h.udpWrite(b, []byte("must-not-reach-encrypted-wire")); op := h.queued(b.stream)
		started := time.Now(); h.close(b); h.canceledSnapshot(queued, op, true, started)
		h.restored(prefix); h.data(1, saved, true); h.wait(first, len(saved), false); h.end(2)
		h.siblings(a, tcp); h.freshSession(); h.closeSurvivors(a, tcp); h.closeParent(false); return
	}
	// The caller returns while the real cipher still owns an unfinished chunk.
	started := time.Now(); h.close(a); h.canceledSnapshot(first, inflight, false, started)
	for i := range body { body[i] ^= 0xff }
	// Concurrent repeat closes must not append another End.
	closes := make([]*encCancelCall, 8)
	for i := range closes { closes[i] = h.start(func() (int, error) { return 0, a.Close() }) }
	for _, c := range closes { h.wait(c, 0, false) }
	h.restored(prefix); h.data(1, saved, true); h.end(1)
	h.waitChannel(inflight.done, "canceled in-flight ownership")
	if !errors.Is(inflight.err, net.ErrClosed) { t.Fatal("later wire completion overwrote cancellation") }
	h.siblings(b, tcp); h.freshSession(); h.closeSurvivors(b, tcp); h.closeParent(false)
}

func encryptedCancellationSuite(t *testing.T, security string) {
	for _, scenario := range []string{"PartialAuthenticatedLength", "PartialCiphertextPayload", "QueuedCancel", "CompletionBeforeClose", "ParentTransportError"} {
		t.Run(scenario, func(t *testing.T) { encCancelRun(t, security, scenario) })
	}
}
func TestMuxEncryptedCancelAES(t *testing.T) { encryptedCancellationSuite(t, "aes-128-gcm") }
func TestMuxEncryptedCancelChaCha(t *testing.T) { encryptedCancellationSuite(t, "chacha20-poly1305") }
