# wtyj/agents/social/senders/zernio.py
# Brief 187 — Sender adapter wrapping zernio_dm_client.send_dm_reply.
# All Zernio-routed channels (WhatsApp via Zernio, Instagram DM, Facebook DM,
# X/Twitter DM) use the same Zernio Inbox API endpoint, so a single class
# covers all four registry entries.
from .base import Sender
from agents.social.zernio_dm_client import send_dm_reply


class ZernioSender(Sender):
    """Sends replies via Zernio's Inbox API (covers all Zernio-routed channels)."""

    @classmethod
    def send(cls, conversation_id: str, account_id: str, text: str,
             attachment_url: str = "", attachment_type: str = "image",
             confirm_delivery: bool = False,
             idempotency_key: str = "", attachment_name: str = "") -> bool:
        # Brief 238 — tenant isolation: refuse outbound sends to accounts
        # not allowlisted in this tenant's client.json. Strict mode blocks
        # the call entirely; permissive mode logs and proceeds.
        from shared.tenant_guard import is_account_allowed
        if not is_account_allowed(account_id, direction="outbound"):
            return False
        if attachment_type == "mermaid_document_language":
            from agents.social.mermaid_document_language import send_picker
            return send_picker(conversation_id, account_id, attachment_url)
        if attachment_type == "mermaid_date_confirmation":
            from agents.social.mermaid_date_changes import send_confirmation
            return send_confirmation(conversation_id, account_id, attachment_url)
        if attachment_url and attachment_type == "file":
            from agents.social.mermaid_document_cards import try_send
            card_result = try_send(conversation_id, account_id, text, attachment_url, idempotency_key)
            if card_result is not None:
                return card_result
        kwargs = {
            "attachment_url": attachment_url,
            "attachment_type": attachment_type,
        }
        if confirm_delivery or idempotency_key:
            kwargs["confirm_delivery"] = confirm_delivery
            kwargs["idempotency_key"] = idempotency_key
        if attachment_name:
            kwargs["attachment_name"] = attachment_name
        return send_dm_reply(conversation_id, account_id, text, **kwargs)
