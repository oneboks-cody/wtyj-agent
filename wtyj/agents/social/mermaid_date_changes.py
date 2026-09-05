"""Guest-confirmed date changes with durable, account-bound proposals and audit."""
import hashlib
import json
import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from shared import config_loader, mermaid_catalog, mermaid_customers, state_registry
from agents.social import mermaid_documents as docs, mermaid_reservation_store as store
from agents.social import mermaid_guest_experience as guest

PREFIX = 'mermaid-date:'
MEDIA_TYPE = 'mermaid_date_confirmation'
LOCAL = timezone(timedelta(hours=-4))


def settings():
    return (config_loader.get_raw() or {}).get('mermaid_date_changes') or {}


def enabled():
    return mermaid_catalog.reservation_demo_enabled() and settings().get('enabled') is True


def copy(locale):
    copies = settings()['copies']
    return copies.get(locale, copies['en'])


def _conn():
    # Initialize shared/customer and document tables before the write transaction.
    state_registry._get_conn().close()
    docs._conn().close()
    conn = store._conn()
    conn.execute('''CREATE TABLE IF NOT EXISTS mermaid_date_changes (
        token TEXT PRIMARY KEY, reservation_public_id TEXT NOT NULL,
        conversation_id TEXT NOT NULL, account_id TEXT NOT NULL,
        source_message_id TEXT NOT NULL, old_date TEXT NOT NULL, new_date TEXT NOT NULL,
        expected_revision INTEGER NOT NULL, locale TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending', expires_at INTEGER NOT NULL,
        document_public_id TEXT, job_id TEXT, provider_message_id TEXT,
        delivery_status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL,
        confirmed_at TEXT, faq_reply TEXT NOT NULL DEFAULT '', UNIQUE(conversation_id, source_message_id)
    )''')
    conn.commit()
    return conn


def pending(conversation_id):
    if not enabled():
        return None
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM mermaid_date_changes WHERE conversation_id=? AND status='pending' AND expires_at>? ORDER BY rowid DESC LIMIT 1", (conversation_id, int(time.time()))).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _text(text):
    return {'text': text, 'media': None}


def _held(conn, reservation):
    return bool(reservation.get('human_takeover')) or store._operator_review_active(conn, reservation['conversation_id'])


