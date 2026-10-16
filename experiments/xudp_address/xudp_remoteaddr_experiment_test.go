// Isolated D2 experiment tests. Copy into the pinned sing-vmess checkout only.
// This file is not used by installed V-UI. Running it is a separate CI gate.
package vmess

import (
	"bytes"
	"context"
	"encoding/binary"
	"errors"
	"io"
	"net"
	"net/netip"
	"reflect"
	"testing"
	"time"

	"github.com/sagernet/sing/common/buf"
	"github.com/sagernet/sing/common/bufio"
	M "github.com/sagernet/sing/common/metadata"
	N "github.com/sagernet/sing/common/network"
)

type xudpExperimentConn struct {
	input *bytes.Reader
	output bytes.Buffer
	peer net.Addr
	local net.Addr
	closed bool
	deadline time.Time
}

func xudpExperimentTransport(input []byte) *xudpExperimentConn {
	return &xudpExperimentConn{
		input: bytes.NewReader(input),
		peer: &net.TCPAddr{IP: net.ParseIP("127.0.0.2"), Port: 24443},
		local: &net.TCPAddr{IP: net.ParseIP("127.0.0.1"), Port: 30000},
	}
}

func (c *xudpExperimentConn) Read(p []byte) (int, error) { return c.input.Read(p) }
func (c *xudpExperimentConn) Write(p []byte) (int, error) { return c.output.Write(p) }
func (c *xudpExperimentConn) Close() error { c.closed = true; return nil }
func (c *xudpExperimentConn) LocalAddr() net.Addr { return c.local }
func (c *xudpExperimentConn) RemoteAddr() net.Addr { return c.peer }
func (c *xudpExperimentConn) SetDeadline(t time.Time) error { c.deadline = t; return nil }
func (c *xudpExperimentConn) SetReadDeadline(t time.Time) error { c.deadline = t; return nil }
func (c *xudpExperimentConn) SetWriteDeadline(t time.Time) error { c.deadline = t; return nil }

// This encodes a server StatusKeep reply. It never opens a network connection.
func xudpExperimentReply(t *testing.T, source M.Socksaddr, payload []byte) []byte {
	t.Helper()
	var address bytes.Buffer
	if err := AddressSerializer.WriteAddrPort(&address, source); err != nil { t.Fatal(err) }
	var frame bytes.Buffer
	if err := binary.Write(&frame, binary.BigEndian, uint16(5+address.Len())); err != nil { t.Fatal(err) }
	frame.Write([]byte{0, 0, StatusKeep, OptionData, NetworkUDP})
	frame.Write(address.Bytes())
	if err := binary.Write(&frame, binary.BigEndian, uint16(len(payload))); err != nil { t.Fatal(err) }
	frame.Write(payload)
	return frame.Bytes()
}

func xudpExperimentReadWritten(t *testing.T, input *bytes.Reader, status byte, destination M.Socksaddr, payload []byte) {
	t.Helper()
	var length uint16
	if err := binary.Read(input, binary.BigEndian, &length); err != nil { t.Fatal(err) }
	header := make([]byte, 5)
	if _, err := io.ReadFull(input, header); err != nil { t.Fatal(err) }
	if !bytes.Equal(header, []byte{0, 0, status, OptionData, NetworkUDP}) { t.Fatalf("header: %x", header) }
	before := input.Len()
	actual, err := AddressSerializer.ReadAddrPort(input)
	if err != nil { t.Fatal(err) }
	if int(length) != 5+before-input.Len() { t.Fatalf("frame length %d", length) }
	if actual != destination { t.Fatalf("wire destination: got %v, want %v", actual, destination) }
	if err := binary.Read(input, binary.BigEndian, &length); err != nil { t.Fatal(err) }
	got := make([]byte, int(length))
	if _, err := io.ReadFull(input, got); err != nil { t.Fatal(err) }
	if !bytes.Equal(got, payload) { t.Fatalf("wire payload: got %x, want %x", got, payload) }
}

