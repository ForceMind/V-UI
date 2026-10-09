"""Do not confuse ordinary throttling or generator latency with overload."""
import copy
import unittest

from scripts import low_resource_cpu_pressure as pressure


def samples():
    return [dict(slot=index * 5, monotonic=100 + index * 5,
        verified_bytes=index * len(pressure.BODY), cpu=dict(
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


if __name__ == '__main__':
    unittest.main()
