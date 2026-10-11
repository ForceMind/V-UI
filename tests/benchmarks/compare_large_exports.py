import argparse,cProfile,hashlib,json,os,platform,pstats,statistics,subprocess,sys,tempfile,time
from pathlib import Path
parser=argparse.ArgumentParser(description='Temporary fake data direct-route profiling; not a cgroup or HTTP acceptance test')
parser.add_argument('--source-root',type=Path,required=True)
parser.add_argument('--output-dir',type=Path,required=True)
parser.add_argument('--expected-commit',required=True)
args=parser.parse_args()
ROOT=args.source_root.resolve();args.output_dir.mkdir(parents=True,exist_ok=True)
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
assert head==args.expected_commit
assert not subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True).strip()
os.sched_setaffinity(0,{min(os.sched_getaffinity(0))})
result={'head':head,'scope':'assistant local direct synchronous route bodies with temporary fake 1000-node DB; one CPU affinity, no cgroup cap; excludes HTTP/auth/concurrency; not resource acceptance','python':sys.version,'kernel':platform.release(),'machine':platform.machine(),'libc':platform.libc_ver(),'affinity':sorted(os.sched_getaffinity(0)),'routes':[]}
with tempfile.TemporaryDirectory(prefix='vui-local-export-profile-') as folder:
 data=Path(folder);os.environ['VUI_DATA_DIR']=folder
 from app.models.database import init_db,SessionLocal
 from scripts.low_resource_data import seed_nodes,routing_fixture,check_body,PATHS
 from loopback_helpers import certificate_files
 from app.services.routing_store import read_snapshot,save_routing
 from app.api import subscription,routing
 from starlette.requests import Request
 init_db();ca,cert,key=certificate_files(data,'local-profile')
 seed_nodes(sys.executable,ROOT,data,cert,key)
 save_routing(routing_fixture(),read_snapshot()['revision'])
 request=Request({'type':'http','scheme':'https','server':('127.0.0.1',443),'path':'/','query_string':b'','headers':[]})
 def invoke(index):
  with SessionLocal() as db:
   if index==0:value=subscription.raw_subscription(request,host='127.0.0.1',core=None,db=db).encode()
   elif index==1:value=subscription.singbox_subscription(request,host='127.0.0.1',core=None,db=db).body
   elif index==2:value=subscription.mihomo_subscription(request,host='127.0.0.1',core=None,db=db).body
   else:value=json.dumps(routing.saved_preview(),ensure_ascii=False).encode()
  return value
 from unittest.mock import patch
 from app.services import routing_validation as public, mihomo_routing as planner
 namespace=vars(planner).copy()
 oracle=ROOT/'tests/fixtures/rule_plan_405b64a.py'
 exec(compile(oracle.read_text(),str(oracle),'exec'),namespace)
 reference=namespace['build_rule_plan']; indexed=public._plan
 result['scope'] += '; alternating original-405 frozen planner vs index in same process, same fake DB and validated identical bodies'
 result['reference_head']='405b64a34c0ca691f14afe39eddd60bb1885b300'
 for index,path in enumerate(PATHS):
  with patch.object(public,'_plan',reference): base=invoke(index)
  check_body(path,base);digest=hashlib.sha256(base).hexdigest()
  times={'reference':[],'indexed':[]}
  for attempt in range(8):
   order=[('reference',reference),('indexed',indexed)]
   if attempt%2:order.reverse()
   for label,implementation in order:
    with patch.object(public,'_plan',implementation):
     start=time.perf_counter();cpu=time.process_time();body=invoke(index)
     times[label].append({'wall':time.perf_counter()-start,'cpu':time.process_time()-cpu})
    assert hashlib.sha256(body).hexdigest()==digest
  profiles={}
  for label,implementation in [('reference',reference),('indexed',indexed)]:
   with patch.object(public,'_plan',implementation):
    profile=cProfile.Profile();profile.enable();body=invoke(index);profile.disable()
   assert hashlib.sha256(body).hexdigest()==digest
   profile.dump_stats(str(args.output_dir/(str(index)+'-'+label+'.pstats')))
   stats=pstats.Stats(profile)
   profiles[label]=[dict(file=file.replace(str(ROOT)+'/', ''),line=line,name=name,calls=calls,self_seconds=self_seconds,cumulative_seconds=cumulative_seconds) for (file,line,name),(primitive,calls,self_seconds,cumulative_seconds,callers) in sorted(stats.stats.items(),key=lambda x:x[1][3],reverse=True)[:25]]
  result['routes'].append(dict(path=path,body_bytes=len(base),sha256=digest,runs=times,profile_rows=profiles))
(args.output_dir/'comparison.json').write_text(json.dumps(result,indent=2)+'\n')
for row in result['routes']:
 print(row['path'],row['sha256'])
 for label in ('reference','indexed'):
  print(label,'mean_wall',statistics.mean(x['wall'] for x in row['runs'][label]),'mean_cpu',statistics.mean(x['cpu'] for x in row['runs'][label]))
