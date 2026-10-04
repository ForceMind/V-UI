"""v0.4.1: real sing-box Shadowsocks server with Mihomo/sing-box TCP+UDP clients."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import tempfile
import time
import unittest

import yaml

from app.models import database
from app.services.core_manager import SingBoxAdapter
import test_subscription_tokens as grant_tests
from loopback_helpers import (
    CoreProcess,
    http_through,
    socks5_udp_through,
    start_http_target,
    start_udp_echo,
    unused_port,
)


@unittest.skipUnless(
    os.getenv("VUI_TEST_MIHOMO") and os.getenv("VUI_TEST_CORES"),
    "pinned real binaries required",
)
class ShadowsocksLoopbackTests(unittest.TestCase):
    method="aes-128-gcm"
    password="shadowsocks-correct-password"

    def setUp(self):
        grant_tests.SubscriptionTokenTests.setUp(self)
        self.stack=ExitStack()
        self.addCleanup(self.stack.close)
        self.root=Path(self.stack.enter_context(
            tempfile.TemporaryDirectory(prefix="vui-ss-chain-")
        ))
        self.server_port=unused_port()
        self.http_port,self.http_requests=start_http_target(self.stack)
        self.udp_port,self.udp_packets=start_udp_echo(self.stack)
        self.env={**os.environ}

        with database.SessionLocal() as db:
            row=db.get(database.Inbound,1)
            row.core="sing-box"
            row.protocol="shadowsocks"
            row.port=self.server_port
            row.remark="ss-real"
            row.settings={"method":self.method,"password":self.password}
            row.stream_settings={}
            db.commit()
            config=SingBoxAdapter().build_config([row])

        config["inbounds"][0]["listen"]="127.0.0.1"
        self.server_config=self.root/"server.json"
        self.server_config.write_text(json.dumps(config))
        self.server=CoreProcess(
            self.stack,
            [str(Path(os.environ["VUI_TEST_CORES"])/"sing-box"),
             "run","-c",str(self.server_config)],
            self.root/"server.log",
            self.env,
        )

    def issue_grant(self):
        response=self.client.post(
            "/api/subscriptions",
            headers=grant_tests.HEADERS,
            json={
                "label":"ss-real",
                "server":"127.0.0.1",
                "inbound_ids":[1],
                "formats":["mihomo.yaml","raw","sing-box.json"],
            },
        )
        self.assertEqual(response.status_code,201,response.text)
        return response.json()

    def public_get(self,path):
        with self.client.__class__(
            self.client.app,
            base_url=grant_tests.ORIGIN,
            client=("127.0.0.1",54112),
        ) as public:
            return public.get(path)

    def exported_mihomo(self,grant,port):
        response=self.public_get(grant["paths"]["mihomo.yaml"])
        self.assertEqual(response.status_code,200,response.text)
        config=yaml.safe_load(response.text)
        proxy=config["proxies"][0]
        self.assertEqual(proxy["type"],"ss")
        self.assertEqual(proxy["cipher"],self._db_settings()["method"])
        self.assertEqual(proxy["password"],self._db_settings()["password"])
        self.assertTrue(proxy["udp"])
        config["mixed-port"]=port
        config["bind-address"]="127.0.0.1"
        # Test the protocol path itself; local-address protection must not bypass it.
        config["rules"]=["MATCH,PROXY"]
        path=self.root/("mihomo-"+str(port)+".yaml")
        path.write_text(yaml.safe_dump(config,allow_unicode=True,sort_keys=False))
        return path

    def exported_singbox(self,grant,port):
        response=self.public_get(grant["paths"]["sing-box.json"])
        self.assertEqual(response.status_code,200,response.text)
        config=response.json()
        outbound=config["outbounds"][0]
        self.assertEqual(outbound["type"],"shadowsocks")
        self.assertEqual(outbound["method"],self._db_settings()["method"])
        self.assertEqual(outbound["password"],self._db_settings()["password"])
        config["inbounds"][0]["listen_port"]=port
        path=self.root/("singbox-"+str(port)+".json")
        path.write_text(json.dumps(config))
        return path

    def _db_settings(self):
        with database.SessionLocal() as db:
            return dict(db.get(database.Inbound,1).settings)

    def start_mihomo(self,grant=None):
        self.server.start(self.server_port)
        grant=grant or self.issue_grant()
        port=unused_port()
        path=self.exported_mihomo(grant,port)
        process=CoreProcess(
            self.stack,
            [os.environ["VUI_TEST_MIHOMO"],"-d",str(self.root/("mihomo-data-"+str(port))),
             "-f",str(path)],
            self.root/("mihomo-client-"+str(port)+".log"),
            self.env,
        )
        process.start(port)
        return process,port

    def start_singbox_client(self,grant):
        port=unused_port()
        path=self.exported_singbox(grant,port)
        process=CoreProcess(
            self.stack,
            [str(Path(os.environ["VUI_TEST_CORES"])/"sing-box"),
             "run","-c",str(path)],
            self.root/("singbox-client-"+str(port)+".log"),
            self.env,
        )
        process.start(port)
        return process,port

    def assert_tcp_success(self,port):
        deadline=time.monotonic()+4
        last=None
        while time.monotonic()<deadline:
            try:
                last=http_through(port,"127.0.0.1",self.http_port)
                if last==(200,b"VUI-LOOPBACK-TARGET"):
                    return
            except (OSError,TimeoutError) as exc:
                last=exc
            time.sleep(.08)
        self.fail("Shadowsocks TCP did not reach target: "+repr(last))

    def assert_udp_success(self,port):
        payload=b"vui-shadowsocks-udp"
        reply=socks5_udp_through(port,"127.0.0.1",self.udp_port,payload)
        self.assertEqual(reply,b"VUI-UDP-ECHO:"+payload)

    def assert_failure_without_target(self,port):
        http_before=len(self.http_requests)
        udp_before=len(self.udp_packets)
        try:
            status,_=http_through(port,"127.0.0.1",self.http_port)
            self.assertGreaterEqual(status,400)
        except (OSError,TimeoutError):
            pass
        try:
            socks5_udp_through(port,"127.0.0.1",self.udp_port,b"must-not-arrive")
        except (OSError,TimeoutError):
            pass
        else:
            self.fail("Invalid Shadowsocks credentials unexpectedly relayed UDP")
        self.assertEqual(len(self.http_requests),http_before)
        self.assertEqual(len(self.udp_packets),udp_before)

    def test_real_shadowsocks_tcp_udp_in_mihomo_and_public_singbox_subscription(self):
        grant=self.issue_grant()

        mihomo,port=self.start_mihomo(grant)
        self.assert_tcp_success(port)
        self.assert_udp_success(port)
        mihomo.stop()

        singbox,port=self.start_singbox_client(grant)
        self.assert_tcp_success(port)
        self.assert_udp_success(port)
        singbox.stop()

        self.assertGreaterEqual(len(self.http_requests),2)
        self.assertGreaterEqual(len(self.udp_packets),2)
        print("Shadowsocks: real TCP+UDP passed in Mihomo and public sing-box subscription")

    def test_wrong_password_is_rejected_for_tcp_and_udp_without_direct_fallback(self):
        with database.SessionLocal() as db:
            row=db.get(database.Inbound,1)
            row.settings={"method":self.method,"password":"wrong-password"}
            db.commit()
        process,port=self.start_mihomo()
        self.assert_failure_without_target(port)
        process.stop()
        print("Shadowsocks: wrong password rejected for TCP+UDP without DIRECT fallback")

    def test_wrong_method_is_rejected_for_tcp_and_udp_without_direct_fallback(self):
        with database.SessionLocal() as db:
            row=db.get(database.Inbound,1)
            row.settings={"method":"aes-256-gcm","password":self.password}
            db.commit()
        process,port=self.start_mihomo()
        self.assert_failure_without_target(port)
        process.stop()
        print("Shadowsocks: mismatched AEAD method rejected for TCP+UDP without DIRECT fallback")


if __name__=="__main__":
    unittest.main()
