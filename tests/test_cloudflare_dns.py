"""Isolated stdlib fake transport tests: no real token, account, DNS or network."""
from __future__ import annotations
import base64
import io
import json
from pathlib import Path
import ssl
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit


from app.certificates import cloudflare as cf
from app.certificates.errors import CertificateError

ZONE = 'a' * 32
RECORD = 'b' * 32
NAME = '_acme-challenge.example.test'
VALUE = base64.urlsafe_b64encode(bytes(range(32))).decode().rstrip('=')
OTHER = base64.urlsafe_b64encode(b'Z' * 32).decode().rstrip('=')
OWNERSHIP = 'v-ui:' + 'c' * 64
TOKEN = 'fake_test_token_not_a_real_credential'


def record(**changes):
    value = {'id': RECORD, 'type': 'TXT', 'name': NAME, 'content': VALUE, 'comment': OWNERSHIP}
    value.update(changes)
    return value


def envelope(result, *, info=None, success=True, errors=None):
    value = {'success': success, 'errors': [] if errors is None else errors, 'messages': [], 'result': result}
    if info is not None: value['result_info'] = info
    return 200, json.dumps(value).encode()


def listing(rows, *, page=1, per_page=50, total=None, total_pages=None):
    count = len(rows) if total is None else total
    pages = (count + per_page - 1) // per_page if total_pages is None else total_pages
    return envelope(rows, info={'page': page, 'per_page': per_page, 'count': len(rows),
                               'total_count': count, 'total_pages': pages})


class FakeTransport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, **options):
        self.calls.append((method, url, options))
        if not self.responses: raise AssertionError('unexpected transport call')
        result = self.responses.pop(0)
        if isinstance(result, Exception): raise result
        return result(method, url, options) if callable(result) else result


