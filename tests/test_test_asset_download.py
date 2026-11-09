"""Pure anonymous download fakes; never fetch or execute a release binary."""
from contextlib import redirect_stderr
import io
from pathlib import Path
import ssl
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import urllib.error
import urllib.request

from scripts import test_asset_download as download
from scripts import fetch_pebble as pebble, fetch_test_cores as cores
from scripts import fetch_mihomo as mihomo, fetch_portable_runtimes as runtimes

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
                    ssl.SSLError('fake SSL failure'),
                    urllib.error.URLError(ssl.SSLCertVerificationError('fake wrapped TLS failure')),
                    urllib.error.URLError(OSError('fake network failure')),
                    urllib.error.URLError('timed out'), OSError('fake OS failure'),
                    urllib.error.URLError('fake transport failure'), RuntimeError('unexpected failure')]
        for failure in failures:
            opener = Mock(); opener.open.side_effect = failure
            with self.subTest(error=type(failure).__name__), self.assertRaises(type(failure)):
                download.download_pinned(URL, max_bytes=100, opener=opener)
            self.assertEqual(opener.open.call_count, 1)
            if isinstance(failure, urllib.error.HTTPError): self.assertTrue(failure.fp.closed)

    def test_explicit_open_timeouts_get_one_exact_anonymous_api_attempt(self):
        failures=(TimeoutError('fake open timeout'),socket.timeout('fake socket timeout'),
                  urllib.error.URLError(TimeoutError('fake wrapped timeout')))
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                response=Response(raw=b'complete fallback bytes',url=API)
                opener=Mock(); opener.open.side_effect=[failure,response]
                with redirect_stderr(io.StringIO()):
                    raw=download.download_pinned(URL,max_bytes=100,opener=opener)
                self.assertEqual(raw,b'complete fallback bytes')
                self.assertEqual(opener.open.call_count,2)
                request=opener.open.call_args.args[0]
                self.assertEqual(request.full_url,API); self.assertEqual(request.get_method(),'GET')
                self.assertEqual(dict(request.header_items()),{'Accept':'application/octet-stream'})
                self.assertTrue(response.stream.closed)

    def test_partial_body_timeout_closes_before_fallback_and_discards_partial_bytes(self):
        for failure in (socket.timeout('fake partial timeout'),
                        urllib.error.URLError(socket.timeout('fake wrapped body timeout'))):
            first=Response(raw=b'partial original bytes')
            def partial_read(size):
                first.reads.append(size); first.stream.read(7)
                raise failure
            first.read=partial_read
            second=Response(raw=b'whole fallback body',url=API)
            calls=[]
            def open_(request,**kwargs):
                calls.append(request)
                if len(calls)==1: return first
                self.assertTrue(first.stream.closed)
                return second
            opener=Mock(); opener.open.side_effect=open_
            with self.subTest(failure=type(failure).__name__),redirect_stderr(io.StringIO()):
                raw=download.download_pinned(URL,max_bytes=100,opener=opener)
            self.assertEqual(raw,b'whole fallback body'); self.assertEqual(len(calls),2)
            self.assertEqual(first.reads,[101]); self.assertEqual(second.reads,[101])
            self.assertTrue(second.stream.closed)

    def test_timeout_on_api_never_creates_a_third_attempt_and_closes_body(self):
        for body_timeout in (False,True):
            response=Response(url=API)
            def failed_read(size):
                response.stream.read(4)
                raise TimeoutError('fake API body timeout')
            response.read=failed_read
            opener=Mock(); opener.open.side_effect=[TimeoutError('fake original timeout'),
                                                    response if body_timeout else TimeoutError('fake API open timeout')]
            with self.subTest(body_timeout=body_timeout),redirect_stderr(io.StringIO()),self.assertRaises(TimeoutError):
                download.download_pinned(URL,max_bytes=100,opener=opener)
            self.assertEqual(opener.open.call_count,2)
            if body_timeout: self.assertTrue(response.stream.closed)

    def test_timeout_fallback_bad_runtime_bytes_still_cannot_write_an_archive(self):
        pin=runtimes.PINS['x86_64-gnu']
        response=Response(raw=b'untrusted fallback archive',url=download.ASSET_API[pin['url']])
        opener=Mock(); opener.open.side_effect=[TimeoutError('fake original timeout'),response]
        def fake_download(url,**kwargs): return download.download_pinned(url,opener=opener,**kwargs)
        with tempfile.TemporaryDirectory() as folder,patch.object(runtimes,'download_pinned',side_effect=fake_download), \
                patch.object(Path,'write_bytes') as write,redirect_stderr(io.StringIO()),self.assertRaisesRegex(ValueError,'Portable Python checksum mismatch'):
            runtimes.fetch(Path(folder),['x86_64-gnu'])
        write.assert_not_called(); self.assertTrue(response.stream.closed)
        self.assertEqual(opener.open.call_count,2)

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
        code = "import sys;sys.path[:0]=[sys.argv[1]+'/scripts',sys.argv[1]];import fetch_test_cores,fetch_pebble,fetch_mihomo,fetch_portable_runtimes;from scripts import fetch_test_cores as m,fetch_mihomo as h,fetch_portable_runtimes as r;assert fetch_test_cores.PINS==m.PINS;assert fetch_mihomo.PIN==h.PIN;assert fetch_portable_runtimes.PINS==r.PINS;assert all(callable(x.download_pinned) for x in (fetch_pebble,fetch_mihomo,fetch_portable_runtimes));print('safe imports')"
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
        self.assertEqual(mihomo.PIN, {'version':'1.19.32',
            'url':'https://github.com/MetaCubeX/mihomo/releases/download/v1.19.32/mihomo-linux-amd64-v1.19.32.gz',
            'sha256':'8451100836c9eda194331c2babfad490b2faf30cebc1a04b0e76fd8ac2d35d10',
            'source':'https://api.github.com/repos/MetaCubeX/mihomo/releases/assets/601415750'})
        python_pins = {
            'x86_64-gnu':('x86_64-unknown-linux-gnu','72748da13197c1fb161e3afeef20a6a385ff24f2165e6e2758e47008e7faba4c','539916161'),
            'x86_64-musl':('x86_64-unknown-linux-musl','1f37044c8cdbd74d5ee112a753c65ef209fedd169c98f3e4e748a93e27eb27a4','539916184'),
            'aarch64-gnu':('aarch64-unknown-linux-gnu','577b4bec0793ad1ff0cbff9adbd0df078eddde38a4c41bf5d83ad381a85ee39d','539915928'),
            'aarch64-musl':('aarch64-unknown-linux-musl','a0ad6f01b9204eba573a08927097b78143c564f98bea68a41ea1e172f041da3a','539915949')}
        self.assertEqual(set(runtimes.PINS),set(python_pins))
        for target,(platform,checksum,asset_id) in python_pins.items():
            filename='cpython-3.12.14%2B20260901-'+platform+'-install_only_stripped.tar.gz'
            self.assertEqual(runtimes.PINS[target],{'url':'https://github.com/astral-sh/python-build-standalone/releases/download/20260901/'+filename,'sha256':checksum})
            ids[filename]=asset_id
        ids['mihomo-linux-amd64-v1.19.32.gz']='601415750'
        ids['mihomo-linux-arm64-v1.19.32.gz']='601415736'
        self.assertEqual(len(download.ASSET_API),13)
        self.assertEqual(set(download.ASSET_API),{pin['url'] for group in cores.PINS.values() for pin in group.values()}|{URL,mihomo.PIN['url'],mihomo.ARM64_PIN['url']}|{pin['url'] for pin in runtimes.PINS.values()})
        for original,api in download.ASSET_API.items():
            download.validate_pair(original,api)
            self.assertEqual(api.rsplit('/',1)[1],ids[original.rsplit('/',1)[1]])

    def test_new_assets_preserve_original_one_call_and_only_transient_api_fallback(self):
        clients = [mihomo.PIN['url'], mihomo.ARM64_PIN['url']]
        for original in clients + [pin['url'] for pin in runtimes.PINS.values()]:
            runtime = original not in clients
            maximum, timeout = (160_000_000,120) if runtime else (100_000_000,90)
            for transient in (None,502,503,504):
                with self.subTest(original=original,transient=transient):
                    api=download.ASSET_API[original]
                    response=Response(url=api if transient else original)
                    opener=Mock()
                    opener.open.side_effect=([http_error(transient,original)[0],response] if transient else [response])
                    with redirect_stderr(io.StringIO()):
                        result=download.download_pinned(original,max_bytes=maximum,timeout=timeout,opener=opener)
                    self.assertEqual(result,b'fake archive bytes')
                    self.assertEqual(response.reads,[maximum+1]); self.assertTrue(response.stream.closed)
                    self.assertEqual(opener.open.call_count,2 if transient else 1)
                    self.assertEqual(opener.open.call_args_list[0].args,(original,))
                    self.assertEqual(opener.open.call_args_list[0].kwargs,{'timeout':timeout})
                    if transient:
                        request=opener.open.call_args.args[0]
                        self.assertEqual(request.full_url,api)
                        self.assertEqual(dict(request.header_items()),{'Accept':'application/octet-stream'})

    def test_runtime_budget_cannot_raise_existing_other_asset_limits(self):
        opener=Mock()
        runtime=next(iter(runtimes.PINS.values()))['url']
        for original,maximum,timeout in ((runtime,160_000_001,120),(runtime,160_000_000,121),
                                         (mihomo.PIN['url'],160_000_000,90),(mihomo.PIN['url'],100_000_000,120)):
            with self.subTest(original=original,maximum=maximum,timeout=timeout), self.assertRaises(ValueError):
                download.download_pinned(original,max_bytes=maximum,timeout=timeout,opener=opener)
        opener.open.assert_not_called()

    def test_bad_runtime_bytes_do_not_write_new_or_existing_archive(self):
        for target,pin in runtimes.PINS.items():
            for fallback in (False,True):
                original=pin['url']; response=Response(raw=b'{"untrusted":"metadata, not pinned archive"}',url=download.ASSET_API[original] if fallback else original)
                opener=Mock(); opener.open.side_effect=([http_error(504,original)[0],response] if fallback else [response])
                def fake_download(url,**kwargs): return download.download_pinned(url,opener=opener,**kwargs)
                with self.subTest(target=target,fallback=fallback), tempfile.TemporaryDirectory() as folder:
                    destination=Path(folder)/'runtimes'; destination.mkdir()
                    retained=destination/(target+'.tar.gz'); retained.write_bytes(b'original local fixture')
                    with patch.object(runtimes,'download_pinned',side_effect=fake_download), \
                            patch.object(Path,'write_bytes') as write, redirect_stderr(io.StringIO()), self.assertRaisesRegex(ValueError,'Portable Python checksum mismatch'):
                        runtimes.fetch(destination,[target])
                    write.assert_not_called(); self.assertEqual(retained.read_bytes(),b'original local fixture')
                    self.assertEqual(list(destination.iterdir()),[retained])
                    self.assertEqual(response.reads,[160_000_001]); self.assertTrue(response.stream.closed)
                    self.assertEqual(opener.open.call_count,2 if fallback else 1)

    def test_bad_mihomo_sha_never_decompresses_or_creates_destination(self):
        for fallback in (False,True):
            original=mihomo.PIN['url']; response=Response(raw=b'untrusted gzip bytes',url=download.ASSET_API[original] if fallback else original)
            opener=Mock(); opener.open.side_effect=([http_error(504,original)[0],response] if fallback else [response])
            def fake_download(url,**kwargs): return download.download_pinned(url,opener=opener,**kwargs)
            with self.subTest(fallback=fallback), tempfile.TemporaryDirectory() as folder:
                destination=Path(folder)/'uncreated'
                with patch.object(mihomo.platform,'machine',return_value='x86_64'), \
                        patch.object(mihomo,'download_pinned',side_effect=fake_download), \
                        patch.object(mihomo.gzip,'decompress') as decompress, \
                        patch.object(Path,'write_bytes') as write, redirect_stderr(io.StringIO()), self.assertRaisesRegex(ValueError,'Mihomo archive checksum mismatch'):
                    mihomo.fetch(destination)
                decompress.assert_not_called(); write.assert_not_called(); self.assertFalse(destination.exists())
                self.assertEqual(opener.open.call_count,2 if fallback else 1); self.assertTrue(response.stream.closed)


if __name__=='__main__': unittest.main()
