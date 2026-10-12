"""Bare fixed-core fixtures, not production transport profiles or exporters."""
from urllib.parse import urlencode

UUID = '11111111-1111-4111-8111-111111111111'
WRONG_UUID = '22222222-2222-4222-8222-222222222222'
SNI = 'vpn.example.test'
WRONG_SNI = 'wrong.example.test'
PATH = '/vui-xhttp/'
HOST = SNI
MODE = 'stream-one'
TARGET_BODY = b'VUI-LOOPBACK-TARGET'
FAILURES = ('uuid', 'ca', 'sni', 'path', 'host', 'mode')


def xray_server(port, cert, key):
    return {'log': {'loglevel': 'debug'}, 'inbounds': [{
        'tag': 'xhttp-in', 'listen': '127.0.0.1', 'port': port, 'protocol': 'vless',
        'settings': {'clients': [{'id': UUID}], 'decryption': 'none'},
        'streamSettings': {'network': 'xhttp', 'security': 'tls',
            'tlsSettings': {'alpn': ['h2'], 'certificates': [
                {'certificateFile': str(cert), 'keyFile': str(key)}]},
            'xhttpSettings': {'path': PATH, 'host': HOST, 'mode': MODE}}}],
        'outbounds': [{'protocol': 'freedom', 'tag': 'application'}]}


def xhttp_values(failure=None):
    return {'uuid': WRONG_UUID if failure == 'uuid' else UUID,
        'sni': WRONG_SNI if failure == 'sni' else SNI,
        'path': '/unrelated-xhttp/' if failure == 'path' else PATH,
        'host': 'other.example.test' if failure == 'host' else HOST,
        'mode': 'packet-up' if failure == 'mode' else MODE}


def mihomo_proxy(server_port, *, failure=None, httpupgrade=False):
    values = xhttp_values(failure)
    proxy = {'name': 'ONLY_PROXY', 'type': 'vless', 'server': '127.0.0.1',
        'port': server_port, 'uuid': values['uuid'], 'udp': False,
        'tls': True, 'servername': values['sni'], 'skip-cert-verify': False,
        'client-fingerprint': 'chrome', 'alpn': ['http/1.1' if httpupgrade else 'h2']}
    if httpupgrade:
        proxy.update({'network': 'ws', 'ws-opts': {'path': PATH,
            'headers': {'Host': HOST}, 'v2ray-http-upgrade': True}})
    else:
        proxy.update({'network': 'xhttp', 'xhttp-opts': {
            key: values[key] for key in ('path', 'host', 'mode')}})
    return proxy


def share_uri(server_port, *, failure=None, httpupgrade=False):
    """Emit the actual URI input; never rewrite the pinned converter's output."""
    values = xhttp_values(failure)
    params = {'security': 'tls', 'type': 'httpupgrade' if httpupgrade else 'xhttp',
        'encryption': 'none', 'sni': values['sni'], 'fp': 'chrome',
        'alpn': 'http/1.1' if httpupgrade else 'h2',
        'path': values['path'], 'host': values['host']}
    if not httpupgrade:
        params['mode'] = values['mode']
    return f"vless://{values['uuid']}@127.0.0.1:{server_port}?"+urlencode(params)+'#URI_ONLY'


def mihomo_config(port, *, proxy=None, provider=None):
    if (proxy is None) == (provider is None):
        raise ValueError('Exactly one explicit proxy or file provider is required')
    config = {'mixed-port': port, 'bind-address': '127.0.0.1', 'allow-lan': False,
        'mode': 'rule', 'log-level': 'debug', 'ipv6': False, 'rules': ['MATCH,ONLY_PROXY']}
    if proxy is not None:
        config['proxies'] = [proxy]
    else:
        config['proxy-providers'] = {'uri': {'type': 'file', 'path': str(provider)}}
        config['proxy-groups'] = [{'name': 'ONLY_PROXY', 'type': 'select', 'use': ['uri']}]
    return config


def singbox_xhttp_client(server_port):
    return {'outbounds': [{'type': 'vless', 'tag': 'ONLY_PROXY', 'server': '127.0.0.1',
        'server_port': server_port, 'uuid': UUID, 'network': 'tcp',
        'tls': {'enabled': True, 'server_name': SNI, 'alpn': ['h2']},
        'transport': {'type': 'xhttp', 'path': PATH, 'host': HOST, 'mode': MODE}}],
        'route': {'final': 'ONLY_PROXY'}}


def singbox_gap_server(port, cert, key, *, httpupgrade=True):
    inbound = {'type': 'vless', 'tag': 'gap-control', 'listen': '127.0.0.1',
        'listen_port': port, 'users': [{'uuid': UUID}],
        'tls': {'enabled': True, 'server_name': SNI, 'certificate_path': str(cert),
                'key_path': str(key), 'alpn': ['http/1.1']}}
    if httpupgrade:
        inbound['transport'] = {'type': 'httpupgrade', 'host': HOST, 'path': PATH}
    return {'log': {'level': 'debug', 'timestamp': False}, 'inbounds': [inbound],
        'outbounds': [{'type': 'direct', 'tag': 'application'}], 'route': {'final': 'application'}}


