"""A challenge-only HTTP server; never serve panel data or traverse directories."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import re
import signal
import socket
import stat
import threading

PREFIX = '/.well-known/acme-challenge/'
TOKEN = re.compile(r'^[A-Za-z0-9_-]{16,256}$')


class ChallengeServer(ThreadingHTTPServer):
    """Bound concurrent challenge clients; a slow client cannot spawn unbounded threads."""
    daemon_threads = True
    request_queue_size = 32
    def __init__(self, *args, **kwargs):
        self.slots = threading.BoundedSemaphore(32)
        super().__init__(*args, **kwargs)
    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try: super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise
    def process_request_thread(self, request, client_address):
        try: super().process_request_thread(request, client_address)
        finally: self.slots.release()


def handler_for(webroot: Path):
    class ChallengeHandler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup(); self.connection.settimeout(5)
        def log_message(self, *args): pass
        def do_HEAD(self): self.serve(False)
        def do_GET(self): self.serve(True)
        def serve(self, body):
            token = self.path[len(PREFIX):] if self.path.startswith(PREFIX) else ''
            if not TOKEN.fullmatch(token):
                self.send_error(404); return
            path = webroot / '.well-known' / 'acme-challenge' / token
            try:
                # Every ancestor and final entry must remain inside the fixed challenge root.
                if any(p.is_symlink() for p in (webroot, path.parent.parent, path.parent)):
                    raise OSError()
                fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                with os.fdopen(fd, 'rb') as handle:
                    if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode): raise OSError()
                    raw = handle.read(4097)
                if len(raw) > 4096 or not re.fullmatch(rb'[A-Za-z0-9_.-]+', raw): raise OSError()
            except OSError:
                self.send_error(404); return
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain')
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            if body: self.wfile.write(raw)
    return ChallengeHandler


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--webroot', type=Path, required=True)
    args = p.parse_args()
    # Production port 80 is opened by systemd, not a root Python service.
    count = int(os.getenv('LISTEN_FDS', '0'))
    if int(os.getenv('LISTEN_PID', '0')) != os.getpid() or not 1 <= count <= 2:
        raise SystemExit('Start through the managed HTTP-01 socket unit')
    servers = []
    for fd in range(3, 3 + count):
        sock = socket.socket(fileno=fd)
        server = ChallengeServer(('127.0.0.1', 0), handler_for(args.webroot), bind_and_activate=False)
        server.socket.close(); server.socket = sock
        server.server_address = sock.getsockname()
        server.server_name = 'vui-http01'; server.server_port = server.server_address[1]
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    stop.wait()
    for server in servers: server.shutdown(); server.server_close()

if __name__ == '__main__': main()
