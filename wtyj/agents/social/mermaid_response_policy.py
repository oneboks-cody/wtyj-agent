"""Catalog calendar and recorded-state replies; the model only selects a route."""
from __future__ import annotations

import hashlib
import json
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from shared import config_loader, mermaid_catalog, state_registry

WEEKDAYS = ('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday')
CALENDAR_REQUESTS = {'this_week', 'next_week', 'weekend', 'next_seven_days', 'operating_days'}


def policy() -> dict:
    configured = Path(config_loader._CONFIG_PATH).with_name('response_policy.json')
    bundled = Path(__file__).with_suffix('.json')
    source = Path(__file__).resolve().parents[3] / 'clients/mermaid/config/response_policy.json'
    path = next((p for p in (configured, bundled, source) if p.is_file()), configured)
    return json.loads(path.read_text(encoding='utf-8'))


def copy(key: str, locale: str) -> str:
    copies = policy()['copies']
    return copies.get(locale, copies['en'])[key]


def local_today(now: datetime | None = None) -> date:
    return (now or datetime.now(ZoneInfo('America/Curacao'))).astimezone(ZoneInfo('America/Curacao')).date()


def calendar_dates(request: str, *, today: date | None = None, catalog: dict | None = None) -> list[date]:
    today = today or local_today()
    monday = today - timedelta(days=today.weekday())
    if request == 'this_week':
        start, end = today, monday + timedelta(days=7)
    elif request == 'next_week':
        start, end = monday + timedelta(days=7), monday + timedelta(days=14)
    elif request == 'weekend':
        start, end = max(today, monday + timedelta(days=5)), monday + timedelta(days=7)
    elif request == 'next_seven_days':
        start, end = today, today + timedelta(days=7)
    else:
        return []
    days = (catalog or mermaid_catalog.get_catalog())['service']['operating_weekdays']
    return [start + timedelta(days=i) for i in range((end - start).days)
            if WEEKDAYS[(start + timedelta(days=i)).weekday()] in days]


def date_label(value: str | date, locale: str, *, include_weekday: bool = True) -> str:
    day = date.fromisoformat(value) if isinstance(value, str) else value
    configured = policy()
    names, months = configured['weekdays'], configured['months']
    formats = configured.get('date_formats', {}).get(locale, {})
    label = formats.get('date', '{day} {month} {year}').format(
        day=day.day, month=months.get(locale, months['en'])[day.month - 1], year=day.year)
    return formats.get('with_weekday', '{weekday} {date}').format(
        weekday=names.get(locale, names['en'])[day.weekday()], date=label) if include_weekday else label


def next_sailings(start: date, *, today: date | None = None, current_date: str | None = None,
                  catalog: dict | None = None) -> list[str]:
    """Offer two future published sailings, never the currently booked date."""
    first = max(start, (today or local_today()) + timedelta(days=1))
    weekdays = (catalog or mermaid_catalog.get_catalog())['service']['operating_weekdays']
    result = []
    for offset in range(15):
        day = first + timedelta(days=offset)
        if WEEKDAYS[day.weekday()] in weekdays and day.isoformat() != current_date:
            result.append(day.isoformat())
            if len(result) == 2:
                break
    return result


def date_recovery(target: str | None, *, today: date | None = None,
                  current_date: str | None = None, allow_today: bool = False) -> dict:
    """Assess structured date input and calculate a useful next step from policy."""
    today = today or local_today()
    try:
        day = date.fromisoformat(target)
    except (TypeError, ValueError):
        return {'reason': 'unclear', 'requested_date': None, 'alternatives': []}
    if day < today:
        reason = 'past'
    elif day == today and not allow_today:
        reason = 'today'
    elif WEEKDAYS[day.weekday()] not in mermaid_catalog.get_catalog()['service']['operating_weekdays']:
        reason = 'closed'
    else:
        reason = None
    return {'reason': reason, 'requested_date': day.isoformat(),
            'alternatives': next_sailings(day, today=today, current_date=current_date) if reason else []}


def date_options_reply(alternatives: list[str], locale: str) -> str:
    if len(alternatives) < 2:
        return copy('date_no_options', locale)
    return copy('date_options', locale).format(
        first=date_label(alternatives[0], locale), second=date_label(alternatives[1], locale))


