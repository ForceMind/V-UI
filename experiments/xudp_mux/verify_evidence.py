"""Reject missing/skipped tests and separate route copies from parent writer."""
import json
from pathlib import Path
import re

UNIT_CASES = (
 'InFlightCancelAndSibling','QueuedCancel','PartialFrameCancel','LargeTCP',
 'LargeTCPCancelPartialCount','EveryOutputEntrypoint','CloseRaceAndTombstoneReuse',
 'UnknownKeepFlood','CapacityNewAndParentCancel','ParentErrorReleasesWaiters',
 'RejectOversizedUDP','LargeConcurrentTCPCallsStayContiguous','CancelLinearization',
 'RejectedPayloadConsumedBeforeWaiting','SameIDNewPayloadBeforeOldEnd','CanceledBufferOwnership','CompletionBeforeCloseReportsSuccess',
)
ENCRYPTED_CASES = ('TestMuxEncryptedAES','TestMuxEncryptedChaCha')
ROUTE_CASES = ('TestMuxRouteReplyBackpressure','TestMuxRouteEndBackpressure')

def need(condition,message):
    if not condition:raise ValueError(message)

def rows(path):
    data=[json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
    need(bool(data),'Empty Go evidence')
    need(all(isinstance(row,dict) for row in data),'Invalid Go row')
    return data

def verify_tests(data,names):
    terminal={}
    for row in data:
        if row.get('Action') in ('pass','skip','fail') and row.get('Test'):
            name=row['Test']
            need(name not in terminal,'Duplicate test terminal')
            terminal[name]=row['Action']
    top={name:result for name,result in terminal.items() if '/' not in name}
    need(set(top)==set(names),'Missing or unexpected top-level tests')
    need(all(result=='pass' for result in terminal.values()),'Failed/skipped tests')
    if any(name.startswith('TestMuxBounded') for name in names):
        expected=set(names)|{'TestMuxBoundedPartialFrameCancel/'+suffix for suffix in ('b','e','w')}
        need(set(terminal)==expected,'Missing or unexpected subtest leaves')
    need(any(row.get('Action')=='pass' and 'Test' not in row for row in data),'Package did not pass')
    return len(terminal)

def verify_route(path,arm):
    data=rows(path);verify_tests(data,ROUTE_CASES)
    events=[]
    for row in data:
        output=row.get('Output','')
        if 'XUDP_ROUTE_LIFECYCLE ' in output:
            events.append(json.loads(output.split('XUDP_ROUTE_LIFECYCLE ',1)[1]))
    for name in ROUTE_CASES:
        e=[row for row in events if row['case']==name]
        snapshots=[row for row in e if row['event']=='mux_arm_snapshot']
        need(len(snapshots)==1,'Missing unique pre-cleanup snapshot')
        s=snapshots[0]
        need(s['arm']==arm,'Wrong arm')
        need(s['parent_writers']==(1 if arm=='mux' else 0),'Hidden/multiple parent writer')
        expected=0 if arm=='mux' else (2 if 'Reply' in name else 1)
        need(s['route_copies']==expected,'Unexpected actual route-copy count')
        if arm=='mux':need(s['route_callbacks']==0 and s['cancelers']==0,'Retained callback/timer worker')
        need(s['parent_transport_open'] is True and s['owned_outbound_closed'] is True,'Ownership/parent boundary lost')
        restore=[row for row in e if row['event']=='restore_reads'];cleanup=[row for row in e if row['event']=='cleanup_start']
        need(len(restore)==len(cleanup)==1 and s['seq']<restore[0]['seq']<cleanup[0]['seq'],'Snapshot contaminated by cleanup')
        joined=[row for row in e if row['event']=='cleanup_joined']
        need(len(joined)==1,'Missing cleanup join')
        j=joined[0]
        for key in ('route_copy_workers','route_callback_workers','canceler_workers','mux_cleanup_workers','active_transport_writes'):
            need(j[key]==0,'Unjoined worker: '+key)
        need(j['test_owned_workers_joined'] is True and j['actual_route_workers_joined'] is True,'Incomplete joins')
    return {'arm':arm,'cases':len(ROUTE_CASES),'product_qualified':False}

def verify_all(directory):
    directory=Path(directory)
    result={'route':[verify_route(directory/(arm+'-route.stdout'),arm) for arm in ('route-only','mux')], 'unit':{},'encrypted':{},'product_qualified':False}
    for label in ('unit-race','unit-debug'):
        result['unit'][label]=verify_tests(rows(directory/('mux-'+label+'.stdout')),['TestMuxBounded'+name for name in UNIT_CASES])
    for arm in ('route-only','mux'):
        for label in ('encrypted-race','encrypted-debug'):
            result['encrypted'][arm+'-'+label]=verify_tests(rows(directory/(arm+'-'+label+'.stdout')),ENCRYPTED_CASES)
    (directory/'VERIFICATION.json').write_text(json.dumps(result,indent=2)+'\n')
    return result
