"""One understanding call, atomic guest/intake updates, and scoped operator work."""
import copy
from datetime import datetime, timezone
import hashlib
import json
from contextlib import contextmanager

from agents.social.isluno_itinerary import ItineraryStore, encoded
from agents.social.isluno_discovery import DiscoveryStore, CLARIFICATIONS
from agents.social import isluno_understanding as discovery_understanding
from agents.social import isluno_conversation_understanding as understanding
from agents.social import isluno_hospitality as hospitality
from shared.isluno_catalog import CatalogStore, CatalogError, quote_rules, validate_snapshot
from shared.isluno_config import require_scope, verified_scope
from shared.isluno_pricing import check, ItineraryError

# Canonical operation/price text is rendered by the application, never the model.
COPY = {
 'en': ['Demo itinerary saved.', 'Itinerary cancelled. No payment or real reservation was made.', 'Your request is recorded for operator review. No change or refund has been made.', 'Please provide', 'guest name', 'guest ages', 'trip date', 'departure slot', 'pickup choice', 'pickup location', 'required extras', 'Which item should change?', 'Please correct these trip details', 'Quote approval is not available yet. No payment or reservation has been made.', 'Demo total', 'Document language'],
 'nl': ['Demo-reisplan opgeslagen.', 'Reisplan geannuleerd. Er is niets betaald of echt gereserveerd.', 'Je verzoek is vastgelegd voor het team. Er is niets gewijzigd of terugbetaald.', 'Geef alsjeblieft', 'naam van de gast', 'leeftijden', 'reisdatum', 'vertrektijd', 'keuze voor ophalen', 'ophaallocatie', 'verplichte extra’s', 'Welk onderdeel wil je wijzigen?', 'Corrigeer deze reisgegevens', 'Offertegoedkeuring is nog niet beschikbaar. Er is niets betaald of gereserveerd.', 'Demototaal', 'Documenttaal'],
 'de': ['Demo-Reiseplan gespeichert.', 'Reiseplan storniert. Keine Zahlung oder echte Buchung erfolgt.', 'Deine Anfrage ist für das Team erfasst. Keine Änderung oder Rückzahlung erfolgt.', 'Bitte angeben', 'Name des Gastes', 'Alter der Gäste', 'Reisedatum', 'Abfahrtszeit', 'Abholwunsch', 'Abholort', 'Pflicht-Extras', 'Welchen Eintrag möchtest du ändern?', 'Bitte diese Reisedaten korrigieren', 'Angebotsfreigabe ist noch nicht verfügbar. Keine Zahlung oder Buchung erfolgt.', 'Demo-Gesamtbetrag', 'Dokumentsprache'],
 'es': ['Itinerario demo guardado.', 'Itinerario cancelado. No se realizó ningún pago ni reserva real.', 'La solicitud está registrada para el equipo. No se realizó ningún cambio ni reembolso.', 'Indica', 'nombre del huésped', 'edades', 'fecha del viaje', 'hora de salida', 'opción de recogida', 'lugar de recogida', 'extras obligatorios', '¿Qué elemento quieres cambiar?', 'Corrige estos datos del viaje', 'La aprobación del presupuesto aún no está disponible. No se realizó ningún pago ni reserva.', 'Total demo', 'Idioma del documento'],
 'pt': ['Itinerário demo guardado.', 'Itinerário cancelado. Sem pagamento ou reserva real.', 'O pedido está registado para a equipa. Não foi feita alteração nem reembolso.', 'Indique', 'nome do hóspede', 'idades', 'data da viagem', 'hora de partida', 'opção de recolha', 'local de recolha', 'extras obrigatórios', 'Que item deseja alterar?', 'Corrija estes dados da viagem', 'A aprovação do orçamento ainda não está disponível. Sem pagamento ou reserva.', 'Total demo', 'Idioma do documento'],
 'pap': ['Itinerario demo wardá.', 'Itinerario kanselá. No a hasi pago ni reservashon real.', 'Bo petishon ta registrá pa e ekipo. No a hasi kambio ni reembolso.', 'Por fabor duna', 'nòmber di e huéspet', 'edatnan', 'fecha di biahe', 'ora di salida', 'opshon di rekohida', 'lugá di rekohida', 'ekstranan obligatorio', 'Kua parti bo ke kambia?', 'Por fabor koregí e datonan di biahe', 'Aprobashon di oferta ainda no ta disponibel. No a hasi pago ni reservashon.', 'Total demo', 'Idioma di dokumento'],
}


def opaque(scope, trigger, suffix):
    return hashlib.sha256(encoded([scope.key, trigger, suffix]).encode()).hexdigest()[:32]


# Process-local ownership only distinguishes an executing call from an unfinished
# durable claim. Losing this set never authorizes another model invocation.
_ACTIVE_UNDERSTANDING = set()

def understanding_inflight(store,scope,trigger):
    return (str(store.itinerary.db_path),scope.key,trigger) in _ACTIVE_UNDERSTANDING

