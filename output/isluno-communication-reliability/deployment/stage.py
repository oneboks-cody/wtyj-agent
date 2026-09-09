import base64,io,tarfile
STAGE=Path('/root/isluno-communication-ab88343-a')
require(not STAGE.exists(),'staging_exists')
raw=base64.b64decode(BUNDLE,validate=True)
require(len(raw)==MANIFEST['bundle']['bytes'] and hashlib.sha256(raw).hexdigest()==MANIFEST['bundle']['sha256'],'bundle_hash')
require(len(raw)<=5000000,'bundle_limit')
with tarfile.open(fileobj=io.BytesIO(raw),mode='r:') as archive:
 members=archive.getmembers();require(len(members)==83 and {m.name for m in members}==set(MANIFEST['files']),'archive_members')
 files={}
 for member in members:
  require(member.isfile() and not member.name.startswith('/') and '..' not in Path(member.name).parts and member.size==MANIFEST['files'][member.name]['bytes'],'archive_type')
  data=archive.extractfile(member).read(member.size+1)
  require(hashlib.sha256(data).hexdigest()==MANIFEST['files'][member.name]['sha256'],'member_hash');files[member.name]=data
STAGE.mkdir(mode=0o700)
for name,data in files.items():
 target=STAGE/name;target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
 fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
 with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
print(json.dumps({'status':'staged','bundle_sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),'files':83}))
