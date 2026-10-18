package vmess

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

	"github.com/sagernet/sing/common/buf"
	M "github.com/sagernet/sing/common/metadata"
	N "github.com/sagernet/sing/common/network"
)

const packetSessionTestBound = 10 * time.Second
const packetSessionTestUser = "11111111-2222-4333-8444-555555555555"

type packetSessionDispatch struct { conn N.PacketConn; target M.Socksaddr; onClose N.CloseHandlerFunc }
type packetSessionHandler struct { udp chan packetSessionDispatch; returned chan struct{}; unexpectedTCP atomic.Int32 }
func (h *packetSessionHandler) NewConnectionEx(_ context.Context, conn net.Conn, _ M.Socksaddr, _ M.Socksaddr, _ N.CloseHandlerFunc) {
	h.unexpectedTCP.Add(1)
	conn.Close()
}
func (h *packetSessionHandler) NewPacketConnectionEx(_ context.Context, conn N.PacketConn, _ M.Socksaddr, target M.Socksaddr, onClose N.CloseHandlerFunc) {
	defer close(h.returned)
	h.udp <- packetSessionDispatch{conn, target, onClose}
}

type packetSessionFaultWire struct { net.Conn; corrupt atomic.Bool; writes atomic.Int32 }
func (c *packetSessionFaultWire) Write(p []byte) (int, error) {
	c.writes.Add(1)
	if c.corrupt.Load() && len(p) > 0 {
		p = append([]byte(nil), p...)
		p[len(p)-1] ^= 0x80
	}
	return c.Conn.Write(p)
}

type packetSessionEncryptedFixture struct {
	t *testing.T
	ctx context.Context
	client *XUDPConn
	protocol *clientConn
	server *serverMuxPacketConn
	serverWire *packetSessionFaultWire
	clientWire net.Conn
	target M.Socksaddr
	onClose N.CloseHandlerFunc
	workers sync.WaitGroup
}

func newPacketSessionEncryptedFixture(t *testing.T, security string, first []byte) *packetSessionEncryptedFixture {
	t.Helper()
	packetSessionTrack(t)
	ctx, cancel := context.WithTimeout(context.Background(), packetSessionTestBound)
	left, right := net.Pipe()
	deadline := time.Now().Add(packetSessionTestBound)
	if err := left.SetDeadline(deadline); err != nil { t.Fatal(err) }
	if err := right.SetDeadline(deadline); err != nil { t.Fatal(err) }
	wire := &packetSessionFaultWire{Conn: right}
	handler := &packetSessionHandler{udp: make(chan packetSessionDispatch, 1), returned: make(chan struct{})}
	service := NewService[string](handler)
	if err := service.UpdateUsers([]string{"fixture"}, []string{packetSessionTestUser}, []int{0}); err != nil { t.Fatal(err) }
	serviceDone := make(chan error, 1)
	go func() { serviceDone <- service.NewConnection(ctx, wire, M.ParseSocksaddr("127.0.0.1:24443"), nil) }()
	f := &packetSessionEncryptedFixture{t: t, ctx: ctx, serverWire: wire, clientWire: left, target: M.ParseSocksaddr("127.0.0.3:12345")}
	t.Cleanup(func() {
		cancel()
		left.Close()
		right.Close()
		service.Close()
		serviceJoined := false
		select { case <-serviceDone: serviceJoined = true; case <-time.After(packetSessionTestBound): t.Error("real Service invocation did not join") }
		joined := make(chan struct{})
		go func() { f.workers.Wait(); close(joined) }()
		callersJoined := false
		select { case <-joined: callersJoined = true; case <-time.After(packetSessionTestBound): t.Error("encrypted callers did not join") }
		if serviceJoined && callersJoined && f.protocol != nil { f.protocol.ReleasePacketSessionReadCacheAfterJoin(); f.protocol.ReleasePacketSessionReadCacheAfterJoin() }
		if f.client != nil { f.client.Close() }
		if handler.unexpectedTCP.Load() != 0 { t.Error("real service saw TCP instead of mux UDP") }
	})
	client, err := NewClient(packetSessionTestUser, security, 0, ClientWithGlobalPadding(), ClientWithAuthenticatedLength())
	if err != nil { t.Fatal(err) }
	packetConn, err := client.DialXUDPPacketConn(left, f.target)
	if err != nil { t.Fatal(err) }
	var ok bool
	f.client, ok = packetConn.(*XUDPConn)
	if !ok { t.Fatal("unexpected XUDP client type") }
	f.protocol, ok = f.client.Conn.(*clientConn)
	if !ok { t.Fatal("unexpected fixed VMess client wrapper") }
	if f.protocol.command != CommandMux || f.protocol.option != byte(RequestOptionChunkStream|RequestOptionChunkMasking|RequestOptionGlobalPadding|RequestOptionAuthenticatedLength) { t.Fatal("actual Client did not encode CommandMux with padding/authenticated lengths") }
	f.client.SetPacketSessionEndReporting()
	firstWrite := f.async(func() error {
		n, err := f.client.WriteTo(first, f.target)
		if err == nil && n != len(first) { err = io.ErrShortWrite }
		return err
	})
	select {
	case dispatch := <-handler.udp:
		if dispatch.target != f.target || dispatch.onClose != nil { t.Fatal("fixed handler destination/onClose contract changed") }
		f.onClose = dispatch.onClose
		f.server, ok = dispatch.conn.(*serverMuxPacketConn)
		if !ok { t.Fatal("actual Service did not dispatch serverMuxPacketConn") }
	case <-ctx.Done(): t.Fatal("real UDP mux dispatch missing")
	}
	select { case <-handler.returned: case <-ctx.Done(): t.Fatal("handler invocation did not return") }
	f.wait(firstWrite)
	if f.server.sessionID != 0 || f.server.stream.id != 0 { t.Fatal("single private session did not use wire SID 0") }
	return f
}

