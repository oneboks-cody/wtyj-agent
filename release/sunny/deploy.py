"""Single authorized Mermaid rollout. No customer clearing or provider probes.

Build first, fence Mermaid writers, validate terminal history, seal, preserve every
business table, swap two-file image and voice profile, verify, reopen. One rollback.
"""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

STAGE=Path('/root/isluno-sunny-welcome-20260909')
ROOT=Path('/root/clients/mermaid')
BASE='sha256:287f3bc3fe755dd9d750ab7a3c8203d69863e0911269ad361a9dc7754e529042'
ORIGINAL='30d4df38832cd94df16c27e23dfa31126e0fb0fb55bf198b9043804390ae74ff'
ICP='unboks-internal-control-panel-wtyj-admin-1'
PROFILE=ROOT/'config/isluno_profile.json'
PROFILE_HASH='5c90d1979c7599e9bdd82a809a6cebe07db150692ed949b7a88e1070b16616b3'
MARKER=ROOT/'.maintenance'
STATE={'ingress_closed':False,'draining':False,'sealed':False,'stopped':False,'changed':False,'open':False}
LOCKS=[]
SOURCE_FILES=['agents/social/isluno_hospitality.py','agents/social/isluno_conversation_understanding.py']


def check(value,code):
    if not value:raise ValueError(code)


def command(args,timeout=20,input=None):
    p=subprocess.run(args,input=input,text=True,capture_output=True,timeout=timeout)
    # Never echo subprocess stderr: docker/compose may include private environment.
    check(p.returncode==0,'command_failed:'+args[0]+':'+str(p.returncode))
    check(len(p.stdout)<1000000,'output_bound')
    return p.stdout


def inspect(name):return json.loads(command(['docker','inspect',name]))[0]
def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def event(name,**kw):print(json.dumps({'phase':name,**kw}),flush=True)


def lock(path,exclusive=False):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    deadline=time.monotonic()+10
    while True:
        try:fcntl.flock(fd,(fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)|fcntl.LOCK_NB);break
        except BlockingIOError:
            if time.monotonic()>deadline:os.close(fd);raise ValueError('writer_busy')
            time.sleep(.1)
    LOCKS.append(fd)


def runtime(code=None,action=None,expected=None):
    args=['docker','run','--rm','--pull','never','--network','none','--env','PYTHONPATH=/app',
          '--env','CLIENT_CONFIG_PATH=/app/config/client.json','--env','TENANT_ID=mermaid',
          '--env','TENANT_ACCOUNT_ALLOWLIST_REQUIRED=true',
          '--mount','type=bind,src='+str(ROOT/'config')+',dst=/app/config,readonly',
          '--mount','type=bind,src='+str(ROOT/'data')+',dst=/app/data',
          '--mount','type=bind,src='+str(STAGE/'release/sunny')+',dst=/operator,readonly',
          '--entrypoint','python',BASE]
    args+=['-c',code] if code else ['/operator/history.py',action,'19',expected or '']
    return json.loads(command(args,20))


def ingress(action=None):
    args=['docker','exec',ICP,'python','-m','host.mermaid_ingress_control']
    if action:
        args += [action,'--execute-approved-action','--expected-generation','17' if action=='close' else '18']
        if action=='reopen':args+=['--runtime-ready-verified']
    return json.loads(command(args))


def semantic(x):
    config=x['Config'];host=x['HostConfig']
    settings={k:config.get(k) for k in ('Env','Entrypoint','Cmd','WorkingDir','User')}
    settings['Env']=sorted(settings['Env'] or [])
    host_settings={k:host.get(k) for k in ('NetworkMode','PortBindings','RestartPolicy','Binds','ReadonlyRootfs','Privileged','CapAdd','CapDrop')}
    for key in ('Binds','CapAdd','CapDrop'):host_settings[key]=sorted(host_settings[key] or [])
    host_settings['PortBindings']={key:sorted(value or [],key=lambda p:(p.get('HostIp',''),p.get('HostPort',''))) for key,value in (host_settings['PortBindings'] or {}).items()}
    return {'config':settings,
            'mounts':sorted((m['Type'],m['Source'],m['Destination'],m['RW']) for m in x['Mounts']),
            'host':host_settings}


def switch(image,filename):
    path=STAGE/filename
    with path.open('x') as out:out.write('services:\n  agent:\n    image: '+image+'\n    pull_policy: never\n')
    command(['docker','compose','--project-directory',str(ROOT),'-f',str(ROOT/'docker-compose.yml'),
             '-f',str(path),'up','-d','--force-recreate','--no-build','--pull','never','--no-deps','agent'],40)


