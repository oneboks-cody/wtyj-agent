"""Opt-in booking emails, durable recipient consent and an at-most-once send ledger."""
import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from shared import mermaid_catalog, mermaid_customers, state_registry
from agents.social import mermaid_documents as docs, mermaid_reservation_store as store
from agents.social import mermaid_email_template as template, mermaid_email_transport as transport

ACTIONS = {'request', 'accept', 'decline', 'address', 'confirm', 'resend', 'status'}


def enabled():
    return mermaid_catalog.reservation_demo_enabled() and template.settings().get('enabled') is True


def ready():
    return enabled() and transport.ready()


def copy(locale, key, **values):
    return template.copy_for(locale)[key].format(**values)


def normalize_email(value):
    if not isinstance(value, str) or len(value) > 254 or any(c in value for c in '\r\n'):
        return None
    value = value.strip()
    if value.count('@') != 1:
        return None
    local, domain = value.rsplit('@', 1)
    if not local or len(local) > 64 or not local.isascii() or not re.fullmatch(r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~.-]+", local) or local.startswith('.') or local.endswith('.') or '..' in local:
        return None
    try:
        domain = domain.encode('idna').decode('ascii').lower()
    except UnicodeError:
        return None
    if '.' not in domain or len(domain) > 253 or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', part) for part in domain.split('.')):
        return None
    return local + '@' + domain


