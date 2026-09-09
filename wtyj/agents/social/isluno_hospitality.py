"""Typed single-call hospitality; no intent or reply-language classification."""
import copy
import json
import re
from string import Formatter

from shared.isluno_pricing import check
from agents.social import isluno_understanding

STAGES = ['welcome', 'exploration', 'recommendation', 'booking_preparation',
          'review_approval', 'payment_confirmation', 'after_booking']
BRANCHES = ['browsing', 'saved', 'pending', 'error', 'review', 'choose_product',
            'choose_item', 'cancelled', 'no_active', 'approval_unavailable']
MEMORY_KEYS = ['holiday', 'party', 'interests', 'pace', 'budget', 'requirements',
               'occasion', 'outstanding_decisions']
SCHEMA = {'type': 'object', 'additionalProperties': False, 'properties': {
    'stage': {'type': 'string', 'enum': STAGES},
    'memory': {'type': 'object', 'additionalProperties': False,
               'properties': {k: {'type': ['string', 'null'], 'maxLength': 800} for k in MEMORY_KEYS}},
    'discussed': {'type': 'array', 'maxItems': 3, 'items': {'type': 'object', 'additionalProperties': False,
        'properties': {'product_id': {'type': 'string'}, 'reason': {'type': 'string', 'maxLength': 400}},
        'required': ['product_id', 'reason']}},
    'consent': {'type': 'object', 'additionalProperties': False,
        'properties': {'action': {'type': 'string'}, 'evidence': {'type': 'string', 'maxLength': 1500}},
        'required': ['action', 'evidence']},
    'photo': {'type': 'string', 'enum': ['none', 'initial', 'more', 'repeat', 'all']},
    'estimate': {'type': 'object', 'additionalProperties': False, 'properties': {
        'product_id': {'type':'string'}, 'date': {'type':'string'}, 'slot_id': {'type':'string'},
        'guest_ages': {'type':'array','items':{'type':'integer'}}, 'options': {'type':'object','additionalProperties':{'type':'integer'}},
        'pickup': {'type':'boolean'}}, 'required':['product_id','date','slot_id','guest_ages','options','pickup']},
    'replies': {'type': 'object', 'additionalProperties': False, 'properties': {
        k: {'type': 'object', 'additionalProperties': False, 'properties': {
            'paragraphs': {'type': 'array', 'minItems': 1, 'maxItems': 4,
                           'items': {'type': 'string', 'maxLength': 1800}},
            'question': {'type': 'string', 'maxLength': 500}},
            'required': ['paragraphs', 'question']} for k in BRANCHES}},
}, 'required': ['stage', 'memory', 'discussed', 'consent', 'photo', 'replies']}

