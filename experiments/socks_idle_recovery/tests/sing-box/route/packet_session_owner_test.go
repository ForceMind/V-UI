package route

import (
	"errors"
	"reflect"
	"sync"
	"testing"
	"time"

	"github.com/sagernet/sing-box/adapter"
	R "github.com/sagernet/sing-box/route/rule"
	"github.com/sagernet/sing/common/buf"
	M "github.com/sagernet/sing/common/metadata"
	N "github.com/sagernet/sing/common/network"
)

type ownerTestLifecycle struct {
	mu sync.Mutex
	abort func() error
	cause error
	admitted bool
	writes int
	writeErr error
}
func (l *ownerTestLifecycle) AttachPacketSessionAbort(abort func() error) error { l.mu.Lock(); defer l.mu.Unlock(); l.abort = abort; return nil }
func (l *ownerTestLifecycle) PacketSessionAdmitted() error { l.mu.Lock(); defer l.mu.Unlock(); l.admitted = true; return nil }
func (l *ownerTestLifecycle) BeginPacketSessionRetire(err error) { l.mu.Lock(); defer l.mu.Unlock(); if l.cause == nil { l.cause = err } }
func (l *ownerTestLifecycle) PacketSessionWriteFinished(err error) { l.mu.Lock(); defer l.mu.Unlock(); l.writes++; l.writeErr = err }

type ownerCountingAllocator struct {
	base buf.Allocator
	mu sync.Mutex
	gets, puts int
	live map[*byte]int
}
func (a *ownerCountingAllocator) Get(size int) []byte {
	b := a.base.Get(size)
	if b != nil { a.mu.Lock(); a.gets++; a.live[&b[0]] = cap(b); a.mu.Unlock() }
	return b
}
func (a *ownerCountingAllocator) Put(b []byte) error {
	a.mu.Lock()
	_, exists := a.live[&b[0]]
	if exists { delete(a.live, &b[0]); a.puts++ }
	a.mu.Unlock()
	if !exists { return errors.New("double or foreign buffer release") }
	return a.base.Put(b)
}
func (a *ownerCountingAllocator) balanced() bool { a.mu.Lock(); defer a.mu.Unlock(); return len(a.live) == 0 && a.gets == a.puts }

type ownerGuardWriter struct { release bool; err error }
func (w ownerGuardWriter) WritePacket(b *buf.Buffer, _ M.Socksaddr) error { if w.release { b.Release() }; return w.err }
type ownerGuardReader struct { destination M.Socksaddr }
func (r ownerGuardReader) ReadPacket(b *buf.Buffer) (M.Socksaddr, error) { b.WriteByte(1); return r.destination, nil }

func TestOwnerUploadGuardPinnedFinalRelease(t *testing.T) {
	for _, failure := range []bool{false, true} { for _, release := range []bool{false, true} {
		name := "success"; if failure { name = "error" }; if release { name += "_lower_release" } else { name += "_lower_retains" }
		t.Run(name, func(t *testing.T) {
			original := buf.DefaultAllocator
			allocator := &ownerCountingAllocator{base: original, live: make(map[*byte]int)}
			buf.DefaultAllocator = allocator
			defer func() { buf.DefaultAllocator = original }()
			var expected error; if failure { expected = errors.New("write failed") }
			lifecycle := &ownerTestLifecycle{}
			retired := false
			target := M.ParseSocksaddr("127.0.0.1:9000")
			writer := &packetSessionWriter{writer: ownerGuardWriter{release, expected}, target: target, lifecycle: lifecycle, retire: func(err error) { retired = true; if err != expected { t.Error("wrong retirement cause") } }}
			buffer := buf.NewSize(64); buffer.WriteByte(9)
			if got := writer.WritePacket(buffer, target); got != expected { t.Fatal(got) }
			if lifecycle.writes != 1 || lifecycle.writeErr != expected || retired != failure { t.Fatal("upload completion fence mismatch") }
			if !allocator.balanced() { t.Fatal("guard leaked backing storage") }
		})
	} }
}

func TestOwnerCopyGuardsNoEscape(t *testing.T) {
	for _, guard := range []any{&packetSessionReader{}, &packetSessionWriter{}} {
		for _, name := range []string{"Upstream", "UpstreamReader", "UpstreamWriter", "ReadCachedPacket", "WaitReadPacket", "InitializeReadWaiter", "ReadFrom", "WriteTo", "CreatePacketReadWaiter", "CreatePacketBatchWriter"} {
			if _, exists := reflect.TypeOf(guard).MethodByName(name); exists { t.Fatalf("guard exposes %s", name) }
		}
	}
}

func TestOwnerGuardErrorFencedBeforeReturn(t *testing.T) {
	target := M.ParseSocksaddr("127.0.0.1:9000")
	other := M.ParseSocksaddr("127.0.0.1:9001")
	var once sync.Once
	var cause error
	retire := func(err error) { once.Do(func() { cause = err }) }
	reader := &packetSessionReader{reader: ownerGuardReader{other}, target: target, retire: retire}
	buffer := buf.NewSize(64)
	defer buffer.Release()
	if _, err := reader.ReadPacket(buffer); err != N.ErrPacketSessionLifecycle || cause != err { t.Fatal("wrong target not fenced inside reader") }
	// A later generic competing completion cannot replace a fenced error.
	done := make(chan struct{})
	go func() { defer close(done); retire(N.ErrPacketSessionEnded) }()
	select { case <-done: case <-time.After(time.Second): t.Fatal("fence caller did not return") }
	if cause != N.ErrPacketSessionLifecycle { t.Fatal("later End replaced local protocol failure") }
	writerCause := error(nil)
	writer := &packetSessionWriter{writer: ownerGuardWriter{}, target: target, retire: func(err error) { writerCause = err }}
	if err := writer.WritePacket(buf.NewSize(64), other); err != N.ErrPacketSessionLifecycle || writerCause != err { t.Fatal("writer error not fenced before return") }
}

func TestOwnerPolicyRejectsBeforePacketAccess(t *testing.T) {
	cases := []struct { name string; action adapter.RuleAction }{
		{"sniff", &R.RuleActionSniff{}}, {"resolve", &R.RuleActionResolve{}}, {"hijack", &R.RuleActionHijackDNS{}}, {"bypass", &R.RuleActionBypass{}},
		{"rewrite", &R.RuleActionRouteOptions{OverridePort: 53}}, {"udp_connect", &R.RuleActionRouteOptions{UDPConnect: true}},
		{"fallback", &R.RuleActionRouteOptions{FallbackDelay: time.Second}}, {"udp_timeout", &R.RuleActionRouteOptions{UDPTimeout: time.Second}},
		{"domain_unmapping", &R.RuleActionRouteOptions{UDPDisableDomainUnmapping: true}}, {"fragment_delay", &R.RuleActionRouteOptions{TLSFragmentFallbackDelay: time.Second}},
	}
	for _, tc := range cases { t.Run(tc.name, func(t *testing.T) { if validatePacketSessionAction(tc.action) == nil { t.Fatal("unsupported action admitted") } }) }
}
