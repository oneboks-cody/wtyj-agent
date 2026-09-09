"""Prepare offline; dispatch only after explicit approval of the immutable packet."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO/'wtyj/scripts'))
from inspect_isluno_target import bounded_command, ssh_argv, Rejected
BASE = REPO/'output/isluno-14/recovery-e'
PRIVATE = REPO/'tmp/isluno-recovery-e'

def durable(path, raw):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'wb') as file:file.write(raw);file.flush();os.fsync(file.fileno())

def prepare():
    PRIVATE.mkdir(mode=0o700,exist_ok=True)
    evidence=REPO/'tmp/isluno-deploy-approved-b'
    expected=json.loads((evidence/'preflight-result.json').read_text())
    stop=json.loads((evidence/'cutover-result.json').read_text())
    inspection=json.loads((evidence/'stop-inspection.json').read_text())
    assert expected['status']=='pass' and stop['status']=='stopped'
    icp_id=next(row['container_id'] for row in stop['events'] if row['phase']=='new_icp_closed')
    raw=(REPO/'wtyj/scripts/recover_isluno_staged_cutover_e.py').read_bytes()
    helper=(REPO/'wtyj/scripts/inspect_isluno_target.py').read_text()
    prefix=helper.split('\ndef fields(')[0]
    safe=helper[helper.index('def safe_open('):helper.index('\ndef path_metadata(')]
    bindings={'EXPECTED':expected,'STOPPED_ICP_ID':icp_id,'STAGED_CONFIG_SHA':inspection['config_sha256'],
              'WORKER_SHA':hashlib.sha256(Path('/Users/calvin/Projects/isluno-control-plane-20260909/host/nr3_provision_worker.py').read_bytes()).hexdigest(),
              'HELPER_FILES':json.loads((REPO/'output/isluno-14/release-helpers.json').read_text())['files'],
              'RECOVERY_HELPER':raw,'RECOVERY_HELPER_SHA':hashlib.sha256(raw).hexdigest(),
              'APPROVAL_REFERENCE':'calvin-isluno-recovery-e'}
    code=prefix+'\n'+safe+'\n'+'\n'.join(key+'='+repr(value) for key,value in bindings.items())+'\n'+(BASE/'remote.py').read_text()+'''
def alarm(*_):raise Rejected('recovery_e_deadline')
signal.signal(signal.SIGALRM,alarm);signal.alarm(330)
try:result=execute()
except Exception as exc:result={'status':'stopped','code':str(exc) if isinstance(exc,Rejected) else type(exc).__name__,'state':STATE,'events':EVENTS,'automatic_retry':False}
finally:
 for descriptor in LOCKS:os.close(descriptor)
print(json.dumps(result,sort_keys=True))
'''
    payload=code.encode();assert len(payload)<=131072
    compile(code,'recovery-e-exact-payload','exec')
    durable(PRIVATE/'exact-payload.py',payload)
    files=['wtyj/scripts/recover_isluno_staged_cutover_e.py','wtyj/scripts/inspect_isluno_target.py','wtyj/tests/isluno/test_staged_recovery_e.py','wtyj/tests/isluno/recovery_e_cli_fixture.py','output/isluno-14/RECOVERY-E.md','output/isluno-14/recovery-e/remote.py','output/isluno-14/recovery-e/dispatch.py']
    packet={'run_id':'calvin-isluno-recovery-e','payload_sha256':hashlib.sha256(payload).hexdigest(),'payload_bytes':len(payload),
            'helper_sha256':bindings['RECOVERY_HELPER_SHA'],'helper_bytes':len(raw),'ssh_dispatches':1,'new_helper_files':1,
            'overall_seconds':360,'remote_seconds':330,'helper_seconds':45,'activation_seal_seconds':120,
            'automatic_retries':0,'files':{name:hashlib.sha256((REPO/name).read_bytes()).hexdigest() for name in files}}
    durable(BASE/'packet.json',(json.dumps(packet,indent=2)+'\n').encode())
    print(json.dumps({'status':'prepared_offline',**packet}))

def dispatch(approval_file):
    packet=json.loads((BASE/'packet.json').read_text())
    approval=json.loads(Path(approval_file).read_text())
    assert approval.get('approved_recovery_e') is True
    assert approval.get('packet_sha256')==hashlib.sha256((BASE/'packet.json').read_bytes()).hexdigest()
    assert approval.get('reviewed_commit')==bounded_command(['git','-C',str(REPO),'rev-parse','HEAD'],timeout=5).strip()
    for path,sha in packet['files'].items():assert hashlib.sha256((REPO/path).read_bytes()).hexdigest()==sha
    payload=(PRIVATE/'exact-payload.py').read_bytes()
    assert len(payload)==packet['payload_bytes'] and hashlib.sha256(payload).hexdigest()==packet['payload_sha256']
    durable(PRIVATE/'dispatch.json',json.dumps({'approval':approval,'at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'payload_sha256':packet['payload_sha256']}).encode())
    try:result=json.loads(bounded_command(ssh_argv(),timeout=360,cap=65536,data=payload))
    except Rejected as exc:result={'status':'transport_stopped','code':str(exc),'automatic_retry':False,'remote_state_requires_receipt_review':True}
    durable(PRIVATE/'result.json',(json.dumps(result,indent=2)+'\n').encode());print(json.dumps(result))

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    group=parser.add_mutually_exclusive_group()
    group.add_argument('--prepare-only',action='store_true');group.add_argument('--execute-approved-recovery-e',action='store_true')
    parser.add_argument('--approval-file')
    args=parser.parse_args()
    if args.prepare_only:prepare()
    elif args.execute_approved_recovery_e:
        assert args.approval_file;dispatch(args.approval_file)
    else:print('{"status":"offline_default","mutations":false}')
