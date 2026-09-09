from pathlib import Path
import sys,json,os,time,hashlib
sys.path.insert(0,str(Path('wtyj/scripts').resolve()))
from inspect_isluno_target import bounded_command,ssh_argv
r=Path('tmp/isluno-no-reply')
prior=(r/'rollout-exact-payload.py').read_text().split('\ndef deadline(')[0]
code=prior+'\n'+(r/'continue.py').read_text()+'''
def deadline(*_):raise Rejected('continuation_deadline')
END=time.monotonic()+CONTINUATION_SECONDS
signal.signal(signal.SIGALRM,deadline);signal.alarm(CONTINUATION_SECONDS)
try:result=complete()
except Exception as exc:result={'status':'stopped','code':str(exc) if isinstance(exc,Rejected) else type(exc).__name__,'events':EVENTS,'state':STATE,'automatic_retry':False}
finally:
 for fd in LOCKS:os.close(fd)
print(json.dumps({'result':result},sort_keys=True))
'''
compile(code,'completion-template','exec');(r/'completion-template.py').write_text(code)
sha=hashlib.sha256(code.encode()).hexdigest()
if len(sys.argv)==1:print(json.dumps({'mode':'offline','sha256':sha}));sys.exit(0)
assert sys.argv[1:]==['--execute-reviewed',sha]
receipt=r/'rollout-dispatch.json';assert json.loads(receipt.read_text())['packet_sha256']=='2b835f6e9d1965323577e5d78d570a1b7e6a594e51d87d003fb2c7c460fcba77'
elapsed=time.time()-receipt.stat().st_mtime;seconds=min(330,int(720-elapsed)-5)
assert 290<=seconds<=330,'original_cumulative_budget_exhausted'
fd=os.open(r/'completion-dispatch.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
with os.fdopen(fd,'w') as f:json.dump({'sha256':sha,'original_dispatch_elapsed':elapsed,'seconds':seconds,'no_automatic_retry':True},f)
payload='CONTINUATION_SECONDS='+str(seconds-2)+'\n'+code
(r/'completion-exact-payload.py').write_text(payload)
try:
 out=bounded_command(ssh_argv(),timeout=seconds,cap=32768,data=payload.encode())
 records=[json.loads(line) for line in out.splitlines()];result=next(x['result'] for x in records if 'result' in x)
except Exception as e:result={'status':'stopped','error':type(e).__name__,'remote_state_requires_inspection':True,'no_automatic_retry':True}
(r/'completion-result.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
