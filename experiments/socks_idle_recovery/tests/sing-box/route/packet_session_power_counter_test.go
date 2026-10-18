package route

import (
	stdbufio "bufio"
	"bytes"
	"context"
	stdtls "crypto/tls"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"os"
	"path/filepath"
	"sync/atomic"
	"testing"
	"time"

	"github.com/sagernet/sing-box/adapter"
	"github.com/sagernet/sing-box/common/dialer"
	boxtls "github.com/sagernet/sing-box/common/tls"
	boxlog "github.com/sagernet/sing-box/log"
	"github.com/sagernet/sing-box/option"
	"github.com/sagernet/sing-box/service/powerreport"
	"github.com/sagernet/sing/common/bufio"
	M "github.com/sagernet/sing/common/metadata"
	N "github.com/sagernet/sing/common/network"
	"github.com/sagernet/sing/service"
)

type cancelCloseFunc func()error
func (f cancelCloseFunc) Close()error {return f()}
type cancelWireConn struct {net.Conn; read,write atomic.Int64}
func(c *cancelWireConn)Read(p []byte)(int,error){n,err:=c.Conn.Read(p);c.read.Add(int64(n));return n,err}
func(c *cancelWireConn)Write(p []byte)(int,error){n,err:=c.Conn.Write(p);c.write.Add(int64(n));return n,err}
type cancelWireLedger struct {read,write int64;err error}
type cancelTLSPacket struct {N.PacketConn; secure net.Conn; closes atomic.Int32}
func(p *cancelTLSPacket)Close()error{p.closes.Add(1);return p.secure.Close()}

