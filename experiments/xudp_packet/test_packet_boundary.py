"""Pure preparation controls: no core starts, sockets, sleeps or compiler."""
from copy import deepcopy
from collections import Counter
import errno
import inspect
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest.mock import Mock

import packet_boundary as b


def records(stage):
    cases = b.plan(stage)
    binaries = {"official": {"path": "/tmp/official-sing-box", "sha256": b.STOCK_SHA, "version": "1.14.2"},
                "candidate": {"path": "/tmp/candidate-sing-box", "sha256": "a" * 64, "version": "experiment"}}
    rows, processes = [], []
    for case in cases:
        packets, target_rows = [], []
        sent = 0
        for spec in case["packets"]:
            leaf = case["id"] + "/" + spec["id"]
            request, reply = b.packet_bodies(leaf, spec)
            row = {"leaf": leaf, "direction": spec["direction"], "request": b.ident(request),
                   "expected_reply": b.ident(reply), "application_socket": ["127.0.0.1", 10000],
                   "socks_relay": ["127.0.0.1", 20000], "requested_target": ["127.0.0.3", 30000],
                   "first_udp": sent == 0 or (case["mode"] == "close" and spec["id"] == "recover")}
            if spec["direction"] == "local_emsgsize":
                row.update(status="local_emsgsize_verified", errno=errno.EMSGSIZE, socks_wire_bytes=65508,
                           local_zero_delivery=True, full_delivery_passed=False)
            else:
                n, m = len(request), len(reply)
                if stage == "A" and spec["expected_prefix"] is not None:
                    n = m = spec["expected_prefix"]
                if stage == "B" and spec["size"] > 8192:
                    m = 16000
                if stage == "U" and spec["size"] == 16375:
                    n = 16374
                outgoing = reply if spec["direction"] in ("downlink", "uplink_ack") else request[:n]
                target = {"leaf": leaf, "target": ["127.0.0.3", 30000], "source": ["127.0.0.4", 40000],
                          "request_expected": b.ident(request), "request_actual": b.ident(request[:n]),
                          "request_full": n == len(request), "request_exact_prefix": True,
                          "reply_sent": b.ident(outgoing), "sendto_return": len(outgoing)}
                full = n == len(request) and m == len(reply)
                wire = b"\0\0\0\x01" + socket.inet_aton("127.0.0.3") + (30000).to_bytes(2, "big") + reply[:m]
                row.update(target=target, actual_reply=b.ident(reply[:m]), reply_exact_prefix=True,
                           reply_source=["127.0.0.1", 20000], reply_target=["127.0.0.3", 30000],
                           socks_reply_wire=b.ident(wire), full_delivery_passed=full,
                           status="full_delivery_passed" if full else "capacity_failed_exact_prefix")
                target_rows.append(target)
                sent += 1
            packets.append(row)
        rows.append({"case": case["id"], "status": "observed", "errors": [], "unexecuted_leaves": [], "cleanup": {"joined": True},
                     "packets": packets, "target_ledger": {"host":"127.0.0.3","port":30000,"closed": True, "thread_joined": True,
                         "socket_closed": True, "planned_count": len(target_rows), "received_count": len(target_rows),
                         "errors": [], "packets": target_rows,
                         "actual_datagrams": [{"target": r["target"], "source": r["source"],
                                               "payload": r["request_actual"], "flags": 0, "ancillary_count": 0}
                                              for r in target_rows]}, "dns": {"business": [], "expected": [], "ordinary": []}})
        for index, (role, origin) in enumerate(case["roles"].items()):
            binary = binaries[origin]
            processes.append({"case": case["id"], "role": role, "origin": origin,
                "classification": "official" if origin == "official" else "experimental",
                "binary": binary["path"], "sha256": binary["sha256"], "version": binary["version"],
                "parser_passed": True, "config_sha256": "e" * 64,
                "launches": [{"pid": 12345+index, "birth_time": 100.0+index, "ready": True, "sha256": binary["sha256"]}]})
        current=rows[-1]
        current["cleanup"]={"threads_joined":True,"fixture_removed":True,"all_target_workers_joined":True,
            "application_sockets_closed":True,"processes":[{"pid":12345+i,"returncode":0,"reaped":True} for i in range(3)]}
        current["process_identity"]=[{"pid":12345+i,"birth_time":100.0+i} for i in range(3)]
        current["frozen_target_ledgers"]=[{"closed":True,"thread_joined":True,"socket_closed":True,
                                          "received":[],"expected":[],"errors":[]}]
        if stage == "D":
            current["lifecycle"] = lifecycle(case,current,processes[-3:])
    return cases, rows, processes, binaries


