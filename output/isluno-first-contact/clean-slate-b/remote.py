"""Approved coordinated cutover; returns with both admission gates closed."""
import os,json,hashlib,stat,fcntl,sqlite3,time,urllib.request,shlex
from pathlib import Path
ROOT=Path('/root/backups/isluno-manual-e2e-20260909-a')
CLIENT=Path('/root/clients/mermaid/config/client.json')
DATABASE=Path('/root/clients/mermaid/data/state_registry.db')
ICP_DATA=Path('/root/unboks-internal-control-panel/data')
BACKEND='sha256:293a23ac71835e382d0da0534f289fa7f95b4d4e0150b4b0ad033b0b6185fa47'
ICP_IMAGE='sha256:2e547a43021e2c3f4e98b3404a0d4780d19af9c0cf751d84c25745eb6acff77c'
HELPER_WRAPPER="import runpy,sys\nsys.argv=sys.argv[1:]\ntry:runpy.run_path(sys.argv[0],run_name='__main__')\nexcept SystemExit as exc:\n if exc.code not in (None,0,1):raise\n"
EVENTS=[];LOCKS=[];STATE={'ingress_installed':True,'mermaid_stopped':False,'activated':True,'gates_reopened':False}


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
def inspect(name):return json.loads(bounded_command(['docker','inspect',name],timeout=10,cap=262144))[0]
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
def runtime(code,seconds=20):
 return json.loads(cmd(['docker','run','--rm','--pull','never','--network','none','--env','PYTHONPATH=/app','--env','CLIENT_CONFIG_PATH=/app/config/client.json','--env','TENANT_ID=mermaid','--env','TENANT_ACCOUNT_ALLOWLIST_REQUIRED=true','--mount','type=bind,src=/root/clients/mermaid/config,dst=/app/config,readonly','--mount','type=bind,src=/root/clients/mermaid/data,dst=/app/data','--entrypoint','python',BACKEND,'-c',code],seconds))


