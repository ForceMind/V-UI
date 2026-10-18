package route

import (
	stdbufio "bufio"
	"context"
	stdtls "crypto/tls"
	"crypto/x509"
	"crypto/ed25519"
	"crypto/rand"
	"crypto/x509/pkix"
	"encoding/pem"
	"math/big"
	"net/netip"
	"encoding/binary"
	"errors"
	"fmt"
	"io"
	"net"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/sagernet/sing-box/adapter"
	boxlog "github.com/sagernet/sing-box/log"
	R "github.com/sagernet/sing-box/route/rule"
	C "github.com/sagernet/sing-box/constant"
	"github.com/sagernet/sing-box/option"
	boxvless "github.com/sagernet/sing-box/protocol/vless"
	boxvmess "github.com/sagernet/sing-box/protocol/vmess"
	vmess "github.com/sagernet/sing-vmess"
	vless "github.com/sagernet/sing-vmess/vless"
	"github.com/sagernet/sing/common/buf"
	M "github.com/sagernet/sing/common/metadata"
	N "github.com/sagernet/sing/common/network"
	"github.com/sagernet/sing/protocol/socks"
	"github.com/sagernet/sing/protocol/socks/socks5"
	"github.com/sagernet/sing/service"
)

// Concrete TCP/TLS leaves and deterministic net.Pipe outer-write leaves are
// separate claims. A tiny outer request need not block on a real TCP socket.
const cancelFixtureUser = "11111111-2222-4333-8444-555555555555"

type cancelFixture struct {
	t *testing.T
	ctx context.Context
	cancel context.CancelFunc
	mu sync.Mutex
	closers []io.Closer
	joins []cancelJoin
	afterJoin []func()
	events []socks.UDPAssociationEvent
	notify chan struct{}
	control net.Conn
	udp *net.UDPConn
	relay, target M.Socksaddr
	ownerDone chan error
	slots atomic.Int32
	manager *ConnectionManager
	handler *cancelFixtureHandler
}

func newCancelFixture(t *testing.T) *cancelFixture {
	t.Helper()
	original := buf.DefaultAllocator
	accounting := &cancelAllocator{underlying:original}
	buf.DefaultAllocator = accounting
	ctx, cancel := context.WithCancel(context.Background())
	f := &cancelFixture{t:t, ctx:ctx, cancel:cancel, notify:make(chan struct{},1), target:M.ParseSocksaddr("127.0.0.3:12345"), ownerDone:make(chan error,1), manager:NewConnectionManager(boxlog.NewNOPFactory().Logger())}
	f.ctx = service.ContextWith[adapter.ConnectionManager](f.ctx,f.manager)
	t.Cleanup(func() {
		deadline := time.NewTimer(8*time.Second)
		defer deadline.Stop()
		f.cancel()
		f.mu.Lock()
		closers := append([]io.Closer(nil),f.closers...)
		joins := append([]cancelJoin(nil),f.joins...)
		afterJoin := append([]func(){},f.afterJoin...)
		f.mu.Unlock()
		for _, closer := range closers { _ = closer.Close() }
		_ = f.manager.Close()
		// Every launched fixture worker has its own returned channel, registered
		// before launch. No additional goroutine waits on a WaitGroup here.
		for _, join := range joins {
			select {
			case <-join.done:
			case <-deadline.C:
				panic("FAIL: unjoined "+join.label+"; refusing allocator restoration")
			}
		}
		for _, after := range afterJoin { after() }
		buf.DefaultAllocator = original
		if !accounting.balanced() { t.Error("allocator not at baseline after complete cleanup/join") }
	})
	return f
}

func (f *cancelFixture) own(closer io.Closer) {
	f.mu.Lock()
	f.closers = append(f.closers,closer)
	stopping := f.ctx.Err() != nil
	f.mu.Unlock()
	if stopping { _ = closer.Close() }
}

func (f *cancelFixture) start(run func()) {
	done := make(chan struct{})
	f.mu.Lock()
	if f.ctx.Err() != nil { f.mu.Unlock(); panic("fixture worker launched after cleanup began") }
	f.joins = append(f.joins,cancelJoin{done,fmt.Sprintf("fixture worker %d",len(f.joins))})
	f.mu.Unlock()
	go func() { defer close(done); run() }()
}

