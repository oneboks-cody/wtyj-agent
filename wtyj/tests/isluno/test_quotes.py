"""Quote stages through real inbound/sender/router callers; no external calls."""
import copy
from datetime import timedelta
import hashlib
import json
import sqlite3
import unittest
from unittest.mock import patch

import test_conversation as conversation
from agents.social.isluno_quotes import QuoteStore, build_public_router
from agents.social.isluno_quote_documents import projection, text_sections, render_pdf, COPY
from agents.social.isluno_quote_delivery import send_job
from shared.isluno_config import JourneyScope
from shared.isluno_pricing import ItineraryError
from shared.isluno_catalog import CatalogStore


class QuoteTests(unittest.TestCase):
    write_config = conversation.ConversationTests.write_config
    write_profile = conversation.ConversationTests.write_profile
    scope = conversation.ConversationTests.scope
    turn = conversation.ConversationTests.turn
    active = conversation.ConversationTests.active
    initial = conversation.ConversationTests.initial

    def setUp(self):
        conversation.ConversationTests.setUp(self)
        self.now = conversation.NOW
        self.itinerary.clock = lambda: self.now
        self.discovery.clock = lambda: self.now
        self.quotes = QuoteStore(self.store, 'https://example.invalid/r/isluno/documents')
        self.posts = []

    def sleep(self, seconds): self.now += timedelta(seconds=seconds)

    def send(self, job_id, statuses=None):
        def post(scope, body, key):
            self.posts.append((scope,body,key))
            status = statuses.pop(0) if statuses else 'accepted'
            return {'status':status, 'provider_id': 'provider-' + str(len(self.posts)) if status == 'accepted' else None}
        return send_job(self.scope().conversation_id,self.scope().account_id,job_id,store=self.quotes,post=post,
                        window=lambda *a:{'open':True},sleep=self.sleep)

    def job(self, result):
        return self.quotes.job(result['media']['url'], self.scope().account_id,self.scope().conversation_id)

    def summary(self):
        self.initial()
        result, calls = self.turn(conversation.response('summary'))
        self.assertEqual(calls,1)
        job = self.job(result)
        self.assertEqual(job['stage'],'summary')
        return job

    def token(self, job): return job['parts'][-1]['buttons'][0]['payload']

    def tap(self, job, **kwargs):
        result,calls = self.turn(token=self.token(job), **kwargs)
        self.assertEqual(calls,0)
        return self.job(result)

    def test_actual_flow_separate_confirm_approve_and_duplicate_taps(self):
        summary = self.summary()
        row = self.quotes.list(self.scope())[0]
        self.assertEqual(row['snapshot']['itinerary'],self.active())
        self.assertEqual(row['projection']['totals']['total_minor'],25000)
        self.assertIsNone(row['approved_at'])
        self.assertIsNone(row['summary_confirmed_at'])
        self.assertTrue(self.send(summary['id']))
        quote = self.tap(summary,trigger='confirm-1')
        self.assertEqual(quote['stage'],'quote')
        self.assertEqual(self.tap(summary,trigger='confirm-1'),quote)
        self.assertIsNone(self.quotes.list(self.scope())[0]['approved_at'])
        self.assertTrue(self.send(quote['id']))
        self.assertEqual(self.posts[-2][1]['attachmentType'],'file')
        self.assertEqual(self.posts[-2][1]['attachmentUrl'],self.quotes.document_url(quote['quote_id']))
        approved = self.tap(quote,trigger='approve-1')
        self.assertEqual(approved['stage'],'approved')
        self.assertEqual(self.tap(quote,trigger='approve-1'),approved)
        self.assertEqual(self.tap(quote,trigger='approve-2'),approved)
        self.assertEqual(self.active()['status'],'draft')
        self.assertTrue(self.send(approved['id']))
        count = len(self.posts)
        self.assertTrue(self.send(approved['id']))
        self.assertEqual(count,len(self.posts))
        row = self.quotes.list(self.scope())[0]
        self.assertEqual(self.quotes.approved_snapshot(self.scope(),row['snapshot']['id']),row['snapshot'])
        self.assertEqual(row['status'],'approved')
        self.assertIsNone(row['delivered'])
        self.assertTrue(all(d['status']=='accepted' for d in row['deliveries']))

    def test_unaccepted_summary_and_quote_cannot_advance(self):
        summary=self.summary()
        with self.assertRaisesRegex(ItineraryError,'quote_not_fully_accepted'):
            self.quotes.act(self.scope(),'early',self.now.isoformat(),self.token(summary),'button_reply')
        self.send(summary['id'])
        quote=self.tap(summary)
        self.assertFalse(self.send(quote['id'],['rejected']))
        with self.assertRaisesRegex(ItineraryError,'quote_not_fully_accepted'):
            self.quotes.act(self.scope(),'early2',self.now.isoformat(),self.token(quote),'button_reply')
        self.assertEqual(self.quotes.list(self.scope())[0]['status'],'quote')

    def test_correction_creates_new_version_revokes_every_old_action_and_document(self):
        summary=self.summary();self.send(summary['id']);quote=self.tap(summary);self.send(quote['id']);approved=self.tap(quote)
        old=self.quotes.list(self.scope())[0]
        result,_=self.turn(conversation.response('update',[{'date':'2026-10-18'}],guest={'name':'Corrected Name'}))
        fresh=self.job(result)
        rows=self.quotes.list(self.scope())
        self.assertEqual([r['snapshot']['version'] for r in rows],[2,1])
        self.assertEqual(rows[1]['snapshot'],old['snapshot'])
        self.assertEqual(rows[1]['status'],'superseded')
        self.assertEqual(rows[0]['projection']['items'][0]['guest_name'],'Corrected Name')
        self.assertEqual(rows[0]['projection']['items'][0]['date'],'2026-10-18')
        for job in [summary,quote]:
            with self.assertRaisesRegex(ItineraryError,'stale_quote_action'):
                self.quotes.act(self.scope(),'old-'+job['id'],self.now.isoformat(),self.token(job),'button_reply')
        self.assertFalse(self.send(approved['id']))
        with self.assertRaises(ItineraryError): self.quotes.approved_snapshot(self.scope(),old['snapshot']['id'])
        with self.assertRaises(ItineraryError):self.quotes.document(old['snapshot']['id'])
        self.assertTrue(rows[0]['valid']);self.assertFalse(rows[1]['valid'])

    def test_question_is_not_approval_and_chat_language_is_separate(self):
        summary=self.summary();self.send(summary['id']);quote=self.tap(summary)
        before=self.quotes.list(self.scope())[0]
        result,calls=self.turn(conversation.response(question='Does lunch include drinks?',fact_keys=['inclusion_0'],language='nl'))
        self.assertEqual(calls,1)
        after=self.quotes.list(self.scope())[0]
        self.assertEqual(before['snapshot'],after['snapshot'])
        self.assertEqual(after['status'],'quote');self.assertIsNone(after['approved_at']);self.assertTrue(after['valid'])
        self.assertEqual(after['snapshot']['document_language'],'en')
        result,_=self.turn(conversation.response(document_language='de',language='nl'))
        self.assertEqual(self.job(result)['stage'],'summary')
        self.assertEqual(self.quotes.list(self.scope())[0]['snapshot']['document_language'],'de')

    def test_wrong_customer_plaintext_expired_and_duplicate_key_conflict(self):
        summary=self.summary();self.send(summary['id'])
        other=JourneyScope('mermaid',self.scope().account_id,self.scope().conversation_id,'different-person')
        with self.assertRaises(ItineraryError):self.quotes.act(other,'wrong',self.now.isoformat(),self.token(summary),'button_reply')
        with self.assertRaisesRegex(ItineraryError,'unverified_quote_action'):
            self.quotes.act(self.scope(),'plain',self.now.isoformat(),self.token(summary),None)
        self.quotes.act(self.scope(),'used',self.now.isoformat(),self.token(summary),'button_reply')
        with self.assertRaisesRegex(ItineraryError,'quote_request_conflict'):
            self.quotes.act(self.scope(),'used',self.now.isoformat(),'iq_wrong','button_reply')
        self.now += timedelta(hours=25)
        with self.assertRaisesRegex(ItineraryError,'stale_quote_action'):
            self.quotes.act(self.scope(),'expired',self.now.isoformat(),self.token(summary),'button_reply')

    def test_catalog_publication_revokes_actions_without_repricing_frozen_items(self):
        summary=self.summary();self.send(summary['id'])
        old=self.quotes.list(self.scope())[0]['snapshot']
        catalog=CatalogStore(self.catalog_path);snap=catalog.snapshot()
        catalog.publish([{'id':'fixture-cruise','changes':{'summary':'Changed source'}}],snap['revision'])
        with self.assertRaisesRegex(ItineraryError,'stale_quote_catalog'):
            self.quotes.act(self.scope(),'stale',self.now.isoformat(),self.token(summary),'button_reply')
        result,_=self.turn(conversation.response('summary'))
        self.assertEqual(self.job(result)['stage'],'summary')
        new=self.quotes.list(self.scope())[0]['snapshot']
        self.assertNotEqual(new['catalog_revision'],old['catalog_revision'])
        self.assertEqual(new['itinerary'],old['itinerary'])

    def test_ambiguous_and_claimed_delivery_never_retry_or_claim_delivered(self):
        summary=self.summary()
        self.assertFalse(self.send(summary['id'],['accepted','ambiguous']))
        count=len(self.posts)
        self.assertFalse(self.send(summary['id']));self.assertEqual(count,len(self.posts))
        states=self.quotes.list(self.scope())[0]['deliveries']
        self.assertEqual([d['status'] for d in states[:3]],['accepted','ambiguous','queued'])
        self.assertIsNone(self.quotes.list(self.scope())[0]['delivered'])
        with self.quotes.db() as db,db:
            db.execute("UPDATE isluno_quote_deliveries SET status='claimed' WHERE job_id=? AND part=1",(summary['id'],))
        self.assertFalse(self.send(summary['id']));self.assertEqual(count,len(self.posts))

    def test_real_sender_dispatch_and_public_document_auth_projection(self):
        from agents.social.senders.zernio import ZernioSender
        from agents.social import isluno_quote_delivery
        from fastapi import FastAPI,Header,HTTPException
        from fastapi.testclient import TestClient
        from dashboard.isluno_api import build_router
        summary=self.summary()
        def post(scope,body,key,guard=None):
            self.assertTrue(guard())
            return {'status':'accepted','provider_id':'fixture-provider'}
        with patch.object(isluno_quote_delivery,'QuoteStore',return_value=self.quotes),patch.object(isluno_quote_delivery,'post_once',side_effect=post),patch.object(isluno_quote_delivery.time,'sleep',side_effect=self.sleep),patch('agents.social.zernio_dm_client.whatsapp_customer_service_window',return_value={'open':True}):
            self.assertTrue(ZernioSender.send(self.scope().conversation_id,self.scope().account_id,'',summary['id'],'isluno_quote'))
            self.assertFalse(ZernioSender.send('wrong-conversation',self.scope().account_id,'',summary['id'],'isluno_quote'))
        quote=self.tap(summary)
        def auth(authorization: str=Header(default='')):
            if authorization!='fixture':raise HTTPException(401)
        app=FastAPI();app.include_router(build_public_router(lambda:self.quotes));app.include_router(build_router(auth,lambda:self.itinerary,lambda:self.store))
        with TestClient(app) as client:
            params={'account_id':self.scope().account_id,'conversation_id':self.scope().conversation_id,'customer_ref':self.scope().customer_ref}
            self.assertEqual(client.get('/isluno/quotes',params=params).status_code,401)
            response=client.get('/isluno/quotes',params=params,headers={'authorization':'fixture'})
            self.assertEqual(response.json()['quotes'],self.quotes.list(self.scope()))
            self.assertEqual(response.headers['cache-control'],'no-store')
            params['customer_ref']='wrong'
            self.assertEqual(client.get('/isluno/quotes',params=params,headers={'authorization':'fixture'}).json(),{'quotes':[]})
            pdf=client.get('/isluno/documents/'+quote['quote_id']+'.pdf')
            self.assertEqual(pdf.status_code,200);self.assertTrue(pdf.content.startswith(b'%PDF'))
            self.assertEqual(hashlib.sha256(pdf.content).hexdigest(),self.quotes.list(self.scope())[0]['pdf_sha256'])
            self.assertEqual(pdf.headers['cache-control'],'private, no-store')
            self.assertEqual(client.get('/isluno/documents/'+'0'*32+'.pdf').status_code,404)

    def test_missing_document_base_does_not_send_approval_button(self):
        summary=self.summary();self.send(summary['id']);quote=self.tap(summary)
        self.quotes.public_base=''
        count=len(self.posts)
        self.assertFalse(self.send(quote['id']));self.assertEqual(count,len(self.posts))
        self.assertTrue(all(d['status']=='queued' for d in self.quotes.list(self.scope())[0]['deliveries'] if d['job_id']==quote['id']))

    def test_immutable_pdf_and_snapshot_and_complete_long_summary(self):
        self.summary()
        row=self.quotes.list(self.scope())[0];snapshot=copy.deepcopy(row['snapshot'])
        snapshot['guest']['name']='Ana María José de Albuquerque ' * 7
        template=snapshot['itinerary']['items'][0]
        snapshot['itinerary']['items']=[{**copy.deepcopy(template),'id':'item-'+str(i),'product':{**template['product'],'name':('Very long excursion name ' * 15)+str(i)}} for i in range(20)]
        snapshot['itinerary']['totals']['total_minor']=500000
        for language in COPY:
            snapshot['document_language']=language
            pdf=render_pdf(snapshot)
            self.assertTrue(pdf.startswith(b'%PDF'));self.assertGreater(len(pdf),20000)
            text='\n'.join(text_sections(snapshot))
            self.assertIn('USD 5000.00',text)
            self.assertIn('20.',text)
        with self.quotes.db() as db, self.assertRaises(sqlite3.IntegrityError):
            db.execute('UPDATE isluno_quote_versions SET pdf=? WHERE id=?',(b'bad',row['snapshot']['id']))

    def test_reserved_old_understanding_cannot_overwrite_native_confirmation(self):
        summary=self.summary();self.send(summary['id'])
        baseline,_,_=self.store.reserve(self.scope(),'in-flight')
        decision=conversation.response('update',[{'date':'2026-10-20'}])
        self.store.record_decision(self.scope(),'in-flight',decision)
        self.tap(summary)
        with self.assertRaisesRegex(ItineraryError,'conversation_revision_changed'):
            self.store.apply(self.scope(),'in-flight',baseline,decision,'Change the date')
        self.assertEqual(self.quotes.list(self.scope())[0]['status'],'quote')

    def test_superseded_discovery_add_button_cannot_change_new_quote(self):
        self.initial()
        result,_=self.turn(conversation.response())
        plan=self.discovery.existing(self.scope(), 'unused')
        with self.discovery.db() as db:
            rows=db.execute('SELECT token FROM isluno_discovery_actions').fetchall()
        tokens=[r[0] for r in rows]
        self.turn(conversation.response('summary'))
        before=self.active()
        for token in tokens:
            result,calls=self.turn(token=token)
            self.assertEqual(result,'')
            self.assertEqual(calls,0)
        self.assertEqual(self.active(),before)

    def test_document_language_guest_only_and_incomplete_corrections_revoke(self):
        summary=self.summary()
        result,_=self.turn(conversation.response('update',[{'item_id':self.active()['items'][0]['id']}],guest={'name':'New guest'}))
        self.assertEqual(self.job(result)['stage'],'summary')
        rows=self.quotes.list(self.scope())
        self.assertEqual(rows[0]['projection']['guest']['name'],'New guest')
        self.assertEqual(rows[0]['projection']['items'][0]['guest_name'],'New guest')
        self.turn(conversation.response('update',[{'date':'invalid'}]))
        self.assertFalse(self.quotes.list(self.scope())[0]['valid'])
        self.assertEqual(self.quotes.list(self.scope())[0]['status'],'superseded')

    def test_closed_window_and_correction_during_send_stop_before_next_part(self):
        summary=self.summary()
        self.assertFalse(send_job(self.scope().conversation_id,self.scope().account_id,summary['id'],store=self.quotes,
            post=lambda *a:self.fail('closed window dispatched'),window=lambda *a:{'open':False},sleep=self.sleep))
        calls=[]
        def post(scope,body,key):
            calls.append(body)
            self.turn(conversation.response('update',[{'date':'2026-10-18'}]))
            return {'status':'accepted','provider_id':'accepted-before-correction'}
        self.assertFalse(send_job(self.scope().conversation_id,self.scope().account_id,summary['id'],store=self.quotes,post=post,
            window=lambda *a:{'open':True},sleep=self.sleep))
        self.assertEqual(len(calls),1)
        self.assertEqual(self.quotes.list(self.scope())[1]['deliveries'][0]['status'],'accepted')

    def test_multi_item_snapshot_keeps_different_guests_pickups_and_exact_prices(self):
        self.initial()
        self.turn(conversation.response('add',[{'product_id':'fixture-cruise','date':'2026-10-16','guest_ages':[31]}],{'name':'Second party'}))
        result,_=self.turn(conversation.response('summary'))
        items=self.quotes.list(self.scope())[0]['projection']['items']
        self.assertEqual([i['guest_name'] for i in items],['Calvin','Second party'])
        self.assertEqual([i['guest_ages'] for i in items],[[35,34,8],[31]])
        self.assertEqual([i['total_minor'] for i in items],[25000,10000])
        self.assertTrue(all(i['pickup_location']=='Synthetic pier' for i in items))

    def test_line_total_mismatch_cannot_render(self):
        self.summary();snapshot=self.quotes.list(self.scope())[0]['snapshot']
        snapshot['itinerary']['items'][0]['lines'][0]['amount_minor']+=1
        with self.assertRaisesRegex(ItineraryError,'quote_line_mismatch'):render_pdf(snapshot)

    def test_correction_replay_returns_same_quote_job_without_second_reply_plan(self):
        self.summary()
        decision=conversation.response('update',[{'date':'2026-10-18'}])
        first,calls=self.turn(decision,trigger='correction-replay')
        second,calls=self.turn(decision,trigger='correction-replay')
        self.assertEqual(first,second);self.assertEqual(calls,0)
        self.assertEqual(len(self.quotes.list(self.scope())),2)

    def test_mixed_unavailable_question_and_quote_correction_preserve_help(self):
        from agents.social.isluno_discovery import CLARIFICATIONS
        self.summary()
        result,_=self.turn(conversation.response('update',[{'date':'2026-10-18'}],question='Unknown safety condition?',fact_keys=[]))
        self.assertEqual(result['media']['type'],'isluno_discovery')
        self.assertIn(CLARIFICATIONS['en'][0],result['text'])
        self.assertIn('updated quote is ready',result['text'])
        rows=self.quotes.list(self.scope())
        self.assertEqual(len(rows),2);self.assertEqual(rows[0]['status'],'summary')
        self.assertTrue(all(d['status']=='queued' for d in rows[0]['deliveries']))
        with self.discovery.db() as db:
            plan=json.loads(db.execute('SELECT payload FROM isluno_discovery_plans WHERE id=?',(result['media']['url'],)).fetchone()[0])
        help_token=plan['body']['buttons'][0]['payload']
        response,_=self.turn(token=help_token)
        self.assertIn('operator review',response['text'])
        self.assertEqual(len(self.store.reviews(self.scope())),1)


    def test_targeted_guest_name_change_preserves_other_item_party(self):
        self.initial()
        first_id=self.active()['items'][0]['id']
        self.turn(conversation.response('add',[{'product_id':'fixture-cruise','date':'2026-10-16'}],{'name':'Second party'}))
        self.turn(conversation.response('summary'))
        self.turn(conversation.response('update',[{'item_id':first_id}],guest={'name':'First party corrected'}))
        items=self.quotes.list(self.scope())[0]['projection']['items']
        self.assertEqual([i['guest_name'] for i in items],['First party corrected','Second party'])
