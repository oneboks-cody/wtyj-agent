"""One model response for discovery, translation and explicit itinerary changes."""
import copy
import json
from agents.social import isluno_understanding as discovery
from shared.isluno_config import LANGUAGES
from shared.isluno_catalog import quote_rules
from shared.isluno_pricing import check
from agents.social import isluno_hospitality as hospitality

TOOL = copy.deepcopy(discovery.TOOL)
TOOL['input_schema']['properties'].update({
    'booking': {'type': 'object', 'additionalProperties': False,
        'properties': {'action': {'type': 'string', 'enum': ['none', 'new', 'add', 'update', 'remove', 'cancel', 'human', 'approve', 'summary', 'documents', 'email', 'stop_reminders']},
                       'updates': {'type': 'array', 'maxItems': 10, 'items': {'type': 'object', 'properties': {
                           'item_id': {'type': 'string'}, 'product_id': {'type': 'string'}, 'date': {'type': 'string'},
                           'slot_id': {'type': 'string'}, 'guest_ages': {'type': 'array', 'items': {'type': 'integer'}},
                           'options': {'type': 'object', 'additionalProperties': {'type': 'integer'}}, 'pickup': {'type': 'boolean'},
                           'pickup_location': {'type': 'string'}}, 'additionalProperties': False}},
                       'guest': {'type': 'object', 'properties': {'name': {'type': 'string'}, 'ages': {'type': 'array', 'items': {'type': 'integer'}}}, 'additionalProperties': False},
                       'email_address': {'type': 'string', 'maxLength': 254},
                       'email_address_correction': {'type': 'boolean'},
                       'document_language': {'type': ['string', 'null'], 'enum': [None, *sorted(LANGUAGES)]}},
        'required': ['action', 'updates', 'guest', 'document_language']},
    'translations': {'type': 'object', 'additionalProperties': {'type': 'object', 'additionalProperties': {'type': 'string'}}},
})
TOOL['input_schema']['properties']['hospitality'] = hospitality.SCHEMA
TOOL['input_schema']['required'] += ['booking', 'translations', 'hospitality']


def system_prompt():
    from shared.isluno_config import active_profile
    voice = active_profile().get('hospitality_voice', '')
    return discovery.system_prompt() + '\nBrand voice: ' + voice + hospitality.PROMPT + (
        ' A verified native_detail_request means the guest tapped Trip details for that exact product. Respond in saved chat language, product_ids containing only that product, booking.action none, no guest/itinerary/document mutations, photo none. Include the requested fact_keys and their faithful translations plus summary in the same response. Keep common prose brief; the server presents these source details in bounded pages. Do not treat this navigation as booking consent. '
        ' Also extract explicit itinerary changes in the SAME response; never make a second understanding call. '
        'Saved session and item IDs are authoritative. Preserve guest data unless explicitly corrected; ask only missing information. '
        'booking.action add means an explicit new trip; update targets existing or pending item IDs; remove targets one item; '
        'cancel means an explicit unpaid whole-itinerary cancellation; new starts a separate itinerary only when explicitly requested. '
        'Use booking.action summary when the customer asks to review the complete itinerary or get a quote. '
        'Use action documents to retrieve or resume paid demo documents. Use action email and email_address for an explicitly supplied receipt email address; an address alone is not consent. Set email_address_correction=true when the customer rejects, withdraws or replaces a previous address, even when no valid replacement is supplied. Ordinary requests to receive an email without an address do not imply correction. Never infer payment from text. '
        'Native reply buttons alone confirm summary details and approve a quote. '
        'Never infer approval from a question, acknowledgement or correction. approve is only explicit approval; the server owns quote stages. '
        'Use the supplied booking_rules for valid slot/option IDs and constraints. Do not invent identifiers. Capture all supplied guest names, exact ages, dates, slot IDs, options and pickup choices. Never invent an adult age from an adult count. '
        'Resolve relative dates from supplied current date and timezone. A bare answer refers to the last missing field. '
        'Reuse saved guest ages for a new trip, not to overwrite another item with different guests. '
        'document_language changes only on an explicit document-language preference; chat language changes do not change it. '
        'For non-English replies, translate summary and every selected source fact for each recommended product into the chat language in translations. '
        'Translations are faithful to those facts only: preserve numbers, restrictions, uncertainty and demo labels; never add new claims. '
        'Product names stay recognizable. All money, changes, cancellation and operator status are rendered by the server. '
        'Only explicit changes to a paid or noneditable itinerary request human recovery; a greeting, name or read-only question never requests handoff. Never claim a refund or completed change. '
        'Use booking.action stop_reminders when the guest asks to stop reminders or follow-up messages. Do not infer opt-in. Use booking.action human for an explicit operator request. Supplier/safety questions with unconfirmed answers use the unavailable-answer path.')