PROMPT = '''
HOSPITALITY CONTRACT (supersedes earlier restrictions on prose):
Return hospitality in this SAME response. You are a warm, thoughtful holiday host,
relaxed in language and precise in arrangements. Welcome first-time guests, recognize
supplied holiday details, give brief relevant inspiration, ask one easy next question.
A name or month-long holiday is browsing memory, never permission to create a draft.
Use natural contractions and 2–3 short paragraphs, no em/en dash sentence breaks,
no repeated greetings/names, forced emojis, hype, invented human experiences or urgency.
Adapt to worried/hurried guests. Accept hesitation or decline without pressure.
Remember all volunteered details together in memory; null explicitly removes a corrected
preference. Memory is guest-provided context, not supplier facts. discussed records
product IDs and why they appeal, not a booking. Never invent ages from party counts.
Use stage flexibly: welcome/exploration/recommendation/booking_preparation/review_approval/
payment_confirmation/after_booking. A greeting never resets an existing booking.
For action none, guest fields only update browsing context. To answer an outstanding
booking-detail question, use update with the relevant item ID and explicit evidence.
For any action, consent.action must equal booking.action and consent.evidence must be
an exact quotation from the latest guest message establishing that action. For none
use empty evidence. Photos, details, "looks nice", and friendly interest are NOT consent.
Resolve "that"/"both" from actual accepted prior messages; clarify ambiguous references.
Never claim a price inquiry created a booking. Ask missing pricing details if necessary.

NATURAL PRESENTATION:
replies maps actual outcome branches to {paragraphs:[...], question:"one next question"}.
Always supply browsing as a neutral shared answer: answer the guest's questions and
acknowledge their request without claiming an operation has succeeded. For any booking
action also supply error with {operation}. Other outcome branches are OPTIONAL; do not
write every speculative branch. If omitted, the server combines the common answer with
the actual authoritative outcome and actual missing-field question. Include a branch only
when a materially different natural response is needed. Keep common prose concise.
For a first general introduction without concrete activity preferences, welcome the guest,
recognize their holiday, and ask one easy question about interests or company. Empty
product_ids/fact_keys are valid here; do not force three recommendations into a greeting.
For a fresh hello after a rejected reply, do not assume the guest saw your recommendations.
The server selects the actual branch after applying the action. Never claim success in
browsing or error. error is a failed attempted item update; other successful independent
parts must still be answered. The server supplies the authoritative operation result.
Rapport/recommendation prose can explain how supplied preferences connect to verified
source facts, but MUST NOT contain standalone product, price, availability, payment,
refund, supplier-confirmation or handoff claims. Insert those through bindings below.
For a price inquiry with all pricing/eligibility/date/option details supplied, set estimate
with product_id,date,slot_id,guest_ages,options,pickup. It calculates a demo estimate
without creating itinerary, quote or payment records. Ask a missing detail otherwise.
Use {estimate_total} in browsing, never write the numerical amount yourself. Include an
error reply with {operation} for incomplete/invalid estimates. This is not availability.
Valid references do NOT license other unsupported business claims in the surrounding prose.
No arbitrary source paraphrases in prose. For a direct question, reference its actual
fact, or say it is unconfirmed and offer help. Do not infer accessibility/safety guarantees.
Recommend one strong choice and at most two alternatives, explaining why each fits.
Use a concrete next question that fits the guest's interest, without pushing a booking.
Use single-brace bindings: {fact:PRODUCT_ID:FACT_KEY}, {name:PRODUCT_ID}, {total},
{items}, {missing_field}, {operation}, {estimate_total}. Facts are substituted from the versioned source
or faithful translations. Prices must ONLY use {total}; do not type amounts or invent
availability. {total} is the current draft subtotal, not a supplier-confirmed price;
use only in saved with complete selections. {items} lists saved draft trips/dates.
{missing_field} identifies the next outstanding booking detail. Reuse known details.
{operation} is authoritative operation/exception information. In error/review/cancelled/
no_active/choose_item/choose_product/approval_unavailable include {operation} so actual
outcomes cannot be masked. Saved means a DEMO draft, never a reservation or payment.
If demo sample rules affect the choice, explain them; the server also labels draft totals.
Keep answers in every applicable branch for mixed question+action, including error.
stop_reminders saved must include {operation}; it is the actual opt-out result.
Documents/email success prose must not claim delivery; durable receipt/document parts
carry the authoritative result separately. Documents/email must not swallow a simultaneous question. Do not promise notification
or a human response; review only records a request. Payment/approval remain native gates.
The replies.question is the final guest-facing next question, not an internal note.
The older question field is only an unanswered supplier-question signal: use a nonempty
value for unsupported questions; use fact_keys for supported questions.

PHOTOS AND MEMORY:
photo initial for a specific newly discussed product, more/all when requested, repeat
only for explicit repeat. The server uses its real gallery, bounded batches and durable
send history. Never put image URLs in prose. No booking action for a gallery request.
Delivery history states provider acceptance, rejection or ambiguity, never proven guest
receipt. Do not assume planned/failed/ambiguous messages or questions were received.
Use actual accepted last question/buttons and preserve existing decisions across turns.
'''


