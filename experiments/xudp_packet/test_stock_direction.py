"""Pure controls for stock-direction supplement; no core, socket, or real sleep."""
from collections import Counter
from copy import deepcopy
import errno
import hashlib
import inspect
import json
from pathlib import Path
import socket
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import packet_boundary as b
from test_packet_boundary import records


class Clock:
    def __init__(self): self.now = 100.
    def monotonic(self): return self.now
    def sleep(self, duration): self.now += duration


class FakeTarget:
    host, port = "127.0.0.3", 30000
    def __init__(self, deliver=True, truncate=None, raw_without_row=False):
        self.deliver, self.truncate, self.raw_without_row = deliver, truncate, raw_without_row
        self.plans, self.rows, self.raw = [], [], []
        self.errors, self.closed = [], set()
    def announce(self, leaf, request, reply): self.plans.append((leaf, request, reply))
    def receive_count(self): return len(self.raw)
    def observed_rows(self, leaf): return [r for r in self.rows if r["leaf"] == leaf]
    def finish_window(self, leaf):
        self.closed.add(leaf)
        return self.observed_rows(leaf)
    def audit(self):
        if self.errors: raise ValueError("Target recorder error")
    def receive(self):
        leaf, request, reply = self.plans[-1]
        if self.raw_without_row:
            self.raw.append({"payload": b.ident(request)})
            self.errors.append("target send failed")
            return None
        if not self.deliver: return None
        actual = request if self.truncate is None else request[:self.truncate]
        outgoing = actual if reply is None else reply
        self.raw.append({"payload": b.ident(actual)})
        row = {"leaf": leaf, "target": [self.host, self.port], "source": ["127.0.0.4", 40000],
               "request_expected": b.ident(request), "request_actual": b.ident(actual),
               "request_full": actual == request, "request_exact_prefix": True,
               "reply_sent": b.ident(outgoing), "sendto_return": len(outgoing), "reply_body": outgoing}
        self.rows.append(row)
        return outgoing


class FakeAssociation:
    relay = ("127.0.0.1", 20000)
    def __init__(self, target, clock, *, reply=True, send_error=False, recv_error=False):
        self.target, self.clock, self.reply = target, clock, reply
        self.send_error, self.recv_error = send_error, recv_error
        self.sent, self.attempts, self.pending = [], [], []
        self.stream = self
        self.timeout = .025
    def getsockname(self): return ("127.0.0.1", 10000)
    def settimeout(self, seconds): self.timeout = seconds
    def send(self, host, port, body):
        self.attempts.append((host, port, body))
        if self.send_error: raise OSError(errno.EBADF, "fake closed socket")
        self.sent.append((host, port, body))
        response = self.target.receive()
        if self.reply and response is not None:
            self.pending.append(b"\0\0\0\x01" + socket.inet_aton(host) + port.to_bytes(2, "big") + response)
    def recvmsg(self, size):
        if self.pending: return self.pending.pop(0), [], 0, self.relay
        if self.recv_error: raise OSError(errno.ECONNREFUSED, "fake refusal")
        self.clock.sleep(self.timeout)
        raise socket.timeout()


def decode(wire):
    return socket.inet_ntoa(wire[4:8]), int.from_bytes(wire[8:10], "big"), wire[10:]


