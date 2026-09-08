"""Native payload/action adapter tests; provider and model calls are mocked."""
import copy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
import test_journey_config as fixtures
from test_catalog import synthetic_catalog
from agents.social.isluno_discovery import DiscoveryStore, handle_message
from agents.social.isluno_delivery import send_plan, post_once
from agents.social import isluno_understanding
from agents.social.channels.whatsapp_zernio import WhatsAppZernioChannel
from shared.isluno_catalog import CatalogStore
from shared.isluno_media import MediaLibrary, MediaUnavailable, build_public_router
from shared.isluno_pricing import ItineraryError

NOW = datetime(2026, 9, 8, 18, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[3]
DECISION = {'language': 'en', 'product_ids': ['fixture-cruise'], 'fact_keys': ['summary'], 'intent': 'details', 'question': ''}


class FakeMedia:
    def __init__(self, missing=()):
        self.missing = missing
    def url(self, asset):
        if asset['id'] in self.missing:
            raise MediaUnavailable('fixture-missing')
        return 'https://example.invalid/' + asset['id'] + '.jpg'


def replies(body):
    if 'interactive' not in body:
        return body.get('buttons', [])
    return [{'title': b['quick_reply']['title'], 'payload': b['quick_reply']['id']}
            for b in body['interactive']['action']['cards'][0]['action']['buttons']]


class DiscoveryTests(unittest.TestCase):
    write_config = fixtures.JourneyConfigTests.write_config
    write_profile = fixtures.JourneyConfigTests.write_profile
    scope = fixtures.JourneyConfigTests.scope

    def setUp(self):
        fixtures.JourneyConfigTests.setUp(self)
        self.now = NOW
        self.config['isluno'] = {'native_carousels': True}
        self.write_config(self.config)
        self.catalog_path = self.directory / 'isluno_catalog.json'
        self.catalog = synthetic_catalog()
        self.gallery(2)

    def gallery(self, count):
        example = json.loads((ROOT / 'clients/mermaid/config/isluno_catalog.json').read_text())['products'][0]['gallery'][0]
        self.catalog['products'][0]['gallery'] = [dict(copy.deepcopy(example), id='image-' + str(i), order=i) for i in range(count)]
        self.catalog_path.write_text(json.dumps(self.catalog))

    def store(self, media=None):
        return DiscoveryStore(self.directory / 'discovery.db', self.catalog_path, media=media or FakeMedia(), clock=lambda: self.now)

    def plan(self, trigger='inbound-1', **kwargs):
        return self.store().plan(self.scope(), trigger, NOW.isoformat(), copy.deepcopy(DECISION), **kwargs)

    def click(self, plan, title, trigger, **kwargs):
        token = next(b['payload'] for b in replies(plan['body']) if b['title'] == title)
        return self.store().plan(self.scope(), trigger, self.now.isoformat(), action_token=token, interactive_type='button_reply', **kwargs)

    def test_all_gallery_sizes_are_accessible_and_correctly_associated(self):
        for count in [1, 2, 10, 11, 21]:
            with self.subTest(count=count):
                self.gallery(count)
                plan = self.plan('size-' + str(count))
                seen = []
                page = 0
                while True:
                    seen.extend(plan['asset_ids'])
                    self.assertEqual(plan['product_ids'], ['fixture-cruise'])
                    body = plan['body']
                    self.assertNotIn('url', json.dumps(replies(body)))
                    if len(plan['asset_ids']) == 1:
                        self.assertEqual(body['attachmentType'], 'image')
                    else:
                        cards = body['interactive']['action']['cards']
                        self.assertTrue(2 <= len(cards) <= 10)
                        self.assertEqual([c['card_index'] for c in cards], list(range(len(cards))))
                        self.assertEqual(len({len(c['action']['buttons']) for c in cards}), 1)
                        self.assertTrue(all(len(b['payload']) <= 20 for b in replies(body)))
                    if not any(b['title'] == 'More photos' for b in replies(body)):
                        break
                    page += 1
                    plan = self.click(plan, 'More photos', 'size-' + str(count) + '-page-' + str(page))
                self.assertEqual(seen, ['image-' + str(i) for i in range(count)])

    def test_single_image_fallback_mode_keeps_every_photo_in_chat(self):
        self.gallery(11)
        self.config['isluno']['native_carousels'] = False
        self.write_config(self.config)
        plan = self.plan()
        seen = list(plan['asset_ids'])
        for i in range(1, 11):
            plan = self.click(plan, 'More photos', 'single-' + str(i))
            self.assertNotIn('interactive', plan['body'])
            seen += plan['asset_ids']
        self.assertEqual(seen, ['image-' + str(i) for i in range(11)])

    def test_scope_stale_unverified_and_changed_catalog_actions(self):
        plan = self.plan()
        token = replies(plan['body'])[0]['payload']
        for field in ['customer_ref', 'conversation_id']:
            with self.assertRaises(ItineraryError):
                self.store().plan(replace(self.scope(), **{field: 'other'}), 'foreign', NOW.isoformat(), action_token=token, interactive_type='button_reply')
        with self.assertRaises(ItineraryError):
            self.store().plan(self.scope(), 'typed-token', NOW.isoformat(), action_token=token, interactive_type='text')
        self.plan('new-discovery')
        with self.assertRaises(ItineraryError):
            self.store().plan(self.scope(), 'old-tap', NOW.isoformat(), action_token=token, interactive_type='button_reply')
        current = self.plan('catalog-before')
        self.catalog['version'] = 'changed-version'
        self.catalog_path.write_text(json.dumps(self.catalog))
        with self.assertRaises(ItineraryError):
            self.click(current, 'Add trip', 'catalog-after')

    def test_expired_action_and_old_delivery_are_rejected(self):
        plan = self.plan()
        self.now += timedelta(hours=24, seconds=1)
        with self.assertRaises(ItineraryError):
            self.click(plan, 'Add trip', 'expired')
        calls = []
        self.assertFalse(send_plan(self.scope().conversation_id, self.scope().account_id, plan['id'], store=self.store(), post=lambda *a: calls.append(a)))
        self.assertEqual(calls, [])

    def test_choose_add_and_duplicate_turn_do_not_create_booking(self):
        second = copy.deepcopy(self.catalog['products'][0])
        second.update(id='other-trip', name='Other trip')
        self.catalog['products'].append(second)
        self.catalog_path.write_text(json.dumps(self.catalog))
        decision = dict(DECISION, product_ids=['fixture-cruise', 'other-trip'], intent='discover')
        start = self.store().plan(self.scope(), 'recommend', NOW.isoformat(), decision)
        chosen = self.click(start, '2. Choose', 'choose-second')
        self.assertEqual(chosen['product_ids'], ['other-trip'])
        added = self.click(chosen, 'Add trip', 'add-second')
        self.assertEqual(added['selected_intent']['product_id'], 'other-trip')
        self.assertEqual(self.store().plan(self.scope(), 'add-second', NOW.isoformat(), DECISION), added)
        with self.store().db() as db:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertFalse(any('itinerar' in name or 'payment' in name for name in tables))

    def test_missing_media_is_reported_without_product_substitution(self):
        self.gallery(11)
        plan = self.store(FakeMedia(['image-0', 'image-1'])).plan(self.scope(), 'missing', NOW.isoformat(), DECISION)
        self.assertEqual(plan['missing_asset_ids'], ['image-0', 'image-1'])
        self.assertEqual(plan['product_ids'], ['fixture-cruise'])
        self.assertEqual(len(plan['asset_ids']), 8)
        self.assertIn('Photos unavailable: 2', plan['body']['interactive']['body']['text'])
        next_page = self.click(plan, 'More photos', 'last-photo')
        self.assertEqual(next_page['asset_ids'], ['image-10'])

    def test_actual_channel_normalization_and_handler_replay(self):
        inbound = WhatsAppZernioChannel.from_zernio({'conversation_id': self.scope().conversation_id,
            'account_id': self.scope().account_id, 'sender_id': self.scope().customer_ref,
            'message_id': 'adapter-inbound', 'channel': 'whatsapp', 'text': 'Tell me about the cruise', 'sent_at': NOW.isoformat()})
        calls = []
        def understanding(*args):
            calls.append(args)
            return copy.deepcopy(DECISION)
        result = handle_message(inbound, store=self.store(), understand=understanding)
        self.assertEqual(result['media']['type'], 'isluno_discovery')
        self.assertEqual(handle_message(inbound, store=self.store(), understand=understanding), result)
        self.assertEqual(len(calls), 1)
        plan, _ = self.store().delivery_plan(result['media']['url'], self.scope().account_id, self.scope().conversation_id)
        token = replies(plan['body'])[0]['payload']
        inbound.update(message_id='adapter-button', _zernio_interactive_id=token, _zernio_interactive_type='button_reply')
        chosen = handle_message(inbound, store=self.store(), understand=understanding)
        self.assertEqual(chosen['isluno_selected_intent']['product_id'], 'fixture-cruise')
        self.assertEqual(len(calls), 1)
        inbound.update(message_id='followup-question', _zernio_interactive_id='', _zernio_interactive_type='', text='What about this trip?')
        handle_message(inbound, store=self.store(), understand=understanding)
        self.assertEqual(calls[-1][2]['discovery']['product_ids'], ['fixture-cruise'])

    def test_window_pacing_replay_and_ambiguous_delivery(self):
        plan = self.plan()
        calls = []
        def post(*args):
            calls.append(args)
            return {'status': 'accepted', 'provider_id': 'fixture-provider'}
        args = (self.scope().conversation_id, self.scope().account_id, plan['id'])
        self.assertFalse(send_plan(*args, store=self.store(), post=post, window=lambda *a: {'open': False}))
        self.assertEqual(calls, [])
        self.assertTrue(send_plan(*args, store=self.store(), post=post, window=lambda *a: {'open': True}))
        self.assertTrue(send_plan(*args, store=self.store(), post=post, window=lambda *a: {'open': True}))
        self.assertEqual(len(calls), 1)
        next_plan = self.plan('next-send')
        pauses = []
        def sleep(seconds):
            pauses.append(seconds)
            self.now += timedelta(seconds=seconds)
        self.assertFalse(send_plan(args[0], args[1], next_plan['id'], store=self.store(), post=lambda *a: {'status': 'ambiguous'}, window=lambda *a: {'open': True}, sleep=sleep))
        self.assertEqual(pauses, [6])
        self.assertFalse(send_plan(args[0], args[1], next_plan['id'], store=self.store(), post=post, window=lambda *a: {'open': True}))
        self.assertEqual(len(calls), 1)

    def test_documented_provider_errors_do_not_trigger_unproven_fallback(self):
        from agents.social import zernio_dm_client as client
        cases = [
            (400, {'error': 'Unsupported feature', 'code': 'PLATFORM_LIMITATION'}, 'rejected'),
            (400, {'error': 'Media rejected', 'code': 'PLATFORM_ERROR', 'platformError': {'code': 131053}}, 'rejected'),
            (400, {'success': False, 'error': {'code': 100}}, 'rejected'),
            (400, {'error': 'Partial', 'code': 'PLATFORM_ERROR', 'data': {'partialFailure': {'part': 'text'}}}, 'ambiguous'),
            (500, {'error': 'Server error', 'code': 'INTERNAL_ERROR'}, 'ambiguous'),
        ]
        for index, (status, payload, expected) in enumerate(cases):
            with self.subTest(payload=payload):
                self.now += timedelta(seconds=6)
                plan = self.plan('provider-shape-' + str(index))
                with patch.dict('os.environ', {'LATE_API_KEY': 'synthetic-no-network'}), \
                     patch.object(client, '_provider_mutation_account_allowed', return_value=True), \
                     patch.object(client.http_requests, 'post') as post:
                    post.return_value = SimpleNamespace(status_code=status, json=lambda: payload)
                    args = (self.scope().conversation_id, self.scope().account_id, plan['id'])
                    self.assertFalse(send_plan(*args, store=self.store(), window=lambda *a: {'open': True}))
                    self.assertFalse(send_plan(*args, store=self.store(), window=lambda *a: {'open': True}))
                    self.assertEqual(post.call_count, 1)
                    self.assertEqual(self.store().delivery_plan(plan['id'], args[1], args[0])[1], expected)

    def test_heterogeneous_fact_sets_choose_via_actual_handler(self):
        second = copy.deepcopy(self.catalog['products'][0])
        second.update(id='other-trip', name='Other trip', inclusions=[])
        self.catalog['products'].append(second)
        self.catalog_path.write_text(json.dumps(self.catalog))
        decision = dict(DECISION, product_ids=['fixture-cruise', 'other-trip'], fact_keys=['inclusion_0'], intent='discover')
        plan = self.store().plan(self.scope(), 'heterogeneous', NOW.isoformat(), decision)
        self.assertEqual(plan['product_fact_keys'], {'fixture-cruise': ['inclusion_0'], 'other-trip': []})
        token = next(b['payload'] for b in replies(plan['body']) if b['title'] == '2. Choose')
        inbound = WhatsAppZernioChannel.from_zernio({'conversation_id': self.scope().conversation_id,
            'account_id': self.scope().account_id, 'sender_id': self.scope().customer_ref,
            'message_id': 'heterogeneous-choose', 'channel': 'whatsapp', 'text': '2. Choose',
            'sent_at': NOW.isoformat(), 'interactive_id': token, 'interactive_type': 'button_reply'})
        result = handle_message(inbound, store=self.store(), understand=lambda *a: self.fail('Button must not call model'))
        selected, _ = self.store().delivery_plan(result['media']['url'], self.scope().account_id, self.scope().conversation_id)
        self.assertEqual(selected['product_ids'], ['other-trip'])
        self.assertEqual(selected['fact_keys'], ['summary'])
        self.assertNotIn('Synthetic lunch', result['text'])
        self.assertIn('Other trip', result['text'])

    def test_unsupported_questions_and_no_match_get_safe_clarification(self):
        for index, question in enumerate(['Would you like operator help with supplier availability?',
                                           'Would you like operator help with a medical safety guarantee?']):
            decision = dict(DECISION, fact_keys=[], question=question)
            plan = self.store().plan(self.scope(), 'unanswered-' + str(index), NOW.isoformat(), decision)
            self.assertEqual(plan['answer_status'], 'unavailable')
            self.assertIn('confirmed information', plan['body']['message'])
            self.assertNotIn('Synthetic test product', plan['body']['message'])
            self.assertEqual(plan['asset_ids'], [])
            self.assertFalse(plan['requires_human'])
            requested = self.click(plan, 'Ask the team', 'human-' + str(index))
            self.assertTrue(requested['requires_human'])
            self.assertEqual(requested['answer_status'], 'human_requested')
            self.assertIsNone(requested['selected_intent'])
        decision = dict(DECISION, product_ids=[], fact_keys=[], question='Untrusted invented price is $1. Which secret activity?')
        plan = self.store().plan(self.scope(), 'unmatched', NOW.isoformat(), decision)
        self.assertEqual(plan['answer_status'], 'no_match')
        self.assertIn('describe the activity', plan['body']['message'])
        self.assertNotIn('$1', plan['body']['message'])
        self.assertNotIn('Which trip interests you?', plan['body']['message'])

    def test_more_info_retains_long_source_text(self):
        long_text = 'Source detail. ' * 150
        self.catalog['products'][0]['summary'] = long_text
        self.catalog_path.write_text(json.dumps(self.catalog))
        plan = self.plan()
        texts = []
        for i in range(10):
            body = plan['body']
            texts.append(body.get('message') or body['interactive']['body']['text'])
            if not any(b['title'] == 'More info' for b in replies(body)):
                break
            plan = self.click(plan, 'More info', 'info-' + str(i))
        self.assertGreater(len(texts), 1)
        self.assertEqual(sum(t.count('Source detail.') for t in texts), 150)

    def test_unknown_model_product_or_fact_is_rejected(self):
        catalog_context = isluno_understanding.context(CatalogStore(self.catalog_path).snapshot())
        for decision in [dict(DECISION, product_ids=['invented']), dict(DECISION, fact_keys=['invented'])]:
            with self.assertRaises(ItineraryError):
                isluno_understanding.validate(decision, catalog_context)

    def test_delivery_superseded_during_window_check_never_posts(self):
        from agents.social import zernio_dm_client as client
        plan = self.plan()
        checks = []
        def window(*args):
            checks.append(args)
            if len(checks) == 2:
                self.plan('newer-view')
            return {'open': True}
        with patch.object(client.http_requests, 'post') as post:
            self.assertFalse(send_plan(self.scope().conversation_id, self.scope().account_id, plan['id'], store=self.store(), window=window))
            post.assert_not_called()

    def test_claimed_crash_cannot_be_replayed_as_a_new_send(self):
        plan = self.plan()
        def crash(*args):
            raise RuntimeError('synthetic process failure')
        args = (self.scope().conversation_id, self.scope().account_id, plan['id'])
        with self.assertRaises(RuntimeError):
            send_plan(*args, store=self.store(), post=crash, window=lambda *a: {'open': True})
        calls = []
        self.assertFalse(send_plan(*args, store=self.store(), post=lambda *a: calls.append(a), window=lambda *a: {'open': True}))
        self.assertEqual(calls, [])

    def test_sender_adapter_routes_to_durable_plan_delivery(self):
        from agents.social.senders.zernio import ZernioSender
        with patch('agents.social.isluno_delivery.send_plan', return_value=True) as sender:
            self.assertTrue(ZernioSender.send(self.scope().conversation_id, self.scope().account_id, 'Trip info', attachment_url='fixture-plan', attachment_type='isluno_discovery'))
            sender.assert_called_once_with(self.scope().conversation_id, self.scope().account_id, 'fixture-plan')

    def test_real_orchestrator_routes_before_legacy_state(self):
        from agents.social import social_agent
        with patch.object(social_agent.state_registry, 'match_ignored_contact', return_value=None), \
             patch.object(social_agent.auto_block, 'evaluate_inbound', return_value={}), \
             patch.object(social_agent.state_registry, 'wa_get_booking_state', side_effect=AssertionError('legacy state read')), \
             patch('agents.social.isluno_discovery.handle_message', return_value={'text': 'fixture'}) as handler:
            self.assertEqual(social_agent.handle_incoming_whatsapp_message({'from': 'fixture', 'text': 'Hello'}, include_media=True), {'text': 'fixture'})
            handler.assert_called_once()

    def test_one_post_only_on_timeout_partial_warning_or_missing_id(self):
        from agents.social import zernio_dm_client as client
        cases = [None, {'success': True, 'data': {}},
                 {'success': True, 'data': {'messageId': 'id', 'partialFailure': {'part': 'text'}}},
                 {'success': True, 'warnings': [{'code': 'ignored_field'}], 'data': {'messageId': 'id'}},
                 {'success': False, 'data': {'messageId': 'id'}}]
        for data in cases:
            with self.subTest(data=data), patch.dict('os.environ', {'LATE_API_KEY': 'synthetic-no-network'}), \
                 patch.object(client, '_provider_mutation_account_allowed', return_value=True), \
                 patch.object(client.http_requests, 'post') as post:
                if data is None:
                    post.side_effect = client.http_requests.Timeout('synthetic timeout')
                else:
                    post.return_value = SimpleNamespace(status_code=200, json=lambda: data)
                result = post_once(self.scope(), {'accountId': self.scope().account_id, 'message': 'Fixture'}, 'fixture-idempotency')
                self.assertEqual(result['status'], 'ambiguous')
                self.assertEqual(post.call_count, 1)
        with patch.dict('os.environ', {'LATE_API_KEY': 'synthetic-no-network'}), \
             patch.object(client, '_provider_mutation_account_allowed', return_value=False), \
             patch.object(client.http_requests, 'post') as post:
            self.assertEqual(post_once(self.scope(), {'accountId': self.scope().account_id}, 'fixture')['status'], 'blocked')
            post.assert_not_called()

    def test_future_inbound_and_disabled_sender_fail_closed(self):
        with self.assertRaises(ItineraryError):
            self.store().plan(self.scope(), 'future', (NOW + timedelta(days=1)).isoformat(), DECISION)
        plan = self.plan()
        scope = self.scope()
        self.config['features']['isluno_itinerary_demo_v1'] = False
        self.write_config(self.config)
        calls = []
        self.assertFalse(send_plan(scope.conversation_id, scope.account_id, plan['id'], store=self.store(), post=lambda *a: calls.append(a)))
        self.assertEqual(calls, [])

    def test_public_media_route_gates_identity_and_serves_verified_jpeg(self):
        path = ROOT / 'clients/mermaid/config/isluno_catalog.json'
        digest = CatalogStore(path).read()['products'][0]['gallery'][0]['sha256']
        app = FastAPI()
        app.include_router(build_public_router(lambda: MediaLibrary(path)))
        with TestClient(app) as client:
            response = client.get('/isluno/media/' + digest + '.jpg')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers['content-type'], 'image/jpeg')
            self.assertEqual(client.get('/isluno/media/' + '0' * 64 + '.jpg').status_code, 404)
            self.config['features']['isluno_itinerary_demo_v1'] = False
            self.write_config(self.config)
            self.assertEqual(client.get('/isluno/media/' + digest + '.jpg').status_code, 404)

    def test_marina_existing_entry_point_uses_same_model_and_no_sdk_retry(self):
        from agents.marina import marina_agent
        snapshot = CatalogStore(self.catalog_path).snapshot()
        saved = {'state': {'chat_language': 'en', 'history': []}}
        response = SimpleNamespace(content=[SimpleNamespace(type='tool_use', input=copy.deepcopy(DECISION))], usage=None)
        with patch.object(marina_agent.anthropic, 'Anthropic') as sdk:
            sdk.return_value.messages.create.return_value = response
            result = isluno_understanding.understand(self.scope(), 'Cruise?', saved, snapshot)
        self.assertEqual(result, DECISION)
        self.assertEqual(sdk.call_args.kwargs['max_retries'], 0)
        call = sdk.return_value.messages.create.call_args.kwargs
        self.assertEqual(call['model'], 'claude-sonnet-4-6')
        self.assertEqual(call['max_tokens'], 2048)
        self.assertIn(snapshot['revision'], call['messages'][0]['content'])
        self.assertNotIn('fake-secret', dump_for_test(call))


def dump_for_test(value):
    return json.dumps(value)


class MediaTests(unittest.TestCase):
    def test_all_originals_convert_to_bounded_jpegs_without_network_or_new_downloads(self):
        path = ROOT / 'clients/mermaid/config/isluno_catalog.json'
        library = MediaLibrary(path)
        catalog = CatalogStore(path).read()
        digests = {a['sha256'] for p in catalog['products'] for a in p['gallery']}
        self.assertEqual(len(digests), 234)
        for digest in digests:
            data = library.jpeg(digest)
            with Image.open(BytesIO(data)) as picture:
                picture.load()
                self.assertEqual(picture.format, 'JPEG')
                self.assertLessEqual(max(picture.size), 1600)
            self.assertLessEqual(len(data), 5 * 1024 * 1024)
        with self.assertRaises(MediaUnavailable):
            library.jpeg('../secret')
        with self.assertRaises(MediaUnavailable):
            library.jpeg('0' * 64)
