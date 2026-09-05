"""Durable Mermaid customer files, linked to canonical chats and reservations.

Contact numbers and emails are guest details, never an instruction to merge accounts.
All writes share the caller's SQLite transaction with the message/intake write.
"""
from contextlib import closing
from datetime import datetime, timezone
import json

from shared import config_loader, state_registry

IDENTIFIER = "mermaid_conversation_id"
DETAIL_KEYS = (
    "customer_name", "contact_phone", "language", "trip_date", "adults",
    "children", "infants", "child_ages", "pickup_preference", "pickup_location",
    "dietary_requirements", "accessibility_notes", "wheelchair_relationship",
    "special_requests", "phase",
)


def enabled():
    raw = config_loader.get_raw() or {}
    return raw.get("slug") == "mermaid" and bool(
        (raw.get("features") or {}).get("mermaid_customer_accounts")
    )


def _schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS mermaid_customer_intakes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id INTEGER NOT NULL REFERENCES customers(id),
        conversation_id TEXT NOT NULL,
        intake_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mermaid_customer_intakes "
                 "ON mermaid_customer_intakes(customer_id, id DESC)")


def capture(conn, conversation_id, *, intake=None, name="", at=None):
    """Save identity and changed details atomically; no network or model calls."""
    if not conversation_id or not enabled():
        return None
    _schema(conn)
    at = at or datetime.now(timezone.utc).isoformat()
    # Serialize first-use creation across concurrent inbound/provider workers.
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    row = conn.execute("SELECT customer_id FROM customer_identifiers WHERE type=? AND value=?",
                       (IDENTIFIER, conversation_id)).fetchone()
    if row:
        customer_id = row[0]
    else:
        # The social ingress already creates a canonical phone or Zernio
        # conversation identity. Reuse that exact source identifier so the
        # Mermaid intake cannot become an undeletable second customer file.
        canonical = conn.execute(
            "SELECT ci.customer_id FROM customer_identifiers ci "
            "JOIN customers c ON c.id=ci.customer_id "
            "WHERE ci.value=? AND ci.type IN "
            "('phone','wa_conversation_id','conversation_id') AND c.active=1 "
            "ORDER BY c.first_seen,c.id LIMIT 1",
            (conversation_id,),
        ).fetchone()
        customer_id = (
            canonical[0]
            if canonical
            else conn.execute(
                "INSERT INTO customers(display_name,first_seen,last_seen) VALUES(?,?,?)",
                (name, at, at),
            ).lastrowid
        )
        conn.execute("INSERT INTO customer_identifiers(customer_id,type,value,first_seen) VALUES(?,?,?,?)",
                     (customer_id, IDENTIFIER, conversation_id, at))
    conn.execute("UPDATE customers SET first_seen=MIN(first_seen,?), last_seen=MAX(last_seen,?) WHERE id=?",
                 (at, at, customer_id))
    if intake is not None:
        details = {key: intake[key] for key in DETAIL_KEYS if key in intake}
        previous = conn.execute("SELECT intake_json FROM mermaid_customer_intakes "
                                "WHERE customer_id=? ORDER BY id DESC LIMIT 1", (customer_id,)).fetchone()
        # Email belongs to the guest profile, not a particular reservation.
        # Booking snapshots (including older ones replayed during backfill) must
        # never erase or replace an explicitly saved contact address.
        if previous:
            email = json.loads(previous[0]).get("email")
            if email:
                details["email"] = email
        encoded = json.dumps(details, ensure_ascii=False, sort_keys=True)
        if not previous or previous[0] != encoded:
            conn.execute("INSERT INTO mermaid_customer_intakes(customer_id,conversation_id,intake_json,created_at) "
                         "VALUES(?,?,?,?)", (customer_id, conversation_id, encoded, at))
        if details.get("customer_name"):
            conn.execute("UPDATE customers SET display_name=? WHERE id=?", (details["customer_name"], customer_id))
    elif name:
        conn.execute("UPDATE customers SET display_name=? WHERE id=? AND display_name=''", (name, customer_id))
    return customer_id


def set_email(conn, conversation_id, email, at=None):
    """Record a caller-validated address in this guest's profile and history.

    Uses the caller's transaction, never links accounts by email, and leaves
    names and all existing booking details intact. The email workflow owns
    consent and syntax validation before calling this helper.
    """
    if not isinstance(email, str) or not email.strip():
        raise ValueError("A non-empty guest email address is required")
    at = at or datetime.now(timezone.utc).isoformat()
    customer_id = capture(conn, conversation_id, at=at)
    if customer_id is None:
        return None
    previous = conn.execute("SELECT intake_json FROM mermaid_customer_intakes "
                            "WHERE customer_id=? ORDER BY id DESC LIMIT 1", (customer_id,)).fetchone()
    details = json.loads(previous[0]) if previous else {}
    details["email"] = email.strip()
    encoded = json.dumps(details, ensure_ascii=False, sort_keys=True)
    if not previous or previous[0] != encoded:
        conn.execute("INSERT INTO mermaid_customer_intakes(customer_id,conversation_id,intake_json,created_at) "
                     "VALUES(?,?,?,?)", (customer_id, conversation_id, encoded, at))
    return customer_id


