# Official device SOCKS buffer boundary

## Observed at exact a2f91cb

The two runtime arms and both explicit ciphers passed the 1-byte and 8,192-byte
datagrams. Each requested 32,768-byte datagram reached the application target as
exactly its first 15,992 bytes, with the expected official overseas source peer.
The four received hashes equal the corresponding original payload prefix hashes.
The 60,000-byte case was not reached. This is an upstream delivery truncation,
not merely a response-address assertion error. The full matrix remains failed.

## Fixed-source explanation, not a syscall capture

Fixed sing `87c33f17688fe3dd80eeabef7c3f7a69152ccd42` allocates the default
16,384-byte UDP buffer. `ReadWaitOptions.NewPacketBuffer` with MTU=0 deducts
front and rear headroom from that buffer rather than adding them to its size:

`16384 - XUDP front 268 - VMess front 18 - VMess rear 96 - IPv4 SOCKS header 10 = 15992`.

- [Default UDP size](https://github.com/SagerNet/sing/blob/87c33f17688fe3dd80eeabef7c3f7a69152ccd42/common/buf/buffer_standard.go#L7).
- [Allocation and headroom](https://github.com/SagerNet/sing/blob/87c33f17688fe3dd80eeabef7c3f7a69152ccd42/common/network/direct.go#L84-L100).
- [XUDP maximum front headroom](https://github.com/SagerNet/sing-vmess/blob/31ec11e8790c48a4bf11711806a61e929ee6b89d/xudp.go#L124-L174).
- [VMess front/rear constants](https://github.com/SagerNet/sing-vmess/blob/31ec11e8790c48a4bf11711806a61e929ee6b89d/protocol.go#L24-L31).
- [SOCKS read-wait option forwarding and header removal](https://github.com/SagerNet/sing/blob/87c33f17688fe3dd80eeabef7c3f7a69152ccd42/protocol/socks/packet_wait.go#L27-L46).
- [POSIX Recvmsg](https://github.com/SagerNet/sing/blob/87c33f17688fe3dd80eeabef7c3f7a69152ccd42/common/bufio/copy_direct_posix.go#L200-L215) reads into `buffer.FreeBytes()` and discards returned flags, including MSG_TRUNC.

This gives a 16,002-byte SOCKS receive capacity; removing the 10-byte IPv4 header
leaves 15,992 bytes. The fallback packet-copy read is bounded by the same buffer.
Two ciphers share this budget; VMess's 15,000-byte encryption chunk is a separate
framing value. The exact source arithmetic and four observed prefixes strongly
locate the earliest truncation at the unchanged official device SOCKS ingress.
No runtime syscall trace or new boundary sweep was performed, so this is not
reported as a captured recvmsg call. The SOCKS first packet has a different
allocation path; downstream packet-copy limits can also reject larger packets.

## Remaining gate and bounded next work

The added direct fixed Client-to-Service encrypted CommandMux test separates
server framing from that SOCKS input budget. It cannot grant official-device
large-datagram compatibility. The 1/8,192-byte successes are observed points,
not a supported product MTU or blanket VMess/UDP qualification.

A separate isolated repair would need explicit non-truncating packet admission
and receive sizing/truncation handling, plus both uplink and downlink capacity
tests. Minimal falsifiable diagnostics are warmed 15,991/15,992/15,993-byte
requests and a single-hop official-device control; current source predicts
truncation of the third. Neither those new diagnostics nor a device-side repair
has run here. Product pins and product UDP remain unchanged.