def lifecycle(case,result,processes):
    roles={r["role"]:r["launches"][0] for r in processes}
    def snapshot(start,socks):
        return {"monotonic":start,"sample_completed_monotonic":start+.01,
            "processes":[{"role":role,"pid":roles[role]["pid"],"create_time":roles[role]["birth_time"],
                          "races":[],"sockets":deepcopy(socks.get(role,[]))} for role in ("domestic","overseas")]}
    def tcp(inode,local,remote):
        return {"inode":inode,"protocol":"tcp","proc_state":"01","local":local,"remote":remote}
    local={"host":"127.0.0.1","port":45000};remote={"host":"127.0.0.2","port":50000}
    values={"domestic":[tcp(11,local,remote),tcp(14,{"host":"127.0.0.1","port":60000},{"host":"127.0.0.1","port":55000})],
            "overseas":[tcp(12,remote,local),{"inode":13,"protocol":"udp","proc_state":"07",
                                                "local":{"host":"127.0.0.4","port":40000}}]}
    baseline,live,absence,final=snapshot(.1,{}),snapshot(1.1,values),snapshot(3,{}),snapshot(7.1,{})
    result["packets"][0]["reply_monotonic"]=1.
    result["packets"][1]["reply_monotonic"]=7.2
    return {"observation_sha256":"a"*64,"proof":{"mode":case["mode"],"baseline":baseline,"live":live,
        "leg":{"upstream_port":50000,"domestic_local":local,"keys":[["domestic",12345,11],["overseas",12346,12],["overseas",12346,13]]},
        "ingress_port":60000,"last_business_reply":1.,"deadline":7.,"first_absence":absence,"final":final,
        "samples":[baseline,live,absence,final],"recovery_leaf":result["packets"][1]["leaf"],"recovery_reply_monotonic":7.2}}


class Plans(unittest.TestCase):
    def test_fixed_nonempty_counts_and_unique_leaves(self):
        self.assertEqual({s: (len(b.plan(s)), len(b.leaf_ids(b.plan(s)))) for s in b.ROLES},
                         {"A": (21, 51), "B": (3, 12), "C": (78, 198), "D": (6, 12), "U": (9, 18), "V": (18, 45)})
        for stage in b.ROLES:
            ids = b.leaf_ids(b.plan(stage))
            self.assertEqual(len(ids), len(set(ids)))

    def test_isolate_only_one_stock_role(self):
        self.assertEqual({k for k,v in b.ROLES["A"].items() if v == "official"}, {"device-singbox"})
        self.assertEqual({k for k,v in b.ROLES["B"].items() if v == "official"}, {"overseas"})
        self.assertEqual(set(b.ROLES["C"].values()), {"candidate"})

    def test_zero_first_warmed_both_directions_and_recovery(self):
        cases = b.plan("C")
        empty = [x for x in cases if x["id"].endswith("_0")]
        self.assertEqual(len(empty), 12)
        for case in empty:
            self.assertEqual(case["packets"][-2]["size"], 0)
            self.assertEqual(case["packets"][-1]["size"], 7)

    def test_nonuniform_counter_payload_and_prefix_identity(self):
        data = b.payload(60000, "same")
        self.assertNotEqual(data[:32], data[32:64])
        self.assertEqual(data[:15992], b.payload(15992, "same"))
        self.assertNotEqual(data, b.payload(60000, "other"))
        self.assertEqual(b.payload(0, "zero"), b"")

    def test_address_budgets_do_not_confuse_mux_uint16_and_udp(self):
        self.assertEqual(65507-b.socks_overhead("ipv4"), 65497)
        self.assertEqual(b.socks_overhead("ipv6"), 22)
        self.assertEqual(b.socks_overhead("domain", 255), 262)
        self.assertEqual(65498+b.socks_overhead("ipv4"), 65508)
        self.assertGreater(65535, 65507)
        for kind, length in (("domain", 0), ("domain", 256), ("unknown", None)):
            with self.assertRaises(ValueError): b.socks_overhead(kind, length)

    def test_known_warmed_vm_no_unproven_first_prediction(self):
        cases=b.plan("A")
        for case in cases:
            for p in case["packets"]:
                if p["expected_prefix"] is not None:
                    self.assertTrue(case["id"].endswith("warmed"))
                    self.assertNotEqual(case["protocol"], "vless")
                    self.assertEqual((p["size"], p["expected_prefix"]), (15993, 15992))


