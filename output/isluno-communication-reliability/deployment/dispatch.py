"""Default offline; one bounded approved deployment, no automatic retries."""
import argparse,base64,hashlib,json,os,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'wtyj/scripts'))
from inspect_isluno_target import bounded_command,ssh_argv
r=ROOT/'tmp/isluno-communication-rollout'
h=(ROOT/'wtyj/scripts/inspect_isluno_target.py').read_text()
common=h.split('\ndef fields(')[0]+'\n'+h[h.index('def safe_open('):h.index('\ndef path_metadata(')]+'\n'+(r/'helpers.py').read_text()
manifest=json.loads((r/'files.json').read_text())
source_hashes={n:v['sha256'] for n,v in manifest['files'].items() if n.endswith('.py')}
hash_code='import hashlib,json\nfrom pathlib import Path\nprint(json.dumps({n:hashlib.sha256(Path("/app",n).read_bytes()).hexdigest() for n in '+repr(list(source_hashes))+'}))'
operator=(r/'retained_history.py').read_text();proof=json.loads((r/'proof.json').read_text())['sha256']
bindings='\nCURRENT_CONTAINER="1aa2e6946220f25f3130746f2cff5bf71412e320d1ec03078202e876640685b2"\nOPERATOR_CODE='+repr(operator)+'\nPROOF_HASH='+repr(proof)+'\nMANIFEST='+repr(manifest)+'\nSOURCE_HASHES='+repr(source_hashes)+'\nHASH_CODE='+repr(hash_code)+'\n'
preflight=common+bindings+'\nsignal.alarm(15)\n'+(r/'preflight.py').read_text()
stage=common+bindings+'\nBUNDLE='+repr(base64.b64encode((r/'source.tar').read_bytes()).decode())+"\ndef deadline(*_):raise Rejected('staging_deadline')\nsignal.signal(signal.SIGALRM,deadline);signal.alarm(50)\n"+(r/'stage.py').read_text()
rollout=common+bindings+(r/'rollout.py').read_text()+'''
def deadline(*_):raise Rejected('rollout_deadline')
END=time.monotonic()+OPERATION_SECONDS
signal.signal(signal.SIGALRM,deadline);signal.alarm(OPERATION_SECONDS)
try:result=execute()
except Exception as exc:
 cause=str(exc) if isinstance(exc,Rejected) else type(exc).__name__
 if any(STATE.get(k) for k in ('watchdog_stopped','worker_stopped','marker_created','gates_closed','sealed')) and not STATE.get('image_changed') and not STATE.get('profile_changed'):
  try:result=restore_original_after_pre_activation_failure();result['cause']=cause
  except Exception as restore_error:result={'status':'held_for_review','code':cause,'restore_code':str(restore_error) if isinstance(restore_error,Rejected) else type(restore_error).__name__,'events':EVENTS,'state':STATE}
 else:result={'status':'stopped','code':cause,'events':EVENTS,'state':STATE}
finally:
 for fd in list(LOCKS):os.close(fd)
result['automatic_retry']=False
print(json.dumps({'result':result},sort_keys=True))
'''
for name,code in [('preflight-payload.py',preflight),('stage-payload.py',stage),('rollout-template.py',rollout)]:compile(code,name,'exec');(r/name).write_text(code)
packet={'source_commit':manifest['source_commit'],'frontend_commit':manifest['frontend_commit'],'preflight_sha256':hashlib.sha256(preflight.encode()).hexdigest(),'stage_sha256':hashlib.sha256(stage.encode()).hexdigest(),'rollout_template_sha256':hashlib.sha256(rollout.encode()).hexdigest(),'bundle':manifest['bundle'],'history_proof':proof,'overall_seconds':900,'transfers':1,'transfer_seconds':60,'build_seconds':120,'minimum_before_disruption_seconds':500,'minimum_before_activation_seconds':300,'rollback_reserve_seconds':180,'no_automatic_retries':True,'authenticated_ui_reserve_seconds':180,'ui_rollback_sha256':hashlib.sha256((r/'ui-rollback-payload.py').read_bytes()).hexdigest(),'frontend_target':'/var/www/unboks-dashboard/releases/isluno-25dfebf-20260909-b','frontend_index_sha256':manifest['files']['frontend/index.html']['sha256']}
packet_sha=hashlib.sha256(json.dumps(packet,sort_keys=True).encode()).hexdigest()
(r/'packet.json').write_text(json.dumps({'packet_sha256':packet_sha,**packet},indent=2)+'\n')
parser=argparse.ArgumentParser();parser.add_argument('--execute-reviewed',metavar='PACKET_SHA256');args=parser.parse_args()
if not args.execute_reviewed:print(json.dumps({'mode':'offline_prepared','packet_sha256':packet_sha,**packet}));sys.exit(0)
assert args.execute_reviewed==packet_sha,'packet_changed'
fd=os.open(r/'dispatch-once.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
with os.fdopen(fd,'w') as f:json.dump({'packet_sha256':packet_sha,**packet,'started_epoch':time.time(),'deadline_epoch':time.time()+900},f)
started=time.monotonic();end=started+900
try:
 pre=bounded_command(ssh_argv(),timeout=20,cap=4096,data=preflight.encode());(r/'preflight-result.json').write_text(pre)
 assert json.loads(pre)['status']=='preflight_pass'
 out=bounded_command(ssh_argv(),timeout=60,cap=4096,data=stage.encode());(r/'stage-result.json').write_text(out)
 assert json.loads(out)['status']=='staged'
 remaining=int(end-time.monotonic())-183;assert remaining>=600,'insufficient_operation_budget'
 payload='OPERATION_SECONDS='+str(remaining-2)+'\nPACKET_SHA='+repr(packet_sha)+'\n'+rollout
 (r/'exact-payload.py').write_text(payload)
 out=bounded_command(ssh_argv(),timeout=remaining,cap=32768,data=payload.encode())
 (r/'raw-result.jsonl').write_text(out)
 result=next(json.loads(line)['result'] for line in out.splitlines() if 'result' in json.loads(line))
except Exception as exc:result={'status':'stopped','error':type(exc).__name__,'remote_state_requires_inspection':True,'no_automatic_retry':True}
result['elapsed_seconds']=round(time.monotonic()-started,3)
(r/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
