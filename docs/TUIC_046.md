# v0.4.6 TUIC v5 bounded characterization

Base: master `838c66d9974dd9f3a944641a2e9e03cc200e0bbe`, tree `e12287d8ebbea233142d58191ee5141a0a49a17a`. HY2 PR #20/#21 final mainline passed eight workflows / 11 jobs; their earlier failures remain historical evidence. This stage is a candidate, not a released or deployed version.

## Actual fixed implementation

- Official sing-box 1.14.2 and Mihomo 1.19.32 binaries, archive SHA-256 pins and builds unchanged
- sing-box [go.mod](https://github.com/SagerNet/sing-box/blob/v1.14.2/go.mod) pins sing-quic `6a3a24d65b99587fad1d4cdd567c88f212acdd63`; its [TUIC wire constant](https://github.com/SagerNet/sing-quic/blob/6a3a24d65b99587fad1d4cdd567c88f212acdd63/tuic/protocol.go) is Version 5
- Mihomo [fixed adapter](https://github.com/MetaCubeX/mihomo/blob/v1.19.32/adapter/outbound/tuic.go) uses the v5 client for UUID/password, v4 only for token. Tokens/v4 are excluded
- One UUID/password pair, normal verified TLS with explicit SNI, native QUIC, explicit `alpn: [h3]` and zero-RTT disabled. Congestion and heartbeat defaults stay unchanged; no tuning, obfs, hopping, REALITY or protocol expansion
- HTTP/TCP payload qualification only. QUIC needs node UDP reachability, separate from application UDP relay. sing-box outbound is `network: tcp`. Mihomo's fixed TUIC adapter advertises UDP support regardless of `udp: false`; no enforcement or tested application-UDP support is claimed
- TUIC URI uses the [fixed Mihomo converter's supported client convention](https://github.com/MetaCubeX/mihomo/blob/v1.19.32/common/convert/converter.go), which upstream explicitly calls temporary/unofficial. Do not describe it as an official universal URI standard. UUID/password are percent-encoded userinfo; `sni`/`alpn` are preserved, insecure and zero-RTT stay disabled by supported defaults

## Evidence gates

Bare config checking of server and both clients passes locally with fixed binaries. Actual local forwarding is separately attempted and may be blocked by environment socket/netlink permissions; such failures never count as runtime success. Real CI must prove both clients reach an HTTP target, then independent wrong UUID, password, CA and SNI refuse delivery without DIRECT. Credential reasons must distinguish `authentication: unknown user` and `authentication: token mismatch`; TLS needs x509 and the specific reason on the same log line. Timeout alone is never evidence. Target delivery count remains fixed throughout bounded negative observation.

Preflight must pass before public TUIC exports are admitted. Integration then covers three exports, real parser checks, shared hidden/blank-preserving credentials, imported advanced fields retained or rejected, certificate bind/renew/failure/stopped application, Chromium create/cancel/edit/refresh/export/damaged stopped restore. Four Linux targets, 40 ToClash presets and explicit protocol-aware UDP installer tests remain mandatory. No real firewall changes, real CA/account, tag/Release, deployment or artifact promotion.

Independent review and all eight exact-head workflows are required before parent-approved normal merge; all eight exact-master workflows are required afterward. This preflight does not claim final support.
