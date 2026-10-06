"""Audited public export surface.

Only protocol profiles with fixed real-core/client/loopback evidence are accepted.
Draft exporters remain internal and are never used as a fallback.
"""
from __future__ import annotations

import base64
import json
import re
from urllib.parse import quote, urlencode

from app.services.subscription_tokens import normalize_server
from app.services.mihomo_routing import unique_proxy_names
from app.services.websocket_profile import websocket_host, websocket_path
from app.services.grpc_profile import grpc_service_name
from app.services.hysteria2_profile import password_value
from app.services import tuic_profile, reality_profile


class ExportError(ValueError):
    pass


def mapping(value, allowed: set[str], label: str) -> dict:
    if not isinstance(value, dict) or set(value) - allowed:
        raise ExportError(f"Unsupported {label} fields; export was not generated")
    return value


def node_host(value: str) -> str:
    try:
        if not isinstance(value, str):
            raise ValueError()
        return normalize_server(value)
    except ValueError:
        raise ExportError("Invalid connection host or TLS name") from None


def _common(item, server: str) -> tuple[str, str]:
    if not item.enable or getattr(item, "expiry_time", 0):
        raise ExportError("Disabled or expiring nodes are not eligible for this export profile")
    if item.core != "sing-box" or item.protocol not in {"vless", "trojan", "shadowsocks", "vmess", "hysteria2", "tuic"}:
        raise ExportError("Validated export requires the bounded sing-box VLESS TCP/TLS, WS/TLS, gRPC/TLS, REALITY/Vision, Trojan/TLS, Shadowsocks, VMess/TLS, Hysteria2/TLS or TUIC v5/TLS profiles")
    server=node_host(server)
    if type(item.port) is not int or not 1 <= item.port <= 65535:
        raise ExportError("Invalid node port")
    name=item.remark or f"node-{item.id}"
    if (not isinstance(name,str) or not 1 <= len(name) <= 128
            or any(ord(c)<32 or ord(c)==127 for c in name)):
        raise ExportError("Invalid node name")
    return server,name


def _verified_tls(item, *, websocket: bool = False, grpc: bool = False, hysteria2: bool = False, tuic: bool = False) -> tuple[str, dict, list[str] | None]:
    stream=mapping(item.stream_settings or {}, {"tls","_vui"} if hysteria2 or tuic else {"tls","transport","_vui"}, "stream")
    if not (websocket or grpc) and stream.get("transport") not in (None,{}):
        raise ExportError("Non-TCP transports need a separately validated export profile")
    tls=mapping(stream.get("tls"), {"enabled","server_name","certificate_path","key_path","alpn"}, "TLS")
    if tls.get("enabled") is not True:
        raise ExportError("Verified TLS is required for this export profile")
    sni=node_host(tls.get("server_name") or "")
    meta=mapping(stream.get("_vui",{}),
                 {"security","server_name","client_fingerprint","skip_cert_verify"} | ({"udp_relay_mode"} if tuic else set()),
                 "client metadata")
    if meta.get("security","tls")!="tls" or meta.get("skip_cert_verify",False) is not False:
        raise ExportError("TLS verification cannot be disabled in this profile")
    if (grpc or hysteria2 or tuic) and "server_name" in meta and not isinstance(meta["server_name"], str):
        raise ExportError(f"{item.protocol} client TLS name must be a string")
    if meta.get("server_name") and node_host(meta["server_name"])!=sni:
        raise ExportError("Conflicting client/server TLS names")
    if hysteria2:
        if "alpn" in tls or meta.get("client_fingerprint", "") != "":
            raise ExportError("The verified Hysteria2 profile uses native QUIC defaults without ALPN or uTLS overrides")
        if not isinstance(tls.get("server_name"), str) or tls["server_name"] != sni:
            raise ExportError("Hysteria2 requires a literal valid TLS server name")
    if tuic:
        if meta.get("client_fingerprint", "") != "":
            raise ExportError("TUIC uses QUIC TLS without a client fingerprint override")
        if not isinstance(tls.get("server_name"), str) or tls["server_name"] != sni:
            raise ExportError("TUIC requires a literal valid TLS server name")
        if tls.get("alpn") != ["h3"]:
            raise ExportError("The characterized TUIC profile requires explicit h3 ALPN")
    fingerprint=meta.get("client_fingerprint","")
    if fingerprint not in ("","chrome"):
        raise ExportError("Client fingerprint is not validated")
    alpn=tls.get("alpn")
    if alpn is not None and (
        not isinstance(alpn,list) or not alpn
        or any(not isinstance(x,str) or not re.fullmatch(r"[A-Za-z0-9./_-]{1,64}",x) for x in alpn)
    ):
        raise ExportError("Invalid TLS ALPN")
    if websocket and alpn not in (None, ["http/1.1"]):
        raise ExportError("The validated WebSocket profile only accepts HTTP/1.1 ALPN")
    if grpc and "alpn" in tls and alpn != ["h2"]:
        raise ExportError("The validated gRPC profile only accepts h2 ALPN")
    return sni,meta,list(alpn) if alpn else None


