"""One-attempt native sends, persistent ambiguity and recipient pacing."""
from shared.mermaid_maintenance import participating as _maintenance_participating
import hashlib
import json
import os
import time
from datetime import datetime
from urllib.parse import quote

from agents.social.isluno_discovery import DiscoveryStore, dump
from shared.isluno_config import JourneyScope, require_scope
from shared.isluno_pricing import ItineraryError


@_maintenance_participating('transport')
def post_once(scope, body, key, guard=None):
    from agents.social import zernio_dm_client as client
    require_scope(scope)
    from agents.social.isluno_wire import validate_body, response_metadata
    try:validate_body(body)
    except ItineraryError as exc:return {'status':'validation_failed','code':exc.code,'dispatched':False}
    if guard is not None and guard() is not True:
        return {'status': 'blocked'}
    if body.get('accountId') != scope.account_id or not client._provider_mutation_account_allowed(scope.account_id, 'isluno_discovery'):
        return {'status': 'blocked'}
    api_key = os.environ.get('LATE_API_KEY', '')
    if not api_key:
        return {'status': 'blocked'}
    try:
        response = client.http_requests.post('https://zernio.com/api/v1/inbox/conversations/' + quote(scope.conversation_id, safe='') + '/messages',
            headers={'Authorization': 'Bearer ' + api_key, 'Content-Type': 'application/json', 'Idempotency-Key': key}, json=body, timeout=15)
    except client.http_requests.RequestException:
        return {'status': 'ambiguous'}  # No blind retry, even with an idempotency key.
    data = client._response_json(response)
    metadata=response_metadata(response,data)
    metadata['dispatched']=True
    details = data.get('data') if isinstance(data.get('data'), dict) else {}
    provider_id = client._response_message_id(response)
    ids=details.get('messageIds') if isinstance(details.get('messageIds'),list) else []
    metadata['provider_ids']=list(dict.fromkeys(v for v in [provider_id,*ids] if isinstance(v,str) and 0<len(v)<=512))[:16]
    if details.get('partialFailure') or data.get('warnings'):
        return {'status':'ambiguous','provider_id':provider_id,**metadata}
    if 200 <= response.status_code < 300:
        return {'status': 'accepted' if provider_id and data.get('success') is not False else 'ambiguous', 'provider_id': provider_id,**metadata}
    # Documented errors use top-level string code and optional platformError.
    # Neither shape proves a safe no-send media failure: never auto-fallback.
    if response.status_code in {408, 409, 429} or response.status_code >= 500:
        return {'status': 'ambiguous',**metadata}
    return {'status': 'rejected',**metadata}


def _incident(store,scope,plan,status,result):
    from shared import bm_logger
    from agents.social.isluno_conversation import ConversationStore
    from agents.social.isluno_recovery import RecoveryStore
    from agents.social.isluno_itinerary import ItineraryStore
    try:
        conversation=ConversationStore(ItineraryStore(store.db_path,store.catalog_path,clock=store.clock))
        RecoveryStore(conversation).incident(scope,plan.get('source_trigger_id',plan['trigger_id']),'delivery_failure',result.get('code') or status)
    except Exception:
        bm_logger.log('isluno_delivery_visibility_failed',code='incident_store_unavailable')
    bm_logger.log('isluno_delivery_outcome',plan_hash=hashlib.sha256(plan['id'].encode()).hexdigest(),
                  status=status,code=result.get('code'),http_status=result.get('http_status'),platform_code=result.get('platform_code'))


def _hold(store,scope,plan,status,code):
    result={'status':status,'code':code,'dispatched':False}
    with store.db() as db,db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT status,payload FROM isluno_discovery_plans WHERE id=?',(plan['id'],)).fetchone()
        if row['status']!='queued':return False
        saved=json.loads(row['payload']);saved['transport_result']=result
        for part in saved.get('parts',[]):
            if part['status']=='queued':part.update(status=status,result=result);break
        db.execute('UPDATE isluno_discovery_plans SET status=?,payload=? WHERE id=?',(status,dump(saved),plan['id']))
    _incident(store,scope,plan,status,result)
    return False


