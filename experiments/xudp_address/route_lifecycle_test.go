// Isolated route ownership tests for sing-box af6e64c3b69e6132ebaee0e1a3d24e93903f6709.
// Copy only into core/route; execute with the unchanged fixed dependency graph.
// VUI_LIFECYCLE_PHASE selects expectations, NEVER the implementation under test.
// Shared VMess/mux paths and backpressure remain outside product qualification.
package route

import (
    "bytes"
    "context"
    "encoding/json"
    "encoding/binary"
    "errors"
    "fmt"
    "io"
    "net"
    "os"
    "runtime"
    "strings"
    "sync"
    "sync/atomic"
    "testing"
    "time"

    "github.com/sagernet/sing-box/adapter"
    vmess "github.com/sagernet/sing-vmess"
    "github.com/sagernet/sing/common/buf"
    "github.com/sagernet/sing/common/canceler"
    "github.com/sagernet/sing/common/logger"
    M "github.com/sagernet/sing/common/metadata"
    N "github.com/sagernet/sing/common/network"
)

const (
    routeLifecycleIdle = 200 * time.Millisecond
    routeLifecycleBound = 2 * time.Second
    routeLifecycleWindow = 450 * time.Millisecond
)

type routeLifecycleLog struct {
    t *testing.T
    phase string
    start time.Time
    mu sync.Mutex
    sequence int
    firstCopyContext atomic.Int32 // 0 unseen, 1 nil cause, 2 canceled cause.
}

func newRouteLifecycleLog(t *testing.T) *routeLifecycleLog {
    t.Helper()
    phase := os.Getenv("VUI_LIFECYCLE_PHASE")
    if phase != "baseline" && phase != "patched" && phase != "lifecycle" { t.Fatalf("invalid VUI_LIFECYCLE_PHASE %q", phase) }
    routeLifecycleCheckStackParser(t)
    log := &routeLifecycleLog{t:t, phase:phase, start:time.Now()}
    log.event("setup", map[string]any{"idle_ns":routeLifecycleIdle.Nanoseconds(), "wait_bound_ns":routeLifecycleBound.Nanoseconds(),
        "window_ns":routeLifecycleWindow.Nanoseconds(), "worker_count_source":"runtime.Stack", "worker_categories_overlap":false,
        "actual_entrypoint":"ConnectionManager.NewPacketConnection", "stack_classifier_checks":5, "full_lifecycle_qualified":false})
    return log
}

func (l *routeLifecycleLog) event(name string, fields map[string]any) {
    l.mu.Lock()
    defer l.mu.Unlock()
    if fields == nil { fields = make(map[string]any) }
    l.sequence++
    fields["schema"], fields["case"], fields["phase"] = 1, l.t.Name(), l.phase
    fields["event"], fields["seq"], fields["elapsed_ns"] = name, l.sequence, time.Since(l.start).Nanoseconds()
    data, err := json.Marshal(fields)
    if err != nil { panic(err) }
    l.t.Logf("XUDP_ROUTE_LIFECYCLE %s", data)
}

func routeLifecycleError(err error) string {
    switch {
    case err == nil: return "nil"
    case errors.Is(err, os.ErrDeadlineExceeded), errors.Is(err, context.DeadlineExceeded): return "deadline_exceeded"
    case errors.Is(err, context.Canceled): return "canceled"
    case errors.Is(err, net.ErrClosed): return "closed"
    case errors.Is(err, io.EOF): return "eof"
    default: return err.Error()
    }
}

// Counts are observed stack categories, not guessed from onClose or I/O events.
// Tests are serial, require a zero route baseline, and emit no stack contents.
// The fixed patch AfterFunc is func1. Exclude copy stacks from callback counts;
// func2 is the completion wrapper, and gowrap frames are not independent hooks.
func routeLifecycleWorkerCounts() map[string]int {
    var data []byte
    for size := 64 << 10; size <= 8 << 20; size *= 2 {
        data = make([]byte, size)
        n := runtime.Stack(data, true)
        if n < len(data) { data = data[:n]; break }
        if size == 8 << 20 { return map[string]int{"route_copy_workers":-1,"route_callback_workers":-1,"canceler_workers":-1,"mux_cleanup_workers":-1} }
    }
    return routeLifecycleCountStacks(string(data))
}

func routeLifecycleCountStacks(data string) map[string]int {
    counts := map[string]int{"route_copy_workers":0,"route_callback_workers":0,"canceler_workers":0,"mux_cleanup_workers":0}
    for _, stack := range strings.Split(data, "\n\n") {
        isCopy := strings.Contains(stack, "/route.(*ConnectionManager).packetConnectionCopy(")
        if isCopy { counts["route_copy_workers"]++ }
        if !isCopy && strings.Contains(stack, "/route.(*ConnectionManager).NewPacketConnection.func1(") { counts["route_callback_workers"]++ }
        if strings.Contains(stack, "/canceler.(*Instance).wait(") { counts["canceler_workers"]++ }
        if strings.Contains(stack, "/sing-vmess.HandleMuxConnection.func1(") { counts["mux_cleanup_workers"]++ }
    }
    return counts
}

