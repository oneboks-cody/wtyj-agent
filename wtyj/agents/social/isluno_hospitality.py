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
    'cards': {'type':'array', 'maxItems':2, 'items':{'type':'object','additionalProperties':False,
        'properties':{'product_id':{'type':'string'},'paragraphs':{'type':'array','minItems':1,'maxItems':2,'items':{'type':'string','maxLength':900}}},
        'required':['product_id','paragraphs']}},
    'photo_location': {'type':'string','maxLength':120},
    'photo_opt_out': {'type':'boolean'},
    'estimate': {'type': 'object', 'additionalProperties': False, 'properties': {
        'product_id': {'type':'string'}, 'date': {'type':'string'}, 'slot_id': {'type':'string'},
        'guest_ages': {'type':'array','items':{'type':'integer'}}, 'options': {'type':'object','additionalProperties':{'type':'integer'}},
        'pickup': {'type':'boolean'}}, 'required':['product_id','date','slot_id','guest_ages','options','pickup']},
    'replies': {'type': 'object', 'additionalProperties': False, 'properties': {
        k: {'type': 'object', 'additionalProperties': False, 'properties': {
            'paragraphs': {'type': 'array', 'minItems': 0, 'maxItems': 4,
                           'items': {'type': 'string', 'maxLength': 1800}},
            'question': {'type': 'string', 'maxLength': 500}},
            'required': ['paragraphs', 'question']} for k in BRANCHES}},
}, 'required': ['stage', 'memory', 'discussed', 'consent', 'photo', 'replies']}

