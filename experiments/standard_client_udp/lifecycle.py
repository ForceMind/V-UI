"""Frozen afbdba1 D lifecycle plus explicit Mihomo identity and passive I/O/FD records."""
import time
from packet_boundary import json_read, need, exchange, save, sha

def lifecycle_exercise(f, target, case, helper, c, psutil, rows, persist, process_records):
    events = c.life.Events(persist.parent / "lifecycle-observation.json", case["id"])
    from socks_observation import Observation, observable_socks_class, FILE
    source = json_read(persist.parent.parent / "input-identity.json")["source_identity"]
    device_roles = [r for r in process_records if r["case"] == case["id"] and r["role"] == "device-mihomo"]
    need(len(device_roles) == 1, "Missing exact device observation role")
    observation = Observation(persist.parent / FILE, case, f.client, psutil, c.observer, source, device_roles[0])
    SocksDatagrams = observable_socks_class(helper)
    association = None
    try:
        watch = c.life.SocketWatch(f, psutil, events)
        before = watch.baseline()
        baseline_index = watch.samples.index(before)
        association = helper.record_association(SocksDatagrams(f.client_port))
        observation.add(association, "initial")
        exchange(association, target, case, case["packets"][0], helper, rows, persist)
        save(persist, rows)
        live = watch.snapshot("after_large_business")
        observation.capture("after_large_business", live, hash_executable=True)
        leg = c.life.paired_leg(before, live, f.upstream_port)
        c.life.verify_new_sockets(before, live, leg, ingress_port=f.ingress_port)
        last = rows[0]["reply_monotonic"]
        observation.data.update(last_business_reply=last, deadline=last + 6)
        if case["mode"] == "close":
            observation.data["explicit_close"] = {"started": time.monotonic()}
            association.close()
            observation.data["explicit_close"]["completed"] = time.monotonic()
            observation.capture("after_explicit_close")
        deadline, gone = last + 6, None
        while time.monotonic() < deadline:
            sample = watch.snapshot("large_then_" + case["mode"])
            observation.capture("release_window", sample)
            if not c.life.leg_present(sample, leg):
                if gone is None:
                    need(sample["sample_completed_monotonic"] <= deadline, "Closure crossing sample is inconclusive")
                    gone = sample
            elif gone is not None:
                raise ValueError("Released association inode reappeared before idle/close window ended")
            time.sleep(.03)
        final = watch.snapshot("large_then_" + case["mode"] + "_window_complete")
        observation.capture("window_complete", final)
        need(gone is not None and not c.life.leg_present(final, leg), "Exact large-packet association sockets survived/reappeared within six seconds")
        # No raw session End is visible on a SOCKS interface. Report only the
        # endpoint socket/packet smoke, keeping the old raw-End suite separate.
        watch.events.event("large_endpoint_resources_released", leg=leg, deadline=deadline,
                           raw_session_end_observed=False, scope="additional_endpoint_smoke")
        if case["mode"] == "close":
            association = helper.record_association(SocksDatagrams(f.client_port))
            observation.add(association, "recovery")
        observation.capture("before_recover")
        exchange(association, target, case, case["packets"][1], helper, rows, persist)
        save(persist, rows)
        observation.capture("after_recover")
        proof = {"mode": case["mode"], "baseline": before, "live": live, "leg": leg,
                 "last_business_reply": last, "deadline": deadline, "first_absence": gone, "ingress_port": f.ingress_port,
                 "final": final, "samples": watch.samples[baseline_index:],
                 "prebaseline_samples": watch.samples[:baseline_index],
                 "recovery_leaf": rows[-1]["leaf"], "recovery_reply_monotonic": rows[-1]["reply_monotonic"]}
        watch.events.event("large_endpoint_proof", proof=proof)
        watch.events.data["status"] = "observed"
        watch.events.flush()
    except Exception as exc:
        observation.capture("failure")
        events.data["status"] = "error"
        events.data["error"] = {"type": type(exc).__name__, "message": c.life.clean(str(exc))}
        events.flush()
        raise
    finally:
        if association is not None:
            try:
                association.close()
                need(association.stream.fileno() == -1, "SOCKS application socket survived cleanup")
                need(association.control.fileno() == -1, "SOCKS control socket survived cleanup")
            except Exception as exc:
                events.data["status"] = "error"
                events.data["cleanup_error"] = {"type": type(exc).__name__, "message": c.life.clean(str(exc))}
                events.data.setdefault("error", events.data["cleanup_error"])
                events.flush()
                raise
            finally:
                observation.capture("after_local_cleanup", hash_executable=True)
                observation.finish()
        else:
            observation.finish()
    return {"proof": proof, "observation_sha256": sha(persist.parent / "lifecycle-observation.json")}

