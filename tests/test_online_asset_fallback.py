"""Pure mocked public asset transport; no network, installer, Git or package download."""
import hashlib
import io
import json
from pathlib import Path
import ssl
import socket
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request

import online_install_helpers as helpers

VERSION = '0.4.22'
COMMIT = 'a' * 40
PREFIX = 'https://github.com/ForceMind/V-UI/releases/download/v' + VERSION + '/'
METADATA = 'https://api.github.com/repos/ForceMind/V-UI/releases/tags/v' + VERSION
API = 'https://api.github.com/repos/ForceMind/V-UI/releases/assets/'
SMALL = ('install.sh', 'SHA256SUMS', 'RELEASE.json')


class Response:
    def __init__(self, url, raw, *, url_error=None, read_error=None):
        self.url = url; self.raw = raw; self.closed = False; self.read_limits = []
        self.url_error = url_error; self.read_error = read_error; self.partial = b''
    def __enter__(self): return self
    def __exit__(self, *args): self.closed = True
    def geturl(self):
        if self.url_error is not None: raise self.url_error
        return self.url
    def read(self, limit):
        self.read_limits.append(limit)
        if self.read_error is not None:
            self.partial = self.raw[:7]  # Simulate bytes buffered before the socket failed.
            raise self.read_error
        return self.raw[:limit]


class OnlineAssetFallbackTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='vui-public-asset-fake-')
        self.addCleanup(temporary.cleanup); self.source = Path(temporary.name)
        (self.source / 'VERSION').write_text(VERSION)
        (self.source / 'install.sh').write_bytes(b'fixed synthetic published installer\n')
        self.files = {'install.sh': (self.source / 'install.sh').read_bytes(),
            'SHA256SUMS': b'fixed synthetic whole checksum file\n',
            'RELEASE.json': json.dumps({'version': VERSION, 'source_commit': COMMIT}).encode()}
        self.assets = {name: {'name': name, 'id': number, 'browser_download_url': PREFIX + name,
            'digest': 'sha256:' + hashlib.sha256(self.files.get(name, b'')).hexdigest()}
            for number, name in enumerate(sorted(helpers.ASSETS), 1000)}
        self.release = {'tag_name': 'v' + VERSION, 'draft': False, 'prerelease': False,
            'assets': list(self.assets.values()), 'body': helpers.installation_command(self.files, VERSION)}
        self.failures = {}; self.primary = {}; self.alternate = {}; self.requests = []; self.responses = []; self.error_bodies = []
        self.metadata_raw = None; self.metadata_response = None

    def gateway(self, url, status=504):
        body = io.BytesIO(b'synthetic gateway failure'); self.error_bodies.append(body)
        return HTTPError(url, status, 'synthetic gateway', {}, body)

    def open(self, request, timeout):
        self.assertEqual(timeout, 60); self.requests.append(request)
        url = request.full_url
        if url in self.failures: raise self.failures[url]
        if url == METADATA:
            raw = self.metadata_raw if self.metadata_raw is not None else json.dumps(self.release).encode()
            result = self.metadata_response or Response(url, raw)
        elif url.startswith(PREFIX):
            result = self.primary.get(url[len(PREFIX):]) or Response(url, self.files[url[len(PREFIX):]])
        elif url.startswith(API):
            selected = next(item['name'] for item in self.assets.values() if API + str(item.get('id')) == url)
            result = self.alternate.get(selected) or Response(url, self.files[selected])
        else:
            raise AssertionError('Unexpected mocked public URL')
        self.responses.append(result); return result

    def invoke(self):
        opener = type('MockOpener', (), {'open': lambda unused, request, timeout: self.open(request, timeout)})()
        with patch.object(helpers, 'build_opener', return_value=opener) as factory, \
                patch.object(helpers.subprocess, 'check_output', return_value=COMMIT + '\n') as git:
            result = helpers.published_commands(self.source, VERSION, COMMIT, [], [])
            self.handlers = factory.call_args.args
            git.assert_called_once_with(['git', 'rev-parse', 'HEAD'], cwd=self.source, text=True)
            return result

    def api_requests(self):
        return [request for request in self.requests if request.full_url.startswith(API)]

    def test_three_verified_small_assets_each_get_one_same_id_fallback_and_no_auth(self):
        for name, status in zip(SMALL, (502, 503, 504)):
            self.failures[PREFIX + name] = self.gateway(PREFIX + name, status)
        with patch.dict('os.environ', {'GH_TOKEN': 'unused-fake-secret', 'GITHUB_TOKEN': 'unused-other-secret'}):
            result = self.invoke()
        self.assertEqual(result['canonical'], helpers.installation_command(self.files, VERSION))
        self.assertEqual(result['transport'], 'anonymous public HTTPS release assets')
        self.assertEqual(len(self.api_requests()), 3)
        for name in SMALL:
            url = API + str(self.assets[name]['id'])
            selected = [request for request in self.api_requests() if request.full_url == url]
            self.assertEqual(len(selected), 1)
            self.assertEqual(selected[0].get_header('Accept'), 'application/octet-stream')
            self.assertEqual(sum(request.full_url == PREFIX + name for request in self.requests), 1)
        for request in self.requests:
            self.assertFalse({'authorization', 'cookie'} & {key.lower() for key, _ in request.header_items()})
            self.assertNotIn('unused-fake-secret', request.full_url)
        self.assertTrue(all(body.closed for body in self.error_bodies))
        self.assertTrue(any(isinstance(handler, ProxyHandler) and handler.proxies == {} for handler in self.handlers))
        self.assertEqual([response.read_limits for response in self.responses], [[1000001], [100001], [64001], [16385]])
        self.assertTrue(all(response.closed for response in self.responses))

    def test_original_success_reads_once_and_does_not_require_new_asset_id_fields(self):
        for asset in self.assets.values(): asset.pop('id')
        result = self.invoke()
        self.assertEqual(result['canonical'], helpers.installation_command(self.files, VERSION))
        self.assertEqual(len(self.requests), 4); self.assertEqual(self.api_requests(), [])
        self.assertEqual({request.full_url for request in self.requests}, {METADATA, *(PREFIX + name for name in SMALL)})

    def test_fallback_rejects_invalid_id_or_unknown_metadata_url_without_alternate_request(self):
        for identifier in (None, True, False, 0, -1, '1000', 1.0):
            with self.subTest(identifier=identifier):
                self.setUp(); self.assets['install.sh']['id'] = identifier
                self.failures[PREFIX + 'install.sh'] = self.gateway(PREFIX + 'install.sh')
                with self.assertRaisesRegex(AssertionError, 'Verified public asset ID'): self.invoke()
                self.assertEqual(self.api_requests(), []); self.assertTrue(self.error_bodies[0].closed)
        self.setUp(); self.assets['install.sh']['browser_download_url'] = 'https://github.com/Other/V-UI/releases/download/v' + VERSION + '/install.sh'
        with self.assertRaisesRegex(AssertionError, 'Unexpected public asset URL'): self.invoke()
        self.assertEqual(len(self.requests), 1); self.assertEqual(self.api_requests(), [])

    def test_403_404_ssl_and_release_metadata_failures_never_change_transport(self):
        for fault in (403, 404, 'ssl', 'metadata-504', 'metadata-format'):
            with self.subTest(fault=fault):
                self.setUp(); url = PREFIX + 'install.sh'
                if fault == 'metadata-format': self.metadata_raw = b'{invalid metadata'; url = METADATA
                elif fault == 'ssl': self.failures[url] = ssl.SSLCertVerificationError('synthetic TLS rejection')
                else:
                    if fault == 'metadata-504': url = METADATA
                    self.failures[url] = self.gateway(url, 504 if fault == 'metadata-504' else fault)
                with self.assertRaises((HTTPError, ssl.SSLError, ValueError)): self.invoke()
                self.assertEqual(self.api_requests(), [])
                self.assertEqual(sum(request.full_url == url for request in self.requests), 1)
                self.assertTrue(all(body.closed for body in self.error_bodies))

    def test_byte_digest_source_complete_asset_set_and_command_checks_are_not_relaxed(self):
        for fault, expected in (('digest', 'metadata/digest'), ('source', 'installer differs'),
                ('record', 'source identity'), ('assets', 'complete fixed asset'), ('command', 'installation command')):
            with self.subTest(fault=fault):
                self.setUp()
                if fault == 'digest': self.assets['install.sh']['digest'] = 'sha256:' + '0' * 64
                elif fault == 'source': (self.source / 'install.sh').write_bytes(b'different local source\n')
                elif fault == 'record':
                    self.files['RELEASE.json'] = json.dumps({'version': VERSION, 'source_commit': 'b' * 40}).encode()
                    self.assets['RELEASE.json']['digest'] = 'sha256:' + hashlib.sha256(self.files['RELEASE.json']).hexdigest()
                elif fault == 'assets': self.release['assets'] = [asset for asset in self.release['assets'] if asset['name'] != 'vui-linux-aarch64-musl.zip']
                else: self.release['body'] = 'untrusted command text'
                with self.assertRaisesRegex(AssertionError, expected): self.invoke()
                self.assertEqual(self.api_requests(), [])
        self.setUp(); self.assets['install.sh']['digest'] = 'not-a-digest'
        self.failures[PREFIX + 'install.sh'] = self.gateway(PREFIX + 'install.sh')
        with self.assertRaisesRegex(AssertionError, 'Verified public asset ID'): self.invoke()
        self.assertEqual(self.api_requests(), [])

    def test_failed_alternate_is_not_retried_and_both_gateway_bodies_are_closed(self):
        self.failures[PREFIX + 'install.sh'] = self.gateway(PREFIX + 'install.sh')
        self.failures[API + str(self.assets['install.sh']['id'])] = self.gateway(API + str(self.assets['install.sh']['id']))
        with self.assertRaises(HTTPError): self.invoke()
        self.assertEqual(len(self.api_requests()), 1); self.assertEqual(len(self.requests), 3)
        self.assertTrue(all(body.closed for body in self.error_bodies))

    def test_alternate_keeps_exact_digest_bounds_and_official_https_response_identity(self):
        for fault, final in (('digest', None), ('bound', None), ('http', 'http://release-assets.githubusercontent.com/fixture'),
                            ('foreign', 'https://foreign.example.test/fixture'),
                            ('other-repo', 'https://api.github.com/repos/Other/V-UI/releases/assets/1000')):
            with self.subTest(fault=fault):
                self.setUp(); self.failures[PREFIX + 'install.sh'] = self.gateway(PREFIX + 'install.sh')
                raw = b'x' * 100001 if fault == 'bound' else b'wrong bytes' if fault == 'digest' else self.files['install.sh']
                self.alternate['install.sh'] = Response(final or API + str(self.assets['install.sh']['id']), raw)
                with self.assertRaises(AssertionError): self.invoke()
                self.assertEqual(len(self.api_requests()), 1)
                self.assertTrue(self.alternate['install.sh'].closed)

    def test_fallback_redirects_keep_context_and_reject_http_foreign_repo_and_other_id(self):
        self.failures[PREFIX + 'install.sh'] = self.gateway(PREFIX + 'install.sh'); self.invoke()
        request = self.api_requests()[0]
        handler = next(handler for handler in self.handlers if isinstance(handler, helpers.HTTPRedirectHandler))
        target = 'https://release-assets.githubusercontent.com/github-production-release-asset/fixture?signature=fake'
        redirected = handler.redirect_request(request, None, 302, 'Found', {}, target)
        self.assertEqual(redirected.full_url, target)
        self.assertEqual(redirected._verified_asset_urls, request._verified_asset_urls)
        self.assertIsNone(redirected.get_header('Authorization'))
        for target in ('http://release-assets.githubusercontent.com/fixture', 'https://foreign.example.test/fixture',
                       'https://api.github.com/repos/Other/V-UI/releases/assets/1000', API + '999999999'):
            with self.subTest(target=target), self.assertRaisesRegex(AssertionError, 'Unexpected public asset API redirect'):
                handler.redirect_request(redirected, None, 302, 'Found', {}, target)


    def test_typed_open_timeouts_get_one_same_id_fallback_without_changing_success_bytes(self):
        for error in (TimeoutError('open status timed out'), socket.timeout('socket status timed out'),
                      URLError(TimeoutError('wrapped timeout')), URLError(socket.timeout('wrapped socket timeout'))):
            with self.subTest(kind=type(error).__name__):
                self.setUp(); self.failures[PREFIX + 'install.sh'] = error
                result = self.invoke()
                self.assertEqual(result['canonical'], helpers.installation_command(self.files, VERSION))
                self.assertEqual(len(self.api_requests()), 1)
                self.assertEqual(self.api_requests()[0].full_url, API + str(self.assets['install.sh']['id']))
                self.assertEqual(sum(request.full_url == PREFIX + 'install.sh' for request in self.requests), 1)

    def test_response_status_and_partial_body_timeout_close_and_discard_original_stream(self):
        for boundary in ('status', 'body'):
            with self.subTest(boundary=boundary):
                self.setUp()
                original = Response(PREFIX + 'install.sh', self.files['install.sh'],
                    url_error=TimeoutError('status timeout') if boundary == 'status' else None,
                    read_error=socket.timeout('partial body timeout') if boundary == 'body' else None)
                self.primary['install.sh'] = original
                result = self.invoke()
                self.assertTrue(original.closed)
                if boundary == 'body':
                    self.assertTrue(original.partial); self.assertEqual(original.read_limits, [100001])
                self.assertEqual(len(self.api_requests()), 1)
                self.assertEqual(result['canonical'], helpers.installation_command(self.files, VERSION),
                                 'Fallback must validate complete fresh bytes, never append a partial prefix')

    def test_api_timeout_has_no_third_call_and_its_response_stream_is_closed(self):
        for boundary in ('open', 'status', 'body'):
            with self.subTest(boundary=boundary):
                self.setUp(); self.failures[PREFIX + 'install.sh'] = TimeoutError('first open timeout')
                url = API + str(self.assets['install.sh']['id'])
                if boundary == 'open': self.failures[url] = URLError(socket.timeout('API timeout'))
                else:
                    self.alternate['install.sh'] = Response(url, self.files['install.sh'],
                        url_error=TimeoutError('API status timeout') if boundary == 'status' else None,
                        read_error=TimeoutError('API body timeout') if boundary == 'body' else None)
                with self.assertRaises((TimeoutError, socket.timeout, URLError)): self.invoke()
                self.assertEqual(len(self.requests), 3); self.assertEqual(len(self.api_requests()), 1)
                if boundary != 'open': self.assertTrue(self.alternate['install.sh'].closed)

    def test_non_timeout_url_errors_and_metadata_timeouts_never_fallback(self):
        for error in (URLError('timed out'), URLError(ConnectionResetError('reset')),
                      URLError(ssl.SSLCertVerificationError('TLS timed out')), URLError('generic network error')):
            with self.subTest(reason=type(error.reason).__name__):
                self.setUp(); self.failures[PREFIX + 'install.sh'] = error
                with self.assertRaises(URLError): self.invoke()
                self.assertEqual(self.api_requests(), []); self.assertEqual(len(self.requests), 2)
        for boundary in ('open', 'body'):
            with self.subTest(metadata=boundary):
                self.setUp()
                if boundary == 'open': self.failures[METADATA] = TimeoutError('metadata timeout')
                else:
                    self.metadata_response = Response(METADATA, json.dumps(self.release).encode(),
                                                       read_error=URLError(socket.timeout('metadata body timeout')))
                with self.assertRaises((TimeoutError, URLError)): self.invoke()
                self.assertEqual(self.api_requests(), []); self.assertEqual(len(self.requests), 1)
                if boundary == 'body': self.assertTrue(self.metadata_response.closed)

    def test_raw_setup_timeout_still_has_no_alternate_transport(self):
        (self.source / 'setup.sh').write_bytes(b'synthetic actual bootstrap')
        url = 'https://raw.githubusercontent.com/ForceMind/V-UI/main/setup.sh'
        self.failures[url] = TimeoutError('raw bootstrap timeout')
        opener = type('MockOpener', (), {'open': lambda unused, request, timeout: self.open(request, timeout)})()
        with patch.object(helpers, 'build_opener', return_value=opener), \
                patch.object(helpers.subprocess, 'check_output', return_value=COMMIT + '\n'):
            with self.assertRaises(TimeoutError):
                helpers.setup_commands(self.source, VERSION, COMMIT, COMMIT, 'main', [], [], release_source=self.source)
        self.assertEqual(self.api_requests(), [])
        self.assertEqual(sum(request.full_url == url for request in self.requests), 1)



if __name__ == '__main__':
    unittest.main()
