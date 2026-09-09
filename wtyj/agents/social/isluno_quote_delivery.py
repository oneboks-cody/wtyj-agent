"""Bounded, resumable multipart quote dispatch with persisted per-part ambiguity."""
from shared.mermaid_maintenance import participating as _maintenance_participating
import copy
import hashlib
import json
import time

from agents.social.isluno_quotes import QuoteStore
from agents.social.isluno_delivery import post_once
from agents.social.isluno_discovery import dump
from shared.isluno_config import JourneyScope
from shared.isluno_pricing import ItineraryError


def _save(store,scope,job,index,result,*,body=None,document_id=None,expected=None):
    status=result.get('status','ambiguous')
    if status=='accepted' and not result.get('provider_id'):
        status='ambiguous';result={**result,'status':status,'code':'provider_id_missing'}
    with store.db() as db,db:
        db.execute('BEGIN IMMEDIATE')
        current=db.execute('SELECT status FROM isluno_quote_deliveries WHERE job_id=? AND part=?',(job['id'],index)).fetchone()[0]
        expected=expected or ('queued' if body is None else 'claimed')
        if current!=expected:return current
        payload=json.loads(db.execute('SELECT payload FROM isluno_quote_jobs WHERE id=?',(job['id'],)).fetchone()[0])
        payload.setdefault('transport_results',{})[str(index)]=result
        db.execute('UPDATE isluno_quote_jobs SET payload=? WHERE id=?',(dump(payload),job['id']))
        db.execute('UPDATE isluno_quote_deliveries SET status=?,provider_id=? WHERE job_id=? AND part=?',(status,result.get('provider_id'),job['id'],index))
        if body is not None and result.get('dispatched') is not False:
            from agents.social.isluno_hospitality import record_delivery
            record_delivery(db,scope,'quote:'+job['id']+':'+str(index),body,status,
                assets=[{'document_quote_id':document_id,'type':body.get('attachmentType')}] if document_id else [])
        from agents.social.isluno_callbacks import reconcile
        reconcile(db,scope)
        status=db.execute('SELECT status FROM isluno_quote_deliveries WHERE job_id=? AND part=?',(job['id'],index)).fetchone()[0]
    if status!='accepted':
        from agents.social.isluno_recovery import RecoveryStore
        from shared import bm_logger
        try:RecoveryStore(store.conversation).incident(scope,job.get('source_trigger_id',job['id']),'delivery_failure',result.get('code') or result.get('provider_code') or status)
        except Exception:bm_logger.log('isluno_delivery_visibility_failed',code='quote_incident_store_unavailable')
    return status


@_maintenance_participating('delivery')
def send_job(conversation_id, account_id, job_id, *, store=None, post=None, window=None, sleep=None):
    from agents.social import zernio_dm_client as client
    store = store or QuoteStore()
    window = window or client.whatsapp_customer_service_window
    sleep = sleep or time.sleep
    try:
        job = store.job(job_id,account_id,conversation_id)
        scope = JourneyScope(**job['scope'])
        if job.get('answer_plan_id') and job.get('followup_job_id'):
            from agents.social.isluno_delivery import send_plan
            from agents.social.isluno_discovery import DiscoveryStore
            discovery = DiscoveryStore(store.itinerary.db_path, store.itinerary.catalog_path, clock=store.clock)
            if not send_plan(conversation_id, account_id, job['answer_plan_id'], store=discovery, post=post, window=window, sleep=sleep):
                return False
            completed = send_job(conversation_id, account_id, job['followup_job_id'], store=store, post=post, window=window, sleep=sleep)
            if completed:
                with store.db() as db, db:
                    db.execute("UPDATE isluno_quote_deliveries SET status='accepted' WHERE job_id=? AND part=0", (job_id,))
            return completed
        recipient = hashlib.sha256(dump([scope.tenant_slug, scope.account_id, scope.customer_ref]).encode()).hexdigest()
        for index, source in enumerate(job['parts']):
            # Recheck the exact quote and bound recipient before EVERY part.
            store.job(job_id,account_id,conversation_id)
            with store.db() as db:
                state = db.execute('SELECT status FROM isluno_quote_deliveries WHERE job_id=? AND part=?', (job_id,index)).fetchone()[0]
            if state == 'accepted': continue
            if state != 'queued': return False
            try:opened=window(conversation_id,account_id,job['trigger_sent_at']).get('open') is True
            except Exception:
                _save(store,scope,job,index,{'status':'blocked','code':'window_check_unavailable','dispatched':False})
                return False
            if not opened:
                _save(store,scope,job,index,{'status':'window_closed','code':'customer_window_closed','dispatched':False})
                return False
            body = copy.deepcopy(source)
            document_id = body.pop('document_quote_id', None)
            if document_id:
                # Missing deployment configuration never silently downgrades a PDF.
                store.document(document_id)
                body.update({'attachmentUrl': store.document_url(document_id), 'attachmentType': 'file',
                             'attachmentName': body.get('attachmentName', 'Isluno-quote.pdf')})
            from agents.social.isluno_wire import validate_body
            try:validate_body(body)
            except ItineraryError as exc:
                _save(store,scope,job,index,{'status':'validation_failed','code':exc.code,'dispatched':False})
                return False
            for attempt in range(2):
                with store.db() as db, db:
                    db.execute('BEGIN IMMEDIATE')
                    status = db.execute('SELECT status FROM isluno_quote_deliveries WHERE job_id=? AND part=?',(job_id,index)).fetchone()[0]
                    if status != 'queued': return False
                    row = db.execute('SELECT last_send FROM isluno_discovery_pacing WHERE recipient=?',(recipient,)).fetchone()
                    now = store.clock().timestamp()
                    wait = max(0,6-(now-row[0])) if row else 0
                    if wait == 0:
                        db.execute("UPDATE isluno_quote_deliveries SET status='claimed' WHERE job_id=? AND part=?",(job_id,index))
                        db.execute('INSERT INTO isluno_discovery_pacing VALUES(?,?) ON CONFLICT(recipient) DO UPDATE SET last_send=excluded.last_send',(recipient,now))
                        break
                if attempt:
                    _save(store,scope,job,index,{'status':'pacing_deferred','code':'recipient_pacing_not_elapsed','dispatched':False})
                    return False
                sleep(min(wait,6))
            def guard():
                store.job(job_id,account_id,conversation_id)
                return window(conversation_id,account_id,job['trigger_sent_at']).get('open') is True
            try:
                if not guard():result={'status':'window_closed','code':'customer_window_closed','dispatched':False}
                else:
                    key=f'isluno-quote-{job_id}-{index}'
                    result=post(scope,body,key) if post else post_once(scope,body,key,guard=guard)
            except Exception as exc:
                result={'status':'ambiguous','code':'transport_exception','exception_type':type(exc).__name__}
            status=_save(store,scope,job,index,result,body=body,document_id=document_id)
            if status != 'accepted': return False
        return True
    except PermissionError:
        return False
    except ItineraryError as exc:
        if 'job' in locals() and 'index' in locals():
            _save(store,scope,job,index,{'status':'blocked','code':exc.code,'dispatched':False},expected='queued')
        return False
