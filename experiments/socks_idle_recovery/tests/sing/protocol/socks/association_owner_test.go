package socks

import (
	std_bufio "bufio"
	"bytes"
	"context"
	"encoding/binary"
	"errors"
	"fmt"
	"io"
	"math"
	"net"
	"os"
	"sort"
	"sync"
	"sync/atomic"
	"syscall"
	"testing"
	"time"

	"github.com/sagernet/sing/common/buf"
	M "github.com/sagernet/sing/common/metadata"
	N "github.com/sagernet/sing/common/network"
	"github.com/sagernet/sing/protocol/socks/socks5"
)

var ownerPeer = M.ParseSocksaddr("127.0.0.1:40001")
var ownerTarget = M.ParseSocksaddr("192.0.2.10:443")
var ownerOther = M.ParseSocksaddr("198.51.100.11:444")

type ownerTestBarrier struct { C chan struct{}; once sync.Once }
func newOwnerTestBarrier() *ownerTestBarrier { return &ownerTestBarrier{C: make(chan struct{})} }
func (b *ownerTestBarrier) Open() { b.once.Do(func() { close(b.C) }) }

type ownerTestHarness struct {
	mu sync.Mutex
	barriers []*ownerTestBarrier
	callers []<-chan struct{}
	closing bool
	joined atomic.Bool
	shutdown func()
	afterJoin func()
}
var ownerTestHarnesses sync.Map

func newOwnerTestHarness(t *testing.T, shutdown func()) *ownerTestHarness {
	t.Helper()
	h := &ownerTestHarness{shutdown: shutdown}
	if a, ok := buf.DefaultAllocator.(*ownerAllocator); ok {
		a.mu.Lock(); a.harnesses = append(a.harnesses, h); a.mu.Unlock()
	}
	t.Cleanup(func() {
		h.mu.Lock()
		h.closing = true
		barriers := append([]*ownerTestBarrier(nil), h.barriers...)
		callers := append([]<-chan struct{}(nil), h.callers...)
		h.mu.Unlock()
		for _, barrier := range barriers { barrier.Open() }
		h.shutdown()
		timer := time.NewTimer(3 * time.Second)
		defer timer.Stop()
		for _, returned := range callers {
			select {
			case <-returned:
			case <-timer.C:
				t.Error("test caller/supervisor did not join during cleanup")
				return
			}
		}
		h.joined.Store(true)
		if h.afterJoin != nil { h.afterJoin() }
	})
	return h
}
func (h *ownerTestHarness) registerBarrier(b *ownerTestBarrier) {
	h.mu.Lock()
	closing := h.closing
	if !closing { h.barriers = append(h.barriers, b) }
	h.mu.Unlock()
	if closing { b.Open() }
}
func (h *ownerTestHarness) launch(call func()) {
	returned := make(chan struct{})
	h.mu.Lock()
	if h.closing { h.mu.Unlock(); panic("test caller launched after cleanup") }
	h.callers = append(h.callers, returned)
	h.mu.Unlock()
	go func() { defer close(returned); call() }()
}
func ownerHarness(o *udpAssociation) *ownerTestHarness {
	value, ok := ownerTestHarnesses.Load(o)
	if !ok { panic("missing owner test harness") }
	return value.(*ownerTestHarness)
}
func ownerLaunch(o *udpAssociation, call func()) { ownerHarness(o).launch(call) }
func ownerRegisterBarrier(o *udpAssociation, b *ownerTestBarrier) { ownerHarness(o).registerBarrier(b) }
func ownerHandlerBarrier(o *udpAssociation) *ownerTestBarrier {
	b := newOwnerTestBarrier(); ownerRegisterBarrier(o, b); return b
}
func ownerAwait[T any](t *testing.T, channel <-chan T) T {
	t.Helper()
	select {
	case value := <-channel: return value
	case <-time.After(3 * time.Second):
		t.Fatal("test barrier/result timed out")
		var zero T
		return zero
	}
}

type ownerWire struct { data []byte; source net.Addr }
type ownerRelay struct {
	mu sync.Mutex
	changed chan struct{}
	closed bool
	closes int
	reads int
	maxReads int
	readDeadline time.Time
	writeDeadline time.Time
	blockWrite bool
	writing bool
	writes []ownerWire
	input chan ownerWire
}
func newOwnerRelay() *ownerRelay { return &ownerRelay{changed: make(chan struct{}), input: make(chan ownerWire, 8)} }
func (r *ownerRelay) signalLocked() { close(r.changed); r.changed = make(chan struct{}) }
func (r *ownerRelay) ReadFrom(data []byte) (int, net.Addr, error) {
	r.mu.Lock(); r.reads++; if r.reads > r.maxReads { r.maxReads = r.reads }; r.signalLocked(); r.mu.Unlock()
	defer func() { r.mu.Lock(); r.reads--; r.signalLocked(); r.mu.Unlock() }()
	for {
		r.mu.Lock()
		if r.closed { r.mu.Unlock(); return 0, nil, net.ErrClosed }
		deadline, changed := r.readDeadline, r.changed
		r.mu.Unlock()
		if !deadline.IsZero() && !time.Now().Before(deadline) { return 0, nil, os.ErrDeadlineExceeded }
		var timer *time.Timer
		var timeout <-chan time.Time
		if !deadline.IsZero() { timer = time.NewTimer(time.Until(deadline)); timeout = timer.C }
		select {
		case wire := <-r.input:
			if timer != nil { timer.Stop() }
			return copy(data, wire.data), wire.source, nil
		case <-changed:
		case <-timeout:
		}
		if timer != nil { timer.Stop() }
	}
}
func (r *ownerRelay) WriteTo(data []byte, source net.Addr) (int, error) {
	r.mu.Lock(); r.writing = true; r.signalLocked(); r.mu.Unlock()
	defer func() { r.mu.Lock(); r.writing = false; r.signalLocked(); r.mu.Unlock() }()
	for {
		r.mu.Lock()
		if r.closed { r.mu.Unlock(); return 0, net.ErrClosed }
		deadline, changed := r.writeDeadline, r.changed
		if !deadline.IsZero() && !time.Now().Before(deadline) { r.mu.Unlock(); return 0, os.ErrDeadlineExceeded }
		if !r.blockWrite {
			r.writes = append(r.writes, ownerWire{data: append([]byte(nil), data...), source: source})
			r.signalLocked(); r.mu.Unlock(); return len(data), nil
		}
		r.mu.Unlock()
		var timer *time.Timer
		var timeout <-chan time.Time
		if !deadline.IsZero() { timer = time.NewTimer(time.Until(deadline)); timeout = timer.C }
		select { case <-changed: case <-timeout: }
		if timer != nil { timer.Stop() }
	}
}
func (r *ownerRelay) Close() error { r.mu.Lock(); r.closes++; r.closed = true; r.signalLocked(); r.mu.Unlock(); return nil }
func (r *ownerRelay) LocalAddr() net.Addr { return M.ParseSocksaddr("127.0.0.1:40002").UDPAddr() }
func (r *ownerRelay) SetReadDeadline(deadline time.Time) error { r.mu.Lock(); r.readDeadline = deadline; r.signalLocked(); r.mu.Unlock(); return nil }
func (r *ownerRelay) SetWriteDeadline(deadline time.Time) error { r.mu.Lock(); r.writeDeadline = deadline; r.signalLocked(); r.mu.Unlock(); return nil }
func (r *ownerRelay) SetDeadline(deadline time.Time) error { _ = r.SetReadDeadline(deadline); return r.SetWriteDeadline(deadline) }

