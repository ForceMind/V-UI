package route

import (
	"bytes"
	"context"
	"crypto/ed25519"
	"crypto/rand"
	stdtls "crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/pem"
	"errors"
	"fmt"
	"math/big"
	"net"
	"reflect"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/sagernet/sing-box/adapter"
	"github.com/sagernet/sing-box/common/dialer"
	boxtls "github.com/sagernet/sing-box/common/tls"
	C "github.com/sagernet/sing-box/constant"
	"github.com/sagernet/sing-box/log"
	"github.com/sagernet/sing-box/option"
	boxvless "github.com/sagernet/sing-box/protocol/vless"
	boxvmess "github.com/sagernet/sing-box/protocol/vmess"
	"github.com/sagernet/sing-vmess"
	"github.com/sagernet/sing-vmess/vless"
	"github.com/sagernet/sing/common/buf"
	M "github.com/sagernet/sing/common/metadata"
	N "github.com/sagernet/sing/common/network"
	"github.com/sagernet/sing/service"
)

const ownerRealBound = 10*time.Second
const ownerRealUUID = "22222222-3333-4444-8555-666666666666"

type ownerRealDispatch struct { conn N.PacketConn; target M.Socksaddr; onClose N.CloseHandlerFunc }
type ownerRealHandler struct { packets chan ownerRealDispatch; returned chan struct{}; tcp atomic.Int32 }
func (h *ownerRealHandler) NewConnectionEx(_ context.Context, conn net.Conn, _ M.Socksaddr, _ M.Socksaddr, _ N.CloseHandlerFunc) { h.tcp.Add(1); conn.Close() }
func (h *ownerRealHandler) NewPacketConnectionEx(ctx context.Context, conn N.PacketConn, _ M.Socksaddr, target M.Socksaddr, onClose N.CloseHandlerFunc) {
	defer close(h.returned)
	select { case h.packets <- ownerRealDispatch{conn, target, onClose}: case <-ctx.Done(): }
}

type ownerRealChild struct {
	N.PacketConn
	ownerTestLifecycle
	target M.Socksaddr
	input chan []byte
	replies chan []byte
	acks chan error
	closed chan struct{}
	once sync.Once
}
func newOwnerRealChild(target M.Socksaddr) *ownerRealChild { return &ownerRealChild{target: target, input: make(chan []byte, 1), replies: make(chan []byte, 1), acks: make(chan error, 8), closed: make(chan struct{})} }
func (c *ownerRealChild) ReadPacket(buffer *buf.Buffer) (M.Socksaddr, error) {
	select { case payload := <-c.input: _, err := buffer.Write(payload); return c.target, err; case <-c.closed: return M.Socksaddr{}, net.ErrClosed }
}
func (c *ownerRealChild) WritePacket(buffer *buf.Buffer, target M.Socksaddr) error {
	defer buffer.Release()
	if target != c.target { return N.ErrPacketSessionLifecycle }
	payload := append([]byte{}, buffer.Bytes()...)
	select { case c.replies <- payload: return nil; case <-c.closed: return net.ErrClosed }
}
func (c *ownerRealChild) PacketSessionWriteFinished(err error) { c.ownerTestLifecycle.PacketSessionWriteFinished(err); c.acks <- err }
func (c *ownerRealChild) Close() error { c.once.Do(func() { close(c.closed) }); return nil }
func (c *ownerRealChild) LocalAddr() net.Addr { return &net.UDPAddr{IP: net.IPv4(127,0,0,1), Port: 20000} }
func (*ownerRealChild) FrontHeadroom() int { return 10 }
func (*ownerRealChild) ReaderOverhead() int { return 0 }

