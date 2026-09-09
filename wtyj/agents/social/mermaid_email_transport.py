"""Outbound-only Gmail transport for consented Mermaid reservation emails.

Credentials are deliberately separate from the general email adapter, whose
credentials also enable the inbound email agent. No mailbox access or network
request is performed by ``ready`` or ``sender_address``.
"""

from __future__ import annotations
from shared.mermaid_maintenance import participating as _maintenance_participating

import os
from pathlib import Path
import re
import smtplib
import ssl
from datetime import datetime, timezone
from email.errors import HeaderParseError
from email.headerregistry import Address
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import format_datetime

from shared import config_loader


_DEFAULT_PASSWORD_FILE = "/app/config/mermaid_email_password"
_SMTP_HOST = "smtp.gmail.com"
_SMTP_PORT = 587
_SMTP_TIMEOUT = 20


class EmailSendError(RuntimeError):
    """Safe delivery outcome: ``code`` never includes server text or secrets."""

    def __init__(self, code: str, *, uncertain: bool = False):
        safe_code = code if re.fullmatch(r"[a-z_]{1,48}", code) else "transport_failed"
        self.code = safe_code
        self.uncertain = bool(uncertain)
        super().__init__(safe_code)


def _header(value: object, *, required: bool = True) -> str:
    if not isinstance(value, str) or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise EmailSendError("invalid_header")
    if required and not value.strip():
        raise EmailSendError("invalid_header")
    return value.strip()


def _address(value: object) -> str:
    value = _header(value)
    try:
        parsed = Address(addr_spec=value)
        if not parsed.username or not parsed.domain or not parsed.username.isascii():
            raise ValueError("Invalid address")
        domain = parsed.domain.encode("idna").decode("ascii").lower()
        if len(domain) > 253 or "." not in domain or any(
            not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
            for label in domain.split(".")
        ):
            raise ValueError("Invalid domain")
        normalized = Address(username=parsed.username, domain=domain).addr_spec
        if len(normalized) > 254 or len(parsed.username.encode("ascii")) > 64:
            raise ValueError("Invalid length")
        return normalized
    except (ValueError, TypeError, UnicodeError, HeaderParseError):
        raise EmailSendError("invalid_address") from None


def sender_address() -> str:
    """Return the configured dedicated sender, or empty when invalid/missing."""
    try:
        return _address(os.environ.get("MERMAID_EMAIL_ADDRESS", ""))
    except EmailSendError:
        return ""


def _password() -> str:
    configured_file = os.environ.get("MERMAID_EMAIL_PASSWORD_FILE", "").strip()
    environment_password = os.environ.get("MERMAID_EMAIL_PASSWORD", "")
    try:
        if configured_file or not environment_password:
            with Path(configured_file or _DEFAULT_PASSWORD_FILE).open(encoding="utf-8") as handle:
                value = handle.read(4097)
        else:
            value = environment_password
        if len(value) > 4096:
            return ""
        # Google displays app passwords in space-separated groups. A trailing
        # newline from a protected credential file is normal.
        value = value.strip().replace(" ", "")
        if not value or any(ord(char) < 33 or ord(char) > 126 for char in value):
            return ""
        return value
    except (OSError, UnicodeError, ValueError):
        return ""


def ready() -> bool:
    """Check local sender/credential presence only; this does not authenticate."""
    return bool(sender_address() and _password())


def _display_name() -> str:
    raw = config_loader.get_raw() or {}
    business = config_loader.get_business() or {}
    agent = _header(business.get("agent_name") or raw.get("agent_name") or "Tracy")
    if agent.isupper():
        agent = agent.title()
    brand = _header(raw.get("name") or business.get("name") or "Mermaid Boat Trips")
    return f"{agent} | {brand}"


