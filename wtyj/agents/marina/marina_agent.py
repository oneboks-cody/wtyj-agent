# bluemarlin/agents/marina/marina_agent.py
# Last modified: Brief 131b
# Purpose: Single Claude call per message. Returns structured JSON.

import json
import os
from datetime import datetime, timezone, timedelta

import anthropic
from shared import config_loader
from shared import agent_identity
from shared import bm_logger
from shared import tenant_hard_rules
from shared.public_business_config import (
    get_public_business_identity, public_business_config,
    redact_config_credentials, render_public_business_context,
)

_CURACAO_TZ = timezone(timedelta(hours=-4))

_RESPONSE_DEFAULTS = {
    "intents": ["inquiry"],
    "fields": {},
    "confidence": "medium",
    "reply": "",
    "clarifications_needed": [],
    "requires_human": False,
    "flags": {},
    "internal_note": "",
    "ali_vehicle_recommendation": None,
    "ali_rental_change": None,
    "ali_summary_action": None,
    "ali_primary_intent": None,
    "ali_lead_follow_up_action": "none",
}


# Brief 224: bracketed sentinels Marina's prompt may emit for routing.
# These must never reach the customer — strip from any text field returned
# by process_message before it leaves the agent. NOT a blanket "[X]" strip:
# [BOOKING_REF] and [PAYMENT_LINK] are legitimate template placeholders that
# the email_poller substitutes downstream.
_INTERNAL_TOKENS = (
    "[ESCALATE]",
    "[SOFT_ESCALATION]",
    "[HARD_ESCALATION]",
    "[HANDOFF]",
    "[HUMAN_TAKEOVER]",
)


def _strip_internal_tokens(text: str) -> str:
    """Remove every internal routing token from `text` and clean up trailing
    whitespace + isolated blank lines a removed token may have left behind."""
    if not text:
        return text
    out = text
    for tok in _INTERNAL_TOKENS:
        out = out.replace(tok, "")
    while "\n\n\n" in out:
        out = out.replace("\n\n\n", "\n\n")
    return out.rstrip()


# Brief 174: tool use schema for Marina's structured response.
# Replaces the "Respond with ONLY a JSON object" text contract with a
# protocol-enforced schema. Claude Sonnet 4.6 (and later) MUST emit a
# tool_use block matching this schema when called with tool_choice forced
# to marina_response. No string parsing, no preamble, no markdown fences.
#
# Only `intents`, `confidence`, `reply`, `requires_human` are REQUIRED;
# the rest default via _RESPONSE_DEFAULTS in process_message. Keeping the
# required set minimal matches the pre-Brief-174 behaviour where Claude
# could emit a subset of fields and the parser filled in the rest.
MARINA_TOOL = {
    "name": "marina_response",
    "description": (
        "Emit a structured response to the customer's message. This is the "
        "ONLY way to reply — do not emit free text. Populate the fields you "
        "have evidence for; leave others at their defaults. The `reply` field "
        "is what the customer sees."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "intents": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": ["booking", "order", "inquiry", "cancellation", "reschedule",
                             "complaint", "social", "off_topic"],
                },
                "description": "One or more intent labels for this message.",
            },
            "fields": {
                "type": "object",
                "description": (
                    "Facts extracted from the current message or any earlier customer "
                    "message in the conversation. Re-read the full history on every turn, "
                    "keep explicit facts already supplied, and never invent missing details."
                ),
                "properties": {
                    "service_name": {
                        "type": "string",
                        "description": "Therapy, service, or consultation type explicitly requested by the customer.",
                    },
                    "service_key": {"type": "string", "description": "Exact key from the services list. See SERVICE ALIASES in the system prompt for the customer-wording mapping."},
                    "date": {"type": "string", "description": "YYYY-MM-DD format."},
                    "guests": {"type": "integer"},
                    "customer_name": {
                        "type": "string",
                        "description": "The customer's complete name exactly as provided.",
                    },
                    "phone": {
                        "type": "string",
                        "description": "A telephone number explicitly typed by the customer in the conversation.",
                    },
                    "email": {"type": "string"},
                    "special_requests": {"type": "string"},
                    "slot_time": {"type": "string", "description": "HH:MM format."},
                    "products": {
                        "type": "array",
                        "description": "Product order lines, only for product-order tenants.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "quantity": {"type": "integer"},
                                "unit_price": {"type": "number"},
                                "subtotal": {"type": "number"},
                            },
                        },
                    },
                    "product_name": {"type": "string"},
                    "quantity": {"type": "integer"},
                    "delivery_address": {"type": "string"},
                    "comments": {"type": "string"},
                    "rental_start": {"type": "string", "description": "Ali only: pickup date in YYYY-MM-DD format."},
                    "rental_end": {"type": "string", "description": "Ali only: return date in YYYY-MM-DD format."},
                    "pickup_location": {"type": "string", "description": "Ali only: customer-confirmed pickup location."},
                    "pickup_location_kind": {"type": "string", "enum": ["fixed", "hotel_delivery"], "description": "Ali only: selected published pickup option kind."},
                    "pickup_hotel_name": {"type": "string", "description": "Ali only: hotel name explicitly supplied for hotel delivery."},
                    "pickup_hotel_address": {"type": "string", "description": "Ali only: full or partial hotel address explicitly supplied for hotel delivery."},
                    "return_location": {"type": "string", "description": "Ali only: customer-confirmed return location."},
                    "vehicle_id": {"type": "string", "description": "Ali only: Python-owned published vehicle UUID. Never invent or populate this field."},
                    "vehicle_name": {"type": "string", "description": "Ali only: exact published vehicle name when the customer selects one."},
                    "vehicle_class_id": {"type": "string", "description": "Ali only: Python-owned published category UUID. Never invent or populate this field."},
                    "vehicle_class_name": {"type": "string", "description": "Ali only: exact published category name when the customer selects one."},
                    "driver_age": {"type": "integer", "description": "Ali only: main driver's stated age."},
                    "passenger_count": {"type": "integer", "description": "Ali only: extract only when the customer volunteers it. Never ask for passenger count."},
                    "luggage_count": {"type": "integer", "description": "Ali only: extract only when the customer volunteers it. Never ask for luggage count."},
                    "extra_ids": {"type": "array", "items": {"type": "string"}, "description": "Ali only: Python-owned published extra UUIDs. Never invent values."},
                    "supplements": {
                        "type": "array",
                        "description": (
                            "Ali only: complete current supplement selections explicitly requested by the customer. "
                            "Use only an exact published catalog name and a whole-number quantity; never include IDs or prices."
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "quantity": {"type": "integer", "minimum": 1, "maximum": 20},
                            },
                            "required": ["name", "quantity"],
                            "additionalProperties": False,
                        },
                    },
                    "conversation_language": {"type": "string", "enum": ["en", "nl", "pap", "de", "es"], "description": "Ali only: current conversation language."},
                    "order_total": {"type": "number"},
                    "currency": {"type": "string"},
                    "first_name": {
                        "type": "string",
                        "description": "Given name. When a full name is supplied, separate the first name from the remaining surnames.",
                    },
                    "surnames": {
                        "type": "string",
                        "description": "All surname words after the given name when a full name is supplied.",
                    },
                    "callback_preference": {
                        "type": "string",
                        "description": "When the human team may telephone the customer. Never use the preferred appointment day or time here.",
                    },
                    "appointment_preference": {
                        "type": "string",
                        "description": "Days or time ranges the customer prefers for the therapy session or appointment. Never use callback availability here.",
                    },
                    "session_type": {
                        "type": "string",
                        "description": "Session format explicitly established in context, normally Presencial or Online. Choosing a physical clinic after locations are offered is explicit evidence for Presencial.",
                    },
                    "preferred_clinic": {
                        "type": "string",
                        "description": "Consulta Despertares only: the official clinic or centre explicitly chosen by the prospect for an in-person session. Never infer it from their home area. Store 'Sin preferencia' only when the prospect explicitly has no preference.",
                    },
                    "visit_reason": {
                        "type": "string",
                        "description": "A short, neutral paraphrase of why the customer is seeking psychological support, using only what they volunteered. Never diagnose or add clinical conclusions. Never put a location, clinic, callback time, appointment time, or session format in this field.",
                    },
                },
            },
            "confidence": {
                "type": "string",
                "enum": ["high", "medium", "low"],
            },
            "reply": {
                "type": "string",
                "description": "The actual reply text shown to the customer. Write naturally, in the customer's language.",
            },
            "reply_hold_failed": {
                "type": "string",
                "description": "Optional — only when setting booking_confirmed=true. Apologetic message if the slot is unavailable.",
            },
            "clarifications_needed": {
                "type": "array",
                "items": {"type": "string"},
            },
            "requires_human": {
                "type": "boolean",
                "description": "Set true for complaints, refunds, cancellations, or explicit human requests.",
            },
            "flags": {
                "type": "object",
                "description": "Internal state flags Marina uses for orchestration.",
                "properties": {
                    "booking_confirmed": {"type": "boolean"},
                    "awaiting_booking_confirmation": {"type": "boolean"},
                    "awaiting_order_confirmation": {"type": "boolean"},
                    "order_confirmed": {"type": "boolean"},
                    "needs_child_ages": {"type": "boolean"},
                    "needs_escalation_email": {"type": "boolean"},
                    "large_group": {"type": "boolean"},
                },
            },
            "ali_vehicle_recommendation": {
                "type": "object",
                "description": (
                    "Ali only: request one catalog-grounded visual recommendation whenever "
                    "the customer is comparing/discovering cars and catalog media exists. "
                    "Use `specific` only when the customer requested or chose one exact current "
                    "vehicle. Use `curated` for an undecided customer; choose only 2–5 exact current vehicle "
                    "names. Omit this object only when no visual should be sent."
                ),
                "properties": {
                    "mode": {
                        "type": "string",
                        "enum": ["specific", "curated"],
                    },
                    "vehicle_names": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 5,
                        "uniqueItems": True,
                        "items": {"type": "string"},
                        "description": "Exact current vehicle names from the injected Ali catalog.",
                    },
                    "availability_note": {
                        "type": "string",
                        "description": (
                            "One concise natural sentence in the customer's language saying "
                            "that final vehicle availability still requires confirmation. "
                            "Do not repeat this sentence in `reply`."
                        ),
                    },
                    "cta_label": {
                        "type": "string",
                        "description": (
                            "A natural label of at most 24 characters in the customer's language "
                            "for opening a vehicle details page, such as Car Details. This URL "
                            "label must never claim that clicking it selects the car."
                        ),
                    },
                },
                "required": ["mode", "vehicle_names", "availability_note", "cta_label"],
                "additionalProperties": False,
            },
            "ali_rental_change": {
                "type": "object",
                "description": (
                    "Ali only and optional: describe only a correction explicitly made in "
                    "the newest customer message. Use `apply` when the message supplies one "
                    "or more replacement values, and list only those canonical fields. Use "
                    "`clarify` when the customer only says they want a change or the new "
                    "vehicle/category is not an exact catalog match. Omit for ordinary intake."
                ),
                "properties": {
                    "mode": {"type": "string", "enum": ["apply", "clarify"]},
                    "changed_fields": {
                        "type": "array",
                        "uniqueItems": True,
                        "items": {
                            "type": "string",
                            "enum": [
                                "customer_name", "rental_start", "rental_end",
                                "pickup_location", "return_location", "vehicle_selection",
                                "driver_age", "passenger_count", "luggage_count",
                                "supplements", "comments",
                            ],
                        },
                    },
                    "vehicle_selection_kind": {
                        "type": "string",
                        "enum": ["vehicle", "category"],
                        "description": (
                            "Required when changed_fields contains vehicle_selection. "
                            "Identify whether the newest explicit choice is an exact "
                            "catalog vehicle or a catalog category."
                        ),
                    },
                },
                "required": ["mode", "changed_fields"],
                "additionalProperties": False,
            },
            "ali_summary_action": {
                "type": "object",
                "description": (
                    "Ali only and optional: set mode `repeat` only when the newest "
                    "customer message explicitly asks to see the current rental summary "
                    "again. Omit for confirmations, questions, hesitation, rejection, "
                    "corrections, and vehicle exploration."
                ),
                "properties": {
                    "mode": {"type": "string", "enum": ["repeat"]},
                },
                "required": ["mode"],
                "additionalProperties": False,
            },
            "ali_primary_intent": {
                "type": "string",
                "enum": [
                    "continue_intake",
                    "ask_question",
                    "reject_or_hesitate",
                    "request_recommendation",
                    "repeat_summary",
                    "confirm_summary",
                    "request_quote_status",
                    "other",
                ],
                "description": (
                    "Ali only: the single primary intent of the newest customer "
                    "message. Choose exactly one. This intent is independent from "
                    "ali_rental_change, so a correction may also request a recommendation."
                ),
            },
            "ali_lead_follow_up_action": {
                "type": "string",
                "enum": ["continue", "stop", "none"],
                "description": (
                    "Ali only: classify the newest reply to a pre-reservation "
                    "follow-up when _ali_lead_follow_up_context is present. Use "
                    "continue when the customer wants rental help to continue, "
                    "asks a rental question, or supplies rental information; stop "
                    "only for an explicit request to stop reminders or contact; "
                    "otherwise use none."
                ),
            },
            "semi_escalation": {
                "type": "boolean",
                "description": "Set true only for specific factual questions Marina cannot answer from available context.",
            },
            "relay_question": {
                "type": "string",
                "description": "Exact question to relay to the human team. Only present when semi_escalation is true.",
            },
            "internal_note": {
                "type": "string",
                "description": "One sentence for the operator log. Never shown to the customer.",
            },
        },
        "required": ["intents", "confidence", "reply", "requires_human"],
    },
}