func ownerTLSFixture(t *testing.T) (*stdtls.Config, *option.OutboundTLSOptions) {
	t.Helper()
	public, private, err := ed25519.GenerateKey(rand.Reader)
	if err != nil { t.Fatal(err) }
	template := &x509.Certificate{SerialNumber: big.NewInt(1), Subject: pkix.Name{CommonName:"owner.local"}, DNSNames: []string{"owner.local"}, NotBefore: time.Now().Add(-time.Hour), NotAfter: time.Now().Add(time.Hour), KeyUsage: x509.KeyUsageDigitalSignature, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}, BasicConstraintsValid: true}
	der, err := x509.CreateCertificate(rand.Reader, template, template, public, private)
	if err != nil { t.Fatal(err) }
	certificate := pem.EncodeToMemory(&pem.Block{Type:"CERTIFICATE", Bytes: der})
	keyDER, err := x509.MarshalPKCS8PrivateKey(private)
	if err != nil { t.Fatal(err) }
	pair, err := stdtls.X509KeyPair(certificate, pem.EncodeToMemory(&pem.Block{Type:"PRIVATE KEY", Bytes:keyDER}))
	if err != nil { t.Fatal(err) }
	return &stdtls.Config{Certificates: []stdtls.Certificate{pair}}, &option.OutboundTLSOptions{Enabled: true, ServerName:"owner.local", Certificate: []string{string(certificate)}}
}

func ownerConcreteOutbound(t *testing.T, ctx context.Context, kind string, address M.Socksaddr, tlsOptions *option.OutboundTLSOptions) adapter.Outbound {
	t.Helper()
	logger := log.NewNOPFactory().Logger()
	server := option.ServerOptions{Server: address.Addr.String(), ServerPort: address.Port}
	var outbound adapter.Outbound
	var err error
	if kind == "vless" {
		outbound, err = boxvless.NewOutbound(ctx, nil, logger, "private", option.VLESSOutboundOptions{ServerOptions:server, UUID:ownerRealUUID, OutboundTLSOptionsContainer:option.OutboundTLSOptionsContainer{TLS:tlsOptions}})
	} else {
		outbound, err = boxvmess.NewOutbound(ctx, nil, logger, "private", option.VMessOutboundOptions{ServerOptions:server, UUID:ownerRealUUID, Security:kind, PacketEncoding:"xudp", GlobalPadding:true, AuthenticatedLength:true, OutboundTLSOptionsContainer:option.OutboundTLSOptionsContainer{TLS:tlsOptions}})
	}
	if err != nil { t.Fatal(err) }
	if err := outbound.(adapter.OwnedPacketSessionDialer).ValidatePacketSession(); err != nil { t.Fatal(err) }
	return outbound
}

func ownerProtocolService(t *testing.T, kind string, handler vmess.Handler) (func(context.Context, net.Conn, M.Socksaddr, N.CloseHandlerFunc) error, func()) {
	t.Helper()
	if kind == "vless" {
		s := vless.NewService[string](log.NewNOPFactory().Logger(), handler)
		s.UpdateUsers([]string{"fixture"}, []string{ownerRealUUID}, []string{""})
		return s.NewConnection, func() {}
	}
	s := vmess.NewService[string](handler)
	if err := s.UpdateUsers([]string{"fixture"}, []string{ownerRealUUID}, []int{0}); err != nil { t.Fatal(err) }
	return s.NewConnection, func() { s.Close() }
}

func ownerWait(t *testing.T, done <-chan struct{}, label string) {
	t.Helper()
	select { case <-done: case <-time.After(ownerRealBound): t.Fatalf("%s did not join", label) }
}

