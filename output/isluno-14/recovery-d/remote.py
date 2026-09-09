"""Approved coordinated cutover; returns with both admission gates closed."""
import os,json,hashlib,stat,fcntl,sqlite3,time,urllib.request,shlex
from pathlib import Path
ROOT=Path('/root/backups/isluno-manual-e2e-20260909-a')
CLIENT=Path('/root/clients/mermaid/config/client.json')
DATABASE=Path('/root/clients/mermaid/data/state_registry.db')
ICP_DATA=Path('/root/unboks-internal-control-panel/data')
BACKEND='sha256:2be4255cecfce5a7355e7868570cbfb7bddfec119bdb7d8f70391c7990df8fb9'
ICP_IMAGE='sha256:2e547a43021e2c3f4e98b3404a0d4780d19af9c0cf751d84c25745eb6acff77c'
EVENTS=[];LOCKS=[];STATE={'ingress_installed':True,'mermaid_stopped':True,'activated':False,'gates_reopened':False}


def event(phase,**extra):
 item={'phase':phase,'at':time.time(),**extra};EVENTS.append(item)
 p=ROOT/'recovery-d-progress.json';p.write_text(json.dumps({'events':EVENTS,'state':STATE},sort_keys=True));p.chmod(0o600)


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
def private_copy(path,name):
 raw=checked_read(path);fd=os.open(ROOT/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
 with os.fdopen(fd,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
 return hashlib.sha256(raw).hexdigest()
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

def one_shot(script,args,seconds=45):
 argv=['docker','run','--rm','--pull','never','--network','none','--env','PYTHONPATH=/app','--env','CLIENT_CONFIG_PATH=/app/config/client.json','--env','TENANT_ID=mermaid','--env','TENANT_ACCOUNT_ALLOWLIST_REQUIRED=true','--mount','type=bind,src=/root/clients/mermaid/config,dst=/app/config','--mount','type=bind,src=/root/clients/mermaid/data,dst=/app/data','--mount','type=bind,src='+str(ROOT)+',dst=/release-backup','--mount','type=bind,src='+str(ROOT/'tools')+',dst=/release-tools,readonly','--entrypoint','python',BACKEND,'/release-tools/wtyj/scripts/'+script]+args
 return json.loads(cmd(argv,seconds))

def execute():
 require(len(RECOVERY_HELPER)<=16384 and hashlib.sha256(RECOVERY_HELPER).hexdigest()==RECOVERY_HELPER_SHA,'helper_payload')
 mm=inspect('wtyj-mermaid');icp=inspect('unboks-internal-control-panel-wtyj-admin-1')
 require(mm['Id']==EXPECTED['mermaid']['id'] and mm['Image']==EXPECTED['mermaid']['image'],'stopped_mermaid_binding')
 assert_stopped(mm['Id'])
 require(icp['Id']==STOPPED_ICP_ID and icp['Image']==ICP_IMAGE and icp['State']['Running'],'closed_icp_binding')
 require(all(m['Type']=='bind' for m in icp['Mounts']) and sorted((m['Source'],m['Destination'],m['RW']) for m in icp['Mounts'])==sorted((m['source'],m['destination'],m['rw']) for m in EXPECTED['icp']['mounts']),'icp_mounts')
 require(digest(CLIENT)==STAGED_CONFIG_SHA and stat.S_IMODE(CLIENT.stat().st_mode)==0o600,'staged_config_cas')
 require(str(Path('/var/www/unboks-dashboard/current').resolve())==EXPECTED['frontend']['resolved_target'],'old_static_pointer')
 require(checked_read('/root/clients/mermaid/.maintenance',1024)==b'isluno-approved-cutover\n','watchdog_marker')
 require(unit('unboks-tracy-watchdog.timer').get('ActiveState')=='inactive' and unit('unboks-tracy-watchdog.service').get('ActiveState')=='inactive' and unit('nr3-provision-worker.service').get('ActiveState')=='inactive','producer_units')
 require(digest('/usr/local/lib/unboks/tracy_watchdog.py')==EXPECTED['watchdog']['script_sha256'],'watchdog_source')
 require(digest('/root/unboks-internal-control-panel/host/nr3_provision_worker.py')==WORKER_SHA,'worker_source')
 for item in EXPECTED['icp']['compose_sources']:require(digest(item['path'])==item['sha256'],'compose_source')
 require(digest('/root/clients/mermaid/docker-compose.yml')==digest(ROOT/'mermaid-compose.before.yml'),'mermaid_compose')
 require(checked_read(ROOT/'mermaid-release.yml',1024)==('services:\n  agent:\n    image: '+BACKEND+'\n    pull_policy: never\n').encode(),'mermaid_override')
 ie=env(icp);me=env(mm)
 if ie.get('NR3_TENANT_CREATE_LOCK_DIR'):lockdir=ie['NR3_TENANT_CREATE_LOCK_DIR']
 else:
  claims=ie.get('NR3_PROVISION_CLAIMS_PATH') or (str(Path(ie['NR3_PORT_REGISTRY_PATH']).with_name('tenant_provision_claims.json')) if ie.get('NR3_PORT_REGISTRY_PATH') else 'data/provisioning/tenant_claims.json')
  lockdir=str(Path(claims).parent/'create-locks')
 inside=os.path.normpath(lockdir if lockdir.startswith('/') else '/app/'+lockdir)
 require(inside.startswith('/app/data/'),'lifecycle_mount');lease(ICP_DATA/inside.removeprefix('/app/data/')/'mermaid.lock',False)
 lease('/var/lib/unboks-tracy-watchdog/status.lock',True)
 jobs=ICP_DATA/'provisioning/jobs'
 worker_unit=dict(line.split('=',1) for line in cmd(['systemctl','show','nr3-provision-worker.service','--property=Environment,EnvironmentFiles'],10).splitlines() if '=' in line)
 require(not worker_unit.get('EnvironmentFiles'),'worker_env_files')
 worker_env=dict(value.split('=',1) for value in shlex.split(worker_unit.get('Environment','')) if '=' in value)
 require(worker_env.get('NR3_PROVISION_QUEUE_DIR')==str(jobs),'worker_queue')
 require(not list(jobs.glob('*.json')) and not list(jobs.glob('*.processing')),'queue_not_idle');lease(jobs/'.nr3-provision-worker.lock',True)
 gate=json.loads(cmd(['docker','exec',icp['Id'],'python','-m','host.mermaid_ingress_control'],10))
 require(gate.get('phase')=='closed' and gate.get('generation')==0,'ingress_generation')
 account=json.loads(checked_read(CLIENT,1048576))['channel_account_allowlist']['zernio_accounts'][0]
 with sqlite3.connect('file:'+str(ICP_DATA/'nr3.db')+'?mode=ro',uri=True,timeout=0) as db:
  db.execute('PRAGMA query_only=ON')
  require(db.execute('SELECT tenant_id,channel,provider,status,zernio_account_verified FROM tenant_channel_connections WHERE zernio_account_id=? LIMIT 2',(account,)).fetchall()==[('mermaid','whatsapp','zernio','connected',1)],'stored_binding')
 for item in HELPER_FILES:require(digest(ROOT/'tools'/item['path'])==item['sha256'],'existing_helper_hash')
 require(digest(ROOT/'state_registry.sqlite3')=='269a45bead0fd1dc85ab651bc256202db3e3dc6c52653195dce4d431741cd349','consumed_snapshot')
 mmc=['env','MERMAID_IMAGE='+BACKEND,'docker','compose','--project-directory','/root/clients/mermaid','-f','/root/clients/mermaid/docker-compose.yml','-f',str(ROOT/'mermaid-release.yml')]
 configured=json.loads(cmd(mmc+['config','--format','json'],15))['services']['agent']
 require(all(me.get(k)==v for k,v in configured.get('environment',{}).items()),'configured_environment')
 fd=os.open(ROOT/'recovery-d-dispatch.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
 with os.fdopen(fd,'w') as file:json.dump({'at':time.time(),'helper_sha256':RECOVERY_HELPER_SHA,'approval':APPROVAL_REFERENCE},file);file.flush();os.fsync(file.fileno())
 target=ROOT/'tools/wtyj/scripts/recover_isluno_staged_cutover.py'
 fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
 with os.fdopen(fd,'wb') as file:file.write(RECOVERY_HELPER);file.flush();os.fsync(file.fileno())
 require(digest(target)==RECOVERY_HELPER_SHA,'installed_helper_hash');event('recovery_helper_staged_bindings_verified')
 wal=Path(str(DATABASE)+'-wal');require(not Path(str(DATABASE)+'-journal').exists(),'database_journal')
 bundle=hashlib.sha256(json.dumps({'db':digest(DATABASE),'wal':digest(wal) if wal.exists() else None},sort_keys=True).encode()).hexdigest()
 event('quarantine_and_seal_dispatch',config_sha256=STAGED_CONFIG_SHA,db_bundle_sha256=bundle)
 sealed=one_shot('recover_isluno_staged_cutover.py',['--execute-approved-recovery-d','--approve-preserved-unresolved-quarantine-71','--expected-config-sha256',STAGED_CONFIG_SHA,'--expected-db-bundle-sha256',bundle,'--verified-quiescence-reference','calvin-isluno-recovery-d'],50)
 require(sealed.get('status')=='sealed' and sealed.get('quarantined_no_replay')==71 and sealed.get('reclassified_as_completed')==0,'recovery_not_sealed')
 generation=sealed['generation'];event('coordinator_sealed',generation=generation,deadline=sealed['deadline_epoch'],preserved_unresolved=71)
 active=one_shot('apply_isluno_release_config.py',['--execute-approved-write','--phase','activate','--expected-sha256',STAGED_CONFIG_SHA,'--sealed-generation',str(generation),'--service-stopped-verified'],35)
 require(active.get('status')=='applied','activation_failed');event('activation_config_written',config_sha256=active['config_sha256'])
 STATE['mermaid_stopped']='unknown_during_start'
 cmd(mmc+['up','-d','--no-build','--pull','never','--no-deps','agent'],35)
 fresh=inspect('wtyj-mermaid');require(fresh['State']['Running'] and fresh['Image']==BACKEND,'new_runtime')
 require(sorted((m['Source'],m['Destination'],m['RW'],m['Type']) for m in fresh['Mounts'])==sorted((m['Source'],m['Destination'],m['RW'],m['Type']) for m in mm['Mounts']),'runtime_mounts')
 ready=False
 for _ in range(15):
  require(time.time()<sealed['deadline_epoch'],'startup_seal_expired')
  try:
   with urllib.request.urlopen('http://127.0.0.1:8102/health',timeout=1) as response:healthy=response.status==200
   with sqlite3.connect('file:'+str(DATABASE)+'?mode=ro',uri=True,timeout=0) as db:
    marker=db.execute("SELECT activated_at FROM isluno_cutover WHERE tenant='mermaid'").fetchone()
    phase=db.execute("SELECT generation,phase FROM mermaid_maintenance WHERE tenant='mermaid'").fetchone()
    quarantined=db.execute("SELECT count(*) FROM isluno_legacy_quarantine WHERE source='inbound_processing_events' AND disposition='operator_review_no_automatic_replay'").fetchone()[0]
   if healthy and marker and phase==(generation,'sealed') and quarantined==71:ready=True;break
  except (sqlite3.OperationalError,OSError):pass
  time.sleep(1)
 require(ready,'startup_not_ready');STATE['activated']=True;STATE['mermaid_stopped']=False
 event('runtime_started_gated',container_id=fresh['Id'],generation=generation)
 return {'status':'runtime_started_gated','state':STATE,'events':EVENTS,'generation':generation,'icp_generation':0,'config_sha256':active['config_sha256'],'mermaid_container_id':fresh['Id'],'icp_container_id':icp['Id'],'preserved_unresolved_records':71,'gates_reopened':False,'frontend_pointer_changed':False}
