"""Verify stock-direction failure observations; never grants capacity or admission.

Run after the independent resource/provenance gate. A missing observation is
unverified, not filled in from a predicted stock limit or a neighbouring leaf.
Contradictory claimed evidence raises ValueError. Historical artifacts without
these fields remain historical incomplete evidence.
"""
import hashlib
import math
from pathlib import Path
import re

import packet_boundary as p

STAGES = frozenset(("A", "U", "V"))
WINDOW_SECONDS = 4
REASON = "packet download closed: short buffer"


def _hash(text):
    return hashlib.sha256(text.encode()).hexdigest()


def _time(value, name):
    p.need(type(value) in (int, float) and math.isfinite(value) and value >= 0,
           "Invalid finite observation time: " + name)
    return value


def _tuple(value, host, name):
    p.need(isinstance(value, list) and len(value) == 2 and value[0] == host and
           type(value[1]) is int and 0 < value[1] <= 65535, "Invalid " + name + " tuple")
    return value


def _error(value, name):
    p.need(isinstance(value, dict) and isinstance(value.get("type"), str) and value["type"] and
           isinstance(value.get("message"), str) and value["message"], "Missing actual " + name + " error")


def _device_text(output, case_id, filename):
    p.need(filename == "003-device-singbox.log", "Wrong device log role/file")
    path = Path(output) / "frozen-fixture" / (case_id + ".log")
    p.need(path.is_file() and not path.is_symlink(), "Missing original frozen device log")
    text = path.read_text()
    headers = list(re.finditer(r"(?m)^([0-9]{3}-(?:overseas|domestic|device-singbox)\.log):\n", text))
    expected = ["001-overseas.log", "002-domestic.log", "003-device-singbox.log"]
    p.need(headers and len(headers) % 3 == 0 and
           [match.group(1) for match in headers] == expected * (len(headers) // 3),
           "Frozen log role sections missing, reordered or contain an unrelated role")
    candidates = []
    for index, header in enumerate(headers):
        if header.group(1) != filename:
            continue
        end = headers[index + 1].start() if index + 1 < len(headers) else len(text)
        segment = text[header.end():end]
        if index + 1 < len(headers):
            p.need(segment.endswith("\n"), "Missing frozen log section separator")
            segment = segment[:-1]  # The fixture's one inter-section separator.
        candidates.append(segment)
    p.need(all(later.startswith(earlier) for earlier, later in zip(candidates, candidates[1:])),
           "Repeated device log snapshots disagree or shrink; cannot select an arbitrary reason line")
    return candidates[-1]


def _fresh_short_buffer(interval, starts_at_boundary):
    lines = interval if starts_at_boundary else interval.partition("\n")[2]
    lines = lines.rsplit("\n", 1)[0] if "\n" in lines else ""
    return [line for line in lines.splitlines() if REASON in line]


def _verify_log(output, case, result, row, processes):
    record = row["device_log"]
    fields = {"role", "file", "pid", "birth_time", "birth_time_after", "start_offset", "end_offset",
              "prefix_sha256", "interval_sha256", "text", "starts_at_line_boundary", "fresh_short_buffer_lines"}
    if not fields <= set(record):
        return "device log interval was not completely collected"
    owned = [item for item in processes if item.get("case") == case["id"] and item.get("role") == "device-singbox"]
    p.need(len(owned) == 1, "Missing/duplicate actual device role")
    device = owned[0]
    p.need(device.get("origin") == "official" and device.get("classification") == "official" and
           device.get("sha256") == p.STOCK_SHA and device.get("version") == "1.14.2" and
           device.get("parser_passed") is True, "Failure log is not from the exact official device")
    launches = device.get("launches", [])
    p.need(len(launches) == 1 and launches[0].get("ready") is True, "Device did not launch exactly once")
    launch = launches[0]
    p.need(type(launch.get("pid")) is int and launch["pid"] > 0 and
           launch.get("sha256") == p.STOCK_SHA, "Device launch identity changed")
    birth = _time(launch.get("birth_time"), "launch birth")
    p.need(birth > 0 and record["role"] == "device-singbox" and
           type(record["pid"]) is int and record["pid"] == launch["pid"] and
           record["birth_time"] == birth == record["birth_time_after"],
           "Failure log PID/birth does not match the official device launch")
    live = [item for item in result.get("process_identity", []) if item.get("pid") == launch["pid"]]
    p.need(len(live) == 1 and live[0].get("birth_time") == birth,
           "Failure log PID/birth does not match the actual case process")
    text = _device_text(output, case["id"], record["file"])
    start, end = record["start_offset"], record["end_offset"]
    p.need(type(start) is int and type(end) is int and 0 <= start <= end <= len(text),
           "Device log interval offsets escaped the original log")
    p.need(record["prefix_sha256"] == _hash(text[:start]) and record["text"] == text[start:end] and
           record["interval_sha256"] == _hash(text[start:end]), "Device log interval/prefix/hash changed")
    boundary = start == 0 or text[start - 1] == "\n"
    p.need(record["starts_at_line_boundary"] is boundary, "Device log start-line boundary forged")
    expected = _fresh_short_buffer(text[start:end], boundary)
    p.need(record["fresh_short_buffer_lines"] == expected,
           "Short-buffer reason is absent, stale, partial or not in the actual device interval")
    return None


def _window(value, name):
    p.need(isinstance(value, dict), "Missing " + name + " observation window")
    start = _time(value.get("started"), name + " start")
    deadline = _time(value.get("deadline"), name + " deadline")
    end = _time(value.get("completed"), name + " completion")
    p.need(deadline == start + WINDOW_SECONDS and end >= start,
           "Shortened or inverted " + name + " observation window")
    count = value.get("observed_count")
    p.need(type(count) is int and count in (0, 1), "Invalid " + name + " observation count")
    return start, deadline, end


def _target_map(case, result):
    ledger = result.get("target_ledger", {})
    raw, rows = ledger.get("actual_datagrams", []), ledger.get("packets", [])
    p.need(isinstance(raw, list) and isinstance(rows, list) and not ledger.get("errors"),
           "Target recorder failed; capacity failure cannot authenticate its ledger")
    p.need(ledger.get("received_count") == len(raw) == len(rows),
           "Raw target receive exists without a complete independently recorded send")
    leaves = [row.get("leaf") for row in rows]
    p.need(len(leaves) == len(set(leaves)), "Duplicate actual target leaf")
    specs = {case["id"] + "/" + spec["id"]: spec for spec in case["packets"]}
    p.need(set(leaves) <= specs.keys(), "Target leaf escaped its frozen case plan")
    expected_order = [leaf for leaf in specs if leaf in leaves]
    p.need(leaves == expected_order, "Actual target leaves reordered across observation windows")
    for event, row in zip(raw, rows):
        spec = specs[row["leaf"]]
        request, reply = p.packet_bodies(row["leaf"], spec)
        size = row["request_actual"]["bytes"]
        p.need(type(size) is int and 0 <= size <= len(request) and
               row["request_expected"] == p.ident(request) and row["request_actual"] == p.ident(request[:size]) and
               row.get("request_full") is (size == len(request)) and row.get("request_exact_prefix") is True,
               "Actual target request is not the declared full payload or exact prefix")
        sent = reply if spec["direction"] in ("downlink", "uplink_ack") else request[:size]
        p.need(row.get("reply_sent") == p.ident(sent) and row.get("sendto_return") == len(sent),
               "Target did not actually complete its declared reply send")
        _tuple(row.get("target"), "127.0.0.3", "target")
        _tuple(row.get("source"), "127.0.0.4", "target source")
        p.need(row["target"] == [ledger.get("host"), ledger.get("port")] and
               event.get("target") == row["target"] and event.get("source") == row["source"] and
               event.get("payload") == row["request_actual"] and event.get("flags") == 0 and
               event.get("ancillary_count") == 0, "Raw target event/ledger changed or recorder truncated")
    return {row["leaf"]: row for row in rows}


def _verify_failed(output, case, result, spec, row, target_rows, processes):
    p.need(row["status"] == "error" and row.get("full_delivery_passed") is False,
           "Failed observation was relabelled successful delivery")
    required = {"send_state", "target_window", "reply_window", "device_log", "error"}
    if not required <= set(row):
        return "historical or incomplete terminal lacks the required collected observation fields"
    _error(row["error"], "packet")
    p.need(row["status"] == "error" and row.get("full_delivery_passed") is False,
           "Failed observation was relabelled successful delivery")
    if row["send_state"] in ("not_attempted", "send_error"):
        p.need(row["target_window"] == {"status": "not_started"} and
               row["reply_window"] == {"status": "not_started"} and
               not {"target", "actual_reply", "reply_receive", "failure_kind"} & row.keys(),
               "Unsent/failed-send leaf claims a completed receive observation")
        return "single-send completion was not established; receive observations were not started"
    p.need(row["send_state"] == "sent", "No terminal actual send state")
    # A collection failure is not guessed from a device limit or another leaf.
    if not {"send_started", "send_completed", "terminal_monotonic"} <= row.keys():
        return "actual send/terminal times were not completely collected"
    if not all({"started", "deadline", "completed", "observed_count"} <= row[key].keys()
               for key in ("target_window", "reply_window")):
        return "target/reply observation was not completely collected"
    if row.get("device_log_error"):
        _error(row["device_log_error"], "device log collection")
        return "device log collection failed; no complete failure reason is claimed"
    reason = _verify_log(output, case, result, row, processes)
    if reason:
        return reason
    sent_start = _time(row["send_started"], "send start")
    sent_end = _time(row["send_completed"], "send completion")
    terminal = _time(row["terminal_monotonic"], "packet terminal")
    ts, td, te = _window(row["target_window"], "target")
    rs, rd, re = _window(row["reply_window"], "reply")
    p.need(sent_start <= sent_end == ts <= te <= rs <= re <= terminal,
           "Send, target, reply and terminal windows are not in actual execution order")
    target = target_rows.get(row["leaf"])
    target_window = row["target_window"]
    expected_count = 1 if target is not None else 0
    p.need(type(target_window.get("actual_datagram_count")) is int and
           target_window["actual_datagram_count"] == target_window["observed_count"] == expected_count,
           "Claimed zero/target increment contradicts the original raw target ledger")
    if target is None:
        p.need(target_window["status"] == "zero_observed" and te >= td and "target" not in row,
               "Target-zero observation is incomplete or has a fabricated target row")
    else:
        p.need(target_window["status"] == "received" and row.get("target") == target and
               row["requested_target"] == target["target"], "Packet and actual target observations differ")
    reply = row["reply_window"]
    if reply["status"] not in ("timeout", "receive_error"):
        return "failure is outside the supported no-reply/receive-error observation classifications"
    p.need(reply["observed_count"] == 0 and not {
               "actual_reply", "reply_receive", "socks_reply_wire", "reply_source", "reply_target",
               "reply_exact_prefix", "reply_monotonic"} & row.keys(),
           "No-reply failure contains invented or conflicting application reply bytes")
    kind = row.get("failure_kind")
    if reply["status"] == "receive_error":
        _error(reply.get("error"), "application receive")
        p.need(kind == "application_receive_error" and row["error"] == reply["error"],
               "Application receive error was converted to a timeout/rejection")
        return None
    p.need(re >= rd and row["error"]["type"] == "TimeoutError",
           "No-reply timeout does not contain a complete real observation window")
    actual_reason = bool(row["device_log"]["fresh_short_buffer_lines"])
    expected_kind = ("explicit_device_short_buffer" if target is not None and actual_reason else
                     "target_received_reply_timeout" if target is not None else "target_zero_reply_timeout")
    p.need(kind == expected_kind, "Failure classification does not follow its actual target/window/device log evidence")
    return None


def verify_stage(output, cases, report, processes):
    """Return verified/unverified failed leaves; contradictions raise ValueError.

    This result must not replace assess(), the original RED, the resource gate,
    or the coordinator's stage result. In particular, verified timeout evidence
    is still a timeout and the failed leaf/case remains an error.
    """
    p.need(cases and len({case["stage"] for case in cases}) == 1, "Missing/mixed fixed stage plan")
    stage = cases[0]["stage"]
    result = {"stage": stage, "applicable": stage in STAGES, "failed_leaf_count": 0,
              "failed_observations_verified": False, "verified_failed_leaves": [],
              "unverified_failed_leaves": [], "unverified_cases": [], "capacity_evaluated": False,
              "product_qualified": False, "scope": "stock_direction_failed_observations_only"}
    if stage not in STAGES:
        return result
    p.need(cases == p.plan(stage) and report.get("stage") == stage,
           "Failure verifier plan/stage is not the frozen stage plan")
    case_rows = report.get("cases", [])
    p.verify_leaf_set([case["id"] for case in cases], [row.get("case") for row in case_rows], "failure case")
    by_case = {row["case"]: row for row in case_rows}
    for case in cases:
        observed = by_case[case["id"]]
        rows = observed.get("packets", [])
        specs = {case["id"] + "/" + spec["id"]: spec for spec in case["packets"]}
        leaves = [row.get("leaf") for row in rows]
        p.need(leaves == list(specs)[:len(leaves)], "Started packet leaves are not the original ordered plan prefix")
        failed = [row for row in rows if row.get("status") in ("error", "running")]
        if not failed:
            if observed.get("status") == "error":
                result["unverified_cases"].append({"case": case["id"],
                    "reason": "case failed without a complete failed packet terminal; no packet failure is inferred"})
            continue
        result["failed_leaf_count"] += len(failed)
        target_rows = _target_map(case, observed)
        sockets, relays = [], []
        last_log_end = None
        for row in rows:
            request, reply = p.packet_bodies(row["leaf"], specs[row["leaf"]])
            p.need(row.get("direction") == specs[row["leaf"]]["direction"] and
                   row.get("request") == p.ident(request) and row.get("expected_reply") == p.ident(reply),
                   "Failed case request/expected reply differs from frozen packet bytes")
            sockets.append(_tuple(row.get("application_socket"), "127.0.0.1", "application"))
            relays.append(_tuple(row.get("socks_relay"), "127.0.0.1", "SOCKS relay"))
            _tuple(row.get("requested_target"), "127.0.0.3", "requested target")
            p.need(row["requested_target"] == [observed["target_ledger"].get("host"), observed["target_ledger"].get("port")],
                   "Failed/zero leaf requested a different target than the actual bound recorder")
            log = row.get("device_log", {})
            if {"start_offset", "end_offset"} <= log.keys():
                start, end = log["start_offset"], log["end_offset"]
                p.need(type(start) is int and type(end) is int and 0 <= start <= end and
                       (last_log_end is None or start >= last_log_end),
                       "Device log interval overlaps/reuses a previous leaf's reason")
                last_log_end = end
        p.need(len({tuple(value) for value in sockets}) == len({tuple(value) for value in relays}) == 1,
               "Failure/recovery secretly changed the original SOCKS association tuples")
        for row in failed:
            before = rows[:rows.index(row)]
            if row.get("status") == "running" or not all(item.get("send_state") in
                    ("not_attempted", "send_error", "sent") for item in before):
                reason = "historical/incomplete send terminals cannot establish the failure observation"
            else:
                prior_sent = sum(item.get("send_state") == "sent" for item in before)
                p.need(row.get("first_udp") is (prior_sent == 0), "Failure leaf first-packet state contradicts actual sends")
                reason = _verify_failed(output, case, observed, specs[row["leaf"]], row, target_rows, processes)
            if reason:
                result["unverified_failed_leaves"].append({"leaf": row["leaf"], "reason": reason})
            else:
                result["verified_failed_leaves"].append(row["leaf"])
    result["failed_observations_verified"] = not result["unverified_failed_leaves"] and not result["unverified_cases"]
    return result
