"""Approved one-shot only: backup, aggregate and coordinator seal; never start app workers."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import sqlite3
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parent))
from inspect_isluno_target import safe_open, require
from project_isluno_old_work import project

DB=Path('/app/data/state_registry.db')
CONFIG=Path('/app/config/client.json')
BACKUP=Path('/release-backup')
ACCOUNT='ca117468b3369f1961ebc941f6774f539d98f82be488dd433c0a2df4eb6a8c09'
MAX_FILE=256*1024*1024


def file_digest(path,optional=False):
    try:fd=safe_open(str(path))
    except FileNotFoundError:
        require(optional);return None
    try:
        before=os.fstat(fd);require(before.st_nlink==1 and before.st_size<=MAX_FILE)
        h=hashlib.sha256();size=0
        while True:
            raw=os.read(fd,65536)
            if not raw:break
            size+=len(raw);require(size<=MAX_FILE);h.update(raw)
        after=os.fstat(fd)
        require((before.st_ino,before.st_size,before.st_mtime_ns,before.st_ctime_ns)==(after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns) and size==before.st_size)
        return h.hexdigest()
    finally:os.close(fd)


def bundle(db):
    values={'db':file_digest(db),'wal':file_digest(Path(str(db)+'-wal'),True)}
    require(file_digest(Path(str(db)+'-journal'),True) is None)
    return hashlib.sha256(json.dumps(values,sort_keys=True).encode()).hexdigest()


def prepare(expected_config,expected_bundle,quiescence_reference,*,db=DB,config=CONFIG,backup=BACKUP,account=ACCOUNT):
    require(re.fullmatch('[0-9a-f]{64}',expected_config or '') and re.fullmatch('[0-9a-f]{64}',expected_bundle or ''))
    require(re.fullmatch('[A-Za-z0-9_-]{1,100}',quiescence_reference or ''))
    import fcntl,stat
    lock=safe_open(str(config)+'.lock')
    try:
        info=os.fstat(lock);require(info.st_nlink==1 and info.st_uid==os.geteuid() and stat.S_IMODE(info.st_mode)==0o600)
        fcntl.flock(lock,fcntl.LOCK_SH | fcntl.LOCK_NB)
        require(file_digest(config)==expected_config and bundle(db)==expected_bundle)
        fd=safe_open(str(config))
        try:
            require(os.fstat(fd).st_size<=1024*1024)
            raw=os.read(fd,1024*1024+1);require(hashlib.sha256(raw).hexdigest()==expected_config)
            def pairs(items):
                result={}
                for key,value in items:require(key not in result);result[key]=value
                return result
            doc=json.loads(raw,object_pairs_hook=pairs)
        finally:os.close(fd)
        require(doc.get('slug')=='mermaid' and doc.get('business',{}).get('slug')=='mermaid')
        binding=doc.get('channel_account_allowlist',{})
        require(binding.get('mode')=='strict' and type(binding.get('zernio_accounts')) is list and len(binding['zernio_accounts'])==1)
        require(hashlib.sha256(binding['zernio_accounts'][0].encode()).hexdigest()==account)
        flags=doc.get('features',{})
        require(flags.get('mermaid_cutover_coordination') is True and flags.get('isluno_itinerary_demo_v1') is False)
        parent=safe_open(str(backup),directory=True)
        try:
            info=os.fstat(parent);require(stat.S_IMODE(info.st_mode)==0o700 and info.st_uid==os.geteuid())
            fd=os.open('state_registry.sqlite3',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=parent);os.close(fd)
        finally:os.close(parent)
        snapshot=backup/'state_registry.sqlite3'
        before=time.monotonic()
        source=sqlite3.connect('file:'+str(db)+'?mode=ro',uri=True,timeout=0)
        destination=sqlite3.connect(snapshot,timeout=0)
        try:
            source.execute('PRAGMA query_only=ON')
            def progress(*_):require(time.monotonic()-before<15)
            source.backup(destination,pages=256,progress=progress,sleep=.01)
        finally:destination.close();source.close()
        require(bundle(db)==expected_bundle and file_digest(config)==expected_config)
        aggregate=project(str(snapshot))
        require(aggregate['blocking_records']==0)
        # Explicit db_path bypasses database(), hence no state_registry import.
        require('shared.state_registry' not in sys.modules)
        from shared import config_loader
        require(Path(config_loader._CONFIG_PATH).resolve()==config.resolve())
        from shared import mermaid_maintenance as maintenance
        require('shared.state_registry' not in sys.modules)
        with sqlite3.connect('file:'+str(db)+'?mode=ro',uri=True,timeout=0) as connection:
            require(connection.execute("SELECT 1 FROM sqlite_master WHERE name IN ('mermaid_maintenance','isluno_cutover')").fetchone() is None)
        require(file_digest(config)==expected_config and bundle(db)==expected_bundle)
        maintenance.initialize(db_path=str(db))
        generation=maintenance.close(0,seconds=120,coverage=maintenance.COVERAGE,
                                      evidence=quiescence_reference,db_path=str(db))
        maintenance.seal(generation,db_path=str(db))
        with sqlite3.connect('file:'+str(db)+'?mode=ro',uri=True,timeout=0) as connection:
            phase,deadline=connection.execute("SELECT phase,deadline FROM mermaid_maintenance WHERE tenant='mermaid'").fetchone()
        require(phase=='sealed' and time.time()<deadline and 'shared.state_registry' not in sys.modules)
        return {'status':'sealed','generation':generation,'deadline_epoch':deadline,
                'snapshot_sha256':aggregate['snapshot_sha256'],'aggregate':aggregate,
                'state_registry_imported':False,'application_workers_started':False}
    finally:os.close(lock)


class FixedParser(argparse.ArgumentParser):
    def error(self,message):raise ValueError()


def main(argv=None):
    try:
        p=FixedParser(description=__doc__);p.add_argument('--execute-approved-seal',action='store_true')
        p.add_argument('--expected-config-sha256');p.add_argument('--expected-db-bundle-sha256');p.add_argument('--verified-quiescence-reference')
        args=p.parse_args(argv)
        if not args.execute_approved_seal:print('{"status":"offline_default","mutations":false}');return 0
        import socket,subprocess,threading
        def denied(*a,**k):raise ValueError()
        socket.socket.connect=denied;socket.socket.connect_ex=denied;socket.socket.sendto=denied
        socket.create_connection=denied;socket.getaddrinfo=denied
        subprocess.Popen=denied;threading.Thread.start=denied
        def expired(*_):raise ValueError()
        old=signal.signal(signal.SIGALRM,expired);signal.setitimer(signal.ITIMER_REAL,45)
        try:result=prepare(args.expected_config_sha256,args.expected_db_bundle_sha256,args.verified_quiescence_reference)
        finally:signal.setitimer(signal.ITIMER_REAL,0);signal.signal(signal.SIGALRM,old)
        print(json.dumps(result,sort_keys=True));return 0
    except Exception:
        print('{"status":"stopped","check":"sealed_start","automatic_retry":false}');return 1


if __name__=='__main__':raise SystemExit(main())
