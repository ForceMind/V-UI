"""Synthetic oracle controls only, never native transport qualification."""
from copy import deepcopy
import unittest

from status_refresh_completion import collect_completion, qualify_completion


BASE = 'http://127.0.0.1:12345'
SYSTEM, CORE = '/api/system/status', '/api/cores/status'
PATHS = (SYSTEM, CORE)


def evidence():
    reads, entries = [], []
    for identity, (path, start) in enumerate(((SYSTEM, 1), (CORE, 1), (SYSTEM, 4), (CORE, 4)), 1):
        reads.append({'identity': identity, 'path': path, 'url': BASE + path,
                      'method': 'GET', 'main_frame': True, 'resource_type': 'xhr',
                      'redirected_from': False, 'redirected_to': False, 'service_worker': False,
                      'from_service_worker': False, 'status': 200,
                      'failure': None, 'finished': True, 'body_size': 42, 'held': True})
        entries.append({'name': BASE + path, 'entryType': 'resource',
                        'initiatorType': 'xmlhttprequest', 'startTime': start,
                        'responseStart': start + 1, 'responseEnd': start + 2})
    return reads, {'document_url': BASE + '/ui/', 'time_origin': 1000,
                   'observed_at': 100, 'entries': entries}


def natural(reads, snapshot, finished=True):
    reads.append({**reads[0], 'identity': len(reads) + 1, 'held': False,
                  'finished': finished, 'body_size': 42 if finished else None})
    if finished:
        snapshot['entries'].append({**snapshot['entries'][0], 'startTime': 7,
                                    'responseStart': 8, 'responseEnd': 9})


class Collection:
    def __init__(self):
        self.reads, self.snapshot = evidence()
        self.now, self.samples, self.pumps = 0, 0, 0
        self.on_sample = self.on_pump = lambda: None

    def cohort(self):
        return deepcopy(self.reads)

    def sample(self):
        self.samples += 1
        self.on_sample()
        return deepcopy(self.snapshot)

    def pump(self, milliseconds):
        self.pumps += 1
        self.now += milliseconds / 1000
        self.on_pump()

    def collect(self):
        return collect_completion(self.cohort, self.sample, self.pump,
                                  lambda: self.now, BASE, PATHS)


