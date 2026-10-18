package vmess

import (
	"bytes"
	"context"
	"crypto/cipher"
	"crypto/sha256"
	"encoding/binary"
	"errors"
	"fmt"
	"io"
	"net"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/sagernet/sing/common/buf"
	M "github.com/sagernet/sing/common/metadata"
	N "github.com/sagernet/sing/common/network"
)

const packetSessionTestBound = 10 * time.Second
const packetSessionTestUser = "11111111-2222-4333-8444-555555555555"

type packetSessionDispatch struct { conn N.PacketConn; target M.Socksaddr; onClose N.CloseHandlerFunc }
type packetSessionHandler struct { udp chan packetSessionDispatch; returned chan struct{}; unexpectedTCP atomic.Int32 }
func (h *packetSessionHandler) NewConnectionEx(_ context.Context, conn net.Conn, _ M.Socksaddr, _ M.Socksaddr, _ N.CloseHandlerFunc) {
	h.unexpectedTCP.Add(1)
	conn.Close()
}
func (h *packetSessionHandler) NewPacketConnectionEx(_ context.Context, conn N.PacketConn, _ M.Socksaddr, target M.Socksaddr, onClose N.CloseHandlerFunc) {
	defer close(h.returned)
	h.udp <- packetSessionDispatch{conn, target, onClose}
}

type packetSessionFaultWire struct {
	net.Conn
	fault atomic.Pointer[packetSessionCipherFault]
	writes atomic.Int32
	mutations atomic.Int32
}

// The fixed WriteBuffer wire layout is authenticated length (18 bytes),
// authenticated payload, then unauthenticated random global padding. Flipping
// the final wire byte only corrupts padding whenever that padding is nonempty.
// Arm one complete post-handshake record, validate its actual crypto layout,
// then flip the first payload-ciphertext byte instead of any padding byte.
func (c *packetSessionFaultWire) Write(p []byte) (int, error) {
	c.writes.Add(1)
	if fault := c.fault.Swap(nil); fault != nil {
		changed, observation := fault.mutate(p)
		fault.observed <- observation
		if observation.err != nil { return 0, observation.err }
		c.mutations.Add(1)
		p = changed
	}
	return c.Conn.Write(p)
}

type packetSessionCipherObservation struct {
	offset, ciphertextBytes, paddingBytes int
	authenticationError string
	err error
}

type packetSessionCipherFault struct {
	lengthCipher, payloadCipher cipher.AEAD
	lengthNonce, payloadNonce []byte
	plaintext []byte
	observed chan packetSessionCipherObservation
}

func newPacketSessionCipherFault(protocol *clientConn, plaintext []byte) (*packetSessionCipherFault, error) {
	responseKey := sha256.Sum256(protocol.requestKey[:])
	responseNonce := sha256.Sum256(protocol.requestNonce[:])
	fault := &packetSessionCipherFault{plaintext: plaintext, observed: make(chan packetSessionCipherObservation, 1)}
	switch protocol.security {
	case SecurityTypeAes128Gcm:
		fault.lengthCipher = newAesGcm(KDF(protocol.requestKey[:], "auth_len")[:16])
		fault.payloadCipher = newAesGcm(responseKey[:16])
	case SecurityTypeChacha20Poly1305:
		fault.lengthCipher = newChacha20Poly1305(GenerateChacha20Poly1305Key(KDF(protocol.requestKey[:], "auth_len")[:16]))
		fault.payloadCipher = newChacha20Poly1305(GenerateChacha20Poly1305Key(responseKey[:16]))
	default:
		return nil, fmt.Errorf("unexpected cipher for authenticated fault: %d", protocol.security)
	}
	fault.lengthNonce = append([]byte(nil), protocol.requestNonce[:fault.lengthCipher.NonceSize()]...)
	fault.payloadNonce = append([]byte(nil), responseNonce[:fault.payloadCipher.NonceSize()]...)
	// Exactly one complete response packet was consumed before arming. The
	// response header uses distinct keys and does not consume either counter.
	binary.BigEndian.PutUint16(fault.lengthNonce, 1)
	binary.BigEndian.PutUint16(fault.payloadNonce, 1)
	return fault, nil
}