def date_recovery_reply(recovery: dict, locale: str, current_date: str | None = None) -> str:
    reason = recovery['reason']
    target = recovery.get('requested_date')
    day = date.fromisoformat(target) if target else None
    names = policy()['weekdays']
    explanation = copy('date_' + reason, locale).format(
        date=date_label(day, locale, include_weekday=reason != 'closed') if day else '',
        weekday=names.get(locale, names['en'])[day.weekday()] if day else '')
    unchanged = copy('date_unchanged', locale).format(date=date_label(current_date, locale)) if current_date else ''
    # Unclear input needs one clarification, not unrelated suggested dates.
    parts = [unchanged, explanation] if reason == 'unclear' else [explanation, unchanged, date_options_reply(recovery['alternatives'], locale)]
    return '\n\n'.join(part for part in parts if part)


def calendar_reply(request: str, locale: str, *, today: date | None = None) -> str:
    catalog = mermaid_catalog.get_catalog()
    if request == 'operating_days':
        names = policy()['weekdays']
        localized = names.get(locale, names['en'])
        days = ', '.join(localized[i] for i, day in enumerate(WEEKDAYS)
                         if day in catalog['service']['operating_weekdays'])
        return copy('operating_days', locale).format(days=days)
    dates = calendar_dates(request, today=today, catalog=catalog)
    if not dates:
        return copy('no_dates', locale) + '\n\n' + date_options_reply(next_sailings(today or local_today(), today=today, catalog=catalog), locale)
    return copy('calendar', locale).format(dates='\n'.join('• ' + date_label(day, locale) for day in dates))


def state_context(conversation: str, reservation: dict | None) -> dict:
    """A payment record proves payment; a hard takeover proves active handling."""
    mode = state_registry.get_active_escalation_mode(conversation)
    active = bool(state_registry.get_human_takeover_at(conversation))
    review = 'active' if active else 'queued' if mode in {'soft', 'hard'} or (reservation or {}).get('human_takeover') else 'none'
    result = {'review': review, 'payment': 'none', 'delivery': 'none', 'delivery_channel': 'whatsapp', 'email_delivery': 'not_established'}
    if not reservation:
        return result
    from agents.social import mermaid_reservation_store as store, mermaid_documents as documents
    db = store._conn()
    try:
        paid = db.execute("SELECT 1 FROM mermaid_demo_payments WHERE tenant_slug='mermaid' AND reservation_public_id=? AND status='simulated_success'", (reservation['public_id'],)).fetchone()
        result['payment'] = 'paid' if paid else 'unpaid'
    finally:
        db.close()
    db = documents._conn()
    try:
        row = db.execute("SELECT status FROM mermaid_delivery_jobs WHERE tenant_slug='mermaid' AND reservation_public_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1", (reservation['public_id'],)).fetchone()
        if row:
            result['delivery'] = row['status'] if row['status'] in {'delivered', 'failed'} else 'waiting'
    finally:
        db.close()
    return result


def status_reply(request: str, locale: str, context: dict) -> str:
    group = 'review' if request == 'handover' else request
    if group not in {'review', 'payment', 'delivery'}:
        return ''
    return copy(group + '_' + context.get(group, 'none'), locale)


def wildlife_guarantee_reply(locale: str, context: dict) -> str:
    response = copy('wildlife_guarantee', locale)
    if context.get('review') in {'queued', 'active'}:
        response += '\n\n' + status_reply('handover', locale, context)
    return response


def pickup_coverage_reply(locale: str) -> str:
    journey = mermaid_catalog.get_catalog()['pricing'].get('pickup_journey', 'unconfirmed')
    return copy('pickup_round_trip' if journey == 'round_trip' else 'pickup_unknown', locale)