func TestExperimentXUDPRemoteAddrCapturesInitialLogicalDestination(t *testing.T) {
	for _, text := range []string{"127.0.0.3:12345", "[2001:db8::3]:12345", "app.example.test:12345", "a_b.example.test:12345"} {
		t.Run(text, func(t *testing.T) {
			want := M.ParseSocksaddr(text)
			input := want
			transport := xudpExperimentTransport(nil)
			conn := NewXUDPConn(transport, input)
			input.Port = 1 // Constructor must retain a value, not caller state.
			got := conn.RemoteAddr()
			if M.SocksaddrFromNet(got) != want { t.Fatalf("D2_EXPECTED_REMOTE_ADDR got=%v want=%v", got, want) }
			if want.IsIP() {
				addr, ok := got.(*net.UDPAddr)
				if !ok { t.Fatalf("literal remote type %T", got) }
				addr.Port = 2
				if len(addr.IP) > 0 { addr.IP[0] ^= 0xff }
				if M.SocksaddrFromNet(conn.RemoteAddr()) != want { t.Fatal("returned address mutation changed destination") }
			} else {
				if _, ok := got.(M.Socksaddr); !ok { t.Fatalf("domain erased: %T %v", got, got) }
				if M.AddrFromNet(got).IsValid() { t.Fatal("unresolved domain was represented as a selected IP") }
			}
			if _, err := conn.WriteTo([]byte("unchanged"), M.ParseSocksaddr("127.0.0.7:54321")); err != nil { t.Fatal(err) }
			if M.SocksaddrFromNet(conn.RemoteAddr()) != want { t.Fatal("per-packet destination changed logical remote") }
			if conn.LocalAddr() != transport.LocalAddr() || conn.Conn.RemoteAddr() != transport.RemoteAddr() || conn.Upstream() != transport {
				t.Fatal("transport identity/access changed")
			}
			deadline := time.Unix(100, 0)
			if err := conn.SetDeadline(deadline); err != nil || transport.deadline != deadline { t.Fatal("deadline forwarding changed") }
			if err := conn.Close(); err != nil || !transport.closed { t.Fatal("close forwarding changed") }
		})
	}
}

func TestExperimentXUDPConnectedWrapperExactReplyTupleAndPayload(t *testing.T) {
	destination := M.ParseSocksaddr("127.0.0.3:12345")
	payloads := [][]byte{[]byte("x"), bytes.Repeat([]byte{0, 1, 255}, 400)}
	for _, packetAPI := range []bool{false, true} {
		t.Run(map[bool]string{false:"ReadFrom", true:"ReadPacket"}[packetAPI], func(t *testing.T) {
			var input []byte
			for _, p := range payloads { input = append(input, xudpExperimentReply(t, destination, p)...)}
			transport := xudpExperimentTransport(input)
			if M.SocksaddrFromNet(transport.RemoteAddr()).Port == destination.Port { t.Fatal("port control is invalid") }
			conn := bufio.NewUnbindPacketConn(NewXUDPConn(transport, destination))
			for _, want := range payloads {
				var got []byte
				var address M.Socksaddr
				if packetAPI {
					buffer := buf.NewPacket()
					value, err := conn.ReadPacket(buffer)
					if err != nil { buffer.Release(); t.Fatal(err) }
					address = value
					got = append([]byte(nil), buffer.Bytes()...)
					buffer.Release()
				} else {
					buffer := make([]byte, 2048)
					n, value, err := conn.ReadFrom(buffer)
					if err != nil { t.Fatal(err) }
					address, got = M.SocksaddrFromNet(value), buffer[:n]
				}
				if address != destination { t.Fatalf("D2_EXPECTED_REPLY_TUPLE got=%v want=%v", address, destination) }
				if !bytes.Equal(got, want) { t.Fatalf("reply payload: got %x, want %x", got, want) }
			}
		})
	}
}

func TestExperimentXUDPConnectedWritesBindChangedFramesToOldTargetNotReject(t *testing.T) {
	destination := M.ParseSocksaddr("127.0.0.3:12345")
	changes := []M.Socksaddr{
		M.ParseSocksaddr("127.0.0.7:54321"), M.ParseSocksaddr("127.0.0.3:53"),
		M.ParseSocksaddr("127.0.0.3:443"), M.ParseSocksaddr("[::1]:12345"),
		M.ParseSocksaddr("metadata-alias.example.test:12345"),
	}
	for _, packetAPI := range []bool{false, true} {
		t.Run(map[bool]string{false:"WriteTo", true:"WritePacket"}[packetAPI], func(t *testing.T) {
			transport := xudpExperimentTransport(nil)
			conn := bufio.NewUnbindPacketConn(NewXUDPConn(transport, destination))
			for index, changed := range changes {
				payload := []byte{0x71, byte(index)}
				if packetAPI {
					if err := conn.WritePacket(buf.As(payload), changed); err != nil { t.Fatal(err) }
				} else {
					n, err := conn.WriteTo(payload, changed)
					if err != nil || n != len(payload) { t.Fatalf("write = %d, %v", n, err) }
				}
			}
			wire := bytes.NewReader(transport.output.Bytes())
			for index := range changes {
				status := byte(StatusKeep)
				if index == 0 { status = StatusNew }
				xudpExperimentReadWritten(t, wire, status, destination, []byte{0x71, byte(index)})
			}
			if wire.Len() != 0 { t.Fatalf("trailing wire data: %d", wire.Len()) }
		})
	}
}