func (f *packetSessionCipherFault) mutate(wire []byte) ([]byte, packetSessionCipherObservation) {
	observation := packetSessionCipherObservation{}
	lengthBytes := 2 + f.lengthCipher.Overhead()
	payloadBytes := len(f.plaintext) + f.payloadCipher.Overhead()
	payloadEnd := lengthBytes + payloadBytes
	if len(wire) < payloadEnd || len(wire)-payloadEnd >= MaxPaddingSize {
		observation.err = fmt.Errorf("fault record shape: wire=%d authenticated_length=%d ciphertext=%d", len(wire), lengthBytes, payloadBytes)
		return nil, observation
	}
	length, err := f.lengthCipher.Open(nil, f.lengthNonce, wire[:lengthBytes], nil)
	if err != nil || len(length) != 2 {
		observation.err = fmt.Errorf("original authenticated length invalid: %v", err)
		return nil, observation
	}
	paddingBytes := len(wire) - payloadEnd
	if int(binary.BigEndian.Uint16(length)) != len(f.plaintext)+paddingBytes {
		observation.err = fmt.Errorf("authenticated length does not identify the expected complete record")
		return nil, observation
	}
	plaintext, err := f.payloadCipher.Open(nil, f.payloadNonce, wire[lengthBytes:payloadEnd], nil)
	if err != nil || !bytes.Equal(plaintext, f.plaintext) {
		observation.err = fmt.Errorf("original payload authentication/content mismatch: %v", err)
		return nil, observation
	}
	changed := append([]byte(nil), wire...)
	changed[lengthBytes] ^= 0x80
	if !bytes.Equal(changed[:lengthBytes], wire[:lengthBytes]) || !bytes.Equal(changed[payloadEnd:], wire[payloadEnd:]) {
		observation.err = errors.New("fault changed length authentication or random padding")
		return nil, observation
	}
	_, err = f.payloadCipher.Open(nil, f.payloadNonce, changed[lengthBytes:payloadEnd], nil)
	if err == nil {
		observation.err = errors.New("payload-ciphertext mutation did not fail authentication")
		return nil, observation
	}
	observation.offset = lengthBytes
	observation.ciphertextBytes = payloadBytes
	observation.paddingBytes = paddingBytes
	observation.authenticationError = err.Error()
	return changed, observation
}

type packetSessionEncryptedFixture struct {
	t *testing.T
	ctx context.Context
	client *XUDPConn
	protocol *clientConn
	server *serverMuxPacketConn
	serverWire *packetSessionFaultWire
	clientWire net.Conn
	target M.Socksaddr
	onClose N.CloseHandlerFunc
	workers sync.WaitGroup
}

func newPacketSessionEncryptedFixture(t *testing.T, security string, first []byte) *packetSessionEncryptedFixture {
	t.Helper()
	packetSessionTrack(t)
	ctx, cancel := context.WithTimeout(context.Background(), packetSessionTestBound)
	left, right := net.Pipe()
	deadline := time.Now().Add(packetSessionTestBound)
	if err := left.SetDeadline(deadline); err != nil { t.Fatal(err) }
	if err := right.SetDeadline(deadline); err != nil { t.Fatal(err) }
	wire := &packetSessionFaultWire{Conn: right}
	handler := &packetSessionHandler{udp: make(chan packetSessionDispatch, 1), returned: make(chan struct{})}
	service := NewService[string](handler)
	if err := service.UpdateUsers([]string{"fixture"}, []string{packetSessionTestUser}, []int{0}); err != nil { t.Fatal(err) }
	serviceDone := make(chan error, 1)
	go func() { serviceDone <- service.NewConnection(ctx, wire, M.ParseSocksaddr("127.0.0.1:24443"), nil) }()
	f := &packetSessionEncryptedFixture{t: t, ctx: ctx, serverWire: wire, clientWire: left, target: M.ParseSocksaddr("127.0.0.3:12345")}
	t.Cleanup(func() {
		cancel()
		left.Close()
		right.Close()
		service.Close()
		serviceJoined := false
		select { case <-serviceDone: serviceJoined = true; case <-time.After(packetSessionTestBound): t.Error("real Service invocation did not join") }
		joined := make(chan struct{})
		go func() { f.workers.Wait(); close(joined) }()
		callersJoined := false
		select { case <-joined: callersJoined = true; case <-time.After(packetSessionTestBound): t.Error("encrypted callers did not join") }
		if serviceJoined && callersJoined && f.protocol != nil { f.protocol.ReleasePacketSessionReadCacheAfterJoin(); f.protocol.ReleasePacketSessionReadCacheAfterJoin() }
		if f.client != nil { f.client.Close() }
		if handler.unexpectedTCP.Load() != 0 { t.Error("real service saw TCP instead of mux UDP") }
	})
	client, err := NewClient(packetSessionTestUser, security, 0, ClientWithGlobalPadding(), ClientWithAuthenticatedLength())
	if err != nil { t.Fatal(err) }
	packetConn, err := client.DialXUDPPacketConn(left, f.target)
	if err != nil { t.Fatal(err) }
	var ok bool
	f.client, ok = packetConn.(*XUDPConn)
	if !ok { t.Fatal("unexpected XUDP client type") }
	f.protocol, ok = f.client.Conn.(*clientConn)
	if !ok { t.Fatal("unexpected fixed VMess client wrapper") }
	if f.protocol.command != CommandMux || f.protocol.option != byte(RequestOptionChunkStream|RequestOptionChunkMasking|RequestOptionGlobalPadding|RequestOptionAuthenticatedLength) { t.Fatal("actual Client did not encode CommandMux with padding/authenticated lengths") }
	f.client.SetPacketSessionEndReporting()
	firstWrite := f.async(func() error {
		n, err := f.client.WriteTo(first, f.target)
		if err == nil && n != len(first) { err = io.ErrShortWrite }
		return err
	})
	select {
	case dispatch := <-handler.udp:
		if dispatch.target != f.target || dispatch.onClose != nil { t.Fatal("fixed handler destination/onClose contract changed") }
		f.onClose = dispatch.onClose
		f.server, ok = dispatch.conn.(*serverMuxPacketConn)
		if !ok { t.Fatal("actual Service did not dispatch serverMuxPacketConn") }
	case <-ctx.Done(): t.Fatal("real UDP mux dispatch missing")
	}
	select { case <-handler.returned: case <-ctx.Done(): t.Fatal("handler invocation did not return") }
	f.wait(firstWrite)
	if f.server.sessionID != 0 || f.server.stream.id != 0 { t.Fatal("single private session did not use wire SID 0") }
	return f
}

