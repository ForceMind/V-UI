//go:build linux || netbsd || darwin

package bufio

import (
	"bytes"
	"errors"
	"net"
	"sync"
	"syscall"
	"testing"

	"github.com/sagernet/sing/common/buf"
	M "github.com/sagernet/sing/common/metadata"
	N "github.com/sagernet/sing/common/network"
)

type packetReceiveBatchRead func() ([]*buf.Buffer, []M.Socksaddr, error)
func packetReceiveBatchBuild(t *testing.T, f *packetReceiveFixture, options N.ReadWaitOptions) packetReceiveBatchRead {
	t.Helper()
	if f.connected {
		w, ok := CreateConnectedPacketBatchReadWaiter(NewUnbindPacketConn(f.receiver))
		if !ok { t.Fatal("connected native batch read waiter unavailable") }
		w.InitializeReadWaiter(options)
		return func() ([]*buf.Buffer, []M.Socksaddr, error) {
			bs, d, err := w.WaitReadConnectedPackets()
			ds := make([]M.Socksaddr, len(bs)); for i := range ds { ds[i] = d }
			if err != nil && d.IsValid() { t.Errorf("batch error exposed destination %v", d) }
			return bs, ds, err
		}
	}
	w, ok := CreatePacketBatchReadWaiter(NewPacketConn(f.receiver))
	if !ok { t.Fatal("native batch read waiter unavailable") }
	w.InitializeReadWaiter(options)
	return w.WaitReadPackets
}
func packetReceiveBatchCheck(t *testing.T, read packetReceiveBatchRead, payloads [][]byte, addr M.Socksaddr, options N.ReadWaitOptions) {
	t.Helper()
	bs, ds, err := read(); defer buf.ReleaseMulti(bs)
	if err != nil || len(bs) != len(payloads) || len(ds) != len(payloads) { t.Fatalf("batch buffers/addresses=%d/%d want=%d error=%v", len(bs), len(ds), len(payloads), err) }
	for i, b := range bs {
		if !bytes.Equal(b.Bytes(), payloads[i]) || ds[i] != addr { t.Fatalf("batch[%d] payload len=%d want=%d source=%v want=%v", i, b.Len(), len(payloads[i]), ds[i], addr) }
		if b.Start() != options.FrontHeadroom || b.FreeLen() < options.RearHeadroom { t.Fatalf("batch[%d] lost headroom", i) }
	}
}
func TestVUICompletePacketBatchMixed(t *testing.T) {
	for _, path := range []string{"ordinary", "connected"} {
		t.Run(path, func(t *testing.T) {
			a := packetReceiveTrack(t); f := packetReceivePair(t, path == "connected")
			o := N.ReadWaitOptions{MTU: 1024, ReadOverhead: 17, FrontHeadroom: 31, RearHeadroom: 33, BatchSize: 4}
			read := packetReceiveBatchBuild(t, f, o)
			// All three are queued before the syscall. Slot four is allocated
			// but never filled; it must be released together with the rejected batch.
			for _, p := range [][]byte{[]byte("small"), packetReceivePayload(1041), packetReceivePayload(1042)} { f.send(t, p) }
			bs, ds, err := read()
			if len(bs) != 0 || len(ds) != 0 || !errors.Is(err, N.ErrPacketTooLarge) {
				buf.ReleaseMulti(bs); t.Fatalf("mixed batch escaped: buffers=%d addresses=%d error=%v", len(bs), len(ds), err)
			}
			a.assertEmpty(t)
			payloads := [][]byte{packetReceivePayload(1041), nil, []byte("after-zero")}
			for _, p := range payloads { f.send(t, p) }
			packetReceiveBatchCheck(t, read, payloads, M.SocksaddrFromNet(f.sender.LocalAddr()).Unwrap(), o)
			a.assertEmpty(t)
		})
	}
}
func TestVUICompletePacketBatchDefaultBudget(t *testing.T) {
	for _, path := range []string{"ordinary", "connected"} {
		t.Run(path, func(t *testing.T) {
			a := packetReceiveTrack(t); f := packetReceivePair(t, path == "connected")
			o := N.ReadWaitOptions{FrontHeadroom: 31, RearHeadroom: 351, ReadOverhead: 382, BatchSize: 8}
			read := packetReceiveBatchBuild(t, f, o)
			payloads := [][]byte{[]byte("small"), packetReceivePayload(60000), packetReceivePayload(65507), nil}
			for _, p := range payloads { f.send(t, p) }
			packetReceiveBatchCheck(t, read, payloads, M.SocksaddrFromNet(f.sender.LocalAddr()).Unwrap(), o)
			a.assertEmpty(t)
		})
	}
}