def execute():
 mm=inspect('wtyj-mermaid');icp=inspect('unboks-internal-control-panel-wtyj-admin-1')
 require(mm['Id']=='011306f287c534df70209c09be21ad99954312fef93f0a3c5435274dae3e9a13' and mm['Image']==BACKEND and mm['State']['Running'],'runtime_binding')
 require(icp['Id']=='6642944f55241667adabb002b16f0859817aa8ec962819ffc5dc61c89dedcb46' and icp['Image']==ICP_IMAGE and icp['State']['Running'],'icp_binding')
 mounts=lambda x:sorted((v['Type'],v['Source'],v['Destination'],v['RW']) for v in x['Mounts'])
 require(mounts(mm)==[('bind','/root/clients/mermaid/config','/app/config',True),('bind','/root/clients/mermaid/data','/app/data',True),('bind','/root/clients/mermaid/logs','/app/logs',True)],'mount_binding')
 require(env(mm).get('MERMAID_DOCUMENT_ROOT','/app/data/mermaid-documents')=='/app/data/mermaid-documents','document_root')
 require(digest(CLIENT)=='1424d087d01af8f93cfa74feb677d603660a9128f5f1d686ee2778479ab7be7a','config_binding')
 raw=json.loads(checked_read(CLIENT));require(raw['slug']=='mermaid' and raw['channel_account_allowlist']['mode']=='strict' and len(raw['channel_account_allowlist']['zernio_accounts'])==1,'tenant_binding')
 account=raw['channel_account_allowlist']['zernio_accounts'][0]
 db=sqlite3.connect('file:'+str(ICP_DATA/'nr3.db')+'?mode=ro',uri=True,timeout=0)
 try:require(db.execute('SELECT tenant_id,channel,provider,status,zernio_account_verified FROM tenant_channel_connections WHERE zernio_account_id=? LIMIT 2',(account,)).fetchall()==[('mermaid','whatsapp','zernio','connected',1)],'account_binding')
 finally:db.close()
 ie=env(icp)
 claims=ie.get('NR3_PROVISION_CLAIMS_PATH') or (str(Path(ie['NR3_PORT_REGISTRY_PATH']).with_name('tenant_provision_claims.json')) if ie.get('NR3_PORT_REGISTRY_PATH') else 'data/provisioning/tenant_claims.json')
 lockdir=ie.get('NR3_TENANT_CREATE_LOCK_DIR') or str(Path(claims).parent/'create-locks')
 inside=os.path.normpath(lockdir if lockdir.startswith('/') else '/app/'+lockdir)
 require(inside.startswith('/app/data/'),'lifecycle_binding');lease(ICP_DATA/inside.removeprefix('/app/data/')/'mermaid.lock',False)
 lease(CLIENT.with_name('client.json.lock'),False)
 protected={p:digest(p) for p in [CLIENT,CLIENT.with_name('isluno_catalog.json'),CLIENT.with_name('isluno_profile.json'),DATABASE.with_name('session_token'),Path('/root/clients/mermaid/docker-compose.yml')]}
 jobs=ICP_DATA/'provisioning/jobs'
 require(not list(jobs.glob('*.json')) and not list(jobs.glob('*.processing')),'worker_not_idle')
 wu=dict(line.split('=',1) for line in cmd(['systemctl','show','nr3-provision-worker.service','--property=Environment,EnvironmentFiles'],10).splitlines() if '=' in line)
 we=dict(v.split('=',1) for v in shlex.split(wu.get('Environment','')) if '=' in v)
 require(not wu.get('EnvironmentFiles') and we.get('NR3_PROVISION_QUEUE_DIR')==str(jobs),'worker_binding')
 require(digest('/usr/local/lib/unboks/tracy_watchdog.py')=='a54f0d2d7a55b59eb707504ff4792544e6eb54507026a35c4afd7fb986169ab5','watchdog_binding')
 require(digest('/root/unboks-internal-control-panel/host/nr3_provision_worker.py')=='ce67264e7d4adb690dde4011d93b23d3d100ba41399ad02e9f93acc0b88c9ea8','worker_source')
 require(unit('nr3-provision-worker.service')['ActiveState']=='active' and unit('unboks-tracy-watchdog.timer')['ActiveState']=='active','unit_state')
 marker=Path('/root/clients/mermaid/.maintenance');require(not marker.exists(),'existing_marker')
 gate=json.loads(cmd(['docker','exec',icp['Id'],'python','-m','host.mermaid_ingress_control'],10));require(gate['phase']=='open' and gate['generation']==13,'ingress_state')
 db=sqlite3.connect('file:'+str(DATABASE)+'?mode=ro',uri=True,timeout=0)
 try:
  require(db.execute("SELECT generation,phase FROM mermaid_maintenance WHERE tenant='mermaid'").fetchall()==[(14,'open')],'runtime_gate')
  require(db.execute('SELECT count(*) FROM customers').fetchone()[0]<=1,'customer_scope')
  tables={n:hashlib.sha256((sql or '').encode()).hexdigest() for n,sql in db.execute("SELECT name,sql FROM sqlite_master WHERE type='table'")}
  require(tables==SCOPE['tables'],'schema_scope')
 finally:db.close()
 # No backup: one exclusive dispatch marker prevents repeating this erasure.
 fd=os.open('/root/clients/mermaid/.isluno-clean-slate-20260909-b',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600);os.close(fd)
 event('bindings_verified')
 cmd(['systemctl','stop','unboks-tracy-watchdog.timer'],10)
 watchdog_fd=lease('/var/lib/unboks-tracy-watchdog/status.lock',True)
 require(unit('unboks-tracy-watchdog.service')['ActiveState']=='inactive','watchdog_running')
 fd=os.open(marker,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600);os.write(fd,b'isluno-clean-slate-b\n');os.close(fd)
 cmd(['systemctl','stop','nr3-provision-worker.service'],15);worker_fd=lease(jobs/'.nr3-provision-worker.lock',True)
 require(not list(jobs.glob('*.json')) and not list(jobs.glob('*.processing')),'worker_queue_changed')
 gate=json.loads(cmd(['docker','exec',icp['Id'],'python','-m','host.mermaid_ingress_control','close','--execute-approved-action','--expected-generation','13'],20));require(gate['phase']=='closed' and gate['generation']==14,'ingress_close')
 result=runtime("import json\nfrom shared import mermaid_maintenance as m\nassert m.close(14,seconds=120,coverage=m.COVERAGE,evidence='Calvin tenant-only clean slate no backup',db_path='/app/data/state_registry.db')==15\nprint(json.dumps({'closed':True}))")
 event('admission_closed')
 # Existing workers get a bounded opportunity to finish before any service stop/deletion.
 end=time.monotonic()+20
 while True:
  db=sqlite3.connect('file:'+str(DATABASE)+'?mode=ro',uri=True,timeout=0)
  try:active=db.execute("SELECT count(*) FROM mermaid_maintenance_workers WHERE status!='finished'").fetchone()[0]
  finally:db.close()
  if active==0:break
  require(time.monotonic()<end,'unfinished_workers');time.sleep(.2)
 cmd(['docker','stop','--time','20',mm['Id']],25);assert_stopped('wtyj-mermaid')
 event('writers_stopped')
 # Inspect fixed file roots before first deletion; no arbitrary attachment DB paths.
 files=[] # No runtime log, attachment, backup or historical file deletion.
 for suffix in ('','-wal','-shm'):
  p=Path(str(DATABASE)+suffix)
  if p.exists():fd=safe_open(str(p));s=os.fstat(fd);os.close(fd);require(stat.S_ISREG(s.st_mode) and s.st_nlink==1 and s.st_size<=100000000,'database_shape')
 result=erase(DATABASE,SCOPE)
 for p,before in files:
  now=p.lstat();require((now.st_dev,now.st_ino,now.st_size,now.st_mtime_ns)==(before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns),'file_changed')
  p.unlink()
 require(all(digest(p)==v for p,v in protected.items()),'protected_changed')
 event('history_cleared',result=result,files_deleted=len(files))
 sealed=runtime("import json\nfrom shared import mermaid_maintenance as m\nm.seal(15,db_path='/app/data/state_registry.db')\ns=m.status('/app/data/state_registry.db');assert s['phase']=='sealed'\nprint(json.dumps({'sealed':True}))")
 cmd(['docker','start',mm['Id']],25)
 new=inspect('wtyj-mermaid');require(new['Id']==mm['Id'] and new['Image']==BACKEND and new['State']['Running'] and mounts(new)==mounts(mm),'restarted_binding')
 for key in ('Env','Entrypoint','Cmd','WorkingDir','User'):require(new['Config'].get(key)==mm['Config'].get(key),'restarted_config')
 end=time.monotonic()+20
 while True:
  try:
   with urllib.request.urlopen('http://127.0.0.1:8102/health',timeout=2) as r:require(r.status==200,'health')
   break
  except (urllib.error.URLError,ConnectionError):
   require(time.monotonic()<end,'health_timeout');time.sleep(.5)
 require(all(digest(p)==v for p,v in protected.items()),'protected_changed_after_start')
 checks=runtime(VERIFY_CODE)
 require(checks['empty'] and checks['catalog_products']==31,'readiness')
 event('ready',checks=checks)
 runtime("import json\nfrom shared import mermaid_maintenance as m\nm.reopen(15,db_path='/app/data/state_registry.db');s=m.status('/app/data/state_registry.db');assert s['phase']=='open' and s['generation']==16\nprint(json.dumps({'open':True}))")
 gate=json.loads(cmd(['docker','exec',icp['Id'],'python','-m','host.mermaid_ingress_control','reopen','--execute-approved-action','--expected-generation','14','--runtime-ready-verified'],20));require(gate['phase']=='open' and gate['generation']==15,'ingress_reopen')
 require(checked_read(marker,100)==b'isluno-clean-slate-b\n','marker_changed');marker.unlink()
 release_lock(worker_fd);cmd(['systemctl','start','nr3-provision-worker.service'],15)
 release_lock(watchdog_fd);cmd(['systemctl','start','unboks-tracy-watchdog.timer'],15)
 require(unit('nr3-provision-worker.service')['ActiveState']=='active' and unit('unboks-tracy-watchdog.timer')['ActiveState']=='active','restore_units')
 return {'status':'clean_slate_open','erasure':result,'files_deleted':len(files),'checks':checks,'new_container_id':new['Id'],'runtime_generation':16,'ingress_generation':15,'backup_created':False,'agent_messages_sent':0}
