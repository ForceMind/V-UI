package bufio

import (
	"bytes"
	"errors"
	"fmt"
	"net"
	"sync"
	"syscall"
	"testing"
	"time"

	"github.com/sagernet/sing/common/buf"
	M "github.com/sagernet/sing/common/metadata"
	N "github.com/sagernet/sing/common/network"
)

// A named net.PacketConn field intentionally hides UDPConn.SyscallConn,
// ReadMsgUDP, and its other concrete methods. Reads remain real UDP loopback.
type packetReceiveHiddenConn struct { net.PacketConn }

// This allocator counts ownership, including unused batch slots. It does not
// infer releases from RSS, GC, or pool reuse. Tests are intentionally serial.
type packetReceiveAllocator struct {
	mu sync.Mutex
	live map[*byte]int
	gets int
	badPuts int
	liveBytes int
	peakBytes int
	maxLive int
}
func (a *packetReceiveAllocator) Get(size int) []byte {
	if size <= 0 { return nil }
	p := make([]byte, size)
	a.mu.Lock()
	a.live[&p[0]] = size; a.gets++; a.liveBytes += size
	if a.liveBytes > a.peakBytes { a.peakBytes = a.liveBytes }
	if len(a.live) > a.maxLive { a.maxLive = len(a.live) }
	a.mu.Unlock()
	return p
}
func (a *packetReceiveAllocator) Put(p []byte) error {
	a.mu.Lock(); defer a.mu.Unlock()
	if len(p) == 0 { a.badPuts++; return fmt.Errorf("empty allocation returned") }
	size, ok := a.live[&p[0]]
	if !ok { a.badPuts++; return fmt.Errorf("foreign or repeated allocation returned") }
	a.liveBytes -= size; delete(a.live, &p[0]); return nil
}
func packetReceiveTrack(t *testing.T) *packetReceiveAllocator {
	t.Helper()
	previous := buf.DefaultAllocator
	a := &packetReceiveAllocator{live: make(map[*byte]int)}
	buf.DefaultAllocator = a
	// Fixture cleanup and all joins registered later run before this cleanup.
	t.Cleanup(func() { a.assertEmpty(t); buf.DefaultAllocator = previous })
	return a
}
func (a *packetReceiveAllocator) assertEmpty(t *testing.T) {
	t.Helper(); a.mu.Lock(); defer a.mu.Unlock()
	if len(a.live) != 0 || a.liveBytes != 0 || a.badPuts != 0 { t.Errorf("allocator outstanding=%d live_bytes=%d bad_returns=%d gets=%d", len(a.live), a.liveBytes, a.badPuts, a.gets) }
}

func (a *packetReceiveAllocator) assertPeak(t *testing.T, bytes, count int) {
	t.Helper(); a.mu.Lock(); defer a.mu.Unlock()
	if a.peakBytes != bytes || a.maxLive != count { t.Errorf("allocator peak_bytes=%d max_live=%d, want %d/%d", a.peakBytes, a.maxLive, bytes, count) }
	t.Logf("tracked_logical_peak_bytes=%d tracked_max_live=%d tracked_live_bytes=%d tracked_live_count=%d", a.peakBytes, a.maxLive, a.liveBytes, len(a.live))
}

type packetReceiveFixture struct {
	receiver *net.UDPConn
	sender *net.UDPConn
	connected bool
}
func packetReceivePair(t *testing.T, connected bool) *packetReceiveFixture {
	t.Helper()
	sender, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.IPv4(127, 0, 0, 1)})
	if err != nil { t.Fatal(err) }
	t.Cleanup(func() { sender.Close() })
	var receiver *net.UDPConn
	if connected { receiver, err = net.DialUDP("udp4", nil, sender.LocalAddr().(*net.UDPAddr)) } else {
		receiver, err = net.ListenUDP("udp4", &net.UDPAddr{IP: net.IPv4(127, 0, 0, 1)})
	}
	if err != nil { t.Fatal(err) }
	t.Cleanup(func() { receiver.Close() })
	if err := receiver.SetReadBuffer(1 << 20); err != nil { t.Fatal(err) }
	if err := receiver.SetDeadline(time.Now().Add(4*time.Second)); err != nil { t.Fatal(err) }
	if err := sender.SetDeadline(time.Now().Add(4*time.Second)); err != nil { t.Fatal(err) }
	return &packetReceiveFixture{receiver: receiver, sender: sender, connected: connected}
}
func (f *packetReceiveFixture) send(t *testing.T, p []byte) {
	t.Helper(); n, err := f.sender.WriteToUDP(p, f.receiver.LocalAddr().(*net.UDPAddr))
	if err != nil || n != len(p) { t.Fatalf("send %d bytes: n=%d err=%v", len(p), n, err) }
}
func packetReceivePayload(n int) []byte {
	p := make([]byte, n)
	for i := range p { p[i] = byte((i*37 + 11) % 251) }
	return p
}

