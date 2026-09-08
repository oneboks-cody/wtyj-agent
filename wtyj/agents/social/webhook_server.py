# bluemarlin/agents/social/webhook_server.py
# Created: Brief 067
# Last modified: Brief 138
# Purpose: FastAPI webhook receiver for Meta WhatsApp Cloud API

import json as _json
import hashlib
import hmac
import os
import re
import time
import threading
from requests import RequestException
from fastapi import BackgroundTasks, FastAPI, Request, Query
from fastapi.responses import PlainTextResponse
from starlette.concurrency import run_in_threadpool
from anyio import CapacityLimiter

from shared.bm_logger import log
from shared import state_registry
from shared import config_loader
from shared import response_timing
from shared import icp_overrides
from agents.social.whatsapp_client import parse_webhook_payload, send_text_message
from agents.social.social_agent import handle_incoming_whatsapp_message
from agents.social.zernio_dm_client import (
    parse_zernio_failed_webhook,
    parse_zernio_webhook,
    parse_zernio_sent_webhook,
    verify_webhook_signature,
    send_dm_reply,
    send_dm_quote_confirmation,
    send_dm_vehicle_recommendation,
    recover_dm_vehicle_recommendation,
    send_typing_indicator,
    set_provider_mutation_guard,
    reset_provider_mutation_guard,
    provider_mutation_scope,
    ZernioReplyError,
)
from agents.social.dm_agent import handle_incoming_dm
from agents.social.channels import ZERNIO_CHANNELS, DEFAULT_ZERNIO_CHANNEL
from agents.social.senders import send_reply
from agents.social.ali_quote_workflow import (
    QUOTE_CONFIRMATION_FALLBACK_INSTRUCTION,
    commit_ali_turn_delivery,
    mark_quote_confirmation_failure_recovered,
    reconcile_quote_confirmation_failure,
    get_intake_catalog,
)

from contextlib import asynccontextmanager


def _quote_confirmation_fallback_text(
    conversation_id: str,
    source_text: str,
) -> str:
    """Return Ali's locale-correct, idempotent text confirmation fallback."""
    state = state_registry.wa_get_booking_state(conversation_id)
    locale = str(
        ((state.get("fields") or {}).get("conversation_language") or "en")
    ).strip().lower()
    instruction = QUOTE_CONFIRMATION_FALLBACK_INSTRUCTION.get(
        locale,
        QUOTE_CONFIRMATION_FALLBACK_INSTRUCTION["en"],
    )
    text = str(source_text or "").strip()
    if any(text.endswith(value) for value in QUOTE_CONFIRMATION_FALLBACK_INSTRUCTION.values()):
        return text
    return f"{text}\n\n{instruction}" if text else instruction

@asynccontextmanager
async def lifespan(app):
    # Brief 190: content pipeline archived — scheduler only starts when explicitly enabled
    if config_loader.get_raw().get("features", {}).get("content_pipeline", False):
        from agents.social.scheduler import start_scheduler
        start_scheduler()
    from agents.social.ali_quote_workflow import resume_pending_processing
    resume_pending_processing()
    recovery_stop = None
    recovery_thread = None
    workflow_type = str(
        (config_loader.get_raw().get("workflow") or {}).get("type") or ""
    )
    if workflow_type == "ali_quote":
        _run_ali_document_retention_cleanup()
    elif workflow_type == "mermaid_reservation_demo":
        repaired = state_registry.reconcile_mermaid_escalation_freezes()
        log("mermaid_escalation_state_reconciled", **repaired)
    # Every WhatsApp tenant needs durable recovery after provider acceptance.
    # Ali additionally runs its workflow schedulers and customer heartbeat.
    recovery_stop = threading.Event()
    recovery_thread = threading.Thread(
        target=_ali_inbound_recovery_loop,
        args=(recovery_stop, workflow_type == "ali_quote"),
        name="whatsapp-inbound-recovery",
        daemon=True,
    )
    recovery_thread.start()
    try:
        yield
    finally:
        if recovery_stop is not None:
            recovery_stop.set()
        if recovery_thread is not None:
            recovery_thread.join(timeout=2)

app = FastAPI(title="WTYJ Agent", docs_url=None, redoc_url=None, lifespan=lifespan)

from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    # J3-N2-13: replaced the previous mixed allow_origins + regex with
    # the owner-specified spec. Explicit production origin +
    # any-localhost-port via regex (Starlette does not interpret "*"
    # inside an origin string, so "http://localhost:*" can only be
    # honoured via allow_origin_regex).
    allow_origins=["https://dashboard.unboks.org"],
    allow_origin_regex=r"^http://localhost(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _use_whatsapp_orchestrator(channel: str) -> bool:
    """Use the structured agent for booking and dedicated WhatsApp workflows."""
    raw = config_loader.get_raw() or {}
    if (raw.get("features") or {}).get("booking_flow", True):
        return True
    workflow = raw.get("workflow") or {}
    return (
        str(channel or "").strip().lower() == "whatsapp"
        and workflow.get("type") in {
            "callback_follow_up", "ali_quote", "mermaid_reservation_demo"
        }
    )


def _sanitize_tenant_whatsapp_reply(reply_text: str, channel: str) -> str:
    """Apply tenant-specific safety at the final AI outbound boundary."""
    text = str(reply_text or "").strip()
    if not text or str(channel or "").strip().lower() != "whatsapp":
        return text
    # Convert unambiguous Markdown strong spans in AI prose to WhatsApp's
    # native single-asterisk form. Keep code spans, multiline constructs,
    # triple emphasis, exponent-like text, and unbalanced delimiters intact.
    code_aware_parts = re.split(r"(`[^`\n]*`)", text)
    for index in range(0, len(code_aware_parts), 2):
        code_aware_parts[index] = re.sub(
            r"(?<![\w*])\*\*(?=\S)([^*\n]*?\S)\*\*(?![\w*])",
            r"*\1*",
            code_aware_parts[index],
        )
    text = "".join(code_aware_parts)
    try:
        from agents.social.ali_quote_workflow import (
            sanitize_intake_reply,
            tenant_configured,
        )
        if not tenant_configured():
            return text
        safe_text = sanitize_intake_reply(text)
        if safe_text != text:
            log("ali_quote_outbound_reply_sanitized", channel="whatsapp")
        return safe_text
    except Exception as exc:
        log("ali_quote_outbound_safety_failed", error=str(exc)[:200])
        return ""

from dashboard.api import public_router as ali_public_router, router as dashboard_router
app.include_router(dashboard_router)
app.include_router(ali_public_router)

# Brief 207: Tasks API mounted at root level (/tasks/*) so SR's frontend's
# /api/unboks/tasks calls (after nginx prefix-strip → /tasks) hit it directly.
from dashboard.tasks_api import router as tasks_router
app.include_router(tasks_router)


@app.get("/api/public/ali-quote/{public_id}")
async def download_ali_quote(public_id: str, expires: int, signature: str):
    """Serve one private quote through a 60-minute HMAC URL."""
    from agents.social.ali_quote_download import quote_download_response
    return quote_download_response(public_id, expires, signature)


@app.get("/api/public/mermaid-document/{public_id}")
async def download_mermaid_document(public_id: str, expires: int, signature: str, card: bool = False):
    """Serve a private Mermaid quote or receipt through an expiring HMAC URL."""
    if card:
        from agents.social.mermaid_document_cards import download_response
        return download_response(public_id, expires, signature)
    from agents.social.mermaid_documents import document_response
    return document_response(public_id, expires, signature)


@app.get("/api/public/mermaid-card-image/{digest}.png")
async def mermaid_card_image(digest: str):
    from agents.social.mermaid_document_cards import image_response
    return image_response(digest)


@app.get("/api/public/mermaid-demo-payment/{reservation_id}")
async def mermaid_demo_checkout(reservation_id: str, expires: int, signature: str):
    from agents.social.mermaid_demo_payment import checkout_page
    return checkout_page(reservation_id, expires, signature)


_mermaid_checkout_limiter = CapacityLimiter(1)


@app.get("/pay/{token}")
async def mermaid_short_checkout(token: str):
    from agents.social.mermaid_demo_payment import short_checkout_page
    return await run_in_threadpool(short_checkout_page, token)


@app.post("/pay/{token}")
async def mermaid_short_checkout_complete(request: Request, token: str):
    from agents.social.mermaid_demo_payment import complete_short_checkout
    form = await request.form()
    async with _mermaid_checkout_limiter:
        return await run_in_threadpool(complete_short_checkout, token, str(form.get("status") or "cancel"))


@app.post("/api/public/mermaid-demo-payment/{reservation_id}")
async def mermaid_demo_checkout_complete(
    request: Request, reservation_id: str, expires: int, signature: str,
):
    from agents.social.mermaid_demo_payment import complete_checkout
    form = await request.form()
    # Serialize checkouts without blocking the PDF/health routes or occupying
    # worker threads for queued duplicate callbacks.
    async with _mermaid_checkout_limiter:
        return await run_in_threadpool(
            complete_checkout, reservation_id, expires, signature,
            str(form.get("status") or "cancel"),
        )

_VERIFY_TOKEN = os.environ.get("WHATSAPP_VERIFY_TOKEN", "")
_last_cleanup_ts = 0


class _RetryableZernioSentControlError(RuntimeError):
    """The operator echo cannot be accepted while strict controls are unknown."""


class _RetryableZernioFailureControlError(RuntimeError):
    """A failed-delivery event cannot be reconciled with unknown ownership."""


def _verify_meta_webhook_signature(body: bytes, signature: str) -> bool:
    """Verify Meta's X-Hub-Signature-256 without logging request content."""
    secret = os.environ.get("META_APP_SECRET", "").strip()
    supplied = str(signature or "").strip()
    if not secret or not supplied.lower().startswith("sha256="):
        return False
    supplied_digest = supplied.split("=", 1)[1].strip().lower()
    if len(supplied_digest) != 64:
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, supplied_digest)


def _meta_payload_matches_tenant(payload: dict) -> bool:
    """Require every direct-Meta event to target this configured tenant."""
    expected_phone = os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "").strip()
    expected_waba = os.environ.get("WHATSAPP_BUSINESS_ACCOUNT_ID", "").strip()
    if not expected_phone or not expected_waba:
        return False
    if payload.get("object") != "whatsapp_business_account":
        return False
    entries = payload.get("entry")
    if not isinstance(entries, list) or not entries:
        return False
    saw_destination = False
    for entry in entries:
        if not isinstance(entry, dict) or str(entry.get("id") or "") != expected_waba:
            return False
        changes = entry.get("changes")
        if not isinstance(changes, list):
            return False
        for change in changes:
            value = change.get("value") if isinstance(change, dict) else None
            if not isinstance(value, dict):
                return False
            if "messages" not in value and "statuses" not in value:
                continue
            metadata = value.get("metadata")
            if not isinstance(metadata, dict):
                return False
            if str(metadata.get("phone_number_id") or "") != expected_phone:
                return False
            saw_destination = True
    return saw_destination


def _direct_meta_batch_destination_state(messages: list[dict]) -> bool | None:
    """Compare every signed batch destination with the current Meta tenant."""
    expected_phone = os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "").strip()
    expected_waba = os.environ.get(
        "WHATSAPP_BUSINESS_ACCOUNT_ID", ""
    ).strip()
    if not expected_phone or not expected_waba:
        return None
    if not messages:
        return False
    return all(
        str(message.get("phone_number_id") or "") == expected_phone
        and str(message.get("business_account_id") or "") == expected_waba
        for message in messages
    )

# Message batching coalesces rapid customer messages into one Marina call. It
# does NOT protect against concurrent orchestrator access - the per-phone lock
# below solves that different problem. Timing is tenant-configurable via
# client.json/Nr2 and optional Nr3 admin override.

_message_buffers = {}   # phone -> {"messages": [...], "timer": Timer, "started": float}
_buffer_lock = threading.Lock()


def _recovery_buffer_key(phone: str, batch_id: str) -> str:
    """Keep a recovered durable batch isolated from newly arriving messages."""
    return f"{phone}\x1erecovery-batch:{batch_id}"


def _stage_recovered_batch(
    phone: str,
    batch_id: str,
    messages: list[dict],
    *,
    processing_token: str,
) -> str:
    """Publish one complete recovered batch without a partial-batch timer."""
    if not batch_id or not messages:
        raise ValueError("A recovered batch needs an identity and messages")
    buffer_key = _recovery_buffer_key(phone, batch_id)
    with _buffer_lock:
        _message_buffers[buffer_key] = {
            "messages": list(messages),
            "timer": None,
            "started": time.time(),
            "timing": {},
            "phone": phone,
            "batch_id": batch_id,
            "recovery": True,
            "processing_token": str(processing_token or ""),
        }
    return buffer_key


# Brief 161: per-phone lock serializes concurrent handle_incoming_whatsapp_message
# calls for the same phone/conversation. Fixes race where msg 2 reads stale state
# before msg 1 has persisted its orchestrator output. Keyed by conversation_id
# (Zernio) or phone (legacy Meta). Registry grows monotonically; locks are cheap.
_phone_locks = {}  # key -> threading.Lock
_phone_locks_registry_lock = threading.Lock()


def _get_phone_lock(key: str) -> threading.Lock:
    """Get or create a per-phone lock for serializing orchestrator calls."""
    with _phone_locks_registry_lock:
        lock = _phone_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _phone_locks[key] = lock
        return lock


def _message_ids(messages: list[dict]) -> list[str]:
    return [m.get("message_id", "") for m in messages or [] if m.get("message_id")]


def _acceptance_batch_bindings(messages: list[dict]) -> dict[str, tuple[str, int]]:
    """Return stable pre-ACK recovery groups for normalized provider events."""
    groups: dict[tuple[str, str], list[str]] = {}
    for msg in messages or []:
        message_id = str(msg.get("message_id") or "").strip()
        if not message_id:
            continue
        # Direct-Meta non-text events are terminalized independently by the
        # background normalizer and must not poison a recoverable text batch.
        conversation = str(msg.get("conversation_id") or msg.get("from") or "")
        channel = str(msg.get("channel") or "whatsapp")
        discriminator = message_id if msg.get("text") is None else "text"
        groups.setdefault((conversation, channel + "\x1f" + discriminator), []).append(
            message_id
        )
    bindings: dict[str, tuple[str, int]] = {}
    for message_ids in groups.values():
        batch_id = hashlib.sha256(
            (
                "whatsapp-inbound-acceptance-v1\x1f"
                + "\x1f".join(message_ids)
            ).encode("utf-8")
        ).hexdigest()
        for position, message_id in enumerate(message_ids):
            bindings[message_id] = (batch_id, position)
    return bindings


def _record_inbound(msg: dict, channel: str, conversation_id: str):
    state_registry.inbound_processing_record(
        msg.get("message_id", ""),
        conversation_id=conversation_id or msg.get("from", ""),
        channel=channel,
        status="received",
        payload=msg,
    )


_ALI_HEARTBEAT_COPY = {
    "en": "I’m still checking that for you. I’ll continue here shortly.",
    "nl": "Ik ben dit nog voor je aan het nakijken. Ik ga hier zo verder.",
    "pap": "Mi ta sigui wak esaki pa bo. Mi ta sigui aki mes un tiki mas lat.",
    "de": "Ich prüfe das noch für Sie. Ich mache hier gleich weiter.",
}

