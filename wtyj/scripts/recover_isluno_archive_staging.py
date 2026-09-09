"""One separately authorized archive recovery; defaults offline, no service changes."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parent))
from inspect_isluno_target import ssh_argv
ROOT='/root/backups/isluno-manual-e2e-20260909-a/archives'
REFERENCE='calvin-isluno-staging-c'
OVERALL_SECONDS=2100
ARTIFACTS=[
 ('mermaid-image.tar','tmp/isluno-image-e2deab8/isluno-image.tar',169445888,'b1ec5bb6733efb1cfa092ee7d7ac884b7715f73ebb340e3d66c7d98cd98151da',900),
 ('icp-image.tar','/Users/calvin/Projects/isluno-control-plane-20260909/tmp/isluno-icp-image-70ea4a7/isluno-icp-image.tar',63651328,'06f0a11c8b9d3a7d2dc1d86a9dbd39b6342d5956a73275b444f9ff4343ab6e46',600),
 ('config-assets.tar.gz','tmp/isluno-release-artifacts/isluno-config-default-off.tar.gz',39521,'d3765cd214b55501b5cf26ec152f8cd49fb733eba0feed09f6d9b40836fa3c63',60),
 ('frontend.tar.gz','tmp/isluno-release-artifacts/isluno-frontend-static.tar.gz',738983,'9faecd003e4d32da89bf032e755307b39e879e937414e753eb22b3f2090d4dd2',60),
 ('release-helpers.tar','tmp/isluno-release-helpers.tar',81920,'61a218c70bafae0e03b8bd92f7a1638a5b46177574391aad41b25482d608f658',60),
]

class Failed(Exception):
 def __init__(self,code,metrics=None):self.code=code;self.metrics=metrics or {};super().__init__(code)


def command(argv,timeout,cap=65536,data=None):
 """Bound live output/time, record exit status/counts, never persist raw stderr."""
 started=time.monotonic();deadline=started+timeout;out=bytearray();counts={'stdout_bytes':0,'stderr_bytes':0};proc=None;sel=selectors.DefaultSelector();failure=None;exit_code=None
 try:
  proc=subprocess.Popen(argv,stdin=subprocess.PIPE if data is not None else subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
  for name,stream in [('stdout_bytes',proc.stdout),('stderr_bytes',proc.stderr)]:os.set_blocking(stream.fileno(),False);sel.register(stream,selectors.EVENT_READ,name)
  pending=memoryview(data or b'')
  if proc.stdin:
   os.set_blocking(proc.stdin.fileno(),False)
   if pending:sel.register(proc.stdin,selectors.EVENT_WRITE,'stdin')
   else:proc.stdin.close()
  while sel.get_map():
   left=deadline-time.monotonic()
   if left<=.1:raise Failed('command_timeout')
   for key,event in sel.select(min(left-.1,.1)):
    if key.data=='stdin':
     n=os.write(key.fd,pending[:8192]);pending=pending[n:]
     if not pending:sel.unregister(key.fileobj);key.fileobj.close()
     continue
    raw=os.read(key.fd,8192)
    if not raw:sel.unregister(key.fileobj);continue
    counts[key.data]+=len(raw)
    if sum(counts.values())>cap:raise Failed('command_output_limit')
    if key.data=='stdout_bytes':out.extend(raw)
  try:exit_code=proc.wait(timeout=max(.001,deadline-time.monotonic()))
  except subprocess.TimeoutExpired:raise Failed('command_timeout')
  if exit_code!=0:raise Failed('command_exit_nonzero')
 except Failed as e:failure=e.code
 except Exception:failure='command_io_error'
 finally:
  sel.close()
  if proc:
   try:os.killpg(proc.pid,signal.SIGKILL)
   except ProcessLookupError:pass
   try:
    observed=proc.wait(timeout=1)
    if exit_code is None:exit_code=observed
   except subprocess.TimeoutExpired:failure='command_cleanup_timeout'
   for stream in (proc.stdin,proc.stdout,proc.stderr):
    if stream and not stream.closed:stream.close()
 metrics=dict(counts,exit_code=exit_code,elapsed_seconds=round(time.monotonic()-started,3),limit_seconds=timeout)
 if failure:raise Failed(failure,metrics)
 return bytes(out),metrics


REMOTE_COMMON="""import os,stat,json,hashlib,signal,time
from pathlib import Path
ROOT=Path('/root/backups/isluno-manual-e2e-20260909-a/archives')
def expired(*_):raise TimeoutError()
signal.signal(signal.SIGALRM,expired);signal.alarm(25)
def require(ok):
 if not ok:raise ValueError()
