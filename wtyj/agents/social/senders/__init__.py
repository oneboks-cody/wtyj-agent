from shared.mermaid_maintenance import participating as _maintenance_participating
# wtyj/agents/social/senders/__init__.py
# Brief 187 — Sender registry + dispatcher.
from .base import Sender
from .zernio import ZernioSender

# Maps channel name (matches parse_zernio_webhook output's "channel" field
# and the ZERNIO_CHANNELS registry from Brief 186) to a Sender class.
# All four Zernio-routed channels share ZernioSender because they all use
# the same Zernio Inbox API to deliver replies.
SENDERS: dict[str, type[Sender]] = {
    "whatsapp": ZernioSender,
    "instagram_dm": ZernioSender,
    "facebook_dm": ZernioSender,
    "twitter_dm": ZernioSender,
}

# Default sender for unknown channels (mirrors DEFAULT_ZERNIO_CHANNEL from
# the parser registry — preserves "process anything Zernio gives us" behavior).
DEFAULT_SENDER: type[Sender] = ZernioSender


@_maintenance_participating('transport')
def send_reply(channel: str, conversation_id: str, account_id: str, text: str,
               attachment_url: str = "", attachment_type: str = "image",
               confirm_delivery: bool = False,
               idempotency_key: str = "", attachment_name: str = "") -> bool:
    """Dispatch a reply to the right sender based on channel name.

    This is the single public entry point for sending outbound replies. Call
    sites should use this instead of calling channel-specific sender functions
    (like send_dm_reply) directly, so the registry stays the source of truth
    for which transport handles which channel.
    """
    if channel == "whatsapp":
        from agents.social.isluno_transition import blocked
        if blocked() and attachment_type not in {"isluno_discovery", "isluno_quote", "isluno_fulfillment"}:
            return False
    sender_cls = SENDERS.get(channel, DEFAULT_SENDER)
    name_kwargs = {"attachment_name": attachment_name} if attachment_name else {}
    return sender_cls.send(conversation_id, account_id, text,
                           attachment_url=attachment_url,
                           attachment_type=attachment_type,
                           confirm_delivery=confirm_delivery,
                           idempotency_key=idempotency_key, **name_kwargs)


__all__ = ["Sender", "ZernioSender", "SENDERS", "DEFAULT_SENDER", "send_reply"]
