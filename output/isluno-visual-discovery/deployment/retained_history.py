"""Exact current terminal-history proof for this authorized image/profile rollout.

Operator-only; no runtime guard changes, customer row relabelling or replay.
"""
import hashlib,json,sqlite3,time
from shared import mermaid_maintenance as maintenance
from shared.isluno_config import JourneyScope,require_scope

TABLES=('inbound_processing_events','isluno_conversation_turns','isluno_recovery_incidents',
        'isluno_recovery_contacts','isluno_booking_sessions','isluno_discovery_plans',
        'isluno_discovery_actions','isluno_discovery_latest','isluno_recovery_inbound')
UNKNOWN=['isluno_conversation_turns','isluno_discovery_plans','isluno_recovery_incidents']


def check(value,code):
    if not value:raise ValueError(code)


def records(db,table):
    cursor=db.execute('SELECT * FROM '+table+' ORDER BY rowid LIMIT 1001')
    rows=[dict(zip([d[0] for d in cursor.description],row)) for row in cursor]
    check(len(rows)<=1000,'proof_row_bound')
    return rows


BASE_HASH='8e3d24c709e57ca4b0da167dd50f523911f1a1eaedc69ff5eb416d8e1ceef04d'
FAILURE_ERROR_HASH='5d0e76c32b17ab5bb260babf01d41c87054e756e4faceff60c1e04c9f375747e'
REJECTED={'22a64451500682b55a7dec2c740004da690b6d80b19c2dd00c779743e9330f79','b4779117df31da0f403cd759414195ef241d6ec82b16de6ea83258c7ae5d3293'}
def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def review(db):
    state=maintenance._snapshot(db)
    check(not state['workers'] and not state['pending'],'work_not_drained')
    check(state['unreviewed_ledgers']==UNKNOWN,'unclassified_ledger')
    check(db.execute('SELECT tenant FROM isluno_cutover').fetchall()==[('mermaid',)],'cutover_identity')
    data={table:records(db,table) for table in TABLES}
    schema={table:db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone()[0] for table in TABLES}
    check(digest({'schema':schema,'rows':data})==BASE_HASH,'reviewed_history_changed')
    check(len(data['isluno_conversation_turns'])==7 and len(data['isluno_discovery_plans'])==6 and len(data['isluno_recovery_incidents'])==3,'history_shape')
    scope=JourneyScope(**json.loads(data['isluno_recovery_contacts'][0]['scope_json']));require_scope(scope)
    completed={r['trigger_id']:json.loads(r['outcome']) for r in data['isluno_conversation_turns'] if r['outcome']}
    check(len(completed)==4,'completed_count')
    check(all(o['status']=='saved' and o['error'] is None and o['itinerary'] is None for o in completed.values()),'unexpected_booking_or_error')
    current=[r for r in data['inbound_processing_events'] if r['reason']!='history_cleared_no_replay']
    check(len(current)==7 and len(data['inbound_processing_events'])==181,'inbound_shape')
    for row in current:
        check(row['conversation_id']==scope.conversation_id and row['channel']=='whatsapp','inbound_scope')
        check(not row['processing_token'] and not row['lease_expires_at'] and row['attempt_count']==row['provider_retry_count']==0,'execution_or_retry')
        if hashlib.sha256(row['message_id'].encode()).hexdigest() in REJECTED:
            check((row['status'],row['reason'])==('send_failed','provider_send_failed'),'failure_disposition')
            check(hashlib.sha256(row['last_error'].encode()).hexdigest()==FAILURE_ERROR_HASH,'failure_code_changed')
        else:check((row['status'],row['reason']) in {('ignored','no_reply_returned'),('replied','provider_send_ok')},'prior_disposition')
    check(sum(r['status']=='accepted' and bool(r['provider_id']) for r in data['isluno_discovery_plans'])==4,'accepted_plan_count')
    check(sum(r['status']=='rejected' and not r['provider_id'] for r in data['isluno_discovery_plans'])==2,'rejected_plan_count')
    check(all(r['scope_key']==scope.key for r in data['isluno_discovery_plans']),'plan_scope')
    check(all(r['status']=='progress_resumed_new_turn' and r['kind']=='understanding_failure' for r in data['isluno_recovery_incidents']),'historic_incidents_changed')
    # Preserve these old labels as historical records, NOT verified delivery.
    for table in ('isluno_itineraries','isluno_operator_requests','isluno_quote_jobs','isluno_quote_deliveries','isluno_payments','isluno_paid_emails','isluno_reminders','zernio_failed_event_queue'):
        exists=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone()
        check(not exists or db.execute('SELECT count(*) FROM '+table).fetchone()[0]==0,'other_work_or_booking')
    for table in ('pending_notifications','inbound_operator_notifications'):
        data[table]=records(db,table);schema[table]=db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone()[0]
        check(len(data[table])==2,'operator_notice_count')
    for row in data['pending_notifications']:
        check(row['notification_type']=='escalation' and row['mode']=='soft' and row['channel']=='whatsapp' and row['customer_id']==scope.conversation_id and row['status']=='pending','operator_notice_scope_or_status')
        check(row['subject']=='[DELIVERY FAILED] Automated reply needs review','operator_notice_kind')
    check({r['id'] for r in data['pending_notifications']}=={r['notification_id'] for r in data['inbound_operator_notifications']},'operator_notice_links')
    control=db.execute('SELECT ai_muted,ai_mute_source,blocked FROM conversation_status WHERE conversation_id=?',(scope.conversation_id,)).fetchone()
    controls={'row_present':control is not None,'ai_muted':bool(control and control[0]),'ai_mute_source':control[1] if control else '', 'blocked':bool(control and control[2])}
    check(not controls['ai_muted'] and not controls['blocked'],'conversation_paused_or_blocked')
    check(controls['ai_mute_source'] in ('',None,'manual','hard_escalation'),'unexpected_mute_source')
    raw=json.dumps({'schema':schema,'rows':data,'controls':controls},sort_keys=True,separators=(',',':')).encode();check(len(raw)<=4000000,'proof_bytes_bound')
    return {'sha256':hashlib.sha256(raw).hexdigest(),'classification':'seven_retained_turns_four_accepted_two_rejected_plans_no_replay','turns':7,'accepted_plans':4,'rejected_plans':2,'historical_progress_labels_preserved':3,'preserved_operator_notices':2,'controls':controls,'unreviewed_ledgers':UNKNOWN}

