import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.database import Base, Inbound
from app.services.inbound_service import editor_dict, update_inbound
from app.services.protocol_profiles import compile_profile, decompile_profile


class InboundEditorTests(unittest.TestCase):
    def setUp(self):
        self.engine=create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session=sessionmaker(bind=self.engine,autoflush=False)
        self.db=self.Session()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def add(self, core, protocol, settings, stream, port=10443):
        item=Inbound(core=core,protocol=protocol,remark="editable",port=port,
                     enable=True,settings=settings,stream_settings=stream,tag="editable")
        self.db.add(item);self.db.commit();self.db.refresh(item)
        return item

    def test_singbox_tls_profile_roundtrip_preserves_uuid_and_cleans_transport(self):
        uuid="11111111-1111-1111-1111-111111111111"
        item=self.add(
            "sing-box","vless",{"users":[{"uuid":uuid}]},
            {"tls":{"enabled":True,"server_name":"old.example.test",
                    "certificate_path":"/old/fullchain.pem","key_path":"/old/privkey.pem"},
             "transport":{"type":"ws","path":"/old","headers":{"Host":"cdn.old.example.test"}},
             "_vui":{"security":"tls","server_name":"old.example.test","client_fingerprint":"chrome"}}
        )
        state=editor_dict(item)
        self.assertEqual(state["profile"]["transport"],"ws")
        self.assertEqual(state["profile"]["path"],"/old")
        self.assertEqual(state["profile"]["server_name"],"old.example.test")
        profile={**state["profile"],"transport":"grpc","service_name":"VUIService",
                 "server_name":"new.example.test","certificate_path":"/new/fullchain.pem",
                 "key_path":"/new/privkey.pem"}
        updated=update_inbound(self.db,item.id,{"core":"sing-box","protocol":"vless",
            "port":11443,"remark":"changed","profile":profile})
        self.assertEqual(updated.settings["users"][0]["uuid"],uuid)
        self.assertEqual(updated.stream_settings["transport"],
                         {"type":"grpc","service_name":"VUIService"})
        self.assertEqual(updated.stream_settings["tls"]["server_name"],"new.example.test")
        self.assertEqual(updated.port,11443)

    def test_reality_private_key_is_never_decompiled_and_survives_edit(self):
        settings={"users":[{"uuid":"11111111-1111-1111-1111-111111111111"}]}
        settings,stream=compile_profile("sing-box","vless",{
            "security":"reality","transport":"direct",
            "reality_target":"target.example.test:443",
            "reality_server_name":"target.example.test",
            "reality_short_id":"0102030405060708",
            "client_fingerprint":"chrome"},settings,{})
        private=stream["tls"]["reality"]["private_key"]
        public=stream["_vui"]["reality_public_key"]
        profile=decompile_profile("sing-box","vless",settings,stream)
        self.assertNotIn("reality_private_key",profile)
        self.assertNotIn(private,str(profile))
        profile["client_fingerprint"]="firefox"
        settings2,stream2=compile_profile("sing-box","vless",profile,settings,stream)
        self.assertEqual(stream2["tls"]["reality"]["private_key"],private)
        self.assertEqual(stream2["_vui"]["reality_public_key"],public)
        self.assertEqual(stream2["_vui"]["client_fingerprint"],"firefox")
        self.assertEqual(settings2["users"][0]["uuid"],settings["users"][0]["uuid"])

    def test_editor_dict_never_contains_persisted_protocol_secrets(self):
        uuid="11111111-1111-1111-1111-111111111111"
        password="server-password-never-return"
        item=self.add(
            "sing-box","tuic",
            {"users":[{"uuid":uuid,"password":password}],"congestion_control":"bbr"},
            {"tls":{"enabled":True,"server_name":"tuic.example.test",
                    "certificate_path":"/cert.pem","key_path":"/key.pem"},
             "_vui":{"security":"tls","server_name":"tuic.example.test",
                     "udp_relay_mode":"native"}},
        )
        state=editor_dict(item)
        serialized=str(state)
        self.assertNotIn(uuid,serialized)
        self.assertNotIn(password,serialized)
        self.assertTrue(state["credentials"]["has_uuid"])
        self.assertTrue(state["credentials"]["has_password"])

        settings={"users":[{"uuid":uuid}]}
        settings,stream=compile_profile("sing-box","vless",{
            "security":"reality","transport":"direct",
            "reality_target":"target.example.test:443",
            "reality_short_id":"0102030405060708",
        },settings,{})
        private=stream["tls"]["reality"]["private_key"]
        reality=self.add("sing-box","vless",settings,stream,port=11443)
        reality_state=editor_dict(reality)
        self.assertNotIn(private,str(reality_state))
        self.assertNotIn(uuid,str(reality_state))

    def test_shadowsocks_method_roundtrip_preserves_hidden_password(self):
        password="hidden-shadowsocks-password"
        item=self.add(
            "sing-box","shadowsocks",
            {"method":"aes-128-gcm","password":password},
            {},
            port=12443,
        )
        state=editor_dict(item)
        self.assertEqual(state["profile"]["shadowsocks_method"],"aes-128-gcm")
        self.assertTrue(state["profile"]["shadowsocks_password_set"])
        self.assertTrue(state["credentials"]["has_password"])
        self.assertNotIn(password,str(state))

        profile={**state["profile"],"shadowsocks_method":"chacha20-ietf-poly1305"}
        updated=update_inbound(
            self.db,item.id,
            {"core":"sing-box","protocol":"shadowsocks","profile":profile},
        )
        self.assertEqual(updated.settings["method"],"chacha20-ietf-poly1305")
        self.assertEqual(updated.settings["password"],password)

    def test_xray_transport_edit_removes_stale_blocks(self):
        settings={"users":[{"id":"11111111-1111-1111-1111-111111111111"}],"decryption":"none"}
        stream={"method":"websocket","wsSettings":{"path":"/old","host":"old.example.test"},
                "security":"none","grpcSettings":{"serviceName":"stale"}}
        profile=decompile_profile("xray","vless",settings,stream)
        profile.update(transport="raw",security="none")
        _,new_stream=compile_profile("xray","vless",profile,settings,stream)
        self.assertEqual(new_stream["method"],"raw")
        self.assertNotIn("wsSettings",new_stream)
        self.assertNotIn("grpcSettings",new_stream)
        self.assertIn("rawSettings",new_stream)

    def test_hysteria_obfs_secret_stays_server_side_and_is_preserved(self):
        settings={"users":[{"password":"transport-password"}]}
        profile={"security":"tls","certificate_path":"/cert.pem","key_path":"/key.pem",
                 "server_name":"hy.example.test","up_mbps":50,"down_mbps":200,
                 "obfs_type":"salamander","obfs_password":"hidden-obfs"}
        settings,stream=compile_profile("sing-box","hysteria2",profile,settings,{})
        visual=decompile_profile("sing-box","hysteria2",settings,stream)
        self.assertEqual(visual["obfs_password"],"")
        self.assertTrue(visual["obfs_password_set"])
        self.assertNotIn("hidden-obfs",str(visual))
        settings2,_=compile_profile("sing-box","hysteria2",visual,settings,stream)
        self.assertEqual(settings2["obfs"]["password"],"hidden-obfs")


if __name__=="__main__":
    unittest.main()
