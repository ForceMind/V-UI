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
 for index,path in enumerate(PATHS):
  base=invoke(index);check_body(path,base);digest=hashlib.sha256(base).hexdigest()
  times=[]
  for attempt in range(3):
   start=time.perf_counter();cpu=time.process_time();body=invoke(index)
   times.append({'wall':time.perf_counter()-start,'cpu':time.process_time()-cpu})
   assert hashlib.sha256(body).hexdigest()==digest
  profile=cProfile.Profile();profile.enable();body=invoke(index);profile.disable()
  assert hashlib.sha256(body).hexdigest()==digest
  profile.dump_stats(str(args.output_dir/('route-'+str(index)+'.pstats')))
  stats=pstats.Stats(profile)
  top=[]
  for (file,line,name),(primitive,calls,self_seconds,cumulative_seconds,callers) in sorted(stats.stats.items(),key=lambda x:x[1][3],reverse=True):
   top.append(dict(file=file.replace(str(ROOT)+'/', ''),line=line,name=name,calls=calls,self_seconds=self_seconds,cumulative_seconds=cumulative_seconds))
  result['routes'].append(dict(path=path,body_bytes=len(base),sha256=digest,runs=times,profile_rows=top))
(args.output_dir/'profile.json').write_text(json.dumps(result,indent=2)+'\n')
for row in result['routes']:
 print(row['path'],'mean_wall',statistics.mean(x['wall'] for x in row['runs']),'mean_cpu',statistics.mean(x['cpu'] for x in row['runs']),'bytes',row['body_bytes'])
 for item in row['profile_rows'][:8]: print(' ',item['name'],item['calls'],round(item['self_seconds'],4),round(item['cumulative_seconds'],4),item['file'].split('/')[-1])
