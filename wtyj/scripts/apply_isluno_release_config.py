"""Explicit stopped-service config leaf update through canonical lock/CAS/exchange."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import re
import signal
import sys

sys.path.insert(0,str(Path(__file__).resolve().parent))
from sync_mermaid_config_fields import sync
from inspect_isluno_target import require, safe_open
from prepare_isluno_sealed_start import ACCOUNT, file_digest

MEDIA='https://api.unboks.org/api/mermaid/r/isluno/media'
DOCUMENTS='https://api.unboks.org/api/mermaid/r/isluno/documents'


def render(target,phase,generation=None,account=ACCOUNT):
    require(phase in ('stage','activate','rollback'))
    require(target.get('slug')=='mermaid' and target.get('business',{}).get('slug')=='mermaid')
    binding=target.get('channel_account_allowlist',{})
    require(binding.get('mode')=='strict' and isinstance(binding.get('zernio_accounts'),list) and len(binding['zernio_accounts'])==1)
    require(hashlib.sha256(binding['zernio_accounts'][0].encode()).hexdigest()==account)
    updated=copy.deepcopy(target)
    for group in ('features','isluno','mermaid_maintenance'):
        require(group not in updated or isinstance(updated[group],dict));updated.setdefault(group,{})
    leaves={'features.mermaid_cutover_coordination':True,
            'features.isluno_itinerary_demo_v1':phase=='activate',
            'features.mermaid_reminders':False,'isluno.native_carousels':False,
            'isluno.media_base_url':MEDIA,'isluno.document_base_url':DOCUMENTS}
    if phase=='activate':
        require(type(generation) is int and 1<=generation<=2**63-1)
        leaves['mermaid_maintenance.activation_generation']=generation
    changed=[]
    for path,value in leaves.items():
        group,key=path.split('.')
        if updated[group].get(key)!=value or type(updated[group].get(key)) is not type(value):changed.append(path)
        updated[group][key]=value
    require(updated['channel_account_allowlist']==target['channel_account_allowlist'])
    return updated,changed


class FixedParser(argparse.ArgumentParser):
    def error(self,message):raise ValueError()


def main(argv=None):
    try:
        parser=FixedParser(description=__doc__);parser.add_argument('--execute-approved-write',action='store_true')
        parser.add_argument('--phase',choices=['stage','activate','rollback']);parser.add_argument('--expected-sha256')
        parser.add_argument('--sealed-generation',type=int);parser.add_argument('--service-stopped-verified',action='store_true')
        args=parser.parse_args(argv)
        if not args.execute_approved_write:print('{"status":"offline_default","config_read":false}');return 0
        def expired(*_):raise ValueError()
        signal.signal(signal.SIGALRM,expired)
        signal.setitimer(signal.ITIMER_REAL,30)
        require(args.service_stopped_verified and args.phase and re.fullmatch('[0-9a-f]{64}',args.expected_sha256 or ''))
        target=Path('/app/config/client.json');source=Path(__file__).with_name('isluno-config-source.json')
        for path in (target.parent,Path('/release-backup'),Path('/release-backup/config')):
            fd=safe_open(str(path),directory=True)
            import os
            os.close(fd)
        if args.phase=='activate':
            # Readiness is checked in the exact pinned application image, no workers.
            import sqlite3
            from shared import config_loader, mermaid_maintenance
            require(Path(config_loader._CONFIG_PATH).resolve()==target.resolve())
            require(mermaid_maintenance.requested())
            require_sealed=mermaid_maintenance.require_sealed
            with sqlite3.connect('file:/app/data/state_registry.db?mode=ro',uri=True,timeout=0) as db:
                require_sealed(db,args.sealed_generation)
        changed,_=sync(source,target,Path('/release-backup/config'),apply=True,service_stopped=True,
                       expected_sha256=args.expected_sha256,
                       merge_function=lambda source,target:render(target,args.phase,args.sealed_generation))
        print(json.dumps({'status':'applied','phase':args.phase,'changed_paths':changed,'config_sha256':file_digest(target)}));return 0
    except Exception:
        print('{"status":"stopped","check":"release_config","automatic_retry":false}');return 1
    finally:
        signal.setitimer(signal.ITIMER_REAL,0)


if __name__=='__main__':raise SystemExit(main())
