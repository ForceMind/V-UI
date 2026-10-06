"""Authenticated editor API: no protocol secrets are returned, but edits preserve them."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from app.models import database
from app.services.protocol_profiles import compile_profile
import app.api.inbounds as inbound_api
import test_auth as auth_tests


class InboundEditorApiTests(unittest.TestCase):
    def setUp(self):
        auth_tests.AuthenticationTests.setUp(self)
        response=auth_tests.AuthenticationTests.login(self)
        self.assertEqual(response.status_code,200,response.text)

    def add_reality(self):
        uuid="11111111-1111-1111-1111-111111111111"
        settings={"users":[{"uuid":uuid}]}
        settings,stream=compile_profile("sing-box","vless",{
            "security":"reality", "flow":"xtls-rprx-vision",
            "transport":"direct",
            "reality_target":"target.example.test:443",
            "reality_server_name":"target.example.test",
            "reality_short_id":"0102030405060708",
            "client_fingerprint":"chrome",
        },settings,{})
        with database.SessionLocal() as db:
            row=database.Inbound(core="sing-box",protocol="vless",remark="secret-node",
                                 port=10443,enable=True,settings=settings,
                                 stream_settings=stream,tag="secret-node")
            db.add(row);db.commit();db.refresh(row)
            return row.id,uuid,stream["tls"]["reality"]["private_key"]

    def test_editor_endpoint_redacts_reality_private_key_and_uuid(self):
        identity,uuid,private=self.add_reality()
        response=self.client.get(f"/api/inbounds/{identity}/editor")
        self.assertEqual(response.status_code,200,response.text)
        raw=response.text
        self.assertNotIn(uuid,raw)
        self.assertNotIn(private,raw)
        value=response.json()
        self.assertTrue(value["credentials"]["has_uuid"])
        self.assertFalse(value["credentials"]["has_password"])
        self.assertEqual(value["profile"]["security"],"reality")
        self.assertEqual(value["profile"]["reality_short_id"], "")
        self.assertTrue(value["profile"]["reality_short_id_set"])
        self.assertNotIn("0102030405060708", raw)

    def test_edit_preserves_uuid_and_reality_private_key(self):
        identity,uuid,private=self.add_reality()
        current=self.client.get(f"/api/inbounds/{identity}/editor").json()
        profile={**current["profile"],"client_fingerprint":"chrome",
                 "reality_server_name":"other.example.test"}
        with patch.object(inbound_api,"apply_checked",return_value={"applied":True,"valid":True}):
            response=self.client.put(f"/api/inbounds/{identity}",headers=auth_tests.HEADERS,json={
                "remark":"edited-secret-node","port":11443,"profile":profile,
            })
        self.assertEqual(response.status_code,200,response.text)
        with database.SessionLocal() as db:
            row=db.get(database.Inbound,identity)
            self.assertEqual(row.settings["users"][0]["uuid"],uuid)
            self.assertEqual(row.stream_settings["tls"]["reality"]["private_key"],private)
            self.assertEqual(row.stream_settings["_vui"]["client_fingerprint"],"chrome")
            self.assertEqual(row.remark,"edited-secret-node")
            self.assertEqual(row.port,11443)

    def test_hysteria_obfs_password_is_not_returned_and_empty_preserves_it(self):
        secret="OBFS-SECRET-NEVER-RETURN"
        settings={"users":[{"password":"transport-password"}]}
        settings,stream=compile_profile("sing-box","hysteria2",{
            "security":"tls",
            "certificate_path":"/cert.pem",
            "key_path":"/key.pem",
            "server_name":"hy.example.test",
            "up_mbps":50,
            "down_mbps":200,
            "obfs_type":"salamander",
            "obfs_password":secret,
        },settings,{})
        with database.SessionLocal() as db:
            row=database.Inbound(core="sing-box",protocol="hysteria2",remark="hy",
                                 port=10444,enable=True,settings=settings,
                                 stream_settings=stream,tag="hy")
            db.add(row);db.commit();db.refresh(row);identity=row.id
        state=self.client.get(f"/api/inbounds/{identity}/editor")
        self.assertEqual(state.status_code,200,state.text)
        self.assertNotIn(secret,state.text)
        profile=state.json()["profile"]
        self.assertEqual(profile["obfs_password"],"")
        self.assertTrue(profile["obfs_password_set"])
        with patch.object(inbound_api,"apply_checked",return_value={"applied":True,"valid":True}):
            response=self.client.put(f"/api/inbounds/{identity}",headers=auth_tests.HEADERS,
                json={"profile":profile})
        self.assertEqual(response.status_code,200,response.text)
        with database.SessionLocal() as db:
            self.assertEqual(db.get(database.Inbound,identity).settings["obfs"]["password"],secret)

    def test_managed_certificate_unbind_requires_replacement_manual_material(self):
        uuid="11111111-1111-1111-1111-111111111111"
        old_cert=Path("/managed/revision/fullchain.pem")
        old_key=Path("/managed/revision/privkey.pem")
        with database.SessionLocal() as db:
            row=database.Inbound(
                core="sing-box",protocol="vless",remark="managed-cert-node",
                port=10445,enable=True,
                settings={"users":[{"uuid":uuid}]},
                stream_settings={
                    "tls":{
                        "enabled":True,
                        "server_name":"vpn.example.test",
                        "certificate_path":str(old_cert),
                        "key_path":str(old_key),
                    },
                    "_vui":{"security":"tls","server_name":"vpn.example.test"},
                },
                tag="managed-cert-node",
            )
            db.add(row);db.commit();db.refresh(row);identity=row.id

        class FakeManager:
            def __init__(self):
                self.unbound=[]
            def binding(self,target):
                return "a"*32
            def material(self,certificate_id):
                return (old_cert,old_key),"vpn.example.test","b"*64
            def unbind(self,target):
                self.unbound.append(target)
                return {"target":target,"configured":False}

        manager=FakeManager()
        editor=self.client.get(f"/api/inbounds/{identity}/editor").json()
        profile=editor["profile"]

        with patch("app.certificates.manager.get_manager",return_value=manager), \
             patch.object(inbound_api,"apply_checked",return_value={"applied":True,"valid":True}):
            response=self.client.put(
                f"/api/inbounds/{identity}",
                headers=auth_tests.HEADERS,
                json={"profile":profile,"certificate_id":None},
            )
        self.assertEqual(response.status_code,409,response.text)
        self.assertEqual(manager.unbound,[])

        profile={**profile,
                 "certificate_path":"/manual/fullchain.pem",
                 "key_path":"/manual/privkey.pem"}
        with patch("app.certificates.manager.get_manager",return_value=manager), \
             patch.object(inbound_api,"apply_checked",return_value={"applied":True,"valid":True}):
            response=self.client.put(
                f"/api/inbounds/{identity}",
                headers=auth_tests.HEADERS,
                json={"profile":profile,"certificate_id":None},
            )
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(manager.unbound,[f"inbound:{identity}"])
        with database.SessionLocal() as db:
            stream=db.get(database.Inbound,identity).stream_settings
            self.assertEqual(stream["tls"]["certificate_path"],"/manual/fullchain.pem")
            self.assertEqual(stream["tls"]["key_path"],"/manual/privkey.pem")

    def test_update_contract_rejects_core_protocol_and_raw_settings_overrides(self):
        identity,_,_=self.add_reality()
        for body in (
            {"core":"xray"},
            {"protocol":"trojan"},
            {"settings":{"users":[]}},
            {"stream_settings":{}},
            {"unexpected":"field"},
        ):
            with self.subTest(body=body):
                response=self.client.put(f"/api/inbounds/{identity}",headers=auth_tests.HEADERS,json=body)
                self.assertEqual(response.status_code,422,response.text)


if __name__=="__main__":
    unittest.main()
