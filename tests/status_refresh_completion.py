"""Finite status-refresh evidence checks, with no browser or network dependency.

Request identities belong to the Python ledger. ResourceTiming has no request ID:
its correspondence is an exact-count, exact-URL set check, NOT an ordinal join.
All interval comparisons use one document's native values without rounding.
"""
import math


OBSERVATION_SECONDS = 4


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def positive(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def validate_reads(reads, base, paths, complete=False):
    fields = {'identity', 'path', 'url', 'method', 'main_frame', 'resource_type',
              'redirected_from', 'redirected_to', 'service_worker', 'from_service_worker',
              'status', 'failure', 'finished', 'body_size', 'held'}
    require(type(reads) is list and all(type(read) is dict and fields <= read.keys() for read in reads),
            'malformed Request ledger')
    identities = [read['identity'] for read in reads]
    require(len(set(identities)) == len(identities), 'duplicate Request identity')
    for read in reads:
        require(read['path'] in paths and read['url'] == base + read['path'], 'exact status GET URL required')
        require(read['method'] == 'GET' and read['main_frame'] is True and
                read['resource_type'] == 'xhr', 'main-frame GET xhr required')
        require(read['service_worker'] is False and read['from_service_worker'] is False,
                'service-worker status response')
        require(read['redirected_from'] is False and read['redirected_to'] is False, 'redirected status read')
        require(read['failure'] is None, 'failed status read: ' + str(read['failure']))
        require(read['status'] in (None, 200), 'status response must be 200')
        if complete:
            require(read['finished'] is True and read['status'] == 200, 'unfinished status response')
            require(type(read['body_size']) is int and read['body_size'] > 0, 'complete response body required')
    for path in paths:
        require(sum(read['path'] == path and read['held'] for read in reads) == 2,
                'both genuine held replies must remain in the cohort: ' + path)


def validate_intervals(snapshot, base, paths):
    require(type(snapshot) is dict, 'malformed native snapshot')
    require(snapshot.get('document_url') == base + '/ui/', 'wrong ResourceTiming document')
    require(positive(snapshot.get('time_origin')) and positive(snapshot.get('observed_at')),
            'missing document timing base')
    entries = snapshot.get('entries')
    require(type(entries) is list, 'missing native resource entries')
    intervals = {path: [] for path in paths}
    seen = set()
    for entry in entries:
        require(type(entry) is dict, 'malformed native entry')
        path = next((path for path in paths if entry.get('name') == base + path), None)
        require(path is not None, 'wrong native status URL')
        require(entry.get('entryType') == 'resource' and entry.get('initiatorType') == 'xmlhttprequest',
                'wrong native resource type')
        start, headers, end = (entry.get(field) for field in ('startTime', 'responseStart', 'responseEnd'))
        require(all(positive(value) for value in (start, headers, end)), 'missing/nonfinite/incomplete native interval')
        require(start <= headers <= end and start < end <= snapshot['observed_at'], 'malformed native interval')
        identity = (entry['name'], start, headers, end)
        require(identity not in seen, 'duplicate native interval')
        seen.add(identity)
        intervals[path].append((start, headers, end))
    for path, values in intervals.items():
        values.sort()
        for previous, following in zip(values, values[1:]):
            require(previous[2] <= following[0], 'overlapping full-response intervals: ' + path)
    return intervals


def qualify_completion(reads, snapshot, base, paths):
    validate_reads(reads, base, paths, complete=True)
    intervals = validate_intervals(snapshot, base, paths)
    counts = {path: sum(read['path'] == path for read in reads) for path in paths}
    require(all(len(intervals[path]) == counts[path] for path in paths),
            'native interval count must exactly equal the entire Request cohort')
    return {'correspondence': 'exact URL/count sets; no ResourceTiming request IDs or ordinal join',
            'document_url': snapshot['document_url'], 'time_origin': snapshot['time_origin'],
            'observed_at': snapshot['observed_at'], 'cohort_counts': counts,
            'intervals_start_headers_end': intervals, 'request_ledger': reads}


def collect_completion(read_cohort, read_snapshot, pump, clock, base, paths):
    """One deadline; await completion, resample ONLY for newly observed requests.

No metadata, count, or interval assertion is caught/retried. Both sides of the
native snapshot must have the same complete Request cohort. Later natural reads
extend that cohort, never a first-two slice or a count-at-least shortcut.
"""
    deadline = clock() + OBSERVATION_SECONDS
    previous, document_clock = (), None
    while True:
        require(clock() < deadline, 'status completion observation deadline exceeded')
        reads = read_cohort()
        identities = tuple(read['identity'] for read in reads)
        require(identities[:len(previous)] == previous, 'Request cohort lost or reordered identities')
        previous = identities
        validate_reads(reads, base, paths)
        if not all(read['finished'] for read in reads):
            remaining = (deadline - clock()) * 1000
            require(remaining > 0, 'status completion observation deadline exceeded')
            pump(min(10, remaining))
            continue
        snapshot = read_snapshot()
        # Even if a natural request arrived during collection, malformed or
        # overlapping completed native entries fail immediately, not on retry.
        validate_intervals(snapshot, base, paths)
        if document_clock is not None:
            require(snapshot['time_origin'] == document_clock[0] and
                    snapshot['observed_at'] >= document_clock[1], 'ResourceTiming document clock changed')
        document_clock = (snapshot['time_origin'], snapshot['observed_at'])
        latest = read_cohort()
        validate_reads(latest, base, paths)
        current = tuple(read['identity'] for read in latest)
        require(current[:len(identities)] == identities, 'Request cohort lost or reordered identities')
        require(clock() < deadline, 'status completion observation deadline exceeded')
        if current != identities:
            previous = current
            continue
        return qualify_completion(latest, snapshot, base, paths)
