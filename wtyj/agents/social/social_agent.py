# bluemarlin/agents/social/social_agent.py
# Created: Brief 068
# Last modified: Brief 100
# Purpose: WhatsApp booking orchestrator with escalation — calls marina_agent, validates, holds, confirms, escalates

import random
import re
import string
import time
import json
import uuid
import os
import urllib.parse
from dataclasses import replace
from datetime import datetime, timezone, timedelta
from shared import appointment_detector
from shared import state_registry
from shared import auto_block
from shared import bm_logger
from shared import config_loader
from shared import tenant_hard_rules
from agents.marina import marina_agent
from agents.marina import gws_calendar
from agents.marina import payment_stub
from agents.marina import sheets_writer
from agents.social.ali_quote_workflow import (
    apply_latest_rental_change,
    apply_recommendation_selection_context,
    confirmation_decision,
    build_quote_confirmation_control,
    QUOTE_CHANGE_PROMPTS,
    QUOTE_CHANGE_VALUE_PROMPTS,
    fail_closed_turn_plan,
    get_intake_catalog as get_ali_intake_catalog,
    invalidate_active_quote_summary,
    infer_relative_rental_end_change,
    hotel_delivery_completion_reply,
    log_rental_change_decision,
    next_intake_question as next_ali_intake_question,
    plan_repeated_quote_confirmation,
    plan_ali_quote_turn,
    resolve_quote_confirmation_interaction,
    sanitize_intake_reply as sanitize_ali_intake_reply,
    tenant_configured as ali_quote_tenant_configured,
    tenant_enabled as ali_quote_tenant_enabled,
    VEHICLE_STATE_FIELDS,
)
from agents.social.ali_vehicle_recommendations import (
    AliVehicleRecommendationError,
    build_vehicle_picker_recovery,
    build_vehicle_recommendation,
)
from agents.social.ali_media_first import (
    catalog_class_recommendation_action,
    conversation_repair_reply,
    derive_media_first_action,
    enforce_vehicle_first_reply,
    explicit_catalog_browse_request,
    explicit_no_preference_request,
    explicit_larger_vehicle_request,
    explicit_smaller_vehicle_request,
    explicit_visual_request,
    explicit_hotel_delivery_choice,
    hotel_delivery_detail_prompt,
    infer_explicit_catalog_class_selection,
    infer_media_first_intent,
    add_first_turn_welcome,
    media_first_clarification,
    rental_location_options_reply,
    rental_location_request_kind,
    resolve_fixed_pickup_option_choice,
    proactive_child_seat_offer,
)
from agents.social.ali_vehicle_selection import (
    AliVehicleSelectionError,
    invalid_vehicle_selection_reply,
    resolve_typed_vehicle_selection,
    resolve_vehicle_selection,
)
from agents.social.ali_reservation_workflow import (
    AliReservationError,
    get_quote_context as get_ali_quote_context,
    get_reservation_context as get_ali_reservation_context,
    handle_exact_reserve as handle_ali_exact_reserve,
    handle_post_quote_action as handle_ali_post_quote_action,
    is_exact_reserve_fallback as is_ali_exact_reserve_fallback,
    resolve_post_quote_interaction as resolve_ali_post_quote_interaction,
)
from agents.social import ali_customer_dossier
from agents.social import ali_lead_follow_up


_BOOKING_INTENTS = {"booking", "reschedule"}
_ORDER_INTENTS = {"order"}

_BOOKING_FLAGS_TO_RESET = {
    "hold_created", "booking_confirmed", "booking_ref", "hold_id",
    "payment_id", "payment_link", "payment_status",
    "event_id", "event_link",
    "slot_checked", "slot_available", "spots_remaining", "trip_capacity",
    "awaiting_booking_confirmation",
    "hold_service_key", "hold_date", "hold_slot_time",
    "awaiting_escalation_email", "needs_escalation_email",
    "awaiting_order_confirmation", "order_confirmed",
    "waiting_for_human_order_confirmation", "order_escalation_id",
}

_PERSISTENT_FIELDS = {"customer_name", "phone", "email"}

# Consulta Despertares' goal is progressive intake, not a one-day transaction.
# These facts are explicitly provided by the prospect and must remain available
# if they return after WhatsApp's 24-hour free-text window has closed.
_CONSULTA_PERSISTENT_INTAKE_FIELDS = _PERSISTENT_FIELDS | {
    "first_name",
    "surnames",
    "phone_raw",
    "callback_preference",
    "appointment_preference",
    "session_type",
    "visit_reason",
}

_ORDER_DRAFT_FIELDS_TO_RESET = {
    "products", "product_name", "quantity", "delivery_address", "address",
    "order_total", "unit_price", "subtotal", "comments", "special_requests",
}

_ORDER_DRAFT_FLAGS_TO_RESET = {
    "awaiting_order_confirmation", "order_confirmed",
    "waiting_for_human_order_confirmation", "order_escalation_id",
    "fully_escalated",
}

_MAX_REPLIES_PER_HOUR = 50
_REPLY_WINDOW_SECONDS = 3600
_STALE_CONVERSATION_SECONDS = 86400  # 24 hours — matches wa_get_history window

_MEDIA_TRIGGER_WORDS = {
    "photo", "photos", "picture", "pictures", "image", "images", "pic",
    "show", "see", "look", "looks", "info", "information", "details",
    "available", "sell", "menu", "cookie", "cookies", "cake", "cakes",
    "pastry", "pastries", "twist", "bread", "product", "products",
    "cinnamon", "cinamon", "cardamom", "pecan", "banana", "tosca",
    "house", "home", "apartment", "villa", "property", "properties",
    "room", "rooms", "view", "bedroom", "bedrooms",
}

_MEDIA_STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "what", "which", "have",
    "has", "can", "you", "your", "our", "are", "about", "info", "please",
    "hello", "hi", "hey", "price", "prices", "ang", "xcg", "eur", "usd",
    "image", "images", "photo", "photos", "picture", "pictures", "show",
    "details", "information", "product", "products", "available",
}


def _valid_callback_phone(value: str) -> tuple[str, str]:
    """Return the original and normalized phone only when it is callable.

    Zernio conversation ids can be 24-character hexadecimal strings. They
    must never satisfy the callback phone requirement.
    """
    raw = str(value or "").strip()
    if not raw or not re.fullmatch(r"[+0-9().\s-]+", raw):
        return "", ""
    normalized = state_registry.normalize_phone_identifier(raw)
    if not 9 <= len(normalized) <= 15:
        return "", ""
    return raw, normalized


def _callback_name_fields(fields: dict) -> tuple[str, str]:
    first_name = str(fields.get("first_name") or "").strip()
    surnames = str(fields.get("surnames") or "").strip()
    if first_name and surnames:
        return first_name, surnames
    full_name = str(fields.get("customer_name") or "").strip()
    parts = full_name.split(maxsplit=1)
    if len(parts) == 2:
        return first_name or parts[0], surnames or parts[1]
    return first_name, surnames


_CALLBACK_AUTOMATED_STATUSES = {
    "collecting", "ready_to_call", "needs_human_answer",
}


def _callback_visit_reason(fields: dict) -> str:
    """Return only explicitly extracted clinical context for the card."""
    return str(fields.get("visit_reason") or "").strip()


def _callback_follow_up_target_status(
    follow_up: dict,
    result: dict,
) -> str:
    """Recalculate the live callback status after every agent turn.

    Operator-controlled outcomes are never overwritten. Automated statuses may
    move in either direction so an old needs-human flag cannot remain after the
    agent has answered, and a card is not marked ready while the agent is still
    asking one natural intake question.
    """
    current = str(follow_up.get("status") or "collecting")
    if current not in _CALLBACK_AUTOMATED_STATUSES:
        return current

    if result.get("requires_human"):
        return "needs_human_answer"

    required = ("first_name", "surnames", "phone_raw", "callback_preference")
    if not all(str(follow_up.get(key) or "").strip() for key in required):
        return "collecting"

    reply = str(result.get("reply") or "")
    if "?" in reply or "¿" in reply:
        return "collecting"
    return "ready_to_call"

_WIBRANDT_PRODUCT_CATALOG = (
    {
        "name": "The Tosca Twist",
        "unit_price": 5,
        "aliases": ("the tosca twist", "tosca twist"),
    },
    {
        "name": "Cinnamon Cardamom Twist",
        "unit_price": 5,
        "aliases": (
            "cinnamon cardamom twist",
            "cinnamon cardamom twists",
            "cinnamon twist",
            "cinnamon twists",
            "cinnamons",
            "cinnamon",
        ),
    },
    {
        "name": "White Chocolate Pecan Cookie",
        "unit_price": 6,
        "aliases": (
            "white chocolate pecan cookie",
            "white chocolate pecan cookies",
            "white chocolate cookie",
            "white chocolate cookies",
            "white chocolate",
        ),
    },
    {
        "name": "The Tosca Cookie",
        "unit_price": 6,
        "aliases": ("the tosca cookie", "tosca cookie", "tosca cookies"),
    },
    {
        "name": "The Banana Carrot Pecan Crunch",
        "unit_price": 7,
        "aliases": (
            "the banana carrot pecan crunch",
            "banana carrot pecan crunch",
            "banana pecan crunch",
            "banana",
        ),
    },
)


def _day_matches(day_name, days_available):
    """Check if day_name matches the service's days_available string."""
    if days_available.lower() == "daily":
        return True
    return day_name.lower() in days_available.lower()


def _build_action_context(flags):
    """Build action_context string for the Claude prompt based on flags."""
    if flags.get("awaiting_order_confirmation"):
        return (
            "ACTION: An order summary was sent and the customer was asked "
            "whether everything looks correct. The customer is replying now. "
            "If they confirm with yes, perfect, looks good, let's do it, or "
            "similar, set order_confirmed: true and "
            "awaiting_order_confirmation: false. Reply with this exact message: "
            "\"Perfect 💛 We've received your order.\n\n"
            "We'll give you a call shortly to confirm the details and delivery.\n\n"
            "Thank you for choosing Wibrandt.\" "
            "If they change something, extract the changed fields, set "
            "awaiting_order_confirmation: false, and continue the order flow. "
            "If unclear, ask one short clarification question. Do NOT set "
            "booking_confirmed and do NOT create a booking summary."
        )
    if flags.get("awaiting_booking_confirmation"):
        return (
            "ACTION: A booking summary was sent and the customer was asked if they "
            "want you to check availability. The customer is replying. "
            "Determine if they are: (a) confirming — set booking_confirmed: true, "
            "awaiting_booking_confirmation: false, write a warm confirmation reply "
            "with the exact string [PAYMENT_LINK] where the payment link goes. "
            "Also write reply_hold_failed — an apologetic message if the slot turns "
            "out to be unavailable, without [PAYMENT_LINK]; "
            "(b) changing something — extract new fields, set "
            "awaiting_booking_confirmation: false; "
            "(c) unclear — ask for clarification; "
            "(d) declining or saying no — set awaiting_booking_confirmation: false, "
            "use intent 'inquiry' (not 'booking'), acknowledge gracefully and ask "
            "if they'd like to look at something else. "
            "Do NOT generate a new booking summary."
        )
    return ""


def _tenant_slug() -> str:
    raw = config_loader.get_raw() or {}
    business = raw.get("business") or {}
    return str(raw.get("tenant_slug") or raw.get("slug") or business.get("slug") or "").strip().lower()


def _public_media_url(filename: str) -> str:
    slug = _tenant_slug()
    safe_name = os.path.basename(str(filename or ""))
    if not slug or not safe_name:
        return ""
    base = os.environ.get("PUBLIC_API_BASE_URL", "https://api.unboks.org").rstrip("/")
    return (
        f"{base}/api/{urllib.parse.quote(slug)}/dashboard/api/public/media/"
        f"{urllib.parse.quote(safe_name)}"
    )


def _media_tokens(text: str) -> set[str]:
    tokens = set()
    for token in re.findall(r"[a-z0-9]+", str(text or "").lower()):
        if token == "cinamon":
            token = "cinnamon"
        if len(token) < 3 or token in _MEDIA_STOPWORDS:
            continue
        tokens.add(token)
    return tokens


def _photo_text(photo: dict) -> str:
    tags = photo.get("tags") if isinstance(photo, dict) else []
    if not isinstance(tags, list):
        tags = []
    parts = [
        photo.get("filename", ""),
        photo.get("original_filename", ""),
        photo.get("service_key", ""),
        photo.get("source", ""),
        photo.get("source_id", ""),
        " ".join(str(t) for t in tags),
    ]
    return " ".join(str(p) for p in parts if p)


def _is_broad_media_query(text: str) -> bool:
    lower = str(text or "").lower()
    broad_phrases = (
        "what else", "what do you have", "what have you got", "what you have",
        "menu", "options", "products", "show me everything", "anything else",
    )
    return any(phrase in lower for phrase in broad_phrases)


def _select_customer_media(text: str, reply_text: str, fields: dict, flags: dict,
                           history: list[dict] | None = None) -> dict | None:
    """Return the best matching tenant media item for a sales/info reply.

    The media library is generic: bakeries upload products, real-estate
    tenants upload properties, and other tenants can upload any customer-facing
    item. We score against image captions/tags and only attach one relevant
    image per reply so the agent helps sell without spamming the customer.
    """
    history_text = " ".join(
        str(msg.get("text") or "")
        for msg in (history or [])[-6:]
        if str(msg.get("role") or "") in {"user", "assistant"}
    )
    customer_text = text or ""
    product_context_text = " ".join([
        reply_text or "",
        fields.get("product_name", ""),
        fields.get("service_name", ""),
        fields.get("property_name", ""),
    ])
    current_text = " ".join([customer_text, product_context_text])
    query_text = " ".join([current_text, history_text])
    lower_query = query_text.lower()
    if not any(word in lower_query for word in _MEDIA_TRIGGER_WORDS):
        return None

    current_tokens = _media_tokens(current_text)
    history_tokens = _media_tokens(history_text)
    query_tokens = current_tokens | history_tokens
    if not query_tokens:
        return None

    try:
        photos = state_registry.get_photos(limit=200)
    except Exception as exc:
        bm_logger.log("customer_media_lookup_failed", error=str(exc)[:200])
        return None

    best: tuple[int, dict] | None = None
    candidates: list[tuple[int, dict]] = []
    last_sent_id = str(flags.get("last_media_id_sent") or "")
    lower_current = current_text.lower()
    explicit_media_request = any(
        word in lower_current for word in ("photo", "photos", "picture", "pictures", "image", "images", "show", "see", "look")
    )

    for photo in photos:
        filename = str(photo.get("filename") or "")
        if not filename:
            continue
        media_text = _photo_text(photo)
        media_tokens = _media_tokens(media_text)
        current_overlap = current_tokens & media_tokens
        history_overlap = history_tokens & media_tokens
        score = len(current_overlap) * 5 + len(history_overlap)
        source_id = str(photo.get("source_id") or "").replace("-", " ").lower()
        service_key = str(photo.get("service_key") or "").replace("-", " ").lower()
        if source_id and source_id in lower_current:
            score += 15
        elif source_id and source_id in lower_query:
            score += 3
        if service_key and service_key in lower_current:
            score += 10
        elif service_key and service_key in lower_query:
            score += 2
        tags = photo.get("tags") if isinstance(photo.get("tags"), list) else []
        title = str(tags[0] if tags else "").lower()
        title_tokens = _media_tokens(title)
        current_title_overlap = current_tokens & title_tokens
        history_title_overlap = history_tokens & title_tokens
        score += len(current_title_overlap) * 6 + len(history_title_overlap)

        # If the current turn names a different product, old history should not
        # keep attaching the previous product image.
        if current_tokens and not (current_overlap or current_title_overlap):
            score -= 4

        if score < 2:
            continue
        if str(photo.get("id")) == last_sent_id and not explicit_media_request:
            continue
        candidates.append((score, photo))
        if best is None or score > best[0]:
            best = (score, photo)

    if best is None:
        return None
    broad_customer_query = _is_broad_media_query(customer_text)
    if broad_customer_query:
        sorted_candidates = sorted(candidates, key=lambda item: item[0], reverse=True)
        if len(sorted_candidates) >= 2 and sorted_candidates[1][0] >= 2:
            bm_logger.log(
                "customer_media_skipped_broad_multi_product",
                top_score=sorted_candidates[0][0],
                second_score=sorted_candidates[1][0],
            )
            return None
    photo = dict(best[1])
    url = _public_media_url(photo.get("filename", ""))
    if not url:
        return None
    tags = photo.get("tags") if isinstance(photo.get("tags"), list) else []
    caption = str(tags[0]) if tags else str(photo.get("original_filename") or photo.get("filename") or "Image")
    return {
        "id": str(photo.get("id")),
        "url": url,
        "caption": caption,
        "filename": str(photo.get("filename") or ""),
        "score": best[0],
    }


