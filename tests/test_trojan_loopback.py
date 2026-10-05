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
    require_tls_rejection,
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

    def issue_grant(self):
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
        return grant.json()

    def public_get(self,path):
        with self.client.__class__(
            self.client.app,
            base_url=grant_tests.ORIGIN,
            client=("127.0.0.1",54012),
        ) as public:
            return public.get(path)

    def exported_mihomo(self,grant,port):
        response=self.public_get(grant["paths"]["mihomo.yaml"])
        self.assertEqual(response.status_code,200,response.text)
        self.assertNotIn(str(self.key),response.text)
        self.assertNotIn("certificate_path",response.text)
        config=yaml.safe_load(response.text)
        self.assertEqual(config["proxies"][0]["type"],"trojan")
        self.assertFalse(config["proxies"][0]["skip-cert-verify"])
        config["mixed-port"]=port
        config["bind-address"]="127.0.0.1"
        path=self.root/("mihomo-client-"+str(port)+".yaml")
        path.write_text(yaml.safe_dump(config,sort_keys=False))
        return path

    def exported_singbox(self,grant,port):
        response=self.public_get(grant["paths"]["sing-box.json"])
        self.assertEqual(response.status_code,200,response.text)
        self.assertNotIn(str(self.key),response.text)
        self.assertNotIn("certificate_path",response.text)
        config=response.json()
        self.assertEqual(config["outbounds"][0]["type"],"trojan")
        self.assertFalse(config["outbounds"][0]["tls"].get("insecure",False))
        config["inbounds"][0]["listen_port"]=port
        path=self.root/("singbox-client-"+str(port)+".json")
        path.write_text(json.dumps(config))
        return path

    def start_mihomo(self, grant=None, *, trust=True):
        self.server.start(self.server_port)
        grant=grant or self.issue_grant()
        port=unused_port()
        path=self.exported_mihomo(grant,port)
        env=self.env.copy()
        if not trust:
            other,_,_=certificate_files(self.root,"untrusted")
            env["SSL_CERT_FILE"]=str(other)
        process=CoreProcess(
            self.stack,
            [os.environ["VUI_TEST_MIHOMO"],"-d",str(self.root/("mihomo-data-"+str(port))),
             "-f",str(path)],
            self.root/("mihomo-"+str(port)+".log"),
            env,
        )
        process.start(port)
        return process,port

    def start_singbox_client(self,grant):
        port=unused_port()
        path=self.exported_singbox(grant,port)
        process=CoreProcess(
            self.stack,
            [str(Path(os.environ["VUI_TEST_CORES"])/"sing-box"),"run","-c",str(path)],
            self.root/("singbox-client-"+str(port)+".log"),
            self.env,
        )
        process.start(port)
        return process,port

    def assert_success(self,port,host="forced.example.test"):
        deadline=time.monotonic()+4
        last=None
        while time.monotonic()<deadline:
            try:
                last=http_through(port,host,self.target_port)
                if last==(200,b"VUI-LOOPBACK-TARGET"):
                    return
            except (OSError,TimeoutError) as exc:
                last=exc
            time.sleep(.08)
        logs="\n".join(
            path.read_text(errors="replace")
            for path in sorted(self.root.glob("*client*.log"))
        )
        self.fail(f"Trojan path did not become usable: {last}\n"+logs)

    def assert_failure_without_direct_fallback(self,port):
        before=len(self.requests)
        try:
            status,_=http_through(
                port,"forced.example.test",self.target_port
            )
            self.assertGreaterEqual(status,400)
        except (OSError,TimeoutError):
            pass
        self.assertEqual(
            len(self.requests),before,
            "Failed Trojan proxy must not reach target through DIRECT",
        )

    def wait_tls_evidence(self,path,port,reason):
        return require_tls_rejection(
            self, path, lambda: self.assert_failure_without_direct_fallback(port),
            self.requests, reason,
        )

    def test_real_trojan_tls_proxy_in_mihomo_and_public_singbox_subscription(self):
        grant=self.issue_grant()

        mihomo,port=self.start_mihomo(grant)
        self.assert_success(port)
        mihomo.stop()

        singbox,port=self.start_singbox_client(grant)
        self.assert_success(port,host="127.0.0.1")
        singbox.stop()

        self.assertGreaterEqual(len(self.requests),2)
        print("Trojan/TLS: real Mihomo and public sing-box subscription paths passed")

    def test_wrong_password_is_rejected_without_direct_fallback(self):
        with database.SessionLocal() as db:
            row=db.get(database.Inbound,1)
            row.settings={"users":[{"password":"wrong-trojan-password"}]}
            db.commit()
        process,port=self.start_mihomo()
        self.assert_failure_without_direct_fallback(port)
        process.stop()
        print("Trojan/TLS: wrong password rejected without DIRECT fallback")

    def test_untrusted_certificate_is_rejected(self):
        process,port=self.start_mihomo(trust=False)
        self.assert_failure_without_direct_fallback(port)
        log=self.wait_tls_evidence(self.root/("mihomo-"+str(port)+".log"),port,"unknown authority")
        process.stop()
        self.assertIn("x509",log)
        print("Trojan/TLS: untrusted certificate rejected")

    def test_wrong_server_name_is_rejected(self):
        with database.SessionLocal() as db:
            row=db.get(database.Inbound,1)
            stream=deepcopy(row.stream_settings)
            stream["tls"]["server_name"]="wrong.example.test"
            stream["_vui"]["server_name"]="wrong.example.test"
            row.stream_settings=stream
            db.commit()
        process,port=self.start_mihomo()
        self.assert_failure_without_direct_fallback(port)
        log=self.wait_tls_evidence(self.root/("mihomo-"+str(port)+".log"),port,"wrong.example.test")
        process.stop()
        self.assertIn("x509",log)
        print("Trojan/TLS: wrong SNI rejected")


if __name__=="__main__":
    unittest.main()