// This gate uses the real DefaultDialer.trackConn path and real power Recorder.
// It checks the exact accepted wrapper chain before installing audit probes.
// The probes forward the original count callbacks and Close; they do not stand
// in for a constructor, TLS record implementation, tracker, or power recorder.
func TestOwnerActualTrackedPowerCounterComposition(t *testing.T){
	reportPath:=t.TempDir()
	f:=newCancelFixture(t)
	power:=powerreport.NewManager()
	if err:=power.Start(powerreport.Options{BasePath:reportPath,Logger:boxlog.NewNOPFactory().Logger(),GateInterval:time.Hour,SampleInterval:time.Hour,FlushInterval:time.Hour,FallbackInterval:time.Hour});err!=nil{t.Fatal(err)}
	f.after(func(){if err:=power.Close();err!=nil{t.Error(err)}})
	ctx:=service.ContextWith[*powerreport.Manager](f.ctx,power)
	ctx=adapter.WithContext(ctx,&adapter.InboundContext{Inbound:"fixture-owner",Destination:f.target,Outbound:"ONLY"})
	certificate,trust:=cancelCertificate(t)
	listener,err:=net.Listen("tcp4","127.0.0.1:0")
	if err!=nil{t.Fatal(err)}
	f.own(listener)
	serverReady:=make(chan struct{})
	serverDone:=make(chan error,1)
	dataDone:=make(chan cancelWireLedger,1)
	readGate,closeGate:=f.gate(),f.gate()
	request,reply:=[]byte("counted request unique"),[]byte("counted response unique")
	f.start(func(){
		result:=func()error{
			tcp,e:=listener.Accept()
			if e!=nil{return e}
			f.own(tcp)
			defer tcp.Close()
			_ = tcp.SetDeadline(time.Now().Add(8*time.Second))
			wire:=&cancelWireConn{Conn:tcp}
			secure:=stdtls.Server(wire,&stdtls.Config{Certificates:[]stdtls.Certificate{certificate},SessionTicketsDisabled:true})
			defer secure.Close()
			if e=secure.HandshakeContext(f.ctx);e!=nil{return e}
			close(serverReady)
			select{case <-readGate.done:case <-f.ctx.Done():return f.ctx.Err()}
			beforeRead,beforeWrite:=wire.read.Load(),wire.write.Load()
			payload:=make([]byte,len(request))
			if _,e=io.ReadFull(secure,payload);e!=nil{return e}
			if !bytes.Equal(payload,request){return fmt.Errorf("TLS request changed")}
			if n,e:=secure.Write(reply);e!=nil||n!=len(reply){return fmt.Errorf("TLS response write n=%d err=%v",n,e)}
			dataDone<-cancelWireLedger{read:wire.read.Load()-beforeRead,write:wire.write.Load()-beforeWrite}
			select{case <-closeGate.done:case <-f.ctx.Done():}
			return nil
		}()
		serverDone<-result // Complete physical/TLS close join, not early result.
	})
	d,err:=dialer.NewDefault(ctx,option.DialerOptions{})
	if err!=nil{t.Fatal(err)}
	config,err:=boxtls.NewClient(ctx,boxlog.NewNOPFactory().Logger(),"owner.test",option.OutboundTLSOptions{Enabled:true,ServerName:"owner.test",Certificate:[]string{trust}})
	if err!=nil{t.Fatal(err)}
	admission,cancel:=context.WithTimeout(ctx,8*time.Second)
	defer cancel()
	var attachedAbort func()error
	secureConn,abort,err:=dialer.DialPacketSessionTLS(admission,d,M.SocksaddrFromNet(listener.Addr()),config,func(closeRaw func()error)error{
		attachedAbort=closeRaw
		f.own(cancelCloseFunc(closeRaw))
		return nil
	})
	if err!=nil{t.Fatal(err)}
	cancelAwait(t,serverReady,"actual power-composed TLS handshake")
	secure,ok:=secureConn.(*stdtls.Conn)
	if !ok{t.Fatalf("not actual ordinary TLS: %T",secureConn)}
	private,ok:=secure.NetConn().(*adapter.PacketSessionOnceConn)
	if !ok{t.Fatalf("TLS does not own exact private wrapper: %T",secure.NetConn())}
	counter,ok:=private.Conn.(*bufio.CounterConn)
	if !ok{t.Fatalf("real power CounterConn absent: %T",private.Conn)}
	extended,ok:=counter.ExtendedConn.(*bufio.ExtendedConnWrapper)
	if !ok{t.Fatalf("unexpected counter inner wrapper: %T",counter.ExtendedConn)}
	tracked,ok:=extended.Conn.(*trackedConn)
	if !ok{t.Fatalf("actual ConnectionManager tracker absent: %T",extended.Conn)}
	if _,ok:=tracked.Conn.(*net.TCPConn);!ok{t.Fatalf("actual leaf is not TCP: %T",tracked.Conn)}
	f.manager.access.Lock()
	trackedCount:=f.manager.Count()
	f.manager.access.Unlock()
	if trackedCount!=1||attachedAbort==nil||abort==nil{t.Fatal("track/attach did not occur exactly once")}
	_,readCounters:=counter.UnwrapReader()
	_,writeCounters:=counter.UnwrapWriter()
	if len(readCounters)!=1||len(writeCounters)!=1{t.Fatal("unexpected power callback composition")}
	originalRead,originalWrite:=readCounters[0],writeCounters[0]
	var readBytes,writeBytes atomic.Int64
	readCounters[0]=func(n int64){readBytes.Add(n);originalRead(n)}
	writeCounters[0]=func(n int64){writeBytes.Add(n);originalWrite(n)}
	f.after(func(){readCounters[0]=originalRead;writeCounters[0]=originalWrite})
	// No I/O is running here. After proving the candidate's real wrapper chain,
	// add only a transparent close-call audit immediately below that same once
	// wrapper. This does not claim capability admission of an unknown wrapper.
	closeAudit:=&cancelCountConn{Conn:counter,closed:make(chan struct{}),writeEntered:make(chan struct{})}
	private.Conn=closeAudit
	_ = secure.SetDeadline(time.Now().Add(8*time.Second))
	_ = readGate.Close()
	if n,e:=secure.Write(request);e!=nil||n!=len(request){t.Fatalf("client Write n=%d err=%v",n,e)}
	response:=make([]byte,len(reply))
	if _,err:=io.ReadFull(secure,response);err!=nil{t.Fatal(err)}
	if !bytes.Equal(response,reply){t.Fatal("actual TLS reply changed")}
	ledger:=cancelAwait(t,dataDone,"server actual TLS byte ledger")
	if ledger.err!=nil||readBytes.Load()!=ledger.write||writeBytes.Load()!=ledger.read||readBytes.Load()<=int64(len(reply))||writeBytes.Load()<=int64(len(request)){
		t.Fatalf("counter double/missing accounting: client in/out=%d/%d peer out/in=%d/%d err=%v",readBytes.Load(),writeBytes.Load(),ledger.write,ledger.read,ledger.err)
	}
	packet:=&cancelTLSPacket{secure:secure}
	var finalized atomic.Int32
	lease:=adapter.NewPacketSessionLease(packet,abort,func(){
		if closeAudit.closes.Load()!=1||packet.closes.Load()!=1{t.Error("cache finalizer preceded once raw/packet close")}
		finalized.Add(1)
	})
	if !lease.IsValid(){t.Fatal("actual composed lease invalid")}
	completed:=make(chan error,16)
	for range 16{
		f.start(func(){_ = lease.AbortTransport();completed<-lease.Finalize()})
	}
	for range 16{
		if err:=cancelAwait(t,completed,"concurrent once-only teardown");err!=nil&&!errors.Is(err,net.ErrClosed){t.Fatal(err)}
	}
	_ = attachedAbort()
	_ = secure.Close()
	if closeAudit.closes.Load()!=1||packet.closes.Load()!=1||finalized.Load()!=1||f.manager.Count()!=0{
		t.Fatalf("duplicate teardown: raw=%d packet=%d finalize=%d tracked=%d",closeAudit.closes.Load(),packet.closes.Load(),finalized.Load(),f.manager.Count())
	}
	_ = closeGate.Close()
	if err:=cancelAwait(t,serverDone,"power test TLS/raw worker join");err!=nil{t.Fatal(err)}
	if err:=power.Close();err!=nil{t.Fatal(err)}
	paths,err:=filepath.Glob(filepath.Join(reportPath,powerreport.ReportsDirectoryName,"*","timeline.jsonl"))
	if err!=nil||len(paths)!=1{t.Fatalf("missing actual joined power report: paths=%v err=%v",paths,err)}
	file,err:=os.Open(paths[0])
	if err!=nil{t.Fatal(err)}
	defer file.Close()
	var opened uint64
	scanner:=stdbufio.NewScanner(file)
	for scanner.Scan(){
		var row struct{ConnectionsOpened uint64}
		if err:=json.Unmarshal(scanner.Bytes(),&row);err!=nil{t.Fatal(err)}
		opened+=row.ConnectionsOpened
	}
	if err:=scanner.Err();err!=nil{t.Fatal(err)}
	if opened!=1{t.Fatalf("actual Recorder connection accounting=%d, want 1",opened)}
}