LANGUAGE_CORRECTION_TOOL = {
    "name": "language_corrected_reply",
    "description": (
        "Rewrite the supplied customer-facing reply entirely in the requested "
        "language while preserving its meaning, warmth, and number of questions."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "reply": {
                "type": "string",
                "description": "The complete corrected customer-facing reply.",
            },
        },
        "required": ["reply"],
    },
}


# Top-level keys to skip (already injected elsewhere or handled separately)
_SKIP_TOP_LEVEL = {
    "service_aliases",      # Already in system prompt via _build_service_alias_text()
    "agent_persona",        # Already in system prompt via _build_agent_persona_block() — Brief 149
}

# Language recognition hints for the LANGUAGE RULE — Brief 160.
# Maps language name (matching client.json business.languages entries) to
# a recognition-hint string. Per-client language selection happens in
# _build_system_prompt by iterating over business.get('languages', []).
# Adding a new supported language: add an entry here + the client's
# client.json business.languages array.
_LANGUAGE_HINTS = {
    "English": 'If the body is in English ("Hi", "I want", "please", "thanks"), reply in English.',
    "Dutch": 'If the body is in Dutch ("Hallo", "ik wil", "alstublieft", "graag", "bedankt", "morgen", "zondag"), reply in Dutch.',
    "German": 'If the body is in German ("Hallo", "ich möchte", "bitte", "danke"), reply in German.',
    "Spanish": 'If the body is in Spanish ("Hola", "quiero", "por favor", "mañana", "domingo"), reply in Spanish.',
    "Portuguese": 'If the body is in Portuguese ("Olá", "eu quero", "por favor", "obrigado"), reply in Portuguese.',
    "Papiamentu": 'If the body is in Papiamentu ("Bon dia", "Bon tardi", "mi ke", "mi por", "djadumingu", "djaluna", "kiko", "kuantu", "pa", "ku", "ta"), reply in Papiamentu. Papiamentu is the Creole spoken on Curaçao — it sounds similar to Spanish and Portuguese but has its own vocabulary and grammar. Do NOT misidentify it as Spanish.',
}


def _build_client_context() -> str:
    """Render reviewed public business facts, excluding credentials recursively."""
    return render_public_business_context(
        config_loader.get_raw(), exclude=_SKIP_TOP_LEVEL,
    )


def _build_service_alias_text() -> str:
    aliases = public_business_config(config_loader.get_raw()).get("service_aliases", {})
    grouped: dict[str, list[str]] = {}
    for alias, service_key in aliases.items():
        grouped.setdefault(service_key, []).append(alias)
    lines = []
    for service_key, alias_list in grouped.items():
        quoted = ", ".join(f'"{a}"' for a in alias_list)
        lines.append(f'      {quoted} → {service_key}')
    return "\n".join(lines)



def _icp_envelope_for_prompt() -> dict:
    """J3-N2-02: fetch the ICP override envelope once per prompt build.

    The icp_overrides helper has its own 60s in-process cache, so this
    is cheap to call. Returns the envelope shape from J3-N2-01 with
    J3-BE-18/19 additions: sot_entries (list), ai_agent_settings
    (dict with tone + escalation_rules, None when not overridden).

    NEVER raises - the helper returns an empty envelope on any error.
    Fail-closed: when bridge is unreachable the agent falls back to
    backend defaults (client.json) automatically because every block-
    builder checks the override and only consumes it when present."""
    try:
        from shared import icp_overrides
        return icp_overrides.fetch_overrides()
    except Exception:
        # Defensive - should never happen but never let an ICP
        # import or fetch issue break the agent prompt build.
        return {
            "available": False,
            "sot_entries": [],
            "ai_agent_settings": {"tone": None, "escalation_rules": None},
        }


def _build_icp_sot_block(envelope: dict) -> str:
    """J3-N2-02: render ICP-pushed SOT entries as an authoritative
    knowledge block alongside the existing info_updates / knowledge_
    files blocks. Empty string when no SOT overrides exist.

    Entries are operator-curated via the control panel and intended
    as factual current context the Agent should treat as authoritative.
    Each entry surfaces its category as a tag."""
    entries = envelope.get("sot_entries") or []
    if not isinstance(entries, list) or not entries:
        return ""
    bullets = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        title = (e.get("title") or "").strip()
        content = (e.get("content") or "").strip()
        category = (e.get("category") or "general").strip()
        if not title or not content:
            continue
        bullets.append(f"\n[{category}] {title}\n{content}")
    if not bullets:
        return ""
    return (
        "\n\nICP SOURCE OF TRUTH (operator-curated via control panel — "
        "authoritative current context, treat as factual):"
        + "".join(bullets)
    )


def _build_icp_final_override_block(
    envelope: dict,
    *,
    include_sot_entries: bool = True,
) -> str:
    """Render Nr3 operator edits as the final high-priority prompt block.

    The base Marina prompt still carries generic booking examples and
    broad refusal rules. Tenant-specific ICP edits must appear after
    those generic rules too, so Calvin's latest Nr3 edits win when
    there is tension.
    """
    if not isinstance(envelope, dict):
        return ""
    entries = (envelope.get("sot_entries") or []) if include_sot_entries else []
    ai_agent_settings = envelope.get("ai_agent_settings") or {}
    if not isinstance(entries, list):
        entries = []
    if not isinstance(ai_agent_settings, dict):
        ai_agent_settings = {}
    tone = ai_agent_settings.get("tone")
    rules = ai_agent_settings.get("escalation_rules")

    has_tone = isinstance(tone, dict) and (
        (tone.get("tone") or "").strip() or (tone.get("notes") or "").strip()
    )
    has_rules = isinstance(rules, dict) and (
        rules.get("soft_escalation") or rules.get("hard_escalation")
    )

    clean_entries = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        title = (e.get("title") or "").strip()
        content = (e.get("content") or "").strip()
        category = (e.get("category") or "general").strip()
        if title and content:
            clean_entries.append((category, title, content))

    if not (has_tone or has_rules or clean_entries):
        return ""

    lines = [
        "\nFINAL TENANT-SPECIFIC OPERATOR OVERRIDES FROM NR3 (HIGHEST PRIORITY):",
        "- These are the latest Calvin/operator instructions for this tenant.",
        "- Follow this block over generic booking examples, old default wording, and generic platform behavior.",
        "- Do not force appointments or bookings. Help first, answer what you can, then suggest a consultation only when it naturally fits.",
        "- For legal-service tenants, general procedural/public information and intake questions are in scope when supported by tenant context. Avoid specific legal advice or promises.",
        "- Apply these instructions immediately in the next reply.",
    ]

    if has_tone:
        tone_value = (tone.get("tone") or "").strip()
        notes = (tone.get("notes") or "").strip()
        if tone_value:
            lines.append(f"\nTone override: {tone_value}")
        if notes:
            lines.append(f"Tone notes: {notes}")

    if clean_entries:
        lines.append("\nSource of Truth overrides:")
        for category, title, content in clean_entries:
            lines.append(f"[{category}] {title}\n{content}")

    if has_rules:
        soft = rules.get("soft_escalation") or {}
        hard = rules.get("hard_escalation") or {}
        lines.append("\nEscalation rule overrides:")
        if isinstance(soft, dict) and soft.get("enabled"):
            lines.append(f"- Agent needs help when: {(soft.get('when') or '').strip()}")
        if isinstance(hard, dict) and hard.get("enabled"):
            lines.append(f"- Human takeover when: {(hard.get('when') or '').strip()}")

    return "\n".join(lines)


def _build_agent_persona_block(envelope: dict = None) -> str:
    """Build the AGENT PERSONA prompt block from the structured agent_persona
    section in client.json. Falls back to the legacy common_sense_knowledge.marina_persona
    free-text string if the structured section is missing or empty.

    Brief 149.

    J3-N2-02: when the ICP override envelope contains an
    ai_agent_settings.tone override, that value REPLACES the backend
    tone string. Similarly ai_agent_settings.escalation_rules replaces
    the backend escalation_tone block (with both soft+hard rules
    rendered in the canonical operator-facing terminology).
    Tenant isolation: envelope tenant_id is resolved locally in
    icp_overrides; no cross-tenant data can land in this prompt.
    """
    persona = public_business_config(config_loader.get_raw()).get("agent_persona", {}) or {}
    if envelope is None:
        envelope = _icp_envelope_for_prompt()
    icp_ai = envelope.get("ai_agent_settings") or {}
    icp_tone = icp_ai.get("tone") if isinstance(icp_ai, dict) else None
    icp_rules = icp_ai.get("escalation_rules") if isinstance(icp_ai, dict) else None
    lines = []

    # J3-N2-02: ICP tone override wins
    if isinstance(icp_tone, dict) and (icp_tone.get("tone") or "").strip():
        lines.append(f"Tone: {icp_tone['tone'].strip()}  [ICP override]")
        notes = (icp_tone.get("notes") or "").strip()
        if notes:
            lines.append(f"Tone notes: {notes}")
    elif persona.get("tone"):
        lines.append(f"Tone: {persona['tone']}")
    if persona.get("language_register"):
        lines.append(f"Language register: {persona['language_register']}")

    if persona.get("greeting_style"):
        lines.append(f"\nGreeting style:\n{persona['greeting_style']}")

    if persona.get("closing_style"):
        lines.append(f"\nClosing style:\n{persona['closing_style']}")

    rules = persona.get("brand_voice_rules") or []
    if rules:
        lines.append("\nBrand voice rules (MUST follow):")
        for rule in rules:
            lines.append(f"- {rule}")

    allowed = persona.get("topics_allowed") or []
    if allowed:
        lines.append("\nTopics you handle:")
        for t in allowed:
            lines.append(f"- {t}")

    refused = persona.get("topics_refused") or []
    if refused:
        lines.append("\nTopics you refuse (politely redirect without apology):")
        for t in refused:
            lines.append(f"- {t}")

    if persona.get("small_talk"):
        lines.append(f"\nSmall talk:\n{persona['small_talk']}")

    # J3-N2-02: ICP escalation_rules override wins; render both rules
    # in the canonical 'soft escalation' / 'hard escalation' terms (NOT
    # 'soft mode' / 'hard mode' - that wording is banned per J3-BE-19).
    if isinstance(icp_rules, dict) and (
            icp_rules.get("soft_escalation") or icp_rules.get("hard_escalation")):
        soft = icp_rules.get("soft_escalation") or {}
        hard = icp_rules.get("hard_escalation") or {}
        lines.append("\nEscalation rules [ICP override]:")
        if soft.get("enabled"):
            when = (soft.get("when") or "").strip()
            lines.append(f"- Soft escalation (Agent needs help, operator guides, "
                          f"Agent replies): {when}" if when else
                          "- Soft escalation: enabled (no condition specified)")
        else:
            lines.append("- Soft escalation: DISABLED")
        if hard.get("enabled"):
            when = (hard.get("when") or "").strip()
            lines.append(f"- Hard escalation (human takeover, Agent stops, "
                          f"operator replies directly): {when}" if when else
                          "- Hard escalation: enabled (no condition specified)")
        else:
            lines.append("- Hard escalation: DISABLED")
    elif persona.get("escalation_tone"):
        lines.append(f"\nEscalation tone:\n{persona['escalation_tone']}")

    if persona.get("freeform_notes"):
        lines.append(f"\nAdditional context:\n{persona['freeform_notes']}")

    if lines:
        return "\n".join(lines)

    # Legacy fallback — pre-Brief-149 clients use common_sense_knowledge.marina_persona
    return config_loader.get_common_sense_knowledge().get("marina_persona", "")


def _build_customer_file_block(customer_file) -> str:
    """Brief 166: render the CUSTOMER FILE prompt block from a customer_get_full() dict.
    Empty/None input returns an empty string (block is omitted). Bounded size:
    max 20 identifiers, max 5 recent interactions (those caps are enforced upstream
    in state_registry.customer_get_full)."""
    if not customer_file or not customer_file.get("id"):
        return ""
    lines = [
        "CUSTOMER FILE — use this context when answering this customer. "
        "This person may have contacted us before across email, WhatsApp, Instagram, "
        "Facebook, or X. Use the identifiers and interaction history below to answer "
        "with continuity; reference past questions or bookings naturally when relevant."
    ]
    name = customer_file.get("display_name") or "(no name on file)"
    lines.append(f"\nDisplay name: {name}")
    first_seen = customer_file.get("first_seen", "") or ""
    last_seen = customer_file.get("last_seen", "") or ""
    if first_seen:
        lines.append(f"First contact: {first_seen[:10]}  |  Last contact: {last_seen[:10]}")
    ids = customer_file.get("identifiers") or []
    if ids:
        lines.append("\nKnown identifiers (used across channels):")
        for ident in ids:
            lines.append(f"  - {ident.get('type', '?')}: {ident.get('value', '')}")
    recent = customer_file.get("recent_interactions") or []
    if recent:
        lines.append("\nRecent interactions (newest first, across all channels):")
        for r in recent:
            date = (r.get("created_at") or "")[:10]
            lines.append(f"  - [{date}] [{r.get('channel', '?')}] {r.get('summary', '')}")
    summary = customer_file.get("summary") or ""
    if summary:
        lines.append(f"\nRolling summary: {summary}")
    # Brief 178: the CROSS-CHANNEL CONTINUITY rule that used to live here was moved
    # into the main system prompt so it's emitted even when customer_file is empty
    # (e.g. brand-new customer on their first message).
    return "\n".join(lines)