def references(text):
    """Parse formatting syntax only; this is not an intent or language classifier."""
    try:
        fields = []
        for _, name, spec, conversion in Formatter().parse(text):
            if name is not None:
                check(not conversion, 'invalid_reply_reference')
                fields.append(name + (':' + spec if spec else ''))
        return fields
    except ValueError as exc:
        raise ValueError('invalid_reply_format') from exc


def validate(value, decision, catalog):
    check(isinstance(value, dict) and set(SCHEMA['required']) <= set(value) <= set(SCHEMA['required']) | {'estimate'}, 'invalid_hospitality_contract')
    check(value['stage'] in STAGES, 'invalid_conversation_stage')
    check(isinstance(value['memory'], dict) and not set(value['memory']) - set(MEMORY_KEYS), 'invalid_browsing_memory')
    check(all(v is None or isinstance(v, str) and len(v) <= 800 for v in value['memory'].values()), 'invalid_browsing_value')
    products = {p['id']: p for p in catalog['products']}
    check(isinstance(value['discussed'], list) and len(value['discussed']) <= 3, 'invalid_discussed_products')
    for entry in value['discussed']:
        check(isinstance(entry, dict) and set(entry) == {'product_id', 'reason'} and entry['product_id'] in products
              and isinstance(entry['reason'], str) and len(entry['reason']) <= 400, 'invalid_recommendation_memory')
    consent = value['consent']
    check(isinstance(consent, dict) and set(consent) == {'action', 'evidence'}
          and consent['action'] == decision['booking']['action'] and isinstance(consent['evidence'], str)
          and len(consent['evidence']) <= 1500, 'invalid_action_authority')
    check(value['photo'] in {'none', 'initial', 'more', 'repeat', 'all'}, 'invalid_photo_request')
    if 'estimate' in value:
        estimate = value['estimate']
        check(isinstance(estimate, dict) and set(estimate) == set(SCHEMA['properties']['estimate']['required'])
              and estimate['product_id'] in decision['product_ids'], 'invalid_price_estimate')
    replies = value['replies']
    check(isinstance(replies, dict) and 'browsing' in replies and not set(replies) - set(BRANCHES), 'invalid_hospitality_replies')
    for branch, reply in replies.items():
        check(isinstance(reply, dict) and set(reply) == {'paragraphs', 'question'}, 'invalid_hospitality_reply')
        check(isinstance(reply['paragraphs'], list) and 1 <= len(reply['paragraphs']) <= 4, 'invalid_reply_paragraphs')
        texts = [*reply['paragraphs'], reply['question']]
        check(all(isinstance(t, str) for t in texts), 'invalid_reply_text_type')
        check(all(len(t) <= 1800 for t in texts), 'invalid_reply_text_length')
        check(len(reply['question']) <= 500, 'invalid_next_question')
        refs = [r for t in texts for r in references(t)]
        for ref in refs:
            parts = ref.split(':')
            if parts[0] == 'fact':
                check(len(parts) == 3 and parts[1] in decision['product_ids']
                      and parts[2] in products[parts[1]]['facts'] and parts[2] in decision['fact_keys'], 'unsupported_reply_fact')
            elif parts[0] == 'name':
                check(len(parts) == 2 and parts[1] in decision['product_ids'], 'unsupported_reply_product')
            else:
                check(ref in {'total', 'items', 'missing_field', 'operation', 'estimate_total'}, 'unsupported_reply_result')
                check(branch != 'browsing' or ref == 'estimate_total' and 'estimate' in value, 'transaction_claim_in_browsing')
        if branch in {'error', 'review', 'cancelled', 'no_active', 'choose_item', 'choose_product', 'approval_unavailable'}:
            check('operation' in refs, 'missing_authoritative_outcome')
    if 'estimate' in value:
        check('error' in replies, 'missing_estimate_error_reply')
    action = decision['booking']['action']
    if action == 'stop_reminders' and 'saved' in replies:
        check('operation' in [r for t in replies.get('saved', {}).get('paragraphs', []) for r in references(t)], 'missing_reminder_result')
    check(decision['intent'] != 'add' or action in {'add','new'}, 'contradictory_selection_authority')
    check((decision['intent'] == 'human') == (action == 'human'), 'contradictory_handoff_authority')
    if action != 'none':
        check('error' in replies and bool(consent['evidence'].strip()), 'missing_action_authority')
    return value