_ALI_PROVIDER_SEND_MAX_RECOVERY_ATTEMPTS = 3


def _ali_recovery_heartbeat(conversation_id: str) -> str:
    state = state_registry.wa_get_booking_state(conversation_id) or {}
    locale = str((state.get("fields") or {}).get("conversation_language") or "en")
    return _ALI_HEARTBEAT_COPY.get(locale.lower(), _ALI_HEARTBEAT_COPY["en"])


def _recover_stale_ali_inbound_once(
    max_age_seconds: int = 40,
    *,
    ali_workflow: bool = True,
) -> int:
    """Resume durably accepted WhatsApp turns without a provider replay."""
    claimed = state_registry.inbound_processing_claim_recoverable(
        max_age_seconds=max_age_seconds,
    )
    grouped = {}
    for item in claimed:
        conversation_id = str(item.get("conversation_id") or "")
        # Batch identity, rather than conversation identity, is the recovery
        # unit. Two abandoned debounce turns for the same customer must never
        # be merged into one newly generated action/provider idempotency key.
        batch_id = str(item.get("batch_id") or "")
        grouped.setdefault((conversation_id, batch_id), []).append(item)

    recovered = 0
    for (conversation_id, durable_batch_id), items in grouped.items():
        items.sort(key=lambda item: (
            int(item.get("batch_position") or 0),
            str(item.get("created_at") or ""),
            str(item.get("message_id") or ""),
        ))
        message_ids = [str(item.get("message_id") or "") for item in items]
        recovery_tokens = {
            str(item.get("processing_token") or "") for item in items
        }
        if len(recovery_tokens) != 1 or not next(iter(recovery_tokens), ""):
            log(
                "whatsapp_recovery_generation_invalid",
                conversation_id=conversation_id[:20],
                message_count=len(message_ids),
            )
            continue
        processing_token = next(iter(recovery_tokens))
        stable_batch_id = durable_batch_id or hashlib.sha256(
            "\x1f".join(message_ids).encode("utf-8")
        ).hexdigest()

        def recovery_payload_is_routable(item: dict) -> bool:
            payload = item.get("payload")
            if not isinstance(payload, dict) or not payload:
                return False
            if str(payload.get("message_id") or "") != str(
                item.get("message_id") or ""
            ):
                return False
            platform = str(payload.get("platform") or "").strip().lower()
            channel = str(payload.get("channel") or "").strip().lower()
            if not platform or not channel:
                return False
            conversation = str(item.get("conversation_id") or "")
            zernio_conversation = str(
                payload.get("conversation_id") or ""
            )
            zernio_account = str(payload.get("account_id") or "")
            if zernio_conversation or zernio_account:
                return bool(
                    zernio_conversation
                    and zernio_account
                    and zernio_conversation == conversation
                )
            return bool(
                platform == "whatsapp"
                and str(payload.get("from") or "") == conversation
                and str(payload.get("business_account_id") or "")
                and str(payload.get("phone_number_id") or "")
            )

        if any(not recovery_payload_is_routable(item) for item in items):
            state_registry.inbound_processing_bulk_update(
                message_ids,
                "processing_failed",
                reason="invalid_recovery_payload",
                error="Durable recovery payload is malformed or unroutable.",
                processing_token=processing_token,
            )
            log(
                "whatsapp_recovery_payload_invalid",
                conversation_id=conversation_id[:20],
                message_count=len(message_ids),
            )
            continue
        payloads = [item["payload"] for item in items]
        latest_payload = payloads[-1]

        # A durable turn may outlive a provider-account reassignment. Recheck
        # the complete batch before a heartbeat, history write, or provider
        # send. One invalid member quarantines the whole action rather than
        # replaying a content-changing subset.
        from shared.tenant_guard import account_access_state

        expected_meta_waba = os.environ.get(
            "WHATSAPP_BUSINESS_ACCOUNT_ID", ""
        ).strip()
        expected_meta_phone = os.environ.get(
            "WHATSAPP_PHONE_NUMBER_ID", ""
        ).strip()

        def payload_account_state(payload: dict) -> bool | None:
            if payload.get("conversation_id") or payload.get("account_id"):
                if not payload.get("conversation_id") or not payload.get("account_id"):
                    return False
                try:
                    return account_access_state(
                        str(payload.get("account_id") or ""),
                        direction="inbound",
                    )
                except Exception:
                    return None
            # Direct-Meta rows carry the destination identifiers captured from
            # the signed envelope. Recheck both before replay because this
            # process may now be mounted for a different tenant/account.
            if not expected_meta_waba or not expected_meta_phone:
                return None
            return bool(
                str(payload.get("business_account_id") or "")
                == expected_meta_waba
                and str(payload.get("phone_number_id") or "")
                == expected_meta_phone
            )

        payload_account_states = [
            payload_account_state(payload) for payload in payloads
        ]
        if any(state is None for state in payload_account_states):
            state_registry.inbound_processing_bulk_update(
                message_ids,
                "recovering",
                reason="tenant_account_control_unavailable",
                processing_token=processing_token,
            )
            log(
                "whatsapp_recovery_account_control_unavailable",
                conversation_id=conversation_id[:20],
                message_count=len(message_ids),
            )
            continue
        if any(state is False for state in payload_account_states):
            if len(message_ids) == 1:
                state_registry.inbound_processing_quarantine(
                    message_ids[0], reason="recovery_account_not_allowlisted",
                    processing_token=processing_token,
                )
            else:
                state_registry.inbound_processing_quarantine_batch(
                    message_ids, reason="recovery_account_not_allowlisted",
                    processing_token=processing_token,
                )
            log(
                "whatsapp_recovery_payload_quarantined",
                conversation_id=conversation_id[:20],
                message_count=len(message_ids),
            )
            continue
        if any(payload.get("platform") != "whatsapp" for payload in payloads):
            state_registry.inbound_processing_bulk_update(
                message_ids,
                "processing_failed",
                reason="unsupported_recovery_payload",
                processing_token=processing_token,
            )
            continue

        provider_retry_items = [
            item for item in items
            if int(item.get("provider_retry_count") or 0) > 0
        ]
        if provider_retry_items and max(
            int(item.get("provider_retry_count") or 0)
            for item in provider_retry_items
        ) > _ALI_PROVIDER_SEND_MAX_RECOVERY_ATTEMPTS:
            delivery_kind = str(
                provider_retry_items[0].get("provider_retry_kind")
                or "ali_turn"
            )
            failure_args = (
                str(items[0].get("channel") or "whatsapp"),
                conversation_id,
                str(latest_payload.get("sender_name") or ""),
                message_ids,
            )
            if delivery_kind in {
                "quote_confirmation", "vehicle_recommendation",
            }:
                _mark_ali_structured_delivery_failed(
                    *failure_args,
                    delivery_kind,
                    processing_token=processing_token,
                )
            else:
                _mark_delivery_failed(
                    *failure_args,
                    "provider send failed after automatic retries",
                    processing_token=processing_token,
                )
            log(
                "ali_provider_send_retry_exhausted",
                conversation_id=conversation_id[:20],
                provider_retry_count=max(
                    int(item.get("provider_retry_count") or 0)
                    for item in provider_retry_items
                ),
                delivery_kind=delivery_kind,
            )
            continue

        account_id = str(latest_payload.get("account_id") or "")
        heartbeat_allowed = True
        if latest_payload.get("platform") == "whatsapp":
            envelope = icp_overrides.fetch_overrides_fresh()
            inbox_state = icp_overrides.whatsapp_inbox_state(envelope)
            auto_reply_state = icp_overrides.auto_reply_state(envelope)
            if inbox_state is None or auto_reply_state is None:
                state_registry.inbound_processing_bulk_update(
                    message_ids,
                    "processing",
                    reason="tenant_runtime_controls_unavailable",
                    processing_token=processing_token,
                )
                log(
                    "whatsapp_recovery_controls_unavailable",
                    conversation_id=conversation_id[:20],
                )
                continue
            if inbox_state is False:
                state_registry.inbound_processing_bulk_update(
                    message_ids,
                    "paused",
                    reason="tenant_whatsapp_inbox_paused",
                    processing_token=processing_token,
                )
                log(
                    "whatsapp_recovery_inbox_paused",
                    conversation_id=conversation_id[:20],
                )
                continue
            heartbeat_allowed = (
                auto_reply_state is True
                and not state_registry.get_ai_muted(conversation_id)
                and not state_registry.get_blocked(conversation_id)
            )
        heartbeat_already_sent = any(
            str(item.get("heartbeat_sent_at") or "") for item in items
        )
        if (
            ali_workflow
            and heartbeat_allowed
            and not heartbeat_already_sent
            and account_id
        ):
            def heartbeat_may_mutate():
                if not _automated_send_still_enabled(
                    "whatsapp", conversation_id, message_ids, processing_token,
                    durable_batch_id, required_status="recovering",
                ):
                    return False
                states = [payload_account_state(payload) for payload in payloads]
                if any(state is None for state in states):
                    state_registry.inbound_processing_bulk_update(
                        message_ids, "recovering",
                        reason="tenant_account_control_unavailable",
                        processing_token=processing_token,
                    )
                    return False
                if any(state is False for state in states):
                    state_registry.inbound_processing_quarantine_batch(
                        message_ids, reason="recovery_account_not_allowlisted",
                        processing_token=processing_token,
                    )
                    return False
                return True

            with provider_mutation_scope(heartbeat_may_mutate):
                heartbeat_ok = heartbeat_may_mutate() and send_reply(
                    "whatsapp", conversation_id, account_id,
                    _ali_recovery_heartbeat(conversation_id),
                    confirm_delivery=True,
                    idempotency_key="ali-turn-heartbeat-" + stable_batch_id,
                )
            if heartbeat_ok:
                state_registry.inbound_processing_mark_heartbeat(
                    message_ids,
                    processing_token=processing_token,
                )
                log(
                    "ali_inbound_recovery_heartbeat_sent",
                    conversation_id=conversation_id[:20],
                    message_count=len(message_ids),
                )

        recovered_messages = []
        for item in items:
            payload = item.get("payload") or {}
            if payload.get("conversation_id") and payload.get("account_id"):
                adapter_cls = ZERNIO_CHANNELS.get(
                    payload.get("channel", "whatsapp"), DEFAULT_ZERNIO_CHANNEL,
                )
                buffered_message = adapter_cls.from_zernio(payload)
            else:
                buffered_message = dict(payload)
            recovered_messages.append(buffered_message)
        buffer_key = _stage_recovered_batch(
            conversation_id,
            stable_batch_id,
            recovered_messages,
            processing_token=processing_token,
        )
        with _buffer_lock:
            buffered = _message_buffers.get(buffer_key)
            if buffered and buffered.get("timer") is not None:
                buffered["timer"].cancel()
        _flush_buffer(buffer_key)
        recovered += len(items)
        log(
            "ali_inbound_recovery_completed",
            conversation_id=conversation_id[:20],
            message_count=len(items),
        )
    return recovered


def _ali_inbound_recovery_loop(
    stop_event: threading.Event,
    ali_workflow: bool = True,
) -> None:
    """Continuously reclaim turns lost to a crash or production deployment."""
    while not stop_event.is_set():
        try:
            _process_queued_zernio_failed_events_once()
        except Exception as exc:
            log(
                "zernio_failed_event_queue_scan_failed",
                error=type(exc).__name__,
            )
        try:
            from agents.social.mermaid_delivery_reconciliation import reconcile_pending_once
            reconcile_pending_once()
        except Exception as exc:
            log("mermaid_document_recovery_failed", error=type(exc).__name__)
        try:
            _recover_stale_ali_inbound_once(ali_workflow=ali_workflow)
        except Exception as exc:
            log("ali_inbound_recovery_failed", error=type(exc).__name__)
        try:
            from agents.social.mermaid_abandoned_reminders import run_once
            run_once()
        except Exception as exc:
            log("mermaid_abandoned_reminder_scheduler_failed", error=type(exc).__name__)
        if ali_workflow:
            try:
                _run_ali_reservation_v2_scheduled_once()
            except Exception as exc:
                log("ali_reservation_v2_scheduler_failed", error=type(exc).__name__)
            try:
                _run_ali_lead_follow_up_scheduled_once()
            except Exception as exc:
                log("ali_lead_follow_up_scheduler_failed", error=type(exc).__name__)
        stop_event.wait(5)


def _run_ali_reservation_v2_scheduled_once() -> int:
    """Expire due V2 holds and send reminders only behind the hard gate."""
    from agents.social import ali_customer_dossier, ali_quote_delivery
    from agents.social import ali_reservation_v2

    if not ali_reservation_v2.enabled():
        return 0
    handled = 0
    for plan in ali_reservation_v2.reminder_plan():
        if plan.get("kind") == "documents_prompt":
            public_id = str(plan.get("reservationPublicId") or "")
            payload = ali_customer_dossier.issue_document_links(
                public_id,
                actor="reservation_v2_scheduler",
            )
            ali_quote_delivery.send_customer_requirement_link(
                public_id,
                "documents",
                payload,
            )
            handled += 1
            continue
        if plan.get("kind") == "expire":
            ali_reservation_v2.expire_due_case(plan)
        if plan.get("kind") in {"expire", "expiry_closure"}:
            public_id = str(plan.get("reservationPublicId") or "")
            context = ali_customer_dossier.customer_delivery_context(public_id)
            delivered = send_reply(
                "whatsapp",
                str(context["conversation_id"]),
                str(context["account_id"]),
                ali_quote_delivery.reservation_hold_expired_text(
                    str(context.get("locale") or "en"),
                ),
                confirm_delivery=True,
                idempotency_key=(
                    f"ali-v2-expiry-closure:{public_id}"
                ),
            )
            ali_reservation_v2.record_expiry_closure_result(
                {
                    **plan,
                    "kind": "expiry_closure",
                    "idempotencyKey": f"ali-v2-expiry-closure:{public_id}",
                },
                sent=bool(delivered),
            )
            handled += 1
            continue
        if not ali_reservation_v2.reminder_sends_enabled():
            continue
        public_id = str(plan.get("reservationPublicId") or "")
        context = ali_customer_dossier.customer_delivery_context(public_id)
        message = ali_quote_delivery.reservation_reminder_text(
            str(context.get("locale") or "en"),
            str(plan.get("nextAction") or ""),
        )
        delivered = send_reply(
            "whatsapp",
            str(context["conversation_id"]),
            str(context["account_id"]),
            message,
            confirm_delivery=True,
            idempotency_key=str(plan["idempotencyKey"]),
        )
        ali_reservation_v2.record_reminder_result(plan, sent=bool(delivered))
        handled += 1
    return handled


