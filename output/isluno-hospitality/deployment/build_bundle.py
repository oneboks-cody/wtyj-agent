"""Offline-only immutable source bundle, no deployment or secret reads."""
import io,json,hashlib,subprocess,tarfile
from pathlib import Path
root=Path.cwd();stage=root/'tmp/isluno-hospitality-rollout';build=stage/'build';build.mkdir(exist_ok=True)
sha='3a5f5240f9d51e71fb47c47fbce8a16d1688f78e'
paths=subprocess.check_output(['git','diff','--name-only','c04512c',sha,'--','wtyj/agents'],text=True).splitlines()
files={p.removeprefix('wtyj/'):subprocess.check_output(['git','show',sha+':'+p]) for p in paths}
profile=json.loads(subprocess.check_output(['git','show',sha+':clients/mermaid/config/isluno_profile.json']))
files['profile-patch.json']=(json.dumps({k:profile[k] for k in ['hospitality_voice','conversation_outcomes']},ensure_ascii=False,indent=2)+'\n').encode()
docker=['FROM isluno-local:no-reply-c04512c','LABEL org.opencontainers.image.revision="'+sha+'"']
for p in files:
 if p.endswith('.py'):docker.append('COPY '+p+' /app/'+p)
files['Dockerfile']=('\n'.join(docker)+'\n').encode()
manifest={'source_commit':sha,'base_image':'sha256:8d63e4d77862d9832892e788ceafc0f4d425aacf6ac594b7ee034aa1547068e3',
          'profile_merge_keys':['hospitality_voice','conversation_outcomes'],'files':{}}
buffer=io.BytesIO()
with tarfile.open(fileobj=buffer,mode='w') as archive:
 for name,data in sorted(files.items()):
  target=build/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
  info=tarfile.TarInfo(name);info.size=len(data);info.mode=0o600;info.mtime=0
  archive.addfile(info,io.BytesIO(data));manifest['files'][name]={'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}
raw=buffer.getvalue();(stage/'source.tar').write_bytes(raw)
manifest['bundle']={'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
(stage/'files.json').write_text(json.dumps(manifest,indent=2)+'\n')
print(json.dumps({'source':sha,'files':len(files),'bundle':manifest['bundle']}))
