"""Bounded H1-H7 metadata inspection. Default is offline; execution needs separate authority.

No application imports, protected config reads, DB queries or provider calls.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import selectors
import signal
import stat
import subprocess
import sys
import time

HOST = '108.61.192.52'
CONTAINER = 'wtyj-mermaid'
ROOT = '/root/clients/mermaid'
POINTER = '/var/www/unboks-dashboard/current'
RELEASES = '/var/www/unboks-dashboard/releases'
MAX_OUTPUT = 65536
FILE_LIMIT = 8 * 1024 * 1024
TOTAL_LIMIT = 64 * 1024 * 1024
DIGEST = re.compile(r'sha256:[0-9a-f]{64}\Z')
HEX = re.compile(r'[0-9a-f]{64}\Z')


class Rejected(Exception):
    """Only fixed reason codes may cross the output boundary."""


def require(ok, reason='invalid_metadata'):
    if not ok:
        raise Rejected(reason)


def bounded_command(argv, *, timeout=15, cap=MAX_OUTPUT, data=None):
    """Bound stdout+stderr together before buffering; never expose stderr."""
    proc = subprocess.Popen(argv, stdin=subprocess.PIPE if data is not None else subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    sel = selectors.DefaultSelector()
    result = bytearray()
    count = 0
    deadline = time.monotonic() + timeout
    end = deadline - min(.1, timeout / 5)  # reserve bounded cleanup inside the call budget
    pending = memoryview(data or b'')
    try:
        for stream in (proc.stdout, proc.stderr):
            os.set_blocking(stream.fileno(), False)
            sel.register(stream, selectors.EVENT_READ)
        if proc.stdin:
            os.set_blocking(proc.stdin.fileno(), False)
            if pending:
                sel.register(proc.stdin, selectors.EVENT_WRITE)
            else:
                proc.stdin.close()
        while sel.get_map():
            left = end - time.monotonic()
            require(left > 0, 'timeout')
            for key, event in sel.select(min(left, .1)):
                if event & selectors.EVENT_WRITE:
                    n = os.write(key.fd, pending[:8192]); pending = pending[n:]
                    if not pending:
                        sel.unregister(key.fileobj); key.fileobj.close()
                    continue
                block = os.read(key.fd, 8192)
                if not block:
                    sel.unregister(key.fileobj)
                    continue
                count += len(block)
                require(count <= cap, 'output_limit')
                if key.fileobj is proc.stdout:
                    result.extend(block)
        require(time.monotonic() < end, 'timeout')
        try:
            code = proc.wait(timeout=max(.001, end-time.monotonic()))
        except subprocess.TimeoutExpired:
            raise Rejected('timeout') from None
        require(code == 0, 'command_failed')
        try:
            return result.decode('utf-8', errors='strict')
        except UnicodeError:
            raise Rejected('invalid_encoding') from None
    finally:
        sel.close()
        try:
            # The direct child may already have exited while descendants retain pipes.
            # This session/process group is ours even after proc.poll() reports exit.
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                proc.wait(timeout=max(.001, min(.1, deadline-time.monotonic())))
            except subprocess.TimeoutExpired:
                raise Rejected('cleanup_timeout') from None
        finally:
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                if stream and not stream.closed:
                    stream.close()


def safe_open(path, directory=False):
    """Walk every component with O_NOFOLLOW and dirfds, including parent dirs."""
    path = PurePosixPath(path)
    require(path.is_absolute() and '..' not in path.parts, 'unsafe_path')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for index, part in enumerate(path.parts[1:]):
            is_dir = index < len(path.parts)-2 or directory
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            if is_dir:
                flags |= os.O_DIRECTORY
            new = os.open(part, flags, dir_fd=fd)
            os.close(fd); fd = new
        info = os.fstat(fd)
        require(stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode), 'nonregular_path')
        return fd
    except BaseException:
        os.close(fd)
        raise


"""Reviewed no-reply rollout helpers; no customer erasure or backups."""
import os,json,hashlib,stat,fcntl,sqlite3,time,urllib.request,shlex
from pathlib import Path
CLIENT=Path('/root/clients/mermaid/config/client.json')
DATABASE=Path('/root/clients/mermaid/data/state_registry.db')
ICP_DATA=Path('/root/unboks-internal-control-panel/data')
BACKEND='sha256:f3cac23eaddd2b904f587399e2537e35b6bb40bc8c5310d4f76a0953cc6f1817'
ICP_IMAGE='sha256:2e547a43021e2c3f4e98b3404a0d4780d19af9c0cf751d84c25745eb6acff77c'
HELPER_WRAPPER="import runpy,sys\nsys.argv=sys.argv[1:]\ntry:runpy.run_path(sys.argv[0],run_name='__main__')\nexcept SystemExit as exc:\n if exc.code not in (None,0,1):raise\n"
EVENTS=[];LOCKS=[];STATE={'gates_closed':False,'sealed':False,'image_changed':False,'open':False,'rolled_back':False}


def event(phase,**extra):
 EVENTS.append({'phase':phase,**extra})
 print(json.dumps({'progress':phase}),flush=True)


def checked_read(path,limit=268435456):
 fd=safe_open(str(path))
 try:
  s=os.fstat(fd);require(stat.S_ISREG(s.st_mode) and s.st_nlink==1 and s.st_size<=limit,'file_shape')
  chunks=[];size=0
  while b:=os.read(fd,65536):size+=len(b);require(size<=limit,'file_size');chunks.append(b)
  after=os.fstat(fd);require((s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns)==(after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns),'file_changed')
  return b''.join(chunks)
 finally:os.close(fd)


def digest(path):
 fd=safe_open(str(path))
 try:
  before=os.fstat(fd);require(stat.S_ISREG(before.st_mode) and before.st_nlink==1 and before.st_size<=268435456,'digest_shape')
  h=hashlib.sha256();size=0
  while raw:=os.read(fd,65536):size+=len(raw);require(size<=268435456,'digest_size');h.update(raw)
  after=os.fstat(fd);require((before.st_ino,before.st_size,before.st_mtime_ns,before.st_ctime_ns)==(after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns) and size==before.st_size,'digest_changed')
  return h.hexdigest()
 finally:os.close(fd)
def inspect(name):return json.loads(bounded_command(['docker','inspect',name],timeout=5,cap=262144))[0]
def cmd(args,seconds=30):return bounded_command(args,timeout=seconds,cap=65536)
def unit(name):return dict(line.split('=',1) for line in cmd(['systemctl','show',name,'--property=LoadState,ActiveState,SubState,MainPID'],10).splitlines() if '=' in line)
def env(x):return dict(v.split('=',1) for v in x['Config'].get('Env',[]) if '=' in v)
def assert_stopped(name):
 x=inspect(name);require(not x['State']['Running'] and x['State']['Pid']==0 and x['State']['ExitCode']==0,'container_not_cleanly_stopped')

def lease(path,exclusive,seconds=15):
 fd=safe_open(str(path));s=os.fstat(fd);require(stat.S_ISREG(s.st_mode) and s.st_uid==0 and s.st_nlink==1,'lease_shape')
 deadline=time.monotonic()+seconds
 while True:
  try:fcntl.flock(fd,(fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)|fcntl.LOCK_NB);break
  except BlockingIOError:
   if time.monotonic()>=deadline:os.close(fd);raise Rejected('lease_busy')
   time.sleep(.05)
 LOCKS.append(fd);return fd

def release_lock(fd):
 LOCKS.remove(fd);os.close(fd)
def runtime(code,seconds=10):
 return json.loads(cmd(['docker','run','--rm','--pull','never','--network','none','--env','PYTHONPATH=/app','--env','CLIENT_CONFIG_PATH=/app/config/client.json','--env','TENANT_ID=mermaid','--env','TENANT_ACCOUNT_ALLOWLIST_REQUIRED=true','--mount','type=bind,src=/root/clients/mermaid/config,dst=/app/config,readonly','--mount','type=bind,src=/root/clients/mermaid/data,dst=/app/data','--entrypoint','python',BACKEND,'-c',code],seconds))

EXPECTED={'agent_messages_sent': 0, 'authenticated_ui': 'pending', 'automatic_retry': False, 'container_id': 'd698b4d8daa5ad58fbdc1de4a13a9a75a046e6dc13bff5e73624199b53d47950', 'customer_tables_preserved': 91, 'frontend_commit': '25dfebfb8688f76ef357b97c60c893fdf1bb8791', 'frontend_index_sha256': 'c15c71d8487780a63999578005560899a50634e4c8f4a1b2a9e9419093c9f11b', 'frontend_target': '/var/www/unboks-dashboard/releases/isluno-25dfebf-20260909-b', 'health': 200, 'image': 'sha256:8ea85eb29ae6bea3ff64012cb276970a2daef15f5e0958c743df2c9bca5ac11d', 'ingress_generation': 11, 'profile_sha256': '5c90d1979c7599e9bdd82a809a6cebe07db150692ed949b7a88e1070b16616b3', 'runtime_generation': 12, 'source': 'ab88343b325d3ee67a6dcdad5e15e1f6dd2d254f', 'state': {'frontend_changed': True, 'gates_closed': True, 'image_changed': True, 'marker_created': True, 'open': True, 'rolled_back': False, 'sealed': True, 'watchdog_stopped': True, 'worker_stopped': True}, 'status': 'fix_deployed_open', 'elapsed_seconds': 22.635}
HASHES={'agents/marina/marina_agent.py': '768e62fb68195bc8e90d5753033f7bb46bc194008de50bfb120c3c8fd80616dd', 'agents/social/isluno_callbacks.py': 'f79916a0d6a8762e1035c966edcfe46b9165a1f63b44f3a000c40f19286644c9', 'agents/social/isluno_conversation.py': 'f3059c0b1e7d20394e37db04f945d14022bb37d1689c260378812e57dd74eb8c', 'agents/social/isluno_conversation_understanding.py': 'd84eb5508973d9d8dfaac67bb378a44a196029221659457ead502197e8124994', 'agents/social/isluno_delivery.py': 'e0109beee9f884537c63451c9beda039e7c2c9b19a21cc2069ec9351307a06d5', 'agents/social/isluno_discovery.py': 'c1482cfcdc992860fc3ea8377ccc5e63d243bf696337496e8b876a8ebbe05733', 'agents/social/isluno_fulfillment.py': '9c44122f3aa397df2d1edeafce02cda8943bc0bdd515f7263055d653bcf032c7', 'agents/social/isluno_hospitality.py': '971f4238f30c756ff0af8709aecafb8945d7c8f34af95e4f7238e076e31362bc', 'agents/social/isluno_payments.py': 'c49e0ff3b5b8026506f47053e72c6882a6d3d10c937b45621a952e4e474ef345', 'agents/social/isluno_quote_delivery.py': '34b8d27e31530ffbae57d9cba17cd9554e0e2bc745e530b2a96fd3111b9f2135', 'agents/social/isluno_quotes.py': '197c2d5d92bd69ac3c7d956bbe2b5cfb85129dd831a2646bdb3205ec98d3d853', 'agents/social/isluno_recovery.py': '7b67d7c4d630fc0f76d819953bfb26018550797df4fffa2ce613670aafc6c8f0', 'agents/social/isluno_recovery_copy.py': '6d7ff02bd215816921ae078844a6080f58963845b216db127d469d7b5047e6fb', 'agents/social/isluno_wire.py': '00ff73372261e0c07399a102ed76264283b43c291254e202621976e2043f58d7', 'agents/social/social_agent.py': 'fa775649d46a67d2187d2dd3627fa986c4d911a846e01a53902179732632b5f0', 'agents/social/webhook_server.py': '0e9f5fffeaf06d4c0b16b078b04acc070e7a9463cca6b0c16de55b4512004755'}
PROOF_CODE='"""Exact current terminal-history proof for this authorized image/profile rollout.\n\nOperator-only; no runtime guard changes, customer row relabelling or replay.\n"""\nimport hashlib,json,sqlite3,time\nfrom shared import mermaid_maintenance as maintenance\nfrom shared.isluno_config import JourneyScope,require_scope\n\nTABLES=(\'inbound_processing_events\',\'isluno_conversation_turns\',\'isluno_recovery_incidents\',\n        \'isluno_recovery_contacts\',\'isluno_booking_sessions\',\'isluno_discovery_plans\',\n        \'isluno_discovery_actions\',\'isluno_discovery_latest\',\'isluno_recovery_inbound\')\nUNKNOWN=[\'isluno_conversation_turns\',\'isluno_discovery_plans\',\'isluno_recovery_incidents\']\n\n\ndef check(value,code):\n    if not value:raise ValueError(code)\n\n\ndef records(db,table):\n    cursor=db.execute(\'SELECT * FROM \'+table+\' ORDER BY rowid LIMIT 1001\')\n    rows=[dict(zip([d[0] for d in cursor.description],row)) for row in cursor]\n    check(len(rows)<=1000,\'proof_row_bound\')\n    return rows\n\n\nBASE_HASH=\'6f5c245490da32498b684357fb9d444cffe0f34989979aa45a45b532b08576d8\'\nFAILURE_ERROR_HASH=\'5d0e76c32b17ab5bb260babf01d41c87054e756e4faceff60c1e04c9f375747e\'\nREJECTED={\'22a64451500682b55a7dec2c740004da690b6d80b19c2dd00c779743e9330f79\',\'b4779117df31da0f403cd759414195ef241d6ec82b16de6ea83258c7ae5d3293\'}\ndef digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(\',\',\':\')).encode()).hexdigest()\ndef review(db):\n    state=maintenance._snapshot(db)\n    check(not state[\'workers\'] and not state[\'pending\'],\'work_not_drained\')\n    check(state[\'unreviewed_ledgers\']==UNKNOWN,\'unclassified_ledger\')\n    check(db.execute(\'SELECT tenant FROM isluno_cutover\').fetchall()==[(\'mermaid\',)],\'cutover_identity\')\n    data={table:records(db,table) for table in TABLES}\n    schema={table:db.execute("SELECT sql FROM sqlite_master WHERE type=\'table\' AND name=?",(table,)).fetchone()[0] for table in TABLES}\n    check(digest({\'schema\':schema,\'rows\':data})==BASE_HASH,\'reviewed_history_changed\')\n    check(len(data[\'isluno_conversation_turns\'])==6 and len(data[\'isluno_discovery_plans\'])==5 and len(data[\'isluno_recovery_incidents\'])==3,\'history_shape\')\n    scope=JourneyScope(**json.loads(data[\'isluno_recovery_contacts\'][0][\'scope_json\']));require_scope(scope)\n    completed={r[\'trigger_id\']:json.loads(r[\'outcome\']) for r in data[\'isluno_conversation_turns\'] if r[\'outcome\']}\n    check(len(completed)==3,\'completed_count\')\n    check(all(o[\'status\']==\'saved\' and o[\'error\'] is None and o[\'itinerary\'] is None for o in completed.values()),\'unexpected_booking_or_error\')\n    current=[r for r in data[\'inbound_processing_events\'] if r[\'reason\']!=\'history_cleared_no_replay\']\n    check(len(current)==6 and len(data[\'inbound_processing_events\'])==180,\'inbound_shape\')\n    for row in current:\n        check(row[\'conversation_id\']==scope.conversation_id and row[\'channel\']==\'whatsapp\',\'inbound_scope\')\n        check(not row[\'processing_token\'] and not row[\'lease_expires_at\'] and row[\'attempt_count\']==row[\'provider_retry_count\']==0,\'execution_or_retry\')\n        if hashlib.sha256(row[\'message_id\'].encode()).hexdigest() in REJECTED:\n            check((row[\'status\'],row[\'reason\'])==(\'send_failed\',\'provider_send_failed\'),\'failure_disposition\')\n            check(hashlib.sha256(row[\'last_error\'].encode()).hexdigest()==FAILURE_ERROR_HASH,\'failure_code_changed\')\n        else:check((row[\'status\'],row[\'reason\']) in {(\'ignored\',\'no_reply_returned\'),(\'replied\',\'provider_send_ok\')},\'prior_disposition\')\n    check(sum(r[\'status\']==\'accepted\' and bool(r[\'provider_id\']) for r in data[\'isluno_discovery_plans\'])==3,\'accepted_plan_count\')\n    check(sum(r[\'status\']==\'rejected\' and not r[\'provider_id\'] for r in data[\'isluno_discovery_plans\'])==2,\'rejected_plan_count\')\n    check(all(r[\'scope_key\']==scope.key for r in data[\'isluno_discovery_plans\']),\'plan_scope\')\n    check(all(r[\'status\']==\'progress_resumed_new_turn\' and r[\'kind\']==\'understanding_failure\' for r in data[\'isluno_recovery_incidents\']),\'historic_incidents_changed\')\n    # Preserve these old labels as historical records, NOT verified delivery.\n    for table in (\'isluno_itineraries\',\'isluno_operator_requests\',\'isluno_quote_jobs\',\'isluno_quote_deliveries\',\'isluno_payments\',\'isluno_paid_emails\',\'isluno_reminders\',\'zernio_failed_event_queue\'):\n        exists=db.execute("SELECT 1 FROM sqlite_master WHERE type=\'table\' AND name=?",(table,)).fetchone()\n        check(not exists or db.execute(\'SELECT count(*) FROM \'+table).fetchone()[0]==0,\'other_work_or_booking\')\n    for table in (\'pending_notifications\',\'inbound_operator_notifications\'):\n        data[table]=records(db,table);schema[table]=db.execute("SELECT sql FROM sqlite_master WHERE type=\'table\' AND name=?",(table,)).fetchone()[0]\n        check(len(data[table])==2,\'operator_notice_count\')\n    for row in data[\'pending_notifications\']:\n        check(row[\'notification_type\']==\'escalation\' and row[\'mode\']==\'soft\' and row[\'channel\']==\'whatsapp\' and row[\'customer_id\']==scope.conversation_id and row[\'status\']==\'pending\',\'operator_notice_scope_or_status\')\n        check(row[\'subject\']==\'[DELIVERY FAILED] Automated reply needs review\',\'operator_notice_kind\')\n    check({r[\'id\'] for r in data[\'pending_notifications\']}=={r[\'notification_id\'] for r in data[\'inbound_operator_notifications\']},\'operator_notice_links\')\n    control=db.execute(\'SELECT ai_muted,ai_mute_source,blocked FROM conversation_status WHERE conversation_id=?\',(scope.conversation_id,)).fetchone()\n    controls={\'row_present\':control is not None,\'ai_muted\':bool(control and control[0]),\'ai_mute_source\':control[1] if control else \'\', \'blocked\':bool(control and control[2])}\n    check(not controls[\'ai_muted\'] and not controls[\'blocked\'],\'conversation_paused_or_blocked\')\n    check(controls[\'ai_mute_source\'] in (\'\',None,\'manual\',\'hard_escalation\'),\'unexpected_mute_source\')\n    raw=json.dumps({\'schema\':schema,\'rows\':data,\'controls\':controls},sort_keys=True,separators=(\',\',\':\')).encode();check(len(raw)<=4000000,\'proof_bytes_bound\')\n    return {\'sha256\':hashlib.sha256(raw).hexdigest(),\'classification\':\'six_retained_turns_three_accepted_two_rejected_plans_no_replay\',\'turns\':6,\'accepted_plans\':3,\'rejected_plans\':2,\'historical_progress_labels_unverified\':3,\'preserved_operator_notices\':2,\'controls\':controls,\'unreviewed_ledgers\':UNKNOWN}\n\ndef verify(db,expected):\n    value=review(db);check(value[\'sha256\']==expected,\'stale_history_proof\');return value\n\n\ndef seal(path,generation,expected):\n    db=sqlite3.connect(path,timeout=0)\n    try:\n        db.execute(\'BEGIN IMMEDIATE\');s=maintenance._snapshot(db)\n        check(s[\'generation\']==generation and s[\'phase\']==\'draining\',\'stale_generation\')\n        check(time.time()<s[\'deadline\'] and s[\'coverage_complete\'],\'invalid_drain_window\')\n        proof=verify(db,expected)\n        db.execute("UPDATE mermaid_maintenance SET phase=\'sealed\' WHERE tenant=\'mermaid\' AND generation=? AND phase=\'draining\'",(generation,))\n        db.execute(\'INSERT INTO mermaid_maintenance_audit(generation,action,at) VALUES(?,?,?)\',(generation,\'seal_communication_history:\'+expected,time.time()))\n        db.commit();return proof\n    except BaseException:db.rollback();raise\n    finally:db.close()\n\n\ndef readiness(path,generation,expected):\n    db=sqlite3.connect(\'file:\'+path+\'?mode=ro\',uri=True,timeout=0)\n    try:\n        db.execute(\'PRAGMA query_only=ON\');db.execute(\'BEGIN\');s=maintenance._snapshot(db)\n        check(s[\'generation\']==generation and s[\'phase\']==\'sealed\' and s[\'coverage_complete\'],\'not_reviewed_sealed\')\n        proof=verify(db,expected)\n        check(db.execute(\'SELECT count(*) FROM mermaid_maintenance_audit WHERE generation=? AND action=?\',(generation,\'seal_communication_history:\'+expected)).fetchone()[0]==1,\'seal_audit_missing\')\n        return proof\n    finally:db.close()\n\n\ndef reopen(path,generation,expected):\n    db=sqlite3.connect(path,timeout=0)\n    try:\n        db.execute(\'BEGIN IMMEDIATE\');s=maintenance._snapshot(db)\n        check(s[\'generation\']==generation and s[\'phase\']==\'sealed\' and s[\'coverage_complete\'],\'not_reviewed_sealed\')\n        proof=verify(db,expected)\n        check(db.execute(\'SELECT count(*) FROM mermaid_maintenance_audit WHERE generation=? AND action=?\',(generation,\'seal_communication_history:\'+expected)).fetchone()[0]==1,\'seal_audit_missing\')\n        db.execute("UPDATE mermaid_maintenance SET phase=\'open\',generation=generation+1,coverage=\'[]\',evidence=\'\' WHERE tenant=\'mermaid\'")\n        db.execute(\'INSERT INTO mermaid_maintenance_audit(generation,action,at) VALUES(?,?,?)\',(generation,\'reopen_communication_history:\'+expected,time.time()))\n        db.commit();return proof\n    except BaseException:db.rollback();raise\n    finally:db.close()\n\n\ndef abort_drain(path,generation,packet):\n    """Cancel a not-yet-sealed attempt on the verified unchanged running runtime.\n\n    Caller must prove original container/image/profile/client and no activation.\n    This is not seal/readiness: workers, unresolved rows and pending IDs stay intact.\n    """\n    db=sqlite3.connect(path,timeout=0)\n    try:\n        db.execute(\'BEGIN IMMEDIATE\');s=maintenance._snapshot(db)\n        check(s[\'generation\']==generation and s[\'phase\']==\'draining\',\'abort_not_unsealed_drain\')\n        check(s[\'coverage_complete\'],\'abort_coverage_missing\')\n        db.execute("UPDATE mermaid_maintenance SET phase=\'open\',generation=generation+1,coverage=\'[]\',evidence=\'\' WHERE tenant=\'mermaid\'")\n        db.execute(\'INSERT INTO mermaid_maintenance_audit(generation,action,at) VALUES(?,?,?)\',(generation,\'abort_communication_before_cutover:\'+packet,time.time()))\n        db.commit()\n    except BaseException:db.rollback();raise\n    finally:db.close()\n\ndb=sqlite3.connect(\'file:/app/data/state_registry.db?mode=ro\',uri=True,timeout=0)\ntry:\n db.execute(\'PRAGMA query_only=ON\');db.execute(\'BEGIN\');print(json.dumps(verify(db,\'c20ab63aa42f4b705033ac0a59c1ee6b9334fa01d53c9d805499f844b5c9f6cc\')))\nfinally:db.close()'
PROOF='c20ab63aa42f4b705033ac0a59c1ee6b9334fa01d53c9d805499f844b5c9f6cc'
signal.alarm(45)
mm=inspect('wtyj-mermaid');icp=inspect('unboks-internal-control-panel-wtyj-admin-1')
require(mm['Id']==EXPECTED['container_id'] and mm['Image']==EXPECTED['image'] and mm['State']['Running'],'live_runtime')
require(icp['Id']=='6642944f55241667adabb002b16f0859817aa8ec962819ffc5dc61c89dedcb46' and icp['Image']==ICP_IMAGE and icp['State']['Running'],'live_icp')
require(digest(CLIENT)=='1424d087d01af8f93cfa74feb677d603660a9128f5f1d686ee2778479ab7be7a','live_config')
require(digest(CLIENT.with_name('isluno_profile.json'))==EXPECTED['profile_sha256'],'live_profile')
require(not Path('/root/clients/mermaid/.maintenance').exists(),'live_maintenance_marker')
require(unit('nr3-provision-worker.service')['ActiveState']=='active' and unit('unboks-tracy-watchdog.timer')['ActiveState']=='active','live_producers')
require(os.readlink('/var/www/unboks-dashboard/current')==EXPECTED['frontend_target'],'dashboard_pointer')
gate=json.loads(cmd(['docker','exec',icp['Id'],'python','-m','host.mermaid_ingress_control'],10));require(gate['phase']=='open' and gate['generation']==11,'live_ingress')
app="import hashlib,json\nfrom pathlib import Path\nfrom shared import mermaid_maintenance as m,isluno_config\nfrom shared.isluno_catalog import CatalogStore\ns=m.status('/app/data/state_registry.db');assert s['phase']=='open' and s['generation']==12\nc=isluno_config.capabilities();assert c['enabled'] and c['tenant_slug']=='mermaid' and all(c['capabilities'].values())\nassert len(CatalogStore(Path('/app/config/isluno_catalog.json')).snapshot()['catalog']['products'])==31\nh={n:hashlib.sha256(Path('/app',n).read_bytes()).hexdigest() for n in "+repr(list(HASHES))+"}\nprint(json.dumps({'hashes':h,'phase':s['phase'],'generation':s['generation'],'catalog':31}))"
value=json.loads(cmd(['docker','exec',mm['Id'],'python','-c',app],10));require(value['hashes']==HASHES,'live_source')
require(digest(Path(EXPECTED['frontend_target'])/'index.html')==EXPECTED['frontend_index_sha256'],'live_frontend_index')
proof=json.loads(cmd(['docker','exec',mm['Id'],'python','-c',PROOF_CODE],15));require(proof['sha256']==PROOF,'live_retained_history')
with urllib.request.urlopen('http://127.0.0.1:8102/health',timeout=3) as response:require(response.status==200,'live_health')
print(json.dumps({'status':'live_verified','source':EXPECTED['source'],'image':mm['Image'],'container_id':mm['Id'],'health':200,'runtime_generation':12,'ingress_generation':11,'catalog_products':31,'source_files_verified':len(HASHES),'dashboard_pointer_verified':True,'producers_restored':True,'agent_messages_sent':0,'retained_history_proof':proof['sha256']}))
