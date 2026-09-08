"""Local packaging only. No network, environment/secret reads or deployment."""
import gzip
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile

BACKEND=Path('/Users/calvin/Projects/isluno-whatsapp-demo-20260908')
FRONTEND=Path('/Users/calvin/Projects/isluno-dashboard-demo-20260908')
SOURCE='4f62147871ada343edb7360c66fe8baae89eae90'
FRONT='2c99c3a13e01af625432e5f681d4c065e95dd1a8'
OUT=BACKEND/'tmp/isluno-release-artifacts'
OUT.mkdir(parents=True,exist_ok=True)
def sha(raw):return hashlib.sha256(raw).hexdigest()
def archive(name,ref,paths):
 raw=subprocess.check_output(['git','archive','--format=tar',ref,*paths],cwd=BACKEND)
 data=gzip.compress(raw,mtime=0);path=OUT/name;path.write_bytes(data)
 with tarfile.open(fileobj=io.BytesIO(raw)) as tar:
  members=[m.name for m in tar if m.isfile()]
 assert not any(Path(n).suffix in ('.db','.sqlite','.env') or Path(n).name in ('client.json','platform.env') for n in members)
 return {'path':str(path),'sha256':sha(data),'bytes':len(data),'source_commit':ref,'members':members}
assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=FRONTEND,text=True).strip()==FRONT
assert not subprocess.check_output(['git','diff','--name-only',FRONT,'--','artifacts/unboks/src'],cwd=FRONTEND,text=True).strip()
backend=archive('isluno-backend-source.tar.gz',SOURCE,['Dockerfile','requirements.txt','supervisord.conf','wtyj/agents','wtyj/shared','wtyj/dashboard','wtyj/assets','wtyj/templates'])
config=archive('isluno-config-default-off.tar.gz',SOURCE,['clients/mermaid/config/isluno_profile.json','clients/mermaid/config/isluno_catalog.json','clients/mermaid/config/isluno_inventory_report.json','clients/mermaid/config/isluno_feature_patch.json','clients/mermaid/config/isluno_recovery.json'])
static=FRONTEND/'artifacts/unboks/dist/public';files=[];buffer=io.BytesIO()
with tarfile.open(fileobj=buffer,mode='w') as tar:
 for path in sorted(static.rglob('*')):
  if not path.is_file():continue
  assert not path.is_symlink();raw=path.read_bytes();rel=str(path.relative_to(static))
  item=tarfile.TarInfo(rel);item.size=len(raw);item.mtime=0;item.mode=0o644;tar.addfile(item,io.BytesIO(raw));files.append({'path':rel,'sha256':sha(raw),'bytes':len(raw)})
assert any(row['path']=='index.html' for row in files)
data=gzip.compress(buffer.getvalue(),mtime=0);target=OUT/'isluno-frontend-static.tar.gz';target.write_bytes(data)
manifest={'schema':'isluno.release-artifacts.v1','execution_authorized':False,'backend_source':backend,'config_default_off':config,'frontend':{'path':str(target),'sha256':sha(data),'bytes':len(data),'source_commit':FRONT,'base_path':'/','env_files_loaded':False,'files':files},'oci_image':{'digest':None,'reason':'Target platform and immutable base/dependency image not currently verified; source archive is not a built runtime image.'}}
(BACKEND/'output/isluno-14/artifacts.json').write_text(json.dumps(manifest,indent=2)+'\n')
print(json.dumps({key:{'sha256':manifest[key]['sha256'],'bytes':manifest[key]['bytes']} for key in ('backend_source','config_default_off','frontend')}))
