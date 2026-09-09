# bluemarlin/shared/state_registry.py
# Last modified: Brief 098
# Purpose: SQLite WAL deduplication, capacity, manifests, bookings
import hashlib
import copy
import fcntl
import json
import os
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from typing import Optional

DB_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "data", "state_registry.db"
)

# A buffered turn can legitimately wait for the tenant-configured debounce
# hard cap (currently up to five minutes).  Once processing starts, model and
# confirmed-provider work can include two independent 600-second model calls
# plus provider/local commit time.  The conservative processing lease covers
# that full path; generation fencing prevents an expired worker from acting if
# recovery nevertheless reclaims it.  A real crash can therefore take up to
# 22 minutes to recover, which is preferable to overlapping live workers until
# a claim-scoped heartbeat is introduced.
INBOUND_DEBOUNCE_LEASE_MARGIN_SECONDS = 15.0
INBOUND_PROCESSING_LEASE_SECONDS = 1320.0
INBOUND_RECOVERY_CLAIM_LEASE_SECONDS = 40.0
VEHICLE_RECOMMENDATION_RECOVERY_LEASE_SECONDS = 300.0
ZERNIO_FAILED_EVENT_LEASE_SECONDS = 1320.0

_EMAIL_STATE_PROCESS_LOCK = threading.RLock()
_MISSING = object()

MERMAID_LOOP_STOPPED_FLAG = "mermaid_loop_stopped"
MERMAID_LOOP_STOP_STATUS = "Loop detected and stopped"


def mermaid_loop_status(flags: dict | None) -> str | None:
    """Return the exact operator status for a terminal Mermaid loop stop."""
    return (
        MERMAID_LOOP_STOP_STATUS
        if isinstance(flags, dict) and flags.get(MERMAID_LOOP_STOPPED_FLAG) is True
        else None
    )


class HandoffAccountReassignedError(PermissionError):
    """A known foreign account cannot persist a tenant operator work item."""


class EscalationRevisionConflictError(RuntimeError):
    """An operator action targeted an older version of a reused work item."""

    def __init__(self, expected: int, current: int):
        self.expected = int(expected)
        self.current = int(current)
        super().__init__(
            "This case changed while the action was being prepared. "
            "Review the latest guest message before trying again"
        )


def _require_escalation_revision(
    current: int | None, expected: int | None,
) -> int:
    """Validate an operator's work-item token while its write lock is held.

    ``None`` remains an internal-call compatibility escape hatch. Dashboard
    routes always pass a positive revision (legacy clients safely default to
    revision 1), so an old UI cannot mutate a row after new guest content has
    reused it.
    """
    normalized = int(current or 1)
    if expected is not None and int(expected) != normalized:
        raise EscalationRevisionConflictError(int(expected), normalized)
    return normalized

# Brief 217: optional callback set by dashboard.api at module-import time.
# `dashboard.api` registers `_fire_escalation_alerts` here so that
# create_pending_notification can fire alerts WITHOUT state_registry
# having to import dashboard.api (would create a circular import).
# When None, alert dispatch is silently skipped (e.g., state_registry
# helper unit tests that don't load the dashboard router).
_alert_dispatcher = None

# Consulta Despertares: optional callback for transition-based prospect queue
# alerts. The social agent emits a state change; dashboard.api owns delivery so
# state_registry stays independent from provider clients.
_follow_up_alert_dispatcher = None

# Brief 227: dashboard.api registers a summary generator here. Mirrors the
# Brief 217 alert-dispatcher pattern — one global, set once at module-load,
# called best-effort with try/except gating so a Claude failure never blocks
# escalation row creation.
_summary_dispatcher = None


_SYSTEM_EMAIL_DOMAINS = {
    "facebookmail.com",
}


def is_system_email_sender(email_addr: str) -> bool:
    """Return True for provider/system notification senders.

    These senders are not customers and should not become operator inbox
    conversations or trigger Marina. Keep this deliberately narrow: domains
    are added only when they are known platform notification domains.
    """
    if not email_addr:
        return False
    value = email_addr.strip().lower()
    if "@" not in value:
        return False
    domain = value.rsplit("@", 1)[-1]
    return domain in _SYSTEM_EMAIL_DOMAINS


def _current_tenant_id() -> str:
    for name in ("TENANT_ID", "TENANT_SLUG"):
        value = os.environ.get(name, "").strip().lower()
        if value:
            return value
    try:
        from shared import config_loader
        raw = config_loader.get_raw()
        slug = raw.get("slug") if isinstance(raw, dict) else ""
        if isinstance(slug, str) and slug.strip():
            return slug.strip().lower()
    except Exception:
        pass
    return "default"


def normalize_phone_identifier(value: str | None) -> str:
    """Normalize phone-like sender ids for exact matching.

    Keep this conservative: strip common extension suffixes and ASCII
    non-digits only. We do not do partial matching or country guessing.
    """
    if not value:
        return ""
    raw = str(value).strip()
    raw = re.split(r"(?:ext\.?|x|#)\s*\d+\s*$", raw, flags=re.IGNORECASE)[0]
    return re.sub(r"[^0-9]", "", raw)


def _is_operator_whatsapp_destination(value: str | None) -> bool:
    """Return True when a sender is the configured operator WhatsApp route.

    The Ignore List is for customers/senders the tenant does not want Marina to
    answer. It must never silence the operator alert destination: if the
    operator's own contact is imported into the list, inbound operator messages
    would otherwise be dropped before Marina can reply.
    """
    phone_norm = normalize_phone_identifier(value)
    if not phone_norm:
        return False
    try:
        settings = get_alert_settings(default_email_destination="")
        destination = (
            settings.get("channels", {})
            .get("whatsapp", {})
            .get("destination", "")
        )
    except Exception:
        return False
    return phone_norm == normalize_phone_identifier(destination)


def normalize_email_identifier(value: str | None) -> str:
    if not value:
        return ""
    raw = str(value).strip().lower()
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", raw):
        return ""
    return raw


def set_summary_dispatcher(fn):
    """Brief 227: register the summary generator (typically dashboard.api's
    _generate_escalation_summary)."""
    global _summary_dispatcher
    _summary_dispatcher = fn


def set_alert_dispatcher(fn):
    """Brief 217: dashboard.api registers _fire_escalation_alerts here at
    import time. Decoupled callback so state_registry doesn't import
    dashboard."""
    global _alert_dispatcher
    _alert_dispatcher = fn


def set_follow_up_alert_dispatcher(fn):
    """Register the provider-facing callback for prospect queue alerts."""
    global _follow_up_alert_dispatcher
    _follow_up_alert_dispatcher = fn


def dispatch_follow_up_alert(follow_up: dict,
                             previous_status: str | None = None) -> None:
    """Fire one best-effort alert when a prospect enters an actionable state.

    Repeated enrichment while the status is unchanged is deliberately silent,
    preventing a WhatsApp alert for every prospect message.
    """
    if not isinstance(follow_up, dict) or _follow_up_alert_dispatcher is None:
        return
    status = str(follow_up.get("status") or "")
    if status not in {"collecting", "ready_to_call", "needs_human_answer"}:
        return
    if previous_status == status:
        return
    try:
        _follow_up_alert_dispatcher(follow_up, previous_status)
    except Exception:
        # Alert delivery must never block the prospect conversation.
        pass


# Brief 241: optional callback set by dashboard.api at module-import time.
# dashboard.api registers _fire_appointment_alerts here so that
# appointment_upsert can fire alerts WITHOUT state_registry having to
# import dashboard.api (would create a circular import). When None,
# appointment alert dispatch is silently skipped.
_appointment_alert_dispatcher = None


def set_appointment_alert_dispatcher(fn):
    """Brief 241: dashboard.api registers _fire_appointment_alerts here at
    import time. Decoupled callback so state_registry doesn't import
    dashboard."""
    global _appointment_alert_dispatcher
    _appointment_alert_dispatcher = fn


def _summaries_materially_differ(old: dict, new: dict) -> bool:
    """Brief 239: compare two escalation_summary dicts; return True only
    if operator-relevant content has changed (proposed times, latest
    customer message, or what the customer wants). Used to suppress
    duplicate update alert emails when the summary regenerated but the
    situation didn't actually change for the operator.

    Returns True when the dicts differ on customerWants OR
    latestCustomerMessage OR extractedDetails.proposedTimes. Returns
    False when all three match. Returns True (defensive: fire alert) if
    either input is not a dict."""
    if not isinstance(old, dict) or not isinstance(new, dict):
        return True
    if old.get("customerWants") != new.get("customerWants"):
        return True
    if old.get("latestCustomerMessage") != new.get("latestCustomerMessage"):
        return True
    _o = (old.get("extractedDetails") or {}).get("proposedTimes") or []
    _n = (new.get("extractedDetails") or {}).get("proposedTimes") or []
    if list(_o) != list(_n):
        return True
    return False


def _get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    # Schema migration: rename trip_bookings → service_bookings + columns
    try:
        conn.execute("ALTER TABLE trip_bookings RENAME TO service_bookings")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE service_bookings RENAME COLUMN trip_key TO service_key")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE service_bookings RENAME COLUMN departure_time TO slot_time")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE manifest_events RENAME COLUMN trip_key TO service_key")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE manifest_events RENAME COLUMN departure_time TO slot_time")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE bookings RENAME COLUMN trip_key TO service_key")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE bookings RENAME COLUMN departure_time TO slot_time")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE photo_library RENAME COLUMN trip_key TO service_key")
    except sqlite3.OperationalError:
        pass
    conn.execute(
        "CREATE TABLE IF NOT EXISTS processed_hashes ("
        "hash TEXT PRIMARY KEY, "
        "created_at TEXT NOT NULL"
        ")"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS service_bookings ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "service_key TEXT NOT NULL, "
        "date TEXT NOT NULL, "
        "slot_time TEXT NOT NULL, "
        "guests INTEGER NOT NULL, "
        "booking_ref TEXT, "
        "status TEXT DEFAULT 'soft_hold', "
        "expires_at TEXT, "
        "created_at TEXT NOT NULL"
        ")"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_service_bookings_lookup "
        "ON service_bookings(service_key, date, slot_time, status)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS manifest_events ("
        "service_key TEXT NOT NULL, "
        "date TEXT NOT NULL, "
        "slot_time TEXT NOT NULL, "
        "calendar_id TEXT NOT NULL, "
        "event_id TEXT NOT NULL, "
        "html_link TEXT DEFAULT '', "
        "created_at TEXT NOT NULL, "
        "PRIMARY KEY (service_key, date, slot_time)"
        ")"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS bookings ("
        "booking_ref TEXT PRIMARY KEY, "
        "service_key TEXT, "
        "customer_name TEXT, "
        "customer_email TEXT, "
        "date TEXT, "
        "slot_time TEXT, "
        "guests INTEGER, "
        "special_requests TEXT, "
        "payment_link TEXT, "
        "event_link TEXT, "
        "status TEXT DEFAULT 'pending_payment', "
        "created_at TEXT NOT NULL"
        ")"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS whatsapp_processed ("
        "message_id TEXT PRIMARY KEY, "
        "created_at TEXT NOT NULL"
        ")"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS inbound_processing_events ("
        "message_id TEXT PRIMARY KEY, "
        "conversation_id TEXT NOT NULL DEFAULT '', "
        "channel TEXT NOT NULL DEFAULT '', "
        "status TEXT NOT NULL DEFAULT 'received', "
        "reason TEXT NOT NULL DEFAULT '', "
        "last_error TEXT NOT NULL DEFAULT '', "
        "created_at TEXT NOT NULL, "
        "updated_at TEXT NOT NULL"
        ")"
    )
    for column, definition in (
        ("payload_json", "TEXT NOT NULL DEFAULT '{}'") ,
        ("heartbeat_sent_at", "TEXT NOT NULL DEFAULT ''"),
        ("attempt_count", "INTEGER NOT NULL DEFAULT 0"),
        ("provider_retry_count", "INTEGER NOT NULL DEFAULT 0"),
        ("provider_retry_kind", "TEXT NOT NULL DEFAULT ''"),
        ("batch_id", "TEXT NOT NULL DEFAULT ''"),
        ("batch_position", "INTEGER NOT NULL DEFAULT 0"),
        ("acceptance_batch_id", "TEXT NOT NULL DEFAULT ''"),
        ("acceptance_position", "INTEGER NOT NULL DEFAULT 0"),
        ("outbound_idempotency_key", "TEXT NOT NULL DEFAULT ''"),
        ("outbound_attempted_at", "TEXT NOT NULL DEFAULT ''"),
        ("lease_expires_at", "TEXT NOT NULL DEFAULT ''"),
        ("processing_token", "TEXT NOT NULL DEFAULT ''"),
    ):
        try:
            conn.execute(
                f"ALTER TABLE inbound_processing_events ADD COLUMN {column} {definition}"
            )
        except sqlite3.OperationalError:
            pass
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_inbound_processing_conversation "
        "ON inbound_processing_events(conversation_id, updated_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_inbound_processing_batch "
        "ON inbound_processing_events(batch_id, batch_position)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS zernio_failed_event_queue ("
        "event_key TEXT PRIMARY KEY, "
        "account_id TEXT NOT NULL, "
        "conversation_id TEXT NOT NULL, "
        "message_id TEXT NOT NULL, "
        "payload_json TEXT NOT NULL, "
        "status TEXT NOT NULL DEFAULT 'pending', "
        "claim_token TEXT NOT NULL DEFAULT '', "
        "available_at TEXT NOT NULL DEFAULT '', "
        "lease_expires_at TEXT NOT NULL DEFAULT '', "
        "attempt_count INTEGER NOT NULL DEFAULT 0, "
        "last_error TEXT NOT NULL DEFAULT '', "
        "created_at TEXT NOT NULL, "
        "updated_at TEXT NOT NULL"
        ")"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_zernio_failed_event_due "
        "ON zernio_failed_event_queue(status, available_at, lease_expires_at)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS whatsapp_threads ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "phone TEXT NOT NULL, "
        "role TEXT NOT NULL, "
        "text TEXT NOT NULL, "
        "created_at TEXT NOT NULL"
        ")"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_whatsapp_threads_phone "
        "ON whatsapp_threads(phone, created_at)"
    )
    # Schema migration: add channel + sender_name columns to whatsapp_threads
    try:
        conn.execute("ALTER TABLE whatsapp_threads ADD COLUMN channel TEXT DEFAULT 'whatsapp'")
    except sqlite3.OperationalError:
        pass  # Column already exists
    try:
        conn.execute("ALTER TABLE whatsapp_threads ADD COLUMN sender_name TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass  # Column already exists
    try:
        conn.execute(
            "ALTER TABLE whatsapp_threads ADD COLUMN source_message_key TEXT DEFAULT ''"
        )
    except sqlite3.OperationalError:
        pass
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_whatsapp_threads_source_message "
        "ON whatsapp_threads(phone, source_message_key) "
        "WHERE source_message_key != ''"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_whatsapp_threads_channel "
        "ON whatsapp_threads(channel, phone, created_at)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS whatsapp_booking_state ("
        "phone TEXT PRIMARY KEY, "
        "fields_json TEXT DEFAULT '{}', "
        "flags_json TEXT DEFAULT '{}', "
        "completed_bookings_json TEXT DEFAULT '[]', "
        "last_activity TEXT NOT NULL, "
        "created_at TEXT NOT NULL"
        ")"
    )
    # Schema migration: rename field names in whatsapp_booking_state JSON blobs
    try:
        _rows = conn.execute("SELECT phone, fields_json, flags_json FROM whatsapp_booking_state").fetchall()
        _renames = {"trip_key": "service_key", "experience": "service_name", "departure_time": "slot_time"}
        _flag_renames = {"hold_trip_key": "hold_service_key", "hold_departure_time": "hold_slot_time"}
        for _phone, _fj, _flj in _rows:
            _fields = json.loads(_fj or "{}")
            _flags = json.loads(_flj or "{}")
            _changed = False
            for _old, _new in _renames.items():
                if _old in _fields:
                    _fields[_new] = _fields.pop(_old)
                    _changed = True
            for _old, _new in _flag_renames.items():
                if _old in _flags:
                    _flags[_new] = _flags.pop(_old)
                    _changed = True
            if _changed:
                conn.execute("UPDATE whatsapp_booking_state SET fields_json = ?, flags_json = ? WHERE phone = ?",
                             (json.dumps(_fields), json.dumps(_flags), _phone))
        if _rows:
            conn.commit()
    except Exception:
        pass  # Table might not exist yet on fresh DB
    # Brief 166: cross-channel customer file
    conn.execute(
        "CREATE TABLE IF NOT EXISTS customers ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "display_name TEXT DEFAULT '', "
        "summary TEXT DEFAULT '', "
        "notes TEXT DEFAULT '', "
        "first_seen TEXT NOT NULL, "
        "last_seen TEXT NOT NULL, "
        "active INTEGER DEFAULT 1"
        ")"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS customer_identifiers ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "customer_id INTEGER NOT NULL, "
        "type TEXT NOT NULL, "
        "value TEXT NOT NULL, "
        "first_seen TEXT NOT NULL, "
        "FOREIGN KEY (customer_id) REFERENCES customers(id)"
        ")"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_customer_identifiers_type_value "
        "ON customer_identifiers(type, value)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_customer_identifiers_customer "
        "ON customer_identifiers(customer_id)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS customer_interactions ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "customer_id INTEGER NOT NULL, "
        "channel TEXT NOT NULL, "
        "summary TEXT NOT NULL, "
        "created_at TEXT NOT NULL, "
        "FOREIGN KEY (customer_id) REFERENCES customers(id)"
        ")"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_customer_interactions_customer "
        "ON customer_interactions(customer_id, created_at)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS customer_merges ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "surviving_id INTEGER NOT NULL, "
        "absorbed_id INTEGER NOT NULL, "
        "merged_at TEXT NOT NULL"
        ")"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS pending_notifications ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "notification_type TEXT NOT NULL, "
        "relay_token TEXT UNIQUE, "
        "channel TEXT NOT NULL, "
        "customer_id TEXT NOT NULL, "
        "customer_name TEXT DEFAULT '', "
        "subject TEXT NOT NULL, "
        "body TEXT NOT NULL, "
        "status TEXT DEFAULT 'pending', "
        "created_at TEXT NOT NULL, "
        "content_revision INTEGER NOT NULL DEFAULT 1, "
        "email_thread_key TEXT NOT NULL DEFAULT '', "
        "email_reply_subject TEXT NOT NULL DEFAULT ''"
        ")"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS conversation_status ("
        "conversation_id TEXT PRIMARY KEY, "
        "channel TEXT NOT NULL DEFAULT 'whatsapp', "
        "status TEXT NOT NULL DEFAULT 'pending', "
        "updated_at TEXT NOT NULL"
        ")"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS inbound_operator_notifications ("
        "action_key TEXT PRIMARY KEY, "
        "notification_id INTEGER NOT NULL, "
        "created_at TEXT NOT NULL"
        ")"
    )
    # Callback follow-ups are intentionally stored in each tenant's own
    # registry database.  A conversation may only have one active request;
    # later messages enrich the same record instead of creating duplicates.
    conn.execute(
        "CREATE TABLE IF NOT EXISTS follow_up_requests ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "conversation_id TEXT NOT NULL UNIQUE, "
        "channel TEXT NOT NULL DEFAULT 'whatsapp', "
        "first_name TEXT DEFAULT '', "
        "surnames TEXT DEFAULT '', "
        "phone_raw TEXT DEFAULT '', "
        "phone_normalized TEXT DEFAULT '', "
        "callback_preference TEXT DEFAULT '', "
        "visit_reason TEXT DEFAULT '', "
        "status TEXT NOT NULL DEFAULT 'collecting', "
        "handoff_reason TEXT DEFAULT '', "
        "source_message_id TEXT DEFAULT '', "
        "created_at TEXT NOT NULL, "
        "updated_at TEXT NOT NULL, "
        "closed_at TEXT"
        ")"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_follow_up_requests_status_updated "
        "ON follow_up_requests(status, updated_at DESC)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS ignored_contacts ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "tenant_id TEXT NOT NULL DEFAULT '', "
        "name TEXT DEFAULT '', "
        "phone_original TEXT DEFAULT '', "
        "phone_normalized TEXT DEFAULT '', "
        "email_original TEXT DEFAULT '', "
        "email_normalized TEXT DEFAULT '', "
        "channel TEXT DEFAULT '', "
        "external_sender_id TEXT DEFAULT '', "
        "label TEXT DEFAULT '', "
        "note TEXT DEFAULT '', "
        "created_by TEXT DEFAULT '', "
        "created_at TEXT NOT NULL, "
        "updated_at TEXT NOT NULL, "
        "deleted_at TEXT"
        ")"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ignored_contacts_phone "
        "ON ignored_contacts(tenant_id, phone_normalized)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ignored_contacts_email "
        "ON ignored_contacts(tenant_id, email_normalized)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ignored_contacts_external "
        "ON ignored_contacts(tenant_id, channel, external_sender_id)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS ignored_contact_events ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "tenant_id TEXT NOT NULL DEFAULT '', "
        "ignored_contact_id INTEGER, "
        "channel TEXT DEFAULT '', "
        "sender_identifier TEXT DEFAULT '', "
        "message_id TEXT DEFAULT '', "
        "reason TEXT DEFAULT '', "
        "created_at TEXT NOT NULL"
        ")"
    )
    # Brief 213: pending_notifications.mode (per-escalation soft/hard)
    try:
        conn.execute("ALTER TABLE pending_notifications ADD COLUMN mode TEXT")
    except sqlite3.OperationalError:
        pass
    # Brief 227: structured escalation summary as JSON. Generated by Claude
    # at escalation-create time (best-effort — null if generation fails).
    try:
        conn.execute(
            "ALTER TABLE pending_notifications "
            "ADD COLUMN escalation_summary TEXT"
        )
    except sqlite3.OperationalError:
        pass
    for _email_column in (
        "email_thread_key TEXT NOT NULL DEFAULT ''",
        "email_reply_subject TEXT NOT NULL DEFAULT ''",
        "content_revision INTEGER NOT NULL DEFAULT 1",
    ):
        try:
            conn.execute(
                "ALTER TABLE pending_notifications ADD COLUMN " + _email_column
            )
        except sqlite3.OperationalError:
            pass
    # Brief 213: conversation_status.ai_muted (per-conversation human takeover flag)
    try:
        conn.execute("ALTER TABLE conversation_status ADD COLUMN ai_muted INTEGER NOT NULL DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE conversation_status ADD COLUMN ai_mute_source TEXT NOT NULL DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    # Brief 213: conversation_status.human_takeover_at (ISO timestamp when muted)
    try:
        conn.execute("ALTER TABLE conversation_status ADD COLUMN human_takeover_at TEXT")
    except sqlite3.OperationalError:
        pass
    # Brief 220: conversation_status.blocked (per-conversation drop flag,
    # operator-controlled via dashboard). Different from ai_muted: blocked
    # drops the inbound BEFORE any storage so the conversation doesn't
    # appear in the inbox at all; ai_muted stores then skips Marina so
    # operator still sees it.
    try:
        conn.execute("ALTER TABLE conversation_status ADD COLUMN blocked INTEGER NOT NULL DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    # Brief 261: conversation_status.reason + blocked_by (audit fields
    # for the Block sender flow per issue #30). Both nullable TEXT;
    # cleared on unblock. Idempotent ALTER for safe re-execution on
    # existing tenant DBs.
    try:
        conn.execute("ALTER TABLE conversation_status ADD COLUMN reason TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE conversation_status ADD COLUMN blocked_by TEXT")
    except sqlite3.OperationalError:
        pass
    # Brief 249: conversation_status.deleted (archived state for the
    # WhatsApp/IG/FB inbox). Brief 237 introduced read+write of this
    # column without a migration -- silently broken since it shipped.
    # Brief 249 adds the missing migration so manual archive endpoints
    # AND Brief 237's bulk archive sweep both work.
    try:
        conn.execute("ALTER TABLE conversation_status ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    # Brief 207: tasks shared between Calvin and Jr (operator-side workflow,
    # not customer-facing). Per-tenant SQLite isolation matches existing tables.
    conn.execute(
        "CREATE TABLE IF NOT EXISTS tasks ("
        "id TEXT PRIMARY KEY, "
        "body_html TEXT NOT NULL DEFAULT '', "
        "body_text TEXT NOT NULL DEFAULT '', "
        "created_by TEXT NOT NULL, "
        "assigned_to TEXT NOT NULL, "
        "status TEXT NOT NULL DEFAULT 'open', "
        "completed_at TEXT, "
        "completed_by TEXT, "
        "created_at TEXT NOT NULL, "
        "updated_at TEXT NOT NULL"
        ")"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS task_attachments ("
        "id TEXT PRIMARY KEY, "
        "task_id TEXT NOT NULL, "
        "file_name TEXT NOT NULL, "
        "mime_type TEXT NOT NULL, "
        "size_bytes INTEGER NOT NULL, "
        "stored_filename TEXT NOT NULL, "
        "created_at TEXT NOT NULL, "
        "FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE"
        ")"
    )
    # Brief 223: tasks.task_number (per-workspace stable integer for the
    # TASK-### badge SR's frontend displays). Idempotent ALTER + backfill
    # of pre-existing rows in chronological order so the oldest task is
    # TASK-001. Placed AFTER the CREATE TABLE tasks block so the ALTER
    # has a target on first init. Backfill runs on every _get_conn() call
    # (matching the existing per-connection schema-init pattern); after
    # the first run the SELECT returns zero rows and the if-guard
    # short-circuits.
    try:
        conn.execute("ALTER TABLE tasks ADD COLUMN task_number INTEGER")
    except sqlite3.OperationalError:
        pass
    to_backfill = conn.execute(
        "SELECT id FROM tasks WHERE task_number IS NULL ORDER BY created_at ASC"
    ).fetchall()
    if to_backfill:
        cur_max = conn.execute(
            "SELECT COALESCE(MAX(task_number), 0) FROM tasks"
        ).fetchone()[0]
        for offset, (row_id,) in enumerate(to_backfill, start=1):
            conn.execute(
                "UPDATE tasks SET task_number = ? WHERE id = ?",
                (cur_max + offset, row_id))
        conn.commit()
    conn.execute(
        "CREATE TABLE IF NOT EXISTS content_drafts ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "content_class TEXT NOT NULL, "
        "instagram_caption TEXT, "
        "facebook_caption TEXT, "
        "twitter_caption TEXT DEFAULT '', "
        "hashtags_json TEXT DEFAULT '[]', "
        "visual_suggestion TEXT DEFAULT '', "
        "reasoning TEXT DEFAULT '', "
        "status TEXT DEFAULT 'pending', "
        "rejection_reason TEXT DEFAULT '', "
        "created_at TEXT NOT NULL, "
        "approved_at TEXT, "
        "published_at TEXT"
        ")"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS content_learnings ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "rule TEXT NOT NULL, "
        "source_draft_ids TEXT DEFAULT '[]', "
        "active INTEGER DEFAULT 1, "
        "created_at TEXT NOT NULL"
        ")"
    )
    # Brief 215: escalation-derived learning entries (operator answers stored
    # as approved knowledge for Marina to reuse in future similar replies).
    # Distinct from content_learnings (content_agent's draft rules).
    conn.execute(
        "CREATE TABLE IF NOT EXISTS escalation_learnings ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "conversation_id TEXT NOT NULL, "
        "channel TEXT NOT NULL, "
        "source_question TEXT NOT NULL DEFAULT '', "
        "human_answer TEXT NOT NULL, "
        "status TEXT NOT NULL DEFAULT 'approved', "
        "ai_may_use_automatically INTEGER NOT NULL DEFAULT 1, "
        "category TEXT, "
        "created_by TEXT, "
        "created_at TEXT NOT NULL, "
        "updated_at TEXT NOT NULL"
        ")"
    )
    # Brief 263: operator-approval audit fields per issue #32.
    # Idempotent; nullable TEXT; populated only on the matching transition.
    try:
        conn.execute("ALTER TABLE escalation_learnings ADD COLUMN approved_at TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE escalation_learnings ADD COLUMN dismissed_at TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE escalation_learnings ADD COLUMN approved_by TEXT")
    except sqlite3.OperationalError:
        pass
    # Brief 216: per-tenant temporary/permanent business updates that Marina
    # injects into her prompt. Two flavors: permanent (no dates → always
    # active) and scheduled (start_date + end_date → active only within
    # the window). Type enum matches SR's product contract Section 5.
    conn.execute(
        "CREATE TABLE IF NOT EXISTS info_updates ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "type TEXT NOT NULL DEFAULT 'general', "
        "text TEXT NOT NULL, "
        "active INTEGER NOT NULL DEFAULT 1, "
        "start_date TEXT, "
        "end_date TEXT, "
        "created_at TEXT NOT NULL, "
        "updated_at TEXT NOT NULL"
        ")"
    )
    # Brief 217: per-tenant alert settings (singleton row, fixed id=1).
    conn.execute(
        "CREATE TABLE IF NOT EXISTS alert_settings ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "email_enabled INTEGER NOT NULL DEFAULT 1, "
        "email_destination TEXT NOT NULL DEFAULT '', "
        "whatsapp_enabled INTEGER NOT NULL DEFAULT 0, "
        "whatsapp_destination TEXT NOT NULL DEFAULT '', "
        "telegram_enabled INTEGER NOT NULL DEFAULT 0, "
        "telegram_destination TEXT NOT NULL DEFAULT '', "
        "messenger_enabled INTEGER NOT NULL DEFAULT 0, "
        "messenger_destination TEXT NOT NULL DEFAULT '', "
        "updated_at TEXT NOT NULL DEFAULT ''"
        ")"
    )
    # Brief 226: alternative email destination for escalation alerts. Optional
    # second recipient that receives a copy of every email alert. ALTER instead
    # of expanding CREATE TABLE so existing tenant DBs migrate without a drop.
    try:
        conn.execute(
            "ALTER TABLE alert_settings "
            "ADD COLUMN email_alternative_destination TEXT NOT NULL DEFAULT ''"
        )
    except sqlite3.OperationalError:
        pass  # column already exists
    # Brief 240: Zernio-route fields for operator WhatsApp alerts. The
    # user-facing whatsapp_destination stays as the displayed phone (e.g.,
    # "+351963618003"); these three columns capture the Zernio
    # conversation_id + account_id needed for outbound delivery, populated
    # automatically by the auto-resolve hook in webhook_server when the
    # operator sends a bootstrap inbound from that number.
    for _coldef in (
        "ADD COLUMN whatsapp_zernio_conversation_id TEXT",
        "ADD COLUMN whatsapp_zernio_account_id TEXT",
        "ADD COLUMN whatsapp_zernio_resolved_at TEXT",
    ):
        try:
            conn.execute(f"ALTER TABLE alert_settings {_coldef}")
        except sqlite3.OperationalError:
            pass  # column already exists
    # Brief 241: alert_type + appointment_id columns on alert_deliveries.
    # Existing rows (Brief 217-240 era) get retro-labeled as 'escalation'
    # via the DEFAULT - semantically correct since they were all
    # escalation-alert deliveries. appointment_id stays NULL for those rows.
    for _coldef in (
        "ADD COLUMN alert_type TEXT NOT NULL DEFAULT 'escalation'",
        "ADD COLUMN appointment_id INTEGER",
    ):
        try:
            conn.execute(f"ALTER TABLE alert_deliveries {_coldef}")
        except sqlite3.OperationalError:
            pass  # column already exists
    # Brief 241: per-alert-type enable flags on alert_settings. Both
    # default ON for backward compat - existing tenants continue to receive
    # escalation alerts; appointment alerts begin firing once the trigger
    # (appointment_upsert transition-to-confirmed) is reached.
    for _coldef in (
        "ADD COLUMN alert_type_escalation_enabled INTEGER NOT NULL DEFAULT 1",
        "ADD COLUMN alert_type_appointment_enabled INTEGER NOT NULL DEFAULT 1",
    ):
        try:
            conn.execute(f"ALTER TABLE alert_settings {_coldef}")
        except sqlite3.OperationalError:
            pass  # column already exists
    # Brief 217: append-only audit log of alert delivery attempts.
    conn.execute(
        "CREATE TABLE IF NOT EXISTS alert_deliveries ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "escalation_id INTEGER, "
        "channel TEXT NOT NULL, "
        "destination TEXT NOT NULL DEFAULT '', "
        "status TEXT NOT NULL, "
        "error TEXT, "
        "sent_at TEXT NOT NULL"
        ")"
    )
    # Brief 228: appointments — derived from escalation summaries when
    # intent=='scheduling'. One row per conversation_id (upsert on duplicate).
    conn.execute(
        "CREATE TABLE IF NOT EXISTS appointments ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "conversation_id TEXT NOT NULL UNIQUE, "
        "channel TEXT NOT NULL, "
        "customer_name TEXT NOT NULL DEFAULT '', "
        "title TEXT NOT NULL DEFAULT '', "
        "date_time_label TEXT NOT NULL DEFAULT '', "
        "proposed_times_json TEXT NOT NULL DEFAULT '[]', "
        "location TEXT NOT NULL DEFAULT '', "
        "status TEXT NOT NULL DEFAULT 'detected', "
        "source TEXT NOT NULL DEFAULT 'conversation', "
        "created_at TEXT NOT NULL, "
        "updated_at TEXT NOT NULL"
        ")"
    )
    # Brief 229: data retention settings (singleton row, fixed id=1).
    # Active inbox archive threshold + archive retention + end-of-retention
    # action + keep-approved-learnings + audit log retention.
    conn.execute(
        "CREATE TABLE IF NOT EXISTS data_retention_settings ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "active_inbox_archive_after_days INTEGER, "
        "archive_retention_months INTEGER, "
        "end_of_retention_action TEXT NOT NULL DEFAULT 'anonymize', "
        "keep_approved_learnings INTEGER NOT NULL DEFAULT 1, "
        "audit_log_retention_months INTEGER NOT NULL DEFAULT 24, "
        "updated_at TEXT NOT NULL DEFAULT ''"
        ")"
    )
    # Brief 237: data retention audit log. Records every archive-now /
    # export / delete-customer-data attempt (success AND blocked). Rule 10
    # of SR's task ab7d8f1eb97c: "Do not silently delete — log retention
    # actions."
    conn.execute(
        "CREATE TABLE IF NOT EXISTS data_retention_audit_log ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "action TEXT NOT NULL, "
        "identifier_type TEXT, "
        "identifier_value TEXT, "
        "affected_counts_json TEXT, "
        "actor TEXT, "
        "created_at TEXT NOT NULL"
        ")"
    )
    # Brief 230: knowledge files (uploaded reference docs Marina reads when
    # features.knowledge_files_in_prompt is true). One row per file. Text is
    # extracted synchronously at upload time and stored here; the actual
    # uploaded file lives on disk under wtyj/data/knowledge/.
    conn.execute(
        "CREATE TABLE IF NOT EXISTS knowledge_files ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "filename TEXT NOT NULL, "
        "stored_filename TEXT NOT NULL, "
        "mime_type TEXT NOT NULL DEFAULT '', "
        "size_bytes INTEGER NOT NULL DEFAULT 0, "
        "status TEXT NOT NULL DEFAULT 'pending', "
        "extracted_text TEXT NOT NULL DEFAULT '', "
        "failure_reason TEXT NOT NULL DEFAULT '', "
        "uploaded_at TEXT NOT NULL, "
        "last_used_at TEXT"
        ")"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS photo_library ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "filename TEXT NOT NULL, "
        "original_filename TEXT NOT NULL, "
        "tags_json TEXT DEFAULT '[]', "
        "service_key TEXT DEFAULT '', "
        "source TEXT DEFAULT 'upload', "
        "source_id TEXT DEFAULT '', "
        "width INTEGER DEFAULT 0, "
        "height INTEGER DEFAULT 0, "
        "file_size INTEGER DEFAULT 0, "
        "used_count INTEGER DEFAULT 0, "
        "uploaded_at TEXT NOT NULL"
        ")"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS oauth_tokens ("
        "provider TEXT PRIMARY KEY, "
        "access_token TEXT NOT NULL, "
        "refresh_token TEXT NOT NULL, "
        "expires_at TEXT, "
        "folder_id TEXT DEFAULT '', "
        "updated_at TEXT NOT NULL"
        ")"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS training_examples ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "caption_text TEXT NOT NULL, "
        "image_path TEXT DEFAULT '', "
        "platform TEXT DEFAULT '', "
        "created_at TEXT NOT NULL"
        ")"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS brand_profile ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "category TEXT NOT NULL, "
        "rule TEXT NOT NULL, "
        "source TEXT DEFAULT 'analysis', "
        "active INTEGER DEFAULT 1, "
        "created_at TEXT NOT NULL"
        ")"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS system_settings ("
        "key TEXT PRIMARY KEY, "
        "value TEXT NOT NULL"
        ")"
    )
    # Brief 262: tenant Source of Truth blob. Single row per tenant
    # (each container has its own DB file -> tenant isolation by
    # construction). blocks_json carries the entire SotBlock[] array
    # the dashboard editor renders.
    conn.execute(
        "CREATE TABLE IF NOT EXISTS source_of_truth ("
        "id INTEGER PRIMARY KEY, "
        "blocks_json TEXT NOT NULL DEFAULT '[]', "
        "updated_at TEXT NOT NULL DEFAULT ''"
        ")"
    )
    try:
        conn.execute("ALTER TABLE content_drafts ADD COLUMN image_path TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE content_drafts ADD COLUMN late_post_id TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE content_drafts ADD COLUMN instagram_url TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE content_drafts ADD COLUMN photo_id INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schedule_slots ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "day_of_week TEXT NOT NULL, "
        "time_utc TEXT NOT NULL, "
        "active INTEGER DEFAULT 1, "
        "created_at TEXT NOT NULL"
        ")"
    )
    try:
        conn.execute("ALTER TABLE content_drafts ADD COLUMN scheduled_at TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE content_drafts ADD COLUMN platforms_json TEXT DEFAULT '[\"instagram\"]'")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE content_drafts ADD COLUMN facebook_url TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE content_drafts ADD COLUMN late_facebook_post_id TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE content_drafts ADD COLUMN twitter_caption TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE service_bookings ADD COLUMN customer_name TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    # Brief 168: payment hold state machine
    try:
        conn.execute("ALTER TABLE service_bookings ADD COLUMN payment_expires_at TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE service_bookings ADD COLUMN payment_reminder_sent_at TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE service_bookings ADD COLUMN customer_phone TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE service_bookings ADD COLUMN customer_email TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    return conn


def generate_content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def has_been_processed(content: str) -> bool:
    content_hash = generate_content_hash(content)
    conn = _get_conn()
    row = conn.execute(
        "SELECT count(*) FROM processed_hashes WHERE hash = ?",
        (content_hash,)
    ).fetchone()
    conn.close()
    return row[0] > 0


def mark_as_processed(content: str):
    content_hash = generate_content_hash(content)
    conn = _get_conn()
    conn.execute(
        "INSERT OR IGNORE INTO processed_hashes (hash, created_at) VALUES (?, ?)",
        (content_hash, datetime.now(timezone.utc).isoformat())
    )
    conn.commit()
    conn.close()


def expire_stale_holds() -> int:
    """Set status='expired' for soft_hold rows past their expires_at. Returns count updated."""
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        "UPDATE service_bookings SET status='expired' "
        "WHERE status='soft_hold' AND expires_at < ?",
        (now,)
    )
    count = cur.rowcount
    conn.commit()
    conn.close()
    return count


def get_spots_remaining(service_key: str, date: str, slot_time: str, capacity: int) -> int:
    """Return capacity minus guests already in soft_hold (non-expired) or confirmed for this slot."""
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()
    row = conn.execute(
        "SELECT COALESCE(SUM(guests), 0) FROM service_bookings "
        "WHERE service_key=? AND date=? AND slot_time=? "
        "AND status IN ('soft_hold', 'confirmed') "
        "AND (status='confirmed' OR expires_at > ?)",
        (service_key, date, slot_time, now)
    ).fetchone()
    conn.close()
    used = row[0] if row else 0
    return max(0, capacity - used)


def create_soft_hold(
    service_key: str, date: str, slot_time: str, guests: int, capacity: int,
    customer_name: str = "", customer_email: str = ""
) -> "int | None":
    """
    Atomic: expire stale holds, check remaining capacity, insert soft_hold with 24h TTL.
    Returns the new row id (hold_id) on success, None if at capacity or on error.
    Uses BEGIN IMMEDIATE to serialise concurrent inserts.
    """
    conn = _get_conn()
    conn.isolation_level = None  # switch to manual commit/rollback for BEGIN IMMEDIATE
    now = datetime.now(timezone.utc).isoformat()
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "UPDATE service_bookings SET status='expired' "
            "WHERE status='soft_hold' AND expires_at < ?",
            (now,)
        )
        row = conn.execute(
            "SELECT COALESCE(SUM(guests), 0) FROM service_bookings "
            "WHERE service_key=? AND date=? AND slot_time=? "
            "AND status IN ('soft_hold', 'confirmed') "
            "AND (status='confirmed' OR expires_at > ?)",
            (service_key, date, slot_time, now)
        ).fetchone()
        used = row[0] if row else 0
        if used + guests > capacity:
            conn.execute("COMMIT")
            conn.close()
            return None
        cur = conn.execute(
            "INSERT INTO service_bookings "
            "(service_key, date, slot_time, guests, status, expires_at, created_at, "
            "customer_name, customer_email) "
            "VALUES (?, ?, ?, ?, 'soft_hold', ?, ?, ?, ?)",
            (service_key, date, slot_time, guests, expires_at, now,
             customer_name, customer_email)
        )
        hold_id = cur.lastrowid
        conn.execute("COMMIT")
        conn.close()
        return hold_id
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        conn.close()
        return None


def confirm_hold(hold_id: int) -> bool:
    """Upgrade a soft_hold to confirmed. Clears expires_at. Returns True if row was updated."""
    conn = _get_conn()
    cur = conn.execute(
        "UPDATE service_bookings SET status='confirmed', expires_at=NULL "
        "WHERE id=? AND status='soft_hold'",
        (hold_id,)
    )
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    return changed


def cancel_hold(hold_id: int) -> bool:
    """Mark a hold as cancelled. Returns True if row was updated."""
    conn = _get_conn()
    cur = conn.execute(
        "UPDATE service_bookings SET status='cancelled' WHERE id=?",
        (hold_id,)
    )
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    return changed


def set_booking_ref(hold_id: int, booking_ref: str) -> bool:
    """Set booking_ref on a service_bookings row. Returns True if row was updated."""
    conn = _get_conn()
    cur = conn.execute(
        "UPDATE service_bookings SET booking_ref=? WHERE id=?",
        (booking_ref, hold_id)
    )
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    return changed


def get_manifest_event(service_key: str, date: str, slot_time: str):
    """Returns dict {service_key, date, slot_time, calendar_id, event_id, html_link} or None."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT service_key, date, slot_time, calendar_id, event_id, html_link "
        "FROM manifest_events WHERE service_key=? AND date=? AND slot_time=?",
        (service_key, date, slot_time)
    ).fetchone()
    conn.close()
    if not row:
        return None
    return {
        "service_key": row[0], "date": row[1], "slot_time": row[2],
        "calendar_id": row[3], "event_id": row[4], "html_link": row[5],
    }


def save_manifest_event(service_key: str, date: str, slot_time: str,
                        calendar_id: str, event_id: str, html_link: str) -> None:
    """INSERT OR REPLACE into manifest_events."""
    conn = _get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO manifest_events "
        "(service_key, date, slot_time, calendar_id, event_id, html_link, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (service_key, date, slot_time, calendar_id, event_id, html_link,
         datetime.now(timezone.utc).isoformat())
    )
    conn.commit()
    conn.close()


def delete_manifest_event(service_key: str, date: str, slot_time: str) -> bool:
    """Delete manifest_events row for this slot. Returns True if row existed."""
    conn = _get_conn()
    cur = conn.execute(
        "DELETE FROM manifest_events WHERE service_key=? AND date=? AND slot_time=?",
        (service_key, date, slot_time)
    )
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    return changed


def get_slot_passengers(service_key: str, date: str, slot_time: str) -> list:
    """Return all active bookings for this slot (soft_hold non-expired + confirmed).
    Each item: {id, guests, booking_ref, status, customer_name, customer_email, created_at}."""
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()
    rows = conn.execute(
        "SELECT id, guests, booking_ref, status, customer_name, customer_email, created_at "
        "FROM service_bookings "
        "WHERE service_key=? AND date=? AND slot_time=? "
        "AND status IN ('soft_hold', 'confirmed') "
        "AND (status='confirmed' OR expires_at > ?) "
        "ORDER BY created_at ASC",
        (service_key, date, slot_time, now)
    ).fetchall()
    conn.close()
    return [
        {
            "id": r[0], "guests": r[1], "booking_ref": r[2] or "",
            "status": r[3], "customer_name": r[4] or "", "customer_email": r[5] or "",
            "created_at": r[6],
        }
        for r in rows
    ]


def save_booking(booking_ref: str, fields: dict, flags: dict,
                 customer_email: str = "") -> None:
    """Upsert a booking record after hold creation success."""
    conn = _get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO bookings "
        "(booking_ref, service_key, customer_name, customer_email, date, "
        "slot_time, guests, special_requests, payment_link, event_link, "
        "status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            booking_ref,
            fields.get("service_key", ""),
            fields.get("customer_name", ""),
            customer_email.strip().lower() if customer_email else "",
            fields.get("date", ""),
            fields.get("slot_time", ""),
            int(fields.get("guests") or 0),
            fields.get("special_requests", ""),
            flags.get("payment_link", ""),
            flags.get("event_link", ""),
            "confirmed",
            datetime.now(timezone.utc).isoformat(),
        )
    )
    conn.commit()
    conn.close()


def get_bookings_by_email(customer_email: str) -> list:
    """Return all bookings for a customer email, newest first."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT booking_ref, service_key, customer_name, customer_email, date, "
        "slot_time, guests, special_requests, payment_link, event_link, "
        "status, created_at "
        "FROM bookings WHERE customer_email = ? ORDER BY created_at DESC",
        (customer_email.strip().lower(),)
    ).fetchall()
    conn.close()
    return [{"booking_ref": r[0], "service_key": r[1], "customer_name": r[2],
             "customer_email": r[3], "date": r[4], "slot_time": r[5],
             "guests": r[6], "special_requests": r[7], "payment_link": r[8],
             "event_link": r[9], "status": r[10], "created_at": r[11]} for r in rows]


def get_booking(booking_ref: str) -> "dict | None":
    """Return full booking dict by ref, or None if not found."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT booking_ref, service_key, customer_name, customer_email, date, "
        "slot_time, guests, special_requests, payment_link, event_link, "
        "status, created_at "
        "FROM bookings WHERE booking_ref = ?",
        (booking_ref,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    return {
        "booking_ref": row[0], "service_key": row[1], "customer_name": row[2],
        "customer_email": row[3], "date": row[4], "slot_time": row[5],
        "guests": row[6], "special_requests": row[7], "payment_link": row[8],
        "event_link": row[9], "status": row[10], "created_at": row[11],
    }


def wa_has_been_processed(message_id: str) -> bool:
    """Check if a WhatsApp message ID has already been processed."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT 1 FROM whatsapp_processed WHERE message_id = ?",
        (message_id,)
    ).fetchone()
    conn.close()
    return row is not None


def wa_mark_as_processed(message_id: str):
    """Record a WhatsApp message ID as processed."""
    conn = _get_conn()
    conn.execute(
        "INSERT OR IGNORE INTO whatsapp_processed (message_id, created_at) VALUES (?, ?)",
        (message_id, datetime.now(timezone.utc).isoformat())
    )
    conn.commit()
    conn.close()


def wa_claim_inbound_processing(
    message_id: str,
    conversation_id: str,
    channel: str,
    payload: dict | None = None,
    *,
    acceptance_batch_id: str = "",
    acceptance_position: int = 0,
) -> bool:
    """Atomically claim a provider event and preserve its recovery payload.

    Returns ``True`` only for the first delivery.  The legacy processed marker
    and durable inbound ledger row share one SQLite transaction, so a crash or
    write failure cannot consume a provider message ID without leaving enough
    data for recovery.
    """
    if not message_id:
        return False
    now = datetime.now(timezone.utc).isoformat()
    serialized_payload = json.dumps(
        payload or {}, ensure_ascii=False, separators=(",", ":"),
    )
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        from shared.mermaid_maintenance import admit
        admit(conn, message_id)
        claimed = conn.execute(
            "INSERT OR IGNORE INTO whatsapp_processed (message_id, created_at) "
            "VALUES (?, ?)",
            (message_id, now),
        )
        if claimed.rowcount == 0:
            conn.rollback()
            return False
        recorded = conn.execute(
            "INSERT OR IGNORE INTO inbound_processing_events "
            "(message_id, conversation_id, channel, status, reason, last_error, "
            "payload_json, batch_id, batch_position, "
            "acceptance_batch_id, acceptance_position, "
            "created_at, updated_at) "
            "VALUES (?, ?, ?, 'received', '', '', ?, ?, ?, ?, ?, ?, ?)",
            (
                message_id,
                conversation_id or "",
                channel or "",
                serialized_payload,
                str(acceptance_batch_id or ""),
                max(0, int(acceptance_position or 0)),
                str(acceptance_batch_id or ""),
                max(0, int(acceptance_position or 0)),
                now,
                now,
            ),
        )
        if recorded.rowcount == 0:
            # A durable ledger row already exists. Roll back the marker inserted
            # above and treat this delivery as a replay.
            conn.rollback()
            return False
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _inbound_lease_deadline(seconds: float) -> str:
    return (
        datetime.now(timezone.utc)
        + timedelta(seconds=max(0.0, float(seconds)))
    ).isoformat()


def _inbound_processing_batch_id(message_id: str) -> str:
    """Return the stable opaque identity for a batch's first provider event."""
    return hashlib.sha256(
        ("whatsapp-inbound-batch-v1\x1f" + str(message_id)).encode("utf-8")
    ).hexdigest()


def _inbound_processing_token() -> str:
    """Return an unguessable generation fence for one batch worker."""
    return hashlib.sha256(os.urandom(32)).hexdigest()


def inbound_processing_extend_batch_lease(
    batch_id: str,
    lease_seconds: float,
) -> int:
    """Keep every still-buffered member leased through one batch deadline."""
    normalized_batch_id = str(batch_id or "").strip()
    if not normalized_batch_id:
        return 0
    deadline = _inbound_lease_deadline(lease_seconds)
    conn = _get_conn()
    try:
        cur = conn.execute(
            "UPDATE inbound_processing_events "
            "SET lease_expires_at = CASE "
            "WHEN lease_expires_at = '' OR lease_expires_at < ? THEN ? "
            "ELSE lease_expires_at END "
            "WHERE batch_id = ? AND status = 'received'",
            (deadline, deadline, normalized_batch_id),
        )
        conn.commit()
        return int(cur.rowcount or 0)
    finally:
        conn.close()


def inbound_processing_ordered_batch_ids(
    message_ids: list[str],
    batch_id: str,
) -> list[str] | None:
    """Return exact durable membership ordered by ``batch_position``.

    ``None`` means the in-memory snapshot is partial, duplicated, or otherwise
    differs from the durable action.  Callers must fail closed and leave the
    non-terminal rows for whole-batch recovery.
    """
    ids = [str(message_id) for message_id in message_ids or [] if message_id]
    normalized_batch_id = str(batch_id or "").strip()
    if not normalized_batch_id or not ids or len(ids) != len(set(ids)):
        return None
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT message_id, batch_position "
            "FROM inbound_processing_events WHERE batch_id = ? "
            "ORDER BY batch_position ASC, created_at ASC, message_id ASC",
            (normalized_batch_id,),
        ).fetchall()
    finally:
        conn.close()
    ordered_ids = [str(row[0]) for row in rows]
    positions = [int(row[1] or 0) for row in rows]
    if (
        len(ordered_ids) != len(ids)
        or set(ordered_ids) != set(ids)
        or positions != list(range(len(rows)))
    ):
        return None
    return ordered_ids


def inbound_processing_join_batch(
    message_id: str,
    batch_id: str = "",
    position: int = 0,
) -> str:
    """Durably bind one accepted provider event to its debounce batch.

    The first event creates the stable batch identity; later events join that
    identity while the in-memory debounce buffer is open. Pre-ACK bindings may
    be coalesced into that open batch, but a claimed or terminal batch cannot
    be rebound into a different outbound turn.

    Direct unit/legacy callers may buffer an event without a durable ledger row.
    They still receive the deterministic identity, but no row is synthesized
    because the original recovery payload is unavailable here.
    """
    normalized_message_id = str(message_id or "").strip()
    if not normalized_message_id:
        return ""
    requested_batch_id = str(batch_id or "").strip()
    desired_batch_id = requested_batch_id or _inbound_processing_batch_id(
        normalized_message_id
    )
    normalized_position = max(0, int(position or 0))
    now = datetime.now(timezone.utc).isoformat()
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT conversation_id, channel, batch_id, batch_position, status "
            "FROM inbound_processing_events WHERE message_id = ?",
            (normalized_message_id,),
        ).fetchone()
        if row is None:
            conn.commit()
            return desired_batch_id
        existing_batch_id = str(row[2] or "")
        if existing_batch_id:
            source = conn.execute(
                "SELECT MIN(conversation_id), MAX(conversation_id), "
                "MIN(channel), MAX(channel), "
                "SUM(CASE WHEN status != 'received' THEN 1 ELSE 0 END) "
                "FROM inbound_processing_events WHERE batch_id = ?",
                (existing_batch_id,),
            ).fetchone()
            if (
                str(row[4] or "") != "received"
                or int(source[4] or 0) > 0
            ):
                conn.commit()
                return ""
            if not requested_batch_id or requested_batch_id == existing_batch_id:
                conn.commit()
                return existing_batch_id
            if (
                str(source[0] or "") != str(source[1] or "")
                or str(source[2] or "") != str(source[3] or "")
            ):
                conn.rollback()
                raise ValueError("Inbound batch has inconsistent ownership")
            target = conn.execute(
                "SELECT MIN(conversation_id), MAX(conversation_id), "
                "MIN(channel), MAX(channel), "
                "SUM(CASE WHEN status != 'received' THEN 1 ELSE 0 END), "
                "COALESCE(MAX(batch_position), -1) "
                "FROM inbound_processing_events WHERE batch_id = ?",
                (requested_batch_id,),
            ).fetchone()
            target_exists = target[0] is not None
            if target_exists and (
                str(target[0] or "") != str(target[1] or "")
                or str(target[2] or "") != str(target[3] or "")
                or str(target[0] or "") != str(row[0] or "")
                or str(target[2] or "") != str(row[1] or "")
            ):
                conn.rollback()
                raise ValueError("Inbound batch cannot cross a conversation or channel")
            if target_exists and int(target[4] or 0) > 0:
                conn.commit()
                return existing_batch_id
            offset = int(target[5]) + 1 if target_exists else 0
            conn.execute(
                "UPDATE inbound_processing_events SET batch_id = ?, "
                "batch_position = batch_position + ?, updated_at = ? "
                "WHERE batch_id = ? AND status = 'received'",
                (requested_batch_id, offset, now, existing_batch_id),
            )
            conn.commit()
            return requested_batch_id
        owner = conn.execute(
            "SELECT conversation_id, channel, "
            "SUM(CASE WHEN status != 'received' THEN 1 ELSE 0 END) "
            "FROM inbound_processing_events WHERE batch_id = ? "
            "GROUP BY conversation_id, channel LIMIT 1",
            (desired_batch_id,),
        ).fetchone()
        if owner is not None and (
            str(owner[0] or "") != str(row[0] or "")
            or str(owner[1] or "") != str(row[1] or "")
        ):
            conn.rollback()
            raise ValueError("Inbound batch cannot cross a conversation or channel")
        if owner is not None and int(owner[2] or 0) > 0:
            # Recovery/flush has sealed the requested batch. Keep this newly
            # accepted event in its own stable batch instead of appending it to
            # a snapshot already being processed.
            desired_batch_id = _inbound_processing_batch_id(normalized_message_id)
            normalized_position = 0
        conn.execute(
            "UPDATE inbound_processing_events "
            "SET batch_id = ?, batch_position = ?, updated_at = ? "
            "WHERE message_id = ? AND batch_id = ''",
            (
                desired_batch_id,
                normalized_position,
                now,
                normalized_message_id,
            ),
        )
        conn.commit()
        return desired_batch_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def wa_store_external_operator_message(
    message_id: str,
    conversation_id: str,
    channel: str,
    text: str,
    sender_name: str = "Secretaría",
    created_at: str = "",
) -> bool:
    """Atomically deduplicate and store a phone-app operator message.

    Returns True only for the first delivery of a provider message id. The
    processed marker and timeline row share one transaction, so a crash cannot
    mark the webhook consumed without preserving the message.
    """
    if not message_id or not conversation_id or not text:
        return False
    now = datetime.now(timezone.utc).isoformat()
    timestamp = str(created_at or "").strip() or now
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        inserted = conn.execute(
            "INSERT OR IGNORE INTO whatsapp_processed (message_id, created_at) "
            "VALUES (?, ?)",
            (message_id, now),
        )
        if inserted.rowcount == 0:
            conn.rollback()
            return False
        conn.execute(
            "INSERT INTO whatsapp_threads "
            "(phone, role, text, created_at, channel, sender_name) "
            "VALUES (?, 'operator', ?, ?, ?, ?)",
            (
                conversation_id,
                text,
                timestamp,
                channel or "whatsapp",
                sender_name or "Secretaría",
            ),
        )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def inbound_processing_record(message_id: str, conversation_id: str,
                              channel: str, status: str = "received",
                              reason: str = "", error: str = "",
                              payload: dict | None = None):
    """Create/update the durable processing state for one inbound message."""
    if not message_id:
        return
    now = datetime.now(timezone.utc).isoformat()
    retry_deferred = status == "recovering" or (
        status == "processing"
        and reason in {
            "tenant_runtime_controls_unavailable",
            "tenant_account_control_unavailable",
        }
    )
    replace_lease = retry_deferred or status not in {
        "received", "processing", "recovering",
    }
    lease_expires_at = now if retry_deferred else ""
    provider_retry_increment = int(
        status == "recovering" and reason == "provider_send_retry"
    )
    provider_retry_kind = (error or "")[:120] if provider_retry_increment else ""
    conn = _get_conn()
    serialized_payload = json.dumps(
        payload or {}, ensure_ascii=False, separators=(",", ":"),
    )
    conn.execute(
        "INSERT INTO inbound_processing_events "
        "(message_id, conversation_id, channel, status, reason, last_error, "
        "payload_json, lease_expires_at, provider_retry_count, provider_retry_kind, "
        "created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(message_id) DO UPDATE SET "
        "conversation_id = excluded.conversation_id, "
        "channel = excluded.channel, "
        "status = excluded.status, "
        "reason = excluded.reason, "
        "last_error = excluded.last_error, "
        "lease_expires_at = CASE WHEN ? THEN excluded.lease_expires_at "
        "ELSE inbound_processing_events.lease_expires_at END, "
        "provider_retry_count = inbound_processing_events.provider_retry_count "
        "+ excluded.provider_retry_count, "
        "provider_retry_kind = CASE WHEN excluded.provider_retry_kind != '' "
        "THEN excluded.provider_retry_kind "
        "ELSE inbound_processing_events.provider_retry_kind END, "
        "payload_json = CASE WHEN excluded.payload_json != '{}' "
        "THEN excluded.payload_json ELSE inbound_processing_events.payload_json END, "
        "updated_at = excluded.updated_at "
        "WHERE inbound_processing_events.status IN "
        "('received', 'processing', 'recovering') "
        "AND inbound_processing_events.processing_token = ''",
        (
            message_id,
            conversation_id or "",
            channel or "",
            status,
            (reason or "")[:500],
            (error or "")[:500],
            serialized_payload,
            lease_expires_at,
            provider_retry_increment,
            provider_retry_kind,
            now,
            now,
            int(replace_lease),
        )
    )
    conn.commit()
    conn.close()


def inbound_processing_update(message_id: str, status: str,
                              reason: str = "", error: str = "") -> bool:
    """Update an unclaimed active record without reviving terminal state."""
    if not message_id:
        return False
    now = datetime.now(timezone.utc).isoformat()
    retry_deferred = status == "recovering" or (
        status == "processing"
        and reason in {
            "tenant_runtime_controls_unavailable",
            "tenant_account_control_unavailable",
        }
    )
    replace_lease = retry_deferred or status not in {
        "received", "processing", "recovering",
    }
    lease_expires_at = now if retry_deferred else ""
    provider_retry_increment = int(
        status == "recovering" and reason == "provider_send_retry"
    )
    provider_retry_kind = (error or "")[:120] if provider_retry_increment else ""
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute(
            "UPDATE inbound_processing_events "
            "SET status = ?, reason = ?, last_error = ?, "
            "lease_expires_at = CASE WHEN ? THEN ? ELSE lease_expires_at END, "
            "provider_retry_count = provider_retry_count + ?, "
            "provider_retry_kind = CASE WHEN ? != '' THEN ? "
            "ELSE provider_retry_kind END, "
            "processing_token = '', updated_at = ? "
            "WHERE message_id = ? AND processing_token = '' "
            "AND status IN ('received', 'processing', 'recovering')",
            (
                status,
                (reason or "")[:500],
                (error or "")[:500],
                int(replace_lease),
                lease_expires_at,
                provider_retry_increment,
                provider_retry_kind,
                provider_retry_kind,
                now,
                message_id,
            )
        )
        if cur.rowcount == 0:
            cur = conn.execute(
                "INSERT OR IGNORE INTO inbound_processing_events "
                "(message_id, status, reason, last_error, lease_expires_at, "
                "provider_retry_count, provider_retry_kind, processing_token, "
                "created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, ?)",
                (
                    message_id,
                    status,
                    (reason or "")[:500],
                    (error or "")[:500],
                    lease_expires_at,
                    provider_retry_increment,
                    provider_retry_kind,
                    now,
                    now,
                ),
            )
        conn.commit()
        return int(cur.rowcount or 0) == 1
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def inbound_processing_quarantine(
    message_id: str,
    reason: str,
    *,
    processing_token: str = "",
) -> bool:
    """Terminally reject a stale tenant-mismatched recovery payload.

    The provider message id remains recorded for deduplication, but customer
    content and routing metadata are erased so a reassigned account cannot
    leak its prior tenant's payload into the current runtime.
    """
    return inbound_processing_quarantine_batch(
        [message_id], reason, processing_token=processing_token,
    )


def inbound_processing_quarantine_batch(
    message_ids: list[str],
    reason: str,
    *,
    processing_token: str = "",
) -> bool:
    """Atomically quarantine every member of a stale tenant-mismatched batch."""
    ids = list(dict.fromkeys(
        str(message_id) for message_id in message_ids or [] if message_id
    ))
    if not ids:
        return False
    now = datetime.now(timezone.utc).isoformat()
    token = str(processing_token or "").strip()
    placeholders = ",".join("?" for _ in ids)
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            f"SELECT status, processing_token FROM inbound_processing_events "
            f"WHERE message_id IN ({placeholders})",
            ids,
        ).fetchall()
        if (
            len(rows) != len(ids)
            or any(str(row[0] or "") not in {
                "received", "processing", "recovering",
            } for row in rows)
            or any(str(row[1] or "") != token for row in rows)
        ):
            conn.rollback()
            return False
        cur = conn.executemany(
            "UPDATE inbound_processing_events "
            "SET status = 'ignored', reason = ?, last_error = '', "
            "payload_json = '{}', conversation_id = '', channel = '', "
            "lease_expires_at = '', processing_token = '', updated_at = ? "
            "WHERE message_id = ? AND processing_token = ? "
            "AND status IN ('received', 'processing', 'recovering')",
            [
                (
                    (reason or "recovery_payload_quarantined")[:500],
                    now,
                    message_id,
                    token,
                )
                for message_id in ids
            ],
        )
        conn.commit()
        return int(cur.rowcount or 0) == len(ids)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def inbound_processing_bulk_update(message_ids: list, status: str,
                                   reason: str = "", error: str = "",
                                   *, processing_token: str = "") -> bool:
    """CAS-update a complete active batch without reviving terminal rows."""
    ids = list(dict.fromkeys(
        str(message_id) for message_id in message_ids or [] if message_id
    ))
    if not ids:
        return False
    now = datetime.now(timezone.utc).isoformat()
    normalized_reason = (reason or "")[:500]
    normalized_error = (error or "")[:500]
    retry_deferred = status == "recovering" or (
        status == "processing"
        and reason in {
            "tenant_runtime_controls_unavailable",
            "tenant_account_control_unavailable",
        }
    )
    replace_lease = retry_deferred or status not in {
        "received", "processing", "recovering",
    }
    lease_expires_at = now if retry_deferred else ""
    provider_retry_increment = int(
        status == "recovering" and normalized_reason == "provider_send_retry"
    )
    provider_retry_kind = (
        normalized_error[:120] if provider_retry_increment else ""
    )
    token = str(processing_token or "").strip()
    placeholders = ",".join("?" for _ in ids)
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            f"SELECT message_id, status, processing_token "
            f"FROM inbound_processing_events WHERE message_id IN ({placeholders})",
            ids,
        ).fetchall()
        if (
            any(str(row[1] or "") not in {
                "received", "processing", "recovering",
            } for row in rows)
            or any(str(row[2] or "") != token for row in rows)
            or (token and len(rows) != len(ids))
        ):
            conn.rollback()
            return False
        existing = {str(row[0]) for row in rows}
        for message_id in ids:
            if message_id not in existing:
                conn.execute(
                    "INSERT INTO inbound_processing_events "
                    "(message_id, status, reason, last_error, lease_expires_at, "
                    "provider_retry_count, provider_retry_kind, processing_token, "
                    "created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, ?)",
                    (
                        message_id,
                        status,
                        normalized_reason,
                        normalized_error,
                        lease_expires_at,
                        provider_retry_increment,
                        provider_retry_kind,
                        now,
                        now,
                    ),
                )
                continue
            cur = conn.execute(
                "UPDATE inbound_processing_events "
                "SET status = ?, reason = ?, last_error = ?, "
                "lease_expires_at = CASE WHEN ? THEN ? "
                "ELSE lease_expires_at END, "
                "provider_retry_count = provider_retry_count + ?, "
                "provider_retry_kind = CASE WHEN ? != '' THEN ? "
                "ELSE provider_retry_kind END, "
                "processing_token = '', updated_at = ? "
                "WHERE message_id = ? AND processing_token = ? "
                "AND status IN ('received', 'processing', 'recovering')",
                (
                    status,
                    normalized_reason,
                    normalized_error,
                    int(replace_lease),
                    lease_expires_at,
                    provider_retry_increment,
                    provider_retry_kind,
                    provider_retry_kind,
                    now,
                    message_id,
                    token,
                ),
            )
            if cur.rowcount != 1:
                conn.rollback()
                return False
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def inbound_processing_begin_batch(
    message_ids: list[str],
    *,
    batch_id: str = "",
    recovering: bool = False,
    recovery_token: str = "",
    processing_lease_seconds: float = INBOUND_PROCESSING_LEASE_SECONDS,
) -> str | bool:
    """Atomically start a batch and return its new worker-generation token."""
    ids = list(dict.fromkeys(
        str(message_id) for message_id in message_ids or [] if message_id
    ))
    if not ids:
        return ""
    placeholders = ",".join("?" for _ in ids)
    allowed = {"recovering"} if recovering else {"received"}
    now = datetime.now(timezone.utc).isoformat()
    lease_expires_at = _inbound_lease_deadline(processing_lease_seconds)
    expected_token = str(recovery_token or "").strip()
    new_token = _inbound_processing_token()
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            f"SELECT message_id, status, batch_id, processing_token "
            f"FROM inbound_processing_events "
            f"WHERE message_id IN ({placeholders})",
            ids,
        ).fetchall()
        normalized_batch_id = str(batch_id or "").strip()
        if (
            (
                normalized_batch_id
                and (
                    len(rows) != len(ids)
                    or any(
                        str(row[2] or "") != normalized_batch_id
                        for row in rows
                    )
                )
            )
            or (
                not normalized_batch_id
                and rows
                and len(rows) != len(ids)
            )
            or any(str(row[1] or "") not in allowed for row in rows)
            or any(str(row[3] or "") != expected_token for row in rows)
            or (recovering and not expected_token)
        ):
            conn.rollback()
            return False
        if normalized_batch_id:
            total = conn.execute(
                "SELECT COUNT(*) FROM inbound_processing_events WHERE batch_id = ?",
                (normalized_batch_id,),
            ).fetchone()
            if int(total[0] or 0) != len(ids):
                conn.rollback()
                return False
        existing = {str(row[0]) for row in rows}
        for position, message_id in enumerate(ids):
            if message_id not in existing:
                continue
            conn.execute(
                "UPDATE inbound_processing_events SET status = 'processing', "
                "reason = 'batch_flush_started', last_error = '', "
                "batch_id = CASE WHEN batch_id = '' THEN ? ELSE batch_id END, "
                "batch_position = CASE WHEN batch_id = '' THEN ? "
                "ELSE batch_position END, lease_expires_at = ?, "
                "processing_token = ?, updated_at = ? WHERE message_id = ? "
                "AND processing_token = ?",
                (
                    normalized_batch_id,
                    position,
                    lease_expires_at,
                    new_token,
                    now,
                    message_id,
                    expected_token,
                ),
            )
        conn.executemany(
            "INSERT INTO inbound_processing_events "
            "(message_id, status, reason, last_error, batch_id, batch_position, "
            "lease_expires_at, processing_token, created_at, updated_at) "
            "VALUES (?, 'processing', 'batch_flush_started', '', ?, ?, ?, ?, ?, ?)",
            [
                (
                    message_id,
                    normalized_batch_id,
                    position,
                    lease_expires_at,
                    new_token,
                    now,
                    now,
                )
                for position, message_id in enumerate(ids)
                if message_id not in existing
            ],
        )
        conn.commit()
        return new_token
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def inbound_processing_claim_outbound_attempt(
    message_ids: list[str],
    idempotency_key: str,
    batch_id: str,
    *,
    processing_token: str = "",
) -> bool:
    """Persist the one allowed direct-provider attempt for an inbound batch."""
    ids = list(dict.fromkeys(
        str(message_id) for message_id in message_ids or [] if message_id
    ))
    key = str(idempotency_key or "").strip()
    normalized_batch_id = str(batch_id or "").strip()
    token = str(processing_token or "").strip()
    if not ids or not key or not normalized_batch_id or not token:
        return False
    placeholders = ",".join("?" for _ in ids)
    now = datetime.now(timezone.utc).isoformat()
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            f"SELECT message_id, status, batch_id, outbound_idempotency_key, "
            f"processing_token "
            f"FROM inbound_processing_events WHERE message_id IN ({placeholders})",
            ids,
        ).fetchall()
        total = conn.execute(
            "SELECT COUNT(*) FROM inbound_processing_events WHERE batch_id = ?",
            (normalized_batch_id,),
        ).fetchone()
        if (
            len(rows) != len(ids)
            or int(total[0] or 0) != len(ids)
            or any(str(row[1] or "") != "processing" for row in rows)
            or any(str(row[2] or "") != normalized_batch_id for row in rows)
            or any(str(row[3] or "") for row in rows)
            or any(str(row[4] or "") != token for row in rows)
        ):
            conn.rollback()
            return False
        conn.executemany(
            "UPDATE inbound_processing_events "
            "SET outbound_idempotency_key = ?, outbound_attempted_at = ?, "
            "updated_at = ? WHERE message_id = ? AND batch_id = ? "
            "AND status = 'processing' AND outbound_idempotency_key = '' "
            "AND processing_token = ?",
            [
                (key, now, now, message_id, normalized_batch_id, token)
                for message_id in ids
            ],
        )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def inbound_processing_is_current(
    message_ids: list[str],
    batch_id: str,
    processing_token: str,
    *,
    required_status: str = "processing",
    renew_lease_seconds: float | None = INBOUND_PROCESSING_LEASE_SECONDS,
) -> bool:
    """Check/freshen an exact worker generation before send or commit."""
    ids = list(dict.fromkeys(
        str(message_id) for message_id in message_ids or [] if message_id
    ))
    token = str(processing_token or "").strip()
    normalized_batch_id = str(batch_id or "").strip()
    if not ids or not token:
        return False
    placeholders = ",".join("?" for _ in ids)
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            f"SELECT message_id, status, batch_id, processing_token "
            f"FROM inbound_processing_events WHERE message_id IN ({placeholders})",
            ids,
        ).fetchall()
        if (
            len(rows) != len(ids)
            or any(str(row[1] or "") != required_status for row in rows)
            or any(str(row[3] or "") != token for row in rows)
            or (
                normalized_batch_id
                and any(str(row[2] or "") != normalized_batch_id for row in rows)
            )
        ):
            conn.rollback()
            return False
        if normalized_batch_id:
            total = conn.execute(
                "SELECT COUNT(*) FROM inbound_processing_events WHERE batch_id = ?",
                (normalized_batch_id,),
            ).fetchone()
            if int(total[0] or 0) != len(ids):
                conn.rollback()
                return False
        if renew_lease_seconds is not None:
            deadline = _inbound_lease_deadline(renew_lease_seconds)
            cur = conn.execute(
                f"UPDATE inbound_processing_events SET lease_expires_at = ?, "
                f"updated_at = ? WHERE message_id IN ({placeholders}) "
                "AND status = ? AND processing_token = ?",
                [deadline, datetime.now(timezone.utc).isoformat(), *ids,
                 required_status, token],
            )
            if int(cur.rowcount or 0) != len(ids):
                conn.rollback()
                return False
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def inbound_processing_mark_stale_failures(max_age_seconds: int = 300) -> int:
    """Mark old non-terminal inbound records as visible failures.

    This closes the crash window where a message was received/buffered but the
    worker died before the timer or model/send path could finish.
    """
    now_dt = datetime.now(timezone.utc)
    cutoff = (now_dt - timedelta(seconds=max_age_seconds)).isoformat()
    now = now_dt.isoformat()
    conn = _get_conn()
    cur = conn.execute(
        "UPDATE inbound_processing_events "
        "SET status = 'processing_failed', "
        "reason = 'stale_non_terminal_state', "
        "last_error = 'Inbound processing did not reach a terminal state in time.', "
        "lease_expires_at = '', processing_token = '', updated_at = ? "
        "WHERE status IN ('received', 'processing', 'recovering') "
        "AND payload_json = '{}' AND ("
        "(lease_expires_at != '' AND lease_expires_at <= ?) OR "
        "(lease_expires_at = '' AND updated_at < ?))",
        (now, now, cutoff)
    )
    conn.commit()
    count = cur.rowcount
    conn.close()
    return count


def inbound_processing_claim_recoverable(
    max_age_seconds: int = 40,
    limit: int = 50,
) -> list[dict]:
    """Claim whole durable WhatsApp batches abandoned before a terminal reply.

    ``limit`` is a batch limit, never a row limit.  Returning every member in
    its original position keeps debounce input and provider idempotency stable
    across repeated crashes.  Legacy unbatched rows are conservatively migrated
    to singleton batches because their former in-memory membership is unknowable.
    """
    now_dt = datetime.now(timezone.utc)
    cutoff = (now_dt - timedelta(seconds=max_age_seconds)).isoformat()
    now = now_dt.isoformat()
    recovery_lease_expires_at = (
        now_dt + timedelta(seconds=INBOUND_RECOVERY_CLAIM_LEASE_SECONDS)
    ).isoformat()

    def expired_sql(prefix: str = "") -> str:
        field = f"{prefix}." if prefix else ""
        return (
            "(("
            f"{field}lease_expires_at != '' "
            f"AND {field}lease_expires_at <= :lease_now"
            ") OR ("
            f"{field}lease_expires_at = '' AND ("
            f"({field}status = 'received' "
            f"AND {field}created_at < :legacy_cutoff) OR "
            f"({field}status IN ('processing', 'recovering') "
            f"AND {field}updated_at < :legacy_cutoff)"
            ")))"
        )

    query_params = {
        "lease_now": now,
        "legacy_cutoff": cutoff,
        "batch_limit": max(1, min(int(limit), 200)),
    }
    expired = expired_sql()
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        acceptance_batches = conn.execute(
            "SELECT DISTINCT acceptance_batch_id FROM inbound_processing_events "
            "WHERE batch_id = '' AND acceptance_batch_id != '' "
            "AND payload_json != '{}' "
            "AND status IN ('received', 'processing', 'recovering') "
            f"AND {expired}",
            query_params,
        ).fetchall()
        for acceptance_batch in acceptance_batches:
            conn.execute(
                "UPDATE inbound_processing_events "
                "SET batch_id = acceptance_batch_id, "
                "batch_position = acceptance_position "
                "WHERE batch_id = '' AND acceptance_batch_id = ?",
                (acceptance_batch[0],),
            )
        legacy_rows = conn.execute(
            "SELECT message_id FROM inbound_processing_events "
            "WHERE batch_id = '' AND payload_json != '{}' "
            "AND status IN ('received', 'processing', 'recovering') "
            f"AND {expired}",
            query_params,
        ).fetchall()
        for legacy_row in legacy_rows:
            conn.execute(
                "UPDATE inbound_processing_events "
                "SET batch_id = ?, batch_position = 0 "
                "WHERE message_id = ? AND batch_id = ''",
                (
                    _inbound_processing_batch_id(str(legacy_row[0] or "")),
                    legacy_row[0],
                ),
            )
        # Supersession is a batch transition. Comparing against the newest
        # member prevents an older outbound from terminally changing only the
        # first row of a multi-message debounce batch.
        stale_batches = conn.execute(
            "SELECT batch_id, conversation_id, MAX(created_at) "
            "FROM inbound_processing_events "
            "WHERE batch_id != '' "
            "AND status IN ('received', 'processing', 'recovering') "
            "GROUP BY batch_id, conversation_id "
            f"HAVING SUM(CASE WHEN NOT {expired} THEN 1 ELSE 0 END) = 0",
            query_params,
        ).fetchall()
        for batch_id, conversation_id, latest_created_at in stale_batches:
            newer_outbound = conn.execute(
                "SELECT 1 FROM whatsapp_threads "
                "WHERE phone = ? AND role IN ('assistant', 'operator') "
                "AND COALESCE(source_message_key, '') NOT LIKE 'mermaid-model-status:%' "
                "AND created_at > ? LIMIT 1",
                (conversation_id, latest_created_at),
            ).fetchone()
            if newer_outbound is not None:
                conn.execute(
                    "UPDATE inbound_processing_events "
                    "SET status = 'superseded', "
                    "reason = 'newer_outbound_exists', lease_expires_at = '', "
                    "processing_token = '', "
                    "updated_at = :updated_at WHERE batch_id = :batch_id "
                    "AND status IN "
                    "('received', 'processing', 'recovering')",
                    {"updated_at": now, "batch_id": batch_id},
                )

        # A terminal or payload-less sibling makes the original action
        # ambiguous. Never resurrect such a partial batch; fail its stale
        # remainder visibly instead. Atomic batch transitions below ensure new
        # writes cannot normally enter this compatibility/corruption state.
        incomplete_batches = conn.execute(
            "SELECT batch_id FROM inbound_processing_events "
            "WHERE batch_id != '' GROUP BY batch_id "
            "HAVING SUM(CASE WHEN status NOT IN "
            "('received', 'processing', 'recovering') OR payload_json = '{}' "
            "THEN 1 ELSE 0 END) > 0 "
            "AND SUM(CASE WHEN status IN "
            "('received', 'processing', 'recovering') AND "
            f"{expired} "
            "THEN 1 ELSE 0 END) > 0",
            query_params,
        ).fetchall()
        for incomplete_batch in incomplete_batches:
            conn.execute(
                "UPDATE inbound_processing_events "
                "SET status = 'processing_failed', "
                "reason = 'incomplete_durable_batch', "
                "last_error = 'A batch member was already terminal; replay suppressed.', "
                "lease_expires_at = '', processing_token = '', "
                "updated_at = :updated_at "
                "WHERE batch_id = :batch_id "
                "AND status IN ('received', 'processing', 'recovering') "
                f"AND {expired}",
                {
                    **query_params,
                    "updated_at": now,
                    "batch_id": incomplete_batch[0],
                },
            )
        rows = conn.execute(
            "WITH eligible_batches AS ("
            " SELECT batch_id, MIN(created_at) AS first_created "
            " FROM inbound_processing_events "
            " WHERE batch_id != '' "
            " GROUP BY batch_id "
            " HAVING SUM(CASE WHEN status IN "
            " ('received', 'processing', 'recovering') AND "
            f"{expired} THEN 1 ELSE 0 END) > 0 "
            " AND SUM(CASE WHEN status IN "
            " ('received', 'processing', 'recovering') AND NOT ("
            f" {expired}) THEN 1 ELSE 0 END) = 0 "
            " AND SUM(CASE WHEN status NOT IN "
            " ('received', 'processing', 'recovering') OR payload_json = '{}' "
            " THEN 1 ELSE 0 END) = 0 "
            " ORDER BY first_created ASC, batch_id ASC LIMIT :batch_limit"
            ") "
            "SELECT inbound.message_id, inbound.conversation_id, inbound.channel, "
            "inbound.payload_json, inbound.created_at, inbound.heartbeat_sent_at, "
            "inbound.attempt_count, inbound.reason, inbound.last_error, "
            "inbound.status, inbound.batch_id, inbound.batch_position, "
            "inbound.provider_retry_count, inbound.provider_retry_kind "
            "FROM inbound_processing_events AS inbound "
            "JOIN eligible_batches AS eligible ON eligible.batch_id = inbound.batch_id "
            "WHERE inbound.status IN ('received', 'processing', 'recovering') "
            "AND inbound.payload_json != '{}' "
            "ORDER BY eligible.first_created ASC, inbound.batch_position ASC, "
            "inbound.created_at ASC, inbound.message_id ASC",
            query_params,
        ).fetchall()
        batch_tokens = {
            str(row[10] or ""): _inbound_processing_token()
            for row in rows
        }
        for batch_id, processing_token in batch_tokens.items():
            conn.execute(
                "UPDATE inbound_processing_events SET status = 'recovering', "
                "reason = 'stale_turn_reclaimed', "
                "attempt_count = attempt_count + 1, "
                "lease_expires_at = ?, processing_token = ?, updated_at = ? "
                "WHERE batch_id = ? "
                "AND status IN ('received', 'processing', 'recovering')",
                (recovery_lease_expires_at, processing_token, now, batch_id),
            )
        conn.commit()
    finally:
        conn.close()
    result = []
    for row in rows:
        try:
            payload = json.loads(row[3] or "{}")
        except (TypeError, ValueError):
            payload = {}
        result.append({
            "message_id": row[0], "conversation_id": row[1],
            "channel": row[2], "payload": payload, "created_at": row[4],
            "heartbeat_sent_at": row[5],
            "attempt_count": int(row[6] or 0) + (
                1 if str(row[9] or "") in {"received", "processing", "recovering"} else 0
            ),
            "provider_retry_count": int(row[12] or 0),
            "provider_retry_kind": str(row[13] or ""),
            "recovery_reason": str(row[7] or ""),
            "recovery_error": str(row[8] or ""),
            "batch_id": str(row[10] or ""),
            "batch_position": int(row[11] or 0),
            "processing_token": batch_tokens.get(str(row[10] or ""), ""),
        })
    return result


def inbound_processing_mark_heartbeat(
    message_ids: list[str],
    *,
    processing_token: str,
) -> bool:
    """Record one recovery heartbeat only for the current claimed generation."""
    ids = [str(value) for value in message_ids or [] if str(value)]
    token = str(processing_token or "").strip()
    if not ids or not token:
        return False
    placeholders = ",".join("?" for _ in ids)
    now = datetime.now(timezone.utc).isoformat()
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            f"SELECT status, processing_token FROM inbound_processing_events "
            f"WHERE message_id IN ({placeholders})",
            ids,
        ).fetchall()
        if (
            len(rows) != len(ids)
            or any(str(row[0] or "") != "recovering" for row in rows)
            or any(str(row[1] or "") != token for row in rows)
        ):
            conn.rollback()
            return False
        cur = conn.execute(
            f"UPDATE inbound_processing_events SET heartbeat_sent_at = ? "
            f"WHERE message_id IN ({placeholders}) "
            "AND status = 'recovering' AND processing_token = ? "
            "AND heartbeat_sent_at = ''",
            [now, *ids, token],
        )
        conn.commit()
        return int(cur.rowcount or 0) > 0
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def zernio_failed_event_key(failed: dict) -> str:
    """Return the stable opaque identity for one normalized failure event."""
    account_id = str((failed or {}).get("account_id") or "").strip()
    conversation_id = str(
        (failed or {}).get("conversation_id") or ""
    ).strip()
    message_id = str((failed or {}).get("message_id") or "").strip()
    if not account_id or not conversation_id or not message_id:
        return ""
    return hashlib.sha256(
        (
            "zernio-failed-event-v1\x1f"
            + account_id
            + "\x1f"
            + conversation_id
            + "\x1f"
            + message_id
        ).encode("utf-8")
    ).hexdigest()


def zernio_failed_event_accept(failed: dict) -> tuple[str, bool]:
    """Durably enqueue a normalized failure before acknowledging Zernio."""
    event_key = zernio_failed_event_key(failed)
    if not event_key:
        return "", False
    account_id = str(failed.get("account_id") or "").strip()
    conversation_id = str(failed.get("conversation_id") or "").strip()
    message_id = str(failed.get("message_id") or "").strip()
    payload_json = json.dumps(
        failed, ensure_ascii=False, separators=(",", ":"),
    )
    now = datetime.now(timezone.utc).isoformat()
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute(
            "INSERT OR IGNORE INTO zernio_failed_event_queue "
            "(event_key, account_id, conversation_id, message_id, payload_json, "
            "status, claim_token, available_at, lease_expires_at, attempt_count, "
            "last_error, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 'pending', '', ?, '', 0, '', ?, ?)",
            (
                event_key,
                account_id,
                conversation_id,
                message_id,
                payload_json,
                now,
                now,
                now,
            ),
        )
        conn.commit()
        return event_key, int(cur.rowcount or 0) == 1
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def zernio_failed_event_claim_due(
    *,
    limit: int = 10,
    lease_seconds: float = ZERNIO_FAILED_EVENT_LEASE_SECONDS,
) -> list[dict]:
    """Lease pending or expired failure events for crash-safe processing."""
    now_dt = datetime.now(timezone.utc)
    now = now_dt.isoformat()
    lease_expires_at = (
        now_dt + timedelta(seconds=max(0.0, float(lease_seconds)))
    ).isoformat()
    conn = _get_conn()
    claims = []
    try:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            "SELECT event_key, payload_json, attempt_count "
            "FROM zernio_failed_event_queue WHERE "
            "(status = 'pending' AND available_at <= ?) OR "
            "(status = 'processing' AND lease_expires_at != '' "
            "AND lease_expires_at <= ?) "
            "ORDER BY created_at ASC, event_key ASC LIMIT ?",
            (now, now, max(1, min(int(limit), 100))),
        ).fetchall()
        for event_key, payload_json, attempt_count in rows:
            claim_token = _inbound_processing_token()
            cur = conn.execute(
                "UPDATE zernio_failed_event_queue SET status = 'processing', "
                "claim_token = ?, lease_expires_at = ?, "
                "attempt_count = attempt_count + 1, updated_at = ? "
                "WHERE event_key = ? AND ((status = 'pending' "
                "AND available_at <= ?) OR (status = 'processing' "
                "AND lease_expires_at != '' AND lease_expires_at <= ?))",
                (
                    claim_token,
                    lease_expires_at,
                    now,
                    event_key,
                    now,
                    now,
                ),
            )
            if cur.rowcount != 1:
                continue
            try:
                failed = json.loads(payload_json or "{}")
            except (TypeError, ValueError):
                failed = {}
            claims.append({
                "event_key": str(event_key or ""),
                "claim_token": claim_token,
                "failed": failed if isinstance(failed, dict) else {},
                "attempt_count": int(attempt_count or 0) + 1,
                "lease_expires_at": lease_expires_at,
            })
        conn.commit()
        return claims
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def zernio_failed_event_is_current(
    event_key: str,
    claim_token: str,
    *,
    renew_lease_seconds: float | None = ZERNIO_FAILED_EVENT_LEASE_SECONDS,
) -> bool:
    """Validate and optionally renew one exact failed-event worker claim."""
    key = str(event_key or "").strip()
    token = str(claim_token or "").strip()
    if not key or not token:
        return False
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT status, claim_token FROM zernio_failed_event_queue "
            "WHERE event_key = ?",
            (key,),
        ).fetchone()
        if (
            row is None
            or str(row[0] or "") != "processing"
            or str(row[1] or "") != token
        ):
            conn.rollback()
            return False
        if renew_lease_seconds is not None:
            deadline = _inbound_lease_deadline(renew_lease_seconds)
            cur = conn.execute(
                "UPDATE zernio_failed_event_queue SET lease_expires_at = ?, "
                "updated_at = ? WHERE event_key = ? AND status = 'processing' "
                "AND claim_token = ?",
                (
                    deadline,
                    datetime.now(timezone.utc).isoformat(),
                    key,
                    token,
                ),
            )
            if cur.rowcount != 1:
                conn.rollback()
                return False
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _zernio_failed_event_finish(
    event_key: str,
    claim_token: str,
    status: str,
) -> bool:
    """CAS a claimed event terminal and scrub its tenant payload."""
    if status not in {"completed", "ignored", "invalid"}:
        raise ValueError("invalid Zernio failure terminal status")
    key = str(event_key or "").strip()
    token = str(claim_token or "").strip()
    if not key or not token:
        return False
    now = datetime.now(timezone.utc).isoformat()
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute(
            "UPDATE zernio_failed_event_queue SET status = ?, "
            "account_id = '', conversation_id = '', message_id = '', "
            "payload_json = '{}', claim_token = '', available_at = '', "
            "lease_expires_at = '', last_error = '', updated_at = ? "
            "WHERE event_key = ? AND status = 'processing' AND claim_token = ?",
            (status, now, key, token),
        )
        conn.commit()
        return cur.rowcount == 1
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def zernio_failed_event_complete(event_key: str, claim_token: str) -> bool:
    return _zernio_failed_event_finish(event_key, claim_token, "completed")


def zernio_failed_event_complete_with_attention(
    event_key: str, claim_token: str, failed: dict,
) -> bool:
    """Atomically surface an unmatched late failure and scrub its queue row."""
    if zernio_failed_event_key(failed) != event_key or not claim_token:
        return False
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT status, claim_token FROM zernio_failed_event_queue WHERE event_key = ?",
            (event_key,),
        ).fetchone()
        if not row or str(row[0]) != "processing" or str(row[1]) != claim_token:
            conn.rollback()
            return False
        from shared.tenant_guard import account_access_state

        account_state = account_access_state(
            str(failed.get("account_id") or ""), direction="inbound",
        )
        if account_state is None:
            raise RuntimeError("tenant account controls unavailable")
        if account_state is False:
            raise HandoffAccountReassignedError("tenant account reassigned")
        now = datetime.now(timezone.utc).isoformat()
        _insert_durable_operator_item(conn, "zernio-failed:" + event_key, {
            "notification_type": "escalation",
            "channel": "whatsapp",
            "customer_id": str(failed.get("conversation_id") or ""),
            "customer_name": "Customer",
            "subject": "[DELIVERY FAILED] Provider reported a late message failure",
            "body": (
                "The provider reported that an outbound message failed. "
                "Do not treat it as delivered. Review the conversation and reply "
                "manually if needed. Provider message reference: "
                + str(failed.get("message_id") or "")
            ),
        }, now)
        cur = conn.execute(
            "UPDATE zernio_failed_event_queue SET status = 'completed', "
            "account_id = '', conversation_id = '', message_id = '', payload_json = '{}', "
            "claim_token = '', available_at = '', lease_expires_at = '', last_error = '', updated_at = ? "
            "WHERE event_key = ? AND status = 'processing' AND claim_token = ?",
            (now, event_key, claim_token),
        )
        if cur.rowcount != 1:
            raise RuntimeError("failed event claim was lost")
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def zernio_failed_event_ignore(event_key: str, claim_token: str) -> bool:
    return _zernio_failed_event_finish(event_key, claim_token, "ignored")


def zernio_failed_event_invalid(event_key: str, claim_token: str) -> bool:
    return _zernio_failed_event_finish(event_key, claim_token, "invalid")


def zernio_failed_event_retry(
    event_key: str,
    claim_token: str,
    *,
    error_code: str,
    retry_delay_seconds: float = 30.0,
) -> bool:
    """Release an exact claim for a bounded-delay durable retry."""
    key = str(event_key or "").strip()
    token = str(claim_token or "").strip()
    if not key or not token:
        return False
    available_at = _inbound_lease_deadline(retry_delay_seconds)
    now = datetime.now(timezone.utc).isoformat()
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute(
            "UPDATE zernio_failed_event_queue SET status = 'pending', "
            "claim_token = '', available_at = ?, lease_expires_at = '', "
            "last_error = ?, updated_at = ? WHERE event_key = ? "
            "AND status = 'processing' AND claim_token = ?",
            (
                available_at,
                str(error_code or "processing_unavailable")[:120],
                now,
                key,
                token,
            ),
        )
        conn.commit()
        return cur.rowcount == 1
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def inbound_processing_all_terminal(message_ids: list[str]) -> bool:
    ids = [str(value) for value in message_ids or [] if str(value)]
    if not ids:
        return True
    placeholders = ",".join("?" for _ in ids)
    conn = _get_conn()
    try:
        row = conn.execute(
            f"SELECT COUNT(*), SUM(CASE WHEN status IN "
            "('received', 'processing', 'recovering') THEN 1 ELSE 0 END) "
            f"FROM inbound_processing_events WHERE message_id IN ({placeholders})",
            ids,
        ).fetchone()
    finally:
        conn.close()
    return int(row[0] or 0) == len(ids) and int(row[1] or 0) == 0


def wa_store_message(phone: str, role: str, text: str):
    """Store a WhatsApp message in conversation history."""
    conn = _get_conn()
    conn.execute(
        "INSERT INTO whatsapp_threads (phone, role, text, created_at) VALUES (?, ?, ?, ?)",
        (phone, role, text, datetime.now(timezone.utc).isoformat())
    )
    from shared import mermaid_customers
    mermaid_customers.capture(conn, phone, name="", at=None)
    conn.commit()
    conn.close()


def wa_get_history(phone: str, limit: int = 10) -> list:
    """Get recent conversation history for a phone number (last 24h, oldest first)."""
    conn = _get_conn()
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    rows = conn.execute(
        "SELECT role, text, created_at FROM whatsapp_threads "
        "WHERE phone = ? AND created_at > ? "
        "ORDER BY created_at DESC LIMIT ?",
        (phone, cutoff, limit)
    ).fetchall()
    conn.close()
    return [{"role": r[0], "text": r[1], "created_at": r[2]} for r in reversed(rows)]


def wa_get_booking_state(phone: str) -> dict:
    """Get booking state for a phone number. Returns {fields, flags, completed_bookings, last_activity}."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT fields_json, flags_json, completed_bookings_json, last_activity "
        "FROM whatsapp_booking_state WHERE phone = ?",
        (phone,)
    ).fetchone()
    conn.close()
    if not row:
        return {"fields": {}, "flags": {}, "completed_bookings": [], "last_activity": None}
    return {
        "fields": json.loads(row[0] or "{}"),
        "flags": json.loads(row[1] or "{}"),
        "completed_bookings": json.loads(row[2] or "[]"),
        "last_activity": row[3],
    }


def _order_coerce_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _order_coerce_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _order_lines_from_fields(fields: dict) -> list:
    products = fields.get("products") or []
    lines = []
    if isinstance(products, list):
        for item in products:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            qty = _order_coerce_int(
                item.get("quantity"),
                _order_coerce_int(fields.get("quantity") or fields.get("guests"), 1),
            )
            unit_price = _order_coerce_float(item.get("unit_price"))
            subtotal = _order_coerce_float(item.get("subtotal"))
            if subtotal is None and unit_price is not None:
                subtotal = unit_price * qty
            lines.append({
                "name": name,
                "quantity": qty,
                "unit_price": unit_price,
                "subtotal": subtotal,
            })
    if not lines:
        name = str(fields.get("product_name") or fields.get("service_name") or "").strip()
        if name:
            qty = _order_coerce_int(fields.get("quantity") or fields.get("guests"), 1)
            unit_price = _order_coerce_float(fields.get("unit_price"))
            subtotal = _order_coerce_float(fields.get("subtotal"))
            if subtotal is None and unit_price is not None:
                subtotal = unit_price * qty
            lines.append({
                "name": name,
                "quantity": qty,
                "unit_price": unit_price,
                "subtotal": subtotal,
            })
    return lines


def _order_total_from_fields(fields: dict, lines: list):
    total = _order_coerce_float(fields.get("order_total") or fields.get("total"))
    if total is not None:
        return total
    subtotals = [line.get("subtotal") for line in lines if line.get("subtotal") is not None]
    return sum(subtotals) if subtotals else None


def _order_address_from_fields(fields: dict) -> str:
    return str(fields.get("delivery_address") or fields.get("address") or "").strip()


def _order_payload_from_state(conversation_id: str, fields: dict,
                              fallback_name: str = "",
                              fallback_channel: str = "whatsapp") -> dict:
    lines = _order_lines_from_fields(fields or {})
    total = _order_total_from_fields(fields or {}, lines)
    name = str(
        (fields or {}).get("customer_name")
        or (fields or {}).get("name")
        or fallback_name
        or ""
    ).strip()
    return {
        "type": "ORDER",
        "customer_name": name,
        "phone": str((fields or {}).get("phone") or "").strip(),
        "products": lines,
        "delivery_address": _order_address_from_fields(fields or {}),
        "total": total,
        "currency": str((fields or {}).get("currency") or "XCG").strip(),
        "comments": str(
            (fields or {}).get("comments")
            or (fields or {}).get("special_requests")
            or ""
        ).strip(),
        "channel": fallback_channel or "whatsapp",
        "customer_id": conversation_id,
    }


def _order_merge_note(existing, update) -> str:
    existing_text = str(existing or "").strip()
    update_text = str(update or "").strip()
    if not update_text:
        return existing_text
    if not existing_text:
        return update_text
    existing_l = existing_text.lower()
    update_l = update_text.lower()
    if update_l in existing_l:
        return existing_text
    if existing_l in update_l:
        return update_text
    return f"{existing_text}\n{update_text}"


def _merge_order_payload_updates(payload: dict, state_payload: dict,
                                 fields: dict) -> dict:
    """Merge late customer-provided contact updates into an active order.

    Confirmed order rows keep the original product snapshot, but customers can
    still add a phone number, delivery note, or address after confirmation.
    The Orders view should reflect those operator-critical updates without
    replacing the confirmed products with a partial later state.
    """
    merged = dict(payload or {})
    state_payload = state_payload or {}
    fields = fields or {}

    customer_name = (
        fields.get("customer_name")
        or fields.get("name")
        or state_payload.get("customer_name")
    )
    if str(customer_name or "").strip():
        merged["customer_name"] = str(customer_name).strip()

    phone = fields.get("phone") or state_payload.get("phone")
    if str(phone or "").strip():
        merged["phone"] = str(phone).strip()

    email = fields.get("email") or state_payload.get("email")
    if str(email or "").strip():
        merged["email"] = str(email).strip()

    delivery_address = (
        fields.get("delivery_address")
        or fields.get("address")
        or state_payload.get("delivery_address")
    )
    if str(delivery_address or "").strip():
        merged["delivery_address"] = str(delivery_address).strip()

    comments = (
        fields.get("comments")
        or fields.get("special_requests")
        or state_payload.get("comments")
    )
    merged["comments"] = _order_merge_note(merged.get("comments"), comments)

    if not merged.get("products") and state_payload.get("products"):
        merged["products"] = state_payload.get("products")
    if merged.get("total") is None and state_payload.get("total") is not None:
        merged["total"] = state_payload.get("total")
    if state_payload.get("currency"):
        merged["currency"] = str(state_payload.get("currency")).strip()
    if not merged.get("channel") and state_payload.get("channel"):
        merged["channel"] = state_payload.get("channel")
    if not merged.get("customer_id") and state_payload.get("customer_id"):
        merged["customer_id"] = state_payload.get("customer_id")
    return merged


def _extract_order_payload_from_body(body: str) -> dict:
    if not body:
        return {}
    marker = "=== ORDER PAYLOAD ==="
    idx = body.find(marker)
    if idx < 0:
        return {}
    raw = body[idx + len(marker):].strip()
    next_marker = re.search(r"\n\s*=== ", raw)
    if next_marker:
        raw = raw[:next_marker.start()].strip()
    try:
        parsed = json.loads(raw)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _active_order_escalation_for(conn, conversation_id: str) -> dict | None:
    row = conn.execute(
        "SELECT id, channel, customer_name, subject, body, status, created_at, mode "
        "FROM pending_notifications "
        "WHERE customer_id = ? AND notification_type = 'escalation' "
        "AND mode = 'order' AND status != 'resolved' "
        "ORDER BY created_at DESC LIMIT 1",
        (conversation_id,),
    ).fetchone()
    if not row:
        return None
    payload = _extract_order_payload_from_body(row[4] or "")
    return {
        "id": row[0],
        "channel": row[1] or "whatsapp",
        "customer_name": row[2] or "",
        "subject": row[3] or "",
        "body": row[4] or "",
        "status": row[5] or "pending",
        "created_at": row[6] or "",
        "mode": row[7] or "order",
        "order_payload": payload,
    }


def _order_status_from_escalation_status(status: str | None) -> str:
    s = (status or "").strip().lower()
    if s == "confirmed":
        return "confirmed"
    return "awaiting_human_confirmation"


def _order_state_from_fields_and_flags(conversation_id: str, fields: dict,
                                       flags: dict, last_activity: str,
                                       channel: str = "whatsapp",
                                       fallback_name: str = "") -> dict | None:
    fields = fields or {}
    flags = flags or {}
    lines = _order_lines_from_fields(fields)
    order_like = bool(
        lines
        or fields.get("product_name")
        or fields.get("quantity")
        or fields.get("delivery_address")
        or fields.get("order_total")
        or flags.get("awaiting_order_confirmation")
        or flags.get("order_confirmed")
        or flags.get("waiting_for_human_order_confirmation")
        or flags.get("order_escalation_id")
    )
    if not order_like:
        return None
    status = "collecting_details"
    if flags.get("awaiting_order_confirmation"):
        status = "awaiting_customer_confirmation"
    if flags.get("waiting_for_human_order_confirmation") or flags.get("order_escalation_id"):
        status = "awaiting_human_confirmation"
    payload = _order_payload_from_state(conversation_id, fields, fallback_name, channel)
    return {
        "conversation_id": conversation_id,
        "customer_name": payload.get("customer_name") or fallback_name or conversation_id,
        "intent": "order",
        "is_order": True,
        "order_status": status,
        "order_payload": payload,
        "escalation_mode": "order" if status == "awaiting_human_confirmation" else None,
        "escalation_id": flags.get("order_escalation_id"),
        "human_action_required": status == "awaiting_human_confirmation",
        "ai_muted": bool(flags.get("fully_escalated")),
        "badge_type": "order",
        "queue_type": "orders",
        "next_operator_action": (
            "Confirm this order with the customer."
            if status == "awaiting_human_confirmation"
            else "Waiting for the customer to confirm the order summary."
            if status == "awaiting_customer_confirmation"
            else "Collect order details."
        ),
        "channel": channel or "whatsapp",
        "created_at": last_activity or datetime.now(timezone.utc).isoformat(),
        "updated_at": last_activity or datetime.now(timezone.utc).isoformat(),
        "source": "booking_state",
    }


def get_order_state_for_conversation(conversation_id: str) -> dict | None:
    if not conversation_id:
        return None
    conn = _get_conn()
    try:
        escalation = _active_order_escalation_for(conn, conversation_id)
        state_row = conn.execute(
            "SELECT fields_json, flags_json, last_activity "
            "FROM whatsapp_booking_state WHERE phone = ?",
            (conversation_id,),
        ).fetchone()
        fields = json.loads(state_row[0] or "{}") if state_row else {}
        flags = json.loads(state_row[1] or "{}") if state_row else {}
        last_activity = state_row[2] if state_row else ""
        sender_row = conn.execute(
            "SELECT sender_name, channel FROM whatsapp_threads "
            "WHERE phone = ? AND role = 'user' "
            "ORDER BY created_at DESC LIMIT 1",
            (conversation_id,),
        ).fetchone()
        fallback_name = sender_row[0] if sender_row and sender_row[0] else ""
        channel = sender_row[1] if sender_row and sender_row[1] else "whatsapp"
    finally:
        conn.close()
    state = _order_state_from_fields_and_flags(
        conversation_id, fields, flags, last_activity, channel, fallback_name)
    if escalation:
        payload = escalation.get("order_payload") or {}
        if not payload or not payload.get("products"):
            payload = (state or {}).get("order_payload") or payload
        payload = _merge_order_payload_updates(
            payload,
            (state or {}).get("order_payload") or {},
            fields,
        )
        order_status = _order_status_from_escalation_status(escalation.get("status"))
        next_operator_action = (
            "Prepare, deliver, and mark this order fulfilled."
            if order_status == "confirmed"
            else "Call the customer to confirm order details and delivery."
        )
        return {
            "conversation_id": conversation_id,
            "customer_name": payload.get("customer_name") or escalation.get("customer_name") or fallback_name or conversation_id,
            "intent": "order",
            "is_order": True,
            "order_status": order_status,
            "order_payload": payload,
            "escalation_mode": "order",
            "escalation_id": escalation.get("id"),
            "human_action_required": True,
            "ai_muted": True,
            "badge_type": "order",
            "queue_type": "orders",
            "next_operator_action": next_operator_action,
            "channel": escalation.get("channel") or channel,
            "created_at": escalation.get("created_at") or (state or {}).get("created_at"),
            "updated_at": escalation.get("created_at") or (state or {}).get("updated_at"),
            "source": "order_escalation",
            "subject": escalation.get("subject") or "",
        }
    return state


def list_order_queue() -> list:
    """Return active order queue items from explicit order state.

    This is intentionally separate from appointments. Orders can be
    waiting on customer confirmation or waiting for human confirmation;
    only the latter is also an escalation.
    """
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT w.phone, w.fields_json, w.flags_json, w.last_activity, "
            "COALESCE(cs.deleted, 0), COALESCE(cs.blocked, 0), "
            "(SELECT sender_name FROM whatsapp_threads t "
            " WHERE t.phone = w.phone AND t.role = 'user' AND t.sender_name != '' "
            " ORDER BY t.created_at DESC LIMIT 1) AS sender_name, "
            "(SELECT channel FROM whatsapp_threads t "
            " WHERE t.phone = w.phone ORDER BY t.created_at DESC LIMIT 1) AS channel "
            "FROM whatsapp_booking_state w "
            "LEFT JOIN conversation_status cs ON w.phone = cs.conversation_id "
            "WHERE COALESCE(cs.deleted, 0) = 0 AND COALESCE(cs.blocked, 0) = 0"
        ).fetchall()
        conversation_ids = {r[0] for r in rows}
        esc_rows = conn.execute(
            "SELECT customer_id FROM pending_notifications pn "
            "LEFT JOIN conversation_status cs ON pn.customer_id = cs.conversation_id "
            "WHERE pn.notification_type = 'escalation' AND pn.mode = 'order' "
            "AND pn.status != 'resolved' "
            "AND COALESCE(cs.deleted, 0) = 0 AND COALESCE(cs.blocked, 0) = 0"
        ).fetchall()
        conversation_ids.update(r[0] for r in esc_rows if r[0])
    finally:
        conn.close()

    items = []
    for cid in conversation_ids:
        state = get_order_state_for_conversation(cid)
        if not state:
            continue
        if state.get("order_status") not in (
            "awaiting_customer_confirmation",
            "awaiting_human_confirmation",
            "confirmed",
        ):
            continue
        items.append(state)
    items.sort(key=lambda r: r.get("updated_at") or r.get("created_at") or "", reverse=True)
    return items


def mark_order_phone_confirmed(escalation_id: int) -> bool:
    """Mark an active order escalation as phone-confirmed.

    Orders stay in the Orders queue after this transition. The final
    fulfillment action still resolves the order and removes it from the
    active queue.
    """
    conn = _get_conn()
    cur = conn.execute(
        "UPDATE pending_notifications SET status = 'confirmed' "
        "WHERE id = ? AND notification_type = 'escalation' "
        "AND mode = 'order' AND status != 'resolved'",
        (escalation_id,),
    )
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    return changed


def wa_save_booking_state(phone: str, fields: dict, flags: dict,
                          completed_bookings: list = None):
    """Save/update booking state for a phone number."""
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()
    cb = json.dumps(completed_bookings or [], ensure_ascii=False)
    conn.execute(
        "INSERT OR REPLACE INTO whatsapp_booking_state "
        "(phone, fields_json, flags_json, completed_bookings_json, last_activity, created_at) "
        "VALUES (?, ?, ?, ?, ?, COALESCE("
        "(SELECT created_at FROM whatsapp_booking_state WHERE phone = ?), ?))",
        (phone, json.dumps(fields, ensure_ascii=False),
         json.dumps(flags, ensure_ascii=False), cb, now, phone, now)
    )
    from shared import mermaid_customers
    mermaid_customers.capture(conn, phone, intake=fields.get("mermaid_intake"), at=now)
    conn.commit()
    conn.close()


def wa_mark_vehicle_recommendation_delivered(
    phone: str,
    state_hash: str,
    delivery: str,
    vehicle_ids: list[str] | None = None,
) -> bool:
    """Atomically remember one accepted Ali discovery presentation.

    Only the flags JSON is changed so a provider callback cannot overwrite
    fields persisted by a newer customer turn.
    """
    if not re.fullmatch(r"[0-9a-f]{64}", str(state_hash or "")):
        return False
    if delivery not in {
        "image", "carousel", "fallback", "carousel_picker",
        "carousel_picker_fallback", "picker", "picker_fallback",
        "individual_picker", "individual_picker_fallback",
    }:
        return False
    normalized_vehicle_ids = []
    for value in vehicle_ids or []:
        vehicle_id = str(value or "").strip()
        if (
            vehicle_id
            and len(vehicle_id) <= 160
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]*", vehicle_id)
            and vehicle_id not in normalized_vehicle_ids
        ):
            normalized_vehicle_ids.append(vehicle_id)
    normalized_vehicle_ids = normalized_vehicle_ids[:5]
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT flags_json FROM whatsapp_booking_state WHERE phone = ?",
            (phone,),
        ).fetchone()
        if not row:
            conn.rollback()
            return False
        flags = json.loads(row[0] or "{}")
        existing = flags.get("ali_vehicle_recommendation_deliveries") or []
        normalized = [
            item for item in existing
            if isinstance(item, dict)
            and re.fullmatch(r"[0-9a-f]{64}", str(item.get("hash") or ""))
        ]
        if not any(item["hash"] == state_hash for item in normalized):
            normalized.append({"hash": state_hash, "delivery": delivery})
        flags["ali_vehicle_recommendation_deliveries"] = normalized[-20:]
        if normalized_vehicle_ids:
            flags["ali_last_recommendation_ids"] = normalized_vehicle_ids
            shown = [
                str(value).strip()
                for value in flags.get("ali_shown_vehicle_ids") or []
                if isinstance(value, str) and str(value).strip()
            ]
            for vehicle_id in normalized_vehicle_ids:
                if vehicle_id not in shown:
                    shown.append(vehicle_id)
            flags["ali_shown_vehicle_ids"] = shown[-40:]
        conn.execute(
            "UPDATE whatsapp_booking_state SET flags_json = ? WHERE phone = ?",
            (json.dumps(flags, ensure_ascii=False), phone),
        )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def wa_reconcile_vehicle_recommendation_failure(
    phone: str,
    provider_message_id: str,
) -> bool:
    """Undo a recommendation marker when WhatsApp later reports failure.

    Zernio may accept an interactive message and emit ``message.failed`` only
    afterwards. Removing the matching delivery prevents Nick from believing a
    car was shown and allows a safe retry or text fallback on a later turn.
    """
    message_id = str(provider_message_id or "").strip()
    if not phone or not message_id or len(message_id) > 240:
        return False
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT flags_json FROM whatsapp_booking_state WHERE phone = ?",
            (phone,),
        ).fetchone()
        if not row:
            conn.rollback()
            return False
        flags = json.loads(row[0] or "{}")
        deliveries = [
            item for item in flags.get("ali_vehicle_recommendation_deliveries") or []
            if isinstance(item, dict)
        ]
        failed = [
            item for item in deliveries
            if message_id in {
                str(value or "").strip()
                for value in item.get("provider_message_ids") or []
            }
        ]
        if not failed:
            pending = [
                str(value or "").strip()
                for value in flags.get("ali_failed_provider_message_ids") or []
                if str(value or "").strip()
            ]
            if message_id not in pending:
                pending.append(message_id)
            flags["ali_failed_provider_message_ids"] = pending[-20:]
            conn.execute(
                "UPDATE whatsapp_booking_state SET flags_json = ? WHERE phone = ?",
                (json.dumps(flags, ensure_ascii=False), phone),
            )
            conn.commit()
            return True
        remaining = [item for item in deliveries if item not in failed]
        flags["ali_vehicle_recommendation_deliveries"] = remaining[-20:]

        failed_vehicle_ids = {
            str(value or "").strip()
            for item in failed
            for value in item.get("vehicle_ids") or []
            if str(value or "").strip()
        }
        remaining_vehicle_ids = {
            str(value or "").strip()
            for item in remaining
            for value in item.get("vehicle_ids") or []
            if str(value or "").strip()
        }
        not_shown = failed_vehicle_ids - remaining_vehicle_ids
        if not_shown:
            flags["ali_shown_vehicle_ids"] = [
                str(value).strip()
                for value in flags.get("ali_shown_vehicle_ids") or []
                if str(value).strip() and str(value).strip() not in not_shown
            ]
        failed_action_ids = {
            str(item.get("action_id") or "").strip()
            for item in failed
            if str(item.get("action_id") or "").strip()
        }
        if str(flags.get("ali_last_delivery_action_id") or "") in failed_action_ids:
            flags.pop("ali_last_delivery_action_id", None)
            flags.pop("ali_last_delivered_kind", None)
            flags["ali_last_recommendation_ids"] = [
                str(value).strip()
                for value in flags.get("ali_last_recommendation_ids") or []
                if str(value).strip() and str(value).strip() not in not_shown
            ]
        conn.execute(
            "UPDATE whatsapp_booking_state SET flags_json = ? WHERE phone = ?",
            (json.dumps(flags, ensure_ascii=False), phone),
        )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _vehicle_recommendation_recovery_claims(raw: object) -> list[dict]:
    """Normalize durable failure claims, including legacy string entries."""
    claims = []
    for value in raw if isinstance(raw, list) else []:
        if isinstance(value, dict):
            message_id = str(value.get("message_id") or "").strip()
            claim_token = str(value.get("claim_token") or "").strip()
            lease_expires_at = str(value.get("lease_expires_at") or "").strip()
        else:
            # The old format stored only message ids and could wedge forever.
            # Treat it as an expired, unfenced claim so the next provider retry
            # can migrate and reclaim it.
            message_id = str(value or "").strip()
            claim_token = ""
            lease_expires_at = ""
        if message_id:
            claims.append({
                "message_id": message_id,
                "claim_token": claim_token,
                "lease_expires_at": lease_expires_at,
            })
    return claims


def _vehicle_recommendation_claim_active(
    claim: dict,
    now: datetime,
) -> bool:
    try:
        expires_at = datetime.fromisoformat(
            str(claim.get("lease_expires_at") or "")
        )
    except (TypeError, ValueError):
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return bool(claim.get("claim_token")) and expires_at > now


def wa_claim_vehicle_recommendation_failure(
    phone: str,
    provider_message_id: str,
) -> dict:
    """Lease one known recommendation-part failure for replay-safe recovery."""
    message_id = str(provider_message_id or "").strip()
    if not phone or not message_id or len(message_id) > 240:
        return {"matched": False}
    now_dt = datetime.now(timezone.utc)
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT flags_json FROM whatsapp_booking_state WHERE phone = ?",
            (phone,),
        ).fetchone()
        if not row:
            conn.rollback()
            return {"matched": False}
        flags = json.loads(row[0] or "{}")
        deliveries = [
            item for item in flags.get("ali_vehicle_recommendation_deliveries") or []
            if isinstance(item, dict)
        ]
        matched = None
        matched_part = ""
        for delivery in reversed(deliveries):
            parts = delivery.get("provider_parts")
            parts = parts if isinstance(parts, dict) else {}
            for part, values in parts.items():
                if message_id in {
                    str(value or "").strip() for value in values or []
                }:
                    matched = delivery
                    matched_part = str(part)
                    break
            if matched:
                break
        if not matched:
            conn.commit()
            return {"matched": False}
        handled = [
            str(value or "").strip()
            for value in flags.get("ali_vehicle_recommendation_handled_failures") or []
            if str(value or "").strip()
        ]
        if message_id in handled:
            conn.commit()
            return {
                "matched": True,
                "already_handled": True,
                "part": matched_part,
            }
        claims = _vehicle_recommendation_recovery_claims(
            flags.get("ali_vehicle_recommendation_recovery_in_progress")
        )
        active_claim = next(
            (
                claim
                for claim in claims
                if claim["message_id"] == message_id
                and _vehicle_recommendation_claim_active(claim, now_dt)
            ),
            None,
        )
        if active_claim is not None:
            conn.commit()
            return {
                "matched": True,
                "already_handled": True,
                "recovery_in_progress": True,
                "lease_expires_at": active_claim["lease_expires_at"],
                "part": matched_part,
            }
        if matched_part not in {"carousel", "carousel_retry"}:
            conn.commit()
            return {
                "matched": True,
                "already_handled": True,
                "part": matched_part,
            }
        claims = [
            claim
            for claim in claims
            if claim["message_id"] != message_id
            and _vehicle_recommendation_claim_active(claim, now_dt)
        ]
        claim_token = hashlib.sha256(os.urandom(32)).hexdigest()
        lease_expires_at = (
            now_dt
            + timedelta(seconds=VEHICLE_RECOMMENDATION_RECOVERY_LEASE_SECONDS)
        ).isoformat()
        claims.append({
            "message_id": message_id,
            "claim_token": claim_token,
            "lease_expires_at": lease_expires_at,
        })
        flags["ali_vehicle_recommendation_recovery_in_progress"] = claims[-20:]
        conn.execute(
            "UPDATE whatsapp_booking_state SET flags_json = ? WHERE phone = ?",
            (json.dumps(flags, ensure_ascii=False), phone),
        )
        conn.commit()
        parts = matched.get("provider_parts") or {}
        return {
            "matched": True,
            "already_handled": False,
            "claim_token": claim_token,
            "lease_expires_at": lease_expires_at,
            "failed_message_id": message_id,
            "part": matched_part,
            "stage": "individual" if matched_part == "carousel_retry" else "retry",
            "hash": str(matched.get("hash") or ""),
            "snapshot": matched.get("snapshot") or {},
            "account_id": str(matched.get("account_id") or ""),
            "picker_present": bool(parts.get("picker") or parts.get("picker_fallback")),
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def wa_stage_vehicle_recommendation_delivery(
    phone: str,
    recommendation: dict,
    delivery: str,
    provider_parts: dict[str, list[str]],
    account_id: str,
) -> bool:
    """Persist provider part IDs before a late failure webhook can race commit."""
    state_hash = str((recommendation or {}).get("state_hash") or "")
    if not phone or not re.fullmatch(r"[0-9a-f]{64}", state_hash):
        return False
    vehicle_ids = []
    for option in (recommendation or {}).get("options") or []:
        if not isinstance(option, dict):
            continue
        value = str(option.get("id") or "").strip()
        if (
            value
            and len(value) <= 160
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]*", value)
            and value not in vehicle_ids
        ):
            vehicle_ids.append(value)
    normalized_parts = {}
    for part, values in (provider_parts or {}).items():
        if part not in {
            "image", "carousel", "picker", "picker_fallback", "individual_images",
        }:
            continue
        ids = list(dict.fromkeys(
            str(value or "").strip()
            for value in values or []
            if str(value or "").strip() and len(str(value or "").strip()) <= 240
        ))[:10]
        if ids:
            normalized_parts[part] = ids
    if not normalized_parts:
        return False
    snapshot = {
        "kind": str((recommendation or {}).get("kind") or "")[:20],
        "mode": str((recommendation or {}).get("mode") or "")[:20],
        "locale": str((recommendation or {}).get("locale") or "en")[:5],
        "state_hash": state_hash,
        "text": str((recommendation or {}).get("text") or "")[:1500],
        "vehicle_ids": vehicle_ids[:5],
        "trigger_message_id": str(
            (recommendation or {}).get("trigger_message_id") or ""
        ).strip()[:240],
        "trigger_sent_at": str(
            (recommendation or {}).get("trigger_sent_at") or ""
        ).strip()[:80],
    }
    if snapshot["kind"] not in {"image", "carousel"} or not snapshot["text"]:
        return False
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT flags_json FROM whatsapp_booking_state WHERE phone = ?", (phone,),
        ).fetchone()
        if not row:
            conn.rollback()
            return False
        flags = json.loads(row[0] or "{}")
        deliveries = [
            item for item in flags.get("ali_vehicle_recommendation_deliveries") or []
            if isinstance(item, dict)
        ]
        record = next((
            item for item in reversed(deliveries) if item.get("hash") == state_hash
        ), None)
        if record is None:
            record = {"hash": state_hash}
            deliveries.append(record)
        parts = record.get("provider_parts")
        parts = dict(parts) if isinstance(parts, dict) else {}
        parts.update(normalized_parts)
        flat_ids = list(dict.fromkeys(
            value for values in parts.values() for value in values
        ))[:20]
        record.update({
            "delivery": delivery,
            "vehicle_ids": vehicle_ids[:5],
            "provider_message_ids": flat_ids,
            "provider_parts": parts,
            "snapshot": snapshot,
            "account_id": str(account_id or "").strip()[:240],
            "recovery_attempts": int(record.get("recovery_attempts") or 0),
            "staged": True,
        })
        flags["ali_vehicle_recommendation_deliveries"] = deliveries[-20:]
        conn.execute(
            "UPDATE whatsapp_booking_state SET flags_json = ? WHERE phone = ?",
            (json.dumps(flags, ensure_ascii=False), phone),
        )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def wa_complete_vehicle_recommendation_recovery(
    phone: str,
    recovery: dict,
    result: dict,
) -> bool:
    """Persist recovery part IDs and release the transactional claim."""
    failed_id = str((recovery or {}).get("failed_message_id") or "").strip()
    state_hash = str((recovery or {}).get("hash") or "").strip()
    claim_token = str((recovery or {}).get("claim_token") or "").strip()
    if (
        not failed_id
        or not claim_token
        or not re.fullmatch(r"[0-9a-f]{64}", state_hash)
    ):
        return False
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT flags_json FROM whatsapp_booking_state WHERE phone = ?",
            (phone,),
        ).fetchone()
        if not row:
            conn.rollback()
            return False
        flags = json.loads(row[0] or "{}")
        claims = _vehicle_recommendation_recovery_claims(
            flags.get("ali_vehicle_recommendation_recovery_in_progress")
        )
        if not any(
            claim["message_id"] == failed_id
            and claim["claim_token"] == claim_token
            for claim in claims
        ):
            conn.rollback()
            return False
        deliveries = flags.get("ali_vehicle_recommendation_deliveries") or []
        delivery = next((
            item for item in reversed(deliveries)
            if isinstance(item, dict) and item.get("hash") == state_hash
        ), None)
        if not delivery:
            conn.rollback()
            return False
        parts = delivery.get("provider_parts")
        parts = dict(parts) if isinstance(parts, dict) else {}
        result_parts = (result or {}).get("provider_parts")
        result_parts = result_parts if isinstance(result_parts, dict) else {}
        if (result or {}).get("success"):
            for part, values in result_parts.items():
                target = "carousel_retry" if (
                    part == "carousel"
                    and (result or {}).get("delivery") == "carousel_retry"
                ) else part
                normalized = [
                    str(value or "").strip()
                    for value in values or []
                    if str(value or "").strip()
                ][:10]
                if normalized:
                    parts[target] = normalized
            delivery["provider_parts"] = parts
            delivery["recovery_attempts"] = int(delivery.get("recovery_attempts") or 0) + 1
            delivery["recovery_delivery"] = str((result or {}).get("delivery") or "")[:40]
        handled = [
            str(value or "").strip()
            for value in flags.get("ali_vehicle_recommendation_handled_failures") or []
            if str(value or "").strip()
        ]
        if failed_id not in handled:
            handled.append(failed_id)
        flags["ali_vehicle_recommendation_handled_failures"] = handled[-20:]
        flags["ali_vehicle_recommendation_recovery_in_progress"] = [
            claim
            for claim in claims
            if not (
                claim["message_id"] == failed_id
                and claim["claim_token"] == claim_token
            )
        ][-20:]
        conn.execute(
            "UPDATE whatsapp_booking_state SET flags_json = ? WHERE phone = ?",
            (json.dumps(flags, ensure_ascii=False), phone),
        )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _get_email_state_path() -> str:
    """Brief 171: resolve the email_thread_state.json path the email_poller uses."""
    # email_poller stores it at /app/config/email_thread_state.json inside the container.
    # Fall back to the source-tree clients/bluemarlin path for local dev.
    _cfg = os.environ.get("CLIENT_CONFIG_PATH", "")
    candidates = [
        "/app/config/email_thread_state.json",
    ]
    if _cfg:
        candidates.insert(0, os.path.join(os.path.dirname(_cfg), "email_thread_state.json"))
    for p in candidates:
        if os.path.exists(p):
            return p
    return candidates[0]


@contextmanager
def email_state_file_lock(path: str):
    """Serialize email sidecar updates across poller and dashboard processes."""
    lock_path = str(path) + ".lock"
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with _EMAIL_STATE_PROCESS_LOCK:
        with open(lock_path, "a+", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _legacy_email_archive_path(path: str) -> str:
    return os.path.join(os.path.dirname(path), "archived_threads.jsonl")


def _merge_legacy_email_thread(archived: dict, current: dict) -> dict:
    """Restore archived history without hiding or rolling back a newer thread.

    The legacy poller removed an archived subject thread from the main sidecar.
    A later inbound email could therefore recreate the same key before this
    migration runs.  The new live values win, while old transcript, fields and
    completed-booking history remain available for export and retention.
    """
    merged = copy.deepcopy(archived)
    for key, value in current.items():
        if key == "messages" and isinstance(value, list):
            merged[key] = _merge_email_messages(
                [], value, archived.get("messages") or []
            )
        elif key == "completed_bookings" and isinstance(value, list):
            historical = archived.get(key) or []
            combined = copy.deepcopy(historical)
            seen = {
                json.dumps(item, sort_keys=True, ensure_ascii=False, default=str)
                for item in combined
            }
            for item in value:
                identity = json.dumps(
                    item, sort_keys=True, ensure_ascii=False, default=str
                )
                if identity not in seen:
                    combined.append(copy.deepcopy(item))
                    seen.add(identity)
            merged[key] = combined
        elif isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = {**copy.deepcopy(merged[key]), **copy.deepcopy(value)}
        else:
            merged[key] = copy.deepcopy(value)

    # A recreated live thread is authoritative about archive visibility.  The
    # migration's synthetic deleted marker must not immediately hide it again.
    current_flags = current.get("flags") or {}
    if not current_flags.get("deleted"):
        merged.setdefault("flags", {}).pop("deleted", None)
    return merged


def _read_email_state_unlocked(path: str, default=None, *, strict: bool = False):
    fallback = default if default is not None else {"threads": {}, "message_id_index": {}}
    legacy_path = _legacy_email_archive_path(path)
    try:
        with open(path, "r", encoding="utf-8") as state_file:
            state = json.load(state_file)
    except FileNotFoundError:
        # A pre-governance poller could leave the raw archive as the only email
        # store after moving every active thread out of the main sidecar.  That
        # archive must still participate in export and privacy deletion.
        if not os.path.exists(legacy_path):
            if strict:
                raise
            return copy.deepcopy(fallback)
        state = copy.deepcopy(fallback)
    except (OSError, ValueError, TypeError):
        if strict:
            raise
        return copy.deepcopy(fallback)
    if not isinstance(state, dict):
        if strict:
            raise ValueError("Email conversation state must be an object")
        state = copy.deepcopy(fallback)
    if not os.path.exists(legacy_path):
        return state

    # Older pollers moved full email conversations into an untracked JSONL
    # shadow archive. Move those rows back into the governed sidecar as normal
    # archived threads, then remove the raw shadow only after the atomic write.
    try:
        with open(legacy_path, "r", encoding="utf-8") as archive_file:
            records = [
                json.loads(line)
                for line in archive_file
                if line.strip()
            ]
    except (OSError, ValueError, TypeError):
        if strict:
            raise
        return state
    threads = state.setdefault("threads", {})
    changed = False
    for record in records:
        if not isinstance(record, dict):
            if strict:
                raise ValueError("Legacy email archive contains an invalid record")
            return state
        thread_key = str(record.get("thread_key") or "")
        archived = record.get("data")
        if not thread_key or not isinstance(archived, dict):
            if strict:
                raise ValueError("Legacy email archive contains an invalid record")
            return state
        archived = copy.deepcopy(archived)
        archived.setdefault("flags", {})["deleted"] = True
        current = threads.get(thread_key)
        if isinstance(current, dict):
            threads[thread_key] = _merge_legacy_email_thread(archived, current)
        else:
            threads[thread_key] = archived
        changed = True
    if changed or not records:
        try:
            _write_email_state_unlocked(path, state)
            os.remove(legacy_path)
        except OSError:
            if strict:
                raise
    return state


def _write_email_state_unlocked(path: str, state: dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp_path = f"{path}.tmp.{os.getpid()}.{threading.get_ident()}"
    try:
        with open(tmp_path, "w", encoding="utf-8") as state_file:
            json.dump(state, state_file, ensure_ascii=False, indent=2)
            state_file.flush()
            os.fsync(state_file.fileno())
        os.replace(tmp_path, path)
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass


def email_state_read(path: str, default=None):
    """Return a consistent snapshot of the shared email sidecar."""
    with email_state_file_lock(path):
        return _read_email_state_unlocked(path, default)


def _message_identity(message) -> str:
    if not isinstance(message, dict):
        return "value:" + json.dumps(message, sort_keys=True, default=str)
    for key in ("source_message_key", "message_id", "internet_message_id"):
        value = str(message.get(key) or "").strip()
        if value:
            return key + ":" + value
    stable = {
        key: message.get(key)
        for key in ("role", "ts", "timestamp", "created_at", "body", "text")
        if key in message
    }
    return "content:" + hashlib.sha256(
        json.dumps(stable, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()


def _merge_email_messages(base: list, local: list, remote: list) -> list:
    """Merge concurrent append-only transcript changes without resurrecting removals."""
    base_ids = {_message_identity(item) for item in base}
    remote_by_id = {_message_identity(item): copy.deepcopy(item) for item in remote}
    merged = [copy.deepcopy(item) for item in remote]
    present = set(remote_by_id)
    for item in local:
        identity = _message_identity(item)
        if identity in present or identity in base_ids:
            continue
        merged.append(copy.deepcopy(item))
        present.add(identity)
    return merged


def _three_way_email_merge(base, local, remote, path=()):
    if local == base:
        return copy.deepcopy(remote)
    if remote == base or local == remote:
        return copy.deepcopy(local)
    if isinstance(base, dict) and isinstance(local, dict) and isinstance(remote, dict):
        merged = {}
        for key in set(base) | set(local) | set(remote):
            base_value = base.get(key, _MISSING)
            local_value = local.get(key, _MISSING)
            remote_value = remote.get(key, _MISSING)
            if local_value is _MISSING:
                if base_value is _MISSING:
                    merged[key] = copy.deepcopy(remote_value)
                elif remote_value is not _MISSING and remote_value != base_value:
                    merged[key] = copy.deepcopy(remote_value)
                continue
            if remote_value is _MISSING:
                if base_value is _MISSING:
                    merged[key] = copy.deepcopy(local_value)
                # A concurrent disk-side deletion wins over stale local state.
                continue
            if base_value is _MISSING:
                if isinstance(local_value, dict) and isinstance(remote_value, dict):
                    merged[key] = _three_way_email_merge({}, local_value, remote_value, path + (key,))
                elif local_value == remote_value:
                    merged[key] = copy.deepcopy(local_value)
                else:
                    merged[key] = copy.deepcopy(local_value)
                continue
            merged[key] = _three_way_email_merge(
                base_value, local_value, remote_value, path + (key,)
            )
        return merged
    if (
        isinstance(base, list)
        and isinstance(local, list)
        and isinstance(remote, list)
        and path
        and path[-1] == "messages"
    ):
        return _merge_email_messages(base, local, remote)
    # Both sides changed the same scalar/list. The poller is committing its
    # deliberate mutation now; unchanged local values were handled above.
    return copy.deepcopy(local)


def email_state_merge_save(path: str, base: dict, local: dict) -> dict:
    """Three-way merge a poller snapshot with newer dashboard-side writes."""
    with email_state_file_lock(path):
        remote = _read_email_state_unlocked(path, {})
        merged = _three_way_email_merge(base or {}, local or {}, remote or {})
        _write_email_state_unlocked(path, merged)
        return merged


def _email_thread_address(thread_key: str) -> str:
    parts = str(thread_key or "").split(":", 2)
    if len(parts) != 3 or parts[0] != "subj" or "@" not in parts[1]:
        return ""
    return parts[1].strip().casefold()


def _find_email_thread_key_in_state(state: dict, customer_email: str) -> str | None:
    needle = str(customer_email or "").strip().casefold()
    if not needle:
        return None
    matches = [
        (str((thread or {}).get("last_activity") or ""), thread_key)
        for thread_key, thread in (state.get("threads") or {}).items()
        if _email_thread_address(thread_key) == needle
    ]
    return max(matches, default=("", None))[1]


def email_thread_customer(thread_key: str) -> str:
    """Return the exact stored thread address, or empty when the key is forged."""
    path = _get_email_state_path()
    with email_state_file_lock(path):
        state = _read_email_state_unlocked(path, {})
        if thread_key not in (state.get("threads") or {}):
            return ""
        return _email_thread_address(thread_key)


def email_list_conversations() -> list:
    """Brief 171: return email threads in the same shape as wa_list_conversations
    (phone, customer_name, last_message, last_message_role, last_message_at,
    status, message_count, channel) so the dashboard Messages page can merge them.

    The `phone` field carries an `email::` prefix to disambiguate from WhatsApp
    rows and make the URL unambiguous for the detail endpoint."""
    path = _get_email_state_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r") as f:
            state = json.load(f)
    except Exception:
        return []
    threads = state.get("threads", {})
    result = []
    for thread_key, th in threads.items():
        messages = th.get("messages", []) or []
        if not messages:
            continue
        last = messages[-1]
        last_ts = last.get("ts") or last.get("timestamp") or ""
        last_role = last.get("role", "")
        last_body = (last.get("body") or last.get("text") or "")[:200]
        # Normalize role: customer -> user, marina -> assistant.
        # Brief 233: 'operator' passes through unchanged so the inbox
        # list can show a distinct indicator for operator-typed replies.
        if last_role == "customer":
            last_role = "user"
        elif last_role == "marina":
            last_role = "assistant"
        fields = th.get("fields", {}) or {}
        flags = th.get("flags", {}) or {}
        customer_name = fields.get("customer_name") or ""
        if not customer_name:
            # derive from thread_key like "subj:alice@x.com:..." → alice@x.com
            parts = thread_key.split(":", 2)
            if len(parts) >= 2:
                customer_name = parts[1]
        # Brief 218: skip threads marked deleted (the dashboard hides them
        # from the active inbox; provider-side cleanup is a follow-up).
        if flags.get("deleted"):
            continue
        # Brief 261: skip threads whose customer email is blocked
        # (issue #30 - the inbound suppression at email_poller.py:685
        # already blocks new mail, this hides the existing thread row
        # from the active inbox). Extract email from thread_key shape
        # "subj:<email>:<normalized_subject>".
        _tk_parts = thread_key.split(":", 2)
        if len(_tk_parts) >= 2 and _tk_parts[0] == "subj":
            if is_system_email_sender(_tk_parts[1]):
                continue
            if get_blocked(_tk_parts[1]):
                continue
        email_address = _email_thread_address(thread_key)
        has_active_review = bool(
            email_address and get_active_escalation_mode(email_address) is not None
        )
        status = "escalated" if (
            flags.get("fully_escalated")
            or flags.get("awaiting_relay")
            or has_active_review
        ) else "active"
        result.append({
            "phone": f"email::{thread_key}",
            "customer_name": customer_name or "(email customer)",
            "last_message": last_body,
            "last_message_role": last_role,
            "last_message_at": last_ts,
            "status": status,
            "message_count": len(messages),
            "channel": "email",
        })
    # Sort newest first
    result.sort(key=lambda r: r["last_message_at"] or "", reverse=True)
    return result


def email_set_archived(thread_key: str, archived: bool) -> bool:
    """Brief 249: toggle the archive state on an email thread. Sets/clears
    flags.deleted in email_thread_state.json (the existing Brief 218 +
    Brief 237 'archived' semantic - the flag is named 'deleted' for
    historical reasons but semantically means 'hidden from active inbox',
    NOT hard-removed from storage). Returns True if the thread was found
    and updated; False if no matching thread_key in state."""
    path = _get_email_state_path()
    with email_state_file_lock(path):
        if not os.path.exists(path):
            return False
        state = _read_email_state_unlocked(path, {})
        threads = state.get("threads") or {}
        th = threads.get(thread_key)
        if not th:
            return False
        flags = th.setdefault("flags", {})
        if archived:
            flags["deleted"] = True
        else:
            # Unarchive -- remove the key entirely so a future re-read sees
            # the thread as never-archived (clean shape).
            flags.pop("deleted", None)
        try:
            _write_email_state_unlocked(path, state)
        except OSError:
            return False
        return True


def wa_set_archived(conversation_id: str, archived: bool) -> bool:
    """Brief 249: toggle the archive state on a WhatsApp/IG/FB
    conversation. Sets/clears conversation_status.deleted (the existing
    Brief 218 + Brief 237 'archived' semantic). UPSERTs the
    conversation_status row when missing so manual archive works for
    conversations that have no prior status entry. Returns True; raises
    on DB error (caller wraps if it cares)."""
    if not conversation_id:
        return False
    now = datetime.now(timezone.utc).isoformat()
    deleted_int = 1 if archived else 0
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT INTO conversation_status "
            "(conversation_id, channel, status, updated_at, deleted) "
            "VALUES (?, 'whatsapp', ?, ?, ?) "
            "ON CONFLICT(conversation_id) DO UPDATE SET "
            "deleted = excluded.deleted, updated_at = excluded.updated_at",
            (conversation_id, "archived" if archived else "active",
             now, deleted_int))
        conn.commit()
    finally:
        conn.close()
    return True


def email_list_archived_conversations() -> list:
    """Brief 249: return email threads with flags.deleted=true (the
    inverse of email_list_conversations' filter). Same response shape
    as email_list_conversations so the frontend can swap the data
    source by URL without re-mapping fields."""
    path = _get_email_state_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r") as f:
            state = json.load(f)
    except Exception:
        return []
    threads = state.get("threads", {})
    result = []
    for thread_key, th in threads.items():
        messages = th.get("messages", []) or []
        if not messages:
            continue
        flags = th.get("flags", {}) or {}
        # Inverse filter: only archived (deleted=true) rows.
        if not flags.get("deleted"):
            continue
        _tk_parts = thread_key.split(":", 2)
        if len(_tk_parts) >= 2 and _tk_parts[0] == "subj":
            if is_system_email_sender(_tk_parts[1]):
                continue
        last = messages[-1]
        last_ts = last.get("ts") or last.get("timestamp") or ""
        last_role = last.get("role", "")
        last_body = (last.get("body") or last.get("text") or "")[:200]
        if last_role == "customer":
            last_role = "user"
        elif last_role == "marina":
            last_role = "assistant"
        fields = th.get("fields", {}) or {}
        customer_name = fields.get("customer_name") or ""
        if not customer_name:
            parts = thread_key.split(":", 2)
            if len(parts) >= 2:
                customer_name = parts[1]
        result.append({
            "phone": f"email::{thread_key}",
            "customer_name": customer_name or "(email customer)",
            "last_message": last_body,
            "last_message_role": last_role,
            "last_message_at": last_ts,
            "status": "archived",
            "message_count": len(messages),
            "channel": "email",
        })
    result.sort(key=lambda r: r["last_message_at"] or "", reverse=True)
    return result


def wa_list_archived_conversations() -> list:
    """Brief 249: return WhatsApp/IG/FB conversations with
    conversation_status.deleted=1 (the inverse of wa_list_conversations'
    new Brief 249 filter). Same response shape as wa_list_conversations."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT t.phone, t.text, t.created_at, t.role, t.channel "
        "FROM whatsapp_threads t "
        "INNER JOIN ("
        "  SELECT phone, MAX(created_at) as max_ts "
        "  FROM whatsapp_threads GROUP BY phone"
        ") latest ON t.phone = latest.phone AND t.created_at = latest.max_ts "
        "INNER JOIN conversation_status cs ON t.phone = cs.conversation_id "
        "WHERE cs.deleted = 1 "
        "ORDER BY t.created_at DESC"
    ).fetchall()
    conversations = []
    for r in rows:
        phone = r[0]
        state_row = conn.execute(
            "SELECT fields_json, flags_json FROM whatsapp_booking_state "
            "WHERE phone = ?", (phone,)
        ).fetchone()
        fields = json.loads(state_row[0] or "{}") if state_row else {}
        flags = json.loads(state_row[1] or "{}") if state_row else {}
        loop_status = mermaid_loop_status(flags)
        name = (fields.get("customer_name") or fields.get("name") or "")
        if not name:
            sender_row = conn.execute(
                "SELECT sender_name FROM whatsapp_threads WHERE phone = ? "
                "AND role = 'user' AND sender_name != '' "
                "ORDER BY created_at DESC LIMIT 1", (phone,)
            ).fetchone()
            if sender_row and sender_row[0]:
                name = sender_row[0]
        if not name:
            name = phone
        count_row = conn.execute(
            "SELECT COUNT(*) FROM whatsapp_threads WHERE phone = ?", (phone,)
        ).fetchone()
        conversations.append({
            "phone": phone,
            "customer_name": name,
            "last_message": loop_status or (r[1] or "")[:200],
            "last_message_role": r[3] or "",
            "last_message_at": r[2] or "",
            "status": "archived",
            "message_count": count_row[0] if count_row else 0,
            "channel": r[4] if len(r) > 4 and r[4] else "whatsapp",
            "loop_stopped": loop_status is not None,
            "loop_status": loop_status,
            "loop_stopped_at": flags.get("mermaid_loop_stopped_at") if loop_status else None,
        })
    conn.close()
    return conversations


def email_get_conversation(thread_key: str) -> dict:
    """Brief 171: return full message history + fields for an email thread.
    Messages are normalized to the WhatsApp shape: {role, text, created_at}."""
    path = _get_email_state_path()
    state = email_state_read(path, {})
    th = state.get("threads", {}).get(thread_key, {})
    raw_messages = th.get("messages", []) or []
    out_messages = []
    for m in raw_messages:
        role = m.get("role", "")
        if role == "customer":
            role = "user"
        elif role == "marina":
            role = "assistant"
        # Brief 233: 'operator' passes through unchanged so the frontend
        # can render verbatim operator replies distinctly from Marina-
        # generated ones. SR's existing mapper falls back to "assistant"
        # for unknown values, so this is a graceful no-op until the
        # frontend opts into the new value.
        text = m.get("body") or m.get("text") or ""
        ts = m.get("ts") or m.get("timestamp") or ""
        out_messages.append({"role": role, "text": text, "created_at": ts})
    return {
        "phone": f"email::{thread_key}",
        "messages": out_messages,
        "booking_state": {
            "fields": th.get("fields", {}) or {},
            "flags": th.get("flags", {}) or {},
            "completed_bookings": th.get("completed_bookings", []) or [],
            "last_activity": th.get("last_activity"),
        },
    }


def _find_email_thread_key_for(customer_email: str):
    """Brief 211: locate the email_thread_state.json thread_key for a given
    customer email. Used by /escalations to expose a routable conversation
    key for email rows, and by email_append_assistant_message to find the
    thread for an outbound reply. Returns the thread_key string or None
    if no thread exists yet for this customer."""
    path = _get_email_state_path()
    with email_state_file_lock(path):
        state = _read_email_state_unlocked(path, {})
        return _find_email_thread_key_in_state(state, customer_email)


def email_mark_deleted(thread_key: str) -> bool:
    """Brief 218: mark an email thread as deleted in our local state.
    The thread is filtered out of email_list_conversations. Provider-side
    IMAP MOVE to trash is deferred — local-state only for v1.
    Returns True on success, False if no such thread."""
    path = _get_email_state_path()
    with email_state_file_lock(path):
        if not os.path.exists(path):
            return False
        state = _read_email_state_unlocked(path, {})
        threads = state.get("threads", {})
        if thread_key not in threads:
            return False
        th = threads[thread_key]
        th.setdefault("flags", {})["deleted"] = True
        th["last_activity"] = datetime.now(timezone.utc).isoformat()
        state["threads"][thread_key] = th
        try:
            _write_email_state_unlocked(path, state)
        except OSError:
            return False
        return True


def email_get_latest_customer_message(thread_key: str) -> dict:
    """Brief 218: return the most recent customer-role message in this
    email thread, or empty dict if none. Used by /forward to pick what
    to forward when the frontend doesn't specify a message id."""
    if not thread_key:
        return {}
    path = _get_email_state_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r") as f:
            state = json.load(f)
    except Exception:
        return {}
    th = state.get("threads", {}).get(thread_key, {}) or {}
    for m in reversed(th.get("messages", []) or []):
        if m.get("role") == "customer":
            return m
    return {}


def email_append_assistant_message(
    customer_email: str,
    body: str,
    role: str = "marina",
    source_message_key: str = "",
    strict: bool = False,
    thread_key: str = "",
):
    """Brief 210: append an outbound reply to the email thread state.
    Brief 233: `role` distinguishes Marina-generated replies (`"marina"`,
    the default) from verbatim operator replies (`"operator"`). The
    /escalations/{id}/guidance path keeps the default because Marina
    reformulates the operator's coaching there. /escalations/{id}/reply
    (hard escalation) and /messages/conversations/{id}/email/reply pass
    `role="operator"` because the operator's text is sent verbatim.
    ``thread_key`` pins a reply to the exact subject thread. Returns that key,
    or None if no matching thread exists."""
    path = _get_email_state_path()
    with email_state_file_lock(path):
        if not os.path.exists(path):
            if strict:
                raise LookupError("Email conversation state was not found")
            return None
        try:
            with open(path, "r", encoding="utf-8") as state_file:
                state = json.load(state_file)
            if not isinstance(state, dict):
                raise ValueError("Email conversation state must be an object")
        except (OSError, ValueError, TypeError) as exc:
            if strict:
                raise RuntimeError("Email conversation state could not be read") from exc
            return None
        threads = state.get("threads") or {}
        matched_key = str(thread_key or "")
        if matched_key:
            if (
                matched_key not in threads
                or _email_thread_address(matched_key)
                != str(customer_email or "").strip().casefold()
            ):
                matched_key = ""
        else:
            matched_key = _find_email_thread_key_in_state(state, customer_email) or ""
        if not matched_key:
            if strict:
                raise LookupError("Email conversation state was not found")
            return None

        th = threads[matched_key]
        source_message_key = str(source_message_key or "")
        if source_message_key and any(
            message.get("source_message_key") == source_message_key
            for message in th.get("messages", [])
        ):
            return matched_key
        now = datetime.now(timezone.utc).isoformat()
        th.setdefault("messages", []).append({
            "role": role,
            "ts": now,
            "body": body,
            **({"source_message_key": source_message_key} if source_message_key else {}),
        })
        th["last_activity"] = now
        threads[matched_key] = th
        state["threads"] = threads
        try:
            _write_email_state_unlocked(path, state)
        except OSError as exc:
            if strict:
                raise RuntimeError("Email conversation state could not be written") from exc
            return None
        return matched_key


def _has_table(conn: sqlite3.Connection, table_name: str) -> bool:
    """Check an optional feature table without importing its owning module."""
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table_name,)
    ).fetchone() is not None


def _delete_mermaid_crew_assistance(
    conn: sqlite3.Connection, conversation_ids
) -> int:
    """Delete private assistance notes and their audit rows for conversations."""
    values = [str(value) for value in conversation_ids if str(value or "").strip()]
    if not values or not _has_table(conn, "mermaid_crew_assistance"):
        return 0
    placeholders = ",".join("?" for _ in values)
    total = 0
    if _has_table(conn, "mermaid_crew_assistance_sources"):
        cur = conn.execute(
            f"DELETE FROM mermaid_crew_assistance_sources WHERE conversation_id IN ({placeholders})",
            values,
        )
        total += cur.rowcount
    if _has_table(conn, "mermaid_crew_assistance_reservations"):
        cur = conn.execute(
            "DELETE FROM mermaid_crew_assistance_reservations WHERE assistance_id IN "
            f"(SELECT id FROM mermaid_crew_assistance WHERE conversation_id IN ({placeholders}))",
            values,
        )
        total += cur.rowcount
    if _has_table(conn, "mermaid_crew_assistance_events"):
        cur = conn.execute(
            "DELETE FROM mermaid_crew_assistance_events WHERE assistance_id IN "
            f"(SELECT id FROM mermaid_crew_assistance WHERE conversation_id IN ({placeholders}))",
            values,
        )
        total += cur.rowcount
    cur = conn.execute(
        f"DELETE FROM mermaid_crew_assistance WHERE conversation_id IN ({placeholders})",
        values,
    )
    total += cur.rowcount
    return total


def _anonymize_mermaid_crew_assistance(
    conn: sqlite3.Connection, conversation_ids, now_iso: str
) -> int:
    """Redact private assistance notes while retaining non-identifying row IDs."""
    values = [str(value) for value in conversation_ids if str(value or "").strip()]
    if not values or not _has_table(conn, "mermaid_crew_assistance"):
        return 0
    placeholders = ",".join("?" for _ in values)
    total = 0
    if _has_table(conn, "mermaid_crew_assistance_sources"):
        cur = conn.execute(
            f"DELETE FROM mermaid_crew_assistance_sources WHERE conversation_id IN ({placeholders})",
            values,
        )
        total += cur.rowcount
    rows = conn.execute(
        f"SELECT id FROM mermaid_crew_assistance WHERE conversation_id IN ({placeholders})",
        values,
    ).fetchall()
    assistance_ids = [int(row[0]) for row in rows]
    if not assistance_ids:
        return total
    id_placeholders = ",".join("?" for _ in assistance_ids)
    if _has_table(conn, "mermaid_crew_assistance_reservations"):
        cur = conn.execute(
            "DELETE FROM mermaid_crew_assistance_reservations "
            f"WHERE assistance_id IN ({id_placeholders})",
            assistance_ids,
        )
        total += cur.rowcount
    if _has_table(conn, "mermaid_crew_assistance_events"):
        cur = conn.execute(
            "UPDATE mermaid_crew_assistance_events SET actor='',source_message_id='',"
            "idempotency_key='[redacted]:' || lower(hex(randomblob(16))),"
            f"payload_json='{{}}' WHERE assistance_id IN ({id_placeholders})",
            assistance_ids,
        )
        total += cur.rowcount
    cur = conn.execute(
        "UPDATE mermaid_crew_assistance SET "
        "conversation_id='[redacted]:' || id,customer_name='[redacted]',"
        "note_text='[redacted]',relationship='',trip_date=NULL,"
        "reservation_public_id=NULL,status='withdrawn',revision=revision+1,"
        "source_message_id='',material_hash='',acknowledged_at=NULL,"
        "acknowledged_by=NULL,updated_at=? "
        f"WHERE id IN ({id_placeholders})",
        [now_iso] + assistance_ids,
    )
    total += cur.rowcount
    return total


def _delete_abandoned_mermaid_crew_assistance(
    conn: sqlite3.Connection, conversation_id: str
) -> int:
    """Delete private notes unless a retained Mermaid reservation owns them."""
    if not _has_table(conn, "mermaid_crew_assistance"):
        return 0
    has_reservations = _has_table(conn, "mermaid_reservations")
    has_links = _has_table(conn, "mermaid_crew_assistance_reservations")
    ownership = "0"
    args: list = [conversation_id]
    if has_reservations:
        ownership = (
            "EXISTS (SELECT 1 FROM mermaid_reservations saved "
            "WHERE saved.tenant_slug='mermaid' "
            "AND saved.conversation_id=a.conversation_id "
            "AND saved.public_id=a.reservation_public_id)"
        )
        if has_links:
            ownership += (
                " OR EXISTS (SELECT 1 FROM mermaid_crew_assistance_reservations link "
                "JOIN mermaid_reservations saved "
                "ON saved.public_id=link.reservation_public_id "
                "AND saved.tenant_slug='mermaid' "
                "AND saved.conversation_id=a.conversation_id "
                "WHERE link.tenant_slug='mermaid' AND link.assistance_id=a.id)"
            )
    rows = conn.execute(
        "SELECT a.id FROM mermaid_crew_assistance a "
        "WHERE a.tenant_slug='mermaid' AND a.conversation_id=? "
        f"AND NOT ({ownership})",
        args,
    ).fetchall()
    assistance_ids = [int(row[0]) for row in rows]
    retained_rows = conn.execute(
        "SELECT a.id FROM mermaid_crew_assistance a "
        "WHERE a.tenant_slug='mermaid' AND a.conversation_id=? "
        f"AND ({ownership})",
        args,
    ).fetchall()
    retained_ids = [int(row[0]) for row in retained_rows]
    if retained_ids:
        columns = {
            str(row[1])
            for row in conn.execute(
                "PRAGMA table_info(mermaid_crew_assistance)"
            ).fetchall()
        }
        if "session_started_at" not in columns:
            conn.execute(
                "ALTER TABLE mermaid_crew_assistance "
                "ADD COLUMN session_started_at TEXT NOT NULL DEFAULT ''"
            )
        retained_placeholders = ",".join("?" for _ in retained_ids)
        trash_session = (
            "trashed:" + datetime.now(timezone.utc).isoformat()
        )[:64]
        conn.execute(
            "UPDATE mermaid_crew_assistance SET session_started_at=? "
            f"WHERE id IN ({retained_placeholders})",
            [trash_session] + retained_ids,
        )
    total = 0
    if _has_table(conn, "mermaid_crew_assistance_sources"):
        if assistance_ids:
            placeholders = ",".join("?" for _ in assistance_ids)
            cur = conn.execute(
                "DELETE FROM mermaid_crew_assistance_sources "
                "WHERE tenant_slug='mermaid' AND conversation_id=? "
                f"AND (assistance_id IS NULL OR assistance_id IN ({placeholders}))",
                [conversation_id] + assistance_ids,
            )
        else:
            cur = conn.execute(
                "DELETE FROM mermaid_crew_assistance_sources "
                "WHERE tenant_slug='mermaid' AND conversation_id=? "
                "AND assistance_id IS NULL",
                (conversation_id,),
            )
        total += cur.rowcount
    if not assistance_ids:
        return total
    placeholders = ",".join("?" for _ in assistance_ids)
    if has_links:
        cur = conn.execute(
            "DELETE FROM mermaid_crew_assistance_reservations "
            f"WHERE assistance_id IN ({placeholders})",
            assistance_ids,
        )
        total += cur.rowcount
    if _has_table(conn, "mermaid_crew_assistance_events"):
        cur = conn.execute(
            "DELETE FROM mermaid_crew_assistance_events "
            f"WHERE assistance_id IN ({placeholders})",
            assistance_ids,
        )
        total += cur.rowcount
    cur = conn.execute(
        "DELETE FROM mermaid_crew_assistance "
        f"WHERE id IN ({placeholders})",
        assistance_ids,
    )
    total += cur.rowcount
    return total


def wa_delete_conversation(phone: str) -> int:
    """Brief 165: hard-delete all messages + booking state for a phone number.
    Returns the total number of rows deleted across whatsapp_threads and
    whatsapp_booking_state. Used by the dashboard delete-conversation endpoint.
    No audit trail — destructive operation meant for removing test pollution
    and unwanted threads from the Messages view.
    """
    conn = _get_conn()
    total = 0
    if _current_tenant_id() == "mermaid":
        total += _delete_abandoned_mermaid_crew_assistance(conn, phone)
    for sql in (
        "DELETE FROM whatsapp_threads WHERE phone = ?",
        "DELETE FROM whatsapp_booking_state WHERE phone = ?",
    ):
        cur = conn.execute(sql, (phone,))
        total += cur.rowcount
    conn.commit()
    conn.close()
    return total


def wa_list_conversations() -> list:
    """List all WhatsApp conversations with latest message and booking state.
    Returns list of dicts sorted by most recent activity."""
    conn = _get_conn()
    # Get unique phones with latest message
    rows = conn.execute(
        "SELECT t.phone, t.text, t.created_at, t.role, t.channel "
        "FROM whatsapp_threads t "
        "INNER JOIN ("
        "  SELECT phone, MAX(created_at) as max_ts "
        "  FROM whatsapp_threads GROUP BY phone"
        ") latest ON t.phone = latest.phone AND t.created_at = latest.max_ts "
        # Brief 249: exclude conversations marked archived
        # (conversation_status.deleted=1 set by Brief 237's bulk sweep
        # OR by Brief 249's manual archive endpoint).
        # Brief 261: exclude blocked (cs.blocked=1) so blocked senders
        # disappear from the active Inbox list per issue #30.
        "LEFT JOIN conversation_status cs ON t.phone = cs.conversation_id "
        "WHERE (cs.deleted IS NULL OR cs.deleted = 0) "
        "AND (cs.blocked IS NULL OR cs.blocked = 0) "
        "ORDER BY t.created_at DESC"
    ).fetchall()

    conversations = []
    for r in rows:
        phone = r[0]
        # Get booking state for name + status
        state_row = conn.execute(
            "SELECT fields_json, flags_json, last_activity "
            "FROM whatsapp_booking_state WHERE phone = ?", (phone,)
        ).fetchone()
        fields = json.loads(state_row[0] or "{}") if state_row else {}
        flags = json.loads(state_row[1] or "{}") if state_row else {}
        # Brief 202: when booking_state has no customer_name (the dm_agent path
        # for booking_flow:false tenants like unboks doesn't populate it), fall
        # back to the most recent user-role sender_name from whatsapp_threads.
        # Marina's path (booking_flow:true) is unaffected — booking_state's
        # customer_name takes priority.
        name = fields.get("customer_name") or fields.get("name") or ""
        if not name:
            sender_row = conn.execute(
                "SELECT sender_name FROM whatsapp_threads "
                "WHERE phone = ? AND role = 'user' AND sender_name != '' "
                "ORDER BY created_at DESC LIMIT 1",
                (phone,)
            ).fetchone()
            if sender_row and sender_row[0]:
                name = sender_row[0]
        if not name:
            name = phone  # final fallback to hex/phone if no name source at all
        status = "escalated" if flags.get("fully_escalated") else "active"
        loop_status = mermaid_loop_status(flags)
        # Count messages
        count_row = conn.execute(
            "SELECT COUNT(*) FROM whatsapp_threads WHERE phone = ?", (phone,)
        ).fetchone()
        channel = r[4] if len(r) > 4 and r[4] else "whatsapp"
        conversations.append({
            "phone": phone,
            "customer_name": name,
            "last_message": loop_status or r[1],
            "last_message_role": r[3],
            "last_message_at": r[2],
            "status": status,
            "message_count": count_row[0] if count_row else 0,
            "channel": channel,
            "loop_stopped": loop_status is not None,
            "loop_status": loop_status,
            "loop_stopped_at": flags.get("mermaid_loop_stopped_at") if loop_status else None,
        })
        order_state = get_order_state_for_conversation(phone)
        if order_state:
            conversations[-1].update({
                "intent": order_state.get("intent"),
                "is_order": True,
                "order_status": order_state.get("order_status"),
                "order_payload": order_state.get("order_payload"),
                "escalation_mode": order_state.get("escalation_mode"),
                "human_action_required": order_state.get("human_action_required"),
                "ai_muted": order_state.get("ai_muted"),
                "badge_type": order_state.get("badge_type"),
                "queue_type": order_state.get("queue_type"),
                "next_operator_action": order_state.get("next_operator_action"),
                "order_escalation_id": order_state.get("escalation_id"),
            })
    conn.close()
    return conversations


def wa_get_full_history(phone: str, limit: int = 100) -> list:
    """Get the most-recent conversation history for a phone number
    (no 24h cutoff). Returns the most recent `limit` messages, ordered
    oldest-first in the output (callers iterate forward through time).

    Brief 201: also returns row id (SQLite autoincrement) so frontends can use it
    as a stable React key.

    Brief 250: SELECT changed from `ORDER BY ASC LIMIT ?` to `ORDER BY
    DESC LIMIT ? ... reversed()`. Pre-Brief-250 the function returned
    the OLDEST N messages when total > limit -- silently truncating the
    most recent ones. This broke escalation summary generation for any
    conversation > 20 messages (escalation_dispatcher.py:37 calls with
    limit=20) because Claude only saw stale history. Output order
    contract preserved: still oldest-first."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT id, role, text, created_at, sender_name, channel "
        "FROM whatsapp_threads "
        "WHERE phone = ? ORDER BY created_at DESC LIMIT ?",
        (phone, limit)
    ).fetchall()
    conn.close()
    # Brief 250: reversed() to keep the documented oldest-first output
    # contract; SELECT picks the most-recent N rows. Sender metadata lets the
    # inbox distinguish phone-app replies from Alia's automated replies.
    return [
        {
            "id": r[0],
            "role": r[1],
            "text": r[2],
            "created_at": r[3],
            "sender_name": r[4] or "",
            "channel": r[5] or "whatsapp",
        }
        for r in reversed(rows)
    ]


def wa_cleanup_stale_data() -> dict:
    """Clean up old WhatsApp data. Returns counts of cleaned rows."""
    conn = _get_conn()
    now = datetime.now(timezone.utc)
    # Conversation messages >30 days
    cutoff_30d = (now - timedelta(days=30)).isoformat()
    from shared import mermaid_customers
    threads_cleaned = 0
    if not mermaid_customers.enabled():
        cur = conn.execute("DELETE FROM whatsapp_threads WHERE created_at < ?", (cutoff_30d,))
        threads_cleaned = cur.rowcount
    # Processed message IDs >7 days
    cutoff_7d = (now - timedelta(days=7)).isoformat()
    cur = conn.execute("DELETE FROM whatsapp_processed WHERE created_at < ?", (cutoff_7d,))
    processed_cleaned = cur.rowcount
    conn.commit()
    conn.close()
    return {"threads_cleaned": threads_cleaned, "processed_cleaned": processed_cleaned}


def dm_store_message(conversation_id: str, channel: str, role: str, text: str,
                     sender_name: str = "", created_at: str = ""):
    """Store a DM message in conversation history.

    created_at is optional and is used for signed provider events so an
    external WhatsApp Business app reply keeps its real timeline position.
    """
    timestamp = str(created_at or "").strip() or datetime.now(timezone.utc).isoformat()
    conn = _get_conn()
    conn.execute(
        "INSERT INTO whatsapp_threads (phone, role, text, created_at, channel, sender_name) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (conversation_id, role, text, timestamp, channel, sender_name)
    )
    from shared import mermaid_customers
    mermaid_customers.capture(conn, conversation_id, name=sender_name if role == "user" else "", at=timestamp)
    conn.commit()
    conn.close()


def dm_store_message_once(
    conversation_id: str,
    channel: str,
    role: str,
    text: str,
    source_message_key: str,
    sender_name: str = "",
    created_at: str = "",
) -> bool:
    """Persist one outbound/system transcript event exactly once.

    A repeated write with the same conversation and source key is considered
    successful because the requested dashboard record already exists.
    """
    source_key = str(source_message_key or "").strip()
    if not source_key or len(source_key) > 240:
        raise ValueError("invalid_source_message_key")
    timestamp = (
        str(created_at or "").strip()
        or datetime.now(timezone.utc).isoformat()
    )
    conn = _get_conn()
    try:
        cursor = conn.execute(
            "INSERT OR IGNORE INTO whatsapp_threads "
            "(phone, role, text, created_at, channel, sender_name, "
            "source_message_key) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                conversation_id,
                role,
                text,
                timestamp,
                channel,
                sender_name,
                source_key,
            ),
        )
        from shared import mermaid_customers
        mermaid_customers.capture(conn, conversation_id, name=sender_name if role == "user" else "", at=timestamp)
        conn.commit()
        if cursor.rowcount == 1:
            return True
        return conn.execute(
            "SELECT 1 FROM whatsapp_threads "
            "WHERE phone = ? AND source_message_key = ? LIMIT 1",
            (conversation_id, source_key),
        ).fetchone() is not None
    finally:
        conn.close()


def dm_store_inbound_message(
    conversation_id: str,
    channel: str,
    text: str,
    sender_name: str,
    message_ids: list[str],
) -> bool:
    """Persist one inbound batch exactly once across crash recovery."""
    normalized_ids = sorted({
        str(value).strip() for value in message_ids or [] if str(value).strip()
    })
    source_key = hashlib.sha256(
        "\x1f".join(normalized_ids).encode("utf-8")
    ).hexdigest() if normalized_ids else ""
    now = datetime.now(timezone.utc).isoformat()
    conn = _get_conn()
    try:
        cur = conn.execute(
            "INSERT OR IGNORE INTO whatsapp_threads "
            "(phone, role, text, created_at, channel, sender_name, source_message_key) "
            "VALUES (?, 'user', ?, ?, ?, ?, ?)",
            (conversation_id, text, now, channel, sender_name, source_key),
        )
        from shared import mermaid_customers
        mermaid_customers.capture(conn, conversation_id, name=sender_name, at=now)
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()


def dm_get_history(conversation_id: str, channel: str, limit: int = 10) -> list:
    """Get recent DM conversation history (last 24h, oldest first)."""
    conn = _get_conn()
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    rows = conn.execute(
        "SELECT role, text, created_at FROM whatsapp_threads "
        "WHERE phone = ? AND channel = ? AND created_at > ? "
        "ORDER BY created_at DESC LIMIT ?",
        (conversation_id, channel, cutoff, limit)
    ).fetchall()
    conn.close()
    return [{"role": r[0], "text": r[1], "created_at": r[2]} for r in reversed(rows)]


def _insert_durable_operator_item(conn, action_key: str, notification: dict, now: str) -> int:
    """Insert exactly once inside the caller's claim-fenced transaction."""
    existing = conn.execute(
        "SELECT notification_id FROM inbound_operator_notifications WHERE action_key = ?",
        (action_key,),
    ).fetchone()
    if existing:
        if conn.execute(
            "SELECT 1 FROM pending_notifications WHERE id = ?", (int(existing[0]),)
        ).fetchone() is None:
            # An operator may have removed the item while this turn was
            # waiting on provider/control recovery. Do not turn the dangling
            # dedup marker into proof that a promised handoff still exists.
            raise RuntimeError("durable operator item is unavailable")
        return int(existing[0])
    notification_type = str(notification.get("notification_type") or "technical")
    if notification_type not in {"technical", "escalation"}:
        raise ValueError("invalid durable operator item type")
    channel = str(notification.get("channel") or "whatsapp")
    customer_id = str(notification.get("customer_id") or "")
    cur = conn.execute(
        "INSERT INTO pending_notifications "
        "(notification_type, channel, customer_id, customer_name, subject, body, "
        "status, created_at, mode) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
        (notification_type, channel, customer_id, str(notification.get("customer_name") or ""),
         str(notification.get("subject") or ""), str(notification.get("body") or ""),
         now, "soft" if notification_type == "escalation" else None),
    )
    notification_id = int(cur.lastrowid)
    conn.execute(
        "INSERT INTO inbound_operator_notifications (action_key, notification_id, created_at) "
        "VALUES (?, ?, ?)", (action_key, notification_id, now),
    )
    if notification_type == "escalation":
        conn.execute(
            "INSERT INTO conversation_status (conversation_id, channel, status, deleted, updated_at) "
            "VALUES (?, ?, 'open', 0, ?) ON CONFLICT(conversation_id) DO UPDATE SET "
            "channel = excluded.channel, status = 'open', deleted = 0, updated_at = excluded.updated_at",
            (customer_id, channel, now),
        )
    return notification_id


def _inbound_processing_commit_operator_item(
    message_ids: list[str],
    batch_id: str,
    processing_token: str,
    *,
    account_id: str,
    notification: dict,
    delivery_failure: bool = False,
) -> int | None:
    """Commit one operator item per durable turn under the current generation.

    This deliberately performs no model/alert/provider I/O. The row and turn
    dedup key are one transaction, so retry after a crash cannot create a
    second handoff, even if an operator has already resolved the first one.
    """
    ids = list(dict.fromkeys(str(value) for value in message_ids or [] if value))
    token = str(processing_token or "").strip()
    normalized_batch_id = str(batch_id or "").strip()
    if not ids or not token or not normalized_batch_id:
        return None
    channel = str(notification.get("channel") or "")
    customer_id = str(notification.get("customer_id") or "")
    if not channel or not customer_id:
        raise ValueError("handoff destination missing")
    action_key = hashlib.sha256(
        "\x1f".join([
            "delivery-failure" if delivery_failure else "handoff",
            account_id, channel, normalized_batch_id, *sorted(ids),
        ]).encode()
    ).hexdigest()
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            "SELECT message_id, status, processing_token, conversation_id, channel "
            "FROM inbound_processing_events WHERE batch_id = ?",
            (normalized_batch_id,),
        ).fetchall()
        if (
            len(rows) != len(ids)
            or {str(row[0]) for row in rows} != set(ids)
            or any(str(row[1]) != "processing" or str(row[2]) != token for row in rows)
            or any(str(row[3]) != customer_id or str(row[4]) != channel for row in rows)
        ):
            conn.rollback()
            return None
        from shared.tenant_guard import account_access_state

        account_state = account_access_state(account_id, direction="inbound")
        if account_state is None:
            raise RuntimeError("tenant account controls unavailable")
        if account_state is False:
            raise HandoffAccountReassignedError("tenant account reassigned")
        now = datetime.now(timezone.utc).isoformat()
        notification = dict(notification)
        # Mermaid's dashboard exposes escalation/relay work items, not the
        # generic technical table lane. Use soft review, never hard takeover.
        notification["notification_type"] = "escalation"
        notification_id = _insert_durable_operator_item(
            conn, action_key, notification, now,
        )
        if delivery_failure:
            cur = conn.execute(
                "UPDATE inbound_processing_events SET status = 'send_failed', "
                "reason = 'provider_send_failed', last_error = 'provider_delivery_unconfirmed', "
                "processing_token = '', lease_expires_at = '', updated_at = ? "
                "WHERE batch_id = ? AND status = 'processing' AND processing_token = ?",
                (now, normalized_batch_id, token),
            )
            if cur.rowcount != len(ids):
                raise RuntimeError("delivery failure claim was lost")
        conn.commit()
        return notification_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def inbound_processing_commit_handoff(
    message_ids: list[str], batch_id: str, processing_token: str, *,
    account_id: str, notification: dict,
) -> int | None:
    """Persist an idempotent handoff before a current worker promises it."""
    return _inbound_processing_commit_operator_item(
        message_ids, batch_id, processing_token,
        account_id=account_id, notification=notification,
    )


def inbound_processing_commit_delivery_failure(
    message_ids: list[str], batch_id: str, processing_token: str, *,
    account_id: str, notification: dict,
) -> int | None:
    """Make a no-retry terminal transition only with durable operator attention."""
    return _inbound_processing_commit_operator_item(
        message_ids, batch_id, processing_token,
        account_id=account_id, notification=notification, delivery_failure=True,
    )


def create_pending_notification(notification_type: str, channel: str,
                                 customer_id: str, customer_name: str,
                                 subject: str, body: str,
                                 relay_token: str = None,
                                 mode: str = None,
                                 preserve_hard_mode: bool = False,
                                 suppress_model_summary: bool = False,
                                 email_thread_key: str = "",
                                 email_reply_subject: str = "") -> int:
    """Insert (or, for an unresolved escalation, UPDATE) a pending
    notification. Brief 227: dedup unresolved escalations + structured
    summary persisted on the same row. Brief 239: optional `mode` param
    ('soft'/'hard') sets pending_notifications.mode at insert time and
    drives the alert email's Mode line. None preserves existing value
    on UPDATE (COALESCE). Automatic review callers can preserve an existing
    operator's hard takeover with ``preserve_hard_mode=True``; the check is
    atomic with the update so a concurrent takeover cannot be downgraded.
    Offline handover can opt out of model summaries; its repeated requests
    update the durable work item without firing another identical alert."""
    if mode is None and notification_type == "relay":
        mode = "soft"
    now = datetime.now(timezone.utc).isoformat()
    conn = _get_conn()
    # The row id, visible content and revision form one operator work item.
    # Always serialize the lookup/update so overlapping inbound messages cannot
    # both derive the same target revision.
    conn.execute("BEGIN IMMEDIATE")

    # Brief 239: gate-safe initialization so non-escalation paths can
    # still compute is_update without NameError.
    existing = None
    row_id = None
    prev_summary = None
    target_content_revision = 1

    # Brief 227: dedup unresolved escalations. If a 'pending' row already
    # exists for this customer_id (escalation only), UPDATE it instead of
    # inserting a new one. Keeps the row id stable so any outstanding
    # alert thread / learning entry stays attached.
    #
    # Order escalations are intentionally not deduped: one customer can place
    # multiple separate orders in the same conversation, and each confirmed
    # order must appear as its own operator work item.
    if notification_type == "escalation" and mode != "order":
        if channel == "email" and email_thread_key:
            existing = conn.execute(
                "SELECT id, escalation_summary, content_revision "
                "FROM pending_notifications "
                "WHERE customer_id = ? AND channel = 'email' "
                "AND email_thread_key = ? "
                "AND notification_type = 'escalation' "
                "AND status IN ('pending', 'sent') "
                "ORDER BY created_at DESC LIMIT 1",
                (customer_id, email_thread_key),
            ).fetchone()
        else:
            existing = conn.execute(
                "SELECT id, escalation_summary, content_revision "
                "FROM pending_notifications "
                "WHERE customer_id = ? AND notification_type = 'escalation' "
                "AND status IN ('pending', 'sent') "
                "ORDER BY created_at DESC LIMIT 1",
                (customer_id,),
            ).fetchone()
        if existing:
            row_id = existing[0]
            target_content_revision = int(existing[2] or 1) + 1
            if existing[1]:
                try:
                    prev_summary = json.loads(existing[1])
                except (json.JSONDecodeError, TypeError):
                    prev_summary = None

    if row_id is None:
        cur = conn.execute(
            "INSERT INTO pending_notifications "
            "(notification_type, relay_token, channel, customer_id, customer_name, "
            "subject, body, status, created_at, mode, email_thread_key, "
            "email_reply_subject) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)",
            (notification_type, relay_token, channel, customer_id, customer_name,
             subject, body, now, mode, email_thread_key, email_reply_subject)
        )
        row_id = cur.lastrowid
    else:
        conn.execute(
            "UPDATE pending_notifications "
            "SET subject = ?, body = ?, customer_name = ?, created_at = ?, "
            "content_revision = content_revision + 1, escalation_summary = NULL, "
            "mode = CASE WHEN ? AND mode = 'hard' THEN mode ELSE COALESCE(?, mode) END, "
            "email_thread_key = CASE WHEN ? != '' THEN ? ELSE email_thread_key END, "
            "email_reply_subject = CASE WHEN ? != '' THEN ? ELSE email_reply_subject END "
            "WHERE id = ?",
            (
                subject, body, customer_name, now, preserve_hard_mode, mode,
                email_thread_key, email_thread_key,
                email_reply_subject, email_reply_subject,
                row_id,
            ))
    conn.commit()
    conn.close()

    # Customer escalations and relays require an operator conversation. Pure
    # technical attention belongs to the operations queue and must not imply
    # human takeover or change the customer's conversation state.
    if notification_type in {"escalation", "relay"}:
        set_conversation_status(customer_id, "open", channel)

    # Brief 239: ``prev_summary`` was captured under the same write lock as the
    # revision increment, before the old derived summary was cleared.
    is_update = (existing is not None) and (notification_type == "escalation")

    # Brief 227 + 239: generate fresh structured summary BEFORE the alert
    # fires so the alert body can use it. Persisted regardless of whether
    # the alert ends up firing.
    summary_dict = None
    if (notification_type == "escalation" and _summary_dispatcher is not None
            and not suppress_model_summary):
        try:
            summary_dict = _summary_dispatcher(
                row_id, channel, customer_id, customer_name)
            if summary_dict:
                summary_conn = _get_conn()
                try:
                    saved = summary_conn.execute(
                        "UPDATE pending_notifications SET escalation_summary = ? "
                        "WHERE id = ? AND content_revision = ?",
                        (
                            json.dumps(summary_dict),
                            row_id,
                            target_content_revision,
                        ),
                    ).rowcount
                    summary_conn.commit()
                finally:
                    summary_conn.close()
                if saved != 1:
                    # A newer inbound revision won the race. Its generator owns
                    # the summary and alert; never overwrite it or alert from
                    # this superseded invocation.
                    summary_dict = None
        except Exception:
            summary_dict = None

    # Even a no-summary or failed-summary path may have been superseded while
    # the model call ran. Only the invocation that still owns this revision may
    # emit its alert body.
    revision_is_current = False
    try:
        revision_conn = _get_conn()
        try:
            current_row = revision_conn.execute(
                "SELECT content_revision FROM pending_notifications WHERE id = ?",
                (row_id,),
            ).fetchone()
            revision_is_current = bool(
                current_row
                and int(current_row[0] or 1) == target_content_revision
            )
        finally:
            revision_conn.close()
    except Exception:
        # The work item is already durable. A follow-up alert is best effort,
        # matching the existing dispatcher contract.
        revision_is_current = False

    # Brief 217 + 239: alert dispatch — suppress duplicate updates with
    # an unchanged summary. Wrapped in try/except so a dispatcher failure
    # NEVER blocks the escalation row from being saved.
    if notification_type == "escalation" and _alert_dispatcher is not None:
        should_fire = revision_is_current and not (
            suppress_model_summary and is_update
        )
        if is_update and prev_summary is not None and summary_dict is not None:
            should_fire = _summaries_materially_differ(
                prev_summary, summary_dict)
        if should_fire:
            try:
                conn = _get_conn()
                _r = conn.execute(
                    "SELECT mode FROM pending_notifications WHERE id = ?",
                    (row_id,)).fetchone()
                conn.close()
                actual_mode = _r[0] if _r else None
            except Exception:
                actual_mode = None
            try:
                try:
                    _alert_dispatcher(row_id, customer_name, channel, subject,
                                      mode=actual_mode,
                                      summary_dict=summary_dict,
                                      body=body,
                                      is_update=is_update)
                except TypeError as exc:
                    if "body" not in str(exc):
                        raise
                    _alert_dispatcher(row_id, customer_name, channel, subject,
                                      mode=actual_mode,
                                      summary_dict=summary_dict,
                                      is_update=is_update)
            except Exception:
                pass

    return row_id


def set_conversation_status(conversation_id: str, status: str,
                            channel: str = "whatsapp") -> None:
    """Set or update the conversation status (pending/open/resolved).
    Uses UPSERT so the first call creates the row and subsequent calls update it."""
    conn = _get_conn()
    should_unarchive = status in {"pending", "open", "active"}
    conn.execute(
        "INSERT INTO conversation_status (conversation_id, channel, status, updated_at) "
        "VALUES (?, ?, ?, ?) "
        "ON CONFLICT(conversation_id) DO UPDATE SET status = excluded.status, "
        "channel = excluded.channel, updated_at = excluded.updated_at, "
        "deleted = CASE WHEN ? THEN 0 ELSE deleted END",
        (conversation_id, channel, status,
         datetime.now(timezone.utc).isoformat(), 1 if should_unarchive else 0)
    )
    conn.commit()
    conn.close()


def get_conversation_status(conversation_id: str) -> str:
    """Get the current conversation status. Returns 'pending' if no record exists."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT status FROM conversation_status WHERE conversation_id = ?",
        (conversation_id,)
    ).fetchone()
    conn.close()
    return row[0] if row else "pending"


def set_escalation_mode(
    escalation_id: int,
    mode: str,
    *,
    expected_content_revision: int | None = None,
) -> bool:
    """Brief 213: set the mode of a pending_notifications row. `mode` must
    be 'soft', 'hard', or 'order' (caller validates). Returns True if a row was
    updated, False if no row matched."""
    if mode not in ("soft", "hard", "order"):
        return False
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT mode, content_revision FROM pending_notifications WHERE id = ?",
            (escalation_id,),
        ).fetchone()
        if row is None:
            conn.rollback()
            return False
        current_mode, current_revision = row
        _require_escalation_revision(
            current_revision, expected_content_revision
        )
        # Version operator-visible mode changes made through a revision-aware
        # API. Unversioned internal callers retain their historical behavior.
        revision_delta = int(
            expected_content_revision is not None and current_mode != mode
        )
        conn.execute(
            "UPDATE pending_notifications SET mode = ?, "
            "content_revision = content_revision + ? WHERE id = ?",
            (mode, revision_delta, escalation_id),
        )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def set_escalation_takeover_state(
    escalation_id: int,
    muted: bool,
    *,
    expected_content_revision: int | None = None,
) -> bool:
    """Atomically change a generic escalation's mode and transport mute.

    Takeover and handback are one operator action. Keeping both writes under
    the same immediate transaction prevents opposite actions from interleaving
    into a hard/unmuted or soft/muted conversation.
    """
    mode = "hard" if muted else "soft"
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()
    takeover_at = now if muted else None
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT customer_id, channel, mode, content_revision "
            "FROM pending_notifications WHERE id = ?",
            (escalation_id,),
        ).fetchone()
        if row is None:
            conn.rollback()
            return False
        customer_id, channel, current_mode, current_revision = row
        _require_escalation_revision(
            current_revision, expected_content_revision
        )
        revision_delta = int(
            expected_content_revision is not None and current_mode != mode
        )
        conn.execute(
            "UPDATE pending_notifications SET mode = ?, "
            "content_revision = content_revision + ? WHERE id = ?",
            (mode, revision_delta, escalation_id),
        )
        conn.execute(
            "INSERT INTO conversation_status "
            "(conversation_id, channel, status, ai_muted, human_takeover_at, updated_at) "
            "VALUES (?, ?, 'pending', ?, ?, ?) "
            "ON CONFLICT(conversation_id) DO UPDATE SET "
            "ai_muted = excluded.ai_muted, "
            "human_takeover_at = excluded.human_takeover_at, "
            "updated_at = excluded.updated_at",
            (
                customer_id,
                channel or "whatsapp",
                1 if muted else 0,
                takeover_at,
                now,
            ),
        )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_ai_muted(conversation_id: str) -> bool:
    """Brief 213: read the ai_muted flag from conversation_status. Returns
    False when no row exists for the conversation (default behavior is
    not muted)."""
    if not conversation_id:
        return False
    conn = _get_conn()
    row = conn.execute(
        "SELECT ai_muted FROM conversation_status WHERE conversation_id = ?",
        (conversation_id,)).fetchone()
    conn.close()
    return bool(row and row[0])


def set_ai_muted(conversation_id: str, muted: bool, channel: str = "whatsapp") -> None:
    """Brief 213: takeover/handback. UPSERTs conversation_status with
    ai_muted set, and stamps human_takeover_at when muting (NULL when
    unmuting). Preserves whatever `status` value the row already had —
    escalation status is independent from mute state."""
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()
    takeover_at = now if muted else None
    conn.execute(
        "INSERT INTO conversation_status "
        "(conversation_id, channel, status, ai_muted, human_takeover_at, updated_at, ai_mute_source) "
        "VALUES (?, ?, 'pending', ?, ?, ?, ?) "
        "ON CONFLICT(conversation_id) DO UPDATE SET "
        "ai_muted = excluded.ai_muted, "
        "ai_mute_source = excluded.ai_mute_source, "
        "human_takeover_at = excluded.human_takeover_at, "
        "updated_at = excluded.updated_at",
        (conversation_id, channel, 1 if muted else 0, takeover_at, now, "manual" if muted else ""))
    conn.commit()
    conn.close()


def set_blocked(conversation_id: str, blocked: bool, channel: str = "",
                 reason: str = "", blocked_by: str = ""):
    """Brief 220: flip the per-conversation blocked flag. Different from
    ai_muted: blocked drops inbound messages BEFORE any storage so the
    conversation doesn't appear in the dashboard inbox at all.
    UPSERT pattern matching set_ai_muted; channel is required for INSERT
    but ignored on UPDATE (existing rows keep their channel).

    Brief 261: optional `reason` (spam / abusive / wrong_contact / other
    or free-form) and `blocked_by` (operator label) audit fields per
    issue #30. Cleared to empty string on unblock so a future block
    event doesn't inherit stale audit context from the prior block."""
    if not conversation_id:
        return
    now = datetime.now(timezone.utc).isoformat()
    _reason = (reason or "") if blocked else ""
    _blocked_by = (blocked_by or "") if blocked else ""
    conn = _get_conn()
    conn.execute(
        "INSERT INTO conversation_status "
        "(conversation_id, channel, status, blocked, reason, blocked_by, updated_at) "
        "VALUES (?, ?, 'pending', ?, ?, ?, ?) "
        "ON CONFLICT(conversation_id) DO UPDATE SET "
        "blocked = excluded.blocked, "
        "reason = excluded.reason, "
        "blocked_by = excluded.blocked_by, "
        "updated_at = excluded.updated_at",
        (conversation_id, channel or "", 1 if blocked else 0,
         _reason, _blocked_by, now))
    conn.commit()
    conn.close()


def get_blocked(conversation_id: str) -> bool:
    """Brief 220: return True if this conversation is blocked. Hot path,
    called on every customer-message ingestion. Single-row PK lookup."""
    if not conversation_id:
        return False
    conn = _get_conn()
    row = conn.execute(
        "SELECT blocked FROM conversation_status WHERE conversation_id = ?",
        (conversation_id,)).fetchone()
    conn.close()
    return bool(row[0]) if row else False


def list_blocked_conversations() -> list:
    """Brief 220 + Brief 261: return all currently-blocked conversations
    for the dashboard's Settings -> Blocked Conversations management list
    AND for the /blocked-senders alias added by Brief 261.

    Each row carries camelCase keys for backward compatibility:
    - Existing Brief 220: conversationId, channel, updatedAt.
    - Brief 261 additions: reason, blockedBy (empty strings when audit
      fields were never set or cleared on unblock)."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT conversation_id, channel, updated_at, reason, blocked_by "
        "FROM conversation_status WHERE blocked = 1 "
        "ORDER BY updated_at DESC").fetchall()
    conn.close()
    return [
        {
            "conversationId": r[0],
            "channel": r[1] or "",
            "updatedAt": r[2],
            "reason": r[3] or "",
            "blockedBy": r[4] or "",
        }
        for r in rows
    ]


_IGNORE_LABELS = {
    "Owner",
    "Staff",
    "VIP",
    "Supplier",
    "Private",
    "Family",
    "Lawyer / Accountant",
    "Test Contact",
    "Other",
}


def _ignored_contact_row_to_dict(row) -> dict:
    return {
        "id": int(row[0]),
        "tenant_id": row[1] or "",
        "name": row[2] or "",
        "phone_original": row[3] or "",
        "phone_normalized": row[4] or "",
        "email_original": row[5] or "",
        "email_normalized": row[6] or "",
        "channel": row[7] or "",
        "external_sender_id": row[8] or "",
        "label": row[9] or "",
        "note": row[10] or "",
        "created_by": row[11] or "",
        "created_at": row[12] or "",
        "updated_at": row[13] or "",
        "deleted_at": row[14] or None,
    }


def _sanitize_ignore_label(label: str) -> str:
    clean = (label or "").strip()
    return clean if clean in _IGNORE_LABELS else ("Other" if clean else "")


def _sanitize_channel(channel: str) -> str:
    return re.sub(r"[^a-z0-9_-]", "", (channel or "").strip().lower())[:40]


def list_ignored_contacts(include_deleted: bool = False) -> list[dict]:
    tenant_id = _current_tenant_id()
    conn = _get_conn()
    sql = (
        "SELECT id, tenant_id, name, phone_original, phone_normalized, "
        "email_original, email_normalized, channel, external_sender_id, "
        "label, note, created_by, created_at, updated_at, deleted_at "
        "FROM ignored_contacts WHERE tenant_id = ? "
    )
    params: list = [tenant_id]
    if not include_deleted:
        sql += "AND deleted_at IS NULL "
    sql += "ORDER BY updated_at DESC, id DESC"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [_ignored_contact_row_to_dict(r) for r in rows]


def find_ignored_contact_duplicate(
    *,
    phone: str = "",
    email: str = "",
    channel: str = "",
    external_sender_id: str = "",
    exclude_id: int | None = None,
) -> dict | None:
    tenant_id = _current_tenant_id()
    phone_norm = normalize_phone_identifier(phone)
    email_norm = normalize_email_identifier(email)
    clean_channel = _sanitize_channel(channel)
    external = (external_sender_id or "").strip()
    clauses = []
    params: list = [tenant_id]
    if phone_norm:
        clauses.append("phone_normalized = ?")
        params.append(phone_norm)
    if email_norm:
        clauses.append("email_normalized = ?")
        params.append(email_norm)
    if clean_channel and external:
        clauses.append("(channel = ? AND external_sender_id = ?)")
        params.extend([clean_channel, external])
    if not clauses:
        return None
    sql = (
        "SELECT id, tenant_id, name, phone_original, phone_normalized, "
        "email_original, email_normalized, channel, external_sender_id, "
        "label, note, created_by, created_at, updated_at, deleted_at "
        "FROM ignored_contacts WHERE tenant_id = ? AND deleted_at IS NULL "
        f"AND ({' OR '.join(clauses)}) "
    )
    if exclude_id is not None:
        sql += "AND id != ? "
        params.append(int(exclude_id))
    sql += "ORDER BY updated_at DESC LIMIT 1"
    conn = _get_conn()
    row = conn.execute(sql, params).fetchone()
    conn.close()
    return _ignored_contact_row_to_dict(row) if row else None


def add_ignored_contact(
    *,
    name: str = "",
    phone: str = "",
    email: str = "",
    channel: str = "",
    external_sender_id: str = "",
    label: str = "",
    note: str = "",
    created_by: str = "operator",
) -> dict:
    phone_norm = normalize_phone_identifier(phone)
    email_norm = normalize_email_identifier(email)
    clean_channel = _sanitize_channel(channel)
    external = (external_sender_id or "").strip()
    if not (phone_norm or email_norm or (clean_channel and external)):
        raise ValueError("Provide a valid phone, email, or channel sender id.")
    if find_ignored_contact_duplicate(
        phone=phone,
        email=email,
        channel=clean_channel,
        external_sender_id=external,
    ):
        raise ValueError("Contact is already on the Ignore List.")
    now = datetime.now(timezone.utc).isoformat()
    tenant_id = _current_tenant_id()
    conn = _get_conn()
    cur = conn.execute(
        "INSERT INTO ignored_contacts "
        "(tenant_id, name, phone_original, phone_normalized, email_original, "
        "email_normalized, channel, external_sender_id, label, note, "
        "created_by, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            tenant_id,
            (name or "").strip()[:160],
            (phone or "").strip()[:120],
            phone_norm,
            (email or "").strip()[:180],
            email_norm,
            clean_channel,
            external[:220],
            _sanitize_ignore_label(label),
            (note or "").strip()[:1000],
            (created_by or "operator").strip()[:80],
            now,
            now,
        ),
    )
    contact_id = cur.lastrowid
    conn.commit()
    row = conn.execute(
        "SELECT id, tenant_id, name, phone_original, phone_normalized, "
        "email_original, email_normalized, channel, external_sender_id, "
        "label, note, created_by, created_at, updated_at, deleted_at "
        "FROM ignored_contacts WHERE id = ?",
        (contact_id,),
    ).fetchone()
    conn.close()
    return _ignored_contact_row_to_dict(row)


def update_ignored_contact(contact_id: int, **updates) -> dict | None:
    existing = next(
        (c for c in list_ignored_contacts() if c["id"] == int(contact_id)),
        None,
    )
    if not existing:
        return None
    merged = {
        "name": updates.get("name", existing["name"]),
        "phone": updates.get("phone", existing["phone_original"]),
        "email": updates.get("email", existing["email_original"]),
        "channel": updates.get("channel", existing["channel"]),
        "external_sender_id": updates.get(
            "external_sender_id", existing["external_sender_id"]),
        "label": updates.get("label", existing["label"]),
        "note": updates.get("note", existing["note"]),
    }
    phone_norm = normalize_phone_identifier(merged["phone"])
    email_norm = normalize_email_identifier(merged["email"])
    clean_channel = _sanitize_channel(merged["channel"])
    external = (merged["external_sender_id"] or "").strip()
    if not (phone_norm or email_norm or (clean_channel and external)):
        raise ValueError("Provide a valid phone, email, or channel sender id.")
    if find_ignored_contact_duplicate(
        phone=merged["phone"],
        email=merged["email"],
        channel=clean_channel,
        external_sender_id=external,
        exclude_id=int(contact_id),
    ):
        raise ValueError("Contact is already on the Ignore List.")
    now = datetime.now(timezone.utc).isoformat()
    conn = _get_conn()
    conn.execute(
        "UPDATE ignored_contacts SET name = ?, phone_original = ?, "
        "phone_normalized = ?, email_original = ?, email_normalized = ?, "
        "channel = ?, external_sender_id = ?, label = ?, note = ?, "
        "updated_at = ? WHERE tenant_id = ? AND id = ? AND deleted_at IS NULL",
        (
            (merged["name"] or "").strip()[:160],
            (merged["phone"] or "").strip()[:120],
            phone_norm,
            (merged["email"] or "").strip()[:180],
            email_norm,
            clean_channel,
            external[:220],
            _sanitize_ignore_label(merged["label"]),
            (merged["note"] or "").strip()[:1000],
            now,
            _current_tenant_id(),
            int(contact_id),
        ),
    )
    conn.commit()
    row = conn.execute(
        "SELECT id, tenant_id, name, phone_original, phone_normalized, "
        "email_original, email_normalized, channel, external_sender_id, "
        "label, note, created_by, created_at, updated_at, deleted_at "
        "FROM ignored_contacts WHERE id = ?",
        (int(contact_id),),
    ).fetchone()
    conn.close()
    return _ignored_contact_row_to_dict(row) if row else None


def delete_ignored_contact(contact_id: int) -> bool:
    now = datetime.now(timezone.utc).isoformat()
    conn = _get_conn()
    cur = conn.execute(
        "UPDATE ignored_contacts SET deleted_at = ?, updated_at = ? "
        "WHERE tenant_id = ? AND id = ? AND deleted_at IS NULL",
        (now, now, _current_tenant_id(), int(contact_id)),
    )
    conn.commit()
    changed = cur.rowcount > 0
    conn.close()
    return changed


def match_ignored_contact(
    *,
    channel: str = "",
    sender_id: str = "",
    phone: str = "",
    email: str = "",
) -> dict | None:
    tenant_id = _current_tenant_id()
    clean_channel = _sanitize_channel(channel)
    external = (sender_id or "").strip()
    phone_norm = normalize_phone_identifier(phone or sender_id)
    email_norm = normalize_email_identifier(email or sender_id)
    if clean_channel in ("whatsapp", "") and _is_operator_whatsapp_destination(phone or sender_id):
        return None
    clauses = []
    params: list = [tenant_id]
    if phone_norm:
        clauses.append("phone_normalized = ?")
        params.append(phone_norm)
    if email_norm:
        clauses.append("email_normalized = ?")
        params.append(email_norm)
    if clean_channel and external:
        clauses.append("(channel = ? AND external_sender_id = ?)")
        params.extend([clean_channel, external])
    if not clauses:
        return None
    conn = _get_conn()
    row = conn.execute(
        "SELECT id, tenant_id, name, phone_original, phone_normalized, "
        "email_original, email_normalized, channel, external_sender_id, "
        "label, note, created_by, created_at, updated_at, deleted_at "
        "FROM ignored_contacts WHERE tenant_id = ? AND deleted_at IS NULL "
        f"AND ({' OR '.join(clauses)}) "
        "ORDER BY updated_at DESC LIMIT 1",
        params,
    ).fetchone()
    conn.close()
    return _ignored_contact_row_to_dict(row) if row else None


def record_ignored_contact_event(
    *,
    contact_id: int | None,
    channel: str = "",
    sender_identifier: str = "",
    message_id: str = "",
    reason: str = "Ignored inbound message because sender is on Excluded Contacts / Ignore List.",
) -> None:
    conn = _get_conn()
    conn.execute(
        "INSERT INTO ignored_contact_events "
        "(tenant_id, ignored_contact_id, channel, sender_identifier, "
        "message_id, reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            _current_tenant_id(),
            contact_id,
            _sanitize_channel(channel),
            (sender_identifier or "").strip()[:220],
            (message_id or "").strip()[:220],
            reason,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.commit()
    conn.close()


def list_ignored_contact_events(limit: int = 100) -> list[dict]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT id, tenant_id, ignored_contact_id, channel, sender_identifier, "
        "message_id, reason, created_at FROM ignored_contact_events "
        "WHERE tenant_id = ? ORDER BY id DESC LIMIT ?",
        (_current_tenant_id(), max(1, min(int(limit), 500))),
    ).fetchall()
    conn.close()
    return [
        {
            "id": int(r[0]),
            "tenant_id": r[1] or "",
            "ignored_contact_id": r[2],
            "channel": r[3] or "",
            "sender_identifier": r[4] or "",
            "message_id": r[5] or "",
            "reason": r[6] or "",
            "created_at": r[7] or "",
        }
        for r in rows
    ]


def get_active_escalation_mode(conversation_id: str):
    """Brief 213: return the mode ('soft' / 'hard') of the most recent
    actionable escalation for this conversation, or None if none exist
    or the most recent has no mode set (legacy rows)."""
    if not conversation_id:
        return None
    conn = _get_conn()
    row = conn.execute(
        "SELECT CASE WHEN COUNT(*) = 0 THEN NULL "
        "WHEN MAX(CASE WHEN mode = 'hard' THEN 1 ELSE 0 END) = 1 "
        "THEN 'hard' ELSE 'soft' END FROM pending_notifications "
        "WHERE customer_id = ? "
        "AND notification_type IN ('escalation', 'relay') "
        "AND status IN ('pending', 'sent') "
        "AND (mode IS NULL OR mode IN ('soft', 'hard'))",
        (conversation_id,)).fetchone()
    conn.close()
    return row[0] if row and row[0] else None


def get_human_takeover_at(conversation_id: str):
    """Brief 222: ISO timestamp of when the operator took over this
    conversation, or None if no active takeover. Reads
    conversation_status.human_takeover_at (set by set_ai_muted(..., True)
    in Brief 213's takeover flow, cleared to NULL on handback)."""
    if not conversation_id:
        return None
    conn = _get_conn()
    row = conn.execute(
        "SELECT human_takeover_at FROM conversation_status "
        "WHERE conversation_id = ?",
        (conversation_id,)).fetchone()
    conn.close()
    return row[0] if row and row[0] else None


# ── Brief 217: Alert settings + delivery audit ──

def get_resolved_operator_whatsapp_route() -> dict | None:
    """Brief 240: return the Zernio route resolved for the operator
    WhatsApp alert destination, or None if not yet bootstrapped.

    Shape: {"conversation_id": str, "account_id": str, "resolved_at": str}.
    Both conversation_id and account_id must be non-empty for the route
    to count as resolved; otherwise returns None."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT whatsapp_zernio_conversation_id, "
        "whatsapp_zernio_account_id, whatsapp_zernio_resolved_at "
        "FROM alert_settings WHERE id = 1").fetchone()
    conn.close()
    if not row or not row[0] or not row[1]:
        return None
    return {
        "conversation_id": row[0],
        "account_id": row[1],
        "resolved_at": row[2] or "",
    }


def set_resolved_operator_whatsapp_route(conversation_id: str,
                                          account_id: str) -> None:
    """Brief 240: persist the Zernio route for operator WhatsApp alerts.
    UPSERTs into alert_settings - preserves the user-controlled
    whatsapp_destination + enabled flags + email columns. Idempotent:
    re-running with the same conv_id + account_id refreshes resolved_at
    only."""
    if not conversation_id or not account_id:
        return  # defensive: never persist a half-resolved route
    now = datetime.now(timezone.utc).isoformat()
    conn = _get_conn()
    conn.execute(
        "INSERT INTO alert_settings (id, whatsapp_zernio_conversation_id, "
        "whatsapp_zernio_account_id, whatsapp_zernio_resolved_at, "
        "updated_at) VALUES (1, ?, ?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET "
        "whatsapp_zernio_conversation_id = excluded.whatsapp_zernio_conversation_id, "
        "whatsapp_zernio_account_id = excluded.whatsapp_zernio_account_id, "
        "whatsapp_zernio_resolved_at = excluded.whatsapp_zernio_resolved_at, "
        "updated_at = excluded.updated_at",
        (conversation_id, account_id, now, now))
    conn.commit()
    conn.close()


def get_alert_settings(default_email_destination: str = "") -> dict:
    """Brief 217 + 226: return the alert config in SR's frontend shape.
    Channels.email always carries an `alternativeDestination` field (empty
    string when not configured). If no row exists yet, synthesize a default
    with email enabled + the given default destination (typically
    business.support_email from client.json).

    The `"default"` sentinel is RESOLVED in the response — i.e., GET
    returns the actual support_email value, not the literal string
    "default" — so the frontend renders the real destination.

    Brief 241: response gains a top-level `alertTypes` block
    (`{escalations: bool, appointments: bool}`) read from the new
    alert_type_*_enabled columns. Both default True for backward compat."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT email_enabled, email_destination, whatsapp_enabled, "
        "whatsapp_destination, telegram_enabled, telegram_destination, "
        "messenger_enabled, messenger_destination, "
        "email_alternative_destination, "
        "alert_type_escalation_enabled, alert_type_appointment_enabled "
        "FROM alert_settings WHERE id = 1").fetchone()
    conn.close()
    if not row:
        return {
            "alertTypes": {"escalations": True, "appointments": True},
            "channels": {
                "email":     {"enabled": True,  "destination": default_email_destination or "",
                              "alternativeDestination": ""},
                "whatsapp":  {"enabled": False, "destination": "",
                              "zernioResolved": False},
                "telegram":  {"enabled": False, "destination": ""},
                "messenger": {"enabled": False, "destination": ""},
            }
        }
    email_dest = row[1] or ""
    if email_dest in ("", "default"):
        email_dest = default_email_destination or ""
    return {
        "alertTypes": {
            "escalations": bool(row[9]),
            "appointments": bool(row[10]),
        },
        "channels": {
            "email":     {
                "enabled": bool(row[0]),
                "destination": email_dest,
                "alternativeDestination": row[8] or "",
            },
            "whatsapp":  {
                "enabled": bool(row[2]),
                "destination": row[3] or "",
                "zernioResolved": bool(get_resolved_operator_whatsapp_route()),
            },
            "telegram":  {"enabled": bool(row[4]), "destination": row[5] or ""},
            "messenger": {"enabled": bool(row[6]), "destination": row[7] or ""},
        }
    }


def save_alert_settings(channels: dict, alert_types: dict = None) -> None:
    """Brief 217 + 226 + 241: upsert the singleton alert_settings row using
    INSERT ... ON CONFLICT(id) DO UPDATE on a fixed id=1. Brief 240:
    switched from INSERT OR REPLACE to ON CONFLICT DO UPDATE so the
    bootstrap-only whatsapp_zernio_* columns survive a Settings save.
    Brief 241: alert_types is optional ({escalations: bool, appointments:
    bool}); both default True when not supplied or missing keys."""
    now = datetime.now(timezone.utc).isoformat()
    em = channels.get("email", {}) or {}
    wa = channels.get("whatsapp", {}) or {}
    tg = channels.get("telegram", {}) or {}
    ms = channels.get("messenger", {}) or {}
    at = alert_types or {}
    ate = 1 if at.get("escalations", True) else 0
    ata = 1 if at.get("appointments", True) else 0
    conn = _get_conn()
    conn.execute(
        "INSERT INTO alert_settings "
        "(id, email_enabled, email_destination, whatsapp_enabled, whatsapp_destination, "
        "telegram_enabled, telegram_destination, messenger_enabled, messenger_destination, "
        "email_alternative_destination, "
        "alert_type_escalation_enabled, alert_type_appointment_enabled, "
        "updated_at) "
        "VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET "
        "email_enabled = excluded.email_enabled, "
        "email_destination = excluded.email_destination, "
        "whatsapp_enabled = excluded.whatsapp_enabled, "
        "whatsapp_destination = excluded.whatsapp_destination, "
        "telegram_enabled = excluded.telegram_enabled, "
        "telegram_destination = excluded.telegram_destination, "
        "messenger_enabled = excluded.messenger_enabled, "
        "messenger_destination = excluded.messenger_destination, "
        "email_alternative_destination = excluded.email_alternative_destination, "
        "alert_type_escalation_enabled = excluded.alert_type_escalation_enabled, "
        "alert_type_appointment_enabled = excluded.alert_type_appointment_enabled, "
        "updated_at = excluded.updated_at",
        (1 if em.get("enabled") else 0, em.get("destination", ""),
         1 if wa.get("enabled") else 0, wa.get("destination", ""),
         1 if tg.get("enabled") else 0, tg.get("destination", ""),
         1 if ms.get("enabled") else 0, ms.get("destination", ""),
         em.get("alternativeDestination", "") or "",
         ate, ata, now))
    conn.commit()
    conn.close()


def record_alert_delivery(escalation_id, channel: str, destination: str,
                           status: str, error: str = None,
                           alert_type: str = "escalation",
                           appointment_id: int = None) -> int:
    """Brief 217 + 241: append a row to alert_deliveries. status one of
    'sent', 'failed', 'skipped'. alert_type is 'escalation' (default,
    backward compat) or 'appointment'. For appointment rows, pass
    escalation_id=None and appointment_id=<row_id>; for escalation rows,
    pass appointment_id=None (default). Returns row id."""
    now = datetime.now(timezone.utc).isoformat()
    conn = _get_conn()
    cur = conn.execute(
        "INSERT INTO alert_deliveries "
        "(escalation_id, channel, destination, status, error, sent_at, "
        "alert_type, appointment_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (escalation_id, channel, destination or "", status, error, now,
         alert_type, appointment_id))
    row_id = cur.lastrowid
    conn.commit()
    conn.close()
    return row_id


def appointment_alert_already_sent(appointment_id: int, channel: str,
                                    destination: str) -> bool:
    """Brief 241: layer-2 dedup for appointment alerts. Returns True when
    a previous appointment-alert delivery has already been recorded for
    this exact (appointment_id, channel, destination) tuple with a
    terminal status ('sent' or 'failed'). 'skipped' rows do NOT count -
    they reflect 'we couldn't send' (e.g., Zernio route not bootstrapped
    yet) and SHOULD retry on the next confirmation event if the
    configuration changes. Layer 1 dedup is the transition-aware trigger
    inside appointment_upsert."""
    if not appointment_id:
        return False
    conn = _get_conn()
    row = conn.execute(
        "SELECT 1 FROM alert_deliveries "
        "WHERE alert_type = 'appointment' AND appointment_id = ? "
        "AND channel = ? AND destination = ? "
        "AND status IN ('sent', 'failed') LIMIT 1",
        (appointment_id, channel, destination or "")).fetchone()
    conn.close()
    return row is not None


def email_clear_fully_escalated_flag(
    customer_email: str,
    *,
    strict: bool = False,
    require_no_active_review: bool = False,
) -> int:
    """Brief 254: clear flags.fully_escalated AND flags.awaiting_relay
    on ALL email_thread_state.json threads matching this customer email.
    Used by resolve_conversation_from_escalation + delete_escalation to
    prevent orphan escalation flags after the underlying pending_notifications
    row is resolved/deleted.

    Without this cleanup, email_list_conversations derives status='escalated'
    forever from flags.get('fully_escalated') OR flags.get('awaiting_relay')
    (state_registry.py:1156-1159) and the Inbox row shows an escalation
    badge with no matching row in /escalations -- the symptom Calvin
    reported in issue #23.

    Returns the count of threads whose flags were cleared. By default this is
    best-effort cleanup; ``strict=True`` raises on a missing, unreadable, or
    unwritable state file so a durable delivery worker can retry the effect.
    A provider-confirmed reply uses ``require_no_active_review`` so a new
    escalation committed after the answered row cannot lose its sidecar flag."""
    if not customer_email:
        return 0
    path = _get_email_state_path()
    with email_state_file_lock(path):
        if require_no_active_review and get_active_escalation_mode(customer_email) is not None:
            return 0
        if not os.path.exists(path):
            if strict:
                raise LookupError("Email conversation state was not found")
            return 0
        try:
            with open(path, "r", encoding="utf-8") as state_file:
                state = json.load(state_file)
            if not isinstance(state, dict):
                raise ValueError("Email conversation state must be an object")
        except (OSError, ValueError, TypeError) as exc:
            if strict:
                raise RuntimeError("Email conversation state could not be read") from exc
            return 0
        threads = state.get("threads") or {}
        cleared = 0
        needle = str(customer_email).strip().casefold()
        for thread_key, th in threads.items():
            if _email_thread_address(thread_key) != needle:
                continue
            flags = th.setdefault("flags", {})
            if flags.get("fully_escalated") or flags.get("awaiting_relay"):
                flags["fully_escalated"] = False
                flags.pop("awaiting_relay", None)
                cleared += 1
        if cleared == 0:
            return 0
        try:
            _write_email_state_unlocked(path, state)
        except OSError as exc:
            if strict:
                raise RuntimeError("Email conversation state could not be written") from exc
            return 0
        return cleared


def resolve_conversation_from_escalation(
    escalation_id: int,
    *,
    expected_content_revision: int | None = None,
    mark_notification_resolved: bool = False,
) -> bool:
    """Brief 188: when operator resolves an escalation, set conversation status
    to 'resolved' AND clear fully_escalated from booking state flags so the
    conversation returns to AI mode on the next customer message.

    Uses json_set() to avoid a read-modify-write cycle within this function.
    Note: a concurrent message thread that already loaded flags before this call
    may overwrite the clear via wa_save_booking_state — low severity, see brief.

    Brief 254: ALSO clears flags.fully_escalated in email_thread_state.json
    for the customer's email threads when esc_channel == 'email'. Pre-Brief-254
    the resolve path only cleared WA flags; email-channel escalations left
    orphan flags driving the Inbox status='escalated' forever (issue #23 root
    cause per Sonia's audit at issue #24)."""
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT customer_id, channel, status, content_revision "
            "FROM pending_notifications WHERE id = ?",
            (escalation_id,),
        ).fetchone()
        if not row:
            conn.rollback()
            return False
        customer_id, esc_channel, current_status, current_revision = row
        _require_escalation_revision(
            current_revision, expected_content_revision
        )
        if mark_notification_resolved:
            revision_delta = int(
                expected_content_revision is not None
                and current_status != "resolved"
            )
            conn.execute(
                "UPDATE pending_notifications SET status = 'resolved', "
                "content_revision = content_revision + ? WHERE id = ?",
                (revision_delta, escalation_id),
            )

        # Set conversation status to resolved and release human takeover/mute.
        # Resolved means the operator has completed the work item; leaving
        # ai_muted=1 makes the agent appear down on later customer follow-ups.
        conn.execute(
            "INSERT INTO conversation_status "
            "(conversation_id, channel, status, updated_at, ai_muted, human_takeover_at) "
            "VALUES (?, ?, 'resolved', ?, 0, NULL) "
            "ON CONFLICT(conversation_id) DO UPDATE SET status = 'resolved', "
            "ai_muted = 0, human_takeover_at = NULL, "
            "updated_at = excluded.updated_at",
            (
                customer_id,
                esc_channel or "whatsapp",
                datetime.now(timezone.utc).isoformat(),
            ),
        )

        # Atomically clear fully_escalated in booking state flags.
        conn.execute(
            "UPDATE whatsapp_booking_state "
            "SET flags_json = json_set(COALESCE(flags_json, '{}'), "
            "'$.fully_escalated', json('false')) WHERE phone = ?",
            (customer_id,),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    # Brief 254: also clear email flags when channel=email. Done OUTSIDE the
    # DB connection because email_thread_state.json is a file write.
    if esc_channel == "email" and customer_id:
        email_clear_fully_escalated_flag(
            customer_id,
            require_no_active_review=mark_notification_resolved,
        )
    return True


def _sync_mermaid_escalation_freezes(
    conn: sqlite3.Connection,
    customer_id: str,
    channel: str,
    now: str,
    *,
    preserve_manual_mute: bool = False,
) -> tuple[bool, bool]:
    """Synchronize every Mermaid freeze from all active review rows.

    One conversation can have both an escalation and a relay.  Resolving one
    row must therefore derive the effective state from the rows that remain,
    rather than blindly releasing the reservation.  A soft review freezes
    reservation changes while still allowing Tracy to talk; a hard review also
    mutes Tracy until an operator hands the conversation back.
    """
    active_modes = conn.execute(
        "SELECT mode FROM pending_notifications "
        "WHERE customer_id = ? "
        "AND notification_type IN ('escalation', 'relay') "
        "AND status IN ('pending', 'sent') "
        "AND (mode IS NULL OR mode IN ('soft', 'hard'))",
        (customer_id,),
    ).fetchall()
    has_active_review = bool(active_modes)
    has_hard_review = any(row[0] == "hard" for row in active_modes)
    has_reservations = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' "
        "AND name = 'mermaid_reservations'"
    ).fetchone()
    manual_mute = False
    if preserve_manual_mute:
        mute = conn.execute(
            "SELECT ai_muted, ai_mute_source FROM conversation_status WHERE conversation_id=?",
            (customer_id,),
        ).fetchone()
        if mute and mute[0]:
            manual_mute = mute[1] == "manual"
            if not mute[1] and not has_active_review:
                # Upgrade an old standalone manual pause conservatively. A
                # booking/escalation freeze is separate evidence of an orphan
                # that the existing repair is allowed to release.
                flag = conn.execute(
                    "SELECT 1 FROM whatsapp_booking_state WHERE phone=? "
                    "AND json_valid(COALESCE(flags_json, '{}')) "
                    "AND json_extract(COALESCE(flags_json, '{}'), '$.fully_escalated')=1",
                    (customer_id,),
                ).fetchone()
                frozen = has_reservations and conn.execute(
                    "SELECT 1 FROM mermaid_reservations WHERE tenant_slug='mermaid' "
                    "AND conversation_id=? AND human_takeover=1", (customer_id,),
                ).fetchone()
                manual_mute = not flag and not frozen
    effective_mute = has_hard_review or manual_mute
    conversation_status = "open" if has_active_review else "resolved"
    takeover_at = now if effective_mute else None

    conn.execute(
        "INSERT INTO conversation_status "
        "(conversation_id, channel, status, updated_at, ai_muted, human_takeover_at, ai_mute_source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(conversation_id) DO UPDATE SET "
        "status = excluded.status, ai_muted = excluded.ai_muted, "
        "ai_mute_source = excluded.ai_mute_source, "
        "human_takeover_at = CASE WHEN excluded.ai_muted = 1 "
        "THEN COALESCE(conversation_status.human_takeover_at, "
        "excluded.human_takeover_at) ELSE NULL END, "
        "updated_at = excluded.updated_at",
        (
            customer_id,
            channel or "whatsapp",
            conversation_status,
            now,
            1 if effective_mute else 0,
            takeover_at,
            "manual" if manual_mute else "escalation" if has_hard_review else "",
        ),
    )
    conn.execute(
        "UPDATE whatsapp_booking_state "
        "SET flags_json = json_set(COALESCE(flags_json, '{}'), "
        "'$.fully_escalated', json(?)) WHERE phone = ?",
        ("true" if has_hard_review else "false", customer_id),
    )
    if has_reservations:
        conn.execute(
            "UPDATE mermaid_reservations SET human_takeover = ?, updated_at = ? "
            "WHERE tenant_slug = 'mermaid' AND conversation_id = ?",
            (1 if has_active_review else 0, now, customer_id),
        )
    return has_active_review, has_hard_review


def reconcile_mermaid_escalation_freezes() -> dict:
    """Repair stale Mermaid mute/freeze state from durable active reviews.

    This runs when the Mermaid service starts, including the first start after
    upgrading from the one-way dashboard flags.  It repairs both orphaned
    freezes and missing freezes in one immediate transaction.
    """
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conversation_ids = {
            str(row[0])
            for row in conn.execute(
                "SELECT DISTINCT customer_id FROM pending_notifications "
                "WHERE notification_type IN ('escalation', 'relay')"
            ).fetchall()
            if row[0]
        }
        conversation_ids.update(
            str(row[0])
            for row in conn.execute(
                "SELECT conversation_id FROM conversation_status "
                "WHERE ai_muted=1 OR human_takeover_at IS NOT NULL"
            ).fetchall()
            if row[0]
        )
        conversation_ids.update(
            str(row[0])
            for row in conn.execute(
                "SELECT phone FROM whatsapp_booking_state "
                "WHERE json_valid(COALESCE(flags_json, '{}')) "
                "AND json_extract(COALESCE(flags_json, '{}'), "
                "'$.fully_escalated') = 1"
            ).fetchall()
            if row[0]
        )
        has_reservations = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='mermaid_reservations'"
        ).fetchone()
        if has_reservations:
            conversation_ids.update(
                str(row[0])
                for row in conn.execute(
                    "SELECT DISTINCT conversation_id FROM mermaid_reservations "
                    "WHERE tenant_slug='mermaid' AND human_takeover=1"
                ).fetchall()
                if row[0]
            )

        active = 0
        hard = 0
        for customer_id in sorted(conversation_ids):
            channel_row = conn.execute(
                "SELECT channel FROM pending_notifications WHERE customer_id=? "
                "ORDER BY created_at DESC,id DESC LIMIT 1",
                (customer_id,),
            ).fetchone()
            if channel_row is None:
                channel_row = conn.execute(
                    "SELECT channel FROM conversation_status WHERE conversation_id=?",
                    (customer_id,),
                ).fetchone()
            has_active, has_hard = _sync_mermaid_escalation_freezes(
                conn,
                customer_id,
                str(channel_row[0] if channel_row else "whatsapp"),
                now,
                preserve_manual_mute=True,
            )
            active += int(has_active)
            hard += int(has_hard)
        conn.commit()
        return {
            "conversations": len(conversation_ids),
            "active": active,
            "hard": hard,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def release_mermaid_escalation(
    escalation_id: int,
    *,
    set_mode_soft: bool = False,
    expected_content_revision: int | None = None,
) -> dict | None:
    """Resolve a Mermaid work item and release every persisted AI freeze.

    Mermaid has two additional sources of truth beyond the generic dashboard
    mute: ``whatsapp_booking_state.flags.fully_escalated`` and
    ``mermaid_reservations.human_takeover``.  Updating only
    ``conversation_status.ai_muted`` leaves Tracy unable to continue the
    reservation.  Keep the notification, conversation, booking, and
    reservation changes in one immediate transaction so a partial handback
    cannot make the dashboard claim that Tracy is active while her booking is
    still frozen.

    ``set_mode_soft`` is used by the Hand back action.  The work item is also
    resolved because an active soft escalation is itself a Mermaid workflow
    freeze.  Returns the released row identity, or ``None`` when the work item
    does not exist.
    """
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT customer_id, channel, mode, status, content_revision "
            "FROM pending_notifications "
            "WHERE id = ? AND notification_type IN ('escalation', 'relay')",
            (escalation_id,),
        ).fetchone()
        if not row:
            conn.rollback()
            return None
        customer_id, esc_channel, current_mode, current_status, current_revision = row
        current_revision = _require_escalation_revision(
            current_revision, expected_content_revision
        )
        resulting_mode = "soft" if set_mode_soft else current_mode
        next_revision = current_revision + int(
            current_status != "resolved" or resulting_mode != current_mode
        )
        if set_mode_soft:
            conn.execute(
                "UPDATE pending_notifications SET status = 'resolved', mode = 'soft', "
                "content_revision = ? "
                "WHERE id = ?",
                (next_revision, escalation_id),
            )
        else:
            conn.execute(
                "UPDATE pending_notifications SET status = 'resolved', "
                "content_revision = ? WHERE id = ?",
                (next_revision, escalation_id),
            )

        has_active_review, _has_hard_review = _sync_mermaid_escalation_freezes(
            conn, customer_id, esc_channel or "whatsapp", now
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    if esc_channel == "email" and customer_id and not has_active_review:
        email_clear_fully_escalated_flag(
            customer_id, require_no_active_review=True
        )
    return {
        "customer_id": customer_id,
        "channel": esc_channel or "whatsapp",
        "mode": resulting_mode,
        "status": "resolved",
        "content_revision": next_revision,
    }


def reply_mermaid_escalation(escalation_id: int) -> dict | None:
    """Mark one delivered operator answer complete and derive every freeze.

    ``replied`` is a terminal notification status.  Mermaid booking and mute
    state must therefore be recomputed in the same transaction, while another
    pending review for the conversation must continue to hold its own freeze.
    """
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT customer_id, channel, mode FROM pending_notifications "
            "WHERE id = ? AND notification_type IN ('escalation', 'relay')",
            (escalation_id,),
        ).fetchone()
        if not row:
            conn.rollback()
            return None
        customer_id, esc_channel, mode = row
        conn.execute(
            "UPDATE pending_notifications SET status = 'replied' WHERE id = ?",
            (escalation_id,),
        )
        has_active_review, _has_hard_review = _sync_mermaid_escalation_freezes(
            conn, customer_id, esc_channel or "whatsapp", now
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    if esc_channel == "email" and customer_id and not has_active_review:
        email_clear_fully_escalated_flag(customer_id)
    return {
        "customer_id": customer_id,
        "channel": esc_channel or "whatsapp",
        "mode": mode,
        "status": "replied",
        "active_review": has_active_review,
    }


def set_mermaid_escalation_mode(
    escalation_id: int,
    mode: str,
    *,
    expected_content_revision: int | None = None,
) -> dict | None:
    """Change a Mermaid review mode and atomically apply its booking freeze."""
    if mode not in {"soft", "hard", "order"}:
        return None
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT customer_id, channel, mode, content_revision "
            "FROM pending_notifications "
            "WHERE id = ? AND notification_type IN ('escalation', 'relay')",
            (escalation_id,),
        ).fetchone()
        if not row:
            conn.rollback()
            return None
        customer_id, esc_channel, current_mode, current_revision = row
        current_revision = _require_escalation_revision(
            current_revision, expected_content_revision
        )
        next_revision = current_revision + int(current_mode != mode)
        conn.execute(
            "UPDATE pending_notifications SET mode = ?, content_revision = ? "
            "WHERE id = ?",
            (mode, next_revision, escalation_id),
        )
        has_active_review, has_hard_review = _sync_mermaid_escalation_freezes(
            conn, customer_id, esc_channel or "whatsapp", now
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {
        "customer_id": customer_id,
        "channel": esc_channel or "whatsapp",
        "mode": mode,
        "content_revision": next_revision,
        "active_review": has_active_review,
        "hard_review": has_hard_review,
    }


def reopen_mermaid_escalation(
    escalation_id: int,
    *,
    expected_content_revision: int | None = None,
) -> dict | None:
    """Reopen a resolved Mermaid review and restore its booking freeze.

    Hard reviews also restore the transport-level AI mute.  Soft reviews keep
    transport delivery enabled, while the active escalation and reservation
    freeze prevent Tracy from changing the booking until the operator releases
    it again.
    """
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT customer_id, channel, mode, status, content_revision "
            "FROM pending_notifications "
            "WHERE id = ? AND notification_type IN ('escalation', 'relay')",
            (escalation_id,),
        ).fetchone()
        if not row:
            conn.rollback()
            return None
        customer_id, esc_channel, stored_mode, current_status, current_revision = row
        current_revision = _require_escalation_revision(
            current_revision, expected_content_revision
        )
        mode = stored_mode if stored_mode in {"soft", "hard"} else "soft"
        next_revision = current_revision + int(
            current_status != "sent" or stored_mode != mode
        )
        conn.execute(
            "UPDATE pending_notifications SET status = 'sent', mode = ?, "
            "content_revision = ? WHERE id = ?",
            (mode, next_revision, escalation_id),
        )
        _sync_mermaid_escalation_freezes(
            conn, customer_id, esc_channel or "whatsapp", now
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {
        "customer_id": customer_id,
        "channel": esc_channel or "whatsapp",
        "mode": mode,
        "status": "sent",
        "content_revision": next_revision,
    }


def reopen_conversation_from_escalation(
    escalation_id: int,
    *,
    expected_content_revision: int | None = None,
) -> bool:
    """Reopen a previously resolved escalation.

    This is the inverse of ``resolve_conversation_from_escalation`` for the
    operator dashboard's Unresolve action. It intentionally does not delete
    messages, learning entries, or alert delivery history. The escalation row
    returns to status='sent' so it is active again, while the stored mode
    ('soft'/'hard') remains untouched and continues to drive the correct UI tab.
    """
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT customer_id, channel, status, content_revision "
            "FROM pending_notifications WHERE id = ?",
            (escalation_id,),
        ).fetchone()
        if not row:
            conn.rollback()
            return False
        customer_id, esc_channel, current_status, current_revision = row
        _require_escalation_revision(
            current_revision, expected_content_revision
        )
        revision_delta = int(
            expected_content_revision is not None and current_status != "sent"
        )
        cur = conn.execute(
            "UPDATE pending_notifications SET status = 'sent', "
            "content_revision = content_revision + ? WHERE id = ?",
            (revision_delta, escalation_id),
        )
        conn.execute(
            "INSERT INTO conversation_status "
            "(conversation_id, channel, status, updated_at) "
            "VALUES (?, ?, 'open', ?) "
            "ON CONFLICT(conversation_id) DO UPDATE SET status = 'open', "
            "updated_at = excluded.updated_at",
            (
                customer_id,
                esc_channel or "whatsapp",
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
        return cur.rowcount > 0
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _lookup_customer_contact(customer_id: str, contact_type: str) -> dict:
    """Brief 183: look up the customer's real email and phone from customer_identifiers
    via the customer_id stored in the escalation. Returns {'email': ..., 'phone': ...}
    with None for any identifier not found."""
    if not customer_id:
        return {"email": None, "phone": None}

    # Determine the identifier type from contact_type
    id_type = "email" if contact_type == "email" else "wa_conversation_id" if contact_type == "whatsapp" else "phone"

    conn = _get_conn()
    # Find the customer row via their identifier
    cust_row = conn.execute(
        "SELECT customer_id FROM customer_identifiers WHERE type = ? AND value = ? LIMIT 1",
        (id_type, customer_id)
    ).fetchone()

    if not cust_row:
        conn.close()
        # If customer_id IS an email, return it directly
        if contact_type == "email":
            return {"email": customer_id, "phone": None}
        return {"email": None, "phone": None}

    cust_id = cust_row[0]

    # Get all identifiers for this customer
    idents = conn.execute(
        "SELECT type, value FROM customer_identifiers WHERE customer_id = ?",
        (cust_id,)
    ).fetchall()
    conn.close()

    email = None
    phone = None
    for ident in idents:
        if ident[0] == "email" and not email:
            email = ident[1]
        elif ident[0] == "phone" and not phone:
            phone = ident[1]

    return {"email": email, "phone": phone}


def _infer_contact_type(customer_id: str) -> str:
    """Brief 181: infer the type of contact identifier for display purposes.
    Replicates the 24-char hex check from whatsapp_client._is_zernio_conversation_id
    (duplicated here to avoid circular import between state_registry and whatsapp_client)."""
    if not customer_id:
        return "unknown"
    if "@" in customer_id:
        return "email"
    if len(customer_id) == 24:
        try:
            int(customer_id, 16)
            return "whatsapp"
        except ValueError:
            pass
    return "phone"


def get_all_escalations() -> list:
    """Return all escalation notifications, newest first.
    Brief 181: contact_type. Brief 183: customer_contact. Brief 188:
    conversation_status. Brief 213: mode. Brief 211: routable phone field.
    Brief 227: escalation_summary parsed and surfaced as escalationSummary +
    recommendedOptions + extractedDetails.
    Brief 253: excludes escalations whose WhatsApp/IG/FB conversation has
    been archived via Brief 249's archive endpoint
    (conversation_status.deleted=1). Email-channel archives use a
    different mechanism (flags.deleted in email_thread_state.json) and
    are NOT filtered by this JOIN -- see Brief 253 out-of-scope notes."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT pn.id, pn.notification_type, pn.relay_token, pn.channel, "
        "pn.customer_id, pn.customer_name, pn.subject, pn.body, pn.status, "
        "pn.created_at, pn.mode, pn.escalation_summary, pn.email_thread_key, "
        "pn.email_reply_subject, pn.content_revision "
        "FROM pending_notifications pn "
        # Brief 253: LEFT JOIN to drop escalations on archived conversations.
        # LEFT JOIN preserves rows whose conversation has no
        # conversation_status entry at all (most active conversations).
        "LEFT JOIN conversation_status cs ON pn.customer_id = cs.conversation_id "
        "WHERE pn.notification_type IN ('escalation', 'relay') "
        "AND (cs.deleted IS NULL OR cs.deleted = 0) "
        "ORDER BY pn.created_at DESC"
    ).fetchall()
    conn.close()
    result = []
    for r in rows:
        ct = _infer_contact_type(r[4] or "")
        contact = _lookup_customer_contact(r[4] or "", ct)
        customer_contact = contact["email"] or contact["phone"] or r[4] or ""
        _email_thread_key = ""
        _email_reply_subject = ""
        if r[3] == "email":
            _email_thread_key = str(r[12] or "")
            # Only legacy rows without a stored identity may resolve to the
            # customer's newest thread.  A nonblank missing key represents a
            # removed/stale exact thread and must remain visible as such so the
            # send path can fail closed instead of targeting another subject.
            if not _email_thread_key:
                _email_thread_key = _find_email_thread_key_for(r[4]) or ""
            _email_reply_subject = str(r[13] or "")
            if not _email_reply_subject and _email_thread_key:
                _thread_parts = _email_thread_key.split(":", 2)
                if len(_thread_parts) == 3 and _thread_parts[2]:
                    _email_reply_subject = "Re: " + _thread_parts[2]
            _phone_routing_key = f"email::{_email_thread_key}" if _email_thread_key else (r[4] or "")
        else:
            _phone_routing_key = r[4] or ""

        # Brief 227: parse the JSON summary blob into structured fields.
        summary_obj = None
        if r[11]:
            try:
                summary_obj = json.loads(r[11])
            except (json.JSONDecodeError, TypeError):
                summary_obj = None

        result.append({
            "id": r[0], "notification_type": r[1], "relay_token": r[2],
            "channel": r[3], "customer_id": r[4], "customer_name": r[5],
            "subject": r[6], "body": r[7], "status": r[8], "created_at": r[9],
            "mode": r[10],
            "content_revision": int(r[14] or 1),
            "contact_type": ct,
            "customer_contact": customer_contact,
            "customer_email": contact["email"],
            "customer_phone": contact["phone"],
            "conversation_status": get_conversation_status(r[4]),
            "phone": _phone_routing_key,
            "escalationSummary": summary_obj,
            "recommendedOptions": (
                (summary_obj or {}).get("recommendedOptions") or []),
            "extractedDetails": (
                (summary_obj or {}).get("extractedDetails") or None),
            "email_thread_key": _email_thread_key,
            "email_reply_subject": _email_reply_subject,
        })
    return result


def get_active_escalation_summary_for(customer_id: str) -> Optional[dict]:
    """Brief 227: return the parsed escalation_summary dict for the most
    recent unresolved escalation on this conversation, or None.

    Used by GET /messages/conversations/:phone to enrich the response
    with escalationSummary so the frontend's EscalationReasonPanel can
    render without a second fetch."""
    if not customer_id:
        return None
    conn = _get_conn()
    row = conn.execute(
        "SELECT escalation_summary FROM pending_notifications "
        "WHERE customer_id = ? AND notification_type = 'escalation' "
        "AND status IN ('pending', 'sent') "
        "ORDER BY created_at DESC LIMIT 1",
        (customer_id,)).fetchone()
    conn.close()
    if not row or not row[0]:
        return None
    try:
        return json.loads(row[0])
    except (json.JSONDecodeError, TypeError):
        return None


def appointment_upsert(conversation_id: str, channel: str, customer_name: str,
                       title: str, proposed_times: list, location: str = "",
                       status: str = "detected",
                       date_time_label: str = None) -> int:
    """Brief 228: upsert an appointment row keyed on conversation_id.
    proposed_times is a list of strings; we store JSON.

    Brief 248: date_time_label is the headline time string the frontend
    displays. When supplied (e.g., the customer's explicit confirmation
    extracted by the Brief 248 confirmedTime schema field), use it
    verbatim. When None (legacy callers like Brief 242's
    appointment_confirm_by_id), fall back to the first proposed_time -
    preserves pre-Brief-248 behavior so existing callers don't change.

    Brief 241: when this call transitions the appointment INTO 'confirmed'
    (insert with status='confirmed', OR update from a non-confirmed status
    to 'confirmed'), fire the registered _appointment_alert_dispatcher
    best-effort. Re-saves of the same 'confirmed' status do NOT fire
    (transition detection)."""
    if not conversation_id:
        return 0
    now = datetime.now(timezone.utc).isoformat()
    pt = proposed_times or []
    # Brief 248: explicit override wins; otherwise fall back to first
    # proposed time (pre-Brief-248 behavior preserved for callers that
    # don't supply date_time_label, e.g. appointment_confirm_by_id).
    label = date_time_label if date_time_label is not None else (pt[0] if pt else "")
    conn = _get_conn()
    existing = conn.execute(
        "SELECT id, status FROM appointments WHERE conversation_id = ?",
        (conversation_id,)).fetchone()
    transitioned_to_confirmed = False
    if existing:
        old_status = existing[1] or ""
        conn.execute(
            "UPDATE appointments SET channel = ?, customer_name = ?, "
            "title = ?, date_time_label = ?, proposed_times_json = ?, "
            "location = ?, status = ?, updated_at = ? "
            "WHERE id = ?",
            (channel, customer_name, title, label, json.dumps(pt),
             location, status, now, existing[0]))
        row_id = existing[0]
        if old_status != "confirmed" and status == "confirmed":
            transitioned_to_confirmed = True
    else:
        cur = conn.execute(
            "INSERT INTO appointments "
            "(conversation_id, channel, customer_name, title, date_time_label, "
            "proposed_times_json, location, status, source, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'conversation', ?, ?)",
            (conversation_id, channel, customer_name, title, label,
             json.dumps(pt), location, status, now, now))
        row_id = cur.lastrowid
        if status == "confirmed":
            transitioned_to_confirmed = True
    conn.commit()
    conn.close()

    # Brief 241: best-effort appointment alert dispatch on transition.
    # Wrapped in try/except so a dispatcher failure NEVER blocks the
    # appointment row from being saved. Tenant gate: alertTypes.appointments
    # in alert_settings; default True (on).
    if transitioned_to_confirmed and _appointment_alert_dispatcher is not None:
        try:
            settings = get_alert_settings(default_email_destination="")
            alert_types = (settings or {}).get("alertTypes") or {}
            if alert_types.get("appointments", True):
                appointment_dict = {
                    "id": row_id,
                    "conversation_id": conversation_id,
                    "channel": channel,
                    "customer_name": customer_name,
                    "title": title,
                    "date_time_label": label,
                    "proposed_times": pt,
                    "location": location,
                    "status": "confirmed",
                }
                _appointment_alert_dispatcher(
                    row_id, customer_name, channel, appointment_dict)
        except Exception:
            pass

    return row_id


def appointment_get_by_conversation(conversation_id: str) -> dict | None:
    """Return the appointment row for a conversation_id, if one exists."""
    if not conversation_id:
        return None
    conn = _get_conn()
    row = conn.execute(
        "SELECT id, conversation_id, channel, customer_name, title, "
        "date_time_label, proposed_times_json, location, status, source, "
        "created_at, updated_at "
        "FROM appointments WHERE conversation_id = ?",
        (conversation_id,),
    ).fetchone()
    conn.close()
    if not row:
        return None
    try:
        proposed = json.loads(row[6]) if row[6] else []
    except (json.JSONDecodeError, TypeError):
        proposed = []
    return {
        "id": str(row[0]),
        "conversationId": row[1],
        "channel": row[2],
        "customerName": row[3] or "",
        "title": row[4] or "Appointment",
        "dateTimeLabel": row[5] or "",
        "proposedTimes": proposed,
        "location": row[7] or None,
        "status": row[8],
        "source": row[9] or "conversation",
        "createdAt": row[10],
        "updatedAt": row[11],
    }


def appointment_confirm_by_id(appointment_id: int,
                               confirmed_by: str = "operator",
                               note: str | None = None) -> dict | None:
    """Brief 242: flip an appointment's status to 'confirmed' by id.
    Re-uses appointment_upsert (keyed on conversation_id) so the Brief
    241 transition detection fires the appointment alert dispatcher
    exactly once - second/duplicate confirm calls find old_status ==
    'confirmed' and the transition guard correctly classifies them as
    no-fire.

    Returns:
        {"id": int, "status": "confirmed", "confirmedAt": iso_str,
         "alreadyConfirmed": bool} on success.
        None when no appointment row matches the given id (caller
        surfaces 404).

    confirmed_by + note are accepted for forward API compat (frontend
    can pass operator identity / note text) but are NOT persisted in
    this brief - no schema column for them yet. A future brief can
    ALTER ADD COLUMN if an audit trail of WHO confirmed is needed.

    Soft coupling note: the confirmedAt timestamp is read from the
    appointments.updated_at column AFTER the upsert - this works
    because appointment_upsert always bumps updated_at (even on
    no-op confirmed->confirmed re-saves at line 2152). If a future
    refactor makes appointment_upsert skip the UPDATE on no-op,
    confirmedAt for alreadyConfirmed=True callers would become
    stale and need explicit recomputation here."""
    if not appointment_id:
        return None
    conn = _get_conn()
    row = conn.execute(
        "SELECT id, conversation_id, channel, customer_name, title, "
        "proposed_times_json, location, status "
        "FROM appointments WHERE id = ?", (appointment_id,)).fetchone()
    conn.close()
    if not row:
        return None
    (rid, conv_id, channel, customer_name, title, ptj, location,
     old_status) = row
    already_confirmed = (old_status == "confirmed")
    try:
        proposed_times = json.loads(ptj) if ptj else []
    except (json.JSONDecodeError, TypeError):
        proposed_times = []
    appointment_upsert(
        conversation_id=conv_id,
        channel=channel,
        customer_name=customer_name or "",
        title=title or "",
        proposed_times=proposed_times,
        location=location or "",
        status="confirmed",
    )
    conn = _get_conn()
    ts_row = conn.execute(
        "SELECT updated_at FROM appointments WHERE id = ?",
        (appointment_id,)).fetchone()
    conn.close()
    confirmed_at = ts_row[0] if ts_row else datetime.now(
        timezone.utc).isoformat()
    return {
        "id": rid,
        "status": "confirmed",
        "confirmedAt": confirmed_at,
        "alreadyConfirmed": already_confirmed,
    }


def appointments_list() -> list:
    """Brief 228: return all appointments newest-updated first, in the
    shape SR's frontend expects (camelCase, ISO timestamps).
    proposed_times_json is parsed and surfaced as proposedTimes for
    detail views; date_time_label is the headline string."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT id, conversation_id, channel, customer_name, title, "
        "date_time_label, proposed_times_json, location, status, source, "
        "created_at, updated_at "
        "FROM appointments ORDER BY updated_at DESC"
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        try:
            proposed = json.loads(r[6]) if r[6] else []
        except (json.JSONDecodeError, TypeError):
            proposed = []
        out.append({
            "id": str(r[0]),
            "conversationId": r[1],
            "channel": r[2],
            "customerName": r[3] or "",
            "title": r[4] or "Appointment",
            "dateTimeLabel": r[5] or "",
            "proposedTimes": proposed,
            "location": r[7] or None,
            "status": r[8],
            "source": r[9] or "conversation",
            "createdAt": r[10],
            "updatedAt": r[11],
        })
    return out


def get_data_retention_settings() -> dict:
    """Brief 229: return retention settings in SR's frontend shape
    (camelCase, status.policyActive=false until cleanup is implemented).
    Synthesizes a default row when none exists yet — defaults match
    SR's `DEFAULT_DATA_RETENTION` constant verbatim."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT active_inbox_archive_after_days, archive_retention_months, "
        "end_of_retention_action, keep_approved_learnings, "
        "audit_log_retention_months FROM data_retention_settings WHERE id = 1"
    ).fetchone()
    conn.close()
    # Brief 237: policyActive stays False until automatic cron ships in
    # a future brief. manualActionsAvailable=True signals the 3 action
    # endpoints (archive-now / export / delete-customer-data) are live.
    _STATUS = {
        "policyActive": False,
        "manualActionsAvailable": True,
        "nextCleanupAt": None,
    }
    if not row:
        return {
            "activeInboxArchiveAfterDays": 90,
            "archiveRetentionMonths": 24,
            "endOfRetentionAction": "anonymize",
            "keepApprovedLearnings": True,
            "auditLogRetentionMonths": 24,
            "status": dict(_STATUS),
        }
    return {
        "activeInboxArchiveAfterDays": row[0],
        "archiveRetentionMonths": row[1],
        "endOfRetentionAction": row[2] or "anonymize",
        "keepApprovedLearnings": bool(row[3]),
        "auditLogRetentionMonths": row[4] or 24,
        "status": dict(_STATUS),
    }


def save_data_retention_settings(active_inbox_archive_after_days,
                                  archive_retention_months,
                                  end_of_retention_action: str,
                                  keep_approved_learnings: bool,
                                  audit_log_retention_months: int) -> None:
    """Brief 229: upsert the singleton retention settings row at id=1
    (mirrors Brief 217's INSERT OR REPLACE pattern). Caller is
    responsible for validating discrete value sets — this helper trusts
    its inputs."""
    now = datetime.now(timezone.utc).isoformat()
    conn = _get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO data_retention_settings "
        "(id, active_inbox_archive_after_days, archive_retention_months, "
        "end_of_retention_action, keep_approved_learnings, "
        "audit_log_retention_months, updated_at) "
        "VALUES (1, ?, ?, ?, ?, ?, ?)",
        (active_inbox_archive_after_days, archive_retention_months,
         end_of_retention_action,
         1 if keep_approved_learnings else 0,
         audit_log_retention_months, now))
    conn.commit()
    conn.close()


def data_retention_audit_write(action: str, identifier_type, identifier_value,
                                affected_counts: dict, actor: str = "dashboard") -> int:
    """Brief 237: record a retention action attempt to data_retention_audit_log.
    Called for archive-now / export / delete-customer-data (success AND blocked).
    Rule 10 of SR's task ab7d8f1eb97c."""
    now = datetime.now(timezone.utc).isoformat()
    conn = _get_conn()
    cur = conn.execute(
        "INSERT INTO data_retention_audit_log "
        "(action, identifier_type, identifier_value, affected_counts_json, "
        "actor, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (action, identifier_type, identifier_value,
         json.dumps(affected_counts or {}, default=str),
         actor, now))
    row_id = cur.lastrowid
    conn.commit()
    conn.close()
    return row_id


def archive_inactive_conversations(active_inbox_archive_after_days: int) -> dict:
    """Brief 237: archive-now sweep. Sets flags.deleted on email threads
    inactive longer than N days; upserts conversation_status.deleted=1 on
    WhatsApp/IG/FB. Skips active escalations (Brief 235's pending|sent
    filter) and human takeover (ai_muted / fully_escalated). Returns
    counts."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=active_inbox_archive_after_days)
    archived = 0
    skipped_escalation = 0
    skipped_takeover = 0
    already_archived = 0

    # Email side — lock the shared sidecar across read/modify/replace.
    email_path = _get_email_state_path()
    with email_state_file_lock(email_path):
        if os.path.exists(email_path):
            state = _read_email_state_unlocked(email_path, {})
            if state:
                now_iso = datetime.now(timezone.utc).isoformat()
                for thread_key, th in state.get("threads", {}).items():
                    flags = th.setdefault("flags", {})
                    if flags.get("deleted"):
                        already_archived += 1
                        continue
                    if flags.get("fully_escalated"):
                        skipped_escalation += 1
                        continue
                    if flags.get("ai_muted"):
                        skipped_takeover += 1
                        continue
                    last_raw = th.get("last_activity")
                    if last_raw is None:
                        continue
                    if isinstance(last_raw, str):
                        try:
                            last_dt = datetime.fromisoformat(last_raw)
                        except ValueError:
                            continue
                    else:
                        last_dt = datetime.fromtimestamp(float(last_raw), tz=timezone.utc)
                    if last_dt < cutoff:
                        flags["deleted"] = True
                        th["last_activity"] = now_iso
                        archived += 1
                try:
                    _write_email_state_unlocked(email_path, state)
                except OSError:
                    pass

    # WA/IG/FB side — group by phone, find max(created_at), check active escalations.
    conn = _get_conn()
    rows = conn.execute(
        "SELECT phone, MAX(created_at) FROM whatsapp_threads GROUP BY phone"
    ).fetchall()
    now_iso = datetime.now(timezone.utc).isoformat()
    for phone, max_created in rows:
        if not max_created:
            continue
        try:
            last_dt = datetime.fromisoformat(max_created)
        except ValueError:
            continue
        if last_dt >= cutoff:
            continue
        cs = conn.execute(
            "SELECT deleted, blocked, ai_muted FROM conversation_status "
            "WHERE conversation_id = ?", (phone,)
        ).fetchone()
        if cs:
            deleted_flag, blocked_flag, ai_muted_flag = cs
            if deleted_flag:
                already_archived += 1
                continue
            if blocked_flag:
                already_archived += 1
                continue
            if ai_muted_flag:
                skipped_takeover += 1
                continue
        active_esc = conn.execute(
            "SELECT 1 FROM pending_notifications WHERE customer_id = ? "
            "AND status IN ('pending', 'sent') LIMIT 1", (phone,)
        ).fetchone()
        if active_esc:
            skipped_escalation += 1
            continue
        conn.execute(
            "INSERT INTO conversation_status "
            "(conversation_id, channel, status, updated_at, deleted) "
            "VALUES (?, 'whatsapp', 'archived', ?, 1) "
            "ON CONFLICT(conversation_id) DO UPDATE SET deleted = 1, "
            "updated_at = excluded.updated_at",
            (phone, now_iso))
        archived += 1
    conn.commit()
    conn.close()
    return {
        "archivedCount": archived,
        "skippedActiveEscalation": skipped_escalation,
        "skippedHumanTakeover": skipped_takeover,
        "alreadyArchived": already_archived,
    }


def export_all_customer_data(export_dir: str, tenant: str) -> dict:
    """Brief 237: dump all customer-side data to a JSON file under
    export_dir. Returns the path + per-table counts. Approved learnings
    and tasks are intentionally excluded — those are operator-curated."""
    now_iso = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = os.path.join(export_dir, f"{tenant}-{now_iso}.json")
    conn = _get_conn()

    def _rows(sql):
        cur = conn.execute(sql)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def _optional_table_rows(table_name):
        if not _has_table(conn, table_name):
            return []
        return _rows(f"SELECT * FROM {table_name}")

    payload = {
        "tenant": tenant,
        "exportedAt": now_iso,
        "customers": _rows("SELECT * FROM customers"),
        "customer_identifiers": _rows("SELECT * FROM customer_identifiers"),
        "customer_interactions": _rows("SELECT * FROM customer_interactions"),
        "whatsapp_threads": _rows("SELECT * FROM whatsapp_threads"),
        "whatsapp_booking_state": _rows("SELECT * FROM whatsapp_booking_state"),
        "pending_notifications": _rows("SELECT * FROM pending_notifications"),
        "appointments": _rows("SELECT * FROM appointments"),
        "bookings": _rows("SELECT * FROM bookings"),
        "service_bookings": _rows("SELECT * FROM service_bookings"),
        "conversation_status": _rows("SELECT * FROM conversation_status"),
        "mermaid_crew_assistance": _optional_table_rows(
            "mermaid_crew_assistance"
        ),
        "mermaid_crew_assistance_events": _optional_table_rows(
            "mermaid_crew_assistance_events"
        ),
        "mermaid_crew_assistance_reservations": _optional_table_rows(
            "mermaid_crew_assistance_reservations"
        ),
        "mermaid_crew_assistance_sources": _optional_table_rows(
            "mermaid_crew_assistance_sources"
        ),
        "mermaid_customer_intakes": _optional_table_rows(
            "mermaid_customer_intakes"
        ),
        "mermaid_reservations": _optional_table_rows("mermaid_reservations"),
        "mermaid_reservation_events": _optional_table_rows(
            "mermaid_reservation_events"
        ),
        "mermaid_demo_payments": _optional_table_rows("mermaid_demo_payments"),
        "mermaid_checkout_links": _optional_table_rows("mermaid_checkout_links"),
        "mermaid_documents": _optional_table_rows("mermaid_documents"),
        "mermaid_delivery_jobs": _optional_table_rows("mermaid_delivery_jobs"),
        "mermaid_card_deliveries": _optional_table_rows(
            "mermaid_card_deliveries"
        ),
        "mermaid_model_events": _optional_table_rows("mermaid_model_events"),
        "operator_delivery_outbox": _optional_table_rows(
            "operator_delivery_outbox"
        ),
    }
    conn.close()

    # Email JSON state (no DB table).
    email_path = _get_email_state_path()
    payload["email_threads"] = email_state_read(email_path, {})

    counts = {k: len(v) if isinstance(v, list) else 1
              for k, v in payload.items() if k not in ("tenant", "exportedAt")}

    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
    os.replace(tmp, path)

    return {
        "exportPath": path,
        "recordCounts": counts,
        "exportedAt": now_iso,
    }


def delete_customer_data(identifier_value: str, identifier_type: str,
                          action: str, keep_approved_learnings: bool) -> dict:
    """Delete or anonymize every record for one resolved customer identity.

    Mermaid keeps a channel identity as well as its own intake identifier. Old
    deployments could put those identifiers on two customer rows, so retention
    deliberately resolves the whole exact-value identity cluster before doing
    any work. Active escalations still block the operation atomically.
    """
    if action not in ("delete", "anonymize"):
        return {"ok": False, "reason": f"invalid_action:{action}"}

    conn = _get_conn()
    conn.execute(
        "CREATE TABLE IF NOT EXISTS data_retention_file_deletions ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,identifier_type TEXT NOT NULL,"
        "identifier_value TEXT NOT NULL,path TEXT NOT NULL UNIQUE,created_at TEXT NOT NULL)"
    )
    conn.commit()
    pending_files = conn.execute(
        "SELECT id,path FROM data_retention_file_deletions "
        "WHERE identifier_type=? AND identifier_value=?",
        (identifier_type, identifier_value),
    ).fetchall()
    for pending_id, pending_path in pending_files:
        try:
            os.remove(pending_path)
        except FileNotFoundError:
            pass
        except OSError:
            conn.close()
            return {
                "ok": False,
                "action": action,
                "deletedCount": 0,
                "anonymizedCount": 0,
                "reason": "document_delete_failed",
            }
        conn.execute(
            "DELETE FROM data_retention_file_deletions WHERE id=?", (pending_id,)
        )
    conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    conversation_types = {
        "phone", "wa_conversation_id", "conversation_id",
        "mermaid_conversation_id",
    }
    type_marks = ",".join("?" for _ in conversation_types)
    if identifier_type in conversation_types:
        rows = conn.execute(
            "SELECT DISTINCT customer_id FROM customer_identifiers "
            f"WHERE value=? AND type IN ({type_marks})",
            [identifier_value] + sorted(conversation_types),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT DISTINCT customer_id FROM customer_identifiers "
            "WHERE type=? AND value=?",
            (identifier_type, identifier_value),
        ).fetchall()
    customer_ids = {int(row[0]) for row in rows}
    if not customer_ids:
        conn.close()
        return {"ok": True, "action": action, "deletedCount": 0,
                "anonymizedCount": 0, "reason": "no_such_customer"}

    # Include legacy duplicate Mermaid identities reached through another exact
    # conversation identifier on the initially resolved customer row.
    customer_marks = ",".join("?" for _ in customer_ids)
    seed_identifiers = conn.execute(
        "SELECT type,value FROM customer_identifiers "
        f"WHERE customer_id IN ({customer_marks})",
        sorted(customer_ids),
    ).fetchall()
    conversation_values = {
        value for type_, value in seed_identifiers if type_ in conversation_types
    }
    if conversation_values:
        value_marks = ",".join("?" for _ in conversation_values)
        related = conn.execute(
            "SELECT DISTINCT customer_id FROM customer_identifiers "
            f"WHERE type IN ({type_marks}) AND value IN ({value_marks})",
            sorted(conversation_types) + sorted(conversation_values),
        ).fetchall()
        customer_ids.update(int(row[0]) for row in related)

    customer_ids = sorted(customer_ids)
    customer_marks = ",".join("?" for _ in customer_ids)
    ident_rows = conn.execute(
        "SELECT id,type,value FROM customer_identifiers "
        f"WHERE customer_id IN ({customer_marks})",
        customer_ids,
    ).fetchall()
    conversation_values = {
        value for _id, type_, value in ident_rows if type_ in conversation_types
    }
    emails = {value for _id, type_, value in ident_rows if type_ == "email"}
    text_keys = sorted(conversation_values | emails)

    if text_keys:
        text_marks = ",".join("?" for _ in text_keys)
        active = conn.execute(
            "SELECT 1 FROM pending_notifications "
            f"WHERE customer_id IN ({text_marks}) "
            "AND status IN ('pending','sent') LIMIT 1",
            text_keys,
        ).fetchone()
        if active:
            conn.close()
            return {"ok": False, "reason": "active_escalation"}

        # A provider call runs outside SQLite. Do not remove or anonymize its
        # durable claim while a live worker may still send to this customer.
        # Expired/released rows are safe to retire inside our write transaction;
        # provider-confirmed and prepared retries then cannot resurrect PII.
        if _has_table(conn, "operator_delivery_outbox"):
            active_delivery = conn.execute(
                "SELECT 1 FROM operator_delivery_outbox "
                f"WHERE conversation_id IN ({text_marks}) "
                "AND status != 'confirmed' AND claim_token != '' "
                "AND lease_until > ? LIMIT 1",
                text_keys + [datetime.now(timezone.utc).timestamp()],
            ).fetchone()
            if active_delivery:
                conn.close()
                return {"ok": False, "reason": "active_delivery"}
    else:
        text_marks = ""

    def _table(name: str) -> bool:
        return _has_table(conn, name)

    def _delete_where(table: str, column: str, values) -> int:
        values = list(values)
        if not values or not _table(table):
            return 0
        marks = ",".join("?" for _ in values)
        try:
            return conn.execute(
                f"DELETE FROM {table} WHERE {column} IN ({marks})", values
            ).rowcount
        except sqlite3.OperationalError as exc:
            # Legacy optional tables do not all share the customer_id column.
            if "no such column" in str(exc).casefold():
                return 0
            raise

    reservation_ids = []
    if conversation_values and _table("mermaid_reservations"):
        marks = ",".join("?" for _ in conversation_values)
        reservation_ids = [
            row[0] for row in conn.execute(
                "SELECT public_id FROM mermaid_reservations "
                f"WHERE conversation_id IN ({marks})",
                sorted(conversation_values),
            ).fetchall()
        ]
    document_ids = []
    document_paths = []
    if reservation_ids and _table("mermaid_documents"):
        marks = ",".join("?" for _ in reservation_ids)
        document_rows = conn.execute(
            "SELECT public_id,path FROM mermaid_documents "
            f"WHERE reservation_public_id IN ({marks})",
            reservation_ids,
        ).fetchall()
        document_ids = [row[0] for row in document_rows]
        document_paths = [row[1] for row in document_rows if row[1]]

    now_iso = datetime.now(timezone.utc).isoformat()
    for document_path in set(document_paths):
        conn.execute(
            "INSERT OR IGNORE INTO data_retention_file_deletions "
            "(identifier_type,identifier_value,path,created_at) VALUES (?,?,?,?)",
            (identifier_type, identifier_value, document_path, now_iso),
        )

    deleted = 0
    anonymized = 0
    skipped_learnings = 0

    if action == "delete":
        deleted += _delete_where(
            "mermaid_card_deliveries", "conversation_id", conversation_values
        )
        deleted += _delete_where(
            "mermaid_card_deliveries", "document_public_id", document_ids
        )
        deleted += _delete_where(
            "mermaid_delivery_jobs", "reservation_public_id", reservation_ids
        )
        deleted += _delete_where(
            "mermaid_documents", "reservation_public_id", reservation_ids
        )
        deleted += _delete_where(
            "mermaid_checkout_links", "reservation_public_id", reservation_ids
        )
        deleted += _delete_where(
            "mermaid_demo_payments", "reservation_public_id", reservation_ids
        )
        deleted += _delete_where(
            "mermaid_reservation_events", "reservation_public_id", reservation_ids
        )
        if text_keys:
            deleted += _delete_mermaid_crew_assistance(conn, text_keys)
        deleted += _delete_where(
            "mermaid_reservations", "public_id", reservation_ids
        )
        deleted += _delete_where(
            "mermaid_model_events", "conversation_id", conversation_values
        )
        deleted += _delete_where(
            "mermaid_customer_intakes", "conversation_id", conversation_values
        )
        deleted += _delete_where(
            "mermaid_customer_intakes", "customer_id", customer_ids
        )
        deleted += _delete_where(
            "whatsapp_booking_state", "phone", conversation_values
        )
        deleted += _delete_where("whatsapp_threads", "phone", conversation_values)
        deleted += _delete_where(
            "operator_delivery_outbox", "conversation_id", text_keys
        )
        if text_keys:
            deleted += conn.execute(
                "DELETE FROM pending_notifications "
                f"WHERE customer_id IN ({text_marks}) "
                "AND status NOT IN ('pending','sent')",
                text_keys,
            ).rowcount
            deleted += conn.execute(
                f"DELETE FROM appointments WHERE conversation_id IN ({text_marks})",
                text_keys,
            ).rowcount
            deleted += conn.execute(
                f"DELETE FROM conversation_status WHERE conversation_id IN ({text_marks})",
                text_keys,
            ).rowcount
        deleted += _delete_where(
            "customer_interactions", "customer_id", customer_ids
        )
        for table in ("bookings", "service_bookings"):
            deleted += _delete_where(table, "customer_id", customer_ids)
        if not keep_approved_learnings and text_keys:
            deleted += _delete_where(
                "escalation_learnings", "conversation_id", text_keys
            )
        elif text_keys and _table("escalation_learnings"):
            skipped_learnings = conn.execute(
                "SELECT COUNT(*) FROM escalation_learnings "
                f"WHERE conversation_id IN ({text_marks})",
                text_keys,
            ).fetchone()[0] or 0
        deleted += _delete_where(
            "customer_identifiers", "customer_id", customer_ids
        )
        deleted += _delete_where("customers", "id", customer_ids)
    else:
        REDACTED = "[redacted]"
        REDACTED_MSG = "[redacted message]"
        # Generated PDFs and their outbound payloads contain rendered customer
        # details, so anonymization removes those artifacts and their files.
        anonymized += _delete_where(
            "mermaid_card_deliveries", "conversation_id", conversation_values
        )
        anonymized += _delete_where(
            "mermaid_card_deliveries", "document_public_id", document_ids
        )
        anonymized += _delete_where(
            "mermaid_delivery_jobs", "reservation_public_id", reservation_ids
        )
        anonymized += _delete_where(
            "mermaid_documents", "reservation_public_id", reservation_ids
        )
        anonymized += _delete_where(
            "mermaid_checkout_links", "reservation_public_id", reservation_ids
        )
        anonymized += _delete_where(
            "mermaid_demo_payments", "reservation_public_id", reservation_ids
        )
        if reservation_ids and _table("mermaid_reservation_events"):
            marks = ",".join("?" for _ in reservation_ids)
            anonymized += conn.execute(
                "UPDATE mermaid_reservation_events SET actor='[redacted]',"
                "reason='[redacted]',payload_json='{}',"
                "idempotency_key='[redacted]:' || lower(hex(randomblob(16))) "
                f"WHERE reservation_public_id IN ({marks})",
                reservation_ids,
            ).rowcount
        if reservation_ids and _table("mermaid_reservations"):
            marks = ",".join("?" for _ in reservation_ids)
            anonymized_intake = json.dumps(
                {
                    "trip_date": "1970-01-01",
                    "adults": 0,
                    "children": 0,
                    "infants": 0,
                    "customer_name": "[redacted]",
                    "pickup_preference": "pier",
                    "language": "en",
                    "phase": "anonymized",
                },
                sort_keys=True,
            )
            anonymized += conn.execute(
                "UPDATE mermaid_reservations SET "
                "conversation_id='[redacted]:reservation:' || public_id,"
                "zernio_account_id='',customer_name='[redacted]',intake_json=?,"
                "summary_version=lower(hex(randomblob(32))),"
                "payment_reference=NULL,quote_public_id=NULL,receipt_public_id=NULL,"
                "updated_at=? "
                f"WHERE public_id IN ({marks})",
                [anonymized_intake, now_iso] + reservation_ids,
            ).rowcount
        if text_keys:
            anonymized += _anonymize_mermaid_crew_assistance(
                conn, text_keys, now_iso
            )
        if conversation_values and _table("mermaid_model_events"):
            marks = ",".join("?" for _ in conversation_values)
            anonymized += conn.execute(
                "UPDATE mermaid_model_events SET "
                "conversation_id='[redacted]:model:' || rowid,"
                "message_id='[redacted]:' || rowid,response_json='{}',"
                "error_kind='' "
                f"WHERE conversation_id IN ({marks})",
                sorted(conversation_values),
            ).rowcount
        if _table("mermaid_customer_intakes"):
            anonymized += conn.execute(
                "UPDATE mermaid_customer_intakes SET "
                "conversation_id='[redacted]:intake:' || id,intake_json='{}' "
                f"WHERE customer_id IN ({customer_marks})",
                customer_ids,
            ).rowcount
        if conversation_values:
            marks = ",".join("?" for _ in conversation_values)
            anonymized += conn.execute(
                "UPDATE whatsapp_booking_state SET fields_json='{}',flags_json='{}',"
                "completed_bookings_json='[]',"
                "phone='[redacted]:state:' || rowid "
                f"WHERE phone IN ({marks})",
                sorted(conversation_values),
            ).rowcount
            anonymized += conn.execute(
                "UPDATE whatsapp_threads SET text=?,sender_name=?,"
                "phone='[redacted]:thread:' || id,source_message_key='' "
                f"WHERE phone IN ({marks})",
                [REDACTED_MSG, REDACTED] + sorted(conversation_values),
            ).rowcount
        if text_keys and _table("operator_delivery_outbox"):
            # Retain only non-identifying delivery metrics. Both action hashes
            # are replaced because request ids and low-entropy messages can be
            # guessed; a stale retry must not reconnect to this tombstone.
            anonymized += conn.execute(
                "UPDATE operator_delivery_outbox SET "
                "action_key=lower(hex(randomblob(32))),"
                "conversation_id='[redacted]:delivery:' || lower(hex(randomblob(16))),"
                "scope='[redacted]',request_hash=lower(hex(randomblob(32))),"
                "anchor='',payload_json='{}',result_json='{}',status='confirmed',"
                "claim_token='',lease_until=0,last_error='' "
                f"WHERE conversation_id IN ({text_marks})",
                text_keys,
            ).rowcount
        if text_keys:
            anonymized += conn.execute(
                "UPDATE pending_notifications SET relay_token=NULL,"
                "customer_id='[redacted]:notification:' || id,"
                "customer_name=?,subject=?,body=?,escalation_summary=NULL,"
                "email_thread_key='',email_reply_subject='' "
                f"WHERE customer_id IN ({text_marks})",
                [REDACTED, REDACTED, REDACTED] + text_keys,
            ).rowcount
            anonymized += conn.execute(
                "UPDATE appointments SET "
                "conversation_id='[redacted]:appointment:' || id,"
                "customer_name=?,title=?,date_time_label='',"
                "proposed_times_json='[]',location='' "
                f"WHERE conversation_id IN ({text_marks})",
                [REDACTED, REDACTED] + text_keys,
            ).rowcount
            anonymized += conn.execute(
                "UPDATE conversation_status SET "
                "conversation_id='[redacted]:status:' || rowid "
                f"WHERE conversation_id IN ({text_marks})",
                text_keys,
            ).rowcount
        anonymized += conn.execute(
            "UPDATE customers SET display_name=?,summary='',notes='' "
            f"WHERE id IN ({customer_marks})",
            [REDACTED] + customer_ids,
        ).rowcount
        anonymized += conn.execute(
            "UPDATE customer_identifiers SET "
            "value='[redacted]:' || id "
            f"WHERE customer_id IN ({customer_marks})",
            customer_ids,
        ).rowcount
        anonymized += conn.execute(
            "UPDATE customer_interactions SET summary=? "
            f"WHERE customer_id IN ({customer_marks})",
            [REDACTED] + customer_ids,
        ).rowcount
        if not keep_approved_learnings and text_keys and _table("escalation_learnings"):
            anonymized += conn.execute(
                "UPDATE escalation_learnings SET human_answer=? "
                f"WHERE conversation_id IN ({text_marks})",
                [REDACTED] + text_keys,
            ).rowcount
        elif text_keys and _table("escalation_learnings"):
            skipped_learnings = conn.execute(
                "SELECT COUNT(*) FROM escalation_learnings "
                f"WHERE conversation_id IN ({text_marks})",
                text_keys,
            ).fetchone()[0] or 0

    # Email conversations live in a JSON sidecar rather than SQLite.  Stage
    # that privacy change before committing the database transaction so an
    # unwritable sidecar cannot leave the request half-applied and impossible
    # to retry with the original customer identifier.
    email_path = _get_email_state_path()
    if emails:
        with email_state_file_lock(email_path):
            if (
                os.path.exists(email_path)
                or os.path.exists(_legacy_email_archive_path(email_path))
            ):
                try:
                    email_state = _read_email_state_unlocked(
                        email_path, {}, strict=True
                    )
                    if not isinstance(email_state, dict):
                        raise ValueError("Email conversation state must be an object")
                except (OSError, ValueError, TypeError):
                    conn.rollback()
                    conn.close()
                    return {
                        "ok": False,
                        "action": action,
                        "deletedCount": 0,
                        "anonymizedCount": 0,
                        "reason": "email_state_read_failed",
                    }

                email_needles = {str(value).strip().casefold() for value in emails}
                email_threads = email_state.get("threads")
                if not isinstance(email_threads, dict):
                    email_threads = {}
                matched_thread_keys = []
                for thread_key, thread in email_threads.items():
                    thread_data = thread if isinstance(thread, dict) else {}
                    from_email = str(thread_data.get("from_email") or "").strip().casefold()
                    if (
                        from_email in email_needles
                        or _email_thread_address(thread_key) in email_needles
                    ):
                        matched_thread_keys.append(thread_key)

                if action == "delete":
                    for thread_key in matched_thread_keys:
                        del email_threads[thread_key]
                        deleted += 1
                else:
                    redacted_threads = dict(email_threads)
                    for thread_key in matched_thread_keys:
                        thread = email_threads.get(thread_key)
                        thread_data = thread if isinstance(thread, dict) else {}
                        clean_messages = []
                        raw_messages = thread_data.get("messages")
                        if not isinstance(raw_messages, list):
                            raw_messages = []
                        for message in raw_messages:
                            message_data = message if isinstance(message, dict) else {}
                            clean_message = {
                                key: message_data[key]
                                for key in ("role", "ts", "timestamp", "created_at")
                                if key in message_data
                            }
                            for key in ("text", "body", "from_email", "subject"):
                                if key in message_data:
                                    clean_message[key] = "[redacted]"
                            clean_messages.append(clean_message)
                        redacted_key = (
                            "[redacted]:email-thread:" + os.urandom(12).hex()
                        )
                        redacted_threads[redacted_key] = {
                            "fields": {},
                            "flags": {},
                            "completed_bookings": [],
                            "from_email": "[redacted]",
                            "subject": "[redacted]",
                            "messages": clean_messages,
                            "last_activity": thread_data.get("last_activity"),
                        }
                        del redacted_threads[thread_key]
                        anonymized += 1
                    email_threads = redacted_threads

                email_state["threads"] = email_threads
                sender_rates = email_state.get("sender_rates")
                if isinstance(sender_rates, dict):
                    email_state["sender_rates"] = {
                        key: value
                        for key, value in sender_rates.items()
                        if str(key).strip().casefold() not in email_needles
                    }

                try:
                    _write_email_state_unlocked(email_path, email_state)
                except OSError:
                    conn.rollback()
                    conn.close()
                    return {
                        "ok": False,
                        "action": action,
                        "deletedCount": 0,
                        "anonymizedCount": 0,
                        "reason": "email_state_write_failed",
                    }

    conn.commit()
    conn.close()

    file_cleanup_failed = False
    if document_paths:
        cleanup_conn = _get_conn()
        for document_path in set(document_paths):
            try:
                os.remove(document_path)
            except FileNotFoundError:
                pass
            except OSError:
                file_cleanup_failed = True
                continue
            cleanup_conn.execute(
                "DELETE FROM data_retention_file_deletions WHERE path=?",
                (document_path,),
            )
        cleanup_conn.commit()
        cleanup_conn.close()
        if file_cleanup_failed:
            return {
                "ok": False,
                "action": action,
                "deletedCount": deleted,
                "anonymizedCount": anonymized,
                "reason": "document_delete_failed",
            }

    return {
        "ok": True,
        "action": action,
        "deletedCount": deleted,
        "anonymizedCount": anonymized,
        "skippedLearnings": skipped_learnings,
    }


def knowledge_file_create(filename: str, stored_filename: str, mime_type: str,
                           size_bytes: int, status: str, extracted_text: str,
                           failure_reason: str = "") -> int:
    """Brief 230: insert a knowledge_files row at upload time. Returns id."""
    now = datetime.now(timezone.utc).isoformat()
    conn = _get_conn()
    cur = conn.execute(
        "INSERT INTO knowledge_files "
        "(filename, stored_filename, mime_type, size_bytes, status, "
        "extracted_text, failure_reason, uploaded_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (filename, stored_filename, mime_type, size_bytes, status,
         extracted_text, failure_reason, now))
    row_id = cur.lastrowid
    conn.commit()
    conn.close()
    return row_id


def source_of_truth_get() -> list:
    """Brief 262: return tenant SOT blocks from the source_of_truth
    single-row blob. Returns [] when no row exists or row contains
    invalid JSON (defensive - never crashes the API)."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT blocks_json FROM source_of_truth WHERE id = 1"
    ).fetchone()
    conn.close()
    if not row:
        return []
    try:
        parsed = json.loads(row[0] or "[]")
    except (ValueError, TypeError):
        return []
    return parsed if isinstance(parsed, list) else []


def source_of_truth_set(blocks: list) -> list:
    """Brief 262: UPSERT the tenant SOT blob (single row id=1). Caller
    is responsible for validation; this helper only persists. Returns
    the parsed-back list to prove the round-trip is clean."""
    if not isinstance(blocks, list):
        blocks = []
    now = datetime.now(timezone.utc).isoformat()
    blob = json.dumps(blocks, ensure_ascii=False)
    conn = _get_conn()
    conn.execute(
        "INSERT INTO source_of_truth (id, blocks_json, updated_at) "
        "VALUES (1, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET "
        "blocks_json = excluded.blocks_json, "
        "updated_at = excluded.updated_at",
        (blob, now))
    conn.commit()
    conn.close()
    return source_of_truth_get()


def knowledge_files_list() -> list:
    """Brief 230: return all knowledge files in SR's frontend shape
    (camelCase, ISO timestamps). extracted_text + failure_reason are NOT
    surfaced — operator UI doesn't need to render them."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT id, filename, mime_type, size_bytes, status, uploaded_at, "
        "last_used_at FROM knowledge_files ORDER BY uploaded_at DESC"
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        out.append({
            "id": str(r[0]),
            "filename": r[1],
            "mimeType": r[2] or "",
            "sizeBytes": r[3],
            "status": r[4],
            "uploadedAt": r[5],
            "lastUsedAt": r[6],
        })
    return out


def knowledge_file_delete(file_id: int) -> Optional[str]:
    """Brief 230: hard-delete a knowledge_files row. Returns the
    stored_filename so the caller can also unlink the file from disk
    (registry stays disk-agnostic). Returns None if the id doesn't exist."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT stored_filename FROM knowledge_files WHERE id = ?",
        (file_id,)).fetchone()
    if not row:
        conn.close()
        return None
    stored = row[0]
    conn.execute("DELETE FROM knowledge_files WHERE id = ?", (file_id,))
    conn.commit()
    conn.close()
    return stored


def get_knowledge_files_for_prompt(limit: int = 5) -> list:
    """Brief 230: return up to `limit` ready knowledge files with their
    extracted text, newest first. Used by Marina's _build_knowledge_files_block."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT filename, extracted_text FROM knowledge_files "
        "WHERE status = 'ready' AND extracted_text != '' "
        "ORDER BY uploaded_at DESC LIMIT ?",
        (limit,)).fetchall()
    conn.close()
    return [{"filename": r[0], "text": r[1]} for r in rows]


_FOLLOW_UP_FIELDS = {
    "first_name", "surnames", "phone_raw", "phone_normalized",
    "callback_preference", "visit_reason", "handoff_reason",
    "source_message_id",
}


def upsert_follow_up_request(conversation_id: str, channel: str = "whatsapp",
                             **fields) -> dict:
    """Create or enrich one callback follow-up for a conversation.

    Empty values never erase data already collected. This makes repeated
    inbound deliveries and partial extraction safe to retry.
    """
    clean = {key: str(value).strip() for key, value in fields.items()
             if key in _FOLLOW_UP_FIELDS and value is not None and str(value).strip()}
    now = datetime.now(timezone.utc).isoformat()
    conn = _get_conn()
    existing = conn.execute(
        "SELECT id FROM follow_up_requests WHERE conversation_id = ?",
        (conversation_id,)).fetchone()
    if existing is None:
        columns = ["conversation_id", "channel", "created_at", "updated_at"] + list(clean)
        values = [conversation_id, channel, now, now] + list(clean.values())
        placeholders = ", ".join("?" for _ in columns)
        conn.execute(
            f"INSERT INTO follow_up_requests ({', '.join(columns)}) VALUES ({placeholders})",
            values,
        )
    elif clean:
        assignments = ", ".join(f"{key} = ?" for key in clean)
        conn.execute(
            f"UPDATE follow_up_requests SET {assignments}, updated_at = ? "
            "WHERE conversation_id = ?",
            [*clean.values(), now, conversation_id],
        )
    conn.commit()
    row = conn.execute(
        "SELECT id, conversation_id, channel, first_name, surnames, phone_raw, "
        "phone_normalized, callback_preference, visit_reason, status, "
        "handoff_reason, source_message_id, created_at, updated_at, closed_at "
        "FROM follow_up_requests WHERE conversation_id = ?", (conversation_id,)
    ).fetchone()
    last_inbound_at = _latest_follow_up_inbound_at(conn, conversation_id)
    conn.close()
    return _follow_up_row(row, last_inbound_at=last_inbound_at)


def _follow_up_context(fields: dict = None) -> dict:
    """Return extra prospect fields already captured in conversation state."""
    fields = fields if isinstance(fields, dict) else {}
    session_type = fields.get("session_type") or fields.get("service_name") or ""
    appointment_preference = fields.get("appointment_preference") or ""
    if not appointment_preference:
        appointment_preference = " ".join(
            str(fields.get(key) or "").strip() for key in ("date", "slot_time")
        ).strip()
    return {
        "session_type": str(session_type).strip(),
        "preferred_clinic": str(fields.get("preferred_clinic") or "").strip(),
        "appointment_preference": str(appointment_preference).strip(),
    }


def _latest_follow_up_inbound_at(conn, conversation_id: str) -> str:
    """Return the latest prospect message time, excluding AI and staff replies."""
    if not conversation_id:
        return ""
    latest = conn.execute(
        "SELECT MAX(created_at) FROM whatsapp_threads "
        "WHERE phone = ? AND role = 'user'",
        (conversation_id,),
    ).fetchone()
    return str(latest[0] or "") if latest else ""


def _follow_up_row(row, thread_fields: dict = None,
                   last_inbound_at: str = "") -> dict:
    keys = ("id", "conversation_id", "channel", "first_name", "surnames",
            "phone_raw", "phone_normalized", "callback_preference", "visit_reason",
            "status", "handoff_reason", "source_message_id", "created_at",
            "updated_at", "closed_at")
    if not row:
        return None
    result = dict(zip(keys, row))
    result.update(_follow_up_context(thread_fields))
    result["last_inbound_at"] = last_inbound_at
    return result


def list_follow_up_requests(status: str = None, limit: int = 200) -> list:
    conn = _get_conn()
    query = ("SELECT id, conversation_id, channel, first_name, surnames, phone_raw, "
             "phone_normalized, callback_preference, visit_reason, status, "
             "handoff_reason, source_message_id, created_at, updated_at, closed_at "
             "FROM follow_up_requests")
    params = []
    if status:
        query += " WHERE status = ?"
        params.append(status)
    rows = conn.execute(query + " ORDER BY updated_at DESC LIMIT ?", [*params, limit]).fetchall()
    fields_by_conversation = {}
    inbound_at_by_conversation = {}
    conversation_ids = [row[1] for row in rows if row[1]]
    if conversation_ids:
        placeholders = ",".join("?" for _ in conversation_ids)
        state_rows = conn.execute(
            f"SELECT phone, fields_json FROM whatsapp_booking_state "
            f"WHERE phone IN ({placeholders})",
            conversation_ids,
        ).fetchall()
        for conversation_id, fields_json in state_rows:
            try:
                fields_by_conversation[conversation_id] = json.loads(fields_json or "{}")
            except (TypeError, ValueError):
                fields_by_conversation[conversation_id] = {}
        inbound_rows = conn.execute(
            f"SELECT phone, MAX(created_at) FROM whatsapp_threads "
            f"WHERE role = 'user' AND phone IN ({placeholders}) GROUP BY phone",
            conversation_ids,
        ).fetchall()
        inbound_at_by_conversation = {
            conversation_id: str(created_at or "")
            for conversation_id, created_at in inbound_rows
        }
    conn.close()
    return [
        _follow_up_row(
            row,
            fields_by_conversation.get(row[1], {}),
            inbound_at_by_conversation.get(row[1], ""),
        )
        for row in rows
    ]


def get_follow_up_request(request_id: int) -> dict:
    conn = _get_conn()
    row = conn.execute(
        "SELECT id, conversation_id, channel, first_name, surnames, phone_raw, "
        "phone_normalized, callback_preference, visit_reason, status, "
        "handoff_reason, source_message_id, created_at, updated_at, closed_at "
        "FROM follow_up_requests WHERE id = ?", (request_id,)
    ).fetchone()
    thread_fields = {}
    if row and row[1]:
        state_row = conn.execute(
            "SELECT fields_json FROM whatsapp_booking_state WHERE phone = ?",
            (row[1],),
        ).fetchone()
        if state_row:
            try:
                thread_fields = json.loads(state_row[0] or "{}")
            except (TypeError, ValueError):
                pass
    last_inbound_at = _latest_follow_up_inbound_at(conn, row[1] if row else "")
    conn.close()
    return _follow_up_row(row, thread_fields, last_inbound_at)


def get_follow_up_request_by_conversation(conversation_id: str) -> dict:
    """Return one callback follow-up by its provider conversation id."""
    if not conversation_id:
        return None
    conn = _get_conn()
    row = conn.execute(
        "SELECT id, conversation_id, channel, first_name, surnames, phone_raw, "
        "phone_normalized, callback_preference, visit_reason, status, "
        "handoff_reason, source_message_id, created_at, updated_at, closed_at "
        "FROM follow_up_requests WHERE conversation_id = ?",
        (conversation_id,),
    ).fetchone()
    last_inbound_at = _latest_follow_up_inbound_at(conn, conversation_id)
    conn.close()
    return _follow_up_row(row, last_inbound_at=last_inbound_at)


def update_follow_up_status(request_id: int, status: str) -> dict:
    allowed = {"collecting", "ready_to_call", "needs_human_answer", "in_progress",
               "copied", "appointment_coordinated", "no_answer", "closed"}
    if status not in allowed:
        raise ValueError("Invalid follow-up status")
    now = datetime.now(timezone.utc).isoformat()
    closed_at = now if status == "closed" else None
    conn = _get_conn()
    conn.execute(
        "UPDATE follow_up_requests SET status = ?, updated_at = ?, "
        "closed_at = CASE WHEN ? IS NOT NULL THEN ? ELSE closed_at END WHERE id = ?",
        (status, now, closed_at, closed_at, request_id),
    )
    conn.commit()
    conn.close()
    return get_follow_up_request(request_id)


def get_pending_notifications(status: str = "pending") -> list:
    """Return all notifications with the given status.
    Brief 183: enriched with customer_contact, customer_email, customer_phone."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT id, notification_type, relay_token, channel, customer_id, "
        "customer_name, subject, body, status, created_at "
        "FROM pending_notifications WHERE status = ? ORDER BY created_at ASC",
        (status,)
    ).fetchall()
    conn.close()
    result = []
    for r in rows:
        ct = _infer_contact_type(r[4] or "")
        contact = _lookup_customer_contact(r[4] or "", ct)
        customer_contact = contact["email"] or contact["phone"] or r[4] or ""
        result.append({
            "id": r[0], "notification_type": r[1], "relay_token": r[2],
            "channel": r[3], "customer_id": r[4], "customer_name": r[5],
            "subject": r[6], "body": r[7], "status": r[8], "created_at": r[9],
            "contact_type": ct,
            "customer_contact": customer_contact,
            "customer_email": contact["email"],
            "customer_phone": contact["phone"],
        })
    return result


def delete_escalation(
    escalation_id: int,
    *,
    expected_content_revision: int | None = None,
) -> bool:
    """Brief 172: hard-delete a pending_notifications row. Returns True if a
    row was deleted. Used by the dashboard Escalations page trash button (SR's
    UX — archive first, then from archive view you can delete permanently).

    Brief 254: BEFORE the DELETE, clear orphan escalation state via
    resolve_conversation_from_escalation so:
      - conversation_status.status flips to 'resolved' (drives email detail's
        escalated=false), and
      - whatsapp_booking_state.flags_json.fully_escalated cleared (drives WA),
      - email_thread_state.json.flags.fully_escalated cleared (drives email list).
    Without this cleanup the dashboard shows escalated=true forever with
    no matching /escalations row -- issue #23 root cause."""
    conn = _get_conn()
    customer_id = ""
    esc_channel = "whatsapp"
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT customer_id, channel, content_revision "
            "FROM pending_notifications WHERE id = ?",
            (escalation_id,),
        ).fetchone()
        if row is None:
            conn.rollback()
            return False
        customer_id, esc_channel, current_revision = row
        _require_escalation_revision(
            current_revision, expected_content_revision
        )
        # Brief 254 cleanup is part of the same write transaction as deletion;
        # otherwise a newer reused revision could appear between validation and
        # the destructive write.
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO conversation_status "
            "(conversation_id, channel, status, updated_at, ai_muted, human_takeover_at) "
            "VALUES (?, ?, 'resolved', ?, 0, NULL) "
            "ON CONFLICT(conversation_id) DO UPDATE SET status = 'resolved', "
            "ai_muted = 0, human_takeover_at = NULL, updated_at = excluded.updated_at",
            (customer_id, esc_channel or "whatsapp", now),
        )
        conn.execute(
            "UPDATE whatsapp_booking_state SET flags_json = "
            "json_set(COALESCE(flags_json, '{}'), '$.fully_escalated', json('false')) "
            "WHERE phone = ?",
            (customer_id,),
        )
        changed = conn.execute(
            "DELETE FROM pending_notifications WHERE id = ?", (escalation_id,)
        ).rowcount > 0
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    if esc_channel == "email" and customer_id:
        email_clear_fully_escalated_flag(
            customer_id, require_no_active_review=True
        )
    return changed


def delete_mermaid_escalation(
    escalation_id: int,
    *,
    expected_content_revision: int | None = None,
) -> bool:
    """Delete one Mermaid review and derive freezes from every row left."""
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()
    customer_id = ""
    channel = "whatsapp"
    has_active_review = False
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT customer_id,channel,content_revision FROM pending_notifications "
            "WHERE id=? AND notification_type IN ('escalation','relay')",
            (escalation_id,),
        ).fetchone()
        if row is None:
            conn.rollback()
            return False
        customer_id, channel = str(row[0]), str(row[1] or "whatsapp")
        _require_escalation_revision(row[2], expected_content_revision)
        changed = conn.execute(
            "DELETE FROM pending_notifications WHERE id=?", (escalation_id,)
        ).rowcount > 0
        has_active_review, _has_hard_review = _sync_mermaid_escalation_freezes(
            conn, customer_id, channel, now
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    if channel == "email" and customer_id and not has_active_review:
        email_clear_fully_escalated_flag(
            customer_id, require_no_active_review=True
        )
    return changed


def update_notification_status(
    notification_id: int,
    status: str,
    *,
    expected_content_revision: int | None = None,
) -> bool:
    """Update the status of a pending notification. Returns True if row updated."""
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT status, content_revision FROM pending_notifications WHERE id = ?",
            (notification_id,),
        ).fetchone()
        if row is None:
            conn.rollback()
            return False
        current_status, current_revision = row
        _require_escalation_revision(
            current_revision, expected_content_revision
        )
        revision_delta = int(
            expected_content_revision is not None and current_status != status
        )
        conn.execute(
            "UPDATE pending_notifications SET status = ?, "
            "content_revision = content_revision + ? WHERE id = ?",
            (status, revision_delta, notification_id),
        )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_relay_by_token(relay_token: str) -> "dict | None":
    """Look up an actionable relay notification by token. Returns dict or None.
    Matches 'pending' (not yet emailed) and 'sent' (emailed, awaiting reply).
    Excludes 'replied' to prevent double-fire."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT id, notification_type, relay_token, channel, customer_id, "
        "customer_name, subject, body, status, created_at "
        "FROM pending_notifications WHERE relay_token = ? AND status IN ('pending', 'sent')",
        (relay_token,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    return {"id": row[0], "notification_type": row[1], "relay_token": row[2],
            "channel": row[3], "customer_id": row[4], "customer_name": row[5],
            "subject": row[6], "body": row[7], "status": row[8], "created_at": row[9]}


def save_content_draft(content_class: str, instagram_caption: str,
                       facebook_caption: str, hashtags: list,
                       visual_suggestion: str, reasoning: str,
                       twitter_caption: str = "") -> int:
    """Save a content draft. Returns row id."""
    conn = _get_conn()
    cur = conn.execute(
        "INSERT INTO content_drafts "
        "(content_class, instagram_caption, facebook_caption, twitter_caption, "
        "hashtags_json, visual_suggestion, reasoning, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
        (content_class, instagram_caption, facebook_caption, twitter_caption,
         json.dumps(hashtags, ensure_ascii=False), visual_suggestion, reasoning,
         datetime.now(timezone.utc).isoformat())
    )
    draft_id = cur.lastrowid
    conn.commit()
    conn.close()
    return draft_id


def get_content_drafts(status: str = None, limit: int = 50) -> list:
    """Get content drafts, optionally filtered by status. Newest first."""
    conn = _get_conn()
    if status:
        rows = conn.execute(
            "SELECT id, content_class, instagram_caption, facebook_caption, twitter_caption, "
            "hashtags_json, visual_suggestion, reasoning, status, rejection_reason, "
            "created_at, approved_at, published_at, image_path, late_post_id, instagram_url, photo_id, "
            "platforms_json, facebook_url, late_facebook_post_id, scheduled_at "
            "FROM content_drafts WHERE status = ? ORDER BY created_at DESC LIMIT ?",
            (status, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, content_class, instagram_caption, facebook_caption, twitter_caption, "
            "hashtags_json, visual_suggestion, reasoning, status, rejection_reason, "
            "created_at, approved_at, published_at, image_path, late_post_id, instagram_url, photo_id, "
            "platforms_json, facebook_url, late_facebook_post_id, scheduled_at "
            "FROM content_drafts ORDER BY created_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
    conn.close()
    return [
        {
            "id": r[0], "content_class": r[1], "instagram_caption": r[2],
            "facebook_caption": r[3], "twitter_caption": r[4] or "",
            "hashtags": json.loads(r[5] or "[]"),
            "visual_suggestion": r[6], "reasoning": r[7], "status": r[8],
            "rejection_reason": r[9], "created_at": r[10], "approved_at": r[11],
            "published_at": r[12], "image_path": r[13],
            "late_post_id": r[14], "instagram_url": r[15],
            "photo_id": r[16] if len(r) > 16 else 0,
            "platforms": json.loads(r[17]) if len(r) > 17 and r[17] else ["instagram"],
            "facebook_url": r[18] if len(r) > 18 else "",
            "late_facebook_post_id": r[19] if len(r) > 19 else "",
            "scheduled_at": r[20] if len(r) > 20 else None,
        }
        for r in rows
    ]


def update_draft_status(draft_id: int, status: str,
                        rejection_reason: str = "") -> bool:
    """Update draft status. For 'approved', sets approved_at. For 'published', sets published_at.
    For 'rejected', stores rejection_reason. Returns True if row updated."""
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()
    if status == "approved":
        cur = conn.execute(
            "UPDATE content_drafts SET status = ?, approved_at = ? WHERE id = ?",
            (status, now, draft_id)
        )
    elif status == "published":
        cur = conn.execute(
            "UPDATE content_drafts SET status = ?, published_at = ? WHERE id = ?",
            (status, now, draft_id)
        )
    elif status == "rejected":
        cur = conn.execute(
            "UPDATE content_drafts SET status = ?, rejection_reason = ? WHERE id = ?",
            (status, rejection_reason, draft_id)
        )
    else:
        cur = conn.execute(
            "UPDATE content_drafts SET status = ? WHERE id = ?",
            (status, draft_id)
        )
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    return changed


def update_draft_content(draft_id: int, instagram_caption: str = None,
                         facebook_caption: str = None, hashtags: list = None,
                         twitter_caption: str = None) -> bool:
    """Update draft content fields. Only works on pending drafts.
    Only updates non-None params. Returns True if row updated."""
    sets = []
    params = []
    if instagram_caption is not None:
        sets.append("instagram_caption = ?")
        params.append(instagram_caption)
    if facebook_caption is not None:
        sets.append("facebook_caption = ?")
        params.append(facebook_caption)
    if twitter_caption is not None:
        sets.append("twitter_caption = ?")
        params.append(twitter_caption)
    if hashtags is not None:
        sets.append("hashtags_json = ?")
        params.append(json.dumps(hashtags, ensure_ascii=False))
    if not sets:
        return False
    params.append(draft_id)
    conn = _get_conn()
    cur = conn.execute(
        f"UPDATE content_drafts SET {', '.join(sets)} WHERE id = ? AND status = 'pending'",
        tuple(params)
    )
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    return changed


# --- Photo Library ---


def save_photo(filename: str, original_filename: str, tags: list,
               service_key: str = "", source: str = "upload",
               source_id: str = "", width: int = 0, height: int = 0,
               file_size: int = 0) -> int:
    """Save a photo record. Returns row id."""
    conn = _get_conn()
    cur = conn.execute(
        "INSERT INTO photo_library "
        "(filename, original_filename, tags_json, service_key, source, source_id, "
        "width, height, file_size, uploaded_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (filename, original_filename, json.dumps(tags, ensure_ascii=False),
         service_key, source, source_id, width, height, file_size,
         datetime.now(timezone.utc).isoformat())
    )
    photo_id = cur.lastrowid
    conn.commit()
    conn.close()
    return photo_id


def get_photos(service_key: str = None, limit: int = 50) -> list:
    """Get photos, optionally filtered by service_key. Newest first."""
    conn = _get_conn()
    if service_key:
        rows = conn.execute(
            "SELECT id, filename, original_filename, tags_json, service_key, "
            "source, source_id, width, height, file_size, used_count, uploaded_at "
            "FROM photo_library WHERE service_key = ? ORDER BY uploaded_at DESC LIMIT ?",
            (service_key, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, filename, original_filename, tags_json, service_key, "
            "source, source_id, width, height, file_size, used_count, uploaded_at "
            "FROM photo_library ORDER BY uploaded_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
    conn.close()
    return [
        {
            "id": r[0], "filename": r[1], "original_filename": r[2],
            "tags": json.loads(r[3] or "[]"), "service_key": r[4],
            "source": r[5], "source_id": r[6], "width": r[7],
            "height": r[8], "file_size": r[9], "used_count": r[10],
            "uploaded_at": r[11],
        }
        for r in rows
    ]


def get_photo_by_id(photo_id: int) -> dict | None:
    """Get a single photo by ID."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT id, filename, original_filename, tags_json, service_key, "
        "source, source_id, width, height, file_size, used_count, uploaded_at "
        "FROM photo_library WHERE id = ?",
        (photo_id,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    return {
        "id": row[0], "filename": row[1], "original_filename": row[2],
        "tags": json.loads(row[3] or "[]"), "service_key": row[4],
        "source": row[5], "source_id": row[6], "width": row[7],
        "height": row[8], "file_size": row[9], "used_count": row[10],
        "uploaded_at": row[11],
    }


def get_photo_by_filename(filename: str) -> dict | None:
    """Get a single photo by stored filename."""
    if not filename or "/" in filename or "\\" in filename:
        return None
    conn = _get_conn()
    row = conn.execute(
        "SELECT id, filename, original_filename, tags_json, service_key, "
        "source, source_id, width, height, file_size, used_count, uploaded_at "
        "FROM photo_library WHERE filename = ?",
        (filename,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    return {
        "id": row[0], "filename": row[1], "original_filename": row[2],
        "tags": json.loads(row[3] or "[]"), "service_key": row[4],
        "source": row[5], "source_id": row[6], "width": row[7],
        "height": row[8], "file_size": row[9], "used_count": row[10],
        "uploaded_at": row[11],
    }


def get_photo_by_source_id(source_id: str) -> dict | None:
    """Get a photo by external source ID (e.g. Google Drive file ID)."""
    if not source_id:
        return None
    conn = _get_conn()
    row = conn.execute(
        "SELECT id, filename, original_filename, tags_json, service_key, "
        "source, source_id, width, height, file_size, used_count, uploaded_at "
        "FROM photo_library WHERE source_id = ?",
        (source_id,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    return {
        "id": row[0], "filename": row[1], "original_filename": row[2],
        "tags": json.loads(row[3] or "[]"), "service_key": row[4],
        "source": row[5], "source_id": row[6], "width": row[7],
        "height": row[8], "file_size": row[9], "used_count": row[10],
        "uploaded_at": row[11],
    }


def update_photo(photo_id: int, tags: list = None, service_key: str = None) -> bool:
    """Update photo tags and/or service_key. Returns True if row updated."""
    sets = []
    params = []
    if tags is not None:
        sets.append("tags_json = ?")
        params.append(json.dumps(tags, ensure_ascii=False))
    if service_key is not None:
        sets.append("service_key = ?")
        params.append(service_key)
    if not sets:
        return False
    params.append(photo_id)
    conn = _get_conn()
    cur = conn.execute(
        f"UPDATE photo_library SET {', '.join(sets)} WHERE id = ?",
        tuple(params)
    )
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    return changed


def update_photo_filename(photo_id: int, filename: str) -> bool:
    """Update photo filename (used after processing upload). Returns True if row updated."""
    conn = _get_conn()
    cur = conn.execute(
        "UPDATE photo_library SET filename = ? WHERE id = ?",
        (filename, photo_id)
    )
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    return changed


def delete_photo(photo_id: int) -> str | None:
    """Delete a photo record. Returns filename (caller deletes file) or None if not found."""
    conn = _get_conn()
    row = conn.execute("SELECT filename FROM photo_library WHERE id = ?", (photo_id,)).fetchone()
    if not row:
        conn.close()
        return None
    conn.execute("DELETE FROM photo_library WHERE id = ?", (photo_id,))
    conn.commit()
    conn.close()
    return row[0]


def get_photo_stats() -> dict:
    """Get photo count total and grouped by service_key."""
    conn = _get_conn()
    total = conn.execute("SELECT COUNT(*) FROM photo_library").fetchone()[0]
    rows = conn.execute(
        "SELECT COALESCE(NULLIF(service_key, ''), 'untagged'), COUNT(*) "
        "FROM photo_library GROUP BY COALESCE(NULLIF(service_key, ''), 'untagged')"
    ).fetchall()
    conn.close()
    return {"total": total, "by_trip": {r[0]: r[1] for r in rows}}


# --- Training Examples ---


def save_training_example(caption_text: str, image_path: str = "",
                          platform: str = "") -> int:
    """Save a training example. Returns row id."""
    conn = _get_conn()
    cur = conn.execute(
        "INSERT INTO training_examples (caption_text, image_path, platform, created_at) "
        "VALUES (?, ?, ?, ?)",
        (caption_text, image_path, platform,
         datetime.now(timezone.utc).isoformat())
    )
    example_id = cur.lastrowid
    conn.commit()
    conn.close()
    return example_id


def get_training_examples() -> list:
    """Get all training examples."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT id, caption_text, image_path, platform, created_at "
        "FROM training_examples ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    return [
        {"id": r[0], "caption_text": r[1], "image_path": r[2],
         "platform": r[3], "created_at": r[4]}
        for r in rows
    ]


def delete_training_example(example_id: int) -> str:
    """Delete a training example. Returns image_path (caller deletes file) or empty string."""
    conn = _get_conn()
    row = conn.execute("SELECT image_path FROM training_examples WHERE id = ?",
                       (example_id,)).fetchone()
    if not row:
        conn.close()
        return ""
    conn.execute("DELETE FROM training_examples WHERE id = ?", (example_id,))
    conn.commit()
    conn.close()
    return row[0] or ""


# --- Brand Profile ---


def save_brand_rule(category: str, rule: str, source: str = "manual") -> int:
    """Save a brand profile rule. Returns row id."""
    conn = _get_conn()
    cur = conn.execute(
        "INSERT INTO brand_profile (category, rule, source, created_at) "
        "VALUES (?, ?, ?, ?)",
        (category, rule, source, datetime.now(timezone.utc).isoformat())
    )
    rule_id = cur.lastrowid
    conn.commit()
    conn.close()
    return rule_id


def get_brand_rules(category: str = None) -> list:
    """Get active brand profile rules, optionally filtered by category."""
    conn = _get_conn()
    if category:
        rows = conn.execute(
            "SELECT id, category, rule, source, created_at "
            "FROM brand_profile WHERE active = 1 AND category = ? ORDER BY created_at",
            (category,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, category, rule, source, created_at "
            "FROM brand_profile WHERE active = 1 ORDER BY category, created_at"
        ).fetchall()
    conn.close()
    return [
        {"id": r[0], "category": r[1], "rule": r[2], "source": r[3], "created_at": r[4]}
        for r in rows
    ]


def update_brand_rule(rule_id: int, rule: str = None, category: str = None) -> bool:
    """Update a brand rule's text or category."""
    sets = []
    params = []
    if rule is not None:
        sets.append("rule = ?")
        params.append(rule)
    if category is not None:
        sets.append("category = ?")
        params.append(category)
    if not sets:
        return False
    params.append(rule_id)
    conn = _get_conn()
    cur = conn.execute(
        f"UPDATE brand_profile SET {', '.join(sets)} WHERE id = ? AND active = 1",
        tuple(params)
    )
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    return changed


def delete_brand_rule(rule_id: int) -> bool:
    """Deactivate a brand rule."""
    conn = _get_conn()
    cur = conn.execute(
        "UPDATE brand_profile SET active = 0 WHERE id = ? AND active = 1",
        (rule_id,)
    )
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    return changed


def replace_brand_rules(category: str, rules: list, source: str = "analysis") -> list:
    """Replace all analysis-sourced rules in a category with new ones.
    Preserves manually-added rules. Returns list of new rule IDs."""
    conn = _get_conn()
    # Deactivate old analysis rules in this category
    conn.execute(
        "UPDATE brand_profile SET active = 0 WHERE category = ? AND source = 'analysis' AND active = 1",
        (category,)
    )
    # Insert new rules
    now = datetime.now(timezone.utc).isoformat()
    new_ids = []
    for rule_text in rules:
        cur = conn.execute(
            "INSERT INTO brand_profile (category, rule, source, created_at) VALUES (?, ?, ?, ?)",
            (category, rule_text, source, now)
        )
        new_ids.append(cur.lastrowid)
    conn.commit()
    conn.close()
    return new_ids


# --- System Settings ---


def get_setting(key: str, default: str = "") -> str:
    """Get a system setting value."""
    conn = _get_conn()
    row = conn.execute("SELECT value FROM system_settings WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row[0] if row else default


def set_setting(key: str, value: str) -> None:
    """Set a system setting value."""
    conn = _get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO system_settings (key, value) VALUES (?, ?)",
        (key, value)
    )
    conn.commit()
    conn.close()


def is_dry_run() -> bool:
    """Check if dry run mode is enabled."""
    return get_setting("dry_run", "false") == "true"


# --- Scheduling ---


def schedule_draft(draft_id: int, scheduled_at: str) -> bool:
    """Set a draft to scheduled status with a publish time."""
    conn = _get_conn()
    cur = conn.execute(
        "UPDATE content_drafts SET status = 'scheduled', scheduled_at = ? WHERE id = ? AND status = 'approved'",
        (scheduled_at, draft_id)
    )
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    return changed


def unschedule_draft(draft_id: int) -> bool:
    """Revert a scheduled draft back to approved."""
    conn = _get_conn()
    cur = conn.execute(
        "UPDATE content_drafts SET status = 'approved', scheduled_at = NULL WHERE id = ? AND status = 'scheduled'",
        (draft_id,)
    )
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    return changed


def get_scheduled_due() -> list:
    """Get all drafts that are scheduled and due for publishing (scheduled_at <= now)."""
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()
    rows = conn.execute(
        "SELECT id, content_class, instagram_caption, facebook_caption, "
        "hashtags_json, visual_suggestion, reasoning, status, rejection_reason, "
        "created_at, approved_at, published_at, image_path, late_post_id, instagram_url, photo_id, "
        "platforms_json, facebook_url, late_facebook_post_id, scheduled_at "
        "FROM content_drafts WHERE status = 'scheduled' AND scheduled_at <= ? "
        "ORDER BY scheduled_at",
        (now,)
    ).fetchall()
    conn.close()
    return [
        {
            "id": r[0], "content_class": r[1], "instagram_caption": r[2],
            "facebook_caption": r[3], "hashtags": json.loads(r[4] or "[]"),
            "visual_suggestion": r[5], "reasoning": r[6], "status": r[7],
            "rejection_reason": r[8], "created_at": r[9], "approved_at": r[10],
            "published_at": r[11], "image_path": r[12],
            "late_post_id": r[13], "instagram_url": r[14],
            "photo_id": r[15] if r[15] else 0,
            "platforms": json.loads(r[16]) if r[16] else ["instagram"],
            "facebook_url": r[17] or "", "late_facebook_post_id": r[18] or "",
            "scheduled_at": r[19],
        }
        for r in rows
    ]


def save_schedule_slots(slots: list) -> None:
    """Replace all schedule slots. slots = [{"day_of_week": "Tuesday", "time_utc": "16:00"}, ...]"""
    conn = _get_conn()
    conn.execute("UPDATE schedule_slots SET active = 0")
    now = datetime.now(timezone.utc).isoformat()
    for slot in slots:
        conn.execute(
            "INSERT INTO schedule_slots (day_of_week, time_utc, active, created_at) VALUES (?, ?, 1, ?)",
            (slot["day_of_week"], slot["time_utc"], now)
        )
    conn.commit()
    conn.close()


def get_schedule_slots() -> list:
    """Get active schedule slots."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT id, day_of_week, time_utc FROM schedule_slots WHERE active = 1 ORDER BY id"
    ).fetchall()
    conn.close()
    return [{"id": r[0], "day_of_week": r[1], "time_utc": r[2]} for r in rows]


def get_next_open_slot() -> str:
    """Compute the next available schedule slot that doesn't have a draft assigned.
    Returns ISO 8601 timestamp or empty string."""
    slots = get_schedule_slots()
    if not slots:
        return ""
    # Get all future scheduled drafts
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()
    rows = conn.execute(
        "SELECT scheduled_at FROM content_drafts WHERE status = 'scheduled' AND scheduled_at > ?",
        (now,)
    ).fetchall()
    conn.close()
    taken = {r[0][:16] for r in rows if r[0]}  # Compare up to minute precision

    day_map = {"Monday": 0, "Tuesday": 1, "Wednesday": 2, "Thursday": 3,
               "Friday": 4, "Saturday": 5, "Sunday": 6}
    today = datetime.now(timezone.utc)

    # Check next 14 days of slots
    for day_offset in range(14):
        check_date = today + timedelta(days=day_offset)
        for slot in slots:
            slot_day = day_map.get(slot["day_of_week"], -1)
            if check_date.weekday() != slot_day:
                continue
            hour, minute = slot["time_utc"].split(":")
            candidate = check_date.replace(hour=int(hour), minute=int(minute), second=0, microsecond=0)
            if candidate <= today:
                continue
            candidate_key = candidate.isoformat()[:16]
            if candidate_key not in taken:
                return candidate.isoformat()
    return ""


def update_draft_platforms(draft_id: int, platforms: list) -> bool:
    """Update which platforms a draft publishes to."""
    conn = _get_conn()
    cur = conn.execute(
        "UPDATE content_drafts SET platforms_json = ? WHERE id = ?",
        (json.dumps(platforms), draft_id)
    )
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    return changed


def set_draft_facebook_info(draft_id: int, late_post_id: str = "",
                            facebook_url: str = "") -> None:
    """Store Facebook post info after publishing."""
    conn = _get_conn()
    conn.execute(
        "UPDATE content_drafts SET late_facebook_post_id = ?, facebook_url = ? WHERE id = ?",
        (late_post_id, facebook_url, draft_id)
    )
    conn.commit()
    conn.close()


def set_draft_photo_id(draft_id: int, photo_id: int) -> bool:
    """Set the photo_id on a content draft."""
    conn = _get_conn()
    cur = conn.execute(
        "UPDATE content_drafts SET photo_id = ? WHERE id = ?",
        (photo_id, draft_id)
    )
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    return changed


def increment_photo_used_count(photo_id: int) -> None:
    """Increment the used_count on a photo."""
    conn = _get_conn()
    conn.execute(
        "UPDATE photo_library SET used_count = used_count + 1 WHERE id = ?",
        (photo_id,)
    )
    conn.commit()
    conn.close()


# --- OAuth Tokens ---


def save_oauth_tokens(provider: str, access_token: str, refresh_token: str,
                      expires_at: str = "") -> None:
    """Insert or replace OAuth tokens for a provider."""
    conn = _get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO oauth_tokens "
        "(provider, access_token, refresh_token, expires_at, folder_id, updated_at) "
        "VALUES (?, ?, ?, ?, COALESCE((SELECT folder_id FROM oauth_tokens WHERE provider = ?), ''), ?)",
        (provider, access_token, refresh_token, expires_at, provider,
         datetime.now(timezone.utc).isoformat())
    )
    conn.commit()
    conn.close()


def get_oauth_tokens(provider: str) -> dict | None:
    """Get OAuth tokens for a provider."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT provider, access_token, refresh_token, expires_at, folder_id, updated_at "
        "FROM oauth_tokens WHERE provider = ?",
        (provider,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    return {
        "provider": row[0], "access_token": row[1], "refresh_token": row[2],
        "expires_at": row[3], "folder_id": row[4], "updated_at": row[5],
    }


def set_oauth_folder(provider: str, folder_id: str) -> bool:
    """Set the sync folder for a provider."""
    conn = _get_conn()
    cur = conn.execute(
        "UPDATE oauth_tokens SET folder_id = ? WHERE provider = ?",
        (folder_id, provider)
    )
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    return changed


def delete_oauth_tokens(provider: str) -> bool:
    """Remove OAuth tokens for a provider."""
    conn = _get_conn()
    cur = conn.execute("DELETE FROM oauth_tokens WHERE provider = ?", (provider,))
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    return changed


def get_availability_summary(days_ahead: int = 7) -> list:
    """Get booking counts for all service slots in the next N days.
    Returns list of {service_key, date, slot_time, booked_guests, capacity, spots_remaining}.
    Used by content_agent to generate operationally-aware posts."""
    from shared import config_loader

    expire_stale_holds()
    trips = config_loader.get_services()
    now_curacao = datetime.now(timezone(timedelta(hours=-4)))
    today = now_curacao.date()

    day_name_map = {
        0: "Monday", 1: "Tuesday", 2: "Wednesday", 3: "Thursday",
        4: "Friday", 5: "Saturday", 6: "Sunday"
    }

    results = []
    conn = _get_conn()

    for service_key, trip_data in trips.items():
        capacity = trip_data.get("capacity", 0)
        days_available = trip_data.get("days_available", "daily")
        slots = trip_data.get("slots", [])

        # Parse which days this service operates
        if days_available.lower() == "daily":
            valid_days = set(range(7))
        else:
            valid_days = set()
            for d_idx, d_name in day_name_map.items():
                if d_name.lower() in days_available.lower():
                    valid_days.add(d_idx)
            # Handle plural forms: "Fridays" → "Friday"
            if not valid_days:
                for d_idx, d_name in day_name_map.items():
                    if d_name.lower() + "s" in days_available.lower():
                        valid_days.add(d_idx)

        for day_offset in range(days_ahead):
            check_date = today + timedelta(days=day_offset)
            if check_date.weekday() not in valid_days:
                continue
            date_str = check_date.isoformat()

            for dep in slots:
                dep_time = dep.get("time", "")
                now_utc = datetime.now(timezone.utc).isoformat()
                row = conn.execute(
                    "SELECT COALESCE(SUM(guests), 0) FROM service_bookings "
                    "WHERE service_key=? AND date=? AND slot_time=? "
                    "AND status IN ('soft_hold', 'confirmed') "
                    "AND (status='confirmed' OR expires_at > ?)",
                    (service_key, date_str, dep_time, now_utc)
                ).fetchone()
                booked = row[0] if row else 0
                results.append({
                    "service_key": service_key,
                    "date": date_str,
                    "slot_time": dep_time,
                    "booked_guests": booked,
                    "capacity": capacity,
                    "spots_remaining": max(0, capacity - booked),
                })

    conn.close()
    return results


def set_draft_image_path(draft_id: int, image_path: str) -> bool:
    """Set the generated image path for a content draft."""
    conn = _get_conn()
    cur = conn.execute(
        "UPDATE content_drafts SET image_path = ? WHERE id = ?",
        (image_path, draft_id)
    )
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    return changed


def set_draft_published_info(draft_id: int, late_post_id: str, instagram_url: str) -> bool:
    """Store the Late post ID and Instagram URL after publishing."""
    conn = _get_conn()
    cur = conn.execute(
        "UPDATE content_drafts SET late_post_id = ?, instagram_url = ? WHERE id = ?",
        (late_post_id, instagram_url, draft_id)
    )
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    return changed


def save_content_learning(rule: str, source_draft_ids: list = None) -> int:
    """Save a brand learning rule. Returns row id."""
    conn = _get_conn()
    cur = conn.execute(
        "INSERT INTO content_learnings (rule, source_draft_ids, active, created_at) "
        "VALUES (?, ?, 1, ?)",
        (rule, json.dumps(source_draft_ids or [], ensure_ascii=False),
         datetime.now(timezone.utc).isoformat())
    )
    learning_id = cur.lastrowid
    conn.commit()
    conn.close()
    return learning_id


def get_active_learnings() -> list:
    """Get all active brand learning rules. Oldest first (chronological order)."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT id, rule, source_draft_ids, created_at "
        "FROM content_learnings WHERE active = 1 ORDER BY created_at ASC"
    ).fetchall()
    conn.close()
    return [
        {"id": r[0], "rule": r[1],
         "source_draft_ids": json.loads(r[2] or "[]"), "created_at": r[3]}
        for r in rows
    ]


def deactivate_learning(learning_id: int) -> bool:
    """Deactivate a brand learning rule. Returns True if row updated."""
    conn = _get_conn()
    cur = conn.execute(
        "UPDATE content_learnings SET active = 0 WHERE id = ? AND active = 1",
        (learning_id,)
    )
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    return changed


# ── Brief 215: Escalation learnings (operator answers as approved knowledge) ──

def save_escalation_learning(conversation_id: str, channel: str,
                              source_question: str, human_answer: str,
                              status: str = "approved",
                              ai_may_use: bool = True,
                              category: str = None,
                              created_by: str = None) -> int:
    """Brief 215: persist an operator answer as an approved learning entry.
    Default status='approved' + ai_may_use=True per SR's contract Section 3.
    Returns the new row id."""
    now = datetime.now(timezone.utc).isoformat()
    conn = _get_conn()
    cur = conn.execute(
        "INSERT INTO escalation_learnings "
        "(conversation_id, channel, source_question, human_answer, status, "
        "ai_may_use_automatically, category, created_by, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (conversation_id, channel, source_question or "", human_answer,
         status, 1 if ai_may_use else 0, category, created_by, now, now))
    row_id = cur.lastrowid
    conn.commit()
    conn.close()
    return row_id


def create_pending_learning(conversation_id: str, channel: str,
                              source_question: str, suggested_text: str,
                              created_by: str = None) -> int:
    """Brief 263: create a NEW learning row in status='suggested' (pending
    per issue #32 vocabulary). Used by POST /escalations/{id}/suggest-learning.
    Distinct from save_escalation_learning which defaults to status='approved'
    (the legacy Brief 215 auto-learn path). ai_may_use_automatically is
    False on creation; approval flips status only — the prompt-path
    filter at get_approved_learnings_for_prompt selects status IN
    ('approved','saved') AND ai_may_use_automatically=1, so a suggested
    row stays out of the prompt regardless of the ai_may_use flag value."""
    return save_escalation_learning(
        conversation_id=conversation_id,
        channel=channel,
        source_question=source_question,
        human_answer=suggested_text,
        status="suggested",
        ai_may_use=False,
        category=None,
        created_by=created_by,
    )


def edit_escalation_learning_text(learning_id: int, new_text: str) -> bool:
    """Brief 263: edit the human_answer text. Allowed only when the row's
    current status is 'suggested' (pending). Returns False if the row
    doesn't exist OR status is approved/saved/deleted - once approved or
    dismissed, the text is frozen (operator must dismiss + create a new
    suggestion to change it)."""
    if not new_text or not isinstance(new_text, str):
        return False
    now = datetime.now(timezone.utc).isoformat()
    conn = _get_conn()
    row = conn.execute(
        "SELECT status FROM escalation_learnings WHERE id = ?",
        (learning_id,)).fetchone()
    if not row or row[0] != "suggested":
        conn.close()
        return False
    conn.execute(
        "UPDATE escalation_learnings SET human_answer = ?, updated_at = ? "
        "WHERE id = ?",
        (new_text, now, learning_id))
    conn.commit()
    conn.close()
    return True


def list_escalation_learnings(status: str = None) -> list:
    """Brief 215 + Brief 263: return escalation learning entries newest-first.
    Skip rows with status='deleted' (dismissed) unless status filter is
    explicitly 'deleted'. Optional status filter. Brief 263 adds approvedAt
    + dismissedAt + approvedBy audit fields to the response shape."""
    conn = _get_conn()
    if status:
        rows = conn.execute(
            "SELECT id, conversation_id, channel, source_question, human_answer, "
            "status, ai_may_use_automatically, category, created_by, "
            "created_at, updated_at, approved_at, dismissed_at, approved_by "
            "FROM escalation_learnings "
            "WHERE status = ? ORDER BY created_at DESC",
            (status,)).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, conversation_id, channel, source_question, human_answer, "
            "status, ai_may_use_automatically, category, created_by, "
            "created_at, updated_at, approved_at, dismissed_at, approved_by "
            "FROM escalation_learnings "
            "WHERE status != 'deleted' ORDER BY created_at DESC").fetchall()
    conn.close()
    return [{
        "id": r[0], "conversationId": r[1], "channel": r[2],
        "sourceQuestion": r[3], "humanAnswer": r[4],
        "status": r[5], "aiMayUseAutomatically": bool(r[6]),
        "category": r[7], "createdBy": r[8],
        "createdAt": r[9], "updatedAt": r[10],
        "approvedAt": r[11], "dismissedAt": r[12], "approvedBy": r[13],
    } for r in rows]


def update_escalation_learning_status(learning_id: int, new_status: str,
                                       operator: str = "") -> bool:
    """Brief 215 + Brief 263: flip status. Allowed: suggested|approved|saved|deleted.
    Brief 263 also records:
    - approved_at + approved_by when new_status='approved' (uniform audit:
      legacy /learning/{id}/approve also stamps approved_at with empty
      operator string).
    - dismissed_at when new_status='deleted' (soft-reject via the new
      /escalation-learnings/{id}/dismiss endpoint; legacy DELETE /learning/{id}
      hard-removes the row instead of stamping)."""
    if new_status not in ("suggested", "approved", "saved", "deleted"):
        return False
    now = datetime.now(timezone.utc).isoformat()
    conn = _get_conn()
    if new_status == "approved":
        # Brief 263: also flip ai_may_use_automatically=1 so a row that
        # was originally suggested (ai_may_use=0) becomes prompt-path
        # eligible immediately on approval. Canonical approve semantics.
        cur = conn.execute(
            "UPDATE escalation_learnings SET status = ?, updated_at = ?, "
            "approved_at = ?, approved_by = ?, ai_may_use_automatically = 1 "
            "WHERE id = ?",
            (new_status, now, now, operator or "", learning_id))
    elif new_status == "deleted":
        cur = conn.execute(
            "UPDATE escalation_learnings SET status = ?, updated_at = ?, "
            "dismissed_at = ? WHERE id = ?",
            (new_status, now, now, learning_id))
    else:
        cur = conn.execute(
            "UPDATE escalation_learnings SET status = ?, updated_at = ? WHERE id = ?",
            (new_status, now, learning_id))
    updated = cur.rowcount > 0
    conn.commit()
    conn.close()
    return updated


def delete_escalation_learning(learning_id: int) -> bool:
    """Brief 215: hard-delete an escalation learning row."""
    conn = _get_conn()
    cur = conn.execute(
        "DELETE FROM escalation_learnings WHERE id = ?", (learning_id,))
    deleted = cur.rowcount > 0
    conn.commit()
    conn.close()
    return deleted


def get_learning_status_for_conversation(conversation_id: str) -> str:
    """Brief 222: highest-precedence escalation_learning status for this
    conversation. Powers the `learningStatus` field on the conversation
    detail response. Precedence: saved > approved > suggested > none.
    Skip deleted rows."""
    if not conversation_id:
        return "none"
    conn = _get_conn()
    rows = conn.execute(
        "SELECT status FROM escalation_learnings "
        "WHERE conversation_id = ? AND status != 'deleted'",
        (conversation_id,)).fetchall()
    conn.close()
    statuses = {r[0] for r in rows}
    for s in ("saved", "approved", "suggested"):
        if s in statuses:
            return s
    return "none"


def get_approved_learnings_for_prompt(channel: str, limit: int = 20) -> list:
    """Brief 219: return the N most-recent approved escalation learnings
    that Marina is allowed to use automatically. Filters: channel match,
    status IN ('approved', 'saved'), ai_may_use_automatically=1.
    Returns newest first. Used by marina_agent._build_system_prompt to
    inject an APPROVED ANSWERS block when the tenant opts in via
    client.json::features.approved_learnings_in_prompt."""
    if not channel or limit <= 0:
        return []
    conn = _get_conn()
    rows = conn.execute(
        "SELECT source_question, human_answer FROM escalation_learnings "
        "WHERE channel = ? "
        "AND status IN ('approved', 'saved') "
        "AND ai_may_use_automatically = 1 "
        "ORDER BY created_at DESC LIMIT ?",
        (channel, limit)).fetchall()
    conn.close()
    return [{"question": r[0] or "", "answer": r[1] or ""} for r in rows]


# ── Brief 216: Your Info Updates (per-tenant temporary/permanent updates) ─────

_INFO_UPDATE_TYPES = {
    "general",
    "offer",
    "holiday",
    "hours",
    "pricing",
    "policy",
    "property",
    "product",
    "other",
}


def info_update_create(text: str, type_: str = "general",
                       active: bool = True,
                       start_date: str = None,
                       end_date: str = None) -> int:
    """Brief 216: insert a new info_update row. Permanent rows omit
    start_date + end_date; scheduled rows include both. Returns row id."""
    if type_ not in _INFO_UPDATE_TYPES:
        type_ = "other"
    now = datetime.now(timezone.utc).isoformat()
    conn = _get_conn()
    cur = conn.execute(
        "INSERT INTO info_updates "
        "(type, text, active, start_date, end_date, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (type_, text, 1 if active else 0, start_date, end_date, now, now))
    row_id = cur.lastrowid
    conn.commit()
    conn.close()
    return row_id


def info_updates_list_all() -> list:
    """Brief 216: return ALL info_updates (active + inactive, in-window
    + out-of-window) for the dashboard's Settings → Your Info Updates
    management list. camelCase keys for SR's frontend."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT id, type, text, active, start_date, end_date, "
        "created_at, updated_at FROM info_updates "
        "ORDER BY created_at DESC").fetchall()
    conn.close()
    return [{
        "id": r[0], "type": r[1], "text": r[2],
        "active": bool(r[3]),
        "startDate": r[4], "endDate": r[5],
        "createdAt": r[6], "updatedAt": r[7],
    } for r in rows]


def info_update_delete(update_id: int) -> bool:
    """Brief 216: hard-delete an info_update row."""
    conn = _get_conn()
    cur = conn.execute("DELETE FROM info_updates WHERE id = ?", (update_id,))
    deleted = cur.rowcount > 0
    conn.commit()
    conn.close()
    return deleted


def info_update_set_active(update_id: int, active: bool) -> bool:
    """Set an info_update active flag without changing its text/type."""
    now = datetime.now(timezone.utc).isoformat()
    conn = _get_conn()
    cur = conn.execute(
        "UPDATE info_updates SET active = ?, updated_at = ? WHERE id = ?",
        (1 if active else 0, now, update_id),
    )
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    return changed


def info_update_update(
    update_id: int,
    *,
    text: str = None,
    type_: str = None,
    active: bool = None,
    start_date: str = None,
    end_date: str = None,
) -> bool:
    """Edit an info_update row while preserving its id and created_at."""
    fields = []
    params = []
    if text is not None:
        fields.append("text = ?")
        params.append(text)
    if type_ is not None:
        fields.append("type = ?")
        params.append(type_ if type_ in _INFO_UPDATE_TYPES else "other")
    if active is not None:
        fields.append("active = ?")
        params.append(1 if active else 0)
    if start_date is not None:
        fields.append("start_date = ?")
        params.append(start_date or None)
    if end_date is not None:
        fields.append("end_date = ?")
        params.append(end_date or None)
    if not fields:
        conn = _get_conn()
        exists = conn.execute(
            "SELECT 1 FROM info_updates WHERE id = ?", (update_id,)
        ).fetchone() is not None
        conn.close()
        return exists
    now = datetime.now(timezone.utc).isoformat()
    fields.append("updated_at = ?")
    params.append(now)
    params.append(update_id)
    conn = _get_conn()
    cur = conn.execute(
        f"UPDATE info_updates SET {', '.join(fields)} WHERE id = ?",
        tuple(params),
    )
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    return changed


def get_active_info_updates() -> list:
    """Brief 216: return currently-active info_updates ready for prompt
    injection. Active iff active=1 AND (no dates OR within [start, end]).
    Half-open windows allowed: one of start/end set, the other null,
    means 'active from X' or 'active until Y'. ISO YYYY-MM-DD format
    expected for date columns; lexicographic comparison works because
    the format is fixed-width."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    conn = _get_conn()
    rows = conn.execute(
        "SELECT type, text, start_date, end_date FROM info_updates "
        "WHERE active = 1 ORDER BY created_at DESC").fetchall()
    conn.close()
    out = []
    for type_, text, start_date, end_date in rows:
        if not start_date and not end_date:
            out.append({"type": type_, "text": text})
            continue
        if start_date and today < start_date:
            continue
        if end_date and today > end_date:
            continue
        out.append({"type": type_, "text": text})
    return out


def _last_customer_message_for(conversation_id: str, channel: str) -> str:
    """Brief 215: look up the most recent customer-role message text for
    this conversation, used as `source_question` when auto-creating a
    learning entry from an operator answer. Returns '' on miss."""
    if not conversation_id:
        return ""
    if channel == "email":
        thread_key = _find_email_thread_key_for(conversation_id)
        if not thread_key:
            return ""
        conv = email_get_conversation(thread_key)
        for m in reversed(conv.get("messages", []) or []):
            if m.get("role") in ("user", "customer"):
                return (m.get("text") or m.get("body") or "")[:1000]
        return ""
    history = wa_get_full_history(conversation_id, limit=10)
    for m in reversed(history):
        if m.get("role") == "user":
            return (m.get("text") or "")[:1000]
    return ""


# ==================== Brief 168: Payment hold state machine ====================

def set_payment_window(hold_id: int, payment_expires_at: str, customer_phone: str = "") -> bool:
    """Brief 168: set a payment expiry timestamp on a confirmed hold.
    Called right after confirm_hold() in the orchestrator when payment.timing
    is upfront/deposit. The reaper (hold_reaper.py) will scan rows where
    payment_expires_at is set and fire reminders / expirations.

    customer_phone is stored so the reaper can route reminders back to the
    customer without re-looking-up the thread state.
    """
    conn = _get_conn()
    cur = conn.execute(
        "UPDATE service_bookings SET payment_expires_at = ?, customer_phone = ? "
        "WHERE id = ? AND status = 'confirmed'",
        (payment_expires_at, customer_phone, hold_id)
    )
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    return changed


def get_holds_needing_reminder(now_iso: str, reminder_before_minutes: int) -> list:
    """Brief 168: return confirmed holds where payment_expires_at is within the
    reminder window AND payment_reminder_sent_at IS NULL. The reaper uses this
    to decide which holds to remind."""
    if not reminder_before_minutes or reminder_before_minutes <= 0:
        return []
    conn = _get_conn()
    rows = conn.execute(
        "SELECT id, booking_ref, service_key, date, slot_time, guests, customer_name, "
        "customer_email, customer_phone, payment_expires_at "
        "FROM service_bookings "
        "WHERE status = 'confirmed' "
        "AND payment_expires_at IS NOT NULL "
        "AND payment_reminder_sent_at IS NULL "
        "AND datetime(?) >= datetime(payment_expires_at, ?) "
        "AND datetime(?) < datetime(payment_expires_at) "
        "ORDER BY payment_expires_at",
        (now_iso, f"-{int(reminder_before_minutes)} minutes", now_iso)
    ).fetchall()
    conn.close()
    return [
        {"id": r[0], "booking_ref": r[1], "service_key": r[2], "date": r[3],
         "slot_time": r[4], "guests": r[5], "customer_name": r[6],
         "customer_email": r[7], "customer_phone": r[8], "payment_expires_at": r[9]}
        for r in rows
    ]


def get_expired_payment_holds(now_iso: str) -> list:
    """Brief 168: return confirmed holds where payment_expires_at has passed.
    The reaper uses this to release slots + mark status."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT id, booking_ref, service_key, date, slot_time, guests, customer_name, "
        "customer_email, customer_phone, payment_expires_at "
        "FROM service_bookings "
        "WHERE status = 'confirmed' "
        "AND payment_expires_at IS NOT NULL "
        "AND datetime(?) >= datetime(payment_expires_at) "
        "ORDER BY payment_expires_at",
        (now_iso,)
    ).fetchall()
    conn.close()
    return [
        {"id": r[0], "booking_ref": r[1], "service_key": r[2], "date": r[3],
         "slot_time": r[4], "guests": r[5], "customer_name": r[6],
         "customer_email": r[7], "customer_phone": r[8], "payment_expires_at": r[9]}
        for r in rows
    ]


def mark_payment_reminder_sent(hold_id: int) -> bool:
    """Brief 168: stamp payment_reminder_sent_at for a booking row."""
    now = datetime.now(timezone.utc).isoformat()
    conn = _get_conn()
    cur = conn.execute(
        "UPDATE service_bookings SET payment_reminder_sent_at = ? WHERE id = ?",
        (now, hold_id)
    )
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    return changed


def expire_payment_hold(hold_id: int) -> bool:
    """Brief 168: mark a hold as payment-expired. Also clears payment_expires_at so
    the reaper stops scanning it. The actual slot release is done by the caller
    (reaper) via cancel_hold if needed — expire_payment_hold only flips the status."""
    conn = _get_conn()
    cur = conn.execute(
        "UPDATE service_bookings SET status = 'payment_expired', "
        "payment_expires_at = NULL WHERE id = ? AND status = 'confirmed'",
        (hold_id,)
    )
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    return changed


# ==================== Brief 166: Cross-channel customer file ====================

def _normalize_identifier_value(type_: str, value: str) -> str:
    """Brief 178: normalize identifier values for storage and lookup so case
    variants don't create silos. Email is case-insensitive in practice
    (every real mail system normalizes for comparison). Other identifier
    types are stripped only. Returns the normalized value. Idempotent."""
    if not value:
        return ""
    normalized = value.strip()
    if type_ == "email":
        normalized = normalized.lower()
    return normalized


def customer_lookup(type_: str, value: str):
    """Brief 166: look up a customer by an identifier. Returns None if not found.
    Brief 178: normalizes value (e.g. lowercases email) before lookup."""
    if not type_ or not value:
        return None
    value = _normalize_identifier_value(type_, value)
    if not value:
        return None
    conn = _get_conn()
    row = conn.execute(
        "SELECT c.id, c.display_name, c.summary, c.notes, c.first_seen, c.last_seen "
        "FROM customers c "
        "INNER JOIN customer_identifiers ci ON ci.customer_id = c.id "
        "WHERE ci.type = ? AND ci.value = ? AND c.active = 1 "
        "LIMIT 1",
        (type_, value)
    ).fetchone()
    conn.close()
    if not row:
        return None
    return {
        "id": row[0], "display_name": row[1] or "", "summary": row[2] or "",
        "notes": row[3] or "", "first_seen": row[4], "last_seen": row[5],
    }


def customer_lookup_or_create(type_: str, value: str, display_name: str = "") -> dict:
    """Brief 166: look up a customer by identifier, or create a new row if not found.
    Idempotent — safe to call on every inbound message.
    Brief 178: normalizes value (e.g. lowercases email) before lookup/insert."""
    if not type_ or not value:
        raise ValueError("type and value required")
    value = _normalize_identifier_value(type_, value)
    if not value:
        raise ValueError("normalized value empty")
    existing = customer_lookup(type_, value)
    if existing:
        if display_name and not existing["display_name"]:
            conn = _get_conn()
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "UPDATE customers SET display_name = ?, last_seen = ? WHERE id = ?",
                (display_name, now, existing["id"])
            )
            conn.commit()
            conn.close()
            existing["display_name"] = display_name
        return existing
    now = datetime.now(timezone.utc).isoformat()
    conn = _get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO customers (display_name, first_seen, last_seen) VALUES (?, ?, ?)",
            (display_name or "", now, now)
        )
        customer_id = cur.lastrowid
        conn.execute(
            "INSERT INTO customer_identifiers (customer_id, type, value, first_seen) "
            "VALUES (?, ?, ?, ?)",
            (customer_id, type_, value, now)
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
        conn.close()
        existing = customer_lookup(type_, value)
        if existing:
            return existing
        raise
    conn.close()
    return {
        "id": customer_id, "display_name": display_name or "",
        "summary": "", "notes": "",
        "first_seen": now, "last_seen": now,
    }


def _customer_choose_merge_survivor(a_id: int, b_id: int):
    """Brief 166: pick the surviving customer. Earlier first_seen wins (older = canonical)."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT id, first_seen FROM customers WHERE id IN (?, ?)", (a_id, b_id)
    ).fetchall()
    conn.close()
    if len(rows) != 2:
        return (a_id, b_id)
    rows = sorted(rows, key=lambda r: r[1])
    return (rows[0][0], rows[1][0])


def customer_merge(surviving_id: int, absorbed_id: int) -> dict:
    """Brief 166: merge absorbed_id into surviving_id. Moves identifiers + interactions,
    writes an audit row, deactivates the absorbed row. Idempotent."""
    if surviving_id == absorbed_id:
        return {"action": "noop"}
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()
    # Remove duplicate identifiers from the absorbed row (UNIQUE constraint would
    # otherwise block the UPDATE below).
    conn.execute(
        "DELETE FROM customer_identifiers WHERE customer_id = ? AND (type, value) IN "
        "(SELECT type, value FROM customer_identifiers WHERE customer_id = ?)",
        (absorbed_id, surviving_id)
    )
    conn.execute(
        "UPDATE customer_identifiers SET customer_id = ? WHERE customer_id = ?",
        (surviving_id, absorbed_id)
    )
    conn.execute(
        "UPDATE customer_interactions SET customer_id = ? WHERE customer_id = ?",
        (surviving_id, absorbed_id)
    )
    # Fold display_name if surviving is empty
    conn.execute(
        "UPDATE customers SET display_name = COALESCE(NULLIF(display_name, ''), "
        "  (SELECT display_name FROM customers WHERE id = ?)), "
        "last_seen = ? WHERE id = ?",
        (absorbed_id, now, surviving_id)
    )
    conn.execute(
        "INSERT INTO customer_merges (surviving_id, absorbed_id, merged_at) VALUES (?, ?, ?)",
        (surviving_id, absorbed_id, now)
    )
    conn.execute("UPDATE customers SET active = 0 WHERE id = ?", (absorbed_id,))
    conn.commit()
    conn.close()
    return {"action": "merged", "surviving_id": surviving_id, "absorbed_id": absorbed_id}


def customer_add_identifier(customer_id: int, type_: str, value: str) -> dict:
    """Brief 166: add a new identifier to an existing customer. Handles the cross-channel
    merge case: if the (type, value) already belongs to a DIFFERENT customer, merge them.
    Brief 178: normalizes value (e.g. lowercases email) before lookup/insert.
    Returns {"action": "added" | "merged" | "already_linked" | "noop", "customer_id": int}."""
    if not customer_id or not type_ or not value:
        return {"action": "noop", "customer_id": customer_id}
    value = _normalize_identifier_value(type_, value)
    if not value:
        return {"action": "noop", "customer_id": customer_id}
    now = datetime.now(timezone.utc).isoformat()
    conn = _get_conn()
    existing_row = conn.execute(
        "SELECT customer_id FROM customer_identifiers WHERE type = ? AND value = ?",
        (type_, value)
    ).fetchone()
    if existing_row:
        existing_customer_id = existing_row[0]
        conn.close()
        if existing_customer_id == customer_id:
            return {"action": "already_linked", "customer_id": customer_id}
        surviving, absorbed = _customer_choose_merge_survivor(customer_id, existing_customer_id)
        customer_merge(surviving, absorbed)
        return {"action": "merged", "customer_id": surviving}
    try:
        conn.execute(
            "INSERT INTO customer_identifiers (customer_id, type, value, first_seen) "
            "VALUES (?, ?, ?, ?)",
            (customer_id, type_, value, now)
        )
        conn.execute(
            "UPDATE customers SET last_seen = ? WHERE id = ?",
            (now, customer_id)
        )
        conn.commit()
        conn.close()
        return {"action": "added", "customer_id": customer_id}
    except sqlite3.IntegrityError:
        conn.rollback()
        conn.close()
        return customer_add_identifier(customer_id, type_, value)


def customer_record_interaction(customer_id: int, channel: str, summary: str):
    """Brief 166: append a one-line interaction summary. Updates last_seen."""
    if not customer_id or not channel or not summary:
        return
    now = datetime.now(timezone.utc).isoformat()
    conn = _get_conn()
    conn.execute(
        "INSERT INTO customer_interactions (customer_id, channel, summary, created_at) "
        "VALUES (?, ?, ?, ?)",
        (customer_id, channel, summary[:500], now)
    )
    conn.execute("UPDATE customers SET last_seen = ? WHERE id = ?", (now, customer_id))
    conn.commit()
    conn.close()


def customer_update_display_name(customer_id: int, display_name: str):
    """Brief 181: update a customer's display_name when Marina extracts a different
    name from the conversation than what was set from the webhook sender_name."""
    if not customer_id or not display_name:
        return
    conn = _get_conn()
    conn.execute(
        "UPDATE customers SET display_name = ? WHERE id = ?",
        (display_name.strip(), customer_id)
    )
    conn.commit()
    conn.close()


def customer_get_full(customer_id: int) -> dict:
    """Brief 166: return the full customer file for marina_agent's prompt block.
    Caps identifiers to 20 and interactions to 5 (prompt-size safety)."""
    if not customer_id:
        return {}
    conn = _get_conn()
    c_row = conn.execute(
        "SELECT id, display_name, summary, notes, first_seen, last_seen "
        "FROM customers WHERE id = ? AND active = 1",
        (customer_id,)
    ).fetchone()
    if not c_row:
        conn.close()
        return {}
    id_rows = conn.execute(
        "SELECT type, value, first_seen FROM customer_identifiers "
        "WHERE customer_id = ? ORDER BY first_seen LIMIT 20",
        (customer_id,)
    ).fetchall()
    int_rows = conn.execute(
        "SELECT channel, summary, created_at FROM customer_interactions "
        "WHERE customer_id = ? ORDER BY created_at DESC LIMIT 5",
        (customer_id,)
    ).fetchall()
    conn.close()
    return {
        "id": c_row[0], "display_name": c_row[1] or "", "summary": c_row[2] or "",
        "notes": c_row[3] or "", "first_seen": c_row[4], "last_seen": c_row[5],
        "identifiers": [{"type": r[0], "value": r[1], "first_seen": r[2]} for r in id_rows],
        "recent_interactions": [
            {"channel": r[0], "summary": r[1], "created_at": r[2]} for r in int_rows
        ],
    }


# ── Brief 207: Tasks helpers (operator-side workflow) ──────────────────────

def tasks_create(task_id: str, body_html: str, body_text: str,
                 created_by: str, assigned_to: str) -> dict:
    """Insert a new task. Returns the task dict (with empty attachments).
    Brief 223: also allocates the next per-workspace task_number."""
    now = datetime.now(timezone.utc).isoformat()
    conn = _get_conn()
    next_num = conn.execute(
        "SELECT COALESCE(MAX(task_number), 0) + 1 FROM tasks"
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO tasks (id, body_html, body_text, created_by, assigned_to, "
        "status, task_number, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, 'open', ?, ?, ?)",
        (task_id, body_html, body_text, created_by, assigned_to,
         next_num, now, now)
    )
    conn.commit()
    conn.close()
    return tasks_get(task_id)


def tasks_get(task_id: str):
    """Fetch a single task with its attachments. Returns None if not found.
    Brief 223: response includes task_number."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT id, body_html, body_text, created_by, assigned_to, status, "
        "completed_at, completed_by, task_number, created_at, updated_at "
        "FROM tasks WHERE id = ?", (task_id,)
    ).fetchone()
    if not row:
        conn.close()
        return None
    attachments = conn.execute(
        "SELECT id, file_name, mime_type, size_bytes, stored_filename, created_at "
        "FROM task_attachments WHERE task_id = ? ORDER BY created_at ASC",
        (task_id,)
    ).fetchall()
    conn.close()
    return {
        "id": row[0], "body_html": row[1], "body_text": row[2],
        "created_by": row[3], "assigned_to": row[4], "status": row[5],
        "completed_at": row[6], "completed_by": row[7],
        "task_number": row[8],
        "created_at": row[9], "updated_at": row[10],
        "attachments": [
            {"id": a[0], "file_name": a[1], "mime_type": a[2],
             "size_bytes": a[3], "stored_filename": a[4], "created_at": a[5]}
            for a in attachments
        ],
    }


def tasks_list() -> list:
    """List all tasks newest first, with attachments."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT id FROM tasks ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    return [tasks_get(r[0]) for r in rows]


def tasks_update_status(task_id: str, status: str,
                        completed_by: str = None):
    """Update task status. status='done' sets completed_at + completed_by;
    status='open' clears them. Returns updated task or None if not found."""
    if status not in ("open", "done"):
        raise ValueError(f"Invalid status: {status}")
    now = datetime.now(timezone.utc).isoformat()
    conn = _get_conn()
    if status == "done":
        conn.execute(
            "UPDATE tasks SET status = 'done', completed_at = ?, "
            "completed_by = ?, updated_at = ? WHERE id = ?",
            (now, completed_by, now, task_id)
        )
    else:
        conn.execute(
            "UPDATE tasks SET status = 'open', completed_at = NULL, "
            "completed_by = NULL, updated_at = ? WHERE id = ?",
            (now, task_id)
        )
    conn.commit()
    conn.close()
    return tasks_get(task_id)


def tasks_add_attachment(task_id: str, attachment_id: str, file_name: str,
                         mime_type: str, size_bytes: int,
                         stored_filename: str) -> dict:
    """Insert an attachment row for an existing task. Bumps task's updated_at."""
    now = datetime.now(timezone.utc).isoformat()
    conn = _get_conn()
    conn.execute(
        "INSERT INTO task_attachments (id, task_id, file_name, mime_type, "
        "size_bytes, stored_filename, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (attachment_id, task_id, file_name, mime_type, size_bytes,
         stored_filename, now)
    )
    conn.execute("UPDATE tasks SET updated_at = ? WHERE id = ?", (now, task_id))
    conn.commit()
    conn.close()
    return {
        "id": attachment_id, "file_name": file_name, "mime_type": mime_type,
        "size_bytes": size_bytes, "stored_filename": stored_filename,
        "created_at": now,
    }


# Initialise database on module load so the file exists as soon as the module is imported
_get_conn().close()