def check_dir(p):
 for a in [*reversed(p.parents),p]:require(not a.is_symlink())
 info=p.stat();require(stat.S_ISDIR(info.st_mode) and info.st_uid==os.geteuid() and stat.S_IMODE(info.st_mode)==0o700)
def project(p,maximum):
 fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW)
 try:
  before=os.fstat(fd);require(stat.S_ISREG(before.st_mode) and before.st_uid==os.geteuid() and before.st_nlink==1 and before.st_size<=maximum)
  h=hashlib.sha256();size=0
  while raw:=os.read(fd,65536):size+=len(raw);require(size<=maximum);h.update(raw)
  after=os.fstat(fd);current=os.stat(p,follow_symlinks=False)
  key=lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
  require(key(before)==key(after)==key(current) and size==before.st_size)
  return {'bytes':size,'sha256':h.hexdigest()}
 finally:os.close(fd)
check_dir(ROOT);check_dir(ROOT.parent)
"""
INSPECT=REMOTE_COMMON+"""
first=ROOT/'mermaid-image.tar'
try:r=project(first,169445888);r['present']=True
except FileNotFoundError:r={'present':False}
require(not (ROOT/'recovery-c').exists() and not (ROOT/'recovery-c').is_symlink())
print(json.dumps({'status':'inspected','first_archive':r,'services_changed':False}))
"""
CREATE=REMOTE_COMMON+"""
(ROOT/'recovery-c').mkdir(mode=0o700)
print(json.dumps({'status':'new_transfer_directory_created','existing_files_preserved':True}))
"""


def first_complete(observation):
 if observation.get('present') is False:return False
 if observation.get('present') is not True:raise Failed('inspection_shape')
 size=observation.get('bytes');digest=observation.get('sha256')
 if type(size) is not int or not 0<=size<=ARTIFACTS[0][2] or not isinstance(digest,str) or len(digest)!=64:raise Failed('inspection_shape')
 return size==ARTIFACTS[0][2] and digest==ARTIFACTS[0][3]


def preserve_payload(directory,phase,payload):
 if payload is None:return {}
 if phase not in ('inspect_only_first_archive','create_new_transfer_directory','verify_exact_artifacts') or not isinstance(payload,bytes):raise Failed('payload_scope')
 name=phase+'.payload.py'
 fd=os.open(directory/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
 with os.fdopen(fd,'wb') as f:f.write(payload);f.flush();os.fsync(f.fileno())
 return {'payload_file':name,'payload_sha256':hashlib.sha256(payload).hexdigest(),'payload_bytes':len(payload)}


def main(argv=None):
 argv=sys.argv[1:] if argv is None else argv
 if not argv:print('{"status":"offline_default","host_reads":false,"mutations":false}');return 0
 if argv!=['--execute-approved-recovery','--approval-reference',REFERENCE]:print('{"status":"stopped","code":"explicit_recovery_approval_required"}');return 1
 repo=Path(__file__).resolve().parents[2];receipts=repo/'tmp/isluno-staging-c';started=time.monotonic();events=[];owns_receipts=False
 try:
  if receipts.exists() or receipts.is_symlink():
   print('{"status":"stopped","code":"recovery_already_dispatched"}');return 1
  rows=[]
  for name,local,size,digest,limit in ARTIFACTS:
   path=Path(local) if Path(local).is_absolute() else repo/local
   if path.stat().st_size!=size:raise Failed('local_artifact_size')
   with path.open('rb') as f:
    if hashlib.file_digest(f,'sha256').hexdigest()!=digest:raise Failed('local_artifact_digest')
   rows.append({'name':name,'local':str(path),'bytes':size,'sha256':digest,'transfer_limit':limit})
  try:receipts.mkdir(mode=0o700)  # One logical attempt, never rewrite an old receipt.
  except FileExistsError:
   print('{"status":"stopped","code":"recovery_already_dispatched"}');return 1
  owns_receipts=True
  def save():
   p=receipts/'receipt.json';p.write_text(json.dumps({'reference':REFERENCE,'events':events,'services_changed':False,'automatic_retry':False},indent=2));p.chmod(0o600)
  events.append({'phase':'dispatch','at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'runner_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'overall_limit_seconds':OVERALL_SECONDS,'maximum_uploads':5,'upload_concurrency':1,'maximum_original_artifact_bytes':233957640,'transfer_limit_seconds':[900,600,60,60,60]});save()
  def call(phase,args,limit,payload=None):
   left=OVERALL_SECONDS-(time.monotonic()-started)
   if left<=0:raise Failed('overall_timeout')
   begin={'phase':phase,'at':datetime.datetime.now(datetime.timezone.utc).isoformat()}
   begin.update(preserve_payload(receipts,phase,payload));events.append(begin);save()
   try:raw,metrics=command(args,min(limit,left),data=payload)
   except Failed as e:begin.update(status='failed',code=e.code,**e.metrics);save();raise
   begin.update(status='complete',**metrics);save();print(json.dumps(begin),flush=True)
   return raw
  observed=json.loads(call('inspect_only_first_archive',ssh_argv(),30,INSPECT.encode()))
  if observed.get('status')!='inspected':raise Failed('inspection_shape')
  complete=first_complete(observed['first_archive']);events.append({'phase':'first_archive_disposition','complete_original_reused':complete,'partial_or_unverified_original_preserved':not complete,'planned_uploads':4 if complete else 5,'planned_artifact_bytes':233957640-(169445888 if complete else 0)});save()
  created=json.loads(call('create_new_transfer_directory',ssh_argv(),30,CREATE.encode()))
  if created.get('status')!='new_transfer_directory_created':raise Failed('create_receipt_shape')
  verified=[]
  for index,row in enumerate(rows):
   remote=ROOT+'/'+row['name'] if index==0 and complete else ROOT+'/recovery-c/'+row['name']
   if not(index==0 and complete):
    args=['scp','-q','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectionAttempts=1','-o','ConnectTimeout=10','-o','ServerAliveInterval=15','-o','ServerAliveCountMax=2',row['local'],'root@108.61.192.52:'+remote]
    call('transfer_'+row['name'],args,row['transfer_limit'])
   verified.append({'name':row['name'],'path':remote,'bytes':row['bytes'],'sha256':row['sha256']})
  code=REMOTE_COMMON+'\nrows='+repr(verified)+"""