def _websocket_transport(item) -> dict:
    transport = mapping(item.stream_settings["transport"], {"type", "path", "headers"}, "WebSocket transport")
    if transport.get("type") != "ws":
        raise ExportError("Only the separately verified VLESS WebSocket transport is accepted")
    headers = mapping(transport.get("headers", {}), {"Host"}, "WebSocket headers")
    try:
        path = websocket_path(transport.get("path"))
        host = websocket_host(headers["Host"]) if "Host" in headers else None
    except ValueError as exc:
        raise ExportError(str(exc)) from exc
    result = {"path": path}
    if host is not None:
        result["headers"] = {"Host": host}
    return result


def _one_user(item) -> dict:
    settings=mapping(item.settings or {}, {"users"}, f"{item.protocol.upper()} settings")
    users=settings.get("users")
    if not isinstance(users,list) or len(users)!=1:
        raise ExportError(f"Select a node with exactly one explicit {item.protocol.upper()} user")
    return users[0]


def _grpc_transport(item) -> dict:
    transport = mapping(item.stream_settings["transport"], {"type", "service_name"}, "gRPC transport")
    try:
        service = grpc_service_name(transport.get("service_name"))
    except ValueError as exc:
        raise ExportError(str(exc)) from exc
    return {"grpc-service-name": service}


def _vless_node(item, server: str, name: str, sni: str, meta: dict,
                alpn: list[str] | None) -> dict:
    user=mapping(_one_user(item), {"uuid","name","flow"}, "VLESS user")
    uid=user.get("uuid")
    if not isinstance(uid,str) or not re.fullmatch(
        r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}",uid
    ):
        raise ExportError("Invalid VLESS UUID")
    if user.get("flow", "") != "":
        raise ExportError("This flow is not part of the validated VLESS TLS profiles")
    result={"name":name,"type":"vless","server":server,"port":item.port,"uuid":uid,
            "udp":True,"tls":True,"servername":sni,"network":"tcp",
            "packet-encoding":"xudp","skip-cert-verify":False}
    if meta.get("client_fingerprint"):
        result["client-fingerprint"]=meta["client_fingerprint"]
    if alpn:
        result["alpn"]=alpn
    return result


def _vmess_node(item, server: str, name: str, sni: str, meta: dict,
                alpn: list[str] | None) -> dict:
    user=mapping(_one_user(item), {"uuid","name"}, "VMess user")
    uid=user.get("uuid")
    if not isinstance(uid,str) or not re.fullmatch(
        r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}",uid
    ):
        raise ExportError("Invalid VMess UUID")
    result={"name":name,"type":"vmess","server":server,"port":item.port,
            "uuid":uid,"alterId":0,"cipher":"auto","udp":True,
            "tls":True,"servername":sni,"network":"tcp",
            "skip-cert-verify":False}
    if meta.get("client_fingerprint"):
        result["client-fingerprint"]=meta["client_fingerprint"]
    if alpn:
        result["alpn"]=alpn
    return result


