// Isolated observation only. Copy into the fixed sing-vmess source checkout.
// Run from the corresponding sing-box module, retaining its pinned sing graph:
// go test -json -count=1 -timeout=20s -run '^TestObservationXUDP' github.com/sagernet/sing-vmess
// A PASS characterizes the existing lifecycle gap; it does NOT qualify idle cleanup.
package vmess

import (
	"context"
	"encoding/json"
	"errors"
	"net"
	"os"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/sagernet/sing/common/buf"
	"github.com/sagernet/sing/common/bufio"
	"github.com/sagernet/sing/common/canceler"
	M "github.com/sagernet/sing/common/metadata"
	N "github.com/sagernet/sing/common/network"
)

const (
	xudpObservationIdle = 200 * time.Millisecond
	xudpObservationHold = 200 * time.Millisecond
	xudpObservationBound = 2 * time.Second
)

// Every Output line containing this prefix ends in one complete JSON object.
// seq and elapsed_ns are monotonic per test; timestamps describe observation,
// not scheduler-independent ordering between the context and copy goroutines.
type xudpObservationLog struct {
	t *testing.T
	start time.Time
	mu sync.Mutex
	seq int
}

func (l *xudpObservationLog) event(name string, values map[string]any) {
	l.mu.Lock()
	defer l.mu.Unlock()
	l.seq++
	if values == nil { values = make(map[string]any) }
	values["schema"] = 1
	values["case"] = l.t.Name()
	values["event"] = name
	values["seq"] = l.seq
	values["elapsed_ns"] = time.Since(l.start).Nanoseconds()
	data, err := json.Marshal(values)
	if err != nil { panic(err) } // All values are primitive local fixture data.
	l.t.Logf("XUDP_TIMEOUT_OBSERVATION %s", data)
}

func xudpObservationError(err error) string {
	switch {
	case err == nil: return "nil"
	case errors.Is(err, os.ErrDeadlineExceeded): return "deadline_exceeded"
	case errors.Is(err, net.ErrClosed): return "closed"
	case errors.Is(err, context.Canceled): return "canceled"
	case errors.Is(err, os.ErrInvalid): return "invalid"
	default: return err.Error()
	}
}

// No sockets, goroutines, packet delivery, or background traffic are hidden in
// this fixture. Close and read deadlines are the only ways to release ReadPacket.
type xudpObservationPacketConn struct {
	log *xudpObservationLog
	role string
	deadlineSupported bool
	closed chan struct{}
	closeOnce sync.Once
	closedCount atomic.Int32
	writes atomic.Int32
	readStarted chan struct{}
	readOnce sync.Once
	readContext context.Context
	readEnteredCanceled atomic.Bool
	mu sync.Mutex
	deadline time.Time
	deadlineChanged chan struct{}
}

var _ N.PacketConn = (*xudpObservationPacketConn)(nil)

func newXUDPObservationConn(log *xudpObservationLog, role string, supported bool) *xudpObservationPacketConn {
	return &xudpObservationPacketConn{log: log, role: role, deadlineSupported: supported,
		closed: make(chan struct{}), readStarted: make(chan struct{}), deadlineChanged: make(chan struct{})}
}

func (c *xudpObservationPacketConn) ReadPacket(_ *buf.Buffer) (M.Socksaddr, error) {
	c.readOnce.Do(func() {
		canceled := c.readContext != nil && c.readContext.Err() != nil
		c.readEnteredCanceled.Store(canceled)
		c.log.event("read_enter", map[string]any{"role": c.role, "context_done_at_entry": canceled})
		close(c.readStarted)
	})
	for {
		c.mu.Lock()
		deadline, changed := c.deadline, c.deadlineChanged
		c.mu.Unlock()
		if deadline.IsZero() {
			select {
			case <-c.closed: return M.Socksaddr{}, net.ErrClosed
			case <-changed: continue
			}
		}
		timer := time.NewTimer(time.Until(deadline))
		select {
		case <-c.closed:
			timer.Stop()
			return M.Socksaddr{}, net.ErrClosed
		case <-changed:
			timer.Stop()
			continue
		case <-timer.C:
			return M.Socksaddr{}, os.ErrDeadlineExceeded
		}
	}
}

func (c *xudpObservationPacketConn) WritePacket(buffer *buf.Buffer, _ M.Socksaddr) error {
	defer buffer.Release()
	select {
	case <-c.closed: return net.ErrClosed
	default:
	}
	c.writes.Add(1)
	c.log.event("write_packet", map[string]any{"role": c.role, "bytes": buffer.Len()})
	return nil
}

