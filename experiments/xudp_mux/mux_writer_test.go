package vmess

import (
    "bytes"
    "context"
    "encoding/binary"
    "errors"
    "io"
    "net"
    "runtime"
    "sync"
    "sync/atomic"
    "testing"
    "time"

    "github.com/sagernet/sing/common/buf"
    M "github.com/sagernet/sing/common/metadata"
    N "github.com/sagernet/sing/common/network"
)
const bound=2*time.Second
var target=M.ParseSocksaddr("127.0.0.3:12345")
type muxTestHandler struct { tcp chan net.Conn; udp chan N.PacketConn }
func (h *muxTestHandler) NewConnectionEx(_ context.Context,c net.Conn,_ M.Socksaddr,_ M.Socksaddr,_ N.CloseHandlerFunc) {h.tcp<-c}
func (h *muxTestHandler) NewPacketConnectionEx(_ context.Context,c N.PacketConn,_ M.Socksaddr,_ M.Socksaddr,_ N.CloseHandlerFunc) {h.udp<-c}
type muxObserved struct {net.Conn; active atomic.Int32; starts atomic.Int32; max atomic.Int32; fail atomic.Bool}
func (c *muxObserved) Write(p []byte)(int,error){
 n:=c.active.Add(1);defer c.active.Add(-1);c.starts.Add(1)
 for {old:=c.max.Load();if n<=old || c.max.CompareAndSwap(old,n){break}}
 if c.fail.Load(){return 0,io.ErrUnexpectedEOF};return c.Conn.Write(p)
}
type muxHarness struct {t *testing.T;client net.Conn;server *muxObserved;cancel context.CancelFunc;done chan struct{};handler *muxTestHandler;session *serverSession}
func newHarness(t *testing.T)*muxHarness{
 t.Helper();a,b:=net.Pipe();ctx,cancel:=context.WithCancel(context.Background());h:=&muxHarness{t:t,client:a,server:&muxObserved{Conn:b},cancel:cancel,done:make(chan struct{}),handler:&muxTestHandler{tcp:make(chan net.Conn,128),udp:make(chan N.PacketConn,128)}}
 go func(){defer close(h.done);_=HandleMuxConnection(ctx,h.server,M.Socksaddr{},h.handler)}()
 t.Cleanup(func(){cancel();_=a.Close();_=b.Close();select{case <-h.done:case <-time.After(bound):t.Error("parent workers failed to join")};if h.server.active.Load()!=0{t.Error("transport writer remained after parent close")}})
 return h
}
func (h *muxHarness) send(id uint16,status,network byte){
 h.t.Helper();var body bytes.Buffer;_=binary.Write(&body,binary.BigEndian,id);body.Write([]byte{status,0});if status==StatusNew{body.WriteByte(network);if err:=AddressSerializer.WriteAddrPort(&body,target);err!=nil{h.t.Fatal(err)}}
 var frame bytes.Buffer;_=binary.Write(&frame,binary.BigEndian,uint16(body.Len()));frame.Write(body.Bytes());_=h.client.SetWriteDeadline(time.Now().Add(bound));_,err:=h.client.Write(frame.Bytes());_=h.client.SetWriteDeadline(time.Time{});if err!=nil{h.t.Fatal(err)}
}
func (h *muxHarness) udp(id uint16)*serverMuxPacketConn{h.send(id,StatusNew,NetworkUDP);select{case c:=<-h.handler.udp:v:=c.(*serverMuxPacketConn);h.session=v.session;return v;case <-time.After(bound):h.t.Fatal("UDP dispatch missing");return nil}}
func (h *muxHarness) tcp(id uint16)*serverMuxConn{h.send(id,StatusNew,NetworkTCP);select{case c:=<-h.handler.tcp:v:=c.(*serverMuxConn);h.session=v.session;return v;case <-time.After(bound):h.t.Fatal("TCP dispatch missing");return nil}}
type muxFrame struct{id uint16;status byte;option byte;address M.Socksaddr;data []byte}
func (h *muxHarness) read()muxFrame{
 h.t.Helper();_=h.client.SetReadDeadline(time.Now().Add(bound));defer h.client.SetReadDeadline(time.Time{});var length uint16;if err:=binary.Read(h.client,binary.BigEndian,&length);err!=nil{h.t.Fatal(err)};if length<4||length>272{h.t.Fatalf("bad frame length %d",length)}
 b:=make([]byte,length);if _,err:=io.ReadFull(h.client,b);err!=nil{h.t.Fatal(err)};f:=muxFrame{id:binary.BigEndian.Uint16(b),status:b[2],option:b[3]};if length>4{if b[4]!=NetworkUDP{h.t.Fatal("unexpected network")};r:=bytes.NewReader(b[5:]);var err error;f.address,err=AddressSerializer.ReadAddrPort(r);if err!=nil||r.Len()!=0{h.t.Fatal("bad address frame")}}
 if f.option&OptionData!=0{if err:=binary.Read(h.client,binary.BigEndian,&length);err!=nil{h.t.Fatal(err)};f.data=make([]byte,length);if _,err:=io.ReadFull(h.client,f.data);err!=nil{h.t.Fatal(err)}};return f
}
func await(t *testing.T,f func()bool,label string){t.Helper();deadline:=time.Now().Add(bound);for !f(){if time.Now().After(deadline){t.Fatal("bounded wait: "+label)};time.Sleep(time.Millisecond)}}
func result(t *testing.T,ch <-chan error,wantClosed bool){t.Helper();select{case err:=<-ch:if wantClosed&&!errors.Is(err,net.ErrClosed){t.Fatalf("want closed, got %v",err)};if !wantClosed&&err!=nil{t.Fatal(err)};case <-time.After(bound):t.Fatal("write caller failed to exit")}}
func (h *muxHarness) stats()(streams,frames,size int){h.session.streamAccess.Lock();defer h.session.streamAccess.Unlock();streams=len(h.session.streams);if h.session.reject!=nil{frames++;size+=len(h.session.reject.frame)};for _,s:=range h.session.streams{if s.pending!=nil{frames++;size+=len(s.pending.frame)};if s.endQueued{frames++;size+=6}};return}
func (h *muxHarness) assertBound(){h.t.Helper();s,f,b:=h.stats();if s>64||f>129||b>64*(muxMaxFrame+6)+6{h.t.Fatalf("unbounded s=%d f=%d bytes=%d",s,f,b)};if h.server.max.Load()>1{h.t.Fatal("concurrent parent transport writes")}}
func writeUDP(c *serverMuxPacketConn,p []byte)<-chan error{ch:=make(chan error,1);go func(){_,err:=c.WriteTo(p,target);ch<-err}();return ch}

