"""Durable journal fault tests with temporary files and an in-memory CF API."""
from __future__ import annotations
from copy import deepcopy
import fcntl
import json
import os
from pathlib import Path
import stat
import tempfile
import threading
from unittest.mock import patch
import unittest

import test_cloudflare_dns as adapter
from app.certificates.dns_store import DNSCredentialStore, private_directory, _read, _write

from app.certificates import dns_journal as journal
from app.certificates.errors import CertificateError

ERROR = CertificateError
ZONE = adapter.ZONE
VALUE = adapter.VALUE
OTHER = adapter.OTHER
DOMAIN = 'example.test'
JOB = '1' * 32
TOKEN = 'fake_first_token_' + 'a' * 24
NEW_TOKEN = 'b' * 32


class Server:
    def __init__(self):
        self.records = {}; self.calls = []; self.tokens = []
        self.create_mode = None; self.delete_mode = None; self.authority_error = None
        self.reject_tokens = set(); self.on_create = None; self.on_read = None

    def factory(self, token):
        self.tokens.append(token)
        server = self
        class Client:
            def check(self, method, *args):
                server.calls.append((method, args))
                if token in server.reject_tokens: raise ERROR('DNS_PROVIDER_FORBIDDEN')
                if server.on_read is not None: server.on_read(method)
            def assert_authoritative_owner(self, zone, zone_name, owner, *, deadline=None):
                self.check('authority', zone, zone_name, owner)
                if server.authority_error: raise ERROR(server.authority_error)
            def create_txt(self, zone, name, value, ownership, *, deadline=None):
                self.check('post', zone, name, value, ownership)
                if server.create_mode == 'forbidden': raise ERROR('DNS_PROVIDER_FORBIDDEN')
                if server.on_create: server.on_create(ownership, value)
                if server.create_mode == 'timeout-before': raise ERROR('DNS_PROVIDER_WRITE_UNKNOWN')
                identity = f'{len(server.records)+10:032x}'
                record = dict(id=identity, zone_id=zone, type='TXT', name=name, content=value, comment=ownership)
                server.records[identity] = record
                if server.create_mode == 'timeout-after': raise ERROR('DNS_PROVIDER_WRITE_UNKNOWN')
                if server.create_mode == 'wrong-owner': return {**record, 'comment': 'v-ui:' + 'f' * 64}
                return deepcopy(record)
            def find_txt(self, zone, name, ownership, *, deadline=None):
                self.check('find', zone, name, ownership)
                return [deepcopy(row) for row in server.records.values()
                        if row['zone_id'] == zone and row['name'] == name and row['comment'] == ownership]
            def get_record(self, zone, identity, *, deadline=None):
                self.check('get', zone, identity)
                return deepcopy(server.records.get(identity))
            def delete_txt(self, zone, identity, *, deadline=None):
                self.check('delete', zone, identity)
                if server.delete_mode == 'timeout-before': raise ERROR('DNS_PROVIDER_WRITE_UNKNOWN')
                server.records.pop(identity, None)
                if server.delete_mode == 'timeout-after': raise ERROR('DNS_PROVIDER_WRITE_UNKNOWN')
        return Client()


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='vui-journal-'); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / 'certificates'; self.root.mkdir(mode=0o700)
        self.store = DNSCredentialStore(self.root)
        profile = self.store.create(label='fake', zone_name=DOMAIN, zone_id=ZONE, api_token=TOKEN)
        self.profile_id = profile['id']; self.revision = self.store.snapshot(self.profile_id)['revision']
        self.server = Server(); self.journal = self.instance()

    def instance(self):
        return journal.DNSChallengeJournal(self.root, store=self.store, client_factory=self.server.factory)

    def begin(self, job=JOB, **changes):
        args = dict(credential_id=self.profile_id, credential_revision=self.revision, domain=DOMAIN,
                    names=[DOMAIN, '*.' + DOMAIN]); args.update(changes)
        return self.journal.begin(job, **args)

    def private(self, job=JOB):
        with private_directory(self.journal.root) as directory: return _read(directory, job + '.json')

    def rewrite(self, value):
        with private_directory(self.journal.root) as directory: _write(directory, value['job_id'] + '.json', value, replace=True)

    def present(self, value=VALUE, identifier=DOMAIN):
        return self.journal.present(JOB, identifier, value)

    def failure(self, operation, code):
        with self.assertRaises(ERROR) as caught: operation()
        self.assertEqual(caught.exception.code, code)
        self.assertNotIn(TOKEN, str(caught.exception))
        return caught.exception

    def test_two_values_share_owner_without_touching_foreign_txt_and_duplicates_do_not_post(self):
        self.begin(); self.server.records['f' * 32] = adapter.record(id='f' * 32, zone_id=ZONE, comment='someone else')
        first = self.present(); second = self.present(OTHER, '*.' + DOMAIN)
        self.assertNotEqual(first, second); self.assertEqual(len(self.private()['entries']), 2)
        self.assertEqual(self.present(), first)
        self.assertEqual(sum(method == 'post' for method, _ in self.server.calls), 2)
        self.assertTrue(self.journal.cleanup(JOB))
        self.assertEqual(set(self.server.records), {'f' * 32})
        self.assertEqual(self.private()['phase'], 'cleaned')
        self.assertEqual(len([call for call in self.server.calls if call[0] == 'delete']), 2)

    def test_intent_is_durably_published_before_the_only_create(self):
        self.begin()
        def before_post(ownership, value):
            self.assertGreater(sync.call_count, 0)  # fsync happened before POST.
            record = self.private(); entry = record['entries'][0]
            self.assertEqual((entry['ownership'], entry['value'], entry['state']), (ownership, value, 'intent'))
            self.assertIsNone(entry['record_id'])
            self.assertEqual(stat.S_IMODE((self.journal.root / (JOB + '.json')).stat().st_mode), 0o600)
        self.server.on_create = before_post
        with patch.object(journal.os, 'fsync', wraps=os.fsync) as sync:
            self.present()
        self.assertGreater(sync.call_count, 0)
        self.assertEqual(self.private()['entries'][0]['state'], 'created')
        self.assertEqual(self.server.calls[0][0], 'authority')

    def test_preflight_delegation_failure_has_no_sent_intent_or_permanent_unknown_barrier(self):
        self.begin(); self.server.authority_error = 'DNS_DELEGATION_UNSUPPORTED'
        self.failure(self.present, 'DNS_DELEGATION_UNSUPPORTED')
        self.assertEqual(self.private()['entries'], [])
        self.assertFalse(any(method == 'post' for method, _ in self.server.calls))
        self.assertTrue(self.journal.cleanup(JOB))
        self.begin(job='2' * 32)  # No fictional unknown write to manually confirm.

    def test_unknown_post_without_match_preserves_barrier_and_never_reposts_after_restart(self):
        self.begin(); self.server.create_mode = 'timeout-before'
        self.failure(self.present, 'DNS_PROVIDER_WRITE_UNKNOWN')
        self.assertEqual(self.private()['entries'][0]['state'], 'unknown')
        self.assertFalse(self.instance().cleanup(JOB))
        self.assertEqual(self.instance().pending()[0]['phase'], 'cleanup_pending')
        self.failure(lambda: self.begin('2' * 32), 'DNS_CHALLENGE_OWNER_BUSY')
        self.failure(lambda: self.instance().present(JOB, DOMAIN, VALUE), 'DNS_CHALLENGE_NOT_ISSUING')
        self.assertEqual(sum(method == 'post' for method, _ in self.server.calls), 1)

    def test_unknown_created_record_is_recovered_then_only_owned_id_is_deleted(self):
        self.begin(); self.server.create_mode = 'timeout-after'
        self.failure(self.present, 'DNS_PROVIDER_WRITE_UNKNOWN')
        owned = next(iter(self.server.records))
        self.server.records['f' * 32] = adapter.record(id='f' * 32, zone_id=ZONE, comment='someone else')
        self.assertTrue(self.instance().cleanup(JOB))
        self.assertEqual([args[1] for method, args in self.server.calls if method == 'delete'], [owned])
        self.assertEqual(set(self.server.records), {'f' * 32})
        self.assertEqual(sum(method == 'post' for method, _ in self.server.calls), 1)

    def test_interrupted_intent_is_unknown_and_backup_validation_never_repairs_it(self):
        self.begin(); original_write = journal._write
        def fail_record_id(directory, name, value, **kwargs):
            if value.get('entries') and value['entries'][0]['state'] == 'created':
                raise OSError('fake durable ID write failed')
            return original_write(directory, name, value, **kwargs)
        with patch.object(journal, '_write', side_effect=fail_record_id):
            self.failure(self.present, 'DNS_JOURNAL_STORAGE_FAILED')
        self.assertEqual(self.private()['entries'][0]['state'], 'intent')
        raw = (self.journal.root / (JOB + '.json')).read_bytes()
        self.instance().validate_for_backup()
        self.assertEqual((self.journal.root / (JOB + '.json')).read_bytes(), raw)
        self.assertTrue(self.instance().cleanup(JOB))
        self.assertEqual(sum(method == 'post' for method, _ in self.server.calls), 1)

    def test_wrong_ownership_and_multiple_recovery_matches_never_delete(self):
        self.begin(); self.present(); identity = self.private()['entries'][0]['record_id']
        self.server.records[identity]['content'] = OTHER
        self.assertFalse(self.journal.cleanup(JOB))
        self.assertEqual(self.private()['error'], 'DNS_RECORD_OWNERSHIP_MISMATCH')
        self.assertFalse(any(method == 'delete' for method, _ in self.server.calls))
        # Exercise ambiguous lost-ID discovery separately, with one real owner
        # nonce deliberately duplicated by the fake provider.
        value = self.private(); value['entries'][0]['record_id'] = None; self.rewrite(value)
        self.server.records[identity]['content'] = VALUE
        self.server.records['e' * 32] = {**self.server.records[identity], 'id': 'e' * 32}
        self.assertFalse(self.journal.cleanup(JOB))
        self.assertEqual(self.private()['error'], 'DNS_CHALLENGE_AMBIGUOUS')
        self.assertFalse(any(method == 'delete' for method, _ in self.server.calls))

    def test_delete_unknown_preserves_known_id_and_next_get404_can_complete(self):
        self.begin(); self.present(); self.server.delete_mode = 'timeout-after'
        self.assertFalse(self.journal.cleanup(JOB))
        entry = self.private()['entries'][0]
        self.assertEqual(entry['state'], 'unknown'); self.assertIsNotNone(entry['record_id'])
        self.assertEqual(self.server.records, {})
        self.server.delete_mode = None
        self.assertTrue(self.instance().cleanup(JOB))
        self.assertEqual(sum(method == 'delete' for method, _ in self.server.calls), 1)

    def test_rotation_keeps_original_revision_and_disabled_original_still_cleans(self):
        self.begin(); self.present()
        state = self.store.list()[0]
        self.store.replace(self.profile_id, expected_revision=state['revision'], api_token=NEW_TOKEN)
        current = self.store.list()[0]
        self.store.set_enabled(self.profile_id, expected_revision=current['revision'], enabled=False)
        self.server.tokens.clear()
        self.assertTrue(self.journal.cleanup(JOB))
        self.assertEqual(self.server.tokens, [TOKEN])
        self.assertEqual(self.private()['credential_revision'], self.revision)

    def test_restored_profile_blocks_all_remote_cleanup_and_local_validation_is_safe(self):
        self.begin(); self.present(); self.store.suspend_restored()
        before = list(self.server.calls); tokens = list(self.server.tokens)
        self.assertFalse(self.instance().cleanup(JOB))
        self.assertEqual(self.private()['error'], 'DNS_CREDENTIAL_RESTORE_PENDING')
        self.assertEqual(self.server.calls, before); self.assertEqual(self.server.tokens, tokens)
        self.assertEqual(self.instance().validate_for_backup()[0]['credential_revision'], self.revision)
        self.failure(lambda: self.journal.authorize_recovery(JOB, self.private()['revision'], self.store.list()[0]['revision']), 'DNS_CREDENTIAL_RESTORE_PENDING')
        self.assertEqual(self.server.calls, before)

    def test_explicit_recovery_authority_uses_current_revision_for_cleanup_only(self):
        self.begin(); self.present(); original = self.private()
        state = self.store.list()[0]; self.store.replace(self.profile_id, expected_revision=state['revision'], api_token=NEW_TOKEN)
        current = self.store.snapshot(self.profile_id)
        self.server.reject_tokens.add(TOKEN)
        self.assertFalse(self.journal.cleanup(JOB))
        before = self.private()
        self.failure(lambda: self.journal.authorize_recovery(JOB, 'f' * 32, current['profile_revision']), 'DNS_CHALLENGE_REVISION_CONFLICT')
        self.failure(lambda: self.journal.authorize_recovery(JOB, before['revision'], 'f' * 32), 'DNS_CREDENTIAL_REVISION_CONFLICT')
        self.journal.authorize_recovery(JOB, before['revision'], current['profile_revision'], confirmed=True)
        authorized = self.private()
        self.assertEqual(authorized['credential_revision'], original['credential_revision'])
        self.assertEqual(authorized['entries'], before['entries'])
        self.assertEqual(authorized['recovery_credential_revision'], current['revision'])
        self.failure(self.present, 'DNS_CHALLENGE_NOT_ISSUING')
        self.assertTrue(self.journal.cleanup(JOB))
        self.assertEqual(self.server.tokens[-1], NEW_TOKEN)
        self.assertEqual(sum(method == 'post' for method, _ in self.server.calls), 1)

    def test_manual_confirmation_requires_cas_explicit_confirmation_and_empty_readback(self):
        self.begin(); self.server.create_mode = 'timeout-before'; self.failure(self.present, 'DNS_PROVIDER_WRITE_UNKNOWN')
        revision = self.private()['revision']
        self.failure(lambda: self.journal.confirm_absent(JOB, revision, confirmed=False), 'DNS_CHALLENGE_CONFIRMATION_REQUIRED')
        self.failure(lambda: self.journal.confirm_absent(JOB, 'f' * 32), 'DNS_CHALLENGE_REVISION_CONFLICT')
        result = self.journal.confirm_absent(JOB, revision, confirmed=True)
        self.assertEqual(result['phase'], 'manual_confirmed')
        self.assertEqual(result['error'], 'DNS_CHALLENGE_MANUAL_CONFIRMATION')
        self.assertEqual(self.journal.pending(), [])
        self.assertFalse(self.journal.cleanup(JOB))  # Never an automatic pass.
        self.begin('2' * 32)
        self.assertEqual(sum(method == 'post' for method, _ in self.server.calls), 1)

    def test_manual_confirmation_cannot_hide_an_existing_or_foreign_record(self):
        self.begin(); self.present(); revision = self.private()['revision']
        self.failure(lambda: self.journal.confirm_absent(JOB, revision), 'DNS_CHALLENGE_RECORD_STILL_PRESENT')
        identity = self.private()['entries'][0]['record_id']; self.server.records[identity]['comment'] = 'someone else'
        self.failure(lambda: self.journal.confirm_absent(JOB, revision), 'DNS_RECORD_OWNERSHIP_MISMATCH')
        self.assertFalse(any(method == 'delete' for method, _ in self.server.calls))

    def test_stop_and_shared_deadline_persist_pending_and_never_claim_cleanup_success(self):
        self.begin(); self.present(); stop = threading.Event(); stop.set()
        before = len(self.server.calls)
        self.assertFalse(self.journal.recover_all(stop))
        self.assertEqual(self.private()['error'], 'DNS_CHALLENGE_CANCELLED')
        self.assertEqual(len(self.server.calls), before)
        with patch.object(journal.time, 'monotonic', return_value=100):
            self.assertFalse(self.journal.cleanup(JOB, deadline=99))
        self.assertEqual(self.private()['error'], 'DNS_CHALLENGE_BUDGET_EXHAUSTED')
        self.assertEqual(len(self.server.calls), before)

    def test_cleanup_makes_progress_on_second_intent_even_when_first_is_unknown(self):
        self.begin(); self.present(VALUE); self.present(OTHER, '*.' + DOMAIN)
        value = self.private(); first = value['entries'][0]
        self.server.records.pop(first['record_id']); first.update(record_id=None, state='unknown')
        value['phase'] = 'cleanup_pending'; self.rewrite(value)
        self.assertFalse(self.journal.cleanup(JOB))
        self.assertEqual([entry['state'] for entry in self.private()['entries']], ['unknown', 'deleted'])

    def test_unexpected_error_payload_is_not_persisted_or_returned(self):
        self.begin()
        def secret_failure(*_): raise RuntimeError(TOKEN + ' private upstream body')
        self.server.on_create = secret_failure
        error = self.failure(self.present, 'DNS_CHALLENGE_OPERATION_FAILED')
        self.assertIsNone(error.__context__)
        self.assertEqual(self.private()['entries'][0]['state'], 'unknown')
        self.assertNotIn(TOKEN, (self.journal.root / (JOB + '.json')).read_text().replace(ZONE, 'zone-id'))
        self.assertNotIn('private upstream body', json.dumps(self.journal.pending()))

    def test_recovery_never_returns_success_while_an_unprocessed_job_remains(self):
        self.begin()
        first = self.private(); first.update(phase='cleanup_pending', error='DNS_CHALLENGE_CANCELLED')
        self.rewrite(first)
        self.begin('2' * 32, domain='other.example.test', names=['other.example.test'])
        self.journal.present('2' * 32, 'other.example.test', VALUE)
        stop = threading.Event(); stop.set()
        self.assertFalse(self.journal.recover_all(stop))
        self.assertEqual(self.private()['phase'], 'cleaned')
        self.assertEqual(self.private('2' * 32)['phase'], 'cleanup_pending')

    def test_manual_interrupted_intent_stays_manual_after_restart(self):
        self.begin(); self.server.create_mode = 'timeout-before'
        self.failure(self.present, 'DNS_PROVIDER_WRITE_UNKNOWN')
        value = self.private(); value['entries'][0].update(state='intent', error=None)
        value.update(phase='active', error=None); self.rewrite(value)
        result = self.journal.confirm_absent(JOB, value['revision'])
        self.assertEqual(result['phase'], 'manual_confirmed')
        self.assertFalse(self.instance().cleanup(JOB))
        self.assertEqual(self.instance().pending(), [])
        self.assertEqual(self.private()['entries'][0]['state'], 'unknown')

    def test_public_and_backup_views_contain_no_token_value_nonce_or_record_id(self):
        self.begin(); self.present(); private = self.private(); entry = private['entries'][0]
        public = self.journal.pending()[0]
        self.assertEqual(set(public), {'job_id','credential_id','zone_name','domain','phase','error','revision','created_at','updated_at','deadline','count'})
        references = self.journal.validate_for_backup()[0]
        self.assertEqual(set(references), {'job_id','credential_id','credential_revision','recovery_credential_revision',
                                           'provider','zone_id','zone_name'})
        output = json.dumps([public, references])
        for secret in (TOKEN, VALUE, entry['ownership'], entry['record_id']): self.assertNotIn(secret, output)

    def test_owner_lock_is_nonblocking_and_bounds_jobs_and_intents(self):
        self.begin()
        with private_directory(self.journal.root) as directory:
            fd = os.open('owner.lock', os.O_RDWR, dir_fd=directory)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.failure(self.journal.pending, 'DNS_JOURNAL_BUSY')
            finally: os.close(fd)
        with patch.object(journal, 'MAX_JOBS', 1):
            self.failure(lambda: self.begin('2' * 32, domain='other.example.test', names=['other.example.test']), 'DNS_JOURNAL_LIMIT_REACHED')
        self.present(); self.present(OTHER, '*.' + DOMAIN)
        third = adapter.base64.urlsafe_b64encode(b'Q' * 32).decode().rstrip('=')
        self.failure(lambda: self.present(third), 'DNS_CHALLENGE_LIMIT_REACHED')
        self.assertEqual(sum(method == 'post' for method, _ in self.server.calls), 2)

    def test_backup_scan_rejects_unknown_members_permissions_links_and_corrupt_identity(self):
        self.begin(); path = self.journal.root / (JOB + '.json'); original = path.read_bytes()
        path.chmod(0o644); self.failure(self.journal.validate_for_backup, 'DNS_JOURNAL_UNSAFE'); path.chmod(0o600)
        for raw in (original[:-2] + b',"schema":1}\n', b'{}', b'x' * 16385):
            path.write_bytes(raw)
            with self.assertRaises(ERROR): self.journal.validate_for_backup()
        path.write_bytes(original)
        external = self.root / 'external'; path.rename(external); path.symlink_to(external)
        with self.assertRaises(ERROR): self.journal.validate_for_backup()
        path.unlink(); os.link(external, path)
        with self.assertRaises(ERROR): self.journal.validate_for_backup()
        path.unlink(); path.write_bytes(original); path.chmod(0o600)
        extra = self.journal.root / 'unknown'; extra.write_text('preserve')
        with self.assertRaises(ERROR): self.journal.validate_for_backup()
        self.assertEqual(extra.read_text(), 'preserve'); self.assertEqual(self.server.calls, [])

    def test_offline_backup_accepts_restored_journal_without_creating_ephemeral_lock(self):
        self.begin(); lock = self.journal.root / 'owner.lock'; lock.unlink()
        path = self.journal.root / (JOB + '.json'); original = path.read_bytes()
        references = self.instance().validate_for_backup()
        self.assertEqual(references[0]['job_id'], JOB)
        self.assertFalse(lock.exists()); self.assertEqual(path.read_bytes(), original)
        self.assertEqual(self.server.calls, []); self.assertEqual(self.server.tokens, [])
        self.instance().pending()  # Runtime coordination creates and validates it.
        self.assertTrue(lock.exists()); self.assertEqual(stat.S_IMODE(lock.stat().st_mode), 0o600)
        path.chmod(0o644); lock.unlink()
        with self.assertRaises(ERROR): self.instance().validate_for_backup()
        self.assertFalse(lock.exists())

    def test_known_dns_write_permission_refusal_is_not_unknown_or_a_permanent_owner_barrier(self):
        self.begin(); self.server.create_mode = 'forbidden'
        self.failure(self.present, 'DNS_PROVIDER_FORBIDDEN')
        self.assertEqual(self.private()['entries'][0]['state'], 'rejected')
        self.assertTrue(self.journal.cleanup(JOB))
        self.assertEqual(self.journal.pending(), [])
        self.assertEqual(self.journal.view(JOB)['error'], 'DNS_PROVIDER_FORBIDDEN')
        self.assertEqual([method for method, _ in self.server.calls], ['authority', 'post'])

    def test_constructor_and_legacy_absence_are_local_and_make_no_state_or_calls(self):
        absent = self.root / 'absent'; absent.mkdir(mode=0o700)
        instance = journal.DNSChallengeJournal(absent, store=self.store, client_factory=self.server.factory)
        self.assertFalse(instance.root.exists()); self.assertEqual(instance.validate_for_backup(), [])
        self.assertEqual(instance.pending(), []); self.assertTrue(instance.recover_all(threading.Event()))
        self.assertFalse(instance.root.exists()); self.assertEqual(self.server.calls, [])


if __name__ == '__main__': unittest.main()
