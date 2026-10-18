package vmess

// Isolated mux-arm regressions. No existing/frozen test is replaced. These
// tests require packet delivery through recvTo's actual io.Pipe and address
// reader; a zero-byte Write result alone is never evidence of delivery.
import (
	"bytes"
	"context"
	"encoding/binary"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"runtime"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/sagernet/sing/common/buf"
	M "github.com/sagernet/sing/common/metadata"
	N "github.com/sagernet/sing/common/network"
)

const muxEmptyBound = 3 * time.Second
var muxEmptyA = M.ParseSocksaddr("127.0.0.3:12345")
var muxEmptyB = M.ParseSocksaddr("127.0.0.4:23456")

// A transparent counter. Every physical Write delegates unchanged to net.Pipe.
type muxEmptyWire struct { net.Conn; active, writes atomic.Int32 }
func (w *muxEmptyWire) Write(p []byte) (int, error) {
	w.active.Add(1); defer w.active.Add(-1); w.writes.Add(1)
	return w.Conn.Write(p)
}
type muxEmptyHandler struct {
	tcp chan net.Conn
	udp chan N.PacketConn
	returned chan struct{}
	active, finished atomic.Int32
}
func (h *muxEmptyHandler) finish() { h.active.Add(-1); h.finished.Add(1); h.returned <- struct{}{} }
func (h *muxEmptyHandler) NewConnectionEx(_ context.Context, c net.Conn, _ M.Socksaddr, _ M.Socksaddr, _ N.CloseHandlerFunc) {
	h.active.Add(1); defer h.finish(); h.tcp <- c
}
func (h *muxEmptyHandler) NewPacketConnectionEx(_ context.Context, c N.PacketConn, _ M.Socksaddr, _ M.Socksaddr, _ N.CloseHandlerFunc) {
	h.active.Add(1); defer h.finish(); h.udp <- c
}
type muxEmptyCall struct { done chan struct{}; n int; err error }
type muxEmptyHarness struct {
	t *testing.T
	cipher string
	ctx context.Context
	cancel context.CancelFunc
	left, right net.Conn
	client net.Conn
	wire *muxEmptyWire
	handler *muxEmptyHandler
	service *Service[string]
	serviceDone chan struct{}
	serviceErr error
	session *serverSession
	workers sync.WaitGroup
	active atomic.Int32
	seq, admitted int
	joined bool
}
func muxEmptyReady(c <-chan struct{}) bool { select { case <-c: return true; default: return false } }
func muxEmptyEventually(t *testing.T, f func() bool, label string) {
	t.Helper(); until := time.Now().Add(muxEmptyBound)
	for !f() { if time.Now().After(until) { t.Fatal("bounded observation: "+label) }; runtime.Gosched() }
}
func (h *muxEmptyHarness) start(f func() (int, error)) *muxEmptyCall {
	c := &muxEmptyCall{done: make(chan struct{})}; h.workers.Add(1); h.active.Add(1)
	go func() { defer func() { h.active.Add(-1); h.workers.Done(); close(c.done) }(); c.n, c.err = f() }()
	return c
}
func (h *muxEmptyHarness) wait(c *muxEmptyCall, n int) {
	h.t.Helper()
	select {
	case <-c.done: if c.n != n || c.err != nil { h.t.Fatalf("operation n=%d err=%v; want %d/nil", c.n, c.err, n) }
	case <-time.After(muxEmptyBound): h.t.Fatal("operation did not complete within bound")
	}
}
func (h *muxEmptyHarness) join(c <-chan struct{}, label string) {
	h.t.Helper(); select { case <-c: case <-time.After(muxEmptyBound): h.t.Fatal(label+" did not join") }
}
func (h *muxEmptyHarness) event(name string, fields map[string]any) {
	h.t.Helper(); h.seq++
	fields["event"], fields["case"], fields["cipher"], fields["seq"] = name, h.t.Name(), h.cipher, h.seq
	fields["scope"], fields["product_qualified"] = "isolated-mux-empty-datagram", false
	p, err := json.Marshal(fields); if err != nil { h.t.Fatal(err) }
	h.t.Logf("XUDP_MUX_EMPTY %s", p)
}
func newMuxEmptyHarness(t *testing.T, security string) *muxEmptyHarness {
	t.Helper(); ctx, cancel := context.WithCancel(context.Background()); left, right := net.Pipe()
	h := &muxEmptyHarness{t:t, cipher:security, ctx:ctx, cancel:cancel, left:left, right:right, client:left,
		wire:&muxEmptyWire{Conn:right}, serviceDone:make(chan struct{}),
		handler:&muxEmptyHandler{tcp:make(chan net.Conn,2), udp:make(chan N.PacketConn,2), returned:make(chan struct{},4)}}
	t.Cleanup(func() {
		cancel(); _ = left.Close(); _ = right.Close(); if h.service != nil { _ = h.service.Close() }
		h.join(h.serviceDone,"parent cleanup")
		all := make(chan struct{}); go func() { h.workers.Wait(); close(all) }(); h.join(all,"caller cleanup")
		if !h.joined { t.Error("explicit successful parent join was not established") }
	})
	if security == "raw" {
		go func() { defer close(h.serviceDone); h.serviceErr = HandleMuxConnection(ctx,h.wire,M.Socksaddr{},h.handler) }()
	} else {
		h.service = NewService[string](h.handler)
		const user = "11111111-2222-4333-8444-555555555555"
		if err := h.service.UpdateUsers([]string{"empty-datagram-fixture"},[]string{user},[]int{0}); err != nil { t.Fatal(err) }
		client, err := NewClient(user,security,0,ClientWithGlobalPadding(),ClientWithAuthenticatedLength()); if err != nil { t.Fatal(err) }
		c := &clientConn{client.dialRaw(left,CommandMux,MuxDestination)}
		required := byte(RequestOptionChunkStream|RequestOptionChunkMasking|RequestOptionGlobalPadding|RequestOptionAuthenticatedLength)
		if c.command != CommandMux || c.option != required || c.security == SecurityTypeNone { t.Fatal("encrypted CommandMux options missing") }
		h.client = c
		go func() { defer close(h.serviceDone); h.serviceErr = h.service.NewConnection(ctx,h.wire,M.ParseSocksaddr("127.0.0.1:24443"),nil) }()
	}
	return h
}

