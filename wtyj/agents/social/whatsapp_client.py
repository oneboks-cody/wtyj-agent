from shared.mermaid_maintenance import participating as _maintenance_participating
# bluemarlin/agents/social/whatsapp_client.py
# Created: Brief 068
# Last modified: Brief 068
# Purpose: Parse inbound WhatsApp payloads + send outbound replies via Cloud API

import json
import os
import time
import urllib.parse
import urllib.request

from shared.bm_logger import log

_API_VERSION = "v22.0"


# Brief 154 — read env vars at call time, not at import time. Same lazy pattern
# Brief 147 used for gws_calendar.py to fix the test_068 import-order bug.
def _access_token() -> str:
    return os.environ.get("WHATSAPP_ACCESS_TOKEN", "")


def _phone_number_id() -> str:
    return os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "")


def parse_webhook_payload(payload: dict) -> list:
    """
    Extract normalized message objects from a Meta webhook payload.
    Returns a list of dicts. Skips status updates and non-message events.
    Non-text messages are included with text=None.
    """
    messages = []
    try:
        for entry in payload.get("entry", []):
            business_account_id = entry.get("id", "")
            for change in entry.get("changes", []):
                value = change.get("value", {})
                # Skip status updates (delivered, read, etc.)
                if "statuses" in value and "messages" not in value:
                    log("webhook_status_update", source="meta_whatsapp",
                        statuses=value.get("statuses"))
                    continue
                metadata = value.get("metadata", {})
                phone_number_id = metadata.get("phone_number_id", "")
                contacts = {c.get("wa_id", ""): c.get("profile", {}).get("name", "")
                            for c in value.get("contacts", [])}
                for msg in value.get("messages", []):
                    sender = msg.get("from", "")
                    normalized = {
                        "platform": "whatsapp",
                        "channel": "whatsapp",
                        "from": sender,
                        "from_name": contacts.get(sender, ""),
                        "message_id": msg.get("id", ""),
                        "text": msg.get("text", {}).get("body") if msg.get("type") == "text" else None,
                        "message_type": msg.get("type", "unknown"),
                        "timestamp": msg.get("timestamp", ""),
                        "business_account_id": business_account_id,
                        "phone_number_id": phone_number_id,
                    }
                    messages.append(normalized)
    except Exception as e:
        log("webhook_parse_error", source="meta_whatsapp", error=str(e))
    return messages


@_maintenance_participating('transport')
def send_text_message(to: str, text: str) -> bool:
    """Send a text message via WhatsApp Cloud API. Returns True on success."""
    url = f"https://graph.facebook.com/{_API_VERSION}/{_phone_number_id()}/messages"
    headers = {
        "Authorization": f"Bearer {_access_token()}",
        "Content-Type": "application/json",
    }
    body = json.dumps({
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text},
    }).encode()
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp_body = resp.read().decode()
            log("whatsapp_send_ok", to=to, response=resp_body)
            return True
    except urllib.error.HTTPError as e:
        error_body = e.read().decode() if e.fp else str(e)
        log("whatsapp_send_failed", to=to, status=e.code, error=error_body)
        return False
    except Exception as e:
        log("whatsapp_send_failed", to=to, error=str(e))
        return False


def _is_zernio_conversation_id(s: str) -> bool:
    """Zernio conversation IDs are 24-char lowercase hex strings.
    Meta phone numbers are E.164 or all-digit (10-15 chars).
    The two formats don't overlap. Brief 159."""
    if len(s) != 24:
        return False
    try:
        int(s, 16)
        return True
    except ValueError:
        return False


# Brief 173: cache conversation_id → account_id for Zernio social DMs.
# Populated on first successful send. Cleared only on process restart.
_zernio_account_cache: dict = {}


def _candidate_zernio_account_ids(social_publisher) -> list[str]:
    """Return outbound Zernio account candidates in safe priority order."""
    candidates: list[str] = []

    try:
        from shared import config_loader
        allowlist = (
            (config_loader.get_raw().get("channel_account_allowlist") or {})
            .get("zernio_accounts") or []
        )
    except Exception:
        allowlist = []

    for account_id in allowlist:
        if account_id and account_id not in candidates:
            candidates.append(account_id)

    for platform in ("whatsapp", "facebook", "instagram", "twitter"):
        account_id = social_publisher.get_account_id(platform)
        if account_id and account_id not in candidates:
            candidates.append(account_id)

    return candidates


# Conversation identity cache used to backfill callback follow-ups created
# before the Zernio webhook adapter preserved participantId.
_zernio_contact_cache: dict[str, dict] = {}
_zernio_contact_attempted_at: dict[str, float] = {}