class Observations(unittest.TestCase):
    def observe(self, stage="U", index=0, *, target=None, reply=True, send_error=False, recv_error=False,
                short_buffer=False, association=None, spec_index=0):
        case = b.plan(stage)[index]
        clock = association.clock if association else Clock()
        target = target or FakeTarget()
        association = association or FakeAssociation(target, clock, reply=reply, send_error=send_error, recv_error=recv_error)
        def log_finish(row, *args):
            row["device_log"].update(end_offset=20, birth_time_after=1.,
                fresh_short_buffer_lines=["packet download closed: short buffer"] if short_buffer else [])
        with tempfile.TemporaryDirectory() as d, patch.object(b.time,"monotonic",clock.monotonic), \
             patch.object(b.time,"sleep",clock.sleep), \
             patch.object(b,"device_log_start",return_value={"pid":123,"birth_time":1.,"start_offset":0}), \
             patch.object(b,"device_log_finish",side_effect=log_finish):
            rows=[]
            row=b.observed_exchange(association,target,case,case["packets"][spec_index],
                SimpleNamespace(decode_socks_datagram=decode),rows,Path(d)/"packets.json",None,None,None)
            saved=json.loads((Path(d)/"packets.json").read_text())
            self.assertEqual(saved, rows)
        return row, association, target

    def test_full_uplink_and_short_ack_have_independent_lengths(self):
        row, a, _ = self.observe()
        self.assertEqual((row["target"]["request_actual"]["bytes"],row["actual_reply"]["bytes"]),(16373,32))
        self.assertTrue(row["full_delivery_passed"])
        self.assertEqual(row["send_state"],"sent")
        self.assertEqual(len(a.attempts),1)

    def test_full_ack_never_hides_truncated_uplink(self):
        row, _, _ = self.observe(index=2,target=FakeTarget(truncate=16374))
        self.assertEqual(row["status"],"capacity_failed_exact_prefix")
        self.assertFalse(row["full_delivery_passed"])
        self.assertEqual(row["actual_reply"]["bytes"],32)
        self.assertEqual(row["target"]["request_actual"]["bytes"],16374)

    def test_a_echo_unchanged_timeout_is_error_even_with_device_reason(self):
        row,a,t=self.observe(stage="A",index=4,reply=False,short_buffer=True)
        self.assertEqual(row["status"],"error")
        self.assertEqual(row["failure_kind"],"explicit_device_short_buffer")
        self.assertEqual(row["target"]["reply_sent"]["bytes"],16373)
        self.assertEqual(row["reply_window"]["status"],"timeout")
        self.assertGreaterEqual(row["reply_window"]["completed"],row["reply_window"]["deadline"])
        self.assertNotIn("actual_reply",row)
        self.assertFalse(row["full_delivery_passed"])
        self.assertEqual(len(a.attempts),1)

    def test_real_same_object_recovery_after_timeout_may_have_zero_target(self):
        first,a,t=self.observe(stage="A",index=4,reply=False,short_buffer=True)
        t.deliver=False
        recovery,a2,_=self.observe(stage="A",index=4,target=t,association=a,spec_index=1)
        self.assertIs(a,a2)
        self.assertEqual(len(a.attempts),2)
        self.assertFalse(recovery["first_udp"])
        self.assertEqual(recovery["send_state"],"sent")
        self.assertEqual(recovery["failure_kind"],"target_zero_reply_timeout")
        self.assertEqual(recovery["target_window"]["actual_datagram_count"],0)
        self.assertNotIn("target",recovery)
        self.assertNotIn("actual_reply",recovery)
        self.assertEqual(first["application_socket"],recovery["application_socket"])
        self.assertEqual(first["socks_relay"],recovery["socks_relay"])

    def test_timeout_without_log_is_never_explicit_rejection(self):
        row,_,_=self.observe(reply=False)
        self.assertEqual(row["status"],"error")
        self.assertEqual(row["failure_kind"],"target_received_reply_timeout")

    def test_send_error_has_no_invented_receive_windows(self):
        row,a,_=self.observe(send_error=True)
        self.assertEqual(row["send_state"],"send_error")
        self.assertEqual(len(a.attempts),1)
        self.assertEqual(len(a.sent),0)
        self.assertEqual(row["target_window"]["status"],"not_started")
        self.assertEqual(row["reply_window"]["status"],"not_started")
        self.assertNotIn("target",row)

    def test_receive_error_is_not_timeout(self):
        row,_,_=self.observe(reply=False,recv_error=True)
        self.assertEqual(row["status"],"error")
        self.assertEqual(row["reply_window"]["status"],"receive_error")
        self.assertEqual(row["failure_kind"],"application_receive_error")

    def test_raw_target_event_with_no_completed_send_is_not_targetzero(self):
        row,_,_=self.observe(target=FakeTarget(raw_without_row=True),reply=False)
        self.assertEqual(row["target_window"]["status"],"unaccounted_datagrams")
        self.assertEqual(row["target_window"]["actual_datagram_count"],1)
        self.assertEqual(row["failure_kind"],"target_recorder_error_reply_timeout")

    def test_v_full_downlink_independent_of_small_request(self):
        row,_,_=self.observe(stage="V",index=0)
        self.assertEqual(row["request"]["bytes"],32)
        self.assertEqual(row["target"]["sendto_return"],16121)
        self.assertEqual(row["actual_reply"]["bytes"],16121)
        self.assertTrue(row["full_delivery_passed"])

    def test_v_shortbuffer_is_error_not_complete_diagnostic(self):
        row,_,_=self.observe(stage="V",index=2,reply=False,short_buffer=True)
        self.assertEqual(row["status"],"error")
        self.assertFalse(row["full_delivery_passed"])
        self.assertEqual(row["target"]["sendto_return"],16123)
        args=records("V")
        args[1][2]["packets"][0]=row
        args[1][2]["status"]="error"
        with self.assertRaises(ValueError):b.assess(*args)

    def test_c_and_d_cannot_opt_into_observed_continuation(self):
        for stage in ("C","D"):
            case=b.plan(stage)[0]
            with self.assertRaisesRegex(ValueError,"escaped"):
                b.observed_exchange(None,None,case,case["packets"][0],None,[],None,None,None,None)


