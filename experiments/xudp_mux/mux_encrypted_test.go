package vmess

// Real fixed Client -> Service encryption, without the device SOCKS packet cap.
// This normal-flow test does not qualify encrypted backpressure cancellation.
import (
 "bytes"
 "context"
 "crypto/sha256"
 "encoding/binary"
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

type encryptedHandler struct { tcp chan net.Conn; udp chan N.PacketConn; dispatched chan struct{} }
func (h *encryptedHandler) NewConnectionEx(_ context.Context,c net.Conn,_ M.Socksaddr,_ M.Socksaddr,_ N.CloseHandlerFunc) {h.tcp<-c;h.dispatched<-struct{}{}}
func (h *encryptedHandler) NewPacketConnectionEx(_ context.Context,c N.PacketConn,_ M.Socksaddr,_ M.Socksaddr,_ N.CloseHandlerFunc) {h.udp<-c;h.dispatched<-struct{}{}}
type encryptedWire struct {net.Conn;writes atomic.Int32}
func(c *encryptedWire)Write(p []byte)(int,error){c.writes.Add(1);return c.Conn.Write(p)}
type encryptedFrame struct {id uint16;status byte;address M.Socksaddr;data []byte}

func encryptedRequest(id uint16,status,network byte,destination M.Socksaddr,data []byte) []byte {
 var header,frame bytes.Buffer
 _=binary.Write(&header,binary.BigEndian,id)
 option:=byte(0);if data!=nil {option=OptionData}
 header.Write([]byte{status,option})
 if status==StatusNew || network==NetworkUDP {header.WriteByte(network);_ = AddressSerializer.WriteAddrPort(&header,destination)}
 _=binary.Write(&frame,binary.BigEndian,uint16(header.Len()));frame.Write(header.Bytes())
 if data!=nil {_=binary.Write(&frame,binary.BigEndian,uint16(len(data)));frame.Write(data)}
 return frame.Bytes()
}
func encryptedRead(t *testing.T,r io.Reader) encryptedFrame {
 t.Helper();var size uint16
 if err:=binary.Read(r,binary.BigEndian,&size);err!=nil {t.Fatal(err)}
 if size<4 || size>272 {t.Fatalf("bad encrypted mux header %d",size)}
 h:=make([]byte,int(size));if _,err:=io.ReadFull(r,h);err!=nil {t.Fatal(err)}
 f:=encryptedFrame{id:binary.BigEndian.Uint16(h),status:h[2]}
 if size>4 {if h[4]!=NetworkUDP {t.Fatal("wrong encrypted reply network")};reader:=bytes.NewReader(h[5:]);var err error;f.address,err=AddressSerializer.ReadAddrPort(reader);if err!=nil || reader.Len()!=0 {t.Fatal("wrong encrypted address header")}}
 if h[3]&OptionError!=0 {t.Fatal("unexpected encrypted error-End")}
 if h[3]&OptionData!=0 {if err:=binary.Read(r,binary.BigEndian,&size);err!=nil {t.Fatal(err)};f.data=make([]byte,int(size));if _,err:=io.ReadFull(r,f.data);err!=nil {t.Fatal(err)}}
 return f
}
func encryptedCompatibility(t *testing.T,security string) {
 t.Helper()
 const limit=10*time.Second
 destination:=M.ParseSocksaddr("127.0.0.3:12345")
 ctx,cancel:=context.WithTimeout(context.Background(),limit)
 left,right:=net.Pipe();clientWire,serverWire:=&encryptedWire{Conn:left},&encryptedWire{Conn:right}
 _=left.SetDeadline(time.Now().Add(limit));_=right.SetDeadline(time.Now().Add(limit))
 h:=&encryptedHandler{tcp:make(chan net.Conn,1),udp:make(chan N.PacketConn,1),dispatched:make(chan struct{},2)}
 service:=NewService[string](h)
 const user="11111111-2222-4333-8444-555555555555"
 if err:=service.UpdateUsers([]string{"fixture"},[]string{user},[]int{0});err!=nil {t.Fatal(err)}
 serviceDone:=make(chan struct{});var workers sync.WaitGroup
 go func(){defer close(serviceDone);_=service.NewConnection(ctx,serverWire,M.ParseSocksaddr("127.0.0.1:24443"),nil)}()
 t.Cleanup(func(){
  cancel();_=left.Close();_=right.Close();_=service.Close()
  select{case <-serviceDone:case <-time.After(limit):t.Error("encrypted Service did not join")}
  joined:=make(chan struct{});go func(){workers.Wait();close(joined)}()
  select{case <-joined:case <-time.After(limit):t.Error("encrypted callers did not join")}
 })
 async:=func(call func()error)<-chan error {done:=make(chan error,1);workers.Add(1);go func(){defer workers.Done();done<-call()}();return done}
 wait:=func(done <-chan error){t.Helper();select{case err:=<-done:if err!=nil {t.Fatal(err)};case <-ctx.Done():t.Fatal("encrypted operation exceeded bound")}}
 client,err:=NewClient(user,security,0,ClientWithGlobalPadding(),ClientWithAuthenticatedLength());if err!=nil {t.Fatal(err)}
 conn:=&clientConn{client.dialRaw(clientWire,CommandMux,MuxDestination)}
 required:=byte(RequestOptionChunkStream|RequestOptionChunkMasking|RequestOptionGlobalPadding|RequestOptionAuthenticatedLength)
 if conn.command!=CommandMux || conn.option!=required || conn.security==SecurityTypeNone {t.Fatal("encrypted CommandMux configuration missing")}
 send:=func(frame []byte)<-chan error{return async(func()error{n,err:=conn.Write(frame);if err==nil && n!=len(frame){err=io.ErrShortWrite};return err})}
 wait(send(encryptedRequest(1,StatusNew,NetworkUDP,destination,nil)))
 var udp N.PacketConn
 select{case udp=<-h.udp:case <-ctx.Done():t.Fatal("encrypted UDP dispatch missing")}
 wait(send(encryptedRequest(2,StatusNew,NetworkTCP,destination,nil)))
 var tcp net.Conn
 select{case tcp=<-h.tcp:case <-ctx.Done():t.Fatal("encrypted TCP dispatch missing")}
 for i:=0;i<2;i++ {select{case <-h.dispatched:case <-ctx.Done():t.Fatal("encrypted handler callback did not join")}}
 tcpBody:=make([]byte,196742)
 for position:=0;position<len(tcpBody);position+=sha256.Size {var counter [8]byte;binary.BigEndian.PutUint64(counter[:],uint64(position));block:=sha256.Sum256(counter[:]);copy(tcpBody[position:],block[:])}
 offset:=0
 checkChunks:=func(direction string,size int,before int32,wire *encryptedWire){t.Helper();delta:=wire.writes.Load()-before;if size>WriteChunkSize && delta<=1 {t.Fatalf("large %s frame did not cross encrypted writes: size=%d delta=%d",direction,size,delta)};t.Logf("encrypted_chunk_observation cipher=%s direction=%s payload=%d wire_write_delta=%d",security,direction,size,delta)}
 for index,size:=range []int{32768,60000,65535,1} {
  body:=bytes.Repeat([]byte{byte(index+1)},size)
  before:=clientWire.writes.Load()
  sent:=send(encryptedRequest(1,StatusKeep,NetworkUDP,destination,body))
  packet:=buf.NewSize(65535);actual,err:=udp.ReadPacket(packet)
  if err!=nil || actual!=destination || !bytes.Equal(packet.Bytes(),body) {packet.Release();t.Fatalf("encrypted UDP request changed size=%d addr=%v err=%v",size,actual,err)}
  packet.Release();wait(sent);checkChunks("udp-upload",size,before,clientWire)
  before=serverWire.writes.Load()
  reply:=async(func()error{n,err:=udp.(net.PacketConn).WriteTo(body,destination);if err==nil&&n!=len(body){err=io.ErrShortWrite};return err})
  f:=encryptedRead(t,conn);if f.id!=1 || f.status!=StatusKeep || f.address!=destination || !bytes.Equal(f.data,body) {t.Fatalf("encrypted UDP reply changed size=%d",size)};wait(reply);checkChunks("udp-reply",size,before,serverWire)
  count:=len(tcpBody)-offset;if count>65535 {count=65535};part:=tcpBody[offset:offset+count]
  before=clientWire.writes.Load()
  sent=send(encryptedRequest(2,StatusKeep,NetworkTCP,destination,part))
  got:=make([]byte,count);if _,err:=io.ReadFull(tcp,got);err!=nil || !bytes.Equal(got,part) {t.Fatal("encrypted TCP request changed")};wait(sent);checkChunks("tcp-upload",count,before,clientWire)
  before=serverWire.writes.Load()
  reply=async(func()error{n,err:=tcp.Write(part);if err==nil&&n!=len(part){err=io.ErrShortWrite};return err})
  f=encryptedRead(t,conn);if f.id!=2 || f.status!=StatusKeep || f.address.IsValid() || !bytes.Equal(f.data,part) {t.Fatal("encrypted TCP response frame changed")};wait(reply);checkChunks("tcp-reply",count,before,serverWire);offset+=count
 }
 if offset!=196742 {t.Fatal("incomplete encrypted TCP payload")}
 closeUDP:=async(udp.Close);f:=encryptedRead(t,conn);if f.id!=1 || f.status!=StatusEnd || len(f.data)!=0 {t.Fatal("missing ordered UDP End")};wait(closeUDP)
 closeTCP:=async(tcp.Close);f=encryptedRead(t,conn);if f.id!=2 || f.status!=StatusEnd || len(f.data)!=0 {t.Fatal("missing ordered TCP End or duplicate UDP End")};wait(closeTCP)
 if ctx.Err()!=nil {t.Fatal("association close ended encrypted parent")}
 select{case <-serviceDone:t.Fatal("Service ended before parent close");default:}
 _=left.SetReadDeadline(time.Now().Add(100*time.Millisecond));var extra [1]byte;n,readErr:=conn.Read(extra[:]);_=left.SetReadDeadline(time.Now().Add(limit))
 timeout,ok:=readErr.(net.Error);if n!=0 || !ok || !timeout.Timeout() {t.Fatalf("unexpected extra encrypted frame: n=%d err=%v",n,readErr)}
 if clientWire.writes.Load()<8 || serverWire.writes.Load()<8 {t.Fatal("large payloads did not traverse multiple encrypted chunks")}
 t.Logf("encrypted CommandMux cipher=%s padding=true authenticated_length=true UDP=32768,60000,65535,1 TCP=196742 interleaved=true client_wire_writes=%d server_wire_writes=%d normal_flow_only=true",security,clientWire.writes.Load(),serverWire.writes.Load())
}
func TestMuxEncryptedAES(t *testing.T){encryptedCompatibility(t,"aes-128-gcm")}
func TestMuxEncryptedChaCha(t *testing.T){encryptedCompatibility(t,"chacha20-poly1305")}
