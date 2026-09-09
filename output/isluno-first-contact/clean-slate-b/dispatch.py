from pathlib import Path
import os,sys,json,hashlib
sys.path.insert(0,str(Path('wtyj/scripts').resolve()))
from inspect_isluno_target import bounded_command,ssh_argv
root=Path('tmp/isluno-clean-slate-b');payload=(root/'payload.py').read_bytes()
assert len(sys.argv)==2 and hashlib.sha256(payload).hexdigest()==sys.argv[1]
fd=os.open(root/'dispatch.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
with os.fdopen(fd,'w') as f:json.dump({'sha256':sys.argv[1],'authority':'Calvin direct clean slate selected Isluno/Mermaid no backup; independent organizer review','no_automatic_retry':True},f)
try:
 out=bounded_command(ssh_argv(),timeout=250,cap=32768,data=payload)
 records=[json.loads(line) for line in out.splitlines()]
 result=next(x['result'] for x in records if 'result' in x)
except Exception as e:result={'status':'stopped','error':type(e).__name__,'remote_state_requires_inspection':True,'automatic_retry':False}
(root/'result.json').write_text(json.dumps(result,indent=2));(root/'result.json').chmod(0o600)
print(json.dumps(result))