func routeLifecycleCheckStackParser(t *testing.T) {
    t.Helper()
    cases := []struct{stack string; copies,callbacks,cancelers,mux int}{
        {"goroutine 1 [running]:\nx/route.(*ConnectionManager).packetConnectionCopy(\nx/route.(*ConnectionManager).NewPacketConnection.func2(\nx/route.(*ConnectionManager).NewPacketConnection.gowrap1()",1,0,0,0},
        {"goroutine 2 [running]:\nx/route.(*ConnectionManager).NewPacketConnection.func1()",0,1,0,0},
        {"goroutine 3 [running]:\nx/route.(*ConnectionManager).NewPacketConnection.func10()\nx/route.(*ConnectionManager).NewPacketConnection.func2()\nx/route.(*ConnectionManager).NewPacketConnection.gowrap2()",0,0,0,0},
        {"goroutine 4 [running]:\nx/canceler.(*Instance).wait(\n\ngoroutine 5 [running]:\nx/other.TimeoutPacketConn.ReadPacket()",0,0,1,0},
        {"goroutine 6 [running]:\nx/sing-vmess.HandleMuxConnection.func1()",0,0,0,1},
    }
    for _,test := range cases {
        got:=routeLifecycleCountStacks(test.stack)
        if got["route_copy_workers"]!=test.copies || got["route_callback_workers"]!=test.callbacks || got["canceler_workers"]!=test.cancelers || got["mux_cleanup_workers"]!=test.mux { t.Fatalf("stack classifier failed synthetic case: %v",got) }
    }
}

func routeLifecycleNoWorkers() bool {
    c := routeLifecycleWorkerCounts()
    return c["route_copy_workers"] == 0 && c["route_callback_workers"] == 0 && c["canceler_workers"] == 0 && c["mux_cleanup_workers"] == 0
}

func routeLifecycleAwait(t *testing.T, predicate func() bool, label string) bool {
    t.Helper()
    deadline := time.Now().Add(routeLifecycleBound)
    for {
        if predicate() { return true }
        if time.Now().After(deadline) { t.Errorf("bounded wait exceeded %s: %s", routeLifecycleBound, label); return false }
        time.Sleep(5 * time.Millisecond)
    }
}

func routeLifecycleChannelReady(ch <-chan struct{}) bool {
    select { case <-ch: return true; default: return false }
}

func routeLifecycleRelease(ch chan struct{}) {
    if ch == nil { return }
    select { case <-ch: default: close(ch) }
}

type routeLifecyclePacket struct { data []byte; err error }

type routeLifecycleEndpoint struct {
    log *routeLifecycleLog
    role string
    supportsDeadline bool
    closed chan struct{}
    closedState atomic.Bool
    closeStarted atomic.Bool
    closeCalls atomic.Int32
    readCalls atomic.Int32
    writeCalls atomic.Int32
    lastWriteNS atomic.Int64
    activeReads atomic.Int32
    activeWrites atomic.Int32
    readPackets chan routeLifecyclePacket
    writes chan []byte
    readStarted chan struct{}
    readOnce sync.Once
    preCloseGate chan struct{}
    preCloseEntered chan struct{}
    closeGate chan struct{}
    closeBlocked chan struct{}
    writeGate chan struct{}
    writeBlocked chan struct{}
    writeBlockOnce sync.Once
    mu sync.Mutex
    deadline time.Time
    deadlineChanged chan struct{}
}

var _ N.PacketConn = (*routeLifecycleEndpoint)(nil)
var _ net.Conn = (*routeLifecycleEndpoint)(nil)
var _ net.PacketConn = (*routeLifecycleEndpoint)(nil)

func newRouteLifecycleEndpoint(log *routeLifecycleLog, role string, deadline bool) *routeLifecycleEndpoint {
    return &routeLifecycleEndpoint{log:log, role:role, supportsDeadline:deadline,
        closed:make(chan struct{}), readPackets:make(chan routeLifecyclePacket,64), writes:make(chan []byte,64),
        readStarted:make(chan struct{}), preCloseEntered:make(chan struct{}), closeBlocked:make(chan struct{}), writeBlocked:make(chan struct{}), deadlineChanged:make(chan struct{})}
}

func (c *routeLifecycleEndpoint) readData() ([]byte,error) {
    c.readCalls.Add(1)
    c.activeReads.Add(1)
    defer c.activeReads.Add(-1)
    c.readOnce.Do(func(){close(c.readStarted)})
    for {
        c.mu.Lock(); deadline, changed := c.deadline, c.deadlineChanged; c.mu.Unlock()
        var timer *time.Timer
        var timeout <-chan time.Time
        if !deadline.IsZero() { timer = time.NewTimer(time.Until(deadline)); timeout = timer.C }
        select {
        case <-c.closed:
            if timer != nil { timer.Stop() }; return nil,net.ErrClosed
        case packet := <-c.readPackets:
            if timer != nil { timer.Stop() }; return packet.data,packet.err
        case <-changed:
            if timer != nil { timer.Stop() }; continue
        case <-timeout:
            return nil,os.ErrDeadlineExceeded
        }
    }
}

