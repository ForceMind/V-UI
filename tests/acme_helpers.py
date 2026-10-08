"""Real Pebble with real HTTP-01 fetches. No always-valid challenge shortcut."""
import errno
import json
import os
from pathlib import Path
import socket
import socketserver
import struct
import ssl
import subprocess
import threading
import time
from urllib.request import build_opener, ProxyHandler, HTTPSHandler
from app.certificates.http01 import handler_for
from http.server import ThreadingHTTPServer
from loopback_helpers import certificate_files, DnsAnswers, unused_port


def start_dual_dns(stack, answers):
    """Own TCP and UDP on one ephemeral port before starting either service.

    An available UDP port need not be available for TCP (and vice versa).
    Keep the first socket bound while claiming the other transport, and only
    retry genuine address collisions. Permission or other failures stay fatal.
    """
    class TcpDns(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.settimeout(3)
            def read(size):
                value = b''
                while len(value) < size:
                    chunk = self.request.recv(size - len(value))
                    if not chunk:
                        raise EOFError()
                    value += chunk
                return value
            try:
                size = struct.unpack('!H', read(2))[0]
                answer = answers.answer(read(size))
                self.request.sendall(struct.pack('!H', len(answer)) + answer)
            except (OSError, EOFError, ValueError, IndexError, UnicodeError):
                pass

    class UdpDns(socketserver.BaseRequestHandler):
        def handle(self):
            packet, transport = self.request
            try:
                transport.sendto(answers.answer(packet), self.client_address)
            except (ValueError, IndexError, UnicodeError):
                pass

    for attempt in range(10):
        tcp = None
        try:
            tcp = socketserver.ThreadingTCPServer(('127.0.0.1', 0), TcpDns)
            udp = socketserver.ThreadingUDPServer(tcp.server_address, UdpDns)
        except OSError as exc:
            if tcp is not None:
                tcp.server_close()
            if exc.errno != errno.EADDRINUSE or attempt == 9:
                raise
        else:
            break

    def start(server):
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever,
                                  kwargs={'poll_interval': .02}, daemon=True)
        thread.start()
        def close():
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
        stack.callback(close)

    # The sockets stay bound continuously from reservation until cleanup.
    start(tcp)
    start(udp)
    return tcp.server_address[1]


class PebbleFixture:
    def __init__(self, stack, root: Path, webroot: Path, domain='panel.example.test', *, http_port=None):
        self.root, self.webroot, self.domain = root, webroot, domain
        self.ca, cert, key = certificate_files(root, 'pebble-server')
        self.http = None
        if http_port is None:
            self.http = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(webroot))
            self.http.daemon_threads=True
            self.thread=threading.Thread(target=self.http.serve_forever,daemon=True);self.thread.start()
            def close_http():self.http.shutdown();self.http.server_close();self.thread.join(timeout=3)
            stack.callback(close_http)
            http_port = self.http.server_port
        elif type(http_port) is not int or not 1024 <= http_port <= 65535:
            raise ValueError('Explicit loopback HTTP-01 responder port required')
        dns=DnsAnswers('PEBBLE_DNS',{domain:'127.0.0.1'})
        dns_port=start_dual_dns(stack,dns)
        port,management=unused_port(),unused_port()
        config={'pebble':{'listenAddress':f'127.0.0.1:{port}',
            'managementListenAddress':f'127.0.0.1:{management}',
            'certificate':str(cert),'privateKey':str(key),
            'httpPort':http_port,'tlsPort':unused_port(),
            'externalAccountBindingRequired':False,'retryAfter':{'authz':1,'order':1},
            'keyAlgorithm':'ecdsa','profiles':{'default':{'description':'VUI certificate tests','validityPeriod':7776000}}}}
        path=root/'pebble.json';path.write_text(json.dumps(config))
        self.log_path=root/'pebble.log';log=stack.enter_context(self.log_path.open('w'))
        env={**os.environ,'PEBBLE_VA_NOSLEEP':'1','PEBBLE_WFE_NONCEREJECT':'0','PEBBLE_AUTHZREUSE':'0'}
        env.pop('PEBBLE_VA_ALWAYS_VALID',None)
        binary=os.environ['VUI_TEST_PEBBLE']
        self.process=subprocess.Popen([binary,'-config',str(path),'-dnsserver',f'127.0.0.1:{dns_port}'],
            stdout=log,stderr=subprocess.STDOUT,env=env)
        def stop():
            self.process.terminate()
            try:self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:self.process.kill();self.process.wait(timeout=5)
        stack.callback(stop)
        self.directory=f'https://127.0.0.1:{port}/dir'
        self.opener=build_opener(ProxyHandler({}),HTTPSHandler(context=ssl.create_default_context(cafile=self.ca)))
        deadline=time.monotonic()+10
        while True:
            if self.process.poll() is not None:raise AssertionError(self.log_path.read_text())
            try:
                with self.opener.open(self.directory,timeout=1):break
            except OSError:
                if time.monotonic()>deadline:raise
                time.sleep(.1)
        with self.opener.open(f'https://127.0.0.1:{management}/roots/0',timeout=3) as response:
            self.root_pem=response.read()