def _run_ali_lead_follow_up_scheduled_once() -> int:
    """Send claimed pre-reservation reminders only in an open Meta window."""
    from agents.social import ali_lead_follow_up
    from agents.social.zernio_dm_client import whatsapp_customer_service_window

    if not ali_lead_follow_up.enabled():
        return 0
    handled = 0
    for plan in ali_lead_follow_up.claim_due_follow_ups():
        window = whatsapp_customer_service_window(
            str(plan.get("conversationId") or ""),
            str(plan.get("accountId") or ""),
            str(plan.get("latestInboundAt") or ""),
        )
        if not window.get("open"):
            reason = str(window.get("reason") or "provider_unavailable")
            status = "skipped_window" if reason == "window_closed" else "failed"
            ali_lead_follow_up.record_delivery_result(
                plan,
                status=status,
                error_code=reason,
            )
            handled += 1
            continue
        try:
            delivered = send_reply(
                "whatsapp",
                str(plan["conversationId"]),
                str(plan["accountId"]),
                str(plan["message"]),
                confirm_delivery=True,
                idempotency_key=str(plan["idempotencyKey"]),
            )
        except Exception as exc:
            delivered = False
            error_code = type(exc).__name__
        else:
            error_code = "" if delivered else "provider_send_failed"
        if delivered:
            state_registry.dm_store_message(
                str(plan["conversationId"]),
                "whatsapp",
                "assistant",
                str(plan["message"]),
            )
        ali_lead_follow_up.record_delivery_result(
            plan,
            status="sent" if delivered else "failed",
            error_code=error_code,
        )
        handled += 1
    return handled


def _mark_delivery_failed(channel: str, conversation_id: str, customer_name: str,
                          message_ids: list[str], error: str, *,
                          processing_token: str = ""):
    if not state_registry.inbound_processing_bulk_update(
        message_ids,
        "send_failed",
        reason="provider_send_failed",
        error=error,
        processing_token=processing_token,
    ):
        return
    subject = f"[DELIVERY FAILED] {channel}: {conversation_id}"
    body = (
        "A reply was generated, but the provider send call failed.\n\n"
        f"Channel: {channel}\n"
        f"Conversation/customer: {conversation_id}\n"
        "Action taken: The assistant reply was NOT stored as sent. "
        "Operator must review and reply manually or reconnect the provider.\n"
        f"Error: {error or 'provider returned false'}\n"
        f"Inbound message ids: {', '.join(message_ids) or '(missing)'}"
    )
    try:
        state_registry.create_pending_notification(
            "escalation", channel, conversation_id,
            customer_name or "Unknown", subject, body, mode="hard")
    except Exception as exc:
        log("delivery_failure_escalation_create_failed",
            channel=channel, conversation_id=conversation_id[:20],
            error=str(exc)[:200])


def _mark_fenced_zernio_delivery_failed(
    channel: str, conversation_id: str, customer_name: str,
    message_ids: list[str], *, batch_id: str, processing_token: str,
    account_id: str, error_code: str,
) -> None:
    """Never terminalize an automated send without its durable operator item."""
    try:
        state_registry.inbound_processing_commit_delivery_failure(
            message_ids, batch_id, processing_token,
            account_id=account_id,
            notification={
                "channel": channel,
                "customer_id": conversation_id,
                "customer_name": customer_name or "Customer",
                "subject": "[DELIVERY FAILED] Automated reply needs review",
                "body": (
                    "A generated reply was not confirmed by the provider and was "
                    "not stored as delivered. Review the conversation and reply "
                    "manually if needed. Error category: " + error_code
                ),
            },
        )
    except state_registry.HandoffAccountReassignedError:
        state_registry.inbound_processing_quarantine_batch(
            message_ids, reason="delivery_failure_account_reassigned",
            processing_token=processing_token,
        )
    except Exception as exc:
        state_registry.inbound_processing_bulk_update(
            message_ids, "recovering",
            reason="delivery_attention_persistence_unavailable",
            error=type(exc).__name__, processing_token=processing_token,
        )


def _mark_ali_structured_delivery_failed(
    channel: str,
    conversation_id: str,
    customer_name: str,
    message_ids: list[str],
    delivery_kind: str,
    *,
    processing_token: str = "",
) -> None:
    """Route Ali control/media failures to technical attention only."""
    if not state_registry.inbound_processing_bulk_update(
        message_ids,
        "send_failed",
        reason="provider_send_failed",
        error=delivery_kind,
        processing_token=processing_token,
    ):
        return
    subject, body = {
        "quote_confirmation": (
            "[ALI QUOTE CONFIRMATION DELIVERY FAILED]",
            "The rental summary and Send My Quote control could not be "
            "delivered. Open the conversation in Unboks.",
        ),
        "vehicle_recommendation": (
            "[ALI VEHICLE RECOMMENDATION DELIVERY FAILED]",
            "The vehicle recommendation could not be delivered. Open the "
            "conversation in Unboks.",
        ),
    }.get(
        delivery_kind,
        (
            "[ALI STRUCTURED MESSAGE DELIVERY FAILED]",
            "An Ali rental message could not be delivered. Open the "
            "conversation in Unboks.",
        ),
    )
    try:
        state_registry.create_pending_notification(
            "technical",
            channel,
            conversation_id,
            customer_name or "Ali rental customer",
            subject,
            body,
        )
    except Exception as exc:
        log(
            "ali_delivery_technical_notification_failed",
            channel=channel,
            conversation_id=conversation_id[:20],
            delivery_kind=delivery_kind,
            error=str(exc)[:200],
        )


def _mark_ali_delivery_retry(
    channel: str,
    conversation_id: str,
    message_ids: list[str],
    delivery_kind: str,
    *,
    processing_token: str = "",
) -> None:
    """Keep a provider-rejected Ali turn durable until bounded recovery."""
    if not state_registry.inbound_processing_bulk_update(
        message_ids,
        "recovering",
        reason="provider_send_retry",
        error=delivery_kind,
        processing_token=processing_token,
    ):
        return
    log(
        "ali_provider_send_retry_scheduled",
        channel=channel,
        conversation_id=conversation_id[:20],
        message_count=len(message_ids),
        delivery_kind=delivery_kind,
    )


def _run_ali_document_retention_cleanup() -> None:
    try:
        from agents.social import ali_customer_dossier

        retention = ali_customer_dossier.purge_expired_documents()
        if retention["documentsDeleted"]:
            log("ali_document_retention_cleanup", **retention)
    except Exception as exc:
        log("ali_document_retention_cleanup_failed", error=type(exc).__name__)


def _maybe_run_cleanup():
    """Run stale data cleanup at most once per hour."""
    global _last_cleanup_ts
    now = time.time()
    stale_failures = state_registry.inbound_processing_mark_stale_failures()
    if stale_failures:
        log("inbound_processing_stale_failures", count=stale_failures)
    if now - _last_cleanup_ts < 3600:
        return
    _last_cleanup_ts = now
    result = state_registry.wa_cleanup_stale_data()
    if result["threads_cleaned"] or result["processed_cleaned"]:
        log("whatsapp_cleanup", **result)
    workflow_type = str(
        ((config_loader.get_raw() or {}).get("workflow") or {}).get("type") or ""
    )
    if workflow_type == "ali_quote":
        _run_ali_document_retention_cleanup()


@app.get("/webhooks/meta/whatsapp")
async def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
):
    """Meta webhook verification — returns challenge if token matches."""
    if hub_mode == "subscribe" and hub_verify_token == _VERIFY_TOKEN:
        log("webhook_verified", source="meta_whatsapp")
        return PlainTextResponse(content=hub_challenge, status_code=200)
    log("webhook_verify_failed", source="meta_whatsapp", mode=hub_mode)
    return PlainTextResponse(content="Forbidden", status_code=403)


@app.post("/webhooks/meta/whatsapp")
async def receive_webhook(request: Request, background_tasks: BackgroundTasks):
    """Receive WhatsApp webhook events — return 200 immediately, process in background."""
    body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")
    if not _verify_meta_webhook_signature(body, signature):
        log("meta_whatsapp_webhook_signature_invalid")
        return PlainTextResponse(content="Forbidden", status_code=403)
    try:
        payload = _json.loads(body)
    except Exception:
        log("meta_whatsapp_webhook_json_invalid")
        return PlainTextResponse(content="Bad Request", status_code=400)
    if not isinstance(payload, dict) or not _meta_payload_matches_tenant(payload):
        log("meta_whatsapp_webhook_destination_invalid")
        return PlainTextResponse(content="Forbidden", status_code=403)
    log(
        "webhook_received",
        source="meta_whatsapp",
        entry_count=len(payload.get("entry") or []),
    )
    messages = parse_webhook_payload(payload)
    if messages:
        envelope = icp_overrides.fetch_overrides()
        inbox_state = icp_overrides.whatsapp_inbox_state(envelope)
        if inbox_state is None:
            log("whatsapp_runtime_controls_unavailable", source="meta_whatsapp")
            return PlainTextResponse(content="Unavailable", status_code=503)
        if inbox_state is False:
            log("whatsapp_inbox_disabled", source="meta_whatsapp")
            return PlainTextResponse(content="OK", status_code=200)
    acceptance_bindings = _acceptance_batch_bindings(messages)
    accepted_messages = []
    for msg in messages:
        message_id = msg.get("message_id", "")
        try:
            claimed = state_registry.wa_claim_inbound_processing(
                message_id,
                conversation_id=msg.get("from", ""),
                channel="whatsapp",
                payload=msg,
                acceptance_batch_id=acceptance_bindings.get(
                    str(message_id), ("", 0)
                )[0],
                acceptance_position=acceptance_bindings.get(
                    str(message_id), ("", 0)
                )[1],
            )
        except Exception as exc:
            log(
                "meta_whatsapp_durable_accept_failed",
                error=type(exc).__name__,
            )
            return PlainTextResponse(content="Unavailable", status_code=503)
        if claimed:
            accepted_messages.append(msg)
        elif message_id:
            log(
                "webhook_duplicate_skipped",
                source="meta_whatsapp",
                message_id=message_id,
            )
    if accepted_messages:
        background_tasks.add_task(
            _process_whatsapp_event,
            payload,
            accepted_messages,
        )
    return PlainTextResponse(content="OK", status_code=200)


def _process_whatsapp_event(
    payload: dict,
    accepted_messages: list[dict] | None = None,
):
    """Background task: parse messages, dedup, buffer for debounce."""
    _maybe_run_cleanup()
    try:
        messages = (
            accepted_messages
            if accepted_messages is not None
            else parse_webhook_payload(payload)
        )
        acceptance_bindings = _acceptance_batch_bindings(messages)
        for msg in messages:
            message_id = msg.get("message_id", "")
            # Nr 3 owns the channel switch. Check it before dedup or payload
            # persistence so a disabled tenant cannot accumulate customer
            # data and cannot poison a future replay of the same message ID.
            if accepted_messages is None:
                envelope = icp_overrides.fetch_overrides()
                inbox_state = icp_overrides.whatsapp_inbox_state(envelope)
                if inbox_state is None:
                    log(
                        "whatsapp_runtime_controls_unavailable",
                        source="meta_whatsapp",
                    )
                    continue
                if inbox_state is False:
                    log(
                        "whatsapp_inbox_disabled",
                        source="meta_whatsapp",
                        message_id=message_id[:30],
                    )
                    continue
                if not state_registry.wa_claim_inbound_processing(
                    message_id,
                    conversation_id=msg.get("from", ""),
                    channel="whatsapp",
                    payload=msg,
                    acceptance_batch_id=acceptance_bindings.get(
                        str(message_id), ("", 0)
                    )[0],
                    acceptance_position=acceptance_bindings.get(
                        str(message_id), ("", 0)
                    )[1],
                ):
                    if message_id:
                        log("webhook_duplicate_skipped", source="meta_whatsapp",
                            message_id=message_id)
                    continue
            log(
                "whatsapp_message_normalized",
                message_id=message_id[:30],
                message_type=str(msg.get("message_type") or "unknown")[:30],
                has_text=msg.get("text") is not None,
            )
            # Only buffer text messages
            if msg.get("text") is None:
                state_registry.inbound_processing_update(
                    message_id, "ignored", reason="non_text_message")
                log("whatsapp_non_text_skipped", source="meta_whatsapp",
                    message_type=msg.get("message_type"), message_id=message_id)
                continue
            ignored = state_registry.match_ignored_contact(
                channel="whatsapp",
                sender_id=msg.get("from", ""),
                phone=msg.get("from", ""),
            )
            if ignored:
                state_registry.record_ignored_contact_event(
                    contact_id=ignored.get("id"),
                    channel="whatsapp",
                    sender_identifier=msg.get("from", ""),
                    message_id=message_id,
                )
                log("ignored_contact_inbound_suppressed",
                    channel="whatsapp",
                    sender=(msg.get("from", "") or "")[:50],
                    message_id=message_id,
                    reason="Ignored inbound message because sender is on Excluded Contacts / Ignore List.")
                state_registry.inbound_processing_update(
                    message_id, "ignored", reason="ignored_contact")
                continue
            _buffer_message(msg)
    except Exception as e:
        log("webhook_process_error", source="meta_whatsapp", error=str(e))


def _buffer_message(msg):
    """Add message to per-phone debounce buffer. Schedule flush after window."""
    phone = msg["from"]
    message_id = str(msg.get("message_id") or "")
    now = time.time()
    effective_timing = _response_timing_for_message(msg)
    with _buffer_lock:
        if phone not in _message_buffers:
            timing = response_timing.runtime_response_timing(effective_timing)
            durable_batch_id = state_registry.inbound_processing_join_batch(
                message_id,
            )
            if message_id and not durable_batch_id:
                log(
                    "whatsapp_message_batch_already_claimed",
                    message_id=str(msg.get("message_id") or "")[:30],
                )
                return
            _message_buffers[phone] = {
                "messages": [],
                "timer": None,
                "started": now,
                "timing": timing,
                "phone": phone,
                "batch_id": durable_batch_id,
            }
        else:
            buf = _message_buffers[phone]
            durable_batch_id = state_registry.inbound_processing_join_batch(
                message_id,
                str(buf.get("batch_id") or ""),
                len(buf["messages"]),
            )
            if buf.get("batch_id") and durable_batch_id != buf["batch_id"]:
                if message_id and not durable_batch_id:
                    log(
                        "whatsapp_message_batch_already_claimed",
                        message_id=str(msg.get("message_id") or "")[:30],
                    )
                    return
                # The previous batch was sealed by a flush/recovery claim while
                # its old timer was still resident. Discard that stale memory
                # snapshot and start a fresh durable batch for this event.
                if buf.get("timer") is not None:
                    buf["timer"].cancel()
                timing = response_timing.runtime_response_timing(effective_timing)
                _message_buffers[phone] = {
                    "messages": [],
                    "timer": None,
                    "started": now,
                    "timing": timing,
                    "phone": phone,
                    "batch_id": durable_batch_id,
                }
            elif not buf.get("batch_id"):
                buf["batch_id"] = durable_batch_id
        buf = _message_buffers[phone]
        if message_id and not durable_batch_id:
            log(
                "whatsapp_message_batch_already_claimed",
                message_id=str(msg.get("message_id") or "")[:30],
            )
            return
        timing = buf.get("timing") or response_timing.runtime_response_timing(effective_timing)
        if timing.get("mode") != "random":
            timing = response_timing.runtime_response_timing(effective_timing)
            buf["timing"] = timing
        buf["messages"].append(msg)
        log("whatsapp_message_buffered", phone=phone,
            buffered_count=len(buf["messages"]),
            batch_delay_seconds=timing["delay_seconds"],
            batch_max_wait_seconds=timing["max_wait_seconds"],
            batch_source=timing.get("source"),
            batch_mode=timing.get("mode"),
            batch_random_picked_seconds=timing.get("random_picked_seconds"))

        # Cancel existing timer
        if buf["timer"] is not None:
            buf["timer"].cancel()

        # Calculate delay: min of debounce window or remaining hard cap
        elapsed = now - buf["started"]
        remaining_cap = max(0.1, float(timing["max_wait_seconds"]) - elapsed)
        delay = min(float(timing["delay_seconds"]), remaining_cap)

        # The recovery scanner's short abandoned-acceptance threshold must not
        # steal a live batch while its configured debounce timer is still
        # running.  Refresh every durable member through the hard cap plus a
        # small scheduler margin; processing establishes its own lease later.
        if durable_batch_id:
            state_registry.inbound_processing_extend_batch_lease(
                durable_batch_id,
                remaining_cap
                + state_registry.INBOUND_DEBOUNCE_LEASE_MARGIN_SECONDS,
            )

        buf["timer"] = threading.Timer(delay, _flush_buffer, args=[phone])
        buf["timer"].daemon = True
        buf["timer"].start()