func TestExperimentXUDPUnboundPerPacketDestinationAndSourceUnchanged(t *testing.T) {
	initial := M.ParseSocksaddr("127.0.0.3:12345")
	changed := M.ParseSocksaddr("127.0.0.7:54321")
	domain := M.ParseSocksaddr("reply.example.test:12346")
	payload := []byte("abc")
	transport := xudpExperimentTransport(append(xudpExperimentReply(t, changed, payload), xudpExperimentReply(t, domain, payload)...))
	conn := NewXUDPConn(transport, initial)
	// Exact known first wire fixture, not just encode/decode with the same serializer.
	if _, err := conn.Write(payload); err != nil { t.Fatal(err) }
	wantWire := []byte{0, 12, 0, 0, 1, 1, 2, 0x30, 0x39, 1, 127, 0, 0, 3, 0, 3, 'a', 'b', 'c'}
	if !bytes.Equal(transport.output.Bytes(), wantWire) { t.Fatalf("wire changed: %x", transport.output.Bytes()) }
	if _, err := conn.WriteTo(payload, changed); err != nil { t.Fatal(err) }
	packet := buf.NewPacket()
	packet.Resize(conn.FrontHeadroom(), 0)
	if _, err := packet.Write(payload); err != nil { packet.Release(); t.Fatal(err) }
	if err := conn.WritePacket(packet, domain); err != nil { t.Fatal(err) }
	wire := bytes.NewReader(transport.output.Bytes())
	xudpExperimentReadWritten(t, wire, StatusNew, initial, payload)
	xudpExperimentReadWritten(t, wire, StatusKeep, changed, payload)
	xudpExperimentReadWritten(t, wire, StatusKeep, domain, payload)
	if wire.Len() != 0 { t.Fatalf("trailing wire data: %d", wire.Len()) }
	for _, want := range []M.Socksaddr{changed, domain} {
		buffer := make([]byte, 2048)
		n, addr, err := conn.ReadFrom(buffer)
		if err != nil { t.Fatal(err) }
		if M.SocksaddrFromNet(addr) != want || !bytes.Equal(buffer[:n], payload) { t.Fatalf("unbound read: %v %x", addr, buffer[:n]) }
	}
}

type xudpExperimentDialer struct {
	t *testing.T
	calls []M.Socksaddr
	selected M.Socksaddr
}

func (d *xudpExperimentDialer) DialContext(_ context.Context, network string, destination M.Socksaddr) (net.Conn, error) {
	d.calls = append(d.calls, destination)
	if network != N.NetworkUDP || !destination.IsIP() || destination.Fqdn != "" { d.t.Fatalf("not an already-selected UDP IP: %s %v", network, destination) }
	if destination != d.selected { return nil, errors.New("controlled first-candidate failure") }
	return NewXUDPConn(xudpExperimentTransport(xudpExperimentReply(d.t, destination, []byte("resolved"))), destination), nil
}
func (d *xudpExperimentDialer) ListenPacket(context.Context, M.Socksaddr) (net.PacketConn, error) { panic("unexpected ListenPacket") }

func TestExperimentXUDPSerialResolvedAddressIsActualSelectedAddress(t *testing.T) {
	original := M.ParseSocksaddr("app.example.test:12345")
	first := M.ParseSocksaddr("192.0.2.10:12345")
	selected := M.ParseSocksaddr("192.0.2.20:12345")
	dialer := &xudpExperimentDialer{t:t, selected:selected}
	conn, err := N.DialSerial(context.Background(), dialer, N.NetworkUDP, original, []netip.Addr{first.Addr, selected.Addr})
	if err != nil { t.Fatal(err) }
	defer conn.Close()
	if !reflect.DeepEqual(dialer.calls, []M.Socksaddr{first, selected}) { t.Fatalf("dial attempts: %v", dialer.calls) }
	if M.SocksaddrFromNet(conn.RemoteAddr()) != selected { t.Fatalf("D2_EXPECTED_SELECTED_ADDR got=%v want=%v", conn.RemoteAddr(), selected) }
	// Reproduce route's mapping using the selected logical IP, not original FQDN or first candidate.
	packetConn := bufio.NewNATPacketConn(bufio.NewUnbindPacketConn(conn), selected, original)
	packet := buf.NewPacket()
	defer packet.Release()
	addr, err := packetConn.ReadPacket(packet)
	if err != nil { t.Fatal(err) }
	if addr != original || !bytes.Equal(packet.Bytes(), []byte("resolved")) { t.Fatalf("domain unmapping: %v %q", addr, packet.Bytes()) }
}