type packetReceiveResult struct { b *buf.Buffer; destination M.Socksaddr; err error }
type packetReceiveReader func() packetReceiveResult

func packetReceiveBuild(t *testing.T, path string, f *packetReceiveFixture, options N.ReadWaitOptions) packetReceiveReader {
	t.Helper()
	switch path {
	case "direct", "fallback":
		var c N.NetPacketConn
		if path == "fallback" {
			hidden := &packetReceiveHiddenConn{f.receiver}
			if _, exposed := any(hidden).(syscall.Conn); exposed { t.Fatal("fallback exposed syscall.Conn") }
			c = NewPacketConn(hidden)
			if _, ok := c.(*ExtendedPacketConn); !ok { t.Fatalf("fallback selected %T", c) }
			if _, ok := CreatePacketReadWaiter(c); ok { t.Fatal("fallback unexpectedly selected a waiter") }
		} else { c = NewPacketConn(f.receiver) }
		return func() packetReceiveResult {
			b := options.NewPacketBuffer(); d, err := c.ReadPacket(b)
			if err == nil { options.PostReturn(b) }
			return packetReceiveResult{b, d, err}
		}
	case "single_waiter", "connected_waiter":
		var c N.PacketReader = NewPacketConn(f.receiver)
		if path == "connected_waiter" { c = NewUnbindPacketConn(f.receiver) }
		w, ok := CreatePacketReadWaiter(c)
		if !ok { t.Fatalf("required waiter unavailable: %s", path) }
		w.InitializeReadWaiter(options)
		return func() packetReceiveResult { b, d, err := w.WaitReadPacket(); return packetReceiveResult{b, d, err} }
	case "connected_read_buffer":
		c := NewExtendedConn(f.receiver)
		if _, ok := c.(*ExtendedUDPConn); !ok { t.Fatalf("connected UDP selected stream adapter %T", c) }
		return func() packetReceiveResult {
			b := options.NewPacketBuffer(); err := c.ReadBuffer(b)
			if err == nil { options.PostReturn(b) }
			var d M.Socksaddr
			if err == nil { d = M.SocksaddrFromNet(f.sender.LocalAddr()).Unwrap() }
			return packetReceiveResult{b, d, err}
		}
	default: t.Fatalf("unknown path %s", path); return nil
	}
}
func packetReceiveCheck(t *testing.T, r packetReceiveResult, want []byte, wantAddr M.Socksaddr, options N.ReadWaitOptions) {
	t.Helper()
	if r.b != nil { defer r.b.Release() }
	if r.err != nil || r.b == nil { t.Fatalf("receive buffer=%v error=%v", r.b != nil, r.err) }
	if !bytes.Equal(r.b.Bytes(), want) { t.Fatalf("payload changed: got len %d want len %d", r.b.Len(), len(want)) }
	if r.destination != wantAddr { t.Fatalf("source=%v want=%v", r.destination, wantAddr) }
	if r.b.Start() != options.FrontHeadroom || r.b.FreeLen() < options.RearHeadroom {
		t.Fatalf("headroom start/free=%d/%d want=%d/%d", r.b.Start(), r.b.FreeLen(), options.FrontHeadroom, options.RearHeadroom)
	}
}
func packetReceiveCheckOversize(t *testing.T, r packetReceiveResult) {
	t.Helper()
	if r.b != nil { defer r.b.Release() }
	if !errors.Is(r.err, N.ErrPacketTooLarge) { t.Fatalf("oversize error=%v, want ErrPacketTooLarge", r.err) }
	if r.b != nil && r.b.Len() != 0 { t.Fatalf("oversize exposed %d bytes of prefix", r.b.Len()) }
	if r.destination.IsValid() { t.Fatalf("oversize exposed destination %v", r.destination) }
}