func TestMuxBoundedInFlightCancelAndSibling(t *testing.T){
 h:=newHarness(t);a:=h.udp(1);b:=h.udp(2);data:=[]byte("owned-before-cancel");done:=writeUDP(a,data);await(t,func()bool{return h.server.active.Load()==1},"inflight writer")
 if err:=a.Close();err!=nil{t.Fatal(err)};result(t,done,true);copy(data,[]byte("MUTATED-CALLER-BYTES"));s,f,_:=h.stats();if s!=2||f!=2{t.Fatalf("missing bounded tombstone/inflight: %d/%d",s,f)};h.assertBound()
 sibling:=writeUDP(b,[]byte("sibling"));first:=h.read();if first.id!=1||first.address!=target||string(first.data)!="owned-before-cancel"{t.Fatalf("inflight corruption %+v",first)};end:=h.read();if end.id!=1||end.status!=StatusEnd{t.Fatal("missing ordered End")};next:=h.read();if next.id!=2||string(next.data)!="sibling"{t.Fatal("sibling corruption")};result(t,sibling,false)
 await(t,func()bool{s,_,_:=h.stats();return s==1},"tombstone release");if h.session.ctx.Err()!=nil{t.Fatal("association cancel closed parent")}
}
func TestMuxBoundedQueuedCancel(t *testing.T){
 h:=newHarness(t);a:=h.udp(1);b:=h.udp(2);d1:=writeUDP(a,[]byte("first"));await(t,func()bool{return h.server.active.Load()==1},"first inflight");d2:=writeUDP(b,[]byte("must-not-be-written"));await(t,func()bool{_,f,_:=h.stats();return f==2},"queued second")
 _=b.Close();result(t,d2,true);f:=h.read();if f.id!=1||string(f.data)!="first"{t.Fatal("first changed")};result(t,d1,false);f=h.read();if f.id!=2||f.status!=StatusEnd{t.Fatal("queued data not withdrawn")};h.assertBound()
}
func TestMuxBoundedPartialFrameCancel(t *testing.T){for _,prefix:=range []int{1,4,14}{t.Run(string(rune('a'+prefix)),func(t *testing.T){
 h:=newHarness(t);c:=h.udp(8);payload:=bytes.Repeat([]byte("x"),128);done:=writeUDP(c,payload);head:=make([]byte,prefix);_=h.client.SetReadDeadline(time.Now().Add(bound));if _,err:=io.ReadFull(h.client,head);err!=nil{t.Fatal(err)};_=h.client.SetReadDeadline(time.Time{});_=c.Close();result(t,done,true)
 // Feed consumed prefix back into the parser without changing the actual writer.
 h.client=&prefixConn{Conn:h.client,reader:io.MultiReader(bytes.NewReader(head),h.client)}
 f:=h.read();if f.id!=8||!bytes.Equal(f.data,payload){t.Fatal("partial frame was truncated")};f=h.read();if f.id!=8||f.status!=StatusEnd{t.Fatal("End did not follow completed partial frame")};h.assertBound()
})}}
type prefixConn struct{net.Conn;reader io.Reader};func(c *prefixConn)Read(p []byte)(int,error){return c.reader.Read(p)}
func TestMuxBoundedLargeTCP(t *testing.T){
 h:=newHarness(t);c:=h.tcp(9);payload:=bytes.Repeat([]byte{0xa5},(128<<10)+13);done:=make(chan error,1);go func(){n,err:=c.Write(payload);if err==nil&&n!=len(payload){err=io.ErrShortWrite};done<-err}();var got []byte
 for len(got)<len(payload){f:=h.read();if f.id!=9||f.status!=StatusKeep||f.address.IsValid()||len(f.data)>65535{t.Fatal("invalid split TCP frame")};got=append(got,f.data...)};result(t,done,false);if !bytes.Equal(got,payload){t.Fatal("large TCP data mismatch")};h.assertBound()
}
func TestMuxBoundedLargeTCPCancelPartialCount(t *testing.T){
 h:=newHarness(t);c:=h.tcp(9);payload:=bytes.Repeat([]byte{7},(128<<10)+13);type answer struct{n int;err error};done:=make(chan answer,1);go func(){n,err:=c.Write(payload);done<-answer{n,err}}();f:=h.read();if len(f.data)!=65535{t.Fatal("first split wrong")};await(t,func()bool{return h.server.starts.Load()>=2},"second split started");_=c.Close();select{case a:=<-done:if a.n!=65535||!errors.Is(a.err,net.ErrClosed){t.Fatalf("partial result %+v",a)};case <-time.After(bound):t.Fatal("partial write stuck")};f=h.read();if len(f.data)!=65535{t.Fatal("inflight second split truncated")};if h.read().status!=StatusEnd{t.Fatal("missing End")}
}
func TestMuxBoundedEveryOutputEntrypoint(t *testing.T){
 h:=newHarness(t);tcp:=h.tcp(1);udp:=h.udp(2);var wg sync.WaitGroup;errs:=make(chan error,6);payloads:=[]string{"tcp-write","tcp-buffer","udp-write","udp-to","udp-packet"}
 calls:=[]func()error{
 func()error{_,e:=tcp.Write([]byte(payloads[0]));return e},
 func()error{b:=buf.NewSize(64);_,_=b.WriteString(payloads[1]);return tcp.WriteBuffer(b)},
 func()error{_,e:=udp.Write([]byte(payloads[2]));return e},
 func()error{_,e:=udp.WriteTo([]byte(payloads[3]),target);return e},
 func()error{b:=buf.NewSize(64);_,_=b.WriteString(payloads[4]);return udp.WritePacket(b,target)},
 };for _,call:=range calls{wg.Add(1);go func(call func()error){defer wg.Done();errs<-call()}(call)}
 seen:=map[string]bool{};for range calls{f:=h.read();if f.status!=StatusKeep{t.Fatal("bad mixed frame")};if f.id==2&&f.address!=target{t.Fatal("packet tuple changed")};if seen[string(f.data)]{t.Fatal("duplicate mixed data")};seen[string(f.data)]=true};wg.Wait();for range calls{result(t,errs,false)};for _,p:=range payloads{if !seen[p]{t.Fatal("lost output entrypoint")}};h.assertBound()
}
func TestMuxBoundedCloseRaceAndTombstoneReuse(t *testing.T){
 h:=newHarness(t);c:=h.udp(7);done:=writeUDP(c,[]byte("old"));await(t,func()bool{return h.server.active.Load()==1},"old inflight");var wg sync.WaitGroup;for i:=0;i<32;i++{wg.Add(1);go func(){defer wg.Done();_=c.Close()}()};wg.Wait();result(t,done,true)
 h.send(7,StatusNew,NetworkUDP);select{case <-h.handler.udp:t.Fatal("same ID reused before End");case <-time.After(20*time.Millisecond):}
 if string(h.read().data)!="old"||h.read().status!=StatusEnd{t.Fatal("old frame/End changed")};var fresh *serverMuxPacketConn;select{case v:=<-h.handler.udp:fresh=v.(*serverMuxPacketConn);case <-time.After(bound):t.Fatal("tombstone waiter did not resume")}
 _=c.Close();newDone:=writeUDP(fresh,[]byte("new"));f:=h.read();if f.status!=StatusKeep||string(f.data)!="new"{t.Fatal("stale handle closed new generation")};result(t,newDone,false);h.assertBound()
}
func TestMuxBoundedUnknownKeepFlood(t *testing.T){
 h:=newHarness(t);live:=h.udp(500);base:=runtime.NumGoroutine();h.send(1,StatusKeep,0)
 await(t,func()bool{return h.server.active.Load()==1},"unknown reject blocked")
 s,f,b:=h.stats();if s!=1||f!=1||b!=6{t.Fatalf("unknown not in fixed slot: %d/%d/%d",s,f,b)};h.assertBound()
 if runtime.NumGoroutine()>base+2{t.Fatal("unknown Keep spawned workers")}
 first:=h.read();if first.id!=1||first.status!=StatusEnd||first.option!=OptionError{t.Fatal("missing rejection")}
 for i:=0;i<128;i++{h.send(uint16(i),StatusKeep,0);f:=h.read();if f.id!=uint16(i)||f.status!=StatusEnd||f.option!=OptionError{t.Fatal("unknown Keep silently dropped or malformed")};h.assertBound()}
 done:=writeUDP(live,[]byte("survives-flood"));if string(h.read().data)!="survives-flood"{t.Fatal("live sibling lost")};result(t,done,false);if h.session.ctx.Err()!=nil{t.Fatal("capacity pressure killed parent")}
}
func TestMuxBoundedCapacityNewAndParentCancel(t *testing.T){
 h:=newHarness(t);for i:=0;i<64;i++{_=h.tcp(uint16(i))};h.send(64,StatusNew,NetworkTCP)
 reject:=h.read();if reject.id!=64||reject.status!=StatusEnd||reject.option!=OptionError{t.Fatal("over-capacity New not explicitly rejected")}
 select{case <-h.handler.tcp:t.Fatal("65th stream admitted");default:}
 // No local Close is used: recv must make progress to the peer's End.
 h.send(0,StatusEnd,0);await(t,func()bool{s,_,_:=h.stats();return s==63},"remote End after excess New")
 fresh:=h.tcp(65);done:=make(chan error,1);go func(){_,err:=fresh.Write([]byte("recovered"));done<-err}();if string(h.read().data)!="recovered"{t.Fatal("capacity recovery corrupt")};result(t,done,false)
 h.send(66,StatusNew,NetworkTCP);await(t,func()bool{return h.server.active.Load()==1},"rejection write blocked")
 h.cancel();select{case <-h.done:case <-time.After(bound):t.Fatal("parent cancel did not join reject waiter")};s,f,b:=h.stats();if s!=0||f!=0||b!=0{t.Fatal("parent retained state")}
}
func TestMuxBoundedParentErrorReleasesWaiters(t *testing.T){
 h:=newHarness(t);a:=h.udp(1);b:=h.udp(2);d1:=writeUDP(a,[]byte("first"));await(t,func()bool{return h.server.active.Load()==1},"inflight");d2:=writeUDP(b,[]byte("second"));await(t,func()bool{_,f,_:=h.stats();return f==2},"queued");_=h.client.Close();for _,ch:=range []<-chan error{d1,d2}{select{case err:=<-ch:if err==nil{t.Fatal("parent error reported success")};case <-time.After(bound):t.Fatal("parent error waiter stuck")}};select{case <-h.done:case <-time.After(bound):t.Fatal("parent error did not join")};s,f,size:=h.stats();if s!=0||f!=0||size!=0{t.Fatal("parent error leaked owned state")}
}
func TestMuxBoundedRejectOversizedUDP(t *testing.T){h:=newHarness(t);c:=h.udp(1);n,err:=c.WriteTo(make([]byte,65536),target);if n!=0||!errors.Is(err,io.ErrShortBuffer){t.Fatal("UDP length wrapped")};_,f,b:=h.stats();if f!=0||b!=0{t.Fatal("oversized datagram allocated queue")}}

