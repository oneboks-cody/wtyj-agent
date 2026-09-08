"""One model response for discovery, translation and explicit itinerary changes."""
import copy
import json
from agents.social import isluno_understanding as discovery
from shared.isluno_config import LANGUAGES
from shared.isluno_catalog import quote_rules
from shared.isluno_pricing import check

TOOL = copy.deepcopy(discovery.TOOL)
TOOL['input_schema']['properties'].update({
    'booking': {'type': 'object', 'additionalProperties': False,
        'properties': {'action': {'type': 'string', 'enum': ['none', 'new', 'add', 'update', 'remove', 'cancel', 'human', 'approve', 'summary', 'documents', 'email']},
                       'updates': {'type': 'array', 'maxItems': 10, 'items': {'type': 'object', 'properties': {
                           'item_id': {'type': 'string'}, 'product_id': {'type': 'string'}, 'date': {'type': 'string'},
                           'slot_id': {'type': 'string'}, 'guest_ages': {'type': 'array', 'items': {'type': 'integer'}},
                           'options': {'type': 'object', 'additionalProperties': {'type': 'integer'}}, 'pickup': {'type': 'boolean'},
                           'pickup_location': {'type': 'string'}}, 'additionalProperties': False}},
                       'guest': {'type': 'object', 'properties': {'name': {'type': 'string'}, 'ages': {'type': 'array', 'items': {'type': 'integer'}}}, 'additionalProperties': False},
                       'email_address': {'type': 'string', 'maxLength': 254},
                       'document_language': {'type': ['string', 'null'], 'enum': [None, *sorted(LANGUAGES)]}},
        'required': ['action', 'updates', 'guest', 'document_language']},
    'translations': {'type': 'object', 'additionalProperties': {'type': 'object', 'additionalProperties': {'type': 'string'}}},
})
TOOL['input_schema']['required'] += ['booking', 'translations']


def system_prompt():
    return discovery.system_prompt() + (
        ' Also extract explicit itinerary changes in the SAME response; never make a second understanding call. '
        'Saved session and item IDs are authoritative. Preserve guest data unless explicitly corrected; ask only missing information. '
        'booking.action add means an explicit new trip; update targets existing or pending item IDs; remove targets one item; '
        'cancel means an explicit unpaid whole-itinerary cancellation; new starts a separate itinerary only when explicitly requested. '
        'Use booking.action summary when the customer asks to review the complete itinerary or get a quote. '
        'Use action documents to retrieve or resume paid demo documents. Use action email and email_address for an explicitly supplied receipt email address; an address alone is not consent. Never infer payment from text. '
        'Native reply buttons alone confirm summary details and approve a quote. '
        'Never infer approval from a question, acknowledgement or correction. approve is only explicit approval; the server owns quote stages. '
        'Use the supplied booking_rules for valid slot/option IDs and constraints. Do not invent identifiers. Capture all supplied guest names, exact ages, dates, slot IDs, options and pickup choices. Never invent an adult age from an adult count. '
        'Resolve relative dates from supplied current date and timezone. A bare answer refers to the last missing field. '
        'Reuse saved guest ages for a new trip, not to overwrite another item with different guests. '
        'document_language changes only on an explicit document-language preference; chat language changes do not change it. '
        'For non-English replies, translate summary and every selected source fact for each recommended product into the chat language in translations. '
        'Translations are faithful to those facts only: preserve numbers, restrictions, uncertainty and demo labels; never add new claims. '
        'Product names stay recognizable. All money, changes, cancellation and operator status are rendered by the server. '
        'If saved itinerary is paid or no longer editable, request human recovery; never claim a refund or completed change. '
        'Use booking.action human for an explicit operator request. Supplier/safety questions with unconfirmed answers use the unavailable-answer path.')


