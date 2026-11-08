"""Test-only Cloudflare HTTPS fixture and real dual-transport DNS answers.

Only the injected transport rewrites a fixed production CF URL to the known
loopback TLS server. The production endpoint/hook API is never changed. Tokens,
TXT validation values and private keys are never printed or put in reports.
"""
from copy import deepcopy
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import re
import secrets
import ssl
import struct
import threading
from urllib.parse import parse_qs, urlsplit

ZONE_ID = '1' * 32
ZONE_NAME = 'example.test'
OTHER_ID = '9' * 32
OTHER_VALUE = 'unrelated operator TXT fixture'
MAX_BODY = 256 * 1024


def strict_json(raw):
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result: raise ValueError('fixture duplicate JSON key')
            result[key] = value
        return result
    if type(raw) is not bytes or len(raw) > MAX_BODY: raise ValueError('fixture JSON size')
    value = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError('fixture nonfinite JSON')))
    json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')
    return value


class FakeCloudflareState:
    """Pure bounded record/router state shared with the DNS responder thread."""
    def __init__(self, token, owner='_acme-challenge.example.test'):
        self.token = token
        self.lock = threading.RLock()
        self.owner = owner
        self.records = {OTHER_ID: dict(id=OTHER_ID, zone_id=ZONE_ID, type='TXT', name=owner,
                                      content=OTHER_VALUE, comment='operator-owned unrelated record')}
        self.created = []; self.created_ownerships = []; self.deleted = []; self.max_owned = 0
        self.http_requests = 0; self.protocol_errors = 0
        self.udp_queries = 0; self.tcp_queries = 0; self.returned_owned = 0
        self.mode = 'normal'
        self.phase = 'issue'; self.queries_by_phase = {}

    def _envelope(self, result, *, rows=False, query=None):
        value = {'success': True, 'errors': [], 'result': result}
        if rows:
            query = query or {}; page = int(query.get('page', ['1'])[0]); per_page = int(query.get('per_page', ['50'])[0])
            if not 1 <= page <= 2 or not 1 <= per_page <= 50: raise ValueError('fixture pagination')
            count = len(result); offset = (page - 1) * per_page
            value['result'] = result[offset:offset + per_page]
            value['result_info'] = {'page': page, 'per_page': per_page, 'count': len(value['result']),
                'total_count': count, 'total_pages': (count + per_page - 1) // per_page}
        return 200, json.dumps(value, separators=(',', ':')).encode()

    def respond(self, method, path, body, authorization):
        with self.lock:
            self.http_requests += 1
            if not secrets.compare_digest(authorization or '', 'Bearer ' + self.token):
                return 403, b'{"success":false,"errors":[{"code":10000}]}'
            target = urlsplit(path)
            if len(path) > 8192 or target.scheme or target.netloc: raise ValueError('fixture path')
            query = parse_qs(target.query, strict_parsing=True) if target.query else {}
            if method == 'GET' and target.path == '/client/v4/zones':
                rows = [{'id': ZONE_ID, 'name': ZONE_NAME}] if query.get('name') == [ZONE_NAME] else []
                return self._envelope(rows, rows=True, query=query)
            prefix = '/client/v4/zones/' + ZONE_ID + '/dns_records'
            if target.path == prefix:
                if method == 'GET':
                    rows = list(self.records.values())
                    for field, key in (('type', 'type'), ('name.exact', 'name'), ('comment.exact', 'comment')):
                        if field in query: rows = [row for row in rows if row[key] == query[field][0]]
                    return self._envelope(deepcopy(rows), rows=True, query=query)
                if method == 'POST':
                    value = strict_json(body)
                    if (type(value) is not dict or set(value) != {'type', 'name', 'content', 'comment'}
                            or value['type'] != 'TXT' or value['name'] != self.owner
                            or type(value['content']) is not str or not re.fullmatch(r'[A-Za-z0-9_-]{43}', value['content'])
                            or not re.fullmatch(r'v-ui:[a-f0-9]{64}', value['comment'])):
                        raise ValueError('fixture TXT shape')
                    identifier = format(len(self.created) + 100, '032x')
                    record = dict(id=identifier, zone_id=ZONE_ID, **value)
                    self.records[identifier] = record; self.created.append(identifier); self.created_ownerships.append(value['comment'])
                    owned = sum(row['name'] == self.owner and row['comment'].startswith('v-ui:') for row in self.records.values())
                    self.max_owned = max(self.max_owned, owned)
                    if self.mode == 'write-unknown': return 503, b'{"success":false,"errors":[]}'
                    return self._envelope(deepcopy(record))
            elif target.path.startswith(prefix + '/'):
                selected = target.path[len(prefix) + 1:]
                if not re.fullmatch(r'[a-f0-9]{32}', selected): raise ValueError('fixture record ID')
                if selected not in self.records: return 404, b'{"success":false,"errors":[]}'
                if method == 'GET': return self._envelope(deepcopy(self.records[selected]))
                if method == 'DELETE':
                    if selected == OTHER_ID: raise ValueError('fixture attempted unrelated deletion')
                    self.records.pop(selected); self.deleted.append(selected)
                    return self._envelope({'id': selected})
            raise ValueError('fixture unsupported request')

    def summary(self):
        with self.lock:
            return {'created': len(self.created), 'unique_ownership_posts': len(set(self.created_ownerships)),
                'deleted': len(self.deleted), 'max_owned_same_owner': self.max_owned,
                'unrelated_preserved': self.records.get(OTHER_ID, {}).get('content') == OTHER_VALUE,
                'owned_remaining': len(self.records) - int(OTHER_ID in self.records), 'http_requests': self.http_requests,
                'protocol_errors': self.protocol_errors, 'udp_queries': self.udp_queries, 'tcp_queries': self.tcp_queries,
                'returned_owned': self.returned_owned, 'queries_by_phase': deepcopy(self.queries_by_phase)}


class CloudflareDNSAnswers:
    def __init__(self, state): self.state = state

    @staticmethod
    def question(packet):
        if not 12 <= len(packet) <= 65535: raise ValueError('fixture DNS packet')
        pos = 12; labels = []
        while pos < len(packet) and packet[pos]:
            length = packet[pos]
            if length > 63 or pos + length + 1 >= len(packet): raise ValueError('fixture DNS label')
            labels.append(packet[pos + 1:pos + 1 + length].decode('ascii').lower()); pos += length + 1
        pos += 1
        if pos + 4 > len(packet): raise ValueError('fixture DNS question')
        return '.'.join(labels), struct.unpack('!H', packet[pos:pos + 2])[0], pos + 4

    def answer_udp(self, packet):
        _, _, end = self.question(packet)
        with self.state.lock: self.state.udp_queries += 1
        # Real UDP response explicitly requests TCP fallback. It never declares
        # challenge validity; full TCP TXT records still have to match the CA.
        return packet[:2] + struct.pack('!HHHHH', 0x8380, 1, 0, 0, 0) + packet[12:end]

    def answer_tcp(self, packet):
        with self.state.lock: self.state.tcp_queries += 1
        return self.answer(packet)

    def answer(self, packet):
        name, kind, end = self.question(packet); answers = []
        with self.state.lock:
            if kind == 16:
                self.state.queries_by_phase[self.state.phase] = self.state.queries_by_phase.get(self.state.phase, 0) + 1
                rows = [row for row in self.state.records.values() if row['name'] == name and row['type'] == 'TXT']
                if self.state.mode == 'no-propagation': rows = [row for row in rows if row['id'] == OTHER_ID]
                for row in rows:
                    raw = row['content'].encode('ascii')
                    if not 0 < len(raw) <= 255: raise ValueError('fixture TXT size')
                    answers.append(bytes([len(raw)]) + raw)
                    self.state.returned_owned += int(row['id'] != OTHER_ID)
            elif kind == 1 and (name == ZONE_NAME or name.endswith('.' + ZONE_NAME)):
                answers.append(b'\x7f\x00\x00\x01')
        records = b''.join(b'\xc0\x0c' + struct.pack('!HHIH', kind, 1, 0, len(raw)) + raw for raw in answers)
        return packet[:2] + struct.pack('!HHHHH', 0x8180, 1, len(answers), 0, 0) + packet[12:end] + records


class LoopbackCloudflareTransport:
    def __init__(self, port, ca_file):
        if type(port) is not int or not 1024 <= port <= 65535: raise ValueError('fixture loopback port')
        self.port, self.ca_file = port, str(ca_file)

    def __call__(self, method, url, *, headers, body, timeout, max_bytes):
        target = urlsplit(url)
        if (method not in ('GET', 'POST', 'DELETE') or target.scheme != 'https'
                or target.netloc != 'api.cloudflare.com' or not target.path.startswith('/client/v4/')
                or target.fragment or type(timeout) not in (int, float) or not math.isfinite(timeout)
                or not 0 < timeout <= 10 or type(max_bytes) is not int or not 0 < max_bytes <= MAX_BODY):
            raise ValueError('fixture fixed CF transport refused')
        context = ssl.create_default_context(cafile=self.ca_file)
        if not context.check_hostname or context.verify_mode != ssl.CERT_REQUIRED:
            raise ValueError('fixture TLS verification required')
        connection = http.client.HTTPSConnection('127.0.0.1', self.port, timeout=timeout, context=context)
        try:
            connection.request(method, target.path + ('?' + target.query if target.query else ''), body=body, headers=headers)
            response = connection.getresponse()
            raw = response.read(max_bytes + 1)
            if len(raw) > max_bytes: raise ValueError('fixture bounded response')
            # Status is returned to the real CF validator; no redirect is followed.
            return response.status, raw
        finally:
            connection.close()


def start_cloudflare_https(stack, state, certificate, private_key):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def respond(self):
            self.connection.settimeout(10)
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 <= length <= MAX_BODY: raise ValueError('fixture request size')
                body = self.rfile.read(length) if length else None
                status, raw = state.respond(self.command, self.path, body, self.headers.get('Authorization'))
            except Exception:
                with state.lock: state.protocol_errors += 1
                status, raw = 500, b'{"success":false,"errors":[]}'
            self.send_response(status); self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw))); self.end_headers()
            try: self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError): pass
        do_GET = respond; do_POST = respond; do_DELETE = respond
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler); server.daemon_threads = True
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); context.load_cert_chain(certificate, private_key)
    context.set_alpn_protocols(['http/1.1']); server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .02}, daemon=True); thread.start()
    def close(): server.shutdown(); server.server_close(); thread.join(timeout=3)
    stack.callback(close)
    return server.server_port
