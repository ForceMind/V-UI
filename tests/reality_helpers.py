"""Bounded, local-only REALITY reference fixture; never install trust roots.

REALITY consults a reference TLS server even for authenticated sessions. Its
deliberate fallback can send an HTTP/2 camouflage request there. This fixture
counts those independently from the application target and implements only the
small HTTP/2 subset needed for a fixed, non-redirecting response.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import socketserver
import ssl
import struct
import threading

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

SNI = 'reference.example.test'
WRONG_SNI = 'disallowed.example.test'
REFERENCE_BODY = b'VUI-REALITY-REFERENCE'


def reference_certificates(root: Path):
    now = datetime.now(timezone.utc)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'VUI isolated REALITY CA')])
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name)
          .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(now-timedelta(minutes=5)).not_valid_after(now+timedelta(days=1))
          .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
          .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False,
              key_encipherment=False, data_encipherment=False, key_agreement=False,
              key_cert_sign=True, crl_sign=True, encipher_only=False, decipher_only=False), critical=True)
          .sign(ca_key, hashes.SHA256()))
    key = ec.generate_private_key(ec.SECP256R1())
    cert = (x509.CertificateBuilder().subject_name(x509.Name([
                x509.NameAttribute(NameOID.COMMON_NAME, SNI)]))
            .issuer_name(ca_name).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now-timedelta(minutes=5)).not_valid_after(now+timedelta(hours=12))
            # Both names verify normally: the negative isolates REALITY's SNI allowlist.
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(SNI), x509.DNSName(WRONG_SNI)]), critical=False)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .sign(ca_key, hashes.SHA256()))
    paths = root/'reference-ca.pem', root/'reference-cert.pem', root/'reference-key.pem'
    paths[0].write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    paths[1].write_bytes(cert.public_bytes(serialization.Encoding.PEM)+ca.public_bytes(serialization.Encoding.PEM))
    paths[2].write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                         serialization.NoEncryption()))
    for path in paths: path.chmod(0o600)
    return paths


def read_exact(conn, size):
    data = b''
    while len(data) < size:
        chunk = conn.recv(size-len(data))
        if not chunk: raise EOFError('reference peer closed')
        data += chunk
    return data


def h2_frame(kind, flags, stream, body=b''):
    return len(body).to_bytes(3, 'big') + bytes([kind, flags]) + struct.pack('!I', stream) + body


class ReferenceTraffic:
    def __init__(self):
        self.lock = threading.Lock()
        self.accepts = 0
        self.client_hellos = []
        self.handshakes = []
        self.camouflage = []

    def snapshot(self):
        with self.lock:
            return {'accepts': self.accepts, 'client_hellos': list(self.client_hellos),
                    'handshakes': list(self.handshakes), 'camouflage': list(self.camouflage)}


def start_reference_target(stack, cert, key):
    traffic = ReferenceTraffic()
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    context.minimum_version = context.maximum_version = ssl.TLSVersion.TLSv1_3
    context.set_ecdh_curve('X25519')
    context.set_alpn_protocols(['h2'])
    context.num_tickets = 0
    def sni_callback(sock, name, original):
        with traffic.lock: traffic.client_hellos.append(name)
    context.set_servername_callback(sni_callback)

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            with traffic.lock: traffic.accepts += 1
            self.request.settimeout(15)
            try:
                with context.wrap_socket(self.request, server_side=True) as conn:
                    with traffic.lock: traffic.handshakes.append((conn.version(), conn.selected_alpn_protocol()))
                    if conn.selected_alpn_protocol() != 'h2': return
                    conn.sendall(h2_frame(4, 0, 0))  # SETTINGS
                    if read_exact(conn, 24) != b'PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n': return
                    for _ in range(64):  # bounded fixture, never a general-purpose proxy
                        header = read_exact(conn, 9)
                        size = int.from_bytes(header[:3], 'big')
                        kind, flags = header[3:5]
                        stream = int.from_bytes(header[5:], 'big') & 0x7fffffff
                        if size > 65536: return
                        read_exact(conn, size)
                        if kind == 4 and not flags & 1:
                            conn.sendall(h2_frame(4, 1, 0))
                        elif kind == 1 and stream and flags & 4:  # complete HEADERS
                            with traffic.lock: traffic.camouflage.append(stream)
                            # HPACK static-table index 8 is :status 200.
                            conn.sendall(h2_frame(1, 4, stream, b'\x88') + h2_frame(0, 1, stream, REFERENCE_BODY))
                        elif kind == 7: return
            except (OSError, EOFError):
                # Accepted REALITY replaces the reference certificate/Finished;
                # a reference-side incomplete handshake is expected, not failure.
                return

    server = socketserver.ThreadingTCPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .02}, daemon=True)
    thread.start()
    def close():
        server.shutdown(); server.server_close(); thread.join(timeout=2)
    stack.callback(close)
    return server.server_address[1], traffic
