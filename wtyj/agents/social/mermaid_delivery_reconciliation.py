"""Read-only provider reconciliation for delayed Mermaid PDF deliveries.

A synchronous timeout is not a failed delivery. This worker never sends and
matches the tenant's exact immutable document, independent of URL signatures.
"""
from shared.mermaid_maintenance import participating as _maintenance_participating
import json
import os
import time
from urllib.parse import quote, urlsplit

from agents.social import mermaid_documents as documents
from agents.social import zernio_dm_client as provider
from shared import bm_logger, mermaid_catalog, state_registry

_next_scan = 0.0


def _document_identity(url):
    parsed = urlsplit(str(url or ""))
    return parsed.scheme, parsed.netloc, parsed.path


@_maintenance_participating('delivery')
def reconcile_job(job_id):
    from agents.social.isluno_transition import blocked
    if blocked():return "quarantined"
    job = documents.delivery_job(job_id)
    if not job or job["status"] == "delivered":
        return "delivered" if job else "unknown"
    conn = documents._conn()
    try:
        row = conn.execute(
            "SELECT r.zernio_account_id, d.public_id FROM mermaid_reservations r "
            "JOIN mermaid_documents d ON d.reservation_public_id=r.public_id "
            "WHERE r.public_id=? AND d.public_id=? AND r.tenant_slug='mermaid' AND d.tenant_slug='mermaid'",
            (job["reservation_public_id"], job["document_public_id"]),
        ).fetchone()
    finally:
        conn.close()
    key = os.environ.get("LATE_API_KEY", "")
    base = os.environ.get("UNBOKS_PUBLIC_BASE_URL", "").rstrip("/")
    if not row or not key or not base or not row["zernio_account_id"]:
        return "unknown"
    account = row["zernio_account_id"]
    from agents.social import mermaid_document_cards as cards
    card_records = cards.records(row["public_id"], job["conversation_id"], account)
    card_ids = {record["provider_message_id"] for record in card_records if record["provider_message_id"]}
    card_urls = {json_payload["interactive"]["action"]["parameters"]["url"]
                 for json_payload in (json.loads(record["payload_json"]) for record in card_records)}
    expected = _document_identity(f"{base}/api/public/mermaid-document/{row['public_id']}")
    response = provider._provider_account_get(
        f"https://zernio.com/api/v1/inbox/conversations/{quote(job['conversation_id'], safe='')}/messages",
        account_id=account, operation="mermaid_document_reconcile",
        headers={"Authorization": f"Bearer {key}"},
        params={"accountId": account, "limit": 100, "sortOrder": "desc"}, timeout=5,
    )
    if response is None or not 200 <= response.status_code < 300:
        return "unknown"
    matched = False
    states = set()
    for message in provider._payload_messages(provider._response_json(response)):
        if message.get("direction") != "outgoing":
            continue
        if message.get("accountId", account) != account or message.get("conversationId", job["conversation_id"]) != job["conversation_id"]:
            continue
        attachments = message.get("attachments") or []
        urls = [message.get("attachmentUrl"), message.get("attachment_url")]
        urls += [item.get("url") or item.get("publicUrl") or item.get("attachmentUrl") for item in attachments if isinstance(item, dict)]
        message_id = message.get("id") or message.get("messageId")
        def contains_card_url(value):
            if isinstance(value, str):
                return value in card_urls
            if isinstance(value, dict):
                return any(contains_card_url(item) for item in value.values())
            if isinstance(value, list):
                return any(contains_card_url(item) for item in value)
            return False
        if message_id not in card_ids and not contains_card_url(message) and expected not in [_document_identity(url) for url in urls if url]:
            continue
        matched = True
        status = str(message.get("deliveryStatus") or message.get("status") or "").lower()
        states.add(status if message_id else "unknown")
        if not message_id or status not in {"delivered", "read"}:
            continue
        if not provider._provider_history_still_owned(account, "mermaid_document_commit"):
            return "unknown"
        text = provider._recommendation_message_text(message)
        created_at = message.get("createdAt") or documents._now()
        conn = state_registry._get_conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            # Preserve provider wording, including older versions, as evidence.
            # Avoid duplicating a synchronous sender's existing history entry.
            if text:
                conn.execute(
                    "INSERT OR IGNORE INTO whatsapp_threads (phone, role, text, created_at, channel, sender_name, source_message_key) "
                    "SELECT ?, 'assistant', ?, ?, 'whatsapp', '', ? WHERE NOT EXISTS "
                    "(SELECT 1 FROM whatsapp_threads WHERE phone=? AND role='assistant' AND text=?)",
                    (job["conversation_id"], text, created_at, "mermaid-job:"+job_id, job["conversation_id"], text),
                )
            conn.execute(
                "UPDATE mermaid_delivery_jobs SET status='delivered', last_error='', updated_at=? "
                "WHERE public_id=? AND tenant_slug='mermaid'",
                (documents._now(), job_id),
            )
            conn.commit()
        finally:
            conn.close()
        bm_logger.log("mermaid_document_delivery_reconciled", job_id=job_id, provider_message_id=message_id)
        return "delivered"
    if matched and states <= {"failed", "rejected", "undeliverable"}:
        if not provider._provider_history_still_owned(account, "mermaid_document_failure_commit"):
            return "unknown"
        conn = documents._conn()
        try:
            conn.execute(
                "UPDATE mermaid_delivery_jobs SET status='failed', last_error='Provider confirmed delivery failure', updated_at=? "
                "WHERE public_id=? AND tenant_slug='mermaid' AND status!='delivered'",
                (documents._now(), job_id),
            )
            conn.commit()
        finally:
            conn.close()
        return "failed"
    return "pending" if matched else "unknown"


@_maintenance_participating('scheduled')
def reconcile_pending_once(limit=5):
    global _next_scan
    if not mermaid_catalog.reservation_demo_enabled() or time.monotonic() < _next_scan:
        return 0
    _next_scan = time.monotonic() + 30
    conn = documents._conn()
    try:
        jobs = conn.execute(
            "SELECT public_id FROM mermaid_delivery_jobs WHERE tenant_slug='mermaid' "
            "AND status IN ('pending','failed') AND attempts>0 ORDER BY updated_at LIMIT ?", (limit,),
        ).fetchall()
    finally:
        conn.close()
    done = 0
    for job in jobs:
        try:
            done += reconcile_job(job["public_id"]) == "delivered"
        except Exception as exc:
            bm_logger.log("mermaid_document_reconcile_deferred", error=type(exc).__name__)
        finally:
            conn = documents._conn()
            try:
                conn.execute("UPDATE mermaid_delivery_jobs SET updated_at=? WHERE public_id=? AND status!='delivered'", (documents._now(), job["public_id"]))
                conn.commit()
            finally:
                conn.close()
    return done
