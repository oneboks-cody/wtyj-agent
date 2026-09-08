"""Deterministic model fixtures through the real inbound application handler."""
import copy
from datetime import datetime, timezone
import json
from unittest.mock import patch
import unittest

import test_journey_config as fixtures
from test_catalog import synthetic_catalog
from test_discovery import FakeMedia
from agents.social.channels.whatsapp_zernio import WhatsAppZernioChannel
from agents.social.isluno_itinerary import ItineraryStore
from agents.social.isluno_discovery import DiscoveryStore
from agents.social.isluno_conversation import ConversationStore, COPY
from shared.isluno_config import JourneyScope

NOW = datetime(2026, 9, 8, 18, tzinfo=timezone.utc)


def response(action='none', updates=None, guest=None, language='en', document_language=None, question='', fact_keys=None, products=None):
    product_ids = ['fixture-cruise'] if products is None else products
    translations = {} if language == 'en' else {p: {'summary': 'Translated source summary in ' + language} for p in product_ids}
    return {'language': language, 'product_ids': product_ids, 'fact_keys': ['summary'] if fact_keys is None else fact_keys,
            'intent': 'human' if action == 'human' else 'details', 'question': question,
            'translations': translations,
            'booking': {'action': action, 'updates': updates or [], 'guest': guest or {}, 'document_language': document_language}}


