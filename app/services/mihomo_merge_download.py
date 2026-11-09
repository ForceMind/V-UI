"""Resource-bounded worker transport for private YAML download bytes."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
import struct
import sys

if __name__ == '__main__' and not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.services import upstream_import as bounded
from app.services.mihomo_merge import MihomoMergeError, serialize_mihomo_merge
from app.services.mihomo_merge_plan import plan_mihomo
from app.services.mihomo_plan_record import MAX_RECORD_BYTES, PlanRecordError, decode_plan_record, encode_plan_record
from app.services.mihomo_merge_review import review_mihomo_merge, serialize_reviewed_mihomo_merge
from app.services.mihomo_preview import MihomoPreviewError, _SLOTS, _source_bytes
from app.services.upstream_profile import UpstreamValidationError


_OPTIONS_LIMIT = 32_768
_PREPARE_OPTIONS_LIMIT = 262_144
_PREPARE_OUTPUT_LIMIT = MAX_RECORD_BYTES + bounded.MAX_INPUT_BYTES + 64
_HEADER = struct.Struct('!III')
_MERGE_ERRORS = frozenset({
    'MERGE_INVALID_NAME', 'MERGE_NAME_COLLISION', 'MERGE_INVALID_CHOICES',
    'MERGE_CHOICES_REQUIRED', 'MERGE_UNUSED_CHOICES', 'MERGE_RESULT_LIMIT',
    'MERGE_INVALID_REFERENCES', 'MERGE_SERIALIZATION_REJECTED', 'MERGE_SERIALIZATION_CHANGED',
    'MERGE_REVIEW_LIMIT', 'MERGE_REVIEW_INVALID', 'MERGE_REVIEW_STALE',
    'PLAN_RULE_MODE_REQUIRED', 'PLAN_UNSUPPORTED_SECTION', 'PLAN_UNSUPPORTED_NODE',
    'PLAN_UNSUPPORTED_GROUP', 'PLAN_INVALID_GROUP_SELECTION', 'PLAN_UNSUPPORTED_RULE',
    'PLAN_GROUP_SELECTION_REQUIRED', 'PLAN_INVALID_TARGET', 'PLAN_UNUSED_GROUP_SELECTION',
    'PLAN_FINAL_REQUIRED', 'PLAN_RECORD_INVALID',
})


def download_mihomo_merge(baseline, candidate, choices):
    return _request(baseline, candidate, list(choices.items()), '--worker')


def reviewed_mihomo_download(baseline, candidate, fingerprint, selections):
    return _request(baseline, candidate, {'fingerprint': fingerprint, 'selections': selections}, '--reviewed')


def prepare_mihomo_draft(baseline, candidate, fingerprint, selections, group_members, dns_upstream):
    options = {'fingerprint': fingerprint, 'selections': selections,
               'group_members': group_members, 'dns_upstream': dns_upstream}
    response = _request(baseline, candidate, options, '--prepare')
    try:
        header, body = response.split(b'\n', 1)
        if not re.fullmatch(rb'[1-9][0-9]{0,6} [1-9][0-9]{0,6}', header):
            raise ValueError()
        record_size, merged_size = map(int, header.split())
        if (record_size > MAX_RECORD_BYTES or merged_size > bounded.MAX_INPUT_BYTES
                or len(body) != record_size + merged_size):
            raise ValueError()
        raw = body[:record_size]
        decode_plan_record(raw)
        merged = body[record_size:]
        sources = {'baseline': _source_bytes(baseline, 'baseline'),
                   'candidate': _source_bytes(candidate, 'candidate'), 'merged': merged}
        expected = decode_plan_record(raw).value['sources']
        if any(hashlib.sha256(value).hexdigest() != expected[role] for role, value in sources.items()):
            raise ValueError()
        return raw, sources
    except (ValueError, UnicodeError, RecursionError, MemoryError, PlanRecordError):
        raise MihomoPreviewError('PARSER_FAILED') from None


def review_mihomo_download(baseline, candidate):
    response = _request(baseline, candidate, [], '--review')
    try:
        report = json.loads(response)
        if (type(report) is not dict or set(report) != {'fingerprint', 'conflicts'}
                or type(report['fingerprint']) is not str
                or not re.fullmatch('[a-f0-9]{64}', report['fingerprint'])
                or type(report['conflicts']) is not list or len(report['conflicts']) > 256):
            raise ValueError()
        line_limits = {
            'baseline': len(_source_bytes(baseline, 'baseline').decode('utf-8').splitlines()) + 1,
            'candidate': len(_source_bytes(candidate, 'candidate').decode('utf-8').splitlines()) + 1,
        }
        for index, row in enumerate(report['conflicts']):
            if (type(row) is not dict or set(row) != {'index', 'section', 'baseline', 'candidate'}
                    or type(row['index']) is not int or row['index'] != index
                    or type(row['section']) is not str
                    or row['section'] not in ('proxies', 'proxy-groups', 'rules', 'mode', 'other')):
                raise ValueError()
            for source in ('baseline', 'candidate'):
                position = row[source]
                if (type(position) is not list or len(position) != 2
                        or any(type(n) is not int or n < 1 for n in position)
                        or position[0] > line_limits[source] or position[1] > bounded.MAX_INPUT_BYTES):
                    raise ValueError()
        return report
    except (ValueError, UnicodeError, RecursionError, MemoryError):
        raise MihomoPreviewError('PARSER_FAILED') from None


def _request(baseline, candidate, options_value, action):
    old = _source_bytes(baseline, 'baseline')
    new = _source_bytes(candidate, 'candidate')
    options = json.dumps(options_value, ensure_ascii=True, separators=(',', ':')).encode('ascii')
    options_limit = _PREPARE_OPTIONS_LIMIT if action == '--prepare' else _OPTIONS_LIMIT
    output_limit = _PREPARE_OUTPUT_LIMIT if action == '--prepare' else bounded.MAX_INPUT_BYTES + 1
    if len(options) > options_limit:
        raise MihomoMergeError('MERGE_INVALID_CHOICES')
    wire = _HEADER.pack(len(old), len(new), len(options)) + old + new + options
    if not _SLOTS.acquire(blocking=False):
        raise MihomoPreviewError('PARSER_BUSY')
    try:
        command = [sys.executable, '-I', '-B', str(Path(__file__).resolve()), action]
        try:
            response = bounded._bounded_worker_io(command, wire, output_limit=output_limit)
        except UpstreamValidationError as error:
            raise MihomoPreviewError(error.code) from None
        if response[:1] == b'Y' and 1 < len(response) <= output_limit:
            try:
                response[1:].decode('utf-8', errors='strict')
            except UnicodeError:
                raise MihomoPreviewError('PARSER_FAILED') from None
            return response[1:]
        if response[:1] == b'E' and len(response) <= 128:
            try:
                code, source = response[1:].decode('ascii').split(' ', 1)
            except (UnicodeError, ValueError):
                raise MihomoPreviewError('PARSER_FAILED') from None
            if code in _MERGE_ERRORS and source == 'pair':
                raise MihomoMergeError(code)
            raise MihomoPreviewError(code, source)
        raise MihomoPreviewError('PARSER_FAILED')
    finally:
        _SLOTS.release()


def _serialize_choices(old, new, rows):
    if (type(rows) is not list or len(rows) > 256
            or any(type(row) is not list or len(row) != 2
                   or type(row[0]) is not list or not 1 <= len(row[0]) <= bounded.MAX_DEPTH
                   or any(type(part) is not str for part in row[0])
                   or type(row[1]) is not str or row[1] not in ('baseline', 'candidate')
                   for row in rows)):
        raise MihomoMergeError('MERGE_INVALID_CHOICES')
    choices = {tuple(path): side for path, side in rows}
    if len(choices) != len(rows):
        raise MihomoMergeError('MERGE_INVALID_CHOICES')
    return serialize_mihomo_merge(old, new, choices=choices)


def _worker(action='--worker'):
    try:
        bounded._set_worker_limits()
        stream = sys.stdin.buffer
        header = stream.read(_HEADER.size)
        if len(header) != _HEADER.size:
            raise MihomoPreviewError('INVALID_INPUT')
        old_size, new_size, options_size = _HEADER.unpack(header)
        if not (0 < old_size <= bounded.MAX_INPUT_BYTES and 0 < new_size <= bounded.MAX_INPUT_BYTES
                and 0 < options_size <= (_PREPARE_OPTIONS_LIMIT if action == '--prepare' else _OPTIONS_LIMIT)):
            raise MihomoPreviewError('INPUT_LIMIT')
        old, new = stream.read(old_size), stream.read(new_size)
        options = stream.read(options_size + 1)
        if len(old) != old_size or len(new) != new_size or len(options) != options_size:
            raise MihomoPreviewError('INVALID_INPUT')
        rows = json.loads(options)
        if action == '--review':
            if rows != [] or type(rows) is not list:
                raise MihomoMergeError('MERGE_INVALID_CHOICES')
            raw = json.dumps(review_mihomo_merge(old, new), separators=(',', ':')).encode('ascii')
        elif action == '--prepare':
            if (type(rows) is not dict or set(rows) != {'fingerprint', 'selections', 'group_members', 'dns_upstream'}):
                raise MihomoMergeError('MERGE_INVALID_CHOICES')
            merged = serialize_reviewed_mihomo_merge(old, new, rows['fingerprint'], rows['selections'])
            plan = plan_mihomo('{}', merged, group_members=rows['group_members'])
            sources = {'baseline': old, 'candidate': new, 'merged': merged}
            try:
                record = encode_plan_record(plan, sources={role: hashlib.sha256(value).hexdigest()
                                            for role, value in sources.items()},
                                            group_members=rows['group_members'], dns_upstream=rows['dns_upstream'])
            except PlanRecordError:
                raise MihomoMergeError('PLAN_RECORD_INVALID') from None
            raw = (str(len(record)) + ' ' + str(len(merged)) + '\n').encode('ascii') + record + merged
        elif action == '--reviewed':
            if (type(rows) is not dict or set(rows) != {'fingerprint', 'selections'}):
                raise MihomoMergeError('MERGE_INVALID_CHOICES')
            raw = serialize_reviewed_mihomo_merge(old, new, rows['fingerprint'], rows['selections'])
        else:
            raw = _serialize_choices(old, new, rows)
        result = b'Y' + raw
    except MihomoPreviewError as error:
        result = f'E{error.code} {error.source}'.encode('ascii')
    except MihomoMergeError as error:
        code = error.code if error.code in _MERGE_ERRORS else 'PARSER_FAILED'
        result = f'E{code} pair'.encode('ascii')
    except UpstreamValidationError as error:
        result = f'E{MihomoPreviewError(error.code).code} pair'.encode('ascii')
    except (MemoryError, RecursionError):
        result = b'EPARSER_RESOURCE_LIMIT pair'
    except Exception:
        result = b'EPARSER_FAILED pair'
    sys.stdout.buffer.write(result)


if __name__ == '__main__':
    if len(sys.argv) != 2 or sys.argv[1] not in ('--worker', '--review', '--reviewed', '--prepare'):
        raise SystemExit(64)
    _worker(sys.argv[1])
