"""CI metadata boundaries with synthetic GitHub responses."""

import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from scripts import ci_github_metadata as metadata


TOKEN = 'fake_only_metadata_token'
TAG_PATH = '/repos/ForceMind/V-UI/releases/tags/v0.4.19'


class GitHubMetadataTests(unittest.TestCase):
    def test_only_fixed_core_api_requests_use_bearer_token(self):
        with patch.object(metadata, 'build_opener') as opener:
            client = opener.return_value
            client.open.side_effect = [io.BytesIO(b'{"sha":"' + char * 40 + b'"}')
                                       for char in (b'a', b'b')]
            result = metadata.prepare('cores', None, TOKEN)
        self.assertEqual(result, {'sing-box': 'a' * 40, 'xray': 'b' * 40})
        requests = [call.args[0] for call in client.open.call_args_list]
        self.assertEqual([request.full_url for request in requests], [
            'https://api.github.com/repos/SagerNet/sing-box/commits/v1.14.2',
            'https://api.github.com/repos/XTLS/Xray-core/commits/v26.3.27',
        ])
        self.assertTrue(all(request.get_header('Authorization') == 'Bearer ' + TOKEN for request in requests))
        self.assertIsInstance(opener.call_args.args[1], metadata.NoRedirect)
        self.assertIsNone(opener.call_args.args[1].redirect_request(
            requests[0], None, 302, 'redirect', {}, 'https://release-assets.githubusercontent.com/asset'))

    def test_only_fixed_stable_release_fields_leave_metadata_preparation(self):
        release = {
            'tag_name': 'v0.4.19', 'draft': False, 'prerelease': False, 'body': 'public content',
            'assets': [{'id': 42, 'name': 'install.sh', 'digest': 'sha256:' + 'a' * 64,
                        'browser_download_url': 'https://example.invalid/install.sh', 'extra': TOKEN}],
            'extra': TOKEN,
        }
        with patch.object(metadata, '_get', return_value=release) as get:
            self.assertNotIn(TOKEN, str(metadata.prepare('release', '0.4.19', TOKEN)))
            get.assert_called_once_with(TAG_PATH, TOKEN)
            with self.assertRaisesRegex(ValueError, 'Fixed public baseline'):
                metadata.prepare('release', '../../arbitrary', TOKEN)
            with self.assertRaisesRegex(ValueError, 'Stable public release'):
                metadata.prepare('release', '0.4.20', TOKEN)
        with self.assertRaisesRegex(ValueError, 'Scoped Actions token required'):
            metadata.prepare('cores', None, '')

    def test_rate_limit_one_retry_then_fail_without_waiting_again(self):
        errors = [HTTPError(metadata.API + TAG_PATH, 403, 'limited', {'Retry-After': '2'}, io.BytesIO())
                  for _ in range(2)]
        with patch.object(metadata, 'build_opener') as opener, patch.object(metadata.time, 'sleep') as sleep:
            opener.return_value.open.side_effect = errors
            with self.assertRaisesRegex(ValueError, 'HTTP 403'):
                metadata._get(TAG_PATH, TOKEN)
            self.assertEqual(opener.return_value.open.call_count, 2)
            sleep.assert_called_once_with(2)

    def test_ordinary_403_redirect_and_unaffordable_limit_fail_once(self):
        for status, headers in ((403, {}), (302, {'Location': 'https://example.invalid'}),
                                (429, {'Retry-After': '300'})):
            with self.subTest(status=status), patch.object(metadata, 'build_opener') as opener, \
                    patch.object(metadata.time, 'sleep') as sleep:
                opener.return_value.open.side_effect = HTTPError(
                    metadata.API + TAG_PATH, status, 'synthetic status', headers, io.BytesIO())
                with self.assertRaisesRegex(ValueError, f'HTTP {status}'):
                    metadata._get(TAG_PATH, TOKEN)
                opener.return_value.open.assert_called_once()
                sleep.assert_not_called()

    def test_exclusive_file_contains_only_prepared_public_values(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'metadata.json'
            with patch.dict('os.environ', {'VUI_CI_METADATA_TOKEN': TOKEN}), \
                    patch('sys.argv', ['ci_github_metadata', 'cores', '--output', str(output)]), \
                    patch.object(metadata.os, 'umask'), \
                    patch.object(metadata, 'prepare', return_value={'core': 'public'}) as prepare:
                metadata.main()
            prepare.assert_called_once_with('cores', None, TOKEN)
            self.assertEqual(output.read_text(encoding='utf-8'), '{"core": "public"}')