def verify(db,expected):
    value=review(db);check(value['sha256']==expected,'stale_history_proof');return value


def seal(path,generation,expected):
    db=sqlite3.connect(path,timeout=0)
    try:
        db.execute('BEGIN IMMEDIATE');s=maintenance._snapshot(db)
        check(s['generation']==generation and s['phase']=='draining','stale_generation')
        check(time.time()<s['deadline'] and s['coverage_complete'],'invalid_drain_window')
        proof=verify(db,expected)
        db.execute("UPDATE mermaid_maintenance SET phase='sealed' WHERE tenant='mermaid' AND generation=? AND phase='draining'",(generation,))
        db.execute('INSERT INTO mermaid_maintenance_audit(generation,action,at) VALUES(?,?,?)',(generation,'seal_communication_history:'+expected,time.time()))
        db.commit();return proof
    except BaseException:db.rollback();raise
    finally:db.close()


def readiness(path,generation,expected):
    db=sqlite3.connect('file:'+path+'?mode=ro',uri=True,timeout=0)
    try:
        db.execute('PRAGMA query_only=ON');db.execute('BEGIN');s=maintenance._snapshot(db)
        check(s['generation']==generation and s['phase']=='sealed' and s['coverage_complete'],'not_reviewed_sealed')
        proof=verify(db,expected)
        check(db.execute('SELECT count(*) FROM mermaid_maintenance_audit WHERE generation=? AND action=?',(generation,'seal_communication_history:'+expected)).fetchone()[0]==1,'seal_audit_missing')
        return proof
    finally:db.close()


def reopen(path,generation,expected):
    db=sqlite3.connect(path,timeout=0)
    try:
        db.execute('BEGIN IMMEDIATE');s=maintenance._snapshot(db)
        check(s['generation']==generation and s['phase']=='sealed' and s['coverage_complete'],'not_reviewed_sealed')
        proof=verify(db,expected)
        check(db.execute('SELECT count(*) FROM mermaid_maintenance_audit WHERE generation=? AND action=?',(generation,'seal_communication_history:'+expected)).fetchone()[0]==1,'seal_audit_missing')
        db.execute("UPDATE mermaid_maintenance SET phase='open',generation=generation+1,coverage='[]',evidence='' WHERE tenant='mermaid'")
        db.execute('INSERT INTO mermaid_maintenance_audit(generation,action,at) VALUES(?,?,?)',(generation,'reopen_communication_history:'+expected,time.time()))
        db.commit();return proof
    except BaseException:db.rollback();raise
    finally:db.close()


def abort_drain(path,generation,packet):
    """Cancel a not-yet-sealed attempt on the verified unchanged running runtime.

    Caller must prove original container/image/profile/client and no activation.
    This is not seal/readiness: workers, unresolved rows and pending IDs stay intact.
    """
    db=sqlite3.connect(path,timeout=0)
    try:
        db.execute('BEGIN IMMEDIATE');s=maintenance._snapshot(db)
        check(s['generation']==generation and s['phase']=='draining','abort_not_unsealed_drain')
        check(s['coverage_complete'],'abort_coverage_missing')
        db.execute("UPDATE mermaid_maintenance SET phase='open',generation=generation+1,coverage='[]',evidence='' WHERE tenant='mermaid'")
        db.execute('INSERT INTO mermaid_maintenance_audit(generation,action,at) VALUES(?,?,?)',(generation,'abort_communication_before_cutover:'+packet,time.time()))
        db.commit()
    except BaseException:db.rollback();raise
    finally:db.close()
