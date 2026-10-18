package socks

import (
	std_bufio "bufio"
	"bytes"
	"context"
	"fmt"
	"net"
	"sync"
	"testing"
	"time"

	"github.com/sagernet/sing/common/buf"
	M "github.com/sagernet/sing/common/metadata"
	N "github.com/sagernet/sing/common/network"
	"github.com/sagernet/sing/protocol/socks/socks5"
)

type socksReceiveAllocator struct { mu sync.Mutex; live map[*byte]int; badPuts int }
func (a *socksReceiveAllocator) Get(n int) []byte {
	if n <= 0 { return nil }; p := make([]byte, n)
	a.mu.Lock(); a.live[&p[0]] = n; a.mu.Unlock(); return p
}
func (a *socksReceiveAllocator) Put(p []byte) error {
	a.mu.Lock(); defer a.mu.Unlock()
	if len(p) == 0 { a.badPuts++; return fmt.Errorf("empty allocation returned") }
	if _, ok := a.live[&p[0]]; !ok { a.badPuts++; return fmt.Errorf("foreign or repeated allocation returned") }
	delete(a.live, &p[0]); return nil
}
func socksReceiveTrack(t *testing.T) *socksReceiveAllocator {
	t.Helper(); previous := buf.DefaultAllocator
	a := &socksReceiveAllocator{live: make(map[*byte]int)}; buf.DefaultAllocator = a
	t.Cleanup(func() {
		a.mu.Lock(); defer a.mu.Unlock()
		if len(a.live) != 0 || a.badPuts != 0 { t.Errorf("allocator outstanding=%d bad_returns=%d", len(a.live), a.badPuts) }
		buf.DefaultAllocator = previous
	})
	return a
}