def _response_timing_for_message(msg: dict) -> dict:
    """Return effective response timing for this message.

    Human takeover and already-blocked conversations should not sit in the
    customer-facing debounce window. They still flow through the same flush
    path so storage/blocked handling remains centralized.
    """
    phone = msg.get("from", "")
    conversation_id = msg.get("_zernio_conversation_id") or phone
    if conversation_id and (
        state_registry.get_blocked(conversation_id)
        or state_registry.get_ai_muted(conversation_id)
    ):
        return {
            "message_batching_enabled": False,
            "preset": "immediate",
            "delay_seconds": 0.1,
            "max_wait_seconds": 0.1,
            "source": "immediate_runtime_state",
        }
    try:
        envelope = icp_overrides.fetch_overrides()
    except Exception:
        envelope = None
    return response_timing.effective_response_timing(envelope)


def _whatsapp_inbox_still_enabled(
    channel: str,
    conversation_id: str,
    message_ids: list[str],
    processing_token: str = "",
) -> bool:
    """Recheck an already-buffered WhatsApp message before processing."""
    if str(channel or "").strip().lower() != "whatsapp":
        return True
    envelope = icp_overrides.fetch_overrides_fresh()
    inbox_state = icp_overrides.whatsapp_inbox_state(envelope)
    if inbox_state is True:
        return True
    if inbox_state is None:
        log(
            "whatsapp_runtime_controls_unavailable_before_flush",
            conversation_id=str(conversation_id or "")[:20],
        )
        state_registry.inbound_processing_bulk_update(
            message_ids,
            "processing",
            reason="tenant_runtime_controls_unavailable",
            processing_token=processing_token,
        )
        return False
    log(
        "whatsapp_inbox_disabled_before_flush",
        conversation_id=str(conversation_id or "")[:20],
    )
    state_registry.inbound_processing_bulk_update(
        message_ids,
        "paused",
        reason="tenant_whatsapp_inbox_paused",
        processing_token=processing_token,
    )
    return False


def _automated_send_still_enabled(
    channel: str,
    conversation_id: str,
    message_ids: list[str],
    processing_token: str = "",
    batch_id: str = "",
    *,
    required_status: str = "processing",
) -> bool:
    """Perform the final authoritative hard-stop check before an AI send."""
    if state_registry.get_blocked(conversation_id):
        state_registry.inbound_processing_bulk_update(
            message_ids,
            "ignored",
            reason="blocked_conversation",
            processing_token=processing_token,
        )
        return False
    if state_registry.get_ai_muted(conversation_id):
        state_registry.inbound_processing_bulk_update(
            message_ids,
            "escalated",
            reason="human_takeover_ai_muted",
            processing_token=processing_token,
        )
        return False
    try:
        envelope = icp_overrides.fetch_overrides_fresh()
        inbox_state = icp_overrides.whatsapp_inbox_state(envelope)
        auto_reply_state = icp_overrides.auto_reply_state(envelope)
    except Exception:
        inbox_state = None
        auto_reply_state = None
    if str(channel or "").strip().lower() == "whatsapp" and inbox_state is None:
        state_registry.inbound_processing_bulk_update(
            message_ids,
            "processing",
            reason="tenant_runtime_controls_unavailable",
            processing_token=processing_token,
        )
        return False
    if str(channel or "").strip().lower() == "whatsapp" and inbox_state is False:
        state_registry.inbound_processing_bulk_update(
            message_ids,
            "paused",
            reason="tenant_whatsapp_inbox_paused",
            processing_token=processing_token,
        )
        return False
    if auto_reply_state is None:
        state_registry.inbound_processing_bulk_update(
            message_ids,
            "processing",
            reason="tenant_runtime_controls_unavailable",
            processing_token=processing_token,
        )
        return False
    if auto_reply_state is False:
        state_registry.inbound_processing_bulk_update(
            message_ids,
            "paused",
            reason="tenant_agent_paused",
            processing_token=processing_token,
        )
        return False
    # A dashboard block/takeover can land while the fresh control read waits
    # on Nr 3. Re-read local hard stops after that I/O, immediately before the
    # generation renewal and provider boundary.
    if state_registry.get_blocked(conversation_id):
        state_registry.inbound_processing_bulk_update(
            message_ids, "ignored", reason="blocked_conversation",
            processing_token=processing_token,
        )
        return False
    if state_registry.get_ai_muted(conversation_id):
        state_registry.inbound_processing_bulk_update(
            message_ids, "escalated", reason="human_takeover_ai_muted",
            processing_token=processing_token,
        )
        return False
    return state_registry.inbound_processing_is_current(
        message_ids,
        batch_id,
        processing_token,
        required_status=required_status,
    )


