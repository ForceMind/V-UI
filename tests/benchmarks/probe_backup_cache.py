"""Fixed fake backup on disk; per-file mincore diagnostics, not a resource gate."""
import argparse,ctypes,hashlib,json,os,resource,sqlite3,subprocess,sys,tempfile,time
from pathlib import Path
parser=argparse.ArgumentParser();parser.add_argument('--source-root',type=Path,required=True);parser.add_argument('--expected-head',required=True);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--scratch-dir',type=Path,required=True)
args=parser.parse_args();root=args.source_root.resolve();sys.path.insert(0,str(root))
assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip()==args.expected_head
assert not subprocess.check_output(['git','status','--porcelain'],cwd=root,text=True).strip()
from app import release_tools as tools
from scripts.low_resource_data import generate_log_fixture
libc=ctypes.CDLL(None,use_errno=True)
libc.mmap.argtypes=[ctypes.c_void_p,ctypes.c_size_t,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_long];libc.mmap.restype=ctypes.c_void_p
libc.mincore.argtypes=[ctypes.c_void_p,ctypes.c_size_t,ctypes.c_void_p];libc.mincore.restype=ctypes.c_int
libc.munmap.argtypes=[ctypes.c_void_p,ctypes.c_size_t];libc.munmap.restype=ctypes.c_int
page=os.sysconf('SC_PAGE_SIZE')
def residency(path):
 size=path.stat().st_size
 if not size:return dict(size=0,resident_bytes=0)
 with path.open('rb') as handle:
  address=libc.mmap(None,size,1,1,handle.fileno(),0)
  if address==ctypes.c_void_p(-1).value:raise OSError(ctypes.get_errno(),'mmap')
  try:
   vector=(ctypes.c_ubyte*((size+page-1)//page))()
   if libc.mincore(address,size,vector):raise OSError(ctypes.get_errno(),'mincore')
   return dict(size=size,resident_bytes=sum(bool(x&1) for x in vector)*page,allocated_bytes=path.stat().st_blocks*512,inode=path.stat().st_ino)
  finally:libc.munmap(address,size)
report=dict(head=args.expected_head,scope='assistant disk-backed fixed fake log; mincore per file and process high-water RSS; no cgroup limit, no cache clearing, not resource acceptance',filesystem=subprocess.check_output(['df','-T',str(args.scratch_dir)],text=True),snapshots=[])
with tempfile.TemporaryDirectory(prefix='vui-backup-probe-',dir=args.scratch_dir) as directory:
 work=Path(directory);data=work/'data';data.mkdir(mode=0o700)
 with sqlite3.connect(data/'v-ui.db') as db:
  db.executescript('CREATE TABLE users(id INTEGER,username TEXT);CREATE TABLE inbounds(id INTEGER,remark TEXT);CREATE TABLE admin_sessions(token_hash TEXT);CREATE TABLE subscription_grants(revoked INTEGER);'+"INSERT INTO users VALUES(1,'fixture');INSERT INTO inbounds VALUES(1,'original');INSERT INTO admin_sessions VALUES('fake-hash');INSERT INTO subscription_grants VALUES(0);")
 evidence=generate_log_fixture(data);report['log']=evidence
 original_create=tools.create_archive;source=data/'resource-fixture/logs/large.log'
 def observe(phase,payload=None,destination=None):
  files={'original_log':residency(source)}
  if payload is not None:files['private_log']=residency(payload/'resource-fixture/logs/large.log')
  if destination is not None:files['archive']=residency(destination)
  report['snapshots'].append(dict(phase=phase,files=files,process_maxrss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024))
 def wrapped(payload,destination,metadata):
  observe('private_snapshot_copied_before_hash_and_compress',payload)
  checksum=original_create(payload,destination,metadata)
  observe('archive_complete_private_snapshot_still_present',payload,destination)
  return checksum
 tools.create_archive=wrapped
 observe('source_generated')
 before=time.perf_counter();cpu=time.process_time();archive=work/'backup.zip';checksum=tools.backup(work,archive)
 report.update(wall_seconds=time.perf_counter()-before,cpu_seconds=time.process_time()-cpu,archive_sha256=checksum,archive_bytes=archive.stat().st_size)
 observe('backup_returned_private_snapshot_removed',destination=archive)
 assert not list(work.glob('.backup-*'))
 report['source_hash_after']=tools.file_digest(source);assert report['source_hash_after']==evidence['large_sha256']
 try:tools.restore(work,archive,'0'*64)
 except tools.ReleaseError:report['bad_digest_rejected']=True
 else:raise AssertionError('bad digest accepted')
 assert tools.file_digest(source)==evidence['large_sha256']
 with sqlite3.connect(data/'v-ui.db') as db:db.execute("UPDATE inbounds SET remark='modified'")
 tools.restore(work,archive,checksum)
 with sqlite3.connect(data/'v-ui.db') as db:
  assert db.execute('SELECT remark FROM inbounds').fetchone()[0]=='original'
  assert db.execute('SELECT count(*) FROM admin_sessions').fetchone()[0]==0
  assert db.execute('SELECT revoked FROM subscription_grants').fetchone()[0]==1
 assert tools.file_digest(data/'resource-fixture/logs/large.log')==evidence['large_sha256']
 assert len(list((work/'recovery').glob('before-*')))==1
 report.update(restored_and_revoked=True,prior_data_retained=True,temporary_cleanup=True)
args.output.write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({k:v for k,v in report.items() if k!='log'},indent=2))
