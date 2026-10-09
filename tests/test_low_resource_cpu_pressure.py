"""Do not confuse ordinary throttling or generator latency with overload."""
import copy
from concurrent.futures import ThreadPoolExecutor
import hashlib
import unittest
from unittest.mock import patch

from scripts import low_resource_cpu_pressure as pressure

UNIT = 'vui-low-resource-' + 'a' * 32 + '.service'
GROUP = '0::/system.slice/' + UNIT
EXTERNAL = '0::/user.slice/test.scope'
COMMIT = 'b' * 40


def samples():
    return [dict(slot=index * 5, monotonic=100 + index * 5,
        verified_bytes=index * len(pressure.BODY), clock_ticks_per_second=100,
        roles={name: dict(identity={'pid': pid}, cpu_ticks=index * ticks)
            for name, pid, ticks in [('worker', 1, 5), ('panel', 2, 10), ('watchdog', 3, 1), ('core', 4, 450)]}, cpu=dict(
            usage_usec=index * 4_800_000, nr_periods=index * 50,
            nr_throttled=index * 45, throttled_usec=index * 500_000))
        for index in range(5)]


class PressureContractTests(unittest.TestCase):
    def test_three_consecutive_full_quota_windows(self):
        self.assertTrue(pressure.pressure_windows(samples())['demonstrated'])

    def test_ordinary_throttling_is_not_pressure(self):
        rows = samples()
        for index, row in enumerate(rows):
            row['cpu']['usage_usec'] = index * 500_000
        self.assertFalse(pressure.pressure_windows(rows)['demonstrated'])

    def test_requires_every_trigger_component(self):
        for key, value in [('usage_usec', 4_700_000), ('nr_throttled', 39), ('throttled_usec', 0)]:
            rows = samples()
            for index, row in enumerate(rows):
                row['cpu'][key] = index * value
            self.assertFalse(pressure.pressure_windows(rows)['demonstrated'], key)
        rows = samples()
        for row in rows:
            row['verified_bytes'] = 0
        self.assertFalse(pressure.pressure_windows(rows)['demonstrated'])

    def test_rejects_missing_corrupt_and_shifted_accounting(self):
        good = samples()
        for index, field, value in [(1, 'monotonic', float('nan')),
                                   (1, 'monotonic', 106), (2, 'slot', 15),
                                   (2, 'verified_bytes', -1)]:
            rows = copy.deepcopy(good); rows[index][field] = value
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                pressure.pressure_windows(rows)
        with self.assertRaises(RuntimeError):
            pressure.pressure_windows(good[:-1])
        rows = samples(); rows[2]['cpu']['usage_usec'] = 1
        with self.assertRaises(RuntimeError):
            pressure.pressure_windows(rows)

    def test_nonadjacent_pressure_does_not_qualify(self):
        rows = samples()
        for index in range(2, 5):
            rows[index]['cpu']['usage_usec'] -= 1_000_000
        self.assertFalse(pressure.pressure_windows(rows)['demonstrated'])

    def test_observer_cpu_cannot_fake_proxy_pressure(self):
        for role, ticks in [('worker', 60), ('core', 300)]:
            rows = samples()
            for index, row in enumerate(rows):
                row['roles'][role]['cpu_ticks'] = index * ticks
            self.assertFalse(pressure.pressure_windows(rows)['demonstrated'])

    def test_budget_is_atomic_and_reserves_recovery_even_after_pressure_exhausts(self):
        with patch.object(pressure, 'MAX_ATTEMPTS', 128), patch.object(pressure, 'RECOVERY_RESERVE', 16):
            budget = pressure.Budget()
            with ThreadPoolExecutor(max_workers=32) as pool:
                allowed = list(pool.map(lambda _: budget.reserve(), range(1000)))
            self.assertEqual(sum(allowed), 112)
            self.assertEqual(sum(budget.reserve(recovery=True) for _ in range(100)), 16)
            self.assertEqual(budget.attempts, 128)

    def test_complete_report_rejects_wrong_source_deadlines_roles_and_counts(self):
        good = full_result()
        pressure.validate_result(good, UNIT, COMMIT)
        changes = [
            lambda d: d.update(source_commit='c'*40),
            lambda d: d.update(pressure_demonstrated=False),
            lambda d: d.update(attempts=d['attempts']+1),
            lambda d: d.update(external_cgroup=GROUP),
            lambda d: d.update(broker_cleanup_complete=False),
            lambda d: d['phases'].pop(),
            lambda d: d['phases'][0]['counts'].update(budget_exhausted=True),
            lambda d: d['phases'][0]['pressure'].update(demonstrated=False),
            lambda d: d['phases'][0]['recovery'].update(origin=123),
            lambda d: d['phases'][0]['recovery']['probes'][0].update(panel_status=401),
            lambda d: d['phases'][0]['recovery']['probes'][0].update(service_roles={}),
            lambda d: d['phases'][0]['samples'][0]['external']['client']['identity'].update(cgroup=GROUP),
            lambda d: d['phases'][0]['samples'][0]['metrics']['memory.events'].update(oom_kill=1),
            lambda d: d['target'].update(completed_writes=0),
        ]
        for change in changes:
            value = copy.deepcopy(good); change(value)
            with self.subTest(change=changes.index(change)), self.assertRaises(RuntimeError):
                pressure.validate_result(value, UNIT, COMMIT)

    def test_profile_preserves_original_stage_order_and_all_controls(self):
        from scripts.low_resource_dual_core import BASE_STAGES
        from scripts import low_resource_acceptance as gate
        from argparse import Namespace
        expected = BASE_STAGES[:12] + ('cpu_quota_pressure_and_recovery',) + BASE_STAGES[12:]
        report = dict(cpu_pressure=full_result(), stages=[dict(name=name) for name in expected])
        pressure.validate_complete(report, UNIT, COMMIT)
        report['stages'][0], report['stages'][1] = report['stages'][1], report['stages'][0]
        with self.assertRaises(RuntimeError): pressure.validate_complete(report, UNIT, COMMIT)
        self.assertEqual(gate.runtime_seconds(Namespace(duration_profile='cpu-pressure', memory_mib=512)), 900)
        for size in (320, 384):
            with self.assertRaises(RuntimeError): gate.duration_profile(Namespace(duration_profile='cpu-pressure', memory_mib=size))


