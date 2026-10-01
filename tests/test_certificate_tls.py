"""Serving sockets see renewed material; invalid material never replaces the context."""
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import socket
import ssl
import tempfile
import threading
import unittest
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from app.certificates.material import make_csr
from app.certificates.tls import LiveTLS
from test_certificates import SigningProvider

class CertificateReloadTests(unittest.TestCase):
    def test_real_tls_peer_certificate_changes_only_after_valid_reload(self):
        with ExitStack() as stack:
            root=Path(stack.enter_context(tempfile.TemporaryDirectory()))
            provider=SigningProvider()
            def pair(name):
                work=root/name;work.mkdir()
                private,csr=make_csr('panel.example.test')
                (work/'request.pem').write_bytes(csr)
                chain=provider.issue({},work,threading.Event())
                (work/'chain.pem').write_bytes(chain);(work/'key.pem').write_bytes(private)
                return work/'chain.pem',work/'key.pem'
            first,second=pair('first'),pair('second')
            live=LiveTLS(*first)
            class Handler(BaseHTTPRequestHandler):
                def log_message(self,*args):pass
                def do_GET(self):self.send_response(204);self.end_headers()
            server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
            server.socket=live.context.wrap_socket(server.socket,server_side=True)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            def close():server.shutdown();server.server_close();thread.join(timeout=3)
            stack.callback(close)
            trusted=ssl.create_default_context(cadata=provider.ca.public_bytes(serialization.Encoding.PEM).decode())
            def serial():
                with socket.create_connection(server.server_address,timeout=3) as connection:
                    with trusted.wrap_socket(connection,server_hostname='panel.example.test') as tls:
                        result=x509.load_der_x509_certificate(tls.getpeercert(binary_form=True)).serial_number
                        tls.sendall(b'GET / HTTP/1.0\r\nHost: panel.example.test\r\n\r\n')
                        while tls.recv(4096):pass
                        return result
            before=serial();live.reload(*second);after=serial();self.assertNotEqual(before,after)
            with self.assertRaises(ssl.SSLError):live.reload(first[0],second[1])
            self.assertEqual(serial(),after)

if __name__=='__main__':unittest.main()