func (c *xudpObservationPacketConn) Close() error {
	c.closeOnce.Do(func() {
		c.closedCount.Store(1)
		c.log.event("close", map[string]any{"role": c.role})
		close(c.closed)
	})
	return nil
}

func (c *xudpObservationPacketConn) LocalAddr() net.Addr { return M.Socksaddr{} }
func (c *xudpObservationPacketConn) SetDeadline(t time.Time) error { return c.SetReadDeadline(t) }
func (c *xudpObservationPacketConn) SetWriteDeadline(time.Time) error { return os.ErrInvalid }
func (c *xudpObservationPacketConn) SetReadDeadline(t time.Time) error {
	if !c.deadlineSupported {
		c.log.event("set_read_deadline", map[string]any{"role": c.role, "error": "invalid"})
		return os.ErrInvalid
	}
	c.mu.Lock()
	c.deadline = t
	close(c.deadlineChanged)
	c.deadlineChanged = make(chan struct{})
	c.mu.Unlock()
	c.log.event("set_read_deadline", map[string]any{"role": c.role, "error": "nil", "zero": t.IsZero()})
	return nil
}

// No Upstream/ReaderReplaceable methods: CopyPacket must traverse this recorder
// and the actual pinned canceler wrapper rather than unwrap either of them.
type xudpObservationReader struct {
	N.PacketConn
	log *xudpObservationLog
	returns atomic.Int32
	result chan error
}

func (c *xudpObservationReader) ReadPacket(buffer *buf.Buffer) (M.Socksaddr, error) {
	address, err := c.PacketConn.ReadPacket(buffer)
	c.returns.Add(1)
	c.log.event("read_packet_return", map[string]any{"error": xudpObservationError(err)})
	select { case c.result <- err: default: }
	return address, err
}

func xudpObservationWait(t *testing.T, done <-chan struct{}, label string) {
	t.Helper()
	if !xudpObservationJoin(done) {
		t.Fatalf("observation exceeded %s waiting for %s", xudpObservationBound, label)
	}
}

func xudpObservationJoin(done <-chan struct{}) bool {
	timer := time.NewTimer(xudpObservationBound)
	defer timer.Stop()
	select {
	case <-done: return true
	case <-timer.C: return false
	}
}

func TestObservationXUDPTimerContextDoesNotUnblockRead(t *testing.T) {
	xudpObservePacketTimeout(t, false)
}

func TestObservationXUDPReadDeadlineUnblocksRead(t *testing.T) {
	xudpObservePacketTimeout(t, true)
}

