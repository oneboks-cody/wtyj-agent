"""Operator-only proof for one retained failed turn; never changes customer rows.

This adds a narrow atomic image-upgrade seal. It does not change the runtime
snapshot, normal seal, replay guards, or classify the failed turn as completed.
"""
import hashlib,json,sqlite3,time
from shared import mermaid_maintenance as maintenance
from shared.isluno_config import JourneyScope,require_scope

TABLES=('inbound_processing_events','isluno_conversation_turns','isluno_recovery_incidents','isluno_recovery_contacts')
UNKNOWN=['isluno_conversation_turns','isluno_recovery_incidents']


def check(value,code):
    if not value:raise ValueError(code)


def records(db,table):
    cursor=db.execute('SELECT * FROM '+table+' ORDER BY rowid LIMIT 1001')
    rows=[dict(zip([d[0] for d in cursor.description],row)) for row in cursor]
    check(len(rows)<=1000,'proof_row_bound')
    return rows


def review(db,expected_message_hash):
    snapshot=maintenance._snapshot(db)
    check(not snapshot['workers'] and not snapshot['pending'],'work_not_drained')
    check(snapshot['unreviewed_ledgers']==UNKNOWN,'other_unreviewed_ledgers')
    check(db.execute("SELECT tenant FROM isluno_cutover").fetchall()==[('mermaid',)],'cutover_marker')
    data={table:records(db,table) for table in TABLES}
    check(len(data['isluno_conversation_turns'])==len(data['isluno_recovery_incidents'])==len(data['isluno_recovery_contacts'])==1,'extra_or_missing_rows')
    turn=data['isluno_conversation_turns'][0];incident=data['isluno_recovery_incidents'][0];contact=data['isluno_recovery_contacts'][0]
    check(turn['decision'] is None and turn['outcome'] is None and bool(turn['claimed_at']),'not_failed_claim')
    check(incident['kind']=='understanding_failure' and incident['status']=='operator_review' and incident['code']=='ItineraryError','incident_not_proven')
    check(turn['scope_key']==incident['scope_key']==contact['scope_key'] and turn['trigger_id']==incident['trigger_id'],'scope_or_trigger_mismatch')
    check(incident['itinerary_id'] is None,'unexpected_itinerary')
    scope=JourneyScope(**json.loads(contact['scope_json']));require_scope(scope)
    check(scope.key==turn['scope_key'],'invalid_scope_key')
    check(hashlib.sha256(turn['trigger_id'].encode()).hexdigest()==expected_message_hash,'different_test_turn')
    inbound=[r for r in data['inbound_processing_events'] if r['reason']!='history_cleared_no_replay']
    check(len(inbound)==1,'extra_inbound')
    row=inbound[0]
    check(row['message_id']==turn['trigger_id'] and row['conversation_id']==scope.conversation_id and row['channel']=='whatsapp','inbound_link')
    check((row['status'],row['reason'])==('ignored','no_reply_returned'),'inbound_not_terminal_failed')
    for key in ('last_error','processing_token','lease_expires_at','outbound_attempted_at','heartbeat_sent_at'):
        check(row[key]=='','inbound_execution_or_uncertainty')
    check(row['attempt_count']==0 and row['provider_retry_count']==0,'attempts_present')
    check(db.execute('SELECT count(*) FROM whatsapp_processed WHERE message_id=?',(row['message_id'],)).fetchone()[0]==1,'dedup_missing')
    check(db.execute('SELECT count(*) FROM mermaid_maintenance_pending WHERE message_id=?',(row['message_id'],)).fetchone()[0]==0,'failed_turn_pending')
    # No current delivery plan/action can execute independently of this failed claim.
    for table in ('isluno_discovery_plans','isluno_discovery_actions','isluno_quote_jobs','isluno_quote_deliveries','isluno_paid_emails','isluno_reminders','zernio_failed_event_queue'):
        exists=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone()
        check(not exists or db.execute('SELECT count(*) FROM '+table).fetchone()[0]==0,'other_dispatchable_work')
    schema={table:db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone()[0] for table in TABLES}
    payload=json.dumps({'schema':schema,'rows':data},sort_keys=True,separators=(',',':')).encode()
    check(len(payload)<=4000000,'proof_bytes_bound')
    return {'sha256':hashlib.sha256(payload).hexdigest(),'classification':'retained_operator_review_not_dispatchable','turns':1,'incidents':1,'ordinary_unreviewed_ledgers':UNKNOWN}


def verify(db,expected_message_hash,proof_hash):
    proof=review(db,expected_message_hash)
    check(proof['sha256']==proof_hash,'stale_retained_proof')
    return proof


def seal(db_path,generation,expected_message_hash,proof_hash):
    db=sqlite3.connect(db_path,timeout=0)
    try:
        db.execute('BEGIN IMMEDIATE')
        s=maintenance._snapshot(db)
        check(s['generation']==generation and s['phase']=='draining','stale_generation')
        check(time.time()<s['deadline'] and s['coverage_complete'],'invalid_drain_window')
        proof=verify(db,expected_message_hash,proof_hash)
        # All ordinary seal conditions passed except the two explicit retained
        # review tables, proven above under the same SQLite write transaction.
        db.execute("UPDATE mermaid_maintenance SET phase='sealed' WHERE tenant='mermaid' AND generation=? AND phase='draining'",(generation,))
        db.execute('INSERT INTO mermaid_maintenance_audit(generation,action,at) VALUES(?,?,?)',
                   (generation,'seal_retained_operator_review:'+proof_hash,time.time()))
        db.commit();return proof
    except BaseException:
        db.rollback();raise
    finally:db.close()


def readiness(db_path,generation,expected_message_hash,proof_hash):
    db=sqlite3.connect('file:'+db_path+'?mode=ro',uri=True,timeout=0)
    try:
        db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
        s=maintenance._snapshot(db)
        check(s['generation']==generation and s['phase']=='sealed' and s['coverage_complete'],'not_reviewed_sealed')
        proof=verify(db,expected_message_hash,proof_hash)
        check(db.execute('SELECT count(*) FROM mermaid_maintenance_audit WHERE generation=? AND action=?',
              (generation,'seal_retained_operator_review:'+proof_hash)).fetchone()[0]==1,'seal_audit_missing')
        return proof
    finally:db.close()


def reopen(db_path,generation,expected_message_hash,proof_hash):
    db=sqlite3.connect(db_path,timeout=0)
    try:
        db.execute('BEGIN IMMEDIATE')
        s=maintenance._snapshot(db)
        check(s['generation']==generation and s['phase']=='sealed' and s['coverage_complete'],'not_reviewed_sealed')
        proof=verify(db,expected_message_hash,proof_hash)
        check(db.execute('SELECT count(*) FROM mermaid_maintenance_audit WHERE generation=? AND action=?',
              (generation,'seal_retained_operator_review:'+proof_hash)).fetchone()[0]==1,'seal_audit_missing')
        db.execute("UPDATE mermaid_maintenance SET phase='open',generation=generation+1,coverage='[]',evidence='' WHERE tenant='mermaid'")
        db.execute('INSERT INTO mermaid_maintenance_audit(generation,action,at) VALUES(?,?,?)',
                   (generation,'reopen_retained_operator_review:'+proof_hash,time.time()))
        db.commit();return proof
    except BaseException:
        db.rollback();raise
    finally:db.close()