// Exact fit and limit+1 are adjacent receives on the same reader. The next
// valid packet and zero-length datagram prove recovery and datagram semantics.
func TestVUICompletePacketDatagram(t *testing.T) {
	for _, path := range []string{"direct", "fallback", "single_waiter", "connected_read_buffer", "connected_waiter"} {
		t.Run(path, func(t *testing.T) {
			packetReceiveTrack(t)
			f := packetReceivePair(t, path == "connected_read_buffer" || path == "connected_waiter")
			o := N.ReadWaitOptions{MTU: 1024, ReadOverhead: 17, FrontHeadroom: 31, RearHeadroom: 33}
			read := packetReceiveBuild(t, path, f, o)
			addr := M.SocksaddrFromNet(f.sender.LocalAddr()).Unwrap()
			fit := packetReceivePayload(1041)
			f.send(t, fit); packetReceiveCheck(t, read(), fit, addr, o)
			f.send(t, packetReceivePayload(1042)); packetReceiveCheckOversize(t, read())
			f.send(t, fit); packetReceiveCheck(t, read(), fit, addr, o)
			f.send(t, nil); packetReceiveCheck(t, read(), nil, addr, o)
			f.send(t, []byte("after-zero")); packetReceiveCheck(t, read(), []byte("after-zero"), addr, o)
		})
	}
}
func TestVUICompletePacketDefaultBudget(t *testing.T) {
	for _, path := range []string{"direct", "fallback", "single_waiter", "connected_read_buffer", "connected_waiter"} {
		t.Run(path, func(t *testing.T) {
			packetReceiveTrack(t)
			f := packetReceivePair(t, path == "connected_read_buffer" || path == "connected_waiter")
			o := N.ReadWaitOptions{FrontHeadroom: 31, RearHeadroom: 351, ReadOverhead: 382}
			read := packetReceiveBuild(t, path, f, o)
			p := packetReceivePayload(65507) // Actual UDP4 maximum; 65535 is a separate logical contract.
			f.send(t, p); packetReceiveCheck(t, read(), p, M.SocksaddrFromNet(f.sender.LocalAddr()).Unwrap(), o)
		})
	}
}
func TestVUICompletePacketCallerPrefix(t *testing.T) {
	for _, path := range []string{"direct", "fallback", "connected_read_buffer"} {
		t.Run(path, func(t *testing.T) {
			packetReceiveTrack(t); f := packetReceivePair(t, path == "connected_read_buffer")
			b := buf.NewSize(32); defer b.Release()
			prefix := []byte("previous"); _, _ = b.Write(prefix)
			f.send(t, packetReceivePayload(b.FreeLen()+1))
			var err error
			if path == "connected_read_buffer" { err = NewExtendedConn(f.receiver).ReadBuffer(b) } else {
				var c net.PacketConn = f.receiver
				if path == "fallback" { c = &packetReceiveHiddenConn{c} }
				_, err = NewPacketConn(c).ReadPacket(b)
			}
			if !errors.Is(err, N.ErrPacketTooLarge) { t.Fatalf("short caller error=%v", err) }
			if !bytes.Equal(b.Bytes(), prefix) { t.Fatalf("preexisting caller data changed: %q", b.Bytes()) }
			// The rejected datagram was consumed once. A subsequent complete
			// datagram appends to, rather than replaces, existing caller data.
			f.send(t, []byte("ok"))
			if path == "connected_read_buffer" { err = NewExtendedConn(f.receiver).ReadBuffer(b) } else {
				var c net.PacketConn = f.receiver
				if path == "fallback" { c = &packetReceiveHiddenConn{c} }
				_, err = NewPacketConn(c).ReadPacket(b)
			}
			if err != nil || !bytes.Equal(b.Bytes(), append(append([]byte(nil), prefix...), []byte("ok")...)) { t.Fatalf("caller recovery data=%q error=%v", b.Bytes(), err) }
		})
	}
}
func TestVUICompletePacketParentClose(t *testing.T) {
	for _, path := range []string{"direct", "fallback", "single_waiter", "connected_read_buffer", "connected_waiter"} {
		t.Run(path, func(t *testing.T) {
			packetReceiveTrack(t); f := packetReceivePair(t, path == "connected_read_buffer" || path == "connected_waiter")
			read := packetReceiveBuild(t, path, f, N.ReadWaitOptions{})
			started, done := make(chan struct{}), make(chan struct{})
			var result packetReceiveResult
			go func() { close(started); result = read(); close(done) }()
			t.Cleanup(func() { f.receiver.Close(); <-done; if result.b != nil { result.b.Release(); result.b = nil } })
			<-started
			select { case <-done: t.Fatalf("empty receive returned before parent close: %v", result.err); case <-time.After(25*time.Millisecond): }
			if err := f.receiver.Close(); err != nil { t.Fatal(err) }
			<-done // Close must unblock; whole test run has an external timeout.
			if !errors.Is(result.err, net.ErrClosed) { t.Fatalf("parent close error=%v", result.err) }
			if result.b != nil && result.b.Len() != 0 { t.Fatal("close returned packet prefix") }
		})
	}
}

