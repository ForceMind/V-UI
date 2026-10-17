package route
import("context";"net";"testing";"time";"os";"runtime";"strings";"bytes"
vmess "github.com/sagernet/sing-vmess"
M "github.com/sagernet/sing/common/metadata"
"github.com/sagernet/sing/common/logger")
func mxRouteRunBackpressure(t *testing.T,reply bool) {
    t.Helper()
    patched:=os.Getenv("VUI_MUX_ARM")=="mux"
    if !routeLifecycleAwait(t,routeLifecycleNoWorkers,"real mux zero baseline"){t.Fatal("worker baseline absent")}
    log:=newRouteLifecycleLog(t)
    outbound:=newRouteLifecycleEndpoint(log,"outbound",false)
    client,rawServer:=net.Pipe()
    server:=&rlMuxObservedConn{Conn:rawServer}
    ctx,cancel:=context.WithCancel(context.Background())
    handler:=&rlMuxHandler{manager:NewConnectionManager(&routeLifecycleLogger{ContextLogger:logger.NOP(),log:log}),
        dialer:&routeLifecycleDialer{outbound:outbound,log:log},log:log,done:make(chan struct{})}
    sessionDone:=make(chan struct{})
    var drainDone chan struct{}
    go func(){defer close(sessionDone);_ = vmess.HandleMuxConnection(ctx,server,M.ParseSocksaddr("127.0.0.2:24443"),handler)}()
    defer func(){
        log.event("cleanup_start",map[string]any{"real_mux":true,"parent_transport_close_only_now":true})
        cancel();_=client.Close();_=server.Close();_=outbound.Close()
        joined:=routeLifecycleAwait(t,func()bool{return routeLifecycleChannelReady(sessionDone)},"real mux session worker join")
        if drainDone!=nil && !routeLifecycleAwait(t,func()bool{return routeLifecycleChannelReady(drainDone)},"real mux drain worker join"){joined=false}
        // An entered flag cannot exclude a launched-but-not-yet-scheduled handler.
        // A failed startup may fail this bounded join too; never claim it joined.
        if !routeLifecycleAwait(t,func()bool{return routeLifecycleChannelReady(handler.done)},"real mux handler worker join"){joined=false}
        routeJoined:=routeLifecycleAwait(t,routeLifecycleNoWorkers,"real mux actual route/callback/canceler/mux cleanup join")
        counts:=routeLifecycleWorkerCounts()
        log.event("cleanup_joined",map[string]any{"route_copy_workers":counts["route_copy_workers"],"route_callback_workers":counts["route_callback_workers"],
            "canceler_workers":counts["canceler_workers"],"mux_cleanup_workers":counts["mux_cleanup_workers"],"test_owned_workers_joined":joined,
            "actual_route_workers_joined":routeJoined,"on_close_calls":handler.callbacks.Load(),"active_transport_writes":server.active.Load(),"full_lifecycle_qualified":false})
    }()
    const session uint16=0
    frame,err:=rlMuxNewUDPFrame(session,M.ParseSocksaddr("127.0.0.3:12345"),[]byte("real-mux-upload"))
    if err!=nil{t.Fatal(err)}
    if err=client.SetWriteDeadline(time.Now().Add(routeLifecycleBound));err!=nil{t.Fatal(err)}
    n,err:=client.Write(frame)
    _=client.SetWriteDeadline(time.Time{})
    if err!=nil || n!=len(frame){t.Fatalf("real mux request write: n=%d err=%v",n,err)}
    if !routeLifecycleAwait(t,func()bool{return routeLifecycleChannelReady(handler.done)},"real mux handler startup"){t.Fatal("handler did not start")}
    select {
    case payload:=<-outbound.writes: if !bytes.Equal(payload,[]byte("real-mux-upload")){t.Fatal("real mux upload payload changed")}
    case <-time.After(routeLifecycleBound):t.Fatal("actual route missed real mux upload")
    }
    if reply {
        outbound.readPackets<-routeLifecyclePacket{data:[]byte("real-mux-target-response")}
        if !routeLifecycleAwait(t,func()bool{return server.replyActive.Load()>0},"real mux reply Write blocked on unread net.Pipe"){t.Fatal("reply backpressure absent")}
    }
    time.Sleep(routeLifecycleWindow)
    if log.phase=="lifecycle" {
        if !outbound.closedState.Load(){t.Error("owned outbound did not close under real mux backpressure")}
        if !reply && !routeLifecycleAwait(t,func()bool{return server.endActive.Load()>0},"real mux StatusEnd blocked"){t.Error("StatusEnd backpressure absent")}
    } else if outbound.closedState.Load() {t.Error("control unexpectedly closed outbound before cleanup")}
    if patched {routeLifecycleAwait(t,func()bool{c:=routeLifecycleWorkerCounts();return c["route_copy_workers"]==0 && c["route_callback_workers"]==0},"association copy workers exit before reads restored")}
    counts:=routeLifecycleWorkerCounts()
    parentWriters:=0
    stackData:=make([]byte,2<<20);stackN:=runtime.Stack(stackData,true)
    if stackN==len(stackData){t.Fatal("stack snapshot truncated")}
    for _,stack:=range strings.Split(string(stackData[:stackN]),"\n\n"){if strings.Contains(stack,"/sing-vmess.(*serverSession).writeLoop("){parentWriters++}}
    log.event("mux_arm_snapshot",map[string]any{"arm":os.Getenv("VUI_MUX_ARM"),"parent_writers":parentWriters,"route_copies":counts["route_copy_workers"],"route_callbacks":counts["route_callback_workers"],"cancelers":counts["canceler_workers"],"parent_transport_open":handler.muxContext.Err()==nil,"owned_outbound_closed":outbound.closedState.Load()})
    if patched && parentWriters!=1{t.Error("bounded parent writer was hidden or duplicated")}
    if !patched && parentWriters!=0{t.Error("control contains experimental writer")}
    expectedCallbacks:=int32(0)
    if log.phase=="lifecycle" && (!reply || patched){expectedCallbacks=1}
    log.event("snapshot",map[string]any{"label":map[bool]string{true:"real_mux_reply_backpressure",false:"real_mux_status_end_backpressure"}[reply],
        "outbound_closed":outbound.closedState.Load(),"on_close_calls":handler.callbacks.Load(),"parent_context_done":handler.muxContext.Err()!=nil,"outer_context_done":ctx.Err()!=nil,
        "route_copy_workers":counts["route_copy_workers"],"route_callback_workers":counts["route_callback_workers"],"canceler_workers":counts["canceler_workers"],
        "reply_write_starts":server.replyStarts.Load(),"reply_write_active":server.replyActive.Load(),"status_end_write_starts":server.endStarts.Load(),
        "status_end_write_active":server.endActive.Load(),"wire_session_id":session,"full_lifecycle_qualified":false})
    if handler.callbacks.Load()!=expectedCallbacks{t.Errorf("real mux callbacks=%d want %d",handler.callbacks.Load(),expectedCallbacks)}
    if reply && (server.replyActive.Load()!=1 || (!patched && counts["route_copy_workers"]!=2) || (patched && counts["route_copy_workers"]!=0)){t.Error("real reply did not retain both blocked copy workers")}
    if !reply && log.phase!="lifecycle" && server.endStarts.Load()!=0{t.Error("control sent premature StatusEnd")}
    if !reply && !patched && log.phase=="lifecycle" && counts["route_copy_workers"]<1{t.Error("real StatusEnd did not retain a copy worker")}
    // Mark the evidence boundary BEFORE explicit cleanup. Restoring client reads
    // must let real frames drain while the parent mux transport is still open.
    if handler.muxContext.Err()!=nil{t.Error("actual parent mux context canceled before restoring reads")}
    log.event("restore_reads",map[string]any{"explicit_cleanup":true,"parent_transport_open":handler.muxContext.Err()==nil})
    drainDone=make(chan struct{})
    drainResult:=make(chan error,1)
    go func(){defer close(drainDone);drainResult<-rlMuxDrainToEnd(client,session,reply)}()
    _=outbound.Close() // Controls need this cleanup; not counted as idle success.
    select{case err:=<-drainResult:if err!=nil{t.Errorf("real mux drain: %v",err)};case <-time.After(routeLifecycleBound):t.Error("real mux drain exceeded bound")}
    routeLifecycleAwait(t,func()bool{
        c:=routeLifecycleWorkerCounts()
        return server.active.Load()==0 && handler.callbacks.Load()==1 && c["route_copy_workers"]==0 && c["route_callback_workers"]==0 && c["canceler_workers"]==0
    },"actual copies settle after reads restored, before parent close")
    if handler.muxContext.Err()!=nil{t.Error("actual parent mux context canceled while draining")}
    log.event("drained_before_parent_close",map[string]any{"parent_context_done":handler.muxContext.Err()!=nil,"outer_context_done":ctx.Err()!=nil,"on_close_calls":handler.callbacks.Load(),
        "active_transport_writes":server.active.Load(),"full_lifecycle_qualified":false})
}

func TestMuxRouteReplyBackpressure(t *testing.T){mxRouteRunBackpressure(t,true)}
func TestMuxRouteEndBackpressure(t *testing.T){mxRouteRunBackpressure(t,false)}