func cancelAwait[T any](t *testing.T, ch <-chan T, what string) T {
	t.Helper()
	select {
	case value := <-ch:
		return value
	case <-time.After(8*time.Second):
		t.Fatalf("FAIL: timeout waiting for %s",what)
		var zero T
		return zero
	}
}

func cancelAssertPending[T any](t *testing.T, ch <-chan T, what string) {
	t.Helper()
	select {
	case <-ch: t.Fatalf("%s completed before required join barrier",what)
	default:
	}
}

func (f *cancelFixture) observe(event socks.UDPAssociationEvent) {
	f.mu.Lock(); f.events = append(f.events,event); f.mu.Unlock()
	// The event is retained above; only these coalesced wakeups may be dropped.
	select { case f.notify <- struct{}{}: default: }
}

func (f *cancelFixture) event(transition string) socks.UDPAssociationEvent {
	f.t.Helper()
	timer := time.NewTimer(8*time.Second)
	defer timer.Stop()
	for {
		f.mu.Lock()
		for _, event := range f.events {
			if event.Transition == transition { f.mu.Unlock(); return event }
		}
		f.mu.Unlock()
		select {
		case <-f.notify:
		case <-timer.C: f.t.Fatalf("missing owner transition %s",transition)
		}
	}
}

func (f *cancelFixture) startOwner(outbound adapter.Outbound) {
	f.t.Helper()
	router := cancelRouter(f.t,outbound,f.manager)
	f.handler = &cancelFixtureHandler{router:router,attached:make(chan struct{},1),admitted:make(chan struct{},1),acks:make(chan error,8),callbacks:make(chan error,2),returned:make(chan struct{},2)}
	listener, err := net.Listen("tcp4","127.0.0.1:0")
	if err != nil { f.t.Fatal(err) }
	f.own(listener)
	f.start(func() {
		control, acceptErr := listener.Accept()
		if acceptErr != nil { f.ownerDone <- acceptErr; return }
		f.own(control)
		f.ownerDone <- socks.HandleConnectionExWithUDPAssociation(f.ctx,control,stdbufio.NewReader(control),nil,f.handler,nil,time.Minute,M.SocksaddrFromNet(control.RemoteAddr()),nil,&socks.UDPAssociationOptions{
			Acquire:func()(func(),bool) { f.slots.Add(1); return func() { f.slots.Add(-1) },true },
			Observe:f.observe,
		})
	})
	f.control, err = net.DialTimeout("tcp4",listener.Addr().String(),8*time.Second)
	if err != nil { f.t.Fatal(err) }
	f.own(f.control)
	_ = f.control.SetDeadline(time.Now().Add(8*time.Second))
	response, err := socks.ClientHandshake5(f.control,socks5.CommandUDPAssociate,M.ParseSocksaddr("0.0.0.0:0"),"","")
	if err != nil { f.t.Fatal(err) }
	_ = f.control.SetDeadline(time.Time{})
	f.relay = response.Bind
	f.udp, err = net.ListenUDP("udp4",&net.UDPAddr{IP:net.IPv4(127,0,0,1)})
	if err != nil { f.t.Fatal(err) }
	f.own(f.udp)
}

func (f *cancelFixture) send(payload []byte) {
	f.t.Helper()
	wire := make([]byte,10+len(payload))
	wire[3] = 1
	ip := f.target.Addr.As4()
	copy(wire[4:8],ip[:])
	binary.BigEndian.PutUint16(wire[8:10],f.target.Port)
	copy(wire[10:],payload)
	if n, err := f.udp.WriteToUDP(wire,f.relay.UDPAddr()); err != nil || n != len(wire) {
		f.t.Fatalf("SOCKS datagram write: n=%d err=%v",n,err)
	}
}

