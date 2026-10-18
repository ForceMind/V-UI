"""Two small packets, one original association, independent release/availability."""
import time
from packet_boundary import json_read, need, exchange, save, sha
from socks_observation import Observation, observable_socks_class, FILE

def exercise(f,target,case,helper,c,psutil,rows,persist,process_records):
    events=c.life.Events(persist.parent/'lifecycle-observation.json',case['id'])
    source=json_read(persist.parent.parent/'input-identity.json')['source_identity']
    roles=[r for r in process_records if r['case']==case['id'] and r['role']=='device-mihomo']
    need(len(roles)==1,'Missing exact device role')
    observation=Observation(persist.parent/FILE,case,f.client,psutil,c.observer,source,roles[0])
    association=None; proof=None
    def capture(phase,hop=None,hash_executable=False):
        observation.capture(phase,hop,hash_executable=hash_executable)
        need(not observation.data['errors'],'Passive identity failure prevents continuation')
        if phase!='after_local_cleanup':
            identity=observation.data['associations'][0]
            actual=observation.data['samples'][-1]['associations'][0]
            need(actual['control_before']==actual['control_after']==identity['control'] and
                 actual['application']==identity['application'] and actual['relay']==identity['relay'],
                 'Original application/control identity changed before recovery')
    try:
        watch=c.life.SocketWatch(f,psutil,events)
        before=watch.baseline(); baseline_index=watch.samples.index(before)
        association=helper.record_association(observable_socks_class(helper)(f.client_port))
        observation.add(association,'initial')
        exchange(association,target,case,case['packets'][0],helper,rows,persist)
        live=watch.snapshot('after_large_business');capture('after_large_business',live,True)
        leg=c.life.paired_leg(before,live,f.upstream_port)
        c.life.verify_new_sockets(before,live,leg,ingress_port=f.ingress_port)
        last=rows[0]['reply_monotonic'];deadline=last+6;gone=None
        observation.data.update(last_business_reply=last,deadline=deadline)
        while time.monotonic()<deadline:
            sample=watch.snapshot('large_then_idle_diagnostic');capture('release_window',sample)
            if not c.life.leg_present(sample,leg):
                if gone is None and sample['sample_completed_monotonic']<=deadline:gone=sample
            elif gone is not None:raise ValueError('Released inode reappeared within diagnostic window')
            time.sleep(.03)
        final=watch.snapshot('large_then_idle_diagnostic_window_complete');capture('window_complete',final)
        proof={'mode':case['mode'],'baseline':before,'live':live,'leg':leg,'last_business_reply':last,
               'deadline':deadline,'first_absence':gone,'ingress_port':f.ingress_port,'final':final,
               'samples':watch.samples[baseline_index:],'prebaseline_samples':watch.samples[:baseline_index],
               'release_passed':gone is not None and not c.life.leg_present(final,leg)}
        events.event('small_endpoint_window',proof=proof)
        # Unlike strict D, absence is measured independently of availability.
        # This is a distinct new payload, one send, and never a retry.
        capture('before_recover')
        exchange(association,target,case,case['packets'][1],helper,rows,persist)
        capture('after_recover')
        events.data['status']='observed';events.flush()
    except Exception as exc:
        observation.capture('failure')
        events.data.update(status='error',error={'type':type(exc).__name__,'message':c.life.clean(str(exc))})
        events.flush();raise
    finally:
        if association is not None:
            association.close()
            need(association.control.fileno()==association.stream.fileno()==-1,'Application descriptors survived')
            observation.capture('after_local_cleanup',hash_executable=True)
        observation.finish()
    return {'proof':proof,'observation_sha256':sha(events.path)}
