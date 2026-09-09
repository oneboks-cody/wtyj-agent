"""Durable browsing plans and opaque, customer-bound WhatsApp reply actions."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import secrets
import sqlite3
import textwrap
from pathlib import Path

from shared import config_loader
from shared.isluno_catalog import CatalogStore, validate_snapshot
from shared.isluno_config import JourneyScope, require_scope, active_profile
from shared.isluno_media import MediaLibrary, MediaUnavailable
from shared.isluno_pricing import check, ItineraryError
from agents.social.isluno_context import ContextStore
from agents.social import isluno_understanding

LABELS = {
 'en': ['Choose', 'Add trip', 'More photos', 'More info', 'Photos unavailable', 'Which trip interests you?', 'Demo: no real reservation or payment.'],
 'nl': ['Kiezen', 'Reis toevoegen', 'Meer foto’s', 'Meer informatie', 'Foto’s niet beschikbaar', 'Welke reis interesseert je?', 'Demo: geen echte reservering of betaling.'],
 'de': ['Auswählen', 'Reise hinzufügen', 'Mehr Fotos', 'Mehr Infos', 'Fotos nicht verfügbar', 'Welche Reise interessiert dich?', 'Demo: keine echte Buchung oder Zahlung.'],
 'es': ['Elegir', 'Añadir viaje', 'Más fotos', 'Más información', 'Fotos no disponibles', '¿Qué viaje te interesa?', 'Demo: sin reserva ni pago real.'],
 'pt': ['Escolher', 'Adicionar viagem', 'Mais fotos', 'Mais informações', 'Fotos indisponíveis', 'Qual viagem te interessa?', 'Demo: sem reserva ou pagamento real.'],
 'pap': ['Skohe', 'Agregá biahe', 'Mas potrèt', 'Mas informashon', 'Potrèt no disponibel', 'Kua biahe ta interesá bo?', 'Demo: sin reservashon ni pago real.'],
}


CLARIFICATIONS = {
 'en': ["I don’t have confirmed information to answer that question. Would you like operator help?", "I couldn’t match a trip. Could you describe the activity or trip you mean?", "Your request for operator help is recorded. No booking or payment has been made.", "Ask the team"],
 'nl': ["Ik heb geen bevestigde informatie om die vraag te beantwoorden. Wil je hulp van het team?", "Ik kon geen passende reis vinden. Kun je de activiteit of reis beschrijven?", "Je verzoek om hulp van het team is vastgelegd. Er is niets geboekt of betaald.", "Vraag het team"],
 'de': ["Mir fehlen bestätigte Informationen zu dieser Frage. Möchtest du Hilfe vom Team?", "Ich konnte keine passende Reise finden. Welche Aktivität oder Reise meinst du?", "Deine Anfrage an das Team ist erfasst. Es wurde nichts gebucht oder bezahlt.", "Team fragen"],
 'es': ["No tengo información confirmada para responder. ¿Quieres ayuda del equipo?", "No encontré un viaje que coincida. ¿Puedes describir la actividad o el viaje?", "Tu solicitud de ayuda está registrada. No se ha realizado ninguna reserva ni pago.", "Preguntar al equipo"],
 'pt': ["Não tenho informação confirmada para responder. Gostaria de ajuda da equipa?", "Não encontrei uma viagem correspondente. Pode descrever a atividade ou viagem?", "O pedido de ajuda está registado. Não foi feita nenhuma reserva ou pagamento.", "Perguntar à equipa"],
 'pap': ["Mi no tin informashon konfirmá pa kontestá e pregunta. Bo ke yudansa di e ekipo?", "Mi no a haña un biahe ku ta kuadra. Bo por deskribí e aktividat òf biahe?", "Bo petishon pa yudansa ta registrá. No a hasi reservashon ni pago.", "Puntra e ekipo"],
}


def dump(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def new_id():
    return secrets.token_hex(16)


class DiscoveryStore:
    def __init__(self, db_path=None, catalog_path=None, media=None, clock=None):
        self.db_path = Path(db_path) if db_path else Path(__file__).resolve().parents[2] / 'data/state_registry.db'
        self.catalog_path = Path(catalog_path) if catalog_path else Path(config_loader._CONFIG_PATH).with_name('isluno_catalog.json')
        self.media = media or MediaLibrary(self.catalog_path)
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    @contextmanager
    def db(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(str(self.db_path), timeout=5)
        db.row_factory = sqlite3.Row
        try:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS isluno_catalog_snapshots (revision TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TRIGGER IF NOT EXISTS isluno_catalog_snapshot_no_update BEFORE UPDATE ON isluno_catalog_snapshots
                    BEGIN SELECT RAISE(ABORT, 'immutable catalog snapshot'); END;
                CREATE TRIGGER IF NOT EXISTS isluno_catalog_snapshot_no_delete BEFORE DELETE ON isluno_catalog_snapshots
                    BEGIN SELECT RAISE(ABORT, 'immutable catalog snapshot'); END;
                CREATE TABLE IF NOT EXISTS isluno_discovery_plans (
                    id TEXT PRIMARY KEY, scope_key TEXT NOT NULL, trigger_id TEXT NOT NULL,
                    payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued', provider_id TEXT,
                    UNIQUE(scope_key, trigger_id));
                CREATE TABLE IF NOT EXISTS isluno_discovery_actions (
                    token TEXT PRIMARY KEY, plan_id TEXT NOT NULL, action_json TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS isluno_discovery_latest (scope_key TEXT PRIMARY KEY, plan_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS isluno_discovery_pacing (recipient TEXT PRIMARY KEY, last_send REAL NOT NULL);
            ''')
            yield db
        finally:
            db.close()

    def existing(self, scope, trigger_id):
        require_scope(scope)
        with self.db() as db:
            row = db.execute('SELECT payload FROM isluno_discovery_plans WHERE scope_key=? AND trigger_id=?', (scope.key, trigger_id)).fetchone()
            return json.loads(row[0]) if row else None

    def plan(self, scope, trigger_id, sent_at, decision=None, *, action_token=None, interactive_type=None, translations=None, response_text=None, catalog_snapshot=None, hospitality=None, next_question=""):
        require_scope(scope)
        check(isinstance(trigger_id, str) and 0 < len(trigger_id) <= 512, 'missing_verified_message_id')
        try:
            inbound_time = datetime.fromisoformat(sent_at.replace('Z', '+00:00'))
        except (ValueError, AttributeError) as exc:
            raise ItineraryError('invalid_inbound_time') from exc
        check(inbound_time.tzinfo is not None and timedelta(0) <= self.clock() - inbound_time < timedelta(hours=24), 'invalid_inbound_time')
        with self.db() as db, db:
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('SELECT payload FROM isluno_discovery_plans WHERE scope_key=? AND trigger_id=?', (scope.key, trigger_id)).fetchone()
            if previous:
                return json.loads(previous[0])
            current_snapshot = CatalogStore(self.catalog_path).snapshot()
            snapshot = validate_snapshot(catalog_snapshot) if catalog_snapshot is not None and action_token is None else current_snapshot
            db.execute('INSERT OR IGNORE INTO isluno_catalog_snapshots VALUES(?,?)', (snapshot['revision'], dump(snapshot)))
            offset, info_offset, selected_intent, force_single = 0, 0, None, False
            if action_token is not None:
                check(interactive_type in {'button_reply', 'list_reply'}, 'unverified_discovery_action')
                row = db.execute('SELECT a.action_json,p.payload,l.plan_id FROM isluno_discovery_actions a JOIN isluno_discovery_plans p ON p.id=a.plan_id JOIN isluno_discovery_latest l ON l.scope_key=p.scope_key WHERE a.token=? AND p.scope_key=?',
                                 (action_token, scope.key)).fetchone()
                check(row is not None, 'invalid_discovery_action')
                old, action = json.loads(row['payload']), json.loads(row['action_json'])
                translations = old.get('translations', {})
                check(row['plan_id'] == old['id'] and old['catalog_revision'] == snapshot['revision']
                      and datetime.fromisoformat(old['expires_at']) > self.clock(), 'stale_discovery_action')
                selected_product = action['product_id']
                selected_keys = old.get('product_fact_keys', {}).get(selected_product, [])
                if action['kind'] == 'choose':
                    selected_keys = selected_keys or ['summary']
                decision = {'product_ids': [selected_product] if selected_product else [], 'language': old['language'],
                            'intent': 'human' if action['kind'] == 'human' else 'details',
                            'fact_keys': selected_keys, 'question': ''}
                offset = action.get('offset', 0)
                force_single = action.get('fallback') is True
                info_offset = action.get('info_offset', 0)
                if action['kind'] == 'add':
                    selected_intent = {'kind': 'add_trip', 'product_id': action['product_id'], 'catalog_revision': snapshot['revision']}
            decision = isluno_understanding.validate(decision, isluno_understanding.context(snapshot))
            if translations is not None:
                from agents.social.isluno_conversation_understanding import validate_translations
                translations = {k: v for k, v in translations.items() if k in decision['product_ids']}
                validate_translations(translations, decision, isluno_understanding.context(snapshot))
            if decision['intent'] == 'add' and len(decision['product_ids']) == 1:
                selected_intent = {'kind': 'add_trip', 'product_id': decision['product_ids'][0], 'catalog_revision': snapshot['revision']}
            locale = decision['language']
            labels = LABELS[locale]
            plan_id = new_id()
            actions = {}
            def button(kind, product_id, title, **extra):
                token = 'isl_' + secrets.token_hex(8)  # 20 characters, opaque and scoped in storage.
                actions[token] = {'kind': kind, 'product_id': product_id, **extra}
                return {'type': 'postback', 'title': title, 'payload': token}
            products = {p['id']: p for p in snapshot['catalog']['products']}
            chosen = [products[key] for key in decision['product_ids']]
            fact_association = {p['id']: [k for k in decision['fact_keys'] if k in isluno_understanding.facts(p)] for p in chosen}
            answer_status = ('human_requested' if decision['intent'] == 'human' else
                             'no_match' if not chosen else
                             'unavailable' if not decision['fact_keys'] and decision['question'].strip() else 'source_backed')
            if answer_status != 'source_backed':
                clarification = CLARIFICATIONS[locale]
                text = clarification[{'unavailable': 0, 'no_match': 1, 'human_requested': 2}[answer_status]]
                clarification_buttons = ([button('human', chosen[0]['id'] if len(chosen) == 1 else None, clarification[3])]
                                         if answer_status == 'unavailable' else [])
                body = {'accountId': scope.account_id, 'message': text, 'buttons': clarification_buttons}
                asset_ids, missing, fallback = [], [], None
            elif not chosen:
                text = CLARIFICATIONS[locale][1]
                body = {'accountId': scope.account_id, 'message': text, 'buttons': []}
                asset_ids, missing, fallback = [], [], None
            elif len(chosen) > 1:
                text = '\n'.join(p['name'] for p in chosen)[:900] + '\n\n' + labels[6]
                body = {'accountId': scope.account_id, 'message': text,
                        'buttons': [button('choose', p['id'], str(i + 1) + '. ' + labels[0]) for i, p in enumerate(chosen)]}
                asset_ids, missing, fallback = [], [], None
            else:
                product = chosen[0]
                known_facts = isluno_understanding.facts(product)
                if translations is not None and locale != 'en':
                    known_facts = translations.get(product['id'], {})
                keys = decision['fact_keys'] or ['summary']
                fact_text = '\n\n'.join(known_facts[k] for k in keys if k in known_facts)
                # Full source-backed information stays accessible through native More info.
                chunks = textwrap.wrap(fact_text, width=650, replace_whitespace=False, drop_whitespace=False) or ['']
                check(0 <= info_offset < len(chunks), 'invalid_info_page')
                text = product['name'][:150] + '\n\n' + chunks[info_offset] + '\n\n' + labels[6]
                buttons = [button('add', product['id'], labels[1])]
                gallery = product['gallery']
                check(type(offset) is int and 0 <= offset <= len(gallery), 'invalid_gallery_page')
                native = not force_single and (config_loader.get_raw().get('isluno') or {}).get('native_carousels') is True
                excluded_assets = set()
                if hospitality and hospitality['photo'] != 'repeat':
                    for sent in db.execute("SELECT payload FROM isluno_discovery_plans WHERE scope_key=? AND status IN ('accepted','claimed','ambiguous')", (scope.key,)):
                        prior = json.loads(sent[0])
                        if prior.get('product_ids') == [product['id']]:
                            excluded_assets.update(prior.get('asset_ids', []))
                positions_page = [(i, asset) for i, asset in enumerate(gallery) if i >= offset and asset['id'] not in excluded_assets]
                positions_page = positions_page[:10 if native else 1]
                if hospitality and hospitality['photo'] == 'none':
                    positions_page = []
                page = [asset for _, asset in positions_page]
                next_offset = positions_page[-1][0] + 1 if positions_page else len(gallery)
                if next_offset < len(gallery):
                    buttons.append(button('photos', product['id'], labels[2], offset=next_offset, fallback=force_single))
                if info_offset + 1 < len(chunks):
                    buttons.append(button('info', product['id'], labels[3], offset=offset, info_offset=info_offset + 1))
                asset_ids, missing, urls, positions = [], [], [], []
                for position, asset in positions_page:
                    try:
                        url = self.media.url(asset)
                    except (MediaUnavailable, OSError, ValueError):
                        missing.append(asset['id'])
                    else:
                        asset_ids.append(asset['id'])
                        urls.append(url)
                        positions.append(position)
                if missing:
                    text += '\n' + labels[4] + ': ' + str(len(missing))
                body = {'accountId': scope.account_id, 'message': text, 'buttons': buttons}
                fallback = None
                if len(urls) == 1:
                    body.update(attachmentUrl=urls[0], attachmentType='image')
                elif len(urls) >= 2:
                    cards = [{'card_index': i, 'type': 'button', 'header': {'type': 'image', 'image': {'link': url}},
                              'body': {'text': product['name'][:120] + ' · ' + str(positions[i] + 1)},
                              'action': {'buttons': [{'type': 'quick_reply', 'quick_reply': {'id': b['payload'], 'title': b['title']}} for b in buttons]}}
                             for i, url in enumerate(urls)]
                    body = {'accountId': scope.account_id, 'interactive': {'type': 'carousel', 'body': {'text': text[:1024]}, 'action': {'cards': cards}}}
            if response_text is not None:
                check(isinstance(response_text, str) and 0 < len(response_text) <= 4096, 'invalid_conversation_reply')
                if hospitality:
                    if 'interactive' in body:
                        check(len(response_text) <= 1024, 'carousel_text_too_long')
                        body['interactive']['body']['text'] = response_text
                    else:
                        body['message'] = response_text
                else:
                    help_buttons = body.get('buttons', []) if answer_status == 'unavailable' else []
                    if answer_status == 'unavailable':
                        response_text += '\n\n' + CLARIFICATIONS[locale][0]
                    body = {'accountId': scope.account_id, 'message': response_text, 'buttons': help_buttons}
                    asset_ids, missing, fallback = [], [], None
            superseded = snapshot['revision'] != current_snapshot['revision']
            if superseded:
                notice = {'en':'Trip information has changed. Please ask for the latest details before confirming.',
                          'nl':'De reisinformatie is gewijzigd. Vraag om de nieuwste gegevens voordat je bevestigt.',
                          'de':'Die Reiseinformationen haben sich geändert. Bitte frage vor der Bestätigung nach aktuellen Angaben.',
                          'es':'La información del viaje ha cambiado. Pide los datos más recientes antes de confirmar.',
                          'pt':'As informações da viagem mudaram. Peça os dados mais recentes antes de confirmar.',
                          'pap':'Informashon di biahe a kambia. Puntra pa e datonan mas resien promé ku konfirmá.'}[locale]
                content = body.get('message') or body['interactive']['body']['text']
                body = {'accountId': scope.account_id, 'message': content[:3500] + '\n\n' + notice, 'buttons': []}
                asset_ids, missing, fallback = [], [], None
            payload = {'id': plan_id, 'scope': scope.__dict__, 'trigger_id': trigger_id, 'trigger_sent_at': sent_at,
                       'catalog_revision': snapshot['revision'], 'catalog_version': snapshot['catalog']['version'], 'catalog_superseded': superseded,
                       'product_ids': decision['product_ids'], 'language': locale, 'fact_keys': decision['fact_keys'],
                       'product_fact_keys': fact_association, 'answer_status': answer_status, 'translations': translations,
                       'body': body, 'next_question': next_question, 'button_meanings': actions, 'fallback': fallback, 'asset_ids': asset_ids, 'missing_asset_ids': missing,
                       'selected_intent': selected_intent, 'requires_human': decision['intent'] == 'human',
                       'created_at': self.clock().isoformat(), 'expires_at': (self.clock() + timedelta(hours=24)).isoformat()}
            db.execute('INSERT INTO isluno_discovery_plans(id,scope_key,trigger_id,payload) VALUES(?,?,?,?)', (plan_id, scope.key, trigger_id, dump(payload)))
            for token, action in actions.items():
                db.execute('INSERT INTO isluno_discovery_actions VALUES(?,?,?)', (token, plan_id, dump(action)))
            db.execute('INSERT INTO isluno_discovery_latest VALUES(?,?) ON CONFLICT(scope_key) DO UPDATE SET plan_id=excluded.plan_id', (scope.key, plan_id))
            return payload

    def source_snapshot(self, scope, plan):
        require_scope(scope)
        check(plan['scope'] == scope.__dict__, 'discovery_source_scope_mismatch')
        with self.db() as db:
            row = db.execute('SELECT payload FROM isluno_catalog_snapshots WHERE revision=?', (plan['catalog_revision'],)).fetchone()
        check(row is not None, 'missing_durable_catalog_snapshot')
        return validate_snapshot(json.loads(row[0]))

    def current_context(self, scope):
        require_scope(scope)
        with self.db() as db:
            row = db.execute('SELECT p.payload FROM isluno_discovery_latest l JOIN isluno_discovery_plans p ON p.id=l.plan_id WHERE l.scope_key=? AND p.scope_key=?', (scope.key, scope.key)).fetchone()
        if row is None:
            return None
        plan = json.loads(row[0])
        return {key: plan[key] for key in ('product_ids', 'catalog_revision', 'language', 'selected_intent')}

    def allow_turn(self, scope):
        require_scope(scope)
        with self.db() as db:
            rows = db.execute("SELECT payload FROM isluno_discovery_plans WHERE scope_key=? AND status!='consumed_action' ORDER BY rowid DESC LIMIT 50", (scope.key,)).fetchall()
        recent = sum(datetime.fromisoformat(json.loads(row[0])['created_at']) > self.clock() - timedelta(hours=1) for row in rows)
        check(recent < 50, 'discovery_rate_limited')

    def delivery_plan(self, plan_id, account_id, conversation_id):
        with self.db() as db:
            row = db.execute('SELECT payload,status FROM isluno_discovery_plans WHERE id=?', (plan_id,)).fetchone()
        check(row is not None, 'discovery_plan_missing')
        plan = json.loads(row['payload'])
        scope = JourneyScope(**plan['scope'])
        require_scope(scope)
        check(scope.account_id == account_id and scope.conversation_id == conversation_id, 'discovery_delivery_scope_mismatch')
        with self.db() as db:
            latest = db.execute('SELECT plan_id FROM isluno_discovery_latest WHERE scope_key=?', (scope.key,)).fetchone()
        check(latest is not None and latest[0] == plan_id and datetime.fromisoformat(plan['expires_at']) > self.clock(), 'stale_discovery_delivery')
        return plan, row['status']


def handle_message(message, *, store=None, understand=None):
    from shared.isluno_config import verified_scope
    store = store or DiscoveryStore()
    scope = verified_scope(account_id=message.get('_zernio_account_id', ''), conversation_id=message.get('from', ''),
                           customer_ref=message.get('_zernio_sender_id') or '')
    trigger = message.get('message_id') or message.get('id') or message.get('_ali_action_id')
    existing = store.existing(scope, trigger)
    if existing:
        plan = existing
    else:
        store.allow_turn(scope)
        token = message.get('_zernio_interactive_id')
        if token:
            plan = store.plan(scope, trigger, message.get('_zernio_sent_at', ''), action_token=token, interactive_type=message.get('_zernio_interactive_type'))
        else:
            check(bool(message.get('text', '').strip()), 'empty_discovery_message')
            context_store = ContextStore(store.db_path)
            saved = context_store.open(scope)
            saved['discovery'] = store.current_context(scope)
            decision = (understand or isluno_understanding.understand)(scope, message.get('text', ''), saved, CatalogStore(store.catalog_path).snapshot())
            plan = store.plan(scope, trigger, message.get('_zernio_sent_at', ''), decision)
            state = saved['state']
            state['chat_language'] = decision['language']
            state['history'] = (state['history'] + [{'role': 'user', 'content': message.get('text', '')}, {'role': 'assistant', 'content': plan['body'].get('message') or plan['body']['interactive']['body']['text']}])[-100:]
            context_store.save(scope, expected_revision=saved['revision'], state=state)
    return {'text': plan['body'].get('message') or plan['body']['interactive']['body']['text'],
            'media': {'url': plan['id'], 'type': 'isluno_discovery', 'caption': 'Isluno trip information'},
            'isluno_selected_intent': plan['selected_intent'], 'isluno_requires_human': plan['requires_human']}
