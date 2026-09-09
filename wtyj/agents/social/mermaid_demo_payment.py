"""Signed, no-money checkout for Mermaid's WhatsApp reservation demonstration."""

from __future__ import annotations
from shared.mermaid_maintenance import participating as _maintenance_participating

import hashlib
import hmac
import html
import os
import re
import secrets
import time
from urllib.parse import urlsplit

from fastapi.responses import HTMLResponse, Response

from agents.social import mermaid_documents, mermaid_reservation_store
from agents.social import mermaid_guest_experience as guest
from agents.social.senders import send_reply
from shared import icp_overrides, mermaid_catalog, state_registry


def _secret() -> str:
    return os.environ.get("MERMAID_DEMO_SIGNING_SECRET", "")


def sign_payment(reservation_id: str, expires: int, secret: str) -> str:
    payload = f"mermaid-payment:{reservation_id}:{int(expires)}"
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def verify_payment(reservation_id: str, expires: int, signature: str, secret: str, now: int | None = None) -> bool:
    current = int(time.time() if now is None else now)
    if not secret or current > int(expires) or int(expires) > current + 3600:
        return False
    return hmac.compare_digest(sign_payment(reservation_id, expires, secret), str(signature or ""))


def build_payment_url(base_url: str, reservation_id: str, secret: str, now: int | None = None) -> str:
    parts = urlsplit(base_url)
    if not secret or not parts.netloc or parts.query or parts.fragment or parts.username or parts.password or not (
        parts.scheme == "https" or (parts.scheme == "http" and parts.hostname in {"localhost", "127.0.0.1"})
    ):
        raise ValueError("Mermaid demo payment configuration is missing")
    current = int(time.time() if now is None else now)
    token = secrets.token_urlsafe(16)
    conn = mermaid_reservation_store._conn()
    try:
        with conn:
            # Serialize minting with cancellation's state check and revocation.
            conn.execute("BEGIN IMMEDIATE")
            reservation = conn.execute(
                "SELECT public_id,conversation_id FROM mermaid_reservations "
                "WHERE public_id=? AND tenant_slug='mermaid' "
                "AND state='demo_payment_pending' AND human_takeover=0",
                (reservation_id,),
            ).fetchone()
            if not reservation or mermaid_reservation_store._operator_review_active(
                conn, reservation["conversation_id"]
            ):
                raise ValueError("Mermaid reservation is unavailable")
            conn.execute("DELETE FROM mermaid_checkout_links WHERE expires_at < ?", (current,))
            conn.execute(
                "INSERT INTO mermaid_checkout_links VALUES (?, 'mermaid', ?, ?, ?)",
                (_checkout_token_hash(token, secret), reservation_id, current + 3600, current),
            )
    finally:
        conn.close()
    return f"{base_url.rstrip('/')}/pay/{token}"


def _checkout_token_hash(token: str, secret: str) -> str:
    return hmac.new(secret.encode(), ("mermaid-short-checkout:" + token).encode(), hashlib.sha256).hexdigest()


def resolve_checkout_token(token: str) -> tuple[str, int, str] | None:
    secret = _secret()
    if not secret or not re.fullmatch(r"[A-Za-z0-9_-]{22}", token) or not mermaid_catalog.reservation_demo_enabled() or not mermaid_catalog.demo_features()["demo_payment"]:
        return None
    conn = mermaid_reservation_store._conn()
    try:
        row = conn.execute(
            "SELECT l.reservation_public_id,l.expires_at,r.conversation_id FROM mermaid_checkout_links l "
            "JOIN mermaid_reservations r ON r.public_id=l.reservation_public_id "
            "WHERE l.token_hash=? AND l.tenant_slug='mermaid' AND r.tenant_slug='mermaid' "
            "AND r.state!='cancelled' AND r.human_takeover=0 AND l.expires_at>=?",
            (_checkout_token_hash(token, secret), int(time.time())),
        ).fetchone()
        if row and mermaid_reservation_store._operator_review_active(conn, row[2]):
            row = None
    finally:
        conn.close()
    if not row:
        return None
    return row[0], row[1], sign_payment(row[0], row[1], secret)