class CloudflareTests(unittest.TestCase):
    def client(self, *responses):
        fake = FakeTransport(*responses)
        return cf.CloudflareClient(TOKEN, transport=fake), fake

    def rejected(self, operation, code):
        with self.assertRaises(CertificateError) as error: operation()
        self.assertEqual(error.exception.code, code)
        self.assertNotIn(TOKEN, str(error.exception)); self.assertNotIn(TOKEN, repr(error.exception))
        return error.exception

    def test_discover_exact_unique_zone_is_read_only_and_bounded(self):
        client, fake = self.client(listing([{'id': ZONE, 'name': 'example.test'}], per_page=2))
        self.assertEqual(client.discover_zone('example.test'), {'zone_id': ZONE, 'zone_name': 'example.test'})
        method, url, options = fake.calls[0]
        self.assertEqual(method, 'GET')
        self.assertEqual(url, cf.BASE + '/zones?name=example.test&page=1&per_page=2')
        self.assertIsNone(options['body']); self.assertEqual(options['timeout'], 10)
        self.assertEqual(options['max_bytes'], 256 * 1024)
        self.assertEqual(options['headers']['Authorization'], 'Bearer ' + TOKEN)
        self.assertNotIn(TOKEN, repr(client))

    def test_zone_absence_ambiguity_and_wrong_identity_are_distinct_failures(self):
        cases = [(listing([], per_page=2), 'DNS_ZONE_NOT_FOUND'),
                 (listing([{'id': ZONE, 'name': 'example.test'}] * 2, per_page=2), 'DNS_ZONE_NOT_UNIQUE'),
                 (listing([{'id': ZONE, 'name': 'other.test'}], per_page=2), 'DNS_ZONE_IDENTITY_MISMATCH')]
        for response, code in cases:
            client, _ = self.client(response)
            with self.subTest(code=code): self.rejected(lambda: client.discover_zone('example.test'), code)

    def test_get_only_404_is_absence_and_all_other_failures_remain_errors(self):
        client, _ = self.client((404, b'not json'))
        self.assertIsNone(client.get_record(ZONE, RECORD))
        client, _ = self.client(envelope(record()))
        value = client.get_record(ZONE, RECORD)
        self.assertEqual(value, {**record(), 'zone_id': ZONE})
        for response, code in ((envelope(None), 'DNS_PROVIDER_INVALID_RESPONSE'),
                               ((403, b'upstream token: ' + TOKEN.encode()), 'DNS_PROVIDER_FORBIDDEN'),
                               ((429, b'limit'), 'DNS_PROVIDER_RATE_LIMITED'),
                               ((302, b'https://untrusted.invalid'), 'DNS_PROVIDER_REDIRECT_REFUSED'),
                               ((400, b'error'), 'DNS_PROVIDER_REJECTED')):
            client, fake = self.client(response)
            with self.subTest(code=code): self.rejected(lambda: client.get_record(ZONE, RECORD), code)
            self.assertEqual(len(fake.calls), 1)

    def test_create_single_txt_preserves_exact_ownership_and_returns_allowlist(self):
        client, fake = self.client(envelope(record(content='"' + VALUE + '"', ignored='private payload')))
        value = client.create_txt(ZONE, NAME, VALUE, OWNERSHIP)
        self.assertEqual(value, {**record(), 'zone_id': ZONE})
        method, url, options = fake.calls[0]
        self.assertEqual((method, url), ('POST', cf.BASE + '/zones/' + ZONE + '/dns_records'))
        self.assertEqual(json.loads(options['body']), {'type': 'TXT', 'name': NAME, 'content': VALUE, 'comment': OWNERSHIP})
        self.assertEqual(len(fake.calls), 1)

    def test_post_and_delete_never_retry_unknown_mutation(self):
        for method in ('create', 'delete'):
            for failure in (TimeoutError(TOKEN), ConnectionError(TOKEN), (500, TOKEN.encode())):
                client, fake = self.client(failure)
                call = (lambda: client.create_txt(ZONE, NAME, VALUE, OWNERSHIP)) if method == 'create' else (lambda: client.delete_txt(ZONE, RECORD))
                with self.subTest(method=method, failure=type(failure).__name__):
                    error = self.rejected(call, 'DNS_PROVIDER_WRITE_UNKNOWN')
                    self.assertIsNone(error.__context__)
                self.assertEqual(len(fake.calls), 1)

    def test_all_sent_write_unconfirmed_responses_are_unknown_without_retry(self):
        good = json.dumps({'success': True, 'errors': [], 'result': record()}).encode()
        responses = ((200, b'{'), (200, good[:-1] + b',"success":true}'),
                     (200, b'x' * (cf.MAX_RESPONSE + 1)), (200, b'[]'), (True, b'{}'),
                     (302, b'go elsewhere'), envelope(record(id='bad')),
                     envelope(record(), success=False, errors=[{'message': TOKEN}]))
        for response in responses:
            for method in ('post', 'delete'):
                client, fake = self.client(response)
                operation = (lambda: client.create_txt(ZONE, NAME, VALUE, OWNERSHIP)) if method == 'post' else (lambda: client.delete_txt(ZONE, RECORD))
                with self.subTest(method=method, response=str(response)[:30]):
                    self.rejected(operation, 'DNS_PROVIDER_WRITE_UNKNOWN')
                self.assertEqual(len(fake.calls), 1)

    def test_each_public_method_honors_one_shared_deadline(self):
        operations = (lambda client: client.discover_zone('example.test', deadline=99),
                      lambda client: client.get_record(ZONE, RECORD, deadline=99),
                      lambda client: client.find_txt(ZONE, NAME, OWNERSHIP, deadline=99),
                      lambda client: client.create_txt(ZONE, NAME, VALUE, OWNERSHIP, deadline=99),
                      lambda client: client.delete_txt(ZONE, RECORD, deadline=99))
        for operation in operations:
            client, fake = self.client()
            with patch.object(cf.time, 'monotonic', return_value=100):
                self.rejected(lambda: operation(client), 'DNS_PROVIDER_TIMEOUT')
            self.assertEqual(fake.calls, [])

    def test_get_retries_once_only_for_transport_or_server_failures(self):
        for first in (TimeoutError(TOKEN), ConnectionError(TOKEN), (503, b'busy')):
            client, fake = self.client(first, envelope(record()))
            self.assertEqual(client.get_record(ZONE, RECORD)['id'], RECORD)
            self.assertEqual(len(fake.calls), 2)
        client, fake = self.client(TimeoutError(TOKEN), TimeoutError(TOKEN))
        self.rejected(lambda: client.get_record(ZONE, RECORD), 'DNS_PROVIDER_TIMEOUT')
        self.assertEqual(len(fake.calls), 2)
        for failure, code in ((ssl.SSLError(TOKEN), 'DNS_PROVIDER_TLS_FAILED'),
                              (RuntimeError(TOKEN), 'DNS_PROVIDER_UNAVAILABLE'),
                              (CertificateError(TOKEN), 'DNS_PROVIDER_UNAVAILABLE'),
                              (CertificateError([TOKEN]), 'DNS_PROVIDER_UNAVAILABLE')):
            client, fake = self.client(failure)
            error = self.rejected(lambda: client.get_record(ZONE, RECORD), code)
            self.assertIsNone(error.__context__); self.assertEqual(len(fake.calls), 1)

    def test_delete_exact_id_and_already_absent_are_successful_but_not_other_responses(self):
        for response in (envelope({'id': RECORD}), (404, b'')):
            client, fake = self.client(response)
            self.assertIsNone(client.delete_txt(ZONE, RECORD))
            self.assertEqual(fake.calls[0][0], 'DELETE')
            self.assertEqual(fake.calls[0][1], cf.BASE + '/zones/' + ZONE + '/dns_records/' + RECORD)
            self.assertIsNone(fake.calls[0][2]['body'])
        client, _ = self.client(envelope({'id': 'd' * 32}))
        self.rejected(lambda: client.delete_txt(ZONE, RECORD), 'DNS_PROVIDER_WRITE_UNKNOWN')

    def test_find_is_exact_locally_case_sensitive_and_successful_empty_is_not_failure(self):
        client, fake = self.client(listing([record(content='"' + VALUE + '"')]))
        self.assertEqual(client.find_txt(ZONE, NAME, OWNERSHIP), [{**record(), 'zone_id': ZONE}])
        query = parse_qs(urlsplit(fake.calls[0][1]).query)
        self.assertEqual(query, {'match': ['all'], 'type': ['TXT'], 'name.exact': [NAME],
                                 'comment.exact': [OWNERSHIP], 'page': ['1'], 'per_page': ['50']})
        client, _ = self.client(listing([])); self.assertEqual(client.find_txt(ZONE, NAME, OWNERSHIP), [])
        client, _ = self.client(envelope([], success=False, errors=[{'message': TOKEN}]))
        self.rejected(lambda: client.find_txt(ZONE, NAME, OWNERSHIP), 'DNS_PROVIDER_REJECTED')
        for change in ({'comment': OWNERSHIP.upper()}, {'name': '_acme-challenge.other.test'}):
            client, _ = self.client(listing([record(**change)]))
            self.rejected(lambda: client.find_txt(ZONE, NAME, OWNERSHIP), 'DNS_RECORD_OWNERSHIP_MISMATCH')

    def test_two_page_find_is_bounded_and_never_returns_partial_or_duplicate_records(self):
        first = [record(id=f'{index:032x}') for index in range(50)]
        last = record(id=f'{50:032x}')
        client, fake = self.client(listing(first, total=51), listing([last], page=2, total=51))
        self.assertEqual(len(client.find_txt(ZONE, NAME, OWNERSHIP)), 51)
        self.assertEqual(len(fake.calls), 2)
        for second, code in ((listing([first[0]], page=2, total=51), 'DNS_PROVIDER_DUPLICATE_RECORD'),
                              (listing([last, record(id=f'{51:032x}')], page=2, total=52), 'DNS_PROVIDER_PAGINATION_CHANGED')):
            client, _ = self.client(listing(first, total=51), second)
            self.rejected(lambda: client.find_txt(ZONE, NAME, OWNERSHIP), code)
        client, fake = self.client(listing(first, total=101))
        self.rejected(lambda: client.find_txt(ZONE, NAME, OWNERSHIP), 'DNS_PROVIDER_RESULT_LIMIT')
        self.assertEqual(len(fake.calls), 1)

    def test_bad_pagination_is_not_an_empty_list(self):
        valid = json.loads(listing([])[1])
        for change in ({'page': 2}, {'count': True}, {'per_page': 100}, {'total_count': 1}, {'total_pages': 99}):
            value = {**valid, 'result_info': {**valid['result_info'], **change}}
            client, _ = self.client((200, json.dumps(value).encode()))
            self.rejected(lambda: client.find_txt(ZONE, NAME, OWNERSHIP), 'DNS_PROVIDER_INVALID_RESPONSE')
        client, _ = self.client(envelope([]))
        self.rejected(lambda: client.find_txt(ZONE, NAME, OWNERSHIP), 'DNS_PROVIDER_INVALID_RESPONSE')

    def test_remote_record_fields_cannot_silently_change_id_zone_type_or_shape(self):
        for change, code in (({'id': 'd' * 32}, 'DNS_RECORD_IDENTITY_MISMATCH'),
                              ({'zone_id': 'd' * 32}, 'DNS_RECORD_IDENTITY_MISMATCH'),
                              ({'zone_name': 'other.test'}, 'DNS_RECORD_IDENTITY_MISMATCH'),
                              ({'type': 'A'}, 'DNS_RECORD_IDENTITY_MISMATCH'),
                              ({'comment': 123}, 'DNS_PROVIDER_INVALID_RESPONSE')):
            client, _ = self.client(envelope(record(**change)))
            self.rejected(lambda: client.get_record(ZONE, RECORD), code)
        for change in ({'content': OTHER}, {'comment': 'v-ui:' + 'd' * 64}):
            client, _ = self.client(envelope(record(**change)))
            self.rejected(lambda: client.create_txt(ZONE, NAME, VALUE, OWNERSHIP), 'DNS_PROVIDER_WRITE_UNKNOWN')

    def test_strict_json_size_duplicate_keys_nonfinite_utf8_and_response_type(self):
        good = json.dumps({'success': True, 'errors': [], 'result': record()}).encode()
        bad = [good[:-1] + b',"success":true}', b'{"nested":{"x":1,"x":1}}',
               b'{"x":NaN}', b'{"x":Infinity}', b'{"x":1e999}', b'{"x":"\xff"}',
               b'{"x":"\\ud800"}', b'{"x":' + b'1' * 40 + b'}', b'[]', b'{']
        for raw in bad:
            client, fake = self.client((200, raw))
            error = self.rejected(lambda: client.get_record(ZONE, RECORD), 'DNS_PROVIDER_INVALID_RESPONSE')
            self.assertIsNone(error.__context__); self.assertEqual(len(fake.calls), 1)
        client, _ = self.client((200, b'x' * (cf.MAX_RESPONSE + 1)))
        self.rejected(lambda: client.get_record(ZONE, RECORD), 'DNS_PROVIDER_RESPONSE_TOO_LARGE')
        for invalid in ((True, b'{}'), (200, '{}'), {'status': 200, 'body': b'{}'}):
            client, _ = self.client(invalid)
            self.rejected(lambda: client.get_record(ZONE, RECORD), 'DNS_PROVIDER_INVALID_RESPONSE')

    def test_all_inputs_are_bounded_before_transport_and_base_url_is_not_an_option(self):
        client, fake = self.client()
        for invalid in ('../escape', 'A' * 32, 'a' * 31, True, None):
            self.rejected(lambda: client.get_record(invalid, RECORD), 'DNS_INVALID_IDENTIFIER')
        for invalid in ('https://example.test', 'example.test.', 'Example.test', '*.example.test',
                        '127.0.0.1', 'one', 'a..test', 'x' * 64 + '.test'):
            self.rejected(lambda: client.discover_zone(invalid), 'DNS_INVALID_NAME')
        for invalid in ('other.example.test', '_acme-challenge.example.test/', NAME + ':443', NAME + '\n'):
            self.rejected(lambda: client.create_txt(ZONE, invalid, VALUE, OWNERSHIP), 'DNS_INVALID_OWNER' if not invalid.startswith('_acme-challenge.') else 'DNS_INVALID_NAME')
        for invalid in ('', 'secret\nheader', ' token', 'x' * 2049, None):
            self.rejected(lambda: cf.CloudflareClient(invalid), 'DNS_INVALID_TOKEN')
        self.assertEqual(fake.calls, [])
        with self.assertRaises(TypeError): cf.CloudflareClient(TOKEN, base_url='https://elsewhere.invalid')

    def test_txt_and_ownership_parsers_allow_only_canonical_single_values(self):
        self.assertEqual(cf.parse_txt_content(VALUE), VALUE)
        self.assertEqual(cf.parse_txt_content('"' + VALUE + '"'), VALUE)
        for invalid in (VALUE + '=', ' ' + VALUE, '"' + VALUE + '" "other"', '""' + VALUE + '""',
                        VALUE[:-1] + 'B', None, True):
            self.rejected(lambda: cf.parse_txt_content(invalid), 'DNS_INVALID_TXT_VALUE')
        self.assertEqual(cf.parse_ownership(OWNERSHIP), OWNERSHIP)
        for invalid in ('c' * 64, OWNERSHIP.upper(), OWNERSHIP + '\n', 'v-ui:' + 'c' * 32, None):
            self.rejected(lambda: cf.parse_ownership(invalid), 'DNS_INVALID_OWNERSHIP')
        result = cf.verify_ownership(record(content='"' + VALUE + '"'), zone_id=ZONE, name=NAME,
                                     value=VALUE, ownership=OWNERSHIP, record_id=RECORD)
        self.assertEqual(result['content'], VALUE)
        self.rejected(lambda: cf.verify_ownership(record(), zone_id=ZONE, name=NAME, value=OTHER,
                                                   ownership=OWNERSHIP), 'DNS_RECORD_OWNERSHIP_MISMATCH')

    def test_authority_proves_label_suffix_and_checks_cname_plus_every_below_zone_ns(self):
        owner = '_acme-challenge.a.b.example.test'
        client, fake = self.client(*[listing([], per_page=2) for _ in range(4)])
        self.assertIsNone(client.assert_authoritative_owner(ZONE, 'example.test', owner))
        queries = [parse_qs(urlsplit(url).query) for _, url, _ in fake.calls]
        self.assertEqual([(q['type'][0], q['name.exact'][0]) for q in queries],
                         [('CNAME', owner), ('NS', owner), ('NS', 'a.b.example.test'), ('NS', 'b.example.test')])
        self.assertTrue(all(q['per_page'] == ['2'] and q['match'] == ['all'] for q in queries))
        self.assertFalse(any(q['name.exact'] == ['example.test'] for q in queries))
        for bad in ('_acme-challenge.badexample.test', '_acme-challenge.example.test.evil.test'):
            client, fake = self.client()
            self.rejected(lambda: client.assert_authoritative_owner(ZONE, 'example.test', bad), 'DNS_OWNER_OUTSIDE_ZONE')
            self.assertEqual(fake.calls, [])

    def test_cname_and_parent_ns_delegations_refuse_before_any_write(self):
        for index in range(3):
            owner = '_acme-challenge.a.example.test'
            names = [('CNAME', owner), ('NS', owner), ('NS', 'a.example.test')]
            kind, name = names[index]
            row = {'id': RECORD, 'name': name, 'type': kind, 'content': 'delegated.example.net'}
            responses = [listing([], per_page=2) for _ in range(index)] + [listing([row], per_page=2)]
            client, fake = self.client(*responses)
            self.rejected(lambda: client.assert_authoritative_owner(ZONE, 'example.test', owner), 'DNS_DELEGATION_UNSUPPORTED')
            self.assertTrue(all(method == 'GET' for method, _, _ in fake.calls))
            self.assertEqual(len(fake.calls), index + 1)
        client, _ = self.client((403, b'error'))
        self.rejected(lambda: client.assert_authoritative_owner(ZONE, 'example.test', NAME), 'DNS_PROVIDER_FORBIDDEN')

    def test_authority_total_budget_and_single_request_duration_fail_closed(self):
        client, fake = self.client()
        with patch.object(cf.time, 'monotonic', return_value=100):
            self.rejected(lambda: client.assert_authoritative_owner(ZONE, 'example.test', NAME, deadline=99), 'DNS_PROVIDER_TIMEOUT')
        self.assertEqual(fake.calls, [])
        now = [100]
        def slow(*_):
            now[0] += 11
            return envelope(record())
        client, fake = self.client(slow, slow)
        with patch.object(cf.time, 'monotonic', side_effect=lambda: now[0]):
            self.rejected(lambda: client.get_record(ZONE, RECORD), 'DNS_PROVIDER_TIMEOUT')
        self.assertEqual(len(fake.calls), 2)

    def test_default_https_is_verified_direct_fixed_host_and_never_follows_redirect(self):
        class Response:
            status = 302
            def getheader(self, key):
                return '0' if key == 'Content-Length' else 'https://elsewhere.invalid'
            def read1(self, amount): return b''
            def isclosed(self): return False
        connection = Mock()
        connection.getresponse.return_value = Response()
        with patch.object(cf.http.client, 'HTTPSConnection', return_value=connection) as connect, \
                patch.dict('os.environ', {'HTTPS_PROXY': 'http://untrusted.invalid', 'ALL_PROXY': 'http://untrusted.invalid'}):
            client = cf.CloudflareClient(TOKEN)
            self.rejected(lambda: client.get_record(ZONE, RECORD), 'DNS_PROVIDER_REDIRECT_REFUSED')
        connect.assert_called_once()
        self.assertEqual(connect.call_args.args, ('api.cloudflare.com',))
        self.assertEqual(connect.call_args.kwargs['port'], 443)
        context = connect.call_args.kwargs['context']
        self.assertTrue(context.check_hostname); self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        connection.request.assert_called_once()
        self.assertTrue(connection.request.call_args.args[1].startswith('/client/v4/zones/'))
        connection.close.assert_called_once()

    def test_default_https_handles_connection_close_after_last_body_chunk(self):
        class Socket:
            closed = False
            def settimeout(self, _):
                if self.closed: raise OSError('socket closed after response body')
        sock = Socket()
        class Response:
            status = 200
            closed = False
            def getheader(self, _): return str(len(envelope(record())[1]))
            def isclosed(self): return self.closed
            def read1(self, amount):
                self.closed = True; sock.closed = True
                return envelope(record())[1]
        connection = Mock(); connection.sock = sock; connection.getresponse.return_value = Response()
        with patch.object(cf.http.client, 'HTTPSConnection', return_value=connection) as connect:
            self.assertEqual(cf.CloudflareClient(TOKEN).get_record(ZONE, RECORD)['id'], RECORD)
        connect.assert_called_once(); connection.close.assert_called_once()

    def test_invalid_authority_deadlines_and_advertised_oversize_are_fixed_errors(self):
        client, fake = self.client()
        for deadline in (True, 'soon', float('inf'), float('nan'), 10**1000):
            self.rejected(lambda: client.assert_authoritative_owner(ZONE, 'example.test', NAME, deadline=deadline), 'DNS_INVALID_DEADLINE')
        self.assertEqual(fake.calls, [])
        response = Mock(); response.getheader.return_value = str(cf.MAX_RESPONSE + 1)
        connection = Mock(); connection.getresponse.return_value = response
        with patch.object(cf.http.client, 'HTTPSConnection', return_value=connection):
            self.rejected(lambda: cf.CloudflareClient(TOKEN).get_record(ZONE, RECORD), 'DNS_PROVIDER_RESPONSE_TOO_LARGE')
        response.read1.assert_not_called(); connection.close.assert_called_once()

    def test_default_https_stream_is_capped_without_returning_oversize_payload(self):
        class Response:
            status = 200
            def __init__(self): self.stream = io.BytesIO(b'x' * (cf.MAX_RESPONSE + 1))
            def getheader(self, _): return None
            def read1(self, amount): return self.stream.read(amount)
            def isclosed(self): return False
        connection = Mock(); connection.getresponse.return_value = Response()
        with patch.object(cf.http.client, 'HTTPSConnection', return_value=connection):
            client = cf.CloudflareClient(TOKEN)
            self.rejected(lambda: client.get_record(ZONE, RECORD), 'DNS_PROVIDER_RESPONSE_TOO_LARGE')
        connection.close.assert_called_once()


if __name__ == '__main__': unittest.main()