def _message(
    sender: str, recipient: str, content: dict,
    pdf_attachment: tuple[str, bytes], message_id: str,
) -> EmailMessage:
    message_id = _header(message_id)
    if not message_id.isascii() or not re.fullmatch(r"<[^<>\s@]+@[^<>\s@]+>", message_id):
        raise EmailSendError("invalid_message_id")
    if not isinstance(content, dict):
        raise EmailSendError("invalid_content")
    subject = _header(content.get("subject"))
    body = content.get("text")
    html = content.get("html")
    if not isinstance(body, str) or not body.strip() or not isinstance(html, str) or not html.strip():
        raise EmailSendError("invalid_content")
    if not isinstance(pdf_attachment, tuple) or len(pdf_attachment) != 2:
        raise EmailSendError("invalid_attachment")
    filename, pdf_bytes = pdf_attachment
    filename = _header(filename)
    if (
        "/" in filename or "\\" in filename
        or not filename.lower().endswith(".pdf")
        or not isinstance(pdf_bytes, bytes) or not pdf_bytes.startswith(b"%PDF-")
    ):
        raise EmailSendError("invalid_attachment")
    message = EmailMessage(policy=SMTP)
    parsed_sender = Address(addr_spec=sender)
    message["From"] = Address(
        display_name=_header(content["sender_display_name"]) if content.get("sender_display_name") else _display_name(), username=parsed_sender.username,
        domain=parsed_sender.domain,
    )
    message["To"] = recipient
    message["Subject"] = subject
    message["Message-ID"] = message_id
    message["Date"] = format_datetime(datetime.now(timezone.utc))
    message.set_content(body, cte="quoted-printable")
    message.add_alternative(html, subtype="html", cte="quoted-printable")
    message.add_attachment(pdf_bytes, maintype="application", subtype="pdf", filename=filename)
    return message


@_maintenance_participating('transport')
def send_email(
    recipient: str, content: dict, pdf_attachment: tuple[str, bytes],
    *, message_id: str,
) -> None:
    """Return only after SMTP accepts DATA; never retry an uncertain result here.

    A connection failure before DATA is safe to retry. A disconnect or timeout
    during DATA can mean Gmail accepted the message without our receiving the
    response, so the caller must retain an uncertain delivery record.
    """
    sender = sender_address()
    password = _password()
    if not sender or not password:
        raise EmailSendError("not_configured")
    recipient = _address(recipient)
    try:
        wire_message = _message(sender, recipient, content, pdf_attachment, message_id).as_bytes()
    except EmailSendError:
        raise
    except (ValueError, TypeError, UnicodeError):
        raise EmailSendError("invalid_content") from None

    client = None
    phase = "connect"
    accepted = False
    try:
        client = smtplib.SMTP(_SMTP_HOST, _SMTP_PORT, timeout=_SMTP_TIMEOUT)
        client.ehlo()
        client.starttls(context=ssl.create_default_context())
        client.ehlo()
        phase = "authenticate"
        client.login(sender, password)
        phase = "envelope"
        code, _ = client.mail(sender)
        if code != 250:
            raise EmailSendError("sender_rejected")
        code, _ = client.rcpt(recipient)
        if code not in (250, 251):
            raise EmailSendError("recipient_rejected")
        phase = "data"
        code, _ = client.data(wire_message)
        if code != 250:
            raise EmailSendError("message_rejected")
        accepted = True
    except EmailSendError:
        raise
    except smtplib.SMTPAuthenticationError:
        raise EmailSendError("authentication_rejected") from None
    except smtplib.SMTPRecipientsRefused:
        raise EmailSendError("recipient_rejected") from None
    except smtplib.SMTPSenderRefused:
        raise EmailSendError("sender_rejected") from None
    except smtplib.SMTPDataError:
        raise EmailSendError("message_rejected") from None
    except smtplib.SMTPResponseException:
        raise EmailSendError("smtp_rejected") from None
    except Exception:
        uncertain = phase == "data"
        raise EmailSendError(
            "delivery_uncertain" if uncertain else "connection_failed", uncertain=uncertain,
        ) from None
    finally:
        if client is not None:
            if accepted:
                # QUIT is connection cleanup. Losing its response does not undo
                # the successful DATA response and must not trigger a resend.
                try:
                    client.quit()
                except Exception:
                    pass
            try:
                client.close()
            except Exception:
                pass
