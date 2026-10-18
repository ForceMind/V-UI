package vmess

import (
	"bytes"
	"encoding/binary"
	"errors"
	"fmt"
	"io"
	"net"
	"sync"
	"testing"

	"github.com/sagernet/sing/common/buf"
	"github.com/sagernet/sing/common/bufio"
	M "github.com/sagernet/sing/common/metadata"
	N "github.com/sagernet/sing/common/network"
)

// These tests are serial because the allocator is process-global. They may run
// under -race and -tags debug; no sleeps, skips or zero-length-read shortcuts.
type packetSessionAllocator struct {
	mu sync.Mutex
	live map[*byte]int
	gets, puts, badPuts, peakBytes, liveBytes int
}

func (a *packetSessionAllocator) Get(size int) []byte {
	if size <= 0 { return nil }
	p := make([]byte, size)
	a.mu.Lock()
	a.live[&p[0]] = size
	a.gets++
	a.liveBytes += size
	if a.liveBytes > a.peakBytes { a.peakBytes = a.liveBytes }
	a.mu.Unlock()
	return p
}

func (a *packetSessionAllocator) Put(p []byte) error {
	a.mu.Lock()
	defer a.mu.Unlock()
	if len(p) == 0 { a.badPuts++; return errors.New("empty allocator return") }
	size, ok := a.live[&p[0]]
	if !ok { a.badPuts++; return errors.New("foreign or repeated allocator return") }
	delete(a.live, &p[0])
	a.puts++
	a.liveBytes -= size
	return nil
}

func packetSessionTrack(t *testing.T) *packetSessionAllocator {
	t.Helper()
	previous := buf.DefaultAllocator
	a := &packetSessionAllocator{live: make(map[*byte]int)}
	buf.DefaultAllocator = a
	t.Cleanup(func() {
		a.mu.Lock()
		if len(a.live) != 0 || a.liveBytes != 0 || a.badPuts != 0 || a.gets != a.puts {
			t.Errorf("allocator imbalance: live=%d bytes=%d gets=%d puts=%d bad=%d", len(a.live), a.liveBytes, a.gets, a.puts, a.badPuts)
		}
		a.mu.Unlock()
		buf.DefaultAllocator = previous
	})
	return a
}

type packetSessionReadConn struct { net.Conn; reader io.Reader; closes int }
func (c *packetSessionReadConn) Read(p []byte) (int, error) { return c.reader.Read(p) }
func (c *packetSessionReadConn) Close() error { c.closes++; return nil }

func packetSessionConn(wire []byte, enabled bool) *XUDPConn {
	c := NewXUDPConn(&packetSessionReadConn{reader: bytes.NewReader(wire)}, M.ParseSocksaddr("127.0.0.3:12345"))
	if enabled { c.SetPacketSessionEndReporting() }
	return c
}

func packetSessionFrame(id uint16, status, flags byte, extra, payload []byte) []byte {
	wire := make([]byte, 6+len(extra))
	binary.BigEndian.PutUint16(wire[:2], uint16(4+len(extra)))
	binary.BigEndian.PutUint16(wire[2:4], id)
	wire[4], wire[5] = status, flags
	copy(wire[6:], extra)
	if flags&OptionData != 0 {
		var size [2]byte
		binary.BigEndian.PutUint16(size[:], uint16(len(payload)))
		wire = append(wire, size[:]...)
		wire = append(wire, payload...)
	}
	return wire
}

func packetSessionAddress(network, family byte, port uint16) []byte {
	return []byte{network, byte(port >> 8), byte(port), family, 127, 0, 0, 3}
}

func TestPacketSessionEndClassification(t *testing.T) {
	end := packetSessionFrame(0, StatusEnd, 0, nil, nil)
	t.Run("canonical_opt_in", func(t *testing.T) {
		c := packetSessionConn(end, true)
		packet := buf.With(nil)
		addr, err := c.ReadPacket(packet)
		if err != N.ErrPacketSessionEnded || addr.IsValid() || packet.Len() != 0 { t.Fatalf("canonical End: addr=%v len=%d err=%v", addr, packet.Len(), err) }
		for _, other := range []error{io.EOF, net.ErrClosed} {
			if errors.Is(err, other) { t.Fatalf("End aliases terminal %v", other) }
		}
	})
	for _, flags := range []byte{0, OptionError, OptionData, 0x80} {
		t.Run(fmt.Sprintf("legacy_flags_%02x", flags), func(t *testing.T) {
			wire := packetSessionFrame(0, StatusEnd, flags, nil, nil)
			packet := buf.With(make([]byte, 32))
			_, err := packetSessionConn(wire, false).ReadPacket(packet)
			if err != io.EOF { t.Fatalf("legacy changed: flags=%02x err=%v", flags, err) }
		})
	}
	for cut := 0; cut < len(end); cut++ {
		t.Run(fmt.Sprintf("truncated_prefix_%d", cut), func(t *testing.T) {
			packet := buf.With(make([]byte, 64))
			_, err := packetSessionConn(end[:cut], true).ReadPacket(packet)
			if err == nil || errors.Is(err, N.ErrPacketSessionEnded) || (!errors.Is(err, io.EOF) && !errors.Is(err, io.ErrUnexpectedEOF)) {
				t.Fatalf("prefix=%d misclassified: %v", cut, err)
			}
			if packet.Len() != 0 { t.Fatal("header leaked into payload") }
		})
	}
}

