"""Anonymous, bounded download of fixed CI/build assets, never runtime code.

The original immutable release URL is attempted once. Only its HTTP502/503/504
permits one fixed official asset-API attempt. Caller size/hash/format checks
remain mandatory: this helper never trusts or interprets GitHub metadata JSON.
"""
from __future__ import annotations
import ssl
import sys
import urllib.error
import urllib.request
from urllib.parse import urljoin, urlsplit

ASSET_API = {
    'https://github.com/SagerNet/sing-box/releases/download/v1.14.2/sing-box-1.14.2-linux-amd64-glibc.tar.gz':
        'https://api.github.com/repos/SagerNet/sing-box/releases/assets/585840737',
    'https://github.com/SagerNet/sing-box/releases/download/v1.14.2/sing-box-1.14.2-linux-amd64-musl.tar.gz':
        'https://api.github.com/repos/SagerNet/sing-box/releases/assets/585840769',
    'https://github.com/SagerNet/sing-box/releases/download/v1.14.2/sing-box-1.14.2-linux-arm64-glibc.tar.gz':
        'https://api.github.com/repos/SagerNet/sing-box/releases/assets/585840820',
    'https://github.com/SagerNet/sing-box/releases/download/v1.14.2/sing-box-1.14.2-linux-arm64-musl.tar.gz':
        'https://api.github.com/repos/SagerNet/sing-box/releases/assets/585840822',
    'https://github.com/XTLS/Xray-core/releases/download/v26.3.27/Xray-linux-64.zip':
        'https://api.github.com/repos/XTLS/Xray-core/releases/assets/383041956',
    'https://github.com/XTLS/Xray-core/releases/download/v26.3.27/Xray-linux-arm64-v8a.zip':
        'https://api.github.com/repos/XTLS/Xray-core/releases/assets/383041834',
    'https://github.com/letsencrypt/pebble/releases/download/v2.10.1/pebble-linux-amd64.tar.gz':
        'https://api.github.com/repos/letsencrypt/pebble/releases/assets/401025613',
    'https://github.com/MetaCubeX/mihomo/releases/download/v1.19.32/mihomo-linux-amd64-v1.19.32.gz':
        'https://api.github.com/repos/MetaCubeX/mihomo/releases/assets/601415750',
    'https://github.com/astral-sh/python-build-standalone/releases/download/20260901/cpython-3.12.14%2B20260901-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz':
        'https://api.github.com/repos/astral-sh/python-build-standalone/releases/assets/539916161',
    'https://github.com/astral-sh/python-build-standalone/releases/download/20260901/cpython-3.12.14%2B20260901-x86_64-unknown-linux-musl-install_only_stripped.tar.gz':
        'https://api.github.com/repos/astral-sh/python-build-standalone/releases/assets/539916184',
    'https://github.com/astral-sh/python-build-standalone/releases/download/20260901/cpython-3.12.14%2B20260901-aarch64-unknown-linux-gnu-install_only_stripped.tar.gz':
        'https://api.github.com/repos/astral-sh/python-build-standalone/releases/assets/539915928',
    'https://github.com/astral-sh/python-build-standalone/releases/download/20260901/cpython-3.12.14%2B20260901-aarch64-unknown-linux-musl-install_only_stripped.tar.gz':
        'https://api.github.com/repos/astral-sh/python-build-standalone/releases/assets/539915949',
}
TRANSIENT = frozenset({502, 503, 504})
ASSET_HOSTS = frozenset({'release-assets.githubusercontent.com', 'objects.githubusercontent.com'})
FALLBACK_MESSAGE = 'Pinned test asset: using fixed official GitHub asset API after transient release endpoint failure.'


def _need(condition, message='Invalid fixed test-asset download'):
    if not condition:
        raise ValueError(message)


def _url(value):
    _need(type(value) is str and 0 < len(value) <= 16384)
    invalid = False
    try:
        parsed = urlsplit(value)
        valid = (parsed.scheme == 'https' and parsed.hostname and not parsed.username and not parsed.password
                 and parsed.port in (None, 443) and not parsed.fragment)
    except ValueError:
        invalid = True
    if invalid:
        _need(False)
    _need(valid)
    return parsed


