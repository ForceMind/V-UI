"""Resource-bounded worker transport for private YAML download bytes."""
from __future__ import annotations

import json
import re
from pathlib import Path
import struct
import sys

if __name__ == '__main__' and not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.services import upstream_import as bounded
from app.services.mihomo_merge import MihomoMergeError, serialize_mihomo_merge
from app.services.mihomo_merge_review import review_mihomo_merge, serialize_reviewed_mihomo_merge
from app.services.mihomo_preview import MihomoPreviewError, _SLOTS, _source_bytes
from app.services.upstream_profile import UpstreamValidationError


_OPTIONS_LIMIT = 32_768
_HEADER = struct.Struct('!III')
_MERGE_ERRORS = frozenset({
    'MERGE_INVALID_NAME', 'MERGE_NAME_COLLISION', 'MERGE_INVALID_CHOICES',
    'MERGE_CHOICES_REQUIRED', 'MERGE_UNUSED_CHOICES', 'MERGE_RESULT_LIMIT',
    'MERGE_INVALID_REFERENCES', 'MERGE_SERIALIZATION_REJECTED', 'MERGE_SERIALIZATION_CHANGED',
    'MERGE_REVIEW_LIMIT', 'MERGE_REVIEW_INVALID', 'MERGE_REVIEW_STALE',
})


def download_mihomo_merge(baseline, candidate, choices):
    return _request(baseline, candidate, list(choices.items()), '--worker')


def reviewed_mihomo_download(baseline, candidate, fingerprint, selections):
    return _request(baseline, candidate, {'fingerprint': fingerprint, 'selections': selections}, '--reviewed')


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
    if len(options) > _OPTIONS_LIMIT:
        raise MihomoMergeError('MERGE_INVALID_CHOICES')
    wire = _HEADER.pack(len(old), len(new), len(options)) + old + new + options
    if not _SLOTS.acquire(blocking=False):
        raise MihomoPreviewError('PARSER_BUSY')
    try:
        command = [sys.executable, '-I', '-B', str(Path(__file__).resolve()), action]
        try:
            response = bounded._bounded_worker_io(command, wire, output_limit=bounded.MAX_INPUT_BYTES + 1)
        except UpstreamValidationError as error:
            raise MihomoPreviewError(error.code) from None
        if response[:1] == b'Y' and 1 < len(response) <= bounded.MAX_INPUT_BYTES + 1:
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
                and 0 < options_size <= _OPTIONS_LIMIT):
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
    if len(sys.argv) != 2 or sys.argv[1] not in ('--worker', '--review', '--reviewed'):
        raise SystemExit(64)
    _worker(sys.argv[1])