def _flush_buffer(buffer_key):
    """Flush buffered messages: concatenate texts, process as single message."""
    with _buffer_lock:
        buf = _message_buffers.pop(buffer_key, None)
    if not buf or not buf["messages"]:
        return
    phone = str(buf.get("phone") or buffer_key)
    messages = list(buf["messages"])
    ids = _message_ids(messages)
    durable_batch_id = str(buf.get("batch_id") or "")
    if durable_batch_id:
        ordered_ids = (
            state_registry.inbound_processing_ordered_batch_ids(
                ids,
                durable_batch_id,
            )
            if len(ids) == len(messages)
            else None
        )
        if ordered_ids is None:
            log(
                "whatsapp_batch_membership_mismatch",
                conversation_id=phone[:20],
                memory_count=len(messages),
                durable_batch_id=durable_batch_id[:20],
            )
            return
        messages_by_id = {
            str(message.get("message_id") or ""): message
            for message in messages
        }
        messages = [messages_by_id[message_id] for message_id in ordered_ids]
        ids = ordered_ids
    # Concatenate all text messages
    texts = [m["text"] for m in messages if m.get("text")]
    combined_text = "\n".join(texts)
    # Use last message's metadata
    final_msg = messages[-1].copy()
    final_msg["text"] = combined_text
    final_msg["_zernio_attachments"] = []
    for buffered_message in messages:
        for attachment in buffered_message.get("_zernio_attachments") or []:
            if not isinstance(attachment, dict):
                continue
            safe_attachment = dict(attachment)
            safe_attachment["provider_message_id"] = str(
                buffered_message.get("message_id")
                or buffered_message.get("_zernio_event_id")
                or ""
            )
            final_msg["_zernio_attachments"].append(safe_attachment)
    final_msg["_ali_action_id"] = str(buf.get("batch_id") or "") or hashlib.sha256(
        "\x1f".join(ids or [str(final_msg.get("message_id") or "")]).encode("utf-8")
    ).hexdigest()
    batched_count = len(messages)
    timing = buf.get("timing") if isinstance(buf, dict) else {}
    if batched_count > 1:
        log("whatsapp_batch_flushed", phone=phone, count=batched_count,
            combined_length=len(combined_text),
            batch_source=timing.get("source") if isinstance(timing, dict) else None)
    # Brief 161: acquire per-phone lock BEFORE the try block so both Zernio
    # and legacy Meta paths are serialized. Lock key: zernio conv id (if
    # present) or phone. Fixes race where msg 2 starts processing while msg
    # 1 is still mid-flight and overwrites msg 1's state.
    _lock_key = final_msg.get("_zernio_conversation_id") or phone
    _phone_lock = _get_phone_lock(_lock_key)
    with _phone_lock:
        processing_token = ""
        if ids:
            processing_token = state_registry.inbound_processing_begin_batch(
                ids,
                batch_id=durable_batch_id,
                recovering=bool(buf.get("recovery")),
                recovery_token=str(buf.get("processing_token") or ""),
            )
            if not processing_token:
                log(
                    "whatsapp_recovery_superseded",
                    conversation_id=str(_lock_key)[:20],
                    message_count=len(ids),
                )
                return

        def worker_is_current() -> bool:
            return bool(ids and processing_token) and (
                state_registry.inbound_processing_is_current(
                    ids,
                    durable_batch_id,
                    processing_token,
                )
            )

        def batch_account_is_current() -> bool:
            if not worker_is_current():
                return False
            if not final_msg.get("_zernio_conversation_id"):
                return True
            from shared.tenant_guard import account_access_state

            try:
                states = [
                    account_access_state(
                        str(message.get("_zernio_account_id") or ""),
                        direction="inbound",
                    )
                    for message in messages
                ]
            except Exception:
                states = [None]
            if any(state is None for state in states):
                state_registry.inbound_processing_bulk_update(
                    ids, "recovering", reason="tenant_account_control_unavailable",
                    processing_token=processing_token,
                )
                return False
            if any(state is False for state in states):
                state_registry.inbound_processing_quarantine_batch(
                    ids, reason="send_account_reassigned",
                    processing_token=processing_token,
                )
                return False
            return True

        mutation_scope_token = set_provider_mutation_guard(
            lambda: _automated_send_still_enabled(
                str(final_msg.get("_zernio_channel") or "whatsapp"),
                str(_lock_key),
                ids,
                processing_token,
                durable_batch_id,
            ) and batch_account_is_current()
        )
        try:
            # Check if this came from Zernio (has _zernio metadata)
            _zernio_conv = final_msg.get("_zernio_conversation_id")
            _zernio_acct = final_msg.get("_zernio_account_id")
            _zernio_channel = final_msg.get("_zernio_channel", "whatsapp")
            _zernio_sender = final_msg.get("_zernio_sender_name", "")
            if _zernio_conv:
                from shared.tenant_guard import account_access_state

                try:
                    account_states = [
                        account_access_state(
                            str(message.get("_zernio_account_id") or ""),
                            direction="inbound",
                        )
                        for message in messages
                    ]
                except Exception:
                    account_states = [None]
                if any(state is None for state in account_states):
                    state_registry.inbound_processing_bulk_update(
                        ids,
                        "recovering",
                        reason="tenant_account_control_unavailable",
                        processing_token=processing_token,
                    )
                    log(
                        "zernio_batch_account_control_unavailable",
                        conversation_id=str(_zernio_conv)[:20],
                        message_count=len(ids),
                    )
                    return
                if any(state is False for state in account_states):
                    state_registry.inbound_processing_quarantine_batch(
                        ids,
                        reason="debounce_account_not_allowlisted",
                        processing_token=processing_token,
                    )
                    log(
                        "zernio_batch_account_quarantined",
                        conversation_id=str(_zernio_conv)[:20],
                        message_count=len(ids),
                    )
                    return
                if not _whatsapp_inbox_still_enabled(
                    _zernio_channel, _zernio_conv, ids, processing_token
                ):
                    return
                auto_reply_state = icp_overrides.auto_reply_state(
                    icp_overrides.fetch_overrides()
                )
                if auto_reply_state is None:
                    state_registry.inbound_processing_bulk_update(
                        ids,
                        "processing",
                        reason="tenant_runtime_controls_unavailable",
                        processing_token=processing_token,
                    )
                    return
                if not worker_is_current():
                    return
                ignored = state_registry.match_ignored_contact(
                    channel=_zernio_channel,
                    sender_id=final_msg.get("from", ""),
                    phone=final_msg.get("from", ""),
                ) or state_registry.match_ignored_contact(
                    channel=_zernio_channel,
                    sender_id=_zernio_conv,
                )
                if ignored:
                    state_registry.record_ignored_contact_event(
                        contact_id=ignored.get("id"),
                        channel=_zernio_channel,
                        sender_identifier=final_msg.get("from", "") or _zernio_conv,
                    )
                    log("ignored_contact_inbound_suppressed",
                        channel=_zernio_channel,
                        sender=(final_msg.get("from", "") or _zernio_conv)[:50],
                        reason="Ignored inbound message because sender is on Excluded Contacts / Ignore List.")
                    state_registry.inbound_processing_bulk_update(
                        ids,
                        "ignored",
                        reason="ignored_contact",
                        processing_token=processing_token,
                    )
                    return
                # Brief 220: per-conversation runtime block. Drop BEFORE
                # storage so the conversation doesn't appear in the inbox.
                if state_registry.get_blocked(_zernio_conv):
                    log("whatsapp_zernio_blocked_conversation", conversation_id=_zernio_conv[:20])
                    state_registry.inbound_processing_bulk_update(
                        ids,
                        "ignored",
                        reason="blocked_conversation",
                        processing_token=processing_token,
                    )
                    return  # exits the with _phone_lock block
                from agents.social.ali_reservation_v2_inbound import (
                    process_structural_text,
                )

                _ali_v2_automation_available = (
                    not state_registry.get_ai_muted(_zernio_conv)
                    and auto_reply_state is True
                )
                if not worker_is_current():
                    return
                structural_result = (
                    process_structural_text(final_msg)
                    if _ali_v2_automation_available
                    else {"handled": False}
                )
                if (
                    structural_result.get("handled")
                    and not structural_result.get("continue_to_documents")
                ):
                    if not worker_is_current():
                        return
                    state_registry.dm_store_inbound_message(
                        _zernio_conv, _zernio_channel, combined_text,
                        _zernio_sender, ids,
                    )
                    structural_reply = str(structural_result.get("reply") or "")
                    if not structural_reply:
                        _mark_delivery_failed(
                            _zernio_channel,
                            _zernio_conv,
                            _zernio_sender,
                            ids,
                            "structural reply failed",
                            processing_token=processing_token,
                        )
                        return
                    if not _automated_send_still_enabled(
                        _zernio_channel,
                        _zernio_conv,
                        ids,
                        processing_token,
                        durable_batch_id,
                    ):
                        return
                    structural_ok = send_reply(
                        _zernio_channel,
                        _zernio_conv,
                        _zernio_acct,
                        structural_reply,
                        confirm_delivery=True,
                        idempotency_key=(
                            "ali-v2-structural-"
                            + str(final_msg.get("_ali_action_id") or "")
                        ),
                    )
                    if not structural_ok:
                        _mark_delivery_failed(
                            _zernio_channel, _zernio_conv, _zernio_sender,
                            ids, "structural reply failed",
                            processing_token=processing_token,
                        )
                        return
                    if not worker_is_current():
                        return
                    state_registry.dm_store_message(
                        conversation_id=_zernio_conv,
                        channel=_zernio_channel,
                        role="assistant",
                        text=structural_reply,
                    )
                    state_registry.inbound_processing_bulk_update(
                        ids, "replied", reason="reservation_v2_structural_gate",
                        processing_token=processing_token,
                    )
                    return
                if (
                    _ali_v2_automation_available
                    and final_msg.get("_zernio_attachments")
                ):
                    from agents.social.ali_reservation_v2_inbound import (
                        process_whatsapp_documents,
                    )

                    if not worker_is_current():
                        return
                    document_result = process_whatsapp_documents(final_msg)
                    if document_result.get("handled"):
                        if not worker_is_current():
                            return
                        state_registry.dm_store_inbound_message(
                            _zernio_conv, _zernio_channel,
                            "[Secure reservation document received]",
                            _zernio_sender, ids,
                        )
                        if state_registry.get_ai_muted(_zernio_conv):
                            state_registry.inbound_processing_bulk_update(
                                ids, "escalated", reason="human_takeover_ai_muted",
                                processing_token=processing_token,
                            )
                            return
                        if auto_reply_state is False:
                            state_registry.inbound_processing_bulk_update(
                                ids, "paused", reason="tenant_agent_paused",
                                processing_token=processing_token,
                            )
                            return
                        reply_text = str(document_result.get("reply") or "")
                        if not reply_text:
                            _mark_delivery_failed(
                                _zernio_channel,
                                _zernio_conv,
                                _zernio_sender,
                                ids,
                                "document acknowledgement failed",
                                processing_token=processing_token,
                            )
                            return
                        if not _automated_send_still_enabled(
                            _zernio_channel,
                            _zernio_conv,
                            ids,
                            processing_token,
                            durable_batch_id,
                        ):
                            return
                        reply_ok = send_reply(
                            _zernio_channel,
                            _zernio_conv,
                            _zernio_acct,
                            reply_text,
                            confirm_delivery=True,
                            idempotency_key=(
                                "ali-v2-document-ack-"
                                + str(final_msg.get("_ali_action_id") or "")
                            ),
                        )
                        if not reply_ok:
                            _mark_delivery_failed(
                                _zernio_channel, _zernio_conv, _zernio_sender,
                                ids, "document acknowledgement failed",
                                processing_token=processing_token,
                            )
                            return
                        if not worker_is_current():
                            return
                        state_registry.dm_store_message(
                            conversation_id=_zernio_conv,
                            channel=_zernio_channel,
                            role="assistant",
                            text=reply_text,
                        )
                        state_registry.inbound_processing_bulk_update(
                            ids,
                            "replied" if document_result.get("success") else "processing_failed",
                            reason=(
                                "reservation_document_stored"
                                if document_result.get("success")
                                else (
                                    "reservation_document_rejected:"
                                    + str(document_result.get("error_code") or "unknown")[:80]
                                )
                            ),
                            processing_token=processing_token,
                        )
                        workflow_after_upload = (
                            document_result.get("workflow_v2") or {}
                        )
                        if (
                            document_result.get("success")
                            and workflow_after_upload.get("state") in {
                                "documents_collected",
                                "prepayment_approval_pending",
                            }
                        ):
                            # Preserve the customer-facing order: acknowledge
                            # secure receipt first, then send the next automatic
                            # step. This path never waits for per-file staff
                            # verification.
                            from agents.social import ali_reservation_v2_automation

                            ali_reservation_v2_automation.after_documents_collected(
                                str(document_result["reservation_public_id"]),
                            )
                        return
                # Brief 213: ai_muted check for Zernio WhatsApp (debounce-buffered path).
                if state_registry.get_ai_muted(_zernio_conv):
                    if not worker_is_current():
                        return
                    state_registry.dm_store_inbound_message(
                        _zernio_conv, _zernio_channel, combined_text,
                        _zernio_sender, ids,
                    )
                    log("whatsapp_zernio_ai_muted", conversation_id=_zernio_conv[:20])
                    state_registry.inbound_processing_bulk_update(
                        ids,
                        "escalated",
                        reason="human_takeover_ai_muted",
                        processing_token=processing_token,
                    )
                    return  # exits the with _phone_lock block; _flush_buffer returns
                if auto_reply_state is False:
                    if not worker_is_current():
                        return
                    state_registry.dm_store_inbound_message(
                        _zernio_conv, _zernio_channel, combined_text,
                        _zernio_sender, ids,
                    )
                    log("tenant_agent_paused",
                        conversation_id=_zernio_conv[:20],
                        channel=_zernio_channel)
                    state_registry.inbound_processing_bulk_update(
                        ids,
                        "paused",
                        reason="tenant_agent_paused",
                        processing_token=processing_token,
                    )
                    return
                if _zernio_channel == "whatsapp":
                    from shared import mermaid_catalog
                    if mermaid_catalog.reservation_demo_enabled():
                        from agents.social.mermaid_autoreply_guard import repeated_automatic_reply
                        if repeated_automatic_reply(combined_text, state_registry.dm_get_history(_zernio_conv, _zernio_channel, limit=20)):
                            if not worker_is_current():
                                return
                            state_registry.dm_store_inbound_message(_zernio_conv, _zernio_channel, combined_text, _zernio_sender, ids)
                            state_registry.inbound_processing_bulk_update(ids, "ignored", reason="mermaid_repeated_automatic_reply", processing_token=processing_token)
                            log("mermaid_repeated_automatic_reply", action="reply_suppressed")
                            return
                # Callback-follow-up tenants need the structured WhatsApp
                # agent even though they intentionally disable booking_flow.
                _orchestrator_on = _use_whatsapp_orchestrator(_zernio_channel)
                reply_media = None
                reply_vehicle_recommendation = None
                reply_quote_confirmation = None
                ali_turn_commit = None
                mermaid_delivery_commit = None
                mermaid_generation_failure = None
                ali_customer_delivery_deferred = False
                handoff_notification = None
                if _orchestrator_on:
                    if not worker_is_current():
                        return
                    state_registry.dm_store_inbound_message(
                        _zernio_conv, _zernio_channel, combined_text,
                        _zernio_sender, ids,
                    )
                    reply_result = handle_incoming_whatsapp_message(
                        final_msg, channel=_zernio_channel,
                        inbound_already_stored=True,
                        include_media=True)
                    reply_media = None
                    if isinstance(reply_result, dict):
                        reply_text = str(reply_result.get("text") or "")
                        reply_media = reply_result.get("media") if isinstance(reply_result.get("media"), dict) else None
                        reply_vehicle_recommendation = (
                            reply_result.get("vehicle_recommendation")
                            if isinstance(reply_result.get("vehicle_recommendation"), dict)
                            else None
                        )
                        reply_quote_confirmation = (
                            reply_result.get("quote_confirmation")
                            if isinstance(reply_result.get("quote_confirmation"), dict)
                            else None
                        )
                        ali_turn_commit = (
                            reply_result.get("ali_turn_commit")
                            if isinstance(reply_result.get("ali_turn_commit"), dict)
                            else None
                        )
                        mermaid_delivery_commit = (
                            reply_result.get("mermaid_delivery_commit")
                            if isinstance(reply_result.get("mermaid_delivery_commit"), dict)
                            else None
                        )
                        mermaid_generation_failure = reply_result.get("mermaid_generation_failure")
                        ali_customer_delivery_deferred = bool(
                            reply_result.get("ali_customer_delivery_deferred")
                        )
                    else:
                        reply_text = reply_result
                else:
                    # Q&A only — use DM agent
                    _dm_msg = {
                        "conversation_id": _zernio_conv,
                        "platform": "whatsapp",
                        "channel": _zernio_channel,
                        "sender_name": _zernio_sender,
                        "text": combined_text,
                        "account_id": _zernio_acct,
                        "message_id": final_msg.get("message_id", ""),
                        # Metadata only: the Q&A agent must not fetch unknown
                        # customer media or lose an attachment-only turn.
                        "attachments": [
                            {"type": str(attachment.get("type") or "attachment")}
                            for attachment in final_msg.get("_zernio_attachments") or []
                            if isinstance(attachment, dict)
                        ],
                    }
                    # Store user message before DM agent (same as DM path)
                    if not worker_is_current():
                        return
                    state_registry.dm_store_inbound_message(
                        _zernio_conv, _zernio_channel, combined_text,
                        _zernio_sender, ids,
                    )
                    dm_result = handle_incoming_dm(_dm_msg, defer_handoff=True)
                    if isinstance(dm_result, dict):
                        reply_text = str(dm_result.get("text") or "")
                        handoff_notification = dm_result.get("handoff_notification")
                    else:
                        reply_text = dm_result
                reply_text = _sanitize_tenant_whatsapp_reply(
                    reply_text, _zernio_channel)
                if reply_text:
                    if not _automated_send_still_enabled(
                        _zernio_channel,
                        _zernio_conv,
                        ids,
                        processing_token,
                        durable_batch_id,
                    ):
                        return
                    if isinstance(handoff_notification, dict):
                        try:
                            committed_handoff = state_registry.inbound_processing_commit_handoff(
                                ids,
                                durable_batch_id,
                                processing_token,
                                account_id=str(_zernio_acct or ""),
                                notification=handoff_notification,
                            )
                        except state_registry.HandoffAccountReassignedError:
                            state_registry.inbound_processing_quarantine_batch(
                                ids, reason="handoff_account_reassigned",
                                processing_token=processing_token,
                            )
                            return
                        except Exception as handoff_error:
                            state_registry.inbound_processing_bulk_update(
                                ids, "recovering",
                                reason="handoff_persistence_unavailable",
                                error=type(handoff_error).__name__,
                                processing_token=processing_token,
                            )
                            return
                        if not committed_handoff:
                            return
                        if not _automated_send_still_enabled(
                            _zernio_channel, _zernio_conv, ids,
                            processing_token, durable_batch_id,
                        ):
                            return
                    attachment_url = str((reply_media or {}).get("url") or "")
                    recommendation_delivery = None
                    confirmation_delivery = None
                    if (
                        _zernio_channel == "whatsapp"
                        and reply_vehicle_recommendation
                    ):
                        reply_vehicle_recommendation = dict(
                            reply_vehicle_recommendation
                        )
                        reply_vehicle_recommendation["text"] = reply_text
                        recommendation_delivery = send_dm_vehicle_recommendation(
                            _zernio_conv,
                            _zernio_acct,
                            reply_vehicle_recommendation,
                        )
                        ok = bool(recommendation_delivery.get("success"))
                    elif (
                        _zernio_channel == "whatsapp"
                        and reply_quote_confirmation
                    ):
                        reply_quote_confirmation = dict(
                            reply_quote_confirmation
                        )
                        reply_quote_confirmation["text"] = reply_text
                        confirmation_delivery = send_dm_quote_confirmation(
                            _zernio_conv,
                            _zernio_acct,
                            reply_quote_confirmation,
                        )
                        ok = bool(confirmation_delivery.get("success"))
                    else:
                        delivery_idempotency_key = (
                            "mermaid-model-status:" + str(final_msg.get("_ali_action_id") or "")
                            if mermaid_generation_failure else
                            f"mermaid-delivery:{mermaid_delivery_commit['job_id']}"
                            if mermaid_delivery_commit else
                            f"ali-turn-{ali_turn_commit['action_id']}"
                            if ali_turn_commit
                            else "unboks-auto-reply-"
                            + str(final_msg.get("_ali_action_id") or "")
                        )
                        delivery_error_code = "provider_returned_false"
                        try:
                            ok = send_reply(
                                _zernio_channel,
                                _zernio_conv,
                                _zernio_acct,
                                reply_text,
                                attachment_url=attachment_url,
                                attachment_type=str((reply_media or {}).get("type") or "image"),
                                attachment_name=str((reply_media or {}).get("filename") or ""),
                                confirm_delivery=True,
                                idempotency_key=delivery_idempotency_key,
                            )
                        except (ZernioReplyError, RequestException, TimeoutError, ConnectionError) as exc:
                            ok = False
                            delivery_error_code = type(exc).__name__
                    if not batch_account_is_current():
                        return
                    if mermaid_delivery_commit:
                        from agents.social.mermaid_documents import mark_delivery
                        mark_delivery(mermaid_delivery_commit["job_id"], bool(ok), "" if ok else "provider returned false")
                    if not ok:
                        # Provider read/mutation guards may have rejected an
                        # account-control outage. Preserve that durable retry
                        # state instead of classifying it as delivery failure.
                        if not batch_account_is_current():
                            return
                        if mermaid_generation_failure:
                            from agents.social.mermaid_model_recovery import defer_inbound
                            defer_inbound(ids, processing_token, mermaid_generation_failure)
                            return
                        log("zernio_reply_send_failed",
                            channel=_zernio_channel,
                            conversation_id=_zernio_conv[:20],
                            media_attached=bool(attachment_url),
                            vehicle_recommendation=bool(reply_vehicle_recommendation))
                        if mermaid_delivery_commit or (reply_media or {}).get("type") in {"mermaid_date_confirmation", "mermaid_document_language"}:
                            _mark_ali_delivery_retry(_zernio_channel, _zernio_conv, ids, "mermaid_quote", processing_token=processing_token)
                        elif ali_turn_commit:
                            _mark_ali_delivery_retry(
                                _zernio_channel,
                                _zernio_conv,
                                ids,
                                (
                                    "quote_confirmation"
                                    if reply_quote_confirmation
                                    else "vehicle_recommendation"
                                    if reply_vehicle_recommendation
                                    else "ali_turn"
                                ),
                                processing_token=processing_token,
                            )
                        else:
                            _mark_fenced_zernio_delivery_failed(
                                _zernio_channel, _zernio_conv, _zernio_sender,
                                ids,
                                batch_id=durable_batch_id,
                                processing_token=processing_token,
                                account_id=str(_zernio_acct or ""),
                                error_code=(
                                    delivery_error_code
                                    if not reply_vehicle_recommendation and not reply_quote_confirmation
                                    else "provider_returned_false"
                                ),
                            )
                        return
                    if not batch_account_is_current():
                        return
                    if ali_turn_commit:
                        if not commit_ali_turn_delivery(
                            _zernio_conv,
                            ali_turn_commit,
                            reply_text,
                            ids,
                            channel=_zernio_channel,
                            recommendation_state_hash=str(
                                (reply_vehicle_recommendation or {}).get("state_hash") or ""
                            ),
                            recommendation_delivery=str(
                                (recommendation_delivery or {}).get("delivery") or ""
                            ),
                            recommendation_vehicle_ids=[
                                str(option.get("id") or "")
                                for option in (reply_vehicle_recommendation or {}).get("options") or []
                                if isinstance(option, dict)
                            ],
                            recommendation_provider_message_ids=list(
                                (recommendation_delivery or {}).get("provider_message_ids") or []
                            ),
                            recommendation_provider_parts=dict(
                                (recommendation_delivery or {}).get("provider_parts") or {}
                            ),
                            recommendation_snapshot=reply_vehicle_recommendation,
                            recommendation_account_id=_zernio_acct,
                            confirmation_delivery=str(
                                (confirmation_delivery or {}).get("delivery") or ""
                            ),
                            confirmation_payload=str(
                                ((reply_quote_confirmation or {}).get("button") or {}).get("payload")
                                or ""
                            ),
                            confirmation_provider_message_ids=list(
                                (confirmation_delivery or {}).get("provider_message_ids") or []
                            ),
                            inbound_processing_token=processing_token,
                        ):
                            return
                    elif mermaid_generation_failure:
                        from agents.social.mermaid_model_recovery import notice_sent
                        state_registry.dm_store_message_once(
                            conversation_id=_zernio_conv, channel=_zernio_channel,
                            role="assistant", text=reply_text,
                            source_message_key=delivery_idempotency_key,
                        )
                        notice_sent(mermaid_generation_failure)
                    else:
                        state_registry.dm_store_message(
                            conversation_id=_zernio_conv,
                            channel=_zernio_channel,
                            role="assistant",
                            text=reply_text,
                        )
                    if recommendation_delivery:
                        delivery = str(
                            recommendation_delivery.get("delivery") or ""
                        )
                        state_hash = str(
                            reply_vehicle_recommendation.get("state_hash") or ""
                        )
                        if not ali_turn_commit:
                            state_registry.wa_mark_vehicle_recommendation_delivered(
                                _zernio_conv,
                                state_hash,
                                delivery,
                                [
                                    str(option.get("id") or "")
                                    for option in reply_vehicle_recommendation.get("options") or []
                                    if isinstance(option, dict)
                                ],
                            )
                        state_registry.dm_store_message(
                            conversation_id=_zernio_conv,
                            channel=_zernio_channel,
                            role="system",
                            text=(
                                "Ali vehicle recommendation sent: "
                                f"{delivery}; {len(reply_vehicle_recommendation.get('options') or [])} option(s)"
                            ),
                        )
                    if attachment_url:
                        if str((reply_media or {}).get("type") or "image") == "image":
                            state_registry.increment_photo_used_count(int(reply_media["id"]))
                        state_registry.dm_store_message(
                            conversation_id=_zernio_conv,
                            channel=_zernio_channel,
                            role="system",
                            text=f"Attachment sent: {reply_media.get('caption') or reply_media.get('filename')}",
                        )
                    if mermaid_generation_failure:
                        from agents.social.mermaid_model_recovery import defer_inbound
                        defer_inbound(ids, processing_token, mermaid_generation_failure)
                    elif not ali_turn_commit:
                        state_registry.inbound_processing_bulk_update(
                            ids,
                            "replied",
                            reason="provider_send_ok",
                            processing_token=processing_token,
                        )
                else:
                    if mermaid_generation_failure:
                        from agents.social.mermaid_model_recovery import defer_inbound
                        defer_inbound(ids, processing_token, mermaid_generation_failure)
                    elif ali_customer_delivery_deferred:
                        log(
                            "ali_customer_delivery_deferred",
                            conversation_id=_zernio_conv[:20],
                            delivery_owner="reservation_v2_scheduler",
                        )
                        state_registry.inbound_processing_bulk_update(
                            ids,
                            "replied",
                            reason="reservation_v2_scheduler_owns_reply",
                            processing_token=processing_token,
                        )
                    else:
                        state_registry.inbound_processing_bulk_update(
                            ids,
                            "ignored",
                            reason="no_reply_returned",
                            processing_token=processing_token,
                        )
            else:
                meta_destination_state = _direct_meta_batch_destination_state(
                    messages
                )
                if meta_destination_state is None:
                    state_registry.inbound_processing_bulk_update(
                        ids,
                        "recovering",
                        reason="tenant_account_control_unavailable",
                        processing_token=processing_token,
                    )
                    return
                if meta_destination_state is False:
                    state_registry.inbound_processing_quarantine_batch(
                        ids,
                        reason="debounce_meta_destination_reassigned",
                        processing_token=processing_token,
                    )
                    return
                if not _whatsapp_inbox_still_enabled(
                    "whatsapp", phone, ids, processing_token
                ):
                    return
                auto_reply_state = icp_overrides.auto_reply_state(
                    icp_overrides.fetch_overrides()
                )
                if auto_reply_state is None:
                    state_registry.inbound_processing_bulk_update(
                        ids,
                        "processing",
                        reason="tenant_runtime_controls_unavailable",
                        processing_token=processing_token,
                    )
                    return
                # Brief 220: per-conversation runtime block (Meta-legacy WhatsApp path).
                if state_registry.get_blocked(phone):
                    log("whatsapp_meta_blocked_conversation", phone=phone[:20])
                    state_registry.inbound_processing_bulk_update(
                        ids,
                        "ignored",
                        reason="blocked_conversation",
                        processing_token=processing_token,
                    )
                    return
                if not worker_is_current():
                    return
                state_registry.dm_store_inbound_message(
                    phone,
                    "whatsapp",
                    combined_text,
                    str(final_msg.get("from_name") or ""),
                    ids,
                )
                # Brief 213: ai_muted check for Meta legacy WhatsApp.
                if state_registry.get_ai_muted(phone):
                    log("whatsapp_meta_ai_muted", phone=phone[:20])
                    state_registry.inbound_processing_bulk_update(
                        ids,
                        "escalated",
                        reason="human_takeover_ai_muted",
                        processing_token=processing_token,
                    )
                    return  # exits the with _phone_lock block; _flush_buffer returns
                if auto_reply_state is False:
                    log("tenant_agent_paused", phone=phone[:20], channel="whatsapp")
                    state_registry.inbound_processing_bulk_update(
                        ids,
                        "paused",
                        reason="tenant_agent_paused",
                        processing_token=processing_token,
                    )
                    return
                # Meta WhatsApp (legacy) — original path
                reply_result = handle_incoming_whatsapp_message(
                    final_msg, inbound_already_stored=True)
                ali_turn_commit = None
                if isinstance(reply_result, dict):
                    reply_text = str(reply_result.get("text") or "")
                    ali_turn_commit = (
                        reply_result.get("ali_turn_commit")
                        if isinstance(reply_result.get("ali_turn_commit"), dict)
                        else None
                    )
                else:
                    reply_text = reply_result
                reply_text = _sanitize_tenant_whatsapp_reply(
                    reply_text, "whatsapp")
                if reply_text:
                    if not _automated_send_still_enabled(
                        "whatsapp",
                        phone,
                        ids,
                        processing_token,
                        durable_batch_id,
                    ):
                        return
                    # Environment-backed provider credentials may have changed
                    # while debounce/model work was running.  Revalidate every
                    # signed member immediately before reserving the one send.
                    meta_destination_state = _direct_meta_batch_destination_state(
                        messages
                    )
                    if meta_destination_state is None:
                        state_registry.inbound_processing_bulk_update(
                            ids,
                            "recovering",
                            reason="tenant_account_control_unavailable",
                            processing_token=processing_token,
                        )
                        return
                    if meta_destination_state is False:
                        state_registry.inbound_processing_quarantine_batch(
                            ids,
                            reason="send_meta_destination_reassigned",
                            processing_token=processing_token,
                        )
                        return
                    meta_outbound_key = (
                        "meta-auto-reply-"
                        + str(final_msg.get("_ali_action_id") or "")
                    )
                    if (
                        ids and (
                            not durable_batch_id
                            or not state_registry.inbound_processing_claim_outbound_attempt(
                                ids,
                                meta_outbound_key,
                                durable_batch_id,
                                processing_token=processing_token,
                            )
                        )
                    ):
                        _mark_delivery_failed(
                            "whatsapp",
                            phone,
                            final_msg.get("from_name", ""),
                            ids,
                            "direct Meta send outcome is already recorded or ambiguous",
                            processing_token=processing_token,
                        )
                        return
                    ok = send_text_message(to=phone, text=reply_text)
                    if not ok:
                        _mark_delivery_failed(
                            "whatsapp", phone, final_msg.get("from_name", ""),
                            ids,
                            "provider returned false",
                            processing_token=processing_token,
                        )
                        return
                    if not worker_is_current():
                        return
                    if ali_turn_commit:
                        if not commit_ali_turn_delivery(
                            phone, ali_turn_commit, reply_text, ids,
                            channel="whatsapp",
                            inbound_processing_token=processing_token,
                        ):
                            return
                    else:
                        state_registry.wa_store_message(phone, "assistant", reply_text)
                        state_registry.inbound_processing_bulk_update(
                            ids,
                            "replied",
                            reason="provider_send_ok",
                            processing_token=processing_token,
                        )
                else:
                    state_registry.inbound_processing_bulk_update(
                        ids,
                        "ignored",
                        reason="no_reply_returned",
                        processing_token=processing_token,
                    )
        except Exception as e:
            state_registry.inbound_processing_bulk_update(
                ids,
                "processing_failed",
                reason="exception",
                error=str(e),
                processing_token=processing_token,
            )
            log("webhook_process_error",
                source="zernio_whatsapp" if final_msg.get("_zernio_conversation_id") else "meta_whatsapp",
                error=str(e), phone=phone)
        finally:
            reset_provider_mutation_guard(mutation_scope_token)