def validate(result, catalog):
    check(isinstance(result, dict) and set(result) == set(discovery.TOOL['input_schema']['required']) | {'booking', 'translations'}, 'invalid_conversation_result')
    base = {k: result[k] for k in discovery.TOOL['input_schema']['required']}
    discovery.validate(base, catalog)
    booking = result['booking']
    check(isinstance(booking, dict) and {'action', 'updates', 'guest', 'document_language'} <= set(booking) <= {'action', 'updates', 'guest', 'document_language', 'email_address'}, 'invalid_booking_contract')
    check(isinstance(booking['action'], str) and booking['action'] in {'none', 'new', 'add', 'update', 'remove', 'cancel', 'human', 'approve', 'summary', 'documents', 'email'}, 'invalid_booking_action')
    check('email_address' not in booking or isinstance(booking['email_address'], str) and len(booking['email_address']) <= 254, 'invalid_email_address')
    updates = booking['updates']
    check(not updates or booking['action'] in {'new','add','update','remove'}, 'updates_require_explicit_action')
    check(isinstance(updates, list) and len(updates) <= 10, 'invalid_booking_updates')
    for update in updates:
        check(isinstance(update, dict) and not set(update) - {'item_id', 'product_id', 'date', 'slot_id', 'guest_ages', 'options', 'pickup', 'pickup_location'}, 'invalid_booking_update')
        for key in ('item_id', 'product_id', 'date', 'slot_id', 'pickup_location'):
            check(key not in update or isinstance(update[key], str) and len(update[key]) <= 500, 'invalid_booking_text')
        if 'guest_ages' in update:
            check(isinstance(update['guest_ages'], list) and 1 <= len(update['guest_ages']) <= 1000 and all(type(a) is int and 0 <= a <= 120 for a in update['guest_ages']), 'invalid_guest_ages')
        check('pickup' not in update or type(update['pickup']) is bool, 'invalid_pickup')
        if 'options' in update:
            check(isinstance(update['options'], dict) and len(update['options']) <= 50 and all(isinstance(k, str) and type(v) is int and 0 <= v <= 1000 for k,v in update['options'].items()), 'invalid_options')
    guest = booking['guest']
    check(isinstance(guest, dict) and not set(guest) - {'name', 'ages'}, 'invalid_guest_contract')
    check('name' not in guest or isinstance(guest['name'], str) and 0 < len(guest['name'].strip()) <= 200, 'invalid_guest_name')
    if 'ages' in guest:
        check(isinstance(guest['ages'], list) and 1 <= len(guest['ages']) <= 1000 and all(type(a) is int and 0 <= a <= 120 for a in guest['ages']), 'invalid_guest_ages')
    language = booking['document_language']
    check(language is None or isinstance(language, str) and language in LANGUAGES, 'invalid_document_language')
    validate_translations(result['translations'], base, catalog)
    return result


def validate_translations(translations, decision, catalog):
    check(isinstance(translations, dict) and not set(translations) - set(decision['product_ids']), 'invalid_translations')
    products = {p['id']: p for p in catalog['products']}
    for product_id in decision['product_ids']:
        provided = translations.get(product_id, {})
        check(isinstance(provided, dict) and not set(provided) - set(products[product_id]['facts']), 'invalid_translation_fact')
        check(all(isinstance(v, str) and 0 < len(v.strip()) <= 12000 for v in provided.values()), 'invalid_translation_text')
        if decision['language'] != 'en':
            needed = {'summary', *(k for k in decision['fact_keys'] if k in products[product_id]['facts'])}
            check(needed <= set(provided), 'translation_missing')


def understand(scope, text, saved, snapshot):
    from shared.isluno_config import require_scope
    from agents.marina import marina_agent
    require_scope(scope)
    catalog = discovery.context(snapshot)
    products = {p['id']: p for p in snapshot['catalog']['products']}
    for entry in catalog['products']:
        product = products[entry['id']]
        entry['booking_rules'] = quote_rules(product, mode='demo') if product['readiness']['quotable'] else None
    result = marina_agent.process_message(from_email=scope.customer_ref, subject='Isluno itinerary', body=text,
        thread_fields={'catalog': catalog, **saved}, thread_flags={}, channel='whatsapp',
        messages=saved.get('history', []), response_contract='isluno_conversation')
    check(not result.get('generation_failed'), 'conversation_generation_failed')
    return validate(result, catalog)
