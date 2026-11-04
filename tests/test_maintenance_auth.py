"""Synthetic ASGI/SQLite maintenance boundary tests; no browser or real core."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import database
from app.middleware.auth import cookie_name
from app.services import auth_service, maintenance_auth as maintenance
import main

ORIGIN = 'https://panel.example.test'
BROWSER = {'Origin': ORIGIN, 'X-VUI-Request': '1'}


class MaintenanceAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='vui-maintenance-')
        self.addCleanup(self.temp.cleanup)
        engine = create_engine('sqlite:///' + str(Path(self.temp.name) / 'test.db'),
                               connect_args={'check_same_thread': False, 'timeout': 20})
        self.addCleanup(engine.dispose)
        self.addCleanup(patch.stopall)
        patch.multiple(database, engine=engine,
                       SessionLocal=sessionmaker(bind=engine, autoflush=False)).start()
        patch.dict(os.environ, {'VUI_PUBLIC_ORIGIN': ORIGIN}).start()
        database.Base.metadata.create_all(engine)
        patch.object(main.core_manager, 'inbound_routing_status', return_value={}).start()
        patch.object(main.core_manager, 'validate_inbound_change', return_value=None).start()
        patch('app.api.inbounds.apply_checked', return_value={'applied': True, 'running': False}).start()
        with database.SessionLocal() as db:
            # Existing synthetic account, never the provisioning/install path.
            db.add(database.User(username='tester', password_hash=auth_service.DUMMY_HASH, is_active=True))
            db.commit()
            self.owner = db.query(database.User).one().id
            self.admin_token = auth_service.secrets.token_urlsafe(32)
            db.add(auth_service.AdminSession(token_hash=auth_service.digest(self.admin_token),
                user_id=self.owner, credential_stamp=auth_service.digest(auth_service.DUMMY_HASH),
                created_at=int(time.time()), expires_at=int(time.time()) + 3600))
            db.commit()
        self.client = TestClient(main.app, base_url=ORIGIN, client=('127.0.0.1', 55001))
        self.addCleanup(self.client.close)
        self.client.cookies.set(cookie_name(), self.admin_token)

    def issue(self, scopes=None, seconds=1800):
        return maintenance.create_grant(self.owner, scopes or ['diagnose'], seconds)

    def bearer(self, scopes=None):
        record, grant = self.issue(scopes)
        exchanged = self.client.post('/api/maintenance/exchange', json={'grant': grant},
                                     headers={'X-VUI-Request': '1'})
        self.assertEqual(exchanged.status_code, 200, exchanged.text)
        return record, {'Authorization': 'Bearer ' + exchanged.json()['token'], 'X-VUI-Request': '1'}

    def test_admin_creation_one_time_link_and_hash_only_storage(self):
        response = self.client.post('/api/maintenance/grants', json={}, headers=BROWSER)
        self.assertEqual(response.status_code, 201, response.text)
        data = response.json()
        token = data['link'].split('#grant=')[1]
        self.assertTrue(data['link'].startswith(ORIGIN + '/maintenance#grant='))
        self.assertNotIn('?', data['link'])
        self.assertEqual(data['expires_at'] - data['created_at'], 1800)
        with database.SessionLocal() as db:
            grant = db.get(maintenance.MaintenanceGrant, data['id'])
            self.assertEqual(grant.token_hash, maintenance.digest(token))
            self.assertNotIn(token, json.dumps(grant.__dict__, default=str))
        listed = self.client.get('/api/maintenance/grants')
        self.assertNotIn(token, listed.text)
        self.assertNotIn('link', listed.json()[0])
        exchanged = self.client.post('/api/maintenance/exchange', json={'grant': token}, headers=BROWSER)
        self.assertEqual(exchanged.status_code, 200)
        self.assertNotIn('set-cookie', exchanged.headers)
        session = exchanged.json()['token']
        with database.SessionLocal() as db:
            row = db.query(maintenance.MaintenanceSession).one()
            self.assertEqual(row.token_hash, maintenance.digest(session))
            self.assertNotIn(session, json.dumps(row.__dict__, default=str))
        replay = self.client.post('/api/maintenance/exchange', json={'grant': token}, headers=BROWSER)
        self.assertEqual(replay.status_code, 401)
        self.assertNotIn(token, replay.text)
        self.assertIn('no-store', replay.headers['cache-control'])

    def test_atomic_exchange_across_sessions(self):
        _, token = self.issue()
        def attempt(_):
            try:
                return maintenance.exchange(token, 'test-client')['token']
            except HTTPException as exc:
                return exc.status_code
        with ThreadPoolExecutor(max_workers=6) as pool:
            values = list(pool.map(attempt, range(6)))
        self.assertEqual(sum(type(value) is str for value in values), 1)
        self.assertEqual(values.count(401), 5)

    def test_revoke_expiry_password_reset_and_disabled_admin(self):
        for change in ['revoke', 'expiry', 'password', 'disabled']:
            with self.subTest(change=change):
                record, headers = self.bearer()
                self.assertEqual(self.client.get('/api/maintenance/session', headers=headers).status_code, 200)
                if change == 'revoke':
                    maintenance.revoke_grant(self.owner, record['id'])
                else:
                    with database.SessionLocal() as db:
                        if change == 'expiry':
                            db.get(maintenance.MaintenanceGrant, record['id']).expires_at = int(time.time()) - 1
                        elif change == 'password':
                            db.get(database.User, self.owner).password_hash = auth_service.DUMMY_HASH + '-changed'
                        else:
                            db.get(database.User, self.owner).is_active = False
                        db.commit()
                self.assertEqual(self.client.get('/api/maintenance/session', headers=headers).status_code, 401)
                with database.SessionLocal() as db:
                    db.get(database.User, self.owner).password_hash = auth_service.DUMMY_HASH
                    db.get(database.User, self.owner).is_active = True
                    db.commit()

    def test_grant_bounds_persistent_exchange_limit_and_safe_audit(self):
        for seconds in [0, 59, 7201, True]:
            with self.assertRaises(HTTPException):
                self.issue(seconds=seconds)
        record, token = self.issue(seconds=7200)
        self.assertEqual(record['expires_at'] - record['created_at'], 7200)
        for _ in range(12):
            with self.assertRaises(HTTPException) as invalid:
                maintenance.exchange('invalid', 'dedicated-client')
            self.assertEqual(invalid.exception.status_code, 401)
        with self.assertRaises(HTTPException) as limited:
            maintenance.exchange(token, 'dedicated-client')
        self.assertEqual(limited.exception.status_code, 429)
        with database.SessionLocal() as db:
            self.assertGreater(db.query(maintenance.MaintenanceBucket).count(), 0)
        maintenance.audit(record['id'], '/attacker/path?token=' + token, 403, token)
        rows = maintenance.audit_rows()
        self.assertEqual(rows[0]['operation'], 'denied')
        self.assertIsNone(rows[0]['task_id'])
        self.assertNotIn(token, json.dumps(rows))
        for invalid_id in (token, 'vui_ms_' + 'A' * 43, '../backup', {'not':'a task'}):
            maintenance.audit(record['id'], 'GET /api/maintenance/tasks', 200, invalid_id)
            self.assertIsNone(maintenance.audit_rows()[0]['task_id'])

    def test_default_deny_precise_methods_and_no_admin_cookie_fallback(self):
        _, headers = self.bearer()
        for method, path, payload in [
                ('POST', '/api/inbounds', {'port': 1234, 'protocol': 'vless'}),
                ('GET', '/api/inbounds/1/editor', None),
                ('GET', '/api/subscription/raw', None),
                ('PUT', '/api/proxy/settings', {}),
                ('POST', '/api/maintenance/grants', {}),
                ('GET', '/api/maintenance/grants', None),
                ('DELETE', '/api/maintenance/grants/1', None),
                ('POST', '/api/auth/update', {}),
                ('GET', '/api/auth/me', None),
                ('GET', '/openapi.json', None),
                ('GET', '/api/files/list_files', None),
                ('POST', '/api/xray/inbounds', {}),
                ('GET', '/api/certificates/' + 'a' * 32 + '/fullchain.pem', None),
                ('GET', '/api/maintenance/session/extra', None),
                ('POST', '/api/maintenance/session', {})]:
            with self.subTest(method=method, path=path):
                result = self.client.request(method, path, json=payload, headers=headers)
                self.assertEqual(result.status_code, 403, result.text)
        self.assertEqual(self.client.get('/api/inbounds', headers=headers).status_code, 200)
        bad = {**headers, 'Authorization': 'Bearer invalid'}
        self.assertEqual(self.client.get('/api/inbounds', headers=bad).status_code, 401)

    def test_browser_origin_and_api_csrf_boundary_query_and_body_limits(self):
        _, headers = self.bearer()
        for changed in [{**headers, 'Origin': 'https://evil.example'},
                        {k: v for k, v in headers.items() if k != 'X-VUI-Request'}]:
            self.assertEqual(self.client.get('/api/maintenance/session', headers=changed).status_code, 403)
        self.assertEqual(self.client.get('/api/maintenance/session', headers={**headers, 'Origin': ORIGIN}).status_code, 200)
        self.assertEqual(self.client.post('/api/maintenance/grants', json={}, headers={'X-VUI-Request': '1'}).status_code, 403)
        self.assertEqual(self.client.get('/api/maintenance/session?token=fake', headers=headers).status_code, 422)
        self.assertEqual(self.client.request('GET', '/api/maintenance/session', content=b'x', headers=headers).status_code, 413)
        duplicate = self.client.post('/api/maintenance/tasks', content=b'{"action":"status","action":"stop"}',
            headers={**headers, 'Content-Type': 'application/json'})
        self.assertEqual(duplicate.status_code, 422)
        oversized = self.client.post('/api/maintenance/tasks', content=b' ' * 65537,
            headers={**headers, 'Content-Type': 'application/json'})
        self.assertEqual(oversized.status_code, 413)

    def test_nodes_payload_requires_relay_certificate_scopes_and_revision(self):
        _, headers = self.bearer(['nodes'])
        for payload in [{'routing_mode': 'direct'}, {'settings': {}}, {'stream_settings': {}},
                        {'profile': {'key_path': '/private/key'}}, {'certificate_id': 'a' * 32}]:
            result = self.client.put('/api/inbounds/1', json={**payload, 'expected_instance_revision': 'a' * 64}, headers=headers)
            self.assertEqual(result.status_code, 403, result.text)
        self.assertEqual(self.client.put('/api/inbounds/1', json={'enable': False}, headers=headers).status_code, 428)
        self.assertEqual(self.client.delete('/api/inbounds/1', headers=headers).status_code, 403)
        _, share = self.bearer(['share'])
        self.assertEqual(self.client.post('/api/subscriptions', json={}, headers=share).status_code, 428)
        self.assertEqual(self.client.delete('/api/subscriptions/1', headers=share).status_code, 403)
        _, destructive = self.bearer(['destructive'])
        self.assertEqual(self.client.post('/api/inbounds', json={}, headers=destructive).status_code, 403)
        self.assertEqual(self.client.post('/api/inbounds/1/delete', json={}, headers=destructive).status_code, 428)

    def test_task_actions_are_separate_scopes_and_payload_is_fixed(self):
        from app.services import maintenance_tasks
        with patch.object(maintenance_tasks, 'submit_task', return_value={'id': 'mt_fake', 'state': 'queued'}) as submit:
            for scopes, action, expected in [(['diagnose'], 'status', 202), (['diagnose'], 'restart', 403),
                    (['operate'], 'restart', 202), (['operate'], 'upgrade', 403),
                    (['upgrade'], 'upgrade', 202), (['upgrade'], 'restart', 403),
                    (['destructive'], 'upgrade', 403)]:
                _, headers = self.bearer(scopes)
                self.assertEqual(self.client.post('/api/maintenance/tasks', json={'action': action}, headers=headers).status_code, expected)
            _, headers = self.bearer(['operate', 'upgrade'])
            for payload in [{'action': 'shell'}, {'action': 'upgrade', 'url': 'https://evil'},
                            {'action': 'restart', 'unit': 'ssh'}, {'action': 'backup', 'path': '/'}]:
                before = submit.call_count
                self.assertEqual(self.client.post('/api/maintenance/tasks', json=payload, headers=headers).status_code, 422)
                self.assertEqual(submit.call_count, before)

    def test_catalog_routes_match_actual_registered_methods(self):
        paths = main.app.openapi()['paths']
        for entry in maintenance.CAPABILITIES:
            self.assertIn(entry['path'], paths)
            self.assertIn(entry['method'].lower(), paths[entry['path']])
        _, headers = self.bearer(['nodes'])
        catalog = self.client.get('/api/maintenance/catalog', headers=headers).json()['operations']
        self.assertTrue(any(item['path'].endswith('/editor') for item in catalog))
        self.assertTrue(any(item['path'] == '/api/maintenance/session' for item in catalog))
        self.assertFalse(any(item['scope'] == 'destructive' for item in catalog))
        _, headers = self.bearer(['certificates', 'nodes'])
        old_bind = self.client.post('/api/certificates/' + 'a' * 32 + '/bind-inbound', json={'inbound_id': 1}, headers=headers)
        self.assertEqual(old_bind.status_code, 403)
        result = self.client.post('/api/certificates', json={'accept_terms': True}, headers=headers)
        self.assertEqual(result.status_code, 409)

    def test_real_node_crud_and_explicit_share_reuse_existing_handlers(self):
        record, headers = self.bearer(['diagnose', 'nodes', 'share', 'relay'])
        created = self.client.post('/api/inbounds', json={'core': 'sing-box', 'protocol': 'shadowsocks',
            'port': 24001, 'remark': 'synthetic node'}, headers=headers)
        self.assertEqual(created.status_code, 200, created.text)
        node = created.json()['inbound']
        self.assertNotIn('password', node)
        editor = self.client.get('/api/inbounds/' + str(node['id']) + '/editor', headers=headers)
        self.assertEqual(editor.status_code, 200, editor.text)
        revision = editor.json()['mutation_revision']
        edited = self.client.put('/api/inbounds/' + str(node['id']), json={
            'remark': 'edited through maintenance', 'routing_mode': 'direct',
            'expected_instance_revision': revision}, headers=headers)
        self.assertEqual(edited.status_code, 200, edited.text)
        self.assertEqual(edited.json()['inbound']['routing_mode'], 'direct')
        exported = self.client.get('/api/subscription/link/' + str(node['id']) + '?host=node.example.test', headers=headers)
        self.assertEqual(exported.status_code, 200, exported.text)
        self.assertTrue(exported.text.startswith('ss://'))
        # The middleware cannot weaken the existing instance check.
        conflict = self.client.put('/api/inbounds/' + str(node['id']), json={
            'remark': 'must not save', 'expected_instance_revision': '0' * 64}, headers=headers)
        self.assertEqual(conflict.status_code, 409)
        with database.SessionLocal() as db:
            self.assertEqual(db.get(database.Inbound, node['id']).remark, 'edited through maintenance')
        maintenance.revoke_grant(self.owner, record['id'])
        self.assertEqual(self.client.get('/api/subscription/link/' + str(node['id']), headers=headers).status_code, 401)

    def test_relay_reuses_exact_if_match_before_store_mutation(self):
        _, headers = self.bearer(['diagnose', 'relay'])
        store = Mock()
        store.read_snapshot.return_value = {'revision': 'a' * 64, 'settings': {}}
        store.save_draft.return_value = {'revision': 'b' * 64, 'settings': {}}
        payload = {'local_proxy_enabled': False, 'relay_enabled': False,
                   'selected_upstream_id': None, 'local_port': 1080, 'local_username': ''}
        with patch('app.api.proxy.get_store', return_value=store):
            read = self.client.get('/api/proxy/settings', headers=headers)
            self.assertEqual(read.status_code, 200)
            self.assertEqual(read.headers['etag'], '"' + 'a' * 64 + '"')
            missing = self.client.put('/api/proxy/settings', json=payload, headers=headers)
            self.assertEqual(missing.status_code, 428)
            store.save_draft.assert_not_called()
            written = self.client.put('/api/proxy/settings', json=payload,
                headers={**headers, 'If-Match': read.headers['etag']})
            self.assertEqual(written.status_code, 200, written.text)
            self.assertEqual(store.save_draft.call_args.args[1], 'a' * 64)
            self.assertEqual(written.headers['etag'], '"' + 'b' * 64 + '"')

    def test_restore_is_destructive_fixed_backup_id_only(self):
        from app.services import maintenance_tasks
        with patch.object(maintenance_tasks, 'submit_task', return_value={'id': 'a' * 32, 'state': 'queued'}) as submit:
            _, operate = self.bearer(['operate', 'upgrade'])
            self.assertEqual(self.client.post('/api/maintenance/tasks', json={'action': 'restore',
                'backup_id': 'b' * 32}, headers=operate).status_code, 403)
            _, destructive = self.bearer(['destructive'])
            good = self.client.post('/api/maintenance/tasks', json={'action': 'restore',
                'backup_id': 'b' * 32}, headers=destructive)
            self.assertEqual(good.status_code, 202, good.text)
            submit.assert_called_once_with('restore', backup_id='b' * 32)
            for payload in [{'action': 'restore'}, {'action': 'restore', 'backup_id': '../private'},
                    {'action': 'restore', 'backup_id': 'b' * 32, 'path': '/tmp/x.zip'},
                    {'action': 'restore', 'backup_id': 'b' * 32, 'sha256': '0' * 64},
                    {'action': 'backup', 'backup_id': 'b' * 32}]:
                before = submit.call_count
                self.assertEqual(self.client.post('/api/maintenance/tasks', json=payload,
                    headers=destructive if payload['action'] == 'restore' else operate).status_code, 422)
                self.assertEqual(submit.call_count, before)
            records = maintenance.audit_rows()
            self.assertTrue(any(row['task_id'] == 'a' * 32 for row in records))

    def test_audit_retention_is_bounded_and_schema_is_metadata_only(self):
        now = int(time.time())
        with database.SessionLocal() as db:
            db.add_all([maintenance.MaintenanceAudit(operation='denied', status=403,
                created_at=now, grant_id=None) for _ in range(2005)])
            db.commit()
        maintenance.audit(None, 'denied', 403)
        with database.SessionLocal() as db:
            self.assertEqual(db.query(maintenance.MaintenanceAudit).count(), 2000)
        self.assertEqual(set(maintenance.audit_rows()[0]),
            {'id', 'grant_id', 'operation', 'created_at', 'status', 'task_id'})

    def test_implicit_certificate_unbind_checked_inside_guarded_node_transaction(self):
        _, headers = self.bearer(['nodes'])
        created = self.client.post('/api/inbounds', json={'core': 'sing-box',
            'protocol': 'shadowsocks', 'port': 24002}, headers=headers)
        self.assertEqual(created.status_code, 200, created.text)
        node = created.json()['inbound']
        manager = Mock()
        manager.binding.return_value = 'a' * 32
        with patch('app.certificates.manager.get_manager', return_value=manager):
            result = self.client.put('/api/inbounds/' + str(node['id']), json={
                'profile': {'security': 'none'}, 'expected_instance_revision': node['mutation_revision']}, headers=headers)
        self.assertEqual(result.status_code, 403, result.text)
        manager.unbind.assert_not_called()
        with database.SessionLocal() as db:
            self.assertEqual(db.get(database.Inbound, node['id']).stream_settings, {})

    def test_manual_tls_remote_editor_preserves_server_material_without_exposing_paths(self):
        for core, transport in [('sing-box', 'direct'), ('xray', 'raw')]:
            with self.subTest(core=core):
                manager = Mock(); manager.binding.return_value = None
                with patch('app.certificates.manager.get_manager', return_value=manager):
                    created = self.client.post('/api/inbounds', headers=BROWSER, json={
                        'core': core, 'protocol': 'vless', 'port': 24013 if core == 'xray' else 24012,
                        'profile': {'security': 'tls', 'transport': transport, 'server_name': 'old.example.test',
                            'certificate_path': '/private/fake/cert.pem', 'key_path': '/private/fake/key.pem'}})
                    self.assertEqual(created.status_code, 200, created.text)
                    identity = created.json()['inbound']['id']
                    _, headers = self.bearer(['nodes'])
                    editor = self.client.get(f'/api/inbounds/{identity}/editor', headers=headers)
                    self.assertEqual(editor.status_code, 200, editor.text)
                    value = editor.json(); profile = value['profile']
                    self.assertNotIn('certificate_path', profile); self.assertNotIn('key_path', profile)
                    profile['server_name'] = 'new.example.test'
                    edited = self.client.put(f'/api/inbounds/{identity}', headers=headers, json={
                        'profile': profile, 'expected_instance_revision': value['mutation_revision']})
                    self.assertEqual(edited.status_code, 200, edited.text)
                    self.assertNotIn('/private/', edited.text)
                    with database.SessionLocal() as db:
                        row = db.get(database.Inbound, identity)
                        stream = row.stream_settings
                        tls = stream['tlsSettings'] if core == 'xray' else stream['tls']
                        cert = tls['certificates'][0] if core == 'xray' else tls
                        self.assertEqual(cert['certificateFile' if core == 'xray' else 'certificate_path'], '/private/fake/cert.pem')
                        self.assertEqual(cert['keyFile' if core == 'xray' else 'key_path'], '/private/fake/key.pem')
                        self.assertEqual(tls['serverName' if core == 'xray' else 'server_name'], 'new.example.test')
