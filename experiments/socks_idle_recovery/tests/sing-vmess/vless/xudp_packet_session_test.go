package vless

import (
	"bytes"
	"context"
	"errors"
	"io"
	"net"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/sagernet/sing-vmess"
	"github.com/sagernet/sing/common/buf"
	"github.com/sagernet/sing/common/logger"
	M "github.com/sagernet/sing/common/metadata"
	N "github.com/sagernet/sing/common/network"
)

const vlessSessionBound = 10 * time.Second
const vlessSessionUser = "11111111-2222-4333-8444-555555555555"

type vlessSessionAllocation struct { mu sync.Mutex; live map[*byte]int; gets, puts, bad int }
func (a *vlessSessionAllocation) Get(n int) []byte {
	if n == 0 { return nil }
	p := make([]byte, n)
	a.mu.Lock(); a.live[&p[0]] = n; a.gets++; a.mu.Unlock()
	return p
}
func (a *vlessSessionAllocation) Put(p []byte) error {
	a.mu.Lock(); defer a.mu.Unlock()
	if len(p) == 0 { a.bad++; return errors.New("empty allocation returned") }
	if _, ok := a.live[&p[0]]; !ok { a.bad++; return errors.New("foreign/repeated allocation returned") }
	delete(a.live, &p[0]); a.puts++
	return nil
}
func vlessSessionTrack(t *testing.T) {
	previous := buf.DefaultAllocator
	a := &vlessSessionAllocation{live: make(map[*byte]int)}
	buf.DefaultAllocator = a
	t.Cleanup(func() {
		a.mu.Lock()
		if len(a.live) != 0 || a.gets != a.puts || a.bad != 0 { t.Errorf("VLESS allocation imbalance live=%d gets=%d puts=%d bad=%d", len(a.live), a.gets, a.puts, a.bad) }
		a.mu.Unlock()
		buf.DefaultAllocator = previous
	})
}

type vlessSessionDispatch struct { conn N.PacketConn; target M.Socksaddr; onClose N.CloseHandlerFunc }
type vlessSessionHandler struct { udp chan vlessSessionDispatch; returned chan struct{}; tcp atomic.Int32 }
func (h *vlessSessionHandler) NewConnectionEx(_ context.Context, conn net.Conn, _ M.Socksaddr, _ M.Socksaddr, _ N.CloseHandlerFunc) { h.tcp.Add(1); conn.Close() }
func (h *vlessSessionHandler) NewPacketConnectionEx(_ context.Context, conn N.PacketConn, _ M.Socksaddr, target M.Socksaddr, onClose N.CloseHandlerFunc) {
	defer close(h.returned)
	h.udp <- vlessSessionDispatch{conn, target, onClose}
}

type vlessSessionWire struct { net.Conn; mu sync.Mutex; first []byte }
func (c *vlessSessionWire) Write(p []byte) (int, error) {
	c.mu.Lock()
	if c.first == nil { c.first = append([]byte(nil), p...) }
	c.mu.Unlock()
	return c.Conn.Write(p)
}

type vlessSessionFixture struct {
	t *testing.T
	ctx context.Context
	client *vmess.XUDPConn
	protocol *Conn
	server N.PacketConn
	left, right net.Conn
	target M.Socksaddr
	onClose N.CloseHandlerFunc
	handler *vlessSessionHandler
	workers sync.WaitGroup
}

func newVlessSessionFixture(t *testing.T) *vlessSessionFixture {
	t.Helper()
	vlessSessionTrack(t)
	ctx, cancel := context.WithTimeout(context.Background(), vlessSessionBound)
	left, right := net.Pipe()
	deadline := time.Now().Add(vlessSessionBound)
	if err := left.SetDeadline(deadline); err != nil { t.Fatal(err) }
	if err := right.SetDeadline(deadline); err != nil { t.Fatal(err) }
	wire := &vlessSessionWire{Conn: left}
	handler := &vlessSessionHandler{udp: make(chan vlessSessionDispatch, 1), returned: make(chan struct{})}
	service := NewService[string](logger.NOP(), handler)
	service.UpdateUsers([]string{"fixture"}, []string{vlessSessionUser}, []string{""})
	serviceDone := make(chan error, 1)
	go func() { serviceDone <- service.NewConnection(ctx, right, M.ParseSocksaddr("127.0.0.1:24443"), nil) }()
	f := &vlessSessionFixture{t: t, ctx: ctx, left: left, right: right, target: M.ParseSocksaddr("127.0.0.3:12345"), handler: handler}
	t.Cleanup(func() {
		cancel(); left.Close(); right.Close()
		select { case <-serviceDone: case <-time.After(vlessSessionBound): t.Error("real VLESS Service did not join") }
		joined := make(chan struct{})
		go func() { f.workers.Wait(); close(joined) }()
		select { case <-joined: case <-time.After(vlessSessionBound): t.Error("real VLESS callers did not join") }
		if f.client != nil { f.client.Close() }
		if handler.tcp.Load() != 0 { t.Error("VLESS actual command was TCP instead of mux") }
	})
	client, err := NewClient(vlessSessionUser, "", logger.NOP())
	if err != nil { t.Fatal(err) }
	// Fixed DialXUDPPacketConn uses CommandTCP. The accepted capability must
	// use the existing 'Early' method, which writes CommandMux synchronously.
	packetConn, err := client.DialEarlyXUDPPacketConn(wire, f.target)
	if err != nil { t.Fatal(err) }
	var ok bool
	f.client, ok = packetConn.(*vmess.XUDPConn)
	if !ok { t.Fatal("unexpected fixed VLESS XUDP wrapper") }
	f.protocol, ok = f.client.Conn.(*Conn)
	if !ok { t.Fatal("unexpected fixed VLESS protocol wrapper") }
	wire.mu.Lock(); actualWire := append([]byte(nil), wire.first...); wire.mu.Unlock()
	request, err := ReadRequest(bytes.NewReader(actualWire))
	if err != nil || request.Command != vmess.CommandMux || request.Flow != "" || f.protocol.request.Command != vmess.CommandMux { t.Fatalf("actual VLESS outer wire command/flow mismatch: request=%v err=%v", request, err) }
	f.client.SetPacketSessionEndReporting()
	return f
}