func TestMuxBoundedLargeConcurrentTCPCallsStayContiguous(t *testing.T){
 h:=newHarness(t);c:=h.tcp(3);a:=bytes.Repeat([]byte("a"),(128<<10)+13);b:=bytes.Repeat([]byte("b"),(128<<10)+17);done:=make(chan error,2)
 go func(){_,e:=c.Write(a);done<-e}();go func(){buffer:=buf.NewSize(len(b));_,_=buffer.Write(b);done<-c.WriteBuffer(buffer)}()
 var all []byte;for len(all)<len(a)+len(b){all=append(all,h.read().data...)};result(t,done,false);result(t,done,false)
 ab:=append(append([]byte{},a...),b...);ba:=append(append([]byte{},b...),a...);if !bytes.Equal(all,ab)&&!bytes.Equal(all,ba){t.Fatal("concurrent public TCP writes interleaved")}
}
func TestMuxBoundedCancelLinearization(t *testing.T){
 h:=newHarness(t);c:=h.udp(4);done:=writeUDP(c,[]byte("cancel-first"));await(t,func()bool{return h.server.active.Load()==1},"inflight")
 h.session.streamAccess.Lock();op:=c.stream.pending;h.session.streamAccess.Unlock();_=c.Close();result(t,done,true);_=h.read();_=h.read()
 select{case <-op.done:if !errors.Is(op.err,net.ErrClosed){t.Fatal("wire completion overwrote cancel result")};case <-time.After(bound):t.Fatal("completion missing")}
 fresh:=h.udp(5);success:=writeUDP(fresh,[]byte("complete-first"));_=h.read();result(t,success,false);_=fresh.Close();if h.read().status!=StatusEnd{t.Fatal("End missing")}
}
func TestMuxBoundedRejectedPayloadConsumedBeforeWaiting(t *testing.T){
 h:=newHarness(t);_=h.udp(500)
 var frame bytes.Buffer;_=binary.Write(&frame,binary.BigEndian,uint16(4));_=binary.Write(&frame,binary.BigEndian,uint16(123));frame.Write([]byte{StatusKeep,OptionData});_=binary.Write(&frame,binary.BigEndian,uint16(32768));frame.Write(bytes.Repeat([]byte("x"),32768))
 _=h.client.SetWriteDeadline(time.Now().Add(bound));if _,err:=h.client.Write(frame.Bytes());err!=nil{t.Fatal("server waited for rejection read before consuming request payload",err)};_=h.client.SetWriteDeadline(time.Time{})
 reject:=h.read();if reject.id!=123||reject.status!=StatusEnd||reject.option!=OptionError{t.Fatal("rejected payload response wrong")};h.assertBound()
}