class AntiFalseGreen(unittest.TestCase):
    def test_all_stages_synthetic_integrity_and_red_separation(self):
        for stage in b.ROLES:
            result = b.assess(*records(stage))
            self.assertTrue(result["diagnostic_complete"])
            self.assertEqual(result["stage_capacity_passed"], stage in ("C", "D", "V"))
            self.assertFalse(result["original_suite_passed"] or result["product_qualified"])

    def test_zero_cases_or_packets_rejected(self):
        with self.assertRaises(ValueError): b.verify_leaf_set([], [], "case")
        cases,rows,processes,binaries=records("C")
        with self.assertRaises(ValueError): b.assess(cases, [], [], binaries)
        rows[0]["packets"] = []
        with self.assertRaises(ValueError): b.assess(cases,rows,processes,binaries)

    def test_missing_duplicate_unexpected_case_or_packet_rejected(self):
        for change in (lambda rows: rows.pop(), lambda rows: rows.append(deepcopy(rows[0])),
                       lambda rows: rows[0]["packets"].pop(),
                       lambda rows: rows[0]["packets"].append(deepcopy(rows[0]["packets"][0]))):
            args=records("C");change(args[1])
            with self.assertRaises(ValueError): b.assess(*args)

    def test_corrupt_payload_source_port_direction_first_and_wire_rejected(self):
        mutations = [lambda p: p["actual_reply"].update(sha256="a"*64),
            lambda p: p["target"]["source"].__setitem__(0,"127.0.0.5"),
            lambda p: p["reply_target"].__setitem__(1,30001),
            lambda p: p.update(direction="wrong"),lambda p:p.update(first_udp=False),
            lambda p:p["socks_reply_wire"].update(bytes=0)]
        for change in mutations:
            args=records("C");change(args[1][0]["packets"][0])
            with self.assertRaises(ValueError): b.assess(*args)

    def test_full_delivery_boolean_cannot_hide_truncation(self):
        args=records("A")
        item=next(p for p in args[1][0]["packets"] if p["leaf"].endswith("15993"))
        item["full_delivery_passed"]=True;item["status"]="full_delivery_passed"
        with self.assertRaises(ValueError): b.assess(*args)

    def test_missing_red_not_relabelled_pass(self):
        args=records("B")
        for row in args[1]:
            for p in row["packets"]:p["full_delivery_passed"]=True
        with self.assertRaises(ValueError): b.assess(*args)

    def test_role_classification_and_unlaunched_process_rejected(self):
        for mutate in (lambda p:p.update(classification="official"), lambda p:p.update(launches=[]),
                       lambda p:p.update(sha256="0"*64), lambda p:p.update(parser_passed=False)):
            args=records("C");mutate(args[2][0])
            with self.assertRaises(ValueError): b.assess(*args)

    def test_local_emsgsize_must_not_count_as_payload_pass(self):
        args=records("C")
        p=next(p for row in args[1] for p in row["packets"] if p["direction"]=="local_emsgsize")
        p["full_delivery_passed"]=True
        with self.assertRaises(ValueError): b.assess(*args)

    def test_cleanup_or_dns_failure_rejected(self):
        for mutate in (lambda r:r["target_ledger"].update(thread_joined=False),
                       lambda r:r.update(cleanup=None),lambda r:r["dns"]["ordinary"].append("fallback")):
            args=records("C");mutate(args[1][0])
            with self.assertRaises(ValueError): b.assess(*args)