def validate_pair(original, api):
    source, target = _url(original), _url(api)
    left, right = source.path.split('/'), target.path.split('/')
    _need(source.netloc == 'github.com' and target.netloc == 'api.github.com'
          and not source.query and not target.query
          and len(left) == 7 and left[0] == '' and left[3:5] == ['releases', 'download']
          and all(left[index] and left[index] not in ('.', '..') for index in (1, 2, 5, 6)))
    _need(len(right) == 7 and right[:2] == ['', 'repos'] and right[2:4] == left[1:3]
          and right[4:6] == ['releases', 'assets'] and right[6].isascii() and right[6].isdigit()
          and 0 < len(right[6]) <= 20 and not right[6].startswith('0'))
    _need(ASSET_API.get(original) == api, 'Unknown immutable test-asset pairing')


def _allowed(url, original, api):
    parsed = _url(url)
    _need(url in (original, api) or parsed.hostname in ASSET_HOSTS, 'Test-asset redirect refused')
    return url


class OfficialAssetRedirects(urllib.request.HTTPRedirectHandler):
    """No HTTP downgrade, arbitrary host/repo or unbounded redirect-body read."""
    def __init__(self, original, api):
        self.original, self.api, self.hops = original, api, 0

    def http_error_302(self, request, response, code, message, headers):
        try:
            location = headers.get('Location')
            _need(type(location) is str and 0 < len(location) <= 16384, 'Test-asset redirect refused')
            target = _allowed(urljoin(request.full_url, location), self.original, self.api)
            self.hops += 1
            _need(self.hops <= 3 and request.get_method() == 'GET', 'Test-asset redirect limit')
            # No credentials/cookies ever follow a signed asset redirect.
            clean = {key: value for key, value in request.header_items()
                     if key.lower() not in {'authorization', 'proxy-authorization', 'cookie', 'host'}}
            redirected = urllib.request.Request(target, headers=clean, method='GET')
        finally:
            response.close()  # Do not drain an unbounded redirect body.
        return self.parent.open(redirected, timeout=request.timeout)

    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302


def _opener(original, api):
    context = ssl.create_default_context()
    _need(context.check_hostname is True and context.verify_mode == ssl.CERT_REQUIRED, 'Verified TLS required')
    # Preserve urllib's existing environment-managed download route.
    return urllib.request.build_opener(urllib.request.ProxyHandler(), urllib.request.HTTPSHandler(context=context),
                                      OfficialAssetRedirects(original, api))


def _attempt(opener, request, original, api, maximum, timeout):
    try:
        response = opener.open(request, timeout=timeout)
    except urllib.error.HTTPError as error:
        error.close()  # Close every failed HTTP response before any fallback.
        raise
    with response:
        _allowed(response.geturl(), original, api)
        _need(response.getcode() == 200, 'Unexpected test-asset response')
        length = response.headers.get('Content-Length')
        if length is not None:
            _need(type(length) is str and length.isascii() and length.isdigit() and len(length) <= 10,
                  'Invalid test-asset response size')
            _need(int(length) <= maximum, 'Test-asset response exceeds size limit')
        data = response.read(maximum + 1)
        _need(type(data) is bytes and len(data) <= maximum, 'Test-asset response exceeds size limit')
        return data


def download_pinned(original, *, max_bytes, timeout=90, opener=None):
    """Return untrusted bytes; callers must retain their original pinned digest."""
    _need(type(original) is str and original in ASSET_API, 'Unknown immutable test asset')
    api = ASSET_API[original]
    validate_pair(original, api)
    # Only the four registered Python archives retain their original larger budget.
    runtime = api.startswith('https://api.github.com/repos/astral-sh/python-build-standalone/releases/assets/')
    _need(type(max_bytes) is int and 0 < max_bytes <= (160_000_000 if runtime else 100_000_000))
    _need(type(timeout) in (int, float) and 0 < timeout <= (120 if runtime else 90))
    try:
        return _attempt(_opener(original, api) if opener is None else opener, original,
                        original, api, max_bytes, timeout)
    except urllib.error.HTTPError as error:
        if error.code not in TRANSIENT:
            raise
    print(FALLBACK_MESSAGE, file=sys.stderr)
    request = urllib.request.Request(api, headers={'Accept': 'application/octet-stream'}, method='GET')
    return _attempt(_opener(original, api) if opener is None else opener, request,
                    original, api, max_bytes, timeout)
