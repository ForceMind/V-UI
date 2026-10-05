# alpha.5 — validated ToClash/Mihomo export

Reference: ToClash 0.3.8 at 95a5c71a516c10f97f47bfb771018ce890b2b570. The independently executed TypeScript implementation emits 100 synthetic cases. V-UI compares complete parsed configuration objects, ordered DNS policies, ordered rules, warnings and all 40 preset definitions. This is not a self-comparison of the Python port.

First verified public export profile: **sing-box 1.14.2 server, VLESS, TCP, TLS, one explicit UUID, empty flow**, client Mihomo 1.19.32. Ordinary TLS and optional Chrome fingerprint/ALPN are checked with real client binaries. Xray 26.3.27 is separately configuration-checked by alpha.4, but is not included in this first public export profile. Other protocol/transport/REALITY combinations remain draft implementations and are explicitly rejected by public exports until separately tested.

All public exports, including authenticated legacy preview endpoints, use the validated boundary. It refuses empty/disabled/expiring nodes, unsupported profiles, unknown fields, missing SNI, conflicting TLS names, unvalidated flows, and insecure certificate bypass. Server certificate/private-key paths are input-only and never emitted. URI IPv6 hosts are bracketed and names are percent-encoded. Duplicate and reserved Mihomo names remain stable.

Mihomo output includes proxies, proxy-groups, dns and rules. Standard and direct routing remain aligned with ToClash. Standard mode requires the client's compatible GeoSite/GeoIP data; reference parity is not proof that a user's remote data download is available. Real config checks use direct mode to avoid introducing uncontrolled external geodata. Actual connectivity and DNS destinations are rc.1's independent gate.

Base64/URI contain connection data only. sing-box JSON adds a loopback mixed listener and routes through the first listed node; it does not claim to translate the complete ToClash routing plan.

No runtime Node dependency or online converter. Attribution and full source license are retained under third_party/. Existing legacy draft protocol helpers are not a public fallback.

Local verification in this iteration: 77 tests including real binaries passed; 100 reference scenarios and 40 presets matched. CI must be verified for the exact final PR head before acceptance. No master merge, Release or VPS deployment.