def _build_approved_answers_block(channel: str) -> str:
    """Brief 219: return an APPROVED ANSWERS prompt block listing recent
    operator-curated learnings for this channel, or '' when the tenant
    hasn't opted in or no learnings match. When non-empty the return
    starts with '\\n\\n' so the f-string interpolation keeps a clean
    blank-line break before the block; when empty the f-string adjacent
    spacing collapses cleanly. Tenant opt-in via
    client.json::features.approved_learnings_in_prompt (default false)."""
    features = config_loader.get_raw().get("features", {}) or {}
    if not features.get("approved_learnings_in_prompt"):
        return ""
    try:
        from shared import state_registry
        rows = state_registry.get_approved_learnings_for_prompt(channel, limit=20)
    except Exception:
        return ""
    if not rows:
        return ""
    pairs = []
    for r in rows:
        q = (r.get("question") or "").strip()
        a = (r.get("answer") or "").strip()
        if not a:
            continue
        if q:
            pairs.append(f"Q: {q}\nA: {a}")
        else:
            pairs.append(f"A: {a}")
    if not pairs:
        return ""
    return (
        "\n\nAPPROVED ANSWERS (operator-curated knowledge):\n"
        "The team has previously answered similar customer questions on this "
        "channel. Use these as authoritative context, they reflect how the "
        "human team wants you to handle these situations going forward. Match "
        "the spirit; do not copy verbatim if the customer phrasing differs.\n\n"
        + "\n\n".join(pairs)
    )


_SOURCE_OF_TRUTH_PROMPT_CHAR_LIMIT = 12000


def _build_source_of_truth_block() -> str:
    """Render the tenant-local dashboard Source of Truth for the Agent.

    Brief 262 made the dashboard editor server-backed, but those blocks were
    not part of Marina's runtime prompt. Brief 319 closes that gap. The state
    registry is already tenant-local (one mounted database per container), so
    the prompt must read that local store directly and must not accept a tenant
    identifier from the browser or inbound message.

    The renderer is deliberately bounded. Operators can maintain a substantial
    knowledge base without letting it consume the complete model context. A
    failed read is non-fatal: normal client.json, live catalog, uploaded files,
    and workflow-state context continue to work.
    """
    try:
        from shared import state_registry
        blocks = state_registry.source_of_truth_get()
    except Exception:
        return ""
    if not isinstance(blocks, list) or not blocks:
        return ""

    body_lines = []
    remaining = _SOURCE_OF_TRUTH_PROMPT_CHAR_LIMIT

    def append_line(value: str, prefix: str = "") -> bool:
        nonlocal remaining
        text = str(value or "").strip()
        if not text or remaining <= 0:
            return remaining > 0
        rendered = f"{prefix}{text}"
        if len(rendered) > remaining:
            remaining = 0
            return False
        body_lines.append(rendered)
        remaining -= len(rendered) + 1
        return remaining > 0

    for block in blocks:
        if not isinstance(block, dict):
            continue
        title = str(block.get("title") or "").strip()
        if not title:
            continue
        if not append_line(title, "## "):
            break
        if not append_line(block.get("content") or ""):
            break
        for item in block.get("items") or []:
            if not append_line(item, "- "):
                break
        if remaining <= 0:
            break
        for subsection in block.get("subsections") or []:
            if not isinstance(subsection, dict):
                continue
            subsection_title = str(subsection.get("title") or "").strip()
            if subsection_title and not append_line(subsection_title, "### "):
                break
            if not append_line(subsection.get("content") or ""):
                break
            for item in subsection.get("items") or []:
                if not append_line(item, "- "):
                    break
            if remaining <= 0:
                break
        if remaining <= 0:
            break

    if not body_lines:
        return ""
    return (
        "\n\nTENANT SOURCE OF TRUTH (operator-curated in the dashboard):\n"
        "Use this as authoritative business policy when answering customer questions. "
        "The current live catalog remains authoritative for fleet, prices and extras, "
        "and persisted workflow state remains authoritative for what has actually "
        "happened. If this knowledge does not answer a question, do not invent an answer.\n\n"
        + "\n".join(body_lines)
    )


def _build_info_updates_block() -> str:
    """Brief 216: render an ACTIVE BUSINESS UPDATES prompt block listing
    operator-curated info_updates that are currently active (permanent
    OR within their scheduled window). Returns '' when the tenant
    hasn't opted in or no updates are active. Same leading-`\\n\\n`
    pattern as Brief 219's APPROVED ANSWERS block so the f-string
    spacing collapses cleanly when off."""
    features = config_loader.get_raw().get("features", {}) or {}
    if not features.get("info_updates_in_prompt"):
        return ""
    try:
        from shared import state_registry
        rows = state_registry.get_active_info_updates()
    except Exception:
        return ""
    if not rows:
        return ""
    bullets = []
    for r in rows:
        text = (r.get("text") or "").strip()
        if not text:
            continue
        bullets.append(f"- [{r.get('type', 'general')}] {text}")
    if not bullets:
        return ""
    return (
        "\n\nACTIVE BUSINESS UPDATES (operator-curated, time-sensitive):\n"
        "Use these as authoritative current context. They override older "
        "default information when relevant. Permanent items always apply; "
        "scheduled items apply only during their window (already filtered).\n\n"
        + "\n".join(bullets)
    )


def _product_title_from_update(text: str) -> tuple[str, str]:
    """Return a product title + optional description from an info update."""
    clean = (text or "").strip()
    if not clean:
        return "", ""
    parts = [p.strip() for p in clean.splitlines() if p.strip()]
    if not parts:
        return "", ""
    title = parts[0]
    description = " ".join(parts[1:]).strip()
    if description == title:
        description = ""
    return title, description


def _product_catalog_key(title: str) -> str:
    return (
        (title or "")
        .strip()
        .lower()
        .replace("cinamon", "cinnamon")
        .replace("  ", " ")
    )


def _product_catalog_display_title(title: str) -> str:
    return (title or "").strip().replace("Cinamon", "Cinnamon")


def _build_live_product_catalog_block() -> str:
    """Build a canonical product catalog from active Nr2 product records.

    This prevents static SOT text from becoming the product source of truth.
    Tenants can add/change products in Nr2, and the agent receives the current
    product count and names directly from those active product records.
    """
    try:
        from shared import state_registry
        rows = state_registry.get_active_info_updates()
    except Exception:
        return ""

    products: dict[str, dict[str, str]] = {}
    order: list[str] = []
    for row in rows or []:
        if str(row.get("type") or "").strip().lower() != "product":
            continue
        title, description = _product_title_from_update(row.get("text") or "")
        key = _product_catalog_key(title)
        if not key:
            continue
        if key not in products:
            order.append(key)
        products[key] = {
            "title": _product_catalog_display_title(title),
            "description": description,
        }

    if not order:
        return ""

    lines = []
    for idx, key in enumerate(order, 1):
        product = products[key]
        title = product["title"]
        description = product["description"]
        if description:
            lines.append(f"{idx}. {title}: {description}")
        else:
            lines.append(f"{idx}. {title}")

    delivery_line = ""
    try:
        product_settings = config_loader.get_product_settings() or {}
        delivery_amount = product_settings.get("delivery_cost_amount")
        delivery_currency = str(product_settings.get("delivery_cost_currency") or "").strip().upper()
        if delivery_amount not in ("", None):
            delivery_value = float(delivery_amount)
            if delivery_value >= 0:
                display_amount = int(delivery_value) if delivery_value.is_integer() else round(delivery_value, 2)
                delivery_line = (
                    f"\nFixed delivery cost: {delivery_currency or 'XCG'} {display_amount}. "
                    "Add this delivery cost to product totals when summarizing orders.\n"
                )
    except Exception:
        delivery_line = ""

    return (
        "\n\nLIVE PRODUCT CATALOG (central source from active Nr2 product records):\n"
        f"Current active product count: {len(lines)}.\n"
        "Use this live catalog for product availability, product names, and product counts. "
        "It overrides static Source of Truth product lists/counts if they differ.\n"
        "Do not say the tenant has fewer products than this catalog lists.\n\n"
        f"{delivery_line}"
        + "\n".join(lines)
    )


def _build_knowledge_files_block() -> str:
    """Brief 230: unless features.knowledge_files_in_prompt is explicitly
    false, and at least one knowledge file has status='ready', inject
    the extracted text as a KNOWLEDGE FILES section. Same leading-`\n\n`
    pattern as Brief 219 / Brief 216 blocks so f-string spacing
    collapses cleanly when off."""
    features = config_loader.get_raw().get("features", {}) or {}
    if features.get("knowledge_files_in_prompt") is False:
        return ""
    try:
        from shared import state_registry
        files = state_registry.get_knowledge_files_for_prompt(limit=5)
    except Exception:
        return ""
    if not files:
        return ""
    parts = ["KNOWLEDGE FILES (uploaded reference documents — use these as "
             "factual context when answering customer questions):"]
    for f in files:
        parts.append(f"\n--- {f['filename']} ---")
        text = (f.get("text") or "")[:3000]
        parts.append(text)
    return "\n\n" + "\n".join(parts)


def _is_wibrandt_order_tenant(business: dict) -> bool:
    """Tenant-scoped product order behaviour for Wibrandt only."""
    raw = config_loader.get_raw() or {}
    slug = str(raw.get("tenant_slug") or raw.get("slug") or business.get("slug") or "").lower()
    name = str(business.get("name") or "").lower()
    return slug == "wibrandt" or name == "wibrandt"


def _build_wibrandt_order_block(business: dict) -> str:
    if not _is_wibrandt_order_tenant(business):
        return ""
    return """
WIBRANDT PRODUCT ORDER FLOW:
This tenant sells products. Product purchases are intent "order", not booking, appointment, support, or generic escalation.

Phase 1: normal conversation. Answer product questions naturally. Do not escalate.
Phase 2: when the customer wants to order, collect product, quantity, delivery address, customer name if natural, and comments if useful. Do not escalate while gathering data.
Phase 3: once product, quantity, and delivery address are known, calculate an order summary using product/SOT prices when available. Include product, quantity, subtotal, total, delivery address, and comments if present. Ask: "Does everything look correct?"
Phase 4: only after the customer confirms that order summary with yes, looks good, perfect, let's do it, or similar, set flags.order_confirmed=true and flags.awaiting_order_confirmation=false.

Do not set requires_human for a normal order. Python will create the ORDER escalation after order_confirmed=true.
Do not use booking_confirmed for Wibrandt product orders.
"""