def replace_profile(data):
    temporary=PROFILE.with_name('isluno_profile.sunny-tmp')
    stat=PROFILE.stat()
    with temporary.open('xb') as out:
        out.write(data);out.flush();os.fsync(out.fileno())
    os.chmod(temporary,stat.st_mode & 0o777);os.chown(temporary,stat.st_uid,stat.st_gid)
    os.replace(temporary,PROFILE)


def ready(image,original,proof,protected,profile_hash,source_hashes=None):
    actual=inspect('wtyj-mermaid')
    check(actual['State']['Running'] and actual['Image']==image,'runtime_identity')
    check(semantic(actual)==semantic(original),'runtime_settings_changed')
    deadline=time.monotonic()+18
    while True:
        try:
            with urllib.request.urlopen('http://127.0.0.1:8102/health',timeout=2) as r:check(r.status==200,'health')
            break
        except (OSError,ValueError):
            check(time.monotonic()<deadline,'health_timeout');time.sleep(.4)
    check(all(digest(p)==v for p,v in protected.items()),'protected_file_changed')
    check(digest(PROFILE)==profile_hash,'profile_changed')
    check(runtime(action='ready',expected=proof['sha256'])==proof,'data_changed')
    if source_hashes:
        code='import hashlib,json;from pathlib import Path;print(json.dumps({p:hashlib.sha256(Path("/app",p).read_bytes()).hexdigest() for p in '+repr(SOURCE_FILES)+'}))'
        check(json.loads(command(['docker','exec','wtyj-mermaid','python','-c',code]))==source_hashes,'source_mismatch')
        code='import json;from shared.isluno_config import capabilities;from agents.social.isluno_conversation_understanding import system_prompt;c=capabilities();p=system_prompt();assert c["enabled"] and c["tenant_slug"]=="mermaid";assert "Isluno.com" in p and "☀️" in p and "Public introduction identity:" in p;print(json.dumps({"prompt_loaded":True}))'
        check(json.loads(command(['docker','exec','wtyj-mermaid','python','-c',code]))['prompt_loaded'],'prompt_readiness')
    return actual


def reopen(proof):
    runtime(action='reopen',expected=proof['sha256'])
    gate=ingress('reopen');check(gate['phase']=='open' and gate['generation']==19,'ingress_reopen')
    STATE['open']=True
    check(MARKER.read_text()=='sunny-welcome-20260909\n','marker_owner');MARKER.unlink()


