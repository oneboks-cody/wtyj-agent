"""Mermaid-only durable recovery; no provider sends or business mutations."""

import hashlib
import json
import os
import re
import sqlite3
import time
import unicodedata
from datetime import datetime, timezone

from shared import state_registry


MAX_ATTEMPTS = 3
TRANSIENT_DELAY_SECONDS = 5
OPERATOR_COOLDOWN_SECONDS = 900
GENERATION_LEASE_SECONDS = 90
LOCALES = ("en", "nl", "de", "es", "pap", "pt")
_MARINA_COMPATIBILITY_KEYS = {
    "intents",
    "clarifications_needed",
    "flags",
    "internal_note",
    "ali_vehicle_recommendation",
    "ali_rental_change",
    "ali_summary_action",
    "ali_primary_intent",
    "ali_lead_follow_up_action",
}

# Forms that were materially wrong in their recorded Mermaid reply context.
# This is deliberately not a dictionary of globally forbidden Papiamentu
# words: legitimate vocabulary belongs in the model's contextual guidance.
_PAPIAMENTU_BLOCKED_OUTPUT_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"(?<!\w)pickup(?!\w)",
        r"(?<!\w)berdat(?!\w)",
        r"(?<!\w)kombersashon(?!\w)",
        r"(?<!\w)período(?!\w)",
        r"(?<!\w)movilidat(?!\w)",
        r"(?<!\w)aworaki(?!\w)",
        r"(?<!\w)prepara(?!\w)",
        r"(?<!\w)adjuntá(?!\w)",
        r"(?<!\w)katálogo(?!\w)",
        r"(?<!\w)lansementu(?!\w)",
        r"(?<!\w)pet(?!\w)",
        r"(?<!\w)september(?!\w)",
        r"(?<!\w)zwemropa(?!\w)",
        r"(?<!\w)kacho(?!\w)",
        r"(?<!\w)pittu(?!\w)",
        r"(?<!\w)almuerzo(?!\w)",
        r"(?<!\w)almoerso(?!\w)",
        r"(?<!\w)almorso(?!\w)",
        r"(?<!\w)refresco(?!\w)",
        r"(?<!\w)djùs(?!\w)",
        r"(?<!\w)jugo(?!\w)",
        r"(?<!\w)wijn(?!\w)",
        r"(?<!\w)kèshi(?!\w)",
        r"(?<!\w)konfirmasion(?!\w)",
        r"(?<!\w)information(?!\w)",
        r"beach\s+house",
        r"roupa\s+di\s+bañu",
        r"blokmènt\s+di\s+solo",
        r"mi\s+number\s+tracy",
        r"ta\s+kòrtèkt",
        r"e\s+sistema\s+mester\s+baliá\s+primero",
    )
)

# High-signal written Curaçao Papiamentu forms and structures.  They are used
# only after the model or conversation has already selected Papiamentu.  This
# catches an obvious whole-language mismatch; it is not an input-language
# classifier and cannot establish grammatical or native-level quality.
_PAPIAMENTU_OUTPUT_STRUCTURE_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"(?<!\w)(?:aki|alohamentu|almuerso|asina|atrobe|beibi|biahe|bishitante(?:nan)?|"
        r"danki|datonan|desayuno|djus|drumi|esaki|esei|hende|hiba|inkluí|kashi|kiko|kon|"
        r"korekto|kòrsou|kuantu|kuminda|mester|nòmber|òf|pasobra|petishon|piká|prepará|"
        r"refresko|registrá|reservashon|risibí|sèn|skohe|sòru|tambe|tokante|"
        r"tripulashon|tur|unda|vino|wardá|yuda)(?!\w)",
        r"(?<!\w)(?:mi|bo|nos|boso|e|nan)\s+(?:ta|a|lo|por|tin|ke|mester)(?!\w)",
        r"(?<!\w)no\s+(?:ta|tin|por|a)(?!\w)",
    )
)

_PAPIAMENTU_SHORT_OUTPUTS = {
    "bon", "danki", "korekto", "klaro", "nò", "no", "sí", "si",
}