func TestPacketSessionInvalidFrames(t *testing.T) {
	cases := []struct { name string; wire []byte }{
		{"end_error", packetSessionFrame(0, StatusEnd, OptionError, nil, nil)},
		{"end_error_data", packetSessionFrame(0, StatusEnd, OptionError|OptionData, nil, nil)},
		{"end_data", packetSessionFrame(0, StatusEnd, OptionData, nil, []byte("x"))},
		{"end_empty_data", packetSessionFrame(0, StatusEnd, OptionData, nil, nil)},
		{"end_wrong_id", packetSessionFrame(1, StatusEnd, 0, nil, nil)},
		{"end_max_id", packetSessionFrame(65535, StatusEnd, 0, nil, nil)},
		{"end_reserved_flags", packetSessionFrame(0, StatusEnd, 0x80, nil, nil)},
		{"end_extended_header", packetSessionFrame(0, StatusEnd, 0, packetSessionAddress(NetworkUDP, 1, 12345), nil)},
		{"new", packetSessionFrame(0, StatusNew, OptionData, packetSessionAddress(NetworkUDP, 1, 12345), nil)},
		{"unknown_status", packetSessionFrame(0, 255, 0, nil, nil)},
		{"keep_wrong_id", packetSessionFrame(1, StatusKeep, OptionData, nil, []byte("x"))},
		{"keep_error", packetSessionFrame(0, StatusKeep, OptionError, nil, nil)},
		{"keep_flags", packetSessionFrame(0, StatusKeep, 4, nil, nil)},
		{"keep_tcp", packetSessionFrame(0, StatusKeep, OptionData, packetSessionAddress(NetworkTCP, 1, 12345), nil)},
		{"keep_unknown_network", packetSessionFrame(0, StatusKeep, OptionData, packetSessionAddress(255, 1, 12345), nil)},
		{"keep_domain_family", packetSessionFrame(0, StatusKeep, OptionData, packetSessionAddress(NetworkUDP, 2, 12345), nil)},
		{"keep_ipv6_family", packetSessionFrame(0, StatusKeep, OptionData, packetSessionAddress(NetworkUDP, 3, 12345), nil)},
		{"keep_unknown_family", packetSessionFrame(0, StatusKeep, OptionData, packetSessionAddress(NetworkUDP, 255, 12345), nil)},
		{"keep_zero_port", packetSessionFrame(0, StatusKeep, OptionData, packetSessionAddress(NetworkUDP, 1, 0), nil)},
		{"keepalive_wrong_id", packetSessionFrame(1, StatusKeepAlive, 0, nil, nil)},
		{"keepalive_data", packetSessionFrame(0, StatusKeepAlive, OptionData, nil, nil)},
		{"keepalive_error", packetSessionFrame(0, StatusKeepAlive, OptionError, nil, nil)},
		{"keepalive_address", packetSessionFrame(0, StatusKeepAlive, 0, packetSessionAddress(NetworkUDP, 1, 12345), nil)},
	}
	for _, length := range []uint16{0, 3, 5, 65535} {
		wire := packetSessionFrame(0, StatusEnd, 0, nil, nil)
		binary.BigEndian.PutUint16(wire[:2], length)
		cases = append(cases, struct { name string; wire []byte }{fmt.Sprintf("end_length_%d", length), wire})
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			a := packetSessionTrack(t)
			packet := buf.With(make([]byte, 64))
			addr, err := packetSessionConn(tc.wire, true).ReadPacket(packet)
			if err == nil || errors.Is(err, N.ErrPacketSessionEnded) || packet.Len() != 0 || addr.IsValid() { t.Fatalf("invalid frame accepted: addr=%v len=%d err=%v", addr, packet.Len(), err) }
			if a.gets != 0 { t.Fatal("parser allocated a pooled header") }
		})
	}
}

