"""Operator-only proof for the owner's approved, preserving-history welcome release.

Does not alter runtime guards, customer records or delivery dispositions.
Only the observed single completed browsing turn and fully accepted plan qualify.
"""
import hashlib
import json
import re
import sqlite3
import sys
import time

from shared import mermaid_maintenance as maintenance
from shared.isluno_config import JourneyScope, require_scope

DB = '/app/data/state_registry.db'
UNKNOWN = ['isluno_conversation_turns', 'isluno_discovery_plans']


def check(value, code):
    if not value:
        raise ValueError(code)


def rows(db, table):
    cursor = db.execute('SELECT * FROM "' + table + '" ORDER BY rowid LIMIT 1001')
    result = [dict(zip([d[0] for d in cursor.description], row)) for row in cursor]
    check(len(result) <= 1000, 'history_bound')
    return result


def fingerprint(db):
    result = {}
    deadline = time.monotonic() + 8
    total = 0
    for name, schema in db.execute("SELECT name,sql FROM sqlite_master WHERE type='table' ORDER BY name").fetchall():
        if name.startswith('mermaid_maintenance') or name == 'sqlite_sequence':
            continue
        check(re.fullmatch('[a-z_]+', name), 'table_name')
        digest = hashlib.sha256(schema.encode())
        count = 0
        for row in db.execute('SELECT * FROM "' + name + '" ORDER BY rowid'):
            encoded = repr(row).encode()
            total += len(encoded)
            check(total < 100000000 and time.monotonic() < deadline, 'fingerprint_bound')
            digest.update(encoded + b'\n')
            count += 1
        result[name] = {'count': count, 'sha256': digest.hexdigest()}
    return {'sha256': hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest(),
            'tables': len(result)}


def review(db):
    state = maintenance._snapshot(db)
    check(not state['workers'] and not state['pending'] and not state['incidents'], 'work_not_drained')
    check(state['unreviewed_ledgers'] == UNKNOWN, 'unclassified_ledger')
    check(db.execute('SELECT tenant FROM isluno_cutover').fetchall() == [('mermaid',)], 'tenant')
    turns = rows(db, 'isluno_conversation_turns')
    plans = rows(db, 'isluno_discovery_plans')
    sessions = rows(db, 'isluno_booking_sessions')
    check(len(turns) == len(plans) == len(sessions) == 1, 'history_shape_changed')
    turn, plan_row, session_row = turns[0], plans[0], sessions[0]
    check(turn['decision'] is not None and turn['outcome'] is not None and turn['claimed_at'], 'unfinished_turn')
    decision, outcome = json.loads(turn['decision']), json.loads(turn['outcome'])
    check(decision['booking']['action'] == 'none' and not decision['booking']['updates'], 'not_browsing')
    check(outcome['status'] == 'saved' and outcome['error'] is None and outcome['itinerary'] is None, 'unresolved_outcome')
    plan = json.loads(plan_row['payload'])
    scope = JourneyScope(**plan['scope'])
    require_scope(scope)
    check(turn['scope_key'] == plan_row['scope_key'] == session_row['scope_key'] == scope.key, 'scope_mismatch')
    session = json.loads(session_row['payload'])
    check(session['active_itinerary_id'] is None and not session['pending'] and not session['item_details'], 'booking_present')
    check(plan_row['status'] == 'accepted' and plan_row['provider_id'], 'plan_not_accepted')
    check(plan_row['id'] == plan['id'] and plan_row['trigger_id'] == plan['trigger_id'], 'plan_identity')
    from agents.social.isluno_conversation import opaque
    check(plan_row['trigger_id'] == 'conversation-' + opaque(scope, turn['trigger_id'], 'reply'), 'plan_turn_link')
    check(plan['selected_intent'] is None and not plan['requires_human'], 'unexpected_action')
    check(plan.get('parts') and all(p['status'] == 'accepted' and p.get('provider_id')
          and p.get('result', {}).get('status') == 'accepted'
          and not p.get('result', {}).get('partial_failure') for p in plan['parts']), 'unfinished_transport')
    current = [r for r in rows(db, 'inbound_processing_events') if r['reason'] != 'history_cleared_no_replay']
    check(len(current) == 1, 'new_inbound')
    inbound = current[0]
    check(inbound['message_id'] == turn['trigger_id'] and inbound['conversation_id'] == scope.conversation_id
          and inbound['channel'] == 'whatsapp' and inbound['status'] == 'replied'
          and inbound['reason'] == 'provider_send_ok', 'inbound_not_terminal')
    check(all(not inbound[k] for k in ('processing_token', 'lease_expires_at', 'last_error')), 'inbound_uncertain')
    check(db.execute('SELECT count(*) FROM whatsapp_processed WHERE message_id=?', (turn['trigger_id'],)).fetchone()[0] == 1, 'dedup_missing')
    return fingerprint(db)


def operate(action, generation=None, expected=None, path=DB):
    read_only = action in {'review', 'ready'}
    db = sqlite3.connect('file:' + path + ('?mode=ro' if read_only else '?mode=rw'), uri=True, timeout=0)
    try:
        db.execute('BEGIN' if read_only else 'BEGIN IMMEDIATE')
        state = maintenance._snapshot(db)
        if action == 'abort':
            check(state['generation'] == generation and state['phase'] == 'draining', 'cannot_abort')
            # No activation has occurred; unfinished work is retained, not relabelled.
            db.execute("UPDATE mermaid_maintenance SET phase='open',generation=generation+1,coverage='[]',evidence='' WHERE tenant='mermaid'")
            proof = {'aborted': True}
        else:
            proof = review(db)
            if expected:
                check(proof['sha256'] == expected, 'customer_history_changed')
            if action != 'review':
                check(state['generation'] == generation and state['coverage_complete'], 'stale_generation')
                check(time.time() < state['deadline'], 'drain_deadline')
                check(state['phase'] == ('draining' if action == 'seal' else 'sealed'), 'invalid_phase')
            if action == 'seal':
                db.execute("UPDATE mermaid_maintenance SET phase='sealed' WHERE tenant='mermaid'")
            if action in {'ready', 'reopen'}:
                check(db.execute('SELECT count(*) FROM mermaid_maintenance_audit WHERE generation=? AND action=?',
                      (generation, 'sunny_seal:' + expected)).fetchone()[0] == 1, 'seal_audit_missing')
            if action == 'reopen':
                db.execute("UPDATE mermaid_maintenance SET phase='open',generation=generation+1,coverage='[]',evidence='' WHERE tenant='mermaid'")
        if not read_only:
            db.execute('INSERT INTO mermaid_maintenance_audit(generation,action,at) VALUES(?,?,?)',
                       (generation, 'sunny_' + action + ':' + (expected or ''), time.time()))
            db.commit()
        return proof
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == '__main__':
    print(json.dumps(operate(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else None,
                             sys.argv[3] if len(sys.argv) > 3 else None)))
