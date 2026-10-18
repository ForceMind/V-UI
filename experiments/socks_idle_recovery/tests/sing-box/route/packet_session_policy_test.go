package route

import (
	"context"
	"net"
	"net/netip"
	"sync/atomic"
	"testing"

	"github.com/sagernet/sing-box/adapter"
	C "github.com/sagernet/sing-box/constant"
	"github.com/sagernet/sing-box/log"
	"github.com/sagernet/sing-box/option"
	R "github.com/sagernet/sing-box/route/rule"
	"github.com/sagernet/sing/common/buf"
	"github.com/sagernet/sing/common/json/badoption"
	M "github.com/sagernet/sing/common/metadata"
	N "github.com/sagernet/sing/common/network"
)

type ownerPolicyDNS struct { adapter.DNSRouter }
func (ownerPolicyDNS) LookupReverseMapping(netip.Addr) (string, bool) { return "", false }
type ownerPolicyTransports struct { adapter.DNSTransportManager }
func (ownerPolicyTransports) FakeIP() adapter.FakeIPTransport { return nil }

type ownerPolicyOutbounds struct { adapter.OutboundManager; selected adapter.Outbound; selections atomic.Int32 }
func (m *ownerPolicyOutbounds) Outbound(string) (adapter.Outbound, bool) { m.selections.Add(1); return m.selected, m.selected != nil }
func (m *ownerPolicyOutbounds) Default() adapter.Outbound { m.selections.Add(1); panic("unexpected DIRECT/default fallback") }

type ownerPolicyChild struct { N.PacketConn; ownerTestLifecycle; pending *buf.Buffer; closed, reads, writes atomic.Int32 }
func (p *ownerPolicyChild) ReadPacket(*buf.Buffer) (M.Socksaddr, error) { p.reads.Add(1); panic("policy consumed first packet") }
func (p *ownerPolicyChild) WritePacket(*buf.Buffer, M.Socksaddr) error { p.writes.Add(1); panic("policy delivered target packet") }
func (p *ownerPolicyChild) Close() error { if p.closed.Add(1) == 1 { p.pending.Release(); p.pending = nil }; return nil }
func (p *ownerPolicyChild) LocalAddr() net.Addr { return &net.UDPAddr{IP: net.IPv4(127,0,0,1), Port: 10000} }

func ownerPolicyRouter(t *testing.T, action option.RuleAction, outbounds adapter.OutboundManager, connection adapter.ConnectionManager) *Router {
	t.Helper()
	logger := log.NewNOPFactory().Logger()
	rule, err := R.NewRule(context.Background(), logger, option.Rule{Type: C.RuleTypeDefault, DefaultOptions: option.DefaultRule{RawDefaultRule: option.RawDefaultRule{Network: []string{N.NetworkUDP}}, RuleAction: action}}, false)
	if err != nil { t.Fatal(err) }
	return &Router{ctx: context.Background(), logger: logger, rules: []adapter.Rule{rule}, outbound: outbounds, dns: ownerPolicyDNS{}, dnsTransport: ownerPolicyTransports{}, connection: connection}
}

func ownerRunActualPolicyReject(t *testing.T, action option.RuleAction) {
	t.Helper()
	outbounds := &ownerPolicyOutbounds{}
	router := ownerPolicyRouter(t, action, outbounds, nil)
	child := &ownerPolicyChild{pending: buf.NewSize(64)}
	defer child.pending.Release()
	target := M.ParseSocksaddr("127.0.0.1:9000")
	ctx := adapter.WithPacketSessionPolicy(context.Background(), adapter.PacketSessionPolicy{Outbound: "private", Target: target, Lifecycle: child})
	calls := 0
	router.RoutePacketConnectionEx(ctx, child, adapter.InboundContext{InboundType: C.TypeMixed, Destination: target}, func(err error) {
		calls++
		if err == nil || err == N.ErrPacketSessionEnded { t.Error("local policy refusal misclassified as clean End") }
	})
	if calls != 1 || child.closed.Load() != 1 || child.pending != nil || child.reads.Load() != 0 || child.writes.Load() != 0 || outbounds.selections.Load() != 0 { t.Fatal("actual policy refusal did not return cleanly before packet/dial access") }
	if child.cause == nil { t.Fatal("retirement cause missing") }
}

