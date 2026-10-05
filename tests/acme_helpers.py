"""Real Pebble with real HTTP-01 fetches. No always-valid challenge shortcut."""
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
from loopback_helpers import certificate_files, DnsAnswers, start_udp_dns, unused_port


class PebbleFixture:
    def __init__(self, stack, root: Path, webroot: Path, domain='panel.example.test'):
        self.root, self.webroot, self.domain = root, webroot, domain
        self.ca, cert, key = certificate_files(root, 'pebble-server')
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(webroot))
        self.http.daemon_threads=True
        self.thread=threading.Thread(target=self.http.serve_forever,daemon=True);self.thread.start()
        def close_http():self.http.shutdown();self.http.server_close();self.thread.join(timeout=3)
        stack.callback(close_http)
        dns=DnsAnswers('PEBBLE_DNS',{domain:'127.0.0.1'})
        dns_port=start_udp_dns(stack,dns)
        class TcpDns(socketserver.BaseRequestHandler):
            def handle(self):
                self.request.settimeout(3)
                def read(size):
                    value=b''
                    while len(value)<size:
                        chunk=self.request.recv(size-len(value))
                        if not chunk:raise EOFError()
                        value+=chunk
                    return value
                try:
                    size=struct.unpack('!H',read(2))[0]
                    answer=dns.answer(read(size))
                    self.request.sendall(struct.pack('!H',len(answer))+answer)
                except (OSError,EOFError,ValueError,IndexError):pass
        tcp_dns=socketserver.ThreadingTCPServer(('127.0.0.1',dns_port),TcpDns)
        tcp_dns.daemon_threads=True
        tcp_thread=threading.Thread(target=tcp_dns.serve_forever,daemon=True);tcp_thread.start()
        def close_tcp():tcp_dns.shutdown();tcp_dns.server_close();tcp_thread.join(timeout=3)
        stack.callback(close_tcp)
        port,management=unused_port(),unused_port()
        config={'pebble':{'listenAddress':f'127.0.0.1:{port}',
            'managementListenAddress':f'127.0.0.1:{management}',
            'certificate':str(cert),'privateKey':str(key),
            'httpPort':self.http.server_port,'tlsPort':unused_port(),
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
