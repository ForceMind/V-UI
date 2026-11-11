from copy import deepcopy
import hashlib
import json
import unittest

from app.services.mihomo_merge_plan import plan_mihomo
from app.services.mihomo_plan_record import PlanRecordError, decode_plan_record, encode_plan_record
from test_mihomo_merge_plan import BASELINE


class MihomoPlanRecordTests(unittest.TestCase):
    def record(self):
        plan = plan_mihomo(BASELINE, '{rules: ["DOMAIN,example.com,DIRECT", "MATCH,Chosen"]}',
                           group_members={'Chosen': 'upstream'})
        return encode_plan_record(plan, sources={key: hashlib.sha256(key.encode()).hexdigest()
                                  for key in ('baseline', 'candidate', 'merged')},
                                  group_members={'Chosen': 'upstream'}, dns_upstream='upstream')

    def test_canonical_private_record_roundtrip_and_content_identity(self):
        raw = self.record()
        decoded = decode_plan_record(raw)
        self.assertEqual(decoded.record_id, hashlib.sha256(raw).hexdigest())
        self.assertEqual(decoded.value['rules'], [['DOMAIN', 'example.com', 'DIRECT'], ['MATCH', '', 'upstream']])
        self.assertNotIn('relay.example.com', repr(decoded))
        self.assertNotIn('11111111-', repr(decoded))
        detached = decoded.value
        detached['rules'][-1][-1] = 'DIRECT'
        self.assertEqual(decoded.value['rules'][-1][-1], 'upstream')
        with self.assertRaisesRegex(PlanRecordError, '^YAML_PLAN_RECORD_INVALID$'):
            decode_plan_record(raw+b'\n')

    def test_corrupt_semantics_and_duplicate_keys_are_refused(self):
        value = json.loads(self.record())
        for field, changed in (
            ('schema', True), ('compiler', True), ('sources', {}),
            ('dns_upstream', 'missing'), ('group_members', {'GLOBAL': 'upstream'}), ('group_members', {'Chosen': 'missing'}),
            ('group_members', {'Chosen': 'DIRECT'}),
            ('rules', [['MATCH', '', 'DIRECT'], ['MATCH', '', 'REJECT']]),
            ('rules', [['IP-CIDR', '192.0.2.1/24', 'DIRECT'], ['MATCH', '', 'REJECT']]),
            ('rules', [['MATCH', '', 'missing']]),
        ):
            bad = deepcopy(value)
            bad[field] = changed
            raw = json.dumps(bad, ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode()
            with self.subTest(field=field), self.assertRaisesRegex(PlanRecordError, '^YAML_PLAN_RECORD_INVALID$'):
                decode_plan_record(raw)
        with self.assertRaisesRegex(PlanRecordError, '^YAML_PLAN_RECORD_INVALID$'):
            decode_plan_record(b'{"schema":1,"schema":1}')

    def test_integer_parser_refuses_oversized_integers_before_conversion(self):
        with self.assertRaisesRegex(PlanRecordError, '^YAML_PLAN_RECORD_INVALID$'):
            decode_plan_record(b'{"schema":' + b'9' * 100000 + b'}')

    def test_control_names_remain_reserved_in_private_records(self):
        value = json.loads(self.record())
        value['upstreams'][0]['name'] = 'GLOBAL'
        value['rules'][-1][-1] = 'GLOBAL'
        value['group_members']['Chosen'] = 'GLOBAL'
        value['dns_upstream'] = 'GLOBAL'
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
        with self.assertRaisesRegex(PlanRecordError, '^YAML_PLAN_RECORD_INVALID$'):
            decode_plan_record(raw)