func xudpObservePacketTimeout(t *testing.T, supported bool) {
	t.Helper()
	log := &xudpObservationLog{t: t, start: time.Now()}
	log.event("setup", map[string]any{"deadline_supported": supported,
		"idle_ns": xudpObservationIdle.Nanoseconds(), "post_cancel_hold_ns": xudpObservationHold.Nanoseconds(),
		"wait_bound_ns": xudpObservationBound.Nanoseconds(), "on_close_scope": "harness_after_real_CopyPacket",
		"activity_clock": "before_successful_wrapped_WritePacket"})
	parent, cancelParent := context.WithCancel(context.Background())
	raw := newXUDPObservationConn(log, "source", supported)
	sink := newXUDPObservationConn(log, "sink", false)
	ctx, wrapped := canceler.NewPacketConn(parent, raw, xudpObservationIdle)
	raw.readContext = ctx // Set before starting either worker.
	reader := &xudpObservationReader{PacketConn: wrapped, log: log, result: make(chan error, 1)}
	copyDone, contextSeen := make(chan struct{}), make(chan struct{})
	var onCloseCalls atomic.Int32
	var copyBytes atomic.Int64
	var contextObservedSinceActivity int64 // Published by closing contextSeen.
	var workerStarted bool
	t.Cleanup(func() {
		log.event("cleanup_start", nil)
		// Force-close the local fake even if the dependency unexpectedly fails to
		// forward Close. Both blocked workers are joined before the test returns.
		raw.Close()
		wrapped.Close()
		sink.Close()
		cancelParent()
		if workerStarted {
			// Attempt both joins even when one fails; do not Fatal out of cleanup.
			copyJoined := xudpObservationJoin(copyDone)
			contextJoined := xudpObservationJoin(contextSeen)
			if !copyJoined || !contextJoined {
				t.Errorf("cleanup did not join within %s each: copy=%t context=%t", xudpObservationBound, copyJoined, contextJoined)
				return
			}
		}
		log.event("cleanup_joined", map[string]any{"source_closed": raw.closedCount.Load(),
			"sink_closed": sink.closedCount.Load(), "on_close_calls": onCloseCalls.Load(), "workers_started": workerStarted})
	})
	if supported {
		if _, ok := wrapped.(*canceler.TimeoutPacketConn); !ok { t.Fatalf("wrong wrapper %T", wrapped) }
		log.event("wrapper_selected", map[string]any{"wrapper": "TimeoutPacketConn"})
	} else {
		if _, ok := wrapped.(*canceler.TimerPacketConn); !ok { t.Fatalf("wrong wrapper %T", wrapped) }
		log.event("wrapper_selected", map[string]any{"wrapper": "TimerPacketConn"})
	}
	// One successful local write starts the idle period through the real wrapper.
	// No write/probe is performed after this, so nothing refreshes the timer.
	packet := buf.NewPacket()
	packet.Write([]byte("last-activity"))
	activityStarted := time.Now()
	if err := wrapped.WritePacket(packet, M.ParseSocksaddr("127.0.0.3:12345")); err != nil { t.Fatal(err) }
	if ctx.Err() != nil { t.Fatal("context expired before observation started") }
	log.event("last_activity", map[string]any{"source_writes": raw.writes.Load(),
		"started_elapsed_ns": activityStarted.Sub(log.start).Nanoseconds()})
	workerStarted = true
	go func() {
		defer close(contextSeen)
		<-ctx.Done()
		contextObservedSinceActivity = time.Since(activityStarted).Nanoseconds()
		log.event("context_done", map[string]any{"error": xudpObservationError(ctx.Err()),
			"cause": xudpObservationError(context.Cause(ctx)), "since_last_activity_ns": contextObservedSinceActivity})
	}()
	go func() {
		defer close(copyDone)
		n, err := bufio.CopyPacket(sink, reader)
		copyBytes.Store(n)
		log.event("copy_packet_return", map[string]any{"bytes": n, "error": xudpObservationError(err)})
		// This callback is a harness witness for the route's after-Copy ordering,
		// NOT an invocation of ConnectionManager or an actual mux callback.
		onCloseCalls.Add(1)
		log.event("harness_on_close", map[string]any{"error": xudpObservationError(err)})
		reader.Close()
		sink.Close()
	}()
	xudpObservationWait(t, raw.readStarted, "underlying ReadPacket entry")
	if raw.readEnteredCanceled.Load() { t.Fatal("context already canceled when underlying ReadPacket began; observation premise absent") }
	xudpObservationWait(t, contextSeen, "idle context cancellation")
	if contextObservedSinceActivity < xudpObservationIdle.Nanoseconds() { t.Fatal("context canceled before the last activity could be idle for the configured duration") }
	if !errors.Is(context.Cause(ctx), os.ErrDeadlineExceeded) { t.Fatalf("wrong cancel cause: %v", context.Cause(ctx)) }
	if parent.Err() != nil { t.Fatal("child idle cancellation propagated to parent") }
	if supported {
		xudpObservationWait(t, copyDone, "deadline copy completion")
		if reader.returns.Load() != 1 || onCloseCalls.Load() != 1 || raw.closedCount.Load() != 1 || sink.closedCount.Load() != 1 {
			t.Fatal("deadline path did not return and perform after-copy closure exactly once")
		}
		select {
		case err := <-reader.result:
			if !errors.Is(err, os.ErrDeadlineExceeded) { t.Fatalf("deadline read result: %v", err) }
		default: t.Fatal("completed copy omitted its ReadPacket result")
		}
	} else {
		hold := time.NewTimer(xudpObservationHold)
		select {
		case <-copyDone:
			hold.Stop()
			t.Fatal("timer cancellation unexpectedly released CopyPacket; hypothesis changed")
		case <-hold.C:
		}
		if reader.returns.Load() != 0 || onCloseCalls.Load() != 0 || raw.closedCount.Load() != 0 || sink.closedCount.Load() != 0 {
			t.Fatal("timer path unexpectedly returned or closed before explicit cleanup")
		}
	}
	if raw.writes.Load() != 1 || sink.writes.Load() != 0 || copyBytes.Load() != 0 { t.Fatal("unexpected idle traffic") }
	log.event("observation_snapshot", map[string]any{"context_done": ctx.Err() != nil,
		"parent_context_done": parent.Err() != nil, "read_returns": reader.returns.Load(),
		"on_close_calls": onCloseCalls.Load(), "source_closed": raw.closedCount.Load(),
		"sink_closed": sink.closedCount.Load(), "source_writes": raw.writes.Load(), "sink_writes": sink.writes.Load(),
		"copy_bytes": copyBytes.Load(), "idle_gate_qualified": false})
}
