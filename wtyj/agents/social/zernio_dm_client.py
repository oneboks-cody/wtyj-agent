from shared.mermaid_maintenance import participating as _maintenance_participating
# bluemarlin/agents/social/zernio_dm_client.py
# Created: Brief 130
# Purpose: Parse Zernio webhook payloads + send DM replies via Zernio Inbox API

import hashlib
import hmac
import os
import re
import time
import urllib.parse
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone

import requests as http_requests

from late import Late
from shared import bm_logger, state_registry


_VEHICLE_MEDIA_PATH = re.compile(r"^/api/v1/vehicle-media/[A-Za-z0-9._~:%-]+$")
_MAX_VEHICLE_MEDIA_BYTES = 10 * 1024 * 1024
_provider_mutation_guard = ContextVar("zernio_provider_mutation_guard", default=None)


def set_provider_mutation_guard(check):
    """Scope an automated worker's fresh controls/claim check to this context."""
    return _provider_mutation_guard.set(check)


def reset_provider_mutation_guard(token):
    _provider_mutation_guard.reset(token)


@contextmanager
def provider_mutation_scope(check):
    token = set_provider_mutation_guard(check)
    try:
        yield
    finally:
        reset_provider_mutation_guard(token)


def _get_client():
    """Create a Late/Zernio API client. Returns None if no API key."""
    api_key = os.environ.get("LATE_API_KEY", "")
    if not api_key:
        bm_logger.log("zernio_dm_no_api_key")
        return None
    return Late(api_key=api_key)


