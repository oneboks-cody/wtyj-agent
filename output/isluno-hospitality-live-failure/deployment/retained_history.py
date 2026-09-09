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


FAILURE_HASHES={'bdb3b701f4058baa1e5d6f86d649ffd7845c6d8ee92110f85a2337216be0b5a7','8b36ea712de281e8b4e02a7d840c69aaee3f8911766412005cccdff0b921957b'}


def review(db):
    state=maintenance._snapshot(db)
    check(not state['workers'] and not state['pending'],'work_not_drained')
    check(state['unreviewed_ledgers']==UNKNOWN,'unclassified_ledger')
    check(db.execute('SELECT tenant FROM isluno_cutover').fetchall()==[('mermaid',)],'cutover_identity')
    data={table:records(db,table) for table in TABLES}
    turns=data['isluno_conversation_turns'];contacts=data['isluno_recovery_contacts']
    check(len(turns)==4 and len(contacts)==1,'different_history_shape')
    check(len(data['isluno_recovery_incidents'])==len(data['isluno_discovery_plans'])==3,'different_history_shape')
    check(len(data['isluno_booking_sessions'])==len(data['isluno_discovery_latest'])==1,'different_history_shape')
    scope=JourneyScope(**json.loads(contacts[0]['scope_json']));require_scope(scope)
    check(all(t['scope_key']==scope.key and bool(t['claimed_at']) for t in turns),'turn_scope')
    failed={t['trigger_id']:t for t in turns if t['decision'] is None and t['outcome'] is None}
    complete=[t for t in turns if t['decision'] is not None and t['outcome'] is not None]
    check(len(failed)==3 and len(complete)==1,'unfinished_or_extra_turn')
    decision=json.loads(complete[0]['decision']);outcome=json.loads(complete[0]['outcome'])
    check(decision['booking']['action']=='none' and not decision['booking']['updates'],'not_browsing_only')
    check(outcome['status']=='saved' and outcome['error'] is None and outcome['itinerary'] is None,'unresolved_turn_result')
    new_failures=set();old_failures=set()
    for incident in data['isluno_recovery_incidents']:
        trigger=incident['trigger_id']
        check(incident['scope_key']==scope.key and trigger in failed and incident['kind']=='understanding_failure' and incident['itinerary_id'] is None,'incident_scope')
        if incident['code']=='invalid_reply_style':
            check(incident['status']=='operator_review','failure_disposition_changed')
            new_failures.add(trigger)
        else:
            check(incident['code']=='ItineraryError' and incident['status']=='progress_resumed_new_turn','unreviewed_incident')
            old_failures.add(trigger)
    check(len(old_failures)==1 and len(new_failures)==2,'different_incidents')
    check({hashlib.sha256(t.encode()).hexdigest() for t in new_failures}==FAILURE_HASHES,'unreviewed_failure_trigger')
    session_row=data['isluno_booking_sessions'][0];check(session_row['scope_key']==scope.key,'session_scope')
    session=json.loads(session_row['payload'])
    check(session['active_itinerary_id'] is None and not session['pending'] and not session['item_details'],'unexpected_booking')
    check(session['revision']==outcome['session']['revision']==1,'unexpected_revision')
    current=[r for r in data['inbound_processing_events'] if r['reason']!='history_cleared_no_replay']
    check(len(current)==4 and {r['message_id'] for r in current}=={t['trigger_id'] for t in turns},'inbound_link')
    for row in current:
        check(row['conversation_id']==scope.conversation_id and row['channel']=='whatsapp','inbound_scope')
        for key in ('last_error','processing_token','lease_expires_at','heartbeat_sent_at'):
            check(row[key]=='','inbound_execution_or_uncertainty')
        check(row['provider_retry_count']==row['attempt_count']==0 and row['outbound_attempted_at']==row['outbound_idempotency_key']=='','unexpected_generic_attempt')
        expected=('ignored','no_reply_returned') if row['message_id'] in old_failures else ('replied','provider_send_ok')
        check((row['status'],row['reason'])==expected,'inbound_disposition')
        check(db.execute('SELECT count(*) FROM whatsapp_processed WHERE message_id=?',(row['message_id'],)).fetchone()[0]==1,'dedup_missing')
        check(db.execute('SELECT count(*) FROM mermaid_maintenance_pending WHERE message_id=?',(row['message_id'],)).fetchone()[0]==0,'turn_still_pending')
    from agents.social.isluno_conversation import opaque
    from agents.social.isluno_recovery_copy import PROCESSING_FAILED
    reply_trigger='conversation-'+opaque(scope,complete[0]['trigger_id'],'reply')
    failure_triggers={'failure-'+opaque(scope,t,'ack') for t in new_failures}
    check({p['trigger_id'] for p in data['isluno_discovery_plans']}=={reply_trigger,*failure_triggers},'plan_trigger_link')
    plan_ids=set();failure_plan_ids=set()
    for plan_row in data['isluno_discovery_plans']:
        plan=json.loads(plan_row['payload']);plan_ids.add(plan_row['id'])
        check(plan_row['id']==plan['id'] and plan_row['trigger_id']==plan['trigger_id'],'plan_identity')
        check(plan_row['scope_key']==scope.key and plan['scope']==scope.__dict__,'plan_scope')
        check(plan_row['status']=='accepted' and bool(plan_row['provider_id']),'plan_not_accepted')
        check(plan['selected_intent'] is None and not plan['requires_human'],'unexpected_plan_action')
        if plan_row['trigger_id'] in failure_triggers:
            failure_plan_ids.add(plan_row['id'])
            body=plan['body'];text=body.get('message') or body.get('interactive',{}).get('body',{}).get('text')
            check(text==PROCESSING_FAILED[session['chat_language']] and not plan['product_ids'] and not plan['fact_keys'],'unexpected_failure_ack')
    latest=data['isluno_discovery_latest'][0]
    check(latest['scope_key']==scope.key and latest['plan_id'] in failure_plan_ids,'latest_plan_binding')
    check(all(a['plan_id'] in plan_ids for a in data['isluno_discovery_actions']),'unbound_native_action')
    check(len(data['isluno_recovery_inbound'])==4 and all(r['scope_key']==scope.key for r in data['isluno_recovery_inbound']),'recovery_link')
    for table in ('isluno_itineraries','isluno_operator_requests','isluno_quote_jobs','isluno_quote_deliveries','isluno_payments','isluno_paid_emails','isluno_reminders','zernio_failed_event_queue'):
        exists=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone()
        check(not exists or db.execute('SELECT count(*) FROM '+table).fetchone()[0]==0,'other_work_or_booking')
    schema={table:db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone()[0] for table in TABLES}
    raw=json.dumps({'schema':schema,'rows':data},sort_keys=True,separators=(',',':')).encode();check(len(raw)<=4000000,'proof_bytes_bound')
    return {'sha256':hashlib.sha256(raw).hexdigest(),'classification':'old_failed_claim_completed_browsing_and_two_failed_style_turns_with_accepted_acknowledgements','turns':4,'accepted_plans':3,'preserved_operator_review_incidents':2,'unreviewed_ledgers':UNKNOWN}


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
        db.execute('INSERT INTO mermaid_maintenance_audit(generation,action,at) VALUES(?,?,?)',(generation,'seal_presentation_history:'+expected,time.time()))
        db.commit();return proof
    except BaseException:db.rollback();raise
    finally:db.close()