_PAPIAMENTU_CONTEXT_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(?:ta|tin|unda|aki|danki|inkluí)\b",
        r"\bkiko\b",
        r"\bpakiko\b",
        r"\bmi\s+ta\b",
        r"\bmi\s+(?:ke|kier|por|tin)\b",
        r"\bbo\s+(?:ta|por|tin)\b",
        r"\b(?:ki\s+ora|kon\s+ta|tur\s+kos|bon\s+bini)\b",
        r"\b(?:reservashon|bishitante|biahe|stul\s+di\s+rueda)\b",
    )
)

# Accepted outage-copy exception. Papiamentu uses the reviewed formal register.
FAILURE_COPY = {
    "en": "I couldn't answer that just now. Your details are saved. Please try again shortly, or ask to speak to a person.",
    "nl": "Ik kon u net niet antwoorden. Uw gegevens zijn opgeslagen. Probeer het zo nog eens of vraag om een medewerker.",
    "de": "Ich konnte gerade nicht antworten. Ihre Angaben sind gespeichert. Versuchen Sie es gleich noch einmal oder bitten Sie um einen Mitarbeiter.",
    "es": "No pude responderle en este momento. Sus datos están guardados. Inténtelo de nuevo en un momento o pida hablar con una persona.",
    "pap": "Mi no a logra kontestá bo na e momento aki. Bo datonan ta wardá. Purba atrobe den un ratu òf pidi pa papia ku un hende di e tim.",
    "pt": "Não consegui responder agora. Seus dados estão salvos. Tente novamente em instantes ou peça para falar com uma pessoa.",
}
HUMAN_COPY = {
    "en": "Your request is queued for Mermaid's team. Your details are saved, and I can still help with general trip questions.",
    "nl": "Uw verzoek staat klaar voor het team van Mermaid. Uw gegevens zijn opgeslagen en ik kan algemene vragen over de trip blijven beantwoorden.",
    "de": "Ihre Anfrage wartet auf die Prüfung durch das Mermaid-Team. Ihre Angaben sind gespeichert und ich kann weiterhin allgemeine Fragen zum Ausflug beantworten.",
    "es": "Su solicitud está en espera de revisión por el equipo de Mermaid. Sus datos están guardados y puedo seguir respondiendo preguntas generales sobre la excursión.",
    "pap": "Bo petishon ta warda pa e tim di Mermaid revisá. Bo datonan ta wardá i mi por sigui yuda ku preguntanan general tokante e biahe.",
    "pt": "Seu pedido está aguardando análise da equipe da Mermaid. Seus dados estão salvos e posso continuar respondendo a perguntas gerais sobre o passeio.",
}

# Issue #342 explicitly authorizes this narrow offline human-request route.
# Whole-message requests only; ordinary mentions of people do not match.
_HUMAN_REQUESTS = {
    "en": r"(?:please )?(?:(?:i (?:want|need|would like) to|can i|could i) (?:speak|talk|chat) (?:to|with) (?:a |the )?(?:real person|human|person|staff member|team|mermaid team)|i (?:want|need|would like) (?:a |the )?(?:human|real person|staff member)|(?:speak|talk) (?:to|with) (?:a |the )?(?:human|real person|team|mermaid team)|would it be possible to (?:speak|talk|chat) (?:to|with) (?:someone from )?(?:the |mermaid )?team|could (?:someone|a person) from (?:the |mermaid )?team (?:call|contact) me)(?: please)?",
    "nl": r"(?:ik wil|ik wil graag|mag ik|kan ik) (?:met )?(?:een |de )?(?:echte )?(?:medewerker|persoon|mens|iemand van het team) (?:spreken|praten)(?: alstublieft| alsjeblieft)?",
    "de": r"(?:ich mochte|ich will|kann ich|ich wurde gern) (?:mit )?(?:einem |einer |dem )?(?:echten )?(?:mitarbeiter|menschen|person|team) (?:sprechen|reden)(?: bitte)?",
    "es": r"(?:quiero|quisiera|puedo) (?:hablar|conversar) con (?:una |un |el )?(?:persona(?: de verdad| real)?|humano|agente|equipo)(?: por favor)?",
    "pap": r"(?:mi ke|mi kier|mi ta desea|mi por) (?:papia|habla) ku (?:un |e )?(?:hende(?: di e tim| di e team| berdadero| real)?|persona|tim)(?: por fabor)?",
    "pt": r"(?:quero|gostaria de|posso) (?:falar|conversar) com (?:uma |um |a )?(?:pessoa(?: de verdade| real)?|humano|atendente|equipe)(?: por favor)?",
}

