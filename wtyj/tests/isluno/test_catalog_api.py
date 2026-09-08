"""Real authenticated catalog callers; no external data or adapters."""
import copy
import json
from pathlib import Path
import unittest
from fastapi import FastAPI
from fastapi.testclient import TestClient
import test_journey_config as fixtures
from test_catalog import synthetic_catalog
from shared.isluno_catalog import CatalogStore
from shared import isluno_config
from dashboard.isluno_catalog_api import build_router
from scripts.serve_isluno_catalog_fixture import fixture_auth

class CatalogApiTests(unittest.TestCase):
    write_config=fixtures.JourneyConfigTests.write_config;write_profile=fixtures.JourneyConfigTests.write_profile
    def setUp(self):
        fixtures.JourneyConfigTests.setUp(self)
        self.path=self.directory/'isluno_catalog.json';self.path.write_text(json.dumps(synthetic_catalog()))
        self.store=CatalogStore(self.path);app=FastAPI();app.include_router(build_router(fixture_auth(),lambda:self.store))
        self.client=TestClient(app);self.addCleanup(self.client.close)
        self.headers={'Authorization':'Bearer isluno-local-fixture-token'}
    def read(self):return self.client.get('/catalog',headers=self.headers)
    def publish(self,changes,revision=None):
        return self.client.put('/catalog',headers=self.headers,json={'expected_revision':revision or self.store.snapshot()['revision'],'changes':[{'id':'fixture-cruise','changes':changes}]})
    def test_auth_tenant_and_disabled_capability_guard_reads_and_writes(self):
        body={'expected_revision':self.store.snapshot()['revision'],'changes':[{'id':'fixture-cruise','changes':{'summary':'Changed'}}]}
        for headers in [{},{'Authorization':'Bearer other-tenant-token'}]:
            self.assertEqual(self.client.get('/catalog',headers=headers).status_code,401)
            self.assertEqual(self.client.put('/catalog',headers=headers,json=body).status_code,401)
        for mutation in [{'slug':'other'},{'features':{}}]:
            config=copy.deepcopy(self.config);config.update(mutation);self.write_config(config)
            self.assertEqual(self.read().status_code,403);self.assertEqual(self.client.put('/catalog',headers=self.headers,json=body).status_code,403)
    def test_actual_publication_conflict_and_immutable_catalog_history(self):
        before=self.read().json();self.assertTrue(before['editable'])
        result=self.publish({'summary':'New recommendation facts'},before['revision']);self.assertEqual(result.status_code,200)
        self.assertNotEqual(result.json()['revision'],before['revision']);self.assertEqual(self.read().headers['cache-control'],'no-store')
        self.assertEqual(self.publish({'summary':'Stale'},before['revision']).status_code,409)
        archived=json.loads((self.path.with_name('isluno_catalog_versions')/(before['revision']+'.json')).read_text())
        self.assertEqual(archived,before['catalog']);self.assertEqual(self.store.read()['products'][0]['summary'],'New recommendation facts')
    def test_invalid_rules_identity_and_asset_injection_are_atomic(self):
        before=self.path.read_bytes();product=self.store.read()['products'][0]
        price=copy.deepcopy(product['price_rules']);price['age_bands'][0]['amount_minor']=-1
        for changes in [{'price_rules':price},{'source':{'url':'https://evil.invalid'}},{'gallery':[{'id':'unknown'}]},{'schedule':{'weekdays':[]}}]:
            self.assertEqual(self.publish(changes).status_code,422);self.assertEqual(self.path.read_bytes(),before)
    def test_existing_demo_provenance_cannot_be_removed_or_promoted(self):
        catalog=synthetic_catalog();p=catalog['products'][0];keys=['price_rules','guest_rules','schedule','options','pickup','policies']
        p['demo_rules']={'authority':'user_approved_demo_sample','version':'fixture','label':'DEMO SAMPLE','approval_ref':'fixture','real_booking_eligible':False,'rules':{k:copy.deepcopy(p[k]) for k in keys}};p['price_rules']=None
        self.path.write_text(json.dumps(catalog));before=self.path.read_bytes()
        for demo in [None,{**p['demo_rules'],'label':'Confirmed supplier rules'},{**p['demo_rules'],'real_booking_eligible':True}]:
            self.assertEqual(self.publish({'demo_rules':demo}).status_code,422);self.assertEqual(self.path.read_bytes(),before)
        demo=copy.deepcopy(p['demo_rules']);demo['rules']['price_rules']['age_bands'][2]['amount_minor']=12500
        result=self.publish({'demo_rules':demo});self.assertEqual(result.status_code,200)
        product=result.json()['catalog']['products'][0];self.assertIsNone(product['price_rules']);self.assertEqual(product['readiness']['pricing_mode'],'demo_sample')
    def test_untrusted_envelope_and_missing_image_rejected(self):
        self.assertEqual(self.client.put('/catalog',headers=self.headers,json={'expected_revision':'bad','changes':[],'tenant':'mermaid'}).status_code,422)
        self.assertEqual(self.client.get('/catalog/media/not-a-hash.jpg',headers=self.headers).status_code,404)
        self.assertEqual(self.client.get('/catalog/media/not-a-hash.jpg').status_code,401)
