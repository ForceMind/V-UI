"""Bounded Cloudflare DNS-01 API adapter; never a DNS journal or secret store.

Transport injection is for isolated tests: (method, fixed_url, *, headers, body,
timeout, max_bytes) -> (status, raw_bytes). It must not log credentials.
The journal must call assert_authoritative_owner before TXT writes and verify
full ownership before deleting an ID. The remote GET->DELETE gap is not atomic.
"""
from __future__ import annotations
import base64
import binascii
import http.client
import ipaddress
import json
import math
import re
import ssl
import time
from urllib.parse import urlencode, urlsplit

from app.certificates.errors import CertificateError

BASE = 'https://api.cloudflare.com/client/v4'
TIMEOUT = 10
MAX_RESPONSE = 256 * 1024
MAX_RECORDS = 100
PER_PAGE = 50
MAX_PAGES = 2
MAX_AUTHORITY_BUDGET = 900
TRANSPORT_CODES = frozenset({'DNS_PROVIDER_TIMEOUT', 'DNS_PROVIDER_RESPONSE_TOO_LARGE',
                             'DNS_PROVIDER_INVALID_RESPONSE', 'DNS_PROVIDER_TLS_FAILED'})


def _fail(code):
    raise CertificateError(code)


def _need(condition, code='DNS_PROVIDER_INVALID_RESPONSE'):
    if not condition:
        _fail(code)


def _identifier(value):
    _need(type(value) is str and len(value) == 32 and re.fullmatch(r'[a-f0-9]{32}', value), 'DNS_INVALID_IDENTIFIER')
    return value


def _domain(value):
    _need(type(value) is str and 1 <= len(value) <= 253 and value.isascii(), 'DNS_INVALID_NAME')
    labels = value.split('.')
    _need(2 <= len(labels) <= 128 and all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label)
                                           for label in labels), 'DNS_INVALID_NAME')
    is_ip = True
    try:
        ipaddress.ip_address(value)
    except ValueError:
        is_ip = False
    _need(not is_ip, 'DNS_INVALID_NAME')
    return value


def _owner(value):
    _need(type(value) is str and value.startswith('_acme-challenge.') and len(value) <= 253,
          'DNS_INVALID_OWNER')
    _domain(value[len('_acme-challenge.'):])
    _need(len(value.split('.')) <= 128, 'DNS_INVALID_OWNER')
    return value


def _value(value):
    valid = type(value) is str and len(value) == 43 and re.fullmatch(r'[A-Za-z0-9_-]{43}', value)
    if valid:
        try:
            decoded = base64.b64decode(value + '=', altchars=b'-_', validate=True)
            valid = len(decoded) == 32 and base64.urlsafe_b64encode(decoded).decode('ascii').rstrip('=') == value
        except (ValueError, binascii.Error):
            valid = False
    _need(valid, 'DNS_INVALID_TXT_VALUE')
    return value


def parse_txt_content(content):
    """Accept one raw ACME digest or exactly one quoted string, never an RRset."""
    _need(type(content) is str and len(content) <= 45, 'DNS_INVALID_TXT_VALUE')
    if content.startswith('"') and content.endswith('"'):
        content = content[1:-1]
    return _value(content)


def parse_ownership(comment):
    """Return the validated complete comment; the journal stores this same value."""
    _need(type(comment) is str and len(comment) == 69 and re.fullmatch(r'v-ui:[a-f0-9]{64}', comment), 'DNS_INVALID_OWNERSHIP')
    return comment


def _pairs(values):
    result = {}
    for key, value in values:
        _need(key not in result)
        result[key] = value
    return result


def _constant(_):
    _fail('DNS_PROVIDER_INVALID_RESPONSE')


def _integer(value):
    _need(len(value.lstrip('-')) <= 18)
    return int(value)


def _float(value):
    number = float(value)
    _need(math.isfinite(number))
    return number


def _unicode(value):
    if type(value) is str:
        value.encode('utf-8')
    elif type(value) is dict:
        for key, child in value.items():
            _unicode(key); _unicode(child)
    elif type(value) is list:
        for child in value:
            _unicode(child)


def _json(raw):
    invalid = False
    try:
        result = json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs, parse_constant=_constant,
                            parse_int=_integer, parse_float=_float)
        _unicode(result)
    except (ValueError, UnicodeError, RecursionError):
        invalid = True
    # Raise outside the decoder handler so a JSON error cannot retain raw body
    # text as the chained exception exposed by our public fixed-code error.
    if invalid:
        _fail('DNS_PROVIDER_INVALID_RESPONSE')
    _need(type(result) is dict)
    return result