PROMPT = '''
HOSPITALITY CONTRACT (supersedes earlier restrictions on prose):
Return hospitality in this SAME response. You are a warm, thoughtful holiday host,
relaxed in language and precise in arrangements. Welcome first-time guests, recognize
supplied holiday details, give brief relevant inspiration, ask one easy next question.
On the first guest-facing introduction, briefly identify yourself by the supplied
assistant name and mention the supplied brand website naturally. This is an introduction,
not a request to leave WhatsApp. Keep helping and arranging experiences in this chat.
Use the brand voice for the destination welcome and emoji style in every supported language.
A name or month-long holiday is browsing memory, never permission to create a draft.
Use natural contractions and 2–3 short paragraphs, no em/en dash sentence breaks,
no repeated greetings/names, hype, invented human experiences or urgency.
Contextual holiday emojis are welcome as directed by the brand voice, even if the guest
has not used emojis. Never pile them up, replace useful words or decorate every sentence.
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
QUICK INTAKE: after the first welcome, ordinary qualification turns should contain
only the next short question. Use browsing.paragraphs=[] and put the question ONCE
in browsing.question. No emojis, praise, party recap, sales pitch or explanation of
why you are asking. Never put the same question or a paraphrase in paragraphs.
For example: "How many adults and children?", "How old are the children?", or
"What would you enjoy most: sea, beaches, adventure or exploring the island?"
Adapt the question to known answers, including corrections. Acknowledge a correction
only when needed to remove ambiguity, in a few words. Do not call an age or group
size ideal for activities without verified suitability. Ask one detail, then move on.
The first welcome may briefly introduce Tracy and explain the quick questions once.
Save atmosphere, emojis and descriptive language for actual trip recommendations.
If the guest also asks a question, answer it briefly before the next intake question;
never suppress a requested answer just to keep intake short.
Always supply browsing as a neutral shared answer: answer the guest's questions and
acknowledge their request without claiming an operation has succeeded. For any booking
action also supply error with {operation}. Other outcome branches are OPTIONAL; do not
write every speculative branch. If omitted, the server combines the common answer with
the actual authoritative outcome. For add/new/update, supply one natural localized
preparation question containing {missing_field}, in pending.question if that branch is
supplied, otherwise in browsing.question. The server binds the actual missing detail,
product and departure times, and omits this question when nothing is missing. This
question must not claim the itinerary is saved. Include a branch only
when a materially different natural response is needed. Keep common prose concise.
For a first general enquiry, welcome the guest and acknowledge their holiday. Briefly
explain that you will ask a few quick questions to find experiences that suit them.
Ask ONE easy question per turn. Start with how many people the experiences are for.
Next establish how many are adults and how many are children; ask children's ages
when needed for suitable recommendations. Skip anything already supplied, and never
invent ages or assume all holiday companions will join every selected activity.
Then ask what they would enjoy most, offering just three or four simple choices at a
time: sea and snorkelling, adventure, beaches, or exploring the island. Follow their
answer rather than reciting a checklist. For adult groups, sunset drinks or a lively
outing can be an optional interest, never an assumption that everyone drinks. Do not
pitch alcohol to children. Ask pace, occasion, accessibility or budget only when useful.
Store volunteered answers together in hospitality.memory and reuse them. These are
browsing preferences, not booking consent or a reason to create a draft. Once you have
enough context, recommend one strong choice and at most one alternative. Explain why
it fits, then invite a useful next step toward planning without urgency or pressure.
If a guest asks about a specific trip or asks to book, answer and help immediately;
do not hold their answer behind the introductory questions. Never repeat known questions.
Keep the introduction to two short paragraphs plus the one next question, without a
catalogue dump. Empty product_ids/fact_keys and cards are valid during qualification.
For a fresh hello after a rejected reply, do not assume the guest saw your recommendations.
The server selects the actual branch after applying the action. Never claim success in
browsing or error. error is a failed attempted item update; other successful independent
parts must still be answered. The server supplies the authoritative operation result.
A broad category overview may summarize enabled catalog categories, but is not a claim
of specific product availability or universal suitability. All specific product claims
still require the source bindings below.
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
Use a concrete next question that helps the guest choose or plan, without pressure.
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

VISUAL DISCOVERY:
A broad first enquiry (name/holiday dates without activity preferences) needs a brief
personal introduction, a brief explanation of the quick questions, and ONE party-size question. Skip questions already answered. Set product_ids=[] and
cards=[]; do not list three tours. When a guest specifies a trip or useful interests,
answer directly; do not force a qualification questionnaire. Usually recommend one or
at most two experiences. Use stage recommendation for suggestions and photo initial.
For recommendations, supply cards [{product_id,paragraphs:[...]}], one card per selected
product. Each card gives one short source-bound description and a natural reason it fits.
Prefer the concise summary binding; long additional_information belongs behind Trip
details or in an answer to an explicitly asked question, not a recommendation card.
Use the existing name/fact bindings; do not add unsupported claims, prices or place lore.
Include the location_context binding when supplied so unfamiliar place names are explained inline.
Keep common browsing paragraphs brief and separate from card content; no duplicated tour
summaries. Put a single next question in browsing.question. The server sends each card
with its correctly associated ordinary gallery image and product-specific controls.
For a specific trip enquiry use the same cards shape when helpful. photo none is for
text-only answers or when images were declined; use intent details for such answers.
More photos is exploration, never booking consent. Never ask guests to leave WhatsApp to
understand a place or book. Explain unfamiliar place names inline ONLY with supplied
verified facts; when the source does not identify a place, say this is unconfirmed.
For a request to see a specific location/stop, set photo_location to that location ID/name.
The server requires explicit asset location provenance; a general trip gallery is not
proof of a specific stop. Do not claim a photo depicts a stop without that evidence.
No video asset is currently approved for this flow. Offer available trip images instead.

PHOTOS AND MEMORY:
Every recommendation or message describing/selling a trip belongs in a product card
with its verified trip image. Use photo initial for these cards, including renewed
recommendations. Do not put trip descriptions in a separate image-free common paragraph.
Set photo_opt_out true ONLY when the guest explicitly asks for no photos; otherwise false.
Use photo none for ordinary intake questions, administrative replies or explicit opt-out,
not for a trip pitch. A trip image may be reused as illustration without claiming it is new.
Use brief, inviting, source-bound descriptions and one reason the experience fits this
party. Premium means attentive, specific and easy to read, never exaggerated superlatives.
photo initial for a specific newly discussed product, more/all when requested, repeat
only for explicit repeat. The server uses its real gallery, bounded batches and durable
send history. Never put image URLs in prose. No booking action for a gallery request.
Delivery history distinguishes provider acceptance from confirmed delivered/read events.
Only delivery_context.confirmed_message_ids proves an earlier assistant message was delivered
in this same conversation. Say "I have shared ideas already" or make similar prior-sharing
references ONLY when those confirmed entries actually contain the relevant recommendations.
Browsing memory, discussed products, generated plans, rejected/failed/ambiguous content and
acceptance without a delivered/read callback are not proof of prior sharing. A confirmed
delivery does not prove the guest read it. Do not repeat a first-contact welcome when the
confirmed history establishes a continuing conversation. For a genuinely fresh broad
introduction, identify yourself, acknowledge the supplied holiday context and ask the next missing party or interests question.
Do not assume planned/failed/ambiguous messages or questions were received.
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
    check(isinstance(value, dict) and set(SCHEMA['required']) <= set(value) <= set(SCHEMA['required']) | {'estimate','cards','photo_location','photo_opt_out'}, 'invalid_hospitality_contract')
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
    check(type(value.get('photo_opt_out',False)) is bool, 'invalid_photo_opt_out')
    if 'estimate' in value:
        estimate = value['estimate']
        check(isinstance(estimate, dict) and set(estimate) == set(SCHEMA['properties']['estimate']['required'])
              and estimate['product_id'] in decision['product_ids'], 'invalid_price_estimate')
    check(isinstance(value.get('photo_location',''),str) and len(value.get('photo_location',''))<=120, 'invalid_photo_location')
    cards=value.get('cards',[])
    check(isinstance(cards,list) and len(cards)<=2,'invalid_visual_cards')
    seen=set()
    for card in cards:
        check(isinstance(card,dict) and set(card)=={'product_id','paragraphs'},'invalid_visual_card')
        product_id=card['product_id']
        check(product_id in decision['product_ids'] and product_id not in seen,'invalid_card_product')
        seen.add(product_id)
        check(isinstance(card['paragraphs'],list) and 1<=len(card['paragraphs'])<=2,'invalid_card_paragraphs')
        for text in card['paragraphs']:
            check(isinstance(text,str) and 0<len(text)<=900,'invalid_card_text')
            for ref in references(text):
                bits=ref.split(':')
                check(len(bits) in {2,3} and bits[0] in {'name','fact'} and bits[1]==product_id,'invalid_card_reference')
                check(bits[0]=='name' and len(bits)==2 or bits[0]=='fact' and len(bits)==3 and bits[2] in products[product_id]['facts'] and bits[2] in decision['fact_keys'],'unsupported_card_fact')
    if cards:
        check(decision['booking']['action']=='none' and seen==set(decision['product_ids']),'invalid_card_scope')
    replies = value['replies']
    check(isinstance(replies, dict) and 'browsing' in replies and not set(replies) - set(BRANCHES), 'invalid_hospitality_replies')
    for branch, reply in replies.items():
        check(isinstance(reply, dict) and set(reply) == {'paragraphs', 'question'}, 'invalid_hospitality_reply')
        check(isinstance(reply['paragraphs'], list) and 0 <= len(reply['paragraphs']) <= 4, 'invalid_reply_paragraphs')
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
                check(branch != 'browsing' or ref == 'estimate_total' and 'estimate' in value
                      or ref == 'missing_field' and decision['booking']['action'] in {'add','new','update'}
                      and all(ref not in references(t) for t in reply['paragraphs']), 'transaction_claim_in_browsing')
        if branch in {'error', 'review', 'cancelled', 'no_active', 'choose_item', 'choose_product', 'approval_unavailable'}:
            check('operation' in refs, 'missing_authoritative_outcome')
    if 'estimate' in value:
        check('error' in replies, 'missing_estimate_error_reply')
    action = decision['booking']['action']
    if action in {'add','new','update'}:
        preparation = replies.get('pending', replies['browsing'])
        check('missing_field' in references(preparation['question']), 'missing_preparation_question')
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
    if branch=='browsing' and action=='none' and not value.get('cards'):
        moved={text for card in card_paragraphs(value) for text in card['paragraphs']}
        selected['paragraphs']=[text for text in selected['paragraphs'] if text not in moved]
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
        # Keep the model's neutral answer and preparation question; no saved claim.
        selected['paragraphs']=[t for t in selected['paragraphs'] if t!='{operation}']
        prefix=COPY[session['chat_language']][3]+': '
        bindings['missing_field']=missing_prompt(session,snapshot).removeprefix(prefix)
    elif fallback and branch in {'choose_item','choose_product'}:
        selected['paragraphs']=[t for t in selected['paragraphs'] if t!='{operation}']
        selected['question']='{operation}'
    elif fallback and branch not in {'saved','browsing'}:
        selected['question']=''
    if branch != 'pending' and 'missing_field' in references(selected['question']):
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
    check(len(text)<=4096 and (bool(text) or branch=='browsing' and action=='none' and bool(card_paragraphs(value))), 'invalid_conversation_reply')
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


def card_paragraphs(value):
    if value.get('cards'):return value['cards']
    # Compatibility: move already structured one-product paragraphs into that
    # product's card. Keep shared comparisons/questions and result bindings intact.
    grouped={}
    for text in value['replies']['browsing']['paragraphs']:
        refs=references(text)
        if refs and all(r.split(':')[0] in {'fact','name'} for r in refs):
            products={r.split(':')[1] for r in refs}
            if len(products)==1:grouped.setdefault(next(iter(products)),[]).append(text)
    return [{'product_id':p,'paragraphs':paragraphs} for p,paragraphs in grouped.items()]


def render_cards(value, decision, snapshot):
    products={p['id']:p for p in snapshot['catalog']['products']}
    output={}
    for card in card_paragraphs(value) if decision['booking']['action']=='none' else []:
        product=products[card['product_id']]
        facts=isluno_understanding.facts(product) if decision['language']=='en' else decision['translations'].get(product['id'],{})
        paragraphs=[]
        for text in card['paragraphs']:
            for ref in references(text):
                bits=ref.split(':');replacement=product['name'] if bits[0]=='name' else facts[bits[2]]
                text=text.replace('{'+ref+'}',replacement)
            paragraphs.append(presentation_text(text))
        text='\n\n'.join(paragraphs)
        from agents.social.isluno_wire import units
        if units(text)>600 and decision['intent']=='discover':
            summary=facts.get('summary','')
            text=summary if units(summary)<=600 else ''
        output[product['id']]=text
    return output