@app.post("/webhooks/zernio")
async def receive_zernio_webhook(request: Request, background_tasks: BackgroundTasks):
    """Receive Zernio events, preserving retries until tenant checks succeed."""
    body = await request.body()
    signature = request.headers.get("X-Zernio-Signature", "")

    if not verify_webhook_signature(body, signature):
        log("zernio_webhook_signature_invalid")
        return PlainTextResponse(content="Forbidden", status_code=403)

    try:
        payload = _json.loads(body)
    except Exception:
        log("zernio_webhook_json_invalid")
        return PlainTextResponse(content="Bad Request", status_code=400)
    if not isinstance(payload, dict):
        return PlainTextResponse(content="Bad Request", status_code=400)
    log("webhook_received", source="zernio", webhook_event=payload.get("event", "unknown"))
    if payload.get("event") == "message.sent":
        # WhatsApp Business app echoes are operator-authored conversation
        # context.  Persist them before acknowledging the provider so an Nr 3
        # outage or local database failure remains retryable instead of being
        # silently collapsed to a disabled boolean in a background task.
        try:
            _process_zernio_sent_event(payload)
        except _RetryableZernioSentControlError:
            return PlainTextResponse(content="Unavailable", status_code=503)
        except Exception as exc:
            log(
                "zernio_sent_durable_accept_failed",
                error=type(exc).__name__,
            )
            return PlainTextResponse(content="Unavailable", status_code=503)
        return PlainTextResponse(content="OK", status_code=200)
    if payload.get("event") == "message.failed":
        failed = parse_zernio_failed_webhook(payload)
        if not failed:
            return PlainTextResponse(content="OK", status_code=200)
        try:
            if not _zernio_failed_account_is_current(failed):
                return PlainTextResponse(content="OK", status_code=200)
            # Paused/taken-over conversations still need a visible record of
            # a late provider failure; only automated fallback sends stop.
            _zernio_failed_automation_enabled(failed)
            if not _zernio_failed_account_is_current(failed):
                return PlainTextResponse(content="OK", status_code=200)
            event_key, _inserted = state_registry.zernio_failed_event_accept(
                failed
            )
            if not event_key:
                raise RuntimeError("invalid normalized failed event")
        except _RetryableZernioFailureControlError:
            return PlainTextResponse(content="Unavailable", status_code=503)
        except Exception as exc:
            log(
                "zernio_failed_event_reconcile_unavailable",
                message_id=failed.get("message_id", "")[:30],
                error=type(exc).__name__,
            )
            return PlainTextResponse(content="Unavailable", status_code=503)
        # Starlette runs synchronous background tasks in its worker pool after
        # the response has been emitted.  The durable scanner above remains
        # the source of truth if this process exits before or during the task.
        background_tasks.add_task(_process_queued_zernio_failed_events_once)
        return PlainTextResponse(content="OK", status_code=200)
    if payload.get("event") == "message.received":
        msg = parse_zernio_webhook(payload)
        if not msg:
            return PlainTextResponse(content="OK", status_code=200)
        from shared.tenant_guard import account_access_state
        try:
            account_state = account_access_state(
                msg.get("account_id", ""), direction="inbound"
            )
        except Exception as exc:
            log(
                "zernio_event_account_control_unavailable",
                message_id=msg.get("message_id", "")[:30],
                error=type(exc).__name__,
            )
            return PlainTextResponse(content="Unavailable", status_code=503)
        if account_state is None:
            log(
                "zernio_event_account_control_unavailable",
                message_id=msg.get("message_id", "")[:30],
            )
            return PlainTextResponse(content="Unavailable", status_code=503)
        if account_state is False:
            log(
                "zernio_event_account_not_allowlisted",
                account_id=msg.get("account_id", "")[:20],
                message_id=msg.get("message_id", "")[:30],
            )
            return PlainTextResponse(content="OK", status_code=200)
        if msg.get("platform") == "whatsapp":
            envelope = icp_overrides.fetch_overrides()
            inbox_state = icp_overrides.whatsapp_inbox_state(envelope)
            if inbox_state is None:
                log("whatsapp_runtime_controls_unavailable", source="zernio")
                return PlainTextResponse(content="Unavailable", status_code=503)
            if inbox_state is False:
                log(
                    "whatsapp_inbox_disabled",
                    source="zernio",
                    account_id=msg.get("account_id", "")[:20],
                    message_id=msg.get("message_id", "")[:30],
                )
                return PlainTextResponse(content="OK", status_code=200)
        try:
            # Nr 3 control I/O may overlap an account reassignment. Do not
            # persist customer payload under the earlier ownership decision.
            final_account_state = account_access_state(
                msg.get("account_id", ""), direction="inbound"
            )
            if final_account_state is None:
                return PlainTextResponse(content="Unavailable", status_code=503)
            if final_account_state is False:
                return PlainTextResponse(content="OK", status_code=200)
            claimed = state_registry.wa_claim_inbound_processing(
                msg.get("message_id", ""),
                conversation_id=msg.get("conversation_id", ""),
                channel=msg.get("channel", ""),
                payload=msg,
                acceptance_batch_id=_acceptance_batch_bindings([msg]).get(
                    str(msg.get("message_id") or ""), ("", 0)
                )[0],
            )
        except Exception as exc:
            log("zernio_durable_accept_failed", error=type(exc).__name__)
            return PlainTextResponse(content="Unavailable", status_code=503)
        if not claimed:
            log(
                "webhook_duplicate_skipped",
                source="zernio",
                message_id=msg.get("message_id", ""),
            )
            return PlainTextResponse(content="OK", status_code=200)
        background_tasks.add_task(_process_zernio_event, payload, msg, True)
    else:
        background_tasks.add_task(_process_zernio_event, payload)
    return PlainTextResponse(content="OK", status_code=200)


def _normalize_phone_digits(phone: str) -> str:
    """Brief 208: collapse a phone-like string to ASCII digits only.
    Strips Unicode digits (fullwidth ５９９ etc.), separators, plus signs,
    and the 'ext'/'x'/'#' suffix that some clients add for extensions."""
    if not phone:
        return ""
    s = str(phone)
    # Strip extension suffix and everything after it
    for marker in (" ext ", " x ", "#"):
        idx = s.lower().find(marker)
        if idx >= 0:
            s = s[:idx]
            break
    import re
    return re.sub(r"[^0-9]", "", s)


def _external_operator_message_text(msg: dict) -> str:
    """Return visible text for a phone-app message, including media-only sends."""
    text = str(msg.get("text") or "").strip()
    labels = {
        "image": "📷 Imagen enviada desde WhatsApp",
        "video": "🎥 Vídeo enviado desde WhatsApp",
        "audio": "🎤 Audio enviado desde WhatsApp",
        "voice": "🎤 Nota de voz enviada desde WhatsApp",
        "document": "📎 Documento enviado desde WhatsApp",
        "file": "📎 Archivo enviado desde WhatsApp",
        "sticker": "Sticker enviado desde WhatsApp",
        "location": "📍 Ubicación enviada desde WhatsApp",
        "contact": "👤 Contacto enviado desde WhatsApp",
    }
    media_lines = []
    for attachment in msg.get("attachments") or []:
        if not isinstance(attachment, dict):
            continue
        attachment_type = str(attachment.get("type") or "").lower()
        label = labels.get(attachment_type, "📎 Archivo enviado desde WhatsApp")
        filename = str(attachment.get("filename") or "").strip()
        media_lines.append(f"{label}: {filename}" if filename else label)
    if text and media_lines:
        return text + "\n" + "\n".join(media_lines)
    return text or "\n".join(media_lines)