def full_result():
    roles = {name: dict(pid=pid, ppid=parent, starttime_ticks=pid*10,
        process_group=1, cgroup=GROUP) for name, pid, parent in
        [('worker', 1, 99), ('panel', 2, 1), ('watchdog', 3, 1), ('core', 4, 3)]}
    value = dict(unit=UNIT, source_commit=COMMIT, outcome='passed', cleanup_complete=True,
        broker_cleanup_complete=True, no_direct=True, proxy_only=True, tls_verify=True,
        body_sha256=hashlib.sha256(pressure.BODY).hexdigest(), service_cgroup=GROUP,
        external_cgroup=EXTERNAL, roles=roles, phases=[], pressure_demonstrated=True,
        attempts=30, reserved_body_bytes=30*len(pressure.BODY),
        target=dict(entered=30, completed_writes=30, connections=30),
        limits=dict(concurrencies=list(pressure.CONCURRENCIES), phase_seconds=20,
            body_bytes=len(pressure.BODY), max_attempts=pressure.MAX_ATTEMPTS,
            max_body_bytes=pressure.MAX_BODY_BYTES, recovery_seconds=15))
    for phase_index, concurrency in enumerate(pressure.CONCURRENCIES):
        origin = 100 + phase_index*40
        points = samples()
        for point in points:
            point['monotonic'] += phase_index*40
            point['read_finished_monotonic'] = point['monotonic'] + .01
            for name in roles: point['roles'][name]['identity'] = copy.deepcopy(roles[name])
            point['metrics'] = {'cpu.stat': copy.deepcopy(point['cpu']),
                'memory.events': dict(oom=0, oom_kill=0, oom_group_kill=0)}
            point['external'] = {'client':dict(identity=dict(pid=90, cgroup=EXTERNAL))}
        probes = [dict(started_monotonic=origin+20.1+i, finished_monotonic=origin+20.2+i,
            success=True, proxy_status=200, panel_status=200, body_bytes=len(pressure.BODY),
            service_roles=copy.deepcopy(roles)) for i in range(6)]
        value['phases'].append(dict(concurrency=concurrency, origin=origin, duration_seconds=20,
            pressure_stop_monotonic=origin+20, load_stopped_monotonic=origin+20.05,
            counts=dict(attempts=4, completed=4, errors=0, budget_exhausted=False),
            samples=points, pressure=pressure.pressure_windows(points), outcome='passed',
            recovery=dict(origin=origin+20, deadline=origin+35, probes=probes,
                consecutive_success=6, completed_monotonic=origin+25.3)))
    return value


if __name__ == '__main__':
    unittest.main()