type ownerHandler func(context.Context, N.PacketConn, M.Socksaddr, M.Socksaddr, N.CloseHandlerFunc)
func (h ownerHandler) NewConnectionEx(context.Context, net.Conn, M.Socksaddr, M.Socksaddr, N.CloseHandlerFunc) { panic("unexpected TCP route") }
func (h ownerHandler) NewPacketConnectionEx(ctx context.Context, conn N.PacketConn, source, target M.Socksaddr, close N.CloseHandlerFunc) { h(ctx, conn, source, target, close) }
func ownerFixture(t *testing.T, handler ownerHandler) (*udpAssociation, *ownerRelay, net.Conn) {
	t.Helper()
	server, client := net.Pipe()
	relay := newOwnerRelay()
	o := newUDPAssociation(context.Background(), server, std_bufio.NewReader(server), relay, handler, time.Minute, ownerPeer)
	h := newOwnerTestHarness(t, func() { o.fail(net.ErrClosed); _ = client.Close() })
	ownerTestHarnesses.Store(o, h)
	h.afterJoin = func() { ownerTestHarnesses.Delete(o) }
	return o, relay, client
}
func ownerRun(o *udpAssociation) <-chan error {
	result := make(chan error, 1)
	ownerLaunch(o, func() { result <- o.run() })
	return result
}
func ownerChild(o *udpAssociation) *associationPacketConn {
	ctx, cancel := context.WithCancel(o.ctx)
	c := &associationPacketConn{owner: o, id: 1, ctx: ctx, cancel: cancel, admissionDeadline: time.Now().Add(time.Minute)}
	o.mu.Lock(); o.child = c; o.generation = 1; o.state = associationAdmitting; if o.pending != nil { o.pending.generation = c.id }; o.mu.Unlock()
	return c
}
func ownerAdmit(t *testing.T, c *associationPacketConn) {
	t.Helper()
	if err := c.AttachPacketSessionAbort(func() error { return nil }); err != nil { t.Fatal(err) }
	if err := c.PacketSessionAdmitted(); err != nil { t.Fatal(err) }
}
func ownerPacket(payload []byte, target M.Socksaddr) *buf.Buffer {
	b := buf.NewSize(N.MaxPacketSize)
	header := b.Extend(10)
	header[0], header[1], header[2], header[3] = 0, 0, 0, 1
	address := target.Addr.As4(); copy(header[4:8], address[:]); binary.BigEndian.PutUint16(header[8:10], target.Port)
	_, _ = b.Write(payload)
	return b
}
func ownerReply(payload []byte) *buf.Buffer {
	b := buf.NewSize(N.MaxPacketSize + 10); b.Resize(10, 0); _, _ = b.Write(payload); return b
}
func ownerIngest(t *testing.T, o *udpAssociation, payload []byte, target M.Socksaddr) {
	t.Helper(); if err := o.ingest(ownerPacket(payload, target), ownerPeer); err != nil { t.Fatal(err) }
}
func ownerWait(t *testing.T, o *udpAssociation, predicate func() bool) {
	t.Helper(); timer := time.NewTimer(3 * time.Second); defer timer.Stop()
	for { o.mu.Lock(); ok, changed := predicate(), o.changed; o.mu.Unlock(); if ok { return }; select { case <-changed: case <-timer.C: t.Fatal("owner state timed out") } }
}
func ownerWaitWrite(t *testing.T, r *ownerRelay) {
	t.Helper(); timer := time.NewTimer(3 * time.Second); defer timer.Stop()
	for { r.mu.Lock(); writing, changed := r.writing, r.changed; r.mu.Unlock(); if writing { return }; select { case <-changed: case <-timer.C: t.Fatal("relay write did not start") } }
}
func ownerResult(t *testing.T, result <-chan error) error { return ownerAwait(t, result) }
func ownerRead(c *associationPacketConn) error {
	b := (N.ReadWaitOptions{FrontHeadroom: 286, RearHeadroom: 96}).NewPacketBuffer(); defer b.Release()
	_, err := c.ReadPacket(b); if err == nil { c.PacketSessionWriteFinished(nil) }; return err
}
func ownerCleanHandler(ctx context.Context, conn N.PacketConn, source, target M.Socksaddr, callback N.CloseHandlerFunc) {
	c := conn.(*associationPacketConn)
	if err := c.AttachPacketSessionAbort(func() error { return nil }); err != nil { callback(err); return }
	if err := c.PacketSessionAdmitted(); err != nil { callback(err); return }
	err := ownerRead(c)
	if err == nil { err = N.ErrPacketSessionEnded }
	c.BeginPacketSessionRetire(err); _ = c.Close(); callback(err)
}

type ownerControl struct { net.Conn }
func (c ownerControl) LocalAddr() net.Addr { return M.ParseSocksaddr("127.0.0.1:1080").TCPAddr() }
func (c ownerControl) RemoteAddr() net.Addr { return ownerPeer.TCPAddr() }
type ownerListener struct { calls atomic.Int32; relay *ownerRelay }
func (l *ownerListener) ListenPacket(net.ListenConfig, context.Context, string, string) (net.PacketConn, error) { l.calls.Add(1); return l.relay, nil }

type ownerAllocator struct {
	upstream buf.Allocator
	mu sync.Mutex
	live map[*byte]int
	bytes int
	peak int
	gets int
	puts int
	harnesses []*ownerTestHarness
}
func (a *ownerAllocator) Get(size int) []byte {
	data := a.upstream.Get(size)
	if len(data) == 0 { return data }
	a.mu.Lock(); defer a.mu.Unlock()
	key := &data[:cap(data)][0]
	if _, exists := a.live[key]; exists { panic("allocator duplicate live backing") }
	a.live[key] = cap(data); a.bytes += cap(data); a.gets++; if a.bytes > a.peak { a.peak = a.bytes }
	return data
}
func (a *ownerAllocator) Put(data []byte) error {
	if len(data) == 0 { return a.upstream.Put(data) }
	a.mu.Lock(); key := &data[:cap(data)][0]; size, exists := a.live[key]
	if !exists { a.mu.Unlock(); return errors.New("allocator unknown or double release") }
	delete(a.live, key); a.bytes -= size; a.puts++; a.mu.Unlock()
	return a.upstream.Put(data)
}
func ownerTrackAllocator(t *testing.T) *ownerAllocator {
	t.Helper()
	a := &ownerAllocator{upstream: buf.DefaultAllocator, live: make(map[*byte]int)}
	buf.DefaultAllocator = a
	t.Cleanup(func() {
		a.mu.Lock(); harnesses := append([]*ownerTestHarness(nil), a.harnesses...); a.mu.Unlock()
		for _, h := range harnesses {
			if !h.joined.Load() { t.Error("allocator restoration withheld: test caller/supervisor is unjoined"); return }
		}
		buf.DefaultAllocator = a.upstream
		a.mu.Lock(); defer a.mu.Unlock()
		if len(a.live) != 0 || a.bytes != 0 || a.gets != a.puts { t.Errorf("allocator imbalance: live=%d bytes=%d gets=%d puts=%d", len(a.live), a.bytes, a.gets, a.puts) }
	})
	return a
}