def _process_zernio_sent_event(payload: dict) -> None:
    """Mirror a secretary reply from the WhatsApp Business app into Unboks.

    Cloud-API echoes are intentionally ignored because Unboks already stores
    its own sent messages. The outgoing event is never passed to Alia.
    """
    msg = parse_zernio_sent_webhook(payload)
    if not msg:
        return
    if (
        msg.get("platform") != "whatsapp"
        or msg.get("direction") != "outgoing"
        or msg.get("source") != "whatsappbusinessapp"
    ):
        log(
            "zernio_sent_event_ignored",
            platform=msg.get("platform", ""),
            direction=msg.get("direction", ""),
            source=msg.get("source", ""),
        )
        return

    from shared.tenant_guard import account_access_state
    account_state = account_access_state(
        msg.get("account_id", ""), direction="inbound"
    )
    if account_state is None:
        log(
            "zernio_sent_event_account_control_unavailable",
            message_id=msg.get("message_id", "")[:30],
        )
        raise _RetryableZernioSentControlError(
            "strict Zernio account controls are unavailable"
        )
    if account_state is False:
        log(
            "zernio_sent_event_account_not_allowlisted",
            account_id=msg.get("account_id", "")[:20],
        )
        return
    envelope = icp_overrides.fetch_overrides_fresh()
    inbox_state = icp_overrides.whatsapp_inbox_state(envelope)
    if inbox_state is None:
        log(
            "whatsapp_runtime_controls_unavailable",
            source="zernio_sent",
            message_id=msg.get("message_id", "")[:30],
        )
        raise _RetryableZernioSentControlError(
            "strict WhatsApp controls are unavailable"
        )
    if inbox_state is False:
        log(
            "whatsapp_inbox_disabled",
            source="zernio_sent",
            message_id=msg.get("message_id", "")[:30],
        )
        return

    visible_text = _external_operator_message_text(msg)
    if not visible_text:
        log(
            "zernio_sent_event_empty",
            conversation_id=msg.get("conversation_id", "")[:20],
            message_id=msg.get("message_id", "")[:30],
        )
        return

    # Runtime controls may involve I/O.  Recheck exact account ownership at
    # the last boundary before operator-authored content mutates local state.
    # Unknown ownership must remain provider-retryable; a known reassignment
    # is a clean no-op for this tenant.
    account_state = account_access_state(
        msg.get("account_id", ""), direction="inbound"
    )
    if account_state is None:
        log(
            "zernio_sent_event_account_control_unavailable",
            message_id=msg.get("message_id", "")[:30],
        )
        raise _RetryableZernioSentControlError(
            "strict Zernio account controls are unavailable"
        )
    if account_state is False:
        log(
            "zernio_sent_event_account_not_allowlisted",
            account_id=msg.get("account_id", "")[:20],
        )
        return

    stored = state_registry.wa_store_external_operator_message(
        message_id=msg["message_id"],
        conversation_id=msg["conversation_id"],
        channel=msg.get("channel") or "whatsapp",
        text=visible_text,
        sender_name="Secretaría",
        created_at=msg.get("created_at") or "",
    )
    # Always repeat the visibility repair for a provider replay.  If the first
    # attempt stored the operator message but crashed while unarchiving, the
    # provider's retry can still finish that second idempotent mutation.
    state_registry.wa_set_archived(msg["conversation_id"], False)
    if not stored:
        log(
            "zernio_sent_event_duplicate",
            conversation_id=msg["conversation_id"][:20],
            message_id=msg["message_id"][:30],
        )
        return

    log(
        "zernio_whatsapp_app_reply_synced",
        conversation_id=msg["conversation_id"][:20],
        message_id=msg["message_id"][:30],
        sender_name="Secretaría",
    )


def _zernio_failed_account_is_current(failed: dict) -> bool:
    """Return known ownership or raise while strict controls are unavailable."""
    from shared.tenant_guard import account_access_state

    try:
        account_state = account_access_state(
            failed.get("account_id", ""), direction="inbound"
        )
    except Exception as exc:
        log(
            "zernio_failed_event_account_control_unavailable",
            message_id=failed.get("message_id", "")[:30],
            error=type(exc).__name__,
        )
        raise _RetryableZernioFailureControlError(
            "strict Zernio account controls are unavailable"
        ) from exc
    if account_state is None:
        log(
            "zernio_failed_event_account_control_unavailable",
            message_id=failed.get("message_id", "")[:30],
        )
        raise _RetryableZernioFailureControlError(
            "strict Zernio account controls are unavailable"
        )
    if account_state is False:
        log(
            "zernio_failed_event_account_not_allowlisted",
            account_id=failed.get("account_id", "")[:20],
        )
        return False
    return True


def _zernio_failed_automation_enabled(failed: dict) -> bool:
    """Known hard stops consume no automatic recovery; outages remain retryable."""
    try:
        envelope = icp_overrides.fetch_overrides_fresh()
        inbox_state = icp_overrides.whatsapp_inbox_state(envelope)
        auto_state = icp_overrides.auto_reply_state(envelope)
    except Exception as exc:
        raise _RetryableZernioFailureControlError(
            "failed-delivery runtime controls unavailable"
        ) from exc
    if inbox_state is None or auto_state is None:
        raise _RetryableZernioFailureControlError(
            "failed-delivery runtime controls unavailable"
        )
    conversation_id = str(failed.get("conversation_id") or "")
    return bool(
        inbox_state is True
        and auto_state is True
        and not state_registry.get_blocked(conversation_id)
        and not state_registry.get_ai_muted(conversation_id)
    )


def _process_zernio_failed_event(failed: dict, *, claim_is_current=None) -> bool | str:
    """Reconcile one durable failure under fresh ownership/claim fences."""
    def account_is_current() -> bool:
        if claim_is_current is not None and not claim_is_current():
            raise _RetryableZernioFailureControlError("failed event claim was lost")
        return _zernio_failed_account_is_current(failed)

    if not account_is_current():
        return False
    if not _zernio_failed_automation_enabled(failed):
        return "needs_attention"

    def provider_may_mutate() -> bool:
        # Refresh the controls first, then ownership and the worker lease, so
        # neither account reassignment nor claim takeover during Nr 3 I/O can
        # authorize a stale fallback.
        return _zernio_failed_automation_enabled(failed) and account_is_current()

    # The recommendation claim itself mutates durable workflow state.  Fence
    # it independently from the route-level preflight above.
    if not account_is_current():
        return False
    recommendation_failure = state_registry.wa_claim_vehicle_recommendation_failure(
        failed["conversation_id"], failed["message_id"],
    )
    if recommendation_failure.get("recovery_in_progress"):
        log(
            "zernio_failed_event_recovery_in_progress",
            conversation_id=failed["conversation_id"][:20],
            message_id=failed["message_id"][:30],
        )
        raise _RetryableZernioFailureControlError(
            "failed-delivery recovery is already leased"
        )
    reconciled = bool(recommendation_failure.get("matched"))
    confirmation_failure = None
    if not reconciled:
        if not account_is_current():
            return False
        confirmation_failure = reconcile_quote_confirmation_failure(
            failed["conversation_id"], failed["message_id"],
        )
    fallback_attempted = False
    fallback_sent = False
    workflow = (config_loader.get_raw() or {}).get("workflow") or {}
    actionable_recommendation_failure = (
        recommendation_failure.get("matched")
        and not recommendation_failure.get("already_handled")
    )
    if actionable_recommendation_failure and workflow.get("type") != "ali_quote":
        # A durable recovery lease must never be consumed by an ACK unless it
        # is completed below.  In strict deployments an unreadable config is
        # exposed as an empty document, so treating this as an irrelevant
        # workflow would strand the leased failure after returning 200.
        raise _RetryableZernioFailureControlError(
            "vehicle recovery workflow controls are unavailable"
        )
    if (
        workflow.get("type") == "ali_quote"
        and actionable_recommendation_failure
    ):
        if not account_is_current():
            return False
        fallback_attempted = True
        recovery_result = {"success": False, "delivery": "recovery_error"}
        account_id = str(
            failed.get("account_id")
            or recommendation_failure.get("account_id")
            or ""
        )
        try:
            catalog = get_intake_catalog(force_refresh=True)
            if not provider_may_mutate():
                return False
            recovery_result = recover_dm_vehicle_recommendation(
                failed["conversation_id"],
                account_id,
                recommendation_failure,
                catalog,
            )
        except _RetryableZernioFailureControlError:
            raise
        except Exception as exc:
            log(
                "ali_vehicle_media_recovery_error",
                conversation_id=failed["conversation_id"][:20],
                error=type(exc).__name__,
            )
        fallback_sent = bool(recovery_result.get("success"))
        if not fallback_sent:
            if not account_is_current():
                return False
            recovery_text = str(
                (recommendation_failure.get("snapshot") or {}).get("text")
                or "I couldn't load the car photos. I can still help you choose here."
            ).strip()
            fallback_key = hashlib.sha256(
                str(failed["message_id"]).encode("utf-8")
            ).hexdigest()
            try:
                if not provider_may_mutate():
                    return False
                fallback_sent = bool(account_id) and send_reply(
                    "whatsapp",
                    failed["conversation_id"],
                    account_id,
                    recovery_text,
                    confirm_delivery=True,
                    idempotency_key=(
                        f"ali-late-media-text-fallback-{fallback_key}"
                    ),
                )
            except _RetryableZernioFailureControlError:
                raise
            except Exception as exc:
                log(
                    "ali_vehicle_media_text_fallback_error",
                    conversation_id=failed["conversation_id"][:20],
                    error=type(exc).__name__,
                )
            if not account_is_current():
                return False
            state_registry.create_pending_notification(
                "technical",
                "whatsapp",
                failed["conversation_id"],
                "Ali rental customer",
                "[ALI VEHICLE IMAGES FAILED]",
                "The vehicle carousel and automatic image recovery failed. Open the conversation in Unboks.",
            )
        if not account_is_current():
            return False
        if not state_registry.wa_complete_vehicle_recommendation_recovery(
            failed["conversation_id"],
            recommendation_failure,
            recovery_result,
        ):
            raise RuntimeError("vehicle recommendation recovery lease was lost")
        log(
            "ali_vehicle_media_recovery_sent"
            if fallback_sent
            else "ali_vehicle_media_recovery_failed",
            conversation_id=failed["conversation_id"][:20],
            message_id=failed["message_id"][:30],
            recovery_delivery=str(recovery_result.get("delivery") or "")[:40],
        )
    confirmation_recovery_attempted = False
    confirmation_recovery_sent = False
    if (
        workflow.get("type") == "ali_quote"
        and confirmation_failure
        and confirmation_failure.get("matched")
        and not confirmation_failure.get("already_recovered")
        and failed.get("account_id")
    ):
        if not account_is_current():
            return False
        confirmation_recovery_attempted = True
        source_text = str(failed.get("text") or "").strip()
        if not source_text:
            history = state_registry.wa_get_full_history(
                failed["conversation_id"], limit=20,
            )
            source_text = next(
                (
                    str(item.get("text") or "").strip()
                    for item in reversed(history)
                    if item.get("role") == "assistant"
                    and str(item.get("text") or "").strip()
                ),
                "",
            )
        recovery_text = _quote_confirmation_fallback_text(
            failed["conversation_id"], source_text,
        )
        fallback_key = hashlib.sha256(
            str(failed["message_id"]).encode("utf-8")
        ).hexdigest()
        try:
            if not provider_may_mutate():
                return False
            confirmation_recovery_sent = send_reply(
                "whatsapp",
                failed["conversation_id"],
                failed["account_id"],
                recovery_text,
                confirm_delivery=True,
                idempotency_key=(
                    f"ali-late-confirmation-fallback-{fallback_key}"
                ),
            )
        except _RetryableZernioFailureControlError:
            raise
        except Exception as exc:
            log(
                "ali_quote_confirmation_late_fallback_error",
                conversation_id=failed["conversation_id"][:20],
                error=type(exc).__name__,
            )
        if not account_is_current():
            return False
        if confirmation_recovery_sent:
            mark_quote_confirmation_failure_recovered(
                failed["conversation_id"], failed["message_id"],
            )
        else:
            state_registry.create_pending_notification(
                "technical",
                "whatsapp",
                failed["conversation_id"],
                "Ali quote customer",
                "[ALI QUOTE CONFIRMATION DELIVERY FAILED]",
                "The Send My Quote control failed and its text fallback could not be delivered. Open the conversation in Unboks.",
            )
        log(
            "ali_quote_confirmation_late_fallback_sent"
            if confirmation_recovery_sent
            else "ali_quote_confirmation_late_fallback_failed",
            conversation_id=failed["conversation_id"][:20],
            message_id=failed["message_id"][:30],
        )
    log(
        "zernio_failed_event_reconciled",
        conversation_id=failed["conversation_id"][:20],
        message_id=failed["message_id"][:30],
        vehicle_recommendation=reconciled,
        fallback_attempted=fallback_attempted,
        fallback_sent=fallback_sent,
        quote_confirmation=bool(confirmation_failure),
        confirmation_recovery_attempted=confirmation_recovery_attempted,
        confirmation_recovery_sent=confirmation_recovery_sent,
        failure_reason=str(failed.get("failure_reason") or "")[:120],
    )
    if not reconciled and not (confirmation_failure or {}).get("matched"):
        return "needs_attention"
    return True


def _process_queued_zernio_failed_events_once(limit: int = 10) -> int:
    """Process leased failed events; every outcome remains crash-recoverable."""
    handled = 0
    claims = state_registry.zernio_failed_event_claim_due(limit=limit)
    for claim in claims:
        event_key = str(claim.get("event_key") or "")
        claim_token = str(claim.get("claim_token") or "")
        failed = claim.get("failed")
        if (
            not isinstance(failed, dict)
            or state_registry.zernio_failed_event_key(failed) != event_key
        ):
            try:
                state_registry.zernio_failed_event_invalid(
                    event_key, claim_token,
                )
            except Exception as exc:
                log(
                    "zernio_failed_event_invalid_transition_failed",
                    error=type(exc).__name__,
                )
            handled += 1
            continue
        try:
            if not state_registry.zernio_failed_event_is_current(
                event_key, claim_token,
            ):
                continue
            def claim_is_current():
                return state_registry.zernio_failed_event_is_current(
                    event_key, claim_token,
                )

            def provider_may_mutate():
                if not _zernio_failed_automation_enabled(failed):
                    return False
                if not _zernio_failed_account_is_current(failed):
                    return False
                if not claim_is_current():
                    raise _RetryableZernioFailureControlError("failed event claim was lost")
                return True

            with provider_mutation_scope(provider_may_mutate):
                reconciled = _process_zernio_failed_event(
                    failed, claim_is_current=claim_is_current,
                )
            transitioned = (
                state_registry.zernio_failed_event_complete_with_attention(
                    event_key, claim_token, failed,
                )
                if reconciled == "needs_attention"
                else
                state_registry.zernio_failed_event_complete(
                    event_key, claim_token,
                )
                if reconciled
                else state_registry.zernio_failed_event_ignore(
                    event_key, claim_token,
                )
            )
            if not transitioned:
                log("zernio_failed_event_queue_claim_superseded")
        except state_registry.HandoffAccountReassignedError:
            state_registry.zernio_failed_event_ignore(event_key, claim_token)
        except Exception as exc:
            try:
                state_registry.zernio_failed_event_retry(
                    event_key,
                    claim_token,
                    error_code=type(exc).__name__,
                )
            except Exception as retry_exc:
                # Leave the processing lease intact.  A restart or the scanner
                # will reclaim it after expiry even if this release write fails.
                log(
                    "zernio_failed_event_retry_transition_failed",
                    error=type(retry_exc).__name__,
                )
            log(
                "zernio_failed_event_processing_deferred",
                error=type(exc).__name__,
            )
        handled += 1
    return handled