class StatusCompletionControls(unittest.TestCase):
    def test_late_python_finished_callback_rejects_valid_synthetic_native_sequence(self):
        # Illustrative scheduling, NOT a reconstruction of the historical run.
        active, maximum = set(), 0
        for event, identity in (('request', 1), ('response', 1), ('request', 2),
                                ('finished', 1), ('response', 2), ('finished', 2)):
            if event == 'request':
                active.add(identity)
                maximum = max(maximum, len(active))
            elif event == 'finished':
                active.remove(identity)
        self.assertEqual(maximum, 2)
        with self.assertRaises(AssertionError):
            self.assertEqual(maximum, 1, 'old callback oracle rejects this ordering')
        proof = qualify_completion(*evidence(), BASE, PATHS)
        self.assertEqual(proof['cohort_counts'], {SYSTEM: 2, CORE: 2})
        self.assertIn('no ResourceTiming request IDs', proof['correspondence'])

    def test_full_response_overlap_fails_even_when_headers_precede_next_start(self):
        reads, snapshot = evidence()
        snapshot['entries'][0]['responseEnd'] = 4.000000000001
        with self.assertRaisesRegex(AssertionError, 'overlapping'):
            qualify_completion(reads, snapshot, BASE, PATHS)
        snapshot['entries'][0]['responseEnd'] = 4
        qualify_completion(reads, snapshot, BASE, PATHS)  # Exact touching boundary, no tolerance.

    def test_missing_headers_only_malformed_and_nonfinite_intervals_fail(self):
        for field in ('startTime', 'responseStart', 'responseEnd'):
            for value in (None, 0, -1, float('nan'), float('inf'), -float('inf'), '2', True):
                with self.subTest(field=field, value=value):
                    reads, snapshot = evidence()
                    snapshot['entries'][0][field] = value
                    with self.assertRaises(AssertionError):
                        qualify_completion(reads, snapshot, BASE, PATHS)
        for change in ({'startTime': 3}, {'responseStart': 4}, {'responseEnd': 101}):
            with self.subTest(change=change):
                reads, snapshot = evidence()
                snapshot['entries'][0].update(change)
                with self.assertRaisesRegex(AssertionError, 'malformed'):
                    qualify_completion(reads, snapshot, BASE, PATHS)
        reads, snapshot = evidence()
        del snapshot['entries'][0]['responseEnd']
        with self.assertRaises(AssertionError):
            qualify_completion(reads, snapshot, BASE, PATHS)

    def test_wrong_url_type_document_and_duplicate_entries_fail(self):
        for change in ({'name': BASE + SYSTEM + '?extra=1'}, {'name': 'https://example.invalid' + SYSTEM},
                       {'initiatorType': 'fetch'}, {'entryType': 'navigation'}):
            with self.subTest(change=change):
                reads, snapshot = evidence()
                snapshot['entries'][0].update(change)
                with self.assertRaises(AssertionError):
                    qualify_completion(reads, snapshot, BASE, PATHS)
        for key, value in (('document_url', 'about:blank'), ('time_origin', float('nan')),
                           ('observed_at', None), ('entries', None)):
            with self.subTest(key=key):
                reads, snapshot = evidence()
                snapshot[key] = value
                with self.assertRaises(AssertionError):
                    qualify_completion(reads, snapshot, BASE, PATHS)
        reads, snapshot = evidence()
        snapshot['entries'][2] = dict(snapshot['entries'][0])
        with self.assertRaisesRegex(AssertionError, 'duplicate'):
            qualify_completion(reads, snapshot, BASE, PATHS)

    def test_exact_counts_reject_missing_extra_and_duplicate_request_identities(self):
        for extra in (False, True):
            reads, snapshot = evidence()
            if extra:
                snapshot['entries'].append({**snapshot['entries'][0], 'startTime': 7,
                                            'responseStart': 8, 'responseEnd': 9})
            else:
                snapshot['entries'].pop()
            with self.subTest(extra=extra), self.assertRaisesRegex(AssertionError, 'exactly equal'):
                qualify_completion(reads, snapshot, BASE, PATHS)
        reads, snapshot = evidence()
        reads[2]['identity'] = reads[0]['identity']
        with self.assertRaisesRegex(AssertionError, 'duplicate Request'):
            qualify_completion(reads, snapshot, BASE, PATHS)

    def test_exact_request_metadata_completion_body_and_failure_are_independent(self):
        for change in ({'url': BASE + SYSTEM + '?extra=1'}, {'method': 'POST'}, {'main_frame': False},
                       {'resource_type': 'fetch'}, {'redirected_from': True}, {'redirected_to': True},
                       {'service_worker': True}, {'from_service_worker': True},
                       {'status': 503}, {'status': None}, {'failure': 'net::ERR_ABORTED'},
                       {'finished': False}, {'body_size': None}, {'body_size': 0}):
            with self.subTest(change=change):
                reads, snapshot = evidence()
                reads[0].update(change)
                with self.assertRaises(AssertionError):
                    qualify_completion(reads, snapshot, BASE, PATHS)

    def test_third_natural_poll_is_included_and_cannot_hide_overlap_or_missing_timing(self):
        reads, snapshot = evidence()
        natural(reads, snapshot)
        proof = qualify_completion(reads, snapshot, BASE, PATHS)
        self.assertEqual(proof['cohort_counts'], {SYSTEM: 3, CORE: 2})
        self.assertEqual(len(proof['intervals_start_headers_end'][SYSTEM]), 3)
        snapshot['entries'][-1]['startTime'] = 5.5
        with self.assertRaisesRegex(AssertionError, 'overlapping'):
            qualify_completion(reads, snapshot, BASE, PATHS)
        snapshot['entries'].pop()
        with self.assertRaisesRegex(AssertionError, 'exactly equal'):
            qualify_completion(reads, snapshot, BASE, PATHS)

    def test_stable_complete_cohort_uses_one_snapshot(self):
        collection = Collection()
        collection.collect()
        self.assertEqual((collection.samples, collection.pumps), (1, 0))

    def test_natural_growth_during_body_or_snapshot_collection_joins_same_deadline(self):
        collection = Collection()
        def sample():
            if collection.samples == 1:
                natural(collection.reads, collection.snapshot, finished=False)
        def pump():
            collection.reads[-1].update(finished=True, body_size=42)
            collection.snapshot['entries'].append({**collection.snapshot['entries'][0],
                'startTime': 7, 'responseStart': 8, 'responseEnd': 9})
        collection.on_sample, collection.on_pump = sample, pump
        proof = collection.collect()
        self.assertEqual(proof['cohort_counts'][SYSTEM], 3)
        self.assertEqual((collection.samples, collection.pumps), (2, 1))

    def test_invalid_ready_snapshot_is_never_retried_even_if_cohort_grows(self):
        for bad in ('overlap', 'wrong-type', 'missing-count', 'metadata'):
            with self.subTest(bad=bad):
                collection = Collection()
                def sample():
                    if bad != 'missing-count':
                        natural(collection.reads, collection.snapshot)
                    if bad == 'overlap':
                        collection.snapshot['entries'][0]['responseEnd'] = 4.001
                    elif bad == 'wrong-type':
                        collection.snapshot['entries'][0]['initiatorType'] = 'fetch'
                    elif bad == 'missing-count':
                        collection.snapshot['entries'].pop()
                    else:
                        collection.reads[-1]['status'] = 500
                collection.on_sample = sample
                with self.assertRaises(AssertionError):
                    collection.collect()
                self.assertEqual((collection.samples, collection.pumps), (1, 0))

    def test_incomplete_cohort_fails_one_bounded_deadline_without_native_snapshot(self):
        collection = Collection()
        collection.reads[0].update(finished=False, body_size=None)
        with self.assertRaisesRegex(AssertionError, 'deadline'):
            collection.collect()
        self.assertEqual(collection.samples, 0)
        self.assertLessEqual(collection.pumps, 401)
        self.assertAlmostEqual(collection.now, 4)

    def test_document_origin_and_snapshot_clock_must_remain_consistent_during_growth(self):
        for field, value in (('time_origin', 1001), ('observed_at', 99)):
            with self.subTest(field=field):
                collection = Collection()
                def sample():
                    if collection.samples == 1:
                        natural(collection.reads, collection.snapshot)
                    else:
                        collection.snapshot[field] = value
                collection.on_sample = sample
                with self.assertRaisesRegex(AssertionError, 'document clock changed'):
                    collection.collect()
                self.assertEqual(collection.samples, 2)

    def test_natural_growth_does_not_reset_observation_deadline(self):
        collection = Collection()
        def sample():
            collection.now = 3.99
            natural(collection.reads, collection.snapshot, finished=False)
        collection.on_sample = sample
        with self.assertRaisesRegex(AssertionError, 'deadline'):
            collection.collect()
        self.assertEqual(collection.samples, 1)
        self.assertAlmostEqual(collection.now, 4)


if __name__ == '__main__':
    unittest.main()