_EMBEDDED_HUMAN_REQUESTS = {
    "en": r"\b(?:(?:please )?let me (?:speak|talk|chat) (?:to|with)|(?:please )?(?:connect|put) me (?:through )?(?:to|with)|i (?:want|need|would like) to (?:speak|talk|chat) (?:to|with)) (?:someone|a person|a human|a real person|staff|the team|mermaid team)\b|\bwould it be possible to (?:speak|talk|chat) (?:to|with) (?:someone from )?(?:the |mermaid )?team\b|\bcould (?:someone|a person) from (?:the |mermaid )?team (?:call|contact) me\b",
    "nl": r"\b(?:laat me (?:met )?(?:iemand|een persoon|een medewerker) (?:spreken|praten)|verbind me met (?:iemand|een persoon|een medewerker|het team)|ik wil (?:met )?(?:iemand|een persoon|een medewerker) (?:spreken|praten))\b",
    "de": r"\b(?:lassen sie mich mit (?:jemandem|einer person|einem mitarbeiter) (?:sprechen|reden)|verbinden sie mich mit (?:jemandem|einer person|einem mitarbeiter|dem team)|ich will mit (?:jemandem|einer person|einem mitarbeiter) (?:sprechen|reden))\b",
    "es": r"\b(?:dejame hablar con (?:alguien|una persona|un agente)|conectame con (?:alguien|una persona|un agente|el equipo)|quiero hablar con (?:alguien|una persona|un agente|el equipo))\b",
    "pap": r"\b(?:laga mi papia ku (?:un hende|un persona|e tim)|konekta mi ku (?:un hende|un persona|e tim)|pasa mi pa (?:un hende|un persona|e tim)|mi ke papia ku (?:un hende|un persona|e tim))\b",
    "pt": r"\b(?:deixe me falar com (?:alguem|uma pessoa|um atendente)|conecte me com (?:alguem|uma pessoa|um atendente|a equipe)|quero falar com (?:alguem|uma pessoa|um atendente|a equipe))\b",
}

_NEGATED_HUMAN_REQUESTS = (
    r"\b(?:i do not|i don t|i dont) (?:want|need) to (?:speak|talk|chat)\b",
    r"\bbut i do not\b", r"\bsaid i (?:want|need) to (?:speak|talk)\b",
    r"\bik wil niet\b", r"\bniet (?:spreken|praten)\b",
    r"\bich will nicht\b", r"\bnicht (?:sprechen|reden)\b",
    r"\bno (?:quiero|deseo|necesito) hablar\b",
    r"\bmi no (?:ke|kier) papia\b",
    r"\bnao (?:quero|preciso) falar\b",
)


def _normalized_request_text(text: str) -> str:
    normalized = "".join(c for c in unicodedata.normalize("NFKD", str(text).casefold()) if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^\w\s]", " ", normalized).split())


def explicit_human_request(text: str) -> str | None:
    normalized = _normalized_request_text(text)
    return next((locale for locale, pattern in _HUMAN_REQUESTS.items() if re.fullmatch(pattern, normalized)), None)


