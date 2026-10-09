"""Bounded loopback CPU-quota pressure and recovery qualification.

This is a test-only workload. It is not a throughput promise or a memory /
connection-exhaustion test. Missing sustained pressure is an explicit failure
to demonstrate this scenario, never silently accepted as recovery coverage.
"""
from __future__ import annotations

import math

CONCURRENCIES = (8, 32, 64)
PHASE_SECONDS = 20
SAMPLE_SECONDS = 5
BODY = bytes(range(256)) * 4096
MAX_ATTEMPTS = 65536
MAX_BODY_BYTES = MAX_ATTEMPTS * len(BODY)
RECOVERY_SECONDS = 15
RECOVERY_SLOTS = (0, 3, 6, 9, 12)
CPU_KEYS = ('usage_usec', 'nr_periods', 'nr_throttled', 'throttled_usec')


def pressure_windows(samples):
    """Return a conservative fixture-specific trigger and raw window deltas.

    nr_throttled alone is insufficient. Require >=95% one-core consumption,
    >=80% throttled periods and actual successful proxy payloads for three
    adjacent 5-second windows. No endpoint-only latency trigger is permitted.
    """
    if len(samples) != PHASE_SECONDS // SAMPLE_SECONDS + 1:
        raise RuntimeError('Missing CPU pressure samples')
    windows = []
    streak = 0
    demonstrated = False
    for index, row in enumerate(samples):
        stamp = row.get('monotonic')
        if type(stamp) not in (int, float) or not math.isfinite(stamp):
            raise RuntimeError('Invalid CPU pressure sample time')
        if row.get('slot') != index * SAMPLE_SECONDS:
            raise RuntimeError('Missing or reordered pressure sample slot')
        for key in CPU_KEYS:
            if type(row.get('cpu', {}).get(key)) is not int or row['cpu'][key] < 0:
                raise RuntimeError('Missing CPU quota counters')
        if type(row.get('verified_bytes')) is not int or row['verified_bytes'] < 0:
            raise RuntimeError('Invalid successful proxy byte counter')
        if not index:
            continue
        previous = samples[index - 1]
        wall = stamp - previous['monotonic']
        if not 4.75 <= wall <= 5.25:
            raise RuntimeError('Pressure sampling window drifted')
        delta = {key: row['cpu'][key] - previous['cpu'][key] for key in CPU_KEYS}
        completed = row['verified_bytes'] - previous['verified_bytes']
        if min(*delta.values(), completed) < 0 or delta['nr_throttled'] > delta['nr_periods']:
            raise RuntimeError('CPU pressure counters decreased or are inconsistent')
        utilization = delta['usage_usec'] / (wall * 1_000_000)
        ratio = delta['nr_throttled'] / delta['nr_periods'] if delta['nr_periods'] else 0
        qualifies = utilization >= .95 and ratio >= .8 and delta['throttled_usec'] > 0 and completed > 0
        streak = streak + 1 if qualifies else 0
        demonstrated |= streak >= 3
        windows.append(dict(wall_seconds=wall, cpu_delta=delta,
            verified_bytes=completed, utilization_one_core=utilization,
            throttled_period_ratio=ratio, qualifies=qualifies))
    return dict(demonstrated=demonstrated, windows=windows)
