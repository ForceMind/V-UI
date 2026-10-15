"""Bounded, isolated import of individual VLESS overseas-outbound candidates.

No input is executed, fetched, persisted, or passed to a core. The public API
always parses in a fresh interpreter with a wall deadline and OS resource caps.
A preview contains only safe summaries; selecting even a sole node requires an
explicit zero-based index. Selection re-parses the exact caller-supplied bytes.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import selectors
import subprocess
import sys
import threading
import time
from urllib.parse import parse_qsl, unquote, urlsplit

# An isolated worker is launched by absolute script path, not PYTHONPATH or cwd.
if __name__ == "__main__" and not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.services.upstream_profile import (
    UpstreamProfile, UpstreamValidationError, literal_string, server_host,
    server_port, strict_mapping,
)

MAX_INPUT_BYTES = 1024 * 1024
MAX_URI_BYTES = 8192
MAX_CANDIDATES = 128
MAX_DEPTH = 16
MAX_TOKENS = 32768
MAX_STRING_BYTES = 8192
MAX_INTEGER = (1 << 63) - 1
MIN_INTEGER = -(1 << 63)
PARSER_TIMEOUT_SECONDS = 2
PARSER_MEMORY_BYTES = 128 * 1024 * 1024
MAX_WORKER_OUTPUT_BYTES = 256 * 1024
MAX_CONCURRENT_PARSERS = 2
_PARSER_SLOTS = threading.BoundedSemaphore(MAX_CONCURRENT_PARSERS)
SOURCE_FORMATS = frozenset({"uri", "v2ray-json", "clash-yaml"})
FORMAT_ALIASES = {"json": "v2ray-json", "xray-json": "v2ray-json", "yaml": "clash-yaml"}
URI_FIELDS = frozenset({"type", "security", "encryption", "flow", "fp", "pbk", "sid", "sni", "alpn"})
XRAY_IGNORED_SECTIONS = frozenset({"log", "inbounds", "routing", "dns", "policy", "api", "stats",
                                   "metrics", "observatory", "burstObservatory", "fakedns"})
CLASH_IGNORED_SECTIONS = frozenset({
    "proxy-groups", "rules", "dns", "tun", "hosts", "profile", "sniffer", "proxy-providers", "rule-providers",
    "port", "socks-port", "mixed-port", "redir-port", "tproxy-port", "allow-lan", "bind-address",
    "mode", "log-level", "ipv6", "external-controller", "external-controller-tls", "external-controller-unix",
    "external-ui", "external-ui-url", "secret", "authentication", "skip-auth-prefixes", "lan-allowed-ips",
    "lan-disallowed-ips", "interface-name", "routing-mark", "find-process-mode", "unified-delay",
    "tcp-concurrent", "keep-alive-interval", "geodata-mode", "geo-auto-update", "geo-update-interval", "geox-url",
})
CLASH_FIELDS = frozenset({"name", "type", "server", "port", "uuid", "network", "tls", "servername", "alpn",
                          "client-fingerprint", "flow", "skip-cert-verify", "udp", "reality-opts"})
KNOWN_URI_SCHEMES = frozenset({"vless", "vmess", "trojan", "ss", "ssr", "hysteria", "hysteria2", "hy2", "tuic"})


class UpstreamImportError(UpstreamValidationError):
    pass


def _fail(code, message):
    raise UpstreamImportError(code, message)


@dataclass(frozen=True)
class CandidateSummary:
    index: int
    name: str
    protocol: str
    server: str
    server_port: int | None
    transport: str
    tls_mode: str
    supported: bool
    reason_code: str
    reason: str

    def to_dict(self):
        return {"index": self.index, "name": self.name, "protocol": self.protocol, "server": self.server,
                "server_port": self.server_port, "transport": self.transport, "tls_mode": self.tls_mode,
                "supported": self.supported, "reason_code": self.reason_code, "reason": self.reason}


@dataclass(frozen=True)
class ImportPreview:
    source_format: str
    source_fingerprint: str
    candidates: tuple[CandidateSummary, ...]
    ignored_sections: tuple[str, ...]
    notice_codes: tuple[str, ...] = ()

    def to_dict(self):
        return {"source_format": self.source_format, "source_fingerprint": self.source_fingerprint,
                "candidates": [item.to_dict() for item in self.candidates],
                "ignored_sections": list(self.ignored_sections), "notice_codes": list(self.notice_codes)}


def _input_bytes(value):
    if type(value) is str:
        # Bound before the UTF-8 copy as well as afterwards.
        if len(value) > MAX_INPUT_BYTES:
            _fail("INPUT_LIMIT", "Import input exceeds 1 MiB")
        try:
            value = value.encode("utf-8", errors="strict")
        except UnicodeError:
            _fail("INVALID_UTF8", "Import input must be valid UTF-8")
    if type(value) is not bytes:
        _fail("INVALID_INPUT", "Import input must be UTF-8 text or bytes")
    if len(value) > MAX_INPUT_BYTES:
        _fail("INPUT_LIMIT", "Import input exceeds 1 MiB")
    try:
        value.decode("utf-8", errors="strict")
    except UnicodeError:
        _fail("INVALID_UTF8", "Import input must be valid UTF-8")
    return value


def _source_format(value):
    if type(value) is not str:
        _fail("UNSUPPORTED_FORMAT", "Use uri, v2ray-json, or clash-yaml")
    value = FORMAT_ALIASES.get(value, value)
    if value not in SOURCE_FORMATS | {"auto"}:
        _fail("UNSUPPORTED_FORMAT", "Use uri, v2ray-json, or clash-yaml")
    return value


def _bounded_worker_io(command, raw):
    """Bound both pipe directions and wall time; reap even on cancellation."""
    process = None
    try:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL)
        output = bytearray()
        remaining = memoryview(raw)
        deadline = time.monotonic() + PARSER_TIMEOUT_SECONDS
        with selectors.DefaultSelector() as selector:
            for pipe, events in ((process.stdin, selectors.EVENT_WRITE), (process.stdout, selectors.EVENT_READ)):
                os.set_blocking(pipe.fileno(), False)
                selector.register(pipe, events)
            while selector.get_map():
                wait = deadline - time.monotonic()
                if wait <= 0:
                    _fail("PARSE_TIMEOUT", "Import parser exceeded its execution deadline")
                for key, events in selector.select(wait):
                    pipe = key.fileobj
                    if events & selectors.EVENT_WRITE:
                        try:
                            count = os.write(pipe.fileno(), remaining[:65536]) if remaining else 0
                        except BrokenPipeError:
                            remaining = remaining[:0]
                        else:
                            remaining = remaining[count:]
                        if not remaining:
                            selector.unregister(pipe)
                            pipe.close()
                    else:
                        chunk = os.read(pipe.fileno(), min(65536, MAX_WORKER_OUTPUT_BYTES + 1 - len(output)))
                        if not chunk:
                            selector.unregister(pipe)
                            pipe.close()
                        else:
                            output.extend(chunk)
                            if len(output) > MAX_WORKER_OUTPUT_BYTES:
                                _fail("PARSER_RESOURCE_LIMIT", "The import parser exceeded its output limit")
            wait = deadline - time.monotonic()
            if wait <= 0:
                _fail("PARSE_TIMEOUT", "Import parser exceeded its execution deadline")
            try:
                returncode = process.wait(timeout=wait)
            except subprocess.TimeoutExpired:
                _fail("PARSE_TIMEOUT", "Import parser exceeded its execution deadline")
        if returncode:
            _fail("PARSER_RESOURCE_LIMIT", "The isolated import parser could not finish within its limits")
        return bytes(output)
    except OSError:
        _fail("PARSER_UNAVAILABLE", "The isolated import parser could not start or communicate")
    finally:
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.wait()
            for pipe in (process.stdin, process.stdout):
                if pipe is not None:
                    pipe.close()


def _worker_result(raw, source_format, selected_index):
    """No preexec_fn: the web process can have multiple threads."""
    if not _PARSER_SLOTS.acquire(blocking=False):
        _fail("PARSER_BUSY", "Two import parsers are already running; retry shortly")
    command = [sys.executable, "-I", "-B", str(Path(__file__).resolve()), "--parse-worker", source_format,
               "preview" if selected_index is None else str(selected_index)]
    try:
        output = _bounded_worker_io(command, raw)
    finally:
        _PARSER_SLOTS.release()
    try:
        result = json.loads(output)
        if type(result) is not dict or type(result.get("ok")) is not bool:
            raise ValueError()
        if not result["ok"]:
            if type(result.get("code")) is not str or type(result.get("message")) is not str:
                raise ValueError()
            raise UpstreamImportError(result["code"], result["message"])
        return result["value"]
    except (ValueError, KeyError, TypeError) as error:
        if isinstance(error, UpstreamImportError):
            raise
        _fail("PARSER_FAILED", "The isolated import parser returned an invalid result")


def preview_upstreams(value, source_format="auto"):
    """Return a credential-free preview; never automatically select a node."""
    result = _worker_result(_input_bytes(value), _source_format(source_format), None)
    return ImportPreview(source_format=result["source_format"], source_fingerprint=result["source_fingerprint"],
                         candidates=tuple(CandidateSummary(**item) for item in result["candidates"]),
                         ignored_sections=tuple(result["ignored_sections"]),
                         notice_codes=tuple(result["notice_codes"]))


def select_upstream(value, selected_index, source_format="auto"):
    """Explicitly select exactly one supported node. The result contains secrets."""
    if type(selected_index) is not int or not 0 <= selected_index < MAX_CANDIDATES:
        _fail("INVALID_SELECTION", "Select an explicit candidate index from the preview")
    return UpstreamProfile.from_dict(_worker_result(_input_bytes(value), _source_format(source_format), selected_index))


def import_upstreams(value, source_format="auto", *, selected_index=None):
    return (preview_upstreams(value, source_format) if selected_index is None
            else select_upstream(value, selected_index, source_format))


def _bounded_int(value):
    if len(value.lstrip("-+")) > 19:
        _fail("INTEGER_LIMIT", "An integer exceeds the signed 64-bit limit")
    result = int(value)
    if not MIN_INTEGER <= result <= MAX_INTEGER:
        _fail("INTEGER_LIMIT", "An integer exceeds the signed 64-bit limit")
    return result


def _bounded_float(value):
    if len(value) > 128:
        _fail("NUMBER_LIMIT", "A numeric literal exceeds the import limit")
    result = float(value)
    if not math.isfinite(result):
        _fail("NONFINITE_NUMBER", "NaN and Infinity are not allowed")
    return result


def _duplicate_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail("DUPLICATE_KEY", "Duplicate object keys are not allowed")
        result[key] = value
    return result


def _json_preflight(text):
    """Check depth, token count and each string before building the object tree."""
    index, depth, count = 0, 0, 0
    previous, last_string = "", ""
    candidate_depth, candidate_count, candidate_ready = None, 0, False
    while index < len(text):
        char = text[index]
        if char in " \r\n\t":
            index += 1
            continue
        if depth == candidate_depth and candidate_ready and char not in ",]":
            candidate_count += 1
            candidate_ready = False
            if candidate_count > MAX_CANDIDATES:
                _fail("CANDIDATE_LIMIT", "The import contains more than 128 candidates")
        count += 1
        if count > MAX_TOKENS:
            _fail("TOKEN_LIMIT", "The import contains too many tokens")
        if char in "[{":
            if char == "[" and depth == 1 and previous == ":" and last_string == "outbounds":
                candidate_depth, candidate_count, candidate_ready = 2, 0, True
            depth += 1
            if depth > MAX_DEPTH:
                _fail("DEPTH_LIMIT", "The import is nested too deeply")
            index += 1
        elif char in "]}":
            if depth == candidate_depth:
                candidate_depth = None
            depth -= 1
            if depth < 0:
                _fail("PARSE_SYNTAX", "Invalid JSON document")
            index += 1
        elif char == '"':
            start = index
            index += 1
            while index < len(text):
                if index - start > 6 * MAX_STRING_BYTES + 2:
                    _fail("STRING_LIMIT", "A string exceeds the import size limit")
                if text[index] == "\\":
                    index += 2
                elif text[index] == '"':
                    index += 1
                    break
                else:
                    index += 1
            try:
                scalar = json.loads(text[start:index])
            except (ValueError, RecursionError):
                _fail("PARSE_SYNTAX", "Invalid JSON string")
            literal_string(scalar)
            last_string = scalar
        elif char in ",:":
            if char == "," and depth == candidate_depth:
                candidate_ready = True
            index += 1
        else:
            start = index
            while index < len(text) and text[index] not in " \r\n\t{}[],:\"":
                index += 1
            if index == start:
                _fail("PARSE_SYNTAX", "Invalid JSON document")
            token = text[start:index]
            if token in {"NaN", "Infinity", "-Infinity"}:
                _fail("NONFINITE_NUMBER", "NaN and Infinity are not allowed")
            if re.fullmatch(r"-?[0-9]+", token):
                _bounded_int(token)
            elif token[:1] in "-0123456789" and len(token) > 128:
                _fail("NUMBER_LIMIT", "A numeric literal exceeds the import limit")
        previous = char
    if depth:
        _fail("PARSE_SYNTAX", "Invalid JSON document")


def _load_json(text):
    _json_preflight(text)
    try:
        return json.loads(text, object_pairs_hook=_duplicate_object, parse_int=_bounded_int,
                          parse_float=_bounded_float,
                          parse_constant=lambda _: _fail("NONFINITE_NUMBER", "NaN and Infinity are not allowed"))
    except (json.JSONDecodeError, RecursionError):
        _fail("PARSE_SYNTAX", "Invalid JSON document")


def _load_yaml(text):
    # PyYAML is an existing pinned runtime dependency, imported only after caps.
    import yaml
    from yaml import events, tokens

    class StrictLoader(yaml.SafeLoader):
        def construct_mapping(self, node, deep=False):
            if not isinstance(node, yaml.MappingNode):
                _fail("INVALID_SHAPE", "YAML mappings must be string-keyed objects")
            result = {}
            for key_node, value_node in node.value:
                key = self.construct_object(key_node, deep=deep)
                if type(key) is not str:
                    _fail("INVALID_SHAPE", "YAML mappings must be string-keyed objects")
                if key == "<<":
                    _fail("YAML_MERGE", "YAML merge keys are not allowed")
                if key in result:
                    _fail("DUPLICATE_KEY", "Duplicate object keys are not allowed")
                result[key] = self.construct_object(value_node, deep=deep)
            return result

    try:
        count = 0
        for token in yaml.scan(text, Loader=yaml.SafeLoader):
            count += 1
            if count > MAX_TOKENS:
                _fail("TOKEN_LIMIT", "The import contains too many tokens")
            if isinstance(token, (tokens.AliasToken, tokens.AnchorToken)):
                _fail("YAML_REFERENCE", "YAML aliases and anchors are not allowed")
            if isinstance(token, (tokens.TagToken, tokens.DirectiveToken)):
                _fail("YAML_TAG", "YAML tags and directives are not allowed")
            if isinstance(token, tokens.ScalarToken):
                literal_string(token.value)
                if token.value == "<<":
                    _fail("YAML_MERGE", "YAML merge keys are not allowed")
                if token.value.lower() in {".nan", ".inf", "+.inf", "-.inf", "nan", "infinity", "-infinity"}:
                    _fail("NONFINITE_NUMBER", "NaN and Infinity are not allowed")
                # No huge numeric conversion inside SafeLoader's constructors.
                if token.plain and len(token.value) > 64:
                    resolvers = yaml.SafeLoader.yaml_implicit_resolvers.get(token.value[:1], [])
                    if any(tag in {"tag:yaml.org,2002:int", "tag:yaml.org,2002:float"} and pattern.match(token.value)
                           for tag, pattern in resolvers):
                        _fail("NUMBER_LIMIT", "A numeric literal exceeds the import limit")
        depth, documents = 0, 0
        root_key, candidate_depth, candidate_count = None, None, 0
        for event in yaml.parse(text, Loader=yaml.SafeLoader):
            if isinstance(event, events.DocumentStartEvent):
                documents += 1
                if documents > 1:
                    _fail("YAML_MULTIDOCUMENT", "Only one YAML document is allowed")
            elif isinstance(event, (events.MappingStartEvent, events.SequenceStartEvent, events.ScalarEvent)):
                if depth == candidate_depth:
                    candidate_count += 1
                    if candidate_count > MAX_CANDIDATES:
                        _fail("CANDIDATE_LIMIT", "The import contains more than 128 candidates")
                if isinstance(event, events.ScalarEvent):
                    if depth == 1:
                        root_key = event.value if root_key is None else None
                    continue
                if isinstance(event, events.SequenceStartEvent) and depth == 1 and root_key == "proxies":
                    candidate_depth, candidate_count = 2, 0
                depth += 1
                if depth > MAX_DEPTH:
                    _fail("DEPTH_LIMIT", "The import is nested too deeply")
            elif isinstance(event, (events.MappingEndEvent, events.SequenceEndEvent)):
                depth -= 1
                if depth == 1:
                    root_key, candidate_depth = None, None
        return yaml.load(text, Loader=StrictLoader)
    except yaml.YAMLError:
        _fail("PARSE_SYNTAX", "Invalid YAML document")


def _validate_tree(value, depth=0):
    """Second-line type/range checks; preflight already bounded construction."""
    if type(value) is str:
        literal_string(value)
    elif type(value) is int:
        if not MIN_INTEGER <= value <= MAX_INTEGER:
            _fail("INTEGER_LIMIT", "An integer exceeds the signed 64-bit limit")
    elif type(value) is float:
        if not math.isfinite(value):
            _fail("NONFINITE_NUMBER", "NaN and Infinity are not allowed")
    elif value is None or type(value) is bool:
        pass
    elif type(value) in {dict, list}:
        if depth >= MAX_DEPTH:
            _fail("DEPTH_LIMIT", "The import is nested too deeply")
        if type(value) is dict:
            for key, item in value.items():
                if type(key) is not str:
                    _fail("INVALID_SHAPE", "Objects must have string keys")
                literal_string(key)
                _validate_tree(item, depth + 1)
        else:
            for item in value:
                _validate_tree(item, depth + 1)
    else:
        _fail("UNSUPPORTED_VALUE", "The import contains an unsupported scalar type")


def _false_only(value, key):
    if key in value and value[key] is not False:
        _fail("UNSUPPORTED_OPTION", "An unsupported or insecure client option is enabled")


def _base(name, host, port, uid, mode, sni, *, alpn, transport="tcp", flow="", encryption="none",
          fingerprint="", key="", short=""):
    return UpstreamProfile.from_dict({"name": name, "server": host, "server_port": port, "uuid": uid,
        "protocol": "vless", "transport": transport, "flow": flow, "encryption": encryption,
        "tls": {"mode": mode, "server_name": sni, "alpn": alpn,
                "fingerprint": fingerprint, "public_key": key, "short_id": short}})


def _uri_parts(text):
    if len(text.encode("utf-8")) > MAX_URI_BYTES:
        _fail("URI_LIMIT", "A share URI exceeds 8 KiB")
    literal_string(text)
    if re.search(r"%(?![0-9A-Fa-f]{2})", text):
        _fail("INVALID_URI", "Invalid percent encoding in share URI")
    try:
        parts = urlsplit(text)
        if parts.scheme not in KNOWN_URI_SCHEMES:
            _fail("UNSUPPORTED_FORMAT", "Remote subscriptions and unknown URI formats are not supported")
        return parts
    except ValueError as error:
        if isinstance(error, UpstreamValidationError):
            raise
        _fail("INVALID_URI", "Invalid share URI")


def _uri_profile(text):
    parts = _uri_parts(text)
    if parts.scheme != "vless":
        _fail("UNSUPPORTED_PROTOCOL", "Only VLESS upstreams are supported")
    try:
        if parts.path or parts.password is not None or not parts.username or not parts.hostname:
            _fail("INVALID_URI", "VLESS requires one UUID and an explicit host and port")
        if parts.netloc.count("@") != 1:
            _fail("INVALID_URI", "VLESS requires one credential")
        port = parts.port
        if port is None:
            _fail("INVALID_PORT", "An explicit upstream port is required")
        # Authority must not carry silently normalized whitespace or a zone ID.
        raw_port = parts.netloc.rsplit(":", 1)[-1]
        if not re.fullmatch(r"[1-9][0-9]{0,4}", raw_port):
            _fail("INVALID_PORT", "An explicit canonical upstream port is required")
        pairs = parse_qsl(parts.query, keep_blank_values=True, strict_parsing=True,
                          encoding="utf-8", errors="strict", max_num_fields=32)
        query = _duplicate_object(pairs)
        strict_mapping(query, URI_FIELDS, {"security", "sni"})
        for key, value in query.items():
            literal_string(key)
            literal_string(value)
        name = unquote(parts.fragment, encoding="utf-8", errors="strict") if parts.fragment else "Imported upstream"
        uid = unquote(parts.username, encoding="utf-8", errors="strict")
        return _base(name, parts.hostname, port, uid, query["security"], query["sni"],
                     transport=query.get("type", "tcp"), flow=query.get("flow", ""),
                     encryption=query.get("encryption", "none"),
                     alpn=query["alpn"].split(",") if "alpn" in query else [],
                     fingerprint=query.get("fp", ""), key=query.get("pbk", ""), short=query.get("sid", ""))
    except (ValueError, UnicodeError) as error:
        if isinstance(error, UpstreamValidationError):
            raise
        _fail("INVALID_URI", "Invalid share URI")


def _xray_profile(value):
    if type(value) is not dict:
        _fail("INVALID_SHAPE", "An outbound must be an object")
    if value.get("protocol") != "vless":
        _fail("UNSUPPORTED_PROTOCOL", "Only VLESS upstreams are supported")
    strict_mapping(value, {"protocol", "tag", "settings", "streamSettings"}, {"settings", "streamSettings"})
    settings = strict_mapping(value["settings"], {"vnext"}, {"vnext"})
    if type(settings["vnext"]) is not list or len(settings["vnext"]) != 1:
        _fail("MULTIPLE_SERVERS", "Exactly one upstream server per outbound is required")
    remote = strict_mapping(settings["vnext"][0], {"address", "port", "users"}, {"address", "port", "users"})
    if type(remote["users"]) is not list or len(remote["users"]) != 1:
        _fail("MULTIPLE_USERS", "Exactly one explicit UUID per upstream is required")
    user = strict_mapping(remote["users"][0], {"id", "encryption", "flow"}, {"id"})
    stream = strict_mapping(value["streamSettings"], {"network", "security", "tlsSettings", "realitySettings"}, {"security"})
    mode = stream["security"]
    if mode == "tls":
        if "realitySettings" in stream:
            _fail("CONFLICTING_SECURITY", "TLS and REALITY representations conflict")
        tls = strict_mapping(stream.get("tlsSettings"), {"serverName", "allowInsecure", "alpn", "fingerprint"}, {"serverName"})
        _false_only(tls, "allowInsecure")
    elif mode == "reality":
        if "tlsSettings" in stream:
            _fail("CONFLICTING_SECURITY", "TLS and REALITY representations conflict")
        tls = strict_mapping(stream.get("realitySettings"), {"serverName", "fingerprint", "publicKey", "shortId"},
                             {"serverName", "fingerprint", "publicKey", "shortId"})
    else:
        _fail("UNSUPPORTED_SECURITY", "Only verified TLS or REALITY is supported")
    return _base(value.get("tag", "Imported upstream"), remote["address"], remote["port"], user["id"], mode,
                 tls["serverName"], transport=stream.get("network", "tcp"), flow=user.get("flow", ""),
                 encryption=user.get("encryption", "none"), alpn=tls.get("alpn", []),
                 fingerprint=tls.get("fingerprint", ""), key=tls.get("publicKey", ""), short=tls.get("shortId", ""))


def _clash_profile(value):
    if type(value) is not dict:
        _fail("INVALID_SHAPE", "A proxy must be an object")
    if value.get("type") != "vless":
        _fail("UNSUPPORTED_PROTOCOL", "Only VLESS upstreams are supported")
    strict_mapping(value, CLASH_FIELDS, {"type", "server", "port", "uuid", "tls", "servername"})
    if value["tls"] is not True:
        _fail("UNSUPPORTED_SECURITY", "TLS must be explicitly enabled")
    _false_only(value, "skip-cert-verify")
    _false_only(value, "udp")
    reality = value.get("reality-opts")
    mode = "reality" if "reality-opts" in value else "tls"
    if mode == "reality":
        strict_mapping(reality, {"public-key", "short-id"}, {"public-key", "short-id"})
    else:
        reality = {}
    return _base(value.get("name", "Imported upstream"), value["server"], value["port"], value["uuid"], mode,
                 value["servername"], transport=value.get("network", "tcp"), flow=value.get("flow", ""),
                 alpn=value.get("alpn", []), fingerprint=value.get("client-fingerprint", ""),
                 key=reality.get("public-key", ""), short=reality.get("short-id", ""))


def _container(document, source_format):
    if type(document) is not dict:
        _fail("INVALID_SHAPE", "Import a single outbound or a recognized client configuration")
    single_key, list_key = ("protocol", "outbounds") if source_format == "v2ray-json" else ("type", "proxies")
    ignored = XRAY_IGNORED_SECTIONS if source_format == "v2ray-json" else CLASH_IGNORED_SECTIONS
    if single_key in document:
        # Selected-item validation reports unknown fields, including mixed containers.
        return [document], []
    strict_mapping(document, ignored | {list_key})
    if list_key not in document:
        if source_format == "clash-yaml" and "proxy-providers" in document:
            return [], sorted(document)
        _fail("MISSING_CANDIDATES", "The document contains no embedded upstream candidates")
    candidates = document[list_key]
    if type(candidates) is not list:
        _fail("INVALID_SHAPE", "The candidate container must be a list")
    if len(candidates) > MAX_CANDIDATES:
        _fail("CANDIDATE_LIMIT", "The import contains more than 128 candidates")
    return candidates, sorted(document.keys() - {list_key})


def _safe_summary(candidate, source_format, index, profile, error):
    if profile is not None:
        return CandidateSummary(index, profile.name, profile.protocol, profile.server, profile.server_port,
                                profile.transport, profile.tls.mode, True, "", "")
    name, protocol, host, port, transport, mode = "Unsupported upstream", "unknown", "", None, "", ""
    raw_name, raw_host, raw_port, raw_transport, raw_mode = None, None, None, None, None
    if source_format == "uri":
        parts = _uri_parts(candidate)
        protocol = parts.scheme
        if protocol == "vless":
            try:
                raw_host, raw_port = parts.hostname, parts.port
                raw_name = unquote(parts.fragment, encoding="utf-8", errors="strict")
            except (ValueError, UnicodeError):
                pass
    elif type(candidate) is dict:
        key = "protocol" if source_format == "v2ray-json" else "type"
        known = {"vless", "vmess", "trojan", "ss", "shadowsocks", "socks", "http", "hysteria2", "tuic", "freedom", "blackhole", "direct", "reject"}
        if type(candidate.get(key)) is str and candidate[key] in known:
            protocol = candidate[key]
        raw_name = candidate.get("tag" if source_format == "v2ray-json" else "name")
        if source_format == "clash-yaml":
            raw_host, raw_port = candidate.get("server"), candidate.get("port")
            raw_transport = candidate.get("network", "tcp")
            raw_mode = "reality" if "reality-opts" in candidate else ("tls" if candidate.get("tls") is True else "")
        else:
            settings, stream = candidate.get("settings"), candidate.get("streamSettings")
            if type(settings) is dict and type(settings.get("vnext")) is list and len(settings["vnext"]) == 1:
                remote = settings["vnext"][0]
                if type(remote) is dict:
                    raw_host, raw_port = remote.get("address"), remote.get("port")
            if type(stream) is dict:
                raw_transport, raw_mode = stream.get("network", "tcp"), stream.get("security")
    try:
        if type(raw_name) is str and 0 < len(raw_name) <= 128:
            name = literal_string(raw_name)
        host, port = server_host(raw_host), server_port(raw_port)
    except UpstreamValidationError:
        host, port = "", None
    if type(raw_transport) is str and raw_transport in {"tcp", "ws", "grpc", "raw", "xhttp", "httpupgrade", "kcp", "http", "quic"}:
        transport = raw_transport
    if type(raw_mode) is str and raw_mode in {"tls", "reality", "none"}:
        mode = raw_mode
    return CandidateSummary(index, name, protocol, host, port, transport, mode, False, error.code, str(error))


def _parse(raw, source_format, selected_index):
    text = raw.decode("utf-8", errors="strict").strip(" \t\r\n")
    if not text:
        _fail("EMPTY_INPUT", "Provide an upstream share URI or configuration")
    if source_format == "auto":
        if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", text):
            source_format = "uri"
        elif text.startswith(("{", "[")):
            source_format = "v2ray-json"
        else:
            source_format = "clash-yaml"
    if source_format == "uri":
        _uri_parts(text)
        candidates, ignored, convert = [text], [], _uri_profile
    else:
        document = _load_json(text) if source_format == "v2ray-json" else _load_yaml(text)
        _validate_tree(document)
        candidates, ignored = _container(document, source_format)
        convert = _xray_profile if source_format == "v2ray-json" else _clash_profile
    if selected_index is not None and selected_index >= len(candidates):
        _fail("INVALID_SELECTION", "Select an explicit candidate index from the preview")
    summaries = []
    for index, candidate in enumerate(candidates):
        profile, error = None, None
        try:
            profile = convert(candidate)
        except UpstreamValidationError as rejected:
            error = rejected
        if selected_index == index:
            if error:
                raise error
            return profile.to_dict()
        summaries.append(_safe_summary(candidate, source_format, index, profile, error).to_dict())
    return {"source_format": source_format, "source_fingerprint": hashlib.sha256(raw).hexdigest(),
            "candidates": summaries, "ignored_sections": ignored,
            "notice_codes": [] if candidates else ["NO_EMBEDDED_NODES"]}


def _set_worker_limits():
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (PARSER_MEMORY_BYTES, PARSER_MEMORY_BYTES))
        resource.setrlimit(resource.RLIMIT_CPU, (PARSER_TIMEOUT_SECONDS, PARSER_TIMEOUT_SECONDS))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    except (ImportError, OSError, ValueError):
        _fail("PARSER_LIMIT_UNAVAILABLE", "Required isolated parser resource limits are unavailable")


def _worker_main():
    try:
        _set_worker_limits()
        raw = _input_bytes(sys.stdin.buffer.read(MAX_INPUT_BYTES + 1))
        source_format = _source_format(sys.argv[2])
        selected = None if sys.argv[3] == "preview" else int(sys.argv[3])
        result = {"ok": True, "value": _parse(raw, source_format, selected)}
    except UpstreamValidationError as error:
        result = {"ok": False, "code": error.code, "message": str(error)}
    except (MemoryError, RecursionError):
        result = {"ok": False, "code": "PARSER_RESOURCE_LIMIT", "message": "The import parser exceeded its resource limits"}
    except Exception:
        # YAML/URI/parser tracebacks can include full credentials. Never return them.
        result = {"ok": False, "code": "PARSE_SYNTAX", "message": "The import could not be parsed safely"}
    sys.stdout.write(json.dumps(result, ensure_ascii=True, allow_nan=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[1] != "--parse-worker":
        raise SystemExit(64)
    raise SystemExit(_worker_main())
