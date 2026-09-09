"""Reviewed no-reply rollout helpers; no customer erasure or backups."""
import os,json,hashlib,stat,fcntl,sqlite3,time,urllib.request,shlex
from pathlib import Path
ROOT=Path('/root/backups/isluno-manual-e2e-20260909-a')
CLIENT=Path('/root/clients/mermaid/config/client.json')
DATABASE=Path('/root/clients/mermaid/data/state_registry.db')
ICP_DATA=Path('/root/unboks-internal-control-panel/data')
BACKEND='sha256:2be4255cecfce5a7355e7868570cbfb7bddfec119bdb7d8f70391c7990df8fb9'
ICP_IMAGE='sha256:2e547a43021e2c3f4e98b3404a0d4780d19af9c0cf751d84c25745eb6acff77c'
HELPER_WRAPPER="import runpy,sys\nsys.argv=sys.argv[1:]\ntry:runpy.run_path(sys.argv[0],run_name='__main__')\nexcept SystemExit as exc:\n if exc.code not in (None,0,1):raise\n"
EVENTS=[];LOCKS=[];STATE={'gates_closed':False,'sealed':False,'image_changed':False,'open':False,'rolled_back':False}


def event(phase,**extra):
 EVENTS.append({'phase':phase,**extra})
 print(json.dumps({'progress':phase}),flush=True)


def checked_read(path,limit=268435456):
 fd=safe_open(str(path))
 try:
  s=os.fstat(fd);require(stat.S_ISREG(s.st_mode) and s.st_nlink==1 and s.st_size<=limit,'file_shape')
  chunks=[];size=0
  while b:=os.read(fd,65536):size+=len(b);require(size<=limit,'file_size');chunks.append(b)
  after=os.fstat(fd);require((s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns)==(after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns),'file_changed')
  return b''.join(chunks)
 finally:os.close(fd)


def digest(path):
 fd=safe_open(str(path))
 try:
  before=os.fstat(fd);require(stat.S_ISREG(before.st_mode) and before.st_nlink==1 and before.st_size<=268435456,'digest_shape')
  h=hashlib.sha256();size=0
  while raw:=os.read(fd,65536):size+=len(raw);require(size<=268435456,'digest_size');h.update(raw)
  after=os.fstat(fd);require((before.st_ino,before.st_size,before.st_mtime_ns,before.st_ctime_ns)==(after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns) and size==before.st_size,'digest_changed')
  return h.hexdigest()
 finally:os.close(fd)
def inspect(name):return json.loads(bounded_command(['docker','inspect',name],timeout=5,cap=262144))[0]
def cmd(args,seconds=30):return bounded_command(args,timeout=seconds,cap=65536)
def unit(name):return dict(line.split('=',1) for line in cmd(['systemctl','show',name,'--property=LoadState,ActiveState,SubState,MainPID'],10).splitlines() if '=' in line)
def env(x):return dict(v.split('=',1) for v in x['Config'].get('Env',[]) if '=' in v)
def assert_stopped(name):
 x=inspect(name);require(not x['State']['Running'] and x['State']['Pid']==0 and x['State']['ExitCode']==0,'container_not_cleanly_stopped')

def lease(path,exclusive,seconds=15):
 fd=safe_open(str(path));s=os.fstat(fd);require(stat.S_ISREG(s.st_mode) and s.st_uid==0 and s.st_nlink==1,'lease_shape')
 deadline=time.monotonic()+seconds
 while True:
  try:fcntl.flock(fd,(fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)|fcntl.LOCK_NB);break
  except BlockingIOError:
   if time.monotonic()>=deadline:os.close(fd);raise Rejected('lease_busy')
   time.sleep(.05)
 LOCKS.append(fd);return fd

def release_lock(fd):
 LOCKS.remove(fd);os.close(fd)
def runtime(code,seconds=10):
 return json.loads(cmd(['docker','run','--rm','--pull','never','--network','none','--env','PYTHONPATH=/app','--env','CLIENT_CONFIG_PATH=/app/config/client.json','--env','TENANT_ID=mermaid','--env','TENANT_ACCOUNT_ALLOWLIST_REQUIRED=true','--mount','type=bind,src=/root/clients/mermaid/config,dst=/app/config,readonly','--mount','type=bind,src=/root/clients/mermaid/data,dst=/app/data','--entrypoint','python',BACKEND,'-c',code],seconds))