def rejection_reason(failure):
    """Exact fixed-source reasons, kept distinct from timeout/EOF or old logs."""
    return {'uuid': ('server', ('invalid request user id:', WRONG_UUID)),
        'ca': ('client', ('x509', 'unknown authority')),
        'sni': ('client', ('x509', WRONG_SNI)),
        'path': ('server', ('failed to validate path', '/unrelated-xhttp/')),
        'host': ('server', ('failed to validate host', 'other.example.test')),
        'mode': ('server', ('packet-up mode is not allowed',))}[failure]


# The fixed Mihomo relay does not log VLESS read errors. These helpers observe
# actual bytes without becoming a transport adapter, proxy, or production path.
WIRE_LIMIT = 4096


def read_protocol_bytes(conn, *, deadline=None):
    """Read one bounded complete header, rejecting EOF/timeout as evidence."""
    import time
    payload = bytearray()
    while b'\r\n\r\n' not in payload:
        if len(payload) >= WIRE_LIMIT:
            raise ValueError('Protocol observation exceeded the byte limit')
        try:
            if deadline is not None:
                remaining = deadline-time.monotonic()
                if remaining <= 0:
                    raise TimeoutError
                conn.settimeout(remaining)
            chunk = conn.recv(WIRE_LIMIT + 1 - len(payload))
        except TimeoutError:
            raise ValueError('Timed out before complete protocol bytes') from None
        if not chunk:
            raise ValueError('EOF before complete protocol bytes')
        payload.extend(chunk)
        if len(payload) > WIRE_LIMIT:
            raise ValueError('Protocol observation exceeded the byte limit')
    if not payload.endswith(b'\r\n\r\n'):
        raise ValueError('Unexpected bytes after the complete request header')
    return bytes(payload)


def _http_headers(payload):
    if not isinstance(payload, bytes) or not 1 <= len(payload) <= WIRE_LIMIT:
        raise ValueError('Missing or oversized protocol bytes')
    if not payload.endswith(b'\r\n\r\n') or payload.count(b'\r\n\r\n') != 1:
        raise ValueError('Incomplete or multiple HTTP request headers')
    lines = payload[:-4].split(b'\r\n')
    headers = {}
    for line in lines[1:]:
        key, separator, value = line.partition(b':')
        if not separator or not key or key.lower() in headers:
            raise ValueError('Malformed or duplicate HTTP request header')
        headers[key.lower()] = value.strip()
    if headers.get(b'content-length', b'0') != b'0' or b'transfer-encoding' in headers:
        raise ValueError('Unexpected request body in protocol observation')
    return lines[0], headers


def classify_protocol_bytes(payload, target_port):
    """Recognize only the two exact synthetic protocols expected by this test."""
    import uuid
    if not isinstance(payload, bytes) or len(payload) > WIRE_LIMIT:
        raise ValueError('Missing or oversized protocol bytes')
    if payload.startswith(b'GET '):
        first, headers = _http_headers(payload)
        if first != f'GET {PATH} HTTP/1.1'.encode():
            raise ValueError('Wrong HTTPUpgrade request line')
        if headers.get(b'host') != HOST.encode():
            raise ValueError('Wrong HTTPUpgrade Host')
        if headers.get(b'connection', b'').lower() != b'upgrade':
            raise ValueError('Missing HTTPUpgrade Connection header')
        if headers.get(b'upgrade', b'').lower() != b'websocket':
            raise ValueError('Missing HTTPUpgrade Upgrade header')
        if b'sec-websocket-key' in headers:
            raise ValueError('Ordinary WebSocket is not the HTTPUpgrade control')
        return 'httpupgrade'
    if not payload or payload[0] != 0:
        raise ValueError('Unknown protocol bytes; expected HTTPUpgrade or raw VLESS')
    if len(payload) < 26:
        raise ValueError('Truncated VLESS request header')
    if payload[1:17] != uuid.UUID(UUID).bytes:
        raise ValueError('Wrong observed fake VLESS UUID')
    if payload[17] != 0:
        raise ValueError('Unexpected VLESS addons')
    if payload[18] != 1:
        raise ValueError('Wrong VLESS command; TCP is required')
    if payload[19:21] != target_port.to_bytes(2, 'big') or payload[21:26] != b'\x01\x7f\x00\x00\x01':
        raise ValueError('Wrong VLESS loopback destination')
    first, headers = _http_headers(payload[26:])
    allowed = (b'GET /probe HTTP/1.1', f'GET http://127.0.0.1:{target_port}/probe HTTP/1.1'.encode())
    if first not in allowed or headers.get(b'host') != f'127.0.0.1:{target_port}'.encode():
        raise ValueError('Wrong VLESS application request')
    return 'vless'


