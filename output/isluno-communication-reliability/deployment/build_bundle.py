"""Build immutable source/frontend packet locally; no host calls."""
from pathlib import Path
import hashlib,io,json,subprocess,tarfile
r=Path('tmp/isluno-communication-rollout');old=Path('tmp/isluno-presentation-rollout')
sha='ab88343b325d3ee67a6dcdad5e15e1f6dd2d254f';front='25dfebfb8688f76ef357b97c60c893fdf1bb8791'
base='sha256:f3cac23eaddd2b904f587399e2537e35b6bb40bc8c5310d4f76a0953cc6f1817'
container='1aa2e6946220f25f3130746f2cff5bf71412e320d1ec03078202e876640685b2'
assert subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()==sha
paths=subprocess.check_output(['git','diff','--name-only','f001cc7',sha,'--','wtyj/agents'],text=True).splitlines()
files={p.removeprefix('wtyj/'):subprocess.check_output(['git','show',sha+':'+p]) for p in paths}
python_names=list(files)
files['Dockerfile']=('FROM isluno-local:presentation-f001cc7\nLABEL org.opencontainers.image.revision="'+sha+'"\n'+''.join('COPY '+p+' /app/'+p+'\n' for p in python_names)).encode()
frontroot=Path('/Users/calvin/Projects/isluno-dashboard-demo-20260908/artifacts/unboks/dist/public')
for path in sorted(frontroot.rglob('*')):
 if path.is_file():
  assert not path.is_symlink();files['frontend/'+path.relative_to(frontroot).as_posix()]=path.read_bytes()
manifest={'source_commit':sha,'frontend_commit':front,'base_image':base,'profile_merge_keys':[],'files':{}}
buf=io.BytesIO()
with tarfile.open(fileobj=buf,mode='w') as archive:
 for name,data in sorted(files.items()):
  member=tarfile.TarInfo(name);member.size=len(data);member.mode=0o600;member.mtime=0;archive.addfile(member,io.BytesIO(data));manifest['files'][name]={'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}
raw=buf.getvalue();assert len(raw)<=5000000
(r/'source.tar').write_bytes(raw);manifest['bundle']={'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()};(r/'files.json').write_text(json.dumps(manifest,indent=2)+'\n')