class PlanAndVerifier(unittest.TestCase):
    def test_original_four_stage_plans_are_byte_identical(self):
        expected = {
            "A": "a2fa9ff43a87ac53f898aff64bff0f429399197f12c6d1a1dc156f365b387a23",
            "B": "a1c4be8cb997618a4a0547a75e7ea1b1c985672aa361131519882aebf3acf1ff",
            "C": "8bef81e88dfca79fec5b8938196e73c40eb1d03b423844d1bfaf35687b726d16",
            "D": "005383050b4555b09cb59d55318b5db664d0061e39108f622ab9ec61a9e9a17c",
        }
        for stage, value in expected.items():
            raw=json.dumps(b.plan(stage),sort_keys=True,separators=(",", ":")).encode()
            self.assertEqual(hashlib.sha256(raw).hexdigest(),value)

    def test_new_stages_fixed_roles_counts_and_no_predicted_expected_prefix(self):
        for stage,count,leaves in (("U",9,18),("V",18,45)):
            cases=b.plan(stage)
            self.assertEqual((len(cases),len(b.leaf_ids(cases))),(count,leaves))
            self.assertEqual({k for k,v in b.ROLES[stage].items() if v=="official"},{"device-singbox"})
            self.assertTrue(all(p["expected_prefix"] is None for c in cases for p in c["packets"]))
        self.assertEqual({p["size"] for c in b.plan("V") for p in c["packets"] if p["id"]=="probe"},{16121,16122,16123})

    def test_u_ack_capacity_forgery_is_rejected(self):
        args=records("U")
        row=args[1][2]["packets"][0]
        self.assertEqual(row["actual_reply"]["bytes"],32)
        row.update(full_delivery_passed=True,status="full_delivery_passed")
        with self.assertRaises(ValueError):b.assess(*args)

    def test_u_ack_cannot_be_replaced_by_echo(self):
        args=records("U")
        row=args[1][0]["packets"][0]
        row["expected_reply"]=deepcopy(row["request"])
        with self.assertRaises(ValueError):b.assess(*args)

    def test_all_stages_reject_error_or_running_leaf_even_with_full_bytes(self):
        for stage in b.ROLES:
            for status in ("error","running"):
                args=records(stage);args[1][0]["packets"][0]["status"]=status
                with self.assertRaises(ValueError):b.assess(*args)

    def test_strict_exchange_exception_persists_terminal_then_propagates(self):
        row={"status":"running","full_delivery_passed":False,"send_state":"sent",
             "target_window":{"status":"observing","started":1.,"deadline":5.},
             "reply_window":{"status":"not_started"}}
        rows=[]
        def fail(*args):
            args[5].append(deepcopy(row))
            raise TimeoutError("no target")
        with tempfile.TemporaryDirectory() as d, patch.object(b,"_exchange",side_effect=fail), \
             patch.object(b.time,"monotonic",return_value=6.):
            path=Path(d)/"packets.json"
            with self.assertRaises(TimeoutError):b.exchange(None,None,None,None,None,rows,path)
            self.assertEqual(json.loads(path.read_text())[0]["status"],"error")
            self.assertEqual(rows[0]["target_window"]["status"],"zero_observed")
            self.assertNotIn("target",rows[0])