func (f *cancelFixture) joinedNoCopies() {
	f.t.Helper()
	cause := cancelAwait(f.t,f.handler.callbacks,"joined route callback")
	if cause == nil || errors.Is(cause,N.ErrPacketSessionEnded) { f.t.Fatalf("constructor failure became recoverable: %v",cause) }
	cancelAwait(f.t,f.handler.returned,"route invocation return")
	_ = cancelAwait(f.t,f.ownerDone,"actual owner join")
	if f.slots.Load() != 0 || f.handler.calls.Load() != 1 || f.handler.reads.Load() != 0 || f.handler.writes.Load() != 0 || f.handler.admissions.Load() != 0 || f.manager.Count() != 0 {
		f.t.Fatalf("canceled construction published resources: slots=%d calls=%d reads=%d writes=%d admissions=%d tracked=%d",f.slots.Load(),f.handler.calls.Load(),f.handler.reads.Load(),f.handler.writes.Load(),f.handler.admissions.Load(),f.manager.Count())
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	for _, event := range f.events {
		if event.Generation > 1 || event.Transition == "admitted" || event.Transition == "dormant" { f.t.Fatalf("late constructor changed owner state: %+v",event) }
	}
}

type cancelFixtureHandler struct {
	router *Router
	calls, reads, writes, admissions, aborts atomic.Int32
	attached, admitted chan struct{}
	acks chan error
	callbacks chan error
	returned chan struct{}
}

func (h *cancelFixtureHandler) NewConnectionEx(_ context.Context,c net.Conn,_,_ M.Socksaddr,_ N.CloseHandlerFunc) { _ = c.Close() }
func (h *cancelFixtureHandler) NewPacketConnectionEx(ctx context.Context,c N.PacketConn,source,target M.Socksaddr,onClose N.CloseHandlerFunc) {
	h.calls.Add(1)
	lifecycle := &cancelFixtureLifecycle{PacketSessionLifecycle:c.(N.PacketSessionLifecycle),h:h}
	counted := &cancelFixturePacket{PacketConn:c,h:h}
	ctx = adapter.WithPacketSessionPolicy(ctx,adapter.PacketSessionPolicy{Outbound:"ONLY",Target:target,Lifecycle:lifecycle})
	h.router.RoutePacketConnectionEx(ctx,counted,adapter.InboundContext{Inbound:"owner",InboundType:C.TypeMixed,Source:source,Destination:target},func(err error) { h.callbacks <- err; onClose(err) })
	h.returned <- struct{}{}
}

type cancelFixtureLifecycle struct { N.PacketSessionLifecycle; h *cancelFixtureHandler }
func (l *cancelFixtureLifecycle) AttachPacketSessionAbort(abort func()error) error {
	err := l.PacketSessionLifecycle.AttachPacketSessionAbort(func()error { l.h.aborts.Add(1); return abort() })
	l.h.attached <- struct{}{}
	return err
}
func (l *cancelFixtureLifecycle) PacketSessionAdmitted() error {
	err := l.PacketSessionLifecycle.PacketSessionAdmitted()
	if err == nil { l.h.admissions.Add(1); l.h.admitted <- struct{}{} }
	return err
}
func (l *cancelFixtureLifecycle) PacketSessionWriteFinished(err error) {
	l.PacketSessionLifecycle.PacketSessionWriteFinished(err)
	l.h.acks <- err
}
type cancelFixturePacket struct { N.PacketConn; h *cancelFixtureHandler }
func (*cancelFixturePacket) FrontHeadroom()int { return 10 }
func (*cancelFixturePacket) ReaderOverhead()int { return 0 }
func (c *cancelFixturePacket) ReadPacket(b *buf.Buffer)(M.Socksaddr,error) { c.h.reads.Add(1); return c.PacketConn.ReadPacket(b) }
func (c *cancelFixturePacket) WritePacket(b *buf.Buffer,target M.Socksaddr)error { c.h.writes.Add(1); return c.PacketConn.WritePacket(b,target) }

func cancelConcreteOutbound(t *testing.T,ctx context.Context,address M.Socksaddr,protocol,trust string) adapter.Outbound {
	t.Helper()
	tlsOptions := option.OutboundTLSOptionsContainer{TLS:&option.OutboundTLSOptions{Enabled:true,ServerName:"owner.test",Certificate:[]string{trust}}}
	var outbound adapter.Outbound
	var err error
	if protocol == "vless" {
		outbound,err = boxvless.NewOutbound(ctx,nil,boxlog.NewNOPFactory().Logger(),"ONLY",option.VLESSOutboundOptions{ServerOptions:option.ServerOptions{Server:address.Addr.String(),ServerPort:address.Port},UUID:cancelFixtureUser,OutboundTLSOptionsContainer:tlsOptions})
	} else {
		outbound,err = boxvmess.NewOutbound(ctx,nil,boxlog.NewNOPFactory().Logger(),"ONLY",option.VMessOutboundOptions{ServerOptions:option.ServerOptions{Server:address.Addr.String(),ServerPort:address.Port},UUID:cancelFixtureUser,Security:protocol,PacketEncoding:"xudp",GlobalPadding:true,AuthenticatedLength:true,OutboundTLSOptionsContainer:tlsOptions})
	}
	if err != nil { t.Fatal(err) }
	return outbound
}

func TestOwnerConcreteTLSConstructionCancellation(t *testing.T) {
	for _, protocol := range []string{"aes-128-gcm","chacha20-poly1305","vless"} {
		for _, ending := range []string{"owner_deadline","control_close"} {
			if !t.Run(protocol+"/"+ending,func(t *testing.T) {
				f := newCancelFixture(t)
				_,trust := cancelCertificate(t)
				listener,err := net.Listen("tcp4","127.0.0.1:0")
				if err != nil { t.Fatal(err) }
				f.own(listener)
				clientHello := make(chan byte,1)
				serverDone := make(chan error,1)
				f.start(func() {
					raw,e := listener.Accept()
					if e != nil { serverDone <- e; return }
					f.own(raw)
					var first [1]byte
					_,e = io.ReadFull(raw,first[:])
					if e != nil { serverDone <- e; return }
					clientHello <- first[0]
					// Consume ClientHello but send no ServerHello. This is actual
					// concrete Go TLS synchronous network I/O, not an outer write.
					_,e = io.Copy(io.Discard,raw)
					serverDone <- e
				})
				f.startOwner(cancelConcreteOutbound(t,f.ctx,M.SocksaddrFromNet(listener.Addr()),protocol,trust))
				started := time.Now()
				f.send([]byte("not yet admitted"))
				if first := cancelAwait(t,clientHello,"real ClientHello"); first != 22 { t.Fatalf("not TLS handshake: %d",first) }
				cancelAwait(t,f.handler.attached,"raw TCP abort attachment")
				cancelAssertPending(t,f.handler.callbacks,"TLS constructor callback")
				if ending == "control_close" { _ = f.control.Close() }
				closing := f.event("closing")
				if ending == "owner_deadline" && (time.Since(started) < 2900*time.Millisecond || (closing.Cause != "admission_timeout" && closing.Cause != "deadline")) {
					t.Fatalf("not the admission deadline: elapsed=%s event=%+v",time.Since(started),closing)
				}
				f.joinedNoCopies()
				if err := cancelAwait(t,serverDone,"actual TCP peer EOF"); err != nil { t.Fatalf("unexpected peer read error instead of EOF: %v",err) }
				if f.handler.aborts.Load() != 1 { t.Fatalf("owner abort calls=%d",f.handler.aborts.Load()) }
			}) { return }
		}
	}
}

type cancelCountConn struct {
	net.Conn
	closes atomic.Int32
	closed chan struct{}
	closeOnce sync.Once
	armed atomic.Bool
	writeEntered chan struct{}
	writeOnce sync.Once
	writeReturned atomic.Bool
}
func (c *cancelCountConn) Close()error {
	c.closes.Add(1)
	c.closeOnce.Do(func() { close(c.closed) })
	return c.Conn.Close()
}
func (c *cancelCountConn) Write(p []byte)(int,error) {
	armed := c.armed.Load()
	if armed { c.writeOnce.Do(func() { close(c.writeEntered) }) }
	n,err := c.Conn.Write(p)
	if armed { c.writeReturned.Store(true) }
	return n,err
}

// Any normal/legacy entry point panics through the nil embedded Outbound if the
// real Router/ConnectionManager unexpectedly bypasses OpenPacketSession.
type cancelProvider struct {
	adapter.Outbound
	open func(context.Context,M.Socksaddr,func(func()error)error)(*adapter.PacketSessionLease,error)
}
func (*cancelProvider) ValidatePacketSession()error { return nil }
func (*cancelProvider) Tag()string { return "ONLY" }
func (*cancelProvider) Type()string { return "fixture-private-constructor" }
func (*cancelProvider) Network()[]string { return []string{N.NetworkUDP} }
func (*cancelProvider) Dependencies()[]string { return nil }
func (p *cancelProvider) OpenPacketSession(ctx context.Context,target M.Socksaddr,attach func(func()error)error)(*adapter.PacketSessionLease,error) { return p.open(ctx,target,attach) }

func TestOwnerRealOuterConstructorPipeCancellation(t *testing.T) {
	for _,protocol := range []string{"aes-128-gcm","chacha20-poly1305","vless"} {
		for _,ending := range []string{"owner_deadline","control_close"} {
			if !t.Run(protocol+"/"+ending,func(t *testing.T) {
				f := newCancelFixture(t)
				certificate,trust := cancelCertificate(t)
				roots := x509.NewCertPool()
				if !roots.AppendCertsFromPEM([]byte(trust)) { t.Fatal("fixture trust") }
				client,server := net.Pipe()
				f.own(server)
				counted := &cancelCountConn{Conn:client,closed:make(chan struct{}),writeEntered:make(chan struct{})}
				raw := &adapter.PacketSessionOnceConn{Conn:counted}
				f.own(raw)
				serverDone := make(chan error,1)
				constructorDone := make(chan error,1)
				var partial atomic.Bool
				f.start(func() {
					secure := stdtls.Server(server,&stdtls.Config{Certificates:[]stdtls.Certificate{certificate},SessionTicketsDisabled:true})
					if err := secure.HandshakeContext(f.ctx); err != nil { serverDone <- err; return }
					// No application reader exists. net.Pipe blocks the real TLS
					// encrypted outer write until the owner closes the attached raw.
					select { case <-counted.closed: case <-f.ctx.Done(): }
					_ = secure.Close()
					serverDone <- nil
				})
				provider := &cancelProvider{open:func(ctx context.Context,target M.Socksaddr,attach func(func()error)error)(*adapter.PacketSessionLease,error) {
					if err := attach(raw.Close); err != nil { return nil,err }
					secure := stdtls.Client(raw,&stdtls.Config{ServerName:"owner.test",RootCAs:roots})
					if err := secure.HandshakeContext(ctx); err != nil { return nil,err }
					counted.armed.Store(true)
					var packet vmess.PacketConn
					var err error
					if protocol == "vless" {
						client,e := vless.NewClient(cancelFixtureUser,"",boxlog.NewNOPFactory().Logger())
						if e != nil { return nil,e }
						packet,err = client.DialEarlyXUDPPacketConn(secure,target)
					} else {
						client,e := vmess.NewClient(cancelFixtureUser,protocol,0,vmess.ClientWithGlobalPadding(),vmess.ClientWithAuthenticatedLength())
						if e != nil { return nil,e }
						packet,err = client.DialXUDPPacketConn(secure,target)
					}
					constructorDone <- err
					_ = raw.Close()
					if packet != nil { partial.Store(true); _ = packet.Close() } else { _ = secure.Close() }
					return nil,err
				}}
				f.startOwner(provider)
				started := time.Now()
				f.send([]byte("no copy before constructor"))
				cancelAwait(t,counted.writeEntered,"actual constructor encrypted outer Write entry")
				if counted.writeReturned.Load() { t.Fatal("outer write was not blocked") }
				cancelAssertPending(t,constructorDone,"actual constructor")
				if ending == "control_close" { _ = f.control.Close() }
				closing := f.event("closing")
				if ending == "owner_deadline" && (closing.Cause != "admission_timeout" || time.Since(started) < 2900*time.Millisecond) {
					t.Fatalf("synchronous outer write was not released by owner deadline: %+v",closing)
				}
				if err := cancelAwait(t,constructorDone,"real constructor joined after raw abort"); err == nil { t.Fatal("blocked outer write succeeded after abort") }
				f.joinedNoCopies()
				if err := cancelAwait(t,serverDone,"TLS fixture peer join"); err != nil { t.Fatal(err) }
				if counted.closes.Load() != 1 || !counted.writeReturned.Load() || partial.Load() != (protocol == "vless") {
					t.Fatalf("constructor cleanup mismatch: closes=%d write-return=%v partial=%v",counted.closes.Load(),counted.writeReturned.Load(),partial.Load())
				}
			}) { return }
		}
	}
}

type cancelLatePacket struct { N.PacketConn; accesses,closes atomic.Int32 }
func (p *cancelLatePacket) ReadPacket(*buf.Buffer)(M.Socksaddr,error) { p.accesses.Add(1); return M.Socksaddr{},net.ErrClosed }
func (p *cancelLatePacket) WritePacket(b *buf.Buffer,_ M.Socksaddr)error { p.accesses.Add(1); b.Release(); return net.ErrClosed }
func (p *cancelLatePacket) Close()error { p.closes.Add(1); return nil }

func TestOwnerLateLeaseCancellationPermutations(t *testing.T) {
	for _,attachment := range []string{"attached_before_close","attach_after_close"} {
		for _,returned := range []string{"late_success","partial_error"} {
			for _,ending := range []string{"owner_deadline","control_close"} {
				if !t.Run(attachment+"/"+returned+"/"+ending,func(t *testing.T) {
					f := newCancelFixture(t)
					left,right := net.Pipe()
					f.own(right)
					counted := &cancelCountConn{Conn:left,closed:make(chan struct{}),writeEntered:make(chan struct{})}
					raw := &adapter.PacketSessionOnceConn{Conn:counted}
					f.own(raw)
					packet := new(cancelLatePacket)
					entered,finalizing := make(chan struct{}),make(chan struct{})
					releaseConstructor,releaseFinalizer := f.gate(),f.gate()
					var constructorReturned atomic.Bool
					var releases atomic.Int32
					var orderFailure atomic.Bool
					lease := adapter.NewPacketSessionLease(packet,raw.Close,func() {
						if !constructorReturned.Load() || counted.closes.Load() != 1 { orderFailure.Store(true) }
						releases.Add(1)
						close(finalizing)
						select { case <-releaseFinalizer.done: case <-f.ctx.Done(): }
					})
					provider := &cancelProvider{open:func(_ context.Context,_ M.Socksaddr,attach func(func()error)error)(*adapter.PacketSessionLease,error) {
						if attachment == "attached_before_close" {
							if err := attach(raw.Close); err != nil { return lease,err }
						}
						close(entered)
						// Model a dial/constructor that ignores its own context.
						// Fixture-wide cancellation remains a failure cleanup escape.
						select { case <-releaseConstructor.done: case <-f.ctx.Done(): }
						if attachment == "attach_after_close" { _ = attach(raw.Close) }
						constructorReturned.Store(true)
						if returned == "partial_error" { return lease,fmt.Errorf("fixture partial constructor error") }
						return lease,nil
					}}
					f.startOwner(provider)
					f.send([]byte("must remain pending"))
					cancelAwait(t,entered,"constructor barrier")
					if ending == "control_close" { _ = f.control.Close() }
					closing := f.event("closing")
					if ending == "owner_deadline" && closing.Cause != "admission_timeout" { t.Fatalf("wrong owner deadline: %+v",closing) }
					if f.slots.Load() != 1 { t.Fatal("owner slot released before constructor return") }
					cancelAssertPending(t,f.ownerDone,"owner")
					cancelAssertPending(t,f.handler.returned,"route invocation")
					_ = releaseConstructor.Close()
					cancelAwait(t,finalizing,"late lease finalizer entry")
					if f.slots.Load() != 1 || packet.accesses.Load() != 0 || packet.closes.Load() != 1 { t.Fatal("late lease published or joined before cache finalization") }
					cancelAssertPending(t,f.handler.callbacks,"route callback")
					cancelAssertPending(t,f.ownerDone,"owner")
					_ = releaseFinalizer.Close()
					f.joinedNoCopies()
					if releases.Load() != 1 || packet.closes.Load() != 1 || counted.closes.Load() != 1 || orderFailure.Load() {
						t.Fatalf("once-close/join order failed: releases=%d packet-close=%d raw-close=%d order-failure=%v",releases.Load(),packet.closes.Load(),counted.closes.Load(),orderFailure.Load())
					}
				}) { return }
			}
		}
	}
}


// These local helpers intentionally remove dependency on other owner test files.
type cancelAllocator struct {
 underlying buf.Allocator
 mu sync.Mutex
 gets, puts int
 live map[*byte]int
 invalid bool
}
func (a *cancelAllocator) Get(size int)[]byte {
 b:=a.underlying.Get(size)
 if len(b)>0 { a.mu.Lock(); if a.live==nil {a.live=make(map[*byte]int)}; if _,ok:=a.live[&b[0]];ok {a.invalid=true}; a.live[&b[0]]=cap(b);a.gets++;a.mu.Unlock() }
 return b
}
func (a *cancelAllocator) Put(b []byte)error {
 a.mu.Lock()
 if len(b)==0 {a.invalid=true;a.mu.Unlock();return errors.New("empty allocator release")}
 _,ok:=a.live[&b[0]]
 if ok {delete(a.live,&b[0]);a.puts++} else {a.invalid=true}
 a.mu.Unlock()
 if !ok {return errors.New("double or foreign allocator release")}
 return a.underlying.Put(b)
}
func (a *cancelAllocator) balanced()bool {a.mu.Lock();defer a.mu.Unlock();return !a.invalid&&len(a.live)==0&&a.gets==a.puts}

type cancelDNS struct {adapter.DNSRouter}
func(cancelDNS)LookupReverseMapping(netip.Addr)(string,bool){return "",false}
type cancelDNSTransports struct {adapter.DNSTransportManager}
func(cancelDNSTransports)FakeIP()adapter.FakeIPTransport{return nil}
type cancelOutbounds struct {adapter.OutboundManager; outbound adapter.Outbound}
func(o *cancelOutbounds)Outbound(tag string)(adapter.Outbound,bool){return o.outbound,tag=="ONLY"}
func(*cancelOutbounds)Default()adapter.Outbound{panic("constructor fixture attempted DIRECT")}
func cancelRouter(t *testing.T,outbound adapter.Outbound,manager *ConnectionManager)*Router {
 t.Helper()
 log:=boxlog.NewNOPFactory().Logger()
 rule,err:=R.NewRule(context.Background(),log,option.Rule{Type:C.RuleTypeDefault,DefaultOptions:option.DefaultRule{RawDefaultRule:option.RawDefaultRule{Network:[]string{N.NetworkUDP}},RuleAction:option.RuleAction{Action:C.RuleActionTypeRoute,RouteOptions:option.RouteActionOptions{Outbound:"ONLY"}}}},false)
 if err!=nil {t.Fatal(err)}
 return &Router{ctx:context.Background(),logger:log,rules:[]adapter.Rule{rule},outbound:&cancelOutbounds{outbound:outbound},dns:cancelDNS{},dnsTransport:cancelDNSTransports{},connection:manager}
}
func cancelCertificate(t *testing.T)(stdtls.Certificate,string){
 t.Helper()
 public,private,err:=ed25519.GenerateKey(rand.Reader);if err!=nil{t.Fatal(err)}
 template:=&x509.Certificate{SerialNumber:big.NewInt(1),Subject:pkix.Name{CommonName:"owner.test"},DNSNames:[]string{"owner.test"},NotBefore:time.Now().Add(-time.Hour),NotAfter:time.Now().Add(time.Hour),KeyUsage:x509.KeyUsageDigitalSignature,ExtKeyUsage:[]x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},BasicConstraintsValid:true}
 der,err:=x509.CreateCertificate(rand.Reader,template,template,public,private);if err!=nil{t.Fatal(err)}
 return stdtls.Certificate{Certificate:[][]byte{der},PrivateKey:private},string(pem.EncodeToMemory(&pem.Block{Type:"CERTIFICATE",Bytes:der}))
}

type cancelJoin struct {done <-chan struct{};label string}
func(f *cancelFixture) join(done <-chan struct{},label string){f.mu.Lock();f.joins=append(f.joins,cancelJoin{done,label});f.mu.Unlock()}
func(f *cancelFixture) after(run func()){f.mu.Lock();f.afterJoin=append(f.afterJoin,run);f.mu.Unlock()}
type cancelGate struct {done chan struct{};once sync.Once}
func(g *cancelGate)Close()error{g.once.Do(func(){close(g.done)});return nil}
func(f *cancelFixture)gate()*cancelGate{g:=&cancelGate{done:make(chan struct{})};f.own(g);return g}
