"""Independent small-packet raw proof replay, with no original D qualification."""
from collections import Counter
import json
from copy import deepcopy
from packet_boundary import need

def verify_window(case, result, process_rows, proof):
    need(proof.get("mode") == case["mode"], "Wrong large-packet lifecycle mode")
    by_role = {r["role"]: r["launches"][0] for r in process_rows}
    def sockets(snapshot):
        need(set(p["role"] for p in snapshot["processes"]) == {"domestic", "overseas"} and
             len(snapshot["processes"]) == 2, "Lifecycle hop snapshots incomplete")
        need(snapshot["monotonic"] <= snapshot["sample_completed_monotonic"], "Inverted socket sample")
        values = {}
        for p in snapshot["processes"]:
            launch = by_role[p["role"]]
            need(p["pid"] == launch["pid"] and p["create_time"] == launch["birth_time"] and not p["races"],
                 "Lifecycle PID birth changed or sampling race unresolved")
            for sock in p["sockets"]:
                key = (p["role"], p["pid"], sock["inode"])
                need(type(sock["inode"]) is int and sock["inode"] > 0 and key not in values,
                     "Invalid/duplicate actual socket inode")
                values[key] = sock
        return values
    prior = None
    for sample in proof.get("prebaseline_samples", []):
        sockets(sample)
        need((prior is None or sample["monotonic"] >= prior) and
             sample["sample_completed_monotonic"] <= proof["baseline"]["monotonic"],
             "Prebaseline readiness samples overlap the actual lifecycle window")
        prior = sample["sample_completed_monotonic"]
    baseline, live = sockets(proof["baseline"]), sockets(proof["live"])
    need(all(r["protocol"] == "tcp" and r["proc_state"] == "0A" for r in baseline.values()),
         "Lifecycle did not start from an idle process baseline")
    added = {k:v for k,v in live.items() if k not in baseline}
    port, ingress = proof["leg"]["upstream_port"], proof["ingress_port"]
    domestic = [(k,r) for k,r in added.items() if k[0] == "domestic" and r["protocol"] == "tcp" and
                r["remote"] == {"host":"127.0.0.2", "port":port}]
    overseas = [(k,r) for k,r in added.items() if k[0] == "overseas" and r["protocol"] == "tcp" and
                r["local"] == {"host":"127.0.0.2", "port":port} and r["proc_state"] == "01"]
    udp = [(k,r) for k,r in added.items() if k[0] == "overseas" and r["protocol"] == "udp" and
           r["local"]["host"] == "127.0.0.4"]
    parent = [(k,r) for k,r in added.items() if k[0] == "domestic" and r["protocol"] == "tcp" and
              r["local"] == {"host":"127.0.0.1", "port":ingress} and r["proc_state"] == "01"]
    need(len(domestic) == len(overseas) == len(udp) == len(parent) == 1 and len(added) == 4,
         "Actual association socket trio and parent are not exact")
    keys = {domestic[0][0], overseas[0][0], udp[0][0]}
    need(Counter(map(tuple, proof["leg"]["keys"])) == Counter(keys) and
         domestic[0][1]["local"] == overseas[0][1]["remote"] == proof["leg"]["domestic_local"],
         "Socket leg keys or reciprocal TCP tuple changed")
    first = result["packets"][0]
    need(udp[0][1]["local"] == {"host": first["target"]["source"][0], "port": first["target"]["source"][1]},
         "Observed UDP socket differs from actual target source tuple")
    last, deadline = proof["last_business_reply"], proof["deadline"]
    need(last == first["reply_monotonic"] and deadline == last + 6 and
         proof["baseline"]["sample_completed_monotonic"] <= last <= proof["live"]["monotonic"],
         "Lifecycle deadline not measured from actual last business reply")
    samples = proof["samples"]
    need(samples and samples[0] == proof["baseline"] and proof["live"] in samples and samples[-1] == proof["final"],
         "Lifecycle sample window omitted baseline/live/final")
    prior, absent = None, None
    for sample in samples:
        if prior is not None:
            need(sample["monotonic"] >= prior, "Nonmonotonic lifecycle samples")
        prior = sample["sample_completed_monotonic"]
        current = sockets(sample)
        need(current.keys() <= live.keys(), "Unrequested replacement socket appeared")
        if sample["monotonic"] >= proof["live"]["sample_completed_monotonic"]:
            if not keys & current.keys():
                absent = absent or sample
            else:
                need(absent is None, "Released inode reappeared")
    need(proof["final"]["monotonic"] >= deadline, "Incomplete six-second window")
    timely = absent if absent is not None and absent["sample_completed_monotonic"] <= deadline else None
    released = timely is not None and not keys & sockets(proof["final"]).keys()
    need(timely == proof["first_absence"] and proof["release_passed"] is released,
         "Forged or hidden measured release result")
    return released