func TestMuxBoundedSameIDNewPayloadBeforeOldEnd(t *testing.T){
 h:=newHarness(t);old:=h.udp(6);_=old.Close();await(t,func()bool{return h.server.active.Load()==1},"old End blocked")
 var header,frame bytes.Buffer;_=binary.Write(&header,binary.BigEndian,uint16(6));header.Write([]byte{StatusNew,OptionData,NetworkUDP});if err:=AddressSerializer.WriteAddrPort(&header,target);err!=nil{t.Fatal(err)};_=binary.Write(&frame,binary.BigEndian,uint16(header.Len()));frame.Write(header.Bytes());_=binary.Write(&frame,binary.BigEndian,uint16(3));frame.WriteString("new")
 _=h.client.SetWriteDeadline(time.Now().Add(bound));if _,err:=h.client.Write(frame.Bytes());err!=nil{t.Fatal("tombstone blocked before consuming payload",err)};_=h.client.SetWriteDeadline(time.Time{})
 if h.read().status!=StatusEnd{t.Fatal("old End missing")};var fresh *serverMuxPacketConn;select{case c:=<-h.handler.udp:fresh=c.(*serverMuxPacketConn);case <-time.After(bound):t.Fatal("new generation not dispatched")}
 read:=make(chan error,1);go func(){p:=make([]byte,32);n,addr,err:=fresh.ReadFrom(p);if err==nil&&(string(p[:n])!="new"||addr.String()!=target.String()){err=io.ErrUnexpectedEOF};read<-err}();result(t,read,false)
 done:=writeUDP(fresh,[]byte("new-reply"));if string(h.read().data)!="new-reply"{t.Fatal("new generation corrupted")};result(t,done,false);h.assertBound()
}

func TestMuxBoundedCanceledBufferOwnership(t *testing.T){
 h:=newHarness(t);c:=h.udp(1);buffer:=buf.NewSize(64);_,_=buffer.WriteString("packet-owned-copy");callerBytes:=buffer.Bytes();done:=make(chan error,1);go func(){done<-c.WritePacket(buffer,target)}();await(t,func()bool{return h.server.active.Load()==1},"packet buffer inflight");_=c.Close();result(t,done,true);buffer.Leak();for i:=range callerBytes{callerBytes[i]='z'}
 f:=h.read();if string(f.data)!="packet-owned-copy"{t.Fatal("writer retained released caller buffer")};if h.read().status!=StatusEnd{t.Fatal("missing End")}
}