def resolve_zernio_conversation_contacts(conversation_ids: list[str]) -> dict[str, dict]:
    """Resolve conversation ids to Zernio participant phone/name metadata.

    Results are tenant-isolated by the configured outbound account allowlist.
    A short negative cache prevents the dashboard's 10-second poll from
    repeatedly calling Zernio for conversations that cannot be resolved.
    """
    wanted = {
        str(value or "").strip()
        for value in conversation_ids or []
        if _is_zernio_conversation_id(str(value or "").strip())
    }
    if not wanted:
        return {}

    from agents.social import social_publisher
    from shared.tenant_guard import is_account_allowed

    def account_allowed(account_id: str) -> bool:
        raw_account_id = str(account_id or "")
        account_id = raw_account_id.strip()
        if not account_id or raw_account_id != account_id:
            return False
        try:
            return is_account_allowed(account_id, direction="outbound") is True
        except Exception as exc:
            log(
                "zernio_contact_resolution_account_control_unavailable",
                error=type(exc).__name__,
            )
            return False

    def current_contacts(contacts: dict[str, dict]) -> dict[str, dict]:
        current = {}
        for conversation_id, contact in contacts.items():
            if account_allowed(contact.get("account_id")):
                current[conversation_id] = contact
            else:
                _zernio_contact_cache.pop(conversation_id, None)
                _zernio_contact_attempted_at.pop(conversation_id, None)
        return current

    now = time.monotonic()
    resolved = {}
    for conversation_id in wanted:
        cached = _zernio_contact_cache.get(conversation_id)
        cached_account_id = (
            str(cached.get("account_id") or "")
            if isinstance(cached, dict) else ""
        )
        if cached and account_allowed(cached_account_id):
            resolved[conversation_id] = cached
        elif cached is not None:
            _zernio_contact_cache.pop(conversation_id, None)
            _zernio_contact_attempted_at.pop(conversation_id, None)
    unresolved = {
        conversation_id for conversation_id in wanted
        if conversation_id not in resolved
        and (
            conversation_id not in _zernio_contact_attempted_at
            or now - _zernio_contact_attempted_at[conversation_id] >= 300
        )
    }
    if not unresolved:
        return current_contacts(resolved)

    api_key = os.environ.get("LATE_API_KEY", "")
    if not api_key:
        return current_contacts(resolved)

    for candidate_account_id in _candidate_zernio_account_ids(social_publisher):
        account_id = str(candidate_account_id or "")
        if not unresolved or not account_allowed(account_id):
            continue
        cursor = ""
        for _page in range(5):
            if not account_allowed(account_id):
                break
            params = {"accountId": account_id, "limit": "100", "sortOrder": "desc"}
            if cursor:
                params["cursor"] = cursor
            url = (
                "https://zernio.com/api/v1/inbox/conversations?"
                + urllib.parse.urlencode(params)
            )
            req = urllib.request.Request(
                url,
                headers={"Authorization": f"Bearer {api_key}"},
                method="GET",
            )
            try:
                with urllib.request.urlopen(req, timeout=8) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            except Exception as exc:
                log(
                    "zernio_contact_resolution_failed",
                    account_id=account_id[:20],
                    error=str(exc)[:200],
                )
                break
            if not account_allowed(account_id):
                break

            rows = payload.get("data", []) if isinstance(payload, dict) else []
            for row in rows if isinstance(rows, list) else []:
                if not isinstance(row, dict):
                    continue
                conversation_id = str(row.get("id") or "").strip()
                if conversation_id not in unresolved:
                    continue
                row_account_id = str(row.get("accountId") or "")
                if row_account_id != account_id or not account_allowed(account_id):
                    continue
                contact = {
                    "phone": str(row.get("participantId") or "").strip(),
                    "name": str(row.get("participantName") or "").strip(),
                    "account_id": account_id,
                }
                _zernio_contact_cache[conversation_id] = contact
                resolved[conversation_id] = contact
                unresolved.discard(conversation_id)

            pagination = payload.get("pagination", {}) if isinstance(payload, dict) else {}
            cursor = str(pagination.get("nextCursor") or "").strip()
            if not cursor or not pagination.get("hasMore") or not unresolved:
                break

    for conversation_id in unresolved:
        _zernio_contact_attempted_at[conversation_id] = now
    return current_contacts(resolved)


