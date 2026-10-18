"""afbdba1 strict case sequence with explicit Mihomo and passive I/O/FD records."""
from collections import Counter
import importlib
import threading
import time
import traceback
import unittest
from packet_boundary import Target, need, save, json_read, ident, exchange, observed_exchange
from lifecycle import lifecycle_exercise
from socks_observation import observable_socks_class

def run_case(case, c, module, output, binaries, process_records, psutil, product):
    directory = output / case["id"]
    directory.mkdir()
    fixture = role_fixture(c, module, output, binaries, case, process_records, product)
    base_helper = importlib.import_module("udp_dns_capability_helpers")
    f, target = None, None
    before_threads = set(threading.enumerate())
    row = {"case": case["id"], "stage": case["stage"], "protocol": case["protocol"],
           "roles": case["roles"], "status": "running", "packets": [], "errors": [],
           "original_suite_passed": False, "product_qualified": False}
    class DecodeRecorder:
        def __getattr__(self, name):
            return getattr(base_helper, name)
        def record_association(self, association):
            original = association.stream
            class SocketRecorder:
                def __getattr__(self, name): return getattr(original, name)
                def sendto(self, wire, relay):
                    packet = row["packets"][-1]
                    sent = {"wire": ident(wire), "relay": list(relay), "started": time.monotonic()}
                    packet.setdefault("send_calls", []).append(sent)
                    try:
                        result = original.sendto(wire, relay)
                        sent["return"] = result
                        return result
                    except OSError as exc:
                        sent["errno"] = exc.errno
                        raise
                    finally:
                        sent["completed"] = time.monotonic()
                        save(directory / "packets.json", row["packets"])
                def recvmsg(self, size):
                    value = original.recvmsg(size)
                    wire, ancillary, flags, relay = value
                    row["packets"][-1].setdefault("reply_calls", []).append({
                        "wire": ident(wire), "relay": list(relay), "flags": flags,
                        "ancillary_count": len(ancillary), "completed": time.monotonic()})
                    save(directory / "packets.json", row["packets"])
                    return value
            association.stream = SocketRecorder()
            return association
        def decode_socks_datagram(self, wire):
            host, port, body = base_helper.decode_socks_datagram(wire)
            if row["packets"]:
                row["packets"][-1]["decoded_reply_before_assertion"] = {
                    "host": host, "port": port, "payload": ident(body)}
                save(directory / "packets.json", row["packets"])
            return host, port, body
    helper = DecodeRecorder()
    try:
        with fixture(unittest.TestCase(), case["id"]) as f:
            target = Target(module.TARGET, module.UPSTREAM_EGRESS, case["stage"] in ("A", "B", "U", "V"),
                            correlate=case["stage"] in ("A", "U", "V"))
            f.application_close_proofs = []
            f.targets.append(target)
            f.target = target
            need(target.port != f.upstream_port, "Target/upstream port collision")
            domestic, client = configs(f, case, product, directory)
            f.start("mihomo", domestic=domestic, client=client)
            row["process_identity"] = [{"pid": core.process.pid,
                "birth_time": psutil.Process(core.process.pid).create_time()} for core in f.processes]
            if case["mode"] in ("close", "idle"):
                row["lifecycle"] = lifecycle_exercise(f, target, case, helper, c, psutil, row["packets"], directory / "packets.json", process_records)
            else:
                association = helper.record_association(observable_socks_class(helper)(f.client_port))
                try:
                    for p in case["packets"]:
                        if case["stage"] in ("A", "U", "V"):
                            observed_exchange(association, target, case, p, helper, row["packets"],
                                              directory / "packets.json", f, c, psutil)
                            if row["packets"][-1]["status"] == "error":
                                row["errors"].append({"leaf": row["packets"][-1]["leaf"],
                                                      **row["packets"][-1]["error"]})
                        else:
                            exchange(association, target, case, p, helper, row["packets"], directory / "packets.json")
                        save(directory / "packets.json", row["packets"])
                finally:
                    association.close()
                    need(association.stream.fileno() == -1 and association.control.fileno() == -1, "SOCKS application/control socket survived cleanup")
                    f.application_close_proofs.append({"control_fd": association.control.fileno(), "application_fd": association.stream.fileno()})
            f.audit()
            f.observations.append({"kind": "separate-packet-boundary", "stage": case["stage"],
                "full_delivery_passed": all(p["full_delivery_passed"] for p in row["packets"] if p["direction"] != "local_emsgsize"),
                "original_gate_invoked": False, "product_qualified": False})
            need(not row["errors"], "One or more actually attempted packet leaves failed; see packet terminals")
    except Exception as exc:
        row["errors"].append({"type": type(exc).__name__, "message": c.life.clean(str(exc))})
        (directory / "error.log").write_text(c.life.clean(traceback.format_exc()))
    finally:
        if f is not None:
            try:
                row["cleanup"] = cleanup(f, before_threads, psutil)
            except Exception as exc:
                row["errors"].append({"check": "cleanup", "type": type(exc).__name__, "message": c.life.clean(str(exc))})
            if target is not None:
                # Preserve failing raw ledger even when Target.audit fails.
                row["target_ledger"] = {"host": target.host, "port": target.port, "closed": target.closed,
                    "thread_joined": not target.thread.is_alive(), "socket_closed": target.stream.fileno() == -1,
                    "planned_count": len(target.plans), "received_count": len(target.received), "errors": target.errors,
                    "actual_datagrams": target.receive_events,
                    "packets": [{k: v for k, v in item.items() if k != "reply_body"} for item in target.rows]}
            row["frozen_target_ledgers"] = [{"host": t.host, "port": t.port, "closed": t.closed,
                "thread_joined": not t.thread.is_alive(), "socket_closed": t.server.socket.fileno() == -1,
                "received": [{"peer": peer, **ident(body)} for peer, body in t.received],
                "expected": [{"peer": peer, **ident(body), "count": count} for (peer, body), count in t.expected.items()],
                "errors": t.errors} for t in f.targets if not isinstance(t, Target)]
            row["dns"] = {"business": f.answers.snapshot() if f.answers else [],
                          "expected": [[*key, n] for key, n in f.expected_dns.items()],
                          "ordinary": f.ordinary_answers.queries if f.ordinary_answers else []}
        save(directory / "packets.json", row["packets"])
        started_leaves = [p["leaf"] for p in row["packets"]]
        expected_leaves = [case["id"] + "/" + p["id"] for p in case["packets"]]
        row["unexecuted_leaves"] = [leaf for leaf in expected_leaves if leaf not in started_leaves]
        row["lifecycle_window_executed"] = bool((directory / "lifecycle-observation.json").exists() and
            any(e.get("phase") in ("large_then_idle", "large_then_close") for e in
                json_read(directory / "lifecycle-observation.json").get("events", [])))
        row["recovery_attempted"] = any(p["leaf"].endswith("/recover") for p in row["packets"])
        row["status"] = "error" if row["errors"] else "observed"
        save(directory / "result.json", row)
    return row


