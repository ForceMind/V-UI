package dialer

import (
	"context"
	"errors"
	"net"
	"testing"
	"time"

	"github.com/sagernet/sing-box/common/tls"
	C "github.com/sagernet/sing-box/constant"
	"github.com/sagernet/sing-box/log"
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

func TestOwnerDirectTCPAndCanceledDial(t *testing.T) {
	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil { t.Fatal(err) }
	defer listener.Close()
	d, err := NewDefault(context.Background(), option.DialerOptions{})
	if err != nil { t.Fatal(err) }
	address := M.SocksaddrFromNet(listener.Addr())
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	conn, err := d.DialContext(ctx, "tcp", address)
	if err != nil { t.Fatal(err) }
	if _, ok := conn.(*net.TCPConn); !ok { conn.Close(); t.Fatal("unexpected direct wrapper") }
	conn.Close()
	config, err := tls.NewSTDClient(context.Background(), log.NewNOPFactory().Logger(), "localhost", option.OutboundTLSOptions{Enabled: true})
	if err != nil { t.Fatal(err) }
	cancel()
	attached := false
	_, _, err = DialPacketSessionTLS(ctx, d, address, config, func(func() error) error { attached = true; return nil })
	if !errors.Is(err, context.Canceled) || attached { t.Fatalf("cancelled raw dial attached: %v %v", attached, err) }
}