func (f *vlessSessionFixture) async(call func() error) <-chan error {
	done := make(chan error, 1); f.workers.Add(1)
	go func() { defer f.workers.Done(); done <- call() }()
	return done
}
func (f *vlessSessionFixture) wait(done <-chan error) {
	f.t.Helper()
	select { case err := <-done: if err != nil { f.t.Fatal(err) }; case <-f.ctx.Done(): f.t.Fatal("VLESS operation exceeded bound") }
}
func (f *vlessSessionFixture) open(first []byte) {
	f.t.Helper()
	write := f.async(func() error { n, err := f.client.WriteTo(first, f.target); if err == nil && n != len(first) { err = io.ErrShortWrite }; return err })
	select {
	case dispatch := <-f.handler.udp:
		if dispatch.target != f.target || dispatch.onClose != nil { f.t.Fatal("actual VLESS mux handler contract changed") }
		f.server, f.onClose = dispatch.conn, dispatch.onClose
	case <-f.ctx.Done(): f.t.Fatal("VLESS UDP handler missing")
	}
	select { case <-f.handler.returned: case <-f.ctx.Done(): f.t.Fatal("VLESS handler did not return") }
	f.wait(write)
}
func (f *vlessSessionFixture) receiveServer(want []byte) {
	f.t.Helper()
	packet := buf.NewSize(65535); defer packet.Release()
	target, err := f.server.ReadPacket(packet)
	if err != nil || target != f.target || !bytes.Equal(packet.Bytes(), want) { f.t.Fatalf("VLESS server packet changed: target=%v size=%d err=%v", target, packet.Len(), err) }
}
func (f *vlessSessionFixture) reply(want []byte) {
	f.t.Helper()
	write := f.async(func() error { packet := buf.NewSize(len(want)); packet.Write(want); return f.server.WritePacket(packet, f.target) })
	packet := buf.NewSize(len(want)); defer packet.Release()
	target, err := f.client.ReadPacket(packet)
	if err != nil || target != f.target || !bytes.Equal(packet.Bytes(), want) { f.t.Fatalf("VLESS client packet changed: target=%v size=%d err=%v", target, packet.Len(), err) }
	f.wait(write)
}

func TestPacketSessionVLESS(t *testing.T) {
	t.Run("normal_end_complete_packets", func(t *testing.T) {
		f := newVlessSessionFixture(t)
		f.open([]byte{}); f.receiveServer([]byte{}); f.reply([]byte{})
		for _, size := range []int{1, 32768, 60000, 65497} {
			payload := bytes.Repeat([]byte{byte(size%251+1)}, size)
			write := f.async(func() error { _, err := f.client.WriteTo(payload, f.target); return err })
			f.receiveServer(payload); f.wait(write); f.reply(payload)
		}
		if err := f.server.Close(); err != nil { t.Fatal(err) }
		_, err := f.client.ReadPacket(buf.With(nil))
		if err != N.ErrPacketSessionEnded { t.Fatalf("VLESS canonical End: %v", err) }
	})
	t.Run("explicit_error_end_terminal", func(t *testing.T) {
		f := newVlessSessionFixture(t)
		// The real fixed mux server rejects an unknown SID with OptionError.
		// No synthetic parser return value or replacement server is involved.
		write := f.async(func() error { _, err := f.protocol.Write([]byte{0, 4, 0, 0, vmess.StatusKeep, 0}); return err })
		_, err := f.client.ReadPacket(buf.With(nil))
		if err == nil || errors.Is(err, N.ErrPacketSessionEnded) || !errors.Is(err, net.ErrClosed) { t.Fatalf("VLESS explicit error End became recoverable: %v", err) }
		f.wait(write)
	})
	t.Run("handshake_failure_is_opaque_end", func(t *testing.T) {
		f := newVlessSessionFixture(t)
		f.open([]byte("never-delivered"))
		rejection := errors.New("fixture handshake policy failure")
		if err := N.CloseOnHandshakeFailure(f.server, f.onClose, rejection); !errors.Is(err, rejection) { t.Fatalf("local VLESS rejection lost: %v", err) }
		_, err := f.client.ReadPacket(buf.With(nil))
		if err != N.ErrPacketSessionEnded { t.Fatalf("VLESS rejection did not yield opaque canonical End: %v", err) }
		t.Log("actual VLESS CommandMux=3 handshake_failure_close=true application_target_reads=0 canonical_no_error_end=true idle_reason_proven=false; actual route rejection/re-admission is a separate route/owner gate")
	})
	t.Run("raw_transport_eof_terminal", func(t *testing.T) {
		f := newVlessSessionFixture(t)
		f.open([]byte("first")); f.receiveServer([]byte("first")); f.reply([]byte("first"))
		f.right.Close()
		_, err := f.client.ReadPacket(buf.With(nil))
		if err == nil || errors.Is(err, N.ErrPacketSessionEnded) { t.Fatalf("VLESS raw close became recoverable: %v", err) }
	})
}
