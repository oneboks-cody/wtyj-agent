"""Offline persistence and HTTP boundary checks with synthetic identities."""
import copy
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import sqlite3
import unittest

from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient

import test_journey_config as fixtures
from agents.social.isluno_context import ContextStore, ContextConflict, validate_state
from dashboard.isluno_api import build_router
from shared import isluno_config as identity


class JourneyContextTests(unittest.TestCase):
    setUp = fixtures.JourneyConfigTests.setUp
    write_config = fixtures.JourneyConfigTests.write_config
    write_profile = fixtures.JourneyConfigTests.write_profile
    scope = fixtures.JourneyConfigTests.scope

    def store(self):
        return ContextStore(self.directory / 'state.db')

    def test_fresh_context_does_not_import_or_change_legacy_rows(self):
        store = self.store()
        with sqlite3.connect(store.db_path) as db:
            db.execute('CREATE TABLE legacy_intake (payload TEXT)')
            db.execute('INSERT INTO legacy_intake VALUES (?)', ('Mermaid pending payment',))
        context = store.open(self.scope())
        self.assertEqual(context['brand_snapshot']['name'], 'Isluno')
        self.assertEqual(context['state']['history'], [])
        self.assertIsNone(context['state']['active_itinerary_id'])
        self.assertEqual(context['revision'], 1)
        with sqlite3.connect(store.db_path) as db:
            self.assertEqual(db.execute('SELECT * FROM legacy_intake').fetchall(), [('Mermaid pending payment',)])

    def test_context_survives_restart_and_brand_snapshot_is_immutable(self):
        scope = self.scope()
        first = self.store().open(scope)
        updated = self.store().append_turn(scope, expected_revision=1, role='user', content='Hello',
                                          inbound_at='2026-09-08T12:00:00+00:00')
        self.profile['profile_version'] = 'next-profile'
        self.profile['brand']['assistant_name'] = 'New name'
        self.write_profile(self.profile)
        reloaded = self.store().open(scope)
        self.assertEqual(reloaded, updated)
        self.assertEqual(reloaded['brand_snapshot'], first['brand_snapshot'])
        other = self.store().open(replace(scope, customer_ref='new-customer'))
        self.assertEqual(other['brand_snapshot']['profile_version'], 'next-profile')
        self.assertEqual(other['state']['history'], [])
        conversation = self.store().open(replace(scope, conversation_id='new-conversation'))
        self.assertEqual(conversation['state']['history'], [])
        self.assertNotEqual(other['session_key'], conversation['session_key'])

    def test_invalid_scope_cannot_create_database(self):
        scope = self.scope()
        for field, value in [('account_id', 'foreign'), ('tenant_slug', 'foreign'),
                             ('journey_type', 'mermaid_legacy'), ('schema_version', 'old')]:
            with self.subTest(field=field), self.assertRaises(identity.IslunoUnavailable):
                self.store().open(replace(scope, **{field: value}))
            self.assertFalse(self.store().db_path.exists())

    def test_disabling_feature_revokes_existing_context_without_writing(self):
        scope = self.scope()
        current = self.store().open(scope)
        before = self.store().db_path.read_bytes()
        self.config['features'][identity.FEATURE] = False
        self.write_config(self.config)
        with self.assertRaises(identity.IslunoUnavailable):
            self.store().open(scope)
        with self.assertRaises(identity.IslunoUnavailable):
            self.store().save(scope, expected_revision=1, state=current['state'])
        self.assertEqual(before, self.store().db_path.read_bytes())

    def test_concurrent_edits_have_exactly_one_winner(self):
        scope = self.scope()
        current = self.store().open(scope)
        def save(language):
            state = copy.deepcopy(current['state'])
            state['chat_language'] = language
            try:
                return self.store().save(scope, expected_revision=1, state=state)['revision']
            except ContextConflict:
                return 'conflict'
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(save, ['nl', 'en']))
        self.assertCountEqual(results, [2, 'conflict'])
        with self.assertRaises(ContextConflict):
            self.store().append_turn(scope, expected_revision=1, role='user', content='stale')
        self.assertEqual(self.store().open(scope)['revision'], 2)

    def test_invalid_or_legacy_state_is_rejected_without_write(self):
        scope = self.scope()
        state = self.store().open(scope)['state']
        invalid = [('legacy_payment_id', 'old'), ('document_language', []),
                   ('reminder_preference', {}), ('history', [{'role': [], 'content': 'x'}]),
                   ('last_inbound_at', '2026-09-08'), ('active_itinerary_id', '../legacy')]
        for field, value in invalid:
            candidate = copy.deepcopy(state)
            candidate[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.store().save(scope, expected_revision=1, state=candidate)
        self.assertEqual(self.store().open(scope)['revision'], 1)
        with self.assertRaises(ValueError):
            self.store().append_turn(scope, expected_revision=1, role='assistant', content='x',
                                     inbound_at='2026-09-08T12:00:00+00:00')

    def test_authenticated_capabilities_http_and_feature_revocation(self):
        def auth(authorization: str = Header(default='')):
            if authorization != 'Bearer synthetic-session':
                raise HTTPException(status_code=401)
        app = FastAPI()
        app.include_router(build_router(auth), prefix='/dashboard/api')
        with TestClient(app) as client:
            path = '/dashboard/api/isluno/capabilities'
            self.assertEqual(client.get(path).status_code, 401)
            self.assertEqual(client.get(path, headers={'Authorization': 'Bearer wrong'}).status_code, 401)
            headers = {'Authorization': 'Bearer synthetic-session'}
            response = client.get(path, headers=headers)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers['cache-control'], 'no-store')
            self.assertTrue(response.json()['enabled'])
            self.assertTrue(response.json()['capabilities']['itinerary_booking'])
            self.config['features'][identity.FEATURE] = False
            self.write_config(self.config)
            self.assertFalse(client.get(path, headers=headers).json()['enabled'])
            self.config['slug'] = 'foreign'
            self.write_config(self.config)
            self.assertFalse(client.get(path, headers=headers).json()['known'])