func muxEmptyRequest(id uint16, status, network byte, address M.Socksaddr, present bool, data []byte) ([]byte,error) {
	var header, frame bytes.Buffer
	_ = binary.Write(&header,binary.BigEndian,id)
	option := byte(0); if present { option = OptionData }
	header.Write([]byte{status,option})
	if status == StatusNew || network == NetworkUDP {
		header.WriteByte(network); if err := AddressSerializer.WriteAddrPort(&header,address); err != nil { return nil,err }
	}
	_ = binary.Write(&frame,binary.BigEndian,uint16(header.Len())); frame.Write(header.Bytes())
	if present { _ = binary.Write(&frame,binary.BigEndian,uint16(len(data))); frame.Write(data) }
	return frame.Bytes(),nil
}
func (h *muxEmptyHarness) send(id uint16,status,network byte,address M.Socksaddr,present bool,data []byte) (*muxEmptyCall,int) {
	h.t.Helper(); p,err := muxEmptyRequest(id,status,network,address,present,data); if err != nil { h.t.Fatal(err) }
	return h.start(func() (int,error) { return h.client.Write(p) }),len(p)
}
func (h *muxEmptyHarness) sent(id uint16,status,network byte,address M.Socksaddr,present bool,data []byte) {
	h.t.Helper(); c,n := h.send(id,status,network,address,present,data); h.wait(c,n)
}
func (h *muxEmptyHarness) opened(session *serverSession) {
	h.t.Helper(); h.join(h.handler.returned,"handler callback"); h.admitted++
	if h.session != nil { if h.session != session { h.t.Fatal("new stream escaped shared parent") }; return }
	h.session = session
	if h.cipher != "raw" {
		parent,ok := session.conn.(*serverConn); if !ok || parent.Conn != h.wire { h.t.Fatal("not the actual Service encrypted parent") }
		client := h.client.(*clientConn)
		if parent.security != client.security || parent.option != client.option { h.t.Fatal("cipher or request options changed") }
	}
	h.event("transport_ready",map[string]any{"actual_mux":true,"real_client_service":h.cipher!="raw",
		"command_mux":h.cipher!="raw","global_padding":h.cipher!="raw","authenticated_length":h.cipher!="raw"})
}
func (h *muxEmptyHarness) udp() *serverMuxPacketConn {
	h.t.Helper(); select { case c := <-h.handler.udp: u,ok:=c.(*serverMuxPacketConn); if !ok { h.t.Fatal("wrong UDP implementation") }; h.opened(u.session); return u
	case <-time.After(muxEmptyBound): h.t.Fatal("UDP dispatch missing"); return nil }
}
func (h *muxEmptyHarness) tcp() *serverMuxConn {
	h.t.Helper(); select { case c := <-h.handler.tcp: u,ok:=c.(*serverMuxConn); if !ok { h.t.Fatal("wrong TCP implementation") }; h.opened(u.session); return u
	case <-time.After(muxEmptyBound): h.t.Fatal("TCP dispatch missing"); return nil }
}
type muxEmptyFrame struct { id uint16; status,option byte; address M.Socksaddr; data []byte }
func muxEmptyRead(r io.Reader) (muxEmptyFrame,error) {
	var f muxEmptyFrame; var size uint16
	if err:=binary.Read(r,binary.BigEndian,&size); err!=nil { return f,err }
	if size<4 || size>272 { return f,fmt.Errorf("bad mux header size %d",size) }
	header:=make([]byte,int(size)); if _,err:=io.ReadFull(r,header); err!=nil { return f,err }
	f.id,f.status,f.option=binary.BigEndian.Uint16(header),header[2],header[3]
	if size>4 {
		if header[4]!=NetworkUDP { return f,fmt.Errorf("bad mux address network %d",header[4]) }
		r:=bytes.NewReader(header[5:]); var err error; f.address,err=AddressSerializer.ReadAddrPort(r)
		if err!=nil { return f,err }; if r.Len()!=0 { return f,fmt.Errorf("trailing address bytes") }
	}
	if f.option&OptionData!=0 {
		if err:=binary.Read(r,binary.BigEndian,&size); err!=nil { return f,err }
		f.data=make([]byte,int(size)); if _,err:=io.ReadFull(r,f.data); err!=nil { return f,err }
	}
	return f,nil
}
func (h *muxEmptyHarness) read() muxEmptyFrame {
	h.t.Helper(); var f muxEmptyFrame
	c:=h.start(func() (int,error) { var err error; f,err=muxEmptyRead(h.client); return 0,err }); h.wait(c,0); return f
}
func (h *muxEmptyHarness) checkReply(f muxEmptyFrame,id uint16,address M.Socksaddr,data []byte) {
	h.t.Helper()
	if f.id!=id || f.status!=StatusKeep || f.option!=OptionData || f.address!=address || f.data==nil || !bytes.Equal(f.data,data) {
		h.t.Fatalf("wrong data frame id=%d status=%d option=%d tuple=%v bytes=%d; want id=%d tuple=%v bytes=%d",f.id,f.status,f.option,f.address,len(f.data),id,address,len(data))
	}
}
func (h *muxEmptyHarness) packet(c *serverMuxPacketConn, api string, expected M.Socksaddr, payload []byte) *muxEmptyCall {
	return h.start(func() (int,error) {
		var actual M.Socksaddr; var got []byte
		if api=="ReadFrom" {
			p:=make([]byte,64); n,a,err:=c.ReadFrom(p); if err!=nil { return n,err }; if a==nil { return n,fmt.Errorf("missing packet address") }
			actual=M.SocksaddrFromNet(a); got=p[:n]
		} else {
			p:=buf.NewSize(64); defer p.Release(); a,err:=c.ReadPacket(p); if err!=nil { return p.Len(),err }; actual=a; got=p.Bytes()
		}
		if actual!=expected || !bytes.Equal(got,payload) { return len(got),fmt.Errorf("packet tuple/payload mismatch: got %v/%x want %v/%x",actual,got,expected,payload) }
		return len(got),nil
	})
}
func (h *muxEmptyHarness) reply(c *serverMuxPacketConn, id uint16, address M.Socksaddr, p []byte) {
	h.t.Helper(); call:=h.start(func() (int,error) { return c.WriteTo(p,address) }); h.checkReply(h.read(),id,address,p); h.wait(call,len(p))
}
func (h *muxEmptyHarness) short(c *serverMuxPacketConn,id uint16,api string) {
	h.t.Helper(); p:=[]byte("after-empty")
	read:=h.packet(c,api,muxEmptyA,p); sent,n:=h.send(id,StatusKeep,0,M.Socksaddr{},true,p)
	h.wait(read,len(p)); h.wait(sent,n); h.reply(c,id,muxEmptyA,p)
}
func (h *muxEmptyHarness) noPending() bool {
	h.session.streamAccess.Lock(); defer h.session.streamAccess.Unlock()
	if len(h.session.queue)!=0 || h.session.reject!=nil { return false }
	for _,s:=range h.session.streams { if s.pending!=nil || s.endQueued { return false } }
	return h.wire.active.Load()==0
}
func (h *muxEmptyHarness) finish(id uint16, c io.Closer) {
	h.t.Helper()
	for i:=0;i<2;i++ { call:=h.start(func() (int,error) { return 0,c.Close() }); h.wait(call,0) }
	f:=h.read()
	if f.id!=id || f.status!=StatusEnd || f.option!=0 || f.address.IsValid() || f.data!=nil { h.t.Fatalf("wrong/extra frame before End: %+v",f) }
	muxEmptyEventually(h.t,func()bool {
		if !h.noPending() { return false }; h.session.streamAccess.Lock(); defer h.session.streamAccess.Unlock(); return len(h.session.streams)==0
	},"End completion and no retained stream/queue")
	if h.ctx.Err()!=nil || muxEmptyReady(h.serviceDone) { h.t.Fatal("association close ended parent") }
	h.event("ends_drained",map[string]any{"end_count":1,"close_calls":2,"each_end_once":true,"parent_alive":true,"streams":0,"queued":0,"active_transport_writes":0})
	h.event("parent_close",map[string]any{"real_peer_close":true,"outer_context_alive":h.ctx.Err()==nil})
	_ = h.left.Close(); h.join(h.serviceDone,"Service/HandleMuxConnection"); h.join(h.session.writerDone,"sole mux writer")
	all:=make(chan struct{}); go func() { h.workers.Wait(); close(all) }(); h.join(all,"all test callers")
	if h.serviceErr==nil || strings.Contains(strings.ToLower(h.serviceErr.Error()),"timeout") || strings.Contains(strings.ToLower(h.serviceErr.Error()),"deadline") { h.t.Fatalf("not a real parent error: %v",h.serviceErr) }
	if h.active.Load()!=0 || h.wire.active.Load()!=0 || h.handler.active.Load()!=0 || int(h.handler.finished.Load())!=h.admitted || h.ctx.Err()!=nil { h.t.Fatal("retained caller/transport/handler or premature context cancel") }
	// Any extra decrypted frame buffered before parent close must be rejected.
	// This uses the same original decoder; it is not reset/replaced to hide bytes.
	extra:=h.start(func()(int,error) { var p [1]byte; n,err:=h.client.Read(p[:]); if n!=0 || err==nil { return n,fmt.Errorf("unexpected bytes after sole End: n=%d err=%v",n,err) }; return 0,nil }); h.wait(extra,0)
	h.session.streamAccess.Lock(); retainedStreams,retainedQueue:=len(h.session.streams),len(h.session.queue); h.session.streamAccess.Unlock()
	if retainedStreams!=0 || retainedQueue!=0 || !h.noPending() { h.t.Fatal("retained parent state after join") }
	h.joined=true
	h.event("parent_joined",map[string]any{"service_joined":h.cipher!="raw","mux_handler_joined":true,"writer_joined":true,"watcher_joined":true,"all_test_workers_joined":true,
		"handler_callbacks_finished":h.handler.finished.Load(),"handler_callbacks_expected":h.admitted,"active_callers":h.active.Load(),"active_transport_writes":h.wire.active.Load(),
		"retained_streams":retainedStreams,"retained_queue":retainedQueue,"handler_callbacks_active":h.handler.active.Load(),
		"no_extra_decoded_bytes":true,"outer_context_alive":h.ctx.Err()==nil,"service_error":h.serviceErr.Error()})
}