@_maintenance_participating('transport')
def send_whatsapp_message(customer_id: str, text: str,
                          attachment_url: str = "",
                          attachment_type: str = "image",
                          confirm_delivery: bool = False,
                          idempotency_key: str = "") -> bool:
    """Send a DM via Zernio Inbox API if customer_id is a Zernio conversation_id,
    otherwise fall back to the legacy Meta WhatsApp Cloud API. Returns True on success.

    Brief 173: Zernio conversation_ids are scoped to accounts (whatsapp / facebook /
    instagram / twitter), so we can't assume a conversation belongs to the WhatsApp
    account. Try the cached account for this conversation first (if known), then
    iterate through all active social accounts until one accepts the send. Caches
    the winning account so repeat sends bypass the fan-out on the fast path.
    """
    if not _is_zernio_conversation_id(customer_id):
        if confirm_delivery or idempotency_key:
            # The legacy Meta helper only observes HTTP acceptance and has no
            # idempotent/confirmed-delivery contract. Do not silently downgrade
            # a durable operator action to an unverified send.
            log("whatsapp_confirmed_legacy_meta_unsupported", to=customer_id[:20])
            return False
        if attachment_url:
            log("whatsapp_attachment_legacy_meta_unsupported",
                to=customer_id[:20],
                attachment_type=attachment_type)
            return False
        return send_text_message(to=customer_id, text=text)

    # Deferred imports to avoid circular dependency with social_publisher
    from agents.social.zernio_dm_client import send_dm_reply
    from agents.social import social_publisher
    from shared.tenant_guard import is_account_allowed

    # Fast path: cache hit
    cached = _zernio_account_cache.get(customer_id)
    attempted_accounts = set()
    if cached:
        if not is_account_allowed(cached, direction="outbound"):
            _zernio_account_cache.pop(customer_id, None)
        else:
            attempted_accounts.add(cached)
            if _send_zernio_candidate(send_dm_reply, customer_id, cached, text,
                                      attachment_url, attachment_type,
                                      confirm_delivery, idempotency_key):
                return True
            # Cache miss (account may have been reconnected with a new id) — fall through
            _zernio_account_cache.pop(customer_id, None)

    # Cold path: try tenant allowlisted accounts first. This is important for
    # strict tenants where Late's generic active account lookup may return a
    # different account than the one that received the inbound conversation.
    for account_id in _candidate_zernio_account_ids(social_publisher):
        if account_id in attempted_accounts:
            continue
        if not is_account_allowed(account_id, direction="outbound"):
            continue
        attempted_accounts.add(account_id)
        if _send_zernio_candidate(send_dm_reply, customer_id, account_id, text,
                                  attachment_url, attachment_type,
                                  confirm_delivery, idempotency_key):
            _zernio_account_cache[customer_id] = account_id
            log("zernio_send_platform_resolved",
                conversation_id=customer_id[:20],
                account_id=account_id[:20])
            return True

    log("zernio_send_all_platforms_failed", conversation_id=customer_id[:20])
    return False


@_maintenance_participating('transport')
def send_whatsapp_template_message(
    customer_id: str,
    template_name: str,
    language: str = "es",
) -> bool:
    """Send an approved WhatsApp template through the tenant-owned account."""
    if not _is_zernio_conversation_id(customer_id):
        return False

    from agents.social.zernio_dm_client import send_dm_template
    from agents.social import social_publisher
    from shared.tenant_guard import is_account_allowed

    cached = _zernio_account_cache.get(customer_id)
    candidates = []
    if cached:
        candidates.append(cached)
    for account_id in _candidate_zernio_account_ids(social_publisher):
        if account_id not in candidates:
            candidates.append(account_id)

    for account_id in candidates:
        if not is_account_allowed(account_id, direction="outbound"):
            continue
        if send_dm_template(
            customer_id,
            account_id,
            template_name,
            language=language,
        ):
            _zernio_account_cache[customer_id] = account_id
            return True
    return False


def _send_zernio_candidate(send_dm_reply, customer_id: str, account_id: str,
                           text: str, attachment_url: str,
                           attachment_type: str,
                           confirm_delivery: bool = False,
                           idempotency_key: str = "") -> bool:
    if attachment_url:
        kwargs = {
            "attachment_url": attachment_url,
            "attachment_type": attachment_type,
        }
        if confirm_delivery or idempotency_key:
            kwargs["confirm_delivery"] = True
        if idempotency_key:
            kwargs["idempotency_key"] = idempotency_key
        return send_dm_reply(customer_id, account_id, text,
                             **kwargs)
    if confirm_delivery or idempotency_key:
        kwargs = {"confirm_delivery": True}
        if idempotency_key:
            kwargs["idempotency_key"] = idempotency_key
        return send_dm_reply(
            customer_id,
            account_id,
            text,
            **kwargs,
        )
    return send_dm_reply(customer_id, account_id, text)