func (c *routeLifecycleEndpoint) writeData(data []byte) error {
    c.writeCalls.Add(1)
    c.activeWrites.Add(1)
    defer c.activeWrites.Add(-1)
    if c.writeGate != nil {
        c.writeBlockOnce.Do(func(){c.log.event("fixture_write_blocked",map[string]any{"role":c.role});close(c.writeBlocked)})
        // Deliberately models a shared writer that a per-stream Close cannot abort.
        <-c.writeGate
    }
    select { case <-c.closed: return net.ErrClosed; default: }
    c.lastWriteNS.Store(time.Since(c.log.start).Nanoseconds())
    select {
    case c.writes <- append([]byte(nil),data...):
        c.log.event("packet_written",map[string]any{"role":c.role,"bytes":len(data)})
        return nil
    case <-c.closed: return net.ErrClosed
    }
}

func (c *routeLifecycleEndpoint) ReadPacket(buffer *buf.Buffer) (M.Socksaddr,error) {
    data,err := c.readData()
    if err == nil { _,err = buffer.Write(data) }
    return M.ParseSocksaddr("127.0.0.3:12345"),err
}
func (c *routeLifecycleEndpoint) WritePacket(buffer *buf.Buffer, _ M.Socksaddr) error {
    defer buffer.Release()
    return c.writeData(buffer.Bytes())
}
func (c *routeLifecycleEndpoint) Read(p []byte) (int,error) { data,err:=c.readData();return copy(p,data),err }
func (c *routeLifecycleEndpoint) Write(p []byte) (int,error) { if err:=c.writeData(p);err!=nil{return 0,err};return len(p),nil }
func (c *routeLifecycleEndpoint) ReadFrom(p []byte) (int,net.Addr,error) { n,err:=c.Read(p);return n,c.RemoteAddr(),err }
func (c *routeLifecycleEndpoint) WriteTo(p []byte,_ net.Addr) (int,error) { return c.Write(p) }
func (c *routeLifecycleEndpoint) LocalAddr() net.Addr { return M.ParseSocksaddr("127.0.0.4:23456").UDPAddr() }
func (c *routeLifecycleEndpoint) RemoteAddr() net.Addr { return M.ParseSocksaddr("127.0.0.3:12345").UDPAddr() }
func (c *routeLifecycleEndpoint) SetDeadline(t time.Time) error { return c.SetReadDeadline(t) }
func (c *routeLifecycleEndpoint) SetWriteDeadline(time.Time) error { return os.ErrInvalid }
func (c *routeLifecycleEndpoint) SetReadDeadline(t time.Time) error {
    if !c.supportsDeadline { return os.ErrInvalid }
    c.mu.Lock(); c.deadline=t; close(c.deadlineChanged); c.deadlineChanged=make(chan struct{}); c.mu.Unlock()
    return nil
}
func (c *routeLifecycleEndpoint) Close() error {
    calls:=c.closeCalls.Add(1)
    first:=c.closeStarted.CompareAndSwap(false,true)
    c.log.event("endpoint_close",map[string]any{"role":c.role,"call":calls,"first":first})
    if first {
        if c.preCloseGate != nil {
            close(c.preCloseEntered)
            c.log.event("fixture_pre_close_blocked",map[string]any{"role":c.role})
            <-c.preCloseGate
        }
        close(c.closed)
        c.closedState.Store(true) // Resource signal closed, not merely Close entered.
        if c.closeGate != nil {
            close(c.closeBlocked)
            c.log.event("fixture_close_blocked",map[string]any{"role":c.role})
            <-c.closeGate
        }
    }
    return nil
}

type routeLifecycleDialer struct { outbound *routeLifecycleEndpoint; log *routeLifecycleLog }
func (d *routeLifecycleDialer) DialContext(_ context.Context, network string, _ M.Socksaddr) (net.Conn,error) {
    d.log.event("dial",map[string]any{"network":network});return d.outbound,nil
}
func (d *routeLifecycleDialer) ListenPacket(context.Context,M.Socksaddr) (net.PacketConn,error) {
    d.log.event("listen_packet",nil);return d.outbound,nil
}

type routeLifecycleLogger struct { logger.ContextLogger; log *routeLifecycleLog }
func (l *routeLifecycleLogger) TraceContext(ctx context.Context,args ...any) { l.record(ctx,args...) }
func (l *routeLifecycleLogger) DebugContext(ctx context.Context,args ...any) { l.record(ctx,args...) }
func (l *routeLifecycleLogger) ErrorContext(ctx context.Context,args ...any) { l.record(ctx,args...) }
func (l *routeLifecycleLogger) record(ctx context.Context,args ...any) {
    cause:=context.Cause(ctx)
    causeKind:="other"
    switch {case cause==nil:causeKind="nil";case errors.Is(cause,os.ErrDeadlineExceeded):causeKind="os.ErrDeadlineExceeded";case errors.Is(cause,context.DeadlineExceeded):causeKind="context.DeadlineExceeded";case errors.Is(cause,context.Canceled):causeKind="context.Canceled";case errors.Is(cause,net.ErrClosed):causeKind="net.ErrClosed"}
    message:=fmt.Sprint(args...)
    if strings.HasPrefix(message,"packet upload") || strings.HasPrefix(message,"packet download") { state:=int32(1);if cause!=nil{state=2};l.log.firstCopyContext.CompareAndSwap(0,state) }
    l.log.event("route_log",map[string]any{"message":message,"context_cause":routeLifecycleError(cause),"context_cause_sentinel":causeKind,"context_error":routeLifecycleError(ctx.Err())})
}

