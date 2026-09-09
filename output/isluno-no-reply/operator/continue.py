"""Reviewed completion only; no rebuild, reseal, upload, deletion or replay."""
def semantic_host_config(value):
 result={k:value.get(k) for k in ('NetworkMode','PortBindings','RestartPolicy','Binds','ReadonlyRootfs','Privileged','CapAdd','CapDrop')}
 for key in ('Binds','CapAdd','CapDrop'):result[key]=sorted(result[key] or [])
 result['PortBindings']={key:sorted(values or [],key=lambda v:(v.get('HostIp',''),v.get('HostPort',''))) for key,values in (result['PortBindings'] or {}).items()}
 return result


def assert_runtime_spec(value,original,image):
 require(value['State']['Running'] and value['Image']==image and mounts(value)==mounts(original),'runtime_spec')
 require(env(value)==env(original),'effective_environment')
 for key in ('Entrypoint','Cmd','WorkingDir','User'):require(value['Config'].get(key)==original['Config'].get(key),'effective_command_'+key)
 actual=semantic_host_config(value['HostConfig']);expected=semantic_host_config(original['HostConfig'])
 for key in expected:require(actual[key]==expected[key],'host_config_'+key)


def complete():
 candidate_id='sha256:8d63e4d77862d9832892e788ceafc0f4d425aacf6ac594b7ee034aa1547068e3'
 mm=inspect('wtyj-mermaid');icp=inspect('unboks-internal-control-panel-wtyj-admin-1')
 require(mm['Id']=='c7c46cc11d54d1ca5025cf75ec36d1af152b7224e0083d5b73c4c99b79f45734' and mm['Image']==BASE_IMAGE and mm['State']['Running'],'rolled_back_runtime')
 require(icp['Id']=='6642944f55241667adabb002b16f0859817aa8ec962819ffc5dc61c89dedcb46' and icp['Image']==ICP_IMAGE and icp['State']['Running'],'icp_binding')
 canonical={'NetworkMode':'mermaid_default','PortBindings':{'8001/tcp':[{'HostIp':'127.0.0.1','HostPort':'8102'}]},'RestartPolicy':{'Name':'unless-stopped','MaximumRetryCount':0},'Binds':['/root/clients/mermaid/config:/app/config:rw','/root/clients/mermaid/data:/app/data:rw','/root/clients/mermaid/logs:/app/logs:rw'],'ReadonlyRootfs':False,'Privileged':False,'CapAdd':None,'CapDrop':None}
 require(semantic_host_config(mm['HostConfig'])==semantic_host_config(canonical),'effective_baseline_host_config')
 require(digest(CLIENT)=='1424d087d01af8f93cfa74feb677d603660a9128f5f1d686ee2778479ab7be7a','config_binding')
 require(digest('/root/clients/mermaid/docker-compose.yml')==digest(ROOT/'mermaid-compose.before.yml'),'compose_changed')
 composition=json.loads(cmd(['env','MERMAID_IMAGE='+BASE_IMAGE,'docker','compose','--project-directory','/root/clients/mermaid','-f','/root/clients/mermaid/docker-compose.yml','-f',str(STAGE/'rollback.yml'),'config','--format','json'],10))['services']['agent']
 base=image_info(BASE_IMAGE);candidate=image_info(candidate_id)
 expected_env=dict(v.split('=',1) for v in base['Config'].get('Env',[]) if '=' in v);expected_env.update(composition.get('environment',{}))
 require(env(mm)==expected_env,'compose_environment')
 require(candidate['RootFS']['Layers'][:len(base['RootFS']['Layers'])]==base['RootFS']['Layers'],'base_layers')
 hashes=json.loads(cmd(['docker','run','--rm','--pull','never','--network','none','--entrypoint','python',candidate_id,'-c',HASH_CODE],10));require(hashes==SOURCE_HASHES,'candidate_hashes')
 ie=env(icp)
 claims=ie.get('NR3_PROVISION_CLAIMS_PATH') or (str(Path(ie['NR3_PORT_REGISTRY_PATH']).with_name('tenant_provision_claims.json')) if ie.get('NR3_PORT_REGISTRY_PATH') else 'data/provisioning/tenant_claims.json')
 lockdir=ie.get('NR3_TENANT_CREATE_LOCK_DIR') or str(Path(claims).parent/'create-locks')
 inside=os.path.normpath(lockdir if lockdir.startswith('/') else '/app/'+lockdir)
 require(inside.startswith('/app/data/'),'lifecycle_binding');lease(ICP_DATA/inside.removeprefix('/app/data/')/'mermaid.lock',False)
 lease(CLIENT.with_name('client.json.lock'),False)
 account=json.loads(checked_read(CLIENT))['channel_account_allowlist']['zernio_accounts'][0]
 db=sqlite3.connect('file:'+str(ICP_DATA/'nr3.db')+'?mode=ro',uri=True,timeout=0)
 try:require(db.execute('SELECT tenant_id,channel,provider,status,zernio_account_verified FROM tenant_channel_connections WHERE zernio_account_id=? LIMIT 2',(account,)).fetchall()==[('mermaid','whatsapp','zernio','connected',1)],'stored_account_binding')
 finally:db.close()
 for name in ['unboks-tracy-watchdog.timer','unboks-tracy-watchdog.service','nr3-provision-worker.service']:require(unit(name)['ActiveState']=='inactive','fence_state')
 watchdog_fd=lease('/var/lib/unboks-tracy-watchdog/status.lock',True)
 jobs=ICP_DATA/'provisioning/jobs';require(not list(jobs.glob('*.json')) and not list(jobs.glob('*.processing')),'worker_queue')
 worker_fd=lease(jobs/'.nr3-provision-worker.lock',True)
 marker=Path('/root/clients/mermaid/.maintenance');require(checked_read(marker,100)==b'isluno-no-reply-c04512c\n','marker_binding')
 gate=json.loads(cmd(['docker','exec',icp['Id'],'python','-m','host.mermaid_ingress_control'],10));require(gate['phase']=='closed' and gate['generation']==4,'ingress_binding')
 proof=runtime(OPERATOR_CODE+'\nprint(json.dumps(readiness(\'/app/data/state_registry.db\',5,'+repr(TEST_HASH)+','+repr(PROOF_HASH)+')))');require(proof['sha256']==PROOF_HASH,'proof_changed')
 protected={p:digest(p) for p in [CLIENT,CLIENT.with_name('isluno_catalog.json'),DATABASE.with_name('session_token'),Path('/root/clients/mermaid/docker-compose.yml')]}
 rows=protected_rows()
 evidence={'protected_rows':rows,'protected_files':{str(k):v for k,v in protected.items()},'retained_proof_sha256':PROOF_HASH}
 fd=os.open(STAGE/'completion-preservation.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
 with os.fdopen(fd,'w') as f:json.dump(evidence,f,sort_keys=True);f.flush();os.fsync(f.fileno())
 event('continuation_baseline',**evidence)
 require(remaining()>=300,'continuation_budget')
 fd=os.open(STAGE/'completion-dispatched',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600);os.close(fd)
 STATE.update(gates_closed=True,sealed=True)
 try:
  fresh=switch(candidate_id,STAGE/'completion-release.yml');STATE['image_changed']=True
  fresh=ready(candidate_id,mm,rows,protected,True)
 except Exception as exc:
  require(remaining()>=180,'completion_rollback_budget')
  current=inspect('wtyj-mermaid');require(current['Image'] in {BASE_IMAGE,candidate_id},'rollback_container_unknown')
  if current['State']['Running']:cmd(['docker','stop','--time','10',current['Id']],15)
  old=switch(BASE_IMAGE,STAGE/'completion-rollback.yml');old=ready(BASE_IMAGE,mm,rows,protected,False)
  STATE['rolled_back']=True;reopen(icp,worker_fd,watchdog_fd)
  return {'status':'completion_rolled_back_open','cause':str(exc) if isinstance(exc,Rejected) else type(exc).__name__,'image':BASE_IMAGE,'state':STATE}
 reopen(icp,worker_fd,watchdog_fd)
 return {'status':'fix_deployed_open','image':candidate_id,'container_id':fresh['Id'],'source':'c04512cfc84ccae329c5d8de881fe9a010882e59','health':200,'packaged_hashes':SOURCE_HASHES,'preserved_tables':len(rows),'retained_proof_sha256':PROOF_HASH,'runtime_generation':6,'ingress_generation':5,'worker_restored':True,'watchdog_restored':True,'marker_removed':True,'agent_messages_sent':0,'state':STATE}
