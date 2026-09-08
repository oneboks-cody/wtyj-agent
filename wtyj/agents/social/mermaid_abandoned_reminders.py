"""Two durable, tenant-scoped reminders for unfinished customer-initiated bookings."""
import hashlib
import json
import sqlite3
import time

_next_scan = 0.0
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from shared import config_loader, state_registry


def settings():
    from agents.social.isluno_transition import blocked
    if blocked():return {}
    raw = config_loader.get_raw()
    if raw.get('slug') != 'mermaid' or not raw.get('features', {}).get('mermaid_reminders'):
        return {}
    return raw.get('mermaid_abandoned_reminders') or {}


def stamp(value):
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00')).astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def connection(now):
    c = sqlite3.connect(state_registry.DB_PATH, timeout=15)
    c.row_factory = sqlite3.Row
    c.executescript('''CREATE TABLE IF NOT EXISTS mermaid_reminder_activation (
        tenant TEXT PRIMARY KEY, activated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS mermaid_abandoned_reminders (
        idempotency_key TEXT PRIMARY KEY, tenant TEXT NOT NULL, conversation_id TEXT NOT NULL,
        anchor_id INTEGER NOT NULL, hours INTEGER NOT NULL, status TEXT NOT NULL,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        UNIQUE(tenant,conversation_id,anchor_id,hours));''')
    with c:
        c.execute("INSERT OR IGNORE INTO mermaid_reminder_activation VALUES ('mermaid',?)", (now.isoformat(),))
    return c


def eligible(c, row, now):
    fields = (json.loads(row['fields_json'] or '{}').get('mermaid_intake') or {})
    flags = json.loads(row['flags_json'] or '{}')
    if flags.get('mermaid_reminders_opt_out') or flags.get(state_registry.MERMAID_LOOP_STOPPED_FLAG):
        return None
    if fields.get('phase') in {'cancelled','cancellation_requested','human_takeover','booked'}:
        return None
    if not any(fields.get(k) for k in ('trip_date','adults','children','infants','party_size_hint')):
        return None
    if fields.get('trip_date') and fields['trip_date'] < now.astimezone(ZoneInfo('America/Curacao')).date().isoformat():
        return None
    reservation = c.execute("SELECT state,human_takeover FROM mermaid_reservations WHERE tenant_slug='mermaid' AND conversation_id=? ORDER BY updated_at DESC LIMIT 1", (row['phone'],)).fetchone()
    if reservation and (reservation['state'] in {'booked','demo_paid','cancelled'} or reservation['human_takeover']):
        return None
    if state_registry.get_ai_muted(row['phone']) or state_registry.get_active_escalation_mode(row['phone']) in {'soft','hard'}:
        return None
    if c.execute("SELECT 1 FROM inbound_processing_events WHERE conversation_id=? AND status IN ('received','processing','recovering') LIMIT 1", (row['phone'],)).fetchone():
        return None
    event = c.execute("SELECT payload_json,created_at FROM inbound_processing_events WHERE conversation_id=? AND channel='whatsapp' AND created_at<=? ORDER BY created_at DESC LIMIT 1", (row['phone'],row['created_at'])).fetchone()
    if not event:
        return None
    payload = json.loads(event['payload_json'] or '{}')
    account = str(payload.get('account_id') or '')
    times = [stamp(row['created_at']),stamp(event['created_at']),stamp(payload.get('sent_at'))]
    anchor = min(t for t in times if t is not None)
    if not account or (now-anchor).total_seconds() >= 85800:
        return None
    return fields,account,anchor


def candidate_rows(c):
    return c.execute("""SELECT u.id,u.phone,u.created_at,s.fields_json,s.flags_json
        FROM whatsapp_threads u JOIN whatsapp_booking_state s ON s.phone=u.phone
        WHERE u.role='user' AND u.channel='whatsapp'
        AND u.created_at >= (SELECT activated_at FROM mermaid_reminder_activation WHERE tenant='mermaid')
        AND u.id=(SELECT MAX(v.id) FROM whatsapp_threads v WHERE v.phone=u.phone AND v.role='user' AND v.channel='whatsapp')
        AND 'assistant'=(SELECT v.role FROM whatsapp_threads v WHERE v.phone=u.phone AND v.role!='system' ORDER BY v.id DESC LIMIT 1)
        ORDER BY u.id""").fetchall()


def run_once(now=None):
    global _next_scan
    config = settings()
    if now is None and time.monotonic() < _next_scan:
        return 0
    if now is None:
        _next_scan = time.monotonic() + 60
    if not config:
        return 0
    now = now or datetime.now(timezone.utc)
    from agents.social import zernio_dm_client as provider
    from agents.social.senders import send_reply
    from shared.tenant_guard import is_account_allowed
    c = connection(now)
    handled = 0
    try:
        for row in candidate_rows(c):
            if handled >= 10:
                break
            context = eligible(c,row,now)
            if not context:
                continue
            fields,account,anchor = context
            if not is_account_allowed(account,direction='outbound'):
                continue
            elapsed = (now-anchor).total_seconds()/3600
            due = [h for h in (6,18) if elapsed >= h]
            if not due:
                continue
            hour = max(due)  # Never send both reminders together after downtime.
            key = 'mermaid-reminder:' + hashlib.sha256(f"{row['phone']}:{row['id']}:{hour}".encode()).hexdigest()[:32]
            locale = fields.get('document_language') or fields.get('language') or 'en'
            text = (config['messages'].get(locale) or config['messages']['en'])[str(hour)]
            with c:
                claimed = c.execute("INSERT OR IGNORE INTO mermaid_abandoned_reminders VALUES (?,'mermaid',?,?,?,'sending',?,?)", (key,row['phone'],row['id'],hour,now.isoformat(),now.isoformat())).rowcount
            if not claimed:
                continue
            status = 'failed'
            try:
                window = provider.whatsapp_customer_service_window(row['phone'],account,anchor.isoformat())
                latest = c.execute("SELECT MAX(id) FROM whatsapp_threads WHERE phone=? AND role='user' AND channel='whatsapp'", (row['phone'],)).fetchone()[0]
                fresh = c.execute("SELECT u.id,u.phone,u.created_at,s.fields_json,s.flags_json FROM whatsapp_threads u JOIN whatsapp_booking_state s ON s.phone=u.phone WHERE u.id=?", (row['id'],)).fetchone()
                if latest != row['id'] or not eligible(c,fresh,now):
                    status = 'cancelled'
                elif not window.get('open'):
                    status = 'skipped_window'
                elif send_reply('whatsapp',row['phone'],account,text,confirm_delivery=True,idempotency_key=key):
                    state_registry.dm_store_message_once(row['phone'],'whatsapp','assistant',text,key,sender_name='Tracy')
                    status = 'sent'
            except Exception:
                # No blind retry after an uncertain provider result.
                status = 'failed'
            finally:
                with c:
                    c.execute('UPDATE mermaid_abandoned_reminders SET status=?,updated_at=? WHERE idempotency_key=?', (status,now.isoformat(),key))
            handled += 1
        return handled
    finally:
        c.close()