type routeLifecycleConfig struct {
    nativeDeadline bool
    noExplicitTimeout bool
    inferredTimeout bool
    notUDPConnect bool
    reusedTimer bool
    blockedOnClose bool
    blockedOutboundClose bool
    blockedInboundClose bool
    blockedInboundWrite bool
    preCanceled bool
    nilOnClose bool
}

type routeLifecycleFixture struct {
    t *testing.T
    log *routeLifecycleLog
    in,out *routeLifecycleEndpoint
    ctx context.Context
    cancel context.CancelFunc
    onCloseCalls atomic.Int32
    onCloseEntered chan struct{}
    onCloseOnce sync.Once
    onCloseGate chan struct{}
    inbound N.PacketConn
    ownedWorkers []<-chan struct{}
    originalOnCloseNil bool
}

func newRouteLifecycleFixture(t *testing.T,cfg routeLifecycleConfig) *routeLifecycleFixture {
    t.Helper()
    if !routeLifecycleAwait(t,routeLifecycleNoWorkers,"zero fixture worker baseline") { t.Fatal("previous route workers remain") }
    log:=newRouteLifecycleLog(t)
    ctx,cancel:=context.WithCancel(context.Background())
    f:=&routeLifecycleFixture{t:t,log:log,ctx:ctx,cancel:cancel,onCloseEntered:make(chan struct{}),originalOnCloseNil:cfg.nilOnClose}
    f.in=newRouteLifecycleEndpoint(log,"inbound",cfg.nativeDeadline)
    f.out=newRouteLifecycleEndpoint(log,"outbound",false)
    if cfg.blockedOnClose { f.onCloseGate=make(chan struct{}) }
    if cfg.blockedOutboundClose { f.out.preCloseGate=make(chan struct{}) }
    if cfg.blockedInboundClose { f.in.closeGate=make(chan struct{}) }
    if cfg.blockedInboundWrite { f.in.writeGate=make(chan struct{}) }
    f.inbound=f.in
    if cfg.reusedTimer { _,f.inbound=canceler.NewPacketConn(ctx,f.in,routeLifecycleIdle) }
    t.Cleanup(f.cleanup)
    metadata:=adapter.InboundContext{Destination:M.ParseSocksaddr("127.0.0.3:12345"),UDPConnect:!cfg.notUDPConnect,UDPTimeout:routeLifecycleIdle}
    if cfg.noExplicitTimeout || cfg.inferredTimeout { metadata.UDPTimeout=0 }
    if cfg.inferredTimeout { metadata.Protocol="dns" }
    manager:=NewConnectionManager(&routeLifecycleLogger{ContextLogger:logger.NOP(),log:log})
    var onClose N.CloseHandlerFunc
    if !cfg.nilOnClose { onClose=func(err error){
        f.onCloseCalls.Add(1)
        log.event("actual_on_close",map[string]any{"error":routeLifecycleError(err)})
        f.onCloseOnce.Do(func(){close(f.onCloseEntered)})
        if f.onCloseGate!=nil { <-f.onCloseGate }
    } }
    if cfg.preCanceled { cancel() }
    manager.NewPacketConnection(ctx,&routeLifecycleDialer{outbound:f.out,log:log},f.inbound,metadata,onClose)
    if !routeLifecycleAwait(t,func()bool{return routeLifecycleChannelReady(f.in.readStarted)&&routeLifecycleChannelReady(f.out.readStarted)},"both actual copy readers entered") { t.Fatal("copy entry precondition absent") }
    if !cfg.preCanceled && f.out.closedState.Load() { t.Fatal("connection closed before observation") }
    return f
}

func (f *routeLifecycleFixture) cleanup() {
    f.log.event("cleanup_start",nil)
    routeLifecycleRelease(f.out.preCloseGate)
    routeLifecycleRelease(f.onCloseGate)
    routeLifecycleRelease(f.in.closeGate)
    routeLifecycleRelease(f.in.writeGate)
    f.cancel()
    _=f.inbound.Close()
    _=f.in.Close()
    _=f.out.Close()
    ownedJoined:=true
    for _,done:=range f.ownedWorkers {
        if !routeLifecycleAwait(f.t,func()bool{return routeLifecycleChannelReady(done)},"test-owned worker join") { ownedJoined=false }
    }
    routeJoined:=routeLifecycleAwait(f.t,routeLifecycleNoWorkers,"actual route/callback/canceler workers exit")
    counts:=routeLifecycleWorkerCounts()
    f.log.event("cleanup_joined",map[string]any{"route_copy_workers":counts["route_copy_workers"],"route_callback_workers":counts["route_callback_workers"],
        "canceler_workers":counts["canceler_workers"],"mux_cleanup_workers":counts["mux_cleanup_workers"],"test_owned_workers_joined":ownedJoined,"actual_route_workers_joined":routeJoined,
        "inbound_reads":f.in.activeReads.Load(),"inbound_writes":f.in.activeWrites.Load(),"outbound_reads":f.out.activeReads.Load(),"outbound_writes":f.out.activeWrites.Load(),
        "on_close_calls":f.onCloseCalls.Load(),"full_lifecycle_qualified":false})
    expectedCallbacks:=int32(1)
    if f.originalOnCloseNil { expectedCallbacks=0 }
    if f.onCloseCalls.Load()!=expectedCallbacks { f.t.Errorf("actual onClose calls=%d, want %d after cleanup",f.onCloseCalls.Load(),expectedCallbacks) }
}

