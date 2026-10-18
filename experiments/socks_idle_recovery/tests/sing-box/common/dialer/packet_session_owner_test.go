package dialer

import (
	"context"
	"testing"
	"time"

	C "github.com/sagernet/sing-box/constant"
	"github.com/sagernet/sing-box/option"
	M "github.com/sagernet/sing/common/metadata"
)

func TestOwnerEffectiveDialPolicy(t *testing.T) {
	cases := []struct { name string; mutate func(*DefaultDialer) }{
		{"tfo", func(d *DefaultDialer) { d.dialer4.DisableTFO = false }},
		{"multipath", func(d *DefaultDialer) { d.dialer4.Dialer.SetMultipathTCP(true) }},
		{"namespace", func(d *DefaultDialer) { d.netns = "unknown" }},
		{"inherited_strategy", func(d *DefaultDialer) { v := C.NetworkStrategyDefault; d.networkStrategy = &v }},
		{"default_strategy", func(d *DefaultDialer) { d.defaultNetworkStrategy = true }},
		{"fallback_delay", func(d *DefaultDialer) { d.networkFallbackDelay = time.Millisecond }},
	}
	for _, tc := range cases { t.Run(tc.name, func(t *testing.T) {
		d, err := NewDefault(context.Background(), option.DialerOptions{})
		if err != nil { t.Fatal(err) }
		if err := ValidatePacketSessionDialer(d, M.ParseSocksaddr("127.0.0.1:443")); err != nil { t.Fatal(err) }
		tc.mutate(d)
		if ValidatePacketSessionDialer(d, M.ParseSocksaddr("127.0.0.1:443")) == nil { t.Fatal("unsupported effective policy admitted") }
	}) }
}

func TestOwnerTLSOptionPredicate(t *testing.T) {
	cases := []struct { name string; mutate func(*option.OutboundTLSOptions) }{
		{"insecure", func(o *option.OutboundTLSOptions) { o.Insecure = true }},
		{"utls", func(o *option.OutboundTLSOptions) { o.UTLS = &option.OutboundUTLSOptions{} }},
		{"reality", func(o *option.OutboundTLSOptions) { o.Reality = &option.OutboundRealityOptions{} }},
		{"kernel", func(o *option.OutboundTLSOptions) { o.KernelRx = true }},
		{"fragment", func(o *option.OutboundTLSOptions) { o.FragmentFallbackDelay = 1 }},
		{"spoof", func(o *option.OutboundTLSOptions) { o.SpoofMethod = "wrong-ack" }},
		{"engine", func(o *option.OutboundTLSOptions) { o.Engine = "apple" }},
	}
	for _, tc := range cases { t.Run(tc.name, func(t *testing.T) {
		o := option.OutboundTLSOptions{Enabled: true}
		if !PacketSessionTLSOptionsSupported(&o) { t.Fatal("ordinary TLS refused") }
		tc.mutate(&o)
		if PacketSessionTLSOptionsSupported(&o) { t.Fatal("unsupported TLS admitted") }
	}) }
}