def _process_zernio_event(
    payload: dict,
    accepted_message: dict | None = None,
    message_was_claimed: bool = False,
):
    """Background task: parse Zernio webhook, dedup, route DM to booking or Q&A."""
    try:
        if payload.get("event") == "message.failed":
            failed = parse_zernio_failed_webhook(payload)
            if failed:
                if _zernio_failed_account_is_current(failed):
                    state_registry.zernio_failed_event_accept(failed)
                    _process_queued_zernio_failed_events_once()
            return
        if payload.get("event") == "message.sent":
            _process_zernio_sent_event(payload)
            return

        msg = accepted_message or parse_zernio_webhook(payload)
        if not msg:
            return  # Not a message event or unparseable

        message_id = msg["message_id"]
        # Zernio subscriptions are team-wide. Validate tenant ownership before
        # *any* dedup marker, inbound ledger row, or customer payload is
        # persisted. Otherwise a foreign event leaks data and consumes its ID,
        # preventing the rightful tenant from processing a replay later.
        if message_was_claimed:
            from shared.tenant_guard import account_access_state

            try:
                account_state = account_access_state(
                    msg.get("account_id", ""), direction="inbound"
                )
            except Exception:
                account_state = None
            if account_state is None:
                log(
                    "zernio_claimed_event_account_control_unavailable",
                    message_id=message_id[:30],
                )
                return
            if account_state is False:
                state_registry.inbound_processing_quarantine(
                    message_id,
                    reason="claimed_account_not_allowlisted",
                )
                log(
                    "zernio_claimed_event_account_quarantined",
                    message_id=message_id[:30],
                )
                return
        else:
            from shared.tenant_guard import is_account_allowed
            if not is_account_allowed(msg.get("account_id", ""), direction="inbound"):
                log(
                    "zernio_event_account_not_allowlisted",
                    account_id=msg.get("account_id", "")[:20],
                    message_id=message_id[:30],
                )
                return
            if msg.get("platform") == "whatsapp":
                envelope = icp_overrides.fetch_overrides()
                inbox_state = icp_overrides.whatsapp_inbox_state(envelope)
                if inbox_state is None:
                    log(
                        "whatsapp_runtime_controls_unavailable",
                        source="zernio",
                    )
                    return
                if inbox_state is False:
                    log(
                        "whatsapp_inbox_disabled",
                        source="zernio",
                        account_id=msg.get("account_id", "")[:20],
                        message_id=message_id[:30],
                    )
                    return
            from shared.tenant_guard import account_access_state
            if account_access_state(
                msg.get("account_id", ""), direction="inbound"
            ) is not True:
                return
            if not state_registry.wa_claim_inbound_processing(
                message_id,
                conversation_id=msg.get("conversation_id", ""),
                channel=msg.get("channel", ""),
                payload=msg,
                acceptance_batch_id=_acceptance_batch_bindings([msg]).get(
                    str(message_id), ("", 0)
                )[0],
            ):
                log("webhook_duplicate_skipped", source="zernio", message_id=message_id)
                return

        ignored = state_registry.match_ignored_contact(
            channel=msg.get("channel", ""),
            sender_id=msg.get("sender_id", ""),
            phone=msg.get("sender_id", ""),
        ) or state_registry.match_ignored_contact(
            channel=msg.get("channel", ""),
            sender_id=msg.get("conversation_id", ""),
        )
        if ignored:
            state_registry.record_ignored_contact_event(
                contact_id=ignored.get("id"),
                channel=msg.get("channel", ""),
                sender_identifier=msg.get("sender_id") or msg.get("conversation_id", ""),
                message_id=message_id,
            )
            log("ignored_contact_inbound_suppressed",
                channel=msg.get("channel", ""),
                sender=(msg.get("sender_id") or msg.get("conversation_id") or "")[:50],
                message_id=message_id,
                reason="Ignored inbound message because sender is on Excluded Contacts / Ignore List.")
            state_registry.inbound_processing_update(
                message_id, "ignored", reason="ignored_contact")
            return

        # Brief 208: per-tenant ignored_phones list. Drop messages from
        # configured numbers BEFORE any reply-generation path runs.
        _ignored = config_loader.get_raw().get("features", {}).get("ignored_phones", [])
        if _ignored:
            sender_digits = _normalize_phone_digits(msg.get("sender_id", ""))
            for ignored in _ignored:
                if sender_digits and sender_digits == _normalize_phone_digits(str(ignored)):
                    log("zernio_dm_ignored_phone",
                        sender=sender_digits,
                        message_id=message_id)
                    state_registry.inbound_processing_update(
                        message_id, "ignored", reason="ignored_phone")
                    return

        # Brief 220: per-conversation runtime block. Mirrors ignored_phones
        # (which runs above, statically configured) but works on a
        # dashboard-controlled per-conversation_id flag. Drop BEFORE any
        # storage so the conversation doesn't appear in the inbox.
        if state_registry.get_blocked(msg.get("conversation_id", "")):
            log("zernio_dm_blocked_conversation",
                conversation_id=msg.get("conversation_id", "")[:20],
                message_id=message_id)
            state_registry.inbound_processing_update(
                message_id, "ignored", reason="blocked_conversation")
            return

        # Brief 240: auto-resolve operator WhatsApp alert route. If this
        # inbound is from the configured operator phone (whatsapp_destination
        # in alert_settings), persist the Zernio conversation_id + account_id
        # so the alert dispatcher can deliver future operator alerts via
        # Zernio (not Meta). WhatsApp-only - DMing the IG/FB account does
        # not bootstrap a WA alert route. Best-effort: never blocks the
        # inbound event from being processed normally.
        if msg.get("platform") == "whatsapp":
            try:
                _alert_settings = state_registry.get_alert_settings(
                    default_email_destination="")
                _wa_dest = (((_alert_settings or {}).get("channels") or {})
                            .get("whatsapp") or {}).get("destination") or ""
                if _wa_dest:
                    _sender_digits = _normalize_phone_digits(
                        msg.get("sender_id", ""))
                    _dest_digits = _normalize_phone_digits(_wa_dest)
                    if _sender_digits and _sender_digits == _dest_digits:
                        state_registry.set_resolved_operator_whatsapp_route(
                            msg.get("conversation_id", ""),
                            msg.get("account_id", ""))
                        log("operator_whatsapp_route_resolved",
                            sender_digits=_sender_digits,
                            conversation_id=msg.get("conversation_id", "")[:20],
                            account_id=msg.get("account_id", "")[:20])
            except Exception as _e:
                log("operator_whatsapp_route_resolve_failed",
                    error=str(_e)[:200])

        text = msg.get("text", "")
        has_whatsapp_interactive_reply = bool(
            msg.get("platform") == "whatsapp"
            and str(msg.get("interactive_id") or "").strip()
        )
        has_whatsapp_attachment = bool(
            msg.get("platform") == "whatsapp" and msg.get("attachments")
        )
        if not text and not has_whatsapp_interactive_reply and not has_whatsapp_attachment:
            state_registry.inbound_processing_update(
                message_id, "ignored", reason="non_text_message")
            log("zernio_dm_non_text_skipped", message_id=message_id,
                platform=msg.get("platform"))
            return

        log("zernio_dm_received",
            conversation_id=msg["conversation_id"][:20],
            platform=msg["platform"],
            sender=msg["sender_name"][:30])

        conversation_id = msg["conversation_id"]
        channel = msg["channel"]
        account_id = msg["account_id"]

        # WhatsApp via Zernio: debounce like Meta WhatsApp
        if msg["platform"] == "whatsapp":
            adapter_cls = ZERNIO_CHANNELS.get(channel, DEFAULT_ZERNIO_CHANNEL)
            _wa_msg = adapter_cls.from_zernio(msg)
            try:
                typing_controls = icp_overrides.fetch_overrides_fresh()
                typing_inbox_state = icp_overrides.whatsapp_inbox_state(
                    typing_controls
                )
                typing_auto_reply_state = icp_overrides.auto_reply_state(
                    typing_controls
                )
            except Exception:
                typing_inbox_state = None
                typing_auto_reply_state = None
            if (
                typing_inbox_state is None
                or typing_auto_reply_state is None
            ):
                # No cosmetic provider mutation while strict controls are
                # unknown.  Leave the signed payload durable for recovery.
                state_registry.inbound_processing_update(
                    message_id,
                    "processing",
                    reason="tenant_runtime_controls_unavailable",
                )
                return
            if state_registry.get_blocked(conversation_id):
                state_registry.inbound_processing_update(
                    message_id,
                    "ignored",
                    reason="blocked_conversation",
                )
                return
            if (
                typing_inbox_state is True
                and typing_auto_reply_state is True
                and not state_registry.get_ai_muted(conversation_id)
            ):
                send_typing_indicator(conversation_id, account_id)
            _buffer_message(_wa_msg)
            return

        # Send typing indicator (best-effort) — outside the critical section
        send_typing_indicator(conversation_id, account_id)

        # Brief 161: per-phone lock serializes the IG/FB DM path the same way
        # the WhatsApp debounce path is serialized. Required so concurrent
        # Zernio webhooks for the same conversation cannot race on state.
        _dm_lock = _get_phone_lock(conversation_id)
        with _dm_lock:
            # Brief 213: respect ai_muted (operator-takeover state). When a
            # conversation has been muted via /escalations/:id/takeover,
            # store the inbound in the dashboard thread so the operator
            # sees it, but do NOT call the reply handler.
            if state_registry.get_ai_muted(conversation_id):
                state_registry.dm_store_message(
                    conversation_id=conversation_id, channel=channel,
                    role="user", text=text, sender_name=msg["sender_name"])
                log("zernio_dm_ai_muted",
                    conversation_id=conversation_id[:20], channel=channel)
                state_registry.inbound_processing_update(
                    message_id, "escalated", reason="human_takeover_ai_muted")
                return

            if not icp_overrides.auto_reply_enabled():
                state_registry.dm_store_message(
                    conversation_id=conversation_id,
                    channel=channel,
                    role="user",
                    text=text,
                    sender_name=msg["sender_name"],
                )
                log("tenant_agent_paused",
                    conversation_id=conversation_id[:20], channel=channel)
                state_registry.inbound_processing_update(
                    message_id, "paused", reason="tenant_agent_paused")
                return

            # Callback-follow-up tenants need the structured WhatsApp agent
            # even though they intentionally disable booking_flow.
            _orchestrator_on = _use_whatsapp_orchestrator(channel)
            mermaid_delivery_commit = None

            if _orchestrator_on:
                # Full booking flow — route through orchestrator
                # Persist before model/order work so crashes remain visible.
                # handle_incoming_whatsapp_message removes this exact inbound
                # from prompt history when inbound_already_stored=True.
                adapter_cls = ZERNIO_CHANNELS.get(channel, DEFAULT_ZERNIO_CHANNEL)
                orchestrator_msg = adapter_cls.from_zernio(msg)
                state_registry.dm_store_message(
                    conversation_id=conversation_id,
                    channel=channel,
                    role="user",
                    text=text,
                    sender_name=msg["sender_name"],
                )
                reply_result = handle_incoming_whatsapp_message(
                    orchestrator_msg, channel=channel,
                    inbound_already_stored=True,
                    include_media=True)
                reply_media = None
                if isinstance(reply_result, dict):
                    reply_text = str(reply_result.get("text") or "")
                    reply_media = reply_result.get("media") if isinstance(reply_result.get("media"), dict) else None
                    mermaid_delivery_commit = (
                        reply_result.get("mermaid_delivery_commit")
                        if isinstance(reply_result.get("mermaid_delivery_commit"), dict) else None
                    )
                else:
                    reply_text = reply_result
                    mermaid_delivery_commit = None
            else:
                # Q&A only — use DM agent
                # DM agent reads dm_get_history which is separate, so store before is fine
                state_registry.dm_store_message(
                    conversation_id=conversation_id,
                    channel=channel,
                    role="user",
                    text=text,
                    sender_name=msg["sender_name"],
                )
                reply_text = handle_incoming_dm(msg)
                reply_media = None

            reply_text = _sanitize_tenant_whatsapp_reply(reply_text, channel)
            if reply_text:
                attachment_url = str((reply_media or {}).get("url") or "")
                # Send reply via the sender registry (Brief 187 — dispatched by channel)
                ok = send_reply(
                    channel,
                    conversation_id,
                    account_id,
                    reply_text,
                    attachment_url=attachment_url,
                    attachment_type=str((reply_media or {}).get("type") or "image"),
                    attachment_name=str((reply_media or {}).get("filename") or ""),
                    confirm_delivery=bool(mermaid_delivery_commit),
                    idempotency_key=(
                        f"mermaid-delivery:{mermaid_delivery_commit['job_id']}"
                        if mermaid_delivery_commit else ""
                    ),
                )
                if not ok:
                    if mermaid_delivery_commit:
                        from agents.social.mermaid_documents import mark_delivery
                        mark_delivery(mermaid_delivery_commit["job_id"], False, "provider returned false")
                    log("zernio_reply_send_failed",
                        channel=channel,
                        conversation_id=conversation_id[:20],
                        media_attached=bool(attachment_url))
                    _mark_delivery_failed(
                        channel, conversation_id, msg.get("sender_name", ""),
                        [message_id], "provider returned false")
                    return
                if mermaid_delivery_commit:
                    from agents.social.mermaid_documents import mark_delivery
                    mark_delivery(mermaid_delivery_commit["job_id"], True)
                # Store assistant reply
                state_registry.dm_store_message(
                    conversation_id=conversation_id,
                    channel=channel,
                    role="assistant",
                    text=reply_text,
                )
                if attachment_url:
                    if str((reply_media or {}).get("type") or "image") == "image":
                        try:
                            state_registry.increment_photo_used_count(int(reply_media["id"]))
                        except (KeyError, TypeError, ValueError):
                            pass
                    state_registry.dm_store_message(
                        conversation_id=conversation_id,
                        channel=channel,
                        role="system",
                        text=f"Attachment sent: {reply_media.get('caption') or reply_media.get('filename')}",
                    )
                state_registry.inbound_processing_update(
                    message_id, "replied", reason="provider_send_ok")
            else:
                state_registry.inbound_processing_update(
                    message_id, "ignored", reason="no_reply_returned")
    except Exception as e:
        try:
            if "message_id" in locals():
                state_registry.inbound_processing_update(
                    message_id, "processing_failed",
                    reason="exception", error=str(e))
        except Exception:
            pass
        log("webhook_process_error", source="zernio", error=str(e))


@app.api_route("/health", methods=["GET", "HEAD"])
async def health():
    """Health check for monitoring. Supports HEAD for UptimeRobot."""
    return {"status": "ok"}