def contains_explicit_human_request(text: str) -> str | None:
    """Detect a person request embedded beside other customer details."""
    normalized = _normalized_request_text(text)
    # A reported quote followed by a denial is not the guest's request.
    if re.search(r"\bsaid i (?:want|need) to (?:speak|talk)\b", normalized) and re.search(
        r"\bbut i do not\b", normalized
    ):
        return None
    # Preserve sentence boundaries before normalization. This catches a polite
    # request placed either before or after a wheelchair detail while keeping
    # every whole-message grammar strict inside its own clause.
    clauses = re.split(
        r"[.!?;]+|\b(?:but|pero|maar|aber|mas)\b",
        str(text or ""),
        flags=re.IGNORECASE,
    )
    for clause in clauses:
        clause_normalized = _normalized_request_text(clause)
        if any(
            re.search(pattern, clause_normalized)
            for pattern in _NEGATED_HUMAN_REQUESTS
        ):
            continue
        locale = explicit_human_request(clause)
        if locale:
            return locale
        embedded = next(
            (
                locale for locale, pattern in _EMBEDDED_HUMAN_REQUESTS.items()
                if re.search(pattern, clause_normalized)
            ),
            None,
        )
        if embedded:
            return embedded
    # The six whole-message grammars also cover polite forms such as "Could I"
    # and "Mi por".  Accept them at the end of a mixed-detail message.  The
    # end anchor prevents noun phrases such as "I need a human-readable PDF"
    # from being truncated into a person request.
    trailing = next(
        (
            locale for locale, pattern in _HUMAN_REQUESTS.items()
            if re.search(r"(?:^|\s)(?:" + pattern + r")$", normalized)
        ),
        None,
    )
    if trailing:
        return trailing
    if any(re.search(pattern, normalized) for pattern in _NEGATED_HUMAN_REQUESTS):
        return None
    return next(
        (
            locale for locale, pattern in _EMBEDDED_HUMAN_REQUESTS.items()
            if re.search(pattern, normalized)
        ),
        None,
    )


def error_metadata(exc: Exception | None = None) -> dict:
    status = getattr(exc, "status_code", None)
    body = getattr(exc, "body", None)
    error = body.get("error", body) if isinstance(body, dict) else {}
    kind = str(error.get("type") or "") if isinstance(error, dict) else ""
    message = str(error.get("message") or "").casefold() if isinstance(error, dict) else ""
    if kind in {"billing_error", "insufficient_quota"} or any(value in message for value in ("credit balance", "billing", "insufficient quota", "quota exhausted")):
        return {"kind": "billing", "retryable": False}
    if status in {401, 403} or kind in {"authentication_error", "permission_error"}:
        return {"kind": "credentials", "retryable": False}
    if isinstance(status, int) and 400 <= status < 500 and status not in {408, 409, 429}:
        return {"kind": "request_rejected", "retryable": False}
    return {"kind": "transient" if exc is not None else "invalid_response", "retryable": True}