check_dir(ROOT/'recovery-c')
for row in rows:
 result=project(Path(row['path']),row['bytes'])
 require(result=={'bytes':row['bytes'],'sha256':row['sha256']})
print(json.dumps({'status':'all_archives_verified','artifacts':rows,'existing_files_preserved':True,'services_changed':False}))
"""
  result=json.loads(call('verify_exact_artifacts',ssh_argv(),120,code.encode()))
  if result.get('status')!='all_archives_verified' or result.get('artifacts')!=verified:raise Failed('validation_receipt_shape')
  p=receipts/'verified-artifacts.json';p.write_text(json.dumps(result,indent=2));p.chmod(0o600)
  events.append({'phase':'finished','status':'all_archives_verified','elapsed_seconds':round(time.monotonic()-started,3)});save();print('{"status":"all_archives_verified","services_changed":false}');return 0
 except Exception as e:
  code=e.code if isinstance(e,Failed) else 'local_or_receipt_error'
  if owns_receipts:
   events.append({'phase':'stopped','code':code,'elapsed_seconds':round(time.monotonic()-started,3)})
   p=receipts/'stop.json'
   fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
   with os.fdopen(fd,'w') as f:json.dump({'code':code,'events':events,'services_changed':False,'automatic_retry':False},f,indent=2)
  print(json.dumps({'status':'stopped','code':code,'services_changed':False,'automatic_retry':False}));return 1

if __name__=='__main__':raise SystemExit(main())