def _strip_media_fallback_links(reply_text: str) -> str:
    """Remove Instagram/Facebook fallback copy when an actual image is attached."""
    paragraphs = re.split(r"\n\s*\n", str(reply_text or "").strip())
    kept = []
    for paragraph in paragraphs:
        lower = paragraph.lower()
        if "instagram.com" in lower or "facebook.com" in lower:
            continue
        if "more photos on our instagram" in lower:
            continue
        if "find more photos" in lower and "instagram" in lower:
            continue
        kept.append(paragraph.strip())
    return "\n\n".join(p for p in kept if p).strip() or str(reply_text or "").strip()


def _is_wibrandt_order_tenant():
    """Keep product-order orchestration scoped to Wibrandt."""
    raw = config_loader.get_raw() or {}
    business = raw.get("business") or {}
    slug = str(raw.get("tenant_slug") or raw.get("slug") or business.get("slug") or "").lower()
    name = str(business.get("name") or "").lower()
    return slug == "wibrandt" or name == "wibrandt"


def _coerce_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_product_name(value):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", str(value or "").lower())).strip()


def _wibrandt_catalog_match(product_name):
    normalized = _normalize_product_name(product_name)
    if not normalized:
        return None
    for product in _WIBRANDT_PRODUCT_CATALOG:
        for alias in product["aliases"]:
            if _normalize_product_name(alias) in normalized:
                return product
    return None


def _apply_wibrandt_catalog_pricing(fields):
    """Fill deterministic Wibrandt product prices when Marina extracts names only."""
    if not _is_wibrandt_order_tenant() or not isinstance(fields, dict):
        return False

    changed = False
    products = fields.get("products") or []
    if isinstance(products, list):
        for item in products:
            if not isinstance(item, dict):
                continue
            match = _wibrandt_catalog_match(item.get("name"))
            if not match:
                continue
            qty = _coerce_int(item.get("quantity"), _order_quantity(fields) or 1) or 1
            item["name"] = match["name"]
            item["quantity"] = qty
            item["unit_price"] = match["unit_price"]
            item["subtotal"] = qty * match["unit_price"]
            changed = True
    elif products:
        fields["products"] = []

    if not fields.get("products"):
        match = _wibrandt_catalog_match(fields.get("product_name") or fields.get("service_name"))
        if match:
            qty = _order_quantity(fields) or 1
            fields["product_name"] = match["name"]
            fields["quantity"] = qty
            fields["unit_price"] = match["unit_price"]
            fields["subtotal"] = qty * match["unit_price"]
            changed = True

    lines = _order_lines(fields)
    priced_lines = [line for line in lines if line.get("subtotal") is not None]
    if lines and len(priced_lines) == len(lines):
        total = sum(line["subtotal"] for line in priced_lines)
        fields["order_total"] = total
        fields["currency"] = "XCG"
        changed = True
    elif changed:
        fields["currency"] = "XCG"

    return changed


def _order_quantity(fields):
    return _coerce_int(fields.get("quantity") or fields.get("guests"), 0)


def _order_address(fields):
    return (fields.get("delivery_address") or fields.get("address") or "").strip()


def _order_lines(fields):
    products = fields.get("products") or []
    lines = []
    if isinstance(products, list):
        for item in products:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            qty = _coerce_int(item.get("quantity"), _order_quantity(fields) or 1)
            unit_price = _coerce_float(item.get("unit_price"))
            subtotal = _coerce_float(item.get("subtotal"))
            if subtotal is None and unit_price is not None:
                subtotal = unit_price * qty
            lines.append({
                "name": name,
                "quantity": qty,
                "unit_price": unit_price,
                "subtotal": subtotal,
            })
    if not lines:
        name = (fields.get("product_name") or fields.get("service_name") or "").strip()
        if name:
            qty = _order_quantity(fields) or 1
            unit_price = _coerce_float(fields.get("unit_price"))
            subtotal = _coerce_float(fields.get("subtotal"))
            if subtotal is None and unit_price is not None:
                subtotal = unit_price * qty
            lines.append({
                "name": name,
                "quantity": qty,
                "unit_price": unit_price,
                "subtotal": subtotal,
            })
    return lines


def _has_order_required_fields(fields):
    lines = _order_lines(fields)
    return bool(
        lines
        and _order_address(fields)
        and all(_coerce_int(line.get("quantity"), 0) > 0 for line in lines)
    )


def _reset_wibrandt_order_draft(fields, flags, escalation_id=None):
    """Clear the active Wibrandt order draft after it has been snapshotted."""
    for key in _ORDER_DRAFT_FIELDS_TO_RESET:
        fields.pop(key, None)
    for key in _ORDER_DRAFT_FLAGS_TO_RESET:
        flags.pop(key, None)
    if escalation_id is not None:
        flags["last_order_escalation_id"] = escalation_id


def _is_wibrandt_order_like(result, fields, flags):
    if not _is_wibrandt_order_tenant():
        return False
    intents = set(result.get("intents") or [])
    if intents & _ORDER_INTENTS:
        return True
    if flags.get("awaiting_order_confirmation") or flags.get("order_confirmed"):
        return True
    if any(fields.get(k) for k in ("products", "product_name", "quantity",
                                   "delivery_address", "order_total")):
        return True
    return False


def _is_wibrandt_order_related_text(*parts) -> bool:
    """Detect Wibrandt order/customer-order follow-up text, not appointments."""
    if not _is_wibrandt_order_tenant():
        return False
    text = "\n".join(str(p or "") for p in parts).lower()
    if not text.strip():
        return False
    order_terms = (
        "order", "pedido", "bestelling", "delivery", "deliver",
        "address", "total", "payment", "cash", "quantity", "qty",
        "cookie", "cookies", "twist", "pecan", "banana carrot",
        "cinnamon", "tosca", "cancel my order", "reduce her order",
        "order summary",
    )
    return any(term in text for term in order_terms)


def _money(value, currency):
    amount = _coerce_float(value)
    if amount is None:
        return "(not calculated)"
    if float(amount).is_integer():
        display = str(int(amount))
    else:
        display = f"{amount:.2f}"
    return f"{currency} {display}".strip()


def _product_settings():
    try:
        settings = config_loader.get_product_settings() or {}
    except Exception:
        return {}
    return settings if isinstance(settings, dict) else {}


def _configured_delivery_cost():
    settings = _product_settings()
    amount = _coerce_float(settings.get("delivery_cost_amount"))
    if amount is None:
        return None
    currency = str(settings.get("delivery_cost_currency") or "").strip().upper()
    if not currency:
        currency = "XCG" if _is_wibrandt_order_tenant() else ""
    return {"amount": amount, "currency": currency or "XCG"}


def _wibrandt_order_has_complete_pricing(fields):
    lines = _order_lines(fields)
    return bool(lines) and all(line.get("subtotal") is not None for line in lines)


def _wibrandt_order_product_total(fields):
    total = _coerce_float(fields.get("order_total") or fields.get("total"))
    if total is not None:
        return total
    subtotals = [line.get("subtotal") for line in _order_lines(fields) if line.get("subtotal") is not None]
    return sum(subtotals) if subtotals else None


def _wibrandt_order_total(fields):
    product_total = _wibrandt_order_product_total(fields)
    if product_total is None:
        return None
    delivery = _configured_delivery_cost()
    if delivery is None:
        return product_total
    return product_total + delivery["amount"]


def _looks_like_price_question(text):
    normalized = str(text or "").lower()
    return any(term in normalized for term in (
        "price", "total", "pay", "payment", "how much", "cost",
        "amount", "including delivery", "delivery fee",
    ))


def _reply_defers_known_price(reply_text):
    normalized = str(reply_text or "").lower()
    defers = ("checking", "team will confirm", "exact total", "exact pricing",
              "get back to you", "pricing ready")
    money_terms = ("price", "total", "payment", "pay", "cost")
    return any(term in normalized for term in defers) and any(term in normalized for term in money_terms)


def _build_wibrandt_order_summary_reply(fields):
    delivery = _configured_delivery_cost()
    currency = (
        (delivery or {}).get("currency")
        or fields.get("currency")
        or "XCG"
    )
    lines = _order_lines(fields)
    line_text = []
    for line in lines:
        qty = line.get("quantity") or 1
        name = line.get("name") or "Item"
        unit_price = _money(line.get("unit_price"), currency)
        subtotal = _money(line.get("subtotal"), currency)
        line_text.append(f"• {qty} x {name} — {unit_price} each — {subtotal}")

    product_total = _wibrandt_order_product_total(fields)
    final_total = _wibrandt_order_total(fields)
    parts = [
        "Here is your order summary:",
        "",
        *line_text,
        "",
        f"Product total: {_money(product_total, currency)}",
    ]
    if delivery is not None:
        parts.append(f"Delivery cost: {_money(delivery['amount'], currency)}")
        parts.append(f"Total: {_money(final_total, currency)}")
    address = _order_address(fields)
    phone = fields.get("phone")
    comments = fields.get("comments") or fields.get("special_requests")
    if address:
        parts.extend(["", f"📍 {address}"])
    if phone:
        parts.append(f"☎️ {phone}")
    if comments:
        parts.append(f"💬 {comments}")
    if delivery is None:
        parts.extend([
            "",
            "Delivery fee is not configured yet, so this total is for the products only. "
            "The team will confirm any delivery fee when they call.",
        ])
    parts.extend(["", "Does everything look correct?"])
    return "\n".join(parts)


def _create_wibrandt_order_escalation(channel, phone, channel_label, fields,
                                      flags, from_name, history, result):
    cname = fields.get("customer_name") or from_name or "Unknown"
    customer_phone = fields.get("phone") or phone
    delivery = _configured_delivery_cost()
    currency = (delivery or {}).get("currency") or fields.get("currency") or "XCG"
    lines = _order_lines(fields)
    product_total = _wibrandt_order_product_total(fields)
    total = _wibrandt_order_total(fields)
    order_payload = {
        "type": "ORDER",
        "state": "WAITING_FOR_HUMAN_ORDER_CONFIRMATION",
        "customer_name": cname,
        "phone": customer_phone,
        "products": lines,
        "delivery_address": _order_address(fields),
        "product_total": product_total,
        "delivery_cost": delivery["amount"] if delivery else None,
        "total": total,
        "currency": currency,
        "comments": fields.get("comments") or fields.get("special_requests") or "",
        "channel": channel,
        "customer_id": phone,
    }
    product_summary = ", ".join(
        f"{line.get('quantity') or 1}x {line.get('name')}" for line in lines
    ) or "order"
    subject = f"[ORDER] {cname} ({channel_label}: {phone}) - {product_summary}"
    chat_lines = []
    for msg in (history or []):
        role = str(msg.get("role", "?")).upper()
        chat_lines.append(f"[{role} | {msg.get('created_at', '')}]")
        chat_lines.append(msg.get("text", ""))
        chat_lines.append("---")
    body = (
        "=== ORDER ===\n"
        "Status: WAITING_FOR_HUMAN_ORDER_CONFIRMATION\n"
        f"Customer: {cname}\n"
        f"Phone: {customer_phone}\n"
        f"Channel: {channel_label}\n"
        f"Delivery address: {_order_address(fields) or '(not provided)'}\n"
        f"Comments: {order_payload['comments'] or '(none)'}\n"
        f"Total: {_money(total, currency)}\n\n"
        "=== PRODUCTS ===\n"
        + "\n".join(
            f"- {line.get('quantity') or 1} x {line.get('name')} "
            f"| unit: {_money(line.get('unit_price'), currency)} "
            f"| subtotal: {_money(line.get('subtotal'), currency)}"
            for line in lines
        )
        + "\n\n=== ORDER PAYLOAD ===\n"
        + json.dumps(order_payload, indent=2, ensure_ascii=False)
        + "\n\n=== CHAT LOG ===\n"
        + ("\n".join(chat_lines) or "(no messages logged)")
        + "\n\n=== HELGA INTERNAL NOTE ===\n"
        + (result.get("internal_note") or "Customer confirmed the order summary.")
    )
    escalation_id = state_registry.create_pending_notification(
        'escalation', channel, phone, cname, subject, body, mode="order")
    _reset_wibrandt_order_draft(fields, flags, escalation_id)
    state_registry.wa_store_message(
        phone, "system", "ORDER escalation created; waiting for human order confirmation")
    sheets_writer.log_escalation({
        "email": phone,
        "subject": channel_label,
        "customer_name": cname,
        "intent": "order",
        "fields_collected": order_payload,
        "internal_note": "Customer confirmed order summary; operator must call to confirm.",
        "messages_json": json.dumps(history, ensure_ascii=False) if history else "[]",
    })
    bm_logger.log("wibrandt_order_escalated", phone=phone, escalation_id=escalation_id)
    return escalation_id


def _post_validate(fields, flags, result, service):
    """
    Decide whether to advance booking state to awaiting_booking_confirmation.

    Brief 161: returns (None, should_set_awaiting). Always returns None for
    reply_override — Marina generates all booking-flow replies in the
    customer's language via her prompt (see BOOKING VALIDATION block in
    marina_agent._build_system_prompt). This function is now a pure state
    manager. It still runs the validation CHECKS so that state is never
    advanced to awaiting_booking_confirmation on a past date, wrong day, or
    ambiguous multi-departure, but it never overrides Marina's reply text.
    """
    if not any(i in _BOOKING_INTENTS for i in result.get("intents", [])):
        return None, False
    if not all(fields.get(k) for k in ("service_name", "date", "guests", "service_key")):
        return None, False
    if flags.get("awaiting_booking_confirmation") or flags.get("booking_confirmed"):
        return None, False

    date = fields["date"]
    slots = service.get("slots", [])

    # Day-of-week: do not advance state on wrong day (Marina's reply will
    # have told the customer which days the service runs).
    try:
        day_name = datetime.strptime(date, "%Y-%m-%d").strftime("%A")
        days_avail = service.get("days_available", "daily")
        if not _day_matches(day_name, days_avail):
            return None, False
    except ValueError:
        pass

    # Past date: do not advance state on past date.
    try:
        _pv_date_obj = datetime.strptime(date, "%Y-%m-%d").date()
        _pv_today = datetime.now(timezone(timedelta(hours=-4))).date()
        if _pv_date_obj < _pv_today:
            return None, False
    except ValueError:
        pass

    # Multi-departure: do not advance until the customer has chosen a slot.
    if len(slots) > 1 and not fields.get("slot_time"):
        return None, False

    # Child pricing: Marina is still gathering ages.
    if result.get("flags", {}).get("needs_child_ages"):
        return None, False

    # All checks pass — advance state. Marina has already written the summary
    # in the customer's language.
    return None, True