class CleanupLifecycleEvidence(unittest.TestCase):
    def test_truthy_cleanup_object_is_insufficient(self):
        args=records("C");args[1][0]["cleanup"]={"joined":True}
        with self.assertRaises(ValueError):b.assess(*args)

    def test_launch_birth_live_reaped_pid_cross_checks(self):
        mutations=[lambda r:r["cleanup"]["processes"][0].update(pid=88888),
            lambda r:r["cleanup"]["processes"][0].update(reaped=False),
            lambda r:r["cleanup"]["processes"][0].update(returncode=None),
            lambda r:r["process_identity"][0].update(birth_time=0),
            lambda r:r["cleanup"].update(threads_joined=False),
            lambda r:r["frozen_target_ledgers"][0].update(socket_closed=False)]
        for mutate in mutations:
            args=records("C");mutate(args[1][0])
            with self.assertRaises(ValueError):b.assess(*args)

    def test_missing_lifecycle_proof_does_not_pass_packet_only_D(self):
        args=records("D");args[1][0].pop("lifecycle")
        with self.assertRaises(ValueError):b.assess(*args)

    def test_late_missing_changed_leg_or_recovery_rejected(self):
        mutations=[lambda p:p["first_absence"].update(sample_completed_monotonic=7.01),
            lambda p:p["leg"]["keys"].pop(),lambda p:p.update(deadline=8.),
            lambda p:p.update(recovery_reply_monotonic=2.),lambda p:p["samples"].pop(),
            lambda p:p["live"]["processes"][0].update(create_time=888.)]
        for mutate in mutations:
            args=records("D");mutate(args[1][0]["lifecycle"]["proof"])
            with self.assertRaises(ValueError):b.assess(*args)

    def test_prior_readiness_samples_preserved_outside_window(self):
        args=records("D");proof=args[1][0]["lifecycle"]["proof"]
        prior=deepcopy(proof["baseline"]);prior.update(monotonic=.01,sample_completed_monotonic=.02)
        proof["prebaseline_samples"]=[prior]
        self.assertTrue(b.assess(*args)["stage_capacity_passed"])

    def test_final_socket_reappearance_rejected(self):
        args=records("D");p=args[1][0]["lifecycle"]["proof"]
        p["final"]["processes"][0]["sockets"]=[deepcopy(p["live"]["processes"][0]["sockets"][0])]
        with self.assertRaises(ValueError):b.assess(*args)

    def test_lifecycle_file_missing_modified_or_wrong_proof_rejected(self):
        cases,rows,_,_=records("D")
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for case,row in zip(cases,rows):
                (root/case["id"]).mkdir()
                path=root/case["id"]/"lifecycle-observation.json"
                b.save(path,{"status":"observed","events":[{"kind":"large_endpoint_proof","proof":row["lifecycle"]["proof"]}]})
                row["lifecycle"]["observation_sha256"]=b.sha(path)
            b.verify_lifecycle_files(root,cases,rows)
            path=root/cases[0]["id"]/"lifecycle-observation.json"
            content=path.read_text();path.unlink()
            with self.assertRaises(ValueError):b.verify_lifecycle_files(root,cases,rows)
            path.write_text(content+" ")
            with self.assertRaises(ValueError):b.verify_lifecycle_files(root,cases,rows)
            b.save(path,{"status":"observed","events":[]})
            rows[0]["lifecycle"]["observation_sha256"]=b.sha(path)
            with self.assertRaises(ValueError):b.verify_lifecycle_files(root,cases,rows)

    def test_D_fixed_artifact_set_requires_lifecycle_file(self):
        cases=b.plan("D")
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for path in ("result.json","plan.json","input-identity.json","process-binaries.json","suite.log"):
                (root/path).write_text("{}")
            for case in cases:
                for path in (case["id"]+"/result.json",case["id"]+"/packets.json",
                             "frozen-fixture/"+case["id"]+".json","frozen-fixture/"+case["id"]+".log"):
                    dest=root/path;dest.parent.mkdir(exist_ok=True);dest.write_text("{}")
            with self.assertRaisesRegex(ValueError,"lifecycle"):
                b.write_evidence_manifest(root,cases)


