"""Local-only test fixtures; never install trust roots or change system DNS."""
from __future__ import annotations
import base64
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
from pathlib import Path
import secrets
import socket
import socketserver
import ssl
import struct
import subprocess
import threading
import time
from urllib.parse import parse_qs, urlsplit
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


def certificate_files(root: Path, name: str = 'test') -> tuple[Path, Path, Path]:
    now = datetime.now(timezone.utc)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'VUI isolated test CA ' + name)])
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name)
          .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(now-timedelta(minutes=5)).not_valid_after(now+timedelta(days=1))
          .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
          .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False,
                         key_encipherment=False, data_encipherment=False, key_agreement=False,
                         key_cert_sign=True, crl_sign=True, encipher_only=False, decipher_only=False), critical=True)
          .sign(ca_key, hashes.SHA256()))
    key = ec.generate_private_key(ec.SECP256R1())
    cert = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'vpn.example.test')]))
            .issuer_name(ca_name).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now-timedelta(minutes=5)).not_valid_after(now+timedelta(hours=12))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName('vpn.example.test'),
                           x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .sign(ca_key, hashes.SHA256()))
    paths = root/(name+'-ca.pem'), root/(name+'-cert.pem'), root/(name+'-key.pem')
    paths[0].write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    paths[1].write_bytes(cert.public_bytes(serialization.Encoding.PEM)+ca.public_bytes(serialization.Encoding.PEM))
    paths[2].write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    for path in paths: path.chmod(0o600)
    return paths


def dns_query(name: str, query_type: int = 16) -> bytes:
    encoded = b''.join(bytes([len(label)]) + label.encode('ascii') for label in name.split('.')) + b'\x00'
    return secrets.token_bytes(2) + struct.pack('!HHHHH', 0x0100, 1, 0, 0, 0) + encoded + struct.pack('!HH', query_type, 1)


def question(packet: bytes) -> tuple[str, int, int]:
    pos, labels = 12, []
    while packet[pos]:
        size = packet[pos]
        if size > 63 or pos + size + 1 >= len(packet): raise ValueError('Invalid test DNS question')
        labels.append(packet[pos+1:pos+1+size].decode('ascii').lower()); pos += size+1
    pos += 1
    return '.'.join(labels), struct.unpack('!H', packet[pos:pos+2])[0], pos+4


class DnsAnswers:
    def __init__(self, marker: str, hosts: dict[str, str] | None = None):
        self.marker, self.hosts, self.queries = marker, hosts or {}, []
        self.lock = threading.Lock()

    def answer(self, packet: bytes) -> bytes:
        name, kind, end = question(packet)
        with self.lock: self.queries.append((name, kind))
        data = None
        if kind == 1 and name in self.hosts: data = socket.inet_aton(self.hosts[name])
        if kind == 16:
            raw = self.marker.encode(); data = bytes([len(raw)]) + raw
        answer = b'' if data is None else b'\xc0\x0c' + struct.pack('!HHIH', kind, 1, 0, len(data)) + data
        return packet[:2] + struct.pack('!HHHHH', 0x8180, 1, bool(answer), 0, 0) + packet[12:end] + answer


def start_udp_dns(stack: ExitStack, answers: DnsAnswers):
    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            packet, transport = self.request
            try: transport.sendto(answers.answer(packet), self.client_address)
            except (ValueError, IndexError, UnicodeError): pass
    server = socketserver.ThreadingUDPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval':0.02}, daemon=True); thread.start()
    def close(): server.shutdown(); server.server_close(); thread.join(timeout=2)
    stack.callback(close)
    return server.server_address[1]


def start_doh(stack: ExitStack, answers: DnsAnswers, cert: Path, key: Path):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        def log_message(self, *args): pass
        def do_POST(self):
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size < 65536: self.send_error(400); return
            self.respond(self.rfile.read(size))
        def do_GET(self):
            encoded = parse_qs(urlsplit(self.path).query).get('dns', [''])[0]
            try: self.respond(base64.urlsafe_b64decode(encoded + '='*(-len(encoded)%4)))
            except (ValueError, IndexError): self.send_error(400)
        def respond(self, packet):
            data = answers.answer(packet)
            self.send_response(200); self.send_header('Content-Type','application/dns-message')
            self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); context.load_cert_chain(cert, key)
    context.set_alpn_protocols(['http/1.1'])
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval':0.02}, daemon=True); thread.start()
    def close(): server.shutdown(); server.server_close(); thread.join(timeout=2)
    stack.callback(close)
    return server.server_address[1]


