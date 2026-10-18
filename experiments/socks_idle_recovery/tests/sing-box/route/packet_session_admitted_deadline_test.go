package route

import (
	"bytes"
	"context"
	stdtls "crypto/tls"
	"encoding/binary"
	"fmt"
	"net"
	"reflect"
	"sync/atomic"
	"testing"
	"time"

	boxlog "github.com/sagernet/sing-box/log"
	vmess "github.com/sagernet/sing-vmess"
	vless "github.com/sagernet/sing-vmess/vless"
	"github.com/sagernet/sing/common/buf"
	M "github.com/sagernet/sing/common/metadata"
	N "github.com/sagernet/sing/common/network"
)

type cancelPacketLedger struct { target M.Socksaddr; payload []byte; err error }
type cancelActiveServer struct {
	fixture *cancelFixture
	payloads [][]byte
	replies []*cancelGate
	end *cancelGate
	ledger chan cancelPacketLedger
	returned chan struct{}
	result chan error
	calls, wrongCommand atomic.Int32
}

func (h *cancelActiveServer) NewConnectionEx(_ context.Context,c net.Conn,_,_ M.Socksaddr,_ N.CloseHandlerFunc) {
	h.wrongCommand.Add(1)
	_ = c.Close()
}
func (h *cancelActiveServer) NewPacketConnectionEx(ctx context.Context,c N.PacketConn,_ M.Socksaddr,target M.Socksaddr,onClose N.CloseHandlerFunc) {
	h.calls.Add(1)
	defer close(h.returned)
	result := func() error {
		defer c.Close()
		if reflect.TypeOf(c).String() != "*vmess.serverMuxPacketConn" || onClose != nil || target != h.fixture.target {
			return fmt.Errorf("actual CommandMux dispatch changed: type=%T target=%v",c,target)
		}
		for index,payload := range h.payloads {
			packet := buf.NewSize(65535)
			actual,err := c.ReadPacket(packet)
			entry := cancelPacketLedger{actual,append([]byte(nil),packet.Bytes()...),err}
			packet.Release()
			select {case h.ledger <- entry: case <-ctx.Done(): return ctx.Err()}
			if err != nil {return err}
			if actual != target || !bytes.Equal(entry.payload,payload) {return fmt.Errorf("actual server packet %d changed",index)}
			select {case <-h.replies[index].done: case <-ctx.Done(): return ctx.Err()}
			reply := buf.NewSize(len(payload))
			_,_ = reply.Write(payload)
			reply.IncRef()
			err = c.WritePacket(reply,target)
			reply.DecRef()
			reply.Release()
			if err != nil {return err}
		}
		select {case <-h.end.done: case <-ctx.Done(): return ctx.Err()}
		return c.Close()
	}()
	h.result <- result
}

func cancelServe(t *testing.T,f *cancelFixture,protocol string,handler vmess.Handler) func(context.Context,net.Conn,M.Socksaddr,N.CloseHandlerFunc)error {
	t.Helper()
	if protocol == "vless" {
		upstream := vless.NewService[string](boxlog.NewNOPFactory().Logger(),handler)
		upstream.UpdateUsers([]string{"fixture"},[]string{cancelFixtureUser},[]string{""})
		return upstream.NewConnection
	}
	upstream := vmess.NewService[string](handler)
	if err := upstream.UpdateUsers([]string{"fixture"},[]string{cancelFixtureUser},[]int{0});err != nil {t.Fatal(err)}
	f.after(func(){_ = upstream.Close()})
	return upstream.NewConnection
}

func cancelReadSOCKSReply(t *testing.T,f *cancelFixture,payload []byte) {
	t.Helper()
	wire := make([]byte,65535)
	_ = f.udp.SetReadDeadline(time.Now().Add(8*time.Second))
	n,source,err := f.udp.ReadFromUDP(wire)
	if err != nil {t.Fatal(err)}
	if M.SocksaddrFromNet(source).Unwrap() != f.relay.Unwrap() || n != 10+len(payload) || wire[0] != 0 || wire[1] != 0 || wire[2] != 0 || wire[3] != 1 {
		t.Fatalf("SOCKS reply envelope changed: n=%d source=%v",n,source)
	}
	ip := f.target.Addr.As4()
	if !bytes.Equal(wire[4:8],ip[:]) || binary.BigEndian.Uint16(wire[8:10]) != f.target.Port || !bytes.Equal(wire[10:n],payload) {
		t.Fatal("SOCKS reply target/payload changed")
	}
}