class RecorderAndArtifacts(unittest.TestCase):
    def target(self,body,flags=0):
        target=b.Target.__new__(b.Target)
        target.host,target.port,target.peer="127.0.0.3",30000,"127.0.0.4"
        target.lock,target.stop=threading.Lock(),threading.Event()
        target.errors,target.received,target.rows,target.receive_events=[],[],[],[]
        target.plans=[{"leaf":"empty","request":body,"reply":None}]
        target.stream=Mock()
        def recv(*args):
            target.stop.set()
            return body,[],flags,(target.peer,40000)
        target.stream.recvmsg.side_effect=recv
        target.stream.sendto.return_value=len(body)
        return target

    def test_zero_datagram_is_recorded_and_sent_not_eof(self):
        target=self.target(b"");target.run()
        self.assertEqual(target.errors,[])
        self.assertEqual(len(target.rows),1)
        self.assertTrue(target.rows[0]["request_full"])
        target.stream.sendto.assert_called_once_with(b"",("127.0.0.4",40000))

    def test_recorder_msg_trunc_rejected_before_send(self):
        target=self.target(b"x",socket.MSG_TRUNC);target.run()
        self.assertTrue(target.errors)
        target.stream.sendto.assert_not_called()

    def test_recorder_keeps_actual_source_port_and_target(self):
        target=self.target(b"abcd");target.run()
        self.assertEqual(target.rows[0]["source"],["127.0.0.4",40000])
        self.assertEqual(target.rows[0]["target"],["127.0.0.3",30000])

    def test_build_checks_missing_duplicate_failure_or_bool_return_rejected(self):
        labels=[p+"-"+m for p in ("common-network","common-bufio","protocol-socks")
                for m in ("race","debug","darwin-compile","windows-compile")]+["build","module-verify"]+[k+"-"+m for k in ("mux-unit","mux-encrypted","mux-encrypted-cancel") for m in ("race","debug")]+["mux-route-race"]
        valid={"checks":[{"label":n,"returncode":0} for n in labels]}
        b.verify_build_record(valid)
        for mutate in (lambda r:r["checks"].pop(),lambda r:r["checks"].append(r["checks"][0]),
                       lambda r:r["checks"][0].update(returncode=1),lambda r:r["checks"][0].update(returncode=False)):
            value=deepcopy(valid);mutate(value)
            with self.assertRaises(ValueError):b.verify_build_record(value)

    def test_manifest_duplicate_json_and_upload_leaf_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);p=root/"dup.json";p.write_text('{"a":1,"a":2}')
            with self.assertRaises(ValueError):b.json_read(p)
            p.unlink();(root/"candidate-sing-box").write_text("binary")
            with self.assertRaises(ValueError):b.write_evidence_manifest(root,b.plan("A"))

    def test_no_original_test_assertion_patching_or_invocation(self):
        source=inspect.getsource(b)
        self.assertNotIn("patch.object",source)
        self.assertNotIn("loadTestsFromModule",source)
        self.assertNotIn("setUpClass()",source)
        self.assertIn("source_identity()",source)


if __name__=="__main__":unittest.main()