def short_checkout_page(token: str) -> Response:
    from agents.social.isluno_transition import blocked
    if blocked():return _page("Mermaid link retired", "<p>This legacy action is quarantined. Please contact the operator. No payment or booking has been made by this action.</p>", status=410)
    payment = resolve_checkout_token(token)
    return checkout_page(*payment, form_action="") if payment else Response(status_code=404)


@_maintenance_participating('operator_writers')
def complete_short_checkout(token: str, status: str) -> Response:
    from agents.social.isluno_transition import blocked
    if blocked():return _page("Mermaid link retired", "<p>This legacy action is quarantined. Please contact the operator. No payment or booking has been made by this action.</p>", status=410)
    payment = resolve_checkout_token(token)
    return complete_checkout(*payment, status) if payment else Response(status_code=404)


def _page(title: str, body: str, *, actions: str = "", status: int = 200) -> HTMLResponse:
    markup = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title><style>
body{{margin:0;background:#eaf8f8;color:#063b46;font:16px/1.5 system-ui,sans-serif}}main{{max-width:620px;margin:40px auto;padding:30px;background:white;border-radius:18px;box-shadow:0 12px 36px #063b4622}}h1{{margin-top:18px}}.demo{{background:#f36c5b;color:white;padding:10px 14px;border-radius:8px;font-weight:800;text-align:center}}.summary{{background:#f4fbfb;padding:18px;border-radius:12px;margin:20px 0}}button{{border:0;border-radius:10px;padding:14px 18px;font-weight:750;cursor:pointer}}.pay{{background:#007f86;color:white}}.cancel{{background:#e9eef0;color:#203c44;margin-left:8px}}small{{display:block;margin-top:20px;color:#4d6870}}
</style></head><body><main><h1>{html.escape(title)}</h1>{body}{actions}</main></body></html>"""
    return HTMLResponse(markup, status_code=status, headers={"Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer"})


def checkout_page(reservation_id: str, expires: int, signature: str, *, form_action: str | None = None) -> Response:
    from agents.social.isluno_transition import blocked
    if blocked():return _page("Mermaid link retired", "<p>This legacy action is quarantined. Please contact the operator. No payment or booking has been made by this action.</p>", status=410)
    if not verify_payment(reservation_id, expires, signature, _secret()):
        return Response(status_code=404)
    reservation = mermaid_reservation_store.get_reservation(reservation_id)
    if (
        not reservation
        or reservation["state"] == "cancelled"
        or reservation["human_takeover"]
        or mermaid_reservation_store.operator_review_active(
            reservation["conversation_id"]
        )
    ):
        return Response(status_code=404)
    money = reservation["monetary_snapshot"]
    intake = reservation["intake"]
    body = (
        f'<div class="summary"><b>{html.escape(reservation["customer_name"])}</b><br>'
        f'{html.escape(intake["trip_date"])} · {intake["adults"]} adults · '
        f'{intake["children"]} children 4-12 · {intake["infants"]} children 0-3<br>'
        f'<b>{html.escape(guest.price_text(money, intake, reservation["language"]))}</b><br>'
        f'{html.escape(guest.transport_text(intake, reservation["language"], money))}</div>'
        '<p>Complete your booking below.</p>'
    )
    action = f"?expires={int(expires)}&signature={signature}" if form_action is None else form_action
    actions = (
        f'<form method="post" action="{html.escape(action, quote=True)}">'
        '<button class="pay" name="status" value="success">Complete payment</button>'
        '<button class="cancel" name="status" value="cancel">Cancel</button></form>'
    )
    return _page("Mermaid checkout", body, actions=actions)


def success_message(reservation: dict, payment: dict) -> str:
    from agents.social import mermaid_document_cards as cards
    from agents.social import mermaid_reservation_email as email
    if cards.enabled():
        return cards.receipt_text(reservation, payment)
    intake = reservation["intake"]
    locale = reservation["language"] if reservation["language"] in mermaid_documents.LABELS else "en"
    copy = guest.guest_copy(locale)
    return "\n\n".join([
        copy["booking_complete"],
        f"{guest.display_reference(reservation['booking_code'])} · {guest.guest_date(intake['trip_date'], locale)}\n{guest.party_text(intake, locale)}",
        f"{copy['paid']}: {payment['currency']} {int(payment['amount']):,.2f}",
        guest.transport_text(intake, locale, reservation["monetary_snapshot"]),
        email.offer(reservation),
    ])



@_maintenance_participating('operator_writers')
def complete_checkout(reservation_id: str, expires: int, signature: str, status: str) -> Response:
    from agents.social.isluno_transition import blocked
    if blocked():return _page("Mermaid link retired", "<p>This legacy action is quarantined. Please contact the operator. No payment or booking has been made by this action.</p>", status=410)
    if not mermaid_catalog.reservation_demo_enabled() or not mermaid_catalog.demo_features()["demo_payment"] or not verify_payment(reservation_id, expires, signature, _secret()):
        return Response(status_code=404)
    reservation = mermaid_reservation_store.get_reservation(reservation_id)
    if not reservation or reservation["state"] == "cancelled":
        return Response(status_code=404)
    if status != "success":
        if reservation["state"] in {"demo_paid", "booked"}:
            return _page("Payment already completed", "<p>Your payment is complete. Closing this page does not cancel your booking.</p>")
        return _page("Payment cancelled", "<p>No payment was recorded. Your reservation remains open, so you can return to WhatsApp and try again.</p>")
    reference = "PAY-" + hashlib.sha256(reservation_id.encode()).hexdigest()[:10].upper()
    try:
        reservation, payment = mermaid_reservation_store.complete_demo_payment(
            reservation_id, payment_reference=reference,
            idempotency_key=f"demo-payment:{reservation_id}",
        )
    except mermaid_reservation_store.MermaidReservationError:
        # Cancellation may win after the signed callback's initial read.
        current = mermaid_reservation_store.get_reservation(reservation_id)
        if current and current["state"] == "cancelled":
            return Response(status_code=404)
        raise
    document, job = mermaid_documents.create_receipt(reservation, payment)
    reservation = mermaid_reservation_store.attach_receipt(reservation_id, document["public_id"])
    base_url = os.environ.get("UNBOKS_PUBLIC_BASE_URL", "http://localhost:8001")
    receipt_url = mermaid_documents.build_signed_url(base_url, document["public_id"], _secret())
    delivered = job["status"] == "delivered"
    controls = icp_overrides.fetch_overrides_fresh()
    can_send = (
        icp_overrides.whatsapp_inbox_state(controls) is True
        and icp_overrides.auto_reply_state(controls) is True
        and not state_registry.get_ai_muted(reservation["conversation_id"])
    )
    if not delivered and job["attempts"]:
        from agents.social.mermaid_delivery_reconciliation import reconcile_job
        try:
            delivered = reconcile_job(job["public_id"]) == "delivered"
        except Exception:
            delivered = False
    # A timeout can mean accepted but not delivered yet. Repeated checkout
    # callbacks must only reconcile; never resend a new signed URL blindly.
    if not delivered and can_send and not job["attempts"] and mermaid_documents.claim_initial_delivery(job["public_id"]):
        delivered = send_reply(
            "whatsapp", reservation["conversation_id"], reservation.get("zernio_account_id") or "",
            success_message(reservation, payment), attachment_url=receipt_url, attachment_type="file",
            attachment_name=document["filename"],
            confirm_delivery=True, idempotency_key=job["idempotency_key"],
        )
        mermaid_documents.mark_delivery(job["public_id"], delivered, "awaiting provider confirmation" if not delivered else "", count_attempt=False)
        if delivered:
            state_registry.dm_store_message_once(
                conversation_id=reservation["conversation_id"], channel="whatsapp", role="assistant",
                text=success_message(reservation, payment),
                source_message_key="mermaid-job:" + job["public_id"],
            )
            state_registry.dm_store_message_once(
                conversation_id=reservation["conversation_id"], channel="whatsapp", role="system",
                text="Payment receipt sent: " + document["filename"],
                source_message_key="mermaid-attachment:" + job["public_id"],
            )
    body = (
        f"<p><b>Payment complete.</b></p><p>Booking code: <b>{html.escape(guest.display_reference(reservation['booking_code']))}</b></p>"
        "<p>Your receipt is ready in WhatsApp.</p>"
    )
    return _page("Mermaid booking complete", body)