func TestOwnerConcreteAdmittedSurvivesOldDeadline(t *testing.T) {
	for _,protocol := range []string{"aes-128-gcm","chacha20-poly1305","vless"} {
		if !t.Run(protocol,func(t *testing.T) {
			f := newCancelFixture(t)
			certificate,trust := cancelCertificate(t)
			listener,err := net.Listen("tcp4","127.0.0.1:0")
			if err != nil {t.Fatal(err)}
			f.own(listener)
			handler := &cancelActiveServer{fixture:f,payloads:[][]byte{[]byte("before-old-deadline-unique"),[]byte("after-old-deadline-unique")},replies:[]*cancelGate{f.gate(),f.gate()},end:f.gate(),ledger:make(chan cancelPacketLedger,2),returned:make(chan struct{}),result:make(chan error,1)}
			serve := cancelServe(t,f,protocol,handler)
			serverDone := make(chan error,1)
			f.start(func(){
				result := func()error {
					raw,e := listener.Accept()
					if e != nil {return e}
					f.own(raw)
					defer raw.Close()
					secure := stdtls.Server(raw,&stdtls.Config{Certificates:[]stdtls.Certificate{certificate},SessionTicketsDisabled:true})
					defer secure.Close()
					return serve(f.ctx,secure,M.SocksaddrFromNet(raw.RemoteAddr()),nil)
				}()
				serverDone <- result // After all TLS/raw deferred closes return.
			})
			f.startOwner(cancelConcreteOutbound(t,f.ctx,M.SocksaddrFromNet(listener.Addr()),protocol,trust))
			controlLocal,controlRemote,relay := f.control.LocalAddr().String(),f.control.RemoteAddr().String(),f.relay
			f.join(handler.returned,"actual mux service handler")
			f.send(handler.payloads[0])
			cancelAwait(t,f.handler.admitted,"actual owner Admitted winning first")
			// Start this timer only after Admitted has returned. The owner's
			// original three-second deadline must already be earlier than this.
			pastOldDeadline := time.NewTimer(3200*time.Millisecond)
			defer pastOldDeadline.Stop()
			first := cancelAwait(t,handler.ledger,"first actual service packet")
			if first.err != nil || first.target != f.target || !bytes.Equal(first.payload,handler.payloads[0]) {t.Fatalf("first packet ledger: %+v",first)}
			if err := cancelAwait(t,f.handler.acks,"first actual upload acknowledgement");err != nil {t.Fatal(err)}
			_ = handler.replies[0].Close()
			cancelReadSOCKSReply(t,f,handler.payloads[0])
			<-pastOldDeadline.C
			cancelAssertPending(t,f.handler.callbacks,"active route callback")
			cancelAssertPending(t,f.ownerDone,"active SOCKS owner")
			f.manager.access.Lock()
			tracked := f.manager.Count()
			f.manager.access.Unlock()
			if tracked != 1 || f.slots.Load() != 1 || f.handler.admissions.Load() != 1 || f.handler.calls.Load() != 1 {
				t.Fatal("the expired admission timer retired an already admitted generation")
			}
			f.send(handler.payloads[1])
			second := cancelAwait(t,handler.ledger,"second actual service packet after old deadline")
			if second.err != nil || second.target != f.target || !bytes.Equal(second.payload,handler.payloads[1]) {t.Fatalf("second packet ledger: %+v",second)}
			if err := cancelAwait(t,f.handler.acks,"second actual upload acknowledgement");err != nil {t.Fatal(err)}
			_ = handler.replies[1].Close()
			cancelReadSOCKSReply(t,f,handler.payloads[1])
			_ = handler.end.Close()
			if cause := cancelAwait(t,f.handler.callbacks,"joined canonical End callback");cause != N.ErrPacketSessionEnded {t.Fatalf("unexpected retirement: %v",cause)}
			cancelAwait(t,f.handler.returned,"actual route invocation join")
			dormant := f.event("dormant")
			if dormant.Generation != 1 || dormant.AcceptedPackets != 2 {t.Fatalf("unexpected generation/packet ledger: %+v",dormant)}
			_ = cancelAwait(t,serverDone,"actual protocol Service and TLS/raw close join")
			if err := cancelAwait(t,handler.result,"actual service handler result");err != nil {t.Fatal(err)}
			cancelAwait(t,handler.returned,"actual service handler join")
			if handler.calls.Load() != 1 || handler.wrongCommand.Load() != 0 || f.manager.Count() != 0 {t.Fatal("extra generation, wrong outer command, or retained private transport")}
			if controlLocal != f.control.LocalAddr().String() || controlRemote != f.control.RemoteAddr().String() || relay != f.relay {t.Fatal("owner socket objects changed")}
			_ = f.control.Close()
			_ = cancelAwait(t,f.ownerDone,"actual owner final join")
			if f.slots.Load() != 0 {t.Fatal("owner slot retained after final join")}
		}) { return }
	}
}