// Fake only tests the protocol limit that UDP4 itself cannot transport.
type packetReceiveFakeConn struct { net.PacketConn; payload []byte; observedCapacity int }
func (c *packetReceiveFakeConn) ReadFrom(p []byte) (int, net.Addr, error) {
	c.observedCapacity = len(p)
	return copy(p, c.payload), &net.UDPAddr{IP: net.IPv4(127,0,0,1), Port: 32001}, nil
}
func TestVUICompletePacketProtocolLimit(t *testing.T) {
	packetReceiveTrack(t)
	f := &packetReceiveFakeConn{}
	c := NewPacketConn(f)
	for _, size := range []int{65535, 65536, 65537} {
		f.payload = packetReceivePayload(size)
		b := buf.NewSize(70000)
		d, err := c.ReadPacket(b)
		if f.observedCapacity != 65536 { b.Release(); t.Fatalf("fallback scratch len=%d want=65536", f.observedCapacity) }
		if size == 65535 {
			if err != nil || !bytes.Equal(b.Bytes(), f.payload) || !d.IsValid() { b.Release(); t.Fatalf("exact protocol limit: len=%d err=%v", b.Len(), err) }
		} else if !errors.Is(err, N.ErrPacketTooLarge) || b.Len() != 0 || d.IsValid() {
			b.Release(); t.Fatalf("protocol oversize %d: len=%d addr=%v err=%v", size, b.Len(), d, err)
		}
		b.Release()
	}
}
func TestVUICompletePacketPublicReadFrom(t *testing.T) {
	for _, path := range []string{"direct", "fallback"} {
		t.Run(path, func(t *testing.T) {
			f := packetReceivePair(t, false)
			var raw net.PacketConn = f.receiver
			if path == "fallback" { raw = &packetReceiveHiddenConn{raw} }
			c := NewPacketConn(raw); p := packetReceivePayload(32); short := make([]byte, 8)
			f.send(t, p); n, addr, err := c.ReadFrom(short)
			// Linux standard ReadFrom returns a valid prefix; this public API is
			// deliberately not promoted to the packet-copy all-or-error contract.
			if err != nil || n != len(short) || addr == nil || !bytes.Equal(short, p[:8]) { t.Fatalf("standard ReadFrom changed: n=%d addr=%v err=%v", n, addr, err) }
		})
	}
}
func TestVUICompletePacketConnectedWriteBuffer(t *testing.T) {
	a := packetReceiveTrack(t); f := packetReceivePair(t, true)
	c := NewExtendedConn(f.receiver)
	for _, p := range [][]byte{packetReceivePayload(60000), nil, []byte("after-zero")} {
		b := buf.NewSize(len(p)); _, _ = b.Write(p)
		if err := c.WriteBuffer(b); err != nil { b.Release(); t.Fatal(err) }
		out := make([]byte, 65535); n, addr, err := f.sender.ReadFromUDP(out)
		if err != nil || !bytes.Equal(out[:n], p) || addr.String() != f.receiver.LocalAddr().String() { t.Fatalf("connected write len=%d addr=%v err=%v", n, addr, err) }
		a.assertEmpty(t)
	}
}

type packetReceiveSink struct { mtu int; calls int }
func (s *packetReceiveSink) WriterMTU() int { return s.mtu }
func (s *packetReceiveSink) WritePacket(b *buf.Buffer, _ M.Socksaddr) error { s.calls++; b.Release(); return nil }
func TestVUICompletePacketCopyNoPrefix(t *testing.T) {
	for _, path := range []string{"fallback", "single_waiter", "connected_waiter"} {
		t.Run(path, func(t *testing.T) {
			packetReceiveTrack(t); f := packetReceivePair(t, path == "connected_waiter")
			var reader N.PacketReader = NewPacketConn(f.receiver)
			if path == "fallback" { reader = NewPacketConn(&packetReceiveHiddenConn{f.receiver}) }
			if path == "connected_waiter" { reader = NewUnbindPacketConn(f.receiver) }
			sink := &packetReceiveSink{mtu: 1024}
			f.send(t, packetReceivePayload(1025))
			n, err := CopyPacket(sink, reader)
			if !errors.Is(err, N.ErrPacketTooLarge) || n != 0 || sink.calls != 0 { t.Fatalf("copy forwarded prefix: n=%d calls=%d err=%v", n, sink.calls, err) }
		})
	}
}