def _trojan_node(item, server: str, name: str, sni: str, meta: dict,
                 alpn: list[str] | None) -> dict:
    user=mapping(_one_user(item), {"password","name"}, "Trojan user")
    password=user.get("password")
    if (not isinstance(password,str) or not 1 <= len(password) <= 256
            or any(ord(c)<32 or ord(c)==127 for c in password)):
        raise ExportError("Invalid Trojan password")
    result={"name":name,"type":"trojan","server":server,"port":item.port,
            "password":password,"udp":True,"tls":True,"sni":sni,
            "skip-cert-verify":False}
    if meta.get("client_fingerprint"):
        result["client-fingerprint"]=meta["client_fingerprint"]
    if alpn:
        result["alpn"]=alpn
    return result


def _hysteria2_node(item, server: str, name: str, sni: str) -> dict:
    user = mapping(_one_user(item), {"password", "name"}, "Hysteria2 user")
    try:
        password = password_value(user.get("password"))
    except ValueError as exc:
        raise ExportError(str(exc)) from exc
    return {"name": name, "type": "hysteria2", "server": server, "port": item.port,
            "password": password, "udp": False, "sni": sni, "skip-cert-verify": False}


def _tuic_node(item, server: str, name: str, sni: str, meta: dict) -> dict:
    settings = mapping(item.settings, {"users", "congestion_control", "zero_rtt_handshake"}, "TUIC settings")
    if settings.get("congestion_control", "cubic") != "cubic":
        raise ExportError("TUIC public export only accepts default cubic congestion control")
    if settings.get("zero_rtt_handshake", False) is not False:
        raise ExportError("TUIC public export requires zero-RTT disabled")
    if meta.get("udp_relay_mode", "native") != "native":
        raise ExportError("TUIC public export does not admit an alternate UDP relay mode")
    try:
        user = tuic_profile.credential_user(settings)
    except ValueError as exc:
        raise ExportError(str(exc)) from exc
    # The pinned Mihomo TUIC adapter hardcodes UDP capability. Do not include
    # ineffective udp:false and falsely claim TCP-only enforcement.
    return {"name": name, "type": "tuic", "server": server, "port": item.port,
            "uuid": user["uuid"], "password": user["password"], "sni": sni,
            "skip-cert-verify": False, "alpn": ["h3"], "reduce-rtt": False}


SS_METHODS={"aes-128-gcm","aes-256-gcm","chacha20-ietf-poly1305"}


def _shadowsocks_node(item, server: str, name: str) -> dict:
    settings=mapping(item.settings or {}, {"method","password"}, "Shadowsocks settings")
    stream=mapping(item.stream_settings or {}, set(), "Shadowsocks stream")
    if stream:
        raise ExportError("Shadowsocks public profile does not accept transport/TLS fields")
    method=settings.get("method")
    password=settings.get("password")
    if method not in SS_METHODS:
        raise ExportError("Shadowsocks method is outside the validated AEAD set")
    if (not isinstance(password,str) or not 1 <= len(password) <= 256
            or any(ord(c)<32 or ord(c)==127 for c in password)):
        raise ExportError("Invalid Shadowsocks password")
    return {"name":name,"type":"ss","server":server,"port":item.port,
            "cipher":method,"password":password,"udp":True}


def validated_node(item, server: str) -> dict:
    server,name=_common(item,server)
    if item.protocol=="shadowsocks":
        return _shadowsocks_node(item,server,name)
    stream = item.stream_settings or {}
    if item.protocol == 'vless' and reality_profile.has_reality(stream):
        try:
            value = reality_profile.validate_stored(item.settings, stream)
        except ValueError as exc:
            raise ExportError(str(exc)) from exc
        return {'name': name, 'type': 'vless', 'server': server, 'port': item.port,
                'uuid': value['uuid'], 'flow': value['flow'], 'network': 'tcp', 'udp': False,
                'tls': True, 'servername': value['server_name'], 'client-fingerprint': 'chrome',
                'skip-cert-verify': False,
                'reality-opts': {'public-key': value['public_key'], 'short-id': value['short_id']}}
    transport = stream.get("transport") if isinstance(stream, dict) else None
    websocket = item.protocol == "vless" and isinstance(transport, dict) and transport.get("type") == "ws"
    grpc = item.protocol == "vless" and isinstance(transport, dict) and transport.get("type") == "grpc"
    sni,meta,alpn=_verified_tls(item, websocket=websocket, grpc=grpc, hysteria2=item.protocol=="hysteria2", tuic=item.protocol=="tuic")
    if item.protocol=="vless":
        node = _vless_node(item,server,name,sni,meta,alpn)
        if websocket:
            node["network"] = "ws"
            node["ws-opts"] = _websocket_transport(item)
            # This version verifies HTTP/TCP forwarding, not WebSocket UDP.
            node["udp"] = False
            node.pop("packet-encoding", None)
        elif grpc:
            node["network"] = "grpc"
            node["grpc-opts"] = _grpc_transport(item)
            # gRPC Lite acceptance is HTTP/TCP, not a UDP support claim.
            node["udp"] = False
            node.pop("packet-encoding", None)
        return node
    if item.protocol=="vmess":
        return _vmess_node(item,server,name,sni,meta,alpn)
    if item.protocol=="trojan":
        return _trojan_node(item,server,name,sni,meta,alpn)
    if item.protocol=="hysteria2":
        return _hysteria2_node(item,server,name,sni)
    if item.protocol=="tuic":
        return _tuic_node(item,server,name,sni,meta)
    raise ExportError("Unreachable export profile")


