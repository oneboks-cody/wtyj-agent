import base64,io,tarfile
STAGE=Path('/root/isluno-no-reply-c04512c')
require(not STAGE.exists(),'staging_exists')
raw=base64.b64decode(BUNDLE,validate=True)
require(len(raw)==194560 and hashlib.sha256(raw).hexdigest()=='66c7df2eceafe53e808c10e423a61c79dfd9dcf1ec950c1e4de4f445dd2c483c','bundle_hash')
with tarfile.open(fileobj=io.BytesIO(raw),mode='r:') as archive:
 members=archive.getmembers();require(len(members)==6 and {m.name for m in members}==set(MANIFEST['files']),'archive_members')
 files={}
 for member in members:
  require(member.isfile() and member.size==MANIFEST['files'][member.name]['bytes'],'archive_type')
  data=archive.extractfile(member).read(member.size+1)
  require(hashlib.sha256(data).hexdigest()==MANIFEST['files'][member.name]['sha256'],'member_hash');files[member.name]=data
STAGE.mkdir(mode=0o700)
for name,data in files.items():
 target=STAGE/name;target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
 fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
 with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
print(json.dumps({'status':'staged','bundle_sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),'files':6}))