def readiness(path,generation,expected):
    db=sqlite3.connect('file:'+path+'?mode=ro',uri=True,timeout=0)
    try:
        db.execute('PRAGMA query_only=ON');db.execute('BEGIN');s=maintenance._snapshot(db)
        check(s['generation']==generation and s['phase']=='sealed' and s['coverage_complete'],'not_reviewed_sealed')
        proof=verify(db,expected)
        check(db.execute('SELECT count(*) FROM mermaid_maintenance_audit WHERE generation=? AND action=?',(generation,'seal_presentation_history:'+expected)).fetchone()[0]==1,'seal_audit_missing')
        return proof
    finally:db.close()


def reopen(path,generation,expected):
    db=sqlite3.connect(path,timeout=0)
    try:
        db.execute('BEGIN IMMEDIATE');s=maintenance._snapshot(db)
        check(s['generation']==generation and s['phase']=='sealed' and s['coverage_complete'],'not_reviewed_sealed')
        proof=verify(db,expected)
        check(db.execute('SELECT count(*) FROM mermaid_maintenance_audit WHERE generation=? AND action=?',(generation,'seal_presentation_history:'+expected)).fetchone()[0]==1,'seal_audit_missing')
        db.execute("UPDATE mermaid_maintenance SET phase='open',generation=generation+1,coverage='[]',evidence='' WHERE tenant='mermaid'")
        db.execute('INSERT INTO mermaid_maintenance_audit(generation,action,at) VALUES(?,?,?)',(generation,'reopen_presentation_history:'+expected,time.time()))
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
        db.execute('INSERT INTO mermaid_maintenance_audit(generation,action,at) VALUES(?,?,?)',(generation,'abort_presentation_before_cutover:'+packet,time.time()))
        db.commit()
    except BaseException:db.rollback();raise
    finally:db.close()