@_maintenance_participating('delivery')
def send_plan(conversation_id, account_id, plan_id, *, store=None, post=None, window=None, sleep=None):
    from agents.social import zernio_dm_client as client
    from agents.social.isluno_wire import messages, validate_body, text_of
    from agents.social.isluno_hospitality import record_delivery
    store=store or DiscoveryStore();window=window or client.whatsapp_customer_service_window;sleep=sleep or time.sleep
    try:plan,status=store.delivery_plan(plan_id,account_id,conversation_id,allow_expired=True)
    except (ItineraryError,PermissionError):return False
    scope=JourneyScope(**plan['scope'])
    if status=='accepted':return True
    if status!='queued':return False  # No automatic replay of any previously claimed attempt.
    if datetime.fromisoformat(plan['expires_at'])<=store.clock():return _hold(store,scope,plan,'expired','discovery_plan_expired')
    try:
        parts=plan.get('parts') or [{'body':body,'status':'queued','provider_id':None} for body in messages(plan['body'],plan.get('next_question',''))]
        for part in parts:validate_body(part['body'])
    except ItineraryError as exc:return _hold(store,scope,plan,'validation_failed',exc.code)
    try:opened=window(conversation_id,account_id,plan['trigger_sent_at']).get('open') is True
    except Exception:return _hold(store,scope,plan,'blocked','window_check_unavailable')
    if not opened:return _hold(store,scope,plan,'window_closed','customer_window_closed')
    recipient=hashlib.sha256(dump([scope.tenant_slug,scope.account_id,scope.customer_ref]).encode()).hexdigest()
    for index,part in enumerate(parts):
        # Every part has a pre-dispatch durable claim. A process crash remains
        # claimed/unknown; neither a duplicate nor a recovery scan replays it.
        for attempt in range(2):
            require_scope(scope)
            with store.db() as db,db:
                db.execute('BEGIN IMMEDIATE')
                row=db.execute('SELECT status,payload FROM isluno_discovery_plans WHERE id=?',(plan_id,)).fetchone()
                saved=json.loads(row['payload'])
                expected='queued' if index==0 else 'claimed'
                if row['status']!=expected:return row['status']=='accepted'
                stored=saved.get('parts',parts)
                if index and not all(p['status']=='accepted' for p in stored[:index]):return False
                if stored[index]['status']!='queued':return False
                previous=db.execute('SELECT last_send FROM isluno_discovery_pacing WHERE recipient=?',(recipient,)).fetchone()
                now=store.clock().timestamp();wait=max(0,6-(now-previous[0])) if previous else 0
                if wait==0:
                    stored[index]['status']='claimed';stored[index]['attempted_at']=store.clock().isoformat();saved['parts']=stored
                    db.execute("UPDATE isluno_discovery_plans SET status='claimed',payload=? WHERE id=?",(dump(saved),plan_id))
                    db.execute('INSERT INTO isluno_discovery_pacing VALUES(?,?) ON CONFLICT(recipient) DO UPDATE SET last_send=excluded.last_send',(recipient,now))
                    break
            if attempt==1:
                if index==0:return _hold(store,scope,plan,'pacing_deferred','recipient_pacing_not_elapsed')
                result={'status':'pacing_deferred','code':'recipient_pacing_not_elapsed','dispatched':False}
                break
            sleep(min(wait,6))
        else:return False
        if wait==0:
            body=part['body']
            def guard():
                current,current_status=store.delivery_plan(plan_id,account_id,conversation_id)
                item=current['parts'][index]
                return current_status=='claimed' and item['status']=='claimed' and item['body']==body
            try:
                if window(conversation_id,account_id,plan['trigger_sent_at']).get('open') is not True:
                    result={'status':'window_closed','code':'customer_window_closed','dispatched':False}
                else:
                    key='isluno-discovery-'+plan_id+('' if len(parts)==1 else '-part-'+str(index))
                    result=(post(scope,body,key) if post else post_once(scope,body,key,guard=guard))
            except Exception as exc:
                result={'status':'ambiguous','code':'transport_exception','exception_type':type(exc).__name__}
        if result.get('status')=='accepted' and not result.get('provider_id'):result={**result,'status':'ambiguous','code':'provider_id_missing'}
        outcome=result.get('status','ambiguous')
        with store.db() as db,db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT payload FROM isluno_discovery_plans WHERE id=?',(plan_id,)).fetchone();saved=json.loads(row['payload'])
            item=saved['parts'][index];item.update(status=outcome,provider_id=result.get('provider_id'),result=result)
            aggregate='accepted' if all(p['status']=='accepted' for p in saved['parts']) else 'claimed' if outcome=='accepted' else outcome
            saved['transport_result']=result
            db.execute('UPDATE isluno_discovery_plans SET status=?,provider_id=?,payload=? WHERE id=?',(aggregate,result.get('provider_id'),dump(saved),plan_id))
            if result.get('dispatched') is not False:
                delivery_id='discovery:'+plan_id+('' if len(parts)==1 else ':part:'+str(index))
                media=bool(part['body'].get('attachmentUrl') or part['body'].get('interactive'))
                record_delivery(db,scope,delivery_id,part['body'],outcome,
                    assets=part.get('assets',[]) if plan.get('visual_cards') else [{'product_ids':plan['product_ids'],'asset_id':asset} for asset in plan['asset_ids']] if media else [],
                    buttons=part.get('button_meanings',{}) if plan.get('visual_cards') else plan.get('button_meanings',{}) if index==len(parts)-1 else {},
                    question=part.get('next_question','') if plan.get('visual_cards') and aggregate=='accepted' else plan.get('next_question','') if aggregate=='accepted' else '')
            from agents.social.isluno_callbacks import reconcile
            reconcile(db,scope)
            actual=db.execute('SELECT status FROM isluno_discovery_plans WHERE id=?',(plan_id,)).fetchone()[0]
            if actual=='provider_failed':outcome='provider_failed'
        if outcome!='accepted':
            _incident(store,scope,plan,outcome,result);return False
    return True
