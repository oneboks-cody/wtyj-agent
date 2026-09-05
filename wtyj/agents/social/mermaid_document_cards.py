"""Mermaid's image + Open PDF messages, with durable payloads and delivery evidence."""
import hashlib
import hmac
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urlsplit, quote

from fastapi.responses import FileResponse, Response
from shared import bm_logger, config_loader, mermaid_catalog


def settings():
    return (config_loader.get_raw() or {}).get("mermaid_document_cards") or {}


def enabled():
    return mermaid_catalog.reservation_demo_enabled() and settings().get("enabled") is True


def copy_for(locale):
    copies = settings()["copies"]
    return copies.get(locale, copies["en"])


def transport_lines(reservation):
    from agents.social import mermaid_guest_experience as guest
    intake, money = reservation["intake"], reservation["monetary_snapshot"]
    locale = reservation["language"]
    copy = copy_for(locale)
    if intake.get("pickup_preference") != "pickup_requested" or money.get("pickup_amount") is None:
        return guest.transport_text(intake, locale, money)
    lines = [copy["pickup_at"].format(time=mermaid_catalog.pickup_time()),
             intake.get("pickup_location") or guest.guest_copy(locale)["hotel"]]
    if mermaid_catalog.get_catalog()["pricing"].get("pickup_journey") == "round_trip":
        lines.append(copy["return_included"])
    return "\n".join(lines)


def quote_text(reservation):
    from agents.social import mermaid_guest_experience as guest
    locale, intake = reservation["language"], reservation["intake"]
    return "\n\n".join([
        copy_for(locale)["quote_ready"],
        guest.guest_date(intake["trip_date"], locale) + "\n" + guest.party_text(intake, locale),
        guest.price_text(reservation["monetary_snapshot"], intake, locale),
        transport_lines(reservation),
    ])


def receipt_text(reservation, payment):
    from agents.social import mermaid_guest_experience as guest
    from agents.social import mermaid_reservation_email as email
    locale, intake = reservation["language"], reservation["intake"]
    copy = copy_for(locale)
    name = (reservation.get("customer_name") or "").split()
    return "\n\n".join([
        copy["welcome"].format(name=name[0][:40] if name else copy["guest"]),
        guest.guest_date(intake["trip_date"], locale) + "\n" + guest.party_text(intake, locale),
        f"{guest.guest_copy(locale)['paid']}: {payment['currency']} {int(payment['amount']):,.2f}",
        transport_lines(reservation),
        f"{copy['booking_code']}: {guest.display_reference(reservation['booking_code'])}",
        email.copy(locale, 'paid_closing') if email.offer(reservation) else copy["closing"],
    ])


def _signature(public_id, expires, secret):
    return hmac.new(secret.encode(), f"mermaid-card:{public_id}:{int(expires)}".encode(), hashlib.sha256).hexdigest()