def validated_nodes(items, server: str) -> list[dict]:
    items=list(items)
    if not items:
        raise ExportError("At least one eligible node is required; no DIRECT fallback was generated")
    return unique_proxy_names([validated_node(item,server) for item in items])


def share_link(item, server: str) -> str:
    node=validated_node(item,server)
    host=f"[{node['server']}]" if ":" in node["server"] else node["server"]
    if node["type"]=="vless":
        params={"security":"tls","type":node["network"],"sni":node["servername"],
                "encryption":"none"}
        if 'reality-opts' in node:
            params.update(security='reality', flow=node['flow'],
                          pbk=node['reality-opts']['public-key'], sid=node['reality-opts']['short-id'])
        elif node["network"] == "ws":
            params["path"] = node["ws-opts"]["path"]
            if node["ws-opts"].get("headers"):
                params["host"] = node["ws-opts"]["headers"]["Host"]
        elif node["network"] == "grpc":
            params["serviceName"] = node["grpc-opts"]["grpc-service-name"]
        else:
            params["packetEncoding"] = "xudp"
        if node.get("client-fingerprint"):
            params["fp"]=node["client-fingerprint"]
        if node.get("alpn"):
            params["alpn"]=",".join(node["alpn"])
        return (
            f"vless://{node['uuid']}@{host}:{node['port']}?"
            f"{urlencode(params)}#{quote(node['name'],safe='')}"
        )

    if node["type"]=="ss":
        userinfo=base64.urlsafe_b64encode(
            f"{node['cipher']}:{node['password']}".encode()
        ).decode().rstrip("=")
        return f"ss://{userinfo}@{host}:{node['port']}#{quote(node['name'],safe='')}"

    if node["type"]=="vmess":
        payload={
            "v":"2","ps":node["name"],"add":node["server"],
            "port":str(node["port"]),"id":node["uuid"],"aid":"0",
            "scy":"auto","net":"tcp","type":"none","host":"","path":"",
            "tls":"tls","sni":node["servername"],
        }
        if node.get("client-fingerprint"):
            payload["fp"]=node["client-fingerprint"]
        if node.get("alpn"):
            payload["alpn"]=",".join(node["alpn"])
        encoded=base64.b64encode(
            json.dumps(payload,separators=(",",":"),ensure_ascii=False).encode()
        ).decode()
        return "vmess://"+encoded

    if node["type"] == "tuic":
        # Fixed Mihomo converter convention, not an official universal standard.
        # Verification and zero-RTT use secure defaults; no invented URI toggles.
        return (f"tuic://{quote(node['uuid'],safe='')}:{quote(node['password'],safe='')}@{host}:{node['port']}?"
                + urlencode({"sni": node["sni"], "alpn": "h3"}) + "#" + quote(node["name"], safe=""))
    if node["type"] == "hysteria2":
        return (f"hysteria2://{quote(node['password'],safe='')}@{host}:{node['port']}/?"
                f"{urlencode({'sni': node['sni'], 'insecure': '0'})}#{quote(node['name'],safe='')}")

    params={"sni":node["sni"]}
    if node.get("client-fingerprint"):
        params["fp"]=node["client-fingerprint"]
    if node.get("alpn"):
        params["alpn"]=",".join(node["alpn"])
    return (
        f"trojan://{quote(node['password'],safe='')}@{host}:{node['port']}?"
        f"{urlencode(params)}#{quote(node['name'],safe='')}"
    )


