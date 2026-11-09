"""Resource-bounded worker transport for private YAML download bytes."""
from __future__ import annotations

import json
from pathlib import Path
import struct
import sys

if __name__ == '__main__' and not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.services import upstream_import as bounded
from app.services.mihomo_merge import MihomoMergeError, serialize_mihomo_merge
from app.services.mihomo_preview import MihomoPreviewError, _SLOTS, _source_bytes
from app.services.upstream_profile import UpstreamValidationError


_OPTIONS_LIMIT = 32_768
_HEADER = struct.Struct('!III')
_MERGE_ERRORS = frozenset({
    'MERGE_INVALID_NAME', 'MERGE_NAME_COLLISION', 'MERGE_INVALID_CHOICES',
    'MERGE_CHOICES_REQUIRED', 'MERGE_UNUSED_CHOICES', 'MERGE_RESULT_LIMIT',
    'MERGE_INVALID_REFERENCES', 'MERGE_SERIALIZATION_REJECTED', 'MERGE_SERIALIZATION_CHANGED',
})


def download_mihomo_merge(baseline, candidate, choices):
    old = _source_bytes(baseline, 'baseline')
    new = _source_bytes(candidate, 'candidate')
    options = json.dumps(list(choices.items()), ensure_ascii=True, separators=(',', ':')).encode('ascii')
    if len(options) > _OPTIONS_LIMIT:
        raise MihomoMergeError('MERGE_INVALID_CHOICES')
    wire = _HEADER.pack(len(old), len(new), len(options)) + old + new + options
    if not _SLOTS.acquire(blocking=False):
        raise MihomoPreviewError('PARSER_BUSY')
    try:
        command = [sys.executable, '-I', '-B', str(Path(__file__).resolve()), '--worker']
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


def _worker():
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
        result = b'Y' + serialize_mihomo_merge(old, new, choices=choices)
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
    if sys.argv[1:] != ['--worker']:
        raise SystemExit(64)
    _worker()