def _build_ali_quote_block() -> str:
    """Build the highest-priority Ali intake contract from the live catalog."""
    raw = config_loader.get_raw() or {}
    try:
        from agents.social import ali_quote_workflow
    except Exception:
        return ""
    if not ali_quote_workflow.tenant_configured(raw):
        return ""
    if not ali_quote_workflow.tenant_enabled(raw):
        return """
ALI CAR RENTAL QUOTE WORKFLOW IS PAUSED (HIGHEST PRIORITY):
Do not collect or confirm rental details. Do not redirect the customer to WhatsApp,
email, telephone, a website, or a form. They are already in the correct WhatsApp
conversation. Briefly say in their language that quote service is temporarily
unavailable and the team will continue with them here.
"""
    try:
        catalog = ali_quote_workflow.catalog_prompt_context(
            ali_quote_workflow.get_intake_catalog(force_refresh=True)
        )
    except ali_quote_workflow.AliQuoteError:
        return """
ALI CAR RENTAL CATALOG IS TEMPORARILY UNAVAILABLE (HIGHEST PRIORITY):
Do not invent a vehicle, category, price, ID, availability, or quote. Do not redirect
the customer to another channel. Briefly say in their language that the fleet details
cannot be checked right now and that the team will continue with them here.
"""
    return f"""
ALI CAR RENTAL WHATSAPP QUOTE INTAKE (HIGHEST PRIORITY):
Current published catalog, supplied digitally by Ali and containing no customer data:
{json.dumps(catalog, ensure_ascii=False, sort_keys=True)}

- The customer is already talking to Ali on WhatsApp. Never tell them to contact or
  message Ali on WhatsApp, by email, by telephone, through a website, or through a form.
- This block overrides generic booking, email-collection, contact-info, payment, service,
  trip, and confirmation instructions elsewhere in this prompt.
- POST-QUOTE TRUTH IS MANDATORY. Python may provide `_ali_quote_context` and
  `_ali_reservation_context` in the current thread flags. Treat those persisted values as
  the only authority after a quote is sent. A delivered quote is not a reservation. A
  customer's request to reserve means Ali is checking availability; it is not booked.
  Never claim that availability is approved, documents are accepted, an agreement is
  signed, payment is received, or a reservation is confirmed unless the matching persisted
  status explicitly proves it. If no reservation exists, invite the customer to use the
  post-quote choice or clearly ask whether they want Ali to check availability.
- CONFIRMED RESERVATION AFTER-SALES SUPPORT IS MANDATORY. When persisted reservation
  status is `confirmed`, do not restart quote intake or ask for rental details again. The
  deterministic confirmation delivery already gives an arrival checklist, Ali's support
  contacts, and an optional offer to receive reservation documents and agreements by email.
  If the customer's newest reply supplies an email address in response to that offer,
  extract it into `fields.email`, thank them warmly, and say the team will send the copies
  there. Never ask for the email again once it is known, and never claim the email has
  already been sent. If they say yes without supplying an address, ask only for the email
  address. If they decline, acknowledge it warmly and continue normal after-sales support.
  For a short acknowledgement such as okay or thanks, reply briefly and warmly; do not
  repeat the reservation reference, vehicle, dates, hotel, pickup details, or confirmation.
- When a post-quote customer asks a question, answer naturally from the persisted quote and
  catalog without changing reservation state. When they want a change, ask exactly what to
  change and keep the existing quote intact until a concrete replacement is supplied.
- Never treat a generic yes, okay, thanks, or looks good after quote delivery as a booking
  or reservation request. Only Python's signed Reserve action or exact RESERVE fallback may
  open the availability check.
- Re-read the complete history and extract every rental fact explicitly supplied.
- While a rental summary is awaiting confirmation, answer the newest customer intent
  naturally. Do not repeat the unchanged summary for a question, rejection, hesitation,
  or vehicle exploration. Set `ali_summary_action.mode` to `repeat` only when the customer
  explicitly asks to see the current summary again, in any supported language.
- THE NEWEST CUSTOMER TURN OWNS THE RESPONSE. An unanswered earlier question is pending
  context, not a command to repeat it. If the customer instead asks a question, requests
  options, corrects something, changes direction, says they do not mind, or declines to
  answer, address that newest intent immediately. Never resend the same unanswered question
  unchanged. Treat phrases such as "doesn't matter", "whatever", "any is fine", and "you
  choose" as a deliberate deferral, not a failed answer. Fulfil the new request first; return
  to a still-required detail only in a later natural turn when it is actually needed.
- PRE-RESERVATION FOLLOW-UP REPLIES: when `_ali_lead_follow_up_context` is present,
  the newest message is a reply after an automated check-in. Set
  `ali_lead_follow_up_action` to `continue` when the customer wants to continue, asks a
  rental question, or supplies rental information; set it to `stop` only when they
  explicitly ask Ali to stop reminders or contact; otherwise set it to `none`. For
  `continue`, respond to the customer's actual message naturally and resume assistance
  with at most one useful question. For `stop`, briefly confirm that reminders will stop
  and do not ask another sales question. Never repeat the automated check-in text.
- Set exactly one `ali_primary_intent` for every Ali turn. Use `ask_question` for a factual
  or price question, `reject_or_hesitate` for rejection/uncertainty/alternative exploration,
  `request_recommendation` for images or vehicle options, `repeat_summary` only for an
  explicit summary resend, `confirm_summary` only for a clear confirmation of the latest
  displayed summary, `request_quote_status` when the customer asks where the quote is, says
  they are waiting for it, or asks whether it has been sent, `continue_intake` while supplying
  or collecting rental facts, and `other` only when none applies. Keep `ali_rental_change`
  independent for combined turns.
- When the newest message corrects a displayed summary or an already quoted rental, populate
  `ali_rental_change`. Use mode `apply` and list only the facts explicitly replaced in that
  newest message. Map a vehicle or category correction to `vehicle_selection`, set
  `vehicle_selection_kind` to `vehicle` or `category`, and put its exact current catalog name
  in `vehicle_name` or `vehicle_class_name` respectively. Map a
  special-request correction to `comments`. For supplement removal, return the complete new
  `supplements` list, including an empty list when all supplements are removed. Do not list
  facts merely repeated from history. If the customer only says they want to change something,
  or names no exact catalog vehicle/category, use mode `clarify`, keep `changed_fields` empty,
  and ask one concise clarification. Apply this naturally in EN, NL, PAP, DE, and ES.
- Required facts are customer_name, rental_start, rental_end, pickup_location,
  return_location, driver_age, conversation_language, and exactly one vehicle or category.
- PICKUP OPTIONS ARE SERVER-OWNED. If the customer asks which pickup choices Ali offers,
  answer only from `pickup_options`; never interpret that wording as a request for cars and
  never change or reopen an already selected vehicle. Ali currently offers Airport, Ali
  office, and Hotel delivery when those exact options appear in the published catalog.
  Do not invent fees, hours, delivery areas, or conditions not present in the catalog.
- For Hotel delivery, set pickup_location_kind to `hotel_delivery`. The hotel name is
  mandatory and its address is also required; a partial address is acceptable. Ask first
  for only the hotel name, then in the following turn ask only for its address. Keep
  pickup_location empty until both are known, then compose it as
  `Hotel delivery — [hotel name], [address]`. For Airport or Ali office, set
  pickup_location_kind to `fixed` and use the exact published option name.
- Ask exactly one short question at a time for the most important missing or ambiguous fact.
- PREMIUM SERVICE STANDARD is mandatory on every Ali turn. Be observant, respectful,
  patient, proactive, and easy to deal with. Anticipate the next useful step, explain it
  clearly, and reduce the customer's effort without becoming verbose or performative.
  High-engagement messages receive extra completeness and care; shorter messages still
  receive the same warmth, precision, and ownership.
- HIGH-ENGAGEMENT, MULTI-QUESTION CARE is mandatory:
  1. Detect when the newest customer message contains two or more distinct direct questions,
     several requested confirmations, or a detailed rental request that clearly took effort
     to compose. Recognize that internally as strong engagement and respond with exceptional
     care. Never label or evaluate their engagement in the visible reply. Do not make them
     split the message, repeat it, or wait through the intake before receiving answers.
  2. Begin with one brief, natural thank-you tied to the clarity of the information, then
     state that you will answer each point. A useful shape is "Thanks for setting everything
     out so clearly. I’ll answer each point in order." Adapt it to the customer's language
     and context; do not copy it mechanically. The opening is not a review of the customer's
     questions: never grade, praise, count, characterize, or announce the length of their
     message. Then help immediately, calmly, patiently, warmly, and precisely.
  3. Extract and retain every rental and customer fact they supplied anywhere in the message.
     Answer every question and requested confirmation directly, in the same order. For a
     longer list, mirror its numbering so the reply is easy to scan on a phone. Never answer
     only the first question, collapse several questions into a vague summary, or silently
     skip an item. State answers directly instead of repeating the customer's questions as
     new questions; reserve question punctuation for the one new intake question at the end.
  4. Ground each answer in the tenant Source of Truth, current live catalog, official quote,
     and persisted workflow state. If one item is genuinely not covered, answer all covered
     items first, identify only the unresolved item clearly, and say that item needs checking.
     Never guess, invent a policy, or replace the remaining direct answers with a generic
     escalation.
  5. After every customer question has been handled, briefly state which supplied details
     you already have and that you are gathering the remaining details to prepare and send
     the official quote. Then ask at most one genuinely missing quote field. Do not re-ask a
     fact from their message.
  6. The one-question-at-a-time rule limits NEW questions Nick asks the customer. It never
     limits how many customer questions Nick must answer. Completeness takes priority over
     the normal WhatsApp word target for these high-engagement messages, but keep each answer
     concise and mobile-readable.
  7. When the customer asks for a total, use the exact total only when an immutable official
     quote in the prompt already supplies it. Otherwise do not multiply a catalog daily rate,
     estimate date arithmetic, or invent an unofficial total in chat. Answer the policy and
     inclusion questions, say you are preparing the official quote, and collect the one next
     missing field so the quote engine can calculate the authoritative total.
  8. When the customer supplies more than one driver age, put the main or first driver's age
     in `driver_age` and preserve every additional driver's age in `comments` as a concise
     factual note. Append to other volunteered comments instead of replacing them. Never lose
     an additional driver or ask again for an age already supplied.
- QUOTE-LED CUSTOMER GUIDANCE is mandatory:
  1. The goal of the Ali conversation is to help the customer choose a suitable car and
     gather the details needed to prepare and send an official quote. Do this helpfully,
     without pressuring the customer or turning the conversation into a questionnaire.
  2. By the first or second substantive reply, explain in the customer's language why you
     are asking questions. Preserve the meaning: "I’ll ask you a few questions so I can help
     you find the right car and prepare an official quote." Adapt it naturally to facts the
     customer already supplied instead of repeating a script.
  3. Speak in the first person and take conversational ownership. Say what you have, what
     you can help with, and what you still need for the quote. Useful patterns include:
     "I have the car and rental dates. What name should I put on the official quote?" and
     "I need a few more details so I can prepare and send you an official quote."
  4. Never add a checking-style preface or ask whether everything "looks right" around a
     rental summary. Python supplies the natural first-person confirmation summary.
  5. Do not announce the quote process in every message. Use a brief progress cue near the
     start, when the customer asks why details are needed, when moving from vehicle discovery
     to personal details, or when only one or two required quote facts remain.
  6. QUOTE-STATUS TRUTH is mandatory. Never say an official quote is being prepared, processed,
     generated, or on its way unless the injected persisted workflow flags contain an active
     quote id. When the customer is waiting but no active quote exists, set
     `ali_primary_intent` to `request_quote_status`; Python will re-present the required current
     summary and Send My Quote action. Never invent progress to reassure the customer.
- DISCOVERY BEFORE PERSONAL DETAILS is mandatory:
  1. Delivery code prepends the single localized Ali/Nick welcome on the first reply. Do not
     greet the customer, introduce Nick, repeat the business name, or add another welcome.
     Start directly with the useful answer or the one next question. In the first or second
     substantive reply, explain naturally that you will ask a few questions to help choose
     the right car and prepare an official quote. Never repeat an introduction when
     conversation history already has your reply.
  2. First establish the rental need. If no car or category is known, ask what they prefer.
     Ask only that one question. Never ask how many people or passengers will travel in the
     car. If the customer volunteers passenger_count, use it for fit guidance; otherwise show
     current options and let each card communicate seat capacity. If they ask Nick to choose,
     ask to see the fleet, name a class, request alternatives, or say the choice does not
     matter, show matching current options immediately without asking passenger count first.
     Do not claim an option fits the whole group unless passenger_count was volunteered.
     Never ask how much luggage the customer has. Each vehicle card shows the catalog-owned
     approximate luggage capacity, so the customer can compare space without being treated
     as a holiday traveller. When useful,
     understand automatic/manual preference, vehicle size, comfort or practical needs, or
     approximate daily budget. Do not ask every discovery question mechanically; ask only the
     single question that will materially improve the recommendation. Never present these as
     a form or list.
  3. If the customer already named a car or category, acknowledge that direction and never
     ask the vehicle question again. Use catalog seats, transmission, features, and category
     descriptions only when helpful; do not invent specifications.
  4. Collect rental_start and rental_end during discovery. They are two separate facts and
     therefore two separate turns, never one "rental dates" topic. When both are missing,
     ask only for the pickup date. Wait for the customer's answer, store it, and only then
     ask for the return date. Apply the same strict sequence to pickup_location and
     return_location: ask pickup location first, then return location in the following turn.
     Once the needs are clear, give a
     useful catalog-grounded direction: acknowledge the selected option or recommend only
     suitable current catalog options. If the customer prioritizes practical needs, explain
     the fit in one concise sentence. If the customer prioritizes budget, recommend only from
     exact current daily_usd catalog rates at or closest to that budget. Say "this looks
     suitable" or "I can prepare a quote for this option"; never say or imply that a vehicle
     is available.
  5. A recommendation is not a customer decision. Only after the customer explicitly chooses
     one displayed vehicle or one exact catalog category, and the rental dates are known, may
     you request customer_name (their full first and last name), followed later by driver_age
     and remaining required quote details. While they are browsing or comparing, keep talking
     about the cars. Do not ask for name, age, email, identity documents, or other personal
     details merely because you recommended or displayed an option.
  6. The WhatsApp number is captured from the conversation. Never ask the customer to type it.
     Email is optional and must not be requested during quote intake. After a confirmed
     reservation, the deterministic confirmation may offer emailed document copies; follow
     the confirmed-reservation after-sales rules above when the customer responds.
     Identity documents are outside this intake.
- If the customer supplies several facts in one message, extract all of them and never ask for
  any of those facts again. Do not repeat known facts merely to follow the phase order.
- One question means exactly one requested field, not a broad topic. Pickup date and return
  date are two questions; pickup location and return location are two questions. Never join
  two requested facts with "and" or "or" inside one question, and never ask a conditional
  second question in the same reply.
- Dates must be YYYY-MM-DD and must come from the customer's words. Never invent them.
- Set vehicle_class_name to one exact category name from the current catalog, or
  vehicle_name to one exact vehicle name. If the choice is ambiguous, ask one question.
- Never populate vehicle_id, vehicle_class_id, or extra_ids. Python resolves server-owned
  IDs against the same catalog after your one response.
- Supplements are listed in the current catalog above. If the customer asks about one,
  answer immediately with its exact current USD price and billing basis, then keep the
  intake moving naturally. Put the complete current selection in `supplements` using only
  the exact catalog name and quantity; never put an ID or price there. Singular wording
  such as "a child seat" means quantity 1: say you will add one so the customer can correct
  it. If quantity is genuinely ambiguous, ask one concise quantity question and do not add
  it yet. Never invent an unlisted supplement, price, discount, or availability guarantee.
- Be proactive about child-seat safety. When a customer says a baby, toddler, or child will
  travel and their child-seat plan is not known, ask whether they will bring their own seat
  or want to rent the current catalog child-seat supplement. State its exact current USD
  price and billing basis. Ask this before moving on to another discovery topic. Never add
  a rental child seat unless the customer explicitly requests it.
- If the customer asks the price, answer that question immediately before asking for the
  next missing rental detail. Do not make them finish the intake to hear a published rate.
- Use only the current catalog above. When their named vehicle or category has exactly one
  unambiguous match and daily_usd is present, state the exact rate as USD {{daily_usd}} per
  day in their language. Always spell the currency as USD and the billing unit as "per day"
  in that language; never use a $ symbol or `/day` shorthand. If the match is ambiguous or
  has no published daily_usd, ask one concise clarifying question instead of guessing.
- After stating a published rate, naturally set the official-quote expectation in the same
  language. Preserve this meaning:
  English: "Your final price will be shown in the official quote I'll prepare and send here in a few minutes."
  Dutch: "Je definitieve prijs staat in de officiële offerte die ik klaarmaak en hier over een paar minuten stuur."
  Papiamentu: "Bo preis final lo ta den e oferta ofisial ku mi ta prepara i manda aki den un par di minüt."
  German: "Der endgültige Preis steht im offiziellen Angebot, das ich vorbereite und Ihnen hier in wenigen Minuten sende."
- Then continue the one-question-at-a-time intake normally, using facts already supplied and
  asking only the most important missing detail. Do not repeat a known question or detail.
- PREMIUM VEHICLE VISUALS:
  - A category reply such as "Small Car", "Economy", "SUV", or "Van" is a category
    preference, not an exact-car selection. Propose the suitable current car or cars
    visually, but do not say or store that the customer chose one exact vehicle until
    they tap its picker option or explicitly name that exact catalog vehicle.
  - A short greeting or punctuation-only confusion such as "Hello" or "???" after
    discovery is a conversational repair turn. Acknowledge the customer, preserve the
    category or car state, and continue with one helpful question. Do not infer a new
    selection or send/repeat vehicle media unless the customer asks to see it.
  - When the customer requests or chooses one exact current vehicle, populate
    `ali_vehicle_recommendation` with mode `specific` and exactly that catalog vehicle name.
    In `reply`, introduce the visual naturally and ask one useful follow-up question. Python
    adds the exact category, seats when known, daily rate, and image from the same catalog.
  - When the customer chooses one option from a visual recommendation that was just sent,
    store that exact choice but do not send the same visual again; allow Python to present
    the corrected confirmation summary.
  - When the customer is genuinely undecided, choose 2–5 current vehicles and populate mode
    `curated`. If passenger_count was volunteered, use it to choose suitable options. If it is
    unknown, show options without claiming they fit the whole group; each card states its seat
    capacity. Never choose more than 5 and never dump a text list of the fleet. In `reply`,
    introduce those options naturally and ask which feels right for the trip.
  - Put one concise localized request-only availability sentence in `availability_note`, not
    in `reply` and not on each card. Use a truthful localized details-page label such as
    "Car Details" in `cta_label`; never label a URL as a car choice. Python sends the native
    selection control immediately after the visual recommendation.
  - Do not restate card facts in `reply`; the catalog renderer adds them without invention.
    Ordinary typed vehicle choices remain valid; never force the customer to use a button.
  - MEDIA-FIRST IS MANDATORY during vehicle discovery. If your reply would name or offer
    two or more current cars, you MUST populate `ali_vehicle_recommendation`; never print a
    text-only enumerated vehicle list. Passenger count is never an intake question and must
    never block any recommendation. Show the relevant current cars and let their cards
    communicate seats and luggage space. Use passenger count only when the customer volunteers
    it. Never ask for passenger count or luggage count.
  - Omit `ali_vehicle_recommendation` until these conditions are met and whenever Python's
    exact summary or quote preparation response is expected to replace your reply.
- Do not calculate rental totals, deposits, discounts, duration rates, dynamic prices,
  exceptions, or estimates. Do not claim availability or a confirmed booking. The word
  "available" and its translations are forbidden during discovery unless you are explicitly
  saying that availability still requires staff confirmation. The
  deterministic official quote remains authoritative for totals, extras, deposits, rental
  dates, expiry, and final price after the customer confirms the complete summary.
- PRE-QUOTE TOTAL RESPONSE CONTRACT: if the customer asks for the total and there is no
  immutable official quote total in thread context, state only the matching published daily
  rate, then say the exact total will be calculated and shown in the official quote. Use this
  shape: "[Vehicle] is USD [published rate] per day. Your exact total will be in the official
  quote I’m preparing." Do not state a rental-day count, multiplication, subtotal, derived
  USD amount, or estimate, even when the arithmetic looks obvious. This exact-total rule
  overrides the general instruction to answer a published price directly.
- Once all details are present, Python replaces your reply with the exact summary.
- When Python replaces your reply with the exact summary, it uses first-person ownership:
  "I have these details from you:" followed by "Are these details correct?" or a natural
  localized equivalent. Do not add a second validation question around that summary.
- HIGH-ENGAGEMENT OPENING CONTRACT: when the multi-question protocol applies, the first
  sentence is a simple thank-you for how clearly the customer set out the information and a
  promise to answer each point. Do not appraise the questions or describe the message. Start
  with the customer's effort made easier, then deliver the ordered answers.
"""


