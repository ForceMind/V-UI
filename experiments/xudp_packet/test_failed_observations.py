"""Pure negative controls for independently checking failed stock observations."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import packet_boundary as p
import verify_failed_observations as v
from test_packet_boundary import records


def h(value):return hashlib.sha256(value.encode()).hexdigest()


def specimen(directory, stage="U", index=0, *, explicit=True, recovery_zero=True):
    cases,results,processes,_=records(stage)
    report={"stage":stage,"cases":results,"diagnostic_complete":False,"stage_capacity_passed":False}
    case,result=cases[index],results[index]
    result.update(status="error",errors=[{"type":"TimeoutError","message":"no reply"}])
    for row in result["packets"]:row["send_state"]="sent"
    device=next(x for x in processes if x["case"]==case["id"] and x["role"]=="device-singbox")
    launch=device["launches"][0]
    text="device ready\nold packet download closed: short buffer\n"
    now=100.
    failed=[]
    for row in result["packets"]:
        start=len(text)
        interval="fresh packet download closed: short buffer\n" if explicit and not failed else ""
        text+=interval
        row["device_log"]={"role":"device-singbox","file":"003-device-singbox.log",
            "pid":launch["pid"],"birth_time":launch["birth_time"],"birth_time_after":launch["birth_time"],
            "start_offset":start,"end_offset":len(text),"prefix_sha256":h(text[:start]),
            "interval_sha256":h(interval),"text":interval,"starts_at_line_boundary":True,
            "fresh_short_buffer_lines":[x for x in interval.splitlines() if v.REASON in x]}
        for key in ("actual_reply","reply_source","reply_target","reply_exact_prefix","socks_reply_wire","reply_monotonic"):
            row.pop(key,None)
        row.update(status="error",full_delivery_passed=False,send_started=now,send_completed=now+.01,
                   error={"type":"TimeoutError","message":"No application reply in complete four-second single-send window"})
        zero=recovery_zero and bool(failed)
        ts=row["send_completed"];td=ts+4;te=td if zero else ts+.01
        rs=te;rd=rs+4;re=rd
        row["target_window"]={"status":"zero_observed" if zero else "received","started":ts,
            "deadline":td,"completed":te,"observed_count":0 if zero else 1,"actual_datagram_count":0 if zero else 1}
        row["reply_window"]={"status":"timeout","started":rs,"deadline":rd,"completed":re,"observed_count":0}
        row["terminal_monotonic"]=re+.01
        row["failure_kind"]=("target_zero_reply_timeout" if zero else "explicit_device_short_buffer" if interval else "target_received_reply_timeout")
        if zero:
            row.pop("target")
            ledger=result["target_ledger"]
            position=next(i for i,x in enumerate(ledger["packets"]) if x["leaf"]==row["leaf"])
            ledger["packets"].pop(position);ledger["actual_datagrams"].pop(position);ledger["received_count"]-=1
        failed.append(row);now=re+.1
    frozen=Path(directory)/"frozen-fixture";frozen.mkdir(exist_ok=True)
    path=frozen/(case["id"]+".log")
    path.write_text("001-overseas.log:\noverseas\n\n002-domestic.log:\ndomestic\n\n003-device-singbox.log:\n"+text)
    return cases,report,processes,result,failed,path


class FailedObservations(unittest.TestCase):
    def check(self,args,directory):return v.verify_stage(directory,*args[:3])
    def mutate_rejected(self,change,pattern=None):
        with tempfile.TemporaryDirectory() as d:
            args=specimen(d);change(args)
            with self.assertRaisesRegex(ValueError,pattern or "."):
                self.check(args,d)

    def test_complete_explicit_rejection_and_true_zero_recovery_are_verified_errors(self):
        with tempfile.TemporaryDirectory() as d:
            args=specimen(d);before=deepcopy(args[1]);result=self.check(args,d)
            self.assertTrue(result["failed_observations_verified"])
            self.assertEqual(result["failed_leaf_count"],2)
            self.assertEqual(result["verified_failed_leaves"],[x["leaf"] for x in args[4]])
            self.assertFalse(result["capacity_evaluated"] or result["product_qualified"])
            self.assertEqual(args[1],before)
            self.assertFalse(args[1]["diagnostic_complete"] or args[1]["stage_capacity_passed"])
            self.assertTrue(all(x["status"]=="error" for x in args[4]))

    def test_prefix_at_target_does_not_prevent_truthful_shortbuffer_observation(self):
        with tempfile.TemporaryDirectory() as d:
            args=specimen(d,index=2)
            self.assertEqual(args[4][0]["target"]["request_actual"]["bytes"],16374)
            self.assertTrue(self.check(args,d)["failed_observations_verified"])
            self.assertFalse(args[4][0]["full_delivery_passed"])

    def test_timeout_without_device_reason_stays_timeout(self):
        with tempfile.TemporaryDirectory() as d:
            args=specimen(d,explicit=False)
            self.assertTrue(self.check(args,d)["failed_observations_verified"])
            self.assertEqual(args[4][0]["failure_kind"],"target_received_reply_timeout")

    def test_new_v_uses_same_failure_proof_without_presuming_16122(self):
        with tempfile.TemporaryDirectory() as d:
            args=specimen(d,stage="V",index=2)
            self.assertEqual(args[4][0]["target"]["sendto_return"],16123)
            self.assertTrue(self.check(args,d)["failed_observations_verified"])

    def test_legacy_missing_fields_is_unverified_not_backfilled(self):
        with tempfile.TemporaryDirectory() as d:
            args=specimen(d)
            for row in args[4]:
                row.pop("device_log");row.pop("send_state");row["status"]="running"
            before=deepcopy(args[1]);result=self.check(args,d)
            self.assertFalse(result["failed_observations_verified"])
            self.assertEqual(len(result["unverified_failed_leaves"]),2)
            self.assertEqual(args[1],before)

    def test_missing_collected_log_interval_is_unverified(self):
        with tempfile.TemporaryDirectory() as d:
            args=specimen(d);args[4][0]["device_log"].pop("end_offset")
            result=self.check(args,d)
            self.assertFalse(result["failed_observations_verified"])
            self.assertIn("log interval",result["unverified_failed_leaves"][0]["reason"])

    def test_before_send_failure_is_unverified_with_no_receive_claim(self):
        with tempfile.TemporaryDirectory() as d:
            args=specimen(d)
            # Use the final recovery so no subsequent first-packet flag changes.
            row=args[4][-1]
            row.update(send_state="not_attempted",target_window={"status":"not_started"},reply_window={"status":"not_started"})
            for key in ("failure_kind","send_started","send_completed","device_log"):
                row.pop(key,None)
            result=self.check(args,d)
            self.assertFalse(result["failed_observations_verified"])

    def test_short_window_even_with_long_completed_time_is_rejected(self):
        self.mutate_rejected(lambda a:a[4][0]["reply_window"].update(deadline=a[4][0]["reply_window"]["started"]+.1),"Shortened")

    def test_timeout_before_actual_deadline_is_rejected(self):
        self.mutate_rejected(lambda a:a[4][0]["reply_window"].update(completed=a[4][0]["reply_window"]["deadline"]-.01),"complete")

    def test_target_zero_window_before_deadline_is_rejected(self):
        self.mutate_rejected(lambda a:a[4][1]["target_window"].update(completed=a[4][1]["target_window"]["deadline"]-.01),"Target-zero")

    def test_zero_claim_with_real_target_row_is_rejected(self):
        self.mutate_rejected(lambda a:a[4][0]["target_window"].update(status="zero_observed",observed_count=0,actual_datagram_count=0),"raw target")

    def test_unaccounted_raw_datagram_cannot_be_zero(self):
        def change(a):a[3]["target_ledger"]["actual_datagrams"].append(deepcopy(a[3]["target_ledger"]["actual_datagrams"][0]))
        self.mutate_rejected(change,"without a complete")

    def test_unsent_claim_cannot_have_completed_receive_windows(self):
        self.mutate_rejected(lambda a:a[4][0].update(send_state="not_attempted"),"Unsent")

    def test_send_and_window_order_cannot_be_inverted(self):
        self.mutate_rejected(lambda a:a[4][0].update(send_started=200.),"execution order")

    def test_nonfinite_or_boolean_times_rejected(self):
        for value in (float("inf"),float("nan"),True):
            with self.subTest(value=value):
                self.mutate_rejected(lambda a:a[4][0].update(send_started=value),"finite")

    def test_wrong_pid_birth_and_official_identity_rejected(self):
        mutations=[lambda a:a[4][0]["device_log"].update(pid=1),
                   lambda a:a[4][0]["device_log"].update(birth_time=1.),
                   lambda a:a[4][0]["device_log"].update(birth_time_after=1.),
                   lambda a:next(x for x in a[2] if x["case"]==a[3]["case"] and x["role"]=="device-singbox").update(origin="candidate")]
        for change in mutations:
            with self.subTest(change=change):self.mutate_rejected(change)

    def test_log_text_offset_prefix_and_interval_hash_rejected(self):
        changes=[lambda a:a[4][0]["device_log"].update(text="foreign line\n"),
                 lambda a:a[4][0]["device_log"].update(start_offset=-1),
                 lambda a:a[4][0]["device_log"].update(prefix_sha256="0"*64),
                 lambda a:a[4][0]["device_log"].update(interval_sha256="0"*64),
                 lambda a:a[4][0]["device_log"].update(file="002-domestic.log")]
        for change in changes:
            with self.subTest(change=change):self.mutate_rejected(change)

    def test_missing_original_log_rejected(self):
        self.mutate_rejected(lambda a:a[5].unlink(),"Missing original")

    def test_changed_original_log_cannot_be_hidden_by_saved_interval(self):
        self.mutate_rejected(lambda a:a[5].write_text(a[5].read_text().replace("fresh ","forged ")),"interval")

    def test_old_line_not_in_interval_cannot_supply_reason(self):
        self.mutate_rejected(lambda a:a[4][0]["device_log"].update(fresh_short_buffer_lines=["old packet download closed: short buffer"]),"stale")

    def test_previous_leaf_log_interval_cannot_be_reused_for_recovery(self):
        def change(a):
            a[4][1]["device_log"]=deepcopy(a[4][0]["device_log"])
            a[4][1]["failure_kind"]="explicit_device_short_buffer"
        self.mutate_rejected(change,"reuses")

    def test_explicit_shortbuffer_label_requires_actual_fresh_line(self):
        with tempfile.TemporaryDirectory() as d:
            args=specimen(d,explicit=False);args[4][0]["failure_kind"]="explicit_device_short_buffer"
            with self.assertRaisesRegex(ValueError,"classification"):self.check(args,d)

    def test_no_reply_cannot_include_payload_hash_or_fake_full_delivery(self):
        for change in (lambda a:a[4][0].update(actual_reply={"bytes":0,"sha256":h("")}),
                       lambda a:a[4][0].update(full_delivery_passed=True)):
            self.mutate_rejected(change)

    def test_zero_leaf_target_must_still_equal_actual_bound_target(self):
        self.mutate_rejected(lambda a:a[4][1]["requested_target"].__setitem__(1,30001),"different target")

    def test_case_startup_failure_is_unverified_without_fake_packet(self):
        with tempfile.TemporaryDirectory() as d:
            args=specimen(d);args[3]["packets"]=[]
            result=self.check(args,d)
            self.assertFalse(result["failed_observations_verified"])
            self.assertEqual(result["failed_leaf_count"],0)
            self.assertEqual(result["unverified_cases"][0]["case"],args[3]["case"])

    def test_recovery_association_change_rejected(self):
        self.mutate_rejected(lambda a:a[4][1]["application_socket"].__setitem__(1,12345),"association")
        self.mutate_rejected(lambda a:a[4][1]["socks_relay"].__setitem__(1,12345),"association")

    def test_first_packet_state_and_request_hash_rejected(self):
        self.mutate_rejected(lambda a:a[4][1].update(first_udp=True),"first-packet")
        self.mutate_rejected(lambda a:a[4][0]["request"].update(sha256="0"*64),"frozen packet")

    def test_actual_target_prefix_source_and_send_are_rechecked(self):
        changes=[lambda a:a[3]["target_ledger"]["packets"][0]["request_actual"].update(sha256="0"*64),
                 lambda a:a[3]["target_ledger"]["packets"][0]["source"].__setitem__(0,"127.0.0.5"),
                 lambda a:a[3]["target_ledger"]["packets"][0].update(sendto_return=1)]
        for change in changes:self.mutate_rejected(change)

    def test_identical_repeated_fixture_log_is_accepted(self):
        with tempfile.TemporaryDirectory() as d:
            args=specimen(d);text=args[5].read_text();args[5].write_text(text+"\n"+text)
            self.assertTrue(self.check(args,d)["failed_observations_verified"])

    def test_growing_repeated_fixture_uses_consistent_final_snapshot(self):
        with tempfile.TemporaryDirectory() as d:
            args=specimen(d);text=args[5].read_text()
            args[5].write_text(text+"\n"+text+"device shutdown\n")
            self.assertTrue(self.check(args,d)["failed_observations_verified"])

    def test_shrinking_repeated_fixture_is_rejected(self):
        def change(a):
            text=a[5].read_text();a[5].write_text(text+"device shutdown\n\n"+text)
        self.mutate_rejected(change,"shrink")

    def test_inconsistent_duplicate_device_section_is_rejected(self):
        def change(a):
            text=a[5].read_text();a[5].write_text(text+"\n"+text.replace("fresh ","foreign "))
        self.mutate_rejected(change,"snapshots disagree")

    def test_raw_section_from_another_role_is_not_a_device_log(self):
        self.mutate_rejected(lambda a:a[5].write_text(a[5].read_text().replace("003-device-singbox.log:","003-domestic.log:")),"sections")

    def test_incomplete_or_unexpected_plan_is_rejected(self):
        self.mutate_rejected(lambda a:a[1]["cases"].pop(),"Missing")
        self.mutate_rejected(lambda a:a[3]["packets"].reverse(),"ordered")

    def test_c_and_d_do_not_request_stock_logs_or_grant_capacity(self):
        for stage in ("C","D"):
            cases,rows,processes,_=records(stage)
            result=v.verify_stage("/no-files",cases,{"stage":stage,"cases":rows},processes)
            self.assertFalse(result["applicable"] or result["capacity_evaluated"] or result["failed_observations_verified"])


if __name__=="__main__":unittest.main()