def passive_identity(observer,case,row,roles,identity):
    from verify_socks_observation import descriptor, device_rows, peek_state
    from socks_observation import KIND
    need(type(observer['schema_version']) is int and observer['schema_version']==1 and observer['kind']==KIND and
         observer['case']==case['id'] and observer['mode']==case['mode'] and observer['source_identity']==identity and
         observer['product_qualified'] is False and observer['capacity_evaluated'] is False and not observer['errors'],
         'Passive scope/source/errors mismatch')
    projected=deepcopy(roles)
    device=[r for r in projected if r['role']=='device-mihomo']
    need(len(device)==1,'Missing exact passive device')
    # Observation captured the launch before clean shutdown; final stop records
    # are independently checked by the shared runtime verifier.
    device[0]['stops']=[]
    need(observer['device_role']==device[0],'Passive launch/config identity differs')
    original=row['application_associations'][0]
    need(len(observer['associations'])==1,'Passive association replaced')
    association=observer['associations'][0]
    need(association['label']=='initial' and all(association[k]==original[k] for k in ('control','application','relay')),
         'Passive and actual original application objects differ')
    need(observer['cleanup']==[{'label':'initial','control_fd':-1,'application_fd':-1}],'Passive cleanup differs')
    previous=None; races=0
    for sample in observer['samples']:
        need('error' not in sample and sample['started']<=sample['completed'] and
             (previous is None or sample['started']>=previous),'Passive sample timing/error')
        previous=sample['completed']
        _,_,stable=device_rows(sample,device[0]);races+=not stable
        need(len(sample['associations'])==1,'Passive sampled association replaced')
        a=sample['associations'][0];closed=sample['phase']=='after_local_cleanup'
        descriptor(a['control_before'],closed=closed);descriptor(a['application'],closed=closed)
        need(a['label']=='initial' and a['control_before']==a['control_after'] and a['relay']==association['relay'],
             'Passive control/relay changed')
        if not closed:need(a['control_before']==association['control'] and a['application']==association['application'],
                           'Original application descriptors changed')
        peek_state(a['peek'],a['control_before']['fd'])
        if sample['phase'] in ('after_large_business','after_local_cleanup'):
            need(sample['executable_sha256']==device[0]['sha256'],'Passive executable differs')
    need(observer['status']==('complete_with_inconclusive_samples' if races else 'complete'),'Passive races/status mismatch')
    return projected

def verify(p,case,row,roles,output,identity):
    path=output/case['id']/'lifecycle-observation.json'
    data=p.json_read(path)
    observer=p.json_read(output/case['id']/'socks-association-observation.json')
    passive_roles=passive_identity(observer,case,row,roles,identity)
    proofs=[e['proof'] for e in data['events'] if e['kind']=='small_endpoint_window']
    windows=[e for e in data['events'] if e.get('phase')=='large_then_idle_diagnostic']
    need(row['lifecycle_window_executed'] is bool(windows),'Window flag differs from raw samples')
    first_ok=bool(row['packets'] and row['packets'][0]['full_delivery_passed'])
    if not first_ok:
        need(not proofs and not windows and not row['recovery_attempted'] and len(row['packets'])<=1,
             'Failed 1200 prerequisite cannot claim window/recovery')
        need([s['phase'] for s in observer['samples']]==['failure','after_local_cleanup'],
             'Failed prerequisite has unexpected passive window')
        return {'window_executed':False,'release_passed':False,'next_packet_passed':False,'prerequisite_passed':False}
    need(len(proofs)==1 and windows and len(row['packets'])==2 and row['recovery_attempted'],
         'Complete small prerequisite lacks window and single next packet')
    proof=proofs[0]
    released=verify_window(case,row,roles,proof)
    raw_samples=[e['snapshot'] for e in data['events'] if e['kind']=='socket_snapshot']
    need(raw_samples==proof['prebaseline_samples']+proof['samples'],'Window proof omits/invents raw hop samples')
    first,recovery=row['packets']
    need(first['request']['bytes']==recovery['request']['bytes']==1200 and
         first['request']['sha256']!=recovery['request']['sha256'],'Next packet is not a distinct 1200-byte payload')
    need(recovery['send_calls'][0]['started']>=proof['final']['sample_completed_monotonic'] and
         first['send_calls'][0]['completed']<=proof['last_business_reply'], 'Send occurred inside quiet window')
    from verify_socks_observation import verify_case
    passive=verify_case(observer,case,row,passive_roles,identity,data)
    need(passive['business_recovery_passed'] is recovery['full_delivery_passed'],'Passive/raw recovery differs')
    if row.get('lifecycle'):
        need(row['lifecycle']=={'proof':proof,'observation_sha256':p.sha(path)},'Lifecycle return/raw digest differs')
    return {'window_executed':True,'release_passed':released,'next_packet_passed':recovery['full_delivery_passed'],
            'prerequisite_passed':True,'passive':passive}
