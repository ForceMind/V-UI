"""Fixed non-Root metadata-only worker; never launches children or binaries."""
from __future__ import annotations
import json
import os
import sys
import time
from pathlib import Path

if __name__ == '__main__' and not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.core_updates import artifacts as a
from app.core_updates.github import GitHubCoreClient, _timestamp

MAX_OUTPUT = 32768
CORES = ('sing-box', 'xray')


def validate_result(raw, core):
    if core not in CORES or type(raw) is not bytes or not 0 < len(raw) <= MAX_OUTPUT:
        raise ValueError('DISCOVERY_RESULT')
    value = a._json(raw)
    if (set(value) != {'core','qualification','hints','truncated'} or value['core'] != core
            or value['qualification'] != 'unverified' or type(value['truncated']) is not bool
            or type(value['hints']) is not list or len(value['hints']) > 32):
        raise ValueError('DISCOVERY_RESULT')
    ids, tags = set(), set()
    for row in value['hints']:
        if type(row) is not dict or set(row) != {'release_id','tag','version','revision','published_at'}:
            raise ValueError('DISCOVERY_RESULT')
        parsed, version, revision = a._tag(row['tag'])
        if (not a._positive(row['release_id']) or row['release_id'] in ids or row['tag'] in tags
                or parsed != core or version != row['version'] or type(row['revision']) is not int
                or revision != row['revision']):
            raise ValueError('DISCOVERY_RESULT')
        _timestamp(row['published_at'])
        ids.add(row['release_id']);tags.add(row['tag'])
    return value


def discover_result(core, client=None):
    if type(core) is not str or core not in CORES:
        raise ValueError('DISCOVERY_CORE')
    client = GitHubCoreClient() if client is None else client
    result = client.discover(core, deadline=time.monotonic()+10)
    value = {'core':core,'qualification':'unverified','truncated':result.truncated,
             'hints':[{'release_id':row.release_id,'tag':row.tag,'version':row.version,
                       'revision':row.revision,'published_at':row.published_at} for row in result.hints]}
    raw = json.dumps(value,ensure_ascii=True,separators=(',',':'),allow_nan=False).encode()
    validate_result(raw,core)
    return raw


def main():
    if os.geteuid() == 0:
        return 2
    # Limits are installed before constructing a client or making a request.
    import resource
    resource.setrlimit(resource.RLIMIT_AS,(256*1024*1024,256*1024*1024))
    resource.setrlimit(resource.RLIMIT_CPU,(5,5))
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    resource.setrlimit(resource.RLIMIT_NOFILE,(64,64))
    resource.setrlimit(resource.RLIMIT_FSIZE,(0,0))
    if len(sys.argv) != 2 or sys.argv[1] not in CORES:
        return 2
    try:
        raw = discover_result(sys.argv[1])
    except Exception:
        # No API response, URL, signed location or exception text is returned.
        return 1
    sys.stdout.buffer.write(raw);sys.stdout.buffer.flush()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