type socksReceiveTCP struct { net.Conn; watchdogStarted chan struct{}; watchdogDone chan struct{}; once sync.Once }
func (c *socksReceiveTCP) Read(p []byte) (int, error) {
	if len(p) == 1 {
		c.once.Do(func() { close(c.watchdogStarted) })
		defer close(c.watchdogDone)
	}
	return c.Conn.Read(p)
}
type socksReceiveListener struct { ready chan net.PacketConn }
func (l *socksReceiveListener) ListenPacket(_ net.ListenConfig, _ context.Context, _, _ string) (net.PacketConn, error) {
	c, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.IPv4(127,0,0,1)})
	if err == nil { err = c.SetDeadline(time.Now().Add(4*time.Second)) }
	if err != nil { if c != nil { c.Close() }; return nil, err }
	l.ready <- c; return c, nil
}
type socksReceiveObserved struct { payload []byte; destination M.Socksaddr; source M.Socksaddr; err error }
type socksReceiveHandler struct { received chan socksReceiveObserved }
func (h *socksReceiveHandler) NewConnectionEx(_ context.Context, c net.Conn, _, _ M.Socksaddr, _ N.CloseHandlerFunc) {
	c.Close(); h.received <- socksReceiveObserved{err: fmt.Errorf("unexpected TCP handler")}
}
func (h *socksReceiveHandler) NewPacketConnectionEx(_ context.Context, c N.PacketConn, source, destination M.Socksaddr, _ N.CloseHandlerFunc) {
	defer c.Close()
	cached, ok := c.(N.CachedPacketReader)
	if !ok { h.received <- socksReceiveObserved{err: fmt.Errorf("first packet not cached")}; return }
	packet := cached.ReadCachedPacket()
	if packet == nil || packet.Buffer == nil { h.received <- socksReceiveObserved{err: fmt.Errorf("missing first packet")}; return }
	result := socksReceiveObserved{payload: append([]byte(nil), packet.Buffer.Bytes()...), destination: packet.Destination, source: source}
	if packet.Destination != destination { result.err = fmt.Errorf("handler destination %v differs from cached %v", destination, packet.Destination) }
	packet.Buffer.Release(); N.PutPacketBuffer(packet)
	h.received <- result
}
type socksReceiveSession struct {
	client *net.TCPConn
	server *socksReceiveTCP
	udp net.PacketConn
	bind M.Socksaddr
	handler *socksReceiveHandler
	done chan struct{}
	err error
}
func socksReceiveStart(t *testing.T) *socksReceiveSession {
	t.Helper()
	ln, err := net.ListenTCP("tcp4", &net.TCPAddr{IP: net.IPv4(127,0,0,1)})
	if err != nil { t.Fatal(err) }; defer ln.Close()
	if err := ln.SetDeadline(time.Now().Add(4*time.Second)); err != nil { t.Fatal(err) }
	client, err := net.DialTCP("tcp4", nil, ln.Addr().(*net.TCPAddr)); if err != nil { t.Fatal(err) }
	t.Cleanup(func() { client.Close() })
	raw, err := ln.AcceptTCP(); if err != nil { t.Fatal(err) }
	t.Cleanup(func() { raw.Close() })
	if err := client.SetDeadline(time.Now().Add(4*time.Second)); err != nil { t.Fatal(err) }
	if err := raw.SetDeadline(time.Now().Add(4*time.Second)); err != nil { t.Fatal(err) }
	s := &socksReceiveSession{client: client, server: &socksReceiveTCP{Conn: raw, watchdogStarted: make(chan struct{}), watchdogDone: make(chan struct{})}, handler: &socksReceiveHandler{received: make(chan socksReceiveObserved, 1)}, done: make(chan struct{})}
	listener := &socksReceiveListener{ready: make(chan net.PacketConn, 1)}
	go func() {
		s.err = HandleConnectionEx(context.Background(), s.server, std_bufio.NewReader(s.server), nil, s.handler, listener, 0, M.SocksaddrFromNet(raw.RemoteAddr()).Unwrap(), nil)
		close(s.done)
	}()
	// Cleanup joins both our handler worker and the production watchdog before
	// the earlier allocator cleanup restores DefaultAllocator.
	t.Cleanup(func() {
		client.Close(); raw.Close()
		if s.udp == nil { select { case s.udp = <-listener.ready: default: } }
		if s.udp != nil { s.udp.Close() }
		<-s.done
		// A listener may have been created while the cleanup was joining.
		if s.udp == nil { select { case s.udp = <-listener.ready: s.udp.Close(); default: } }
		select { case <-s.server.watchdogStarted: <-s.server.watchdogDone; default: }
	})
	response, err := ClientHandshake5(client, socks5.CommandUDPAssociate, M.ParseSocksaddr("127.0.0.1:1"), "", "")
	if err != nil { t.Fatal(err) }
	s.bind = response.Bind
	s.udp = <-listener.ready
	<-s.server.watchdogStarted
	return s
}
func socksReceiveFrame(t *testing.T, payload []byte) ([]byte, M.Socksaddr) {
	t.Helper(); destination := M.ParseSocksaddr("127.0.0.1:32001")
	b := buf.NewSize(3 + M.SocksaddrSerializer.AddrPortLen(destination) + len(payload)); defer b.Release()
	if err := b.WriteZeroN(3); err != nil { t.Fatal(err) }
	if err := M.SocksaddrSerializer.WriteAddrPort(b, destination); err != nil { t.Fatal(err) }
	if n, err := b.Write(payload); err != nil || n != len(payload) { t.Fatalf("frame payload n=%d err=%v", n, err) }
	return append([]byte(nil), b.Bytes()...), destination
}
func socksReceiveSend(t *testing.T, s *socksReceiveSession, packet []byte) M.Socksaddr {
	t.Helper(); c, err := net.DialUDP("udp4", nil, s.bind.UDPAddr()); if err != nil { t.Fatal(err) }; defer c.Close()
	if err := c.SetDeadline(time.Now().Add(4*time.Second)); err != nil { t.Fatal(err) }
	n, err := c.Write(packet); if err != nil || n != len(packet) { t.Fatalf("SOCKS UDP write n=%d err=%v", n, err) }
	return M.SocksaddrFromNet(c.LocalAddr()).Unwrap()
}
func TestVUICompletePacketSOCKSFirstPacket(t *testing.T) {
	for _, tc := range []struct { name string; payloadSize int }{{"large_payload", 60000}, {"udp4_maximum", 65497}, {"zero_payload", 0}} {
		t.Run(tc.name, func(t *testing.T) {
			socksReceiveTrack(t); s := socksReceiveStart(t)
			payload := bytes.Repeat([]byte{0x9d}, tc.payloadSize)
			wire, destination := socksReceiveFrame(t, payload)
			if len(wire) != tc.payloadSize+10 { t.Fatalf("IPv4 SOCKS header=%d, want=10", len(wire)-tc.payloadSize) }
			source := socksReceiveSend(t, s, wire)
			<-s.done; <-s.server.watchdogDone
			if s.err != nil { t.Fatalf("SOCKS first packet: %v", s.err) }
			select {
			case result := <-s.handler.received:
				if result.err != nil || !bytes.Equal(result.payload, payload) || result.destination != destination || result.source != source { t.Fatalf("handler payload=%d/%d destination=%v/%v source=%v/%v err=%v", len(result.payload), len(payload), result.destination, destination, result.source, source, result.err) }
			default: t.Fatal("handler did not receive first packet")
			}
		})
	}
}
func TestVUICompletePacketSOCKSFirstPacketError(t *testing.T) {
	for _, path := range []string{"short_header", "invalid_address", "parent_close"} {
		t.Run(path, func(t *testing.T) {
			socksReceiveTrack(t); s := socksReceiveStart(t)
			switch path {
			case "short_header": socksReceiveSend(t, s, []byte{0, 0})
			case "invalid_address": socksReceiveSend(t, s, []byte{0, 0, 0, 0xff})
			case "parent_close": s.client.Close()
			}
			<-s.done; <-s.server.watchdogDone
			if s.err == nil { t.Fatal("invalid first packet unexpectedly accepted") }
			select { case result := <-s.handler.received: t.Fatalf("failed first packet reached handler: %+v", result); default: }
		})
	}
}
