// Extracted unchanged Count-only helpers and test body from frozen afbdba1.
package route

import (
 "net"
 "runtime"
 "sync/atomic"
 "testing"
 "time"
 "github.com/sagernet/sing-box/log"
)

// No socket I/O is needed to exercise the manager's real list, wrappers and
// synchronization. Every underlying Close re-enters Count synchronously.
type ownerCountConn struct { net.Conn; onClose func() error }
func (c *ownerCountConn) Close() error { return c.onClose() }
type ownerCountPacketConn struct { net.PacketConn; onClose func() error }
func (c *ownerCountPacketConn) Close() error { return c.onClose() }

func TestOwnerConnectionManagerConcurrentCount(t *testing.T) {
	const writers = 8
	const iterations = 256
	manager := NewConnectionManager(log.NewNOPFactory().Logger())
	var callbacks, tracked atomic.Int32
	var invalid atomic.Bool
	checkCount := func() {
		if count := manager.Count(); count < 0 || count > 2*writers { invalid.Store(true) }
	}
	onClose := func() error {
		// CloseAll and tracked Close must release access before calling this.
		checkCount()
		callbacks.Add(1)
		return nil
	}
	start, stop := make(chan struct{}), make(chan struct{})
	returned := make([]chan struct{}, writers+2)
	for i := range returned { returned[i] = make(chan struct{}) }
	// Register before launching any worker. On failure, signal stop and join
	// each worker directly under one deadline, never a detached Wait goroutine.
	t.Cleanup(func() {
		close(stop)
		deadline := time.NewTimer(10*time.Second); defer deadline.Stop()
		for _, done := range returned {
			select { case <-done: case <-deadline.C: panic("connection manager count workers failed bounded cleanup join") }
		}
		manager.CloseAll()
	})
	for worker := 0; worker < writers; worker++ {
		go func(done chan struct{}) {
			defer close(done)
			<-start
			for range iterations {
				select { case <-stop: return; default: }
				stream := manager.TrackConn(&ownerCountConn{onClose:onClose})
				packet := manager.TrackPacketConn(&ownerCountPacketConn{onClose:onClose})
				tracked.Add(2)
				checkCount()
				runtime.Gosched()
				stream.Close()
				packet.Close()
			}
		}(returned[worker])
	}
	go func() {
		defer close(returned[writers])
		<-start
		for i := 0; i < writers*iterations*4; i++ {
			select { case <-stop: return; default: }
			checkCount()
			if i%16 == 0 { runtime.Gosched() }
		}
	}()
	go func() {
		defer close(returned[writers+1])
		<-start
		for range iterations*4 {
			select { case <-stop: return; default: }
			manager.CloseAll()
			runtime.Gosched()
		}
	}()
	close(start)
	deadline := time.NewTimer(10*time.Second); defer deadline.Stop()
	for _, done := range returned {
		select { case <-done: case <-deadline.C: t.Fatal("connection manager Count/Track/CloseAll did not return") }
	}
	manager.CloseAll()
	if invalid.Load() || tracked.Load() != writers*iterations*2 || callbacks.Load() < tracked.Load() || manager.Count() != 0 {
		t.Fatalf("manager count baseline mismatch: invalid=%v tracked=%d callbacks=%d remaining=%d",invalid.Load(),tracked.Load(),callbacks.Load(),manager.Count())
	}
}
