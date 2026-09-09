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


EMPTY_TABLES=['conversation_status', 'customer_identifiers', 'customer_interactions', 'customer_merges', 'customers', 'inbound_operator_notifications', 'isluno_booking_sessions', 'isluno_conversation_turns', 'isluno_discovery_actions', 'isluno_discovery_latest', 'isluno_discovery_pacing', 'isluno_discovery_plans', 'isluno_email_consents', 'isluno_fulfillment_events', 'isluno_itineraries', 'isluno_itinerary_requests', 'isluno_itinerary_versions', 'isluno_operator_requests', 'isluno_paid_documents', 'isluno_paid_emails', 'isluno_payment_actions', 'isluno_payment_events', 'isluno_payments', 'isluno_quote_actions', 'isluno_quote_deliveries', 'isluno_quote_jobs', 'isluno_quote_latest', 'isluno_quote_requests', 'isluno_quote_state', 'isluno_quote_versions', 'isluno_recovery_contacts', 'isluno_recovery_inbound', 'isluno_recovery_incidents', 'isluno_reminders', 'pending_notifications', 'whatsapp_booking_state', 'whatsapp_threads', 'zernio_failed_event_queue']
def review(db):
 state=maintenance._snapshot(db)
 check(not state['workers'] and not state['pending'] and not state['unreviewed_ledgers'],'work_not_drained')
 check(db.execute('SELECT tenant FROM isluno_cutover').fetchall()==[('mermaid',)],'cutover_identity')
 for table in EMPTY_TABLES:
  check(db.execute('SELECT count(*) FROM '+table).fetchone()[0]==0,'new_customer_state')
 data={table:records(db,table) for table in TABLES}
 check(len(data['inbound_processing_events'])==182,'tombstone_count')
 for row in data['inbound_processing_events']:
  check(row['status']=='ignored' and row['reason']=='history_cleared_no_replay' and row['payload_json']=='{}' and not row['conversation_id'] and not row['processing_token'],'tombstone_payload_or_status')
 schema={table:db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone()[0] for table in TABLES}
 raw=json.dumps({'schema':schema,'rows':data},sort_keys=True,separators=(',',':')).encode()
 return {'sha256':hashlib.sha256(raw).hexdigest(),'classification':'empty_demo_context_182_no_replay_tombstones','empty_tables':len(EMPTY_TABLES),'turns':0,'unreviewed_ledgers':[]}
def verify(db,expected):
 value=review(db);check(value['sha256']==expected,'stale_history_proof');return value
def proof_at(path,expected):
 db=sqlite3.connect('file:'+path+'?mode=ro',uri=True,timeout=0)
 try:
  db.execute('PRAGMA query_only=ON');db.execute('BEGIN');return verify(db,expected)
 finally:db.close()
def seal(path,generation,expected):
 proof_at(path,expected);maintenance.seal(generation,db_path=path);return readiness(path,generation,expected)
def readiness(path,generation,expected):
 s=maintenance.status(path);check(s['phase']=='sealed' and s['generation']==generation and s['coverage_complete'],'not_sealed')
 return proof_at(path,expected)
def reopen(path,generation,expected):
 readiness(path,generation,expected);maintenance.reopen(generation,db_path=path)

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
