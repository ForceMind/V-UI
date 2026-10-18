package route

import (
	std_bufio "bufio"
	"bytes"
	"context"
	stdtls "crypto/tls"
	"encoding/binary"
	"io"
	"net"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/sagernet/sing-box/adapter"
	C "github.com/sagernet/sing-box/constant"
	"github.com/sagernet/sing-box/log"
	"github.com/sagernet/sing-box/option"
	"github.com/sagernet/sing/common/buf"
	M "github.com/sagernet/sing/common/metadata"
	N "github.com/sagernet/sing/common/network"
	"github.com/sagernet/sing/protocol/socks"
	"github.com/sagernet/sing/service"
)

// Observe the actual synchronous upload acknowledgement without replacing the
// real owner lifecycle. Only after it runs may the downstream Router reject.
type ownerAckLifecycle struct { N.PacketSessionLifecycle; ctx context.Context; ack chan error }
func (l *ownerAckLifecycle) PacketSessionWriteFinished(err error) {
	l.PacketSessionLifecycle.PacketSessionWriteFinished(err)
	select { case l.ack <- err: case <-l.ctx.Done(): }
}
type ownerRoutedHandler struct {
	router *Router
	ack chan error
	metadataTargets chan M.Socksaddr
	calls, callbacks, returns atomic.Int32
}
func (*ownerRoutedHandler) NewConnectionEx(_ context.Context, c net.Conn, _ M.Socksaddr, _ M.Socksaddr, _ N.CloseHandlerFunc) { c.Close() }
func (h *ownerRoutedHandler) NewPacketConnectionEx(ctx context.Context, c N.PacketConn, source, destination M.Socksaddr, onClose N.CloseHandlerFunc) {
	h.calls.Add(1)
	defer h.returns.Add(1)
	select { case h.metadataTargets <- destination: case <-ctx.Done(): c.Close(); onClose(ctx.Err()); return }
	lifecycle := &ownerAckLifecycle{PacketSessionLifecycle:c.(N.PacketSessionLifecycle), ctx:ctx, ack:h.ack}
	ctx = adapter.WithPacketSessionPolicy(ctx, adapter.PacketSessionPolicy{Outbound:"private", Target:destination, Lifecycle:lifecycle})
	h.router.RoutePacketConnectionEx(ctx, c, adapter.InboundContext{InboundType:C.TypeMixed, Source:source, Destination:destination}, func(err error) { h.callbacks.Add(1); onClose(err) })
}

type ownerRemoteRejectLedger struct { dispatchTarget, packetTarget M.Socksaddr; payload []byte; err error }
type ownerRejectRoutedHandler struct {
	router *Router
	ack chan error
	ledger chan ownerRemoteRejectLedger
	returned chan struct{}
	calls, tcp atomic.Int32
}
func (h *ownerRejectRoutedHandler) NewConnectionEx(_ context.Context, c net.Conn, _ M.Socksaddr, _ M.Socksaddr, _ N.CloseHandlerFunc) { h.tcp.Add(1); c.Close() }
func (h *ownerRejectRoutedHandler) NewPacketConnectionEx(ctx context.Context, c N.PacketConn, source, destination M.Socksaddr, onClose N.CloseHandlerFunc) {
	h.calls.Add(1)
	defer func() { h.returned <- struct{}{} }()
	// This is a reject-boundary observation of the actual mux payload, not a
	// send to the requested application target or a substitute router.
	buffer := buf.NewSize(65535)
	actual, err := c.ReadPacket(buffer)
	entry := ownerRemoteRejectLedger{dispatchTarget:destination, packetTarget:actual, payload:append([]byte{}, buffer.Bytes()...), err:err}
	buffer.Release()
	select { case h.ledger <- entry: case <-ctx.Done(): return }
	if err != nil { c.Close(); if onClose != nil { onClose(err) }; return }
	select {
	case writeErr := <-h.ack:
		if writeErr != nil { c.Close(); if onClose != nil { onClose(writeErr) }; return }
	case <-ctx.Done(): c.Close(); return
	}
	// Actual unmarked downstream Router executes its real reject rule. The
	// fixed mux child Close emits the canonical opaque End seen by the owner.
	h.router.RoutePacketConnectionEx(ctx, c, adapter.InboundContext{Network:N.NetworkUDP, Source:source, Destination:destination}, onClose)
}