func (f *packetSessionEncryptedFixture) async(call func() error) <-chan error {
	done := make(chan error, 1)
	f.workers.Add(1)
	go func() { defer f.workers.Done(); done <- call() }()
	return done
}
func (f *packetSessionEncryptedFixture) wait(done <-chan error) {
	f.t.Helper()
	select { case err := <-done: if err != nil { f.t.Fatal(err) }; case <-f.ctx.Done(): f.t.Fatal("encrypted operation exceeded bound") }
}
func (f *packetSessionEncryptedFixture) receiveServer(want []byte) {
	f.t.Helper()
	packet := buf.NewSize(65535)
	defer packet.Release()
	target, err := f.server.ReadPacket(packet)
	if err != nil || target != f.target || !bytes.Equal(packet.Bytes(), want) { f.t.Fatalf("real server packet changed: target=%v size=%d err=%v", target, packet.Len(), err) }
}
func (f *packetSessionEncryptedFixture) reply(want []byte) {
	f.t.Helper()
	write := f.async(func() error { _, err := f.server.WriteTo(want, f.target); return err })
	packet := buf.NewSize(len(want))
	defer packet.Release()
	target, err := f.client.ReadPacket(packet)
	if err != nil || target != f.target || !bytes.Equal(packet.Bytes(), want) { f.t.Fatalf("real client packet changed: target=%v size=%d err=%v", target, packet.Len(), err) }
	f.wait(write)
}

