from pathlib import Path
import hashlib,io,json,subprocess,tarfile
r=Path('tmp/isluno-presentation-rollout');old=Path('tmp/isluno-hospitality-rollout')
sha=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip();assert sha.startswith('f001cc7')
base='sha256:f80fefc3b9e49f2580f0e1b0c5b7affc643b129d80a54d32c8c4abd64538f7ac';oldbase='sha256:8d63e4d77862d9832892e788ceafc0f4d425aacf6ac594b7ee034aa1547068e3'
oldid='5acae4259714c41aeca3e310e6466cca27cfda19f0724d6439675c02e5c624b7';newid='8bc4006c9fa3d5f83eef6d3c7f275e241bfcfc2ee7532b6e3d436a0d8123d21c'
oldprofile='1be5a6297fd2fe3e5609214925a220c1b80598eb7a5f031d8af8caa80b2fce58';newprofile='5c90d1979c7599e9bdd82a809a6cebe07db150692ed949b7a88e1070b16616b3'
def common(s):return s.replace('isluno-hospitality-rollout','isluno-presentation-rollout').replace(oldbase,base).replace(oldid,newid).replace(oldprofile,newprofile).replace('isluno-hospitality-3a5f524-a','isluno-presentation-f001cc7-a').replace('isluno-local:hospitality-3a5f524','isluno-local:presentation-f001cc7').replace('3a5f5240f9d51e71fb47c47fbce8a16d1688f78e',sha)
paths=subprocess.check_output(['git','diff','--name-only','3a5f524',sha,'--','wtyj/agents'],text=True).splitlines();assert len(paths)==2
files={p.removeprefix('wtyj/'):subprocess.check_output(['git','show',sha+':'+p]) for p in paths}
files['Dockerfile']=('FROM isluno-local:hospitality-3a5f524\nLABEL org.opencontainers.image.revision="'+sha+'"\n'+''.join('COPY '+p+' /app/'+p+'\n' for p in files)).encode()
manifest={'source_commit':sha,'base_image':base,'profile_merge_keys':[],'files':{}};buf=io.BytesIO()
with tarfile.open(fileobj=buf,mode='w') as archive:
 for name,data in sorted(files.items()):
  member=tarfile.TarInfo(name);member.size=len(data);member.mode=0o600;member.mtime=0;archive.addfile(member,io.BytesIO(data));manifest['files'][name]={'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}
raw=buf.getvalue();(r/'source.tar').write_bytes(raw);manifest['bundle']={'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()};(r/'files.json').write_text(json.dumps(manifest,indent=2)+'\n')
(r/'helpers.py').write_text(common((old/'helpers.py').read_text()))
s=common((old/'rollout.py').read_text())
a=s.index('def replace_profile(');b=s.index('\ndef execute():',a);s=s[:a]+s[b:]
a=s.index(' current_profile=json.loads(profile_before)');b=s.index(' protected={',a);s=s[:a]+s[b:]
s=s.replace("[CLIENT,CLIENT.with_name('isluno_catalog.json')", "[CLIENT,profile_path,CLIENT.with_name('isluno_catalog.json')")
s=s.replace("image_info('isluno-local:no-reply-c04512c')", "image_info('isluno-local:hospitality-3a5f524')")
s=s.replace("s['generation']==7", "s['generation']==9").replace("\\',7,", "\\',9,")
s=s.replace("'--expected-generation','6'", "'--expected-generation','8'").replace("'--expected-generation','5'", "'--expected-generation','7'")
s=s.replace("gate['generation']==7", "gate['generation']==9").replace("gate['generation']==6", "gate['generation']==8").replace("gate['generation']==5", "gate['generation']==7")
s=s.replace('assert m.close(6,seconds=120','assert m.close(8,seconds=120').replace("state_registry.db')==7", "state_registry.db')==9")
s=s.replace("'generation':7", "'generation':9").replace("'runtime_generation':8,'ingress_generation':7,'profile_sha256':profile_hash", "'runtime_generation':10,'ingress_generation':9,'profile_sha256':hashlib.sha256(profile_before).hexdigest()")
s=s.replace("state['generation']==7", "state['generation']==9").replace("state['generation']==6", "state['generation']==8")
s=s.replace("  replace_profile(profile_path,profile_before,profile_after,STAGE/'profile-before.json')\n  STATE['profile_changed']=True\n  protected[profile_path]=profile_hash\n",'')
s=s.replace("  require(actual_profile in (profile_before,profile_after),'rollback_profile_unknown')\n  if actual_profile!=profile_before:replace_profile(profile_path,profile_after,profile_before,None)\n", "  require(actual_profile==profile_before,'rollback_profile_changed')\n")
s=s.replace('Exactly one image-only rollback','Exactly one image-only rollback').replace('reviewed hospitality image and two-field profile rollout','reviewed presentation-only image rollout')
(r/'rollout.py').write_text(s)
s=common((old/'preflight.py').read_text()).replace("s['generation']==6", "s['generation']==8");(r/'preflight.py').write_text(s)
s=common((old/'stage.py').read_text()).replace('len(members)==12','len(members)==3').replace("'files':12","'files':3");(r/'stage.py').write_text(s)
(r/'dispatch.py').write_text(common((old/'dispatch.py').read_text()))
for name in ('rollout.py','helpers.py','preflight.py','stage.py','dispatch.py'):compile((r/name).read_text(),name,'exec')
print(json.dumps({'source':sha,'bundle':manifest['bundle']}))