def verify_webhook_signature(payload_bytes: bytes, signature: str) -> bool:
    """Verify Zernio webhook HMAC-SHA256 signature. Returns True if valid."""
    secret = os.environ.get("ZERNIO_WEBHOOK_SECRET", "")
    if not secret:
        bm_logger.log("zernio_webhook_no_secret")
        return False
    expected = hmac.new(secret.encode(), payload_bytes, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def parse_zernio_webhook(payload: dict) -> dict | None:
    """Parse a Zernio webhook payload into a normalized message dict.
    Returns None if not a message.received event or if parsing fails.

    Returns: {conversation_id, platform, channel, sender_name, sender_id, text,
              message_id, account_id, attachments}
    """
    event = payload.get("event", "")
    if event != "message.received":
        bm_logger.log("zernio_webhook_non_message", webhook_event=event)
        return None

    # Try nested structures — Zernio may use data.message or data directly
    data = payload.get("data", {})
    if not data:
        data = payload.get("message", {})

    text = data.get("text", "")
    msg_obj = data.get("message", {})
    if not text:
        # Try nested message object
        text = msg_obj.get("text", "") if isinstance(msg_obj, dict) else ""

    metadata = data.get("metadata")
    if not isinstance(metadata, dict) and isinstance(msg_obj, dict):
        metadata = msg_obj.get("metadata")
    if not isinstance(metadata, dict):
        metadata = payload.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    interactive_type = str(
        metadata.get("interactiveType")
        or metadata.get("interactive_type")
        or ""
    ).strip()
    interactive_id = str(
        metadata.get("interactiveId")
        or metadata.get("interactive_id")
        or ""
    ).strip()

    raw_attachments = data.get("attachments")
    if not isinstance(raw_attachments, list) and isinstance(msg_obj, dict):
        raw_attachments = msg_obj.get("attachments")
    if not isinstance(raw_attachments, list):
        raw_attachments = []
    attachments = []
    for item in raw_attachments[:8]:
        if not isinstance(item, dict):
            continue
        attachment_payload = item.get("payload")
        attachment_payload = (
            attachment_payload if isinstance(attachment_payload, dict) else {}
        )
        media_id = str(
            attachment_payload.get("id")
            or item.get("mediaId")
            or item.get("media_id")
            or ""
        ).strip()
        attachment_id = str(
            item.get("id")
            or item.get("attachmentId")
            or item.get("attachment_id")
            or media_id
        ).strip()
        if not media_id or not attachment_id:
            continue
        # Provider URLs are deliberately discarded.  V2 retrieves media only
        # through Zernio's authenticated WhatsApp media endpoint.
        attachments.append({
            "provider_attachment_id": attachment_id[:240],
            "media_id": media_id[:240],
            "type": str(item.get("type") or attachment_payload.get("type") or "").lower()[:40],
            "filename": str(
                item.get("filename")
                or item.get("fileName")
                or attachment_payload.get("filename")
                or ""
            )[:180],
            "mime_type": str(
                item.get("mimeType")
                or item.get("contentType")
                or attachment_payload.get("mimeType")
                or attachment_payload.get("contentType")
                or ""
            ).split(";", 1)[0].strip().lower()[:100],
        })

    conversation_id = data.get("conversationId", "") or data.get("conversation_id", "")
    message_id = data.get("id", "") or data.get("messageId", "")
    # Keep the platform-native message id separately from Zernio's internal
    # message id.  GET /inbox/.../messages exposes the former (for WhatsApp,
    # the ``wamid``), so it is the only exact causal anchor suitable for
    # delivery reconciliation.
    provider_message_id = str(
        data.get("platformMessageId")
        or data.get("platform_message_id")
        or (
            msg_obj.get("platformMessageId")
            if isinstance(msg_obj, dict) else ""
        )
        or (
            msg_obj.get("platform_message_id")
            if isinstance(msg_obj, dict) else ""
        )
        or ""
    ).strip()
    sent_at = str(
        data.get("sentAt")
        or data.get("sent_at")
        or (
            msg_obj.get("sentAt") if isinstance(msg_obj, dict) else ""
        )
        or (
            msg_obj.get("sent_at") if isinstance(msg_obj, dict) else ""
        )
        or payload.get("timestamp")
        or ""
    ).strip()
    # account_id may be in message object or top-level account object
    account_id = data.get("accountId", "") or data.get("account_id", "")
    if not account_id:
        account_obj = payload.get("account", {})
        if isinstance(account_obj, dict):
            account_id = account_obj.get("id", "")

    sender = data.get("sender", {})
    if isinstance(sender, dict):
        sender_name = sender.get("name", "")
        sender_id = sender.get("id", "")
    else:
        sender_name = ""
        sender_id = ""

    platform = str(data.get("platform") or "").strip().lower()
    # Brief 170: normalize provider platform strings before routing. Zernio has
    # reported X as both "x" and "twitter", and provider casing must never divert
    # WhatsApp into the generic DM path.
    if platform == "x":
        platform = "twitter"

    if not conversation_id or not message_id:
        bm_logger.log("zernio_webhook_missing_ids",
                       payload_keys=list(payload.keys()),
                       data_keys=list(data.keys()) if isinstance(data, dict) else [])
        return None

    channel = "whatsapp" if platform == "whatsapp" else (f"{platform}_dm" if platform else "unknown_dm")

    return {
        "conversation_id": conversation_id,
        "platform": platform,
        "channel": channel,
        "sender_name": sender_name,
        "sender_id": sender_id,
        "text": text,
        "message_id": message_id,
        "provider_message_id": provider_message_id[:240],
        "sent_at": sent_at[:80],
        "account_id": account_id,
        "interactive_type": interactive_type,
        "interactive_id": interactive_id,
        "attachments": attachments,
        "event_id": str(
            payload.get("id") or payload.get("eventId") or message_id
        )[:240],
    }


def parse_zernio_sent_webhook(payload: dict) -> dict | None:
    """Normalize an outgoing Zernio inbox event.

    WhatsApp Coexistence mirrors messages sent from the WhatsApp Business app
    as message.sent with message.source=whatsappbusinessapp. This parser stays
    separate from the inbound parser so outgoing events never enter the AI
    reply path.
    """
    if payload.get("event") != "message.sent":
        return None

    raw_data = payload.get("data")
    raw_data = raw_data if isinstance(raw_data, dict) else {}
    message = payload.get("message")
    if not isinstance(message, dict):
        nested = raw_data.get("message")
        message = nested if isinstance(nested, dict) else raw_data

    conversation = payload.get("conversation")
    if not isinstance(conversation, dict):
        nested = raw_data.get("conversation")
        conversation = nested if isinstance(nested, dict) else {}
    account = payload.get("account")
    if not isinstance(account, dict):
        nested = raw_data.get("account")
        account = nested if isinstance(nested, dict) else {}

    conversation_id = str(
        message.get("conversationId")
        or message.get("conversation_id")
        or conversation.get("id")
        or conversation.get("conversationId")
        or ""
    )
    message_id = str(message.get("id") or message.get("messageId") or "")
    account_id = str(
        message.get("accountId")
        or message.get("account_id")
        or conversation.get("accountId")
        or account.get("id")
        or account.get("accountId")
        or ""
    )
    platform = str(
        message.get("platform")
        or conversation.get("platform")
        or ""
    ).lower()
    source = str(message.get("source") or "").replace("_", "").lower()
    text = str(message.get("text") or message.get("message") or "")

    sender = message.get("sender")
    sender = sender if isinstance(sender, dict) else {}
    sender_name = str(
        sender.get("name")
        or account.get("name")
        or account.get("displayName")
        or "Secretaría"
    )
    sender_id = str(sender.get("id") or "")

    attachments = message.get("attachments")
    if not isinstance(attachments, list):
        attachments = []

    created_at = str(
        message.get("createdAt")
        or message.get("sentAt")
        or payload.get("timestamp")
        or ""
    )

    if not conversation_id or not message_id:
        bm_logger.log(
            "zernio_sent_webhook_missing_ids",
            payload_keys=list(payload.keys()),
            message_keys=list(message.keys()),
        )
        return None

    channel = (
        "whatsapp"
        if platform == "whatsapp"
        else (f"{platform}_dm" if platform else "unknown_dm")
    )
    return {
        "event": "message.sent",
        "conversation_id": conversation_id,
        "platform": platform,
        "channel": channel,
        "sender_name": sender_name,
        "sender_id": sender_id,
        "text": text,
        "message_id": message_id,
        "account_id": account_id,
        "direction": str(message.get("direction") or "outgoing").lower(),
        "source": source,
        "created_at": created_at,
        "attachments": attachments,
    }


def parse_zernio_failed_webhook(payload: dict) -> dict | None:
    """Normalize a late provider delivery failure without routing it to Nick."""
    if payload.get("event") != "message.failed":
        return None
    raw_data = payload.get("data")
    raw_data = raw_data if isinstance(raw_data, dict) else {}
    message = payload.get("message")
    if not isinstance(message, dict):
        nested = raw_data.get("message")
        message = nested if isinstance(nested, dict) else raw_data
    conversation = payload.get("conversation")
    if not isinstance(conversation, dict):
        nested = raw_data.get("conversation")
        conversation = nested if isinstance(nested, dict) else {}
    account = payload.get("account")
    if not isinstance(account, dict):
        nested = raw_data.get("account")
        account = nested if isinstance(nested, dict) else {}
    conversation_id = str(
        message.get("conversationId")
        or message.get("conversation_id")
        or conversation.get("id")
        or conversation.get("conversationId")
        or ""
    )
    message_id = str(message.get("id") or message.get("messageId") or "")
    if not conversation_id or not message_id:
        bm_logger.log(
            "zernio_failed_webhook_missing_ids",
            payload_keys=list(payload.keys()),
            message_keys=list(message.keys()),
        )
        return None
    metadata = message.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    attachments = message.get("attachments")
    attachments = attachments if isinstance(attachments, list) else []
    interactive = message.get("interactive")
    delivery_error = message.get("deliveryError")
    delivery_error = delivery_error if isinstance(delivery_error, dict) else {}
    return {
        "event": "message.failed",
        "conversation_id": conversation_id,
        "message_id": message_id,
        "account_id": str(
            message.get("accountId")
            or message.get("account_id")
            or conversation.get("accountId")
            or account.get("id")
            or account.get("accountId")
            or raw_data.get("accountId")
            or ""
        ),
        "text": str(message.get("message") or message.get("text") or "").strip(),
        "recoverable_media": bool(
            attachments
            or message.get("attachmentUrl")
            or message.get("attachmentType")
            or isinstance(interactive, dict)
            or isinstance(metadata.get("waInteractive"), dict)
        ),
        "failure_reason": str(
            message.get("failureReason")
            or message.get("errorMessage")
            or raw_data.get("failureReason")
            or delivery_error.get("message")
            or delivery_error.get("title")
            or ""
        ),
    }


class ZernioReplyError(RuntimeError):
    """Provider rejected or did not confirm an operator reply."""


class WhatsAppWindowClosedError(ZernioReplyError):
    """WhatsApp free-text window is closed for this conversation."""


def _provider_account_allowed(
    account_id: str,
    operation: str,
    *,
    boundary: str,
) -> bool:
    """Re-read tenant ownership at a Zernio provider boundary."""
    raw_account_id = str(account_id or "")
    if not raw_account_id.strip() or raw_account_id != raw_account_id.strip():
        bm_logger.log(
            "zernio_provider_account_missing",
            operation=operation,
            boundary=boundary,
        )
        return False
    try:
        from shared.tenant_guard import is_account_allowed

        allowed = is_account_allowed(account_id, direction="outbound")
    except Exception as exc:
        bm_logger.log(
            "zernio_provider_account_control_unavailable",
            operation=operation,
            boundary=boundary,
            error=type(exc).__name__,
        )
        return False
    if allowed is not True:
        bm_logger.log(
            "zernio_provider_account_not_allowlisted",
            operation=operation,
            boundary=boundary,
            account_id=str(account_id or "")[:20],
        )
        return False
    return True


def _provider_mutation_account_allowed(account_id: str, operation: str) -> bool:
    """Re-read tenant ownership at the final boundary of a Zernio mutation."""
    guard = _provider_mutation_guard.get()
    if guard is not None and guard() is not True:
        bm_logger.log("zernio_automation_mutation_stopped", operation=operation)
        return False
    return _provider_account_allowed(
        account_id,
        operation,
        boundary="mutation",
    )


def _provider_account_get(
    url: str,
    *,
    account_id: str,
    operation: str,
    headers: dict,
    params: dict | None = None,
    timeout: int = 15,
):
    """Perform one account-scoped provider read while ownership is current.

    Zernio API keys can see team-wide inbox data.  Check the mounted tenant
    allowlist immediately before the request and again before exposing its
    response to reconciliation logic.  ``None`` deliberately means that the
    caller must fail closed without trusting any provider data.
    """
    request_params = dict(params or {})
    normalized_account_id = str(account_id or "").strip()
    requested_account_id = str(request_params.get("accountId") or "").strip()
    if not normalized_account_id or requested_account_id != normalized_account_id:
        bm_logger.log(
            "zernio_provider_read_account_mismatch",
            operation=operation,
        )
        return None
    request_params["accountId"] = normalized_account_id
    if not _provider_account_allowed(
        account_id,
        operation,
        boundary="read_preflight",
    ):
        return None
    response = http_requests.get(
        url,
        headers=headers,
        params=request_params,
        timeout=timeout,
    )
    if not _provider_account_allowed(
        account_id,
        operation,
        boundary="read_response",
    ):
        return None
    return response


def _provider_history_still_owned(account_id: str, operation: str) -> bool:
    """Fence success/reconciliation derived from an earlier provider read."""
    return _provider_account_allowed(
        account_id,
        operation,
        boundary="history_reconcile",
    )


def _response_json(response) -> dict:
    try:
        payload = response.json()
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _payload_messages(payload: dict) -> list[dict]:
    messages = payload.get("messages")
    if not isinstance(messages, list):
        messages = payload.get("data", [])
    return [item for item in messages if isinstance(item, dict)] if isinstance(messages, list) else []


def _parse_provider_time(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@_maintenance_participating('transport')
def _confirmed_text_reply(
    conversation_id: str,
    account_id: str,
    text: str,
    api_key: str,
    idempotency_key: str = "",
) -> bool:
    """Send free text only inside WhatsApp's active window and confirm status."""
    if not _provider_mutation_account_allowed(account_id, "confirmed_text_preflight"):
        return False
    base_url = (
        "https://zernio.com/api/v1/inbox/conversations/"
        f"{urllib.parse.quote(conversation_id)}"
    )
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key

    detail_response = _provider_account_get(
        base_url,
        account_id=account_id,
        operation="confirmed_text_detail",
        headers=headers,
        params={"accountId": account_id},
        timeout=15,
    )
    if detail_response is None:
        return False
    if detail_response.status_code == 404:
        return False
    if not 200 <= detail_response.status_code < 300:
        bm_logger.log(
            "zernio_dm_confirmation_detail_failed",
            conversation_id=conversation_id[:20],
            status=detail_response.status_code,
            error=detail_response.text[:200],
        )
        return False

    detail_payload = _response_json(detail_response)
    detail = detail_payload.get("data", detail_payload)
    platform = (
        str(detail.get("platform") or "").strip().lower()
        if isinstance(detail, dict) else ""
    )
    if platform != "whatsapp":
        bm_logger.log(
            "zernio_dm_confirmation_platform_unverified",
            conversation_id=conversation_id[:20],
            platform=platform[:40],
        )
        return False

    messages_response = _provider_account_get(
        f"{base_url}/messages",
        account_id=account_id,
        operation="confirmed_text_history",
        headers=headers,
        params={"accountId": account_id, "limit": 100, "sortOrder": "desc"},
        timeout=15,
    )
    if messages_response is None:
        return False
    if not 200 <= messages_response.status_code < 300:
        bm_logger.log(
            "zernio_dm_confirmation_history_failed",
            conversation_id=conversation_id[:20],
            status=messages_response.status_code,
            error=messages_response.text[:200],
        )
        return False
    if not _provider_history_still_owned(
        account_id,
        "confirmed_text_history",
    ):
        return False

    existing_messages = _payload_messages(_response_json(messages_response))
    if platform == "whatsapp":
        latest_incoming = next(
            (
                _parse_provider_time(item.get("createdAt") or item.get("created_at"))
                for item in existing_messages
                if str(item.get("direction") or "").lower() == "incoming"
            ),
            None,
        )
        if latest_incoming is None or datetime.now(timezone.utc) - latest_incoming > timedelta(hours=24):
            bm_logger.log(
                "zernio_whatsapp_window_closed",
                conversation_id=conversation_id[:20],
                latest_incoming_at=latest_incoming.isoformat() if latest_incoming else "",
            )
            raise WhatsAppWindowClosedError(
                "Han pasado más de 24 horas desde el último mensaje del contacto. "
                "WhatsApp no permite enviar texto libre. El contacto debe escribir "
                "primero o se debe usar una plantilla aprobada."
            )

    if not _provider_mutation_account_allowed(account_id, "confirmed_text_reply"):
        return False
    send_response = http_requests.post(
        f"{base_url}/messages",
        headers=headers,
        json={"accountId": account_id, "message": text},
        timeout=15,
    )
    send_payload = _response_json(send_response)
    if not 200 <= send_response.status_code < 300:
        detail_message = (
            send_payload.get("message")
            or send_payload.get("error")
            or send_payload.get("detail")
            or send_response.text[:200]
        )
        bm_logger.log(
            "zernio_dm_send_failed",
            conversation_id=conversation_id[:20],
            status=send_response.status_code,
            error=str(detail_message)[:200],
        )
        raise ZernioReplyError(
            "WhatsApp rechazó el mensaje. Inténtalo de nuevo o usa una plantilla aprobada."
        )

    message_id = _response_message_id(send_response)
    if not message_id:
        bm_logger.log(
            "zernio_dm_delivery_unconfirmed",
            conversation_id=conversation_id[:20],
            outcome="missing_provider_message_id",
        )
        raise ZernioReplyError(
            "Zernio aceptó el mensaje, pero no devolvió una referencia verificable. "
            "No se mostrará como enviado."
        )
    terminal_success = {"sent", "delivered", "read"}
    terminal_failure = {"failed", "rejected", "undeliverable"}

    for attempt in range(8):
        if attempt:
            time.sleep(0.5)
        status_response = _provider_account_get(
            f"{base_url}/messages",
            account_id=account_id,
            operation="confirmed_text_delivery_status",
            headers=headers,
            params={"accountId": account_id, "limit": 20, "sortOrder": "desc"},
            timeout=15,
        )
        if status_response is None:
            break
        if not 200 <= status_response.status_code < 300:
            continue
        candidates = _payload_messages(_response_json(status_response))
        matched = next(
            (
                item for item in candidates
                if str(item.get("id") or item.get("messageId") or "") == message_id
            ),
            None,
        )
        if not matched:
            continue
        if not _provider_history_still_owned(
            account_id,
            "confirmed_text_delivery_status",
        ):
            break
        status = str(
            matched.get("status") or matched.get("deliveryStatus") or ""
        ).lower()
        if status in terminal_success:
            bm_logger.log(
                "zernio_dm_delivery_confirmed",
                conversation_id=conversation_id[:20],
                status=status,
            )
            return True
        if status in terminal_failure:
            bm_logger.log(
                "zernio_dm_delivery_failed",
                conversation_id=conversation_id[:20],
                status=status,
                error_code=str(matched.get("errorCode") or "")[:80],
                failure_reason=str(matched.get("failureReason") or "")[:200],
            )
            raise ZernioReplyError(
                "WhatsApp marcó el mensaje como fallido. No se ha entregado al contacto."
            )

    bm_logger.log(
        "zernio_dm_delivery_unconfirmed",
        conversation_id=conversation_id[:20],
        message_id=message_id[:20],
    )
    raise ZernioReplyError(
        "Zernio aceptó el mensaje, pero WhatsApp no confirmó el envío. "
        "No se mostrará como enviado."
    )


def _recommendation_message_text(item: dict) -> str:
    direct = str(item.get("message") or item.get("text") or "")
    interactive = item.get("interactive")
    interactive = interactive if isinstance(interactive, dict) else {}
    body = interactive.get("body")
    body = body if isinstance(body, dict) else {}
    return direct or str(body.get("text") or "")


def _recommendation_session_open(
    base_url: str,
    headers: dict,
    account_id: str,
    trigger_sent_at: str = "",
) -> tuple[bool, list[dict]]:
    response = _provider_account_get(
        f"{base_url}/messages",
        account_id=account_id,
        operation="recommendation_session_history",
        headers=headers,
        params={"accountId": account_id, "limit": 100, "sortOrder": "desc"},
        timeout=15,
    )
    if response is None:
        return False, []
    if not 200 <= response.status_code < 300:
        bm_logger.log(
            "ali_vehicle_recommendation_window_check_failed",
            status=response.status_code,
        )
        return False, []
    messages = _payload_messages(_response_json(response))
    incoming_times = [
        _parse_provider_time(item.get("createdAt") or item.get("created_at"))
        for item in messages
        if str(item.get("direction") or "").lower() == "incoming"
    ]
    # The signed webhook can arrive before Zernio's list endpoint reflects the
    # same inbound message.  Its platform timestamp is still a valid 24-hour
    # session anchor; without it, a brand-new conversation can be dropped
    # before any recommendation POST is attempted.
    trigger_time = _parse_provider_time(trigger_sent_at)
    if trigger_time is not None:
        incoming_times.append(trigger_time)
    latest = max((item for item in incoming_times if item is not None), default=None)
    if not _provider_history_still_owned(
        account_id,
        "recommendation_session_history",
    ):
        return False, []
    return (
        latest is not None
        and datetime.now(timezone.utc) - latest <= timedelta(hours=24),
        messages,
    )


def whatsapp_customer_service_window(
    conversation_id: str,
    account_id: str,
    trigger_sent_at: str = "",
) -> dict:
    """Verify the WhatsApp free-form service window at the provider boundary.

    A local timestamp is never sufficient authorization for an automated
    free-form send.  Zernio's current message history is the primary source;
    the signed inbound webhook timestamp is accepted as a race-safe fallback
    while that history endpoint catches up.  Any provider error fails closed.
    """
    api_key = os.environ.get("LATE_API_KEY", "")
    if not api_key or not conversation_id or not account_id:
        return {"open": False, "reason": "provider_unavailable"}
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    base_url = (
        "https://zernio.com/api/v1/inbox/conversations/"
        f"{urllib.parse.quote(conversation_id)}/messages"
    )
    try:
        response = _provider_account_get(
            base_url,
            account_id=account_id,
            operation="customer_service_window_history",
            headers=headers,
            params={"accountId": account_id, "limit": 100, "sortOrder": "desc"},
            timeout=15,
        )
    except http_requests.RequestException as exc:
        bm_logger.log(
            "ali_lead_follow_up_window_check_failed",
            conversation_id=conversation_id[:20],
            error=type(exc).__name__,
        )
        return {"open": False, "reason": "provider_unavailable"}
    if response is None or not 200 <= response.status_code < 300:
        bm_logger.log(
            "ali_lead_follow_up_window_check_failed",
            conversation_id=conversation_id[:20],
            status=getattr(response, "status_code", None),
        )
        return {"open": False, "reason": "provider_unavailable"}
    messages = _payload_messages(_response_json(response))
    incoming_times = [
        _parse_provider_time(item.get("createdAt") or item.get("created_at"))
        for item in messages
        if str(item.get("direction") or "").lower() == "incoming"
    ]
    trigger_time = _parse_provider_time(trigger_sent_at)
    if trigger_time is not None:
        incoming_times.append(trigger_time)
    latest = max((item for item in incoming_times if item is not None), default=None)
    if not _provider_history_still_owned(
        account_id,
        "customer_service_window_history",
    ):
        return {"open": False, "reason": "provider_unavailable"}
    if latest is None:
        return {"open": False, "reason": "missing_inbound"}
    expires_at = latest + timedelta(hours=24)
    is_open = datetime.now(timezone.utc) < expires_at
    return {
        "open": is_open,
        "reason": "open" if is_open else "window_closed",
        "latest_inbound_at": latest.isoformat(),
        "expires_at": expires_at.isoformat(),
    }


def _recommendation_visible_message(messages: list[dict], text: str) -> dict | None:
    return next(
        (
            item for item in messages
            if str(item.get("direction") or "").lower() == "outgoing"
            and _recommendation_message_text(item) == text
        ),
        None,
    )


def _messages_after_latest_incoming(messages: list[dict]) -> list[dict]:
    """Return only messages newer than the latest customer inbound."""
    for index, item in enumerate(messages):
        if str(item.get("direction") or "").lower() == "incoming":
            return messages[:index]
    return []


def _messages_after_trigger(
    messages: list[dict],
    trigger_message_id: str,
    trigger_sent_at: str,
) -> list[dict]:
    """Return only provider messages causally newer than this inbound turn.

    Zernio webhook payloads expose the platform-native message id while the
    message-list endpoint may lag behind the webhook.  Prefer the exact id; if
    it has not appeared yet, use the platform-reported send time as a strict
    lower bound.  With neither anchor, reconciliation is deliberately disabled
    so an older same-text message can never acknowledge a new customer turn.
    """
    normalized_id = str(trigger_message_id or "").strip()
    if normalized_id:
        for index, item in enumerate(messages):
            if (
                str(item.get("direction") or "").lower() == "incoming"
                and str(item.get("id") or item.get("messageId") or "").strip()
                == normalized_id
            ):
                return messages[:index]
    trigger_time = _parse_provider_time(trigger_sent_at)
    if trigger_time is None:
        return []
    return [
        item for item in messages
        if (
            (item_time := _parse_provider_time(
                item.get("createdAt") or item.get("created_at")
            )) is not None
            and item_time > trigger_time
        )
    ]


def _response_message_id(response) -> str:
    payload = _response_json(response)
    data = payload.get("data")
    data = data if isinstance(data, dict) else {}
    nested = data.get("message")
    nested = nested if isinstance(nested, dict) else {}
    message = payload.get("message")
    message = message if isinstance(message, dict) else {}
    return str(
        data.get("id")
        or data.get("messageId")
        or nested.get("id")
        or nested.get("messageId")
        or message.get("id")
        or message.get("messageId")
        or payload.get("id")
        or payload.get("messageId")
        or ""
    ).strip()


def _delivery_result(
    success: bool,
    delivery: str,
    *provider_ids: str,
    provider_parts: dict[str, list[str]] | None = None,
) -> dict:
    result = {"success": success, "delivery": delivery}
    normalized = list(dict.fromkeys(
        str(value or "").strip() for value in provider_ids if str(value or "").strip()
    ))
    if normalized:
        result["provider_message_ids"] = normalized
    normalized_parts = {}
    for part, values in (provider_parts or {}).items():
        ids = list(dict.fromkeys(
            str(value or "").strip()
            for value in values or []
            if str(value or "").strip()
        ))[:10]
        if part in {"image", "carousel", "picker", "picker_fallback", "individual_images"} and ids:
            normalized_parts[part] = ids
    if normalized_parts:
        result["provider_parts"] = normalized_parts
    return result


def _preflight_vehicle_media(url: str) -> bool:
    """Validate one server-owned Ali JPEG without following redirects."""
    configured = str(
        os.environ.get("ALI_QUOTE_API_BASE_URL") or "https://alicarrental.com"
    ).rstrip("/")
    expected = urllib.parse.urlparse(configured)
    parsed = urllib.parse.urlparse(str(url or ""))
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    if (
        expected.scheme != "https"
        or parsed.scheme != "https"
        or parsed.netloc != expected.netloc
        or not _VEHICLE_MEDIA_PATH.fullmatch(parsed.path)
        or parsed.username
        or parsed.password
        or parsed.fragment
        or set(query) != {"v"}
        or len(query.get("v") or []) != 1
        or not str(query["v"][0]).isdigit()
    ):
        return False
    try:
        response = http_requests.get(
            url,
            timeout=15,
            allow_redirects=False,
            stream=True,
        )
    except http_requests.RequestException:
        return False
    if response.status_code != 200:
        return False
    headers = getattr(response, "headers", {}) or {}
    media_type = str(headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
    if media_type != "image/jpeg" or headers.get("Location"):
        return False
    try:
        declared = int(headers.get("Content-Length") or 0)
    except (TypeError, ValueError):
        return False
    if declared < 0 or declared > _MAX_VEHICLE_MEDIA_BYTES:
        return False
    total = 0
    iterator = getattr(response, "iter_content", None)
    if callable(iterator):
        chunks = iterator(chunk_size=64 * 1024)
    else:
        chunks = [getattr(response, "content", b"")]
    try:
        for chunk in chunks:
            total += len(chunk or b"")
            if total > _MAX_VEHICLE_MEDIA_BYTES:
                return False
    except http_requests.RequestException:
        return False
    return total > 0


@_maintenance_participating('transport')
def _post_recommendation_message(
    url: str,
    headers: dict,
    body: dict,
) -> tuple[str, int | None, str]:
    """Return ``sent``, ``rejected``, or ``ambiguous`` with status."""
    last_status = None
    for _attempt in range(2):
        # Structured recommendations and confirmations bypass the registry
        # sender adapter used by ordinary text replies.  Re-read the mounted
        # tenant allowlist immediately before *every* provider attempt so an
        # account reassignment between generation, fallback, or retry cannot
        # send from another tenant's WhatsApp account.
        account_id = str((body or {}).get("accountId") or "")
        if not _provider_mutation_account_allowed(account_id, "structured_message"):
            bm_logger.log(
                "zernio_structured_send_account_not_allowlisted",
                account_id=account_id[:20],
            )
            return "rejected", last_status, ""
        try:
            response = http_requests.post(
                url,
                headers=headers,
                json=body,
                timeout=15,
            )
        except http_requests.RequestException:
            continue
        last_status = response.status_code
        if 200 <= response.status_code < 300:
            provider_message_id = _response_message_id(response)
            return (
                "sent" if provider_message_id else "ambiguous",
                response.status_code,
                provider_message_id,
            )
        if response.status_code not in {408, 409, 429} and response.status_code < 500:
            return "rejected", response.status_code, ""
    return "ambiguous", last_status, ""


def _confirm_recommendation_status(
    request_url: str,
    headers: dict,
    account_id: str,
    provider_message_id: str,
    require_delivered: bool = False,
) -> str:
    """Return sent, rejected, or ambiguous for an accepted provider message."""
    if not str(provider_message_id or "").strip():
        return "ambiguous"
    terminal_success = (
        {"delivered", "read"}
        if require_delivered else {"sent", "delivered", "read"}
    )
    terminal_failure = {"failed", "rejected", "undeliverable"}
    attempts = 20 if require_delivered else 8
    for attempt in range(attempts):
        if attempt:
            time.sleep(0.5)
        try:
            response = _provider_account_get(
                request_url,
                account_id=account_id,
                operation="recommendation_delivery_status",
                headers=headers,
                params={"accountId": account_id, "limit": 20, "sortOrder": "desc"},
                timeout=15,
            )
        except http_requests.RequestException:
            continue
        if response is None:
            return "ambiguous"
        if not 200 <= response.status_code < 300:
            continue
        matched = next(
            (
                item for item in _payload_messages(_response_json(response))
                if str(item.get("id") or item.get("messageId") or "")
                == provider_message_id
            ),
            None,
        )
        if not matched:
            continue
        if not _provider_history_still_owned(
            account_id,
            "recommendation_delivery_status",
        ):
            return "ambiguous"
        status = str(
            matched.get("status") or matched.get("deliveryStatus") or ""
        ).lower()
        if status in terminal_success:
            return "sent"
        if status in terminal_failure:
            return "rejected"
    return "ambiguous"


def _send_recommendation_part(
    request_url: str,
    base_headers: dict,
    account_id: str,
    existing_messages: list[dict],
    *,
    body: dict,
    idempotency_key: str,
    visible_text: str,
    reconcile_after_latest_incoming: bool = False,
    reconcile_after_trigger: bool = False,
    trigger_message_id: str = "",
    trigger_sent_at: str = "",
    require_delivered: bool = False,
) -> tuple[str, int | None, bool, str]:
    """Send or reconcile one idempotent visible part of a discovery bundle."""
    terminal_history_success = (
        {"delivered", "read"}
        if require_delivered else {"sent", "delivered", "read"}
    )
    if not _provider_history_still_owned(
        account_id,
        "recommendation_existing_history",
    ):
        return "ambiguous", None, False, ""
    if reconcile_after_trigger:
        reconciliation_messages = _messages_after_trigger(
            existing_messages, trigger_message_id, trigger_sent_at,
        )
    elif reconcile_after_latest_incoming:
        reconciliation_messages = _messages_after_latest_incoming(
            existing_messages,
        )
    else:
        reconciliation_messages = existing_messages
    visible = _recommendation_visible_message(
        reconciliation_messages, visible_text,
    )
    if visible:
        visible_id = str(visible.get("id") or visible.get("messageId") or "").strip()
        visible_status = str(
            visible.get("status") or visible.get("deliveryStatus") or ""
        ).lower()
        if not visible_id:
            return "ambiguous", None, True, ""
        if visible_status in {
            "failed", "rejected", "undeliverable",
        }:
            return "rejected", None, True, visible_id
        if require_delivered and visible_status == "sent" and visible_id:
            confirmed = _confirm_recommendation_status(
                request_url,
                base_headers,
                account_id,
                visible_id,
                require_delivered=True,
            )
            if confirmed != "sent":
                return confirmed, None, True, visible_id
        elif visible_status not in terminal_history_success:
            return "ambiguous", None, True, visible_id
        if not _provider_history_still_owned(
            account_id,
            "recommendation_existing_visible",
        ):
            return "ambiguous", None, False, ""
        return "sent", None, True, visible_id
    headers = dict(base_headers)
    headers["Idempotency-Key"] = idempotency_key
    outcome, status, provider_message_id = _post_recommendation_message(
        request_url,
        headers,
        body,
    )
    if outcome == "sent" and not provider_message_id:
        outcome = "ambiguous"
    if outcome == "sent" and provider_message_id:
        outcome = _confirm_recommendation_status(
            request_url,
            base_headers,
            account_id,
            provider_message_id,
            require_delivered=require_delivered,
        )
    if outcome == "sent":
        return outcome, status, False, provider_message_id
    if outcome == "rejected":
        return outcome, status, False, provider_message_id
    try:
        response = _provider_account_get(
            request_url,
            account_id=account_id,
            operation="recommendation_retry_history",
            headers=base_headers,
            params={"accountId": account_id, "limit": 20, "sortOrder": "desc"},
            timeout=15,
        )
    except http_requests.RequestException:
        response = None
    if response is not None and 200 <= response.status_code < 300:
        retry_messages = _payload_messages(_response_json(response))
        if reconcile_after_trigger:
            retry_messages = _messages_after_trigger(
                retry_messages, trigger_message_id, trigger_sent_at,
            )
        elif reconcile_after_latest_incoming:
            retry_messages = _messages_after_latest_incoming(retry_messages)
        visible = _recommendation_visible_message(retry_messages, visible_text)
        if visible:
            visible_id = str(
                visible.get("id") or visible.get("messageId") or ""
            ).strip()
            visible_status = str(
                visible.get("status")
                or visible.get("deliveryStatus")
                or ""
            ).lower()
            if not visible_id:
                return "ambiguous", status, True, ""
            if visible_status in {"failed", "rejected", "undeliverable"}:
                return "rejected", status, True, visible_id
            if require_delivered and visible_status == "sent" and visible_id:
                confirmed = _confirm_recommendation_status(
                    request_url,
                    base_headers,
                    account_id,
                    visible_id,
                    require_delivered=True,
                )
                if confirmed != "sent":
                    return confirmed, status, True, visible_id
            elif visible_status not in terminal_history_success:
                return "ambiguous", status, True, visible_id
            if not _provider_history_still_owned(
                account_id,
                "recommendation_retry_visible",
            ):
                return "ambiguous", status, False, ""
            return "sent", status, True, visible_id
    return outcome, status, False, ""


def send_dm_quote_confirmation(
    conversation_id: str,
    account_id: str,
    confirmation: dict,
) -> dict:
    """Send one replay-safe signed Ali summary confirmation control."""
    # The workflow owns the customer-facing language contract.  Import here
    # to keep the generic provider adapter load order independent while still
    # validating every locale from one canonical source.
    from agents.social.ali_quote_workflow import (
        QUOTE_CONFIRMATION_ACTIONS,
        QUOTE_CONFIRMATION_FALLBACK_INSTRUCTION,
    )

    accepted_action_titles = set(QUOTE_CONFIRMATION_ACTIONS.values())
    accepted_fallbacks = tuple(QUOTE_CONFIRMATION_FALLBACK_INSTRUCTION.values())
    api_key = os.environ.get("LATE_API_KEY", "")
    state_hash = str((confirmation or {}).get("state_hash") or "")
    idempotency_key = str((confirmation or {}).get("idempotency_key") or "")
    text = str((confirmation or {}).get("text") or "")
    fallback_text = str((confirmation or {}).get("fallback_text") or "")
    buttons = (confirmation or {}).get("buttons")
    buttons = buttons if isinstance(buttons, list) else []
    button_titles = tuple(
        str(button.get("title") or "")
        for button in buttons
        if isinstance(button, dict)
    )
    if (
        not api_key
        or not re.fullmatch(r"[0-9a-f]{64}", state_hash)
        or not idempotency_key
        or not text
        or not fallback_text.endswith(accepted_fallbacks)
        or len(buttons) != 2
        or button_titles not in accepted_action_titles
        or any(
            not isinstance(button, dict)
            or button.get("type") != "postback"
            for button in buttons
        )
        or not str(buttons[0].get("payload") or "").startswith(
            "ali_quote_confirm:v1:"
        )
        or not str(buttons[1].get("payload") or "").startswith(
            "ali_quote_change:v1:"
        )
    ):
        return {"success": False, "delivery": "invalid"}
    base_url = (
        "https://zernio.com/api/v1/inbox/conversations/"
        f"{urllib.parse.quote(conversation_id)}"
    )
    request_url = f"{base_url}/messages"
    base_headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    window_open, existing_messages = _recommendation_session_open(
        base_url, base_headers, account_id,
    )
    if not window_open:
        bm_logger.log(
            "ali_quote_confirmation_window_closed",
            confirmation_hash=state_hash[:12],
        )
        return {"success": False, "delivery": "window_closed"}
    outcome, status, reconciled, provider_id = _send_recommendation_part(
        request_url,
        base_headers,
        account_id,
        existing_messages,
        body={
            "accountId": account_id,
            "message": text,
            "buttons": buttons,
        },
        idempotency_key=f"{idempotency_key}-interactive",
        visible_text=text,
        reconcile_after_latest_incoming=True,
    )
    if outcome == "sent":
        bm_logger.log(
            "ali_quote_confirmation_reconciled" if reconciled
            else "ali_quote_confirmation_sent",
            confirmation_hash=state_hash[:12],
        )
        return _delivery_result(True, "interactive", provider_id)
    fallback_outcome, fallback_status, _, fallback_provider_id = _send_recommendation_part(
        request_url,
        base_headers,
        account_id,
        existing_messages,
        body={"accountId": account_id, "message": fallback_text},
        idempotency_key=f"{idempotency_key}-fallback",
        visible_text=fallback_text,
        reconcile_after_latest_incoming=True,
    )
    if fallback_outcome == "sent":
        bm_logger.log(
            "ali_quote_confirmation_fallback_sent",
            interactive_status=status,
            confirmation_hash=state_hash[:12],
        )
        # Only the provider-confirmed fallback anchors this summary.  The
        # interactive message may have received an ID before later reporting
        # a terminal failure; retaining that failed ID would make its late
        # webhook look like a failure of the successful fallback delivery.
        return _delivery_result(True, "text_fallback", fallback_provider_id)
    bm_logger.log(
        "ali_quote_confirmation_failed",
        interactive_status=status,
        fallback_status=fallback_status,
        confirmation_hash=state_hash[:12],
    )
    return {"success": False, "delivery": "confirmation_failed"}


def send_dm_post_quote_actions(
    conversation_id: str,
    account_id: str,
    actions: dict,
) -> dict:
    """Send one replay-safe, provider-confirmed Ali post-quote action control."""
    actions = actions if isinstance(actions, dict) else {}
    api_key = os.environ.get("LATE_API_KEY", "")
    state_hash = str(actions.get("state_hash") or "")
    idempotency_key = str(actions.get("idempotency_key") or "")
    text = str(actions.get("text") or "")
    buttons = actions.get("buttons")
    allowed_title_sets = {
        ("Reserve This Car", "Change Something", "Ask A Question"),
        ("Reserveer Auto", "Iets Wijzigen", "Stel Een Vraag"),
        ("Reserva E Outo Aki", "Kambia Algu", "Hasi Pregunta"),
        ("Auto Reservieren", "Etwas Ändern", "Frage Stellen"),
    }
    button_titles = tuple(
        str(button.get("title") or "")
        for button in buttons or []
        if isinstance(button, dict)
    )
    valid_buttons = (
        isinstance(buttons, list)
        and len(buttons) == 3
        and button_titles in allowed_title_sets
        and all(
            isinstance(button, dict)
            and button.get("type") == "postback"
            and str(button.get("payload") or "").startswith(
                "ali_post_quote:v1:"
            )
            for button in buttons
        )
        and len({str(button.get("payload") or "") for button in buttons}) == 3
    )
    if (
        not api_key
        or not str(conversation_id or "").strip()
        or not str(account_id or "").strip()
        or not re.fullmatch(r"[0-9a-f]{64}", state_hash)
        or not idempotency_key
        or not text
        or not valid_buttons
    ):
        return {"success": False, "delivery": "invalid"}

    base_url = (
        "https://zernio.com/api/v1/inbox/conversations/"
        f"{urllib.parse.quote(conversation_id)}"
    )
    request_url = f"{base_url}/messages"
    base_headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    window_open, existing_messages = _recommendation_session_open(
        base_url, base_headers, account_id,
    )
    if not window_open:
        bm_logger.log(
            "ali_post_quote_actions_window_closed",
            actions_hash=state_hash[:12],
        )
        return {"success": False, "delivery": "window_closed"}
    if not _provider_history_still_owned(
        account_id,
        "post_quote_existing_history",
    ):
        return {"success": False, "delivery": "ambiguous"}

    current_messages = _messages_after_latest_incoming(existing_messages)
    visible = _recommendation_visible_message(current_messages, text)
    if visible:
        visible_status = str(
            visible.get("status") or visible.get("deliveryStatus") or ""
        ).lower()
        visible_id = str(
            visible.get("id") or visible.get("messageId") or ""
        ).strip()
        if visible_id and visible_status in {"sent", "delivered", "read"}:
            if not _provider_history_still_owned(
                account_id,
                "post_quote_existing_visible",
            ):
                return {"success": False, "delivery": "ambiguous"}
            bm_logger.log(
                "ali_post_quote_actions_reconciled",
                actions_hash=state_hash[:12],
                status=visible_status,
            )
            return _delivery_result(True, "interactive", visible_id)
        if visible_status in {"failed", "rejected", "undeliverable"}:
            bm_logger.log(
                "ali_post_quote_actions_failed",
                actions_hash=state_hash[:12],
                status=visible_status,
            )
            return {"success": False, "delivery": "failed"}

    headers = dict(base_headers)
    headers["Idempotency-Key"] = f"{idempotency_key}-interactive"
    outcome, status, provider_message_id = _post_recommendation_message(
        request_url,
        headers,
        {
            "accountId": account_id,
            "message": text,
            "buttons": buttons,
        },
    )
    if outcome == "sent" and provider_message_id:
        outcome = _confirm_recommendation_status(
            request_url,
            base_headers,
            account_id,
            provider_message_id,
        )
    if outcome == "sent" and provider_message_id:
        bm_logger.log(
            "ali_post_quote_actions_sent",
            actions_hash=state_hash[:12],
        )
        return _delivery_result(True, "interactive", provider_message_id)

    # A successful HTTP response without a message id, or a transport timeout,
    # is not proof of WhatsApp delivery. Reconcile once by the visible body and
    # still require a terminal provider status before reporting success.
    try:
        reconcile_response = _provider_account_get(
            request_url,
            account_id=account_id,
            operation="post_quote_reconcile_history",
            headers=base_headers,
            params={"accountId": account_id, "limit": 20, "sortOrder": "desc"},
            timeout=15,
        )
    except http_requests.RequestException:
        reconcile_response = None
    if (
        reconcile_response is not None
        and 200 <= reconcile_response.status_code < 300
    ):
        reconcile_messages = _messages_after_latest_incoming(
            _payload_messages(_response_json(reconcile_response))
        )
        visible = _recommendation_visible_message(reconcile_messages, text)
        if visible:
            visible_status = str(
                visible.get("status") or visible.get("deliveryStatus") or ""
            ).lower()
            visible_id = str(
                visible.get("id") or visible.get("messageId") or ""
            ).strip()
            if visible_id and visible_status in {"sent", "delivered", "read"}:
                if not _provider_history_still_owned(
                    account_id,
                    "post_quote_reconcile_visible",
                ):
                    return {"success": False, "delivery": "ambiguous"}
                bm_logger.log(
                    "ali_post_quote_actions_reconciled",
                    actions_hash=state_hash[:12],
                    status=visible_status,
                )
                return _delivery_result(True, "interactive", visible_id)
            if visible_status in {"failed", "rejected", "undeliverable"}:
                outcome = "rejected"

    delivery = "failed" if outcome == "rejected" else "ambiguous"
    bm_logger.log(
        "ali_post_quote_actions_failed",
        actions_hash=state_hash[:12],
        delivery=delivery,
        status=status,
    )
    return {"success": False, "delivery": delivery}


def _send_individual_vehicle_images(
    request_url: str,
    base_headers: dict,
    account_id: str,
    existing_messages: list[dict],
    recommendation: dict,
    *,
    idempotency_suffix: str,
) -> tuple[bool, list[str]]:
    """Deliver every validated option image independently, without a picker."""
    options = recommendation.get("options") or []
    if not options or not all(
        _preflight_vehicle_media(str(option.get("whatsapp_image_url") or ""))
        for option in options
        if isinstance(option, dict)
    ):
        return False, []
    provider_ids = []
    for index, option in enumerate(options):
        media_url = str(option.get("whatsapp_image_url") or "")
        caption = "\n".join(filter(None, [
            str(option.get("name") or "").strip(),
            str(option.get("category") or "").strip(),
            f"USD ${str(option.get('daily_usd') or '').removesuffix('.00')}/day",
        ]))
        outcome, _status, _reconciled, provider_id = _send_recommendation_part(
            request_url,
            base_headers,
            account_id,
            existing_messages,
            body={
                "accountId": account_id,
                "message": caption,
                "attachmentUrl": media_url,
                "attachmentType": "image",
            },
            idempotency_key=(
                f"{recommendation['idempotency_key']}-{idempotency_suffix}-{index}"
            ),
            visible_text=caption,
            reconcile_after_trigger=True,
            trigger_message_id=str(
                recommendation.get("trigger_message_id") or ""
            ),
            trigger_sent_at=str(
                recommendation.get("trigger_sent_at") or ""
            ),
            require_delivered=True,
        )
        if outcome != "sent":
            return False, provider_ids
        if provider_id:
            provider_ids.append(provider_id)
    return len(provider_ids) == len(options), provider_ids


def recover_dm_vehicle_recommendation(
    conversation_id: str,
    account_id: str,
    recovery: dict,
    catalog: dict,
) -> dict:
    """Retry one failed carousel, then fall back to individual car images."""
    from agents.social.ali_vehicle_recommendations import (
        rebuild_vehicle_recommendation,
    )

    api_key = os.environ.get("LATE_API_KEY", "")
    plan = rebuild_vehicle_recommendation(
        recovery.get("snapshot") or {}, catalog,
    )
    stage = str(recovery.get("stage") or "retry")
    if not api_key or not plan or plan.get("kind") != "carousel":
        return {"success": False, "delivery": "invalid_recovery"}
    base_url = (
        "https://zernio.com/api/v1/inbox/conversations/"
        f"{urllib.parse.quote(conversation_id)}"
    )
    request_url = f"{base_url}/messages"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    window_open, existing_messages = _recommendation_session_open(
        base_url,
        headers,
        account_id,
        str(plan.get("trigger_sent_at") or ""),
    )
    if not window_open:
        return {"success": False, "delivery": "window_closed"}
    media_valid = all(
        _preflight_vehicle_media(str(option.get("whatsapp_image_url") or ""))
        for option in plan.get("options") or []
    )
    if stage == "retry" and media_valid:
        outcome, status, _reconciled, provider_id = _send_recommendation_part(
            request_url,
            headers,
            account_id,
            existing_messages,
            body={
                "accountId": account_id,
                "interactive": {
                    "type": "carousel",
                    "body": {"text": plan["text"]},
                    "action": {"cards": plan["cards"]},
                },
            },
            idempotency_key=f"{plan['idempotency_key']}-late-carousel-retry",
            visible_text=plan["text"],
            reconcile_after_trigger=True,
            trigger_message_id=str(plan.get("trigger_message_id") or ""),
            trigger_sent_at=str(plan.get("trigger_sent_at") or ""),
        )
        if outcome == "sent":
            return _delivery_result(
                True,
                "carousel_retry",
                provider_id,
                provider_parts={"carousel": [provider_id]},
            )
        bm_logger.log(
            "ali_vehicle_carousel_retry_failed",
            recommendation_hash=str(plan.get("state_hash") or "")[:12],
            status=status,
        )
    images_sent, image_ids = _send_individual_vehicle_images(
        request_url,
        headers,
        account_id,
        existing_messages,
        plan,
        idempotency_suffix="late-individual",
    )
    if images_sent:
        return _delivery_result(
            True,
            "individual_images",
            *image_ids,
            provider_parts={"individual_images": image_ids},
        )
    return {"success": False, "delivery": "media_recovery_failed"}


def send_dm_vehicle_recommendation(
    conversation_id: str,
    account_id: str,
    recommendation: dict,
) -> dict:
    """Send one replay-safe Ali image, carousel bundle, or recovery picker.

    A failed interactive send falls back to the same Claude-generated text.
    No free-form message is attempted when WhatsApp's service window is closed.
    """
    api_key = os.environ.get("LATE_API_KEY", "")
    kind = str(recommendation.get("kind") or "")
    state_hash = str(recommendation.get("state_hash") or "")
    idempotency_key = str(recommendation.get("idempotency_key") or "")
    trigger_message_id = str(
        recommendation.get("trigger_message_id") or ""
    ).strip()
    trigger_sent_at = str(
        recommendation.get("trigger_sent_at") or ""
    ).strip()
    text = str(recommendation.get("text") or "")
    options = recommendation.get("options") or []
    if (
        not api_key
        or kind not in {"image", "carousel", "picker"}
        or not re.fullmatch(r"[0-9a-f]{64}", state_hash)
        or not idempotency_key
        or not text
        or not isinstance(options, list)
    ):
        return {"success": False, "delivery": "invalid"}
    if kind == "picker" and not 1 <= len(options) <= 5:
        return {"success": False, "delivery": "invalid"}
    buttons = recommendation.get("buttons") or []
    if (
        kind in {"image", "picker"}
        and len(options) == 1
        and (
            not isinstance(buttons, list)
            or len(buttons) != 1
            or not isinstance(buttons[0], dict)
            or buttons[0].get("type") != "postback"
            or buttons[0].get("payload") != options[0].get("selection_id")
            or not str(buttons[0].get("title") or "")
        )
    ):
        return {"success": False, "delivery": "invalid"}
    cards = recommendation.get("cards") or []
    picker = recommendation.get("picker") or {}
    if kind in {"carousel", "picker"} and len(options) > 1:
        sections = picker.get("sections") if isinstance(picker, dict) else None
        rows = (
            sections[0].get("rows")
            if isinstance(sections, list)
            and len(sections) == 1
            and isinstance(sections[0], dict)
            else None
        )
        if (
            not 2 <= len(options) <= 5
            or (kind == "carousel" and (
                not isinstance(cards, list) or len(options) != len(cards)
            ))
            or not isinstance(rows, list)
            or len(rows) != len(options)
            or [row.get("id") for row in rows if isinstance(row, dict)]
            != [option.get("selection_id") for option in options]
            or not str(picker.get("text") or "")
            or not str(picker.get("button") or "")
            or not str(picker.get("fallback_text") or "")
        ):
            return {"success": False, "delivery": "invalid"}

    base_url = (
        "https://zernio.com/api/v1/inbox/conversations/"
        f"{urllib.parse.quote(conversation_id)}"
    )
    request_url = f"{base_url}/messages"
    base_headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    window_open, existing_messages = _recommendation_session_open(
        base_url,
        base_headers,
        account_id,
        trigger_sent_at,
    )
    if not window_open:
        bm_logger.log(
            "ali_vehicle_recommendation_window_closed",
            mode=kind,
            recommendation_hash=state_hash[:12],
        )
        return {"success": False, "delivery": "window_closed"}
    current_turn_messages = _messages_after_trigger(
        existing_messages, trigger_message_id, trigger_sent_at,
    )
    bm_logger.log(
        "ali_vehicle_recommendation_reconciliation_scope",
        mode=kind,
        recommendation_hash=state_hash[:12],
        trigger_id_present=bool(trigger_message_id),
        trigger_time_present=bool(_parse_provider_time(trigger_sent_at)),
        candidate_count=len(current_turn_messages),
    )
    if kind == "image":
        media_url = str(
            options[0].get("whatsapp_image_url")
            or options[0].get("image_url")
            or ""
        )
        primary_body = {
            "accountId": account_id,
            "message": text,
            "attachmentUrl": media_url,
            "attachmentType": "image",
            "buttons": buttons,
        }
    elif kind == "carousel":
        primary_body = {
            "accountId": account_id,
            "interactive": {
                "type": "carousel",
                "body": {"text": text},
                "action": {"cards": cards},
            },
        }
    elif len(options) == 1:
        primary_body = {
            "accountId": account_id,
            "message": text,
            "buttons": buttons,
        }
    else:
        primary_body = {
            "accountId": account_id,
            "interactive": {
                "type": "list",
                "body": {"text": str(picker["text"])},
                "action": {
                    "button": str(picker["button"]),
                    "sections": picker["sections"],
                },
            },
        }
    primary_visible_text = (
        str(picker["text"])
        if kind == "picker" and len(options) > 1
        else text
    )
    individual_provider_ids = []
    primary_part = "picker" if kind == "picker" else kind
    carousel_media_valid = (
        kind != "carousel"
        or all(
            _preflight_vehicle_media(str(option.get("whatsapp_image_url") or ""))
            for option in options
        )
    )
    if kind == "carousel" and not carousel_media_valid:
        individual_sent, individual_provider_ids = _send_individual_vehicle_images(
            request_url,
            base_headers,
            account_id,
            existing_messages,
            recommendation,
            idempotency_suffix="preflight-individual",
        )
        outcome = "sent" if individual_sent else "rejected"
        status = None
        primary_reconciled = False
        primary_provider_id = ""
        primary_part = "individual_images"
        bm_logger.log(
            "ali_vehicle_carousel_preflight_failed",
            option_count=len(options),
            individual_fallback=individual_sent,
            recommendation_hash=state_hash[:12],
        )
    else:
        outcome, status, primary_reconciled, primary_provider_id = _send_recommendation_part(
            request_url,
            base_headers,
            account_id,
            existing_messages,
            body=primary_body,
            idempotency_key=f"{idempotency_key}-primary",
            visible_text=primary_visible_text,
            reconcile_after_trigger=True,
            trigger_message_id=trigger_message_id,
            trigger_sent_at=trigger_sent_at,
            require_delivered=(kind == "carousel"),
        )
    if outcome == "sent":
        bm_logger.log(
            (
                "ali_vehicle_recommendation_reconciled"
                if primary_reconciled
                else "ali_vehicle_recommendation_sent"
            ),
            mode=kind,
            option_count=len(options),
            recommendation_hash=state_hash[:12],
        )
        primary_ids = (
            individual_provider_ids
            if primary_part == "individual_images"
            else [primary_provider_id]
        )
        try:
            state_registry.wa_stage_vehicle_recommendation_delivery(
                conversation_id,
                recommendation,
                primary_part,
                {primary_part: primary_ids},
                account_id,
            )
        except Exception as exc:
            bm_logger.log(
                "ali_vehicle_recommendation_stage_failed",
                error=type(exc).__name__,
                recommendation_hash=state_hash[:12],
            )
        if kind in {"image", "picker"}:
            return _delivery_result(
                True,
                "picker" if kind == "picker" else "image",
                primary_provider_id,
                provider_parts={primary_part: [primary_provider_id]},
            )
    else:
        if outcome == "ambiguous":
            bm_logger.log(
                "ali_vehicle_recommendation_ambiguous",
                mode=kind,
                status=status,
                recommendation_hash=state_hash[:12],
            )
            return {"success": False, "delivery": "ambiguous"}

        fallback_text = text
        if kind == "carousel":
            fallback_text = str(picker.get("fallback_text") or text)
        elif kind == "picker":
            fallback_text = str(
                recommendation.get("fallback_text")
                or picker.get("fallback_text")
                or text
            )
        fallback_headers = dict(base_headers)
        fallback_headers["Idempotency-Key"] = f"{idempotency_key}-fallback"
        fallback_outcome, fallback_status, fallback_provider_id = _post_recommendation_message(
            request_url,
            fallback_headers,
            {"accountId": account_id, "message": fallback_text},
        )
        if fallback_outcome == "sent" and fallback_provider_id:
            fallback_outcome = _confirm_recommendation_status(
                request_url,
                base_headers,
                account_id,
                fallback_provider_id,
            )
        if fallback_outcome == "sent":
            bm_logger.log(
                "ali_vehicle_recommendation_fallback_sent",
                mode=kind,
                primary_status=status,
                recommendation_hash=state_hash[:12],
            )
            return _delivery_result(
                True,
                "picker_fallback" if kind == "picker" else "fallback",
                fallback_provider_id,
                provider_parts={"picker_fallback": [fallback_provider_id]},
            )
        if fallback_outcome == "ambiguous":
            bm_logger.log(
                "ali_vehicle_recommendation_fallback_ambiguous",
                mode=kind,
                primary_status=status,
                fallback_status=fallback_status,
                recommendation_hash=state_hash[:12],
            )
            return {"success": False, "delivery": "ambiguous"}
        bm_logger.log(
            "ali_vehicle_recommendation_failed",
            mode=kind,
            primary_status=status,
            fallback_status=fallback_status,
            recommendation_hash=state_hash[:12],
        )
        return {"success": False, "delivery": "failed"}

    picker_text = str(picker["text"])
    picker_body = {
        "accountId": account_id,
        "interactive": {
            "type": "list",
            "body": {"text": picker_text},
            "action": {
                "button": str(picker["button"]),
                "sections": picker["sections"],
            },
        },
    }
    # The picker is independently idempotent, but may reconcile only inside
    # this trigger's causal window.  Never reuse the globally repeated picker
    # body from an older recommendation bundle.
    picker_existing_messages = existing_messages
    picker_outcome, picker_status, picker_reconciled, picker_provider_id = _send_recommendation_part(
        request_url,
        base_headers,
        account_id,
        picker_existing_messages,
        body=picker_body,
        idempotency_key=f"{idempotency_key}-picker",
        visible_text=picker_text,
        reconcile_after_trigger=True,
        trigger_message_id=trigger_message_id,
        trigger_sent_at=trigger_sent_at,
    )
    if picker_outcome == "sent":
        bm_logger.log(
            (
                "ali_vehicle_picker_reconciled"
                if picker_reconciled
                else "ali_vehicle_picker_sent"
            ),
            option_count=len(options),
            recommendation_hash=state_hash[:12],
        )
        primary_ids = (
            individual_provider_ids
            if primary_part == "individual_images"
            else [primary_provider_id]
        )
        try:
            state_registry.wa_stage_vehicle_recommendation_delivery(
                conversation_id,
                recommendation,
                "individual_picker"
                if primary_part == "individual_images"
                else "carousel_picker",
                {
                    primary_part: primary_ids,
                    "picker": [picker_provider_id],
                },
                account_id,
            )
        except Exception as exc:
            bm_logger.log(
                "ali_vehicle_recommendation_stage_failed",
                error=type(exc).__name__,
                recommendation_hash=state_hash[:12],
            )
        return _delivery_result(
            True,
            "individual_picker" if primary_part == "individual_images" else "carousel_picker",
            *primary_ids,
            picker_provider_id,
            provider_parts={
                primary_part: primary_ids,
                "picker": [picker_provider_id],
            },
        )
    if picker_outcome == "ambiguous":
        bm_logger.log(
            "ali_vehicle_picker_ambiguous",
            status=picker_status,
            recommendation_hash=state_hash[:12],
        )

    picker_fallback_text = str(picker["fallback_text"])
    fallback_outcome, fallback_status, _, fallback_provider_id = _send_recommendation_part(
        request_url,
        base_headers,
        account_id,
        existing_messages,
        body={"accountId": account_id, "message": picker_fallback_text},
        idempotency_key=f"{idempotency_key}-picker-fallback",
        visible_text=picker_fallback_text,
        reconcile_after_trigger=True,
        trigger_message_id=trigger_message_id,
        trigger_sent_at=trigger_sent_at,
    )
    if fallback_outcome == "sent":
        bm_logger.log(
            "ali_vehicle_picker_fallback_sent",
            picker_status=picker_status,
            recommendation_hash=state_hash[:12],
        )
        return _delivery_result(
            True,
            "individual_picker_fallback"
            if primary_part == "individual_images"
            else "carousel_picker_fallback",
            *(individual_provider_ids or [primary_provider_id]),
            fallback_provider_id,
            provider_parts={
                primary_part: individual_provider_ids or [primary_provider_id],
                "picker_fallback": [fallback_provider_id],
            },
        )
    bm_logger.log(
        "ali_vehicle_picker_failed",
        picker_status=picker_status,
        fallback_status=fallback_status,
        recommendation_hash=state_hash[:12],
    )
    return {"success": False, "delivery": "picker_failed"}


@_maintenance_participating('transport')
def send_dm_reply(conversation_id: str, account_id: str, text: str,
                  attachment_url: str = "",
                  attachment_type: str = "image",
                  confirm_delivery: bool = False,
                  idempotency_key: str = "", attachment_name: str = "") -> bool:
    """Send a DM reply; optionally require provider delivery confirmation."""
    if attachment_url:
        return send_dm_reply_with_attachment(
            conversation_id=conversation_id,
            account_id=account_id,
            text=text,
            attachment_url=attachment_url,
            attachment_type=attachment_type,
            attachment_name=attachment_name,
            idempotency_key=idempotency_key,
            reconcile_existing=not (confirm_delivery or idempotency_key),
        )

    api_key = os.environ.get("LATE_API_KEY", "")
    if not api_key:
        bm_logger.log("zernio_dm_no_api_key")
        return False

    if confirm_delivery or idempotency_key:
        return _confirmed_text_reply(
            conversation_id=conversation_id,
            account_id=account_id,
            text=text,
            api_key=api_key,
            idempotency_key=idempotency_key,
        )

    client = _get_client()
    if not client:
        return False
    try:
        if not _provider_mutation_account_allowed(account_id, "sdk_text_reply"):
            return False
        client.inbox.send_inbox_message(
            conversation_id=conversation_id,
            account_id=account_id,
            message=text,
        )
        bm_logger.log("zernio_dm_sent", conversation_id=conversation_id[:20])
        return True
    except Exception as e:
        from shared.mermaid_maintenance import retain_uncertain_outbound
        retain_uncertain_outbound()
        bm_logger.log("zernio_dm_send_failed", conversation_id=conversation_id[:20],
                       error=str(e)[:200])
        return False

@_maintenance_participating('transport')
def send_dm_template(
    conversation_id: str,
    account_id: str,
    template_name: str,
    language: str = "es",
) -> bool:
    """Send and confirm an approved WhatsApp template in an existing thread."""
    api_key = os.environ.get("LATE_API_KEY", "")
    if not api_key:
        raise ZernioReplyError("No está configurada la conexión con WhatsApp.")
    if not _provider_mutation_account_allowed(account_id, "whatsapp_template_preflight"):
        return False

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    templates_response = _provider_account_get(
        "https://zernio.com/api/v1/whatsapp/templates",
        account_id=account_id,
        operation="whatsapp_template_catalog",
        headers=headers,
        params={"accountId": account_id},
        timeout=15,
    )
    if templates_response is None:
        return False
    if not _provider_history_still_owned(
        account_id,
        "whatsapp_template_catalog",
    ):
        return False
    templates_payload = _response_json(templates_response)
    templates = templates_payload.get("templates", [])
    selected = next(
        (
            item for item in templates
            if isinstance(item, dict)
            and item.get("name") == template_name
            and str(item.get("language") or "") == language
        ),
        None,
    )
    template_status = str(selected.get("status") or "").upper() if selected else ""
    if template_status != "APPROVED":
        bm_logger.log(
            "zernio_whatsapp_template_unavailable",
            template_name=template_name,
            status=template_status or "missing",
        )
        if template_status == "PENDING":
            raise ZernioReplyError(
                "La plantilla de seguimiento está pendiente de aprobación de Meta. "
                "Todavía no se puede reabrir esta conversación."
            )
        raise ZernioReplyError(
            "No hay una plantilla de WhatsApp aprobada para reabrir esta conversación."
        )

    base_url = (
        "https://zernio.com/api/v1/inbox/conversations/"
        f"{urllib.parse.quote(conversation_id)}/messages"
    )
    if not _provider_mutation_account_allowed(account_id, "whatsapp_template"):
        return False
    send_response = http_requests.post(
        base_url,
        headers=headers,
        json={
            "accountId": account_id,
            "template": {
                "elements": [{
                    "name": template_name,
                    "language": language,
                }],
            },
        },
        timeout=15,
    )
    payload = _response_json(send_response)
    if not 200 <= send_response.status_code < 300:
        provider_error = (
            payload.get("message")
            or payload.get("error")
            or payload.get("detail")
            or send_response.text[:200]
        )
        bm_logger.log(
            "zernio_whatsapp_template_send_failed",
            template_name=template_name,
            status=send_response.status_code,
            error=str(provider_error)[:200],
        )
        raise ZernioReplyError(
            "WhatsApp rechazó la plantilla de seguimiento. No se ha enviado."
        )

    message_id = _response_message_id(send_response)
    if not message_id:
        raise ZernioReplyError(
            "WhatsApp no devolvió una referencia verificable de la plantilla."
        )

    for attempt in range(8):
        if attempt:
            time.sleep(0.5)
        status_response = _provider_account_get(
            base_url,
            account_id=account_id,
            operation="whatsapp_template_delivery_status",
            headers=headers,
            params={"accountId": account_id, "limit": 20, "sortOrder": "desc"},
            timeout=15,
        )
        if status_response is None:
            break
        if not 200 <= status_response.status_code < 300:
            continue
        messages = _payload_messages(_response_json(status_response))
        matched = next(
            (
                item for item in messages
                if message_id
                and str(item.get("id") or item.get("messageId") or "") == message_id
            ),
            None,
        )
        if not matched:
            continue
        if not _provider_history_still_owned(
            account_id,
            "whatsapp_template_delivery_status",
        ):
            break
        status = str(
            matched.get("status") or matched.get("deliveryStatus") or ""
        ).lower()
        if status in {"sent", "delivered", "read"}:
            bm_logger.log(
                "zernio_whatsapp_template_confirmed",
                template_name=template_name,
                status=status,
            )
            return True
        if status in {"failed", "rejected", "undeliverable"}:
            bm_logger.log(
                "zernio_whatsapp_template_delivery_failed",
                template_name=template_name,
                status=status,
            )
            raise ZernioReplyError(
                "WhatsApp marcó la plantilla como fallida. No se ha entregado."
            )

    raise ZernioReplyError(
        "WhatsApp no confirmó el envío de la plantilla de seguimiento."
    )


@_maintenance_participating('transport')
def send_dm_reply_with_attachment(conversation_id: str, account_id: str, text: str,
                                  attachment_url: str,
                                  attachment_type: str = "image",
                                  attachment_name: str = "",
                                  idempotency_key: str = "",
                                  reconcile_existing: bool = True) -> bool:
    """Send a Zernio inbox message with a public attachment URL.

    The current Python SDK wrapper only exposes text parameters for
    send_inbox_message, so attachment sends use Zernio's documented REST shape.
    Resource-specific callers (for example a unique quote PDF URL) may reconcile
    an existing identical attachment. Operator actions disable that shortcut:
    a new action must prove its own provider message, even if content repeats.
    """
    api_key = os.environ.get("LATE_API_KEY", "")
    if not api_key:
        bm_logger.log("zernio_dm_no_api_key")
        return False
    if attachment_type not in {"image", "video", "audio", "file"}:
        bm_logger.log("zernio_dm_attachment_invalid_type",
                      attachment_type=attachment_type)
        return False
    url = (
        "https://zernio.com/api/v1/inbox/conversations/"
        f"{urllib.parse.quote(conversation_id)}/messages"
    )
    body = {
        "accountId": account_id,
        "message": text or "",
        "attachmentUrl": attachment_url,
        "attachmentType": attachment_type,
    }
    if attachment_type == "file" and str(attachment_name or "").strip():
        safe_name = os.path.basename(
            str(attachment_name).replace("\x00", "").replace("\r", "").replace("\n", "")
        ).strip()[:160]
        if safe_name:
            body["attachmentName"] = safe_name
    try:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key

            base_url = url.removesuffix("/messages")
            window_open, existing_messages = _recommendation_session_open(
                base_url, headers, account_id,
            )
            if not window_open:
                bm_logger.log(
                    "zernio_dm_attachment_window_closed",
                    conversation_id=conversation_id[:20],
                    attachment_type=attachment_type,
                )
                return False

            existing_ids = {
                str(item.get("id") or item.get("messageId") or "").strip()
                for item in existing_messages
            }
            send_started_at = datetime.now(timezone.utc)

            def matching_attachment(
                messages: list[dict], *, after_send: bool = False,
            ) -> dict | None:
                if not reconcile_existing and not after_send:
                    return None
                for item in messages:
                    if not reconcile_existing:
                        provider_id = str(
                            item.get("id") or item.get("messageId") or ""
                        ).strip()
                        created_at = _parse_provider_time(
                            item.get("createdAt") or item.get("created_at")
                        )
                        if (
                            not provider_id or provider_id in existing_ids
                            or created_at is None or created_at < send_started_at
                        ):
                            continue
                    if (
                        str(item.get("direction") or "").lower() != "outgoing"
                        or _recommendation_message_text(item) != body["message"]
                    ):
                        continue
                    urls = {
                        str(item.get("attachmentUrl") or "").strip(),
                        str(item.get("attachment_url") or "").strip(),
                    }
                    attachments = item.get("attachments")
                    if isinstance(attachments, list):
                        for attachment in attachments:
                            if not isinstance(attachment, dict):
                                continue
                            urls.add(str(
                                attachment.get("url")
                                or attachment.get("publicUrl")
                                or attachment.get("attachmentUrl")
                                or ""
                            ).strip())
                    if body["attachmentUrl"] in urls:
                        return item
                return None

            def confirmed_attachment(item: dict | None) -> str:
                if not item:
                    return "missing"
                if not _provider_history_still_owned(
                    account_id,
                    "attachment_history_reconcile",
                ):
                    return "ambiguous"
                provider_id = str(
                    item.get("id") or item.get("messageId") or ""
                ).strip()
                status = str(
                    item.get("status") or item.get("deliveryStatus") or ""
                ).lower()
                terminal_success = (
                    {"delivered", "read"}
                    if attachment_type == "file"
                    else {"sent", "delivered", "read"}
                )
                if provider_id and status in terminal_success:
                    return "sent"
                if status in {"failed", "rejected", "undeliverable"}:
                    return "rejected"
                if provider_id:
                    return _confirm_recommendation_status(
                        url, headers, account_id, provider_id,
                        require_delivered=attachment_type == "file",
                    )
                return "ambiguous"

            if not _provider_history_still_owned(
                account_id,
                "attachment_existing_history",
            ):
                return False
            existing = matching_attachment(existing_messages)
            if existing:
                existing_outcome = confirmed_attachment(existing)
                if existing_outcome == "sent":
                    bm_logger.log(
                        "zernio_dm_attachment_reconciled",
                        conversation_id=conversation_id[:20],
                        attachment_type=attachment_type,
                    )
                    return True
                if existing_outcome == "rejected" and attachment_type == "file":
                    failed_matches = sum(
                        1
                        for item in existing_messages
                        if matching_attachment([item]) is not None
                        and str(
                            item.get("status")
                            or item.get("deliveryStatus")
                            or ""
                        ).lower() in {"failed", "rejected", "undeliverable"}
                    )
                    retry_number = max(1, failed_matches)
                    headers["Idempotency-Key"] = (
                        f"{idempotency_key}-retry-{retry_number}"
                    )[:255]
                    bm_logger.log(
                        "zernio_dm_file_attachment_retrying",
                        conversation_id=conversation_id[:20],
                        failed_attempts=failed_matches,
                        retry_number=retry_number,
                    )
                else:
                    bm_logger.log(
                        "zernio_dm_attachment_delivery_unconfirmed",
                        conversation_id=conversation_id[:20],
                        attachment_type=attachment_type,
                        outcome=existing_outcome,
                    )
                    return False

            outcome, status, provider_id = _post_recommendation_message(
                url, headers, body,
            )
            if outcome == "sent" and provider_id:
                outcome = _confirm_recommendation_status(
                    url, headers, account_id, provider_id,
                    require_delivered=attachment_type == "file",
                )
            if outcome == "sent" and provider_id:
                bm_logger.log(
                    "zernio_dm_attachment_delivery_confirmed",
                    conversation_id=conversation_id[:20],
                    attachment_type=attachment_type,
                )
                return True
            if outcome == "rejected":
                bm_logger.log(
                    "zernio_dm_attachment_delivery_failed",
                    conversation_id=conversation_id[:20],
                    attachment_type=attachment_type,
                    status=status,
                )
                return False

            try:
                reconcile_response = _provider_account_get(
                    url,
                    account_id=account_id,
                    operation="attachment_reconcile_history",
                    headers=headers,
                    params={
                        "accountId": account_id,
                        "limit": 20,
                        "sortOrder": "desc",
                    },
                    timeout=15,
                )
            except http_requests.RequestException:
                reconcile_response = None
            reconciled = None
            if (
                reconcile_response is not None
                and 200 <= reconcile_response.status_code < 300
            ):
                reconciled = matching_attachment(
                    _payload_messages(_response_json(reconcile_response)),
                    after_send=True,
                )
            reconcile_outcome = confirmed_attachment(reconciled)
            if reconcile_outcome == "sent":
                bm_logger.log(
                    "zernio_dm_attachment_reconciled",
                    conversation_id=conversation_id[:20],
                    attachment_type=attachment_type,
                )
                return True
            bm_logger.log(
                "zernio_dm_attachment_delivery_unconfirmed",
                conversation_id=conversation_id[:20],
                attachment_type=attachment_type,
                outcome=reconcile_outcome,
                status=status,
            )
            return False

        # Legacy/no-key attachment sends bypass the idempotent structured
        # transport above. Keep the same final tenant boundary immediately at
        # their sole provider POST so a stored quote/account route cannot send
        # after that Zernio account has been reassigned.
        if not _provider_mutation_account_allowed(account_id, "legacy_attachment"):
            bm_logger.log("zernio_attachment_send_account_not_allowlisted")
            return False
        resp = http_requests.post(
            url,
            headers=headers,
            json=body,
            timeout=15,
        )
        if not 200 <= resp.status_code < 300:
            bm_logger.log("zernio_dm_attachment_send_failed",
                          conversation_id=conversation_id[:20],
                          status=resp.status_code,
                          error=resp.text[:200])
            return False
        provider_id = _response_message_id(resp)
        if not provider_id:
            bm_logger.log(
                "zernio_dm_attachment_delivery_unconfirmed",
                conversation_id=conversation_id[:20],
                attachment_type=attachment_type,
                outcome="missing_provider_message_id",
                status=resp.status_code,
            )
            return False
        outcome = _confirm_recommendation_status(
            url,
            headers,
            account_id,
            provider_id,
            require_delivered=attachment_type == "file",
        )
        if outcome != "sent":
            bm_logger.log(
                "zernio_dm_attachment_delivery_unconfirmed",
                conversation_id=conversation_id[:20],
                attachment_type=attachment_type,
                outcome=outcome,
                status=resp.status_code,
            )
            return False
        bm_logger.log(
            "zernio_dm_attachment_delivery_confirmed",
            conversation_id=conversation_id[:20],
            attachment_type=attachment_type,
        )
        return True
    except Exception as e:
        bm_logger.log("zernio_dm_attachment_send_failed",
                      conversation_id=conversation_id[:20],
                      error=str(e)[:200])
        return False


@_maintenance_participating('transport')
def send_typing_indicator(conversation_id: str, account_id: str):
    """Send typing indicator via Zernio. Best-effort, no error on failure."""
    client = _get_client()
    if not client:
        return
    try:
        if not _provider_mutation_account_allowed(account_id, "typing_indicator"):
            return
        client.messages.send_typing_indicator(
            conversation_id=conversation_id,
            account_id=account_id,
        )
    except Exception:
        pass  # Typing indicator is cosmetic — never block on failure