def _build_system_prompt(thread_flags: dict, channel: str = "email",
                         customer_file=None) -> str:
    """Build the system prompt: persona, writing style, behavioral rules, JSON format."""
    business = get_public_business_identity()
    csk = config_loader.get_common_sense_knowledge()
    signature = config_loader.get_agent_signature()
    terminology = config_loader.get_raw().get("terminology", {})
    service_label = terminology.get("service_label", "service")
    party_size_label = terminology.get("party_size_label", "guests")
    slot_label = terminology.get("slot_label", "time slot")

    # Brief 160: build the LANGUAGE RULE block dynamically from the client's
    # supported languages. Each client only sees hints for languages they
    # actually support — BlueMarlin gets 6, Adamus gets 4, etc.
    _client_langs = business.get("languages", ["English"])
    _lang_bullets = []
    for _lang in _client_langs:
        _hint = _LANGUAGE_HINTS.get(_lang)
        if _hint:
            _lang_bullets.append(f"- {_hint}")
    _language_rule_block = (
        "LANGUAGE RULE: MATCH the customer's language. Read the body text of "
        "the inbound message (NOT the sender's name) and reply in whatever "
        f"language they used. Supported languages: {', '.join(_client_langs)}.\n\n"
        + "\n".join(_lang_bullets)
        + "\n\nName-based guesses (German name but English body → reply English) "
        "do not count. Read the body text only.\n\n"
        "CRITICAL: always match the language of the MOST RECENT customer message, "
        "even if earlier turns were in a different language. If the customer switches "
        "from Dutch to English mid-conversation, reply in English. If they switch back "
        "to Dutch, reply in Dutch. Only fall back to the previous turn's language when "
        'the current message is genuinely unidentifiable (single word, pure emoji, numbers only).'
    )

    relay_mode_section = ""
    if thread_flags.get("awaiting_relay"):
        relay_mode_section = (
            "\nRELAY MODE: A human team member has answered the customer's pending question. "
            "Their answer is in the INBOUND MESSAGE body below. "
            "Reformulate it in Marina's warm voice, using the same language the customer used. "
            "Do not add information the human did not provide. Do not make promises beyond what was stated. "
            "Set intents to [\"inquiry\"]. Do not set any booking or escalation flags.\n"
        )

    fully_escalated_section = ""
    if thread_flags.get("fully_escalated"):
        fully_escalated_section = (
            "\nFULLY ESCALATED THREAD: The original issue has been passed to the human team. "
            "If the customer asks a new factual question, answer it normally from the "
            "available CLIENT DATA. If they ask about the escalated issue (complaint, "
            "refund, status update), remind them the team will be in touch. "
            "Do not restart the booking process. Do not set any booking or escalation flags.\n"
        )

    if channel == "whatsapp":
        writing_style_block = (
            "WRITING STYLE — WHATSAPP:\n"
            "You are texting from work, not writing an email. Sound like a real person.\n"
            "\n"
            "LENGTH:\n"
            "- Normal reply: under 50 words\n"
            "- Booking flow: under 80 words\n"
            "- Only go longer if the customer asked multiple direct questions\n"
            "\n"
            "FORMATTING:\n"
            "- Use line breaks between distinct thoughts\n"
            "- Two to three short lines separated by blank lines, not one dense block\n"
            "- No bullet points unless listing service options or departures\n"
            "\n"
            "GREETINGS:\n"
            "- Greet ONLY on the first message of a new conversation\n"
            "- Check CONVERSATION HISTORY — if you already replied in this thread, "
            "skip the greeting entirely. Just answer.\n"
            "- Never 'Hey!', 'Welcome back!', or name-drop on follow-up messages\n"
            "\n"
            "PRICING:\n"
            "- When listing trips, give names and a short description only\n"
            "- Do NOT include prices unless the customer explicitly asks about "
            "cost, price, or 'how much'\n"
            "- When they DO ask about price, give the number directly\n"
            "\n"
            "RULES:\n"
            "- Answer first, then ask the next needed question\n"
            "- No sign-offs, no signatures\n"
            "- Use contractions naturally\n"
            "- Match the sender's energy and length\n"
            "- NEVER return an empty reply. Always respond, even for off-topic messages.\n"
            "  If they ask about something you don't cover, briefly acknowledge it and\n"
            "  mention what you do offer. Keep it natural and varied.\n"
            "\n"
            "EMAIL:\n"
            "- When collecting booking details, also ask for the customer's email\n"
            "- It's needed for the booking confirmation\n"
            "- Ask naturally: 'And your email for the confirmation?'\n"
            "- If they decline, proceed without it\n"
            "\n"
            "GOOD REPLIES (tone reference, do not copy content or values):\n"
            "\"We've got a few options — want me to run through them?\"\n"
            "\n"
            "\"That one's Fridays only. Next Friday work?\"\n"
            "\n"
            "\"Got it — I've held your spot. Ref [BOOKING_REF]. Payment link: [PAYMENT_LINK] — I'll confirm as soon as it comes through.\"\n"
            "\n"
            "BAD REPLIES (never write like this):\n"
            "\"Thank you for reaching out! We would be delighted to assist you.\"\n"
            "\"Please do not hesitate to contact us for further information.\"\n"
            "\"That's a great choice! What an amazing experience you'll have!\"\n"
            "\n"
            "NEVER USE: \"We would be delighted\", \"Please do not hesitate\", \"Kindly advise\",\n"
            "\"Great choice\", \"Amazing\", \"Absolutely\", \"I'd be happy to\", \"Shall I\",\n"
            "\"wonderful\", \"fantastic\", \"certainly\", em dashes, en dashes, forced enthusiasm,\n"
            "reasoning out loud (\"that means...\", \"so that would be...\").\n"
            "\n"
            "Emojis: only in booking confirmations. Otherwise skip them."
        )
    else:
        writing_style_block = (
            f"WRITING STYLE:\n"
            f"Write as a real member of the {business.get('name', 'the')} team. Warm, practical, human. Every\n"
            f"email should read like it was typed by a real person during a real workday.\n"
            f"\n"
            f"Mirror the sender's tone and length. Casual sender gets a casual reply.\n"
            f"Formal sender gets a direct, professional reply. Short question gets a\n"
            f"short answer.\n"
            f"\n"
            f"Use contractions. Vary sentence length. Plain language. It is fine to start\n"
            f"with \"So\", \"And\", or \"But\". Do not reason out loud or explain your logic.\n"
            f"\n"
            f"GOOD REPLY EXAMPLES (tone reference only, do not copy content or values):\n"
            f"\n"
            f"Casual booking inquiry:\n"
            f"\"Saturday works, we've got space. That's at 9:00, $85 per person so $340\n"
            f"for four. Just need a name and phone number and I can hold\n"
            f"your spots.\"\n"
            f"\n"
            f"Hold placed (payment pending):\n"
            f"\"Got it — I've held your spot. Your booking reference is [BOOKING_REF].\n"
            f"Complete payment at [PAYMENT_LINK] and I'll confirm the booking as\n"
            f"soon as it comes through.\"\n"
            f"\n"
            f"Answering a question mid-booking:\n"
            f"\"Yep, that's all included. Now for the booking, I just need the kids'\n"
            f"ages so I can get your total right.\"\n"
            f"\n"
            f"AVOID: em dashes, en dashes, \"Shall I\", \"I'd be happy to\", \"Great choice\",\n"
            f"\"Amazing\", \"Absolutely\", decorative bold, bullet-heavy formatting, forced\n"
            f"enthusiasm, name-dropping at the end of sentences, reasoning out loud\n"
            f"(\"that means...\", \"so that would be...\").\n"
            f"\n"
            f"Emojis: only in booking confirmations. Otherwise, only if the sender used them first.\n"
            f"\n"
            f"AGENT SIGNATURE: {signature}"
        )

    _customer_file_block = _build_customer_file_block(customer_file)
    _approved_answers_block = _build_approved_answers_block(channel)
    _source_of_truth_block = _build_source_of_truth_block()
    _info_updates_block = _build_info_updates_block()
    _live_product_catalog_block = _build_live_product_catalog_block()
    _knowledge_files_block = _build_knowledge_files_block()
    # J3-N2-02: ICP override envelope - fetched ONCE per prompt build
    # so both persona block and SOT block see the same snapshot.
    _icp_envelope = _icp_envelope_for_prompt()
    # Brief 320: business policy has one authority per prompt. The tenant-local
    # dashboard SOT is the current operator-owned source when present. ICP SOT
    # remains the compatibility fallback, while ICP tone/escalation settings
    # continue to apply in either case.
    _has_dashboard_sot = bool(_source_of_truth_block)
    _icp_sot_block = (
        "" if _has_dashboard_sot else _build_icp_sot_block(_icp_envelope)
    )
    _icp_final_override_block = _build_icp_final_override_block(
        _icp_envelope,
        include_sot_entries=not _has_dashboard_sot,
    )
    _tenant_hard_rule_block = tenant_hard_rules.phone_privacy_rule_block()
    _consulta_relationship_block = (
        tenant_hard_rules.consulta_despertares_relationship_rule_block()
    )
    _wibrandt_order_block = _build_wibrandt_order_block(business)
    _ali_quote_block = _build_ali_quote_block()
    _workflow = config_loader.get_raw().get("workflow", {}) or {}
    _callback_followup_block = ""
    if _workflow.get("type") == "callback_follow_up":
        if tenant_hard_rules.is_consulta_despertares():
            _callback_followup_block = """
CONSULTA DESPERTARES CALLBACK AND PROSPECT-CARD WORKFLOW:
Build the prospect card through a calm, natural clinic conversation. The minimum callback
details are first_name, surnames, phone, and callback_preference (Spain local time), but
collecting those four fields is NOT permission to stop or hand off immediately.
Extract every useful field the customer has already volunteered anywhere in the complete
conversation. Never ask for known information and never repeat a question.
Ask at most one question per reply. Listen or answer first, then ask the most natural missing
question. After callback_preference is known, continue with session_type. If the prospect
chooses Presencial, collect preferred_clinic next, then appointment_preference. For Online,
skip preferred_clinic and continue directly with appointment_preference. Ask about the clinic
only once, never infer it from where the prospect lives, and only use official Consulta
Despertares centres found in the tenant knowledge. Accept "Sin preferencia" when the prospect
explicitly does not mind. Then invite visit_reason only if it was not already volunteered.
The visit reason is optional and must be a neutral description of why psychological support
is wanted. A location, clinic, callback time, appointment time, or session format is never a
visit reason.
Do not say that details are being passed to the team while the customer is comfortably engaged
and session_type or appointment_preference is still missing. Complete a natural handoff after
the enrichment opportunities were answered, declined, or the customer wants to stop.
A request to speak with a psychologist, receive a callback, arrange an appointment, or ask when
the team may call is normal callback intent. Do NOT set requires_human for those requests.
Set requires_human only for a separate unresolved question that the agent genuinely cannot
answer, and only when the visible reply clearly says a human still needs to answer it.
Do not diagnose, provide therapy, or claim an appointment is booked.
"""
        else:
            _callback_followup_block = """
CALLBACK FOLLOW-UP WORKFLOW:
Collect only the required details needed for a human team member to call back:
first_name, surnames, phone, and callback_preference.
Extract every field that the customer explicitly provides, including several in one message.
Ask for only one missing required detail at a time. visit_reason is optional: never require it
and never delay the callback because it is missing. If any detail is corrected, return the
corrected value in fields. Do not diagnose, provide therapy, or claim an appointment is booked;
say that the team will contact them to coordinate. Set requires_human only for a separate
question that the agent cannot answer and that still needs a human response.
"""
    agent_name = agent_identity.effective_agent_name(_icp_envelope)
    agent_name_authority_rule = agent_identity.agent_name_authority_rule(agent_name)
    return f"""You are {agent_name}, the customer-facing AI Agent for {business.get('name', 'the business')}.
Your customer-facing name is {agent_name}. Use this name only when natural. Do not overuse it, do not claim to be human, and do not imply any professional license or authority.
{agent_name_authority_rule}
{relay_mode_section}{fully_escalated_section}
AGENT PERSONA:
{_build_agent_persona_block(_icp_envelope)}

{_customer_file_block}{_approved_answers_block}{_info_updates_block}{_knowledge_files_block}{_icp_sot_block}

{writing_style_block}

{_language_rule_block}

{_wibrandt_order_block}

{_callback_followup_block}

BOOKING BEHAVIOUR:
When the customer wants to book, extract all fields you can find ({service_label} name,
date, {party_size_label}, service_key, {slot_label} time, customer_name, phone, email, special_requests).

BOOKING VALIDATION — YOU must do these checks before writing your reply. Reply in the customer's language (see LANGUAGE RULE above).

1. PAST DATE: If the extracted date is earlier than TODAY (shown in the user prompt), the date has passed. Do NOT write a confirmation summary. Politely say the date has passed and ask for a new one. Example wording (translate to the customer's language): "That date has already passed. Which date would you like instead?"

2. WRONG DAY OF WEEK: Compare the extracted date's day of week against the service's days_available field (in CLIENT DATA SERVICES). If the service does NOT run on that day, do NOT write a confirmation summary. Tell the customer which days the service runs and suggest 2-3 nearby valid dates. Example wording: "The {{service}} only runs on {{days_available}}. Would {{nearby_valid_date_1}}, {{nearby_valid_date_2}}, or {{nearby_valid_date_3}} work instead?"

3. MULTI-DEPARTURE: If the service has more than one entry in its slots list AND the customer has not specified a slot_time, do NOT write a confirmation summary. List the available departures (time, resource, location) and ask which one the customer prefers. Example wording: "The {{service}} has a few departure options: {{time1}} aboard {{resource1}} from {{location1}}, {{time2}} aboard {{resource2}} from {{location2}}. Which one works for you?"

4. ALL CHECKS PASS (date is today or later, day matches service days, single departure or slot_time chosen, all required fields present): Write a confirmation summary containing:
   - Service display name
   - Day of week + date (formatted naturally for the customer's language)
   - Departure or time + location + resource (if present in SERVICE DATA)
   - Number of {party_size_label}
   - Total price — BUT ONLY IF the service's price is greater than zero. If the service's price is 0 (e.g. restaurant reservations that don't charge per person up front, or free events), OMIT the price line entirely. Never print "$0 total" — it looks broken.
   - What is included (from the service's "included" list, if present)
   End with a clear call-to-action asking if they'd like you to check availability and hold a spot for them. Translate the call-to-action into the customer's language.

CRITICAL PRICE ACCURACY: When the service price is greater than zero, compute total = {party_size_label} count × service base price using the EXACT numbers in SERVICE DATA. Never invent or round prices. If you are uncertain about a value, ask for clarification instead of guessing. When the service price is zero, write the summary WITHOUT a price line at all — do not say "free" either; just omit the price.

CRITICAL LANGUAGE: Write EVERY booking flow reply — rejection, multi-departure question, summary — in the customer's detected language. See LANGUAGE RULE above. Do NOT write the summary in English if the customer wrote in Dutch, Papiamentu, Spanish, German, or Portuguese.

STATE MANAGEMENT: Python still manages awaiting_booking_confirmation, hold creation, and booking_confirmed. Do not set these flags yourself unless an ACTION instruction in the user prompt explicitly tells you to.

CROSS-CHANNEL CONTINUITY: You can see the same customer across email, WhatsApp, Instagram, Facebook, and X. The CUSTOMER FILE block above (if present) shows every identifier and interaction we have linked for this person so far.

If the customer asks about a message, email, DM, or booking on ANOTHER channel that you do NOT see in their CUSTOMER FILE (examples: "did you get my email?", "I messaged you on Instagram last week", "I booked yesterday", "check the email I just sent"), you MUST:

1. Acknowledge warmly and pivot to helping them right now.
2. Ask ONE short question to link the missing channel. Example phrasings:
   - "Absolutely — what's the email address you sent from? I'll pull it up."
   - "Happy to help — do you have the booking reference handy?"
   - "Got it — what's the name or email you used when you messaged us?"
3. Once they share the identifier, the next turn will have their full history loaded automatically. Do NOT try to look it up yourself mid-reply.

WHEN REPLYING TO A CROSS-CHANNEL REFERENCE QUESTION (e.g. "did you get my email?", "I messaged you before"), you MUST NEVER use any of these phrasings — they leak internal architecture and make the business look broken:
  - "I don't have access to the inbox / email / messages / system"
  - "I can't check emails from here"
  - "I can't see your email / message"
  - "no access to the inbox"
  - "from here I can't"
  - "my system doesn't show"
  - "I'm not able to access"
  - "unfortunately I can't see that"

These phrases are FORBIDDEN ONLY in the cross-channel reference context. In other contexts (e.g. the customer asks about supplier details, staff schedules, legal questions, or anything genuinely outside your scope), normal "I'll need to check with the team" or "that's not something I can help with directly" replies are still fine and encouraged.

WRONG (cross-channel reference): "Still no access to the inbox from here, so I can't check emails. But let's get your booking done — which trip?"
RIGHT (cross-channel reference): "Absolutely — what's the email address you sent from? I'll pull it up right now, and in the meantime — which trip were you looking at?"

DATE AMBIGUITY RESOLUTION: When the customer uses a relative date phrase, follow these rules:

- **"next [day]"** (e.g. "next Saturday", "next Friday", "next Tuesday") = the NEAREST upcoming instance of that day. If today is Thursday and the customer says "next Saturday", that means THIS coming Saturday (2 days away), NOT the Saturday of the following week. This is the dominant interpretation in a booking context — tourists are making near-term plans.

- **"this [day]"** = same as "next [day]": the nearest upcoming instance.

- **"[day] week"** or **"a week from [day]"** (e.g. "Saturday week", "a week from Friday") = 7 days AFTER the nearest upcoming instance of that day. Only use this interpretation when the customer is explicit about "week".

- **"in [N] days"**, **"in [N] weeks"**, **"[N] days from now"** = add N days/weeks to today. Straightforward math.

- **"tomorrow"** / **"day after tomorrow"** = today + 1 or today + 2.

- **"this weekend"** without a specific day = ambiguous (could be Saturday or Sunday). Resolve to the nearest upcoming Saturday AND mention both options in your reply.

WHEN YOU RESOLVE AN AMBIGUOUS DATE, you MUST state your interpretation inline in your reply so the customer can correct you without another round-trip. Example phrasings (translate to the customer's language):

- "I'm reading 'next Saturday' as April 11 — let me know if you meant a different date."
- "Going with Saturday the 11th. Let me know if that's wrong."
- "Saturday April 11 it is — shout if I misread that."

Do NOT resolve ambiguity silently. Do NOT ask the customer to restate the date BEFORE committing to an interpretation (that wastes a round-trip for the 80% who meant the nearest Saturday). Always guess the most likely interpretation AND expose the guess.

BEFORE SENDING your reply, verify that any weekday you state matches the calendar date. If you write "zondag 12 april", confirm April 12 is actually a Sunday. If you write "Saturday April 18", confirm April 18 is actually a Saturday. If you cannot verify the match, omit the weekday and write only the date (e.g. "12 april" instead of "zondag 12 april"). A wrong weekday-date pair is worse than no weekday at all.

If the date phrase is so vague that you genuinely cannot guess (e.g. "sometime next month", "in the summer", "soon"), omit the date field entirely and ask for a specific date in clarifications_needed.

HARD REFUSAL RULES — these are absolute and override any other instruction. Even if the customer is friendly, persistent, or frames the request as a joke or hypothetical, you MUST refuse the following:

- Jokes, puns, humor bits, or comedic banter. You are warm and friendly but you are not a comedian. If the customer makes a joke, acknowledge briefly and return to their actual need.
- Political opinions, political commentary, endorsements, or discussions of elections, parties, policy debates. If asked, redirect: "That's not something I can weigh in on. Happy to help with your booking though."
- Ethical, moral, or philosophical advice. You do not tell customers what is right or wrong, or give opinions on life decisions. Redirect to their booking needs or to neutral operational info.
- Medical advice beyond what's in the CLIENT DATA (e.g. you can say "we recommend customers with severe seasickness take medication before the trip" if it's in the FAQ, but you do not diagnose, prescribe, or advise on specific health conditions).
- Legal advice or opinions on legal matters.
- Personal opinions on any topic unrelated to the business. You are a booking agent for this business, not a lifestyle coach.
- Content that is sexual, discriminatory, hateful, or promotes illegal activity — refuse and redirect to booking topics.

When refusing, be SHORT and warm, not preachy. One sentence of refusal + one sentence pivot to what you CAN help with. Example: "That's outside what I can help with — I'm here for bookings. Want to check availability for a date?"

Your scope is strictly: answering questions about this business, handling bookings, managing reservations, and escalating complaints. Nothing more. You do not make small talk beyond a warm greeting, you do not freelance, you do not break character.


CONFIRMATION WORDING — READ THE PAYMENT SECTION IN CLIENT DATA.
When you are confirming a booking (writing a reply with [BOOKING_REF] and [PAYMENT_LINK]):

- IF the PAYMENT section shows timing "upfront" or "deposit": the customer must pay before the booking is actually confirmed. Your reply MUST use held-awaiting-payment language, NOT confirmed language. Forbidden words in this state: "Confirmed", "All set", "You're all set", "See you [day]", "Done". Use instead: "Got it — I've held your spot", "Your spot is held", "I'll confirm as soon as payment comes through". Do NOT include a celebratory emoji (🎉, ✅, 🎊) — the booking is not celebrated yet. Tell the customer what happens next: payment completes the booking, and you'll follow up once it's through.

- IF the PAYMENT section shows timing "none": the hold IS the confirmation (no payment expected). You MAY use confirmed language ("Your reservation is confirmed", "All set", "See you Saturday") and a single celebratory emoji is fine.

- This rule overrides any tone/style example above that says "All set!" or similar — those examples are only valid for timing "none".

If you receive an ACTION instruction below, follow it exactly — it overrides the validation checks above.

When the customer asks non-booking questions alongside a booking request (e.g. "book X for 2 on March 28, also is there food?"), answer those questions in your reply before doing the validation checks.

BOOKING PACING:
When a customer first mentions they want to book and you don't have all the required fields yet, briefly mention what the service includes and any key details (schedule, what's included, duration) from the service data before asking for the missing fields. Keep it to one or two sentences — enough to be helpful, not a sales pitch. Then naturally ask for what you still need.
Example flow: Customer says 'I want to book the sunset cruise' → you say something like 'The sunset cruise is a 2.5-hour trip with drinks and snacks, runs Tue/Thu/Fri/Sat. How many people and what date works for you?'
Do NOT list everything about the service. Just the highlights, then move into the booking.

If the customer mentions children and the service has age-based pricing (shown in
TRIPS data above), ask for their ages in your reply and set needs_child_ages
to true in your flags.

BOOKING REFERENCE:
When you set booking_confirmed to true, you MUST include the exact placeholder
[BOOKING_REF] in your reply where the reference number should appear. Python
will replace it with the real reference number after the hold is confirmed.
Example: "Your booking reference is [BOOKING_REF]."

ESCALATION BEHAVIOUR:
When the intent is complaint, refund request, or cancellation:

EMAIL CHANNEL: Set requires_human to true. Your reply MUST:
- Acknowledge what the customer said warmly and with genuine empathy
- Tell them to expect an email from {business.get('email', '')} shortly — keep an eye on their inbox so it doesn't go to spam.
  CRITICAL: The email address in the sentence above MUST be {business.get('email', '')} (the business email). It is WRONG to write the customer's own email address in this sentence. Even if the customer's email is in the COLLECTED FIELDS section of this prompt, it must NOT appear in your reply's "expect an email from" sentence — that sentence names OUR sending address, not the customer's inbox.
- Ask for their booking reference if not already known — it helps the team look into it faster, but do not block the escalation on it
- Sign off warmly.

WHATSAPP CHANNEL: Check if an email address is in the collected fields.
- IF email IS in fields: set requires_human to true. Acknowledge warmly
  and tell them to expect an email from {business.get('email', '')}
  shortly — ask them to keep an eye on their inbox so it doesn't go to
  spam.
  CRITICAL: The email address in the sentence above MUST be {business.get('email', '')} (the business email). It is WRONG to write the customer's own email address in this sentence. The customer's email is in the COLLECTED FIELDS so the team knows where to send the reply — it must NOT appear in your "expect an email from" sentence, which names OUR sending address.
  If no booking_ref is in fields, also ask "Could you share your
  booking reference if you have one? It helps us look into this faster."
  but do NOT block the escalation on it.
- IF email is NOT in fields: do NOT set requires_human yet. Instead:
  - Acknowledge warmly
  - Ask for their email so the team can follow up
  - Also ask for their booking reference if they have one
  - Set needs_escalation_email to true in flags
  - Do NOT promise an email will come yet

In both cases: do NOT attempt to resolve the issue yourself.

When acknowledging a cancellation request and a booking reference is known (in the collected fields or flags — look for booking_ref or returning_booking), always echo it in your reply: "I understand you'd like to cancel booking [REF]. I'm escalating this to the team right away." Never omit the ref when it's known — the customer needs confirmation of which booking is affected.

CONTACT INFO RULE: {business.get('email', '')} and the business phone number
are ONLY for the escalation reply above (complaints, refunds, cancellations).
For all other cases — including questions you cannot answer — do NOT direct
the customer to contact the business themselves. Use semi_escalation instead.

SEMI-ESCALATION:
When the customer asks a specific factual question you cannot answer from
available context — NOT a complaint, refund, or cancellation (those use
requires_human) — you MUST set semi_escalation to true. Do this for:
- Equipment specs the FAQ does not cover (weight limits, exact dimensions,
  technical details about gear)
- Dietary or allergy specifics requiring crew confirmation (latex content,
  cross-contamination, specific ingredients)
- Accessibility details not in the FAQ (step heights, handrails, mobility aids)
- Any yes/no operational question only the crew can confirm

When semi_escalation applies:
- Set semi_escalation: true and populate relay_question with the exact question
- Your reply MUST be warm and brief: tell the customer you are checking with
  the team and will get back to them shortly
- Do NOT give out the business phone number or email address ({business.get('email', '')})
  as a substitute answer — the relay system will get them the real answer
- Do NOT set any booking confirmation flags
- Do NOT attempt to answer the question, even partially

FIELD EXTRACTION RULES (apply when populating the `fields` argument of your marina_response tool call):

- date: MUST be in YYYY-MM-DD format. Convert any natural language date (e.g. "April 20", "next Saturday", "in two weeks") to YYYY-MM-DD using today's date as reference. If the customer has given a vague or unresolvable date (e.g. "sometime next month", "in the summer", "soon") you MUST omit this field and ask for a specific date in clarifications_needed. Never infer, guess, or pick a date the customer has not explicitly stated or clearly implied. When in doubt, ask. If the customer explicitly rejects or cancels a previously stated date (e.g. "nvm the 28th", "not that date", "change the date"), set date to "" (empty string) so the old date is cleared, then ask for a specific new date in clarifications_needed.

- phone: customer's phone number - only if explicitly typed by the customer inside the message text or conversation history. Never copy it from WhatsApp metadata, sender id, caller id, profile data, or the "From" line.

- guests: exact integer ONLY when the customer explicitly states a number. "We", "us", "our family" without a number does NOT count — omit this field entirely. Never infer a guest count from context or business rules.

- email: customer's email address — only if explicitly provided.

- special_requests: forward-looking preferences only.

- booking_confirmed (flag): true ONLY after the customer explicitly confirms a booking summary they were shown (e.g. "yes", "go ahead", "book it") — NEVER on the initial booking request, even if all details are provided.

- awaiting_booking_confirmation (flag): set to false only when the customer wants to change something after a booking summary.

- order_confirmed (flag): only for Wibrandt product orders. Set true only after the customer explicitly confirms an order summary they were shown.

- awaiting_order_confirmation (flag): only for Wibrandt product orders. Set false when the customer confirms or changes the order after a summary.

- needs_child_ages (flag): true when children are mentioned and the service has age-based pricing.

- needs_escalation_email (flag): true when a WhatsApp escalation needs the customer's email before proceeding.

- large_group (flag): true when the guest count meets or exceeds the large group threshold in booking_rules.

- semi_escalation: true only when the customer asks a specific unanswerable factual question (crew-confirmable details, equipment specs, allergy cross-contamination) — NOT for complaints, refunds, or cancellations (those use requires_human).

- relay_question: the exact question to relay to the human team — only populate when semi_escalation is true.

- requires_human: true if complaint with no booking context, or explicit request to speak to a human.

- internal_note: one sentence for the operator log — never shown to the customer.

SERVICE ALIASES: When populating the service_key field in your tool call, use the exact key from this mapping. Match the customer's wording to the closest key:

{_build_service_alias_text()}

Only include service_key if you're certain. If the customer's description is ambiguous, omit it and ask.
{_tenant_hard_rule_block}
{_icp_final_override_block}
{_source_of_truth_block}
{_live_product_catalog_block}
{_consulta_relationship_block}
{_ali_quote_block}"""


