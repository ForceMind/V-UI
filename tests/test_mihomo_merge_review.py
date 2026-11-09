import unittest
from unittest.mock import patch

from app.services import mihomo_merge_download as transport
from app.services.mihomo_preview import MihomoPreviewError

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


class MergeReviewTransportTests(unittest.TestCase):
    def test_parent_rejects_unknown_fields_types_and_arbitrary_text(self):
        valid = {'fingerprint': 'a'*64, 'conflicts': [
            {'index': 0, 'section': 'other', 'baseline': [1, 1], 'candidate': [1, 1]}]}
        import json
        invalid = [
            {**valid, 'extra': 'private'},
            {**valid, 'fingerprint': 'private'},
            {**valid, 'conflicts': [{**valid['conflicts'][0], 'section': 'private'}]},
            {**valid, 'conflicts': [{**valid['conflicts'][0], 'baseline': [True, 1]}]},
            {**valid, 'conflicts': [{**valid['conflicts'][0], 'candidate': [99, 1]}]},
        ]
        for report in invalid:
            with self.subTest(report=report), patch.object(transport, '_request', return_value=json.dumps(report).encode()):
                with self.assertRaisesRegex(MihomoPreviewError, '^PARSER_FAILED$'):
                    transport.review_mihomo_download('x: 1', 'x: 2')
        with patch.object(transport, '_request', return_value=json.dumps(valid).encode()):
            self.assertEqual(transport.review_mihomo_download('x: 1', 'x: 2'), valid)

    def test_parent_rejects_excessively_nested_worker_json(self):
        raw = b'[' * 2000 + b'0' + b']' * 2000
        with patch.object(transport, '_request', return_value=raw):
            with self.assertRaisesRegex(MihomoPreviewError, '^PARSER_FAILED$'):
                transport.review_mihomo_download('x: 1', 'x: 2')

    def test_prepared_record_response_requires_exact_framing_and_source_hashes(self):
        from test_mihomo_plan_record import MihomoPlanRecordTests
        raw = MihomoPlanRecordTests().record()
        wrong_sources = (str(len(raw)) + ' 2\n').encode() + raw + b'{}'
        for response in (b'0 2\n{}', b'9999999 2\n{}', b'2 2\n{}', wrong_sources):
            with self.subTest(size=len(response)), patch.object(transport, '_request', return_value=response):
                with self.assertRaisesRegex(MihomoPreviewError, '^PARSER_FAILED$'):
                    transport.prepare_mihomo_draft('{}', '{}', 'a'*64, [], {}, None)

    def test_private_options_allow_all_declared_nodes_and_both_builtin_leaves(self):
        import json
        names = ['node-' + str(index) for index in range(128)]
        report = {'upstreams': names, 'groups': [{'name': 'Chosen', 'members': names + ['DIRECT', 'REJECT']}]}
        with patch.object(transport, '_request', return_value=json.dumps(report).encode()):
            self.assertEqual(transport.mihomo_draft_options('{}', '{}', 'a'*64, []), report)
