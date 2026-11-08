"""Pure anonymous download fakes; never fetch or execute a release binary."""
from contextlib import redirect_stderr
import io
from pathlib import Path
import ssl
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import urllib.error
import urllib.request

from scripts import test_asset_download as download
from scripts import fetch_pebble as pebble, fetch_test_cores as cores

URL = pebble.URL
API = download.ASSET_API[URL]


class Response:
    def __init__(self, raw=b'fake archive bytes', url=URL, length=None):
        self.stream = io.BytesIO(raw); self.url = url; self.reads = []
        self.headers = {} if length is None else {'Content-Length': length}
    def geturl(self): return self.url
    def getcode(self): return 200
    def read(self, size): self.reads.append(size); return self.stream.read(size)
    def close(self): self.stream.close()
    def __enter__(self): return self
    def __exit__(self, *args): self.close()


def http_error(code, url=URL):
    stream = io.BytesIO(b'ignored untrusted upstream body')
    return urllib.error.HTTPError(url, code, 'synthetic failure', {}, stream), stream


class DownloadTests(unittest.TestCase):
    def test_original_success_is_one_attempt_bounded_and_closed_without_api(self):
        response = Response(); opener = Mock(); opener.open.return_value = response
        output = io.StringIO()
        with redirect_stderr(output): raw = download.download_pinned(URL, max_bytes=100, opener=opener)
        self.assertEqual(raw, b'fake archive bytes'); opener.open.assert_called_once_with(URL, timeout=90)
        self.assertEqual(response.reads, [101]); self.assertTrue(response.stream.closed); self.assertEqual(output.getvalue(), '')

    def test_only_transient_status_gets_exactly_one_anonymous_api_attempt_after_close(self):
        for status in (502, 503, 504):
            error, failed = http_error(status); response = Response(url=API)
            calls = []
            def open_(request, **kwargs):
                calls.append((request, kwargs))
                if len(calls) == 1: raise error
                self.assertTrue(failed.closed)
                self.assertIsInstance(request, urllib.request.Request)
                self.assertEqual(request.full_url, API)
                self.assertEqual(request.get_method(), 'GET')
                self.assertEqual(dict(request.header_items()), {'Accept': 'application/octet-stream'})
                self.assertIsNone(request.data)
                return response
            opener = Mock(); opener.open.side_effect = open_; output = io.StringIO()
            with self.subTest(status=status), redirect_stderr(output):
                self.assertEqual(download.download_pinned(URL, max_bytes=100, opener=opener), b'fake archive bytes')
            self.assertEqual(len(calls), 2); self.assertTrue(response.stream.closed)
            self.assertEqual(output.getvalue(), download.FALLBACK_MESSAGE + '\n')
            self.assertNotIn('ignored untrusted', output.getvalue())

    def test_404_403_tls_and_unknown_errors_never_fallback(self):
        failures = [http_error(404)[0], http_error(403)[0], ssl.SSLCertVerificationError('fake TLS failure'),
                    urllib.error.URLError('fake transport failure'), RuntimeError('unexpected failure')]
        for failure in failures:
            opener = Mock(); opener.open.side_effect = failure
            with self.subTest(error=type(failure).__name__), self.assertRaises(type(failure)):
                download.download_pinned(URL, max_bytes=100, opener=opener)
            self.assertEqual(opener.open.call_count, 1)
            if isinstance(failure, urllib.error.HTTPError): self.assertTrue(failure.fp.closed)

    def test_second_5xx_does_not_retry_and_both_error_responses_close(self):
        first, fp1 = http_error(504); second, fp2 = http_error(503, API)
        opener = Mock(); opener.open.side_effect = [first, second]
        with redirect_stderr(io.StringIO()), self.assertRaises(urllib.error.HTTPError):
            download.download_pinned(URL, max_bytes=100, opener=opener)
        self.assertEqual(opener.open.call_count, 2); self.assertTrue(fp1.closed); self.assertTrue(fp2.closed)

    def test_original_or_fallback_oversize_and_wrong_final_host_are_rejected_and_closed(self):
        for fallback in (False, True):
            for raw, length, final in ((b'x' * 101, None, API if fallback else URL),
                                       (b'x', '101', API if fallback else URL),
                                       (b'x', None, 'https://untrusted.invalid/asset')):
                response = Response(raw=raw, length=length, url=final)
                opener = Mock(); opener.open.side_effect = ([http_error(504)[0], response] if fallback else [response])
                with self.subTest(fallback=fallback, length=length, final=final), redirect_stderr(io.StringIO()), self.assertRaises(ValueError):
                    download.download_pinned(URL, max_bytes=100, opener=opener)
                self.assertTrue(response.stream.closed)
                self.assertEqual(opener.open.call_count, 2 if fallback else 1)
                self.assertEqual(response.reads, [] if length is not None or final.startswith('https://untrusted') else [101])

    def test_invalid_pairing_scheme_repo_asset_id_or_query_refuses_before_any_request(self):
        pairs = [(URL, API.replace('letsencrypt/pebble', 'other/repo')), (URL.replace('https:', 'http:'), API),
                 (URL, API.replace('https:', 'http:')), (URL, API.rsplit('/', 1)[0] + '/123'),
                 (URL + '?cache=1', API), (URL, API + '?x=1')]
        for original, api in pairs:
            with self.subTest(original=original, api=api), self.assertRaises(ValueError): download.validate_pair(original, api)
        opener = Mock()
        for original in (URL.replace('https:', 'http:'), 'https://example.invalid/file', URL + '?cache=1'):
            with self.assertRaises(ValueError): download.download_pinned(original, max_bytes=100, opener=opener)
        opener.open.assert_not_called()
        for options in ({'max_bytes': True}, {'max_bytes': 0}, {'max_bytes': 100_000_001},
                        {'max_bytes': 100, 'timeout': 91}, {'max_bytes': 100, 'timeout': float('nan')}):
            with self.assertRaises(ValueError): download.download_pinned(URL, opener=opener, **options)
        opener.open.assert_not_called()

    def test_redirects_never_drain_bodies_and_never_forward_authentication(self):
        handler = download.OfficialAssetRedirects(URL, API); handler.parent = Mock()
        request = urllib.request.Request(API, headers={'Accept':'application/octet-stream','Authorization':'never-forward',
                                                       'Proxy-Authorization':'never-forward','Cookie':'never-forward'})
        request.timeout = 90
        response = Response(raw=b'must never be read')
        handler.http_error_302(request, response, 302, 'unused', {'Location':'https://release-assets.githubusercontent.com/asset?signature=opaque'})
        self.assertTrue(response.stream.closed); self.assertEqual(response.reads, [])
        redirected = handler.parent.open.call_args.args[0]
        self.assertEqual(dict(redirected.header_items()), {'Accept':'application/octet-stream'})
        for target in ('http://release-assets.githubusercontent.com/file', 'https://untrusted.invalid/file',
                       'https://github.com/other/repo/releases/download/tag/file',
                       'https://user:pass@release-assets.githubusercontent.com/file'):
            response = Response(); handler.parent.reset_mock()
            with self.subTest(target=target), self.assertRaises(ValueError):
                handler.http_error_302(request, response, 302, 'unused', {'Location':target})
            self.assertTrue(response.stream.closed); self.assertEqual(response.reads, []); handler.parent.open.assert_not_called()

    def test_redirect_hops_are_finite_and_verified_tls_preserves_default_proxy_route(self):
        handler = download.OfficialAssetRedirects(URL, API); handler.parent = Mock()
        request = urllib.request.Request(URL); request.timeout = 90
        for _ in range(3): handler.http_error_302(request, Response(), 302, '', {'Location':URL})
        final = Response()
        with self.assertRaises(ValueError): handler.http_error_302(request, final, 302, '', {'Location':URL})
        self.assertTrue(final.stream.closed); self.assertEqual(handler.parent.open.call_count, 3)
        with patch.object(download.urllib.request, 'build_opener') as build, \
                patch.object(download.urllib.request, 'ProxyHandler', wraps=urllib.request.ProxyHandler) as proxy:
            download._opener(URL, API)
        proxy.assert_called_once_with()
        handlers = build.call_args.args
        self.assertIsInstance(handlers[1], urllib.request.HTTPSHandler)
        self.assertTrue(handlers[1]._context.check_hostname)
        self.assertEqual(handlers[1]._context.verify_mode, ssl.CERT_REQUIRED)

    def test_fallback_bad_json_bytes_still_fail_callers_original_hash_before_extraction(self):
        for fetcher in ('pebble', 'cores'):
            response = Response(raw=b'{"digest":"untrusted","digest":"still untrusted"}', url=API if fetcher=='pebble' else download.ASSET_API[cores.PINS['x86_64-gnu']['sing-box']['url']])
            original = URL if fetcher=='pebble' else cores.PINS['x86_64-gnu']['sing-box']['url']
            error, stream = http_error(504, original); opener = Mock(); opener.open.side_effect = [error, response]
            module = pebble if fetcher=='pebble' else cores
            def fake_download(url, **kwargs): return download.download_pinned(url, opener=opener, **kwargs)
            with tempfile.TemporaryDirectory() as folder, patch.object(module, 'download_pinned', side_effect=fake_download), \
                    patch.object(module.tarfile, 'open') as extract, redirect_stderr(io.StringIO()), self.assertRaises(ValueError):
                module.fetch(Path(folder))
            extract.assert_not_called(); self.assertEqual(opener.open.call_count, 2)
            self.assertTrue(stream.closed); self.assertTrue(response.stream.closed)

    def test_bad_hash_or_format_does_not_initiate_api_fallback_after_original_success(self):
        opener = Mock(); opener.open.return_value = Response(raw=b'bad archive')
        def fake_download(url, **kwargs): return download.download_pinned(url, opener=opener, **kwargs)
        with tempfile.TemporaryDirectory() as folder, patch.object(pebble, 'download_pinned', side_effect=fake_download), \
                self.assertRaises(ValueError): pebble.fetch(Path(folder))
        self.assertEqual(opener.open.call_count, 1)
        opener.reset_mock(); opener.open.return_value = Response(raw=b'fake format-error bytes')
        with tempfile.TemporaryDirectory() as folder, patch.object(pebble, 'download_pinned', side_effect=fake_download), \
                patch.object(pebble.hashlib, 'sha256', return_value=Mock(hexdigest=lambda:pebble.SHA256)), \
                patch.object(pebble.tarfile, 'open', side_effect=ValueError('synthetic format rejection')), self.assertRaises(ValueError):
            pebble.fetch(Path(folder))
        self.assertEqual(opener.open.call_count, 1)

    def test_script_and_package_import_modes_use_same_stdlib_helper_without_executing_fetch(self):
        root = Path(__file__).resolve().parents[1]
        code = "import sys;sys.path[:0]=[sys.argv[1]+'/scripts',sys.argv[1]];import fetch_test_cores,fetch_pebble;from scripts import fetch_test_cores as m;assert fetch_test_cores.PINS==m.PINS;assert callable(fetch_pebble.download_pinned);print('safe imports')"
        result = subprocess.run([sys.executable,'-B','-I','-S','-c',code,str(root)],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr); self.assertEqual(result.stdout.strip(),'safe imports')

    def test_every_original_pin_and_fixed_official_asset_pair_is_preserved(self):
        sing = {
            'x86_64-gnu':('amd64-glibc','5c7bc18461827b28d0e5ee7e89d33b276d3ff7c818531104c8e8d26d85b0656e'),
            'x86_64-musl':('amd64-musl','8f6cb4bcf94d2b33c65d52e0d5b142db29a938336f1ff7267f397ac3758fc297'),
            'aarch64-gnu':('arm64-glibc','87db5c3a96ebad1c44c0be1fe7955db2f76b8c97bbc0ed62173063d675e078cf'),
            'aarch64-musl':('arm64-musl','675297394f9430cebb72b3c48ba8bce0d6f7c750a9d68a8f7f88c515c8255cd1')}
        self.assertEqual(set(cores.PINS),set(sing))
        for target,(suffix,checksum) in sing.items():
            base='sing-box-1.14.2-linux-'+suffix
            self.assertEqual(cores.PINS[target]['sing-box'], {'version':'1.14.2','url':'https://github.com/SagerNet/sing-box/releases/download/v1.14.2/'+base+'.tar.gz','sha256':checksum,'member':base+'/sing-box'})
            arm=target.startswith('aarch64')
            self.assertEqual(cores.PINS[target]['xray'],{'version':'26.3.27','url':'https://github.com/XTLS/Xray-core/releases/download/v26.3.27/'+('Xray-linux-arm64-v8a.zip' if arm else 'Xray-linux-64.zip'),
                'sha256':'4d30283ae614e3057f730f67cd088a42be6fdf91f8639d82cb69e48cde80413c' if arm else '23cd9af937744d97776ee35ecad4972cf4b2109d1e0fe6be9930467608f7c8ae','member':'xray'})
        self.assertEqual(pebble.SHA256,'4f2fcb5bca8c85c9cf73ad140fccfc0d2be40bd81ab99879c79b7b8a0b4f70ed')
        self.assertEqual(pebble.URL,'https://github.com/letsencrypt/pebble/releases/download/v2.10.1/pebble-linux-amd64.tar.gz')
        ids={'sing-box-1.14.2-linux-amd64-glibc.tar.gz':'585840737','sing-box-1.14.2-linux-amd64-musl.tar.gz':'585840769',
             'sing-box-1.14.2-linux-arm64-glibc.tar.gz':'585840820','sing-box-1.14.2-linux-arm64-musl.tar.gz':'585840822',
             'Xray-linux-64.zip':'383041956','Xray-linux-arm64-v8a.zip':'383041834','pebble-linux-amd64.tar.gz':'401025613'}
        self.assertEqual(set(download.ASSET_API),{pin['url'] for group in cores.PINS.values() for pin in group.values()}|{URL})
        for original,api in download.ASSET_API.items():
            download.validate_pair(original,api)
            self.assertEqual(api.rsplit('/',1)[1],ids[original.rsplit('/',1)[1]])


if __name__=='__main__': unittest.main()