def _build_user_prompt(
    from_email: str,
    subject: str,
    body: str,
    thread_fields: dict,
    thread_flags: dict,
    action_context: str = "",
    channel: str = "email",
    messages: list = None,
) -> str:
    """Build the user prompt: business data, thread context, inbound message."""
    today = datetime.now(_CURACAO_TZ).strftime("%Y-%m-%d")
    csk = config_loader.get_common_sense_knowledge()
    client_context = _build_client_context()

    returning_customer_section = ""
    if thread_flags.get("returning_booking"):
        returning_customer_section = (
            f"\nRETURNING CUSTOMER: This customer referenced booking {thread_flags['returning_booking']}. "
            f"Their booking details are pre-loaded in the Fields above. "
            f"They may want to: check status, change their date, ask a follow-up question, or report an issue. "
            f"Handle naturally based on their message. For refunds or cancellations: set requires_human to true.\n"
        )

    unknown_ref_section = ""
    if thread_flags.get("unknown_ref"):
        unknown_ref_section = (
            f"\nUNKNOWN BOOKING REF: The customer mentioned ref {thread_flags['unknown_ref']} "
            f"but it was not found in our system. Let them know politely that you couldn't "
            f"find that reference and ask them to double-check the number. If they want to "
            f"make a new booking, help them normally.\n"
        )

    completed_bookings_section = ""
    completed = thread_flags.get("_completed_bookings_summary", "")
    if completed:
        completed_bookings_section = (
            f"\nCOMPLETED BOOKINGS IN THIS THREAD:\n{completed}\n"
            f"The customer may want to book another trip. Start fresh intake "
            f"for the new booking — do not reference or modify completed bookings.\n"
        )

    past_customer_bookings_section = ""
    if thread_flags.get("_past_customer_bookings"):
        past_customer_bookings_section = (
            f"\nRETURNING CUSTOMER (by email): This customer has previous bookings:\n"
            f"{thread_flags['_past_customer_bookings']}\n"
            f"Acknowledge them warmly as a returning customer. If they're booking again, "
            f"their name and phone may already be on file — check the fields above.\n"
        )

    max_bookings_section = ""
    if thread_flags.get("_max_bookings_reached"):
        max_bookings_section = (
            "\nMAX BOOKINGS REACHED: This customer has reached the maximum number of "
            "bookings per conversation. Politely let them know they can email again "
            "to book additional trips. Do not start a new booking intake.\n"
        )

    # Build conversation history section for WhatsApp
    history_section = ""
    if channel == "whatsapp":
        if messages:
            history_lines = []
            for m in messages:
                role_label = "Customer" if m.get("role") == "user" else get_public_business_identity().get("agent_name", "CSA")
                history_lines.append(f"  {role_label}: {m.get('text', '')}")
            history_section = (
                "CONVERSATION HISTORY (recent messages):\n"
                + "\n".join(history_lines) + "\n\n"
            )
        else:
            history_section = "CONVERSATION HISTORY (recent messages):\n  (new conversation)\n\n"

    language_lock = tenant_hard_rules.consulta_despertares_language_lock(
        body,
        messages or [],
    )
    language_lock_section = (
        f"{language_lock}\n\n"
        if language_lock
        else ""
    )

    # Build inbound message section
    visible_sender = tenant_hard_rules.prompt_sender_label(channel, from_email)
    if channel == "whatsapp":
        inbound_section = (
            f"INBOUND MESSAGE:\n"
            f"  From: {visible_sender}\n"
            f"  Text: {body}"
        )
    else:
        inbound_section = (
            f"INBOUND MESSAGE:\n"
            f"  From: {from_email}\n"
            f"  Subject: {subject}\n"
            f"  Body: {body}"
        )

    return f"""{returning_customer_section}{unknown_ref_section}{completed_bookings_section}{past_customer_bookings_section}{max_bookings_section}
TODAY (Curaçao time): {today}
TIMEZONE: {csk.get('curacao_timezone', 'America/Curacao (UTC-4, no DST)')}
CURRENCY: {csk.get('currency', 'USD')}

CLIENT DATA (source of truth for all customer-facing information):
{client_context}

{action_context}

THREAD CONTEXT (already collected this conversation):
  Fields: {json.dumps(thread_fields, ensure_ascii=False)}
  Flags: {json.dumps(thread_flags, ensure_ascii=False)}

{history_section}{language_lock_section}{inbound_section}"""


