"""Operator-only history projections across actual synthetic booking flows."""
import copy
import json
import unittest
from fastapi import FastAPI
from fastapi.testclient import TestClient
import test_payments as payments
from test_conversation import response
from dashboard.isluno_operations import Operations,build_router
from scripts.serve_isluno_catalog_fixture import fixture_auth

class OperationsTests(unittest.TestCase):
    write_config=payments.PaymentTests.write_config;write_profile=payments.PaymentTests.write_profile;scope=payments.PaymentTests.scope
    turn=payments.PaymentTests.turn;active=payments.PaymentTests.active;initial=payments.PaymentTests.initial;summary=payments.PaymentTests.summary
    send_quote=payments.PaymentTests.send_quote;send=payments.PaymentTests.send;job=payments.PaymentTests.job;tap=payments.PaymentTests.tap;token=payments.PaymentTests.token;sleep=payments.PaymentTests.sleep
    approved=payments.PaymentTests.approved;pay=payments.PaymentTests.pay;deliver=payments.PaymentTests.deliver
    def setUp(self):
        payments.PaymentTests.setUp(self);self.operations=Operations(self.store)
        app=FastAPI();app.include_router(build_router(fixture_auth(),lambda:self.operations));self.client=TestClient(app);self.addCleanup(self.client.close)
        self.headers={'Authorization':'Bearer isluno-local-fixture-token'}
    def get(self,path):return self.client.get('/operations/'+path,headers=self.headers)
    def listing(self):return self.get('journeys').json()
    def test_real_multi_item_payment_tickets_and_truthful_partial_delivery(self):
        paid=self.pay(self.approved(multi=True));self.assertFalse(self.deliver(paid,['accepted','accepted','ambiguous']))
        row=self.listing()['items'][0];self.assertEqual(row['stage'],'demo_paid');self.assertEqual(row['item_count'],2)
        detail=self.get('journeys/'+row['id']).json()
        self.assertEqual(detail['totals']['total_minor'],50000);self.assertFalse(detail['supplier_booking_made'])
        self.assertEqual(len([d for d in detail['documents'] if d['kind']=='ticket']),2)
        self.assertIn('ambiguous',{d['status'] for d in detail['deliveries']});self.assertTrue(all(d['delivered_at'] is None for d in detail['deliveries']))
        self.assertFalse(detail['valid_actions']['advance_booking']);self.assertEqual(detail['brand'],'Isluno')
        for document in detail['documents']:
            response=self.get('journeys/'+row['id']+'/documents/'+document['id']+'.pdf')
            self.assertEqual(response.status_code,200);self.assertTrue(response.content.startswith(b'%PDF'));self.assertEqual(response.headers['cache-control'],'no-store')
        self.assertEqual(self.get('journeys/'+row['id']+'/documents/'+'0'*32+'.pdf').status_code,404)
    def test_corrected_quote_history_remains_visible_and_old_guest_not_renamed(self):
        approved=self.approved()
        result,_=self.turn(response('update',[{'date':'2026-10-18'}]));summary=self.job(result)
        self.send_quote(summary['id']);quote=self.tap(summary);self.send_quote(quote['id']);approved=self.tap(quote);self.send_quote(approved['id']);self.pay(approved)
        first=self.listing()['items'][0];detail=self.get('journeys/'+first['id']).json()
        self.assertEqual(self.get('guests').json()['total'],1);self.assertEqual(detail['item_stages'][detail['itinerary']['items'][0]['id']]['stage'],'demo_paid');self.assertEqual(len(detail['quotes']),2);self.assertEqual(detail['quotes'][0]['status'],'superseded')
        self.assertGreater(len(detail['versions']),1)
        self.turn(response('new',[{'product_id':'fixture-cruise','date':'2026-11-01'}],{'name':'Different party','ages':[25]}))
        rows=self.listing()['items'];self.assertEqual(len(rows),2)
        self.assertEqual(self.get('guests').json()['items'][0]['itinerary_count'],2)
        original=next(r for r in rows if r['id']==first['id']);self.assertEqual(original['guest_name'],'Calvin')
        self.assertEqual(self.get('journeys?q=Different').json()['total'],1)
        self.assertEqual(len(self.get('journeys/'+first['id']).json()['related_itineraries']),1)
    def test_auth_feature_scope_and_pagination(self):
        self.initial();row=self.listing()['items'][0]
        self.assertEqual(self.client.get('/operations/journeys').status_code,401)
        self.assertEqual(self.client.get('/operations/journeys',headers={'Authorization':'Bearer wrong'}).status_code,401)
        self.assertEqual(self.get('journeys?limit=0').status_code,422)
        self.assertEqual(self.get('journeys?guest_id=invalid').status_code,422)
        self.assertEqual(self.get('journeys/'+'0'*64+'.'+row['itinerary_id']).status_code,404)
        with self.store.db() as db,db:
            original=db.execute('SELECT * FROM isluno_itineraries').fetchone();scope=json.loads(original['scope_json']);scope['account_id']='wrong-account'
            db.execute('INSERT INTO isluno_itineraries VALUES(?,?,?,?,?)',('f'*64,'private',json.dumps(scope),1,original['payload_json']))
        self.assertEqual(self.listing()['total'],1)
        config=copy.deepcopy(self.config);config['features']={};self.write_config(config)
        self.assertEqual(self.get('journeys').status_code,403);self.assertEqual(self.get('journeys/'+row['id']).status_code,403)
    def test_date_only_correction_preserves_each_trip_party(self):
        self.approved(multi=True)
        first_id=self.active()['items'][0]['id']
        self.turn(response('update',[{'item_id':first_id,'date':'2026-10-19'}]))
        row=self.listing()['items'][0];detail=self.get('journeys/'+row['id']).json()
        self.assertEqual(detail['item_details'][first_id]['guest_name'],'Calvin')
        second_id=self.active()['items'][1]['id']
        self.assertEqual(detail['item_details'][second_id]['guest_name'],'Second Party')
        self.assertEqual(detail['quotes'][-1]['snapshot']['item_details'][first_id]['guest_name'],'Calvin')
        frozen=copy.deepcopy(detail['quotes'][-1]['snapshot'])
        self.turn(response('update',[{'item_id':first_id}],guest={'name':'First party revised'}))
        revised=self.get('journeys/'+row['id']).json()
        self.assertEqual(revised['item_details'][first_id]['guest_name'],'First party revised')
        self.assertEqual(revised['item_details'][second_id]['guest_name'],'Second Party')
        self.assertEqual(next(q['snapshot'] for q in revised['quotes'] if q['id']==frozen['id']),frozen)
        self.turn(response('add',[{'product_id':'fixture-cruise','date':'2026-10-22'}]))
        added=self.get('journeys/'+row['id']).json()
        third_id=self.active()['items'][-1]['id']
        self.assertEqual(added['item_details'][third_id]['guest_name'],'First party revised')
        self.assertEqual(added['item_details'][second_id]['guest_name'],'Second Party')


    def test_pending_guest_replacement_survives_invalid_date_then_completion(self):
        self.approved(multi=True)
        first,second=[item['id'] for item in self.active()['items']]
        row=self.listing()['items'][0]
        frozen=copy.deepcopy(self.get('journeys/'+row['id']).json()['quotes'][-1]['snapshot'])
        self.turn(response('update',[{'item_id':first,'date':'2026-02-30'}],guest={'name':'First party revised'}))
        invalid=self.get('journeys/'+row['id']).json()
        self.assertEqual(invalid['item_details'][first]['guest_name'],'Calvin')
        self.turn(response('update',[{'item_id':first,'date':'2026-10-19'}]))
        completed=self.get('journeys/'+row['id']).json()
        self.assertEqual(completed['item_details'][first]['guest_name'],'First party revised')
        self.assertEqual(completed['item_details'][second]['guest_name'],'Second Party')
        self.assertEqual(completed['quotes'][-1]['snapshot']['item_details'][first]['guest_name'],'First party revised')
        self.assertEqual(next(q['snapshot'] for q in completed['quotes'] if q['id']==frozen['id']),frozen)

    def test_incomplete_new_trip_keeps_its_party_when_another_party_changes(self):
        self.approved(multi=True)
        first,second=[item['id'] for item in self.active()['items']]
        self.turn(response('add',[{'product_id':'fixture-cruise'}],guest={'name':'Third Party'}))
        pending=self.store.session(self.scope())['pending']
        third=next(iter(pending))
        self.turn(response('update',[{'item_id':first}],guest={'name':'First party revised'}))
        self.turn(response('update',[{'item_id':third,'date':'2026-10-22'}]))
        row=self.listing()['items'][0];detail=self.get('journeys/'+row['id']).json()
        self.assertEqual(detail['item_details'][first]['guest_name'],'First party revised')
        self.assertEqual(detail['item_details'][second]['guest_name'],'Second Party')
        self.assertEqual(detail['item_details'][third]['guest_name'],'Third Party')
        self.assertEqual(detail['quotes'][-1]['snapshot']['item_details'][third]['guest_name'],'Third Party')

    def test_delivery_parts_identify_superseded_and_current_quote_versions(self):
        summary=self.summary();self.send_quote(summary['id'])
        old_quote=self.tap(summary);self.assertFalse(self.send_quote(old_quote['id'],['ambiguous']))
        result,_=self.turn(response('update',[{'date':'2026-10-19'}]))
        summary=self.job(result);self.send_quote(summary['id'])
        new_quote=self.tap(summary);self.send_quote(new_quote['id'])
        row=self.listing()['items'][0];detail=self.get('journeys/'+row['id']).json()
        old=[d for d in detail['deliveries'] if d['quote_id']==old_quote['quote_id']]
        new=[d for d in detail['deliveries'] if d['quote_id']==new_quote['quote_id']]
        self.assertTrue(old and new)
        self.assertTrue(all(d['quote_version']==1 and d['quote_status']=='superseded' for d in old))
        self.assertTrue(all(d['quote_version']==2 and d['quote_status']=='quote' for d in new))
        self.assertIn('ambiguous',{d['status'] for d in old})
        self.assertEqual({d['status'] for d in new},{'accepted'})
        for d in old+new:
            self.assertGreater(d['part_count'],d['part'])
            if d['document_id']:
                self.assertEqual(d['document_id'],d['quote_id']);self.assertEqual(d['document_kind'],'quote')

    def test_name_only_correction_updates_matching_pending_party_before_completion(self):
        self.turn(response('add',[{'product_id':'fixture-cruise'}],guest={'name':'Other Party','ages':[35]}))
        other=next(iter(self.store.session(self.scope())['pending']))
        self.turn(response('add',[{'product_id':'fixture-cruise'}],guest={'name':'Calvin'}))
        item_id=next(item for item in self.store.session(self.scope())['pending'] if item!=other)
        self.turn(response('none',guest={'name':'Kelvin'}),text='My name is Kelvin, not Calvin.')
        pending=self.store.session(self.scope())['pending']
        self.assertEqual(pending[item_id]['guest_name'],'Kelvin')
        self.assertEqual(pending[other]['guest_name'],'Other Party')
        self.turn(response('update',[{'item_id':item_id,'date':'2026-10-19'}]))
        self.turn(response('update',[{'item_id':other,'date':'2026-10-20'}]))
        self.turn(response('summary'))
        row=self.listing()['items'][0];detail=self.get('journeys/'+row['id']).json()
        self.assertEqual(detail['guest_name'],'Kelvin')
        self.assertEqual(detail['item_details'][item_id]['guest_name'],'Kelvin')
        self.assertEqual(detail['item_details'][other]['guest_name'],'Other Party')
        self.assertEqual(detail['quotes'][-1]['snapshot']['item_details'][item_id]['guest_name'],'Kelvin')
        self.assertEqual(detail['quotes'][-1]['snapshot']['item_details'][other]['guest_name'],'Other Party')

    def test_reads_do_not_modify_snapshots_or_create_delivery(self):
        self.pay();before=self.payments.records(self.scope());row=self.listing()['items'][0]
        for _ in range(2):self.get('journeys/'+row['id'])
        self.assertEqual(self.payments.records(self.scope()),before)
