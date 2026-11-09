import unittest

from app.services.mihomo_merge import MihomoMergeError, merge_mihomo
from app.services.mihomo_merge_review import review_mihomo_merge, serialize_reviewed_mihomo_merge


class MihomoMergeReviewTests(unittest.TestCase):
    def test_review_locates_conflicts_without_returning_names_or_values(self):
        baseline = 'proxies:\n  - {name: private-name, token: private-old}\nsecret-key: private-old\n'
        candidate = 'secret-key: private-new\nproxies:\n  - {name: private-name, token: private-new}\n'
        review = review_mihomo_merge(baseline, candidate)
        self.assertEqual(review['conflicts'], [
            {'index': 0, 'section': 'other', 'baseline': [3, 13], 'candidate': [1, 13]},
            {'index': 1, 'section': 'proxies', 'baseline': [2, 5], 'candidate': [3, 5]},
        ])
        for secret in ('private-name', 'private-old', 'private-new', 'secret-key'):
            self.assertNotIn(secret, repr(review))
        raw = serialize_reviewed_mihomo_merge(baseline, candidate, review['fingerprint'],
                                               ['baseline', 'candidate'])
        self.assertEqual(merge_mihomo('{}', raw),
                         {'proxies': [{'name': 'private-name', 'token': 'private-new'}], 'secret-key': 'private-old'})

    def test_review_choices_are_bound_to_exact_bytes_and_cover_every_conflict(self):
        baseline, candidate = 'key: false', 'key: 0'
        fingerprint = review_mihomo_merge(baseline, candidate)['fingerprint']
        for old, new in ((baseline+'\n', candidate), (baseline, candidate+'\n'), (candidate, baseline)):
            with self.subTest(old=old, new=new), self.assertRaisesRegex(MihomoMergeError, '^MERGE_REVIEW_STALE$'):
                serialize_reviewed_mihomo_merge(old, new, fingerprint, ['candidate'])
        for selections in ([], ['candidate', 'baseline'], ['unknown'], [None]):
            with self.subTest(selections=selections), self.assertRaisesRegex(MihomoMergeError, '^MERGE_INVALID_CHOICES$'):
                serialize_reviewed_mihomo_merge(baseline, candidate, fingerprint, selections)

    def test_conflict_limit_refuses_a_partial_review(self):
        left = '\n'.join(f'key-{i}: false' for i in range(257))
        right = '\n'.join(f'key-{i}: true' for i in range(257))
        with self.assertRaisesRegex(MihomoMergeError, '^MERGE_REVIEW_LIMIT$'):
            review_mihomo_merge(left, right)
