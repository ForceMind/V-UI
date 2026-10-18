"""Pure selected-node export boundaries with explicit fake runtimes."""
import copy
import unittest
from unittest.mock import patch

import yaml

from app.services.proxy_export import StrictExportError
from app.services.proxy_store import ProxyConflict
from app.services.validated_export import share_link, validated_node
from tests.test_proxy_apply import row
from tests.test_proxy_exports import ExportFixture
from tests.test_proxy_store import FAKE_UUID, FAKE_UUID_2, writable_settings

DIRECT_UUID = '55555555-5555-4555-8555-555555555555'


class InboundRoutingExportTests(ExportFixture, unittest.TestCase):
    def setUp(self):
        self.setup_export()
        # Follow the supported first-activation path: commit the selected
        # upstream at 00 before introducing an enabled explicit relay.
        snapshot = self.store.read_snapshot()
        settings = writable_settings(snapshot)
        settings['relay_enabled'] = False
        saved = self.store.save_draft(settings, snapshot['revision'])
        self.coordinator.apply(saved['revision'], 'initial-selected-00')
        self.rows[0].routing_mode = 'relay'
        direct = row(2, 25444)
        direct.routing_mode = 'direct'
        direct.remark = 'ordinary direct'
        direct.settings['users'][0]['uuid'] = DIRECT_UUID
        self.rows.append(direct)

    def apply_modes(self, global_relay=False, label='mixed-export'):
        # This is the real prospective mutation/fence entry point, not a
        # hand-written state.json that bypasses saved-source validation.
        self.coordinator.validate_inbound_change(self.rows)
        snapshot = self.store.read_snapshot()
        settings = writable_settings(snapshot)
        settings['relay_enabled'] = global_relay
        saved = self.store.save_draft(settings, snapshot['revision'])
        self.rev = saved['revision']
        checked = self.coordinator.check(self.rev)
        self.assertFalse(checked['applied'])
        result = self.coordinator.apply(self.rev, label,
            expected_source_fingerprint=checked['source_fingerprint'])
        self.assertTrue(result['applied'])
        return result

    def test_global_off_explicit_relay_exports_selected_ingress_only(self):
        self.apply_modes()
        self.assertFalse(self.store.load_active()['settings']['relay_enabled'])
        for kind in ('sing-box', 'mihomo'):
            exported = self.exported(kind)
            self.assertIn(FAKE_UUID, exported.content)
            for forbidden in (FAKE_UUID_2, DIRECT_UUID, 'routing_mode', 'source_fingerprint'):
                self.assertNotIn(forbidden, exported.content)
            config = yaml.safe_load(exported.content)
            node = config['outbounds'][0] if kind == 'sing-box' else config['proxies'][0]
            self.assertEqual(node['server'], '8.8.8.8')
            self.assertEqual(node.get('port', node.get('server_port')), self.rows[0].port)
            if kind == 'mihomo':
                self.assertIs(node['udp'], False)
            else:
                self.assertEqual(node['network'], 'tcp')

    def test_explicit_direct_is_never_a_strict_relay_even_global_on(self):
        self.apply_modes(True)
        for kind in ('sing-box', 'mihomo'):
            with self.assertRaisesRegex(StrictExportError, 'RELAY_NOT_ACTIVE'):
                self.exported(kind, inbound=2)
            self.assertEqual(self.exported(kind).revision, self.rev)

    def test_same_config_mode_edit_cannot_export_old_applied_identity(self):
        self.rows[0].routing_mode = 'inherit'
        self.apply_modes(True)
        old = self.coordinator.compile_active(self.rows).config
        self.rows[0].routing_mode = 'relay'
        # Both modes route via the same upstream with global on, but the saved
        # intent has changed and cannot borrow the previous applied identity.
        with self.assertRaisesRegex(StrictExportError, 'STATE_MISMATCH|RUNTIME_UNAVAILABLE'):
            self.exported()
        self.apply_modes(True, 'apply-explicit-intent')
        self.assertEqual(old, self.coordinator.compile_active(self.rows).config)
        self.assertEqual(self.exported().revision, self.rev)

    def test_post_read_context_change_with_same_proxy_core_revision_rejects(self):
        self.apply_modes()
        original = self.store.load_active
        calls = 0
        def active(*args, **kwargs):
            nonlocal calls
            value = original(*args, **kwargs)
            calls += 1
            # compile_active performs another active read. Only mutate the
            # final reread, after actual config/identity/runtime checks.
            if calls == 3:
                value = copy.deepcopy(value)
                value['runtime_context']['policy_identity'] = 'f' * 64
            return value
        with patch.object(self.store, 'load_active', side_effect=active):
            with self.assertRaises(ProxyConflict):
                self.exported()
        self.assertEqual(calls, 3)

    def test_ordinary_protocol_export_is_mode_agnostic_and_preserves_udp_bytes(self):
        selected = self.rows[0]
        selected.routing_mode = 'inherit'
        baseline = (validated_node(selected, '8.8.8.8'), share_link(selected, '8.8.8.8'))
        self.assertIs(baseline[0]['udp'], True)
        self.assertEqual(baseline[0]['packet-encoding'], 'xudp')
        for mode in ('direct', 'relay'):
            selected.routing_mode = mode
            self.assertEqual((validated_node(selected, '8.8.8.8'),
                              share_link(selected, '8.8.8.8')), baseline)
        # Pure protocol exports make no applied-route declaration. Runtime
        # badges and the purpose-specific strict export provide that boundary.
        self.assertNotIn('routing_mode', baseline[1])


if __name__ == '__main__':
    unittest.main()