def _finite_deadline(value):
    if type(value) not in (int, float):
        return False
    finite = False
    try:
        finite = math.isfinite(value)
    except OverflowError:
        pass
    return finite


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    _need(remaining > 0, 'DNS_PROVIDER_TIMEOUT')
    return min(TIMEOUT, remaining)


def _https(method, url, *, headers, body, timeout, max_bytes):
    """Direct stdlib HTTPS: no environment proxy and no redirect handler."""
    target = urlsplit(url)
    _need(target.scheme == 'https' and target.netloc == 'api.cloudflare.com'
          and target.path.startswith('/client/v4/') and not target.fragment)
    _need(method in ('GET', 'POST', 'DELETE'))
    context = ssl.create_default_context()
    _need(context.check_hostname is True and context.verify_mode == ssl.CERT_REQUIRED, 'DNS_PROVIDER_TLS_FAILED')
    deadline = time.monotonic() + timeout
    connection = http.client.HTTPSConnection('api.cloudflare.com', port=443, timeout=timeout, context=context)
    try:
        connection.connect()
        sock = connection.sock
        sock.settimeout(_remaining(deadline))
        connection.request(method, target.path + ('?' + target.query if target.query else ''), body=body, headers=headers)
        sock.settimeout(_remaining(deadline))
        response = connection.getresponse()
        length = response.getheader('Content-Length')
        if length is not None:
            _need(type(length) is str and re.fullmatch(r'[0-9]{1,10}', length))
            _need(int(length) <= max_bytes, 'DNS_PROVIDER_RESPONSE_TOO_LARGE')
        chunks, total = [], 0
        while not response.isclosed():
            sock.settimeout(_remaining(deadline))
            chunk = response.read1(min(16384, max_bytes + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            _need(total <= max_bytes, 'DNS_PROVIDER_RESPONSE_TOO_LARGE')
            chunks.append(chunk)
        _remaining(deadline)
        return response.status, b''.join(chunks)
    finally:
        connection.close()


def _record(raw, zone_id, *, record_id=None):
    _need(type(raw) is dict)
    identity = _identifier(raw.get('id'))
    _need(record_id is None or identity == record_id, 'DNS_RECORD_IDENTITY_MISMATCH')
    _need(raw.get('type') == 'TXT', 'DNS_RECORD_IDENTITY_MISMATCH')
    name = _owner(raw.get('name'))
    content = parse_txt_content(raw.get('content'))
    comment = raw.get('comment')
    _need(comment is None or type(comment) is str and len(comment) <= 500)
    _need('zone_id' not in raw or raw['zone_id'] == zone_id, 'DNS_RECORD_IDENTITY_MISMATCH')
    if 'zone_name' in raw:
        zone = _domain(raw['zone_name']).split('.')
        _need(name.split('.')[-len(zone):] == zone, 'DNS_RECORD_IDENTITY_MISMATCH')
    return {'id': identity, 'zone_id': zone_id, 'type': 'TXT', 'name': name,
            'content': content, 'comment': comment}


def verify_ownership(record, *, zone_id, name, value, ownership, record_id=None):
    """Exact journal identity check; mismatch is an error, never an absent record."""
    zone_id = _identifier(zone_id); name = _owner(name); value = _value(value); parse_ownership(ownership)
    if record_id is not None:
        _identifier(record_id)
    result = _record(record, zone_id, record_id=record_id)
    _need(result['name'] == name and result['content'] == value and result['comment'] == ownership,
          'DNS_RECORD_OWNERSHIP_MISMATCH')
    return result


def _page(envelope, *, page, per_page, maximum):
    rows, info = envelope.get('result'), envelope.get('result_info')
    _need(type(rows) is list and type(info) is dict and len(rows) <= per_page)
    keys = ('page', 'per_page', 'count', 'total_count', 'total_pages')
    _need(all(type(info.get(key)) is int for key in keys))
    _need(info['page'] == page and info['per_page'] == per_page and info['count'] == len(rows))
    count, pages = info['total_count'], info['total_pages']
    _need(0 <= count <= maximum, 'DNS_PROVIDER_RESULT_LIMIT')
    expected_pages = (count + per_page - 1) // per_page
    _need(pages == expected_pages or count == 0 and pages in (0, 1))
    _need(page >= 1 and (count == 0 and page == 1 or page <= pages))
    _need(len(rows) == min(per_page, max(0, count - (page - 1) * per_page)))
    return rows, count, pages


class CloudflareClient:
    def __init__(self, token, *, transport=None):
        _need(type(token) is str and 1 <= len(token) <= 2048
              and re.fullmatch(r'[A-Za-z0-9._~+/-]+={0,2}', token), 'DNS_INVALID_TOKEN')
        _need(transport is None or callable(transport), 'DNS_INVALID_TRANSPORT')
        self._token = token
        self._transport = _https if transport is None else transport

    def __repr__(self):
        return 'CloudflareClient(<redacted>)'

    def _request(self, method, path, *, query=None, payload=None, absent=False, deadline=None):
        _need(deadline is None or _finite_deadline(deadline), 'DNS_INVALID_DEADLINE')
        url = BASE + path + ('?' + urlencode(query) if query else '')
        body = None if payload is None else json.dumps(payload, separators=(',', ':'), allow_nan=False).encode('utf-8')
        headers = {'Authorization': 'Bearer ' + self._token, 'Accept': 'application/json', 'User-Agent': 'V-UI-DNS01/1'}
        if body is not None:
            headers['Content-Type'] = 'application/json'
        for attempt in range(2 if method == 'GET' else 1):
            timeout = TIMEOUT if deadline is None else _remaining(deadline)
            started = time.monotonic()
            failure, retry = None, False
            try:
                response = self._transport(method, url, headers=headers, body=body, timeout=timeout, max_bytes=MAX_RESPONSE)
            except Exception as error:
                if type(error) is CertificateError and type(error.code) is str and error.code in TRANSPORT_CODES:
                    failure = error.code
                elif isinstance(error, ssl.SSLError):
                    failure = 'DNS_PROVIDER_TLS_FAILED'
                elif isinstance(error, TimeoutError):
                    failure, retry = 'DNS_PROVIDER_TIMEOUT', True
                elif isinstance(error, (ConnectionError, OSError)):
                    failure, retry = 'DNS_PROVIDER_UNAVAILABLE', True
                else:
                    failure = 'DNS_PROVIDER_UNAVAILABLE'
            if failure is not None:
                if method != 'GET':
                    failure = 'DNS_PROVIDER_WRITE_UNKNOWN'
                if retry and attempt == 0 and method == 'GET':
                    continue
                _fail(failure)
            if deadline is not None and time.monotonic() > deadline:
                _fail('DNS_PROVIDER_TIMEOUT' if method == 'GET' else 'DNS_PROVIDER_WRITE_UNKNOWN')
            if time.monotonic() - started > timeout:
                if method == 'GET' and attempt == 0:
                    continue
                _fail('DNS_PROVIDER_TIMEOUT' if method == 'GET' else 'DNS_PROVIDER_WRITE_UNKNOWN')
            response_error = 'DNS_PROVIDER_INVALID_RESPONSE' if method == 'GET' else 'DNS_PROVIDER_WRITE_UNKNOWN'
            _need(type(response) is tuple and len(response) == 2, response_error)
            status, raw = response
            _need(type(status) is int and 100 <= status <= 599 and type(raw) is bytes, response_error)
            _need(len(raw) <= MAX_RESPONSE, 'DNS_PROVIDER_RESPONSE_TOO_LARGE' if method == 'GET' else 'DNS_PROVIDER_WRITE_UNKNOWN')
            if 300 <= status < 400:
                _fail('DNS_PROVIDER_REDIRECT_REFUSED' if method == 'GET' else 'DNS_PROVIDER_WRITE_UNKNOWN')
            if status in (401, 403):
                _fail('DNS_PROVIDER_FORBIDDEN')
            if status == 429:
                _fail('DNS_PROVIDER_RATE_LIMITED')
            if status == 404 and absent:
                return None
            if status >= 500:
                if method == 'GET' and attempt == 0:
                    continue
                _fail('DNS_PROVIDER_UNAVAILABLE' if method == 'GET' else 'DNS_PROVIDER_WRITE_UNKNOWN')
            if 400 <= status < 500:
                _fail('DNS_PROVIDER_REJECTED')
            _need(status == 200, 'DNS_PROVIDER_REJECTED' if method == 'GET' else 'DNS_PROVIDER_WRITE_UNKNOWN')
            malformed = False
            try:
                envelope = _json(raw)
            except CertificateError:
                if method == 'GET':
                    raise
                malformed = True
            if malformed:
                _fail('DNS_PROVIDER_WRITE_UNKNOWN')
            _need(envelope.get('success') is True and envelope.get('errors') == [],
                  'DNS_PROVIDER_REJECTED' if method == 'GET' else 'DNS_PROVIDER_WRITE_UNKNOWN')
            return envelope
        _fail('DNS_PROVIDER_UNAVAILABLE')

    def discover_zone(self, zone_name, *, deadline=None):
        zone_name = _domain(zone_name)
        result = self._request('GET', '/zones', query={'name': zone_name, 'page': 1, 'per_page': 2}, deadline=deadline)
        rows, count, _ = _page(result, page=1, per_page=2, maximum=10000)
        _need(count != 0, 'DNS_ZONE_NOT_FOUND')
        _need(count == 1 and len(rows) == 1, 'DNS_ZONE_NOT_UNIQUE')
        row = rows[0]
        _need(type(row) is dict and row.get('name') == zone_name, 'DNS_ZONE_IDENTITY_MISMATCH')
        return {'zone_id': _identifier(row.get('id')), 'zone_name': zone_name}

    def get_record(self, zone_id, record_id, *, deadline=None):
        zone_id, record_id = _identifier(zone_id), _identifier(record_id)
        result = self._request('GET', f'/zones/{zone_id}/dns_records/{record_id}', absent=True, deadline=deadline)
        return None if result is None else _record(result.get('result'), zone_id, record_id=record_id)

    def create_txt(self, zone_id, name, value, ownership, *, deadline=None):
        """Single write, no retry. Caller must first assert authoritative owner."""
        zone_id, name, value = _identifier(zone_id), _owner(name), _value(value)
        ownership = parse_ownership(ownership)
        result = self._request('POST', f'/zones/{zone_id}/dns_records',
                               payload={'type': 'TXT', 'name': name, 'content': value, 'comment': ownership}, deadline=deadline)
        uncertain = False
        try:
            record = verify_ownership(result.get('result'), zone_id=zone_id, name=name, value=value, ownership=ownership)
        except CertificateError:
            uncertain = True
        if uncertain:
            _fail('DNS_PROVIDER_WRITE_UNKNOWN')
        return record

    def find_txt(self, zone_id, name, ownership, *, deadline=None):
        zone_id, name, ownership = _identifier(zone_id), _owner(name), parse_ownership(ownership)
        records, seen, totals = [], set(), None
        for page in range(1, MAX_PAGES + 1):
            result = self._request('GET', f'/zones/{zone_id}/dns_records', query={
                'match': 'all', 'type': 'TXT', 'name.exact': name, 'comment.exact': ownership,
                'page': page, 'per_page': PER_PAGE}, deadline=deadline)
            rows, count, pages = _page(result, page=page, per_page=PER_PAGE, maximum=MAX_RECORDS)
            _need(totals is None or totals == (count, pages), 'DNS_PROVIDER_PAGINATION_CHANGED')
            totals = count, pages
            for raw in rows:
                record = _record(raw, zone_id)
                _need(record['name'] == name and record['comment'] == ownership, 'DNS_RECORD_OWNERSHIP_MISMATCH')
                _need(record['id'] not in seen, 'DNS_PROVIDER_DUPLICATE_RECORD')
                seen.add(record['id']); records.append(record)
            if page >= pages:
                return records
        _fail('DNS_PROVIDER_RESULT_LIMIT')

    def delete_txt(self, zone_id, record_id, *, deadline=None):
        """Caller verifies ownership before this single, non-atomic DELETE."""
        zone_id, record_id = _identifier(zone_id), _identifier(record_id)
        result = self._request('DELETE', f'/zones/{zone_id}/dns_records/{record_id}', absent=True, deadline=deadline)
        if result is not None:
            _need(type(result.get('result')) is dict and result['result'].get('id') == record_id,
                  'DNS_PROVIDER_WRITE_UNKNOWN')

    def assert_authoritative_owner(self, zone_id, zone_name, owner, *, deadline=None):
        zone_id, zone_name, owner = _identifier(zone_id), _domain(zone_name), _owner(owner)
        labels, zone = owner.split('.'), zone_name.split('.')
        _need(len(labels) > len(zone) and labels[-len(zone):] == zone, 'DNS_OWNER_OUTSIDE_ZONE')
        now = time.monotonic()
        _need(deadline is None or _finite_deadline(deadline), 'DNS_INVALID_DEADLINE')
        deadline = now + MAX_AUTHORITY_BUDGET if deadline is None else min(deadline, now + MAX_AUTHORITY_BUDGET)
        checks = [('CNAME', owner)] + [('NS', '.'.join(labels[index:])) for index in range(len(labels) - len(zone))]
        for kind, name in checks:
            result = self._request('GET', f'/zones/{zone_id}/dns_records', query={
                'match': 'all', 'type': kind, 'name.exact': name, 'page': 1, 'per_page': 2}, deadline=deadline)
            rows, count, _ = _page(result, page=1, per_page=2, maximum=10000)
            for row in rows:
                _need(type(row) is dict and row.get('type') == kind and row.get('name') == name
                      and type(row.get('content')) is str and len(row['content']) <= 4096)
                _identifier(row.get('id'))
                _need('zone_id' not in row or row['zone_id'] == zone_id, 'DNS_RECORD_IDENTITY_MISMATCH')
            _need(count == 0, 'DNS_DELEGATION_UNSUPPORTED')