def validate(result, catalog, *, require_hospitality=False):
    check(isinstance(result, dict) and set(discovery.TOOL['input_schema']['required']) | {'booking', 'translations'} <= set(result) <= set(discovery.TOOL['input_schema']['required']) | {'booking', 'translations', 'hospitality'}, 'invalid_conversation_result')
    base = {k: result[k] for k in discovery.TOOL['input_schema']['required']}
    discovery.validate(base, catalog)
    booking = result['booking']
    check(isinstance(booking, dict) and {'action', 'updates', 'guest', 'document_language'} <= set(booking) <= {'action', 'updates', 'guest', 'document_language', 'email_address', 'email_address_correction'}, 'invalid_booking_contract')
    check(isinstance(booking['action'], str) and booking['action'] in {'none', 'new', 'add', 'update', 'remove', 'cancel', 'human', 'approve', 'summary', 'documents', 'email', 'stop_reminders'}, 'invalid_booking_action')
    check('email_address' not in booking or isinstance(booking['email_address'], str) and len(booking['email_address']) <= 254, 'invalid_email_address')
    check('email_address_correction' not in booking or type(booking['email_address_correction']) is bool, 'invalid_email_correction')
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
    check(not require_hospitality or 'hospitality' in result, 'missing_hospitality_contract')
    if 'hospitality' in result:
        hospitality.validate(result['hospitality'], result, catalog)
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



def delivery_context(saved):
    """Only this scope's correlated delivery evidence permits prior-sharing claims."""
    history = saved.get('history', [])
    confirmed = [entry['delivery_id'] for entry in history
                 if entry.get('role') == 'assistant' and entry.get('delivery_id')
                 and entry.get('delivery_status') == 'accepted'
                 and entry.get('provider_delivery_status') in {'delivered', 'read'}]
    return {'confirmed_message_ids': confirmed,
            'prior_guest_turns': sum(entry.get('role') == 'user' for entry in history)}


def prompt_history(saved):
    """Keep delivery failures visible without presenting their unsent prose as dialogue."""
    result = []
    for entry in saved.get('history', []):
        value = copy.deepcopy(entry)
        if value.get('role') == 'assistant' and value.get('delivery_status') != 'accepted':
            value = {key: value[key] for key in ('role', 'delivery_id', 'delivery_status', 'provider_delivery_status') if key in value}
            value.setdefault('delivery_status', 'legacy_unverified')
        result.append(value)
    return result


def prompt_state(saved):
    """Project current conversational state; history has one separate owner."""
    keys=('revision','guest','pending','active_itinerary_id','chat_language','document_language',
          'item_details','browsing','stage','native_detail_request','last_accepted_question','current_time','timezone','discovery','quote_context')
    result={k:copy.deepcopy(saved[k]) for k in keys if k in saved}
    result['delivery_context'] = delivery_context(saved)
    itinerary=saved.get('itinerary')
    if itinerary:
        result['itinerary']={k:copy.deepcopy(itinerary[k]) for k in ('id','revision','status','totals') if k in itinerary}
        result['itinerary']['items']=[{k:copy.deepcopy(item[k]) for k in ('id','item_id','product','selection','starts_at','ends_at','total_minor','currency','currency_exponent','lines') if k in item} for item in itinerary.get('items',[])]
    return result


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
        thread_fields={'catalog': catalog, **prompt_state(saved)}, thread_flags={}, channel='whatsapp',
        messages=prompt_history(saved), response_contract='isluno_conversation')
    check(not result.get('generation_failed'), (result.get('model_error') or {}).get('code') or 'conversation_generation_failed')
    validated = validate(result, catalog)
    if 'hospitality' in validated:
        hospitality.authorize(validated['hospitality'], text)
    return validated