def download_url(base, document, reservation, now=None):
    now = int(time.time() if now is None else now)
    secret = os.environ.get("MERMAID_DEMO_SIGNING_SECRET", "")
    if not secret:
        raise ValueError("Document signing is not configured")
    trip = datetime.strptime(reservation["intake"]["trip_date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    # Separate signing scope: the existing one-hour attachment/checkout grants remain unchanged.
    expires = max(now + 30 * 86400, int((trip + timedelta(days=31)).timestamp()))
    signature = _signature(document["public_id"], expires, secret)
    return f"{base}/api/public/mermaid-document/{document['public_id']}?" + urlencode({"expires": expires, "signature": signature, "card": "true"})


def download_response(public_id, expires, signature):
    from agents.social import mermaid_documents as docs
    secret = os.environ.get("MERMAID_DEMO_SIGNING_SECRET", "")
    if not mermaid_catalog.reservation_demo_enabled() or not secret or int(time.time()) > expires:
        return Response(status_code=404)
    if not hmac.compare_digest(_signature(public_id, expires, secret), str(signature or "")):
        return Response(status_code=404)
    return docs.stored_document_response(public_id)


def image_response(digest):
    from agents.social.mermaid_documents import HERO_IMAGE
    if not enabled() or digest != hashlib.sha256(HERO_IMAGE.read_bytes()).hexdigest():
        return Response(status_code=404)
    return FileResponse(HERO_IMAGE, media_type="image/png", headers={"Cache-Control": "public, max-age=86400", "X-Content-Type-Options": "nosniff"})


def _conn():
    from agents.social import mermaid_documents as docs
    conn = docs._conn()
    conn.execute("""CREATE TABLE IF NOT EXISTS mermaid_card_deliveries (
        action_key TEXT PRIMARY KEY, document_public_id TEXT NOT NULL,
        conversation_id TEXT NOT NULL, account_id TEXT NOT NULL,
        payload_json TEXT NOT NULL, provider_message_id TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'prepared', created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL)""")
    return conn


def records(document_id, conversation_id, account_id):
    conn = _conn()
    try:
        return [dict(row) for row in conn.execute(
            "SELECT * FROM mermaid_card_deliveries WHERE document_public_id=? AND conversation_id=? AND account_id=?",
            (document_id, conversation_id, account_id))]
    finally:
        conn.close()


def try_send(conversation_id, account_id, text, attachment_url, idempotency_key):
    """Return None for unrelated attachments; Mermaid document failures never fall back to a bare file."""
    if not enabled():
        return None
    base = os.environ.get("UNBOKS_PUBLIC_BASE_URL", "").rstrip("/")
    parsed, origin = urlsplit(attachment_url), urlsplit(base)
    match = re.fullmatch(re.escape(origin.path) + r"/api/public/mermaid-document/(mdoc_[a-f0-9]{24})", parsed.path)
    if (parsed.scheme, parsed.netloc) != (origin.scheme, origin.netloc) or not match:
        return None
    try:
        return _send(conversation_id, account_id, text, match[1], base, idempotency_key)
    except Exception as exc:
        bm_logger.log("mermaid_card_deferred", error=type(exc).__name__)
        return False


def _send(conversation_id, account_id, text, document_id, base, idempotency_key):
    from agents.social import mermaid_documents as docs, mermaid_reservation_store as store
    from agents.social import zernio_dm_client as provider
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM mermaid_documents WHERE public_id=? AND tenant_slug='mermaid'", (document_id,)).fetchone()
        document = dict(row) if row else None
        if not document:
            return False
        reservation = store.get_reservation(document["reservation_public_id"])
        if not reservation or reservation["conversation_id"] != conversation_id or reservation.get("zernio_account_id") != account_id:
            return False
        if reservation["state"] == "cancelled" or reservation.get("human_takeover"):
            return False
        key = idempotency_key or f"mermaid-card:{document_id}"
        existing = conn.execute("SELECT * FROM mermaid_card_deliveries WHERE action_key=?", (key,)).fetchone()
        if not existing:
            copy = copy_for(reservation["language"])
            body_text = copy["title"] + "\n\n" + text
            if len(body_text.encode("utf-16-le")) // 2 > 1024:
                raise ValueError("WhatsApp card body exceeds 1024 characters")
            digest = hashlib.sha256(docs.HERO_IMAGE.read_bytes()).hexdigest()
            payload = {"accountId": account_id, "interactive": {
                "type": "cta_url", "header": {"type": "image", "image": {"link": f"{base}/api/public/mermaid-card-image/{digest}.png"}},
                "body": {"text": body_text}, "footer": {"text": "Mermaid Boat Trips Curaçao"},
                "action": {"name": "cta_url", "parameters": {"display_text": copy["open_pdf"], "url": download_url(base, document, reservation)}},
            }}
            with conn:
                conn.execute("INSERT OR IGNORE INTO mermaid_card_deliveries (action_key,document_public_id,conversation_id,account_id,payload_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                             (key, document_id, conversation_id, account_id, json.dumps(payload, ensure_ascii=False), docs._now(), docs._now()))
            existing = conn.execute("SELECT * FROM mermaid_card_deliveries WHERE action_key=?", (key,)).fetchone()
        record = dict(existing)
        if (record["document_public_id"], record["conversation_id"], record["account_id"]) != (document_id, conversation_id, account_id):
            return False
        if record["status"] == "delivered":
            return True
        headers = {"Authorization": "Bearer " + os.environ.get("LATE_API_KEY", ""), "Content-Type": "application/json", "Idempotency-Key": key}
        endpoint = "https://zernio.com/api/v1/inbox/conversations/" + quote(conversation_id, safe="")
        if not provider._provider_mutation_account_allowed(account_id, "mermaid_card"):
            return False
        window, _ = provider._recommendation_session_open(endpoint, headers, account_id)
        if not window:
            return False
        pid = record["provider_message_id"]
        if not pid:
            outcome, status, pid = provider._post_recommendation_message(endpoint + "/messages", headers, json.loads(record["payload_json"]))
            with conn:
                conn.execute("UPDATE mermaid_card_deliveries SET provider_message_id=?,status=?,updated_at=? WHERE action_key=?",
                             (pid, "failed" if outcome == "rejected" else "pending", docs._now(), key))
        if not pid:
            return False
        outcome = provider._confirm_recommendation_status(endpoint + "/messages", headers, account_id, pid, require_delivered=True)
        with conn:
            conn.execute("UPDATE mermaid_card_deliveries SET status=?,updated_at=? WHERE action_key=?",
                         ("delivered" if outcome == "sent" else "pending", docs._now(), key))
        return outcome == "sent"
    finally:
        conn.close()