def _maybe_reset_stale_conversation(last_activity, fields, flags, completed_bookings):
    """Reset booking state if >24h since last activity. Returns True if reset happened."""
    if not last_activity:
        return False
    try:
        last = datetime.fromisoformat(last_activity)
        now = datetime.now(timezone.utc)
        if (now - last).total_seconds() < _STALE_CONVERSATION_SECONDS:
            return False
    except (ValueError, TypeError):
        return False

    # Archive current booking if one exists
    if flags.get("hold_created"):
        archived = {
            "booking_ref": flags.get("booking_ref", ""),
            "service_key": fields.get("service_key", ""),
            "service_name": fields.get("service_name", ""),
            "date": fields.get("date", ""),
            "guests": fields.get("guests", ""),
            "slot_time": fields.get("slot_time", ""),
            "payment_link": flags.get("payment_link", ""),
        }
        completed_bookings.append(archived)

    # Consulta Despertares retains its progressively collected intake after
    # inactivity. A WhatsApp free-text window closing is a delivery rule, not a
    # reason to make a prospect repeat their name, preferences, or concern.
    persistent_fields = (
        _CONSULTA_PERSISTENT_INTAKE_FIELDS
        if tenant_hard_rules.is_consulta_despertares()
        else _PERSISTENT_FIELDS
    )
    preserved = {k: v for k, v in fields.items() if k in persistent_fields}
    fields.clear()
    fields.update(preserved)

    # Reset all booking + escalation + rate-limit flags
    for fk in _BOOKING_FLAGS_TO_RESET:
        flags.pop(fk, None)
    for fk in ("fully_escalated", "awaiting_relay", "relay_token",
               "relay_question", "reply_times", "returning_booking",
               "ali_vehicle_recommendation_deliveries",
               "consulta_non_patient_service_contact",
               "consulta_service_contact_escalated"):
        flags.pop(fk, None)

    return True


def _history_for_agent(phone: str) -> list:
    """Return the appropriate prompt context for a WhatsApp conversation."""

    # Consulta Despertares must not restart intake after the WhatsApp 24-hour
    # delivery window. Use the active stored conversation (up to 200 messages)
    # so Alia can see what the prospect already shared. Other tenants retain
    # the standard short, recent prompt window.
    if tenant_hard_rules.is_consulta_despertares():
        try:
            return state_registry.wa_get_full_history(phone, limit=200)
        except Exception:
            # Preserve service continuity if the longer-history lookup is
            # temporarily unavailable.
            return state_registry.wa_get_history(phone, limit=10)
    return state_registry.wa_get_history(phone, limit=10)