def _date_error(reservation, target):
    cfg = mermaid_catalog.get_catalog()
    now = datetime.now(LOCAL)
    try:
        day = datetime.strptime(target, '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return 'invalid'
    if day <= now.date() or day.strftime('%A').lower() not in cfg['service']['operating_weekdays']:
        return 'invalid'
    old = datetime.fromisoformat(reservation['intake']['trip_date'] + 'T' + cfg['service']['arrival_time']).replace(tzinfo=LOCAL)
    if old - now < timedelta(hours=settings().get('minimum_notice_hours', 24)):
        return 'cutoff'
    if target == reservation['intake']['trip_date']:
        return 'same'
    return None


def proposal_reply(proposal):
    c = copy(proposal['locale'])
    return {'text': '\n\n'.join(part for part in (proposal.get('faq_reply',''), c['confirm'].format(old=guest.guest_date(proposal['old_date'], proposal['locale']), new=guest.guest_date(proposal['new_date'], proposal['locale']))) if part),
            'media': {'url': proposal['token'], 'type': MEDIA_TYPE}}


def propose(message, reservation, target, locale, faq_reply=""):
    conn = _conn()
    try:
        conn.execute('BEGIN IMMEDIATE')
        current = store.get_reservation(reservation['public_id'], connection=conn)
        if (not current or current['state'] != 'booked' or
            current['conversation_id'] != str(message.get('from') or '') or
            current['zernio_account_id'] != str(message.get('_zernio_account_id') or '') or _held(conn, current)):
            return _text(copy(locale)['held'])
        error = _date_error(current, target)
        if error:
            return _text(copy(locale)[error])
        existing = conn.execute('SELECT * FROM mermaid_date_changes WHERE conversation_id=? AND source_message_id=?', (current['conversation_id'], str(message.get('message_id') or ''))).fetchone()
        if existing:
            return proposal_reply(dict(existing)) if existing['status']=='pending' else _text(copy(locale)['stale'])
        # Repeating the same request retains its bound proposal and deadline.
        existing = conn.execute("SELECT * FROM mermaid_date_changes WHERE conversation_id=? AND new_date=? AND expected_revision=? AND status='pending' AND expires_at>?", (current['conversation_id'], target, current['revision'], int(time.time()))).fetchone()
        if existing and existing['faq_reply']==faq_reply:
            return proposal_reply(dict(existing))
        token = secrets.token_urlsafe(24)
        conn.execute("UPDATE mermaid_date_changes SET status='superseded' WHERE conversation_id=? AND status='pending'", (current['conversation_id'],))
        conn.execute('INSERT INTO mermaid_date_changes (token,reservation_public_id,conversation_id,account_id,source_message_id,old_date,new_date,expected_revision,locale,expires_at,created_at,faq_reply) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                     (token,current['public_id'],current['conversation_id'],current['zernio_account_id'],str(message.get('message_id') or token),current['intake']['trip_date'],target,current['revision'],locale,int(time.time())+60*settings().get('confirmation_minutes',30),docs._now(),faq_reply))
        row=dict(conn.execute('SELECT * FROM mermaid_date_changes WHERE token=?',(token,)).fetchone())
        conn.commit()
        return proposal_reply(row)
    finally:
        conn.close()


def _completed_reply(proposal, reservation):
    document_id, job_id = proposal['document_public_id'], proposal['job_id']
    document = next(d for d in docs.documents_for_reservation(reservation['public_id']) if d['public_id']==document_id)
    base = os.environ.get('UNBOKS_PUBLIC_BASE_URL','').rstrip('/')
    return {'text':copy(proposal['locale'])['complete'].format(new=guest.guest_date(proposal['new_date'],proposal['locale'])),
            'media':{'url':docs.build_signed_url(base,document_id,os.environ.get('MERMAID_DEMO_SIGNING_SECRET','')), 'type':'file','filename':document['filename']},
            'mermaid_delivery_commit':{'job_id':job_id}}


def handle_button(message):
    value=str(message.get('_zernio_interactive_id') or '')
    if not enabled() or not value.startswith(PREFIX):
        return None
    parts=value[len(PREFIX):].split(':')
    locale='en'
    if len(parts)!=2 or parts[1] not in {'confirm','keep'}:
        return _text(copy(locale)['stale'])
    token, choice=parts
    conn=_conn()
    try:
        conn.execute('BEGIN IMMEDIATE')
        row=conn.execute('SELECT * FROM mermaid_date_changes WHERE token=?',(token,)).fetchone()
        if not row or (row['conversation_id'],row['account_id']) != (str(message.get('from') or ''),str(message.get('_zernio_account_id') or '')):
            return _text(copy(locale)['stale'])
        proposal=dict(row);locale=proposal['locale']
        reservation=store.get_reservation(proposal['reservation_public_id'],connection=conn)
        if not reservation or _held(conn,reservation):
            return _text(copy(locale)['held'])
        if proposal['status']=='confirmed':
            # A replay can redeliver only the same revision; never undo a later change.
            if reservation['intake']['trip_date']!=proposal['new_date']:
                return _text(copy(locale)['stale'])
            conn.commit()
            if (docs.delivery_job(proposal['job_id']) or {}).get('status')=='delivered':
                return {'text':'','media':None,'duplicate':True}
            return _completed_reply(proposal,reservation)
        if (proposal['status']!='pending' or proposal['expires_at']<int(time.time()) or reservation['revision']!=proposal['expected_revision'] or reservation['intake']['trip_date']!=proposal['old_date'] or reservation['state']!='booked'):
            return _text(copy(locale)['stale'])
        if choice=='keep':
            conn.execute("UPDATE mermaid_date_changes SET status='cancelled' WHERE token=?",(token,));conn.commit()
            return _text(copy(locale)['kept'].format(old=guest.guest_date(proposal['old_date'],locale)))
        error=_date_error(reservation,proposal['new_date'])
        if error:return _text(copy(locale)[error])
        payment_row=conn.execute('SELECT * FROM mermaid_demo_payments WHERE reservation_public_id=?',(reservation['public_id'],)).fetchone()
        if not payment_row:return _text(copy(locale)['held'])
        now=docs._now();revision=reservation['revision']+1
        intake={**reservation['intake'],'trip_date':proposal['new_date']}
        updated={**reservation,'intake':intake,'revision':revision}
        doc_id='mdoc_'+hashlib.sha256(('date-receipt:'+token).encode()).hexdigest()[:24]
        job_id='mjob_'+hashlib.sha256(('date-receipt:'+token).encode()).hexdigest()[:24]
        filename=f"Mermaid - Updated Receipt - {guest.display_reference(reservation['booking_code'])} - {proposal['new_date']}.pdf"
        target=docs._root()/reservation['public_id']/token/filename
        digest=docs.render_receipt_pdf(updated,dict(payment_row),target)
        conn.execute("INSERT INTO mermaid_documents (public_id,tenant_slug,reservation_public_id,kind,locale,filename,path,sha256,content_type,created_at,document_revision) VALUES (?,'mermaid',?,'receipt',?,?,?,?, 'application/pdf',?,?)",(doc_id,reservation['public_id'],locale,filename,str(target),digest,now,revision))
        conn.execute("INSERT INTO mermaid_delivery_jobs (public_id,tenant_slug,reservation_public_id,document_public_id,conversation_id,kind,status,idempotency_key,created_at,updated_at) VALUES (?,'mermaid',?,?,?,'receipt','pending',?,?,?)",(job_id,reservation['public_id'],doc_id,reservation['conversation_id'],'mermaid-date-receipt:'+token,now,now))
        conn.execute('UPDATE mermaid_reservations SET intake_json=?,revision=?,receipt_public_id=?,updated_at=? WHERE public_id=? AND revision=?',(json.dumps(intake,ensure_ascii=False),revision,doc_id,now,reservation['public_id'],proposal['expected_revision']))
        audit=f"TRACY changed the reservation date from {proposal['old_date']} to {proposal['new_date']} after customer confirmation. Payment and guest details unchanged. Revised receipt: {filename}. HO note recorded."
        conn.execute("INSERT INTO mermaid_reservation_events (reservation_public_id,tenant_slug,event_type,from_state,to_state,actor,reason,idempotency_key,revision,payload_json,created_at) VALUES (?,'mermaid','date_changed','booked','booked','customer',?,?,?,?,?)",(reservation['public_id'],audit,'date-change:'+token,revision,json.dumps({'old_date':proposal['old_date'],'new_date':proposal['new_date'],'confirmation_message_id':str(message.get('message_id') or ''),'document_public_id':doc_id}),now))
        state=conn.execute('SELECT fields_json FROM whatsapp_booking_state WHERE phone=?',(reservation['conversation_id'],)).fetchone()
        fields=json.loads(state[0]) if state else {}
        fields['mermaid_intake']={**fields.get('mermaid_intake',intake),'trip_date':proposal['new_date']}
        conn.execute('UPDATE whatsapp_booking_state SET fields_json=? WHERE phone=?',(json.dumps(fields,ensure_ascii=False),reservation['conversation_id']))
        for table in ('mermaid_crew_assistance', 'mermaid_crew_assistance_reservations'):
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone():
                conn.execute(f"UPDATE {table} SET trip_date=? WHERE tenant_slug='mermaid' AND reservation_public_id=? AND trip_date=?",(proposal['new_date'],reservation['public_id'],proposal['old_date']))
        mermaid_customers.capture(conn,reservation['conversation_id'],intake=intake,name=reservation['customer_name'],at=now)
        conn.execute("INSERT INTO whatsapp_threads (phone,role,text,created_at,channel,sender_name,source_message_key) VALUES (?,'system',? ,?,'whatsapp','TRACY',?)",(reservation['conversation_id'],audit,now,'mermaid-date:'+token))
        conn.execute("UPDATE mermaid_date_changes SET status='confirmed',document_public_id=?,job_id=?,confirmed_at=? WHERE token=?",(doc_id,job_id,now,token))
        conn.commit()
        return _completed_reply({**proposal,'document_public_id':doc_id,'job_id':job_id},updated)
    finally:
        conn.close()


def send_confirmation(conversation_id, account_id, token):
    """Use the provider's reply-button API; persist its receipt before polling."""
    from agents.social import zernio_dm_client as provider
    if not enabled():return False
    conn=_conn()
    try:
        row=conn.execute('SELECT * FROM mermaid_date_changes WHERE token=?',(token,)).fetchone()
        if not row or (row['conversation_id'],row['account_id'])!=(conversation_id,account_id):return False
        p=dict(row)
        if p['status']!='pending' or p['expires_at']<int(time.time()):return False
        if p['delivery_status']=='delivered':return True
        if not provider._provider_mutation_account_allowed(account_id,'mermaid_date_confirmation'):return False
        headers={'Authorization':'Bearer '+os.environ.get('LATE_API_KEY',''),'Content-Type':'application/json','Idempotency-Key':'mermaid-date-proposal:'+token}
        endpoint='https://zernio.com/api/v1/inbox/conversations/'+quote(conversation_id,safe='')
        window,_=provider._recommendation_session_open(endpoint,headers,account_id)
        if not window:return False
        pid=p['provider_message_id']
        if not pid:
            c=copy(p['locale']);payload={'accountId':account_id,'text':proposal_reply(p)['text'],'buttons':[{'type':'postback','title':c['yes'],'payload':PREFIX+token+':confirm'},{'type':'postback','title':c['keep'],'payload':PREFIX+token+':keep'}]}
            outcome,status,pid=provider._post_recommendation_message(endpoint+'/messages',headers,payload)
            with conn:conn.execute('UPDATE mermaid_date_changes SET provider_message_id=? WHERE token=?',(pid,token))
        if not pid:return False
        delivered=provider._confirm_recommendation_status(endpoint+'/messages',headers,account_id,pid,require_delivered=True)=='sent'
        with conn:conn.execute('UPDATE mermaid_date_changes SET delivery_status=? WHERE token=?',('delivered' if delivered else 'pending',token))
        return delivered
    finally:
        conn.close()
