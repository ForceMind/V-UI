package route

import (
	"context"
	stdtls "crypto/tls"
	"crypto/x509"
	"fmt"
	"net"
	"sync/atomic"
	"testing"

	"github.com/sagernet/sing-box/adapter"
	boxlog "github.com/sagernet/sing-box/log"
	vmess "github.com/sagernet/sing-vmess"
	vless "github.com/sagernet/sing-vmess/vless"
	M "github.com/sagernet/sing/common/metadata"
	N "github.com/sagernet/sing/common/network"
)

// Outer CommandMux alone has no application session to dispatch. Reaching either
// handler is a protocol mismatch and an immediate failed test, never a fake End.
type cancelOuterOnlyHandler struct{}
func (cancelOuterOnlyHandler) NewConnectionEx(context.Context,net.Conn,M.Socksaddr,M.Socksaddr,N.CloseHandlerFunc) {
	panic("outer-only fixture unexpectedly dispatched an application TCP session")
}
func (cancelOuterOnlyHandler) NewPacketConnectionEx(context.Context,N.PacketConn,M.Socksaddr,M.Socksaddr,N.CloseHandlerFunc) {
	panic("outer-only fixture unexpectedly dispatched an application UDP session")
}

// Here the real constructor succeeds before cancellation, but its provider
// return is held. This complements the blocked-Write error cases: cancellation
// wins the publication race and the real late lease still gets finalized with
// zero application copies. The provider barrier is test-only, not a dial hook.
func TestOwnerRealConstructedLateLeaseCancellation(t *testing.T) {
	for _,protocol:=range []string{"aes-128-gcm","chacha20-poly1305","vless"}{
		for _,ending:=range []string{"owner_deadline","control_close"}{
			if !t.Run(protocol+"/"+ending,func(t *testing.T){
				f:=newCancelFixture(t)
				certificate,trust:=cancelCertificate(t)
				roots:=x509.NewCertPool()
				if !roots.AppendCertsFromPEM([]byte(trust)){t.Fatal("fixture trust")}
				left,right:=net.Pipe()
				f.own(right)
				counted:=&cancelCountConn{Conn:left,closed:make(chan struct{}),writeEntered:make(chan struct{})}
				raw:=&adapter.PacketSessionOnceConn{Conn:counted}
				f.own(raw)
				holdReturn:=f.gate()
				leaseReady:=make(chan *adapter.PacketSessionLease,1)
				serverDone:=make(chan error,1)
				var constructorJoined,finalizerOrderFailure atomic.Bool
				var finalized atomic.Int32
				serve:=cancelServe(t,f,protocol,cancelOuterOnlyHandler{})
				f.start(func(){
					result:=func()error{
						secure:=stdtls.Server(right,&stdtls.Config{Certificates:[]stdtls.Certificate{certificate},SessionTicketsDisabled:true})
						defer secure.Close()
						return serve(f.ctx,secure,M.ParseSocksaddr("127.0.0.1:24567"),nil)
					}()
					serverDone<-result
				})
				provider:=&cancelProvider{open:func(ctx context.Context,target M.Socksaddr,attach func(func()error)error)(*adapter.PacketSessionLease,error){
					if err:=attach(raw.Close);err!=nil{return nil,err}
					secure:=stdtls.Client(raw,&stdtls.Config{ServerName:"owner.test",RootCAs:roots})
					if err:=secure.HandshakeContext(ctx);err!=nil{return nil,err}
					counted.armed.Store(true)
					var packet vmess.PacketConn
					var err error
					if protocol=="vless"{
						client,e:=vless.NewClient(cancelFixtureUser,"",boxlog.NewNOPFactory().Logger())
						if e!=nil{return nil,e}
						packet,err=client.DialEarlyXUDPPacketConn(secure,target)
					}else{
						client,e:=vmess.NewClient(cancelFixtureUser,protocol,0,vmess.ClientWithGlobalPadding(),vmess.ClientWithAuthenticatedLength())
						if e!=nil{return nil,e}
						packet,err=client.DialXUDPPacketConn(secure,target)
					}
					constructorJoined.Store(true)
					if err!=nil{
						_ = raw.Close()
						if packet!=nil{_ = packet.Close()}else{_ = secure.Close()}
						return nil,err
					}
					xudp,ok:=packet.(*vmess.XUDPConn)
					if !ok{_ = raw.Close();_ = packet.Close();return nil,fmt.Errorf("unexpected actual XUDP type %T",packet)}
					var releaseCache func()
					if protocol!="vless"{
						cache,ok:=xudp.Conn.(interface{ReleasePacketSessionReadCacheAfterJoin()})
						if !ok{_ = raw.Close();_ = packet.Close();return nil,fmt.Errorf("actual VMess read-cache finalizer missing")}
						releaseCache=cache.ReleasePacketSessionReadCacheAfterJoin
					}
					xudp.SetPacketSessionEndReporting()
					lease:=adapter.NewPacketSessionLease(xudp,raw.Close,func(){
						if !constructorJoined.Load()||counted.closes.Load()!=1{finalizerOrderFailure.Store(true)}
						if releaseCache!=nil{releaseCache()}
						finalized.Add(1)
					})
					leaseReady<-lease
					select{case <-holdReturn.done:case <-f.ctx.Done():}
					return lease,nil
				}}
				f.startOwner(provider)
				f.send([]byte("still held by original owner pending slot"))
				lease:=cancelAwait(t,leaseReady,"actual successful constructor before held lease publication")
				if !lease.IsValid()||!constructorJoined.Load()||!counted.writeReturned.Load(){t.Fatal("actual constructor did not finish before publication barrier")}
				cancelAssertPending(t,f.handler.callbacks,"held provider")
				if ending=="control_close"{_ = f.control.Close()}
				closing:=f.event("closing")
				if ending=="owner_deadline"&&closing.Cause!="admission_timeout"{t.Fatalf("wrong cancellation winner: %+v",closing)}
				if f.slots.Load()!=1||finalized.Load()!=0||counted.closes.Load()!=1{t.Fatal("constructor/provider join boundary was bypassed")}
				cancelAssertPending(t,f.ownerDone,"owner awaiting late provider")
				_ = holdReturn.Close()
				f.joinedNoCopies()
				_ = cancelAwait(t,serverDone,"actual outer-only protocol Service join")
				if finalized.Load()!=1||counted.closes.Load()!=1||finalizerOrderFailure.Load(){t.Fatalf("late real lease cleanup: finalizers=%d raw-closes=%d order-failure=%v",finalized.Load(),counted.closes.Load(),finalizerOrderFailure.Load())}
			}) { return }
		}
	}
}
