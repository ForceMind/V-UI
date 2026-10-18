package adapter

import (
	"context"
	"sync/atomic"
	"testing"
	"time"

	N "github.com/sagernet/sing/common/network"
)

type ownerLeasePacket struct { N.PacketConn; closed atomic.Int32 }
func (p *ownerLeasePacket) Close() error { p.closed.Add(1); return nil }

func TestOwnerLeaseAbortFinalizeOnce(t *testing.T) {
	packet := &ownerLeasePacket{}
	var aborts, caches atomic.Int32
	lease := NewPacketSessionLease(packet, func() error { aborts.Add(1); return nil }, func() {
		if aborts.Load() != 1 || packet.closed.Load() != 1 { t.Error("cache finalized before abort/protocol close") }
		caches.Add(1)
	})
	var returned []chan struct{}
	for range 32 { returned = append(returned, make(chan struct{})) }
	t.Cleanup(func() {
		deadline := time.NewTimer(3*time.Second); defer deadline.Stop()
		for _, done := range returned { select { case <-done: case <-deadline.C: panic("lease callers failed bounded join") } }
	})
	for _, done := range returned { go func(done chan struct{}) { defer close(done); lease.AbortTransport(); lease.Finalize() }(done) }
	deadline := time.NewTimer(3*time.Second); defer deadline.Stop()
	for _, done := range returned { select { case <-done: case <-deadline.C: t.Fatal("lease caller did not return") } }
	if aborts.Load() != 1 || caches.Load() != 1 || packet.closed.Load() != 1 { t.Fatal("lease cleanup was not exactly once") }
}

func TestOwnerPolicyCopiedValue(t *testing.T) {
	input := PacketSessionPolicy{Outbound: "original"}
	ctx := WithPacketSessionPolicy(context.Background(), input)
	input.Outbound = "changed"
	got, ok := PacketSessionPolicyFromContext(ctx)
	if !ok || got.Outbound != "original" { t.Fatal("policy aliases caller value") }
	got.Outbound = "again"
	stored, _ := PacketSessionPolicyFromContext(ctx)
	if stored.Outbound != "original" { t.Fatal("policy aliases returned value") }
	if _, ok := PacketSessionPolicyFromContext(context.Background()); ok { t.Fatal("unmarked context opted in") }
}