def base64_subscription(items, server: str) -> str:
    items=list(items)
    validated_nodes(items,server)
    return base64.b64encode(
        "\n".join(share_link(item,server) for item in items).encode()
    ).decode()


def _client_tls(node: dict) -> dict:
    tls={"enabled":True,"server_name":node.get("servername") or node["sni"]}
    if node.get("client-fingerprint"):
        tls["utls"]={"enabled":True,"fingerprint":node["client-fingerprint"]}
    if node.get("alpn"):
        tls["alpn"]=list(node["alpn"])
    if 'reality-opts' in node:
        tls['reality'] = {'enabled': True, 'public_key': node['reality-opts']['public-key'],
                          'short_id': node['reality-opts']['short-id']}
    return tls


def singbox_client_config(items, server: str) -> dict:
    proxies=validated_nodes(items,server)
    outbounds=[]
    for node in proxies:
        if node["type"]=="vless":
            outbound={"type":"vless","tag":node["name"],"server":node["server"],
                      "server_port":node["port"],"uuid":node["uuid"],
                      "packet_encoding":"xudp","tls":_client_tls(node)}
            if 'reality-opts' in node:
                outbound.pop('packet_encoding')
                outbound['network'] = 'tcp'
                outbound['flow'] = node['flow']
            elif node["network"] == "ws":
                outbound.pop("packet_encoding")
                outbound["network"] = "tcp"
                outbound["transport"] = {"type": "ws", **node["ws-opts"]}
            elif node["network"] == "grpc":
                outbound.pop("packet_encoding")
                outbound["network"] = "tcp"
                outbound["transport"] = {"type": "grpc", "service_name": node["grpc-opts"]["grpc-service-name"]}
        elif node["type"]=="trojan":
            outbound={"type":"trojan","tag":node["name"],"server":node["server"],
                      "server_port":node["port"],"password":node["password"],
                      "tls":_client_tls(node)}
        elif node["type"]=="hysteria2":
            outbound={"type":"hysteria2","tag":node["name"],"server":node["server"],
                      "server_port":node["port"],"password":node["password"],
                      "network":"tcp","tls":_client_tls(node)}
        elif node["type"]=="tuic":
            outbound={"type":"tuic","tag":node["name"],"server":node["server"],
                      "server_port":node["port"],"uuid":node["uuid"],"password":node["password"],
                      "network":"tcp","zero_rtt_handshake":False,"tls":_client_tls(node)}
        elif node["type"]=="vmess":
            outbound={"type":"vmess","tag":node["name"],"server":node["server"],
                      "server_port":node["port"],"uuid":node["uuid"],
                      "security":"auto","alter_id":0,"tls":_client_tls(node)}
        else:
            outbound={"type":"shadowsocks","tag":node["name"],"server":node["server"],
                      "server_port":node["port"],"method":node["cipher"],
                      "password":node["password"]}
        outbounds.append(outbound)
    return {
        "log":{"level":"warn"},
        "inbounds":[{"type":"mixed","tag":"local","listen":"127.0.0.1","listen_port":7890}],
        "outbounds":outbounds,
        "route":{"final":outbounds[0]["tag"]},
    }


def export_warnings(items) -> list[dict]:
    warnings=[]
    for item in items:
        try:
            validated_node(item,"example.test")
        except (ValueError,TypeError,AttributeError):
            warnings.append({
                "inbound_id":item.id,
                "code":"UNVERIFIED_EXPORT_PROFILE",
                "message":"This node is outside the bounded sing-box VLESS TCP/TLS, WS/TLS, gRPC/TLS, REALITY/Vision, Trojan/TLS, Shadowsocks, VMess/TLS, Hysteria2/TLS and TUIC v5/TLS export profiles.",
            })
    return warnings