func ownerRealJoinedCycles(t *testing.T, kind string) {
	serverTLS, tlsOptions := ownerTLSFixture(t)
	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil { t.Fatal(err) }
	defer listener.Close()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	manager := NewConnectionManager(log.NewNOPFactory().Logger())
	defer manager.Close()
	ctx = service.ContextWith[adapter.ConnectionManager](ctx, manager)
	outbound := ownerConcreteOutbound(t, ctx, kind, M.SocksaddrFromNet(listener.Addr()), tlsOptions)
	originalAllocator := buf.DefaultAllocator
	allocator := &ownerCountingAllocator{base: originalAllocator, live: make(map[*byte]int)}
	buf.DefaultAllocator = allocator
	var allJoined atomic.Bool
	allJoined.Store(true)
	defer func() {
		cancel(); manager.Close(); listener.Close()
		if closer, ok := outbound.(interface { Close() error }); ok { closer.Close() }
		if !allJoined.Load() { panic("owner test caller unjoined: refusing allocator restoration") }
		buf.DefaultAllocator = originalAllocator
	}()
	target := M.ParseSocksaddr("127.0.0.3:9000")
	for generation := 0; generation < 200; generation++ {
		if !t.Run(fmt.Sprintf("generation_%03d", generation), func(t *testing.T) {
			generationCtx, cancelGeneration := context.WithCancel(ctx)
			child := newOwnerRealChild(target) // A fresh synthetic lifecycle child.
			handler := &ownerRealHandler{packets: make(chan ownerRealDispatch, 1), returned: make(chan struct{})}
			serve, closeService := ownerProtocolService(t, kind, handler)
			accepted := make(chan struct{})
			serviceDone := make(chan error, 1)
			serviceReturned, returned, callbackReturned := make(chan struct{}), make(chan struct{}), make(chan struct{})
			callback := make(chan error, 1)
			var rawMu sync.Mutex
			var raw net.Conn
			dispatchExpected := false
			// Register before launching either worker or waiting for Accept.
			t.Cleanup(func() {
				cancelGeneration(); child.Close(); manager.CloseAll()
				rawMu.Lock(); if raw != nil { raw.Close() } else { listener.Close() }; rawMu.Unlock()
				deadline := time.NewTimer(ownerRealBound); defer deadline.Stop()
				waits := []struct { done <-chan struct{}; name string }{{returned,"route invocation"}, {callbackReturned,"callback"}, {serviceReturned,"Service/TLS close"}}
				if dispatchExpected { waits = append(waits, struct { done <-chan struct{}; name string }{handler.returned,"mux handler"}) }
				for _, wait := range waits { select { case <-wait.done: case <-deadline.C: allJoined.Store(false); t.Errorf("cleanup did not join %s", wait.name); return } }
				closeService()
			})
			go func() {
				defer close(serviceReturned)
				result := func() error {
					conn, err := listener.Accept()
					if err != nil { return err }
					rawMu.Lock(); raw = conn; rawMu.Unlock()
					defer conn.Close()
					if generationCtx.Err() != nil { return generationCtx.Err() }
					conn.SetDeadline(time.Now().Add(ownerRealBound))
					secure := stdtls.Server(conn, serverTLS)
					defer secure.Close()
					close(accepted)
					return serve(generationCtx, secure, M.SocksaddrFromNet(conn.RemoteAddr()), nil)
				}()
				serviceDone <- result // All secure/raw close defers already returned.
			}()
			go func() {
				defer close(returned)
				policy := adapter.PacketSessionPolicy{Outbound:"private", Target:target, Lifecycle:child}
				manager.NewPacketConnection(adapter.WithPacketSessionPolicy(generationCtx, policy), outbound, child, adapter.InboundContext{InboundType:C.TypeMixed, Destination:target}, func(err error) { defer close(callbackReturned); callback <- err })
			}()
			select { case <-accepted: case <-time.After(ownerRealBound): t.Fatal("private raw TCP accept timed out") }
			var remote N.PacketConn
			for index, size := range []int{0, 1, 32768, 60000, 65497} {
				payload := bytes.Repeat([]byte{byte(generation%251+index+1)}, size)
				if index == 0 { dispatchExpected = true }
				select { case child.input <- payload: case <-time.After(ownerRealBound): t.Fatal("input queue blocked") }
				if index == 0 {
					select { case dispatch := <-handler.packets:
						remote = dispatch.conn
						if dispatch.target != target || dispatch.onClose != nil || reflect.TypeOf(remote).String() != "*vmess.serverMuxPacketConn" { t.Fatal("actual CommandMux dispatch contract changed") }
					case <-time.After(ownerRealBound): t.Fatal("real mux dispatch missing") }
				}
				packet := buf.NewSize(65535)
				actual, readErr := remote.ReadPacket(packet)
				matched := actual == target && bytes.Equal(packet.Bytes(), payload)
				packet.Release()
				if readErr != nil || !matched { t.Fatalf("actual server packet mismatch size=%d err=%v", size, readErr) }
				select { case err := <-child.acks: if err != nil { t.Fatal(err) }; case <-time.After(ownerRealBound): t.Fatal("upload acknowledgement missing") }
				reply := buf.NewSize(size); reply.Write(payload); reply.IncRef()
				writeErr := remote.WritePacket(reply, target)
				reply.DecRef(); reply.Release()
				if writeErr != nil { t.Fatal(writeErr) }
				select { case received := <-child.replies: if !bytes.Equal(received, payload) { t.Fatal("reply prefix or corruption") }; case <-time.After(ownerRealBound): t.Fatal("reply missing") }
			}
			if err := remote.Close(); err != nil { t.Fatal(err) }
			select { case err := <-callback: if err != N.ErrPacketSessionEnded { t.Fatalf("clean End cause: %v", err) }; case <-time.After(ownerRealBound): t.Fatal("route callback missing") }
			ownerWait(t, returned, "route invocation"); ownerWait(t, callbackReturned, "callback"); ownerWait(t, serviceReturned, "Service/TLS close"); ownerWait(t, handler.returned, "mux handler")
			<-serviceDone
			if manager.Count() != 0 || !allocator.balanced() || handler.tcp.Load() != 0 { t.Fatal("joined generation retained tracked transport/buffer or used TCP command") }
			t.Logf("real TLS/route/lease with fresh synthetic lifecycle child: cipher=%s generation=%d payloads=0,1,32768,60000,65497 joined=true allocator_baseline=true", kind, generation)
		}) { return }
	}
}