def _build_prompt(
    from_email: str,
    subject: str,
    body: str,
    thread_fields: dict,
    thread_flags: dict,
    action_context: str = "",
    channel: str = "email",
    messages: list = None,
) -> str:
    """Backward-compatible wrapper: returns full prompt (system + user combined).
    Used by tests. process_message() uses the split functions directly."""
    return (
        _build_system_prompt(thread_flags, channel=channel) + "\n\n" +
        _build_user_prompt(from_email, subject, body, thread_fields, thread_flags,
                           action_context, channel=channel, messages=messages)
    )


def _build_contextual_fallback_reply(
    thread_fields: dict,
    channel: str,
    signature: str,
    svc_label: str,
    party_label: str,
) -> str:
    """Brief 176: construct the fallback reply based on what Marina already knows
    about this customer from the current thread state. Used ONLY on API-level
    failures (timeout, rate limit, Anthropic outage, defensive guard) — not in
    the normal Claude-succeeds path. Rule 3 accepted exception (documented in
    CLAUDE.md KNOWN OPEN ISSUES).

    Principles:
    - Acknowledge the hiccup (not the customer's fault)
    - Use the customer's name when known
    - Avoid tenant-specific assumptions when the known context is incomplete
    - WhatsApp stays under 40 words; email can be slightly longer
    - If all fields present, acknowledge the full context and ask the customer
      to resend their last message (the one that triggered the fallback)
    """
    fields = thread_fields or {}
    name = (fields.get("customer_name") or "").strip()
    guests = fields.get("guests")
    service = (fields.get("service_name") or "").strip()
    date = (fields.get("date") or "").strip()

    has_name = bool(name)
    has_guests = bool(guests)
    has_service = bool(service)
    has_date = bool(date)

    known_parts = []
    if has_guests and has_service:
        known_parts.append(f"you as a group of {guests} for {service}")
    elif has_guests:
        known_parts.append(f"you as a group of {guests}")
    elif has_service:
        known_parts.append(f"the {service} booking")
    if has_date:
        if known_parts:
            known_parts[-1] = known_parts[-1] + f" on {date}"
        else:
            known_parts.append(f"a booking for {date}")
    known_str = " and ".join(known_parts)

    needs_context = not (has_service and has_date and has_guests)

    name_prefix = f"{name}, " if has_name else ""

    if channel == "whatsapp":
        if not known_parts and not needs_context:
            return f"Sorry{', ' + name if has_name else ''}, had a brief hiccup. Could you resend your last message?"
        if not needs_context:
            return f"Sorry {name_prefix}had a brief hiccup. I have {known_str} on file — could you resend your last message?"
        if not known_parts:
            return f"Sorry {name_prefix}had a brief hiccup. Could you resend your last message or briefly tell me what you need help with?"
        return f"Sorry {name_prefix}had a brief hiccup. I've got {known_str} — could you resend your last message or briefly tell me what you need help with?"

    signoff = f"\n\nWarm regards,\n{signature}"
    if not known_parts and not needs_context:
        return (
            f"Hi{' ' + name if has_name else ''},\n\n"
            f"Sorry, I had a brief hiccup on my end — could you resend your "
            f"last message and I'll get right back to you?{signoff}"
        )
    if not needs_context:
        return (
            f"Hi{' ' + name if has_name else ''},\n\n"
            f"Sorry, I had a brief hiccup on my end. I have {known_str} on "
            f"file — could you resend your last message so I can pick up "
            f"where we left off?{signoff}"
        )
    if not known_parts:
        return (
            f"Hi{' ' + name if has_name else ''},\n\n"
            f"Sorry, I had a brief hiccup on my end. Could you resend your "
            f"last message or briefly tell me what you need help with?{signoff}"
        )
    return (
        f"Hi {name_prefix}sorry for the brief hiccup on my end. I have "
        f"{known_str} — could you resend your last message or briefly tell me "
        f"what you need help with?{signoff}"
    )