func (f *routeLifecycleFixture) snapshot(label string) map[string]int {
    counts:=routeLifecycleWorkerCounts()
    f.log.event("snapshot",map[string]any{"label":label,"outbound_closed":f.out.closedState.Load(),"inbound_closed":f.in.closedState.Load(),
        "on_close_calls":f.onCloseCalls.Load(),"original_on_close_nil":f.originalOnCloseNil,"outbound_close_calls":f.out.closeCalls.Load(),"parent_context_done":f.ctx.Err()!=nil,
        "route_copy_workers":counts["route_copy_workers"],"route_callback_workers":counts["route_callback_workers"],"canceler_workers":counts["canceler_workers"],
        "inbound_reads":f.in.activeReads.Load(),"inbound_writes":f.in.activeWrites.Load(),"outbound_reads":f.out.activeReads.Load(),"outbound_writes":f.out.activeWrites.Load(),
        "inbound_read_calls":f.in.readCalls.Load(),"inbound_write_calls":f.in.writeCalls.Load(),"outbound_read_calls":f.out.readCalls.Load(),"outbound_write_calls":f.out.writeCalls.Load(),
        "full_lifecycle_qualified":false})
    return counts
}

func (f *routeLifecycleFixture) expectClosed(expected bool,label string) {
    f.snapshot(label)
    if f.out.closedState.Load()!=expected { f.t.Errorf("%s: outbound_closed=%t want %t in phase %s",label,f.out.closedState.Load(),expected,f.log.phase) }
}

func (f *routeLifecycleFixture) transfer(upload bool,payload []byte) int64 {
    f.t.Helper()
    source,destination:=f.out,f.in
    if upload { source,destination=f.in,f.out }
    select { case source.readPackets<-routeLifecyclePacket{data:append([]byte(nil),payload...)}:
    case <-time.After(routeLifecycleBound): f.t.Fatal("fixture packet input blocked") }
    select {
    case actual:=<-destination.writes:
        if !bytes.Equal(actual,payload) { f.t.Fatalf("payload changed: %x != %x",actual,payload) }
    case <-time.After(routeLifecycleBound): f.t.Fatal("actual route failed to deliver packet")
    }
    return destination.lastWriteNS.Load()
}

