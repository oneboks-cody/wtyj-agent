"""Exact pricing, transactional replay and isolation, without network access."""
import copy
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import unittest

from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient
import test_journey_config as fixtures
from test_catalog import synthetic_catalog
from agents.social.isluno_itinerary import ItineraryStore, ItineraryConflict
from dashboard.isluno_api import build_router
from shared.isluno_catalog import CatalogStore, CatalogError
from shared.isluno_config import IslunoUnavailable
from shared.isluno_pricing import ItineraryError, price_item

NOW = datetime(2026, 9, 8, 12, tzinfo=timezone.utc)


def selection(item='trip-1', day='2026-10-15', product='fixture-cruise'):
    return {'item_id': item, 'product_id': product, 'date': day, 'slot_id': 'morning',
            'guest_ages': [35, 34, 8], 'options': {}, 'pickup': False}


class ItineraryTests(unittest.TestCase):
    write_config = fixtures.JourneyConfigTests.write_config
    write_profile = fixtures.JourneyConfigTests.write_profile
    scope = fixtures.JourneyConfigTests.scope

    def setUp(self):
        fixtures.JourneyConfigTests.setUp(self)
        self.catalog_path = self.directory / 'isluno_catalog.json'
        self.catalog = synthetic_catalog()
        self.publish_fixture()

    def publish_fixture(self):
        self.catalog_path.write_text(json.dumps(self.catalog))

    def store(self):
        return ItineraryStore(self.directory / 'itineraries.db', self.catalog_path, clock=lambda: NOW)

    def add(self, chosen=None, revision=1, request='add-1'):
        return self.store().mutate(self.scope(), 'itinerary', request_id=request, expected_revision=revision,
                                   mutation={'action': 'add', 'selection': chosen or selection()})

    def create(self):
        return self.store().create(self.scope(), 'itinerary', request_id='create-1')

    def assert_error(self, code, function):
        with self.assertRaises(ItineraryError) as caught:
            function()
        self.assertEqual(caught.exception.code, code)

    def test_single_multi_restart_and_exact_lines(self):
        self.create()
        first = self.add()
        self.assertEqual(first['items'][0]['total_minor'], 25000)
        multi = self.add(selection('trip-2', '2026-10-16'), revision=2, request='add-2')
        self.assertEqual(multi['totals']['total_minor'], 50000)
        self.assertEqual(sum(line['amount_minor'] for item in multi['items'] for line in item['lines']), 50000)
        self.assertEqual(self.store().get(self.scope(), 'itinerary'), multi)
        self.assertEqual(self.store().get(self.scope(), 'itinerary', revision=2), first)
        self.assertEqual(self.store().summaries(self.scope())[0]['item_count'], 2)
        self.assertFalse(multi['totals']['real_money_moved'])

    def test_duplicate_replay_and_reused_key_protection(self):
        initial = self.create()
        self.assertEqual(self.create(), initial)
        first = self.add()
        self.assertEqual(self.add(), first)
        self.add(selection('trip-2', '2026-10-16'), revision=2, request='add-2')
        self.assertEqual(self.add(), first)  # Return original response even after later edits.
        self.assertEqual(len(self.store().get(self.scope(), 'itinerary')['items']), 2)
        self.assert_error('request_id_reused_with_different_payload', lambda: self.add(selection('other')))
        self.assert_error('item_already_exists', lambda: self.add(revision=3, request='new-request'))

    def test_concurrent_distinct_edits_have_one_winner(self):
        self.create()
        def add(index):
            try:
                return self.add(selection('trip-' + str(index), '2026-10-' + str(15 + index)),
                                request='request-' + str(index))['revision']
            except ItineraryConflict:
                return 'conflict'
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(list(pool.map(add, [1, 2])), [2, 'conflict'])
        self.assertEqual(len(self.store().get(self.scope(), 'itinerary')['items']), 1)

    def test_simultaneous_duplicate_request_has_one_effect(self):
        self.create()
        with ThreadPoolExecutor(max_workers=2) as pool:
            left, right = list(pool.map(lambda _: self.add(), range(2)))
        self.assertEqual(left, right)
        self.assertEqual(self.store().get(self.scope(), 'itinerary')['revision'], 2)

    def test_update_remove_and_catalog_change_keep_previous_revisions(self):
        self.create()
        first = self.add()
        catalog = CatalogStore(self.catalog_path)
        snapshot = catalog.snapshot()
        rules = copy.deepcopy(self.catalog['products'][0]['price_rules'])
        rules['age_bands'][-1]['amount_minor'] = 12000
        catalog.publish([{'id': 'fixture-cruise', 'changes': {'price_rules': rules}}], snapshot['revision'])
        updated = self.store().mutate(self.scope(), 'itinerary', request_id='update-1', expected_revision=2,
                                     mutation={'action': 'update', 'selection': selection()})
        self.assertEqual(updated['totals']['total_minor'], 29000)
        self.assertEqual(self.store().get(self.scope(), 'itinerary', revision=2), first)
        removed = self.store().mutate(self.scope(), 'itinerary', request_id='remove-1', expected_revision=3,
                                     mutation={'action': 'remove', 'item_id': 'trip-1'})
        self.assertEqual(removed['totals']['total_minor'], 0)
        self.assertEqual(removed['items'], [])
        with sqlite3.connect(self.store().db_path) as db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute('UPDATE isluno_itinerary_versions SET payload_json=?', ('{}',))
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute('DELETE FROM isluno_itinerary_versions')

    def test_known_overlap_including_checkin_is_correctable_and_atomic(self):
        product = self.catalog['products'][0]
        product['schedule']['slots'].append({'id': 'noon', 'start': '12:00', 'duration_minutes': 60})
        product['schedule']['check_in_minutes_before'] = 20
        self.publish_fixture()
        self.create()
        first = self.add()
        other = selection('trip-2')
        other['slot_id'] = 'noon'
        self.assert_error('schedule_overlap', lambda: self.add(other, revision=2, request='add-2'))
        self.assertEqual(self.store().get(self.scope(), 'itinerary'), first)
        other['date'] = '2026-10-16'
        self.assertEqual(self.add(other, revision=2, request='add-2')['revision'], 3)

    def test_guest_date_and_option_errors_do_not_mutate(self):
        self.create()
        for field, value, code in [('guest_ages', [], 'invalid_guest_count'), ('guest_ages', [True], 'invalid_guest_age'),
                                   ('guest_ages', [121], 'invalid_guest_age'), ('date', '2026-02-30', 'invalid_date'),
                                   ('date', '2026-01-01', 'date_in_past'), ('slot_id', 'missing', 'departure_slot_unavailable'),
                                   ('options', {'missing': 1}, 'unknown_option'), ('pickup', True, 'pickup_unavailable')]:
            chosen = selection()
            chosen[field] = value
            with self.subTest(field=field, value=value):
                self.assert_error(code, lambda: self.add(chosen))
        self.assertEqual(self.store().get(self.scope(), 'itinerary')['revision'], 1)

    def test_child_bands_extras_pickup_and_capacity(self):
        p = self.catalog['products'][0]
        p['guest_rules'].update(children_require_adult=True, max_guests=4)
        p['options'] = [{'id': 'pickup', 'name': 'Pickup', 'basis': 'per_booking', 'amount_minor': 2500, 'max_quantity': 1, 'required': False},
                        {'id': 'gear', 'name': 'Gear', 'basis': 'per_person', 'amount_minor': 500, 'max_quantity': 4, 'required': True},
                        {'id': 'photo', 'name': 'Photo', 'basis': 'per_unit', 'amount_minor': 1000, 'max_quantity': 2, 'required': False}]
        p['pickup'] = {'mode': 'priced_option', 'meeting_point': 'Fixture hotel', 'option_id': 'pickup'}
        self.publish_fixture()
        snapshot = CatalogStore(self.catalog_path).snapshot()
        chosen = selection()
        chosen.update(guest_ages=[35, 8, 2], options={'pickup': 1, 'gear': 3, 'photo': 2}, pickup=True)
        item = price_item(snapshot, chosen, now=NOW)
        self.assertEqual(item['total_minor'], 21000)
        chosen['options']['gear'] = 2
        self.assert_error('required_option_missing', lambda: price_item(snapshot, chosen, now=NOW))
        chosen['options']['gear'] = 3
        chosen['pickup'] = False
        self.assert_error('pickup_option_mismatch', lambda: price_item(snapshot, chosen, now=NOW))
        chosen['guest_ages'] = [8]
        self.assert_error('adult_required', lambda: price_item(snapshot, chosen, now=NOW))
        chosen['guest_ages'] = [35] * 5
        self.assert_error('guest_capacity_exceeded', lambda: price_item(snapshot, chosen, now=NOW))

    def test_per_booking_and_included_pickup(self):
        p = self.catalog['products'][0]
        p['price_rules'].update(basis='per_booking', amount_minor=30000, max_guests=3)
        p['pickup'] = {'mode': 'included', 'meeting_point': 'Fixture hotel'}
        self.publish_fixture()
        chosen = selection()
        chosen['pickup'] = True
        item = price_item(CatalogStore(self.catalog_path).snapshot(), chosen, now=NOW)
        self.assertEqual(item['total_minor'], 30000)
        self.assertEqual(len(item['lines']), 1)
        chosen['guest_ages'].append(30)
        self.assert_error('guest_capacity_exceeded', lambda: price_item(CatalogStore(self.catalog_path).snapshot(), chosen, now=NOW))

    def test_mixed_currency_and_unverified_tax_are_never_invented(self):
        second = copy.deepcopy(self.catalog['products'][0])
        second['id'] = 'euro-trip'
        second['price_rules']['currency'] = 'EUR'
        self.catalog['products'].append(second)
        self.publish_fixture()
        self.create()
        self.add()
        self.assert_error('mixed_currency_requires_confirmed_conversion', lambda: self.add(selection('trip-2', '2026-10-16', 'euro-trip'), revision=2, request='add-2'))
        self.catalog['products'][0]['price_rules']['taxes_fees'] = 'unverified'
        self.publish_fixture()
        with self.assertRaises(CatalogError):
            self.add(selection('trip-2', '2026-10-16'), revision=2, request='add-3')
        self.assertEqual(self.store().get(self.scope(), 'itinerary')['revision'], 2)

    def test_scope_isolation_and_additive_legacy_compatibility(self):
        with sqlite3.connect(self.store().db_path) as db:
            db.execute('CREATE TABLE legacy_quote (value TEXT)')
            db.execute('INSERT INTO legacy_quote VALUES (?)', ('old immutable quote',))
        self.create()
        self.add()
        for field, value in [('customer_ref', 'other'), ('conversation_id', 'other')]:
            scope = replace(self.scope(), **{field: value})
            self.assertEqual(self.store().summaries(scope), [])
            self.assert_error('itinerary_not_found', lambda: self.store().get(scope, 'itinerary'))
        for field, value in [('account_id', 'other'), ('tenant_slug', 'other'), ('journey_type', 'legacy')]:
            with self.assertRaises(IslunoUnavailable):
                self.store().get(replace(self.scope(), **{field: value}), 'itinerary')
        with sqlite3.connect(self.store().db_path) as db:
            self.assertEqual(db.execute('SELECT value FROM legacy_quote').fetchone()[0], 'old immutable quote')
            self.assertEqual(db.execute('SELECT count(*) FROM isluno_itinerary_versions').fetchone()[0], 2)

    def test_all_31_products_demo_price_with_explicit_sample_labels(self):
        path = Path(__file__).resolve().parents[3] / 'clients/mermaid/config/isluno_catalog.json'
        snapshot = CatalogStore(path).snapshot()
        from datetime import date, timedelta
        for product in snapshot['catalog']['products']:
            rules = product['demo_rules']['rules']
            day = date(2026, 10, 15)
            while day.weekday() not in rules['schedule']['weekdays']:
                day += timedelta(days=1)
            chosen = selection(product=product['id'], day=day.isoformat())
            chosen.update(slot_id=rules['schedule']['slots'][0]['id'], guest_ages=[35],
                          options={o['id']: 1 for o in rules['options'] if o['required']})
            with self.subTest(product=product['id']):
                item = price_item(snapshot, chosen, now=NOW)
                self.assertEqual(item['pricing_snapshot']['pricing_mode'], 'demo_sample')
                self.assertTrue(item['pricing_snapshot']['label'])
                self.assertFalse(item['real_booking_eligible'])
                self.assertEqual(item['total_minor'], sum(l['amount_minor'] for l in item['lines']))

    def test_disabled_access_cannot_create_or_replay_itinerary(self):
        scope = self.scope()
        self.config['features']['isluno_itinerary_demo_v1'] = False
        self.write_config(self.config)
        with self.assertRaises(IslunoUnavailable):
            self.store().create(scope, 'itinerary', request_id='create-1')
        self.assertFalse(self.store().db_path.exists())
        self.config['features']['isluno_itinerary_demo_v1'] = True
        self.write_config(self.config)
        self.create()
        self.add()
        original = self.store().db_path.read_bytes()
        self.config['features']['isluno_itinerary_demo_v1'] = False
        self.write_config(self.config)
        with self.assertRaises(IslunoUnavailable):
            self.store().mutate(scope, 'itinerary', request_id='add-1', expected_revision=1,
                                mutation={'action': 'add', 'selection': selection()})
        self.assertEqual(original, self.store().db_path.read_bytes())

    def test_published_jungle_child_bands_are_retained_with_sample_provenance(self):
        path = Path(__file__).resolve().parents[3] / 'clients/mermaid/config/isluno_catalog.json'
        snapshot = CatalogStore(path).snapshot()
        chosen = selection(product='the-jungle-tour')
        chosen.update(guest_ages=[3, 4, 11, 12], slot_id='sample-0900')
        item = price_item(snapshot, chosen, now=NOW)
        self.assertEqual(item['total_minor'], 4000)
        self.assertEqual([row['quantity'] for row in item['lines']], [1, 2, 1])
        self.assertEqual(item['pricing_snapshot']['pricing_mode'], 'demo_sample')
        self.assertTrue(item['product']['source_claims'])
        self.assertIn('Non-refundable', item['pricing_snapshot']['rules']['policies']['cancellation'])

    def test_unedited_items_retain_prices_and_departure_days_are_enforced(self):
        self.create()
        first = self.add()
        self.catalog['products'][0]['price_rules']['age_bands'][-1]['amount_minor'] = 12000
        self.publish_fixture()
        combined = self.add(selection('trip-2', '2026-10-16'), revision=2, request='add-2')
        self.assertEqual(combined['items'][0], first['items'][0])
        self.assertEqual(combined['totals']['total_minor'], 54000)
        self.catalog['products'][0]['schedule']['weekdays'] = [0]
        self.publish_fixture()
        self.assert_error('departure_day_unavailable', lambda: self.add(selection('trip-3', '2026-10-17'), revision=3, request='add-3'))
        self.assertEqual(self.store().get(self.scope(), 'itinerary'), combined)

    def test_authenticated_http_scope_and_revision_projection(self):
        self.create()
        first = self.add()
        def auth(authorization: str = Header(default='')):
            if authorization != 'Bearer test':
                raise HTTPException(401)
        app = FastAPI()
        app.include_router(build_router(auth, itinerary_store_factory=self.store))
        scope = self.scope()
        params = {'account_id': scope.account_id, 'conversation_id': scope.conversation_id, 'customer_ref': scope.customer_ref}
        with TestClient(app) as client:
            url = '/isluno/itineraries/itinerary'
            self.assertEqual(client.get(url, params=params).status_code, 401)
            headers = {'Authorization': 'Bearer test'}
            result = client.get(url, params=params, headers=headers)
            self.assertEqual(result.json(), first)
            self.assertEqual(result.headers['cache-control'], 'no-store')
            self.assertEqual(client.get(url, params={**params, 'revision': 1}, headers=headers).json()['items'], [])
            self.assertEqual(client.get(url, params={**params, 'customer_ref': 'other'}, headers=headers).status_code, 404)
            self.assertEqual(client.get(url, params={**params, 'account_id': 'other'}, headers=headers).status_code, 403)
            self.assertEqual(len(client.get('/isluno/itineraries', params=params, headers=headers).json()['itineraries']), 1)