def pickup_pricing_reply(locale: str, fields: dict, reservation: dict | None = None) -> str:
    """Informational transport facts never select pickup or reprice a booking."""
    from agents.social import mermaid_guest_experience as guest

    catalog = mermaid_catalog.get_catalog()
    intake = reservation['intake'] if reservation else fields
    counts = [intake.get(key) for key in ('adults', 'children', 'infants')]
    complete_party = all(type(count) is int and count >= 0 for count in counts)
    passengers = sum(counts) if complete_party else 0
    parts = []
    if complete_party and passengers > 0:
        parts.append(guest.party_text(intake, locale) + '. ' +
                     copy('pickup_party_count', locale).format(count=passengers))

    def offer(plan):
        if not plan.get('vehicle_key'):
            return copy('pickup_current_amount', locale).format(
                currency=plan['currency'], amount=f"{plan['amount']:,.2f}")
        vehicle = guest.pickup_label({'pickup_plan': plan}, locale)
        text = copy('pickup_option', locale).format(
            vehicle=vehicle, quantity=plan['quantity'], currency=plan['currency'],
            amount=f"{plan['unit_amount']:,.2f}")
        if plan['quantity'] > 1:
            text += ' ' + copy('pickup_option_total', locale).format(
                currency=plan['currency'], amount=f"{plan['amount']:,.2f}")
        return text

    money = reservation.get('monetary_snapshot') if reservation else None
    if money and money.get('pickup_amount') is not None:
        plan = money.get('pickup_plan') or {}
        if plan.get('vehicle_key'):
            parts.append(copy('pickup_recorded_vehicle', locale).format(
                quantity=plan['quantity'], vehicle=guest.pickup_label(money, locale),
                currency=money['currency'], amount=f"{money['pickup_amount']:,.2f}"))
        else:
            # Historical flat-fee quotes establish an amount, not a vehicle.
            parts.append(copy('pickup_recorded_amount', locale).format(
                currency=money['currency'], amount=f"{money['pickup_amount']:,.2f}"))
    else:
        if reservation:
            parts.append(copy('pickup_not_included', locale))
        if not complete_party or passengers <= 0:
            for vehicle in catalog['pricing'].get('pickup_vehicles') or []:
                parts.append(offer(mermaid_catalog.pickup_quote(vehicle['capacity'], catalog)))
            if not catalog['pricing'].get('pickup_vehicles'):
                plan = mermaid_catalog.pickup_quote(1, catalog)
                parts.append(offer(plan) if plan['status'] == 'quoted' else copy('pickup_unpriced', locale))
            parts.append(copy('pickup_need_party', locale))
        else:
            plan = mermaid_catalog.pickup_quote(passengers, catalog)
            if plan['status'] == 'quoted':
                parts.append(offer(plan))
            else:
                parts.append(copy('pickup_offer_review' if plan['status'] == 'requires_review'
                                  else 'pickup_unpriced', locale))
    parts.append(copy('pickup_schedule', locale).format(time=mermaid_catalog.pickup_time(catalog)))
    journey = catalog['pricing'].get('pickup_journey', 'unconfirmed')
    parts.append(copy('pickup_round_trip' if journey == 'round_trip' else 'pickup_unknown', locale))
    return '\n\n'.join(parts)


def record_security_event(conversation: str, message_id: str, event: str, *, now: float | None = None) -> bool:
    """Log classifications, never supplied secrets/text. Return durable review need.

    Two distinct blocked attempts within 24 hours, or one actionable incident,
    merit a staff task. Replayed event IDs do not increase the count.
    """
    if event not in {'blocked_override', 'actionable_incident'}:
        return False
    if not message_id:
        # No stable provider identity means this attempt cannot safely be counted twice.
        return event == 'actionable_incident'
    now = time.time() if now is None else now
    settings = policy()['security']
    db = state_registry._get_conn()
    try:
        db.execute('CREATE TABLE IF NOT EXISTS mermaid_security_events (conversation_id TEXT NOT NULL, event_id TEXT NOT NULL, classification TEXT NOT NULL, created_at REAL NOT NULL, PRIMARY KEY(conversation_id,event_id))')
        db.execute('CREATE INDEX IF NOT EXISTS mermaid_security_recent ON mermaid_security_events(conversation_id,created_at)')
        db.commit()
        db.execute('BEGIN IMMEDIATE')
        event_id = hashlib.sha256(message_id.encode()).hexdigest()
        db.execute('INSERT OR IGNORE INTO mermaid_security_events VALUES (?,?,?,?)', (conversation, event_id, event, now))
        rows = db.execute('SELECT classification FROM mermaid_security_events WHERE conversation_id=? AND created_at>=?', (conversation, now-settings['window_seconds'])).fetchall()
        needs_review = any(row[0] == 'actionable_incident' for row in rows) or len(rows) >= settings['distinct_attempt_threshold']
        db.commit()
        return needs_review
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