def execute(revision):
    original=inspect('wtyj-mermaid')
    check(original['Id']==ORIGINAL and original['Image']==BASE and original['State']['Running'],'changed_live_base')
    check(digest(PROFILE)==PROFILE_HASH,'changed_live_profile')
    old_profile=PROFILE.read_bytes()
    candidate_profile=(STAGE/'clients/mermaid/config/isluno_profile.json').read_bytes()
    old=json.loads(old_profile);new=json.loads(candidate_profile)
    check({k:v for k,v in old.items() if k!='hospitality_voice'}=={k:v for k,v in new.items() if k!='hospitality_voice'},'profile_scope')
    check(new['tenant_slug']=='mermaid' and new['brand']['website']=='https://isluno.com','profile_identity')
    protected={p:digest(p) for p in [ROOT/'config/client.json',ROOT/'config/isluno_catalog.json',ROOT/'docker-compose.yml',ROOT/'data/session_token']}
    peers={inspect(n)['Id']:inspect(n)['State']['StartedAt'] for n in command(['docker','ps','--format','{{.Names}}']).splitlines() if n!='wtyj-mermaid'}
    hashes={p:digest(STAGE/'wtyj'/p) for p in SOURCE_FILES}
    with (STAGE/'dispatch-once').open('x') as out:out.write(revision+'\n')
    command(['docker','build','--network','none','--pull=false','--build-arg','SOURCE_REV='+revision,
             '-f',str(STAGE/'release/sunny/Dockerfile'),'-t','isluno-local:sunny-'+revision[:8],str(STAGE)],60)
    image=json.loads(command(['docker','image','inspect','isluno-local:sunny-'+revision[:8]]))[0]
    base=json.loads(command(['docker','image','inspect',BASE]))[0]
    check(image['RootFS']['Layers'][:len(base['RootFS']['Layers'])]==base['RootFS']['Layers'],'base_layers')
    for key in ('Env','Entrypoint','Cmd','WorkingDir','User','ExposedPorts','Volumes'):
        check(image['Config'].get(key)==base['Config'].get(key),'image_settings_changed')
    check(image['Config']['Labels']['org.opencontainers.image.revision']==revision,'revision')
    code='import hashlib,json;from pathlib import Path;print(json.dumps({p:hashlib.sha256(Path("/app",p).read_bytes()).hexdigest() for p in '+repr(SOURCE_FILES)+'}))'
    check(json.loads(command(['docker','run','--rm','--pull','never','--network','none','--entrypoint','python',image['Id'],'-c',code]))==hashes,'packaged_source_mismatch')
    event('image_built',image=image['Id'])
    # Shared tenant lifecycle lease excludes destructive provisioning without stopping other tenants.
    lock('/root/unboks-internal-control-panel/data/provisioning/create-locks/mermaid.lock')
    lock(str(ROOT/'config/client.json.lock'))
    lock('/var/lib/unboks-tracy-watchdog/status.lock',True)
    check(not MARKER.exists(),'maintenance_already_active')
    check(inspect('wtyj-mermaid')['Id']==ORIGINAL,'concurrent_release')
    check(all(digest(p)==v for p,v in protected.items()) and digest(PROFILE)==PROFILE_HASH,'configuration_changed')
    check(ingress()['phase']=='open' and ingress()['generation']==17,'ingress_precondition')
    status=runtime(code='import json;from shared import mermaid_maintenance as m;print(json.dumps(m.status("/app/data/state_registry.db")))')
    check(status['phase']=='open' and status['generation']==18,'runtime_precondition')
    proof=runtime(action='review')
    with (STAGE/'profile-before.json').open('xb') as out:out.write(old_profile)
    os.chmod(STAGE/'profile-before.json',0o600)
    with MARKER.open('x') as out:out.write('sunny-welcome-20260909\n')
    try:
        gate=ingress('close');STATE['ingress_closed']=True
        check(gate['phase']=='closed' and gate['generation']==18,'ingress_close')
        runtime(code='import json;from shared import mermaid_maintenance as m;g=m.close(18,seconds=120,coverage=m.COVERAGE,evidence="Calvin authorized sunny welcome deployment and terminal-history preservation check; Mermaid lifecycle/config/watchdog leases; fenced ingress and participating runtime producers",db_path="/app/data/state_registry.db");assert g==19;print(json.dumps({"closed":True}))')
        STATE['draining']=True
        runtime(action='seal',expected=proof['sha256']);STATE['sealed']=True
        event('sealed',protected_tables=proof['tables'])
        command(['docker','stop','--time','20',ORIGINAL],25);STATE['stopped']=True
        stopped=inspect('wtyj-mermaid');check(not stopped['State']['Running'] and stopped['State']['ExitCode']==0,'unclean_stop')
        runtime(action='ready',expected=proof['sha256'])
        replace_profile(candidate_profile)
        switch(image['Id'],'release.yml');STATE['changed']=True
        actual=ready(image['Id'],original,proof,protected,hashlib.sha256(candidate_profile).hexdigest(),hashes)
        for ident,started in peers.items():check(inspect(ident)['State']['StartedAt']==started and inspect(ident)['State']['Running'],'peer_changed')
        reopen(proof)
        result={'status':'deployed','revision':revision,'image':image['Id'],'container':actual['Id'],
                'health':200,'customer_data_preserved':proof,'profile_sha256':digest(PROFILE),
                'source_hashes':hashes,'other_containers_unchanged':len(peers),
                'runtime_generation':20,'ingress_generation':19,'engineering_messages_sent':0}
        (STAGE/'receipt.json').write_text(json.dumps(result,indent=2)+'\n')
        event('deployed',**result)
    except Exception as exc:
        event('failure',code=str(exc) if isinstance(exc,ValueError) else type(exc).__name__,state=STATE)
        if STATE['sealed'] and not STATE['open']:
            # At most one image/profile rollback, never restore the customer database.
            current=inspect('wtyj-mermaid')
            check(current['Image'] in {BASE,image['Id']},'rollback_unknown_runtime')
            if STATE['stopped'] or STATE['changed']:
                if current['State']['Running']:command(['docker','stop','--time','10',current['Id']],15)
                check(PROFILE.read_bytes() in {old_profile,candidate_profile},'rollback_profile_conflict')
                replace_profile(old_profile)
                switch(BASE,'rollback.yml')
            ready(BASE,original,proof,protected,PROFILE_HASH)
            reopen(proof);event('rolled_back_original_open')
        elif not STATE['sealed']:
            check(inspect('wtyj-mermaid')['Id']==ORIGINAL and inspect('wtyj-mermaid')['State']['Running'],'abort_runtime_changed')
            check(digest(PROFILE)==PROFILE_HASH,'abort_profile_changed')
            if STATE['draining']:runtime(action='abort')
            if STATE['ingress_closed']:check(ingress('reopen')['phase']=='open','abort_ingress_reopen')
            check(MARKER.read_text()=='sunny-welcome-20260909\n','abort_marker_owner');MARKER.unlink()
            event('aborted_original_open')
        raise
    finally:
        for fd in LOCKS:os.close(fd)


if __name__=='__main__':
    try:execute(sys.argv[1])
    except Exception as exc:
        event('stopped',reason=str(exc) if isinstance(exc,ValueError) else type(exc).__name__)
        sys.exit(1)