func TestSOCKSOwnerCompletePacket(t *testing.T) {
	for _, size := range []int{0, 1, 32768, 60000, 65497} {
		t.Run(fmt.Sprint(size), func(t *testing.T) {
			ownerTrackAllocator(t)
			o, relay, _ := ownerFixture(t, nil)
			c := ownerChild(o); ownerAdmit(t, c)
			payload := bytes.Repeat([]byte{byte(size + 19)}, size)
			ownerIngest(t, o, payload, ownerTarget)
			b := (N.ReadWaitOptions{FrontHeadroom: 286, RearHeadroom: 96}).NewPacketBuffer(); defer b.Release()
			target, err := c.ReadPacket(b)
			if err != nil || target != ownerTarget || !bytes.Equal(b.Bytes(), payload) { t.Fatalf("complete packet: %v target=%v length=%d", err, target, b.Len()) }
			c.PacketSessionWriteFinished(nil)
			if err := c.WritePacket(ownerReply(payload), ownerTarget); err != nil { t.Fatal(err) }
			relay.mu.Lock(); writes := append([]ownerWire(nil), relay.writes...); relay.mu.Unlock()
			if len(writes) != 1 || len(writes[0].data) != size+10 || !bytes.Equal(writes[0].data[10:], payload) || M.SocksaddrFromNet(writes[0].source).Unwrap() != ownerPeer { t.Fatal("reply mismatch") }
			if binary.BigEndian.Uint16(writes[0].data[8:10]) != ownerTarget.Port || !bytes.Equal(writes[0].data[4:8], ownerTarget.Addr.AsSlice()) { t.Fatal("reply target mismatch") }
		})
	}
}
func TestSOCKSOwnerShortBuffer(t *testing.T) {
	ownerTrackAllocator(t)
	o, _, _ := ownerFixture(t, nil); c := ownerChild(o); ownerAdmit(t, c)
	ownerIngest(t, o, []byte{1, 2}, ownerTarget)
	b := buf.NewSize(1); defer b.Release()
	if _, err := c.ReadPacket(b); err != N.ErrPacketTooLarge || b.Len() != 0 { t.Fatalf("prefix exposed: %v %d", err, b.Len()) }
	if o.cause != N.ErrPacketTooLarge { t.Fatal(o.cause) }
}
func TestSOCKSOwnerSinglePendingOverflow(t *testing.T) {
	ownerTrackAllocator(t)
	o, _, _ := ownerFixture(t, nil)
	ownerIngest(t, o, []byte{1}, ownerTarget)
	if err := o.ingest(ownerPacket([]byte{2}, ownerTarget), ownerPeer); err != ErrUDPAssociationOverloaded { t.Fatal(err) }
	if o.pending == nil || o.pending.buffer.Byte(0) != 1 { t.Fatal("pending packet replaced") }
}
func TestSOCKSOwnerSourceAndTarget(t *testing.T) {
	t.Run("bind_and_remap", func(t *testing.T) {
		o, _, _ := ownerFixture(t, nil); o.peer.Port = 0; c := ownerChild(o); ownerAdmit(t, c)
		ownerIngest(t, o, []byte{1}, ownerTarget)
		if err := ownerRead(c); err != nil { t.Fatal(err) }
		ownerIngest(t, o, []byte{2}, ownerOther)
		b := buf.NewSize(10); defer b.Release(); target, err := c.ReadPacket(b)
		if err != nil || target != ownerTarget || o.peer != ownerPeer || o.lastRequested != ownerOther || o.remapped != 1 { t.Fatal("source binding or initial-target remap changed") }
		c.PacketSessionWriteFinished(nil)
	})
	for _, name := range []string{"hint_rejects_other_port", "wrong_ip"} {
		t.Run(name, func(t *testing.T) {
			o, _, _ := ownerFixture(t, nil); before := o.lastActivity; source := ownerPeer
			if name == "wrong_ip" { source = M.ParseSocksaddr("127.0.0.2:40001") } else { source.Port++ }
			if err := o.ingest(ownerPacket([]byte{1}, ownerTarget), source); err != nil { t.Fatal(err) }
			if o.pending != nil || o.rejectedSource != 1 || !o.lastActivity.Equal(before) || o.sequence != 0 { t.Fatal("rejected source changed owner") }
		})
	}
}
func TestSOCKSOwnerMalformed(t *testing.T) {
	for _, name := range []string{"rsv", "fragment", "domain", "ipv6", "short", "oversize", "zero_port"} {
		t.Run(name, func(t *testing.T) {
			ownerTrackAllocator(t)
			o, _, _ := ownerFixture(t, nil); b := ownerPacket([]byte{1}, ownerTarget)
			switch name {
			case "rsv": b.SetByte(0, 1)
			case "fragment": b.SetByte(2, 1)
			case "domain": b.SetByte(3, 3)
			case "ipv6": b.SetByte(3, 4)
			case "short": b.Truncate(9)
			case "oversize": b.Extend(65508 - b.Len())
			case "zero_port": b.SetByte(8, 0); b.SetByte(9, 0)
			}
			if err := o.ingest(b, ownerPeer); err != ErrUDPAssociationMalformed { t.Fatal(err) }
			if o.pending != nil || o.sequence != 0 { t.Fatal("malformed packet accepted") }
		})
	}
}
func TestSOCKSOwnerEndFences(t *testing.T) {
	for _, name := range []string{"pending", "inflight", "completed", "raw_eof"} {
		t.Run(name, func(t *testing.T) {
			ownerTrackAllocator(t)
			o, _, _ := ownerFixture(t, nil); c := ownerChild(o); ownerAdmit(t, c)
			ownerIngest(t, o, []byte{1}, ownerTarget)
			b := buf.NewSize(10); defer b.Release()
			if name != "pending" { if _, err := c.ReadPacket(b); err != nil { t.Fatal(err) } }
			if name == "completed" || name == "raw_eof" { c.PacketSessionWriteFinished(nil) }
			cause, want := N.ErrPacketSessionEnded, N.ErrPacketSessionEnded
			switch name { case "pending": want = N.ErrPendingAtSessionEnd; case "inflight": want = N.ErrPacketOutcomeUnknown; case "raw_eof": cause, want = io.EOF, io.EOF }
			c.BeginPacketSessionRetire(cause); _ = c.Close()
			if c.cause != want { t.Fatalf("cause=%v want=%v", c.cause, want) }
			if name == "completed" {
				ownerIngest(t, o, []byte{2}, ownerOther)
				if o.pending.generation != 0 || o.state != associationRetiring { t.Fatal("post-fence packet attached to old generation") }
			} else if o.state != associationClosing { t.Fatal("uncertain end survived") }
		})
	}
}
func TestSOCKSOwnerAttachAndAdmission(t *testing.T) {
	for _, name := range []string{"close_before_attach", "attach_before_close", "duplicate_attach", "expired_admission"} {
		t.Run(name, func(t *testing.T) {
			o, _, _ := ownerFixture(t, nil); c := ownerChild(o); var aborts atomic.Int32
			abort := func() error { aborts.Add(1); return nil }
			if name == "close_before_attach" { o.fail(net.ErrClosed); if c.AttachPacketSessionAbort(abort) == nil { t.Fatal("late abort attachment accepted") } } else {
				if err := c.AttachPacketSessionAbort(abort); err != nil { t.Fatal(err) }
				switch name {
				case "duplicate_attach": if c.AttachPacketSessionAbort(abort) == nil { t.Fatal("duplicate attachment accepted") }; o.fail(net.ErrClosed)
				case "expired_admission": c.admissionDeadline = time.Now().Add(-time.Second); if err := c.PacketSessionAdmitted(); err != ErrUDPAssociationAdmissionTimeout { t.Fatal(err) }
				default: o.fail(net.ErrClosed)
				}
			}
			want := int32(1); if name == "duplicate_attach" { want = 2 }
			if aborts.Load() != want { t.Fatalf("aborts=%d want=%d", aborts.Load(), want) }
			if c.PacketSessionAdmitted() == nil { t.Fatal("closed child admitted") }
		})
	}
}
func TestSOCKSOwnerNoEscapeInterfaces(t *testing.T) {
	o, _, _ := ownerFixture(t, nil); c := ownerChild(o); var value any = c
	checks := []bool{}
	_, ok := value.(interface{ Upstream() any }); checks = append(checks, ok)
	_, ok = value.(N.WithUpstreamReader); checks = append(checks, ok)
	_, ok = value.(N.WithUpstreamWriter); checks = append(checks, ok)
	_, ok = value.(N.PacketReadWaiter); checks = append(checks, ok)
	_, ok = value.(N.PacketReadWaitCreator); checks = append(checks, ok)
	_, ok = value.(N.PacketBatchReadWaiter); checks = append(checks, ok)
	_, ok = value.(N.PacketBatchReadWaitCreator); checks = append(checks, ok)
	_, ok = value.(N.PacketBatchWriter); checks = append(checks, ok)
	_, ok = value.(syscall.Conn); checks = append(checks, ok)
	_, ok = value.(net.PacketConn); checks = append(checks, ok)
	for index, escaped := range checks { if escaped { t.Fatalf("escape interface %d", index) } }
	if c.FrontHeadroom() != 10 || c.ReaderOverhead() != 0 { t.Fatal("incorrect packet geometry") }
}
func TestSOCKSOwnerReplyCancellation(t *testing.T) {
	for _, name := range []string{"end", "control_close", "deadline"} {
		t.Run(name, func(t *testing.T) {
			ownerTrackAllocator(t)
			o, relay, _ := ownerFixture(t, nil); c := ownerChild(o); ownerAdmit(t, c)
			ownerIngest(t, o, []byte{1}, ownerTarget); if err := ownerRead(c); err != nil { t.Fatal(err) }
			relay.blockWrite = true
			result := make(chan error, 1); ownerLaunch(o, func() { result <- c.WritePacket(ownerReply([]byte{2}), ownerTarget) })
			ownerWaitWrite(t, relay)
			switch name { case "end": c.BeginPacketSessionRetire(N.ErrPacketSessionEnded); case "control_close": o.fail(io.EOF); case "deadline": if err := c.SetWriteDeadline(time.Now().Add(-time.Second)); err != nil { t.Fatal(err) } }
			if err := ownerResult(t, result); err == nil { t.Fatal("blocked relay write succeeded") }
			if name == "end" && (c.cause != N.ErrPacketSessionEnded || o.state != associationRetiring) { t.Fatal("retire interruption changed clean end") }
		})
	}
}
func TestSOCKSOwnerReplyGuards(t *testing.T) {
	for _, name := range []string{"target", "oversize", "headroom", "stale"} {
		t.Run(name, func(t *testing.T) {
			ownerTrackAllocator(t)
			o, relay, _ := ownerFixture(t, nil); c := ownerChild(o); ownerAdmit(t, c); o.target = ownerTarget
			b, target, want := ownerReply([]byte{1}), ownerTarget, N.ErrPacketTooLarge
			switch name { case "target": target, want = ownerOther, ErrUDPAssociationTarget; case "oversize": b.Extend(65498-b.Len()); case "headroom": b.Release(); b = buf.NewSize(1); _, _ = b.Write([]byte{1}); case "stale": c.retiring = true; want = net.ErrClosed }
			if err := c.WritePacket(b, target); err != want { t.Fatalf("got %v want %v", err, want) }
			if len(relay.writes) != 0 { t.Fatal("invalid reply reached relay") }
			if name == "stale" && o.state >= associationClosing { t.Fatal("stale facade terminated owner") }
		})
	}
}
func TestSOCKSOwnerGenerationWrap(t *testing.T) {
	o, _, _ := ownerFixture(t, ownerCleanHandler); o.generation = math.MaxUint64
	ownerIngest(t, o, []byte{1}, ownerTarget)
	if err := ownerResult(t, ownerRun(o)); err != N.ErrPacketSessionLifecycle { t.Fatal(err) }
	if o.generation != math.MaxUint64 || o.child != nil { t.Fatal("generation reused") }
}
func TestSOCKSOwnerCallbackReturnFence(t *testing.T) {
	first := make(chan *associationPacketConn, 1); release := newOwnerTestBarrier()
	handler := ownerHandler(func(ctx context.Context, conn N.PacketConn, source, target M.Socksaddr, callback N.CloseHandlerFunc) {
		c := conn.(*associationPacketConn)
		ownerCleanHandler(ctx, conn, source, target, callback)
		if c.id == 1 { first <- c; <-release.C }
	})
	o, _, client := ownerFixture(t, handler); ownerRegisterBarrier(o, release)
	result := ownerRun(o); ownerIngest(t, o, []byte{1}, ownerTarget)
	old := ownerAwait(t, first)
	ownerIngest(t, o, []byte{2}, ownerOther)
	o.mu.Lock(); generation, state, pendingGeneration := o.generation, o.state, o.pending.generation; o.mu.Unlock()
	if generation != 1 || state != associationRetiring || pendingGeneration != 0 { t.Fatal("callback started generation before invocation returned") }
	release.Open(); ownerWait(t, o, func() bool { return o.generation == 2 && o.state == associationDormant })
	old.PacketSessionWriteFinished(errors.New("stale error")); _ = old.Close()
	if err := old.WritePacket(buf.NewSize(1), ownerOther); err != net.ErrClosed { t.Fatal(err) }
	o.mu.Lock(); closed := o.state >= associationClosing; o.mu.Unlock(); if closed { t.Fatal("stale child closed new owner") }
	_ = client.Close(); if err := ownerResult(t, result); !errors.Is(err, io.EOF) { t.Fatal(err) }
}
func TestSOCKSOwnerMissingCallback(t *testing.T) {
	o, _, _ := ownerFixture(t, func(ctx context.Context, conn N.PacketConn, source, target M.Socksaddr, callback N.CloseHandlerFunc) { ownerCleanHandler(ctx, conn, source, target, func(error) {}) })
	result := ownerRun(o); ownerIngest(t, o, []byte{1}, ownerTarget)
	if err := ownerResult(t, result); err != N.ErrPacketSessionLifecycle { t.Fatal(err) }
}
func TestSOCKSOwnerUnjoinedInvocation(t *testing.T) {
	entered := make(chan struct{}, 1); release := newOwnerTestBarrier()
	o, _, client := ownerFixture(t, func(ctx context.Context, conn N.PacketConn, source, target M.Socksaddr, callback N.CloseHandlerFunc) { entered <- struct{}{}; <-release.C; callback(ctx.Err()) })
	ownerRegisterBarrier(o, release); result := ownerRun(o); ownerIngest(t, o, []byte{1}, ownerTarget); ownerAwait(t, entered)
	_ = client.Close(); ownerWait(t, o, func() bool { return o.state == associationClosing })
	select { case <-result: t.Fatal("supervisor returned with invocation unjoined"); default: }
	release.Open(); if err := ownerResult(t, result); !errors.Is(err, io.EOF) { t.Fatal(err) }
}
func TestSOCKSOwnerIdleStates(t *testing.T) {
	for _, name := range []string{"await_first", "dormant", "admitting", "active"} {
		t.Run(name, func(t *testing.T) {
			o, _, _ := ownerFixture(t, nil)
			switch name { case "dormant": o.state = associationDormant; case "admitting": ownerChild(o); case "active": c := ownerChild(o); ownerAdmit(t, c) }
			o.timeout = time.Second; o.lastActivity = time.Now().Add(-2*time.Second)
			if err := ownerResult(t, ownerRun(o)); err != ErrUDPAssociationIdleTimeout { t.Fatal(err) }
		})
	}
}
func TestSOCKSOwnerAdmissionAbort(t *testing.T) {
	entered := make(chan *associationPacketConn, 1); var aborts atomic.Int32
	o, _, _ := ownerFixture(t, func(ctx context.Context, conn N.PacketConn, source, target M.Socksaddr, callback N.CloseHandlerFunc) {
		c := conn.(*associationPacketConn); err := c.AttachPacketSessionAbort(func() error { aborts.Add(1); return nil }); if err != nil { callback(err); return }; entered <- c; <-ctx.Done(); callback(ctx.Err())
	})
	result := ownerRun(o); ownerIngest(t, o, []byte{1}, ownerTarget); c := ownerAwait(t, entered)
	o.mu.Lock(); c.admissionDeadline = time.Now().Add(-time.Second); o.signalLocked(); o.mu.Unlock()
	if err := ownerResult(t, result); err != ErrUDPAssociationAdmissionTimeout || aborts.Load() != 1 { t.Fatalf("cause=%v aborts=%d", err, aborts.Load()) }
}
func TestSOCKSOwnerControlBufferedByte(t *testing.T) {
	o, _, _ := ownerFixture(t, nil); o.controlReader = std_bufio.NewReader(bytes.NewReader([]byte{7}))
	if err := ownerResult(t, ownerRun(o)); err != ErrUDPAssociationMalformed { t.Fatal(err) }
}

