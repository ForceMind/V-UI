"""Reload new certificate material without a panel restart or listener privilege."""
import ssl
import threading


def server_context(chain, key):
    context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version=ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(chain,key)
    return context


class LiveTLS:
    def __init__(self, chain, key):
        self.context=server_context(chain,key)
        self.lock=threading.RLock()
    def reload(self, chain, key):
        # Validate both inputs as a pair before touching the serving context.
        server_context(chain,key)
        with self.lock:self.context.load_cert_chain(chain,key)