func muxEmptyReceiveCase(t *testing.T, security, api string, newFrame bool) {
	h:=newMuxEmptyHarness(t,security); const id=7
	status,network,address:=byte(StatusKeep),byte(NetworkUDP),muxEmptyB
	if newFrame { status=StatusNew; address=muxEmptyA } else { h.sent(id,StatusNew,NetworkUDP,muxEmptyA,false,nil) }
	// One Keep case also requires the original destination fallback.
	if !newFrame && api=="ReadFrom" { network=0; address=M.Socksaddr{} }
	sent,n:=h.send(id,status,network,address,true,[]byte{})
	c:=h.udp(); expected:=address; if !expected.IsValid() { expected=muxEmptyA }
	read:=h.packet(c,api,expected,[]byte{}); h.wait(read,0); h.wait(sent,n)
	h.reply(c,id,expected,[]byte{}); h.short(c,id,api)
	h.event("case_verified",map[string]any{"read_api":api,"status_new":newFrame,"empty_uploads":1,"empty_replies":1,"short_roundtrips":1,
		"tuple_payload_exact":true,"option_data_observed":true,"pipe_and_address_completed":true,"fallback_destination":!newFrame&&api=="ReadFrom"})
	h.finish(id,c)
}
func muxEmptyNoData(t *testing.T,security string) {
	h:=newMuxEmptyHarness(t,security); const id=8
	h.sent(id,StatusNew,NetworkUDP,muxEmptyA,false,nil); c:=h.udp(); h.short(c,id,"ReadFrom")
	h.sent(id,StatusKeep,NetworkUDP,muxEmptyB,false,nil); h.short(c,id,"ReadPacket")
	h.event("case_verified",map[string]any{"no_data_frames":2,"short_roundtrips":2,"first_received_is_short":true,"tuple_payload_exact":true,"no_spurious_empty":true})
	h.finish(id,c)
}