def _conn():
    state_registry._get_conn().close()
    conn = docs._conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS mermaid_email_preferences (
          reservation_public_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL,
          account_id TEXT NOT NULL, phase TEXT NOT NULL, proposed_email TEXT,
          consent_message_id TEXT, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS mermaid_reservation_emails (
          public_id TEXT PRIMARY KEY, reservation_public_id TEXT NOT NULL,
          conversation_id TEXT NOT NULL, source_message_id TEXT NOT NULL,
          recipient TEXT NOT NULL, revision INTEGER NOT NULL, document_public_id TEXT NOT NULL,
          status TEXT NOT NULL, message_id TEXT NOT NULL, subject TEXT NOT NULL DEFAULT '',
          error_code TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          UNIQUE(reservation_public_id, source_message_id));
    """)
    return conn


def _preference(conn, reservation):
    row = conn.execute('SELECT * FROM mermaid_email_preferences WHERE reservation_public_id=?', (reservation['public_id'],)).fetchone()
    return dict(row) if row else None


def _set(conn, reservation, phase, email=None, consent=None):
    conn.execute('''INSERT INTO mermaid_email_preferences VALUES (?,?,?,?,?,?,?)
        ON CONFLICT(reservation_public_id) DO UPDATE SET phase=excluded.phase,
        proposed_email=excluded.proposed_email, consent_message_id=excluded.consent_message_id,
        updated_at=excluded.updated_at''', (reservation['public_id'], reservation['conversation_id'], reservation['zernio_account_id'], phase, email, consent, docs._now()))


def _audit(conn, reservation, key, text):
    conn.execute("""INSERT INTO whatsapp_threads(phone,role,text,created_at,channel,sender_name,source_message_key)
        SELECT ?,'system',?,?,'whatsapp','TRACY',? WHERE NOT EXISTS
        (SELECT 1 FROM whatsapp_threads WHERE source_message_key=?)""",
        (reservation['conversation_id'], text, docs._now(), key, key))


def context(reservation):
    if not enabled() or not reservation:
        return None
    conn = _conn()
    try:
        pref = _preference(conn, reservation)
        job = conn.execute('SELECT recipient,status,revision FROM mermaid_reservation_emails WHERE reservation_public_id=? ORDER BY rowid DESC LIMIT 1', (reservation['public_id'],)).fetchone()
        return {'available': ready(), 'phase': pref['phase'] if pref else 'not_offered',
                'proposed_email': pref['proposed_email'] if pref else None,
                'last_email': dict(job) if job else None}
    finally:
        conn.close()


def offer(reservation):
    """Called while preparing a paid receipt; no network and no email is sent."""
    if not ready() or reservation['state'] != 'booked':
        return ''
    conn = _conn()
    try:
        with conn:
            pref = _preference(conn, reservation)
            if pref and pref['phase'] != 'offered':
                return ''
            if not pref:
                _set(conn, reservation, 'offered')
        return copy(reservation['language'], 'offer')
    finally:
        conn.close()


def _known_email(conn, reservation):
    customer = mermaid_customers.capture(conn, reservation['conversation_id'])
    row = conn.execute('SELECT intake_json FROM mermaid_customer_intakes WHERE customer_id=? ORDER BY id DESC LIMIT 1', (customer,)).fetchone()
    return normalize_email(json.loads(row[0]).get('email')) if row else None


def _job_reply(job, locale):
    if job['status'] == 'accepted':
        return copy(locale, 'sent', email=job['recipient'])
    if job['status'] in {'sending', 'uncertain'}:
        return copy(locale, 'uncertain', email=job['recipient'])
    return copy(locale, 'failed', email=job['recipient'])


def _send(reservation, recipient, source_id, locale, *, allow_resend=False):
    """Prepare current data before claiming the SMTP attempt, then never blind-retry."""
    from agents.social.isluno_transition import blocked
    if blocked():return ""
    conn = _conn()
    attempted = False
    try:
        conn.execute('BEGIN IMMEDIATE')
        current = store.get_reservation(reservation['public_id'], connection=conn)
        if not current or current['state'] != 'booked':
            return copy(locale, 'unpaid')
        payment = conn.execute("SELECT * FROM mermaid_demo_payments WHERE reservation_public_id=? AND status='simulated_success'", (current['public_id'],)).fetchone()
        if not payment:
            return copy(locale, 'unpaid')
        existing = conn.execute('SELECT * FROM mermaid_reservation_emails WHERE reservation_public_id=? AND source_message_id=?', (current['public_id'], source_id)).fetchone()
        if existing:
            return _job_reply(dict(existing), locale)
        active = conn.execute("SELECT * FROM mermaid_reservation_emails WHERE reservation_public_id=? ORDER BY rowid DESC LIMIT 1", (current['public_id'],)).fetchone()
        if active and active['status'] in {'sending','uncertain'}:
            recent_send = active['status'] == 'sending' and (datetime.now(timezone.utc) - datetime.fromisoformat(active['updated_at'])).total_seconds() < 180
            if not allow_resend or recent_send:
                return _job_reply(dict(active), locale)
        if not ready():
            return copy(locale, 'not_ready')
        document = conn.execute("SELECT * FROM mermaid_documents WHERE public_id=? AND reservation_public_id=? AND kind='receipt'", (current['receipt_public_id'], current['public_id'])).fetchone()
        if not document:
            return copy(locale, 'failed', email=recipient)
        document = dict(document)
        path = Path(document['path']).resolve()
        if not path.is_relative_to(docs._root().resolve()):
            raise ValueError('Receipt path outside document store')
        attachment = path.read_bytes()
        if hashlib.sha256(attachment).hexdigest() != document['sha256']:
            raise ValueError('Receipt digest mismatch')
        from agents.social import mermaid_document_cards as cards
        base = os.environ.get('UNBOKS_PUBLIC_BASE_URL', '').rstrip('/')
        from urllib.parse import urlsplit
        origin = urlsplit(base)
        if origin.scheme != 'https' or not origin.netloc or origin.username or origin.password or origin.query or origin.fragment:
            raise ValueError('Public email URL is not configured')
        receipt_url = cards.download_url(base, document, current)
        hero_url = base + '/api/public/mermaid-card-image/' + hashlib.sha256(docs.HERO_IMAGE.read_bytes()).hexdigest() + '.png'
        content = template.render_email(current, dict(payment), recipient, receipt_url=receipt_url, hero_url=hero_url)
        key = hashlib.sha256((current['public_id'] + ':' + source_id).encode()).hexdigest()[:32]
        public_id = 'memail_' + key
        message_id = '<mermaid.' + key + '@' + transport.sender_address().split('@')[1] + '>'
        now = docs._now()
        conn.execute('INSERT INTO mermaid_reservation_emails (public_id,reservation_public_id,conversation_id,source_message_id,recipient,revision,document_public_id,status,message_id,subject,created_at,updated_at) VALUES (?,?,?,?,?,?,?,\'sending\',?,?,?,?)', (public_id,current['public_id'],current['conversation_id'],source_id,recipient,current['revision'],document['public_id'],message_id,content['subject'],now,now))
        _audit(conn,current,public_id+':requested',f"Guest requested reservation email to {recipient}. Booking revision {current['revision']}; receipt {document['filename']}. Email send started.")
        conn.commit()
        attempted = True
        try:
            if blocked():raise transport.EmailSendError("legacy_quarantined")
            transport.send_email(recipient, content, (document['filename'], attachment), message_id=message_id)
            status, error = 'accepted', ''
        except transport.EmailSendError as exc:
            status, error = ('uncertain' if exc.uncertain else 'failed'), exc.code
        except Exception:
            status, error = 'uncertain', 'unexpected_send_error'
        with conn:
            conn.execute('UPDATE mermaid_reservation_emails SET status=?,error_code=?,updated_at=? WHERE public_id=?', (status,error,docs._now(),public_id))
            if status == 'accepted':
                _set(conn,current,'sent',recipient,source_id)
            _audit(conn,current,public_id+':'+status,f"Reservation email to {recipient}: {status}. Booking revision {current['revision']}; receipt {document['filename']}." + (' SMTP accepted the email; inbox delivery is not verified.' if status=='accepted' else ' No automatic resend.'))
        return _job_reply({'status':status,'recipient':recipient},locale)
    except Exception:
        conn.rollback()
        # Preparation failures cannot claim a send. If the SMTP attempt was
        # claimed, its durable sending row prevents an unsafe automatic retry.
        return copy(locale, 'uncertain' if attempted else 'failed', email=recipient)
    finally:
        conn.close()


def handle(message, reservation, understood, locale, *, wait_for_date=False):
    action = understood.get('email_action', 'none')
    if not enabled() or action not in ACTIONS:
        return None
    if not reservation or reservation['state'] != 'booked':
        return copy(locale, 'unpaid')
    if (reservation['conversation_id'], reservation['zernio_account_id']) != (str(message.get('from') or ''), str(message.get('_zernio_account_id') or '')):
        return copy(locale, 'not_ready')
    source = str(message.get('message_id') or '')
    evidence = understood.get('email_request_excerpt')
    text = str(message.get('text') or '')
    if not source or not isinstance(evidence,str) or not evidence.strip() or evidence not in text:
        return copy(locale, 'offer')
    conn = _conn()
    recipient = None
    try:
        conn.execute('BEGIN IMMEDIATE')
        pref = _preference(conn, reservation) or {}
        existing = conn.execute('SELECT * FROM mermaid_reservation_emails WHERE reservation_public_id=? AND source_message_id=?', (reservation['public_id'],source)).fetchone()
        if existing:
            return _job_reply(dict(existing),locale)
        if action == 'decline':
            _set(conn,reservation,'declined')
            _audit(conn,reservation,'mermaid-email-declined:'+source,'Guest declined the optional reservation email.')
            conn.commit()
            return copy(locale,'declined')
        if action == 'status':
            job = conn.execute('SELECT * FROM mermaid_reservation_emails WHERE reservation_public_id=? ORDER BY rowid DESC LIMIT 1',(reservation['public_id'],)).fetchone()
            if job:
                return _job_reply(dict(job),locale)
            if not ready():
                return copy(locale,'not_ready')
            if pref.get('phase') == 'awaiting_date_confirmation':
                return copy(locale,'pending_change')
            if pref.get('phase') == 'awaiting_address':
                return copy(locale,'ask_address')
            if pref.get('phase') == 'awaiting_confirmation' and pref.get('proposed_email'):
                return copy(locale,'confirm_address',email=pref['proposed_email'])
            _set(conn,reservation,'offered')
            conn.commit()
            return copy(locale,'no_email_sent')
        value = understood.get('email_address')
        if value:
            recipient = normalize_email(value)
            if not recipient or value.strip() not in text:
                return copy(locale,'invalid_address')
            mermaid_customers.set_email(conn,reservation['conversation_id'],recipient)
        if not ready():
            conn.commit()
            return copy(locale,'not_ready')
        if action in {'accept','confirm'}:
            if pref.get('phase') == 'awaiting_confirmation' and pref.get('proposed_email'):
                recipient = recipient or pref['proposed_email']
            elif pref.get('phase') not in {'offered','awaiting_address'}:
                _set(conn,reservation,'offered')
                conn.commit()
                return copy(locale,'offer')
        if action == 'address' and (not pref.get('consent_message_id') or pref.get('phase') not in {'awaiting_address','awaiting_confirmation','consented'}):
            if recipient:
                _set(conn,reservation,'awaiting_confirmation',recipient)
                conn.commit()
                return copy(locale,'confirm_address',email=recipient)
            return copy(locale,'invalid_address')
        if action == 'resend' and not recipient:
            latest = conn.execute('SELECT recipient FROM mermaid_reservation_emails WHERE reservation_public_id=? ORDER BY rowid DESC LIMIT 1',(reservation['public_id'],)).fetchone()
            recipient = latest[0] if latest else None
        has_proposals = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='mermaid_date_changes'").fetchone()
        if wait_for_date or (has_proposals and conn.execute("SELECT 1 FROM mermaid_date_changes WHERE conversation_id=? AND status='pending' AND expires_at>?", (reservation['conversation_id'],int(time.time()))).fetchone()):
            _set(conn,reservation,'awaiting_date_confirmation',recipient,source)
            conn.commit()
            return copy(locale,'pending_change')
        if not recipient and action == 'request' and pref.get('phase')=='awaiting_date_confirmation':
            recipient = pref.get('proposed_email')
        if not recipient:
            saved = _known_email(conn,reservation)
            _set(conn,reservation,'awaiting_confirmation' if saved else 'awaiting_address',saved,source)
            _audit(conn,reservation,'mermaid-email-consent:'+source,'Guest agreed to receive the reservation details and receipt by email.')
            conn.commit()
            return copy(locale,'saved_address',email=saved) if saved else copy(locale,'ask_address')
        _set(conn,reservation,'consented',recipient,source)
        conn.commit()
    finally:
        conn.close()
    return _send(reservation,recipient,source,locale,allow_resend=action=='resend')


def after_date_change(reservation):
    if not enabled():
        return ''
    conn = _conn()
    try:
        pref = _preference(conn,reservation)
        if pref and pref['phase']=='awaiting_date_confirmation' and not pref['proposed_email']:
            with conn:
                saved = _known_email(conn,reservation)
                _set(conn,reservation,'awaiting_confirmation' if saved else 'awaiting_address',saved,pref['consent_message_id'])
            return copy(reservation['language'],'saved_address',email=saved) if saved else copy(reservation['language'],'ask_address')
    finally:
        conn.close()
    if pref and pref['phase']=='awaiting_date_confirmation' and pref['proposed_email'] and pref['consent_message_id']:
        return _send(reservation,pref['proposed_email'],pref['consent_message_id'],reservation['language'])
    return ''
