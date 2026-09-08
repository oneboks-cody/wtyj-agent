"""One-way legacy quarantine. Rollback never re-enables old automated actions."""
import hashlib
import json
import sqlite3
from datetime import datetime,timezone
from pathlib import Path
from shared import config_loader


def path():
    from shared import state_registry
    return Path(state_registry.DB_PATH)


def requested():
    raw=config_loader.get_raw() or {}
    return raw.get('slug')=='mermaid' and isinstance(raw.get('features'),dict) and raw['features'].get('isluno_itinerary_demo_v1') is True


def blocked(db_path=None):
    if (config_loader.get_raw() or {}).get('slug')!='mermaid':return False
    if requested():return True
    target=Path(db_path) if db_path is not None else path()
    if not target.exists():return False
    with sqlite3.connect(target) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='isluno_cutover'").fetchone():return False
        return bool(db.execute("SELECT 1 FROM isluno_cutover WHERE tenant='mermaid'").fetchone())


def ensure(db_path=None,now=None):
    from shared.isluno_config import active_profile
    active_profile()
    target=Path(db_path) if db_path is not None else path();target.parent.mkdir(parents=True,exist_ok=True)
    at=(now or datetime.now(timezone.utc)).isoformat()
    with sqlite3.connect(target) as db:
        db.row_factory=sqlite3.Row
        db.executescript('''CREATE TABLE IF NOT EXISTS isluno_cutover(tenant TEXT PRIMARY KEY,activated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS isluno_legacy_quarantine(source TEXT NOT NULL,reference TEXT NOT NULL,conversation_id TEXT,
            original_status TEXT,sha256 TEXT NOT NULL,disposition TEXT NOT NULL,quarantined_at TEXT NOT NULL,PRIMARY KEY(source,reference));''')
        db.execute('BEGIN IMMEDIATE')
        if db.execute("SELECT 1 FROM isluno_cutover WHERE tenant='mermaid'").fetchone():return
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        definitions=[('mermaid_reservations','public_id','state'),('mermaid_checkout_links','token',''),
                     ('mermaid_abandoned_reminders','idempotency_key','status'),('mermaid_delivery_jobs','public_id','status'),
                     ('inbound_processing_events','message_id','status'),('mermaid_date_changes','token','status'),('mermaid_reservation_emails','public_id','status')]
        for table,identity,status in definitions:
            if table not in tables:continue
            columns={r[1] for r in db.execute('PRAGMA table_info('+table+')')}
            if identity not in columns:identity=next((key for key in ('public_id','token_hash','idempotency_key') if key in columns),None)
            if not identity:continue
            for row in db.execute('SELECT * FROM '+table):
                row=dict(row)
                if row.get('tenant_slug',row.get('tenant','mermaid'))!='mermaid':continue
                value=row.get(status,'unknown')
                if table=='inbound_processing_events':
                    payload=json.loads(row.get('payload_json') or '{}')
                    allowed=(config_loader.get_raw().get('channel_account_allowlist') or {}).get('zernio_accounts',[])
                    if payload.get('account_id') not in allowed or row.get('channel')!='whatsapp':continue
                if value in {'booked','demo_paid','cancelled','sent','delivered','completed','processed','skipped_window','accepted','declined','expired','confirmed'}:continue
                disposition='uncertain_outcome_reconcile_no_replay' if value in {'sending','uncertain','claimed','ambiguous'} or row.get('attempts',0)>0 else 'operator_review_no_automatic_replay'
                digest=hashlib.sha256(json.dumps(row,sort_keys=True,default=str).encode()).hexdigest()
                db.execute('INSERT OR IGNORE INTO isluno_legacy_quarantine VALUES(?,?,?,?,?,?,?)',(table,str(row[identity]),row.get('conversation_id'),str(value),digest,disposition,at))
        db.execute("INSERT INTO isluno_cutover VALUES('mermaid',?)",(at,))


def quarantined_inbound(message_ids,db_path=None):
    if (config_loader.get_raw() or {}).get('slug')!='mermaid':return False
    target=Path(db_path) if db_path is not None else path()
    if not target.exists():return False
    with sqlite3.connect(target) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='isluno_legacy_quarantine'").fetchone():return False
        return any(db.execute("SELECT 1 FROM isluno_legacy_quarantine WHERE source='inbound_processing_events' AND reference=?",(str(key),)).fetchone() for key in message_ids)


def audit(db_path):
    with sqlite3.connect(db_path) as db:
        db.row_factory=sqlite3.Row
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='isluno_cutover'").fetchone():return {'activated_at':None,'quarantined':[]}
        row=db.execute("SELECT activated_at FROM isluno_cutover WHERE tenant='mermaid'").fetchone()
        return {'activated_at':row[0] if row else None,'quarantined':[dict(r) for r in db.execute('SELECT * FROM isluno_legacy_quarantine ORDER BY source,reference')]}


def rollback(db_path=None,now=None):
    """Record rollback disposition without deleting history or removing fences."""
    if (config_loader.get_raw() or {}).get('slug')!='mermaid':return
    target=Path(db_path) if db_path is not None else path()
    if not target.exists():return
    with sqlite3.connect(target) as db:
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'isluno_cutover' not in tables:return
        db.execute('CREATE TABLE IF NOT EXISTS isluno_rollback_audit(at TEXT PRIMARY KEY,disposition TEXT NOT NULL UNIQUE)')
        db.execute('INSERT OR IGNORE INTO isluno_rollback_audit VALUES(?,?)',((now or datetime.now(timezone.utc)).isoformat(),'legacy_fence_retained_queued_reminders_suppressed'))
        if 'isluno_reminders' in tables:db.execute("UPDATE isluno_reminders SET status='suppressed',reason='rollback' WHERE status='queued'")