func testPacketSessionEncrypted(t *testing.T, security string) {
	t.Run("normal_end_complete_packets", func(t *testing.T) {
		f := newPacketSessionEncryptedFixture(t, security, []byte{})
		f.receiveServer([]byte{})
		f.reply([]byte{})
		for _, size := range []int{1, 32768, 60000, 65497} {
			payload := bytes.Repeat([]byte{byte(size%251+1)}, size)
			write := f.async(func() error { _, err := f.client.WriteTo(payload, f.target); return err })
			f.receiveServer(payload)
			f.wait(write)
			f.reply(payload)
		}
		if err := f.server.Close(); err != nil { t.Fatal(err) }
		_, err := f.client.ReadPacket(buf.With(nil))
		if err != N.ErrPacketSessionEnded { t.Fatalf("real encrypted canonical End: %v", err) }
		if f.serverWire.writes.Load() < 8 { t.Fatal("large packets did not exercise encrypted chunking") }
		t.Logf("real Client/Service cipher=%s CommandMux=3 SID=0 global_padding=true authenticated_length=true zero=1,32768,60000,65497 canonical_end=true", security)
	})
	t.Run("explicit_error_end_terminal", func(t *testing.T) {
		f := newPacketSessionEncryptedFixture(t, security, []byte("first"))
		f.receiveServer([]byte("first"))
		f.server.session.closeStream(f.server.stream, true, errors.New("fixture explicit stream failure"))
		_, err := f.client.ReadPacket(buf.With(nil))
		if err == nil || errors.Is(err, N.ErrPacketSessionEnded) || !errors.Is(err, net.ErrClosed) { t.Fatalf("explicit error End became recoverable: %v", err) }
	})
	t.Run("handshake_failure_is_opaque_end", func(t *testing.T) {
		f := newPacketSessionEncryptedFixture(t, security, []byte("never-delivered"))
		// The real fixed handler receives nil onClose and closes the mux child
		// without reading it. No packet is delivered to any downstream target.
		rejection := errors.New("fixture handshake policy failure")
		if err := N.CloseOnHandshakeFailure(f.server, f.onClose, rejection); !errors.Is(err, rejection) { t.Fatalf("local rejection was lost: %v", err) }
		_, err := f.client.ReadPacket(buf.With(nil))
		if err != N.ErrPacketSessionEnded { t.Fatalf("fixed rejection did not expose canonical opaque End: %v", err) }
		t.Log("handshake_failure_close=true application_target_reads=0 canonical_no_error_end=true idle_reason_proven=false; actual route rejection/re-admission is a separate route/owner gate")
	})
	t.Run("raw_transport_eof_terminal", func(t *testing.T) {
		f := newPacketSessionEncryptedFixture(t, security, []byte("first"))
		f.receiveServer([]byte("first"))
		f.reply([]byte("first"))
		f.serverWire.Close()
		_, err := f.client.ReadPacket(buf.With(nil))
		if err == nil || errors.Is(err, N.ErrPacketSessionEnded) { t.Fatalf("raw close became recoverable: %v", err) }
	})
	t.Run("corrupt_aead_terminal", func(t *testing.T) {
		f := newPacketSessionEncryptedFixture(t, security, []byte("first"))
		f.receiveServer([]byte("first"))
		f.reply([]byte("first"))
		payload := []byte("corrupt")
		plainFrame := packetSessionFrame(0, StatusKeep, OptionData, packetSessionAddress(NetworkUDP, 1, f.target.Port), payload)
		fault, err := newPacketSessionCipherFault(f.protocol, plainFrame)
		if err != nil { t.Fatal(err) }
		f.serverWire.fault.Store(fault)
		write := f.async(func() error { _, err := f.server.WriteTo(payload, f.target); return err })
		packet := buf.With(make([]byte, 32))
		_, readErr := f.client.ReadPacket(packet)
		var observation packetSessionCipherObservation
		select { case observation = <-fault.observed: case <-f.ctx.Done(): t.Fatal("authenticated mutation was not reached") }
		if observation.err != nil { t.Fatal(observation.err) }
		if f.serverWire.mutations.Load() != 1 || observation.offset != 2+CipherOverhead || observation.authenticationError == "" {
			t.Fatalf("authenticated mutation proof missing: count=%d observation=%+v", f.serverWire.mutations.Load(), observation)
		}
		if readErr == nil || errors.Is(readErr, N.ErrPacketSessionEnded) || readErr.Error() != observation.authenticationError || packet.Len() != 0 {
			t.Fatalf("actual Client must return the exact payload AEAD failure without data: len=%d got=%v want=%s", packet.Len(), readErr, observation.authenticationError)
		}
		// Abort before waiting even on a failure path: the actual synchronous
		// writer and Service invocation must join before cache finalization.
		f.clientWire.Close()
		select { case <-write: case <-f.ctx.Done(): t.Fatal("corrupt encrypted writer did not join after raw abort") }
		t.Logf("authenticated_mutations=1 ciphertext_offset=%d ciphertext_bytes=%d unchanged_padding_bytes=%d original_authentication=pass mutated_authentication=fail actual_client_error=%s", observation.offset, observation.ciphertextBytes, observation.paddingBytes, readErr)
	})
}

func TestPacketSessionEncryptedAES(t *testing.T) { testPacketSessionEncrypted(t, "aes-128-gcm") }
func TestPacketSessionEncryptedChaCha(t *testing.T) { testPacketSessionEncrypted(t, "chacha20-poly1305") }
