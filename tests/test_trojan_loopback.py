"""v0.4.0: actual sing-box Trojan/TLS server and Mihomo client paths."""
from contextlib import ExitStack
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import yaml

from app.models import database
from app.services import routing_store
from app.services.core_manager import SingBoxAdapter
from app.services.mihomo_routing import default_routing
from app.services.routing_store import read_snapshot,save_routing
import test_subscription_tokens as grant_tests
from loopback_helpers import (
    certificate_files,
    start_http_target,
    unused_port,
    http_through,
    CoreProcess,
)


@unittest.skipUnless(
    os.getenv("VUI_TEST_MIHOMO") and os.getenv("VUI_TEST_CORES"),
    "pinned real binaries required",
)
class TrojanLoopbackTests(unittest.TestCase):
    def setUp(self):
        grant_tests.SubscriptionTokenTests.setUp(self)
        self.stack=ExitStack()
        self.addCleanup(self.stack.close)
        self.root=Path(self.stack.enter_context(
            tempfile.TemporaryDirectory(prefix="vui-trojan-chain-")
        ))
        patch.object(routing_store,"ROUTING_FILE",self.root/"routing.json").start()
        self.ca,self.cert,self.key=certificate_files(self.root)
        self.server_port,self.proxy_port=unused_port(),unused_port()
        self.target_port,self.requests=start_http_target(self.stack)
        self.env={
            **os.environ,
            "SSL_CERT_FILE":str(self.ca),
            "SSL_CERT_DIR":str(self.root/"empty-roots"),
        }
        (self.root/"empty-roots").mkdir()

        with database.SessionLocal() as db:
            row=db.get(database.Inbound,1)
            row.protocol="trojan"
            row.port=self.server_port
            row.remark="trojan-real"
            row.settings={"users":[{"password":"correct-trojan-password"}]}
            row.stream_settings={
                "tls":{
                    "enabled":True,
                    "server_name":"vpn.example.test",
                    "certificate_path":str(self.cert),
                    "key_path":str(self.key),
                },
                "_vui":{
                    "security":"tls",
                    "server_name":"vpn.example.test",
                    "skip_cert_verify":False,
                },
            }
            db.commit()
            config=SingBoxAdapter().build_config([row])

        config["inbounds"][0]["listen"]="127.0.0.1"
        config["dns"]={
            "servers":[{
                "type":"hosts",
                "tag":"fixture",
                "predefined":{"forced.example.test":"127.0.0.1"},
            }]
        }
        config["route"]["default_domain_resolver"]="fixture"
        self.server_config=self.root/"server.json"
        self.server_config.write_text(json.dumps(config))
        self.server=CoreProcess(
            self.stack,
            [str(Path(os.environ["VUI_TEST_CORES"])/"sing-box"),
             "run","-c",str(self.server_config)],
            self.root/"server.log",
            self.env,
        )
        save_routing(
            {**default_routing(),"mode":"direct","proxy_domains":["forced.example.test"]},
            read_snapshot()["revision"],
        )

    def exported_config(self):
        grant=self.client.post(
            "/api/subscriptions",
            headers=grant_tests.HEADERS,
            json={
                "label":"trojan-real",
                "server":"127.0.0.1",
                "inbound_ids":[1],
                "formats":["mihomo.yaml","raw","sing-box.json"],
            },
        )
        self.assertEqual(grant.status_code,201,grant.text)
        with self.client.__class__(
            self.client.app,
            base_url=grant_tests.ORIGIN,
            client=("127.0.0.1",54012),
        ) as public:
            response=public.get(grant.json()["paths"]["mihomo.yaml"])
        self.assertEqual(response.status_code,200,response.text)
        self.assertNotIn(str(self.key),response.text)
        self.assertNotIn("certificate_path",response.text)
        config=yaml.safe_load(response.text)
        self.assertEqual(config["proxies"][0]["type"],"trojan")
        self.assertFalse(config["proxies"][0]["skip-cert-verify"])
        config["mixed-port"]=self.proxy_port
        config["bind-address"]="127.0.0.1"
        path=self.root/"client.yaml"
        path.write_text(yaml.safe_dump(config,sort_keys=False))
        return path

    def start(self, *, trust=True):
        self.server.start(self.server_port)
        path=self.exported_config()
        env=self.env.copy()
        if not trust:
            other,_,_=certificate_files(self.root,"untrusted")
            env["SSL_CERT_FILE"]=str(other)
        self.client_core=CoreProcess(
            self.stack,
            [os.environ["VUI_TEST_MIHOMO"],"-d",str(self.root/"client-data"),
             "-f",str(path)],
            self.root/"client.log",
            env,
        )
        self.client_core.start(self.proxy_port)

    def assert_success(self):
        deadline=time.monotonic()+4
        last=None
        while time.monotonic()<deadline:
            try:
                last=http_through(self.proxy_port,"forced.example.test",self.target_port)
                if last==(200,b"VUI-LOOPBACK-TARGET"):
                    return
            except (OSError,TimeoutError) as exc:
                last=exc
            time.sleep(.08)
        self.fail(f"Trojan path did not become usable: {last}\n"+
                  (self.root/"client.log").read_text())

    def assert_failure_without_direct_fallback(self):
        before=len(self.requests)
        try:
            status,_=http_through(
                self.proxy_port,"forced.example.test",self.target_port
            )
            self.assertGreaterEqual(status,400)
        except (OSError,TimeoutError):
            pass
        self.assertEqual(
            len(self.requests),before,
            "Failed Trojan proxy must not reach target through DIRECT",
        )

    def test_real_trojan_tls_proxy(self):
        self.start()
        self.assert_success()
        print("Trojan/TLS: real sing-box server and Mihomo proxy path passed")

    def test_wrong_password_is_rejected_without_direct_fallback(self):
        with database.SessionLocal() as db:
            row=db.get(database.Inbound,1)
            row.settings={"users":[{"password":"wrong-trojan-password"}]}
            db.commit()
        self.start()
        self.assert_failure_without_direct_fallback()
        print("Trojan/TLS: wrong password rejected without DIRECT fallback")

    def test_untrusted_certificate_is_rejected(self):
        self.start(trust=False)
        self.assert_failure_without_direct_fallback()
        self.assertIn("certificate",(self.root/"client.log").read_text().lower())
        print("Trojan/TLS: untrusted certificate rejected")

    def test_wrong_server_name_is_rejected(self):
        with database.SessionLocal() as db:
            row=db.get(database.Inbound,1)
            stream=deepcopy(row.stream_settings)
            stream["tls"]["server_name"]="wrong.example.test"
            stream["_vui"]["server_name"]="wrong.example.test"
            row.stream_settings=stream
            db.commit()
        self.start()
        self.assert_failure_without_direct_fallback()
        self.assertIn("certificate",(self.root/"client.log").read_text().lower())
        print("Trojan/TLS: wrong SNI rejected")


if __name__=="__main__":
    unittest.main()
