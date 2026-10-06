"""An unqualified transport must not bypass REALITY's non-destructive guard."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from app.services import inbound_service, reality_profile
from app.services.protocol_profiles import compile_profile
from app.services.validated_export import ExportError, validated_node
from test_reality_profile import reality_node, state


class RealityDraftTransitionTests(unittest.TestCase):
    def draft(self, kind):
        item = reality_node(); item.settings['users'][0].pop('flow')
        item.stream_settings['transport'] = ({'type':'grpc','service_name':'Draft.Service'} if kind=='grpc' else
            {'type':'httpupgrade','path':'/draft','host':'reference.example.test'} if kind=='httpupgrade' else
            {'type':'ws','path':'/draft'})
        return item

    def transition(self):
        return {'security':'tls','transport':'direct','flow':'','server_name':'vpn.example.test',
                'certificate_path':'/synthetic/cert.pem','key_path':'/synthetic/key.pem'}

    def test_review_reproduction_cannot_drop_server_parameter_and_become_public(self):
        item = self.draft('ws')
        item.settings['users'][0]['flow'] = ''
        item.stream_settings['tls']['reality']['max_time_difference'] = '1m'
        before = state(item)
        with self.assertRaises(HTTPException):
            inbound_service._prepared_payload({'profile':self.transition()}, item)
        self.assertEqual(state(item), before)
        with self.assertRaises(ExportError): validated_node(item, 'vpn.example.test')

    def test_all_known_non_direct_drafts_reject_missing_secrets_before_ensure(self):
        for kind in ('ws','grpc','httpupgrade'):
            for missing in ('uuid','private_key','public_key','short_id'):
                item = self.draft(kind)
                if missing=='uuid': item.settings['users'][0].pop('uuid')
                elif missing=='public_key': item.stream_settings['_vui'].pop('reality_public_key')
                else: item.stream_settings['tls']['reality'].pop(missing)
                before = state(item)
                for payload in ({'remark':'rename'}, {'profile':{}}, {'profile':self.transition()}):
                    with self.subTest(kind=kind,missing=missing,payload=payload), \
                         patch.object(inbound_service,'ensure_credentials') as ensure, \
                         patch.object(reality_profile,'generate_keypair') as keys:
                        with self.assertRaises(HTTPException): inbound_service._prepared_payload(payload,item)
                        ensure.assert_not_called(); keys.assert_not_called()
                        self.assertEqual(state(item),before)

    def test_unknown_nested_fields_reject_every_security_transition(self):
        for kind in ('ws','grpc','httpupgrade'):
            for location in ('settings','user','stream','transport','tls','reality','handshake','meta'):
                item = self.draft(kind)
                targets={'settings':item.settings,'user':item.settings['users'][0],'stream':item.stream_settings,
                    'transport':item.stream_settings['transport'],'tls':item.stream_settings['tls'],
                    'reality':item.stream_settings['tls']['reality'],
                    'handshake':item.stream_settings['tls']['reality']['handshake'],'meta':item.stream_settings['_vui']}
                targets[location]['unrepresented'] = 'must-not-disappear'; before=state(item)
                for security in ('none','tls','reality'):
                    with self.subTest(kind=kind,location=location,security=security):
                        profile={**self.transition(),'security':security}
                        with self.assertRaises(HTTPException): inbound_service._prepared_payload({'profile':profile},item)
                        with self.assertRaises(HTTPException): compile_profile('sing-box','vless',profile,item.settings,item.stream_settings)
                        self.assertEqual(state(item),before)

    def test_known_draft_partial_edits_preserve_credentials_and_transport(self):
        for kind in ('ws','grpc','httpupgrade'):
            item=self.draft(kind); before=state(item)
            transport=item.stream_settings['transport']
            profile={'security':'reality','transport':kind,'reality_short_id':'',
                     'path':transport.get('path','/'),'host':transport.get('host',''),
                     'service_name':transport.get('service_name','')}
            # The generic draft compiler may add its known compatibility marker;
            # it cannot rotate secrets or change the actual transport contract.
            result=inbound_service._prepared_payload({'profile':profile},item)
            self.assertEqual(result[3],before[0])
            self.assertEqual(result[4]['transport'],before[1]['transport'])
            self.assertEqual(result[4]['tls'],before[1]['tls'])
            self.assertEqual(result[4]['_vui']['reality_public_key'],before[1]['_vui']['reality_public_key'])
            with self.assertRaises(ExportError): validated_node(item,'vpn.example.test')

    def test_actual_omission_preserves_path_host_service_and_secrets(self):
        for kind in ('ws','grpc','httpupgrade'):
            item=self.draft(kind)
            if kind=='ws': item.stream_settings['transport']['headers']={'Host':'routing.example.test'}
            before=state(item)
            result=inbound_service._prepared_payload({'profile':{'reality_server_name':'edited.example.test'}},item)
            self.assertEqual(result[3],before[0])
            self.assertEqual(result[4]['transport'],before[1]['transport'])
            self.assertEqual(result[4]['tls']['reality'],before[1]['tls']['reality'])
            self.assertEqual(result[4]['tls']['server_name'],'edited.example.test')
            self.assertEqual(result[4]['_vui']['reality_public_key'],before[1]['_vui']['reality_public_key'])


if __name__=='__main__': unittest.main()
