"""Durable reservation aggregate for Mermaid's no-money demonstration."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Iterable

from shared import mermaid_catalog, state_registry
from shared.mermaid_contact import normalize_contact_phone


class MermaidReservationError(RuntimeError):
    pass


class MermaidCancellationReviewRequired(MermaidReservationError):
    """The authoritative reservation cannot be cancelled automatically."""

    def __init__(self, reservation: dict, reason: str):
        self.reservation = reservation
        self.reason = reason
        super().__init__(reason)


TERMINAL_STATES = {"cancelled", "booked"}
SUMMARY_VERSION_KEY = "_reservation_summary_version"
_schema_lock = threading.Lock()
TRANSITIONS = {
    "demo_availability_approved": {"quote_ready", "cancelled"},
    "quote_ready": {"demo_payment_pending", "cancelled"},
    "demo_payment_pending": {"demo_paid", "cancelled"},
    "demo_paid": {"booked"},
    "booked": set(),
    "cancelled": set(),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(state_registry.DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        # Concurrent first-use callbacks must not race the journal-mode switch.
        # Only schema initialization is serialized; reservation transactions
        # retain their existing SQLite uniqueness and transactional authority.
        with _schema_lock:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            _ensure_schema(conn)
    except Exception:
        conn.close()
        raise
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS mermaid_reservations (
            public_id TEXT PRIMARY KEY,
            tenant_slug TEXT NOT NULL CHECK (tenant_slug = 'mermaid'),
            conversation_id TEXT NOT NULL,
            zernio_account_id TEXT NOT NULL DEFAULT '',
            summary_version TEXT NOT NULL,
            customer_name TEXT NOT NULL,
            language TEXT NOT NULL,
            intake_json TEXT NOT NULL,
            catalog_version TEXT NOT NULL,
            monetary_snapshot_json TEXT NOT NULL,
            state TEXT NOT NULL,
            revision INTEGER NOT NULL DEFAULT 1,
            availability_source TEXT NOT NULL CHECK (availability_source = 'demo_assumed'),
            booking_code TEXT NOT NULL UNIQUE,
            quote_public_id TEXT,
            payment_reference TEXT,
            receipt_public_id TEXT,
            human_takeover INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (tenant_slug, conversation_id, summary_version)
        );
        CREATE INDEX IF NOT EXISTS idx_mermaid_reservation_conversation
          ON mermaid_reservations(tenant_slug, conversation_id, updated_at DESC);
        CREATE TABLE IF NOT EXISTS mermaid_reservation_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reservation_public_id TEXT NOT NULL,
            tenant_slug TEXT NOT NULL,
            event_type TEXT NOT NULL,
            from_state TEXT,
            to_state TEXT,
            actor TEXT NOT NULL,
            reason TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            revision INTEGER NOT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            UNIQUE (tenant_slug, idempotency_key),
            FOREIGN KEY (reservation_public_id) REFERENCES mermaid_reservations(public_id)
        );
        CREATE TABLE IF NOT EXISTS mermaid_demo_payments (
            public_id TEXT PRIMARY KEY,
            tenant_slug TEXT NOT NULL CHECK (tenant_slug='mermaid'),
            reservation_public_id TEXT NOT NULL UNIQUE,
            payment_reference TEXT NOT NULL UNIQUE,
            idempotency_key TEXT NOT NULL UNIQUE,
            amount INTEGER NOT NULL,
            currency TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status='simulated_success'),
            paid_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS mermaid_checkout_links (
            token_hash TEXT PRIMARY KEY,
            tenant_slug TEXT NOT NULL CHECK (tenant_slug='mermaid'),
            reservation_public_id TEXT NOT NULL,
            expires_at INTEGER NOT NULL,
            created_at INTEGER NOT NULL,
            FOREIGN KEY (reservation_public_id) REFERENCES mermaid_reservations(public_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_mermaid_checkout_link_expiry ON mermaid_checkout_links(expires_at);
        """
    )
    try:
        conn.execute("ALTER TABLE mermaid_reservations ADD COLUMN zernio_account_id TEXT NOT NULL DEFAULT ''")
    except sqlite3.OperationalError:
        pass


