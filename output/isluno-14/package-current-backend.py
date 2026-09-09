"""Package accepted Git source once in a distinct directory; no builds/network/secrets.

Prior backend/config/frontend archives and historical metadata are never written.
"""
import gzip
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import subprocess
import tarfile

ROOT=Path(__file__).resolve().parents[2]
PACKET=ROOT/'output/isluno-14'
SOURCE='e2deab8c2ad6c4313324d5b94769b91a1f0ae168'
ALLOWED=['Dockerfile','requirements.txt','supervisord.conf','wtyj/agents','wtyj/shared','wtyj/dashboard','wtyj/assets','wtyj/templates']
OUT=ROOT/'tmp/isluno-release-artifacts'/SOURCE
TARGET=OUT/'isluno-backend-source.tar.gz'


def git(*args):
    return subprocess.check_output(['git',*args],cwd=ROOT)


def digest(data):return hashlib.sha256(data).hexdigest()


def main():
    assert not TARGET.exists(), 'Versioned artifact already exists; do not overwrite/repeat.'
    historical_bytes=(PACKET/'artifacts.json').read_bytes()
    historical=json.loads(historical_bytes)
    expected=set(historical['backend_source']['members'])|{'wtyj/shared/mermaid_maintenance.py'}
    tree={}
    for record in git('ls-tree','-r','-z',SOURCE,'--',*ALLOWED).split(b'\0'):
        if not record:continue
        metadata,name=record.split(b'\t',1);mode,kind,oid=metadata.decode().split();name=name.decode()
        assert kind=='blob' and mode in {'100644','100755'}
        path=PurePosixPath(name)
        assert not path.is_absolute() and '..' not in path.parts
        assert not any(part in {'config','data','logs','tmp','tests','__pycache__'} for part in path.parts)
        assert not any(part.startswith('.env') for part in path.parts)
        assert path.suffix not in {'.db','.sqlite','.sqlite3','.pem','.key','.env'}
        assert path.name not in {'client.json','platform.env','calendar-key.json'}
        tree[name]={'mode':mode,'oid':oid}
    assert set(tree)==expected, 'Review unexpected source membership changes before packaging.'
    raw=git('-c','tar.umask=0022','archive','--format=tar',SOURCE,'--',*ALLOWED)
    compressed=gzip.compress(raw,compresslevel=9,mtime=0)
    # Verify archive bytes independently against Git tree blob identities, not archive metadata.
    records=[];seen=set()
    with tarfile.open(fileobj=io.BytesIO(compressed),mode='r:gz') as archive:
        for item in archive:
            assert not item.name.startswith('/') and '..' not in PurePosixPath(item.name).parts
            if item.isdir():continue
            assert item.isfile() and item.name in tree and item.name not in seen
            seen.add(item.name);data=archive.extractfile(item).read()
            obj=hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
            assert obj==tree[item.name]['oid'], item.name
            assert item.mode==int(tree[item.name]['mode'][-3:],8)
            records.append({'path':item.name,'bytes':len(data),'sha256':digest(data),'git_blob':obj})
    assert seen==set(tree)
    changed=json.loads((PACKET/'maintenance-evidence.json').read_text())['files']
    required=[x for x in changed if '/tests/' not in x['path']]
    members={x['path']:x for x in records}
    for item in required:
        assert members[item['path']]['sha256']==item['sha256']
    config_paths=historical['config_default_off']['members']
    assert not git('diff','--name-only',historical['config_default_off']['source_commit'],SOURCE,'--',*config_paths)
    OUT.mkdir(parents=True,exist_ok=False)
    with TARGET.open('xb') as file:file.write(compressed)
    manifest={'schema':'isluno.release-artifacts.v2','accepted_runtime_source':SOURCE,'execution_authorized':False,
        'backend_source':{'path':str(TARGET),'sha256':digest(compressed),'bytes':len(compressed),'source_commit':SOURCE,'files':records},
        'config_default_off':{'artifact':historical['config_default_off'],'byte_identical_at_runtime_source':True,'verification':'Git diff of exact five config paths is empty'},
        'frontend':{'artifact':historical['frontend'],'verification':'Carried prior independently accepted static build; no rebuild or archive rewrite'},
        'historical_provenance':{'file':'artifacts.json','sha256':digest(historical_bytes),'prior_backend_source_commit':historical['backend_source']['source_commit'],'prior_archives_preserved':True},
        'oci_image':{'digest':None,'target_platform_observed':'linux/amd64','reason':'Source archive is not OCI image. Immutable compatible base/dependency recipe and final image remain unverified; no image built/pulled/published.'}}
    (PACKET/'current-artifacts.json').write_text(json.dumps(manifest,indent=2)+'\n')
    check={'source_commit':SOURCE,'archive_sha256':digest(compressed),'archive_bytes':len(compressed),
           'git_tree_members_verified':len(records),'maintenance_runtime_files_verified':len(required),
           'unexpected_members':0,'missing_members':0,'git_blob_or_mode_mismatches':0,'symlinks_or_submodules':0,
           'new_runtime_member':'wtyj/shared/mermaid_maintenance.py','config_paths_unchanged':len(config_paths),
           'frontend_rebuilt':False,'old_archive_bytes_read_or_written':False,'old_metadata_sha256':digest(historical_bytes)}
    (PACKET/'current-package-checks.json').write_text(json.dumps(check,indent=2)+'\n')
    print(json.dumps(check))


if __name__=='__main__':main()