func TestOwnerActualRouterLocalReject(t *testing.T) { ownerRunActualPolicyReject(t, option.RuleAction{Action: C.RuleActionTypeReject}) }
func TestOwnerActualRouterUnsupportedActions(t *testing.T) {
	cases := []struct { name string; action option.RuleAction }{
		{"sniff", option.RuleAction{Action: C.RuleActionTypeSniff}}, {"resolve", option.RuleAction{Action: C.RuleActionTypeResolve}},
		{"hijack", option.RuleAction{Action: C.RuleActionTypeHijackDNS}},
		{"udp_timeout", option.RuleAction{Action: C.RuleActionTypeRouteOptions, RouteOptionsOptions: option.RouteOptionsActionOptions{UDPTimeout: badoption.Duration(1000000000)}}},
		{"domain_unmapping", option.RuleAction{Action: C.RuleActionTypeRouteOptions, RouteOptionsOptions: option.RouteOptionsActionOptions{UDPDisableDomainUnmapping: true}}},
		{"target_rewrite", option.RuleAction{Action: C.RuleActionTypeRouteOptions, RouteOptionsOptions: option.RouteOptionsActionOptions{OverridePort: 1234}}},
	}
	for _, tc := range cases { t.Run(tc.name, func(t *testing.T) { ownerRunActualPolicyReject(t, tc.action) }) }
}

type ownerInvalidLeaseOutbound struct { adapter.Outbound; lease *adapter.PacketSessionLease; tag string; opens int }
func (o *ownerInvalidLeaseOutbound) Tag() string { return o.tag }
func (*ownerInvalidLeaseOutbound) ValidatePacketSession() error { return nil }
func (o *ownerInvalidLeaseOutbound) OpenPacketSession(context.Context, M.Socksaddr, func(func() error) error) (*adapter.PacketSessionLease, error) { o.opens++; return o.lease, nil }
type ownerInvalidLeasePacket struct { N.PacketConn; closed int }
func (p *ownerInvalidLeasePacket) Close() error { p.closed++; return nil }

func TestOwnerInvalidLeaseTerminal(t *testing.T) {
	for _, name := range []string{"nil_lease", "nil_packet", "nil_abort", "wrong_tag", "wrong_inbound"} { t.Run(name, func(t *testing.T) {
		target := M.ParseSocksaddr("127.0.0.1:9000")
		child := &ownerPolicyChild{pending:buf.NewSize(64)}
		defer child.pending.Release()
		packet := &ownerInvalidLeasePacket{}
		aborts := 0
		outbound := &ownerInvalidLeaseOutbound{tag:"private"}
		inbound := C.TypeMixed
		switch name {
		case "nil_packet": outbound.lease = adapter.NewPacketSessionLease(nil, func() error { aborts++; return nil }, nil)
		case "nil_abort": outbound.lease = adapter.NewPacketSessionLease(packet, nil, nil)
		case "wrong_tag": outbound.tag = "DIRECT"
		case "wrong_inbound": inbound = C.TypeDirect
		}
		policy := adapter.PacketSessionPolicy{Outbound:"private", Target:target, Lifecycle:child}
		manager := NewConnectionManager(log.NewNOPFactory().Logger())
		calls := 0
		manager.NewPacketConnection(adapter.WithPacketSessionPolicy(context.Background(), policy), outbound, child, adapter.InboundContext{InboundType:inbound, Destination:target}, func(err error) { calls++; if err == nil || err == N.ErrPacketSessionEnded { t.Error("invalid lease recovered") } })
		if calls != 1 || child.closed.Load() != 1 || child.admitted || child.pending != nil { t.Fatal("invalid lease did not reject before admission and return once") }
		if (name == "wrong_tag" || name == "wrong_inbound") && outbound.opens != 0 { t.Fatal("unsupported policy reached constructor") }
		if name == "nil_packet" && aborts != 1 { t.Fatal("partial lease transport not aborted") }
		if name == "nil_abort" && packet.closed != 1 { t.Fatal("invalid lease packet not finalized") }
	}) }
}
