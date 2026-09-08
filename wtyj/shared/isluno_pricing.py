"""Deterministic demo pricing from validated catalog rules; no conversion guesses."""
from collections import Counter
import copy
from datetime import date, datetime, timedelta, timezone
import re
from zoneinfo import ZoneInfo

from shared.isluno_catalog import quote_rules


class ItineraryError(ValueError):
    def __init__(self, code, **details):
        self.code, self.details = code, details
        super().__init__(code)


def check(condition, code, **details):
    if not condition:
        raise ItineraryError(code, **details)


def identifier(value):
    check(isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,119}', value), 'invalid_identifier')
    return value


def price_item(catalog_snapshot, selection, *, now=None):
    """Selections contain explicit ages and option quantities, never inferred ages."""
    check(isinstance(selection, dict) and set(selection) == {
        'item_id', 'product_id', 'date', 'slot_id', 'guest_ages', 'options', 'pickup'
    }, 'invalid_item_fields')
    for key in ('item_id', 'product_id', 'slot_id'):
        identifier(selection[key])
    product = next((p for p in catalog_snapshot['catalog']['products'] if p['id'] == selection['product_id']), None)
    check(product is not None and product['enabled'], 'product_unavailable')
    selected = quote_rules(product, mode='demo')
    rules = selected['rules']
    guest, prices, schedule = (rules[key] for key in ('guest_rules', 'price_rules', 'schedule'))
    ages = selection['guest_ages']
    check(isinstance(ages, list) and 1 <= len(ages) <= 1000, 'invalid_guest_count')
    check(all(type(age) is int and guest['minimum_age'] <= age <= guest['maximum_age'] for age in ages), 'invalid_guest_age')
    check(len(ages) <= guest.get('max_guests', 1000), 'guest_capacity_exceeded')
    check(not guest.get('children_require_adult') or not any(a < guest['adult_min_age'] for a in ages)
          or any(a >= guest['adult_min_age'] for a in ages), 'adult_required')
    if prices['basis'] == 'per_booking':
        check(len(ages) <= prices['max_guests'], 'guest_capacity_exceeded')
    check(type(selection['pickup']) is bool, 'invalid_pickup')
    check(isinstance(selection['date'], str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', selection['date']), 'invalid_date')
    try:
        day = date.fromisoformat(selection['date'])
    except ValueError as exc:
        raise ItineraryError('invalid_date') from exc
    zone = ZoneInfo(schedule['timezone'])
    now = now or datetime.now(timezone.utc)
    check(now.tzinfo is not None, 'clock_requires_timezone')
    check(day >= now.astimezone(zone).date(), 'date_in_past')
    check(day.weekday() in schedule['weekdays'], 'departure_day_unavailable')
    slot = next((s for s in schedule['slots'] if s['id'] == selection['slot_id']), None)
    check(slot is not None, 'departure_slot_unavailable')
    start = datetime.fromisoformat(day.isoformat() + 'T' + slot['start']).replace(tzinfo=zone)
    check(start.astimezone(timezone.utc).astimezone(zone) == start and
          start.utcoffset() == start.replace(fold=1).utcoffset(), 'ambiguous_or_missing_departure_time')
    check(start > now, 'departure_in_past')
    end = (start.astimezone(timezone.utc) + timedelta(minutes=slot['duration_minutes'])).astimezone(zone)
    checkin = start - timedelta(minutes=schedule.get('check_in_minutes_before', 0))
    if 'published_check_in_times' in schedule:
        checkin = datetime.fromisoformat(day.isoformat() + 'T' + schedule['published_check_in_times'][schedule['slots'].index(slot)]).replace(tzinfo=zone)
    check(checkin > now, 'check_in_in_past')
    lines = []
    def line(kind, key, quantity, unit):
        lines.append({'kind': kind, 'key': key, 'quantity': quantity, 'unit_minor': unit, 'amount_minor': quantity * unit})
    if prices['basis'] == 'per_booking':
        line('base', 'booking', 1, prices['amount_minor'])
    else:
        counts = Counter()
        for age in ages:
            band = next((b for b in prices['age_bands'] if b['minimum_age'] <= age <= b['maximum_age']), None)
            check(band is not None, 'age_price_unavailable')
            counts[band['id']] += 1
        for band in prices['age_bands']:
            if counts[band['id']]:
                line('base', band['id'], counts[band['id']], band['amount_minor'])
    quantities = selection['options']
    check(isinstance(quantities, dict), 'invalid_options')
    known = {o['id']: o for o in rules['options']}
    check(not set(quantities) - set(known), 'unknown_option')
    for key, option in known.items():
        quantity = quantities.get(key, 0)
        check(type(quantity) is int and 0 <= quantity <= option['max_quantity'], 'invalid_option_quantity', option_id=key)
        if option['basis'] == 'per_booking':
            check(quantity <= 1, 'invalid_booking_option_quantity', option_id=key)
        if option['basis'] == 'per_person':
            check(quantity <= len(ages), 'option_guest_count_exceeded', option_id=key)
        if option['required']:
            check(quantity >= (len(ages) if option['basis'] == 'per_person' else 1), 'required_option_missing', option_id=key)
        if quantity:
            line('option', key, quantity, option['amount_minor'])
    pickup = rules['pickup']
    check(not selection['pickup'] or pickup['mode'] != 'meeting_point', 'pickup_unavailable')
    if pickup['mode'] == 'priced_option':
        quantity = quantities.get(pickup['option_id'], 0)
        check(bool(quantity) == selection['pickup'], 'pickup_option_mismatch')
        if selection['pickup'] and known[pickup['option_id']]['basis'] == 'per_person':
            check(quantity == len(ages), 'pickup_must_cover_party')
    check(prices['taxes_fees'] == 'included', 'taxes_fees_unverified')
    return {'id': selection['item_id'], 'selection': copy.deepcopy(selection), 'status': 'demo_selected',
            'product': {'id': product['id'], 'name': product['name'], 'source': copy.deepcopy(product['source']),
                        'source_claims': copy.deepcopy(product.get('source_claims', {}))},
            'catalog_revision': catalog_snapshot['revision'], 'catalog_version': catalog_snapshot['catalog']['version'],
            'pricing_snapshot': selected, 'starts_at': start.isoformat(), 'ends_at': end.isoformat(),
            'check_in_at': checkin.isoformat(), 'timezone': schedule['timezone'],
            'currency': prices['currency'], 'currency_exponent': prices['currency_exponent'],
            'lines': lines, 'total_minor': sum(row['amount_minor'] for row in lines),
            'availability_source': 'demo_assumed', 'real_booking_eligible': False}


def totals(items):
    check(len(items) <= 50, 'too_many_items')
    currencies = {(i['currency'], i['currency_exponent']) for i in items}
    check(len(currencies) <= 1, 'mixed_currency_requires_confirmed_conversion')
    for index, left in enumerate(items):
        for right in items[index + 1:]:
            if (datetime.fromisoformat(left['check_in_at']) < datetime.fromisoformat(right['ends_at']) and
                    datetime.fromisoformat(right['check_in_at']) < datetime.fromisoformat(left['ends_at'])):
                raise ItineraryError('schedule_overlap', item_ids=[left['id'], right['id']])
    currency, exponent = next(iter(currencies), (None, None))
    return {'currency': currency, 'currency_exponent': exponent,
            'total_minor': sum(item['total_minor'] for item in items), 'taxes_fees': 'included',
            'mode': 'demo', 'real_money_moved': False}
