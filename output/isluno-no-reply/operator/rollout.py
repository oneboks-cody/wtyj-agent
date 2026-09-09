STAGE=Path('/root/isluno-no-reply-c04512c')
BASE_IMAGE=BACKEND
CURRENT_CONTAINER='b5ae7a2601fdff630a4e8e2f32e9a97f7576e5c00e27290ccafd6e2da7e6735c'
TAG='isluno-local:no-reply-c04512c'


def image_info(name):return json.loads(cmd(['docker','image','inspect',name],10))[0]
def mounts(x):return sorted((v['Type'],v['Source'],v['Destination'],v['RW']) for v in x['Mounts'])
def remaining():return END-time.monotonic()
def protected_rows():
 db=sqlite3.connect('file:'+str(DATABASE)+'?mode=ro',uri=True,timeout=0)
 try:
  db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
  end=time.monotonic()+5
  db.set_progress_handler(lambda: int(time.monotonic()>=end),1000)
  result={};total=0;size=0
  tables=db.execute("SELECT name,sql FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
  require(len(tables)<=250,'table_bound')
  for name,sql in tables:
   if name.startswith('mermaid_maintenance') or name=='sqlite_sequence':continue
   require(re.fullmatch('[a-z_]+',name) is not None,'table_name')
   h=hashlib.sha256(sql.encode());count=0
   for row in db.execute('SELECT * FROM "'+name+'" ORDER BY rowid'):
    encoded=repr(row).encode();size+=len(encoded);total+=1;count+=1
    require(size<=100000000 and total<=100000 and time.monotonic()<end,'row_bound');h.update(encoded);h.update(b'\n')
   result[name]={'count':count,'sha256':h.hexdigest()}
  return result
 finally:db.close()


def assert_runtime_spec(value,original,image):
 require(value['State']['Running'] and value['Image']==image and mounts(value)==mounts(original),'runtime_spec')
 require(env(value)==env(original),'effective_environment')
 for key in ('Entrypoint','Cmd','WorkingDir','User'):require(value['Config'].get(key)==original['Config'].get(key),'effective_command')
 for key in ('NetworkMode','PortBindings','RestartPolicy','Binds','ReadonlyRootfs','Privileged','CapAdd','CapDrop'):
  require(value['HostConfig'].get(key)==original['HostConfig'].get(key),'host_config')


def switch(image,path):
 fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
 with os.fdopen(fd,'w') as f:f.write('services:\n  agent:\n    image: '+image+'\n    pull_policy: never\n')
 cmd(['env','MERMAID_IMAGE='+image,'docker','compose','--project-directory','/root/clients/mermaid','-f','/root/clients/mermaid/docker-compose.yml','-f',str(path),'up','-d','--force-recreate','--no-build','--pull','never','--no-deps','agent'],40)
 return inspect('wtyj-mermaid')


def ready(image,original,rows,protected,check_source):
 fresh=inspect('wtyj-mermaid');assert_runtime_spec(fresh,original,image)
 end=time.monotonic()+15
 while True:
  try:
   with urllib.request.urlopen('http://127.0.0.1:8102/health',timeout=2) as response:require(response.status==200,'health')
   break
  except (urllib.error.URLError,ConnectionError):require(time.monotonic()<end,'health_timeout');time.sleep(.3)
 require(all(digest(p)==v for p,v in protected.items()),'protected_files_changed')
 require(protected_rows()==rows,'customer_or_business_data_changed')
 code="""import json
from pathlib import Path
from shared import config_loader,isluno_config,mermaid_maintenance as m
from shared.isluno_catalog import CatalogStore
caps=isluno_config.capabilities();assert caps['enabled'] and caps['tenant_slug']=='mermaid' and all(caps['capabilities'].values())
assert len(CatalogStore(Path(config_loader._CONFIG_PATH).with_name('isluno_catalog.json')).snapshot()['catalog']['products'])==31
s=m.status('/app/data/state_registry.db');assert s['phase']=='sealed' and s['generation']==5 and not s['workers'] and not s['pending'] and s['unreviewed_ledgers']==['isluno_conversation_turns','isluno_recovery_incidents']
print(json.dumps({'ready':True,'catalog':31,'generation':5}))
"""
 require(runtime(code)['ready'],'readiness_failed')
 proof=runtime(OPERATOR_CODE+'\nprint(json.dumps(readiness(\'/app/data/state_registry.db\',5,'+repr(TEST_HASH)+','+repr(PROOF_HASH)+')))')
 require(proof['sha256']==PROOF_HASH,'retained_readiness')
 if check_source:
  actual=json.loads(cmd(['docker','exec',fresh['Id'],'python','-c',HASH_CODE],10));require(actual==SOURCE_HASHES,'live_packaged_hashes')
 return fresh


def reopen(icp,worker_fd,watchdog_fd):
 runtime(OPERATOR_CODE+'\nreopen(\'/app/data/state_registry.db\',5,'+repr(TEST_HASH)+','+repr(PROOF_HASH)+');print(json.dumps({\'open\':True}))')
 gate=json.loads(cmd(['docker','exec',icp['Id'],'python','-m','host.mermaid_ingress_control','reopen','--execute-approved-action','--expected-generation','4','--runtime-ready-verified'],15));require(gate['phase']=='open' and gate['generation']==5,'ingress_reopen')
 marker=Path('/root/clients/mermaid/.maintenance');require(checked_read(marker,100)==b'isluno-no-reply-c04512c\n','marker_changed');marker.unlink()
 release_lock(worker_fd);cmd(['systemctl','start','nr3-provision-worker.service'],10)
 release_lock(watchdog_fd);cmd(['systemctl','start','unboks-tracy-watchdog.timer'],10)
 require(unit('nr3-provision-worker.service')['ActiveState']=='active' and unit('unboks-tracy-watchdog.timer')['ActiveState']=='active','unit_restore')
 STATE['open']=True


def execute():
 require(all(digest(STAGE/n)==v['sha256'] for n,v in MANIFEST['files'].items()),'staged_hashes')
 mm=inspect('wtyj-mermaid');icp=inspect('unboks-internal-control-panel-wtyj-admin-1')
 require(mm['Id']==CURRENT_CONTAINER and mm['Image']==BASE_IMAGE and mm['State']['Running'],'runtime_binding')
 require(icp['Id']=='6642944f55241667adabb002b16f0859817aa8ec962819ffc5dc61c89dedcb46' and icp['Image']==ICP_IMAGE and icp['State']['Running'],'icp_binding')
 require(mounts(mm)==[('bind','/root/clients/mermaid/config','/app/config',True),('bind','/root/clients/mermaid/data','/app/data',True),('bind','/root/clients/mermaid/logs','/app/logs',True)],'mount_binding')
 require(digest(CLIENT)=='1424d087d01af8f93cfa74feb677d603660a9128f5f1d686ee2778479ab7be7a','config_binding')
 require(digest('/root/clients/mermaid/docker-compose.yml')==digest(ROOT/'mermaid-compose.before.yml'),'compose_changed')
 protected={p:digest(p) for p in [CLIENT,CLIENT.with_name('isluno_catalog.json'),DATABASE.with_name('session_token'),Path('/root/clients/mermaid/docker-compose.yml')]}
 raw=json.loads(checked_read(CLIENT));require(raw['slug']=='mermaid' and raw['channel_account_allowlist']['mode']=='strict' and len(raw['channel_account_allowlist']['zernio_accounts'])==1,'tenant_binding')
 db=sqlite3.connect('file:'+str(ICP_DATA/'nr3.db')+'?mode=ro',uri=True,timeout=0)
 try:require(db.execute('SELECT tenant_id,channel,provider,status,zernio_account_verified FROM tenant_channel_connections WHERE zernio_account_id=? LIMIT 2',(raw['channel_account_allowlist']['zernio_accounts'][0],)).fetchall()==[('mermaid','whatsapp','zernio','connected',1)],'account_binding')
 finally:db.close()
 base=image_info('isluno-local:e2deab8');require(base['Id']==BASE_IMAGE,'base_image_tag')
 fd=os.open(STAGE/'rollout-dispatched',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600);os.close(fd)
 cmd(['docker','build','--network','none','--pull=false','-t',TAG,str(STAGE)],120)
 candidate=image_info(TAG);candidate_id=candidate['Id']
 require(candidate['RootFS']['Layers'][:len(base['RootFS']['Layers'])]==base['RootFS']['Layers'],'base_layers')
 for key in ('Env','Entrypoint','Cmd','WorkingDir','User','ExposedPorts','Volumes'):
  require(candidate['Config'].get(key)==base['Config'].get(key),'image_configuration')
 require(candidate['Config'].get('Labels',{}).get('org.opencontainers.image.revision')=='c04512cfc84ccae329c5d8de881fe9a010882e59','source_revision')
 hashes=json.loads(cmd(['docker','run','--rm','--pull','never','--network','none','--entrypoint','python',candidate_id,'-c',HASH_CODE],15));require(hashes==SOURCE_HASHES,'packaged_hashes')
 event('image_verified',image=candidate_id)
 # Fixed reusable producer/tenant fences. No customer database writes.
 ie=env(icp)
 claims=ie.get('NR3_PROVISION_CLAIMS_PATH') or (str(Path(ie['NR3_PORT_REGISTRY_PATH']).with_name('tenant_provision_claims.json')) if ie.get('NR3_PORT_REGISTRY_PATH') else 'data/provisioning/tenant_claims.json')
 lockdir=ie.get('NR3_TENANT_CREATE_LOCK_DIR') or str(Path(claims).parent/'create-locks')
 inside=os.path.normpath(lockdir if lockdir.startswith('/') else '/app/'+lockdir)
 require(inside.startswith('/app/data/'),'lifecycle_binding');lease(ICP_DATA/inside.removeprefix('/app/data/')/'mermaid.lock',False)
 lease(CLIENT.with_name('client.json.lock'),False)
 require(inspect('wtyj-mermaid')['Id']==CURRENT_CONTAINER and all(digest(p)==v for p,v in protected.items()),'binding_changed')
 jobs=ICP_DATA/'provisioning/jobs';require(not list(jobs.glob('*.json')) and not list(jobs.glob('*.processing')),'worker_queue')
 wu=dict(line.split('=',1) for line in cmd(['systemctl','show','nr3-provision-worker.service','--property=Environment,EnvironmentFiles'],10).splitlines() if '=' in line)
 we=dict(v.split('=',1) for v in shlex.split(wu.get('Environment','')) if '=' in v)
 require(not wu.get('EnvironmentFiles') and we.get('NR3_PROVISION_QUEUE_DIR')==str(jobs),'worker_binding')
 require(digest('/usr/local/lib/unboks/tracy_watchdog.py')=='a54f0d2d7a55b59eb707504ff4792544e6eb54507026a35c4afd7fb986169ab5','watchdog_binding')
 require(digest('/root/unboks-internal-control-panel/host/nr3_provision_worker.py')=='ce67264e7d4adb690dde4011d93b23d3d100ba41399ad02e9f93acc0b88c9ea8','worker_source')
 require(unit('nr3-provision-worker.service')['ActiveState']=='active' and unit('unboks-tracy-watchdog.timer')['ActiveState']=='active','unit_state')
 marker=Path('/root/clients/mermaid/.maintenance');require(not marker.exists(),'existing_marker')
 gate=json.loads(cmd(['docker','exec',icp['Id'],'python','-m','host.mermaid_ingress_control'],10));require(gate['phase']=='open' and gate['generation']==3,'ingress_state')
 proof=runtime(OPERATOR_CODE+"\ndb=sqlite3.connect('file:/app/data/state_registry.db?mode=ro',uri=True,timeout=0)\ntry:\n db.execute('PRAGMA query_only=ON');db.execute('BEGIN')\n print(json.dumps(verify(db,"+repr(TEST_HASH)+","+repr(PROOF_HASH)+")))\nfinally:db.close()")
 require(proof['sha256']==PROOF_HASH,'retained_proof_changed')
 require(remaining()>=460,'insufficient_disruption_budget')
 cmd(['systemctl','stop','unboks-tracy-watchdog.timer'],10);watchdog_fd=lease('/var/lib/unboks-tracy-watchdog/status.lock',True)
 require(unit('unboks-tracy-watchdog.service')['ActiveState']=='inactive','watchdog_running')
 fd=os.open(marker,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600);os.write(fd,b'isluno-no-reply-c04512c\n');os.close(fd)
 cmd(['systemctl','stop','nr3-provision-worker.service'],15);worker_fd=lease(jobs/'.nr3-provision-worker.lock',True)
 require(not list(jobs.glob('*.json')) and not list(jobs.glob('*.processing')),'worker_queue_changed')
 gate=json.loads(cmd(['docker','exec',icp['Id'],'python','-m','host.mermaid_ingress_control','close','--execute-approved-action','--expected-generation','3'],20));require(gate['phase']=='closed' and gate['generation']==4,'ingress_close')
 runtime("import json\nfrom shared import mermaid_maintenance as m\nassert m.close(4,seconds=120,coverage=m.COVERAGE,evidence='reviewed no-reply image-only rollout',db_path='/app/data/state_registry.db')==5\nprint(json.dumps({'closed':True}))")
 STATE['gates_closed']=True
 # Sealing polls only read status; failures/unresolved rows are never erased or relabelled.
 end=time.monotonic()+20
 while True:
  status=runtime("import json\nfrom shared import mermaid_maintenance as m\ns=m.status('/app/data/state_registry.db');print(json.dumps({'ready':not s['workers'] and not s['pending'] and s['unreviewed_ledgers']==['isluno_conversation_turns','isluno_recovery_incidents']}))",10)
  if status['ready']:break
  require(time.monotonic()<end,'drain_not_complete');time.sleep(.2)
 runtime(OPERATOR_CODE+'\nseal(\'/app/data/state_registry.db\',5,'+repr(TEST_HASH)+','+repr(PROOF_HASH)+');print(json.dumps({\'sealed\':True}))')
 STATE['sealed']=True
 cmd(['docker','stop','--time','20',mm['Id']],25);assert_stopped('wtyj-mermaid')
 rows=protected_rows();event('sealed_and_stopped',protected_tables=len(rows))
 require(remaining()>=300,'insufficient_activation_rollback_budget')
 try:
  fresh=switch(candidate_id,STAGE/'release.yml');STATE['image_changed']=True
  fresh=ready(candidate_id,mm,rows,protected,True)
 except Exception as exc:
  # Exactly one image-only rollback; no DB restore, migration or customer replay.
  require(remaining()>=180,'rollback_budget_unavailable')
  current=inspect('wtyj-mermaid');require(current['Image'] in {BASE_IMAGE,candidate_id},'rollback_container_unknown')
  if current['State']['Running']:cmd(['docker','stop','--time','10',current['Id']],15)
  old=switch(BASE_IMAGE,STAGE/'rollback.yml')
  old=ready(BASE_IMAGE,mm,rows,protected,False)
  STATE['rolled_back']=True;event('image_rollback_ready',cause=type(exc).__name__)
  reopen(icp,worker_fd,watchdog_fd)
  return {'status':'rolled_back_open','image':BASE_IMAGE,'customer_tables_preserved':len(rows),'state':STATE}
 reopen(icp,worker_fd,watchdog_fd)
 return {'status':'fix_deployed_open','image':candidate_id,'container_id':fresh['Id'],'source':'c04512cfc84ccae329c5d8de881fe9a010882e59','customer_tables_preserved':len(rows),'runtime_generation':6,'ingress_generation':5,'agent_messages_sent':0,'state':STATE}