func TestOwnerRealJoinedAES(t *testing.T) { ownerRealJoinedCycles(t, "aes-128-gcm") }
func TestOwnerRealJoinedChaCha(t *testing.T) { ownerRealJoinedCycles(t, "chacha20-poly1305") }
func TestOwnerRealJoinedVLESS(t *testing.T) { ownerRealJoinedCycles(t, "vless") }

// Promoted methods must not turn a TLS wrapper into a supported concrete config.
type ownerUnknownTLSConfig struct { boxtls.Config }

func TestOwnerDirectTCPAndCanceledDial(t *testing.T) {
	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil { t.Fatal(err) }
	defer listener.Close()
	d, err := dialer.NewDefault(context.Background(), option.DialerOptions{})
	if err != nil { t.Fatal(err) }
	address := M.SocksaddrFromNet(listener.Addr())
	if dialer.ValidatePacketSessionDialer((*dialer.DefaultDialer)(nil), address) == nil { t.Fatal("typed-nil direct dialer admitted") }
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	conn, err := d.DialContext(ctx, "tcp", address)
	if err != nil { t.Fatal(err) }
	if _, ok := conn.(*net.TCPConn); !ok { conn.Close(); t.Fatal("unexpected direct wrapper") }
	conn.Close()
	config, err := boxtls.NewSTDClient(context.Background(), log.NewNOPFactory().Logger(), "localhost", option.OutboundTLSOptions{Enabled: true})
	if err != nil { t.Fatal(err) }
	// The lower-level signature must not weaken the exact concrete TLS gate.
	if err := dialer.ValidatePacketSessionTLS(config); err != nil { t.Fatal(err) }
	for _, invalid := range []boxtls.Config{nil, (*boxtls.STDClientConfig)(nil), &ownerUnknownTLSConfig{Config: config}} {
		if dialer.ValidatePacketSessionTLS(invalid) == nil { t.Fatal("unknown or nil TLS config admitted") }
	}
	inner, err := config.STDConfig()
	if err != nil { t.Fatal(err) }
	inner.InsecureSkipVerify = true
	if dialer.ValidatePacketSessionTLS(config) == nil { t.Fatal("insecure actual TLS config admitted") }
	inner.InsecureSkipVerify = false
	inner.ServerName = ""
	if dialer.ValidatePacketSessionTLS(config) == nil { t.Fatal("missing TLS server name admitted") }
	inner.ServerName = "localhost"
	inner.EncryptedClientHelloConfigList = []byte{1}
	if dialer.ValidatePacketSessionTLS(config) == nil { t.Fatal("actual ECH TLS config admitted") }
	inner.EncryptedClientHelloConfigList = nil
	cancel()
	attached := false
	_, _, err = dialer.DialPacketSessionTLS(ctx, d, address, config, func(func() error) error { attached = true; return nil })
	if !errors.Is(err, context.Canceled) || attached { t.Fatalf("cancelled raw dial attached: %v %v", attached, err) }
}