func TestPacketSessionData(t *testing.T) {
	for _, size := range []int{0, 1, 32768, 60000, 65497, 65535} {
		for _, addressed := range []bool{false, true} {
			t.Run(fmt.Sprintf("exact_%d_address_%t", size, addressed), func(t *testing.T) {
				payload := bytes.Repeat([]byte{0xa7}, size)
				var address []byte
				if addressed { address = packetSessionAddress(NetworkUDP, 1, 12345) }
				wire := packetSessionFrame(0, StatusKeep, OptionData, address, payload)
				wire = append(wire, packetSessionFrame(0, StatusKeep, OptionData, nil, []byte("after"))...)
				c := packetSessionConn(wire, true)
				packet := buf.With(make([]byte, size))
				addr, err := c.ReadPacket(packet)
				if err != nil || addr != c.destination || !bytes.Equal(packet.Bytes(), payload) { t.Fatalf("packet changed: size=%d addr=%v err=%v", size, addr, err) }
				packet = buf.With(make([]byte, 5))
				addr, err = c.ReadPacket(packet)
				if err != nil || addr != c.destination || string(packet.Bytes()) != "after" { t.Fatalf("following packet lost: %v", err) }
			})
		}
	}
	t.Run("addressed_then_initial_target", func(t *testing.T) {
		address := packetSessionAddress(NetworkUDP, 1, 23456)
		address[7] = 9
		wire := packetSessionFrame(0, StatusKeep, OptionData, address, []byte("first"))
		wire = append(wire, packetSessionFrame(0, StatusKeep, OptionData, nil, []byte("next"))...)
		c := packetSessionConn(wire, true)
		packet := buf.With(make([]byte, 5))
		target, err := c.ReadPacket(packet)
		if err != nil || target != M.ParseSocksaddr("127.0.0.9:23456") || string(packet.Bytes()) != "first" { t.Fatalf("addressed packet changed: %v %v", target, err) }
		packet.Reset()
		target, err = c.ReadPacket(packet)
		if err != nil || target != c.destination || string(packet.Bytes()) != "next" { t.Fatalf("implicit target no longer initial: %v %v", target, err) }
	})
	for cut := 0; cut < 14; cut++ {
		t.Run(fmt.Sprintf("addressed_header_prefix_%d", cut), func(t *testing.T) {
			wire := packetSessionFrame(0, StatusKeep, OptionData, packetSessionAddress(NetworkUDP, 1, 12345), nil)
			packet := buf.With(make([]byte, 8))
			_, err := packetSessionConn(wire[:cut], true).ReadPacket(packet)
			if err == nil || errors.Is(err, N.ErrPacketSessionEnded) || packet.Len() != 0 { t.Fatalf("partial address accepted: %v", err) }
		})
	}
	t.Run("maximum_headroom_exact_payload", func(t *testing.T) {
		payload := bytes.Repeat([]byte{0x17}, 65497)
		c := packetSessionConn(packetSessionFrame(0, StatusKeep, OptionData, nil, payload), true)
		packet := buf.With(make([]byte, 286+len(payload)+96))
		packet.Resize(286, 0)
		packet.Reserve(96)
		_, err := c.ReadPacket(packet)
		if err != nil || packet.Start() != 286 || !bytes.Equal(packet.Bytes(), payload) { t.Fatalf("headroom reduced capacity: %v", err) }
	})
	t.Run("short_buffer_no_prefix", func(t *testing.T) {
		c := packetSessionConn(packetSessionFrame(0, StatusKeep, OptionData, nil, []byte("abcd")), true)
		packet := buf.With(make([]byte, 3))
		_, err := c.ReadPacket(packet)
		if !errors.Is(err, N.ErrPacketTooLarge) || packet.Len() != 0 { t.Fatalf("short buffer delivered prefix: len=%d err=%v", packet.Len(), err) }
	})
	for _, cut := range []int{0, 1, 2, 3, 4, 5} {
		t.Run(fmt.Sprintf("truncated_payload_prefix_%d", cut), func(t *testing.T) {
			wire := packetSessionFrame(0, StatusKeep, OptionData, nil, []byte("abcd"))
			packet := buf.With(make([]byte, 8))
			packet.WriteByte(0x5a)
			_, err := packetSessionConn(wire[:6+cut], true).ReadPacket(packet)
			if err == nil || errors.Is(err, N.ErrPacketSessionEnded) || packet.Len() != 1 || packet.Byte(0) != 0x5a { t.Fatalf("partial payload exposed: len=%d err=%v", packet.Len(), err) }
		})
	}
	for _, tc := range []struct { name, target string }{
		{"domain", "example.com:12345"},
		{"ipv6", "[::1]:12345"},
		{"zero_port", "127.0.0.3:0"},
	} {
		t.Run("invalid_initial_"+tc.name, func(t *testing.T) {
			c := packetSessionConn(packetSessionFrame(0, StatusKeep, OptionData, nil, nil), true)
			c.destination = M.ParseSocksaddr(tc.target)
			_, err := c.ReadPacket(buf.With(nil))
			if err == nil || errors.Is(err, N.ErrPacketSessionEnded) { t.Fatalf("invalid initial target accepted: %v", err) }
		})
	}
}