func TestLifecycleXUDPIdleCancellation(t *testing.T) {
    f:=newRouteLifecycleFixture(t,routeLifecycleConfig{})
    f.transfer(true,[]byte("initial-upload"))
    time.Sleep(routeLifecycleWindow)
    f.expectClosed(f.log.phase=="lifecycle","idle")
}
func TestLifecycleXUDPActivityUpload(t *testing.T) { routeLifecycleActivity(t,true) }
func TestLifecycleXUDPActivityDownload(t *testing.T) { routeLifecycleActivity(t,false) }
func routeLifecycleActivity(t *testing.T,upload bool) {
    f:=newRouteLifecycleFixture(t,routeLifecycleConfig{})
    var previous,first,maxGap int64
    for i:=0;i<15;i++ {
        if previous!=0 && time.Since(f.log.start).Nanoseconds()-previous>=routeLifecycleIdle.Nanoseconds(){
            f.log.event("harness_precondition_failed",map[string]any{"reason":"activity scheduling gap reached idle timeout"});t.Fatal("activity timing precondition absent")
        }
        now:=f.transfer(upload,[]byte(fmt.Sprintf("activity-%d",i)))
        gap:=int64(0);if previous!=0{gap=now-previous}
        f.log.event("activity_success",map[string]any{"index":i,"successful_write_ns":now,"gap_ns":gap,"idle_ns":routeLifecycleIdle.Nanoseconds()})
        if previous!=0 && gap>=routeLifecycleIdle.Nanoseconds(){t.Fatal("actual successful I/O interval reached idle timeout; harness premise absent")}
        if first==0{first=now};if gap>maxGap{maxGap=gap};previous=now
        if f.out.closedState.Load() { t.Fatal("active association was closed") }
        if i<14 { time.Sleep(50*time.Millisecond) }
    }
    span:=previous-first
    f.log.event("activity_window",map[string]any{"packet_count":15,"max_gap_ns":maxGap,"activity_span_ns":span,"direction":map[bool]string{true:"upload",false:"download"}[upload]})
    if span<3*routeLifecycleIdle.Nanoseconds() || maxGap>=routeLifecycleIdle.Nanoseconds(){t.Fatal("activity duration/gap premise absent")}
    f.snapshot("activity_alive")
    time.Sleep(routeLifecycleWindow)
    f.expectClosed(f.log.phase=="lifecycle","after_activity_idle")
}
func TestLifecycleXUDPNormalUploadEnd(t *testing.T) { routeLifecycleNormalEnd(t,true) }
func TestLifecycleXUDPNormalDownloadEnd(t *testing.T) { routeLifecycleNormalEnd(t,false) }
func routeLifecycleNormalEnd(t *testing.T,upload bool) {
    f:=newRouteLifecycleFixture(t,routeLifecycleConfig{})
    f.transfer(true,[]byte("normal-end-fresh-activity"))
    normalStarted:=time.Now()
    source:=f.out
    if upload { source=f.in }
    source.readPackets<-routeLifecyclePacket{err:io.EOF}
    if !routeLifecycleAwait(t,routeLifecycleNoWorkers,"normal copy completion") { t.Fatal("normal completion blocked") }
    if time.Since(normalStarted)>=routeLifecycleIdle/2 || f.log.firstCopyContext.Load()!=1{
        f.log.event("harness_precondition_failed",map[string]any{"reason":"normal completion overlapped possible idle expiry"});t.Fatal("normal-before-expiry premise absent")
    }
    time.Sleep(routeLifecycleWindow)
    f.expectClosed(true,"normal_end_no_stale_hook")
    expected:=int32(2)
    if f.log.phase=="lifecycle" { expected=3 }
    if f.out.closeCalls.Load()!=expected { t.Errorf("normal completion close calls=%d want %d; possible stale hook",f.out.closeCalls.Load(),expected) }
}
func TestLifecycleXUDPParentCancel(t *testing.T) {
    f:=newRouteLifecycleFixture(t,routeLifecycleConfig{})
    f.cancel()
    time.Sleep(routeLifecycleWindow)
    f.expectClosed(f.log.phase=="lifecycle","parent_cancel")
}
func TestLifecycleXUDPCancelEOFCompetition(t *testing.T) {
    f:=newRouteLifecycleFixture(t,routeLifecycleConfig{blockedOutboundClose:true})
    f.transfer(true,[]byte("competition-fresh-activity"))
    // Hold the FIRST real outbound Close before the resource signal closes.
    // Lifecycle's actual AfterFunc owns this call; controls enter the same gate
    // only from first-copy cleanup. Concurrent Close calls intentionally return
    // while the first is held, exposing completion-vs-callback overlap.
    if f.log.phase=="lifecycle" {
        if !routeLifecycleAwait(t,func()bool{return routeLifecycleChannelReady(f.out.preCloseEntered)},"actual timer callback entered outbound Close gate"){t.Fatal("timer callback premise absent")}
        if routeLifecycleWorkerCounts()["route_callback_workers"]!=1 || f.onCloseCalls.Load()!=0 || f.out.closedState.Load(){t.Fatal("real AfterFunc overlap premise absent")}
    }
    start:=make(chan struct{})
    for i:=0;i<2;i++ {
        done:=make(chan struct{});f.ownedWorkers=append(f.ownedWorkers,done)
        go func(index int){defer close(done);<-start;if index==0{f.cancel()}else{select{case f.in.readPackets<-routeLifecyclePacket{err:io.EOF}:case <-f.in.closed:}}}(i)
    }
    close(start)
    expectedCallbacks:=0
    if f.log.phase=="lifecycle"{expectedCallbacks=1}
    if !routeLifecycleAwait(t,func()bool{
        counts:=routeLifecycleWorkerCounts()
        return routeLifecycleChannelReady(f.out.preCloseEntered) && f.onCloseCalls.Load()==1 && f.ctx.Err()!=nil && counts["route_callback_workers"]==expectedCallbacks
    },"first-copy handler overlaps held Close"){t.Fatal("competition window not established")}
    counts:=routeLifecycleWorkerCounts()
    f.log.event("race_window",map[string]any{"pre_close_gate_entered":true,"outbound_closed":f.out.closedState.Load(),
        "on_close_calls":f.onCloseCalls.Load(),"parent_context_done":f.ctx.Err()!=nil,"route_copy_workers":counts["route_copy_workers"],
        "route_callback_workers":counts["route_callback_workers"],"actual_timer_callback_held":f.log.phase=="lifecycle","full_lifecycle_qualified":false})
    if f.out.closedState.Load() || counts["route_copy_workers"]<1{t.Fatal("resource closed or copy workers escaped held gate")}
    routeLifecycleRelease(f.out.preCloseGate)
    if !routeLifecycleAwait(t,routeLifecycleNoWorkers,"cancel/EOF completion") { t.Fatal("competing completion blocked") }
    f.expectClosed(true,"cancel_eof_competition")
    if f.onCloseCalls.Load()!=1 { t.Fatal("competing paths duplicated handler") }
}
func TestLifecycleXUDPBlockedOnClose(t *testing.T) {
    f:=newRouteLifecycleFixture(t,routeLifecycleConfig{blockedOnClose:true})
    f.in.readPackets<-routeLifecyclePacket{err:io.EOF}
    if !routeLifecycleAwait(t,func()bool{return routeLifecycleChannelReady(f.onCloseEntered)},"blocked actual onClose entered") { t.Fatal("handler not entered") }
    time.Sleep(routeLifecycleWindow)
    f.expectClosed(f.log.phase=="lifecycle","blocked_actual_on_close")
}
func TestLifecycleXUDPBlockedInboundClose(t *testing.T) {
    f:=newRouteLifecycleFixture(t,routeLifecycleConfig{blockedInboundClose:true})
    f.in.readPackets<-routeLifecyclePacket{err:io.EOF}
    if !routeLifecycleAwait(t,func()bool{return routeLifecycleChannelReady(f.in.closeBlocked)},"blocked inbound Close entered") { t.Fatal("Close not entered") }
    time.Sleep(routeLifecycleWindow)
    f.expectClosed(f.log.phase=="lifecycle","blocked_inbound_close")
}
func TestLifecycleXUDPBlockedInboundWrite(t *testing.T) {
    f:=newRouteLifecycleFixture(t,routeLifecycleConfig{blockedInboundWrite:true})
    f.out.readPackets<-routeLifecyclePacket{data:[]byte("blocked-downlink")}
    if !routeLifecycleAwait(t,func()bool{return routeLifecycleChannelReady(f.in.writeBlocked)},"blocked inbound writer entered") { t.Fatal("write not entered") }
    time.Sleep(routeLifecycleWindow)
    f.expectClosed(f.log.phase=="lifecycle","blocked_inbound_write_incomplete")
    if f.onCloseCalls.Load()!=0 || routeLifecycleWorkerCounts()["route_copy_workers"]!=2 { t.Fatal("backpressure was not actually retained") }
}
func TestLifecycleXUDPGuardScoping(t *testing.T) {
    cases:=[]struct{name string;cfg routeLifecycleConfig;parentCancel bool;closes bool}{
        {"native_deadline",routeLifecycleConfig{nativeDeadline:true},false,true},
        {"no_explicit_timeout",routeLifecycleConfig{noExplicitTimeout:true},true,false},
        {"inferred_timeout",routeLifecycleConfig{inferredTimeout:true},true,false},
        {"not_udp_connect",routeLifecycleConfig{notUDPConnect:true},false,false},
        {"reused_timer",routeLifecycleConfig{reusedTimer:true},false,false},
    }
    for _,test:=range cases { t.Run(test.name,func(t *testing.T){
        f:=newRouteLifecycleFixture(t,test.cfg)
        if test.parentCancel { f.cancel() }
        time.Sleep(routeLifecycleWindow)
        f.expectClosed(test.closes,"guard_"+test.name)
    }) }
}