def _row(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    value = dict(row)
    for key in ("intake_json", "monetary_snapshot_json"):
        value[key.removesuffix("_json")] = json.loads(value.pop(key))
    value["human_takeover"] = bool(value["human_takeover"])
    return value


def _summary_version(intake: dict, *, booking_generation: str = "") -> str:
    owned = {
        key: intake.get(key)
        for key in (
            "trip_date", "adults", "children", "infants", "customer_name",
            "pickup_preference", "pickup_location", "dietary_requirements",
            "accessibility_notes", "special_requests", "language",
        )
    }
    # Keep the legacy idempotency identity byte-for-byte compatible even
    # though private assistance fields are no longer copied into intake_json.
    if "wheelchair_relationship" in intake:
        owned["wheelchair_relationship"] = intake["wheelchair_relationship"]
    # Omit the absent field to preserve identity for pre-contact reservations.
    if "contact_phone" in intake:
        owned["contact_phone"] = intake["contact_phone"]
    if intake.get("child_ages"):
        owned["child_ages"] = intake["child_ages"]
    if booking_generation:
        # A provider-stable booking generation distinguishes an explicitly new
        # booking whose customer-owned details happen to match an earlier one.
        # Empty generations retain the pre-upgrade identity byte-for-byte.
        owned["_booking_generation"] = str(booking_generation)
    encoded = json.dumps(owned, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _money_snapshot(intake: dict, catalog: dict) -> dict:
    currency = str(intake.get("currency") or catalog["pricing"]["default_currency"])
    prices = catalog["pricing"]["currencies"].get(currency)
    if not prices:
        raise MermaidReservationError("unsupported reservation currency")
    quantities = {
        "adult": int(intake.get("adults") or 0),
        "child_4_12": int(intake.get("children") or 0),
        "infant_0_3": int(intake.get("infants") or 0),
    }
    items = []
    total = 0
    for key, label in (
        ("adult", "Adult"),
        ("child_4_12", "Child age 4-12"),
        ("infant_0_3", "Child age 0-3"),
    ):
        unit = int(prices[key])
        quantity = quantities[key]
        line_total = unit * quantity
        total += line_total
        items.append({
            "key": key, "label": label, "quantity": quantity,
            "unit_amount": unit, "line_total": line_total,
        })
    pickup_amount = None
    pickup_plan = None
    if intake.get("pickup_preference") == "pickup_requested":
        pickup_plan = (
            mermaid_catalog.pickup_quote(sum(quantities.values()), catalog)
            if all(key in intake for key in ("adults", "children", "infants"))
            else {"status": "awaiting_guest_count"}
        )
        if pickup_plan["status"] == "quoted":
            pickup_amount = pickup_plan["amount"]
            if currency != catalog["pricing"]["pickup_currency"]:
                raise MermaidReservationError("pickup is unavailable in this currency; no conversion rate configured")
            items.append({
                "key": "pickup", "label": "Pickup", "quantity": pickup_plan["quantity"],
                "unit_amount": pickup_plan["unit_amount"], "line_total": pickup_amount,
            })
            total += pickup_amount
    return {
        "currency": currency,
        "items": items,
        "total": total,
        "pickup_amount": pickup_amount,
        "pickup_plan": pickup_plan,
        "catalog_version": catalog["version"],
    }


def _booking_code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "MER-" + "".join(secrets.choice(alphabet) for _ in range(8))


def confirm_reservation(
    conversation_id: str,
    intake: dict,
    *,
    idempotency_key: str,
    actor: str = "tracy",
    zernio_account_id: str = "",
    assistance_session_owned: bool = False,
) -> dict:
    """Create exactly one assumed-available reservation per confirmed summary."""
    from agents.social.isluno_transition import blocked
    if blocked():raise MermaidReservationError("Legacy action quarantined for operator review")
    required = {"trip_date", "adults", "children", "infants", "customer_name", "pickup_preference", "language"}
    if not required.issubset(intake):
        raise MermaidReservationError("confirmed intake is incomplete")
    if intake.get("phase") != "summary_confirmed":
        raise MermaidReservationError("summary is not confirmed")
    intake = dict(intake)
    replay_summary_version = str(intake.pop(SUMMARY_VERSION_KEY, "") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", replay_summary_version):
        replay_summary_version = ""
    contact = normalize_contact_phone(intake.get("contact_phone"))
    if contact:
        intake["contact_phone"] = contact
    catalog = mermaid_catalog.get_catalog()
    from shared.mermaid_guest_ages import normalize_child_ages
    if "child_ages" in intake:
        ages = normalize_child_ages(intake["child_ages"], intake)
        if ages is None:
            raise MermaidReservationError("Child ages must match the supplied guest counts")
        intake = {**intake, "child_ages": ages}
    from agents.social import mermaid_crew_assistance
    assistance_session_started_at = (
        mermaid_crew_assistance._conversation_session_started_at(conversation_id)
    )
    accessibility_note = str(intake.get("accessibility_notes") or "")
    relationship_value = intake.get("wheelchair_relationship")
    private_assistance = bool(
        accessibility_note
        and (
            relationship_value in {"husband", "other", "unspecified"}
            or any(
                marker in accessibility_note.casefold()
                for marker in (
                    "wheelchair", "wheel chair", "rolstoel", "rollstuhl",
                    "silla de ruedas", "cadeira de rodas", "stul di rueda",
                )
            )
        )
    )
    version = _summary_version(
        intake,
        booking_generation=(
            assistance_session_started_at if assistance_session_owned else ""
        ),
    )
    if private_assistance:
        existing_assistance_owner = ""
        owner_conn = _conn()
        try:
            owner_row = owner_conn.execute(
                "SELECT public_id FROM mermaid_reservations "
                "WHERE tenant_slug='mermaid' AND conversation_id=? "
                "AND summary_version=?",
                (conversation_id, version),
            ).fetchone()
            if owner_row is not None:
                existing_assistance_owner = str(owner_row["public_id"] or "")
        finally:
            owner_conn.close()
        # Migrate a pre-feature saved intake before removing its private fields
        # from the reservation snapshot. An explicit private fact on a changed
        # summary also reasserts wheelchair ownership for that corrected
        # reservation. Failure aborts confirmation rather than discarding it.
        relationship = str(relationship_value or "unspecified")
        if relationship not in {"husband", "other", "unspecified"}:
            relationship = "unspecified"
        mermaid_crew_assistance.record_wheelchair_note(
            conversation_id,
            note=accessibility_note,
            relationship=relationship,
            trip_date=str(intake.get("trip_date") or ""),
            customer_name=str(intake.get("customer_name") or ""),
            source_message_id=f"legacy-confirm:{conversation_id}:{version}",
            reservation_public_id=existing_assistance_owner,
        )
    reassign_kinds = (
        (mermaid_crew_assistance.KIND_WHEELCHAIR,)
        if private_assistance
        else ()
    )
    stored_intake = {
        key: value
        for key, value in intake.items()
        if not (
            private_assistance
            and key in {"accessibility_notes", "wheelchair_relationship"}
        )
    }
    # Keep only the one-way reservation identity after moving the private
    # assistance details to the crew queue.  A caller replaying the returned
    # reservation can then recover the original legacy hash without storing
    # the wheelchair fact in the reservation snapshot.
    stored_intake[SUMMARY_VERSION_KEY] = version
    now = _now()
    conn = _conn()
    # The assistance table is private operational state. Initialize it before
    # the reservation transaction so schema DDL cannot split that transaction.
    mermaid_crew_assistance._ensure_schema(conn)
    try:
        conn.execute("BEGIN IMMEDIATE")
        if replay_summary_version:
            replay_row = conn.execute(
                "SELECT * FROM mermaid_reservations WHERE tenant_slug='mermaid' "
                "AND conversation_id=? AND summary_version=?",
                (conversation_id, replay_summary_version),
            ).fetchone()
            if replay_row:
                replay_intake = json.loads(replay_row["intake_json"] or "{}")
                replay_intake.pop(SUMMARY_VERSION_KEY, None)
                supplied_public = dict(stored_intake)
                supplied_public.pop(SUMMARY_VERSION_KEY, None)
                if replay_intake == supplied_public:
                    mermaid_crew_assistance.link_reservation(
                        conn,
                        conversation_id,
                        replay_row["public_id"],
                        idempotency_key=idempotency_key
                        or f"confirm:{conversation_id}:{replay_summary_version}",
                        trip_date=str(intake.get("trip_date") or ""),
                        session_started_at=assistance_session_started_at,
                        reassign_kinds=reassign_kinds,
                        allow_session_reassignment=assistance_session_owned,
                    )
                    conn.commit()
                    return get_reservation(replay_row["public_id"], connection=conn)
        existing = conn.execute(
            "SELECT * FROM mermaid_reservations WHERE tenant_slug='mermaid' "
            "AND conversation_id=? AND summary_version=?",
            (conversation_id, version),
        ).fetchone()
        if existing:
            if private_assistance:
                existing_intake = json.loads(existing["intake_json"] or "{}")
                if any(
                    key in existing_intake
                    for key in ("accessibility_notes", "wheelchair_relationship")
                ):
                    migrated_revision = int(existing["revision"]) + 1
                    conn.execute(
                        "UPDATE mermaid_reservations SET intake_json=?,revision=?,updated_at=? "
                        "WHERE public_id=?",
                        (
                            json.dumps(stored_intake, ensure_ascii=False, sort_keys=True),
                            migrated_revision,
                            now,
                            existing["public_id"],
                        ),
                    )
                    conn.execute(
                        "INSERT OR IGNORE INTO mermaid_reservation_events "
                        "(reservation_public_id,tenant_slug,event_type,from_state,to_state,"
                        "actor,reason,idempotency_key,revision,payload_json,created_at) "
                        "VALUES (?,'mermaid','crew_assistance_migrated',?,?,"
                        "'system','Private assistance moved to the crew queue',?,?, '{}',?)",
                        (
                            existing["public_id"],
                            existing["state"],
                            existing["state"],
                            f"crew-assistance-migrate:{existing['public_id']}",
                            migrated_revision,
                            now,
                        ),
                    )
            mermaid_crew_assistance.link_reservation(
                conn,
                conversation_id,
                existing["public_id"],
                idempotency_key=idempotency_key or f"confirm:{conversation_id}:{version}",
                trip_date=str(intake.get("trip_date") or ""),
                session_started_at=assistance_session_started_at,
                reassign_kinds=reassign_kinds,
                allow_session_reassignment=assistance_session_owned,
            )
            existing = conn.execute(
                "SELECT * FROM mermaid_reservations WHERE public_id=?",
                (existing["public_id"],),
            ).fetchone()
            conn.commit()
            return get_reservation(existing["public_id"], connection=conn)
        if _operator_review_active(conn, conversation_id):
            raise MermaidReservationError("reservation is frozen for human takeover")
        if not contact:
            raise MermaidReservationError("a customer-supplied international contact number is required")
        public_id = "mer_" + uuid.uuid4().hex
        snapshot = _money_snapshot(intake, catalog)
        if (snapshot.get("pickup_plan") or {}).get("status") in {"requires_review", "awaiting_guest_count"}:
            raise MermaidReservationError("pickup requires review or a complete passenger count")
        for _ in range(10):
            code = _booking_code()
            try:
                conn.execute(
                    "INSERT INTO mermaid_reservations (public_id, tenant_slug, conversation_id, "
                    "summary_version, customer_name, language, intake_json, catalog_version, "
                    "monetary_snapshot_json, state, revision, availability_source, booking_code, "
                    "zernio_account_id, created_at, updated_at) VALUES (?, 'mermaid', ?, ?, ?, ?, ?, ?, ?, "
                    "'demo_availability_approved', 1, 'demo_assumed', ?, ?, ?, ?)",
                    (
                        public_id, conversation_id, version, intake["customer_name"], intake["language"],
                        json.dumps(stored_intake, ensure_ascii=False, sort_keys=True), catalog["version"],
                        json.dumps(snapshot, ensure_ascii=False, sort_keys=True), code,
                        str(zernio_account_id or ""), now, now,
                    ),
                )
                break
            except sqlite3.IntegrityError as exc:
                if "booking_code" not in str(exc):
                    raise
        else:
            raise MermaidReservationError("unable to allocate unique booking code")
        conn.execute(
            "INSERT INTO mermaid_reservation_events (reservation_public_id, tenant_slug, event_type, "
            "from_state, to_state, actor, reason, idempotency_key, revision, payload_json, created_at) "
            "VALUES (?, 'mermaid', 'summary_confirmed', NULL, 'demo_availability_approved', ?, ?, ?, 1, ?, ?)",
            (
                public_id, actor, "Demo availability assumed; no inventory provider called",
                idempotency_key or f"confirm:{conversation_id}:{version}",
                json.dumps({"availability_source": "demo_assumed", "catalog_version": catalog["version"]}), now,
            ),
        )
        mermaid_crew_assistance.link_reservation(
            conn,
            conversation_id,
            public_id,
            idempotency_key=idempotency_key or f"confirm:{conversation_id}:{version}",
            trip_date=str(intake.get("trip_date") or ""),
            session_started_at=assistance_session_started_at,
            reassign_kinds=reassign_kinds,
            allow_session_reassignment=assistance_session_owned,
        )
        conn.commit()
        return get_reservation(public_id, connection=conn)
    except sqlite3.IntegrityError:
        conn.rollback()
        existing = conn.execute(
            "SELECT * FROM mermaid_reservations WHERE tenant_slug='mermaid' "
            "AND conversation_id=? AND summary_version=?",
            (conversation_id, version),
        ).fetchone()
        if existing:
            return _row(existing)
        raise
    finally:
        conn.close()


def get_reservation(public_id: str, *, connection: sqlite3.Connection | None = None) -> dict | None:
    owns = connection is None
    conn = connection or _conn()
    try:
        return _row(conn.execute(
            "SELECT * FROM mermaid_reservations WHERE tenant_slug='mermaid' AND public_id=?",
            (public_id,),
        ).fetchone())
    finally:
        if owns:
            conn.close()


def latest_for_conversation(conversation_id: str) -> dict | None:
    conn = _conn()
    try:
        return _row(conn.execute(
            "SELECT * FROM mermaid_reservations WHERE tenant_slug='mermaid' AND conversation_id=? "
            "ORDER BY created_at DESC LIMIT 1",
            (conversation_id,),
        ).fetchone())
    finally:
        conn.close()


def list_reservations(limit: int = 100) -> list[dict]:
    conn = _conn()
    try:
        return [_row(row) for row in conn.execute(
            "SELECT * FROM mermaid_reservations WHERE tenant_slug='mermaid' "
            "ORDER BY updated_at DESC LIMIT ?", (max(1, min(int(limit), 500)),)
        ).fetchall()]
    finally:
        conn.close()


def transition(
    public_id: str,
    to_state: str,
    *,
    idempotency_key: str,
    actor: str,
    reason: str,
    updates: dict | None = None,
) -> dict:
    """Apply one optimistic, replay-safe transition with an audit event."""
    conn = _conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        replay = conn.execute(
            "SELECT reservation_public_id FROM mermaid_reservation_events "
            "WHERE tenant_slug='mermaid' AND idempotency_key=?", (idempotency_key,)
        ).fetchone()
        if replay:
            conn.commit()
            return get_reservation(replay[0], connection=conn)
        row = conn.execute(
            "SELECT * FROM mermaid_reservations WHERE tenant_slug='mermaid' AND public_id=?",
            (public_id,),
        ).fetchone()
        if row is None:
            raise MermaidReservationError("reservation not found")
        current = _row(row)
        if current["human_takeover"] or _operator_review_active(
            conn, current["conversation_id"]
        ):
            raise MermaidReservationError("reservation is frozen for human takeover")
        from_state = current["state"]
        if to_state not in TRANSITIONS.get(from_state, set()):
            raise MermaidReservationError(f"invalid transition {from_state} -> {to_state}")
        allowed_updates = {"quote_public_id", "payment_reference", "receipt_public_id"}
        clean_updates = {k: v for k, v in (updates or {}).items() if k in allowed_updates}
        revision = int(current["revision"]) + 1
        now = _now()
        assignments = ["state=?", "revision=?", "updated_at=?"]
        values: list = [to_state, revision, now]
        for key, value in clean_updates.items():
            assignments.append(f"{key}=?")
            values.append(value)
        values.extend([public_id, int(current["revision"])])
        changed = conn.execute(
            f"UPDATE mermaid_reservations SET {', '.join(assignments)} "
            "WHERE tenant_slug='mermaid' AND public_id=? AND revision=?",
            values,
        ).rowcount
        if changed != 1:
            raise MermaidReservationError("concurrent reservation update")
        conn.execute(
            "INSERT INTO mermaid_reservation_events (reservation_public_id, tenant_slug, event_type, "
            "from_state, to_state, actor, reason, idempotency_key, revision, payload_json, created_at) "
            "VALUES (?, 'mermaid', 'state_transition', ?, ?, ?, ?, ?, ?, ?, ?)",
            (public_id, from_state, to_state, actor, reason, idempotency_key, revision,
             json.dumps(clean_updates, ensure_ascii=False, sort_keys=True), now),
        )
        conn.commit()
        return get_reservation(public_id, connection=conn)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _operator_review_active(conn: sqlite3.Connection, conversation_id: str) -> bool:
    """Read operator/review authority inside a reservation write transaction."""
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('conversation_status', 'pending_notifications')"
    ).fetchall()}
    if "conversation_status" in tables and conn.execute(
        "SELECT 1 FROM conversation_status WHERE conversation_id=? AND ai_muted=1",
        (conversation_id,),
    ).fetchone():
        return True
    return bool("pending_notifications" in tables and conn.execute(
        "SELECT 1 FROM pending_notifications WHERE customer_id=? "
        "AND notification_type IN ('escalation', 'relay') "
        "AND status IN ('pending', 'sent') "
        "AND (mode IS NULL OR mode IN ('soft', 'hard')) LIMIT 1",
        (conversation_id,),
    ).fetchone())


def operator_review_active(conversation_id: str) -> bool:
    conn = _conn()
    try:
        return _operator_review_active(conn, conversation_id)
    finally:
        conn.close()


def cancel(public_id: str, *, idempotency_key: str, actor: str = "customer") -> dict:
    """Cancel and revoke checkout tokens in the payment writer's transaction order."""
    conn = _conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        current = get_reservation(public_id, connection=conn)
        if current is None:
            raise MermaidReservationError("reservation not found")
        paid = conn.execute(
            "SELECT 1 FROM mermaid_demo_payments WHERE tenant_slug='mermaid' AND reservation_public_id=?",
            (public_id,),
        ).fetchone()
        if paid or current["payment_reference"] or current["state"] in {"demo_paid", "booked"}:
            raise MermaidCancellationReviewRequired(current, "paid demo reservation cannot be cancelled automatically")
        if current["human_takeover"] or _operator_review_active(conn, current["conversation_id"]):
            raise MermaidCancellationReviewRequired(current, "reservation is frozen for human takeover")
        if current["state"] != "cancelled":
            if "cancelled" not in TRANSITIONS.get(current["state"], set()):
                raise MermaidReservationError(f"invalid transition {current['state']} -> cancelled")
            revision, now = int(current["revision"]) + 1, _now()
            conn.execute(
                "UPDATE mermaid_reservations SET state='cancelled', revision=?, updated_at=? "
                "WHERE tenant_slug='mermaid' AND public_id=?",
                (revision, now, public_id),
            )
            conn.execute(
                "INSERT INTO mermaid_reservation_events (reservation_public_id, tenant_slug, event_type, "
                "from_state, to_state, actor, reason, idempotency_key, revision, payload_json, created_at) "
                "VALUES (?, 'mermaid', 'state_transition', ?, 'cancelled', ?, "
                "'Cancelled before simulated payment; checkout tokens revoked', ?, ?, '{}', ?)",
                (public_id, current["state"], actor, idempotency_key, revision, now),
            )
        conn.execute(
            "DELETE FROM mermaid_checkout_links WHERE tenant_slug='mermaid' AND reservation_public_id=?",
            (public_id,),
        )
        conn.commit()
        return get_reservation(public_id, connection=conn)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def freeze_for_human(public_id: str) -> dict:
    conn = _conn()
    try:
        conn.execute(
            "UPDATE mermaid_reservations SET human_takeover=1, updated_at=? "
            "WHERE tenant_slug='mermaid' AND public_id=?", (_now(), public_id)
        )
        conn.commit()
        result = get_reservation(public_id, connection=conn)
        if result is None:
            raise MermaidReservationError("reservation not found")
        return result
    finally:
        conn.close()


def complete_demo_payment(
    public_id: str,
    *,
    payment_reference: str,
    idempotency_key: str,
    actor: str = "demo_checkout",
) -> tuple[dict, dict]:
    """Atomically record the simulated payment and the paid/booked transitions."""
    from agents.social.isluno_transition import blocked
    if blocked():raise MermaidReservationError("Legacy action quarantined for operator review")
    conn = _conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        replay = conn.execute(
            "SELECT * FROM mermaid_demo_payments WHERE tenant_slug='mermaid' AND idempotency_key=?",
            (idempotency_key,),
        ).fetchone()
        if replay:
            reservation = conn.execute(
                "SELECT * FROM mermaid_reservations WHERE tenant_slug='mermaid' AND public_id=?",
                (replay["reservation_public_id"],),
            ).fetchone()
            conn.commit()
            return _row(reservation), dict(replay)
        row = conn.execute(
            "SELECT * FROM mermaid_reservations WHERE tenant_slug='mermaid' AND public_id=?",
            (public_id,),
        ).fetchone()
        if row is None:
            raise MermaidReservationError("reservation not found")
        reservation = _row(row)
        if reservation["human_takeover"] or _operator_review_active(
            conn, reservation["conversation_id"]
        ):
            raise MermaidReservationError("reservation is frozen for human takeover")
        if reservation["state"] == "booked":
            payment = conn.execute(
                "SELECT * FROM mermaid_demo_payments WHERE tenant_slug='mermaid' AND reservation_public_id=?",
                (public_id,),
            ).fetchone()
            if payment:
                conn.commit()
                return reservation, dict(payment)
        if reservation["state"] != "demo_payment_pending":
            raise MermaidReservationError(
                f"invalid transition {reservation['state']} -> demo_paid"
            )
        paid_at = _now()
        money = reservation["monetary_snapshot"]
        payment_id = "mpay_" + uuid.uuid4().hex
        conn.execute(
            "INSERT INTO mermaid_demo_payments (public_id, tenant_slug, reservation_public_id, "
            "payment_reference, idempotency_key, amount, currency, status, paid_at) "
            "VALUES (?, 'mermaid', ?, ?, ?, ?, ?, 'simulated_success', ?)",
            (payment_id, public_id, payment_reference, idempotency_key,
             int(money["total"]), money["currency"], paid_at),
        )
        first_revision = int(reservation["revision"]) + 1
        final_revision = first_revision + 1
        changed = conn.execute(
            "UPDATE mermaid_reservations SET state='booked', revision=?, payment_reference=?, updated_at=? "
            "WHERE tenant_slug='mermaid' AND public_id=? AND revision=?",
            (final_revision, payment_reference, paid_at, public_id, reservation["revision"]),
        ).rowcount
        if changed != 1:
            raise MermaidReservationError("concurrent reservation update")
        conn.execute(
            "INSERT INTO mermaid_reservation_events (reservation_public_id, tenant_slug, event_type, "
            "from_state, to_state, actor, reason, idempotency_key, revision, payload_json, created_at) "
            "VALUES (?, 'mermaid', 'state_transition', 'demo_payment_pending', 'demo_paid', ?, "
            "'Signed demo payment callback verified', ?, ?, ?, ?)",
            (public_id, actor, idempotency_key + ":paid", first_revision,
             json.dumps({"payment_reference": payment_reference}), paid_at),
        )
        conn.execute(
            "INSERT INTO mermaid_reservation_events (reservation_public_id, tenant_slug, event_type, "
            "from_state, to_state, actor, reason, idempotency_key, revision, payload_json, created_at) "
            "VALUES (?, 'mermaid', 'state_transition', 'demo_paid', 'booked', ?, "
            "'Demo booking completed', ?, ?, '{}', ?)",
            (public_id, actor, idempotency_key + ":booked", final_revision, paid_at),
        )
        conn.commit()
        updated = conn.execute(
            "SELECT * FROM mermaid_reservations WHERE tenant_slug='mermaid' AND public_id=?",
            (public_id,),
        ).fetchone()
        payment = conn.execute(
            "SELECT * FROM mermaid_demo_payments WHERE tenant_slug='mermaid' AND public_id=?",
            (payment_id,),
        ).fetchone()
        return _row(updated), dict(payment)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def attach_receipt(public_id: str, receipt_public_id: str) -> dict:
    conn = _conn()
    try:
        conn.execute(
            "UPDATE mermaid_reservations SET receipt_public_id=?, updated_at=? "
            "WHERE tenant_slug='mermaid' AND public_id=? AND state='booked' "
            "AND (receipt_public_id IS NULL OR receipt_public_id=?)",
            (receipt_public_id, _now(), public_id, receipt_public_id),
        )
        conn.commit()
        result = get_reservation(public_id, connection=conn)
        if result is None:
            raise MermaidReservationError("reservation not found")
        return result
    finally:
        conn.close()


def events(public_id: str) -> list[dict]:
    conn = _conn()
    try:
        return [dict(row) for row in conn.execute(
            "SELECT * FROM mermaid_reservation_events WHERE tenant_slug='mermaid' "
            "AND reservation_public_id=? ORDER BY id", (public_id,)
        ).fetchall()]
    finally:
        conn.close()


def revise_unpaid_quote(public_id, intake, *, expected_revision, conversation_id, account_id, idempotency_key):
    """Version an unpaid quote atomically; retain its old PDF and audit history."""
    conn = _conn()
    try:
        conn.execute('BEGIN IMMEDIATE')
        current = get_reservation(public_id, connection=conn)
        if (not current or current['conversation_id'] != conversation_id
                or current['zernio_account_id'] != account_id or current['state'] != 'quote_ready'
                or current['human_takeover'] or _operator_review_active(conn, conversation_id)):
            raise MermaidReservationError('Quote cannot be revised')
        replay = conn.execute("SELECT 1 FROM mermaid_reservation_events WHERE tenant_slug='mermaid' AND idempotency_key=? AND reservation_public_id=?", (idempotency_key, public_id)).fetchone()
        if replay:
            conn.commit()
            return current
        if current['revision'] != expected_revision:
            raise MermaidReservationError('Quote changed before correction')
        from shared.mermaid_guest_ages import normalize_child_ages
        intake = dict(intake)
        if intake.get('child_ages') and normalize_child_ages(intake['child_ages'], intake) is None:
            raise MermaidReservationError('Invalid guest ages')
        revision = current['revision'] + 1
        intake['_quote_revision'] = revision
        money = _money_snapshot(intake, mermaid_catalog.get_catalog())
        now = _now()
        conn.execute("UPDATE mermaid_reservations SET intake_json=?,monetary_snapshot_json=?,customer_name=?,language=?,quote_public_id=NULL,revision=?,updated_at=? WHERE public_id=? AND tenant_slug='mermaid'", (json.dumps(intake,ensure_ascii=False),json.dumps(money),intake['customer_name'],intake['language'],revision,now,public_id))
        conn.execute("DELETE FROM mermaid_checkout_links WHERE reservation_public_id=? AND tenant_slug='mermaid'", (public_id,))
        changed = {k: {'before': current['intake'].get(k), 'after': v} for k,v in intake.items() if not k.startswith('_') and k != 'phase' and current['intake'].get(k) != v}
        conn.execute("INSERT INTO mermaid_reservation_events (reservation_public_id,tenant_slug,event_type,from_state,to_state,actor,reason,idempotency_key,revision,payload_json,created_at) VALUES (?,'mermaid','quote_revised','quote_ready','quote_ready','customer','Customer requested quote corrections',?,?,?,?)", (public_id,idempotency_key,revision,json.dumps({'changes':changed,'previous_document':current.get('quote_public_id')},ensure_ascii=False),now))
        conn.commit()
        return get_reservation(public_id)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def attach_revised_quote(public_id, document_id, expected_revision):
    conn = _conn()
    try:
        with conn:
            changed = conn.execute("UPDATE mermaid_reservations SET quote_public_id=?,updated_at=? WHERE public_id=? AND tenant_slug='mermaid' AND state='quote_ready' AND revision=? AND human_takeover=0 AND (quote_public_id IS NULL OR quote_public_id=?)", (document_id,_now(),public_id,expected_revision,document_id)).rowcount
            if changed != 1:
                raise MermaidReservationError('Revised quote is no longer current')
        return get_reservation(public_id)
    finally:
        conn.close()
