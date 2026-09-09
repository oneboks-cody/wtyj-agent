"""Default offline. One exact pointer rollback only if authenticated UI fails."""
import argparse,hashlib,json,os,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];r=ROOT/'tmp/isluno-communication-rollout'
sys.path.insert(0,str(ROOT/'wtyj/scripts'))
from inspect_isluno_target import bounded_command,ssh_argv
h=(ROOT/'wtyj/scripts/inspect_isluno_target.py').read_text()
common=h.split('\ndef fields(')[0]+'\n'+h[h.index('def safe_open('):h.index('\ndef path_metadata(')]+'\n'+(r/'helpers.py').read_text()
manifest=json.loads((r/'files.json').read_text())
code=common+'''\nsignal.alarm(20)
pointer=Path('/var/www/unboks-dashboard/current');new='/var/www/unboks-dashboard/releases/isluno-25dfebf-20260909-b';old='/var/www/unboks-dashboard/releases/isluno-2c99c3a-20260909-a'
require(pointer.is_symlink() and os.readlink(pointer)==new,'ui_pointer_changed')
require(digest(Path(old)/'index.html')=='9c7639e7321a9d4cbb3047c5672a1c1878457f19af44a18c3c309a7e1a59028e','old_ui_changed')
require(digest(Path(new)/'index.html')==FRONT_HASH,'new_ui_changed')
require(digest(CLIENT)=='1424d087d01af8f93cfa74feb677d603660a9128f5f1d686ee2778479ab7be7a','tenant_config_changed')
fd=os.open('/root/isluno-communication-ab88343-a/ui-rollback-once',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600);os.close(fd)
temporary=pointer.parent/'communication-ui-only-rollback'
require(not temporary.exists() and not temporary.is_symlink(),'ui_rollback_pointer_exists')
os.symlink(old,temporary);os.replace(temporary,pointer)
require(os.readlink(pointer)==old,'ui_rollback_not_selected')
print(json.dumps({'status':'frontend_only_rolled_back','backend_and_data_unchanged':True}))
'''
code='FRONT_HASH='+repr(manifest['files']['frontend/index.html']['sha256'])+'\n'+code
(r/'ui-rollback-payload.py').write_text(code)
parser=argparse.ArgumentParser();parser.add_argument('--execute-if-ui-failed',metavar='PACKET_SHA');args=parser.parse_args()
if not args.execute_if_ui_failed:print(json.dumps({'mode':'offline','sha256':hashlib.sha256(code.encode()).hexdigest()}));sys.exit(0)
packet=json.loads((r/'packet.json').read_text());guard=json.loads((r/'dispatch-once.json').read_text());result=json.loads((r/'result.json').read_text())
assert args.execute_if_ui_failed==packet['packet_sha256']==guard['packet_sha256']
assert hashlib.sha256(code.encode()).hexdigest()==packet['ui_rollback_sha256'],'rollback_payload_changed'
assert result['status']=='fix_deployed_open' and result['source']==manifest['source_commit']
assert time.time()+30<=guard['deadline_epoch'],'overall_budget_exhausted'
fd=os.open(r/'ui-rollback-once.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
with os.fdopen(fd,'w') as f:json.dump({'packet':args.execute_if_ui_failed,'payload_sha256':hashlib.sha256(code.encode()).hexdigest()},f)
out=bounded_command(ssh_argv(),timeout=25,cap=2048,data=code.encode());(r/'ui-rollback-result.json').write_text(out);print(out)