func TestLifecycleXUDPAlreadyCanceledContext(t *testing.T) {
    f:=newRouteLifecycleFixture(t,routeLifecycleConfig{preCanceled:true})
    time.Sleep(routeLifecycleWindow)
    f.expectClosed(f.log.phase=="lifecycle","already_canceled_context")
}
func TestLifecycleXUDPNilOnClose(t *testing.T) {
    f:=newRouteLifecycleFixture(t,routeLifecycleConfig{nilOnClose:true})
    time.Sleep(routeLifecycleWindow)
    f.expectClosed(f.log.phase=="lifecycle","nil_original_on_close")
    if f.onCloseCalls.Load()!=0 { t.Fatal("nil original handler was invoked") }
}

// Real mux backpressure: this wrapper OBSERVES ordinary net.Conn.Write only.
// net.Pipe itself supplies blocking; no fabricated mux or hook is involved.
type rlMuxObservedConn struct {
    net.Conn
    replyStarts, endStarts, replyActive, endActive, active atomic.Int32
}
func (c *rlMuxObservedConn) Write(p []byte) (int,error) {
    c.active.Add(1);defer c.active.Add(-1)
    if len(p)>=6 {
        switch p[4] {
        case vmess.StatusKeep: c.replyStarts.Add(1);c.replyActive.Add(1);defer c.replyActive.Add(-1)
        case vmess.StatusEnd: c.endStarts.Add(1);c.endActive.Add(1);defer c.endActive.Add(-1)
        }
    }
    return c.Conn.Write(p)
}

