"""One bounded read-only post-deployment verification; no messages/provider calls."""
import hashlib,json,sys
from pathlib import Path
r=Path(__file__).resolve().parent;root=r.parents[1]
sys.path.insert(0,str(root/'wtyj/scripts'))
from inspect_isluno_target import bounded_command,ssh_argv
result=json.loads((r/'result.json').read_text());assert result['status']=='fix_deployed_open'
src=(root/'wtyj/scripts/inspect_isluno_target.py').read_text()
common=src.split('\ndef fields(')[0]+'\n'+src[src.index('def safe_open('):src.index('\ndef path_metadata(')]+'\n'+(r/'helpers.py').read_text()
files=json.loads((r/'files.json').read_text())['files'];hashes={n:v['sha256'] for n,v in files.items() if n.endswith('.py')}
code=common+'\nEXPECTED='+repr(result)+'\nHASHES='+repr(hashes)+'''
signal.alarm(45)
mm=inspect('wtyj-mermaid');icp=inspect('unboks-internal-control-panel-wtyj-admin-1')
require(mm['Id']==EXPECTED['container_id'] and mm['Image']==EXPECTED['image'] and mm['State']['Running'],'live_runtime')
require(icp['Id']=='6642944f55241667adabb002b16f0859817aa8ec962819ffc5dc61c89dedcb46' and icp['Image']==ICP_IMAGE and icp['State']['Running'],'live_icp')
require(digest(CLIENT)=='1424d087d01af8f93cfa74feb677d603660a9128f5f1d686ee2778479ab7be7a','live_config')
require(digest(CLIENT.with_name('isluno_profile.json'))==EXPECTED['profile_sha256'],'live_profile')
require(not Path('/root/clients/mermaid/.maintenance').exists(),'live_maintenance_marker')
require(unit('nr3-provision-worker.service')['ActiveState']=='active' and unit('unboks-tracy-watchdog.timer')['ActiveState']=='active','live_producers')
require(os.readlink('/var/www/unboks-dashboard/current')=='/var/www/unboks-dashboard/releases/isluno-2c99c3a-20260909-a','dashboard_pointer')
gate=json.loads(cmd(['docker','exec',icp['Id'],'python','-m','host.mermaid_ingress_control'],10));require(gate['phase']=='open' and gate['generation']==9,'live_ingress')
app="import hashlib,json\\nfrom pathlib import Path\\nfrom shared import mermaid_maintenance as m,isluno_config\\nfrom shared.isluno_catalog import CatalogStore\\ns=m.status('/app/data/state_registry.db');assert s['phase']=='open' and s['generation']==10\\nc=isluno_config.capabilities();assert c['enabled'] and c['tenant_slug']=='mermaid' and all(c['capabilities'].values())\\nassert len(CatalogStore(Path('/app/config/isluno_catalog.json')).snapshot()['catalog']['products'])==31\\nh={n:hashlib.sha256(Path('/app',n).read_bytes()).hexdigest() for n in "+repr(list(HASHES))+"}\\nprint(json.dumps({'hashes':h,'phase':s['phase'],'generation':s['generation'],'catalog':31}))"
value=json.loads(cmd(['docker','exec',mm['Id'],'python','-c',app],10));require(value['hashes']==HASHES,'live_source')
with urllib.request.urlopen('http://127.0.0.1:8102/health',timeout=3) as response:require(response.status==200,'live_health')
print(json.dumps({'status':'live_verified','source':EXPECTED['source'],'image':mm['Image'],'container_id':mm['Id'],'health':200,'runtime_generation':10,'ingress_generation':9,'catalog_products':31,'source_files_verified':len(HASHES),'dashboard_unchanged':True,'producers_restored':True,'agent_messages_sent':0}))
'''
compile(code,'verification','exec');(r/'verification-payload.py').write_text(code)
out=bounded_command(ssh_argv(),timeout=50,cap=4096,data=code.encode());(r/'verification.json').write_text(out);print(out)