func TestSOCKSOwnerRepeatedRecovery(t *testing.T) {
	a := ownerTrackAllocator(t)
	outcomes := make(chan error, 1)
	handler := ownerHandler(func(ctx context.Context, conn N.PacketConn, source, target M.Socksaddr, callback N.CloseHandlerFunc) {
		c := conn.(*associationPacketConn)
		err := c.AttachPacketSessionAbort(func() error { return nil })
		if err == nil { err = c.PacketSessionAdmitted() }
		if err == nil {
			b := (N.ReadWaitOptions{FrontHeadroom: 286, RearHeadroom: 96}).NewPacketBuffer()
			actual, readErr := c.ReadPacket(b)
			err = readErr
			if err == nil && (actual != ownerTarget || source != ownerPeer || target != ownerTarget) { err = errors.New("generation route metadata changed") }
			if err == nil { c.PacketSessionWriteFinished(nil); err = c.WritePacket(ownerReply(b.Bytes()), actual) }
			b.Release()
		}
		outcomes <- err
		if err == nil { err = N.ErrPacketSessionEnded }
		c.BeginPacketSessionRetire(err); _ = c.Close(); callback(err)
	})
	o, relay, client := ownerFixture(t, handler); result := ownerRun(o)
	sizes := []int{0, 1, 32768, 60000, 65497}
	for index := 0; index < 256; index++ {
		payload := bytes.Repeat([]byte{byte(index)}, sizes[index%len(sizes)])
		target := ownerTarget; if index%2 != 0 { target = ownerOther }
		packet := ownerPacket(payload, target); wire := append([]byte(nil), packet.Bytes()...); packet.Release()
		select { case relay.input <- ownerWire{data: wire, source: ownerPeer.UDPAddr()}: case <-time.After(3*time.Second): t.Fatal("ingress blocked") }
		if err := ownerResult(t, outcomes); err != nil { t.Fatalf("generation %d: %v", index+1, err) }
		ownerWait(t, o, func() bool { return o.state == associationDormant && o.generation == uint64(index+1) })
		relay.mu.Lock()
		writes := len(relay.writes); var got ownerWire; if writes != 0 { got = relay.writes[writes-1] }; closes, readers := relay.closes, relay.maxReads
		relay.mu.Unlock()
		if writes != index+1 || len(got.data) < 10 || !bytes.Equal(got.data[10:], payload) || M.SocksaddrFromNet(got.source).Unwrap() != ownerPeer || closes != 0 || readers > 1 { t.Fatalf("generation %d relay invariant", index+1) }
	}
	_ = client.Close(); if err := ownerResult(t, result); !errors.Is(err, io.EOF) { t.Fatal(err) }
	o.mu.Lock(); valid := o.generations == 256 && o.pending == nil && o.state == associationClosed && o.remapped == 128; o.mu.Unlock()
	if !valid { t.Fatal("recovery count or ownership mismatch") }
	relay.mu.Lock(); closes := relay.closes; relay.mu.Unlock(); if closes != 1 { t.Fatalf("relay closed %d times", closes) }
	a.mu.Lock(); live, peak := a.bytes, a.peak; a.mu.Unlock()
	if live != 0 || peak > 344064 { t.Fatalf("backing live=%d peak=%d", live, peak) }
}
func TestSOCKSOwnerStorageBudget(t *testing.T) {
	a := ownerTrackAllocator(t)
	o, _, _ := ownerFixture(t, nil); c := ownerChild(o); ownerAdmit(t, c)
	ownerIngest(t, o, []byte{1}, ownerTarget)
	upload := (N.ReadWaitOptions{FrontHeadroom: 286, RearHeadroom: 96}).NewPacketBuffer(); defer upload.Release()
	if _, err := c.ReadPacket(upload); err != nil { t.Fatal(err) }
	ownerIngest(t, o, []byte{2}, ownerTarget)
	construction := buf.NewSize(N.MaxPacketSize); defer construction.Release()
	probe := buf.NewSize(N.MaxPacketSize+1); defer probe.Release()
	download := (N.ReadWaitOptions{FrontHeadroom: 286, RearHeadroom: 96}).NewPacketBuffer(); defer download.Release()
	a.mu.Lock(); live := a.bytes; a.mu.Unlock()
	if live != 344064 { t.Fatalf("five bounded backings=%d want344064", live) }
	// The existing construction slot is rejected and consumed, never queued.
	construction.Release()
	if err := o.ingest(ownerPacket([]byte{3}, ownerTarget), ownerPeer); err != ErrUDPAssociationOverloaded { t.Fatal(err) }
	c.PacketSessionWriteFinished(io.ErrClosedPipe)
}
func TestSOCKSOwnerLimiterBeforeAllocation(t *testing.T) {
	var active atomic.Int32
	acquire := func() (func(), bool) {
		for { old := active.Load(); if old >= 8 { return nil, false }; if active.CompareAndSwap(old, old+1) { var once sync.Once; return func() { once.Do(func() { active.Add(-1) }) }, true } }
	}
	var slots []func()
	for index := 0; index < 8; index++ { release, ok := acquire(); if !ok { t.Fatal("premature limit") }; slots = append(slots, release); defer release() }
	server, client := net.Pipe(); relay := newOwnerRelay(); listener := &ownerListener{relay: relay}
	h := newOwnerTestHarness(t, func() { _ = client.Close(); _ = server.Close(); _ = relay.Close() })
	_ = client.SetDeadline(time.Now().Add(3*time.Second))
	result := make(chan error, 1)
	h.launch(func() { result <- handleUDPAssociation(context.Background(), ownerControl{server}, std_bufio.NewReader(server), ownerHandler(ownerCleanHandler), listener, time.Minute, M.ParseSocksaddr("0.0.0.0:0"), &UDPAssociationOptions{Acquire: acquire}) })
	response, err := socks5.ReadResponse(std_bufio.NewReader(client))
	if err != nil || response.ReplyCode != socks5.ReplyCodeFailure { t.Fatalf("limit response: %v %+v", err, response) }
	if err := ownerResult(t, result); err != ErrUDPAssociationOverloaded || listener.calls.Load() != 0 || active.Load() != 8 { t.Fatal("ninth owner allocated relay or stole slot") }
	slots[0](); release, ok := acquire(); if !ok || active.Load() != 8 { t.Fatal("joined owner slot not reusable") }; release()
}
func ownerHandshake(t *testing.T, client net.Conn) socks5.Response {
	t.Helper(); reader := std_bufio.NewReader(client)
	if err := socks5.WriteAuthRequest(client, socks5.AuthRequest{Methods: []byte{socks5.AuthTypeNotRequired}}); err != nil { t.Fatal(err) }
	if _, err := socks5.ReadAuthResponse(reader); err != nil { t.Fatal(err) }
	if err := socks5.WriteRequest(client, socks5.Request{Command: socks5.CommandUDPAssociate, Destination: M.ParseSocksaddr("0.0.0.0:0")}); err != nil { t.Fatal(err) }
	response, err := socks5.ReadResponse(reader); if err != nil { t.Fatal(err) }; return response
}
func TestSOCKSOwnerLegacyOff(t *testing.T) {
	for _, name := range []string{"legacy_entrypoint", "nil_options"} {
		t.Run(name, func(t *testing.T) {
			server, client := net.Pipe(); relay := newOwnerRelay(); listener := &ownerListener{relay: relay}
			h := newOwnerTestHarness(t, func() { _ = client.Close(); _ = server.Close(); _ = relay.Close() }); _ = client.SetDeadline(time.Now().Add(3*time.Second))
			called := make(chan error, 1)
			handler := ownerHandler(func(ctx context.Context, conn N.PacketConn, source, target M.Socksaddr, callback N.CloseHandlerFunc) {
				var err error
				if _, opted := conn.(N.PacketSessionLifecycle); opted { err = errors.New("legacy gained lifecycle") }
				b := buf.NewSize(20); actual, readErr := conn.ReadPacket(b)
				if readErr != nil { err = readErr } else if actual != ownerTarget || !bytes.Equal(b.Bytes(), []byte{7}) { err = errors.New("legacy payload changed") }
				b.Release(); _ = conn.Close(); called <- err
			})
			result := make(chan error, 1)
			h.launch(func() {
				if name == "legacy_entrypoint" { result <- HandleConnectionEx(context.Background(), ownerControl{server}, std_bufio.NewReader(server), nil, handler, listener, 0, ownerPeer, nil) } else { result <- HandleConnectionExWithUDPAssociation(context.Background(), ownerControl{server}, std_bufio.NewReader(server), nil, handler, listener, 0, ownerPeer, nil, nil) }
			})
			if response := ownerHandshake(t, client); response.ReplyCode != socks5.ReplyCodeSuccess { t.Fatal(response) }
			b := ownerPacket([]byte{7}, ownerTarget); wire := append([]byte(nil), b.Bytes()...); b.Release(); relay.input <- ownerWire{data: wire, source: ownerPeer.UDPAddr()}
			if err := ownerResult(t, called); err != nil { t.Fatal(err) }; if err := ownerResult(t, result); err != nil { t.Fatal(err) }
		})
	}
}
func TestSOCKSOwnerResponseFailureReleases(t *testing.T) {
	ownerTrackAllocator(t)
	server, client := net.Pipe(); relay := newOwnerRelay(); listener := &ownerListener{relay: relay}
	newOwnerTestHarness(t, func() { _ = client.Close(); _ = server.Close(); _ = relay.Close() })
	_ = client.Close(); var releases atomic.Int32
	err := handleUDPAssociation(context.Background(), ownerControl{server}, std_bufio.NewReader(server), ownerHandler(ownerCleanHandler), listener, time.Minute, M.ParseSocksaddr("0.0.0.0:0"), &UDPAssociationOptions{Acquire: func() (func(), bool) { return func() { releases.Add(1) }, true }})
	if err == nil || releases.Load() != 1 || listener.calls.Load() != 1 || relay.closes != 1 { t.Fatal("response failure leaked relay or slot") }
}
func TestSOCKSOwnerFinalizeFailure(t *testing.T) {
	failure := errors.New("test finalizer failure")
	o, _, _ := ownerFixture(t, func(ctx context.Context, conn N.PacketConn, source, target M.Socksaddr, callback N.CloseHandlerFunc) {
		ownerCleanHandler(ctx, conn, source, target, func(error) { callback(failure) })
	})
	result := ownerRun(o); ownerIngest(t, o, []byte{1}, ownerTarget)
	if err := ownerResult(t, result); err != failure { t.Fatalf("finalizer error ignored: %v", err) }
}
func TestSOCKSOwnerConcurrentIngress(t *testing.T) {
	ownerTrackAllocator(t)
	o, _, _ := ownerFixture(t, nil); start := ownerHandlerBarrier(o); results := make(chan error, 2)
	for index := 0; index < 2; index++ { ownerLaunch(o, func() { <-start.C; results <- o.ingest(ownerPacket([]byte{1}, ownerTarget), ownerPeer) }) }
	start.Open(); a, b := ownerResult(t, results), ownerResult(t, results)
	if !((a == nil && b == ErrUDPAssociationOverloaded) || (b == nil && a == ErrUDPAssociationOverloaded)) { t.Fatalf("two ingress results %v %v", a, b) }
	if o.accepted != 1 || o.sequence != 1 || o.pending == nil { t.Fatal("more than one pending slot") }
}
func TestSOCKSOwnerChildReadDeadline(t *testing.T) {
	o, relay, _ := ownerFixture(t, nil); c := ownerChild(o); ownerAdmit(t, c)
	physical := time.Now().Add(time.Hour); _ = relay.SetReadDeadline(physical)
	if err := c.SetReadDeadline(time.Now().Add(-time.Second)); err != nil { t.Fatal(err) }
	b := buf.NewSize(10); defer b.Release()
	if _, err := c.ReadPacket(b); !errors.Is(err, os.ErrDeadlineExceeded) { t.Fatal(err) }
	if relay.readDeadline != physical || o.state != associationActive { t.Fatal("child deadline modified owner relay") }
	if err := c.SetReadDeadline(time.Time{}); err != nil { t.Fatal(err) }
	ownerIngest(t, o, []byte{1}, ownerTarget); if _, err := c.ReadPacket(b); err != nil { t.Fatal(err) }; c.PacketSessionWriteFinished(nil)
}
func TestSOCKSOwnerCloseRaces(t *testing.T) {
	for _, name := range []string{"first_packet", "raw_attach", "admitted"} {
		t.Run(name, func(t *testing.T) {
			ownerTrackAllocator(t)
			o, _, _ := ownerFixture(t, nil); c := ownerChild(o); start := ownerHandlerBarrier(o); results := make(chan error, 2); var aborts atomic.Int32
			if name == "admitted" { if err := c.AttachPacketSessionAbort(func() error { aborts.Add(1); return nil }); err != nil { t.Fatal(err) } }
			ownerLaunch(o, func() { <-start.C; o.fail(io.EOF); results <- nil })
			ownerLaunch(o, func() {
				<-start.C
				switch name { case "first_packet": results <- o.ingest(ownerPacket([]byte{1}, ownerTarget), ownerPeer); case "raw_attach": results <- c.AttachPacketSessionAbort(func() error { aborts.Add(1); return nil }); case "admitted": results <- c.PacketSessionAdmitted() }
			})
			start.Open(); _ = ownerResult(t, results); _ = ownerResult(t, results)
			o.mu.Lock(); closed, pending, cause := o.state == associationClosing, o.pending, o.cause; o.mu.Unlock()
			if !closed || pending != nil || cause != io.EOF { t.Fatal("close lost terminal ownership") }
			if name != "first_packet" && aborts.Load() != 1 { t.Fatalf("raw abort count %d", aborts.Load()) }
		})
	}
}
func TestSOCKSOwnerReplyDeadlineCannotExtend(t *testing.T) {
	o, relay, _ := ownerFixture(t, nil); c := ownerChild(o); ownerAdmit(t, c); o.target = ownerTarget; relay.blockWrite = true
	result := make(chan error, 1); ownerLaunch(o, func() { result <- c.WritePacket(ownerReply([]byte{1}), ownerTarget) }); ownerWaitWrite(t, relay)
	o.mu.Lock(); ceiling := o.replyLimit; o.mu.Unlock()
	for index := 0; index < 10; index++ {
		if err := c.SetWriteDeadline(time.Now().Add(time.Hour)); err != nil { t.Fatal(err) }
		relay.mu.Lock(); got := relay.writeDeadline; relay.mu.Unlock(); if got.After(ceiling) { t.Fatal("reply ceiling extended") }
	}
	if err := c.SetWriteDeadline(time.Time{}); err != nil { t.Fatal(err) }
	relay.mu.Lock(); got := relay.writeDeadline; relay.mu.Unlock(); if got.After(ceiling) || got.IsZero() { t.Fatal("zero deadline removed reply ceiling") }
	c.BeginPacketSessionRetire(N.ErrPacketSessionEnded); if err := ownerResult(t, result); err == nil { t.Fatal("retiring write survived") }
}
func TestSOCKSOwnerUnboundMalformed(t *testing.T) {
	ownerTrackAllocator(t)
	o, _, _ := ownerFixture(t, nil); o.peer.Port = 0; before := o.lastActivity
	b := ownerPacket([]byte{1}, ownerTarget); b.SetByte(2, 1)
	if err := o.ingest(b, ownerPeer); err != nil { t.Fatal(err) }
	if o.peer.Port != 0 || o.sequence != 0 || o.pending != nil || o.rejectedMalformed != 1 || !o.lastActivity.Equal(before) { t.Fatal("unbound malformed packet bound/refreshed owner") }
	ownerIngest(t, o, []byte{1}, ownerTarget)
	bad := ownerPacket([]byte{1}, ownerTarget); bad.SetByte(2, 1)
	if err := o.ingest(bad, ownerPeer); err != ErrUDPAssociationMalformed { t.Fatal(err) }
}