def authorize(value, text):
    consent = value['consent']
    check(consent['action'] == 'none' or consent['evidence'] in text, 'unbound_action_authority')


def remember(session, value):
    browsing = session.setdefault('browsing', {})
    for key, val in value['memory'].items():
        if val is None:
            browsing.pop(key, None)
        else:
            browsing[key] = val
    discussed = browsing.setdefault('discussed', {})
    discussed.update({d['product_id']: d['reason'] for d in value['discussed']})
    session['stage'] = value['stage']


def presentation_text(text):
    """Mechanical typography after binding expansion, never an intent classifier.

    Commas replace spaced sentence breaks; hyphens preserve ranges and names.
    No prose truncation, semantic classification or model retry.
    """
    text = re.sub(r'(?<=\d)[ \t]*[\u2014\u2013][ \t]*(?=\d)', '-', text)
    text = re.sub(r'[ \t]+[\u2014\u2013][ \t]+', ', ', text)
    return text.translate(str.maketrans({'\u2014': '-', '\u2013': '-'}))


def reply_diagnostics(result):
    """Bounded structural metadata only; no model prose or source/guest values."""
    value = result.get('hospitality', {}) if isinstance(result, dict) else {}
    replies = value.get('replies', {}) if isinstance(value, dict) else {}
    texts = []
    branches = []
    if isinstance(replies, dict):
        for branch in BRANCHES:
            reply = replies.get(branch)
            if not isinstance(reply, dict):
                continue
            branches.append(branch)
            paragraphs = reply.get('paragraphs')
            if isinstance(paragraphs, list):
                texts.extend(paragraphs[:4])
            texts.append(reply.get('question'))
    strings = [t for t in texts if isinstance(t, str)]
    return {'reply_branches': branches, 'reply_nonstring_count': sum(not isinstance(t, str) for t in texts),
            'reply_max_text_length': max((len(t) for t in strings), default=0),
            'reply_unicode_dash_count': sum(t.count('\u2014') + t.count('\u2013') for t in strings)}