def start_http_target(stack: ExitStack):
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            requests.append((self.headers.get('Host'), self.path))
            data = b'VUI-LOOPBACK-TARGET'
            self.send_response(200); self.send_header('Content-Length',str(len(data)))
            self.end_headers(); self.wfile.write(data)
    server = ThreadingHTTPServer(('127.0.0.1',0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval':0.02}, daemon=True); thread.start()
    def close(): server.shutdown(); server.server_close(); thread.join(timeout=2)
    stack.callback(close)
    return server.server_address[1], requests


def start_udp_echo(stack: ExitStack):
    packets = []
    lock = threading.Lock()

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            packet, transport = self.request
            with lock:
                packets.append(packet)
            transport.sendto(b"VUI-UDP-ECHO:" + packet, self.client_address)

    server = socketserver.ThreadingUDPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(
        target=server.serve_forever,
        kwargs={"poll_interval": 0.02},
        daemon=True,
    )
    thread.start()

    def close():
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    stack.callback(close)
    return server.server_address[1], packets


def _recv_exact(stream: socket.socket, size: int) -> bytes:
    value=b""
    while len(value)<size:
        chunk=stream.recv(size-len(value))
        if not chunk:
            raise OSError("SOCKS5 control channel closed")
        value+=chunk
    return value


def _socks_address_from_stream(stream: socket.socket, atyp: int) -> tuple[str,int]:
    if atyp == 1:
        host=socket.inet_ntop(socket.AF_INET,_recv_exact(stream,4))
    elif atyp == 4:
        host=socket.inet_ntop(socket.AF_INET6,_recv_exact(stream,16))
    elif atyp == 3:
        length=_recv_exact(stream,1)[0]
        host=_recv_exact(stream,length).decode("ascii")
    else:
        raise OSError("Unsupported SOCKS5 address type")
    port=struct.unpack("!H",_recv_exact(stream,2))[0]
    return host,port


def _socks_udp_target(host: str, port: int) -> bytes:
    try:
        raw=socket.inet_pton(socket.AF_INET,host)
        address=b"\x01"+raw
    except OSError:
        try:
            raw=socket.inet_pton(socket.AF_INET6,host)
            address=b"\x04"+raw
        except OSError:
            encoded=host.encode("idna")
            if not 1 <= len(encoded) <= 255:
                raise OSError("Invalid SOCKS5 UDP target")
            address=b"\x03"+bytes([len(encoded)])+encoded
    return address+struct.pack("!H",port)


def _strip_socks_udp(packet: bytes) -> bytes:
    if len(packet)<4 or packet[:2]!=b"\x00\x00" or packet[2]!=0:
        raise OSError("Invalid SOCKS5 UDP response")
    pos=3
    atyp=packet[pos];pos+=1
    if atyp==1:
        pos+=4
    elif atyp==4:
        pos+=16
    elif atyp==3:
        if pos>=len(packet): raise OSError("Invalid SOCKS5 UDP domain")
        size=packet[pos];pos+=1+size
    else:
        raise OSError("Invalid SOCKS5 UDP address type")
    pos+=2
    if pos>len(packet): raise OSError("Truncated SOCKS5 UDP response")
    return packet[pos:]


def socks5_udp_through(proxy_port: int, host: str, target_port: int, payload: bytes) -> bytes:
    with socket.create_connection(("127.0.0.1",proxy_port),timeout=3) as control:
        control.settimeout(3)
        control.sendall(b"\x05\x01\x00")
        if _recv_exact(control,2)!=b"\x05\x00":
            raise OSError("SOCKS5 no-auth negotiation failed")
        control.sendall(b"\x05\x03\x00\x01\x00\x00\x00\x00\x00\x00")
        reply=_recv_exact(control,4)
        if reply[:3]!=b"\x05\x00\x00":
            raise OSError("SOCKS5 UDP associate failed")
        relay_host,relay_port=_socks_address_from_stream(control,reply[3])
        if relay_host=="0.0.0.0":
            relay_host="127.0.0.1"
        elif relay_host=="::":
            relay_host="::1"
        family=socket.AF_INET6 if ":" in relay_host else socket.AF_INET
        with socket.socket(family,socket.SOCK_DGRAM) as udp:
            udp.settimeout(3)
            request=b"\x00\x00\x00"+_socks_udp_target(host,target_port)+payload
            udp.sendto(request,(relay_host,relay_port))
            response,_=udp.recvfrom(65535)
        return _strip_socks_udp(response)


def unused_port() -> int:
    with socket.socket() as sock: sock.bind(('127.0.0.1',0)); return sock.getsockname()[1]


def http_through(proxy_port: int, host: str, target_port: int) -> tuple[int, bytes]:
    connection = http.client.HTTPConnection('127.0.0.1', proxy_port, timeout=3)
    try:
        connection.request('GET', f'http://{host}:{target_port}/probe', headers={'Connection':'close'})
        response = connection.getresponse()
        return response.status, response.read()
    finally: connection.close()


def query_txt(port: int, name: str) -> bytes:
    packet = dns_query(name)
    with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
        sock.settimeout(3); sock.sendto(packet,('127.0.0.1',port))
        response = sock.recv(65535)
    if response[:2] != packet[:2]: raise AssertionError('DNS transaction mismatch')
    return response


class CoreProcess:
    def __init__(self, stack: ExitStack, command: list[str], log_path: Path, env: dict):
        self.command, self.log_path, self.env, self.process = command,log_path,env,None
        self.log = stack.enter_context(log_path.open('w'))
        stack.callback(self.stop)
    def start(self, port: int):
        self.process = subprocess.Popen(self.command,stdout=self.log,stderr=subprocess.STDOUT,env=self.env)
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            if self.process.poll() is not None: raise AssertionError(self.log_path.read_text())
            try:
                with socket.create_connection(('127.0.0.1',port),timeout=.1): return
            except OSError: time.sleep(.03)
        raise AssertionError('Core listener not ready: '+self.log_path.read_text())
    def stop(self):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try: self.process.wait(timeout=5)
            except subprocess.TimeoutExpired: self.process.kill(); self.process.wait(timeout=3)