class CorrelationAndLogs(unittest.TestCase):
    def target(self, body, plans, closed=()):
        target=b.Target.__new__(b.Target)
        target.host,target.port,target.peer="127.0.0.3",30000,"127.0.0.4"
        target.correlate,target.closed_windows=True,set(closed)
        target.lock,target.stop=threading.Lock(),threading.Event()
        target.errors,target.received,target.rows,target.receive_events=[],[],[],[]
        target.plans=plans;target.stream=Mock()
        def recv(*args):
            target.stop.set()
            return body,[],0,(target.peer,40000)
        target.stream.recvmsg.side_effect=recv
        target.stream.sendto.side_effect=lambda outgoing,peer:len(outgoing)
        return target

    def test_missing_first_target_cannot_shift_recovery_leaf_identity(self):
        plans=[{"leaf":"first","request":b"large","reply":b"ack"},
               {"leaf":"recover","request":b"recover","reply":None}]
        target=self.target(b"recover",plans,closed=("first",));target.run()
        self.assertEqual(target.errors,[])
        self.assertEqual(target.rows[0]["leaf"],"recover")
        self.assertEqual(target.rows[0]["request_actual"],b.ident(b"recover"))

    def test_late_prior_packet_is_raw_event_and_error_never_recovery(self):
        plans=[{"leaf":"first","request":b"large","reply":b"ack"},
               {"leaf":"recover","request":b"recover","reply":None}]
        target=self.target(b"large",plans,closed=("first",));target.run()
        self.assertTrue(target.errors)
        self.assertEqual(len(target.receive_events),1)
        self.assertEqual(target.rows,[])
        target.stream.sendto.assert_not_called()

    def test_ambiguous_prefix_cannot_manufacture_leaf(self):
        target=self.target(b"a",[{"leaf":"one","request":b"abc","reply":None},
                                  {"leaf":"two","request":b"ax","reply":None}])
        target.run();self.assertTrue(target.errors);self.assertEqual(target.rows,[])

    def test_log_reason_requires_fresh_complete_line_same_pid_birth(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/"003-device-singbox.log"
            path.write_text("old packet download closed: short buffer\n")
            core=SimpleNamespace(log_path=path,process=SimpleNamespace(pid=123))
            f=SimpleNamespace(client=core,safe=lambda text:text)
            c=SimpleNamespace(life=SimpleNamespace(clean=lambda text:text))
            proc=Mock();proc.create_time.return_value=1.
            ps=SimpleNamespace(Process=lambda pid:proc)
            start=b.device_log_start(f,c,ps)
            row={"device_log":deepcopy(start)}
            b.device_log_finish(row,f,c,ps)
            self.assertEqual(row["device_log"]["fresh_short_buffer_lines"],[])
            path.write_text(path.read_text()+"fresh packet download closed: short buffer\n")
            row={"device_log":deepcopy(start)};b.device_log_finish(row,f,c,ps)
            self.assertEqual(len(row["device_log"]["fresh_short_buffer_lines"]),1)
            proc.create_time.return_value=2.
            with self.assertRaisesRegex(ValueError,"birth changed"):
                b.device_log_finish({"device_log":deepcopy(start)},f,c,ps)

    def test_partial_log_line_cannot_supply_fresh_reason(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/"device.log";path.write_text("packet download closed: ")
            core=SimpleNamespace(log_path=path,process=SimpleNamespace(pid=123))
            f=SimpleNamespace(client=core,safe=lambda text:text)
            c=SimpleNamespace(life=SimpleNamespace(clean=lambda text:text))
            proc=Mock();proc.create_time.return_value=1.;ps=SimpleNamespace(Process=lambda pid:proc)
            row={"device_log":b.device_log_start(f,c,ps)}
            path.write_text(path.read_text()+"short buffer\n")
            b.device_log_finish(row,f,c,ps)
            self.assertEqual(row["device_log"]["fresh_short_buffer_lines"],[])


class LifecycleFailureTerminals(unittest.TestCase):
    class Events:
        def __init__(self,path,kind):
            self.path=path;self.data={"status":"running","kind":kind,"events":[]};self.flush()
        def flush(self):b.save(self.path,self.data)
        def event(self,kind,**fields):
            self.data["events"].append({"kind":kind,**fields});self.flush()

    def run_failure(self, *, baseline_error=False, cleanup_error=False):
        class Watch:
            def __init__(self,f,psutil,events):self.events=events;self.samples=[]
            def baseline(self):
                self.events.event("actual_baseline_started")
                if baseline_error:raise ValueError("baseline sampling failed")
                value={"baseline":True};self.samples.append(value);return value
        association=Mock()
        association.stream.fileno.return_value=-1
        if cleanup_error:association.close.side_effect=OSError("close failed")
        helper=SimpleNamespace(SocksDatagrams=Mock(return_value=association))
        life=SimpleNamespace(Events=self.Events,SocketWatch=Watch,clean=lambda text:text)
        c=SimpleNamespace(life=life);case=b.plan("D")[0]
        with tempfile.TemporaryDirectory() as d, patch.object(b,"exchange",side_effect=TimeoutError("real exchange timeout")):
            persist=Path(d)/"packets.json"
            with self.assertRaises((ValueError,TimeoutError,OSError)):
                b.lifecycle_exercise(SimpleNamespace(client_port=1234),None,case,helper,c,None,[],persist)
            data=json.loads((Path(d)/"lifecycle-observation.json").read_text())
        self.assertEqual(data["status"],"error")
        self.assertTrue(data["error"]["message"])
        self.assertEqual(data["events"],[{"kind":"actual_baseline_started"}])
        return data,association,helper

    def test_initial_packet_failure_preserves_events_and_closes_association(self):
        data,association,_=self.run_failure()
        self.assertEqual(data["error"]["type"],"TimeoutError")
        association.close.assert_called_once()

    def test_baseline_failure_still_finalizes_started_observation(self):
        data,association,helper=self.run_failure(baseline_error=True)
        self.assertEqual(data["error"]["type"],"ValueError")
        helper.SocksDatagrams.assert_not_called()
        association.close.assert_not_called()

    def test_cleanup_failure_retains_prior_error_and_separate_cleanup_cause(self):
        data,_,_=self.run_failure(cleanup_error=True)
        self.assertEqual(data["error"]["type"],"TimeoutError")
        self.assertEqual(data["cleanup_error"]["type"],"OSError")


if __name__=="__main__":unittest.main()
