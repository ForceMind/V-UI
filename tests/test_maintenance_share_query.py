"""Real ASGI/temporary SQLite regressions; fake node data, no core or network."""
import base64
import unittest

from app.middleware.auth import cookie_name
from app.models import database
from app.services.inbound_instance_revision import content_revision, mutation_revision
from tests import test_maintenance_auth as maintenance_fixture


FAKE_UUID = '11111111-1111-4111-8111-111111111111'
CHANGED_UUID = '22222222-2222-4222-8222-222222222222'
HOST = 'vpn.example.test'


class MaintenanceShareQueryTests(unittest.TestCase):
    # Reuse fixture methods without inheriting/discovering its unrelated tests.
    setUp = maintenance_fixture.MaintenanceAuthorizationTests.setUp
    issue = maintenance_fixture.MaintenanceAuthorizationTests.issue
    bearer = maintenance_fixture.MaintenanceAuthorizationTests.bearer

    def create_node(self):
        with database.SessionLocal() as db:
            node = database.Inbound(user_id=self.owner, core='sing-box', protocol='vless',
                port=10443, remark='synthetic share node', enable=True, expiry_time=0,
                settings={'users': [{'uuid': FAKE_UUID}]},
                stream_settings={'tls': {'enabled': True, 'server_name': HOST,
                    'certificate_path': '/fake/share-cert.pem', 'key_path': '/fake/share-key.pem'}})
            db.add(node)
            db.commit()
            return node.id, self.conditions(node)

    @staticmethod
    def conditions(node):
        return {'expected_instance_revision': mutation_revision(node),
                'expected_content_revision': content_revision(node)}

    def get_link(self, node_id, headers, params=None):
        return self.client.get(f'/api/subscription/link/{node_id}',
                               params={'host': HOST} if params is None else params, headers=headers)

    def assert_rejected(self, response, status):
        self.assertEqual(response.status_code, status, response.text)
        for secret in (FAKE_UUID, CHANGED_UUID, 'vless://', '/fake/share-cert.pem', '/fake/share-key.pem'):
            self.assertNotIn(secret, response.text)

    def test_exact_link_get_accepts_both_conditions_and_legacy_selectors(self):
        node_id, conditions = self.create_node()
        _, headers = self.bearer(['share'])
        for params in ({'host': HOST, **conditions}, {'host': HOST},
                       {'host': HOST, 'core': 'sing-box'}, {},
                       {'host': HOST, 'expected_instance_revision': conditions['expected_instance_revision']}):
            with self.subTest(params=params):
                response = self.get_link(node_id, headers, params)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertTrue(response.text.startswith('vless://' + FAKE_UUID + '@'))
                self.assertIn('security=tls', response.text)
                self.assertIn('no-store', response.headers['cache-control'])
                self.assertNotIn('/fake/', response.text)
        raw = self.client.get('/api/subscription/raw',
            params={'host': HOST, 'core': 'sing-box'}, headers=headers)
        self.assertEqual(raw.status_code, 200, raw.text)
        self.assertTrue(base64.b64decode(raw.text).decode().startswith('vless://' + FAKE_UUID + '@' + HOST))

    def test_replaced_instance_rejects_old_conditions_before_export(self):
        node_id, before = self.create_node()
        _, headers = self.bearer(['share'])
        with database.SessionLocal() as db:
            current = db.get(database.Inbound, node_id)
            values = {column.name: getattr(current, column.name)
                      for column in database.Inbound.__table__.columns}
            db.delete(current)
            db.flush()
            values.update(instance_id='f' * 32, settings={'users': [{'uuid': CHANGED_UUID}]})
            replacement = database.Inbound(**values)
            db.add(replacement)
            db.commit()
            after = self.conditions(replacement)
        self.assertNotEqual(before['expected_instance_revision'], after['expected_instance_revision'])
        self.assert_rejected(self.get_link(node_id, headers, {'host': HOST, **before}), 409)
        response = self.get_link(node_id, headers, {'host': HOST, **after})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn(CHANGED_UUID, response.text)

    def test_same_instance_credential_or_configuration_change_rejects_stale_content(self):
        _, headers = self.bearer(['share'])
        for field, value in (('settings', {'users': [{'uuid': CHANGED_UUID}]}), ('port', 11443)):
            with self.subTest(field=field):
                node_id, before = self.create_node()
                with database.SessionLocal() as db:
                    current = db.get(database.Inbound, node_id)
                    setattr(current, field, value)
                    db.commit()
                    after = self.conditions(current)
                self.assertEqual(before['expected_instance_revision'], after['expected_instance_revision'])
                self.assertNotEqual(before['expected_content_revision'], after['expected_content_revision'])
                self.assert_rejected(self.get_link(node_id, headers, {'host': HOST, **before}), 409)
                self.assertEqual(self.get_link(node_id, headers, {'host': HOST, **after}).status_code, 200)

    def test_malformed_conditions_and_content_without_instance_remain_invalid(self):
        node_id, conditions = self.create_node()
        _, headers = self.bearer(['share'])
        for key in conditions:
            for malformed in ('', 'a' * 63, 'a' * 65, 'A' * 64, 'g' * 64, 'true', 'null'):
                with self.subTest(key=key, malformed=malformed):
                    self.assert_rejected(self.get_link(node_id, headers,
                        {'host': HOST, **conditions, key: malformed}), 422)
        self.assert_rejected(self.get_link(node_id, headers,
            {'host': HOST, 'expected_content_revision': conditions['expected_content_revision']}), 422)

    def test_duplicate_unknown_and_query_credentials_remain_rejected(self):
        node_id, conditions = self.create_node()
        _, headers = self.bearer(['share'])
        base = [('host', HOST), ('core', 'sing-box'), *conditions.items()]
        for key, value in base:
            with self.subTest(duplicate=key):
                self.assert_rejected(self.get_link(node_id, headers, [*base, (key, value)]), 422)
        for key in ('unknown', 'token', 'access_token', 'grant', 'authorization'):
            with self.subTest(query=key):
                response = self.get_link(node_id, headers, [*base, (key, headers['Authorization'])])
                self.assert_rejected(response, 422)
                self.assertNotIn(headers['Authorization'].removeprefix('Bearer '), response.text)

    def test_conditions_are_not_enabled_for_other_exports_or_methods(self):
        node_id, conditions = self.create_node()
        _, headers = self.bearer(['share'])
        for key, value in conditions.items():
            for method, path in (
                ('GET', '/api/subscription/raw'),
                ('GET', '/api/subscription/mihomo.yaml'),
                ('GET', '/api/subscription/mihomo-warnings'),
                ('GET', '/api/subscription/sing-box.json'),
                ('POST', f'/api/subscription/upstream/{node_id}'),
                ('GET', f'/api/subscription/upstream/{node_id}'),
                ('POST', f'/api/subscription/link/{node_id}'),
                ('HEAD', f'/api/subscription/link/{node_id}'),
                ('GET', f'/api/subscription/link/{node_id}/extra'),
                ('GET', '/api/subscription/link/01')):
                with self.subTest(key=key, method=method, path=path):
                    self.assert_rejected(self.client.request(method, path,
                        params={key: value}, headers=headers), 422)

    def test_share_permission_and_request_source_still_apply_with_admin_cookie(self):
        node_id, conditions = self.create_node()
        _, headers = self.bearer(['share'])
        params = {'host': HOST, **conditions}
        self.client.cookies.clear()
        self.assertEqual(self.get_link(node_id, headers, params).status_code, 200)
        self.client.cookies.set(cookie_name(), self.admin_token)
        _, denied = self.bearer(['diagnose'])
        self.assert_rejected(self.get_link(node_id, denied, params), 403)
        self.assert_rejected(self.get_link(node_id,
            {**headers, 'Authorization': 'Bearer invalid'}, params), 401)
        for changed in ({**headers, 'Origin': 'https://evil.example'},
                        {key: value for key, value in headers.items() if key != 'X-VUI-Request'}):
            with self.subTest(headers=changed):
                self.assert_rejected(self.get_link(node_id, changed, params), 403)