func TestPacketSessionControlLoop(t *testing.T) {
	for _, count := range []int{1, 100000} {
		t.Run(fmt.Sprintf("controls_%d", count), func(t *testing.T) {
			a := packetSessionTrack(t)
			var wire []byte
			for index := 0; index < count; index++ {
				wire = append(wire, packetSessionFrame(0, StatusKeepAlive, 0, nil, nil)...)
				wire = append(wire, packetSessionFrame(0, StatusKeep, 0, nil, nil)...)
				wire = append(wire, packetSessionFrame(0, StatusKeep, 0, packetSessionAddress(NetworkUDP, 1, 12345), nil)...)
			}
			wire = append(wire, packetSessionFrame(0, StatusKeep, OptionData, nil, nil)...)
			wire = append(wire, packetSessionFrame(0, StatusKeep, OptionData, nil, []byte("next"))...)
			c := packetSessionConn(wire, true)
			packet := buf.With(make([]byte, 4))
			addr, err := c.ReadPacket(packet)
			if err != nil || addr != c.destination || packet.Len() != 0 { t.Fatalf("controls became datagrams: %v", err) }
			addr, err = c.ReadPacket(packet)
			if err != nil || addr != c.destination || string(packet.Bytes()) != "next" { t.Fatalf("zero/control boundary changed: %v", err) }
			if a.gets != 0 { t.Fatalf("control loop pooled allocations=%d", a.gets) }
		})
	}
	t.Run("control_then_end", func(t *testing.T) {
		wire := packetSessionFrame(0, StatusKeepAlive, 0, nil, nil)
		wire = append(wire, packetSessionFrame(0, StatusEnd, 0, nil, nil)...)
		_, err := packetSessionConn(wire, true).ReadPacket(buf.With(nil))
		if err != N.ErrPacketSessionEnded { t.Fatalf("control/End boundary: %v", err) }
	})
}

type packetSessionReaderSpy struct { releases int }
func (*packetSessionReaderSpy) Read([]byte) (int, error) { return 0, io.EOF }
func (*packetSessionReaderSpy) ReadBuffer(*buf.Buffer) error { return io.EOF }
func (s *packetSessionReaderSpy) ReleaseCacheAfterJoin() { s.releases++ }

func TestPacketSessionClientFinalizer(t *testing.T) {
	t.Run("known_cache_idempotent_non_closer", func(t *testing.T) {
		a := packetSessionTrack(t)
		chunk := bufio.NewChunkReader(bytes.NewReader([]byte("retained")), 32)
		if _, ok := any(chunk).(io.Closer); ok { t.Fatal("ChunkReader acquired global io.Closer") }
		transport := &packetSessionReadConn{}
		c := &rawClientConn{Conn: transport, reader: chunk}
		var first [1]byte
		if n, err := c.reader.Read(first[:]); n != 1 || err != nil { t.Fatalf("cache fixture: n=%d err=%v", n, err) }
		if a.gets != 1 || len(a.live) != 1 { t.Fatal("fixture did not retain exactly one cache") }
		if err := c.Close(); err != nil { t.Fatal(err) }
		if transport.closes != 1 || len(a.live) != 1 { t.Fatal("legacy Close changed cache ownership") }
		c.ReleasePacketSessionReadCacheAfterJoin()
		c.ReleasePacketSessionReadCacheAfterJoin()
		if transport.closes != 1 || len(a.live) != 0 || a.puts != 1 { t.Fatal("post-join finalizer did not release exactly once") }
	})
	t.Run("uninitialized_reader", func(t *testing.T) {
		c := &rawClientConn{}
		c.ReleasePacketSessionReadCacheAfterJoin()
		c.ReleasePacketSessionReadCacheAfterJoin()
	})
	t.Run("unknown_reader_not_walked", func(t *testing.T) {
		spy := &packetSessionReaderSpy{}
		c := &rawClientConn{reader: spy}
		c.ReleasePacketSessionReadCacheAfterJoin()
		if spy.releases != 0 { t.Fatal("finalizer walked an unrecognized reader") }
	})
}