// Observe an actual RawConn callback returning false, so close tests prove a
// receive was genuinely waiting after EAGAIN rather than merely not scheduled.
type packetReceiveObservedRaw struct { syscall.RawConn; waiting chan struct{}; once sync.Once }
func (c *packetReceiveObservedRaw) Read(fn func(uintptr) bool) error {
	return c.RawConn.Read(func(fd uintptr) bool { done := fn(fd); if !done { c.once.Do(func() { close(c.waiting) }) }; return done })
}
type packetReceiveSyscallOnly struct { raw syscall.RawConn }
func (c packetReceiveSyscallOnly) SyscallConn() (syscall.RawConn, error) { return c.raw, nil }
func TestVUICompletePacketBatchParentClose(t *testing.T) {
	for _, path := range []string{"ordinary", "connected"} {
		t.Run(path, func(t *testing.T) {
			a := packetReceiveTrack(t); f := packetReceivePair(t, path == "connected")
			raw, err := f.receiver.SyscallConn(); if err != nil { t.Fatal(err) }
			observed := &packetReceiveObservedRaw{RawConn: raw, waiting: make(chan struct{})}
			var read func() error
			if f.connected {
				w, ok := createSyscallConnectedPacketBatchReadWaiter(packetReceiveSyscallOnly{observed}, M.Socksaddr{})
				if !ok { t.Fatal("connected native waiter unavailable") }
				w.InitializeReadWaiter(N.ReadWaitOptions{BatchSize: 4})
				read = func() error { bs, _, err := w.WaitReadConnectedPackets(); buf.ReleaseMulti(bs); return err }
			} else {
				w, ok := createSyscallPacketBatchReadWaiter(packetReceiveSyscallOnly{observed})
				if !ok { t.Fatal("native waiter unavailable") }
				w.InitializeReadWaiter(N.ReadWaitOptions{BatchSize: 4})
				read = func() error { bs, _, err := w.WaitReadPackets(); buf.ReleaseMulti(bs); return err }
			}
			done := make(chan struct{}); var receiveErr error
			go func() { receiveErr = read(); close(done) }()
			t.Cleanup(func() { f.receiver.Close(); <-done })
			select { case <-observed.waiting: case <-done: t.Fatalf("receive exited without waiting: %v", receiveErr) }
			if err := f.receiver.Close(); err != nil { t.Fatal(err) }
			<-done
			if !errors.Is(receiveErr, net.ErrClosed) { t.Fatalf("parent close returned %v", receiveErr) }
			a.assertEmpty(t)
		})
	}
}
func TestVUICompletePacketBatchInvalidSize(t *testing.T) {
	packetReceiveTrack(t); f := packetReceivePair(t, false)
	w, ok := CreatePacketBatchReadWaiter(NewPacketConn(f.receiver)); if !ok { t.Fatal("native waiter unavailable") }
	defer func() { if recover() == nil { t.Fatal("batch size 65 did not panic") } }()
	w.InitializeReadWaiter(N.ReadWaitOptions{BatchSize: 65})
}