func (f *packetSessionEncryptedFixture) async(call func() error) <-chan error {
	done := make(chan error, 1)
	f.workers.Add(1)
	go func() { defer f.workers.Done(); done <- call() }()
	return done
}
func (f *packetSessionEncryptedFixture) wait(done <-chan error) {
	f.t.Helper()
	select { case err := <-done: if err != nil { f.t.Fatal(err) }; case <-f.ctx.Done(): f.t.Fatal("encrypted operation exceeded bound") }
}
func (f *packetSessionEncryptedFixture) receiveServer(want []byte) {
	f.t.Helper()
	packet := buf.NewSize(65535)
	defer packet.Release()
	target, err := f.server.ReadPacket(packet)
	if err != nil || target != f.target || !bytes.Equal(packet.Bytes(), want) { f.t.Fatalf("real server packet changed: target=%v size=%d err=%v", target, packet.Len(), err) }
}
func (f *packetSessionEncryptedFixture) reply(want []byte) {
	f.t.Helper()
	write := f.async(func() error { _, err := f.server.WriteTo(want, f.target); return err })
	packet := buf.NewSize(len(want))
	defer packet.Release()
	target, err := f.client.ReadPacket(packet)
	if err != nil || target != f.target || !bytes.Equal(packet.Bytes(), want) { f.t.Fatalf("real client packet changed: target=%v size=%d err=%v", target, packet.Len(), err) }
	f.wait(write)
}

func testPacketSessionEncrypted(t *testing.T, security string) {
	t.Run("normal_end_complete_packets", func(t *testing.T) {
		f := newPacketSessionEncryptedFixture(t, security, []byte{})
		f.receiveServer([]byte{})
		f.reply([]byte{})
		for _, size := range []int{1, 32768, 60000, 65497} {
			payload := bytes.Repeat([]byte{byte(size%251+1)}, size)
			write := f.async(func() error { _, err := f.client.WriteTo(payload, f.target); return err })
			f.receiveServer(payload)
			f.wait(write)
			f.reply(payload)
		}
		if err := f.server.Close(); err != nil { t.Fatal(err) }
		_, err := f.client.ReadPacket(buf.With(nil))
		if err != N.ErrPacketSessionEnded { t.Fatalf("real encrypted canonical End: %v", err) }
		if f.serverWire.writes.Load() < 8 { t.Fatal("large packets did not exercise encrypted chunking") }
		t.Logf("real Client/Service cipher=%s CommandMux=3 SID=0 global_padding=true authenticated_length=true zero=1,32768,60000,65497 canonical_end=true", security)
	})
	t.Run("explicit_error_end_terminal", func(t *testing.T) {
		f := newPacketSessionEncryptedFixture(t, security, []byte("first"))
		f.receiveServer([]byte("first"))
		f.server.session.closeStream(f.server.stream, true, errors.New("fixture explicit stream failure"))
		_, err := f.client.ReadPacket(buf.With(nil))
		if err == nil || errors.Is(err, N.ErrPacketSessionEnded) || !errors.Is(err, net.ErrClosed) { t.Fatalf("explicit error End became recoverable: %v", err) }
	})
	t.Run("handshake_failure_is_opaque_end", func(t *testing.T) {
		f := newPacketSessionEncryptedFixture(t, security, []byte("never-delivered"))
		// The real fixed handler receives nil onClose and closes the mux child
		// without reading it. No packet is delivered to any downstream target.
		rejection := errors.New("fixture handshake policy failure")
		if err := N.CloseOnHandshakeFailure(f.server, f.onClose, rejection); !errors.Is(err, rejection) { t.Fatalf("local rejection was lost: %v", err) }
		_, err := f.client.ReadPacket(buf.With(nil))
		if err != N.ErrPacketSessionEnded { t.Fatalf("fixed rejection did not expose canonical opaque End: %v", err) }
		t.Log("handshake_failure_close=true application_target_reads=0 canonical_no_error_end=true idle_reason_proven=false; actual route rejection/re-admission is a separate route/owner gate")
	})
	t.Run("raw_transport_eof_terminal", func(t *testing.T) {
		f := newPacketSessionEncryptedFixture(t, security, []byte("first"))
		f.receiveServer([]byte("first"))
		f.reply([]byte("first"))
		f.serverWire.Close()
		_, err := f.client.ReadPacket(buf.With(nil))
		if err == nil || errors.Is(err, N.ErrPacketSessionEnded) { t.Fatalf("raw close became recoverable: %v", err) }
	})
	t.Run("corrupt_aead_terminal", func(t *testing.T) {
		f := newPacketSessionEncryptedFixture(t, security, []byte("first"))
		f.receiveServer([]byte("first"))
		f.reply([]byte("first"))
		f.serverWire.corrupt.Store(true)
		write := f.async(func() error { _, err := f.server.WriteTo([]byte("corrupt"), f.target); return err })
		_, err := f.client.ReadPacket(buf.With(make([]byte, 32)))
		if err == nil || errors.Is(err, N.ErrPacketSessionEnded) { t.Fatalf("authenticated corruption became recoverable: %v", err) }
		// Abort before waiting: rejecting an authenticated length may leave the
		// synchronous writer blocked on the remaining ciphertext. Both must join.
		f.clientWire.Close()
		select { case <-write: case <-f.ctx.Done(): t.Fatal("corrupt encrypted writer did not join after raw abort") }
	})
}

func TestPacketSessionEncryptedAES(t *testing.T) { testPacketSessionEncrypted(t, "aes-128-gcm") }
func TestPacketSessionEncryptedChaCha(t *testing.T) { testPacketSessionEncrypted(t, "chacha20-poly1305") }
