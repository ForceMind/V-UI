"""Synthetic archive controls only: no Go, cores, source build, CI or network."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import prepare_runtime_evidence as p

# A deliberately tiny synthetic contract still reuses the real owner/old lists.
CONTRACT = {'groups': [{'label': 'owner-example'}],
            'files': [{'source': 'tests/sing/example_test.go'}]}


def encoded(value):
    return json.dumps(value, sort_keys=True).encode() + b'\n'


def source_record():
    return {'vui_commit': '1' * 40, 'vui_tree': '2' * 40,
            'files': {name: str(index + 1) * 64 for index, name in enumerate(p.PATCHES)}}


def provenance_record():
    source = source_record()
    return {'schema_version': 1, 'kind': p.KIND, 'source_identity': source,
            'patches': [{'path': name, 'sha256': source['files'][name]} for name in p.PATCHES]}


def input_record(arm):
    return {'schema_version': 1, 'kind': p.INPUT_KIND, 'arm': arm,
            'source_identity': source_record(),
            'official': {'path': '/not-present/official/sing-box', 'sha256': '3' * 64, 'version': '1.14.2'},
            'candidate': {'path': '/not-present/owner-sing-box', 'sha256': '4' * 64,
                          'version': '1.14.2-vui-socks-owner-experiment-' + '1' * 12,
                          'provenance_path': '/not-present/owner-provenance.json',
                          'provenance_sha256': '5' * 64},
            'build_evidence': {'path': '/not-present/build/evidence', 'files': {}}}


def owner_event():
    return {'pid': 123, 'owner_id': 7, 'sequence': 1, 'transition': 'create', 'generation': 0,
            'packet_sequence': 0, 'accepted_packets': 0, 'rejected_sources': 0, 'remapped_packets': 0,
            'cause': 'none', 'source': '127.0.0.1:0', 'relay': '127.0.0.1:23456', 'target': ':0'}


def runtime_record():
    return {'schema_version': 1, 'kind': p.RUNTIME_KIND, 'product_qualified': False,
            'runtime_executed': True, 'device_whole_window_continuity_proven': False,
            'finite_experiment_contract_passed': False, 'errors': ['synthetic failed run']}


class ArchiveFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.build = self.root / 'build'
        self.runtime = self.root / 'runtime'
        self.output = self.root / 'archive'
        self.build.mkdir()
        self.runtime.mkdir()

    def write(self, name, value, *, runtime=False):
        path = (self.runtime if runtime else self.build) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value if isinstance(value, bytes) else value.encode())
        return path

    def prepare(self):
        return p.prepare(self.build, self.output, CONTRACT, self.runtime)

    def partial(self):
        self.write('RESULT.json', encoded({'kind': p.KIND, 'product_qualified': False, 'passed': False}))
        self.write('base/compiler.stderr', b'compiler failed\r\n')
        self.write('off/D/runner-error.log', b'early runtime failure\n', runtime=True)

    def manifest(self):
        return json.loads((self.output / p.MANIFEST).read_text())

    def replace_manifest(self, value):
        (self.output / p.MANIFEST).write_bytes(encoded(value))

    def reject_prepare(self):
        with self.assertRaises((ValueError, OSError, UnicodeError)):
            self.prepare()
        self.assertFalse(self.output.exists())

    def reject_verify(self):
        with self.assertRaises((ValueError, OSError, UnicodeError)):
            p.verify_archive(self.output, CONTRACT)


class AllowlistTests(unittest.TestCase):
    def test_authoritative_build_lists_exact(self):
        files = p.allowed_files(CONTRACT)
        self.assertEqual({name for name in files if '/' not in name}, p.owner_prepare.owner_files(CONTRACT))
        for prefix in ('base', 'regression'):
            self.assertEqual({name.split('/', 1)[1] for name in files if name.startswith(prefix + '/')},
                             p.h.packet_prepare.allowed_build())

    def test_finite_runtime_tree_exact(self):
        files = p.allowed_files(CONTRACT)
        expected = {'runtime/off-input.json', 'runtime/on-input.json', 'runtime/RUNTIME_RESULT.json',
                    'runtime/off/stages.json', 'runtime/on/stages.json'}
        for arm, stages in p.ARMS.items():
            for stage in stages:
                expected.update('runtime/' + arm + '/' + stage + '/' + name
                                for name in p.boundary.expected_artifacts(p.boundary.plan(stage)) | {'manifest.json'})
        self.assertEqual({name for name in files if name.startswith('runtime/')}, expected)
        self.assertEqual(p.ARMS, {'off': ('C', 'D', 'A', 'B', 'U', 'V'), 'on': ('C', 'D')})

    def test_jsonl_only_exact_original_and_owner_paths(self):
        files = p.allowed_files(CONTRACT)
        expected = {'owner-example-' + mode + '.stdout' for mode in p.h.MODES}
        expected.update(prefix + '/' + name for prefix in ('base', 'regression') for name in p.h.packet_prepare.GO_JSONL)
        self.assertEqual({name for name, format in files.items() if format == 'jsonl'}, expected)
        self.assertEqual(files['modules.stdout'], 'text')
        self.assertEqual(files['owner-example-race.stderr'], 'text')

    def test_distinct_archive_kind(self):
        self.assertEqual(p.ARCHIVE_KIND, 'vui-socks-owner-source-experiment-text-evidence-archive')
        self.assertNotEqual(p.ARCHIVE_KIND, p.KIND)
        self.assertNotEqual(p.RUNTIME_KIND, p.ARCHIVE_KIND)

    def test_noncanonical_member_names(self):
        for name in ('', '.', '..', '../a', 'a/../b', '/a', 'a//b', './a', 'a/./b', 'a/', 'a\\b', 'a\0b'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                p.member_name(name)


class PreservationTests(ArchiveFixture):
    def test_partial_failed_evidence_preserved_exactly(self):
        self.partial()
        self.write('owner-example-race.stdout', b'{"Action":"fail","Test":"TestExample/b"}\r\n\r\n')
        self.write('regression/common-network-race.stdout', b'{"Action":"fail","Test":"TestOld/b"}\n')
        report = self.prepare()
        self.assertFalse(report['product_qualified'])
        self.assertTrue(report['packaged_only'])
        self.assertEqual(report['runtime_verdict'], 'not_assessed')
        self.assertEqual(report['count'], 5)
        self.assertEqual((self.output / 'base/compiler.stderr').read_bytes(), b'compiler failed\r\n')
        self.assertEqual((self.output / 'owner-example-race.stdout').read_bytes(),
                         (self.build / 'owner-example-race.stdout').read_bytes())
        self.assertEqual(report, p.verify_archive(self.output, CONTRACT))
        self.assertEqual([row['path'] for row in report['files']], sorted(row['path'] for row in report['files']))

    def test_all_allowed_artifacts_can_be_preserved(self):
        files = p.allowed_files(CONTRACT)
        for name, format in files.items():
            data = b'{}\n' if format in ('json', 'jsonl') else b'synthetic fixture output\n'
            if name == 'provenance.json':
                data = encoded(provenance_record())
            elif p.re.fullmatch(r'runtime/(?:off|on)/[CDABUV]/input-identity\.json', name):
                data = encoded({'source_identity': source_record(), 'build_provenance': provenance_record()})
            elif name in ('runtime/off-input.json', 'runtime/on-input.json'):
                data = encoded(input_record(name.split('/')[1].split('-')[0]))
            elif name == 'runtime/RUNTIME_RESULT.json' or p.re.fullmatch(r'runtime/(?:off|on)/[CDABUV]/result\.json', name):
                data = encoded(runtime_record())
            elif p.re.fullmatch(r'runtime/(?:off|on)/[CDABUV]/manifest\.json', name):
                data = encoded({'schema_version': 1, 'kind': p.RUNTIME_KIND + '-evidence', 'files': {}})
            self.write(name.removeprefix('runtime/') if name.startswith('runtime/') else name,
                       data, runtime=name.startswith('runtime/'))
        report = self.prepare()
        self.assertEqual(report['count'], len(files))
        self.assertEqual(report, p.verify_archive(self.output, CONTRACT))

    def test_missing_runtime_preserves_build(self):
        self.runtime.rmdir()
        self.write('RESULT.json', b'{"passed":false}\n')
        self.assertEqual(self.prepare()['count'], 1)

    def test_missing_build_preserves_runtime(self):
        self.build.rmdir()
        self.write('on/D/runner-error.log', b'early failed\n', runtime=True)
        self.assertEqual(self.prepare()['count'], 1)

    def test_optional_roots_can_be_omitted(self):
        self.write('RESULT.json', b'{}')
        self.assertEqual(p.prepare(self.build, self.output, CONTRACT)['count'], 1)

    def test_no_files_cannot_create_archive(self):
        self.reject_prepare()

    def test_no_files_with_roots_missing_cannot_create_archive(self):
        self.build.rmdir()
        self.runtime.rmdir()
        self.reject_prepare()

    def test_input_metadata_never_opens_or_executes_recorded_paths(self):
        self.write('off-input.json', encoded(input_record('off')), runtime=True)
        self.write('on-input.json', encoded(input_record('on')), runtime=True)
        self.write('RUNTIME_RESULT.json', encoded(runtime_record()), runtime=True)
        with patch.object(p.boundary, 'validate_manifest', side_effect=AssertionError('old validator forbidden')), \
             patch.object(p.h, 'identity', side_effect=AssertionError('source inspection forbidden')), \
             patch('subprocess.run', side_effect=AssertionError('execution forbidden')), \
             patch('subprocess.Popen', side_effect=AssertionError('execution forbidden')):
            report = self.prepare()
        self.assertEqual(report['runtime_verdict'], 'not_assessed')
        self.assertFalse(report['product_qualified'])

    def test_recorded_pass_never_becomes_archive_verdict(self):
        value = runtime_record()
        value['finite_experiment_contract_passed'] = True
        self.write('RUNTIME_RESULT.json', encoded(value), runtime=True)
        self.assertEqual(self.prepare()['runtime_verdict'], 'not_assessed')
        self.assertTrue(json.loads((self.output / 'runtime/RUNTIME_RESULT.json').read_text())['finite_experiment_contract_passed'])

    def test_existing_output_not_overwritten(self):
        self.partial()
        self.output.mkdir()
        (self.output / 'keep').write_text('leave unchanged')
        with self.assertRaises(FileExistsError):
            self.prepare()
        self.assertEqual((self.output / 'keep').read_text(), 'leave unchanged')


class TreeSafetyTests(ArchiveFixture):
    def test_unknown_build_file(self):
        for name in ('owner-sing-box', 'module/source.go', 'key.pem', 'compiler.stdout', 'runtime/off-input.json'):
            with self.subTest(name=name):
                path = self.write(name, b'not allowed\n')
                self.reject_prepare()
                path.unlink()
                if path.parent != self.build:
                    path.parent.rmdir()

    def test_unknown_empty_directory(self):
        self.partial()
        (self.build / 'source').mkdir()
        self.reject_prepare()

    def test_unknown_runtime_file_or_directory(self):
        self.partial()
        for name in ('on/A', 'on/U', 'on/V', 'off/E', 'off/C/unknown', 'on/D/D_aes_added'):
            with self.subTest(name=name):
                path = self.runtime / name
                path.mkdir(parents=True)
                self.reject_prepare()
                path.rmdir()

    def test_old_or_alternate_stage_manifest_not_allowed(self):
        self.write('on/D/evidence-manifest.json', b'{}', runtime=True)
        self.reject_prepare()

    def test_linked_build_root(self):
        self.build.rmdir()
        self.write('off/stages.json', b'[]', runtime=True)
        self.build.symlink_to(self.runtime, target_is_directory=True)
        self.reject_prepare()

    def test_dangling_runtime_root(self):
        self.runtime.rmdir()
        self.runtime.symlink_to(self.root / 'absent')
        self.write('RESULT.json', b'{}')
        self.reject_prepare()

    def test_linked_ancestor(self):
        self.write('RESULT.json', b'{}')
        link = self.root / 'alias'
        link.symlink_to(self.build, target_is_directory=True)
        with self.assertRaises((ValueError, OSError)):
            p.prepare(link / 'missing', self.output, CONTRACT, self.runtime)
        self.assertFalse(self.output.exists())

    def test_file_symlink_and_dangling_file_symlink(self):
        for target in (self.root / 'missing', self.root / 'outside'):
            target.write_text('outside') if target.name == 'outside' else None
            path = self.build / 'RESULT.json'
            path.symlink_to(target)
            self.reject_prepare()
            path.unlink()

    def test_directory_symlink(self):
        (self.build / 'base').symlink_to(self.runtime, target_is_directory=True)
        self.reject_prepare()

    def test_hardlink_to_external_file(self):
        source = self.root / 'outside.json'
        source.write_text('{}')
        os.link(source, self.build / 'RESULT.json')
        self.reject_prepare()

    def test_hardlink_aliases_inside_and_across_roots(self):
        source = self.write('RESULT.json', b'{}')
        for alias in (self.build / 'VERIFICATION.json', self.runtime / 'off-input.json'):
            os.link(source, alias)
            self.reject_prepare()
            alias.unlink()

    def test_fifo_is_rejected_without_opening(self):
        os.mkfifo(self.build / 'RESULT.json')
        self.reject_prepare()

    def test_file_used_as_root(self):
        self.build.rmdir()
        self.build.write_text('{}')
        self.reject_prepare()

    def test_overlapping_roots_and_output(self):
        self.partial()
        for output in (self.build, self.build / 'upload', self.root):
            with self.subTest(output=output), self.assertRaises(ValueError):
                p.prepare(self.build, output, CONTRACT, self.runtime)
        with self.assertRaises(ValueError):
            p.prepare(self.build, self.output, CONTRACT, self.build)

    def test_parent_traversal_root(self):
        self.partial()
        with self.assertRaises(ValueError):
            p.prepare(self.build / '..' / 'build', self.output, CONTRACT)

    def test_symlinked_output_parent(self):
        self.partial()
        alias = self.root / 'out-parent'
        alias.symlink_to(self.runtime, target_is_directory=True)
        with self.assertRaises((ValueError, OSError)):
            p.prepare(self.build, alias / 'archive', CONTRACT)
        self.assertFalse((self.runtime / 'archive').exists())

    def test_file_replaced_with_symlink_between_stat_and_open(self):
        victim = self.write('RESULT.json', b'{}')
        outside = self.root / 'external'
        outside.write_text('secret outside')
        real_open = os.open

        def racing_open(name, flags, *args, **kwargs):
            if name == 'RESULT.json' and 'dir_fd' in kwargs:
                victim.unlink()
                victim.symlink_to(outside)
            return real_open(name, flags, *args, **kwargs)
        with patch.object(p.os, 'open', side_effect=racing_open):
            self.reject_prepare()


class ResourceLimitTests(ArchiveFixture):
    def test_member_ceiling_before_read(self):
        self.write('modules.stderr', b'x' * 65)
        with patch.object(p, 'MAX_FILE_BYTES', 64), patch.object(p.os, 'fdopen', side_effect=AssertionError('must not read')):
            self.reject_prepare()

    def test_shared_build_runtime_budget(self):
        self.write('modules.stderr', b'x' * 40)
        self.write('off/D/runner-error.log', b'x' * 40, runtime=True)
        with patch.object(p, 'MAX_TOTAL_BYTES', 70):
            self.reject_prepare()

    def test_archive_manifest_counts_toward_budget(self):
        self.write('modules.stderr', b'x' * 40)
        with patch.object(p, 'MAX_TOTAL_BYTES', 100):
            self.reject_prepare()

    def test_verify_enforces_member_and_total_limits(self):
        self.partial()
        self.prepare()
        with patch.object(p, 'MAX_FILE_BYTES', 20):
            self.reject_verify()
        with patch.object(p, 'MAX_TOTAL_BYTES', 20):
            self.reject_verify()


class TextSafetyTests(ArchiveFixture):
    def test_duplicate_json_keys_at_any_depth(self):
        for value in (b'{"same":1,"same":2}', b'{"nested":{"same":1,"same":2}}',
                      b'{"nested":[{"same":1,"same":2}]}'):
            with self.subTest(value=value):
                self.write('RESULT.json', value)
                self.reject_prepare()

    def test_invalid_json_and_jsonl(self):
        for name, value in (('RESULT.json', b'{'), ('owner-example-race.stdout', b'[]\n'),
                            ('owner-example-race.stdout', b'{"a":1,"a":2}\n'),
                            ('owner-example-race.stdout', b'not JSON\n'),
                            ('base/common-network-race.stdout', b'{}\n{invalid}\n')):
            with self.subTest(name=name, value=value):
                self.write(name, value)
                self.reject_prepare()
                (self.build / name).unlink()

    def test_nonfinite_json_numbers(self):
        for value in ('NaN', 'Infinity', '-Infinity', '1e999', '{"nested":[NaN]}'):
            with self.subTest(value=value):
                self.write('RESULT.json', value)
                self.reject_prepare()

    def test_utf8_nul_pem_and_fixture_secrets(self):
        for value in (b'\xff', b'\x00', b'-----BEGIN PRIVATE KEY-----', b'-----BEGIN CERTIFICATE-----',
                      b'-----BEGIN PUBLIC KEY-----', b'-----BEGIN OPENSSH PRIVATE KEY-----',
                      b'11111111-1111-4111-8111-111111111111', b'\nb"actual packet payload"\n'):
            with self.subTest(value=value):
                self.write('modules.stderr', value)
                self.reject_prepare()

    def test_escaped_nul_pem_and_fixture_secrets(self):
        for value in ('{"x":"\\u0000"}', '{"x":"\\ud800"}', '{"x":"-----BEGIN\\u0020CERTIFICATE-----"}',
                      '{"x":["11111111-1111-4111-8111-111111111111"]}'):
            with self.subTest(value=value):
                self.write('RESULT.json', value)
                self.reject_prepare()

    def test_nested_sensitive_fields(self):
        for key in ('password', 'api_key', 'apiKey', 'client-secret', 'access_token', 'privateKey',
                    'Authorization', 'credentials', 'uuid', 'tls_private_key', 'secret_key', 'session_token'):
            with self.subTest(key=key):
                self.write('RESULT.json', encoded({'outer': [{'nested': {key: 'not-safe'}}]}))
                self.reject_prepare()

    def test_plaintext_secret_assignments(self):
        for text in ('password=not-safe', 'Authorization: Bearer abc.def', 'api_key = secret',
                     '{"client_secret":"do-not-archive"}', 'token=private', 'https://user:private@example.test/path'):
            with self.subTest(text=text):
                self.write('modules.stderr', text)
                self.reject_prepare()

    def test_owner_log_strict_json_keys_in_text_and_json(self):
        for event in ('{"pid":1,"pid":2}', '{"pid":1,"nested":{"x":1,"x":2}}',
                      '{"pid":NaN}', '[]', '{"pid":1} trailing', '{"password":"secret"}'):
            for structured in (False, True):
                with self.subTest(event=event, structured=structured):
                    line = 'INFO device socks_udp_owner ' + event
                    name = 'RESULT.json' if structured else 'modules.stderr'
                    self.write(name, encoded({'log': line}) if structured else line)
                    self.reject_prepare()
                    (self.build / name).unlink()

    def test_owner_log_valid(self):
        event = owner_event()
        self.write('on/D/frozen-fixture/D_aes_large_idle.log',
                   'INFO device socks_udp_owner ' + json.dumps(event) + '\n', runtime=True)
        self.assertEqual(self.prepare()['count'], 1)

    def test_owner_log_typed_shape_bounds_and_partial_events(self):
        for key, value in (('pid', 0), ('pid', True), ('owner_id', -1), ('sequence', 2**64),
                           ('transition', 'mystery'), ('cause', 'private arbitrary error'),
                           ('source', 'localhost:1234'), ('relay', '127.0.0.1:01'),
                           ('target', '[::1]:80'), ('accepted_packets', 1.5)):
            with self.subTest(key=key, value=value):
                event = owner_event()
                event[key] = value
                self.write('modules.stderr', 'INFO socks_udp_owner ' + json.dumps(event) + '\n')
                self.reject_prepare()
        path = self.build / 'modules.stderr'
        path.unlink()
        event = owner_event()
        event.update(sequence=14, transition='retire', generation=2, cause='outcome_unknown', target='127.0.0.3:1234')
        self.write('modules.stderr', 'INFO socks_udp_owner ' + json.dumps(event) + '\n')
        self.assertEqual(self.prepare()['count'], 1)  # No complete lifecycle requirement.

    def test_owner_log_missing_extra_unterminated_and_oversized(self):
        event = owner_event()
        missing = dict(event)
        missing.pop('pid')
        extra = {**event, 'extra': 'not allowed'}
        for text in ('socks_udp_owner ' + json.dumps(missing) + '\n',
                     'socks_udp_owner ' + json.dumps(extra) + '\n',
                     'socks_udp_owner ' + json.dumps(event),
                     'x' * 4096 + 'socks_udp_owner ' + json.dumps(event) + '\n'):
            self.write('modules.stderr', text)
            self.reject_prepare()

    def test_known_credential_prefixes_in_text_and_nested_json(self):
        for token in ('ghp_abcdefghijk', 'github_pat_abcdef_1234', 'sk-proj-abcdef1234',
                      'AKIA' + 'A' * 16, 'ASIA' + 'B' * 16):
            for name, data in (('modules.stderr', token), ('RESULT.json', encoded({'log': [token]}))):
                with self.subTest(token=token, name=name):
                    self.write(name, data)
                    self.reject_prepare()
                    (self.build / name).unlink()

    def test_owner_source_identity_shape_and_exact_patch_paths(self):
        for mutation in ('commit', 'tree', 'missing', 'extra', 'digest', 'version'):
            record = input_record('on')
            source = record['source_identity']
            if mutation == 'commit': source['vui_commit'] = 'bad'
            if mutation == 'tree': source['vui_tree'] = '3' * 39
            if mutation == 'missing': del source['files'][p.PATCHES[-1]]
            if mutation == 'extra': source['files']['other.patch'] = 'a' * 64
            if mutation == 'digest': source['files'][p.PATCHES[0]] = 'bad'
            if mutation == 'version': record['candidate']['version'] = '1.14.2-vui-complete-packet-experiment-' + '1' * 12
            with self.subTest(mutation=mutation):
                self.write('on-input.json', encoded(record), runtime=True)
                self.reject_prepare()

    def test_new_provenance_requires_ordered_eight_patch_chain(self):
        for mutation in ('kind', 'schema', 'missing', 'order', 'hash'):
            value = provenance_record()
            if mutation == 'kind': value['kind'] = 'vui-complete-packet-source-experiment'
            if mutation == 'schema': value['schema_version'] = True
            if mutation == 'missing': value['patches'].pop()
            if mutation == 'order': value['patches'].reverse()
            if mutation == 'hash': value['patches'][0]['sha256'] = '0' * 64
            for nested in (False, True):
                with self.subTest(mutation=mutation, nested=nested):
                    name = 'off/D/input-identity.json' if nested else 'provenance.json'
                    data = {'source_identity': source_record(), 'build_provenance': value} if nested else value
                    path = self.write(name, encoded(data), runtime=nested)
                    self.reject_prepare()
                    path.unlink()

    def test_new_provenance_does_not_require_success_or_binary_access(self):
        value = provenance_record()
        value['passed'] = False
        self.write('provenance.json', encoded(value))
        self.assertEqual(self.prepare()['count'], 1)

    def test_old_five_patch_input_kind_rejected(self):
        record = input_record('off')
        record['kind'] = 'vui-complete-packet-source-experiment'
        self.write('off-input.json', encoded(record), runtime=True)
        self.reject_prepare()

    def test_wrong_input_arm_and_implicit_mode_rejected(self):
        for mutation in ('arm', 'missing', 'bool_schema', 'unknown'):
            record = input_record('off')
            if mutation == 'arm': record['arm'] = 'on'
            if mutation == 'missing': del record['arm']
            if mutation == 'bool_schema': record['schema_version'] = True
            if mutation == 'unknown': record['legacy'] = True
            self.write('off-input.json', encoded(record), runtime=True)
            self.reject_prepare()

    def test_bad_input_binary_descriptor(self):
        for key, value in (('sha256', 'bad'), ('version', ''), ('path', 123)):
            record = input_record('on')
            record['candidate'][key] = value
            self.write('on-input.json', encoded(record), runtime=True)
            self.reject_prepare()

    def test_old_stage_evidence_manifest_kind_rejected(self):
        self.write('off/D/manifest.json', encoded({'schema_version': 1, 'kind': p.boundary.KIND + '-evidence', 'files': {}}), runtime=True)
        self.reject_prepare()

    def test_old_stage_result_kind_rejected(self):
        value = runtime_record()
        value['kind'] = p.boundary.KIND
        self.write('on/C/result.json', encoded(value), runtime=True)
        self.reject_prepare()

    def test_runtime_result_cannot_claim_continuity_or_product(self):
        for key, value in (('device_whole_window_continuity_proven', True), ('product_qualified', True), ('schema_version', True),
                           ('kind', p.boundary.KIND)):
            report = runtime_record()
            report[key] = value
            self.write('RUNTIME_RESULT.json', encoded(report), runtime=True)
            self.reject_prepare()


class IndependentVerificationTests(ArchiveFixture):
    def setUp(self):
        super().setUp()
        self.partial()
        self.prepare()

    def test_each_scope_field_cannot_be_forged(self):
        original = self.manifest()
        for key, value in (('schema_version', True), ('schema_version', 2), ('count', True), ('count', 0),
                           ('kind', p.KIND), ('packaged_only', False), ('packaged_only', 1),
                           ('runtime_verdict', 'passed'), ('product_qualified', True), ('product_qualified', 0),
                           ('allowlist_sha256', '0' * 64)):
            with self.subTest(key=key, value=value):
                report = copy.deepcopy(original)
                report[key] = value
                self.replace_manifest(report)
                self.reject_verify()
        self.replace_manifest(original)

    def test_each_file_field_cannot_be_forged(self):
        original = self.manifest()
        for key, value in (('bytes', 999), ('bytes', True), ('bytes', -1), ('sha256', '0' * 64),
                           ('format', 'jsonl'), ('path', '../escape.json'), ('path', '/RESULT.json'),
                           ('path', './RESULT.json'), ('path', 'base//compiler.stderr')):
            with self.subTest(key=key, value=value):
                report = copy.deepcopy(original)
                report['files'][0][key] = value
                self.replace_manifest(report)
                self.reject_verify()

    def test_duplicate_unsorted_missing_extra_rows(self):
        original = self.manifest()
        for mode in ('duplicate', 'unsorted', 'missing', 'extra', 'row-key', 'root-key'):
            report = copy.deepcopy(original)
            if mode == 'duplicate': report['files'].append(report['files'][0])
            if mode == 'unsorted': report['files'].reverse()
            if mode == 'missing': report['files'].pop()
            if mode == 'extra': report['files'].append({'path': 'VERIFICATION.json', 'bytes': 0, 'sha256': '0' * 64, 'format': 'json'})
            if mode == 'row-key': report['files'][0]['extra'] = 'not allowed'
            if mode == 'root-key': report['extra'] = 'not allowed'
            report['count'] = len(report['files'])
            with self.subTest(mode=mode):
                self.replace_manifest(report)
                self.reject_verify()

    def test_duplicate_manifest_json_keys(self):
        raw = (self.output / p.MANIFEST).read_text()
        (self.output / p.MANIFEST).write_text('{"count":3,' + raw[1:])
        self.reject_verify()

    def test_tampered_or_missing_actual_file(self):
        path = self.output / 'RESULT.json'
        path.write_text('{"passed":true}')
        self.reject_verify()
        path.unlink()
        self.reject_verify()

    def test_unmanifested_allowed_actual_file(self):
        (self.output / 'VERIFICATION.json').write_text('{}')
        self.reject_verify()

    def test_unknown_empty_archive_directory(self):
        (self.output / 'hidden').mkdir()
        self.reject_verify()

    def test_changed_allowlist_rejected(self):
        changed = copy.deepcopy(CONTRACT)
        changed['groups'].append({'label': 'extra-owner'})
        with self.assertRaises(ValueError):
            p.verify_archive(self.output, changed)

    def test_rehashed_secret_file_still_rejected(self):
        value = b'{"nested":{"password":"do-not-export"}}'
        (self.output / 'RESULT.json').write_bytes(value)
        report = self.manifest()
        row = next(row for row in report['files'] if row['path'] == 'RESULT.json')
        row.update(bytes=len(value), sha256=p.digest(value))
        self.replace_manifest(report)
        self.reject_verify()

    def test_archive_root_link_and_dangling_link(self):
        for target in (self.output, self.root / 'missing'):
            link = self.root / 'linked-archive'
            link.symlink_to(target, target_is_directory=True)
            with self.assertRaises((ValueError, OSError)):
                p.verify_archive(link, CONTRACT)
            link.unlink()

    def test_manifest_symlink_and_hardlink(self):
        target = self.root / 'external-manifest.json'
        target.write_bytes((self.output / p.MANIFEST).read_bytes())
        manifest = self.output / p.MANIFEST
        manifest.unlink()
        manifest.symlink_to(target)
        self.reject_verify()
        manifest.unlink()
        os.link(target, manifest)
        self.reject_verify()


if __name__ == '__main__':
    unittest.main()
