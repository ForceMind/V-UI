"""Independent installed HTTP-01 responder for a disposable certificate fixture."""
import argparse
import http.client
import os
from pathlib import Path
import signal
import sys
import threading
import time


def serve(request, output):
    from scripts.low_resource_certificates import read, write, observed_handler
    from scripts.low_resource_service_tree import identity, observed_allocator
    payload=Path(request['payload']).resolve();root=Path(request['root'])
    sys.path.insert(0,str(payload))
    from app.certificates.http01 import ChallengeServer, handler_for
    if not Path(sys.modules[ChallengeServer.__module__].__file__).resolve().is_relative_to(payload):
        raise RuntimeError('Responder must import actual installed handler')
    manager=request['manager_identity']
    current=identity(os.getpid())
    allocator=observed_allocator(os.getpid())
    if (current['ppid']!=manager['pid'] or current['cgroup']!=manager['cgroup']
            or current['process_group']!=current['pid'] or allocator!={'mmap_threshold':None,'malloc_tunable_present':False}):
        raise RuntimeError('Independent responder inheritance mismatch')
    report=dict(outcome='running',identity=current,allocator=allocator,imported_installed_handler=True,
                started_monotonic=time.monotonic(),challenges=[],certbot_processes=[],observation_errors=[],
                cleanup_complete=False,drained=False)
    lock=threading.RLock();stop=threading.Event();pending=set();threads=set()
    def checkpoint():
        with lock:write(output,report)
    def phase():
        if identity(manager['pid'])!=manager:raise RuntimeError('Manager changed while responder active')
        return read(root/'phase.json')
    handler=observed_handler(handler_for(root/'managed/http-webroot'),root,report,phase,lock,on_change=checkpoint)
    class Tracked(ChallengeServer):
        def process_request(self,request,address):
            with lock:pending.add(id(request))
            try:super().process_request(request,address)
            finally:
                if request.fileno()==-1:
                    with lock:pending.discard(id(request))
        def process_request_thread(self,request,address):
            with lock:threads.add(threading.current_thread())
            try:super().process_request_thread(request,address)
            finally:
                try:checkpoint()
                finally:
                    with lock:pending.discard(id(request))
    server=Tracked(('127.0.0.1',request.get('http_port',0)),handler)
    thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.05},daemon=True)
    for name in (signal.SIGTERM,signal.SIGINT):signal.signal(name,lambda *_:stop.set())
    try:
        thread.start()
        connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
        try:
            connection.request('GET','/fixture-readiness')
            response=connection.getresponse();response.read()
            if response.status!=404:raise RuntimeError('Responder readiness probe failed')
        finally:connection.close()
        checkpoint()
        write(root/'responder.json',dict(http_port=server.server_port,pid=os.getpid(),
            starttime_ticks=current['starttime_ticks'],service_cgroup=current['cgroup'],unit=request['unit'],
            source_commit=request['source_commit'],identity=current,allocator=allocator))
        deadline=time.monotonic()+840
        while not stop.wait(.1):
            if identity(manager['pid'])!=manager or not thread.is_alive():raise RuntimeError('Responder parent/server stopped')
            if time.monotonic()>deadline:raise RuntimeError('Responder residency deadline exceeded')
        report['outcome']='passed'
    except Exception as exc:
        report.update(outcome='failed',error_type=type(exc).__name__)
    finally:
        server.shutdown();server.server_close();thread.join(timeout=3)
        deadline=time.monotonic()+6
        while True:
            with lock:active=list(threads);remaining=len(pending)
            for item in active:item.join(timeout=max(0,min(.1,deadline-time.monotonic())))
            if remaining==0 and all(not item.is_alive() for item in active):break
            if time.monotonic()>=deadline:break
        with lock:
            report.update(stopped_monotonic=time.monotonic(),active_requests=len(pending),
                final_event_count=len(report['challenges']),drained=not pending and all(not item.is_alive() for item in threads),
                cleanup_complete=not thread.is_alive())
            if not report['drained'] or not report['cleanup_complete'] or report['observation_errors']:report['outcome']='failed'
            checkpoint()
    return 0 if report['outcome']=='passed' else 1


def main():
    from scripts.low_resource_acceptance import require_hosted_runner
    from scripts.low_resource_certificates import read
    require_hosted_runner()
    parser=argparse.ArgumentParser();parser.add_argument('--request',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();return serve(read(args.request),args.output)


if __name__=='__main__':
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    raise SystemExit(main())