type rlMuxHandler struct {
    manager *ConnectionManager
    dialer *routeLifecycleDialer
    log *routeLifecycleLog
    entered atomic.Bool
    muxContext context.Context // Actual HandleMuxConnection child context, published by done.
    callbacks atomic.Int32
    done chan struct{}
}
var _ vmess.Handler = (*rlMuxHandler)(nil)
func (h *rlMuxHandler) NewConnectionEx(_ context.Context,c net.Conn,_ M.Socksaddr,_ M.Socksaddr,_ N.CloseHandlerFunc) {
    h.entered.Store(true);defer close(h.done)
    h.log.t.Error("unexpected real mux TCP dispatch")
    _=c.Close()
}
func (h *rlMuxHandler) NewPacketConnectionEx(ctx context.Context,c N.PacketConn,_ M.Socksaddr,dest M.Socksaddr,onClose N.CloseHandlerFunc) {
    h.entered.Store(true);defer close(h.done)
    h.muxContext=ctx
    h.manager.NewPacketConnection(ctx,h.dialer,c,adapter.InboundContext{Destination:dest,UDPConnect:true,UDPTimeout:routeLifecycleIdle},func(err error){
        h.callbacks.Add(1)
        h.log.event("actual_on_close",map[string]any{"error":routeLifecycleError(err),"real_mux":true})
        if onClose!=nil { onClose(err) }
    })
}
func rlMuxNewUDPFrame(session uint16,dest M.Socksaddr,payload []byte) ([]byte,error) {
    var header,frame bytes.Buffer
    if err:=binary.Write(&header,binary.BigEndian,session);err!=nil{return nil,err}
    header.Write([]byte{vmess.StatusNew,vmess.OptionData,vmess.NetworkUDP})
    if err:=vmess.AddressSerializer.WriteAddrPort(&header,dest);err!=nil{return nil,err}
    if err:=binary.Write(&frame,binary.BigEndian,uint16(header.Len()));err!=nil{return nil,err}
    frame.Write(header.Bytes())
    if err:=binary.Write(&frame,binary.BigEndian,uint16(len(payload)));err!=nil{return nil,err}
    frame.Write(payload)
    return frame.Bytes(),nil
}
func rlMuxDrainToEnd(client net.Conn,session uint16,wantReply bool) error {
    if err:=client.SetReadDeadline(time.Now().Add(routeLifecycleBound));err!=nil{return err}
    defer client.SetReadDeadline(time.Time{})
    replies:=0
    for i:=0;i<4;i++ {
        var length uint16
        if err:=binary.Read(client,binary.BigEndian,&length);err!=nil{return err}
        if length<4 || length>512 { return fmt.Errorf("invalid mux header length %d",length) }
        header:=make([]byte,int(length))
        if _,err:=io.ReadFull(client,header);err!=nil{return err}
        if binary.BigEndian.Uint16(header[:2])!=session { return errors.New("mux session changed") }
        status,option:=header[2],header[3]
        if status==vmess.StatusEnd {
            if length!=4 || option!=0 { return errors.New("unexpected StatusEnd shape") }
            expected:=0;if wantReply{expected=1}
            if replies!=expected{return fmt.Errorf("reply count %d want %d",replies,expected)}
            return nil
        }
        if status!=vmess.StatusKeep || option!=vmess.OptionData || length<=5 || header[4]!=vmess.NetworkUDP { return errors.New("unexpected mux reply header") }
        addressReader:=bytes.NewReader(header[5:])
        address,err:=vmess.AddressSerializer.ReadAddrPort(addressReader)
        if err!=nil{return err}
        if address!=M.ParseSocksaddr("127.0.0.3:12345") || addressReader.Len()!=0 { return errors.New("mux reply address/port changed") }
        var payloadLength uint16
        if err:=binary.Read(client,binary.BigEndian,&payloadLength);err!=nil{return err}
        payload:=make([]byte,int(payloadLength))
        if _,err:=io.ReadFull(client,payload);err!=nil{return err}
        if !bytes.Equal(payload,[]byte("real-mux-target-response")){return errors.New("mux reply payload changed")}
        replies++
    }
    return errors.New("mux drain exceeded four-frame limit")
}
func rlMuxRunBackpressure(t *testing.T,reply bool) {
    t.Helper()
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
    counts:=routeLifecycleWorkerCounts()
    expectedCallbacks:=int32(0)
    if log.phase=="lifecycle" && !reply{expectedCallbacks=1}
    log.event("snapshot",map[string]any{"label":map[bool]string{true:"real_mux_reply_backpressure",false:"real_mux_status_end_backpressure"}[reply],
        "outbound_closed":outbound.closedState.Load(),"on_close_calls":handler.callbacks.Load(),"parent_context_done":handler.muxContext.Err()!=nil,"outer_context_done":ctx.Err()!=nil,
        "route_copy_workers":counts["route_copy_workers"],"route_callback_workers":counts["route_callback_workers"],"canceler_workers":counts["canceler_workers"],
        "reply_write_starts":server.replyStarts.Load(),"reply_write_active":server.replyActive.Load(),"status_end_write_starts":server.endStarts.Load(),
        "status_end_write_active":server.endActive.Load(),"wire_session_id":session,"full_lifecycle_qualified":false})
    if handler.callbacks.Load()!=expectedCallbacks{t.Errorf("real mux callbacks=%d want %d",handler.callbacks.Load(),expectedCallbacks)}
    if reply && (server.replyActive.Load()!=1 || counts["route_copy_workers"]!=2){t.Error("real reply did not retain both blocked copy workers")}
    if !reply && log.phase!="lifecycle" && server.endStarts.Load()!=0{t.Error("control sent premature StatusEnd")}
    if !reply && log.phase=="lifecycle" && counts["route_copy_workers"]<1{t.Error("real StatusEnd did not retain a copy worker")}
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
func TestLifecycleXUDPRealMuxReplyBackpressure(t *testing.T){rlMuxRunBackpressure(t,true)}
func TestLifecycleXUDPRealMuxStatusEndBackpressure(t *testing.T){rlMuxRunBackpressure(t,false)}