def backfill():
    """Explicit, repeatable migration; leaves original chats/booking snapshots intact."""
    if not enabled():
        raise RuntimeError("Mermaid customer accounts are not enabled")
    from agents.social import mermaid_reservation_store as reservations
    with closing(reservations._conn()):
        pass
    with closing(state_registry._get_conn()) as conn, conn:
        _schema(conn)
        for cid, first, last in conn.execute(
            "SELECT phone,MIN(created_at),MAX(created_at) FROM whatsapp_threads GROUP BY phone"
        ).fetchall():
            sender = conn.execute("SELECT sender_name FROM whatsapp_threads WHERE phone=? AND role='user' "
                                  "AND sender_name!='' ORDER BY id DESC LIMIT 1", (cid,)).fetchone()
            capture(conn, cid, name=sender[0] if sender else "", at=first)
            capture(conn, cid, at=last)
        # Seed immutable booking details only when no durable intake exists yet.
        for cid, raw, at in conn.execute(
            "SELECT conversation_id,intake_json,created_at FROM mermaid_reservations ORDER BY created_at"
        ).fetchall():
            customer_id = capture(conn, cid, at=at)
            if not conn.execute("SELECT 1 FROM mermaid_customer_intakes WHERE customer_id=?", (customer_id,)).fetchone():
                capture(conn, cid, intake=json.loads(raw), at=at)
        for cid, raw, at in conn.execute(
            "SELECT phone,fields_json,last_activity FROM whatsapp_booking_state ORDER BY last_activity"
        ).fetchall():
            intake = json.loads(raw).get("mermaid_intake")
            capture(conn, cid, intake=intake, at=at)
        return conn.execute("SELECT COUNT(*) FROM customer_identifiers WHERE type=?", (IDENTIFIER,)).fetchone()[0]


def _base(conn):
    sql = """SELECT c.id, c.display_name, c.first_seen, c.last_seen, ci.value AS conversation_id,
        COALESCE((SELECT intake_json FROM mermaid_customer_intakes i WHERE i.customer_id=c.id ORDER BY i.id DESC LIMIT 1),'{}') AS details_json,
        (SELECT COUNT(*) FROM mermaid_reservations r WHERE r.conversation_id=ci.value AND r.tenant_slug='mermaid') AS reservation_count,
        (SELECT COUNT(*) FROM whatsapp_threads t WHERE t.phone=ci.value) AS message_count
        FROM customers c JOIN customer_identifiers ci ON ci.customer_id=c.id
        WHERE ci.type=? AND c.active=1"""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='mermaid_reservations'").fetchone():
        sql = sql.replace("(SELECT COUNT(*) FROM mermaid_reservations r WHERE r.conversation_id=ci.value AND r.tenant_slug='mermaid')", "0")
    return sql


def _account(row):
    return dict(id=row[0], customerName=row[1], firstSeen=row[2], lastSeen=row[3],
                conversationId=row[4], details=json.loads(row[5]), reservationCount=row[6], messageCount=row[7])


def list_accounts(query="", offset=0, limit=50):
    with closing(state_registry._get_conn()) as conn:
        _schema(conn)
        sql = _base(conn)
        args = [IDENTIFIER]
        if query.strip():
            sql += " AND (c.display_name LIKE ? OR ci.value LIKE ? OR details_json LIKE ?)"
            args += ["%" + query.strip() + "%"] * 3
        rows = conn.execute(sql + " ORDER BY c.last_seen DESC,c.id DESC LIMIT ? OFFSET ?",
                            args + [limit + 1, offset]).fetchall()
        return dict(items=[_account(r) for r in rows[:limit]], nextOffset=offset + limit if len(rows) > limit else None)


def get_account(customer_id):
    with closing(state_registry._get_conn()) as conn:
        _schema(conn)
        row = conn.execute(_base(conn) + " AND c.id=?", (IDENTIFIER, customer_id)).fetchone()
        return _account(row) if row else None


def account_id(conversation_id):
    with closing(state_registry._get_conn()) as conn:
        row = conn.execute("SELECT ci.customer_id FROM customer_identifiers ci JOIN customers c ON c.id=ci.customer_id "
                           "WHERE ci.type=? AND ci.value=? AND c.active=1", (IDENTIFIER, conversation_id)).fetchone()
        return row[0] if row else None


def history(customer_id, before=None, limit=100, *, changes=False):
    account = get_account(customer_id)
    if account is None:
        return None
    with closing(state_registry._get_conn()) as conn:
        if changes:
            sql = "SELECT id,intake_json,created_at FROM mermaid_customer_intakes WHERE customer_id=?"
            args = [customer_id]
        else:
            sql = "SELECT id,role,text,created_at,sender_name,channel FROM whatsapp_threads WHERE phone=?"
            args = [account["conversationId"]]
        if before is not None:
            sql += " AND id<?"
            args.append(before)
        rows = conn.execute(sql + " ORDER BY id DESC LIMIT ?", args + [limit + 1]).fetchall()
        page = rows[:limit]
        items = ([dict(id=r[0], details=json.loads(r[1]), createdAt=r[2]) for r in page] if changes else
                 [dict(id=r[0], role=r[1], text=r[2], created_at=r[3], sender_name=r[4], channel=r[5]) for r in reversed(page)])
        return dict(items=items, nextBefore=page[-1][0] if len(rows) > limit else None)


def reservations_for_account(customer_id):
    account = get_account(customer_id)
    if account is None:
        return []
    from agents.social import mermaid_reservation_store as store
    with closing(store._conn()) as conn:
        return [store._row(r) for r in conn.execute("SELECT * FROM mermaid_reservations "
            "WHERE tenant_slug='mermaid' AND conversation_id=? ORDER BY created_at DESC",
            (account["conversationId"],)).fetchall()]
