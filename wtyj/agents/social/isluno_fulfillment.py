"""WhatsApp fulfillment uses the existing durable sender; email is independent."""
from shared.mermaid_maintenance import participating as _maintenance_participating
from html import escape
import json
import threading

from agents.social.isluno_payments import PaymentStore
from agents.social.isluno_payment_copy import COPY
from agents.social.isluno_quote_delivery import send_job
from shared.isluno_config import JourneyScope, require_scope
from shared.isluno_pricing import ItineraryError


def email_guard(scope):
    """Email owns its durable claim, not the completed inbound message lease."""
    from shared import state_registry, icp_overrides
    from agents.social.zernio_dm_client import _provider_account_allowed
    require_scope(scope)
    if state_registry.get_blocked(scope.conversation_id) or state_registry.get_ai_muted(scope.conversation_id):
        return False
    try:
        controls = icp_overrides.fetch_overrides_fresh()
        if icp_overrides.auto_reply_state(controls) is not True or icp_overrides.whatsapp_inbox_state(controls) is not True:
            return False
    except Exception:
        return False
    if state_registry.get_blocked(scope.conversation_id) or state_registry.get_ai_muted(scope.conversation_id):
        return False
    return _provider_account_allowed(scope.account_id, 'isluno_receipt_email', boundary='mutation') is True


@_maintenance_participating('delivery')
def send_pending_email(scope, *, store=None, transport=None, guard=None):
    from agents.social import mermaid_email_transport
    store=store or PaymentStore();transport=transport or mermaid_email_transport.send_email
    guard=guard or (lambda: email_guard(scope))
    require_scope(scope)
    if not guard():return False
    with store.db() as db,db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute("SELECT e.*,c.consent_trigger,p.snapshot FROM isluno_paid_emails e JOIN isluno_email_consents c ON c.token=e.consent_token JOIN isluno_payments p ON p.id=e.payment_id WHERE e.scope_key=? AND e.status='queued' ORDER BY e.rowid LIMIT 1",(scope.key,)).fetchone()
        if row is None:return True
        if not row['consent_trigger']:return False
        document=db.execute("SELECT pdf FROM isluno_paid_documents WHERE payment_id=? AND kind='receipt'",(row['payment_id'],)).fetchone()
        if document is None:return False
        raw=bytes(document[0]);snapshot=json.loads(row['snapshot']);w=COPY[snapshot['document_language']]
        db.execute("UPDATE isluno_paid_emails SET status='claimed' WHERE id=?",(row['id'],))
    try:
        require_scope(scope)
        if not guard():
            status,error='blocked','scope_guard'
        else:
            content={'subject':'Isluno - '+w[0], 'text':w[2], 'html':'<p>'+escape(w[2])+'</p>', 'sender_display_name':'TRACY | Isluno'}
            transport(row['recipient'],content,('Isluno-demo-receipt.pdf',raw),message_id=row['message_id'])
            status,error='accepted',None
    except mermaid_email_transport.EmailSendError as exc:
        status,error=('ambiguous' if exc.uncertain else 'failed'),exc.code
    except Exception:
        status,error='ambiguous','unexpected_transport_error'
    with store.db() as db,db:
        db.execute('UPDATE isluno_paid_emails SET status=?,error=? WHERE id=?',(status,error,row['id']))
    return status=='accepted'


@_maintenance_participating('delivery')
def send_fulfillment(conversation_id, account_id, job_id, *, store=None, dispatch=None, schedule_email=None, send_question=None):
    store=store or PaymentStore()
    try:
        job=store.job(job_id,account_id,conversation_id)
    except (ItineraryError, PermissionError):
        return False
    scope=JourneyScope(**job['scope'])
    if job['stage'] == 'composed':
        if send_question is None:
            from agents.social.isluno_delivery import send_plan
            from agents.social.isluno_discovery import DiscoveryStore
            discovery = DiscoveryStore(store.itinerary.db_path, store.itinerary.catalog_path, clock=store.clock)
            send_question = lambda conversation, account, plan: send_plan(conversation, account, plan, store=discovery)
        if not send_question(conversation_id, account_id, job['answer_plan_id']):
            return False
        return send_fulfillment(conversation_id, account_id, job['fulfillment_job_id'], store=store, dispatch=dispatch, schedule_email=schedule_email)
    result=(dispatch or send_job)(conversation_id,account_id,job_id,store=store)
    # An email failure can never alter the payment or suppress WhatsApp parts.
    with store.db() as db:
        queued=db.execute("SELECT 1 FROM isluno_paid_emails WHERE scope_key=? AND status='queued'",(scope.key,)).fetchone()
    if queued:
        if schedule_email:
            schedule_email(scope)
        else:
            threading.Thread(target=send_pending_email,args=(scope,),kwargs={'store':store},daemon=True).start()
    if result and job['stage']=='fulfilled':
        with store.db() as db,db:
            db.execute("UPDATE isluno_operator_requests SET status='resolved' WHERE id=? AND scope_key=? AND reason='demo_fulfillment_review'",('fulfillment-'+job['payment_id'],scope.key))
    if not result:
        with store.db() as db,db:
            uncertain=db.execute("SELECT 1 FROM isluno_quote_deliveries WHERE job_id=? AND status IN ('ambiguous','claimed','rejected')",(job_id,)).fetchone()
            if uncertain:
                key='fulfillment-'+job['payment_id']
                db.execute("INSERT OR IGNORE INTO isluno_operator_requests(id,scope_key,itinerary_id,reason,request_json) VALUES(?,?,?,'demo_fulfillment_review',?)",(key,scope.key,store._paid(db,scope,job['payment_id'])['itinerary_id'],json.dumps({'job_id':job_id})))
    return result