class ConversationTests(unittest.TestCase):
    write_config = fixtures.JourneyConfigTests.write_config
    write_profile = fixtures.JourneyConfigTests.write_profile
    scope = fixtures.JourneyConfigTests.scope

    def setUp(self):
        fixtures.JourneyConfigTests.setUp(self)
        self.catalog_path = self.directory / 'isluno_catalog.json'
        self.catalog_path.write_text(json.dumps(synthetic_catalog()))
        self.itinerary = ItineraryStore(self.directory / 'state.db', self.catalog_path, clock=lambda: NOW)
        self.store = ConversationStore(self.itinerary)
        self.discovery = DiscoveryStore(self.itinerary.db_path, self.catalog_path, media=FakeMedia(), clock=lambda: NOW)
        self.serial = 0

    def turn(self, decision=None, text='Synthetic guest turn', *, token=None, trigger=None):
        from agents.social import social_agent, isluno_conversation
        self.serial += 1
        message = WhatsAppZernioChannel.from_zernio({'conversation_id': self.scope().conversation_id,
            'account_id': self.scope().account_id, 'sender_id': self.scope().customer_ref,
            'message_id': trigger or 'turn-' + str(self.serial), 'channel': 'whatsapp', 'text': text,
            'sent_at': NOW.isoformat(), 'interactive_id': token or '', 'interactive_type': 'button_reply' if token else ''})
        with patch.object(social_agent.state_registry, 'match_ignored_contact', return_value=None), \
             patch.object(social_agent.auto_block, 'evaluate_inbound', return_value={}), \
             patch.object(social_agent.state_registry, 'wa_get_booking_state', side_effect=AssertionError('Legacy state accessed')), \
             patch.object(isluno_conversation, 'ConversationStore', return_value=self.store), \
             patch.object(isluno_conversation, 'DiscoveryStore', return_value=self.discovery), \
             patch.object(isluno_conversation.understanding, 'understand', return_value=copy.deepcopy(decision)) as model:
            result = social_agent.handle_incoming_whatsapp_message(message, include_media=True)
        return result, model.call_count

    def active(self):
        saved = self.store.session(self.scope())
        return self.itinerary.get(self.scope(), saved['active_itinerary_id'])

    def initial(self):
        result, calls = self.turn(response('add', [{'product_id': 'fixture-cruise', 'date': '2026-10-15'}], {'name': 'Calvin', 'ages': [35, 34, 8]}))
        self.assertEqual(calls, 1)
        return result

    def test_one_trip_second_trip_targeted_date_change_and_removal(self):
        self.initial()
        first = self.active()['items'][0]
        self.turn(response('add', [{'product_id': 'fixture-cruise', 'date': '2026-10-16'}]))
        multi = self.active()
        self.assertEqual(len(multi['items']), 2)
        self.assertEqual(multi['totals']['total_minor'], 50000)
        second = multi['items'][1]
        self.turn(response('update', [{'item_id': first['id'], 'date': '2026-10-17'}]))
        changed = self.active()
        self.assertEqual(changed['items'][0]['selection']['date'], '2026-10-17')
        self.assertEqual(changed['items'][1], second)
        self.turn(response('remove', [{'item_id': second['id']}]))
        self.assertEqual(len(self.active()['items']), 1)
        self.assertEqual(self.store.session(self.scope())['guest'], {'name': 'Calvin', 'ages': [35, 34, 8]})

    def test_missing_details_reuse_guest_data_without_repeated_intake(self):
        result, _ = self.turn(response('add', [{'product_id': 'fixture-cruise'}], {'name': 'Calvin', 'ages': [35, 8]}))
        self.assertIn('trip date', result['text'])
        self.assertNotIn('Please provide: guest name', result['text'])
        self.assertEqual(self.active()['items'], [])
        result, _ = self.turn(response('update', [{'date': '2026-10-15'}]))
        self.assertEqual(self.active()['items'][0]['selection']['guest_ages'], [35, 8])
        self.assertEqual(self.store.session(self.scope())['pending'], {})

    def test_question_and_approval_do_not_mutate_itinerary(self):
        self.initial()
        before = self.active()
        result, count = self.turn(response(question='Does it include lunch?', fact_keys=['inclusion_0']))
        self.assertIn('Synthetic lunch', result['text'])
        self.assertEqual(self.active(), before)
        result, _ = self.turn(response('approve'))
        self.assertIn('approval is not available', result['text'])
        self.assertEqual(self.active(), before)
        self.assertEqual(count, 1)

    def test_language_switches_preserve_state_and_separate_document_language(self):
        self.initial()
        before = self.active()
        for language in ['nl', 'de', 'es', 'pt', 'pap', 'en']:
            result, _ = self.turn(response(language=language, document_language='nl' if language == 'nl' else None))
            saved = self.store.session(self.scope())
            self.assertEqual(saved['chat_language'], language)
            self.assertEqual(saved['document_language'], 'nl')
            self.assertEqual(saved['guest']['name'], 'Calvin')
            self.assertEqual(self.active(), before)
            if language not in {'nl', 'en'}:
                self.assertIn('Translated source summary in ' + language, result['text'])
                self.assertNotIn('Synthetic test product', result['text'])

    def test_invalid_correction_retains_previous_revision_and_guest_data(self):
        self.initial()
        before = self.active()
        item_id = before['items'][0]['id']
        result, _ = self.turn(response('update', [{'item_id': item_id, 'date': '2026-02-30'}]))
        self.assertIn('correct', result['text'])
        self.assertEqual(self.active(), before)
        self.assertEqual(self.store.session(self.scope())['guest']['name'], 'Calvin')
        self.turn(response('update', [{'item_id': item_id, 'date': '2026-10-18'}]))
        self.assertEqual(self.active()['items'][0]['selection']['date'], '2026-10-18')

    def test_cancel_unpaid_and_route_paid_changes_to_operator_without_mutation(self):
        self.initial()
        result, _ = self.turn(response('cancel'))
        self.assertEqual(self.active()['status'], 'cancelled')
        self.assertIn('cancelled', result['text'])
        self.turn(response('new', [{'product_id': 'fixture-cruise', 'date': '2026-10-20'}]))
        # Synthetic future payment state: no provider/payment service is invoked.
        paid = self.active()
        paid['status'] = 'demo_paid'
        with self.itinerary._connection() as db, db:
            db.execute('UPDATE isluno_itineraries SET payload_json=? WHERE scope_key=? AND itinerary_id=?', (json.dumps(paid), self.scope().key, paid['id']))
        result, _ = self.turn(response('cancel'))
        self.assertEqual(self.active(), paid)
        self.assertIn('operator review', result['text'])
        self.assertEqual(self.store.reviews(self.scope())[0]['reason'], 'post_booking_change')
        self.assertEqual(self.store.reviews(self.scope())[0]['status'], 'pending')

    def test_native_add_and_human_buttons_are_consumed_by_application(self):
        result, _ = self.turn(response())
        plan, _ = self.discovery.delivery_plan(result['media']['url'], self.scope().account_id, self.scope().conversation_id)
        token = plan['body']['buttons'][0]['payload']
        result, calls = self.turn(token=token)
        self.assertEqual(calls, 0)
        self.assertIn('guest name', result['text'])
        self.assertTrue(self.store.session(self.scope())['pending'])
        result, _ = self.turn(response(fact_keys=[], question='Would you like operator help?'))
        plan, _ = self.discovery.delivery_plan(result['media']['url'], self.scope().account_id, self.scope().conversation_id)
        token = plan['body']['buttons'][0]['payload']
        result, calls = self.turn(token=token)
        self.assertEqual(calls, 0)
        self.assertIn('operator review', result['text'])
        self.assertEqual(len(self.store.reviews(self.scope())), 1)

    def test_duplicate_inbound_does_not_call_model_or_add_again(self):
        decision = response('add', [{'product_id': 'fixture-cruise', 'date': '2026-10-15'}], {'name': 'Calvin', 'ages': [35]})
        first, count = self.turn(decision, trigger='duplicate')
        repeated, repeated_count = self.turn(decision, trigger='duplicate')
        self.assertEqual(count, 1)
        self.assertEqual(repeated_count, 0)
        self.assertEqual(first, repeated)
        self.assertEqual(len(self.active()['items']), 1)

    def test_ambiguous_item_and_missing_product_are_correctable(self):
        result, _ = self.turn(response('add', products=[], fact_keys=[]))
        self.assertIn('describe the activity', result['text'])
        self.assertIsNone(self.store.session(self.scope())['active_itinerary_id'])
        self.initial()
        self.turn(response('add', [{'product_id': 'fixture-cruise', 'date': '2026-10-16'}]))
        before = self.active()
        result, _ = self.turn(response('update', [{'date': '2026-10-18'}]))
        self.assertIn('Which item', result['text'])
        self.assertEqual(self.active(), before)

    def test_multi_item_validation_rolls_back_all_item_writes(self):
        self.turn(response('add', [{'product_id':'fixture-cruise','date':'2026-10-15'},
                                   {'product_id':'fixture-cruise','date':'2026-02-30'}], {'name':'Calvin','ages':[35]}))
        self.assertEqual(self.active()['items'], [])
        pending = self.store.session(self.scope())['pending']
        bad_id = next(k for k,v in pending.items() if v['date'] == '2026-02-30')
        self.turn(response('update', [{'item_id': bad_id, 'date':'2026-10-16'}]))
        self.assertEqual(len(self.active()['items']), 2)
        self.assertEqual(self.active()['totals']['total_minor'], 20000)

    def test_option_correction_preserves_other_extras(self):
        catalog = synthetic_catalog()
        catalog['products'][0]['options'] = [
            {'id': key, 'name':key, 'basis':'per_booking', 'amount_minor':1000, 'max_quantity':1, 'required':False}
            for key in ['photo','lunch']]
        self.catalog_path.write_text(json.dumps(catalog))
        self.turn(response('add', [{'product_id':'fixture-cruise','date':'2026-10-15','options':{'photo':1,'lunch':1}}], {'name':'Calvin','ages':[35]}))
        item_id = self.active()['items'][0]['id']
        self.turn(response('update', [{'item_id':item_id,'options':{'photo':0}}]))
        self.assertEqual(self.active()['items'][0]['selection']['options'], {'photo':0,'lunch':1})
        self.assertEqual(self.active()['totals']['total_minor'], 11000)

    def test_recorded_outcome_recovers_without_second_model_call(self):
        decision = response('add', [{'product_id':'fixture-cruise','date':'2026-10-15'}], {'name':'Calvin','ages':[35]})
        with patch.object(self.discovery, 'plan', side_effect=RuntimeError('Synthetic reply persistence interruption')):
            with self.assertRaises(RuntimeError):
                self.turn(decision, trigger='crash-after-commit')
        result, calls = self.turn(decision, trigger='crash-after-commit')
        self.assertEqual(calls, 0)
        self.assertIn('saved', result['text'])
        self.assertEqual(len(self.active()['items']), 1)

    def test_operator_queue_is_deduplicated_and_available_to_authenticated_api(self):
        from fastapi import FastAPI, Header, HTTPException
        from fastapi.testclient import TestClient
        from dashboard.isluno_api import build_router
        self.turn(response('human'))
        self.turn(response('human'))
        self.assertEqual(len(self.store.reviews(self.scope())), 1)
        def auth(authorization: str = Header(default='')):
            if authorization != 'Bearer fixture': raise HTTPException(401)
        app = FastAPI()
        app.include_router(build_router(auth, conversation_store_factory=lambda:self.store))
        params = {'account_id':self.scope().account_id,'conversation_id':self.scope().conversation_id,'customer_ref':self.scope().customer_ref}
        with TestClient(app) as client:
            self.assertEqual(client.get('/isluno/operator-requests',params=params).status_code,401)
            result = client.get('/isluno/operator-requests',params=params,headers={'Authorization':'Bearer fixture'})
            self.assertEqual(result.status_code,200)
            self.assertEqual(len(result.json()['requests']),1)
            params['customer_ref'] = 'other-customer'
            self.assertEqual(client.get('/isluno/operator-requests',params=params,headers={'Authorization':'Bearer fixture'}).json()['requests'],[])

    def test_conversation_sdk_uses_single_existing_model_request(self):
        from types import SimpleNamespace
        from agents.marina import marina_agent
        from agents.social import isluno_conversation_understanding
        from shared.isluno_catalog import CatalogStore
        decision = response('add', [{'product_id':'fixture-cruise'}])
        sdk_response = SimpleNamespace(content=[SimpleNamespace(type='tool_use',input=decision)],usage=None)
        with patch.object(marina_agent.anthropic,'Anthropic') as sdk:
            sdk.return_value.messages.create.return_value = sdk_response
            result = isluno_conversation_understanding.understand(self.scope(),'Book a trip',self.store.session(self.scope()),CatalogStore(self.catalog_path).snapshot())
        self.assertEqual(result,decision)
        self.assertEqual(sdk.return_value.messages.create.call_count,1)
        call = sdk.return_value.messages.create.call_args.kwargs
        self.assertEqual(call['model'],'claude-sonnet-4-6')
        self.assertEqual(call['max_tokens'],2048)
        self.assertIn('booking',call['tools'][0]['input_schema']['properties'])

    def test_pickup_location_and_price_survive_targeted_date_change(self):
        catalog = synthetic_catalog()
        product = catalog['products'][0]
        product['options'] = [{'id':'transfer','name':'Transfer','basis':'per_booking','amount_minor':2500,'max_quantity':1,'required':False}]
        product['pickup'] = {'mode':'priced_option','meeting_point':'Fixture hotel','option_id':'transfer'}
        self.catalog_path.write_text(json.dumps(catalog))
        self.turn(response('add',[{'product_id':'fixture-cruise','date':'2026-10-15','pickup':True,'pickup_location':'Fixture Hotel'}], {'name':'Calvin','ages':[35,8]}))
        item = self.active()['items'][0]
        self.assertEqual(item['total_minor'],17500)
        self.assertEqual(self.store.session(self.scope())['item_details'][item['id']]['pickup_location'],'Fixture Hotel')
        result,_ = self.turn(response('update',[{'item_id':item['id'],'date':'2026-10-16'}]))
        self.assertNotIn('Please provide',result['text'])
        self.assertEqual(self.active()['items'][0]['total_minor'],17500)
        self.turn(response('update',[{'item_id':item['id'],'pickup':False}]))
        self.assertEqual(self.active()['items'][0]['total_minor'],15000)

    def test_sample_labels_and_customer_scope_remain_explicit(self):
        from dataclasses import replace
        catalog = synthetic_catalog()
        p = catalog['products'][0]
        p['demo_rules'] = {'authority':'user_approved_demo_sample','version':'sample-fixture','label':'DEMO SAMPLE',
                          'approval_ref':'Synthetic approval','real_booking_eligible':False,
                          'rules':{key:copy.deepcopy(p[key]) for key in ('guest_rules','price_rules','schedule','options','pickup','policies')}}
        self.catalog_path.write_text(json.dumps(catalog))
        result = self.initial()
        self.assertIn('Sample demo rules',result['text'])
        self.assertFalse(self.active()['items'][0]['real_booking_eligible'])
        self.assertEqual(self.store.session(replace(self.scope(),customer_ref='another-guest'))['guest'],{})
        self.assertEqual(self.store.reviews(replace(self.scope(),customer_ref='another-guest')),[])

    def test_missing_non_english_translation_is_rejected(self):
        from shared.isluno_pricing import ItineraryError
        from shared.isluno_catalog import CatalogStore
        from agents.social import isluno_conversation_understanding, isluno_understanding
        decision = response(language='nl')
        decision['translations'] = {}
        with self.assertRaises(ItineraryError):
            isluno_conversation_understanding.validate(decision,isluno_understanding.context(CatalogStore(self.catalog_path).snapshot()))

    def test_departure_choices_are_shown_inside_chat(self):
        catalog = synthetic_catalog()
        catalog['products'][0]['schedule']['slots'].append({'id':'afternoon','start':'14:00','duration_minutes':180})
        self.catalog_path.write_text(json.dumps(catalog))
        result,_ = self.turn(response('add',[{'product_id':'fixture-cruise','date':'2026-10-15'}],{'name':'Calvin','ages':[35]}))
        self.assertIn('09:00, 14:00',result['text'])
        self.turn(response('update',[{'slot_id':'afternoon'}]))
        self.assertEqual(self.active()['items'][0]['selection']['slot_id'],'afternoon')