func ownerRemoteRejectNewPacketOnly(t *testing.T, kind string) {
	serverTLS, tlsOptions := ownerTLSFixture(t)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	upstream, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil { t.Fatal(err) }
	defer upstream.Close()
	controlListener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil { t.Fatal(err) }
	defer controlListener.Close()
	targetSocket, err := net.ListenPacket("udp4", "127.0.0.1:0")
	if err != nil { t.Fatal(err) }
	defer targetSocket.Close()
	application, err := net.ListenPacket("udp4", "127.0.0.1:0")
	if err != nil { t.Fatal(err) }
	defer application.Close()
	target := M.SocksaddrFromNet(targetSocket.LocalAddr()).Unwrap()
	changed := target; if changed.Port == 65535 { changed.Port = 1 } else { changed.Port++ }
	manager := NewConnectionManager(log.NewNOPFactory().Logger())
	defer manager.Close()
	ctx = service.ContextWith[adapter.ConnectionManager](ctx, manager)
	outbound := ownerConcreteOutbound(t, ctx, kind, M.SocksaddrFromNet(upstream.Addr()), tlsOptions)
	defer func() { if closer, ok := outbound.(interface { Close() error }); ok { closer.Close() } }()
	ack := make(chan error, 2)
	downstreamOutbounds := &ownerPolicyOutbounds{}
	downstream := ownerPolicyRouter(t, option.RuleAction{Action:C.RuleActionTypeReject}, downstreamOutbounds, nil)
	serverHandler := &ownerRejectRoutedHandler{router:downstream, ack:ack, ledger:make(chan ownerRemoteRejectLedger,2), returned:make(chan struct{},2)}
	serve, closeService := ownerProtocolService(t, kind, serverHandler)
	defer closeService()
	clientOutbounds := &ownerPolicyOutbounds{selected:outbound}
	clientRouter := ownerPolicyRouter(t, option.RuleAction{Action:C.RuleActionTypeRoute, RouteOptions:option.RouteActionOptions{Outbound:"private"}}, clientOutbounds, manager)
	clientHandler := &ownerRoutedHandler{router:clientRouter, ack:ack, metadataTargets:make(chan M.Socksaddr,2)}
	events := make(chan socks.UDPAssociationEvent,32)
	var dropped atomic.Bool
	var slots, accepted, attempts atomic.Int32
	var connMu sync.Mutex
	var connections []net.Conn
	track := func(c net.Conn) { connMu.Lock(); connections = append(connections,c); connMu.Unlock() }
	ownerReturned, serviceReturned := make(chan struct{}), make(chan struct{})
	ownerResult := make(chan error,1)
	serviceResult := make(chan error,2)
	// Both accept loops and every fatal path are covered before workers start.
	t.Cleanup(func() {
		cancel(); upstream.Close(); controlListener.Close(); manager.CloseAll()
		connMu.Lock(); for _, c := range connections { c.Close() }; connMu.Unlock()
		deadline := time.NewTimer(ownerRealBound); defer deadline.Stop()
		for _, done := range []<-chan struct{}{ownerReturned,serviceReturned} { select { case <-done: case <-deadline.C: panic("real owner/service caller failed bounded cleanup join") } }
		// Pre-register every application attempt before WriteTo. A scheduled mux
		// handler can enter after Service returns; calls.Load is not a join fence.
		for range int(attempts.Load()) { select { case <-serverHandler.returned: case <-deadline.C: panic("real rejection handler failed bounded cleanup join") } }
		if slots.Load() != 0 { t.Error("owner limiter released before/after incorrect join") }
	})
	go func() {
		defer close(serviceReturned)
		for range 2 {
			result := func() error {
				raw, err := upstream.Accept()
				if err != nil { return err }
				track(raw)
				defer raw.Close()
				if ctx.Err() != nil { return ctx.Err() }
				accepted.Add(1)
				raw.SetDeadline(time.Now().Add(ownerRealBound))
				secure := stdtls.Server(raw,serverTLS)
				defer secure.Close()
				return serve(ctx,secure,M.SocksaddrFromNet(raw.RemoteAddr()),nil)
			}()
			serviceResult <- result
			if ctx.Err() != nil { return }
		}
	}()
	go func() {
		defer close(ownerReturned)
		result := func() error {
			control, err := controlListener.Accept()
			if err != nil { return err }
			track(control)
			defer control.Close()
			if ctx.Err() != nil { return ctx.Err() }
			control.SetDeadline(time.Now().Add(ownerRealBound))
			return socks.HandleConnectionExWithUDPAssociation(ctx,control,std_bufio.NewReader(control),nil,clientHandler,nil,time.Minute,M.SocksaddrFromNet(control.RemoteAddr()),nil,&socks.UDPAssociationOptions{
				Acquire:func()(func(),bool) { slots.Add(1); var once sync.Once; return func(){ once.Do(func(){ slots.Add(-1) }) },true },
				Observe:func(event socks.UDPAssociationEvent) { select { case events <- event: default: dropped.Store(true) } },
			})
		}()
		ownerResult <- result
	}()
	control, err := net.DialTimeout("tcp4",controlListener.Addr().String(),ownerRealBound)
	if err != nil { t.Fatal(err) }
	track(control)
	control.SetDeadline(time.Now().Add(ownerRealBound))
	if _, err := control.Write([]byte{5,1,0}); err != nil { t.Fatal(err) }
	var auth [2]byte
	if _, err := io.ReadFull(control,auth[:]); err != nil || auth != [2]byte{5,0} { t.Fatalf("SOCKS auth: %v %v",auth,err) }
	if _, err := control.Write([]byte{5,3,0,1,0,0,0,0,0,0}); err != nil { t.Fatal(err) }
	var response [10]byte
	if _, err := io.ReadFull(control,response[:]); err != nil || response[0] != 5 || response[1] != 0 || response[3] != 1 { t.Fatalf("SOCKS association: %v %v",response,err) }
	relay := &net.UDPAddr{IP:net.IP(response[4:8]),Port:int(binary.BigEndian.Uint16(response[8:10]))}
	for generation := 1; generation <= 2; generation++ {
		requested := target; if generation == 2 { requested = changed }
		frame := []byte{0,0,0,1,0,0,0,0,0,0,byte(generation)}
		ip := requested.Addr.As4(); copy(frame[4:8],ip[:]); binary.BigEndian.PutUint16(frame[8:10],requested.Port)
		attempts.Add(1)
		if n, err := application.WriteTo(frame,relay); err != nil || n != len(frame) { t.Fatalf("UDP frame: %d %v",n,err) }
		var metadataTarget M.Socksaddr
		select { case metadataTarget = <-clientHandler.metadataTargets: case <-time.After(ownerRealBound): t.Fatal("actual generation metadata missing") }
		var entry ownerRemoteRejectLedger
		select { case entry = <-serverHandler.ledger: case <-time.After(ownerRealBound): t.Fatal("actual mux payload ledger missing") }
		if metadataTarget != target || entry.dispatchTarget != target || entry.packetTarget != target || entry.err != nil || !bytes.Equal(entry.payload,[]byte{byte(generation)}) { t.Fatalf("actual generation ledger mismatch: metadata=%v dispatch=%v packet=%v payload=%x err=%v",metadataTarget,entry.dispatchTarget,entry.packetTarget,entry.payload,entry.err) }
		deadline := time.NewTimer(ownerRealBound)
	waitDormant:
		for { select {
			case event := <-events:
				if event.Transition == "closed" { deadline.Stop(); t.Fatalf("owner closed before recovery: %+v",event) }
				if event.Transition == "dormant" {
					if event.Generation != uint64(generation) || event.Target != target || event.AcceptedPackets != uint64(generation) || event.RemappedPackets != uint64(generation-1) { deadline.Stop(); t.Fatalf("owner accounting mismatch: %+v",event) }
					break waitDormant
				}
			case <-deadline.C: t.Fatal("owner did not join into Dormant")
		} }
		deadline.Stop()
		if clientHandler.calls.Load() != int32(generation) || clientHandler.callbacks.Load() != int32(generation) || clientHandler.returns.Load() != int32(generation) || serverHandler.calls.Load() != int32(generation) || accepted.Load() != int32(generation) || manager.Count() != 0 { t.Fatal("generation did not fully return exactly once") }
		quiet := time.NewTimer(30*time.Millisecond)
		select { case <-clientHandler.metadataTargets: quiet.Stop(); t.Fatal("automatic retry without a new application datagram"); case <-quiet.C: }
		t.Logf("actual reject-boundary ledger generation=%d attempt=1 requested=%s metadata=%s dispatch=%s packet=%s payload=%x target_delivery=0",generation,requested,metadataTarget,entry.dispatchTarget,entry.packetTarget,entry.payload)
	}
	if downstreamOutbounds.selections.Load() != 0 || serverHandler.tcp.Load() != 0 || dropped.Load() { t.Fatal("DIRECT/target fallback, TCP dispatch or observer overflow") }
	targetSocket.SetReadDeadline(time.Now().Add(30*time.Millisecond))
	var scratch [16]byte
	if _, _, err := targetSocket.ReadFrom(scratch[:]); err == nil { t.Fatal("rejected packet reached application target") } else if timeout, ok := err.(net.Error); !ok || !timeout.Timeout() { t.Fatal(err) }
	control.Close()
	select { case <-ownerResult: case <-time.After(ownerRealBound): t.Fatal("owner control close did not join") }
	ownerWait(t,ownerReturned,"real owner invocation")
	ownerWait(t,serviceReturned,"real downstream Service/TLS closes")
	if slots.Load() != 0 || clientHandler.calls.Load() != 2 || serverHandler.calls.Load() != 2 || clientOutbounds.selections.Load() != 2 { t.Fatal("joined owner replayed or retained limiter slot") }
}

func TestOwnerActualRemoteRejectNewPacketOnlyAES(t *testing.T) { ownerRemoteRejectNewPacketOnly(t,"aes-128-gcm") }
func TestOwnerActualRemoteRejectNewPacketOnlyChaCha(t *testing.T) { ownerRemoteRejectNewPacketOnly(t,"chacha20-poly1305") }
func TestOwnerActualRemoteRejectNewPacketOnlyVLESS(t *testing.T) { ownerRemoteRejectNewPacketOnly(t,"vless") }