func TestVUICompletePacketSyscallParentClose(t *testing.T) {
	a := packetReceiveTrack(t); f := packetReceivePair(t, false)
	raw, err := f.receiver.SyscallConn(); if err != nil { t.Fatal(err) }
	observed := &packetReceiveObservedRaw{RawConn: raw, waiting: make(chan struct{})}
	w, ok := createSyscallPacketReadWaiter(packetReceiveSyscallOnly{observed}); if !ok { t.Fatal("native single-packet waiter unavailable") }
	w.InitializeReadWaiter(N.ReadWaitOptions{})
	done := make(chan struct{}); var receiveErr error
	go func() { b, _, err := w.WaitReadPacket(); if b != nil { b.Release() }; receiveErr = err; close(done) }()
	t.Cleanup(func() { f.receiver.Close(); <-done })
	select { case <-observed.waiting: case <-done: t.Fatalf("receive exited without waiting: %v", receiveErr) }
	if err := f.receiver.Close(); err != nil { t.Fatal(err) }
	<-done
	if !errors.Is(receiveErr, net.ErrClosed) { t.Fatalf("parent close returned %v", receiveErr) }
	a.assertEmpty(t)
}

func TestVUICompletePacketBatchAggregateBudget(t *testing.T) {
	for _, path := range []string{"ordinary", "connected"} {
		t.Run(path, func(t *testing.T) {
			a := packetReceiveTrack(t); f := packetReceivePair(t, path == "connected")
			raw, err := f.receiver.SyscallConn(); if err != nil { t.Fatal(err) }
			observed := &packetReceiveObservedRaw{RawConn: raw, waiting: make(chan struct{})}
			var initialize func(N.ReadWaitOptions) bool
			var read packetReceiveBatchRead
			if f.connected {
				w, ok := createSyscallConnectedPacketBatchReadWaiter(packetReceiveSyscallOnly{observed}, M.Socksaddr{})
				if !ok { t.Fatal("connected native waiter unavailable") }
				initialize = w.InitializeReadWaiter
				read = func() ([]*buf.Buffer, []M.Socksaddr, error) { bs, d, err := w.WaitReadConnectedPackets(); ds := make([]M.Socksaddr, len(bs)); for i := range ds { ds[i] = d }; return bs, ds, err }
			} else {
				w, ok := createSyscallPacketBatchReadWaiter(packetReceiveSyscallOnly{observed})
				if !ok { t.Fatal("native waiter unavailable") }
				initialize = w.InitializeReadWaiter; read = w.WaitReadPackets
			}
			o := N.ReadWaitOptions{FrontHeadroom: 286, RearHeadroom: 96} // BatchSize 0 must use 64.
			initialize(o)
			f.send(t, []byte("one"))
			packetReceiveBatchCheck(t, read, [][]byte{[]byte("one")}, M.SocksaddrFromNet(f.sender.LocalAddr()).Unwrap(), o)
			// The other 63 allocated slots were not returned to the caller.
			a.assertEmpty(t); a.assertPeak(t, 64*(65535+286+96), 64)
			// Reuse the same waiter with a smaller explicit limit, then fail a
			// receive. The earlier maximum must remain the aggregate peak.
			o.MTU = 1024; initialize(o)
			f.send(t, packetReceivePayload(1025))
			bs, ds, err := read()
			if len(bs) != 0 || len(ds) != 0 || !errors.Is(err, N.ErrPacketTooLarge) { buf.ReleaseMulti(bs); t.Fatalf("budget error returned buffers=%d addresses=%d err=%v", len(bs), len(ds), err) }
			a.assertEmpty(t); a.assertPeak(t, 64*(65535+286+96), 64)
			// Now the same waiter reaches a real empty socket. Close while
			// its 64 slots are pending and join before checking ownership.
			done := make(chan struct{}); var receiveErr error
			go func() { bs, _, err := read(); buf.ReleaseMulti(bs); receiveErr = err; close(done) }()
			t.Cleanup(func() { f.receiver.Close(); <-done })
			select { case <-observed.waiting: case <-done: t.Fatalf("receive exited before EAGAIN: %v", receiveErr) }
			if err := f.receiver.Close(); err != nil { t.Fatal(err) }; <-done
			if !errors.Is(receiveErr, net.ErrClosed) { t.Fatalf("parent close returned %v", receiveErr) }
			a.assertEmpty(t); a.assertPeak(t, 64*(65535+286+96), 64)
		})
	}
}
