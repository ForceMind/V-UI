"""Fixed core-tag source reads with synthetic JSON/tar bytes; no build/network."""
import ast
import hashlib
import io
import json
from pathlib import Path
import re
import socket
import ssl
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request


SOURCE = Path(__file__).resolve().parents[1] / 'scripts/build_bundle.py'


def load_core_sources():
    # Execute only this stdlib function, never builder imports/main/tooling.
    tree = ast.parse(SOURCE.read_text())
    function, = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'core_sources']
    namespace = dict(Path=Path, urllib=urllib, json=json, socket=socket, re=re,
                     tarfile=tarfile, io=io, hashlib=hashlib)
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(SOURCE), 'exec'), namespace)
    return namespace['core_sources']


def source_archive(font=False):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w:gz') as archive:
        for name, raw in [('root/LICENSE', b'synthetic upstream license\n')] + ([('root/font.ttf', b'font')] if font else []):
            member = tarfile.TarInfo(name); member.size = len(raw)
            archive.addfile(member, io.BytesIO(raw))
    return output.getvalue()


def error(code):
    stream = io.BytesIO(b'untrusted error body')
    return urllib.error.HTTPError('https://api.github.com/', code, 'synthetic', {}, stream), stream


class CoreSourceReadTests(unittest.TestCase):
    def setUp(self):
        self.core_sources = load_core_sources()
        self.archive = source_archive()
        self.raw = json.dumps({'sha': 'a' * 40}).encode()

    def exercise(self, failures=()):
        responses = [io.BytesIO(self.raw), io.BytesIO(self.archive), io.BytesIO(self.raw), io.BytesIO(self.archive)]
        with tempfile.TemporaryDirectory() as temporary, patch.object(
                urllib.request, 'urlopen', side_effect=[*failures, *responses]) as get:
            self.core_sources(Path(temporary))
            proof = json.loads((Path(temporary) / 'sources/PROVENANCE.json').read_text())
            for name, repo, tag in (('sing-box','SagerNet/sing-box','v1.14.2'),('xray','XTLS/Xray-core','v26.3.27')):
                self.assertEqual(proof[name], {'repository':repo,'release_tag':tag,'commit':'a'*40,
                    'archive_url':'https://codeload.github.com/'+repo+'/tar.gz/'+'a'*40,
                    'sha256':hashlib.sha256(self.archive).hexdigest()})
                self.assertEqual((Path(temporary)/'sources'/(name+'-source.tar.gz')).read_bytes(),self.archive)
                self.assertEqual((Path(temporary)/'third_party/core-licenses'/(name+'-LICENSE')).read_bytes(),b'synthetic upstream license\n')
        self.assertTrue(all(response.closed for response in responses))
        return get.call_args_list

    def test_original_success_uses_each_exact_tag_once_and_retains_sources_and_licenses(self):
        calls = self.exercise()
        self.assertEqual(len(calls),4)
        for call,repo,tag in ((calls[0],'SagerNet/sing-box','v1.14.2'),(calls[2],'XTLS/Xray-core','v26.3.27')):
            request=call.args[0]
            self.assertEqual(request.full_url,f'https://api.github.com/repos/{repo}/commits/{tag}')
            self.assertEqual(request.get_method(),'GET')
            self.assertEqual(dict(request.header_items()),{'User-agent':'V-UI-release-builder'})
            self.assertEqual(call.kwargs,{'timeout':30})
        self.assertEqual(calls[1].kwargs,{'timeout':90}); self.assertEqual(calls[3].kwargs,{'timeout':90})

    def test_each_transient_status_closes_error_then_retries_same_request_once(self):
        for status in (502,503,504):
            failure, stream = error(status)
            original_load = json.load
            def load(response):
                self.assertTrue(stream.closed)
                return original_load(response)
            with self.subTest(status=status), patch.object(json,'load',side_effect=load):
                calls=self.exercise([failure])
            self.assertEqual(len(calls),5)
            self.assertIs(calls[0].args[0],calls[1].args[0])
            self.assertEqual(calls[0].kwargs,calls[1].kwargs)

    def test_explicit_transport_timeouts_retry_same_url_once(self):
        for failure in (TimeoutError('synthetic'),socket.timeout('synthetic'),urllib.error.URLError(socket.timeout('synthetic'))):
            with self.subTest(failure=type(failure).__name__): calls=self.exercise([failure])
            self.assertEqual(len(calls),5); self.assertIs(calls[0].args[0],calls[1].args[0])
            self.assertEqual(calls[1].kwargs,{'timeout':30})

    def test_second_transient_or_timeout_fails_without_third_call(self):
        for failures in ((error(503)[0],error(504)[0]),(TimeoutError('one'),TimeoutError('two')),
                         (urllib.error.URLError(socket.timeout('one')),urllib.error.URLError(socket.timeout('two')))):
            with self.subTest(failures=tuple(type(item).__name__ for item in failures)), tempfile.TemporaryDirectory() as temporary, patch.object(
                    urllib.request,'urlopen',side_effect=failures) as get:
                with self.assertRaises((urllib.error.HTTPError,TimeoutError,urllib.error.URLError)):
                    self.core_sources(Path(temporary))
                self.assertEqual(get.call_count,2)
                self.assertIs(get.call_args_list[0].args[0],get.call_args_list[1].args[0])
                self.assertFalse(list((Path(temporary)/'sources').iterdir()))
            for failure in failures:
                if isinstance(failure,urllib.error.HTTPError): self.assertTrue(failure.fp.closed)

    def test_partial_json_timeout_is_closed_before_new_request_and_not_concatenated(self):
        partial=io.BytesIO(b'not a JSON prefix')
        def read(*args):
            io.BytesIO.read(partial,5)
            raise socket.timeout('synthetic partial body')
        partial.read=read
        original_load=json.load
        loads=[]
        def load(response):
            loads.append(response)
            if response is not partial: self.assertTrue(partial.closed)
            return original_load(response)
        with patch.object(json,'load',side_effect=load): calls=self.exercise([partial])
        self.assertTrue(partial.closed); self.assertEqual(len(calls),5)

    def test_403_404_ssl_generic_url_and_os_errors_never_retry(self):
        for failure in (error(403)[0],error(404)[0],ssl.SSLCertVerificationError('synthetic'),
                        urllib.error.URLError(ssl.SSLCertVerificationError('synthetic')),
                        urllib.error.URLError('timed out'),urllib.error.URLError(OSError('synthetic')),
                        OSError('synthetic')):
            with self.subTest(failure=type(failure).__name__), tempfile.TemporaryDirectory() as temporary, patch.object(
                    urllib.request,'urlopen',side_effect=failure) as get:
                with self.assertRaises(type(failure)): self.core_sources(Path(temporary))
                self.assertEqual(get.call_count,1)
            if isinstance(failure,urllib.error.HTTPError): self.assertTrue(failure.fp.closed)

    def test_json_shape_and_commit_format_errors_never_retry(self):
        for raw in (b'{bad',b'{}',b'{"sha":"not40"}',json.dumps({'sha':'A'*40}).encode(),b'{"sha":null}'):
            response=io.BytesIO(raw)
            with self.subTest(raw=raw), tempfile.TemporaryDirectory() as temporary, patch.object(
                    urllib.request,'urlopen',return_value=response) as get:
                with self.assertRaises((ValueError,KeyError,TypeError)): self.core_sources(Path(temporary))
                self.assertEqual(get.call_count,1); self.assertTrue(response.closed)
                self.assertFalse(list((Path(temporary)/'sources').iterdir()))

    def test_source_font_rejection_does_not_retry_archive_or_publish_source(self):
        with tempfile.TemporaryDirectory() as temporary,patch.object(urllib.request,'urlopen',side_effect=[io.BytesIO(self.raw),io.BytesIO(source_archive(font=True))]) as get:
            with self.assertRaisesRegex(ValueError,'font files'): self.core_sources(Path(temporary))
            self.assertEqual(get.call_count,2)
            self.assertFalse(list((Path(temporary)/'sources').iterdir()))

    def test_codeload_archive_failure_is_outside_the_tag_metadata_retry(self):
        failure,stream=error(503)
        with tempfile.TemporaryDirectory() as temporary,patch.object(urllib.request,'urlopen',side_effect=[io.BytesIO(self.raw),failure]) as get:
            with self.assertRaises(urllib.error.HTTPError): self.core_sources(Path(temporary))
            self.assertEqual(get.call_count,2)
            self.assertIsInstance(get.call_args_list[1].args[0],str)
            self.assertTrue(get.call_args_list[1].args[0].startswith('https://codeload.github.com/SagerNet/sing-box/tar.gz/'))
            self.assertEqual(get.call_args_list[1].kwargs,{'timeout':90})
            self.assertFalse(list((Path(temporary)/'sources').iterdir()))
        # The codeload call itself is unchanged, including its existing error
        # ownership; this test grants no retry to that separate request.
        failure.close()
        self.assertTrue(stream.closed)


if __name__ == '__main__': unittest.main()
