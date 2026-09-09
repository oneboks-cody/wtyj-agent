"""Bounded, resumable multipart quote dispatch with persisted per-part ambiguity."""
from shared.mermaid_maintenance import participating as _maintenance_participating
import copy
import hashlib
import time

from agents.social.isluno_quotes import QuoteStore
from agents.social.isluno_delivery import post_once
from agents.social.isluno_discovery import dump
from shared.isluno_config import JourneyScope
from shared.isluno_pricing import ItineraryError


@_maintenance_participating('delivery')
def send_job(conversation_id, account_id, job_id, *, store=None, post=None, window=None, sleep=None):
    from agents.social import zernio_dm_client as client
    store = store or QuoteStore()
    window = window or client.whatsapp_customer_service_window
    sleep = sleep or time.sleep
    try:
        job = store.job(job_id,account_id,conversation_id)
        scope = JourneyScope(**job['scope'])
        recipient = hashlib.sha256(dump([scope.tenant_slug, scope.account_id, scope.customer_ref]).encode()).hexdigest()
        for index, source in enumerate(job['parts']):
            # Recheck the exact quote and bound recipient before EVERY part.
            store.job(job_id,account_id,conversation_id)
            with store.db() as db:
                state = db.execute('SELECT status FROM isluno_quote_deliveries WHERE job_id=? AND part=?', (job_id,index)).fetchone()[0]
            if state == 'accepted': continue
            if state != 'queued': return False
            if window(conversation_id,account_id,job['trigger_sent_at']).get('open') is not True: return False
            body = copy.deepcopy(source)
            document_id = body.pop('document_quote_id', None)
            if document_id:
                # Missing deployment configuration never silently downgrades a PDF.
                store.document(document_id)
                body.update({'attachmentUrl': store.document_url(document_id), 'attachmentType': 'file',
                             'attachmentName': body.get('attachmentName', 'Isluno-quote.pdf')})
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
                if attempt: return False
                sleep(min(wait,6))
            def guard():
                store.job(job_id,account_id,conversation_id)
                return window(conversation_id,account_id,job['trigger_sent_at']).get('open') is True
            if not guard():
                result = {'status':'window_closed'}
            else:
                key = f'isluno-quote-{job_id}-{index}'
                result = post(scope,body,key) if post else post_once(scope,body,key,guard=guard)
            status = result.get('status')
            if status not in {'accepted','rejected','ambiguous','blocked','window_closed'}: status = 'ambiguous'
            if status == 'accepted' and not result.get('provider_id'): status = 'ambiguous'
            with store.db() as db, db:
                db.execute('UPDATE isluno_quote_deliveries SET status=?,provider_id=? WHERE job_id=? AND part=?', (status,result.get('provider_id'),job_id,index))
            if status != 'accepted': return False
        return True
    except (ItineraryError, PermissionError):
        return False