def present(value, decision, outcome, snapshot, *, now=None):
    from agents.social.isluno_conversation import render, complete_selection, COPY
    session, itinerary = outcome['session'], outcome['itinerary']
    action = decision['booking']['action']
    branch = ('error' if outcome['error'] else outcome['status'])
    if branch == 'saved':
        branch = 'browsing' if action == 'none' else 'pending' if session['pending'] else 'saved'
    estimate = None
    if 'estimate' in value:
        from shared.isluno_pricing import price_item, ItineraryError
        from shared.isluno_catalog import CatalogError
        try:
            estimate = price_item(snapshot, {'item_id':'browsing-estimate', **value['estimate']}, now=now)
        except (ItineraryError, CatalogError) as exc:
            branch = 'error'
            outcome = {**outcome, 'error':exc.code if isinstance(exc, ItineraryError) else 'product_rules_unavailable'}
    fallback=branch not in value['replies']
    selected=copy.deepcopy(value['replies'].get(branch,value['replies']['browsing']))
    if fallback:selected['paragraphs'].append('{operation}')
    products = {p['id']: p for p in snapshot['catalog']['products']}
    missing = None
    for pending in session['pending'].values():
        try:
            missing = complete_selection(copy.deepcopy(pending), session['guest'], snapshot)
        except Exception:
            continue
        if missing:
            break
    bindings = {'operation': render(outcome, snapshot),
                'missing_field': COPY[decision['language']][missing] if missing else ''}
    if fallback and branch=='pending' and missing:
        from agents.social.isluno_conversation import missing_prompt
        question=missing_prompt(session,snapshot)
        bindings['operation']='\n\n'.join(line for line in bindings['operation'].split('\n\n') if line!=question+'.')
        selected['question']=question+'?'
    elif fallback and branch in {'choose_item','choose_product'}:
        selected['paragraphs']=[t for t in selected['paragraphs'] if t!='{operation}']
        selected['question']='{operation}'
    elif fallback and branch not in {'saved','browsing'}:
        selected['question']=''
    if branch == 'error':
        bindings['operation'] = bindings['operation'].replace('\n\n', '\n')
    if action == 'stop_reminders':
        from agents.social.isluno_recovery_copy import STOP
        bindings['operation'] = STOP[decision['language']]
    if estimate:
        amount = estimate['total_minor']
        bindings['estimate_total'] = estimate['currency'] + ' ' + str(amount // 100) + '.' + str(amount % 100).zfill(2)
    if itinerary:
        totals = itinerary['totals']
        if totals['currency'] and not session['pending']:
            bindings['total'] = totals['currency'] + ' ' + str(totals['total_minor']//100) + '.' + str(totals['total_minor']%100).zfill(2)
        bindings['items'] = '\n'.join(i['product']['name'] + ' · ' + i['selection']['date'] + ' · ' + i['starts_at'][11:16] for i in itinerary['items'])
    def expand(text):
        def resolve(ref):
            bits = ref.split(':')
            if bits[0] == 'fact':
                facts = (isluno_understanding.facts(products[bits[1]]) if decision['language'] == 'en'
                         else decision['translations'][bits[1]])
                return facts[bits[2]]
            if bits[0] == 'name':
                return products[bits[1]]['name']
            check(ref in bindings and bool(bindings[ref]), 'unavailable_reply_binding')
            return bindings[ref]
        for ref in references(text):
            text = text.replace('{' + ref + '}', resolve(ref))
        return presentation_text(text)
    paragraphs = [expand(t) for t in selected['paragraphs']]
    question = expand(selected['question'])
    if question:
        paragraphs.append(question)
    # A source-backed administrative label belongs only to actual draft changes.
    if estimate or branch == 'saved' and itinerary and itinerary['items']:
        from agents.social.isluno_discovery import LABELS
        paragraphs.append(LABELS[decision['language']][6])
        if estimate and estimate['pricing_snapshot']['pricing_mode'] == 'demo_sample' or itinerary and any(i['pricing_snapshot']['pricing_mode'] == 'demo_sample' for i in itinerary['items']):
            paragraphs.append({'en': 'Sample demo rules', 'nl': 'Voorbeeldregels voor de demo', 'de': 'Beispielregeln für die Demo',
                               'es': 'Reglas de ejemplo para la demo', 'pt': 'Regras de exemplo para a demo', 'pap': 'Reglanan di ehèmpel pa demo'}[decision['language']])
    text = '\n\n'.join(paragraphs)
    check(0 < len(text) <= 4096, 'invalid_conversation_reply')
    return text, question, branch


def record_delivery(db, scope, delivery_id, body, status, *, assets=None, buttons=None, question=''):
    """Same local transaction as transport status. Acceptance is not guest receipt."""
    from shared.isluno_config import require_scope
    require_scope(scope)
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'isluno_booking_sessions' not in tables:
        return
    row = db.execute('SELECT payload FROM isluno_booking_sessions WHERE scope_key=?', (scope.key,)).fetchone()
    if row is None:
        return
    session = json.loads(row[0])
    history = session.setdefault('history', [])
    if any(h.get('delivery_id') == delivery_id for h in history):
        return
    text = body.get('message') or body.get('interactive', {}).get('body', {}).get('text', '')
    event = {'role': 'assistant', 'content': text, 'delivery_id': delivery_id, 'delivery_status': status,
             'guest_receipt_verified': False, 'media': assets or [], 'buttons': buttons or body.get('buttons', []),
             'question': question}
    history.append(event)
    session['history'] = history[-100:]
    if status == 'accepted' and question:
        session['last_accepted_question'] = question
    db.execute('UPDATE isluno_booking_sessions SET payload=? WHERE scope_key=?',
               (json.dumps(session, ensure_ascii=False), scope.key))
