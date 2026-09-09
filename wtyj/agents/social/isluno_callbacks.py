"""Signed provider status evidence, correlated exactly; never sends or retries."""
import hashlib
import json
from shared.isluno_config import JourneyScope,require_scope
from shared.mermaid_maintenance import participating


def delivery_status(previous,new):
    rank={None:0,'delivered':1,'read':2,'failed':3}
    return new if rank.get(new,0)>rank.get(previous,0) else previous


def reconcile(db,scope):
    """Run in the sender/callback transaction to cover callback-before-response races."""
    require_scope(scope)
    tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'isluno_booking_sessions' not in tables:return
    row=db.execute('SELECT payload FROM isluno_booking_sessions WHERE scope_key=?',(scope.key,)).fetchone()
    if not row:return
    session=json.loads(row[0]);events=session.get('provider_events',[])
    if not events:return
    targets=[]
    if 'isluno_discovery_plans' in tables:
        for row in db.execute('SELECT id,payload,provider_id,status FROM isluno_discovery_plans WHERE scope_key=?',(scope.key,)):
            payload=json.loads(row['payload']);parts=payload.get('parts') or [{'provider_id':row['provider_id'],'status':row['status'],'body':payload['body']}]
            for index,part in enumerate(parts):
                ids=set(part.get('result',{}).get('provider_ids',[]))|{part.get('provider_id')}
                targets.append(('discovery',row['id'],index,payload,parts,ids-{None,''}))
    if 'isluno_quote_jobs' in tables:
        for row in db.execute('SELECT id,payload FROM isluno_quote_jobs WHERE scope_key=?',(scope.key,)):
            payload=json.loads(row['payload'])
            for delivery in db.execute('SELECT part,provider_id,status FROM isluno_quote_deliveries WHERE job_id=?',(row['id'],)):
                index=delivery['part'];result=payload.get('transport_results',{}).get(str(index),{})
                ids=set(result.get('provider_ids',[]))|{delivery['provider_id']}
                targets.append(('quote',row['id'],index,payload,None,ids-{None,''}))
    for event in events:
        if event.get('matched'):continue
        matches=[target for target in targets if set(event['provider_ids']) & target[-1]]
        if len(matches)!=1:continue
        kind,identifier,index,payload,parts,ids=matches[0]
        # Every explicit ID on a callback must belong to the same wire part.
        if not set(event['provider_ids'])<=ids:continue
        event['matched']={'kind':kind,'id':identifier,'part':index}
        failed=event['status']=='failed'
        if kind=='discovery':
            part=parts[index];part['provider_delivery_status']=delivery_status(part.get('provider_delivery_status'),event['status'])
            if failed:part['status']='provider_failed'
            payload['parts']=parts
            if failed:payload['transport_result']={'status':'provider_failed','code':'provider_delivery_failed','provider_ids':event['provider_ids']}
            db.execute('UPDATE isluno_discovery_plans SET payload=?,status=CASE WHEN ? THEN ? ELSE status END WHERE id=?',
                (json.dumps(payload,ensure_ascii=False),failed,'provider_failed',identifier))
            delivery_id='discovery:'+identifier+('' if len(parts)==1 else ':part:'+str(index))
        else:
            result=payload.setdefault('transport_results',{}).setdefault(str(index),{})
            result['provider_delivery_status']=delivery_status(result.get('provider_delivery_status'),event['status'])
            if failed:
                result['status']='provider_failed'
                db.execute("UPDATE isluno_quote_deliveries SET status='provider_failed' WHERE job_id=? AND part=?",(identifier,index))
            db.execute('UPDATE isluno_quote_jobs SET payload=? WHERE id=?',(json.dumps(payload,ensure_ascii=False),identifier))
            delivery_id='quote:'+identifier+':'+str(index)
        for history in session.get('history',[]):
            if history.get('delivery_id')==delivery_id:
                history['provider_delivery_status']=delivery_status(history.get('provider_delivery_status'),event['status'])
                if failed:history['delivery_status']='provider_failed'
        if failed:
            session.pop('communication_progress',None)
            session['last_accepted_question']=next((h['question'] for h in reversed(session.get('history',[])) if h.get('delivery_status')=='accepted' and h.get('question')),'')
            if 'isluno_recovery_incidents' in tables:
                incident=hashlib.sha256((scope.key+event['key']).encode()).hexdigest()
                db.execute('INSERT OR IGNORE INTO isluno_recovery_incidents VALUES(?,?,?,?,?,?,?,?)',
                    (incident,scope.key,session.get('active_itinerary_id'),payload.get('source_trigger_id',payload.get('trigger_id',identifier)),
                     'delivery_failure','operator_review','provider_delivery_failed',event['received_at']))
    db.execute('UPDATE isluno_booking_sessions SET payload=? WHERE scope_key=?',(json.dumps(session,ensure_ascii=False),scope.key))


@participating('recovery')
def accept(payload,*,store=None):
    from agents.social.isluno_conversation import ConversationStore
    from agents.social.isluno_recovery import RecoveryStore
    from agents.social.zernio_dm_client import parse_zernio_failed_webhook
    from shared.tenant_guard import account_access_state
    from shared import bm_logger
    event=payload.get('event')
    if event not in {'message.delivered','message.read','message.failed'}:return False
    normalized=parse_zernio_failed_webhook({**payload,'event':'message.failed'})
    if not normalized:return False
    account=normalized['account_id'];conversation=normalized['conversation_id']
    access=account_access_state(account,direction='inbound')
    if access is None:raise RuntimeError('callback_account_unavailable')
    if access is not True:return False
    store=store or ConversationStore();RecoveryStore(store).initialize()
    with store.db() as db,db:
        db.execute('BEGIN IMMEDIATE')
        rows=db.execute("SELECT scope_json FROM isluno_recovery_contacts WHERE json_extract(scope_json,'$.account_id')=? AND json_extract(scope_json,'$.conversation_id')=?",(account,conversation)).fetchall()
        if len(rows)!=1:
            bm_logger.log('isluno_callback_unmatched',code='unknown_conversation')
            return False
        scope=JourneyScope(**json.loads(rows[0][0]));require_scope(scope)
        row=db.execute('SELECT payload FROM isluno_booking_sessions WHERE scope_key=?',(scope.key,)).fetchone()
        if not row:return False
        session=json.loads(row[0]);events=session.setdefault('provider_events',[])
        provider_id=normalized['message_id']
        if not isinstance(provider_id,str) or not 0<len(provider_id)<=512:return False
        key=hashlib.sha256(json.dumps([account,conversation,event,provider_id]).encode()).hexdigest()
        if not any(e['key']==key for e in events):
            # Retain unmatched evidence rather than evicting uncertain events.
            if len(events)>=1000:raise RuntimeError('callback_ledger_full')
            events.append({'key':key,'status':event.split('.')[1],'provider_ids':[provider_id],'received_at':store.itinerary.clock().isoformat()})
            db.execute('UPDATE isluno_booking_sessions SET payload=? WHERE scope_key=?',(json.dumps(session,ensure_ascii=False),scope.key))
        reconcile(db,scope)
    return True