def handle_incoming_whatsapp_message(message: dict, channel: str = "whatsapp",
                                     inbound_already_stored: bool = False,
                                     include_media: bool = False) -> str | dict:
    """
    Process a WhatsApp message: full booking orchestrator.
    Fetch state + history -> build action_context -> call marina_agent ->
    merge fields/flags -> post-validate -> availability + hold ->
    booking confirmation -> persist state -> return reply.
    """
    phone = message.get("from", "")
    text = message.get("text", "")
    from_name = message.get("from_name", "")

    _channel_label = {"whatsapp": "WhatsApp", "instagram_dm": "Instagram",
                      "facebook_dm": "Facebook", "twitter_dm": "X/Twitter"}.get(channel, channel)

    ignored = state_registry.match_ignored_contact(
        channel=channel,
        sender_id=phone,
        phone=phone,
    )
    if ignored:
        state_registry.record_ignored_contact_event(
            contact_id=ignored.get("id"),
            channel=channel,
            sender_identifier=phone,
        )
        bm_logger.log("ignored_contact_inbound_suppressed",
                      channel=channel,
                      sender=phone[:50],
                      reason="Ignored inbound message because sender is on Excluded Contacts / Ignore List.")
        return ""

    _moderation = auto_block.evaluate_inbound(
        channel=channel,
        user_identifier=phone,
        text=text,
        customer_name=from_name,
    )
    if _moderation.get("action") == "blocked":
        bm_logger.log("whatsapp_auto_blocked", phone=phone[:50],
                      category=_moderation.get("category"))
        return ""
    if _moderation.get("action") == "warn":
        bm_logger.log("whatsapp_auto_block_warning", phone=phone[:50])
        return _moderation.get("reply", "")

    # Isluno owns a separate context before any legacy intake/reset is loaded.
    from shared import isluno_config
    _isluno_features = config_loader.get_raw().get("features") or {}
    if channel == "whatsapp" and isinstance(_isluno_features, dict) and _isluno_features.get(isluno_config.FEATURE) is True:
        from agents.social.isluno_conversation import handle_message
        from shared.isluno_pricing import ItineraryError
        try:
            return handle_message(message) if include_media else ""
        except (isluno_config.IslunoUnavailable, ItineraryError):
            return ""

    # Get existing booking state
    state = state_registry.wa_get_booking_state(phone)
    fields = state.get("fields", {})
    flags = state.get("flags", {})
    completed_bookings = state.get("completed_bookings", [])
    last_activity = state.get("last_activity")
    from shared import mermaid_catalog
    mermaid_demo = (
        channel == "whatsapp" and mermaid_catalog.reservation_demo_enabled()
    )

    # Stale conversation reset — 24h inactivity gap means new conversation
    if _maybe_reset_stale_conversation(last_activity, fields, flags, completed_bookings):
        bm_logger.log("whatsapp_stale_reset", phone=phone)
        if mermaid_demo:
            flags["mermaid_session_started_at"] = datetime.now(timezone.utc).isoformat()
        state_registry.wa_store_message(phone, "system", "Conversation reset after 24h inactivity")
        # Mermaid's tenant-specific handler rereads state from the database.
        # Persist the reset before that early return so a new session cannot
        # silently reload the stale intake and suppress its first welcome.
        state_registry.wa_save_booking_state(phone, fields, flags, completed_bookings)

    # Anti-loop guard — rate limit per phone
    _reply_times = flags.get("reply_times", [])
    _now_ts = int(time.time())
    _reply_times = [t for t in _reply_times if _now_ts - t <= _REPLY_WINDOW_SECONDS]
    flags["reply_times"] = _reply_times
    if len(_reply_times) >= _MAX_REPLIES_PER_HOUR:
        bm_logger.log("whatsapp_rate_limited", phone=phone,
                      count=len(_reply_times))
        state_registry.wa_save_booking_state(phone, fields, flags, completed_bookings)
        return ""

    # Mermaid's demo reservation flow is deterministic and tenant-scoped.
    # It runs before the generic model orchestrator so prices, availability,
    # confirmations, and later payment state can never be model-invented.
    if mermaid_demo:
        from agents.social.mermaid_reservation_workflow import handle_demo_message
        return handle_demo_message(message, include_media=include_media, use_model=True)

    # Consulta Despertares receives commercial proposals, supplier outreach,
    # professional referrals, and job applications on the patient WhatsApp
    # number. Route those contacts before Claude and before follow-up-card
    # persistence so patient-intake keywords inside a sales pitch (for example
    # "patients", "addictions", or "schedule a call") cannot start intake.
    _service_contact_was_routed = bool(
        flags.get("consulta_non_patient_service_contact")
    )
    _is_non_patient_service_contact = (
        tenant_hard_rules.consulta_despertares_non_patient_service_contact(
            text,
            already_routed=_service_contact_was_routed,
        )
    )
    if _service_contact_was_routed and not _is_non_patient_service_contact:
        # An explicit request for care as a patient starts a normal new intake.
        flags.pop("consulta_non_patient_service_contact", None)
        flags.pop("consulta_service_contact_escalated", None)
    elif _is_non_patient_service_contact:
        flags["consulta_non_patient_service_contact"] = True
        _short_acknowledgement = (
            tenant_hard_rules.consulta_despertares_service_contact_acknowledgement(
                text
            )
        )
        _needs_service_escalation = bool(
            _service_contact_was_routed and not _short_acknowledgement
        )
        if (
            _needs_service_escalation
            and not flags.get("consulta_service_contact_escalated")
        ):
            _contact_name = from_name or "Contacto comercial"
            state_registry.create_pending_notification(
                "escalation",
                channel,
                phone,
                _contact_name,
                f"[CONTACTO NO PACIENTE] {_contact_name}",
                (
                    "Un contacto comercial o profesional continuó escribiendo "
                    "después de ser dirigido a info@consultadespertares.com.\n\n"
                    f"Nombre: {_contact_name}\n"
                    f"Canal: {_channel_label}\n"
                    f"Mensaje más reciente: {text}"
                ),
                mode="soft",
            )
            flags["consulta_service_contact_escalated"] = True
            state_registry.wa_store_message(
                phone,
                "system",
                "Contacto no paciente escalado al equipo.",
            )

        _service_reply = (
            tenant_hard_rules.consulta_despertares_service_contact_reply(
                text,
                escalated=_needs_service_escalation,
            )
        )
        _reply_times.append(int(time.time()))
        flags["reply_times"] = _reply_times
        state_registry.wa_save_booking_state(
            phone, fields, flags, completed_bookings
        )
        bm_logger.log(
            "consulta_non_patient_service_contact_routed",
            phone=phone,
            escalated=_needs_service_escalation,
        )
        if include_media:
            return {
                "text": _service_reply,
                "media": None,
                "vehicle_recommendation": None,
                "quote_confirmation": None,
                "ali_turn_commit": None,
            }
        return _service_reply

    # Brief 290: post-quote controls are signed workflow commands. Resolve and
    # apply them before Claude so a tap can never be mistaken for prose or a
    # generic confirmation. The exact RESERVE token is the sole text fallback.
    _ali_account_id = str(message.get("_zernio_account_id") or "").strip()
    _ali_post_quote_result = None
    _ali_post_quote_interaction = None
    if channel == "whatsapp" and ali_quote_tenant_enabled() and _ali_account_id:
        try:
            _ali_post_quote_interaction = resolve_ali_post_quote_interaction(
                message.get("_zernio_interactive_type"),
                message.get("_zernio_interactive_id"),
                phone,
                _ali_account_id,
            )
            if _ali_post_quote_interaction is not None:
                _ali_post_quote_result = handle_ali_post_quote_action(
                    _ali_post_quote_interaction,
                    action_id=str(
                        message.get("_ali_action_id")
                        or message.get("message_id")
                        or ""
                    ),
                )
                if (
                    not _ali_post_quote_result.get("text")
                    and not _ali_post_quote_result.get(
                        "customer_delivery_deferred"
                    )
                ):
                    _post_status = str(
                        _ali_post_quote_result.get("status") or "invalid"
                    )
                    _post_action = str(
                        _ali_post_quote_result.get("action") or ""
                    )
                    if _post_status == "question":
                        _ali_post_quote_result["text"] = (
                            "What would you like to know about your quote?"
                        )
                    elif _post_status == "stale":
                        _ali_post_quote_result["text"] = (
                            "That choice is no longer current. Please use the choices "
                            "under your latest quote."
                        )
                    else:
                        _ali_post_quote_result["text"] = (
                            "I couldn't verify that choice. Please use the choices "
                            "under your latest quote."
                        )
            elif is_ali_exact_reserve_fallback(text):
                _ali_post_quote_result = handle_ali_exact_reserve(
                    phone,
                    _ali_account_id,
                    action_id=str(
                        message.get("_ali_action_id")
                        or message.get("message_id")
                        or ""
                    ),
                )
        except AliReservationError as exc:
            bm_logger.log(
                "ali_post_quote_action_rejected",
                code=exc.code,
                conversation_id=phone[:20],
            )
            _ali_post_quote_result = {
                "text": (
                    "I couldn't find a current quote for that choice. "
                    "I can help you prepare a new quote here."
                ),
                "status": "rejected",
                "action": None,
                "reservation": None,
            }

    if _ali_post_quote_result is not None:
        if (
            _ali_post_quote_result.get("status") == "change_requested"
            and _ali_post_quote_result.get("action") == "change"
        ):
            flags["ali_post_quote_change_requested"] = {
                "quote_public_id": str(
                    (_ali_post_quote_interaction or {}).get("quote_public_id")
                    or ""
                ),
                "requested_at": datetime.now(timezone.utc).isoformat(),
            }
        _reservation = _ali_post_quote_result.get("reservation")
        if (
            _ali_post_quote_result.get("status") == "created"
            and isinstance(_reservation, dict)
            and _reservation.get("availability_status") == "pending"
        ):
            state_registry.create_pending_notification(
                "escalation",
                "whatsapp",
                phone,
                fields.get("customer_name") or from_name or "Ali quote customer",
                "[ALI AVAILABILITY CHECK]",
                (
                    "A customer requested a vehicle availability check. "
                    f"Reservation: {_reservation.get('public_id') or ''}. "
                    "Review it in the Ali reservation queue."
                ),
                mode="soft",
            )
        _reply_text = str(_ali_post_quote_result.get("text") or "")
        _customer_delivery_deferred = bool(
            _ali_post_quote_result.get("customer_delivery_deferred")
        )
        if _reply_text:
            _reply_times.append(int(time.time()))
            flags["reply_times"] = _reply_times
        state_registry.wa_save_booking_state(
            phone, fields, flags, completed_bookings,
        )
        if include_media:
            return {
                "text": _reply_text,
                "media": None,
                "vehicle_recommendation": None,
                "quote_confirmation": None,
                "ali_turn_commit": None,
                "ali_customer_delivery_deferred": _customer_delivery_deferred,
            }
        return _reply_text

    # Issue 241: a customer's clear standalone payment report records only
    # customer_reports_paid. It never verifies payment or confirms a rental.
    if (
        channel == "whatsapp"
        and ali_quote_tenant_enabled()
        and _ali_account_id
        and ali_customer_dossier.is_customer_payment_report(text)
    ):
        try:
            _payment_case = ali_customer_dossier.record_customer_payment_report(
                phone,
                _ali_account_id,
                str(message.get("_ali_action_id") or message.get("message_id") or ""),
            )
        except AliReservationError as exc:
            if exc.code != "payment_report_not_expected":
                bm_logger.log(
                    "ali_payment_report_rejected",
                    code=exc.code,
                    conversation_id=phone[:20],
                )
        else:
            _payment_locale = ali_customer_dossier.customer_delivery_context(
                str(_payment_case.get("public_id") or "")
            ).get("locale", "en")
            _payment_reply = {
                "en": "Thanks. I’ve recorded that you paid. Our team will verify the deposit manually before final approval.",
                "nl": "Dank je. Ik heb genoteerd dat je hebt betaald. Ons team controleert de borg handmatig vóór de definitieve goedkeuring.",
                "pap": "Danki. Mi a registrá ku bo a paga. Nos tim ta verifiká e depósito manualmente promé ku aprobashon final.",
                "de": "Danke. Ich habe vermerkt, dass Sie bezahlt haben. Unser Team prüft die Kaution vor der endgültigen Freigabe manuell.",
            }.get(str(_payment_locale), "Thanks. I’ve recorded that you paid. Our team will verify the deposit manually before final approval.")
            _reply_times.append(int(time.time()))
            flags["reply_times"] = _reply_times
            state_registry.wa_save_booking_state(phone, fields, flags, completed_bookings)
            if include_media:
                return {
                    "text": _payment_reply,
                    "media": None,
                    "vehicle_recommendation": None,
                    "quote_confirmation": None,
                    "ali_turn_commit": None,
                }
            return _payment_reply

    # Brief 285: quote confirmation postbacks are signed protocol events, not
    # prose. Resolve them before Claude and reload all quote facts from this
    # tenant's persisted state; the interaction payload carries no rental data.
    _ali_interactive_type = message.get("_zernio_interactive_type")
    _ali_interactive_id = message.get("_zernio_interactive_id")
    _ali_quote_interaction = None
    if channel == "whatsapp" and ali_quote_tenant_enabled():
        _ali_quote_interaction = resolve_quote_confirmation_interaction(
            _ali_interactive_type,
            _ali_interactive_id,
            phone,
            flags,
        )
    if _ali_quote_interaction:
        _action_id = str(
            message.get("_ali_action_id")
            or message.get("message_id")
            or ""
        )
        if _ali_quote_interaction == "change":
            _change_locale = str(
                fields.get("conversation_language") or "en"
            ).lower()
            if _change_locale not in QUOTE_CHANGE_PROMPTS:
                _change_locale = "en"
            _quote_plan = plan_ali_quote_turn(
                conversation_id=phone,
                zernio_account_id=str(
                    message.get("_zernio_account_id") or ""
                ),
                whatsapp_number=str(
                    message.get("_zernio_sender_id") or phone
                ),
                message_text="CHANGE DETAILS",
                fields=fields,
                flags=flags,
                model_reply=QUOTE_CHANGE_PROMPTS[_change_locale],
                from_name=from_name,
                primary_intent="reject_or_hesitate",
                change_outcome="clarify",
                supplied_action_id=_action_id,
            )
        elif _ali_quote_interaction == "repeated":
            _quote_plan = plan_repeated_quote_confirmation(
                phone, fields, flags, _action_id,
            )
        else:
            _quote_plan = plan_ali_quote_turn(
                conversation_id=phone,
                zernio_account_id=str(message.get("_zernio_account_id") or ""),
                whatsapp_number=str(message.get("_zernio_sender_id") or phone),
                message_text=(
                    "SEND QUOTE"
                    if _ali_quote_interaction == "current"
                    else ""
                ),
                fields=fields,
                flags=flags,
                model_reply="",
                from_name=from_name,
                primary_intent=(
                    "confirm_summary"
                    if _ali_quote_interaction == "current"
                    else "repeat_summary"
                ),
                summary_action=(
                    None
                    if _ali_quote_interaction == "current"
                    else {"mode": "repeat"}
                ),
                supplied_action_id=_action_id,
            )
        _reply_times.append(int(time.time()))
        flags["reply_times"] = _reply_times
        state_registry.wa_save_booking_state(
            phone, fields, flags, completed_bookings,
        )
        confirmation_control = None
        if _quote_plan.outbound_kind == "summary":
            confirmation_control = build_quote_confirmation_control(
                phone, _quote_plan,
                locale=fields.get("conversation_language") or "en",
            )
        if include_media:
            return {
                "text": _quote_plan.text,
                "media": None,
                "vehicle_recommendation": None,
                "quote_confirmation": confirmation_control,
                "ali_turn_commit": _quote_plan.delivery_commit(),
            }
        return _quote_plan.text

    # Issue 198: native picker taps are catalog commands, not prose for the
    # model to interpret. Resolve them before Claude and make a clear typed
    # exact vehicle choice follow the same deterministic path. The canonical
    # fields are re-applied after model extraction below so the model cannot
    # replace a provider-validated selection with a label guess.
    _ali_selected_this_turn = None
    _ali_selection_source = ""
    if channel == "whatsapp" and ali_quote_tenant_enabled():
        try:
            _selection_catalog = get_ali_intake_catalog()
            _interactive_type = message.get("_zernio_interactive_type")
            _interactive_id = message.get("_zernio_interactive_id")
            if str(_interactive_id or "").strip():
                _ali_selected_this_turn = resolve_vehicle_selection(
                    _interactive_type,
                    _interactive_id,
                    _selection_catalog,
                )
                if _ali_selected_this_turn:
                    _ali_selection_source = "native_picker"
            else:
                _ali_selected_this_turn = resolve_typed_vehicle_selection(
                    text,
                    _selection_catalog,
                )
                if _ali_selected_this_turn:
                    _ali_selection_source = "typed_exact"
        except AliVehicleSelectionError as exc:
            clarification = invalid_vehicle_selection_reply(
                fields.get("conversation_language")
            )
            recovery_recommendation = build_vehicle_picker_recovery(
                _selection_catalog,
                fields,
                flags,
                clarification,
                turn_id=str(
                    message.get("_ali_action_id")
                    or message.get("message_id")
                    or ""
                ),
                trigger_message_id=str(
                    message.get("_zernio_provider_message_id") or ""
                ),
                trigger_sent_at=str(message.get("_zernio_sent_at") or ""),
            )
            invalid_plan = fail_closed_turn_plan(
                phone,
                text,
                fields.get("conversation_language"),
                str(
                    message.get("_ali_action_id")
                    or message.get("message_id")
                    or ""
                ),
            )
            invalid_plan = replace(
                invalid_plan,
                text=clarification,
                primary_intent="ask_question",
                reason_code=str(exc)[:60],
                outbound_kind=(
                    "vehicle_recommendation"
                    if recovery_recommendation
                    else invalid_plan.outbound_kind
                ),
                phase=(
                    "DISCOVERY"
                    if recovery_recommendation
                    else invalid_plan.phase
                ),
            )
            flags["reply_times"] = [*_reply_times, int(time.time())]
            state_registry.wa_save_booking_state(
                phone, fields, flags, completed_bookings
            )
            bm_logger.log(
                "ali_vehicle_selection_invalid",
                source="native_picker",
                reason=str(exc)[:80],
            )
            if include_media:
                return {
                    "text": clarification,
                    "media": None,
                    "vehicle_recommendation": recovery_recommendation,
                    "ali_turn_commit": invalid_plan.delivery_commit(),
                }
            return clarification

    if _ali_selected_this_turn:
        for key in VEHICLE_STATE_FIELDS:
            fields.pop(key, None)
        fields.update(_ali_selected_this_turn)
        invalidate_active_quote_summary(flags)
        flags.pop("ali_summary_deferred_for_recommendation", None)
        # Some provider taps contain only metadata. Give the single normal
        # model turn the canonical catalog name instead of an empty body.
        if not str(text or "").strip():
            text = _ali_selected_this_turn["vehicle_name"]
        bm_logger.log(
            "ali_vehicle_selection_applied",
            source=_ali_selection_source,
            vehicle_id_prefix=str(
                _ali_selected_this_turn.get("vehicle_id") or ""
            )[:12],
        )

    history = _history_for_agent(phone)
    if inbound_already_stored and history:
        # The webhook layer may persist the inbound before model/order
        # processing for reliability. Keep the current inbound out of the
        # prompt history because it is already passed as the active body.
        for idx in range(len(history) - 1, -1, -1):
            if history[idx].get("role") == "user" and history[idx].get("text") == text:
                history.pop(idx)
                break

    # A first Ali WhatsApp turn should feel like a welcome, while keeping the
    # deterministic reply's one useful next question.  Do not infer this from
    # the inbound alone: a prior customer message without an assistant reply
    # needs recovery, not a repeated greeting.
    _ali_first_customer_turn = bool(
        channel == "whatsapp"
        and ali_quote_tenant_enabled()
        and not fields
        and not completed_bookings
        and not flags.get("ali_welcome_sent")
        and not history
    )

    # Build from identifier with name if available
    display_name = fields.get("customer_name") or from_name
    from_id = f"{phone} ({display_name})" if display_name else phone

    bm_logger.log("whatsapp_processing", phone=phone, text=text[:100],
                  from_name=from_name)

    def _upsert_appointment_signal(reply_text: str):
        workflow = config_loader.get_raw().get("workflow", {}) or {}
        if workflow.get("type") == "callback_follow_up":
            bm_logger.log(
                "appointment_signal_skipped_for_callback_workflow",
                phone=phone,
            )
            return
        if _is_wibrandt_order_related_text(
            text,
            reply_text,
            "\n".join(m.get("text", "") for m in (history or [])),
        ):
            bm_logger.log("wibrandt_order_skipped_appointment_signal", phone=phone)
            return
        _cname = fields.get("customer_name") or from_name or ""
        appointment_detector.upsert_pending_from_exchange(
            conversation_id=phone,
            channel=channel,
            customer_name=_cname,
            user_text=text,
            assistant_reply=reply_text or "",
            history=history,
        )

    # Brief 166: cross-channel customer lookup. Use a typed identifier so WhatsApp
    # conversation ids don't collide with IG/FB/X DMs.
    from agents.social.whatsapp_client import _is_zernio_conversation_id
    _cust_type = "wa_conversation_id" if _is_zernio_conversation_id(phone) else "phone"
    _cust_row = None
    _cust_file = None
    try:
        _cust_row = state_registry.customer_lookup_or_create(
            _cust_type, phone, display_name=from_name or ""
        )
        _cust_file = state_registry.customer_get_full(_cust_row["id"])
    except Exception as _e:
        bm_logger.log("customer_lookup_failed", phone=phone, error=str(_e))

    if (
        _is_wibrandt_order_tenant()
        and flags.get("waiting_for_human_order_confirmation")
        and flags.get("order_escalation_id")
    ):
        _released_order_id = flags.get("order_escalation_id")
        _reset_wibrandt_order_draft(fields, flags, _released_order_id)
        bm_logger.log(
            "wibrandt_order_draft_released",
            phone=phone,
            escalation_id=_released_order_id,
        )

    # Fully escalated guard — still calls marina_agent (one Claude call), skip booking flow
    if flags.get("fully_escalated"):
        _esc_flags = dict(flags)
        for _rk in ("awaiting_relay", "relay_token", "relay_question", "reply_times"):
            _esc_flags.pop(_rk, None)
        esc_result = marina_agent.process_message(
            from_email=from_id, subject="", body=text,
            thread_fields=fields, thread_flags=_esc_flags,
            channel=channel, messages=history,
            customer_file=_cust_file,
        )
        esc_reply = esc_result.get("reply", "")
        bm_logger.log("whatsapp_escalated_reply", phone=phone,
                      reply_length=len(esc_reply))

        # Brief 184: even in fully-escalated mode, Marina may flag a relay question
        # (e.g. wheelchair accessibility) that the operator needs to answer.
        # semi_escalation and requires_human are TOP-LEVEL keys in the response.
        if esc_result.get("semi_escalation"):
            _relay_q = esc_result.get("relay_question", "(no question captured)")
            _relay_token = uuid.uuid4().hex[:12]
            _cname = fields.get("customer_name") or from_name or "Unknown"
            _ref = flags.get("booking_ref") or flags.get("returning_booking") or "NO-REF"
            _alert_subject = f"[RELAY-{_relay_token}] {_ref} - {_cname}"
            _alert_body = (
                f"Customer: {_cname} ({_channel_label}: {phone})\n"
                f"Their question: {_relay_q}\n\n"
                f"Booking context:\n"
                f"  Trip: {fields.get('service_key', '')} | "
                f"Date: {fields.get('date', '')} | "
                f"Guests: {fields.get('guests', '')}\n"
                f"  Ref: {_ref}\n\n"
                f"INSTRUCTIONS: Reply to this email with your answer.\n"
                f"Marina will relay it to the customer in her own words."
            )
            state_registry.create_pending_notification(
                'relay', channel, phone, _cname,
                _alert_subject, _alert_body, relay_token=_relay_token)
            flags["awaiting_relay"] = True
            flags["relay_token"] = _relay_token
            flags["relay_question"] = _relay_q
            bm_logger.log("whatsapp_escalated_semi_relay", phone=phone,
                          relay_question=_relay_q, relay_token=_relay_token)
            state_registry.wa_store_message(phone, "system",
                f"Relay question sent to team: {_relay_q}")

        _esc_req_human = esc_result.get("requires_human")
        if _esc_req_human and not esc_result.get("semi_escalation"):
            _cname = fields.get("customer_name") or from_name or "Unknown"
            _ref = flags.get("booking_ref") or flags.get("returning_booking") or "NO-REF"
            _esc_note = esc_result.get("internal_note", "")
            _esc_mode = (
                "order" if _is_wibrandt_order_related_text(
                    text,
                    _esc_note,
                    esc_result.get("reply", ""),
                    "\n".join(m.get("text", "") for m in (history or [])),
                ) else "hard"
            )
            _esc_prefix = "[ORDER]" if _esc_mode == "order" else "[ESCALATION]"
            state_registry.create_pending_notification(
                'escalation', channel, phone, _cname,
                f"{_esc_prefix} {_ref} - {_cname} ({_channel_label}: {phone}) - {_esc_note[:200]}",
                f"=== RE-ESCALATION (fully_escalated conversation) ===\n"
                f"Customer: {_cname}\nNew issue: {_esc_note}\n\n"
                f"=== CHAT LOG ===\n" + "\n".join(
                    f"[{m.get('role','?').upper()}] {m.get('text','')}" for m in (history or [])
                ),
                mode=_esc_mode)
            bm_logger.log("whatsapp_escalated_re_escalation", phone=phone)

        selected_media = None
        if include_media and channel == "whatsapp" and esc_reply:
            selected_media = _select_customer_media(text, esc_reply, fields, flags, history)
            if selected_media:
                flags["last_media_id_sent"] = selected_media["id"]
                esc_reply = _strip_media_fallback_links(esc_reply)
                bm_logger.log(
                    "customer_media_selected_escalated",
                    phone=phone,
                    media_id=selected_media["id"],
                    filename=selected_media["filename"][:120],
                    score=selected_media["score"],
                )

        # Record reply timestamp + persist (early return bypasses end-of-function persistence)
        if esc_reply:
            _upsert_appointment_signal(esc_reply)
            _reply_times = flags.get("reply_times", [])
            _reply_times.append(int(time.time()))
            flags["reply_times"] = _reply_times
        state_registry.wa_save_booking_state(phone, fields, flags, completed_bookings)
        if include_media:
            return {
                "text": esc_reply,
                "media": selected_media,
            }
        return esc_reply

    # Brief 188: conversation is being handled by AI → status "pending"
    state_registry.set_conversation_status(phone, "pending", channel)

    # Step 1: Build action context
    action_context = _build_action_context(flags)

    # Filter relay flags + internal state before marina_agent call
    agent_flags = dict(flags)
    for _rk in ("awaiting_relay", "relay_token", "relay_question", "reply_times"):
        agent_flags.pop(_rk, None)
    _quote_context = None
    _reservation_context = None
    _ali_lead_follow_up_context = None
    _ali_reservation_confirmed = False
    if channel == "whatsapp" and ali_quote_tenant_enabled() and _ali_account_id:
        try:
            _quote_context = get_ali_quote_context(phone, _ali_account_id)
            _reservation_context = get_ali_reservation_context(
                phone, _ali_account_id,
            )
            if _quote_context:
                agent_flags["_ali_quote_context"] = _quote_context
            if _reservation_context:
                agent_flags["_ali_reservation_context"] = _reservation_context
                _ali_reservation_confirmed = bool(
                    _reservation_context.get("status") == "confirmed"
                )
        except AliReservationError as exc:
            bm_logger.log(
                "ali_post_quote_context_unavailable",
                code=exc.code,
                conversation_id=phone[:20],
            )
        try:
            _ali_lead_follow_up_context = (
                ali_lead_follow_up.pending_reply_context(phone)
            )
            if _ali_lead_follow_up_context:
                agent_flags["_ali_lead_follow_up_context"] = (
                    _ali_lead_follow_up_context
                )
        except Exception as exc:
            bm_logger.log(
                "ali_lead_follow_up_context_unavailable",
                conversation_id=phone[:20],
                error=type(exc).__name__,
            )

    # Returning customer — booking ref detection
    # Brief 161: require at least one digit so all-caps service words like
    # "SUNSET" or "FRIDAY" don't get misread as booking references.
    _detected_ref = None
    _ref_match = re.search(r'\b(?=[A-Z0-9]*\d)[A-Z0-9]{6}\b', text)
    if _ref_match:
        _detected_ref = _ref_match.group()
        if not flags.get("booking_ref"):
            _past_booking = state_registry.get_booking(_detected_ref)
            if _past_booking:
                flags["returning_booking"] = _detected_ref
                agent_flags["returning_booking"] = _detected_ref
                for _rbk in ("service_key", "date", "guests", "customer_name", "slot_time"):
                    _rbv = _past_booking.get(_rbk)
                    if _rbv and not fields.get(_rbk):
                        fields[_rbk] = _rbv if not isinstance(_rbv, int) else str(_rbv)
                bm_logger.log("whatsapp_returning_customer", phone=phone, booking_ref=_detected_ref)
            else:
                flags["unknown_ref"] = _detected_ref
                agent_flags["unknown_ref"] = _detected_ref
                bm_logger.log("whatsapp_unknown_ref", phone=phone, ref=_detected_ref)

    # Returning customer — phone-based lookup (cross-thread memory)
    if not _detected_ref and not completed_bookings:
        _phone_bookings = state_registry.get_bookings_by_email(phone)
        if _phone_bookings:
            _eb_lines = []
            for _eb in _phone_bookings[:3]:
                _eb_lines.append(
                    f"  - {_eb['service_key']} on {_eb['date']} for {_eb['guests']} guests "
                    f"(ref: {_eb['booking_ref']})")
            agent_flags["_past_customer_bookings"] = "\n".join(_eb_lines)
            bm_logger.log("whatsapp_returning_by_phone", phone=phone,
                          past_count=len(_phone_bookings))

    # Completed bookings context for multi-service conversations
    if completed_bookings:
        _cb_lines = []
        for _cb in completed_bookings:
            _cb_lines.append(
                f"  - {_cb.get('service_name', _cb.get('service_key', '?'))} on "
                f"{_cb.get('date', '?')} for {_cb.get('guests', '?')} guests "
                f"(ref: {_cb.get('booking_ref', 'N/A')})")
        agent_flags["_completed_bookings_summary"] = "\n".join(_cb_lines)
        _max_bk = config_loader.get_booking_rules().get("max_bookings_per_thread", 3)
        if len(completed_bookings) >= _max_bk and flags.get("hold_created"):
            agent_flags["_max_bookings_reached"] = True

    # Call marina_agent with actual channel
    result = marina_agent.process_message(
        from_email=from_id,
        subject="",
        body=text,
        thread_fields=fields,
        thread_flags=agent_flags,
        action_context=action_context,
        channel=channel,
        messages=history,
        customer_file=_cust_file,
    )

    if _ali_lead_follow_up_context:
        try:
            ali_lead_follow_up.record_customer_action(
                phone,
                _ali_lead_follow_up_context,
                result.get("ali_lead_follow_up_action"),
            )
        except Exception as exc:
            bm_logger.log(
                "ali_lead_follow_up_action_failed",
                conversation_id=phone[:20],
                error=type(exc).__name__,
            )

    # Brief 166: record interaction + merge any new identifiers Marina extracted
    if _cust_row and _cust_row.get("id"):
        try:
            state_registry.customer_record_interaction(
                _cust_row["id"], channel, f"{_channel_label}/DM: {text[:80]}"
            )
            _new_fields_for_merge = result.get("fields", {}) or {}
            for _ftype, _fkey in (("email", "email"), ("phone", "phone")):
                _val = _new_fields_for_merge.get(_fkey)
                if _val and str(_val).strip() and str(_val).strip() != phone:
                    state_registry.customer_add_identifier(
                        _cust_row["id"], _ftype, str(_val).strip()
                    )
            # Brief 181: update customer display_name when Marina extracts a
            # different name from the conversation (e.g. customer says "Hi, Mark
            # here" but Zernio sender_name was "Calvin Adamus").
            _extracted_name = (_new_fields_for_merge.get("customer_name") or "").strip()
            if _extracted_name and _extracted_name != (_cust_row.get("display_name") or ""):
                state_registry.customer_update_display_name(_cust_row["id"], _extracted_name)
                _cust_row["display_name"] = _extracted_name
        except Exception as _e:
            bm_logger.log("customer_postprocess_failed", phone=phone, error=str(_e))

    reply = result.get("reply", "")

    if not reply:
        bm_logger.log("whatsapp_empty_reply", phone=phone,
                      intents=result.get("intents", []),
                      confidence=result.get("confidence", ""),
                      internal_note=result.get("internal_note", "")[:200])
        return ""

    # Multi-service: if booking intent + previous booking completed, archive and reset
    if (any(i in _BOOKING_INTENTS for i in result.get("intents", []))
            and flags.get("hold_created")):
        _max_bk = config_loader.get_booking_rules().get("max_bookings_per_thread", 3)
        if len(completed_bookings) < _max_bk:
            archived = {
                "booking_ref": flags.get("booking_ref", ""),
                "service_key": fields.get("service_key", ""),
                "service_name": fields.get("service_name", ""),
                "date": fields.get("date", ""),
                "guests": fields.get("guests", ""),
                "slot_time": fields.get("slot_time", ""),
                "payment_link": flags.get("payment_link", ""),
            }
            completed_bookings.append(archived)
            preserved = {k: v for k, v in fields.items() if k in _PERSISTENT_FIELDS}
            fields.clear()
            fields.update(preserved)
            for _fk in _BOOKING_FLAGS_TO_RESET:
                flags.pop(_fk, None)
            bm_logger.log("whatsapp_multi_trip_reset", phone=phone,
                          booking_number=len(completed_bookings))
            state_registry.wa_store_message(phone, "system",
                f"Previous booking archived ({archived.get('service_key', '')} {archived.get('date', '')}). Starting new booking.")

    # Clear one-shot flags after Claude has seen them
    flags.pop("unknown_ref", None)

    # Step 3: Merge fields — overwrite when Claude returns non-empty values
    new_fields = result.get("fields", {}) or {}
    _ali_change_outcome = "not_applicable"
    _ali_change_fields = ()
    _ali_change_configured = ali_quote_tenant_configured()
    _ali_location_request_this_turn = (
        rental_location_request_kind(text)
        if _ali_change_configured
        else ""
    )
    _ali_hotel_detail_reply = ""
    _ali_hotel_finished_this_turn = False
    _ali_hotel_stage = str(
        flags.get("ali_pickup_hotel_detail_stage") or ""
    )
    _ali_hotel_choice_this_turn = bool(
        _ali_change_configured
        and explicit_hotel_delivery_choice(text)
    )
    _ali_fixed_location_kind = (
        "pickup"
        if _ali_hotel_stage
        else (
            "return"
            if (
                (
                    new_fields.get("return_location")
                    and not new_fields.get("pickup_location")
                )
                or (
                    fields.get("pickup_location")
                    and not fields.get("return_location")
                )
            )
            else "pickup"
        )
    )
    try:
        _ali_fixed_location_choice = (
            resolve_fixed_pickup_option_choice(
                text,
                get_ali_intake_catalog(),
                _ali_fixed_location_kind,
            )
            if _ali_change_configured and not _ali_location_request_this_turn
            else None
        )
    except Exception:
        _ali_fixed_location_choice = None
    _ali_fixed_location_finished_this_turn = False
    _ali_repair_reply = (
        conversation_repair_reply(text, fields, flags)
        if _ali_change_configured else ""
    )
    _ali_structured_primary_intent = str(
        result.get("ali_primary_intent") or ""
    ).strip().lower()
    _ali_hotel_answer_candidate = bool(
        "?" not in str(text or "")
        and _ali_structured_primary_intent
        not in {"ask_question", "request_recommendation", "reject_or_hesitate"}
    )
    _ali_post_quote_change_pending = isinstance(
        flags.get("ali_post_quote_change_requested"), dict,
    )
    _ali_relative_rental_end = (
        infer_relative_rental_end_change(
            text,
            fields,
            change_requested=_ali_post_quote_change_pending,
        )
        if _ali_change_configured
        else None
    )
    _ali_forced_change_action = None
    if _ali_relative_rental_end:
        new_fields = dict(new_fields)
        new_fields["rental_end"] = _ali_relative_rental_end
        _ali_forced_change_action = {
            "mode": "apply",
            "changed_fields": ["rental_end"],
        }
    _ali_pure_confirmation = (
        _ali_change_configured
        and confirmation_decision(text)[0]
    )
    # A deterministic pure affirmative contains no correction details. Do not
    # let opportunistic model extraction mutate the provider-delivered draft.
    _ali_change_action = _ali_forced_change_action or (
        None
        if (
            _ali_pure_confirmation
            or _ali_repair_reply
            or _ali_location_request_this_turn
            or _ali_hotel_choice_this_turn
            or _ali_fixed_location_choice
            or _ali_hotel_stage
        )
        else result.get("ali_rental_change")
    )
    if _ali_fixed_location_choice:
        new_fields = dict(new_fields)
        _location_name = str(
            _ali_fixed_location_choice.get("name") or ""
        ).strip()
        if _ali_fixed_location_kind == "return":
            new_fields["return_location"] = _location_name
        else:
            new_fields["pickup_location"] = _location_name
            new_fields["pickup_location_kind"] = "fixed"
            new_fields["pickup_hotel_name"] = ""
            new_fields["pickup_hotel_address"] = ""
            flags.pop("ali_pickup_hotel_detail_stage", None)
        _ali_fixed_location_finished_this_turn = True
    elif _ali_hotel_choice_this_turn:
        new_fields = dict(new_fields)
        for _key in (
            "pickup_location", "pickup_hotel_name", "pickup_hotel_address",
        ):
            new_fields.pop(_key, None)
        new_fields["pickup_location_kind"] = "hotel_delivery"
        flags["ali_pickup_hotel_detail_stage"] = "name"
        _ali_hotel_detail_reply = hotel_delivery_detail_prompt("name", fields)
    elif (
        _ali_hotel_stage == "name"
        and not _ali_location_request_this_turn
        and _ali_hotel_answer_candidate
    ):
        _hotel_name = str(text or "").strip()
        if _hotel_name:
            new_fields = dict(new_fields)
            new_fields["pickup_location_kind"] = "hotel_delivery"
            new_fields["pickup_hotel_name"] = _hotel_name
            new_fields.pop("pickup_location", None)
            flags["ali_pickup_hotel_detail_stage"] = "address"
            _ali_hotel_detail_reply = hotel_delivery_detail_prompt("address", fields)
    elif (
        _ali_hotel_stage == "address"
        and not _ali_location_request_this_turn
        and _ali_hotel_answer_candidate
    ):
        _hotel_address = str(text or "").strip()
        _hotel_name = str(
            new_fields.get("pickup_hotel_name")
            or fields.get("pickup_hotel_name")
            or ""
        ).strip()
        if _hotel_name and _hotel_address:
            new_fields = dict(new_fields)
            new_fields["pickup_location_kind"] = "hotel_delivery"
            new_fields["pickup_hotel_name"] = _hotel_name
            new_fields["pickup_hotel_address"] = _hotel_address
            new_fields["pickup_location"] = (
                f"Hotel delivery — {_hotel_name}, {_hotel_address}"
            )
            flags.pop("ali_pickup_hotel_detail_stage", None)
            _ali_hotel_finished_this_turn = True
    if (
        _ali_pure_confirmation
        and _ali_post_quote_change_pending
        and _ali_forced_change_action is None
    ):
        _change_locale = str(
            fields.get("conversation_language") or "en"
        ).lower()
        if _change_locale not in QUOTE_CHANGE_VALUE_PROMPTS:
            _change_locale = "en"
        reply = QUOTE_CHANGE_VALUE_PROMPTS[_change_locale]
        _ali_structured_primary_intent = "reject_or_hesitate"
        _ali_change_action = {"mode": "clarify", "changed_fields": []}
    _ali_summary_anchor_active = bool(
        flags.get("ali_phase") == "SUMMARY_PRESENTED"
        or (
            flags.get("awaiting_quote_confirmation")
            and (
                flags.get("ali_presented_summary_hash")
                or flags.get("ali_summary_hash")
            )
        )
    )
    _ali_quote_merge_keys = {
        "customer_name", "first_name", "surnames", "rental_start", "rental_end", "pickup_location",
        "pickup_location_kind", "pickup_hotel_name", "pickup_hotel_address",
        "return_location", "vehicle_id", "vehicle_name", "vehicle_class_id",
        "vehicle_class_name", "driver_age", "passenger_count", "luggage_count",
        "vehicle_catalog_class_id", "vehicle_catalog_class_name",
        "vehicle_daily_rate_usd", "vehicle_rate_currency",
        "supplements", "extra_ids", "comments", "special_requests",
        "conversation_language",
    }
    _ali_protect_quote_fields = (
        _ali_pure_confirmation
        or bool(_ali_repair_reply)
        or bool(_ali_location_request_this_turn)
        or (
            _ali_summary_anchor_active
            and _ali_structured_primary_intent == "ask_question"
            and not isinstance(_ali_change_action, dict)
        )
    )
    _ali_explicit_class_this_turn = None
    if tenant_hard_rules.is_consulta_despertares():
        # A concise answer such as "15:30" may be unambiguous only because
        # the prior Alia turn asked when the team may call. Preserve it even
        # if the model misses the field extraction on that turn.
        _callback_answer = (
            tenant_hard_rules.consulta_despertares_callback_preference_from_reply(
                text, history, fields
            )
        )
        if _callback_answer and not str(
            new_fields.get("callback_preference") or ""
        ).strip():
            new_fields = dict(new_fields)
            new_fields["callback_preference"] = _callback_answer

        # "Me da igual" is meaningful only in the context of Alia's prior
        # preferred-clinic question. Preserve that explicit answer even when
        # the model omits the structured field on a very short reply.
        _clinic_answer = (
            tenant_hard_rules.consulta_despertares_preferred_clinic_from_reply(
                text, history, fields
            )
        )
        if _clinic_answer and not str(
            new_fields.get("preferred_clinic") or ""
        ).strip():
            new_fields = dict(new_fields)
            new_fields["preferred_clinic"] = _clinic_answer
    if _ali_selected_this_turn:
        _ali_change_outcome = "changed"
        _ali_change_fields = ("vehicle_selection",)
        _quote_vehicle_keys = set(VEHICLE_STATE_FIELDS)
        for k, v in new_fields.items():
            if k in _quote_vehicle_keys:
                continue
            if v is not None and v != "":
                fields[k] = v
            elif v == "" and k in fields:
                del fields[k]
        for key in _quote_vehicle_keys:
            fields.pop(key, None)
        fields.update(_ali_selected_this_turn)
        log_rental_change_decision(_ali_change_outcome, _ali_change_fields)
    elif _ali_change_configured and isinstance(_ali_change_action, dict):
        try:
            changed_state, _ali_change_outcome, _ali_change_fields = (
                apply_latest_rental_change(
                    fields,
                    new_fields,
                    _ali_change_action,
                    get_ali_intake_catalog(),
                )
            )
        except Exception:
            changed_state = dict(fields)
            _ali_change_outcome = "clarify"
            _ali_change_fields = ()
        if _ali_change_outcome == "changed":
            fields.clear()
            fields.update(changed_state)
            invalidate_active_quote_summary(flags)
            flags.pop("ali_post_quote_change_requested", None)
        # Merge only non-quote fields. Quote fields are exclusively owned by
        # the validated newest-change action on correction turns.
        for k, v in new_fields.items():
            if k in _ali_quote_merge_keys:
                continue
            if v is not None and v != "":
                fields[k] = v
            elif v == "" and k in fields:
                del fields[k]
        log_rental_change_decision(_ali_change_outcome, _ali_change_fields)
    else:
        for k, v in new_fields.items():
            if _ali_protect_quote_fields and k in _ali_quote_merge_keys:
                continue
            if v is not None and v != "":
                fields[k] = v
            elif v == "" and k in fields:
                del fields[k]
        if (
            _ali_change_configured
            and not _ali_pure_confirmation
            and not _ali_repair_reply
            and not _ali_location_request_this_turn
            and not _ali_hotel_detail_reply
            and isinstance(result.get("ali_vehicle_recommendation"), dict)
        ):
            try:
                changed_state, _ali_change_outcome, _ali_change_fields = (
                    apply_recommendation_selection_context(
                        fields,
                        result["ali_vehicle_recommendation"],
                        get_ali_intake_catalog(),
                    )
                )
            except Exception:
                changed_state = dict(fields)
                _ali_change_outcome = "clarify"
                _ali_change_fields = ()
            if _ali_change_outcome == "changed":
                fields.clear()
                fields.update(changed_state)
                invalidate_active_quote_summary(flags)
            log_rental_change_decision(
                _ali_change_outcome,
                _ali_change_fields,
            )

    # P0 #195: an explicit catalog-class discovery request is independently
    # actionable even when the model omits or misclassifies its structured
    # rental-change action. Resolve only an exact active catalog class and
    # apply it after model-field merging so a stale exact vehicle cannot win.
    if (
        _ali_change_configured
        and not _ali_selected_this_turn
        and not _ali_location_request_this_turn
        and not _ali_hotel_detail_reply
    ):
        try:
            _explicit_class = infer_explicit_catalog_class_selection(
                text,
                get_ali_intake_catalog(),
            )
        except Exception:
            _explicit_class = None
        if _explicit_class:
            _ali_explicit_class_this_turn = _explicit_class
            _before_selection = {
                key: fields.get(key)
                for key in VEHICLE_STATE_FIELDS
                if fields.get(key) not in (None, "")
            }
            for key in VEHICLE_STATE_FIELDS:
                fields.pop(key, None)
            fields.update(_explicit_class)
            _after_selection = {
                key: fields.get(key)
                for key in ("vehicle_class_id", "vehicle_class_name")
            }
            if _after_selection != _before_selection:
                _ali_change_outcome = "changed"
                _ali_change_fields = ("vehicle_selection",)
                invalidate_active_quote_summary(flags)
                log_rental_change_decision(
                    _ali_change_outcome,
                    _ali_change_fields,
                )

    if _ali_hotel_finished_this_turn:
        _ali_hotel_detail_reply = (
            next_ali_intake_question(fields)
            or hotel_delivery_completion_reply(fields)
        )
    elif _ali_fixed_location_finished_this_turn:
        _ali_hotel_detail_reply = next_ali_intake_question(fields)

    if _ali_change_outcome == "changed":
        flags.pop("ali_post_quote_change_requested", None)

    # Callback-follow-up tenants persist one evolving, tenant-local request.
    # The normal booking fields remain untouched so this capability is isolated
    # from every existing tenant workflow.
    _workflow = config_loader.get_raw().get("workflow", {}) or {}
    if _workflow.get("type") == "callback_follow_up":
        # Despertares wants the old operator-alert experience for every
        # actionable prospect queue state. Capture the state before enrichment
        # so repeated messages in the same state do not spam Roberto.
        _followup_alerts_enabled = _tenant_slug() == "consulta-despertares"
        _previous_followup = (
            state_registry.get_follow_up_request_by_conversation(phone)
            if _followup_alerts_enabled else None
        )
        _previous_followup_status = (
            _previous_followup.get("status") if _previous_followup else None
        )
        _first_name, _surnames = _callback_name_fields(fields)
        if not _first_name and not _surnames and from_name:
            _profile_name_parts = str(from_name).strip().split(maxsplit=1)
            _first_name = _profile_name_parts[0] if _profile_name_parts else ""
            _surnames = _profile_name_parts[1] if len(_profile_name_parts) > 1 else ""
        _phone_raw, _phone_normalized = _valid_callback_phone(
            fields.get("phone")
            or message.get("_zernio_sender_id")
            or phone)
        _followup_fields = {
            "first_name": _first_name,
            "surnames": _surnames,
            "phone_raw": _phone_raw,
            "phone_normalized": _phone_normalized,
            "callback_preference": fields.get("callback_preference", ""),
            # Only the dedicated field may populate the clinical visit reason.
            # Generic comments/special_requests often contain location or
            # scheduling notes and must never be relabelled as clinical context.
            "visit_reason": _callback_visit_reason(fields),
        }
        _followup = state_registry.upsert_follow_up_request(phone, channel, **_followup_fields)
        _target_status = _callback_follow_up_target_status(_followup, result)
        if _target_status != _followup.get("status"):
            _followup = state_registry.update_follow_up_status(
                _followup["id"], _target_status
            )
        if _followup_alerts_enabled:
            # Context fields live in the conversation state instead of the
            # follow_up_requests table. Include the just-extracted clinic in
            # the transition alert before the state record is saved below.
            _followup["preferred_clinic"] = str(
                fields.get("preferred_clinic") or ""
            ).strip()
            state_registry.dispatch_follow_up_alert(
                _followup,
                previous_status=_previous_followup_status,
            )
        bm_logger.log("callback_follow_up_updated", follow_up_id=_followup["id"],
                      status=_followup["status"])

    # Step 4: Merge flags — Python manages awaiting_booking_confirmation (set only)
    new_flags = result.get("flags", {}) or {}
    _was_awaiting = flags.get("awaiting_booking_confirmation", False)
    if new_flags.get("awaiting_booking_confirmation"):
        new_flags.pop("awaiting_booking_confirmation")
    flags.update(new_flags)
    _apply_wibrandt_catalog_pricing(fields)

    # Step 5: Change detection — cancel soft hold if customer changed booking details
    if (_was_awaiting and not flags.get("awaiting_booking_confirmation")
            and not flags.get("booking_confirmed")):
        if flags.get("hold_id"):
            state_registry.cancel_hold(flags["hold_id"])
            _h_svc = flags.pop("hold_service_key", "")
            _h_date = flags.pop("hold_date", "")
            _h_dep = flags.pop("hold_slot_time", "")
            flags.pop("hold_id", None)
            if _h_svc and _h_date and _h_dep:
                gws_calendar.remove_from_manifest(_h_svc, _h_date, _h_dep)
        flags["slot_checked"] = False
        flags["slot_available"] = False
        bm_logger.log("whatsapp_hold_cancelled", phone=phone,
                      reason="customer_changed_details")

    reply_text = reply

    # Wibrandt product order flow: confirmed orders are not bookings and do
    # not mean "customer needs reply". Once the customer confirms an order
    # summary, create a dedicated ORDER escalation for the operator to call.
    _skip_booking = False
    _ali_workflow_configured = ali_quote_tenant_configured()
    _ali_workflow_on = ali_quote_tenant_enabled()
    _ali_turn_plan = None
    recommendation_action = (
        None
        if (
            _ali_selected_this_turn
            or _ali_explicit_class_this_turn
            or _ali_pure_confirmation
            or _ali_location_request_this_turn
            or _ali_hotel_detail_reply
        )
        else result.get("ali_vehicle_recommendation")
    )
    media_first_status = "not_evaluated"
    media_first_reason = ""
    media_first_intent = ""
    _ali_capacity_advisory = False
    _ali_catalog_for_media = None
    if (
        channel == "whatsapp"
        and _ali_workflow_on
        and not _ali_reservation_confirmed
        and not result.get("requires_human")
        and not _ali_selected_this_turn
        and (
            include_media
            or bool(_ali_location_request_this_turn)
            or bool(_ali_hotel_detail_reply)
        )
    ):
        try:
            _ali_catalog_for_media = get_ali_intake_catalog()
            if _ali_location_request_this_turn:
                media_first_intent = "ask_question"
                recommendation_action = None
                reply_text = rental_location_options_reply(
                    _ali_location_request_this_turn,
                    fields,
                    _ali_catalog_for_media,
                )
                media_first_status = "rental_location"
                media_first_reason = (
                    f"explicit_{_ali_location_request_this_turn}_options"
                )
            elif _ali_hotel_detail_reply:
                media_first_intent = "ask_question"
                recommendation_action = None
                reply_text = _ali_hotel_detail_reply
                media_first_status = "hotel_delivery_details"
                media_first_reason = "sequential_hotel_delivery_intake"
            elif _ali_explicit_class_this_turn:
                _class_action = catalog_class_recommendation_action(
                    _ali_explicit_class_this_turn,
                    _ali_catalog_for_media,
                )
                if _class_action:
                    recommendation_action = _class_action
            if _ali_location_request_this_turn or _ali_hotel_detail_reply:
                pass
            elif _ali_repair_reply:
                media_first_intent = "continue_intake"
                recommendation_action = None
                reply_text = _ali_repair_reply
                media_first_status = "repair"
                media_first_reason = "customer_confusion_or_reengagement"
            else:
                media_first_intent = str(
                    result.get("ali_primary_intent") or ""
                ).strip().lower()
                _deterministic_media_intent = infer_media_first_intent(
                    text,
                    reply_text,
                    recommendation_action,
                    fields,
                    flags,
                    _ali_catalog_for_media,
                )
                if _deterministic_media_intent:
                    media_first_intent = _deterministic_media_intent
                media_first = derive_media_first_action(
                    media_first_intent,
                    recommendation_action,
                    reply_text,
                    fields,
                    flags,
                    _ali_catalog_for_media,
                    message_text=text,
                )
                media_first_status = str(media_first.get("status") or "")
                media_first_reason = str(media_first.get("reason") or "")
                if media_first_status == "planned":
                    recommendation_action = media_first["action"]
                    reply_text = str(media_first.get("reply_text") or reply_text)
                    _ali_capacity_advisory = bool(
                        media_first.get("capacity_advisory")
                    )
                elif media_first_status == "needs_context":
                    recommendation_action = None
                    reply_text = str(media_first.get("reply_text") or reply_text)
        except Exception as exc:
            media_first_status = "invalid"
            media_first_reason = type(exc).__name__
            if media_first_intent or isinstance(recommendation_action, dict):
                recommendation_action = None
                reply_text = media_first_clarification(fields)
            bm_logger.log(
                "ali_media_first_policy_failed",
                reason=media_first_reason[:80],
            )
    _ali_child_seat_offer = ""
    if _ali_workflow_on and not _ali_reservation_confirmed:
        try:
            _ali_child_seat_offer = proactive_child_seat_offer(
                text,
                fields,
                flags,
                _ali_catalog_for_media or get_ali_intake_catalog(),
            )
        except Exception as exc:
            bm_logger.log(
                "ali_child_seat_offer_failed",
                reason=type(exc).__name__,
            )
        if _ali_child_seat_offer:
            reply_text = _ali_child_seat_offer
            recommendation_action = None
            media_first_intent = "ask_question"
            flags["ali_child_seat_prompted"] = True
            bm_logger.log("ali_child_seat_offer_added")
        if not _ali_location_request_this_turn and not _ali_hotel_detail_reply:
            reply_text = enforce_vehicle_first_reply(reply_text, fields)
    _ali_effective_primary_intent = (
        "ask_question"
        if (
            _ali_child_seat_offer
            or _ali_location_request_this_turn
            or _ali_hotel_detail_reply
        )
        else "confirm_summary"
        if _ali_pure_confirmation and not _ali_selected_this_turn
        else (
            "continue_intake"
            if _ali_selected_this_turn
            else media_first_intent or result.get("ali_primary_intent")
        )
    )
    recommendation_requested = (
        isinstance(recommendation_action, dict)
        and not result.get("requires_human")
    )
    if (
        media_first_intent == "reject_or_hesitate"
        and flags.get("ali_last_recommendation_ids")
    ):
        rejected = [
            str(value)
            for value in flags.get("ali_rejected_vehicle_ids") or []
            if str(value).strip()
        ]
        for vehicle_id in flags.get("ali_last_recommendation_ids") or []:
            if str(vehicle_id).strip() and str(vehicle_id) not in rejected:
                rejected.append(str(vehicle_id))
        flags["ali_rejected_vehicle_ids"] = rejected[-20:]
    vehicle_recommendation = None
    if _ali_workflow_on and not _ali_reservation_confirmed:
        reply_text = sanitize_ali_intake_reply(
            reply_text,
            fields.get("conversation_language"),
            fields,
        )
        try:
            _ali_turn_plan = plan_ali_quote_turn(
                conversation_id=phone,
                zernio_account_id=str(message.get("_zernio_account_id") or ""),
                whatsapp_number=str(message.get("_zernio_sender_id") or phone),
                message_text=text,
                fields=fields,
                flags=flags,
                model_reply=reply_text,
                from_name=from_name,
                primary_intent=_ali_effective_primary_intent,
                requires_human=bool(result.get("requires_human")),
                recommendation_requested=recommendation_requested,
                summary_action=result.get("ali_summary_action"),
                change_outcome=_ali_change_outcome,
                changed_fields=_ali_change_fields,
                supplied_action_id=str(
                    message.get("_ali_action_id")
                    or message.get("message_id")
                    or ""
                ),
            )
            reply_text = _ali_turn_plan.text
        except Exception as exc:
            bm_logger.log(
                "ali_turn_plan_failed",
                error_code=type(exc).__name__,
            )
            _ali_turn_plan = fail_closed_turn_plan(
                phone,
                text,
                fields.get("conversation_language"),
                str(
                    message.get("_ali_action_id")
                    or message.get("message_id")
                    or ""
                ),
            )
            reply_text = _ali_turn_plan.text
    elif _ali_workflow_on:
        recommendation_action = None
        bm_logger.log(
            "ali_confirmed_reservation_model_reply_preserved",
            conversation_id=phone[:20],
        )
    if (
        include_media
        and channel == "whatsapp"
        and _ali_workflow_on
        and _ali_turn_plan is not None
        and _ali_turn_plan.outbound_kind == "vehicle_recommendation"
    ):
        deliveries = flags.get("ali_vehicle_recommendation_deliveries") or []
        already_delivered_for_action = any(
            isinstance(item, dict)
            and item.get("action_id") == _ali_turn_plan.action_id
            for item in deliveries
        )
        if already_delivered_for_action:
            bm_logger.log(
                "ali_vehicle_recommendation_suppressed",
                reason="already_delivered_for_this_turn",
                action_id_prefix=_ali_turn_plan.action_id[:12],
            )
            reply_text = ""
            _ali_turn_plan = None
        else:
            bm_logger.log(
                "ali_vehicle_recommendation_build_decision",
                deterministic_reason=media_first_reason[:80],
                capacity_advisory=_ali_capacity_advisory,
                recommendation_mode=str(
                    recommendation_action.get("mode") or ""
                )[:20],
            )
            try:
                vehicle_recommendation = build_vehicle_recommendation(
                    recommendation_action,
                    _ali_catalog_for_media or get_ali_intake_catalog(),
                    fields,
                    flags,
                    reply_text,
                    turn_id=str(message.get("message_id") or ""),
                    trigger_message_id=str(
                        message.get("_zernio_provider_message_id") or ""
                    ),
                    trigger_sent_at=str(
                        message.get("_zernio_sent_at") or ""
                    ),
                    allow_repeat=(
                        explicit_visual_request(text)
                        or explicit_catalog_browse_request(text)
                        or explicit_no_preference_request(text)
                        or explicit_smaller_vehicle_request(text)
                        or explicit_larger_vehicle_request(text)
                        or _ali_explicit_class_this_turn is not None
                    ),
                    capacity_advisory=_ali_capacity_advisory,
                )
            except AliVehicleRecommendationError as exc:
                media_first_status = "invalid"
                media_first_reason = str(exc)
                reply_text = media_first_clarification(fields)
                bm_logger.log(
                    "ali_vehicle_recommendation_rejected",
                    reason=str(exc)[:80],
                    mode=str(recommendation_action.get("mode") or "")[:20],
                )
            if vehicle_recommendation:
                reply_text = vehicle_recommendation["text"]
                _ali_turn_plan = replace(_ali_turn_plan, text=reply_text)
            else:
                _ali_turn_plan = replace(
                    _ali_turn_plan,
                    outbound_kind="agent_reply",
                    text=reply_text,
                    phase="DISCOVERY",
                    reason_code="recommendation_invalid",
                )
    if _ali_workflow_on:
        bm_logger.log(
            "ali_summary_route_decision",
            route=(
                _ali_turn_plan.outbound_kind
                if _ali_turn_plan is not None else "model_reply"
            ),
            change_outcome=_ali_change_outcome,
            media_first_status=media_first_status,
            media_first_reason=media_first_reason[:80],
        )
    if _ali_workflow_configured:
        # The generic booking engine must never create holds or contact redirects
        # for Ali, including while its master automation switch is off.
        _skip_booking = True
    # A callback-follow-up tenant never enters the generic booking engine:
    # its human team coordinates appointments after the callback instead.
    _callback_workflow_on = (_workflow.get("type") == "callback_follow_up")
    if _callback_workflow_on:
        _skip_booking = True
    _wibrandt_order_like = _is_wibrandt_order_like(result, fields, flags)
    if _wibrandt_order_like:
        if flags.get("order_confirmed") and not flags.get("order_escalation_id"):
            reply_text = (
                "Perfect 💛 We've received your order.\n\n"
                "We'll give you a call shortly to confirm the details and delivery.\n\n"
                "Thank you for choosing Wibrandt."
            )
            _create_wibrandt_order_escalation(
                channel, phone, _channel_label, fields, flags, from_name, history, result)
            _skip_booking = True
        elif _has_order_required_fields(fields) and not flags.get("awaiting_order_confirmation"):
            flags["awaiting_order_confirmation"] = True
            flags.pop("booking_confirmed", None)
            if _wibrandt_order_has_complete_pricing(fields):
                reply_text = _build_wibrandt_order_summary_reply(fields)
            elif "look correct" not in reply_text.lower() and "everything correct" not in reply_text.lower():
                reply_text = reply_text.rstrip() + "\n\nDoes everything look correct?"
            _skip_booking = True
        elif (
            _has_order_required_fields(fields)
            and flags.get("awaiting_order_confirmation")
            and not flags.get("order_confirmed")
            and _wibrandt_order_has_complete_pricing(fields)
            and (_looks_like_price_question(text) or _reply_defers_known_price(reply_text))
        ):
            reply_text = _build_wibrandt_order_summary_reply(fields)
            _skip_booking = True
        elif result.get("intents") and any(i in _ORDER_INTENTS for i in result.get("intents", [])):
            flags.pop("booking_confirmed", None)
            _skip_booking = True

    # Step 6: Post-validation (booking intents only)
    _pv_service_key = fields.get("service_key", "")
    _pv_service = config_loader.get_service(_pv_service_key) if _pv_service_key else {}
    _run_pv = (not _skip_booking and any(i in _BOOKING_INTENTS for i in result.get("intents", [])))
    # Guard: if customer was responding to a booking summary and didn't change
    # any booking fields, skip post-validate to prevent decline loop
    if _run_pv and _was_awaiting and not flags.get("booking_confirmed"):
        _new_f = result.get("fields", {}) or {}
        if not any(_new_f.get(k) for k in ("service_name", "date", "guests", "service_key", "slot_time")):
            _run_pv = False
    if _run_pv:
        # Brief 161: _post_validate no longer returns reply text — Marina
        # writes all booking-flow replies in the customer's language via her
        # prompt. This step only decides whether to advance state.
        _pv_override, _pv_set_awaiting = _post_validate(fields, flags, result, _pv_service)
        if _pv_set_awaiting:
            flags["awaiting_booking_confirmation"] = True

    _booking_flow_on = config_loader.get_raw().get("features", {}).get("booking_flow", True)

    # Step 7: Availability pre-check + soft hold (SKIP when booking_flow is OFF)
    if (_booking_flow_on
            and flags.get("awaiting_booking_confirmation")
            and not flags.get("slot_checked")):
        _ck_svc = fields.get("service_key", "")
        _ck_deps = config_loader.get_service(_ck_svc).get("slots", []) if _ck_svc else []
        _ck_start = (fields.get("slot_time")
                     or (_ck_deps[0].get("time", "09:00") if _ck_deps else "09:00"))
        _ck_guests = int(fields.get("guests") or 1)
        _svc_capacity = config_loader.get_service(_ck_svc).get("capacity", 20) if _ck_svc else 20
        if _ck_guests > _svc_capacity:
            # Group exceeds capacity — escalate, don't check availability
            flags["slot_checked"] = True
            flags["slot_available"] = False
            flags["awaiting_booking_confirmation"] = False
            _cname = fields.get("customer_name") or from_name or "Unknown"
            state_registry.create_pending_notification(
                'escalation', channel, phone, _cname,
                f"[LARGE GROUP] {_cname} ({_channel_label}: {phone}) — {_ck_guests} guests exceeds {_svc_capacity} capacity",
                (f"=== LARGE GROUP — EXCEEDS CAPACITY ===\n"
                 f"Customer: {_cname}\nPhone: {phone}\n"
                 f"Service: {fields.get('service_name', _ck_svc)}\n"
                 f"Date: {fields.get('date', '?')}\n"
                 f"Guests: {_ck_guests} (capacity: {_svc_capacity})\n\n"
                 f"Group exceeds standard capacity. Contact customer to discuss options."),
                mode="soft")
            bm_logger.log("whatsapp_large_group_exceeds_capacity", phone=phone,
                          guests=_ck_guests, capacity=_svc_capacity,
                          service_key=_ck_svc)
            # Use Marina's original conversational reply (not the booking summary)
            reply_text = reply
        else:
            avail = gws_calendar.check_availability(
                _ck_svc, fields.get("date", ""), _ck_start, _ck_guests)
            flags["slot_checked"] = True
            flags["slot_available"] = avail.get("available", False)
            flags["spots_remaining"] = avail.get("spots_remaining", 0)
            flags["trip_capacity"] = avail.get("capacity", 0)
            if avail.get("available"):
                hold_id = state_registry.create_soft_hold(
                    _ck_svc,
                    fields.get("date", ""),
                    _ck_start,
                    _ck_guests,
                    avail.get("capacity", 20),
                    customer_name=fields.get("customer_name", ""),
                    customer_email=fields.get("email") or phone,
                )
                if hold_id is not None:
                    flags["hold_id"] = hold_id
                    flags["hold_service_key"] = _ck_svc
                    flags["hold_date"] = fields.get("date", "")
                    flags["hold_slot_time"] = _ck_start
                    bm_logger.log("whatsapp_soft_hold_created", phone=phone,
                                  hold_id=hold_id, service_key=_ck_svc)
                else:
                    # Race: capacity was grabbed between check and insert
                    flags["slot_available"] = False
                    flags["awaiting_booking_confirmation"] = False
                    flags["slot_checked"] = False
                    _unavail_name = _pv_service.get("display_name", _ck_svc)
                    reply_text = (
                        f"Unfortunately the {_unavail_name} is fully booked on that date. "
                        f"Would you like to try a different date?"
                    )
                    bm_logger.log("whatsapp_soft_hold_race", phone=phone, service_key=_ck_svc)
            else:
                flags["awaiting_booking_confirmation"] = False
                flags["slot_checked"] = False
                _unavail_name = _pv_service.get("display_name", _ck_svc)
                reply_text = (
                    f"Unfortunately the {_unavail_name} is fully booked on that date. "
                    f"Would you like to try a different date?"
                )
                bm_logger.log("whatsapp_slot_unavailable", phone=phone, service_key=_ck_svc,
                              spots=avail.get("spots_remaining", 0))

    # Step 7.5: Semi-escalation → create relay (operator notified via email poller)
    if result.get("semi_escalation"):
        relay_question = result.get("relay_question", "(no question captured)")
        # Cancel any soft hold (capacity leak prevention)
        if flags.get("hold_id"):
            state_registry.cancel_hold(flags["hold_id"])
            _h_svc = flags.pop("hold_service_key", "")
            _h_date = flags.pop("hold_date", "")
            _h_dep = flags.pop("hold_slot_time", "")
            flags.pop("hold_id", None)
            if _h_svc and _h_date and _h_dep:
                gws_calendar.remove_from_manifest(_h_svc, _h_date, _h_dep)
        flags["slot_checked"] = False
        flags["slot_available"] = False
        flags["awaiting_booking_confirmation"] = False
        # Set relay flags (proper relay bridge, not promote to full)
        relay_token = uuid.uuid4().hex[:12]
        flags["awaiting_relay"] = True
        flags["relay_token"] = relay_token
        flags["relay_question"] = relay_question
        reply_text = result["reply"]
        # Build relay alert for operator
        _ref = flags.get("booking_ref") or flags.get("returning_booking") or "NO-REF"
        _cname = fields.get("customer_name") or from_name or "Unknown"
        _alert_subject = f"[RELAY-{relay_token}] {_ref} - {_cname}"
        _alert_body = (
            f"Customer: {_cname} ({_channel_label}: {phone})\n"
            f"Their question: {relay_question}\n\n"
            f"Booking context:\n"
            f"  Trip: {fields.get('service_key', '')} | "
            f"Date: {fields.get('date', '')} | "
            f"Guests: {fields.get('guests', '')}\n"
            f"  Ref: {_ref}\n\n"
            f"INSTRUCTIONS: Reply to this email with your answer.\n"
            f"Marina will relay it to the customer in her own words."
        )
        state_registry.create_pending_notification(
            'relay', channel, phone, _cname,
            _alert_subject, _alert_body, relay_token=relay_token)
        sheets_writer.log_escalation({
            "email": phone,
            "subject": _channel_label,
            "customer_name": _cname,
            "intent": "semi_escalation",
            "fields_collected": fields,
            "internal_note": f"Relay question: {relay_question}",
            "messages_json": json.dumps(history, ensure_ascii=False) if history else "[]",
        })
        bm_logger.log("whatsapp_semi_escalation", phone=phone,
                      relay_question=relay_question, relay_token=relay_token)
        state_registry.wa_store_message(phone, "system", f"Relay question sent to team: {relay_question}")
        _skip_booking = True

    # Step 7.5: Awaiting escalation email — email provided, now fire escalation
    if flags.get("awaiting_escalation_email") and fields.get("email"):
        flags.pop("awaiting_escalation_email", None)
        flags.pop("needs_escalation_email", None)
        result["requires_human"] = True
        bm_logger.log("whatsapp_escalation_email_received", phone=phone,
                      email=fields.get("email", "")[:50])

    # Step 7.55: Needs escalation email — hold escalation, ask for email
    if not _skip_booking and result.get("flags", {}).get("needs_escalation_email"):
        flags["awaiting_escalation_email"] = True
        reply_text = result["reply"]
        _skip_booking = True

    # Step 7.6: Full escalation — requires_human, holding reply to customer
    if not _skip_booking and result.get("requires_human"):
        # Cancel any soft hold (same pattern as semi-escalation — capacity leak prevention)
        if flags.get("hold_id"):
            state_registry.cancel_hold(flags["hold_id"])
            _h_svc = flags.pop("hold_service_key", "")
            _h_date = flags.pop("hold_date", "")
            _h_dep = flags.pop("hold_slot_time", "")
            flags.pop("hold_id", None)
            if _h_svc and _h_date and _h_dep:
                gws_calendar.remove_from_manifest(_h_svc, _h_date, _h_dep)
        flags["slot_checked"] = False
        flags["slot_available"] = False
        flags["fully_escalated"] = True
        flags["awaiting_booking_confirmation"] = False
        reply_text = result["reply"]  # Claude's warm holding reply
        _cname = fields.get("customer_name") or from_name or "Unknown"
        sheets_writer.log_escalation({
            "email": phone,
            "subject": _channel_label,
            "customer_name": _cname,
            "intent": (result.get("intents") or ["unknown"])[0],
            "fields_collected": fields,
            "internal_note": result.get("internal_note", ""),
            "messages_json": json.dumps(history, ensure_ascii=False) if history else "[]",
        })
        bm_logger.log("whatsapp_full_escalation", phone=phone,
                      intents=result.get("intents", []))
        _esc_intent = (result.get("intents") or ["unknown"])[0]
        state_registry.wa_store_message(phone, "system", f"Escalated to human: {_esc_intent}")
        # Build escalation alert for operator
        _esc_ref = flags.get("booking_ref") or flags.get("returning_booking") or "NO-REF"
        _esc_intents = ", ".join(result.get("intents") or ["unknown"])
        _esc_history = state_registry.wa_get_history(phone, limit=20)
        _esc_chat_lines = []
        for _em in _esc_history:
            _esc_chat_lines.append(
                f"[{_em['role'].upper()} | {_em.get('created_at', '')}]")
            _esc_chat_lines.append(_em.get("text", ""))
            _esc_chat_lines.append("---")
        _esc_chat_log = "\n".join(_esc_chat_lines) or "(no messages logged)"
        _esc_note = result.get("internal_note", "").strip()
        _esc_summary = _esc_note if _esc_note else _esc_intents
        _esc_subject = (
            f"[ESCALATION] {_esc_ref} - {_cname} "
            f"({_channel_label}: {phone}) - {_esc_summary}")
        _customer_email = fields.get("email", "")
        _esc_body = (
            f"=== CUSTOMER ===\n"
            f"{_channel_label}: {phone}\n"
            f"Name: {_cname}\n"
            f"Email: {_customer_email or '(not provided)'}\n\n"
            f"=== CHAT LOG ===\n{_esc_chat_log}\n\n"
            f"=== BOOKING FIELDS ===\n"
            f"{json.dumps(fields, indent=2, ensure_ascii=False)}\n\n"
            f"=== MARINA'S INTERNAL NOTE ===\n"
            f"{result.get('internal_note', '')}"
        )
        state_registry.create_pending_notification(
            'escalation', channel, phone, _cname,
            _esc_subject, _esc_body, mode="hard")
        _skip_booking = True

    # Step 7.8: Booking flow toggle — if OFF, escalate booking intents instead
    if not _skip_booking and not _booking_flow_on:
        if any(i in _BOOKING_INTENTS for i in result.get("intents", [])):
            if fields.get("service_name") or fields.get("date") or fields.get("guests"):
                _cname = fields.get("customer_name", phone)
                _customer_email = fields.get("email", "")
                _esc_msgs = state_registry.wa_get_full_history(phone, limit=20)
                _esc_chat_lines = []
                for _em in _esc_msgs:
                    _esc_chat_lines.append(
                        f"[{_em['role'].upper()} | {_em.get('created_at', '')}]")
                    _esc_chat_lines.append(_em.get("text", ""))
                    _esc_chat_lines.append("---")
                _esc_chat_log = "\n".join(_esc_chat_lines) or "(no messages logged)"
                _esc_note = result.get("internal_note", "")
                _esc_subject = (
                    f"[BOOKING REQUEST] {_cname} "
                    f"({_channel_label}: {phone}) - {_esc_note or 'wants to book'}")
                _esc_body = (
                    f"=== BOOKING REQUEST (booking_flow OFF) ===\n\n"
                    f"=== CUSTOMER ===\n"
                    f"{_channel_label}: {phone}\n"
                    f"Name: {_cname}\n"
                    f"Email: {_customer_email or '(not provided)'}\n\n"
                    f"=== COLLECTED FIELDS ===\n"
                    f"{json.dumps(fields, indent=2, ensure_ascii=False)}\n\n"
                    f"=== CHAT LOG ===\n{_esc_chat_log}\n\n"
                    f"=== MARINA'S NOTE ===\n{_esc_note}"
                )
                state_registry.create_pending_notification(
                    'escalation', channel, phone, _cname,
                    _esc_subject, _esc_body, mode="soft")
                bm_logger.log("booking_flow_off_escalated", phone=phone)
                _skip_booking = True

    # Step 8: Booking confirmation flow (skip if escalated)
    if not _skip_booking and any(i in _BOOKING_INTENTS for i in result.get("intents", [])):
        if (fields.get("service_name") and fields.get("date")
                and fields.get("guests") and fields.get("service_key")
                and flags.get("booking_confirmed")
                and not flags.get("hold_created")):
            bm_logger.log("whatsapp_booking_attempted", phone=phone,
                          service_key=fields.get("service_key"),
                          date=fields.get("date"), guests=fields.get("guests"))

            # Generate booking_ref + set on soft hold BEFORE manifest creation
            _chars = string.ascii_uppercase + string.digits
            booking_ref = ''.join(random.choices(_chars, k=6))
            flags["booking_ref"] = booking_ref
            if flags.get("hold_id"):
                state_registry.set_booking_ref(flags["hold_id"], booking_ref)

            res = gws_calendar.create_or_update_manifest(fields)
            if not res.get("ok"):
                _manifest_error = str(res.get("error", ""))
                _is_api_error = any(s in _manifest_error for s in (
                    '"code": 404', '"code": 500', '"code": 403', '"code": 401',
                    "'code': 404", "'code': 500", "'code': 403", "'code': 401",
                    'Calendar ID not configured'))
                bm_logger.log("whatsapp_manifest_failed", phone=phone,
                              error=_manifest_error[:200],
                              error_type="api" if _is_api_error else "business")
                if flags.get("hold_id"):
                    state_registry.cancel_hold(flags["hold_id"])
                    _h_svc = flags.pop("hold_service_key", "")
                    _h_date = flags.pop("hold_date", "")
                    _h_dep = flags.pop("hold_slot_time", "")
                    flags.pop("hold_id", None)
                    if _h_svc and _h_date and _h_dep:
                        gws_calendar.remove_from_manifest(_h_svc, _h_date, _h_dep)
                flags["slot_checked"] = False
                flags["slot_available"] = False
                if _is_api_error:
                    _retry_count = flags.get("manifest_retry_count", 0) + 1
                    flags["manifest_retry_count"] = _retry_count
                    if _retry_count >= 2:
                        _cname = fields.get("customer_name") or from_name or "Unknown"
                        state_registry.create_pending_notification(
                            'escalation', channel, phone, _cname,
                            f"[SYSTEM] Manifest failure for {_cname} ({_channel_label}: {phone})",
                            f"Booking failed {_retry_count} times due to API error.\n"
                            f"Error: {_manifest_error[:300]}\n"
                            f"Fields: {json.dumps(fields, indent=2, ensure_ascii=False)}",
                            mode="hard")
                        bm_logger.log("whatsapp_manifest_escalated", phone=phone,
                                      retry_count=_retry_count)
                    flags["booking_confirmed"] = False
                    flags["awaiting_booking_confirmation"] = True
                reply_text = result.get("reply_hold_failed") or reply_text
                sheets_writer.log_hold_failed({
                    "email": phone, "subject": _channel_label,
                    "service_name": fields.get("service_name"),
                    "date": fields.get("date"),
                    "guests": fields.get("guests"),
                    "error": _manifest_error[:200],
                })
            else:
                flags.pop("manifest_retry_count", None)
                flags["hold_created"] = True
                if flags.get("hold_id"):
                    state_registry.confirm_hold(flags["hold_id"])
                    # Brief 168: set payment window if the client has configured one
                    # AND the service requires payment (timing=upfront/deposit).
                    _raw_for_window = config_loader.get_raw() or {}
                    _pt_for_window = _raw_for_window.get("payment", {}).get("timing", "upfront")
                    _hold_hours = _raw_for_window.get("payment", {}).get("hold_duration_hours")
                    if _pt_for_window in ("upfront", "deposit") and _hold_hours:
                        try:
                            from datetime import datetime as _dt, timezone as _tz, timedelta as _td
                            _payment_expires_at = (
                                _dt.now(_tz.utc) + _td(hours=float(_hold_hours))
                            ).isoformat()
                            state_registry.set_payment_window(
                                flags["hold_id"], _payment_expires_at,
                                customer_phone=str(phone or "")
                            )
                            flags["payment_expires_at"] = _payment_expires_at
                        except Exception as _e:
                            bm_logger.log("payment_window_set_failed",
                                          phone=phone, error=str(_e))
                flags["event_id"] = res.get("eventId")
                flags["event_link"] = res.get("htmlLink")
                service_key = fields.get("service_key", "")
                price_usd = (config_loader.get_service(service_key).get("price", 0)
                             if service_key else 0)
                reply_text = reply_text.replace("[BOOKING_REF]", booking_ref)

                # Payment timing: only generate link for upfront/deposit
                _payment_timing = config_loader.get_raw().get("payment", {}).get("timing", "upfront")
                if _payment_timing in ("upfront", "deposit"):
                    pay = payment_stub.generate_payment_link(booking_ref, price_usd)
                    pay_link = f"https://demo.pay/{pay['payment_id']}"
                    flags["payment_id"] = pay.get("payment_id")
                    flags["payment_link"] = pay_link
                    flags["payment_status"] = pay.get("status")
                    reply_text = reply_text.replace("[PAYMENT_LINK]", pay_link)
                else:
                    reply_text = reply_text.replace("[PAYMENT_LINK]", "")
                    flags["payment_status"] = "not_required"
                bm_logger.log("whatsapp_booking_confirmed", phone=phone,
                              booking_ref=booking_ref, service_key=service_key)
                # Brief 163: wording depends on payment state.
                # If payment is required (upfront/deposit), the hold is placed but the booking
                # is NOT yet confirmed — say "Hold placed — awaiting payment" so the dashboard
                # tag stays amber until the payment webhook fires (Brief 168).
                # If no payment is required (timing="none", e.g. restaurant reservations), the
                # hold IS the confirmation — keep the "Booking confirmed" wording.
                if _payment_timing in ("upfront", "deposit"):
                    _system_msg = (f"Hold placed — awaiting payment: "
                                   f"{fields.get('service_name', service_key)}, "
                                   f"{fields.get('date', '')}, "
                                   f"{fields.get('guests', '')} guests (Ref: {booking_ref})")
                else:
                    _system_msg = (f"Booking confirmed: "
                                   f"{fields.get('service_name', service_key)}, "
                                   f"{fields.get('date', '')}, "
                                   f"{fields.get('guests', '')} guests (Ref: {booking_ref})")
                state_registry.wa_store_message(phone, "system", _system_msg)
                sheets_writer.log_hold_created({
                    "booking_ref": booking_ref,
                    "email": phone, "subject": _channel_label,
                    "customer_name": fields.get("customer_name"),
                    "service_name": fields.get("service_name"),
                    "service_key": fields.get("service_key"),
                    "date": fields.get("date"),
                    "guests": fields.get("guests"),
                    "slot_time": fields.get("slot_time"),
                    "phone": phone,
                    "special_requests": fields.get("special_requests"),
                    "total_price": int(fields.get("guests") or 0) * price_usd,
                    "html_link": flags.get("event_link"),
                    "payment_link": flags.get("payment_link"),
                    "payment_status": flags.get("payment_status", ""),
                })
                # Log manifest summary to Sheets
                _m_passengers = state_registry.get_slot_passengers(
                    service_key, fields.get("date", ""), fields.get("slot_time", ""))
                _m_confirmed = sum(1 for p in _m_passengers if p["status"] == "confirmed")
                _m_pending = sum(1 for p in _m_passengers if p["status"] == "soft_hold")
                _m_total_guests = sum(p["guests"] for p in _m_passengers)
                _m_total_revenue = _m_total_guests * price_usd
                _m_capacity = config_loader.get_service(service_key).get("capacity", 20)
                sheets_writer.log_manifest_update({
                    "service_key": service_key,
                    "date": fields.get("date", ""),
                    "slot_time": fields.get("slot_time", ""),
                    "total_guests": _m_total_guests,
                    "capacity": _m_capacity,
                    "confirmed_count": _m_confirmed,
                    "pending_count": _m_pending,
                    "total_revenue": _m_total_revenue,
                    "calendar_link": flags.get("event_link", ""),
                    "booking_ref": booking_ref,
                })
                # Save booking for cross-thread memory
                state_registry.save_booking(
                    booking_ref, fields, flags,
                    customer_email=fields.get("email") or phone,
                )

                # Large group notification — operator review after auto-confirm
                _lg_threshold = config_loader.get_booking_rules().get("group_threshold_requires_human", 15)
                _lg_guests = int(fields.get("guests", 0) or 0)
                if _lg_guests >= _lg_threshold:
                    _lg_ref = flags.get("booking_ref", "NO-REF")
                    _lg_name = fields.get("customer_name", "Unknown")
                    _lg_note = (f"Large group booking: {_lg_guests} guests for "
                                f"{fields.get('service_name', '?')} on {fields.get('date', '?')}. "
                                f"Ref: {_lg_ref}. Auto-confirmed — operator review recommended.")
                    state_registry.create_pending_notification(
                        'escalation', channel, phone, _lg_name,
                        f"[LARGE GROUP] {_lg_ref} - {_lg_name} ({_channel_label}: {phone}) - {_lg_note}",
                        (f"=== LARGE GROUP BOOKING ===\n"
                         f"Ref: {_lg_ref}\nGuests: {_lg_guests}\n"
                         f"Trip: {fields.get('service_name', '?')}\n"
                         f"Date: {fields.get('date', '?')}\n"
                         f"Customer: {_lg_name}\nPhone: {phone}\n"
                         f"Email: {fields.get('email', 'not provided')}\n\n"
                         f"This booking was auto-confirmed. Review and adjust if needed."),
                        mode="soft")
                    state_registry.wa_store_message(phone, "system",
                        f"Large group booking ({_lg_guests} guests) — operator notified for review")
                    bm_logger.log("large_group_booking", phone=phone,
                                  guests=str(_lg_guests), booking_ref=_lg_ref)

    # Step 9: Strip remaining placeholders (safety net)
    reply_text = reply_text.replace("[BOOKING_REF]", "").replace("[PAYMENT_LINK]", "")

    # Consulta Despertares' first greeting and callback closing are mandatory
    # output boundaries, not optional style guidance. Apply them after every
    # model/booking rewrite so no later branch can remove or misplace them.
    _reply_before_tenant_boundaries = reply_text
    _boundary_history = history
    if tenant_hard_rules.is_consulta_despertares():
        # The prompt uses a short recent window, but a hard anti-repetition
        # guard must consider the whole active timeline. Otherwise a question
        # can return as soon as it falls out of the ten-message prompt window.
        try:
            _boundary_history = state_registry.wa_get_full_history(phone, limit=200)
        except Exception:
            _boundary_history = history
    reply_text = tenant_hard_rules.enforce_consulta_despertares_boundaries(
        reply=reply_text,
        inbound_text=text,
        history=_boundary_history,
        fields=fields,
        intents=result.get("intents", []),
    )
    if reply_text != _reply_before_tenant_boundaries:
        bm_logger.log(
            "consulta_despertares_mandatory_boundaries_applied",
            phone=phone,
            first_reply=not any(
                str(message.get("role") or "").lower() == "assistant"
                for message in (history or [])
                if isinstance(message, dict)
            ),
        )

    if _ali_first_customer_turn and reply_text:
        reply_text = add_first_turn_welcome(reply_text, fields)
        flags["ali_welcome_sent"] = True
        if _ali_turn_plan is not None:
            _ali_turn_plan = replace(_ali_turn_plan, text=reply_text)
        bm_logger.log("ali_first_turn_welcome_added")

    selected_media = None
    if (
        include_media
        and channel == "whatsapp"
        and not _ali_workflow_on
        and not result.get("requires_human")
        and not isinstance(recommendation_action, dict)
    ):
        selected_media = _select_customer_media(text, reply_text, fields, flags, history)
        if selected_media:
            flags["last_media_id_sent"] = selected_media["id"]
            reply_text = _strip_media_fallback_links(reply_text)
            bm_logger.log(
                "customer_media_selected",
                phone=phone,
                media_id=selected_media["id"],
                filename=selected_media["filename"][:120],
                score=selected_media["score"],
            )

    # Record reply timestamp for anti-loop tracking
    if reply_text:
        _reply_times = flags.get("reply_times", [])
        _reply_times.append(int(time.time()))
        flags["reply_times"] = _reply_times

    # Persist state + log
    _upsert_appointment_signal(reply_text)
    state_registry.wa_save_booking_state(phone, fields, flags, completed_bookings)
    bm_logger.log("whatsapp_agent_reply", phone=phone,
                  intents=result.get("intents", []), reply_length=len(reply_text))

    if include_media:
        quote_confirmation = None
        if (
            _ali_turn_plan is not None
            and _ali_turn_plan.outbound_kind == "summary"
        ):
            quote_confirmation = build_quote_confirmation_control(
                phone, _ali_turn_plan,
                locale=fields.get("conversation_language") or "en",
            )
        return {
            "text": reply_text,
            "media": selected_media,
            "vehicle_recommendation": vehicle_recommendation,
            "quote_confirmation": quote_confirmation,
            "ali_turn_commit": (
                _ali_turn_plan.delivery_commit()
                if _ali_turn_plan is not None else None
            ),
        }
    return reply_text