def require_httpupgrade_bad_request(status, reason, body):
    """Generic errors, disconnects and timeouts cannot replace the real response."""
    if (status, reason, body) != (400, 'Bad Request', b'400 Bad Request'):
        raise ValueError('Missing actual fixed-server HTTP400 Bad Request response')


def chain_cleanup_errors(errors):
    """Preserve nested cleanup causes when appending later boundary failures."""
    first = tail = errors[0]
    seen = {id(first)}
    for error in errors[1:]:
        while tail.__cause__ is not None and id(tail.__cause__) not in seen:
            tail = tail.__cause__
            seen.add(id(tail))
        if id(error) not in seen:
            tail.__cause__ = error
            tail = error
            seen.add(id(error))
    return first


class ProtocolObservations:
    """Bounded, sticky evidence and dispatch accounting, never a success oracle."""
    def __init__(self):
        import threading
        self.lock = threading.Condition()
        self.records = []
        self.release = threading.Event()
        self.accepted = self.active = 0
        self.failure = None

    def begin(self):
        with self.lock:
            if self.accepted >= 16:
                self.failure = 'Protocol recorder connection limit exceeded'
                return False
            self.accepted += 1
            self.active += 1
            return True

    def end(self):
        with self.lock:
            self.active -= 1
            self.lock.notify_all()

    def append(self, record):
        with self.lock:
            if len(self.records) >= 16:
                self.failure = 'Protocol recorder record limit exceeded'
            else:
                self.records.append(dict(record))

    def snapshot(self):
        with self.lock:
            records = [dict(record) for record in self.records]
            if self.failure:
                records.append({'error': self.failure})
            return records

    def fail(self, message):
        with self.lock:
            self.failure = self.failure or message

    def state(self):
        with self.lock:
            return {'accepted': self.accepted, 'active': self.active}

    def drain(self, timeout=5):
        with self.lock:
            if not self.lock.wait_for(lambda: self.active == 0, timeout=timeout):
                self.failure = 'Protocol recorder drain timed out'
            if len(self.records) != self.accepted:
                self.failure = self.failure or 'Protocol recorder completion count mismatch'
            if self.failure:
                raise AssertionError(self.failure)


def start_protocol_recorder(stack, cert, key):
    """Separate verified-TLS observation endpoint; never forwards any bytes."""
    import socketserver
    import ssl
    import threading
    import time
    observations = ProtocolObservations()
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.set_alpn_protocols(['http/1.1'])
    context.set_servername_callback(lambda sock, name, original: setattr(sock, '_vui_sni', name))

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            deadline = time.monotonic()+4
            self.request.settimeout(4)
            try:
                with context.wrap_socket(self.request, server_side=True) as conn:
                    record = {'sni': getattr(conn, '_vui_sni', None),
                        'alpn': conn.selected_alpn_protocol(), 'tls': conn.version(),
                        'payload': read_protocol_bytes(conn, deadline=deadline)}
                    observations.append(record)
                    # Hold without inventing a response; cleanup first stops
                    # the client, then releases all completed observations.
                    observations.release.wait(8)
            except (OSError, ValueError) as exc:
                observations.append({'error': type(exc).__name__+': '+str(exc)[:160]})

    class Server(socketserver.ThreadingTCPServer):
        daemon_threads = True

        def process_request(self, request, client_address):
            # Claim before spawning: shutdown synchronizes this dispatch with
            # sealing, unlike accounting only when a handler happens to run.
            if not observations.begin():
                self.shutdown_request(request)
                return
            try:
                super().process_request(request, client_address)
            except Exception:
                observations.append({'error': 'Protocol handler dispatch failed'})
                observations.end()
                self.shutdown_request(request)

        def process_request_thread(self, request, client_address):
            try:
                super().process_request_thread(request, client_address)
            except BaseException:
                # ThreadingMixIn does not catch a shutdown_request exception.
                # Even teardown after valid bytes must leave sticky failure.
                observations.fail('Protocol handler teardown failed')
            finally:
                observations.end()

        def handle_error(self, request, client_address):
            observations.append({'error': 'Unexpected protocol handler failure'})

    server = Server(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .02}, daemon=True)
    thread.start()

    def close():
        observations.release.set()

        def join():
            thread.join(timeout=2)
            if thread.is_alive():
                raise AssertionError('Protocol serving thread did not stop')

        errors = []
        for action in (server.shutdown, server.server_close, join, observations.drain):
            try:
                action()
            except BaseException as exc:
                observations.fail('Protocol recorder cleanup failed')
                errors.append(exc)
        if errors:
            raise chain_cleanup_errors(errors)
    stack.callback(close)
    return server.server_address[1], observations
