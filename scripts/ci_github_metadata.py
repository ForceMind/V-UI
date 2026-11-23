"""Fetch bounded public GitHub metadata in a credential-scoped CI step."""

import argparse
import json
import os
from pathlib import Path
import re
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from scripts.release_acceptance import BASELINES

CORE_TAGS = (
    ('sing-box', 'SagerNet/sing-box', 'v1.14.2'),
    ('xray', 'XTLS/Xray-core', 'v26.3.27'),
)
MAX_METADATA = 1_000_000
RETRY_WINDOW = 120
API = 'https://api.github.com'


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        return None


def retry_rate_limit(error, phase, deadline):
    """Log safe response fields and accept one server-directed, bounded delay."""
    headers = error.headers

    def number(name):
        value = headers.get(name, '') if headers is not None else ''
        return int(value) if re.fullmatch(r'[0-9]{1,12}', value) else None

    retry_after = number('Retry-After')
    remaining = number('X-RateLimit-Remaining')
    reset = number('X-RateLimit-Reset')
    rate_limited = error.code == 429 or (error.code == 403 and (remaining == 0 or retry_after is not None))
    label = 'rate limited' if rate_limited else 'request refused'
    print(f'{phase}: HTTP {error.code} {label}; Retry-After={retry_after} '
          f'X-RateLimit-Remaining={remaining} X-RateLimit-Reset={reset}', file=sys.stderr)
    if not rate_limited:
        return False
    delay = retry_after
    if remaining == 0 and reset is not None:
        reset_delay = max(1, reset - int(time.time()) + 1)
        delay = max(delay or 0, reset_delay)
    if delay is None or max(1, delay) >= deadline - time.monotonic():
        return False
    time.sleep(max(1, delay))
    return True


def _get(path, token):
    opener = build_opener(ProxyHandler({}), NoRedirect())
    request = Request(API + path, headers={
        'Authorization': 'Bearer ' + token,
        'Accept': 'application/vnd.github+json',
        'User-Agent': 'V-UI-CI-metadata',
    })
    deadline = time.monotonic() + RETRY_WINDOW
    for attempt in range(2):
        try:
            with opener.open(request, timeout=min(20, max(1, deadline - time.monotonic()))) as response:
                raw = response.read(MAX_METADATA + 1)
            if len(raw) > MAX_METADATA:
                raise ValueError('GitHub metadata exceeds its byte limit')
            return json.loads(raw)
        except HTTPError as error:
            error.close()
            if error.code not in (403, 429):
                raise ValueError(f'{path}: HTTP {error.code}') from None
            if not retry_rate_limit(error, path, deadline if attempt == 0 else 0):
                raise ValueError(f'{path}: HTTP {error.code}') from None
        except (TimeoutError, URLError, ConnectionError):
            raise ValueError(f'{path}: GitHub metadata request failed') from None


def prepare(kind, version, token):
    if not token or not re.fullmatch(r'[A-Za-z0-9_]+', token):
        raise ValueError('Scoped Actions token required')
    if kind == 'cores':
        result = {}
        for name, repository, tag in CORE_TAGS:
            commit = _get(f'/repos/{repository}/commits/{tag}', token)['sha']
            if not isinstance(commit, str) or not re.fullmatch(r'[a-f0-9]{40}', commit):
                raise ValueError(f'Invalid source commit for {name}')
            result[name] = commit
        return result
    if version not in BASELINES:
        raise ValueError('Fixed public baseline required')
    release = _get('/repos/ForceMind/V-UI/releases/tags/v' + version, token)
    if (release.get('tag_name') != 'v' + version
            or release.get('draft') is not False or release.get('prerelease') is not False):
        raise ValueError('Stable public release is required')
    return {
        'tag_name': release['tag_name'], 'draft': False, 'prerelease': False,
        'body': release['body'],
        'assets': [{key: asset[key] for key in ('id', 'name', 'digest', 'browser_download_url')}
                   for asset in release['assets']],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kind', choices=('cores', 'release'))
    parser.add_argument('--version')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if (args.kind == 'release') != (args.version is not None):
        parser.error('Supply a version for release metadata only')
    os.umask(0o077)
    try:
        value = prepare(args.kind, args.version, os.environ.get('VUI_CI_METADATA_TOKEN'))
        with args.output.open('x', encoding='utf-8') as output:
            json.dump(value, output, sort_keys=True)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        # Report only controlled diagnostics. Do not echo response fields or the token.
        if type(error) is ValueError:
            parser.exit(1, f'GitHub metadata preparation failed: {error}\n')
        parser.exit(1, 'GitHub metadata preparation failed: invalid response or output\n')


if __name__ == '__main__':
    main()
