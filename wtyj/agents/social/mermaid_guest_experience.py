"""Shared guest-facing facts for chat, checkout and reservation documents."""
from datetime import datetime
from shared import mermaid_catalog
from agents.social.mermaid_reservation_store import _money_snapshot


def guest_copy(locale):
    copies = mermaid_catalog.get_catalog()["guest_copy"]
    return copies.get(locale, copies["en"])


def pickup_label(money, locale):
    copy = guest_copy(locale)
    plan = money.get("pickup_plan") or {}
    if plan.get("vehicle_key"):
        return copy["pickup_" + plan["vehicle_key"]].format(capacity=plan["vehicle_capacity"])
    return copy["pickup_line"]


def transport_text(intake, locale, money=None):
    copy = guest_copy(locale)
    if intake.get("pickup_preference") == "pickup_requested":
        money = intake_money(intake) if money is None else money
        plan = money.get("pickup_plan") or {}
        if money.get("pickup_amount") is not None:
            if plan.get("vehicle_key"):
                return copy["pickup_vehicle_priced"].format(
                    location=intake.get("pickup_location") or copy["hotel"],
                    pickup_time=mermaid_catalog.pickup_time(),
                    vehicle=pickup_label(money, locale), quantity=plan["quantity"],
                    currency=money["currency"], amount=f"{money['pickup_amount']:,.2f}",
                )
            return copy["pickup_priced"].format(
                location=intake.get("pickup_location") or copy["hotel"],
                currency=money["currency"], amount=f"{money['pickup_amount']:,.2f}",
                pickup_time=mermaid_catalog.pickup_time(),
            )
        if plan.get("status") in {"requires_review", "awaiting_guest_count"}:
            return copy["pickup_" + plan["status"]]
        return copy["pickup_pending"].format(location=intake.get("pickup_location") or copy["hotel"])
    service = mermaid_catalog.get_catalog()["service"]
    return copy["pier_arrival"].format(place=service["meeting_point"], time=service["arrival_time"])


def price_text(money, intake, locale):
    copy = guest_copy(locale)
    label = copy["trip_total"]
    if intake.get("pickup_preference") == "pickup_requested":
        label = copy["pickup_total"] if money.get("pickup_amount") is not None else copy["trip_only"]
    return f"{label}: {money['currency']} {int(money['total']):,.2f}"


def intake_money(intake):
    return _money_snapshot(intake, mermaid_catalog.get_catalog())


def guest_date(value, locale="en"):
    # Both weekday and date are calculated, never supplied by model prose.
    if locale == "en":
        return datetime.strptime(value, "%Y-%m-%d").strftime("%A %d %B %Y").replace(" 0", " ")
    from agents.social.mermaid_response_policy import date_label
    return date_label(value, locale)


def party_text(intake, locale):
    """Professional summary wording; exact ages are facts, fare bands are fallback."""
    from shared.mermaid_guest_ages import normalize_child_ages, age_band, age_months
    copy = guest_copy(locale)
    formal = copy.get("formal_party")
    if not formal:
        return ", ".join((copy.get(key + "_one", copy[key]) if intake.get(key) == 1 else copy[key]).format(count=intake[key])
                         for key in ("adults", "children", "infants") if intake.get(key))
    parts = []
    ages = normalize_child_ages(intake.get("child_ages", []), intake) or []
    # Adult fare starts at 13; a teenager is still described with their age.
    adults = intake.get("adults", 0) - sum(age_band(age) == "adults" for age in ages)
    if adults:
        parts.append(formal["adult_one" if adults == 1 else "adults"].format(count=adults))
    for band in ("adults", "children", "infants"):
        known = [age for age in ages if age_band(age) == band]
        for kind in ("child", "infant"):
            group = [age for age in known if ("infant" if age_months(age) < 12 else "child") == kind]
            if group:
                age_text = ", ".join(formal[age["unit"] + ("_one" if age["value"] == 1 else "")].format(value=age["value"]) for age in group)
                label = formal[kind + ("_one" if len(group) == 1 else "s")].format(count=len(group))
                parts.append(f"{label} ({age_text})")
        unknown = 0 if band == "adults" else intake.get(band, 0) - len(known)
        if unknown > 0:
            parts.append(formal[band + "_unknown" + ("_one" if unknown == 1 else "")].format(count=unknown))
    return " · ".join(parts)


def display_reference(value):
    """Preserve the unique suffix while hiding the legacy environment prefix."""
    return str(value or "").replace("MER-DEMO-", "MER-", 1).replace("PAY-DEMO-", "PAY-", 1)