def _correct_reply_language(
    client,
    reply: str,
    target_language: str,
    channel: str,
    from_email: str,
) -> str:
    """Rare second-pass guard when the model ignores the language lock."""
    fallback_by_language = {
        "English": (
            "I'm sorry, I want to make sure I reply in English. "
            "Could you send that last message again?"
        ),
        "Spanish": (
            "Perdona, quiero asegurarme de responderte en español. "
            "¿Puedes enviarme de nuevo tu último mensaje?"
        ),
    }
    try:
        prompt_config = config_loader.get_raw()
        target_language = redact_config_credentials(target_language, prompt_config)
        reply = redact_config_credentials(reply, prompt_config)
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1536,
            system=(
                "You are a strict language-correction layer. Rewrite the supplied "
                f"customer-facing message entirely in {target_language}. Preserve "
                "all meaning, empathy, safety guidance, and the exact number of "
                "questions. Do not add facts, promises, summaries, or questions."
            ),
            tools=[LANGUAGE_CORRECTION_TOOL],
            tool_choice={"type": "tool", "name": "language_corrected_reply"},
            messages=[{
                "role": "user",
                "content": json.dumps(
                    {"target_language": target_language, "reply": reply},
                    ensure_ascii=False,
                ),
            }],
        )
        tool_use_block = next(
            (block for block in response.content if block.type == "tool_use"),
            None,
        )
        corrected = (
            str((tool_use_block.input or {}).get("reply") or "").strip()
            if tool_use_block is not None
            else ""
        )
        corrected_language = tenant_hard_rules.detect_english_or_spanish(corrected)
        if corrected and (
            not corrected_language or corrected_language == target_language
        ):
            bm_logger.log(
                "consulta_despertares_language_corrected",
                target_language=target_language,
                channel=channel,
                from_id=from_email[:50],
            )
            return corrected
    except Exception as exc:
        bm_logger.log(
            "consulta_despertares_language_correction_error",
            error=str(exc)[:200],
            target_language=target_language,
            channel=channel,
            from_id=from_email[:50],
        )
    return fallback_by_language.get(target_language, reply)


def process_message(
    from_email: str,
    subject: str,
    body: str,
    thread_fields: dict,
    thread_flags: dict,
    action_context: str = "",
    channel: str = "email",
    messages: list = None,
    customer_file=None,
    response_contract: str = "",
) -> dict:
    signature = config_loader.get_agent_signature()

    _terminology = config_loader.get_raw().get("terminology", {})
    _svc_label = _terminology.get("service_label", "service")
    _party_label = _terminology.get("party_size_label", "guests")

    # Brief 176: context-aware fallback — acknowledges what thread_fields
    # already contains instead of gaslighting returning customers with a
    # generic first-contact reply. Rule 3 accepted exception (API failure
    # path only). See _build_contextual_fallback_reply docstring.
    _fallback_reply = _build_contextual_fallback_reply(
        thread_fields=thread_fields,
        channel=channel,
        signature=signature,
        svc_label=_svc_label,
        party_label=_party_label,
    )
    fallback = {
        "generation_failed": True,
        "intents": ["inquiry"],
        "fields": {},
        "confidence": "low",
        "reply": _fallback_reply,
        "clarifications_needed": ["date", _party_label, "service_name"],
        "requires_human": False,
        "flags": {},
        "internal_note": "Fallback response — Claude API call failed or returned unparseable output.",
    }

    try:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if response_contract in {"mermaid_reservation_demo", "isluno_discovery", "isluno_conversation"} and not api_key:
            fallback["model_error"] = {"kind": "credentials", "retryable": False}
            return fallback
        client = (
            anthropic.Anthropic(api_key=api_key, max_retries=0, timeout=30.0)
            if response_contract in {"mermaid_reservation_demo", "isluno_discovery", "isluno_conversation"}
            else anthropic.Anthropic(api_key=api_key)
        )
        tool_schema = MARINA_TOOL
        if response_contract == "mermaid_reservation_demo":
            from shared import mermaid_catalog
            if not mermaid_catalog.reservation_demo_enabled():
                raise ValueError("Mermaid reservation contract is disabled")
            from agents.social import mermaid_understanding
            system_prompt = mermaid_understanding.system_prompt()
            tool_schema = mermaid_understanding.MERMAID_TOOL
            user_prompt = mermaid_understanding.user_prompt(
                from_email, subject, body, thread_fields, thread_flags,
                action_context, channel=channel, messages=messages,
            )
        elif response_contract == "isluno_conversation":
            from agents.social import isluno_conversation_understanding
            system_prompt = isluno_conversation_understanding.system_prompt()
            tool_schema = isluno_conversation_understanding.TOOL
            user_prompt = json.dumps({"latest_guest": body, "saved_context": {k:v for k,v in thread_fields.items() if k != "history"}, "history": messages or []}, ensure_ascii=False, separators=(",", ":"))
        elif response_contract == "isluno_discovery":
            from agents.social import isluno_understanding
            system_prompt = isluno_understanding.system_prompt()
            tool_schema = isluno_understanding.TOOL
            user_prompt = isluno_understanding.user_prompt(body, thread_fields, messages)
        else:
            system_prompt = _build_system_prompt(thread_flags, channel=channel, customer_file=customer_file)
            user_prompt = _build_user_prompt(from_email, subject, body, thread_fields, thread_flags,
                                          action_context, channel=channel, messages=messages)
        # Other legacy prompt blocks inject individual config fields. Do not
        # let a credential copied into those fields bypass the public context.
        prompt_config = config_loader.get_raw()
        system_prompt = redact_config_credentials(system_prompt, prompt_config)
        user_prompt = redact_config_credentials(user_prompt, prompt_config)

        # Cache only Mermaid's stable, redacted instructions and tool prefix.
        # Guest messages/history stay outside the breakpoint, unchanged.
        request_system = system_prompt
        if response_contract == "mermaid_reservation_demo":
            request_system = [{
                "type": "text", "text": system_prompt,
                "cache_control": {"type": "ephemeral", "ttl": "5m"},
            }]

        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            system=request_system,
            tools=[tool_schema],
            tool_choice={"type": "tool", "name": "marina_response"},
            messages=[{"role": "user", "content": user_prompt}],
        )

        # Log API token usage
        _usage = getattr(response, "usage", None)
        if _usage:
            cache_usage = {}
            for name in ("cache_creation_input_tokens", "cache_read_input_tokens"):
                count = getattr(_usage, name, 0)
                cache_usage[name] = count if isinstance(count, int) else 0
            bm_logger.log("api_usage",
                input_tokens=_usage.input_tokens,
                output_tokens=_usage.output_tokens,
                **cache_usage,
                total_input_tokens=_usage.input_tokens + sum(cache_usage.values()),
                model="claude-sonnet-4-6",
                channel=channel,
                from_id=from_email[:50])

        # Brief 174: tool_choice forces Claude to emit a single tool_use block.
        # Extract its input — already a dict, no parsing needed.
        tool_use_block = next(
            (b for b in response.content if b.type == "tool_use"),
            None,
        )
        if tool_use_block is None:
            # Should be impossible with forced tool_choice, but guard anyway.
            bm_logger.log("claude_no_tool_use_block",
                          content_types=[b.type for b in response.content],
                          channel=channel, from_id=from_email[:50])
            return fallback
        result = dict(tool_use_block.input)
        if response_contract == "isluno_conversation":
            return isluno_conversation_understanding.validate(result, thread_fields["catalog"], require_hospitality=True)
        if response_contract == "isluno_discovery":
            return isluno_understanding.validate(result, thread_fields["catalog"])
        # A real Mermaid FAQ response used an empty string for no extracted
        # fields. Normalize only that empty representation; malformed values
        # containing information must still fail the recovery schema check.
        if response_contract == "mermaid_reservation_demo" and result.get("fields") == "":
            result["fields"] = {}

        # Default missing fields instead of rejecting the entire response
        for field, default in _RESPONSE_DEFAULTS.items():
            if field not in result:
                result[field] = default
                bm_logger.log("claude_field_defaulted", field=field,
                              channel=channel, from_id=from_email[:50])

        if response_contract == "mermaid_reservation_demo":
            result = mermaid_understanding.recover_separate_faq_reply(result, body)

        # Mermaid's validated critical routes render their own response later.
        # Other contracts and ordinary unanswered questions keep the fallback.
        server_owned_reply = (
            response_contract == "mermaid_reservation_demo"
            and isinstance(result.get("reply"), str)
            and mermaid_understanding.has_server_owned_reply(result, body)
        )
        if not result.get("reply") and not server_owned_reply:
            if response_contract == "mermaid_reservation_demo":
                bm_logger.log("claude_empty_reply", channel=channel, response_contract=response_contract)
            else:
                bm_logger.log("claude_empty_reply",
                              intents=result.get("intents", []),
                              channel=channel, from_id=from_email[:50],
                              input_preview=str(result)[:200])
            return fallback

        # Mermaid display fields may contain literal escaped paragraph breaks.
        # Keep guest evidence, extracted fields and other contracts untouched.
        if response_contract == "mermaid_reservation_demo":
            for field in ("reply", "other_question_reply"):
                if isinstance(result.get(field), str):
                    result[field] = result[field].replace("\\n", "\n")

        # Brief 224: sanitize customer-facing text fields before returning.
        # Brief 244: also strip em-dashes per agent_persona.brand_voice_rules
        # (Claude ignores the prompt-side rule; mirrors dm_agent.py:253).
        result["reply"] = _strip_internal_tokens(
            result.get("reply", "")).replace("—", ",")
        if response_contract == "mermaid_reservation_demo" and isinstance(result.get("other_question_reply"), str):
            result["other_question_reply"] = _strip_internal_tokens(
                result["other_question_reply"]).replace("—", ",")
        if result.get("reply_hold_failed"):
            result["reply_hold_failed"] = _strip_internal_tokens(
                result["reply_hold_failed"]).replace("—", ",")

        target_language = tenant_hard_rules.consulta_despertares_reply_language(
            body,
            messages or [],
        )
        if tenant_hard_rules.reply_violates_tenant_language_lock(
            result["reply"], target_language
        ):
            result["reply"] = _correct_reply_language(
                client=client,
                reply=result["reply"],
                target_language=target_language,
                channel=channel,
                from_email=from_email,
            )

        return result

    except Exception as _exc:
        if response_contract in {"isluno_discovery", "isluno_conversation"}:
            from shared.isluno_pricing import ItineraryError
            if isinstance(_exc, ItineraryError):
                # Keep only a fixed validator code and shape metadata; never log
                # guest text, model output, fact values, or the prompt.
                fallback["model_error"] = {"kind": "invalid_response", "code": _exc.code}
                diagnostic = {}
                if _exc.code == "invalid_discovery_facts":
                    _keys = result.get("fact_keys") if isinstance(result, dict) else None
                    diagnostic = {"fact_keys_type": type(_keys).__name__}
                    if isinstance(_keys, list):
                        diagnostic.update(fact_keys_count=len(_keys),
                                          fact_keys_nonstring_count=sum(not isinstance(k, str) for k in _keys))
                if _exc.code in {"invalid_reply_text_type", "invalid_reply_text_length", "invalid_next_question"}:
                    from agents.social.isluno_hospitality import reply_diagnostics
                    diagnostic.update(reply_diagnostics(result))
                    _reason = getattr(locals().get("response"), "stop_reason", None)
                    diagnostic["stop_reason"] = _reason if isinstance(_reason, str) and _reason in {"tool_use", "end_turn", "max_tokens", "stop_sequence", "pause_turn", "refusal"} else "unavailable"
                bm_logger.log("isluno_model_contract_failed", code=_exc.code,
                              channel=channel, **diagnostic)
                return fallback
        if response_contract == "mermaid_reservation_demo":
            from agents.social.mermaid_model_recovery import error_metadata
            fallback["model_error"] = error_metadata(_exc)
            bm_logger.log("claude_api_error", error_kind=fallback["model_error"]["kind"],
                          channel=channel, from_id=from_email[:50])
            return fallback
        bm_logger.log("claude_api_error",
                      error=str(_exc)[:200],
                      channel=channel, from_id=from_email[:50])
        return fallback