func TestSOCKSOwnerLifecycleEvents(t *testing.T) {
	var mu sync.Mutex; var events []UDPAssociationEvent
	o, _, client := ownerFixture(t, ownerCleanHandler)
	o.observe = func(event UDPAssociationEvent) {
		// Taking the mutex here verifies the observer is outside the state lock.
		o.mu.Lock(); o.mu.Unlock()
		mu.Lock(); events = append(events, event); mu.Unlock()
	}
	result := ownerRun(o)
	ownerIngest(t, o, []byte{1}, ownerTarget); ownerWait(t, o, func() bool { return o.state == associationDormant && o.generation == 1 })
	ownerIngest(t, o, []byte{2}, ownerOther); ownerWait(t, o, func() bool { return o.state == associationDormant && o.generation == 2 })
	_ = client.Close(); if err := ownerResult(t, result); !errors.Is(err, io.EOF) { t.Fatal(err) }
	mu.Lock(); snapshot := append([]UDPAssociationEvent(nil), events...); mu.Unlock()
	sort.Slice(snapshot, func(i, j int) bool { return snapshot[i].Sequence < snapshot[j].Sequence })
	want := []string{"create", "admitting", "admitted", "retire", "join", "dormant", "admitting", "admitted", "retire", "join", "dormant", "closing", "closed"}
	if len(snapshot) != len(want) { t.Fatalf("events=%d want%d: %+v", len(snapshot), len(want), snapshot) }
	for index, event := range snapshot {
		if event.Sequence != uint64(index+1) || event.Transition != want[index] || event.Source != ownerPeer || event.Relay != M.ParseSocksaddr("127.0.0.1:40002") { t.Fatalf("event %d: %+v", index, event) }
		if event.Transition == "dormant" && event.Cause != "none" { t.Fatal("dormant cause contract") }
		if event.Transition == "retire" || event.Transition == "join" { if event.Cause != "session_end" { t.Fatal(event) } }
	}
	last := snapshot[len(snapshot)-1]
	if last.Cause != "eof" || last.Generation != 2 || last.PacketSequence != 2 || last.AcceptedPackets != 2 || last.RemappedPackets != 1 || last.Target != ownerTarget { t.Fatalf("closed event: %+v", last) }
}
func TestSOCKSOwnerSlotReleasedAfterClosedObserver(t *testing.T) {
	server, client := net.Pipe(); relay := newOwnerRelay(); listener := &ownerListener{relay: relay}
	h := newOwnerTestHarness(t, func() { _ = client.Close(); _ = server.Close(); _ = relay.Close() })
	_ = client.SetDeadline(time.Now().Add(3*time.Second))
	barrier := newOwnerTestBarrier(); h.registerBarrier(barrier); entered := make(chan UDPAssociationEvent, 1); var releases atomic.Int32
	options := &UDPAssociationOptions{Acquire: func() (func(), bool) { return func() { releases.Add(1) }, true }, Observe: func(event UDPAssociationEvent) { if event.Transition == "closed" { entered <- event; <-barrier.C } }}
	result := make(chan error, 1)
	h.launch(func() { result <- handleUDPAssociation(context.Background(), ownerControl{server}, std_bufio.NewReader(server), ownerHandler(ownerCleanHandler), listener, time.Minute, M.ParseSocksaddr("0.0.0.0:0"), options) })
	response, err := socks5.ReadResponse(std_bufio.NewReader(client)); if err != nil || response.ReplyCode != socks5.ReplyCodeSuccess { t.Fatalf("response: %v %+v", err, response) }
	_ = client.Close(); event := ownerAwait(t, entered)
	if event.Cause != "eof" || releases.Load() != 0 { t.Fatal("slot released before closed observer returned") }
	select { case <-result: t.Fatal("handler returned before closed observer"); default: }
	barrier.Open(); if err := ownerResult(t, result); !errors.Is(err, io.EOF) || releases.Load() != 1 { t.Fatal("final join did not release slot") }
}
func TestSOCKSOwnerEventSequenceCannotWrap(t *testing.T) {
	var mu sync.Mutex; var events []UDPAssociationEvent
	o, _, _ := ownerFixture(t, ownerCleanHandler)
	// Simulate a valid completed prior generation retaining the final two IDs.
	o.state = associationDormant; o.eventSequence = math.MaxUint64-7
	o.observe = func(event UDPAssociationEvent) { mu.Lock(); events = append(events, event); mu.Unlock() }
	ownerIngest(t, o, []byte{1}, ownerTarget)
	if err := ownerResult(t, ownerRun(o)); err != N.ErrPacketSessionLifecycle { t.Fatal(err) }
	mu.Lock(); snapshot := append([]UDPAssociationEvent(nil), events...); mu.Unlock()
	if len(snapshot) != 3 || o.generation != 0 { t.Fatal("event space exhaustion admitted generation") }
	sort.Slice(snapshot, func(i, j int) bool { return snapshot[i].Sequence < snapshot[j].Sequence })
	for index, event := range snapshot { if event.Sequence != math.MaxUint64-6+uint64(index) { t.Fatal("event sequence wrapped/reused") } }
	if snapshot[1].Transition != "closing" || snapshot[2].Transition != "closed" { t.Fatal("terminal events missing") }
}
func TestSOCKSOwnerEventSequenceLastGeneration(t *testing.T) {
	var mu sync.Mutex; var events []UDPAssociationEvent
	o, _, _ := ownerFixture(t, ownerCleanHandler)
	// create leaves exactly seven IDs: all five generation events plus closing/closed.
	o.state = associationDormant; o.eventSequence = math.MaxUint64-8
	o.observe = func(event UDPAssociationEvent) { mu.Lock(); events = append(events, event); mu.Unlock() }
	result := ownerRun(o); ownerIngest(t, o, []byte{1}, ownerTarget)
	ownerWait(t, o, func() bool { return o.state == associationDormant && o.generation == 1 })
	ownerIngest(t, o, []byte{2}, ownerTarget)
	if err := ownerResult(t, result); err != N.ErrPacketSessionLifecycle { t.Fatal(err) }
	mu.Lock(); snapshot := append([]UDPAssociationEvent(nil), events...); mu.Unlock()
	sort.Slice(snapshot, func(i, j int) bool { return snapshot[i].Sequence < snapshot[j].Sequence })
	want := []string{"create", "admitting", "admitted", "retire", "join", "dormant", "closing", "closed"}
	if len(snapshot) != len(want) || o.generation != 1 { t.Fatalf("last generation did not complete: %+v", snapshot) }
	for index, event := range snapshot { if event.Sequence != math.MaxUint64-7+uint64(index) || event.Transition != want[index] { t.Fatalf("last sequence: %+v", event) } }
}
func TestSOCKSOwnerErrorCallbackBeforeReturn(t *testing.T) {
	failure := errors.New("post-join finalizer error")
	entered := make(chan struct{}, 1); release := newOwnerTestBarrier()
	o, _, _ := ownerFixture(t, func(ctx context.Context, conn N.PacketConn, source, target M.Socksaddr, callback N.CloseHandlerFunc) {
		ownerCleanHandler(ctx, conn, source, target, func(error) { callback(failure) })
		entered <- struct{}{}; <-release.C
	})
	ownerRegisterBarrier(o, release); result := ownerRun(o); ownerIngest(t, o, []byte{1}, ownerTarget); ownerAwait(t, entered)
	o.mu.Lock(); state, cause := o.state, o.cause; o.mu.Unlock()
	if state != associationClosing || cause != failure { t.Fatal("error callback failed to terminate owner immediately") }
	select { case <-result: t.Fatal("terminal callback substituted for invocation join"); default: }
	release.Open(); if err := ownerResult(t, result); err != failure { t.Fatal(err) }
}
