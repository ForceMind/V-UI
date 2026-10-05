"""No panel, private key, directory listing, traversal or symlink on public HTTP-01."""
from contextlib import ExitStack
import os
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import build_opener,ProxyHandler,Request
from app.certificates.http01 import ChallengeServer,handler_for,PREFIX

class Http01Tests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.webroot=self.root/'web';self.challenge=self.webroot/'.well-known'/'acme-challenge'
        self.challenge.mkdir(parents=True)
        self.token='A'*43;self.value=b'challenge.key_authorization'
        (self.challenge/self.token).write_bytes(self.value)
        self.server=ChallengeServer(('127.0.0.1',0),handler_for(self.webroot))
        thread=threading.Thread(target=self.server.serve_forever,daemon=True);thread.start()
        def close():self.server.shutdown();self.server.server_close();thread.join(timeout=3)
        self.stack.callback(close)
        self.base='http://127.0.0.1:'+str(self.server.server_port)
        self.opener=build_opener(ProxyHandler({}))
    def get(self,path,method='GET'):
        return self.opener.open(Request(self.base+path,method=method),timeout=3)
    def test_get_and_head_only_return_exact_bounded_token(self):
        with self.get(PREFIX+self.token) as response:
            self.assertEqual(response.read(),self.value);self.assertEqual(response.headers['Cache-Control'],'no-store')
        with self.get(PREFIX+self.token,'HEAD') as response:self.assertEqual(response.read(),b'')
        with self.assertRaises(HTTPError) as error:self.get(PREFIX+self.token,'POST')
        self.assertEqual(error.exception.code,501)
    def test_other_paths_and_traversal_fail(self):
        for path in ('/','/login','/api/certificates',PREFIX,PREFIX+'..%2Fprivate.pem',PREFIX+self.token+'?x=1',PREFIX+'../secret',PREFIX+'B'*257):
            with self.subTest(path=path),self.assertRaises(HTTPError) as error:self.get(path)
            self.assertEqual(error.exception.code,404)
    def test_symlink_fifo_and_large_or_invalid_content_are_not_served(self):
        target=self.challenge/self.token;target.unlink();target.symlink_to('/etc/passwd')
        with self.assertRaises(HTTPError):self.get(PREFIX+self.token)
        target.unlink();os.mkfifo(target)
        with self.assertRaises(HTTPError):self.get(PREFIX+self.token)
        target.unlink()
        for content in (b'A'*4097,b'-----BEGIN PRIVATE KEY-----\n',b'<script>'):
            target.write_bytes(content)
            with self.assertRaises(HTTPError):self.get(PREFIX+self.token)
    def test_symlink_directory_is_not_followed(self):
        (self.challenge/self.token).unlink();self.challenge.rmdir()
        self.challenge.symlink_to(self.root,target_is_directory=True)
        (self.root/self.token).write_bytes(self.value)
        with self.assertRaises(HTTPError):self.get(PREFIX+self.token)

if __name__=='__main__':unittest.main()