def _conn():
    conn = state_registry._get_conn()
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS mermaid_model_events (
            conversation_id TEXT NOT NULL, message_id TEXT NOT NULL,
            status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
            error_kind TEXT NOT NULL DEFAULT '', retryable INTEGER NOT NULL DEFAULT 1,
            retry_at REAL NOT NULL DEFAULT 0, notice_sent INTEGER NOT NULL DEFAULT 0,
            response_json TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL,
            PRIMARY KEY (conversation_id, message_id)
        );
        CREATE TABLE IF NOT EXISTS mermaid_model_circuit (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1),
            blocked_until REAL NOT NULL, error_kind TEXT NOT NULL,
            credential_hash TEXT NOT NULL, probe_id TEXT NOT NULL DEFAULT '',
            failed_at REAL NOT NULL
        );
    """)
    return conn


def _metadata(row, *, retry_at=None, kind=None, retryable=None) -> dict:
    can_retry = bool(row["retryable"]) if retryable is None else retryable
    return {
        "conversation_id": row["conversation_id"], "message_id": row["message_id"],
        "kind": kind or row["error_kind"],
        "retryable": can_retry,
        "retry_at": row["retry_at"] if retry_at is None else retry_at,
        # A recoverable attempt is internal work, not a failed conversation.
        # The durable worker retries silently; notify once only at exhaustion
        # or when the provider requires an operator's intervention.
        "send_notice": not can_retry and not row["notice_sent"] and row["status"] not in {"generating", "superseded"},
        "superseded": row["status"] == "superseded",
    }


def _failure(locale, metadata):
    return {
        "generation_failed": True, "generation_failure": metadata,
        "language": locale,
        "reply": FAILURE_COPY[locale] if metadata["send_notice"] else "",
    }


def _valid_schema_value(value, schema, *, allow_metadata=False):
    """Validate supplied contract values; legacy omitted fields stay optional."""
    value_type = schema.get("type")
    if value_type == "object":
        if not isinstance(value, dict):
            return False
        properties = schema.get("properties", {})
        for key, item in value.items():
            if key in properties:
                if not _valid_schema_value(item, properties[key]):
                    return False
            elif schema.get("additionalProperties") is False and not allow_metadata:
                return False
    elif value_type == "string":
        if not isinstance(value, str):
            return False
    elif value_type == "boolean":
        if type(value) is not bool:
            return False
    elif value_type == "array":
        if not isinstance(value, list):
            return False
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            return False
        item_schema = schema.get("items")
        if item_schema and any(not _valid_schema_value(item, item_schema) for item in value):
            return False
    elif value_type == "integer":
        if type(value) is not int:
            return False
        if "minimum" in schema and value < schema["minimum"]:
            return False
        if "maximum" in schema and value > schema["maximum"]:
            return False
    else:
        return False
    return "enum" not in schema or value in schema["enum"]


def _normalize_nfc(value):
    """Return a copy with every model-supplied string in canonical NFC."""
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, dict):
        return {key: _normalize_nfc(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize_nfc(item) for item in value]
    return value


def _normalize_generated_result(value):
    result = _normalize_nfc(value)
    if isinstance(result, dict) and result.get("language") == "pap":
        # The approved facility name is already used in Mermaid's PAP FAQ.
        # Repair this exact, unambiguous English phrase before the strict
        # register check, rather than discard an otherwise valid answer.
        for key in ("reply", "other_question_reply"):
            if isinstance(result.get(key), str):
                result[key] = re.sub(r"(?<!\w)beach\s+house(?!\w)", "kas di playa", result[key], flags=re.IGNORECASE)
    return result


def _has_disallowed_format_control(text: str) -> bool:
    """Reject invisible controls while retaining ordinary joined emoji."""
    for index, character in enumerate(text):
        if unicodedata.category(character) != "Cf":
            continue
        if character in {"\u200c", "\u200d"}:
            before = text[index - 1] if index else ""
            after = text[index + 1] if index + 1 < len(text) else ""
            if (
                before
                and after
                and unicodedata.category(before).startswith("S")
                and unicodedata.category(after).startswith("S")
            ):
                continue
        return True
    return False


def _has_minimum_papiamentu_structure(text: str) -> bool:
    """Detect an obvious whole-language mismatch in a PAP-designated reply."""
    words = re.findall(r"[^\W\d_]+", text.casefold(), flags=re.UNICODE)
    if not words:
        return True
    if len(words) <= 3 and any(word in _PAPIAMENTU_SHORT_OUTPUTS for word in words):
        return True
    return any(pattern.search(text) for pattern in _PAPIAMENTU_OUTPUT_STRUCTURE_PATTERNS)


def _validation_error(result, guest_text="", expected_locale=""):
    from agents.social.mermaid_understanding import MERMAID_TOOL, has_server_owned_reply

    properties = MERMAID_TOOL["input_schema"]["properties"]
    formal_papiamentu = True
    if isinstance(result, dict):
        # Validate only model text that can reach the guest. Server-owned
        # selectors replace raw `reply` with deterministic copy, while the
        # separate FAQ field can still be appended and must always pass the
        # language/register gate. Schema validation below still covers the
        # entire result, including discarded raw prose.
        server_replaces_reply = has_server_owned_reply(result, guest_text)
        customer_text = str(result.get("other_question_reply") or "")
        if not server_replaces_reply:
            customer_text += "\n" + str(result.get("reply") or "")
        customer_text = unicodedata.normalize("NFC", customer_text)
        language_names = {
            "en": "english|engels|englisch|inglés|ingles",
            "nl": "dutch|nederlands|neerlandés|hulandes|holandés|holandês",
            "de": "german|deutsch|duits|alemán|aleman|alemão",
            "es": "spanish|español|spaans|spanisch|spañó",
            "pt": "portuguese|português|portugees|portugiesisch|portugés",
        }
        result_locale = result.get("chat_language") or result.get("language")
        target = language_names.get(result_locale) if isinstance(result_locale, str) else None
        explicit_switch = bool(target and re.search(
            r"(?:\b(?:in|na|en|auf|em|to)\s+(?:" + target + r")\b|\b(?:" + target + r")\s+(?:please|por fabor|por favor|alstublieft|bitte)\b)",
            str(guest_text or ""), re.IGNORECASE,
        ))
        papiamentu_context = (
            result_locale == "pap"
            or (not result.get("chat_language") and (
                (expected_locale == "pap" and not explicit_switch)
                or any(
                    pattern.search(text)
                    for text in (("" if explicit_switch else str(guest_text or "")), customer_text)
                    for pattern in _PAPIAMENTU_CONTEXT_PATTERNS
                )
            ))
        )
        if papiamentu_context:
            formal_papiamentu = (
                not _has_disallowed_format_control(customer_text)
                and not any(
                    pattern.search(customer_text)
                    for pattern in _PAPIAMENTU_BLOCKED_OUTPUT_PATTERNS
                )
                and _has_minimum_papiamentu_structure(customer_text)
            )
    if not isinstance(result, dict):
        return "response_not_object"
    if result.get("generation_failed"):
        return "provider_failure"
    if not isinstance(result.get("reply"), str):
        return "reply_not_string"
    if not isinstance(result.get("mermaid_action"), str):
        return "action_not_string"
    if not set(result).issubset(set(properties) | _MARINA_COMPATIBILITY_KEYS):
        return "unknown_response_field"
    for key, schema in properties.items():
        if key in result and not _valid_schema_value(result[key], schema):
            # Only fixed schema keys, never guest text or model values, reach logs.
            return "schema_" + key
    if not formal_papiamentu:
        return "papiamentu_register"
    if not result["reply"].strip() and not has_server_owned_reply(result, guest_text):
        return "empty_reply"
    return None


def _valid_result(result, guest_text="", expected_locale=""):
    return _validation_error(result, guest_text, expected_locale) is None


def _alert(conn, kind, now):
    timestamp = datetime.fromtimestamp(now, timezone.utc).isoformat()
    existing = conn.execute("SELECT id FROM pending_notifications WHERE notification_type='technical' AND customer_id='mermaid:model-provider' AND status IN ('pending','sent') ORDER BY id DESC LIMIT 1").fetchone()
    body = "TRACY could not complete model generation. Cause: " + kind + ". Saved guest details are preserved. Explicit human requests remain available."
    if existing:
        conn.execute("UPDATE pending_notifications SET body=? WHERE id=?", (body, existing[0]))
    else:
        conn.execute("INSERT INTO pending_notifications (notification_type,channel,customer_id,customer_name,subject,body,status,created_at) VALUES ('technical','system','mermaid:model-provider','TRACY','TRACY model service needs attention',?,'pending',?)", (body, timestamp))


def generate(message: dict, locale: str, call_model) -> dict:
    """One bounded attempt per invocation; the existing durable worker retries."""
    locale = locale if locale in LOCALES else "en"
    explicit = contains_explicit_human_request(message.get("text", ""))
    if explicit:
        return {"language": explicit, "understanding_source": "explicit_human_request", "mermaid_action": "request_human", "requires_human": True, "has_open_question": False, "fields": {}, "reply": HUMAN_COPY[explicit]}
    conversation = str(message.get("from") or "")
    message_id = str(message.get("message_id") or "")
    # Every production inbound has an ID. A direct caller without one receives
    # a request-local key rather than sharing another message's retry budget.
    if not message_id:
        message_id = "direct-" + os.urandom(12).hex()
    now = time.time()
    credential_hash = hashlib.sha256(os.environ.get("ANTHROPIC_API_KEY", "").encode()).hexdigest()
    conn = _conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("INSERT OR IGNORE INTO mermaid_model_events (conversation_id,message_id,status,created_at) VALUES (?,?,'new',?)", (conversation, message_id, now))
        row = conn.execute("SELECT * FROM mermaid_model_events WHERE conversation_id=? AND message_id=?", (conversation, message_id)).fetchone()
        if row["status"] == "generated":
            try:
                cached_raw = json.loads(row["response_json"])
                cached = _normalize_generated_result(cached_raw)
            except (TypeError, ValueError, json.JSONDecodeError):
                cached_raw = cached = None
            if _valid_result(
                cached,
                str(message.get("text") or ""),
                expected_locale=locale,
            ):
                if cached != cached_raw:
                    conn.execute(
                        "UPDATE mermaid_model_events SET response_json=? "
                        "WHERE conversation_id=? AND message_id=?",
                        (json.dumps(cached, ensure_ascii=False), conversation, message_id),
                    )
                conn.commit()
                return cached
            # A stored generation can predate the current output contract.
            # Re-enter the normal bounded path so it is replaced atomically;
            # never replay known-invalid customer text.
            conn.execute(
                "UPDATE mermaid_model_events SET status='new',attempts=0,"
                "error_kind='',retryable=1,retry_at=0,notice_sent=0,response_json='{}' "
                "WHERE conversation_id=? AND message_id=?",
                (conversation, message_id),
            )
            row = conn.execute(
                "SELECT * FROM mermaid_model_events "
                "WHERE conversation_id=? AND message_id=?",
                (conversation, message_id),
            ).fetchone()
        if row["status"] == "superseded" or row["attempts"] >= MAX_ATTEMPTS:
            conn.commit()
            return _failure(locale, _metadata(row, retryable=False))
        circuit = conn.execute("SELECT * FROM mermaid_model_circuit WHERE singleton=1").fetchone()
        if circuit and circuit["credential_hash"] != credential_hash:
            conn.execute("DELETE FROM mermaid_model_circuit")
            circuit = None
            if row["error_kind"] in {"credentials", "billing", "request_rejected"}:
                conn.execute("UPDATE mermaid_model_events SET retry_at=0 WHERE conversation_id=? AND message_id=?", (conversation, message_id))
                row = conn.execute("SELECT * FROM mermaid_model_events WHERE conversation_id=? AND message_id=?", (conversation, message_id)).fetchone()
        if row["retry_at"] > now or (circuit and circuit["blocked_until"] > now):
            retry_at = max(row["retry_at"], circuit["blocked_until"] if circuit else 0)
            kind = circuit["error_kind"] if circuit else row["error_kind"]
            retryable = kind not in {"billing", "credentials", "request_rejected"}
            conn.commit()
            return _failure(locale, _metadata(row, retry_at=retry_at, kind=kind, retryable=retryable))
        conn.execute("UPDATE mermaid_model_events SET status='generating',attempts=attempts+1,retry_at=? WHERE conversation_id=? AND message_id=?", (now + GENERATION_LEASE_SECONDS, conversation, message_id))
        attempt = row["attempts"] + 1
        probe_id = hashlib.sha256(f"{conversation}:{message_id}:{attempt}".encode()).hexdigest()
        if circuit:
            # During an outage allow one probe across concurrent conversations.
            conn.execute("UPDATE mermaid_model_circuit SET blocked_until=?,probe_id=? WHERE singleton=1", (now + GENERATION_LEASE_SECONDS, probe_id))
        conn.commit()
    finally:
        conn.close()
    try:
        result = _normalize_generated_result(call_model())
        rejection = _validation_error(
            result,
            str(message.get("text") or ""),
            expected_locale=locale,
        )
        if rejection:
            from shared.bm_logger import log
            log("mermaid_model_response_rejected", reason=rejection, attempt=attempt)
            failure = (result or {}).get("model_error") if isinstance(result, dict) else None
            failure = failure if isinstance(failure, dict) else error_metadata()
        else:
            failure = None
    except Exception as exc:
        failure = error_metadata(exc)
        result = {}
    conn = _conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM mermaid_model_events WHERE conversation_id=? AND message_id=?", (conversation, message_id)).fetchone()
        if row["status"] != "generating" or row["attempts"] != attempt:
            # A worker that outlived its lease cannot replace a newer result.
            metadata = _metadata(row, retryable=False)
            metadata.update(send_notice=False, superseded=True)
            conn.commit()
            return _failure(locale, metadata)
        if failure is None:
            conn.execute("UPDATE mermaid_model_events SET status='generated',response_json=?,retry_at=0 WHERE conversation_id=? AND message_id=?", (json.dumps(result, ensure_ascii=False), conversation, message_id))
            conn.execute("UPDATE mermaid_model_events SET status='superseded' WHERE conversation_id=? AND status IN ('failed','new') AND created_at<?", (conversation, row["created_at"]))
            conn.execute("DELETE FROM mermaid_model_circuit WHERE failed_at<=?", (now,))
            if not conn.execute("SELECT 1 FROM mermaid_model_circuit").fetchone():
                conn.execute("UPDATE pending_notifications SET status='resolved' WHERE notification_type='technical' AND customer_id='mermaid:model-provider' AND status IN ('pending','sent')")
            conn.commit()
            return result
        retryable = bool(failure.get("retryable")) and row["attempts"] < MAX_ATTEMPTS
        kind = str(failure.get("kind") or "transient")
        delay = TRANSIENT_DELAY_SECONDS * row["attempts"] if failure.get("retryable") else OPERATOR_COOLDOWN_SECONDS
        retry_at = time.time() + delay
        conn.execute("UPDATE mermaid_model_events SET status='failed',error_kind=?,retryable=?,retry_at=? WHERE conversation_id=? AND message_id=?", (kind, retryable, retry_at, conversation, message_id))
        if kind == "invalid_response":
            # The provider answered, but this event's structured output was
            # invalid. Keep its retry local so healthy guests can still reply.
            # A probe may clear only its own older outage, never a concurrent
            # newer provider failure or another worker's lease.
            cleared = conn.execute("DELETE FROM mermaid_model_circuit WHERE probe_id=? AND failed_at<=?", (probe_id, now)).rowcount
            if cleared and not conn.execute("SELECT 1 FROM mermaid_model_circuit").fetchone():
                conn.execute("UPDATE pending_notifications SET status='resolved' WHERE notification_type='technical' AND customer_id='mermaid:model-provider' AND status IN ('pending','sent')")
            updated = conn.execute("SELECT * FROM mermaid_model_events WHERE conversation_id=? AND message_id=?", (conversation, message_id)).fetchone()
            conn.commit()
            return _failure(locale, _metadata(updated))
        circuit = conn.execute("SELECT * FROM mermaid_model_circuit WHERE singleton=1").fetchone()
        if circuit and circuit["probe_id"] != probe_id and circuit["blocked_until"] > time.time():
            retry_at = max(retry_at, circuit["blocked_until"])
            if circuit["error_kind"] in {"billing", "credentials", "request_rejected"}:
                kind = circuit["error_kind"]
        conn.execute("INSERT INTO mermaid_model_circuit (singleton,blocked_until,error_kind,credential_hash,probe_id,failed_at) VALUES (1,?,?,?,'',?) ON CONFLICT(singleton) DO UPDATE SET blocked_until=excluded.blocked_until,error_kind=excluded.error_kind,credential_hash=excluded.credential_hash,probe_id='',failed_at=excluded.failed_at", (retry_at, kind, credential_hash, time.time()))
        _alert(conn, kind, time.time())
        updated = conn.execute("SELECT * FROM mermaid_model_events WHERE conversation_id=? AND message_id=?", (conversation, message_id)).fetchone()
        conn.commit()
        return _failure(locale, _metadata(updated))
    finally:
        conn.close()


def notice_sent(metadata):
    conn = _conn()
    try:
        conn.execute("UPDATE mermaid_model_events SET notice_sent=1 WHERE conversation_id=? AND message_id=?", (metadata["conversation_id"], metadata["message_id"]))
        conn.commit()
    finally:
        conn.close()


def defer_inbound(message_ids, processing_token, metadata):
    """CAS-release one complete batch with a future retry lease or honest failure."""
    ids = list(dict.fromkeys(message_ids))
    if not ids or not processing_token:
        return False
    retryable = metadata["retryable"] and not metadata.get("superseded")
    status = "recovering" if retryable else "ignored" if metadata.get("superseded") else "processing_failed"
    reason = "mermaid_model_superseded" if metadata.get("superseded") else "mermaid_model_" + metadata["kind"]
    lease = datetime.fromtimestamp(metadata["retry_at"], timezone.utc).isoformat() if retryable else ""
    conn = state_registry._get_conn()
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN IMMEDIATE")
        placeholders = ",".join("?" for _ in ids)
        rows = conn.execute(f"SELECT message_id,status,processing_token FROM inbound_processing_events WHERE message_id IN ({placeholders})", ids).fetchall()
        if len(rows) != len(ids) or any(row["status"] != "processing" or row["processing_token"] != processing_token for row in rows):
            conn.rollback()
            return False
        conn.executemany("UPDATE inbound_processing_events SET status=?,reason=?,lease_expires_at=?,processing_token='',updated_at=? WHERE message_id=? AND processing_token=?", [(status, reason, lease, datetime.now(timezone.utc).isoformat(), mid, processing_token) for mid in ids])
        conn.commit()
        return True
    finally:
        conn.close()