@contextmanager
def understanding_claim(store,scope,trigger):
    key=(str(store.itinerary.db_path),scope.key,trigger)
    _ACTIVE_UNDERSTANDING.add(key)
    try:yield
    finally:_ACTIVE_UNDERSTANDING.discard(key)


def default_session():
    return {'revision': 0, 'guest': {}, 'pending': {}, 'active_itinerary_id': None,
            'chat_language': 'en', 'document_language': None, 'history': [], 'item_details': {}, 'browsing': {}, 'stage': 'welcome'}


class ConversationStore:
    def __init__(self, itinerary=None):
        self.itinerary = itinerary or ItineraryStore()

    @contextmanager
    def db(self):
        with self.itinerary._connection() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS isluno_catalog_snapshots (revision TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS isluno_booking_sessions (scope_key TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS isluno_conversation_turns (
                    scope_key TEXT NOT NULL, trigger_id TEXT NOT NULL, baseline TEXT NOT NULL,
                    decision TEXT, outcome TEXT, claimed_at TEXT, PRIMARY KEY(scope_key,trigger_id));
                CREATE TABLE IF NOT EXISTS isluno_operator_requests (
                    id TEXT PRIMARY KEY, scope_key TEXT NOT NULL, itinerary_id TEXT, reason TEXT NOT NULL,
                    request_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending');
            ''')
            if 'claimed_at' not in {row[1] for row in db.execute('PRAGMA table_info(isluno_conversation_turns)')}:
                with db:
                    db.execute('BEGIN IMMEDIATE')
                    if 'claimed_at' not in {row[1] for row in db.execute('PRAGMA table_info(isluno_conversation_turns)')}:
                        db.execute('ALTER TABLE isluno_conversation_turns ADD COLUMN claimed_at TEXT')
            from agents.social.isluno_quotes import init_schema
            init_schema(db)
            yield db

    def session(self, scope):
        require_scope(scope)
        with self.db() as db:
            row = db.execute('SELECT payload FROM isluno_booking_sessions WHERE scope_key=?', (scope.key,)).fetchone()
            return json.loads(row[0]) if row else default_session()

    def reserve(self, scope, trigger, source_snapshot=None):
        require_scope(scope)
        check(isinstance(trigger, str) and 0 < len(trigger) <= 512, 'missing_verified_message_id')
        with self.db() as db, db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM isluno_conversation_turns WHERE scope_key=? AND trigger_id=?', (scope.key, trigger)).fetchone()
            if row:
                check(row['decision'] is not None or row['outcome'] is not None, 'understanding_already_claimed')
                return json.loads(row['baseline']), json.loads(row['decision']) if row['decision'] else None, json.loads(row['outcome']) if row['outcome'] else None
            row = db.execute('SELECT payload FROM isluno_booking_sessions WHERE scope_key=?', (scope.key,)).fetchone()
            saved = json.loads(row[0]) if row else default_session()
            snapshot = validate_snapshot(source_snapshot) if source_snapshot is not None else CatalogStore(self.itinerary.catalog_path).snapshot()
            db.execute('INSERT OR IGNORE INTO isluno_catalog_snapshots VALUES(?,?)', (snapshot['revision'], encoded(snapshot)))
            saved['catalog_revision'] = snapshot['revision']
            db.execute('INSERT INTO isluno_conversation_turns(scope_key,trigger_id,baseline,claimed_at) VALUES(?,?,?,?)', (scope.key, trigger, encoded(saved), self.itinerary.clock().isoformat()))
            return saved, None, None

    def source_snapshot(self, scope, baseline):
        require_scope(scope)
        check(bool(baseline.get('catalog_revision')), 'missing_durable_catalog_binding')
        with self.db() as db:
            row = db.execute('SELECT payload FROM isluno_catalog_snapshots WHERE revision=?', (baseline['catalog_revision'],)).fetchone()
        check(row is not None, 'missing_durable_catalog_snapshot')
        return validate_snapshot(json.loads(row[0]))

    def record_decision(self, scope, trigger, decision):
        require_scope(scope)
        with self.db() as db, db:
            db.execute('UPDATE isluno_conversation_turns SET decision=? WHERE scope_key=? AND trigger_id=? AND decision IS NULL', (encoded(decision), scope.key, trigger))

    def reviews(self, scope):
        require_scope(scope)
        with self.db() as db:
            return [dict(row) for row in db.execute('SELECT id,itinerary_id,reason,status FROM isluno_operator_requests WHERE scope_key=?', (scope.key,))]

    def apply(self, scope, trigger, baseline, decision, text):
        require_scope(scope)
        booking = decision['booking']
        snapshot = self.source_snapshot(scope, baseline)
        with self.db() as db, db:
            db.execute('BEGIN IMMEDIATE')
            turn = db.execute('SELECT outcome FROM isluno_conversation_turns WHERE scope_key=? AND trigger_id=?', (scope.key, trigger)).fetchone()
            if turn['outcome']:
                return json.loads(turn['outcome'])
            row = db.execute('SELECT payload FROM isluno_booking_sessions WHERE scope_key=?', (scope.key,)).fetchone()
            current = json.loads(row[0]) if row else default_session()
            check(current['revision'] == baseline['revision'], 'conversation_revision_changed')
            session = copy.deepcopy(current)
            session.setdefault('item_details', {})
            session['chat_language'] = decision['language']
            session['catalog_revision'] = snapshot['revision']
            if booking['document_language'] is not None:
                session['document_language'] = booking['document_language']
            active = self.itinerary._current(db, scope, session['active_itinerary_id']) if session['active_itinerary_id'] else None
            action = booking['action']
            if decision.get('hospitality'):
                hospitality.authorize(decision['hospitality'], text)
                hospitality.remember(session, decision['hospitality'])
            if action in {'summary', 'approve'} and session['document_language'] is None:
                session['document_language'] = session['chat_language']
            review = action == 'human' or decision['intent'] == 'human' or (active and active['status'] != 'draft' and (action in {'add', 'update', 'remove', 'cancel'} or (booking['guest'] and action in {'update', 'remove'})))
            status, error = 'saved', None
            if action == 'none':
                # Browsing information cannot complete pending intake, alter booked
                # guest records, request review, or invalidate an existing quote.
                session.setdefault('browsing', {}).setdefault('guest', {}).update(booking['guest'])
            elif review:
                request_id = opaque(scope, trigger, 'review')
                reason = 'post_booking_change' if active and active['status'] != 'draft' and action != 'human' and decision['intent'] != 'human' else 'customer_request'
                existing_review = db.execute("SELECT id FROM isluno_operator_requests WHERE scope_key=? AND itinerary_id IS ? AND reason=? AND status IN ('pending','active')",
                                             (scope.key, session['active_itinerary_id'], reason)).fetchone()
                if existing_review is None:
                    db.execute('INSERT INTO isluno_operator_requests(id,scope_key,itinerary_id,reason,request_json) VALUES(?,?,?,?,?)',
                               (request_id, scope.key, session['active_itinerary_id'], reason, encoded(booking)))
                status = 'review'
            elif action in {'add', 'new'} and not (booking['updates'] or decision['product_ids']):
                session['guest'].update(booking['guest'])
                status = 'choose_product'
            elif action in {'update', 'remove'} and (
                active is None or not booking['updates'] or any(
                    (u.get('item_id') and u['item_id'] not in ({i['id'] for i in active['items']} | set(session['pending'])))
                    or (not u.get('item_id') and len({i['id'] for i in active['items']} | set(session['pending'])) != 1)
                    for u in booking['updates'])):
                status = 'choose_item'
            elif action == 'approve':
                session['guest'].update(booking['guest'])
                status = 'approval_unavailable'
            elif action == 'cancel':
                if active:
                    active = self.itinerary._apply(scope, active['id'], opaque(scope, trigger, 'cancel'), active['revision'], {'action': 'cancel'}, connection=db, catalog_snapshot=snapshot)
                    session['pending'] = {}
                status = 'cancelled' if active else 'no_active'
            else:
                if action in {'add', 'new', 'update'}:
                    for key, value in session.get('browsing', {}).get('guest', {}).items():
                        session['guest'].setdefault(key, value)
                session['guest'].update(booking['guest'])
                if action in {'add', 'new'}:
                    if active is None or action == 'new':
                        itinerary_id = opaque(scope, trigger, 'itinerary')
                        active = self.itinerary._apply(scope, itinerary_id, opaque(scope, trigger, 'create'), None, {'action': 'create'}, connection=db, catalog_snapshot=snapshot)
                        session['active_itinerary_id'], session['pending'] = itinerary_id, {}
                    updates = booking['updates'] or [{'product_id': p} for p in decision['product_ids']]
                    for index, update in enumerate(updates):
                        item_id = opaque(scope, trigger, 'item-' + str(index))
                        session['pending'][item_id] = {'item_id': item_id, 'guest_name': session['guest'].get('name'), **{k:v for k,v in update.items() if k != 'item_id'}}
                elif action in {'update', 'remove'}:
                    check(active is not None, 'no_active_itinerary')
                    for update in booking['updates']:
                        candidates = {i['id'] for i in active['items']} | set(session['pending'])
                        item_id = update.get('item_id') or (next(iter(candidates)) if len(candidates) == 1 else None)
                        check(item_id in candidates, 'choose_item')
                        if action == 'remove':
                            session['pending'].pop(item_id, None)
                            if any(i['id'] == item_id for i in active['items']):
                                active = self.itinerary._apply(scope, active['id'], opaque(scope, trigger, 'remove-' + item_id), active['revision'], {'action': 'remove', 'item_id': item_id}, connection=db, catalog_snapshot=snapshot)
                        else:
                            existing = next((i['selection'] for i in active['items'] if i['id'] == item_id), {})
                            pending = session['pending'].setdefault(item_id, {**copy.deepcopy(existing), **{k:v for k,v in session['item_details'].get(item_id, {}).items() if k == 'pickup_location'}})
                            if update.get('product_id') and update['product_id'] != pending.get('product_id'):
                                for key in ('slot_id', 'options', 'pickup', 'pickup_location'):
                                    pending.pop(key, None)
                            options = {**pending.get('options', {}), **update.get('options', {})}
                            pending.update(update)
                            pending['options'] = options
                            pending['item_id'] = item_id
                            if booking['guest'].get('name'):
                                pending['guest_name'] = booking['guest']['name']
                # Guests and incomplete selections persist even when pricing rejects
                # a correction; all item writes from this turn roll back together.
                db.execute('SAVEPOINT item_updates')
                pending_before = copy.deepcopy(session['pending'])
                active_before = copy.deepcopy(active)
                details_before = copy.deepcopy(session['item_details'])
                try:
                    for item_id, pending in (list(session['pending'].items()) if action in {'add', 'update', 'new'} else []):
                        missing = complete_selection(pending, session['guest'], snapshot)
                        if missing:
                            continue
                        exists = any(i['id'] == item_id for i in active['items'])
                        selection = {k: pending[k] for k in ('item_id','product_id','date','slot_id','guest_ages','options','pickup')}
                        active = self.itinerary._apply(scope, active['id'], opaque(scope, trigger, 'save-' + item_id), active['revision'],
                            {'action': 'update' if exists else 'add', 'selection': selection}, connection=db, catalog_snapshot=snapshot)
                        prior_name = session['item_details'].get(item_id, {}).get('guest_name')
                        # Keep the selected party through incomplete/invalid corrections.
                        item_guest = pending.get('guest_name') or prior_name or session['guest']['name']
                        session['item_details'][item_id] = {'guest_name': item_guest, 'pickup_location': pending.get('pickup_location')}
                        del session['pending'][item_id]
                except (ItineraryError, CatalogError) as exc:
                    db.execute('ROLLBACK TO item_updates')
                    session['pending'], active = pending_before, active_before
                    session['item_details'] = details_before
                    error = exc.code if isinstance(exc, ItineraryError) else 'product_rules_unavailable'
                db.execute('RELEASE item_updates')
            from agents.social.isluno_quotes import invalidate_changed
            if booking['guest'].get('name') and action not in {'none', 'new', 'add', 'update'} and status != 'review':
                for detail in [*session['item_details'].values(), *session['pending'].values()]:
                    if detail.get('guest_name') == current['guest'].get('name'):
                        detail['guest_name'] = booking['guest']['name']
            if action != 'none' or booking['document_language'] is not None:
                invalidate_changed(db, scope, session, active)
            session['revision'] += 1
            outcome = {'session': session, 'itinerary': active, 'status': status, 'error': error, 'review_reason': reason if review else None, 'catalog_revision': snapshot['revision']}
            session['history'] = (session['history'] + [{'role':'user','content':text or '[WhatsApp reply action]', 'trigger_id': trigger}])[-100:]
            db.execute('INSERT INTO isluno_booking_sessions VALUES(?,?) ON CONFLICT(scope_key) DO UPDATE SET payload=excluded.payload', (scope.key, encoded(session)))
            db.execute('UPDATE isluno_conversation_turns SET outcome=? WHERE scope_key=? AND trigger_id=?', (encoded(outcome), scope.key, trigger))
            return outcome


def complete_selection(pending, guest, snapshot):
    product = next((p for p in snapshot['catalog']['products'] if p['id'] == pending.get('product_id')), None)
    check(product is not None, 'product_unavailable')
    rules = quote_rules(product, mode='demo')['rules']
    if 'guest_ages' not in pending and guest.get('ages'):
        pending['guest_ages'] = copy.deepcopy(guest['ages'])
    if 'slot_id' not in pending and len(rules['schedule']['slots']) == 1:
        pending['slot_id'] = rules['schedule']['slots'][0]['id']
    if rules['pickup']['mode'] != 'priced_option':
        pending.setdefault('pickup', rules['pickup']['mode'] == 'included')
    pending.setdefault('options', {})
    if not guest.get('name'): return 4
    for key, label in [('guest_ages',5), ('date',6), ('slot_id',7), ('pickup',8)]:
        if key not in pending: return label
    if pending['pickup'] and not pending.get('pickup_location'): return 9
    if rules['pickup']['mode'] == 'priced_option':
        option_id = rules['pickup']['option_id']
        option = next(o for o in rules['options'] if o['id'] == option_id)
        if not pending['pickup']:
            pending['options'][option_id] = 0
        elif option['basis'] == 'per_person':
            pending['options'][option_id] = len(pending['guest_ages'])
        elif option['basis'] == 'per_booking':
            pending['options'][option_id] = 1
        elif not pending['options'].get(option_id):
            return 10
    if any(o['required'] and not pending['options'].get(o['id']) for o in rules['options']): return 10
    return None


def render(outcome, snapshot):
    session, itinerary = outcome['session'], outcome['itinerary']
    words = COPY[session['chat_language']]
    from shared.isluno_config import active_profile
    outcomes = active_profile().get('conversation_outcomes', {}).get(session['chat_language'], {})
    if outcome['status'] == 'no_active':
        return {'en':'There is no active draft itinerary to cancel.', 'nl':'Er is geen actief conceptreisplan om te annuleren.',
                'de':'Es gibt keinen aktiven Reiseentwurf zum Stornieren.', 'es':'No hay un borrador de itinerario activo para cancelar.',
                'pt':'Não há itinerário em rascunho ativo para cancelar.', 'pap':'No tin un itinerario di borrador aktivo pa kanselá.'}[session['chat_language']]
    if outcome['status'] == 'choose_product': return CLARIFICATIONS[session['chat_language']][1]
    if outcome['status'] == 'choose_item': return words[11]
    if outcome['status'] == 'review':
        return outcomes.get('human_requested', words[2]) if outcome.get('review_reason') == 'customer_request' else words[2]
    if outcome['status'] == 'cancelled': return words[1]
    if outcome['status'] == 'approval_unavailable': return words[13]
    error_field = {'schedule_overlap': 6, 'invalid_date': 6, 'date_in_past': 6, 'departure_in_past': 6,
                   'departure_day_unavailable': 6, 'invalid_guest_age': 5, 'guest_capacity_exceeded': 5,
                   'adult_required': 5, 'invalid_option_quantity': 10, 'pickup_option_mismatch': 8}.get(outcome['error'])
    lines = [outcomes.get(outcome['error']) or (words[12] + (': ' + words[error_field] if error_field else '')) if outcome['error'] else words[0]]
    if not outcome['error'] and session['pending']:
        lines=[]  # Native Plan starts intake; no claim that a trip has been saved.
    if itinerary:
        for item in itinerary['items'][:5]:
            lines.append(item['product']['name'] + ' · ' + item['selection']['date'] + ' · ' + item['starts_at'][11:16])
        if any(i['pricing_snapshot']['pricing_mode'] == 'demo_sample' for i in itinerary['items']) or any(
                p.get('demo_rules') for p in snapshot['catalog']['products'] if p['id'] in {v.get('product_id') for v in session['pending'].values()}):
            lines.append({'en':'Sample demo rules', 'nl':'Voorbeeldregels voor de demo', 'de':'Beispielregeln für die Demo',
                          'es':'Reglas de ejemplo para la demo', 'pt':'Regras de exemplo para a demo', 'pap':'Reglanan di ehèmpel pa demo'}[session['chat_language']])
        total = itinerary['totals']
        if total['currency']:
            lines.append(words[14] + ': ' + total['currency'] + ' ' + str(total['total_minor']//100) + '.' + str(total['total_minor']%100).zfill(2))
    prompt = missing_prompt(session, snapshot)
    if prompt:
        lines.append(prompt + '.')
    if session['document_language']:
        lines.append(words[15] + ': ' + session['document_language'])
    return '\n\n'.join(lines)[:4096]


def missing_prompt(session, snapshot):
    words = COPY[session['chat_language']]
    for pending in session['pending'].values():
        try:
            missing = complete_selection(copy.deepcopy(pending), session['guest'], snapshot)
        except (ItineraryError, CatalogError):
            missing = None
        if missing:
            product = next((p for p in snapshot['catalog']['products'] if p['id'] == pending.get('product_id')), None)
            prompt = words[3] + ': ' + words[missing]
            if product and missing not in {4,5}:
                prompt += ' · ' + product['name']
            if product and missing == 7:
                rules = quote_rules(product, mode='demo')['rules']
                prompt += ' (' + ', '.join(slot['start'] for slot in rules['schedule']['slots']) + ')'
            return prompt
    return ''


def handle_message(message, *, store=None, discovery=None, understand=None):
    from agents.social.isluno_recovery import RecoveryStore
    from agents.social.isluno_transition import ensure,quarantined_inbound
    store=store or ConversationStore()
    scope=verified_scope(account_id=message.get('_zernio_account_id',''),conversation_id=message.get('from',''),customer_ref=message.get('_zernio_sender_id',''))
    trigger=message.get('message_id') or message.get('_ali_action_id')
    check(isinstance(trigger,str) and bool(trigger),'missing_verified_message_id')
    recovery=RecoveryStore(store)
    try:
        ensure(store.itinerary.db_path,store.itinerary.clock())
        check(not quarantined_inbound([trigger],store.itinerary.db_path),'legacy_turn_quarantined')
        recovery.observe(scope,trigger,message.get('_zernio_sent_at',''))
        recovery.reconcile_claims()
        token=str(message.get('_zernio_interactive_id',''))
        if token and not token.startswith(('ip_','ie_','iq_','isl_')):
            raise ItineraryError('legacy_action_quarantined')
        result=_handle_message(message,store=store,discovery=discovery,understand=understand)
    except PermissionError:
        raise
    except Exception as exc:
        if isinstance(exc, ItineraryError) and exc.code in {'understanding_already_claimed', 'legacy_turn_quarantined'}:
            raise
        try:
            recovery.incident(scope,trigger,'conversation_failure',exc.code if isinstance(exc, ItineraryError) else type(exc).__name__)
        except PermissionError:
            raise
        except Exception:
            pass  # Incident persistence must not swallow an already-claimed reply.
        result=failure_reply(store, discovery, scope, trigger, message, stale_choice=isinstance(exc,ItineraryError) and exc.code in {'stale_discovery_action','stale_quote_action','stale_quote_catalog'})
    if not result.get('generation_failed'):
        try:
            recovery.schedule(scope,trigger,result)
        except Exception:
            from shared import bm_logger
            bm_logger.log('isluno_reminder_schedule_failed',code='schedule_store_unavailable')
    return result


def _handle_message(message, *, store=None, discovery=None, understand=None):
    store = store or ConversationStore()
    discovery = discovery or DiscoveryStore(store.itinerary.db_path, store.itinerary.catalog_path, clock=store.itinerary.clock)
    scope = verified_scope(account_id=message.get('_zernio_account_id', ''), conversation_id=message.get('from', ''), customer_ref=message.get('_zernio_sender_id', ''))
    trigger = message.get('message_id') or message.get('_ali_action_id')
    timestamp = message.get('_zernio_sent_at', '')
    base_decision = None
    native_details = None
    source_snapshot = None
    token = str(message.get('_zernio_interactive_id', ''))
    if token.startswith(('ip_', 'ie_')):
        from agents.social.isluno_payments import PaymentStore, envelope as paid_envelope
        payments = PaymentStore(store)
        action = payments.complete if token.startswith('ip_') else payments.consent_email
        try:
            return paid_envelope(action(scope, trigger, timestamp, token, message.get('_zernio_interactive_type')))
        except ItineraryError as exc:
            if exc.code != 'expired_payment_action':
                raise
            from agents.social.isluno_quotes import envelope as quote_envelope
            return quote_envelope(payments.refresh_pending(scope, trigger, timestamp, token))
    if str(message.get('_zernio_interactive_id', '')).startswith('iq_'):
        from agents.social.isluno_quotes import QuoteStore, envelope as quote_envelope
        return quote_envelope(QuoteStore(store).act(scope, trigger, timestamp, message['_zernio_interactive_id'], message.get('_zernio_interactive_type')))
    if message.get('_zernio_interactive_id'):
        action_plan = discovery.plan(scope, trigger, timestamp, action_token=message['_zernio_interactive_id'], interactive_type=message.get('_zernio_interactive_type'))
        native_details=action_plan.get('detail_translation_required')
        if not action_plan['selected_intent'] and not action_plan['requires_human'] and not native_details:
            return envelope(action_plan)
        with discovery.db() as db, db:
            db.execute("UPDATE isluno_discovery_plans SET status='consumed_action' WHERE id=?", (action_plan['id'],))
        source_snapshot = discovery.source_snapshot(scope, action_plan)
        base_decision = None if native_details else {'language': action_plan['language'], 'product_ids': action_plan['product_ids'], 'fact_keys': action_plan['fact_keys'],
                         'intent': 'human' if action_plan['requires_human'] else 'add', 'question': '',
                         'translations': action_plan.get('translations') or {},
                         'booking': {'action': 'human' if action_plan['requires_human'] else 'add', 'updates': [], 'guest': {}, 'document_language': None}}
    saved, decision, outcome = store.reserve(scope, trigger, source_snapshot)
    snapshot = store.source_snapshot(scope, saved)
    if outcome is None:
        if decision is None:
            discovery.allow_turn(scope)
            if base_decision is not None:
                decision = base_decision
            else:
                active = store.itinerary.get(scope, saved['active_itinerary_id']) if saved['active_itinerary_id'] else None
                model_history = [{**entry, 'delivery_status':'legacy_unverified'} if entry.get('role') == 'assistant' and 'delivery_status' not in entry else entry for entry in saved['history']]
                model_state = {**saved, 'history':model_history, 'itinerary': active, 'discovery': discovery.current_context(scope),
                               'current_time': store.itinerary.clock().isoformat(), 'timezone': 'America/Curacao',
                               'native_detail_request':{k:v for k,v in native_details.items() if k!='translations'} if native_details else None}
                try:
                    with understanding_claim(store,scope,trigger):
                        decision = (understand or understanding.understand)(scope, message.get('text', ''), model_state, snapshot)
                        understanding.validate(decision, discovery_understanding.context(snapshot))
                        if native_details:
                            request=native_details;booking=decision['booking']
                            check(decision['language']==request['language'] and decision['product_ids']==[request['product_id']] and booking['action']=='none' and not booking['updates'] and not booking['guest'] and booking['document_language'] is None,'native_detail_authority')
                            check(set(request['fact_keys'])<=set(decision['fact_keys']),'native_detail_facts_missing')
                            previous=request['translations'].get(request['product_id'],{})
                            decision['translations'][request['product_id']]={**previous,**decision['translations'].get(request['product_id'],{})}
                        store.record_decision(scope, trigger, decision)
                except Exception as exc:
                    from agents.social.isluno_recovery import RecoveryStore
                    try:
                        RecoveryStore(store).incident(scope,trigger,'understanding_failure',
                                                      exc.code if isinstance(exc, ItineraryError) else type(exc).__name__)
                    except PermissionError:
                        raise
                    except Exception:
                        pass  # Preserve the failure reply even when its incident cannot be saved.
                    from agents.social.isluno_recovery_copy import PROCESSING_FAILED
                    # This turn was claimed exactly once. Leave its decision and
                    # outcome unresolved; a duplicate must never call the model
                    # or resend this acknowledgement. A fresh guest turn may proceed.
                    return failure_reply(store, discovery, scope, trigger, message, understanding_failed=True)
            if base_decision is not None:
                store.record_decision(scope, trigger, decision)
        outcome = store.apply(scope, trigger, saved, decision, message.get('text', ''))
    if decision['booking']['action']=='stop_reminders':
        from agents.social.isluno_recovery import RecoveryStore
        RecoveryStore(store).opt_out(scope)
    prepared_fulfillment = None
    if decision['booking']['action'] in {'documents', 'email'}:
        from agents.social.isluno_payments import PaymentStore, envelope as paid_envelope
        payments = PaymentStore(store)
        try:
            if decision['booking']['action'] == 'documents':
                prepared_fulfillment = payments.resume(scope, timestamp)
            else:
                prepared_fulfillment = payments.propose_email(scope, trigger, timestamp, decision['booking'].get('email_address', ''), replace=decision['booking'].get('email_address_correction', False))
        except ItineraryError as exc:
            if not decision.get('hospitality'):
                raise
            outcome = {**outcome, 'error':exc.code}
            from agents.social.isluno_recovery import RecoveryStore
            RecoveryStore(store).incident(scope,trigger,'fulfillment_failure',exc.code)
        if not decision['question'] and not decision.get('hospitality'):
            return paid_envelope(prepared_fulfillment)
    with store.db() as db:
        existing_quote_reply = db.execute('SELECT job_id FROM isluno_quote_requests WHERE scope_key=? AND trigger_id=?', (scope.key, trigger)).fetchone()
        old_quote = db.execute('SELECT s.status FROM isluno_quote_latest l JOIN isluno_quote_state s ON s.quote_id=l.quote_id WHERE l.scope_key=?', (scope.key,)).fetchone()
    ready = outcome['itinerary'] and outcome['itinerary']['status'] == 'draft' and outcome['itinerary']['items'] and not outcome['session']['pending']
    prepared_quote = None
    if existing_quote_reply or ready and outcome['status'] != 'review' and (decision['booking']['action'] in {'summary', 'approve'} or (decision['booking']['action'] != 'none' or decision['booking']['document_language'] is not None) and old_quote and old_quote['status'] == 'superseded'):
        from agents.social.isluno_quotes import QuoteStore, envelope as quote_envelope
        prepared_quote = QuoteStore(store).prepare(scope, trigger, timestamp, expected_session_revision=outcome['session']['revision'])
        if not decision['question'] and not decision.get('hospitality') and decision['booking']['action']!='stop_reminders':
            return quote_envelope(prepared_quote)
    base = {key: decision[key] for key in discovery_understanding.TOOL['input_schema']['required']}
    booking = decision['booking']
    response_text = None
    if booking['action'] != 'none':
        if booking['action']=='stop_reminders':
            from agents.social.isluno_recovery_copy import STOP
            response_text=STOP[decision['language']]
            if booking['guest'] or booking['document_language'] or booking['updates']:
                response_text+='\n\n'+render(outcome,snapshot)
        else:
            response_text = '' if prepared_fulfillment else render(outcome, snapshot)
        # A simultaneous supported question is answered from its selected,
        # versioned fact translations before the canonical operation result.
        facts = []
        products = {p['id']: p for p in snapshot['catalog']['products']}
        for product_id in decision['product_ids']:
            source = discovery_understanding.facts(products[product_id]) if decision['language'] == 'en' else decision['translations'].get(product_id, {})
            facts += [source[k] for k in decision['fact_keys'] if k in source]
        if facts and decision['question']:
            fact_text = '\n\n'.join(facts)[:max(0, 4096 - len(response_text) - 2)]
            response_text = fact_text + '\n\n' + response_text
    next_question = ''
    if decision.get('hospitality'):
        response_text, next_question, presentation_branch = hospitality.present(decision['hospitality'], decision, outcome, snapshot, now=store.itinerary.clock())
    if prepared_fulfillment and not response_text:
        response_text = None
    if prepared_quote and not decision.get('hospitality'):
        from agents.social.isluno_quote_documents import REVIEW_READY
        response_text = (response_text or '') + '\n\n' + REVIEW_READY[decision['language']]
    # Action resolution used this trigger already; use a distinct durable reply
    # ID for its application result so selection metadata cannot mask intake.
    reply_trigger = 'conversation-' + opaque(scope, trigger, 'reply')
    selected_products = {i['product']['id'] for i in (outcome['itinerary'] or {}).get('items', [])} | {p.get('product_id') for p in outcome['session']['pending'].values()}
    offer_selection = booking['action'] == 'none' and not selected_products.intersection(decision['product_ids'])
    delivered = understanding.delivery_context(saved)['confirmed_message_ids']
    welcome_image = (not delivered and saved.get('stage') == 'welcome' and booking['action'] == 'none'
                     and not decision['product_ids'] and decision.get('hospitality', {}).get('stage') in {'welcome', 'exploration'})
    plan = discovery.plan(scope, reply_trigger, timestamp, base, translations=decision['translations'], response_text=response_text, catalog_snapshot=snapshot,
                          hospitality=decision.get('hospitality'), next_question=next_question, offer_selection=offer_selection, source_trigger_id=trigger,
                          card_texts=hospitality.render_cards(decision['hospitality'],decision,snapshot) if decision.get('hospitality') else None,detail_request=native_details,
                          welcome_image=welcome_image)
    if prepared_quote and decision.get('hospitality'):
        from agents.social.isluno_quotes import QuoteStore, envelope as quote_envelope
        return quote_envelope(QuoteStore(store).compose_answer(scope, trigger, timestamp, prepared_quote, plan))
    if prepared_fulfillment:
        return paid_envelope(payments.compose(scope, trigger, timestamp, prepared_fulfillment, plan))
    return envelope(plan)


def envelope(plan):
    return {'text': plan['body'].get('message') or plan['body']['interactive']['body']['text'],
            'media': {'url': plan['id'], 'type': 'isluno_discovery', 'caption': 'Isluno itinerary'}}


def failure_reply(store, discovery, scope, trigger, message, *, understanding_failed=False, stale_choice=False):
    """Best-effort history/plan persistence after verified scope and inbound claim.

    This never bypasses the webhook's lease/account/automation guards or retries an
    action. Storage failure cannot manufacture history, but must not hide the text.
    """
    from agents.social.isluno_recovery_copy import PROCESSING_FAILED, RESPONSE_FAILED, STALE_CHOICE
    require_scope(scope)
    check(isinstance(trigger, str) and bool(trigger), 'missing_verified_message_id')
    copy = STALE_CHOICE if stale_choice else PROCESSING_FAILED if understanding_failed else RESPONSE_FAILED
    text = copy['en']
    try:
        discovery = discovery or DiscoveryStore(store.itinerary.db_path, store.itinerary.catalog_path, clock=store.itinerary.clock)
        with store.db() as db, db:
            row = db.execute('SELECT payload FROM isluno_booking_sessions WHERE scope_key=?', (scope.key,)).fetchone()
            session = json.loads(row[0]) if row else default_session()
            text = copy.get(session['chat_language'], copy['en'])
            if not any(h.get('trigger_id') == trigger for h in session['history']):
                session['history'] = (session['history'] + [{'role':'user','content':message.get('text',''), 'trigger_id':trigger}])[-100:]
            db.execute('INSERT INTO isluno_booking_sessions VALUES(?,?) ON CONFLICT(scope_key) DO UPDATE SET payload=excluded.payload', (scope.key, encoded(session)))
        base = {'language':session['chat_language'], 'product_ids':[], 'fact_keys':[], 'intent':'discover', 'question':''}
        plan = discovery.plan(scope, 'failure-' + opaque(scope,trigger,'ack'), message.get('_zernio_sent_at',''), base, response_text=text, source_trigger_id=trigger)
        return {**envelope(plan), 'generation_failed':True}
    except PermissionError:
        raise
    except Exception:
        try:
            plan = discovery.notice(scope, 'failure-' + opaque(scope,trigger,'ack'),
                                    message.get('_zernio_sent_at',''), text, source_trigger_id=trigger)
            return {**envelope(plan), 'generation_failed':True}
        except PermissionError:
            raise
        except Exception:
            # Storage is required for a one-attempt claim. The webhook records
            # its durable delivery-failure notification; never bypass its ledger.
            from shared import bm_logger
            bm_logger.log('isluno_failure_notice_unavailable',code='notice_store_unavailable')
            raise ItineraryError('notice_store_unavailable')
