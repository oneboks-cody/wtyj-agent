"""Default offline preparation; exact approved packet required for two bounded SSH steps."""
import argparse,base64,hashlib,json,os,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
# Avoid the diagnostic inspect.py filename shadowing Python's standard library.
sys.path=[v for v in sys.path if Path(v or '.').resolve()!=Path(__file__).resolve().parent]
sys.path.insert(0,str(ROOT/'wtyj/scripts'))
from inspect_isluno_target import bounded_command,ssh_argv
r=ROOT/'tmp/isluno-no-reply'
h=(ROOT/'wtyj/scripts/inspect_isluno_target.py').read_text()
common=h.split('\ndef fields(')[0]+'\n'+h[h.index('def safe_open('):h.index('\ndef path_metadata(')]+'\n'+(r/'helpers.py').read_text()
manifest=json.loads((ROOT/'output/isluno-no-reply/files.json').read_text())
source_hashes={n:v['sha256'] for n,v in manifest['files'].items() if n!='Dockerfile'}
hash_code='import hashlib,json\nfrom pathlib import Path\nprint(json.dumps({n:hashlib.sha256(Path("/app",n).read_bytes()).hexdigest() for n in '+repr(list(source_hashes))+'}))'
operator_code=(r/'retained_review.py').read_text()
proof_hash=json.loads((r/'retained-proof.json').read_text())['sha256']
test_hash='caa6a3ec4d4af4d15e08ed85fe09bb4371f8393bae204b31f5d2054a6eadcaec'
bindings='\nOPERATOR_CODE='+repr(operator_code)+'\nPROOF_HASH='+repr(proof_hash)+'\nTEST_HASH='+repr(test_hash)+'\nMANIFEST='+repr(manifest)+'\nSOURCE_HASHES='+repr(source_hashes)+'\nHASH_CODE='+repr(hash_code)+'\n'
preflight=common+bindings+'\nsignal.alarm(15)\n'+(r/'preflight.py').read_text()
stage=common+bindings+'\nBUNDLE='+repr(base64.b64encode((r/'source.tar').read_bytes()).decode())+'''
def deadline(*_):raise Rejected('staging_deadline')
signal.signal(signal.SIGALRM,deadline);signal.alarm(50)
'''+(r/'stage.py').read_text()
rollout=common+bindings+(r/'rollout.py').read_text()+'''
def deadline(*_):raise Rejected('rollout_deadline')
END=time.monotonic()+OPERATION_SECONDS
signal.signal(signal.SIGALRM,deadline);signal.alarm(OPERATION_SECONDS)
try:result=execute()
except Exception as exc:result={'status':'stopped','code':str(exc) if isinstance(exc,Rejected) else type(exc).__name__,'events':EVENTS,'state':STATE,'automatic_retry':False}
finally:
 for fd in LOCKS:os.close(fd)
print(json.dumps({'result':result},sort_keys=True))
'''
for name,code in [('preflight-payload.py',preflight),('stage-payload.py',stage),('rollout-template.py',rollout)]:
 compile(code,name,'exec');(r/name).write_text(code)
packet={'preflight_sha256':hashlib.sha256(preflight.encode()).hexdigest(),'source_sha':manifest['source_sha'],'stage_sha256':hashlib.sha256(stage.encode()).hexdigest(),'rollout_template_sha256':hashlib.sha256(rollout.encode()).hexdigest(),'overall_seconds':720,'stage_seconds':60,'build_seconds':120,'no_automatic_retries':True}
packet_sha=hashlib.sha256(json.dumps(packet,sort_keys=True).encode()).hexdigest()
parser=argparse.ArgumentParser();parser.add_argument('--execute-approved',metavar='PACKET_SHA256');args=parser.parse_args()
if not args.execute_approved:
 print(json.dumps({'mode':'offline_prepared','packet_sha256':packet_sha,**packet}));sys.exit(0)
assert args.execute_approved==packet_sha,'packet_changed'
fd=os.open(r/'rollout-dispatch.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
with os.fdopen(fd,'w') as f:json.dump({'packet_sha256':packet_sha,**packet},f)
started=time.monotonic();overall_end=started+720
try:
 pre=bounded_command(ssh_argv(),timeout=20,cap=4096,data=preflight.encode())
 (r/'preflight-result.json').write_text(pre);assert json.loads(pre)['status']=='preflight_pass'
 out=bounded_command(ssh_argv(),timeout=60,cap=4096,data=stage.encode())
 (r/'stage-result.json').write_text(out);assert json.loads(out)['status']=='staged'
 remaining=int(overall_end-time.monotonic())-3
 assert remaining>=180,'insufficient_rollout_budget'
 payload='OPERATION_SECONDS='+str(remaining-2)+'\n'+rollout
 (r/'rollout-exact-payload.py').write_text(payload)
 out=bounded_command(ssh_argv(),timeout=remaining,cap=32768,data=payload.encode())
 records=[json.loads(line) for line in out.splitlines()]
 result=next(v['result'] for v in records if 'result' in v)
except Exception as exc:result={'status':'stopped','error':type(exc).__name__,'no_automatic_retry':True,'remote_state_requires_inspection':True}
result['elapsed_seconds']=round(time.monotonic()-started,3)
(r/'rollout-result.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