// Captures XUDP's emitted plaintext only, then forwards it to the unchanged
// original raw/real encrypted Client. No mocked XUDP request encoder is used.
type muxEmptyCapture struct { net.Conn; mu sync.Mutex; frames [][]byte }
func (c *muxEmptyCapture) Write(p []byte) (int,error) {
	c.mu.Lock(); c.frames=append(c.frames,append([]byte(nil),p...)); c.mu.Unlock()
	return c.Conn.Write(p)
}
func muxEmptyXUDP(t *testing.T,security string) {
	h:=newMuxEmptyHarness(t,security); capture:=&muxEmptyCapture{Conn:h.client}; x:=NewXUDPConn(capture,muxEmptyA)
	var c *serverMuxPacketConn
	for i,p:=range [][]byte{nil,{},[]byte("after-empty")} {
		address:=muxEmptyA; if i==1 { address=muxEmptyB }
		sent:=h.start(func()(int,error) { return x.WriteTo(p,address) }); if c==nil { c=h.udp() }
		read:=h.packet(c,"ReadFrom",address,p); h.wait(read,len(p)); h.wait(sent,len(p))
		reply:=h.start(func()(int,error) { return c.WriteTo(p,address) })
		response:=h.start(func()(int,error) {
			b:=make([]byte,64); n,a,err:=x.ReadFrom(b); if err!=nil { return n,err }
			if a==nil || M.SocksaddrFromNet(a)!=address || !bytes.Equal(b[:n],p) { return n,fmt.Errorf("real XUDP reply changed tuple/payload") }; return n,nil
		}); h.wait(response,len(p)); h.wait(reply,len(p))
	}
	capture.mu.Lock(); frames:=append([][]byte(nil),capture.frames...); capture.mu.Unlock()
	if len(frames)!=3 { t.Fatalf("XUDP emitted %d writes, expected 3 complete request frames",len(frames)) }
	for i,p:=range frames {
		r:=bytes.NewReader(p); f,err:=muxEmptyRead(r); status:=byte(StatusKeep); if i==0 { status=StatusNew }
		address:=muxEmptyA; if i==1 { address=muxEmptyB }; payload:=[]byte{}; if i==2 { payload=[]byte("after-empty") }
		if err!=nil || r.Len()!=0 || f.id!=0 || f.status!=status || f.option!=OptionData || f.address!=address || f.data==nil || !bytes.Equal(f.data,payload) { t.Fatalf("actual XUDP request %d was not exact New/Keep + OptionData + payload: %+v err=%v",i,f,err) }
	}
	h.event("case_verified",map[string]any{"real_xudp":true,"request_frames":3,"new_empty_frames":1,"keep_empty_frames":1,"empty_uploads":2,"empty_replies":2,
		"short_roundtrips":1,"nil_and_empty_slices":true,"tuple_payload_exact":true,"option_data_observed":true,"pipe_and_address_completed":true})
	h.finish(0,c) // x.Close would close the parent, so close the association instead.
}
func muxEmptyOutputs(t *testing.T,security string) {
	h:=newMuxEmptyHarness(t,security); const id=9
	h.sent(id,StatusNew,NetworkUDP,muxEmptyA,false,nil); c:=h.udp()
	calls:=[]func()(int,error){
		func()(int,error){return c.Write(nil)},
		func()(int,error){return c.WriteTo([]byte{},muxEmptyB)},
		func()(int,error){return 0,c.WritePacket(buf.NewSize(0),muxEmptyB)},
	}
	for i,call:=range calls { done:=h.start(call); address:=muxEmptyB; if i==0 { address=muxEmptyA }; h.checkReply(h.read(),id,address,[]byte{}); h.wait(done,0) }
	h.short(c,id,"ReadPacket")
	h.event("case_verified",map[string]any{"entrypoints":[]string{"Write","WriteTo","WritePacket"},"empty_replies":3,"short_roundtrips":1,
		"tuple_payload_exact":true,"option_data_observed":true,"exactly_one_frame_per_call":true})
	h.finish(id,c)
}
func muxEmptyTCP(t *testing.T,security string) {
	h:=newMuxEmptyHarness(t,security); const id=10
	h.sent(id,StatusNew,NetworkTCP,muxEmptyA,true,[]byte{}); c:=h.tcp()
	body:=[]byte("tcp-after-empty")
	read:=h.start(func()(int,error) { p:=make([]byte,64); n,err:=c.Read(p); if err==nil && !bytes.Equal(p[:n],body) { err=fmt.Errorf("TCP empty input created a spurious read: n=%d payload=%x",n,p[:n]) }; return n,err })
	h.sent(id,StatusKeep,NetworkTCP,M.Socksaddr{},true,[]byte{})
	sent,n:=h.send(id,StatusKeep,NetworkTCP,M.Socksaddr{},true,body); h.wait(read,len(body)); h.wait(sent,n)
	before:=h.wire.writes.Load()
	for _,p:=range [][]byte{nil,{}} { call:=h.start(func()(int,error){return c.Write(p)}); h.wait(call,0) }
	bufferCall:=h.start(func()(int,error){return 0,c.WriteBuffer(buf.NewSize(0))}); h.wait(bufferCall,0)
	if !h.noPending() || h.wire.writes.Load()!=before { t.Fatal("TCP zero write emitted/queued a frame") }
	reply:=h.start(func()(int,error){return c.Write(body)}); h.checkReply(h.read(),id,M.Socksaddr{},body); h.wait(reply,len(body))
	h.event("case_verified",map[string]any{"empty_input_frames":2,"empty_output_calls":3,"empty_output_frames":0,"spurious_zero_reads":0,
		"short_roundtrips":1,"entrypoints":[]string{"Write(nil)","Write(empty)","WriteBuffer(empty)"},"tuple_payload_exact":true})
	h.finish(id,c)
}
func muxEmptySuite(t *testing.T,security string) {
	t.Run("StatusNewReadFrom",func(t *testing.T){muxEmptyReceiveCase(t,security,"ReadFrom",true)})
	t.Run("StatusNewReadPacket",func(t *testing.T){muxEmptyReceiveCase(t,security,"ReadPacket",true)})
	t.Run("StatusKeepReadFrom",func(t *testing.T){muxEmptyReceiveCase(t,security,"ReadFrom",false)})
	t.Run("StatusKeepReadPacket",func(t *testing.T){muxEmptyReceiveCase(t,security,"ReadPacket",false)})
	t.Run("NoDataNewAndKeep",func(t *testing.T){muxEmptyNoData(t,security)})
	t.Run("XUDPWriteTo",func(t *testing.T){muxEmptyXUDP(t,security)})
	t.Run("UDPOutputEntrypoints",func(t *testing.T){muxEmptyOutputs(t,security)})
	t.Run("TCPEmptyWriteNoop",func(t *testing.T){muxEmptyTCP(t,security)})
}
func TestMuxEmptyDatagramRaw(t *testing.T) { muxEmptySuite(t,"raw") }
func TestMuxEmptyDatagramAES(t *testing.T) { muxEmptySuite(t,"aes-128-gcm") }
func TestMuxEmptyDatagramChaCha(t *testing.T) { muxEmptySuite(t,"chacha20-poly1305") }
