"""Actual native payment, immutable artifacts, concurrency and durable recovery."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import BytesIO
import json
import sqlite3
import unittest
from unittest.mock import patch

import test_quotes as quote_tests
from test_conversation import response
from agents.social.isluno_payments import PaymentStore
from agents.social.isluno_quote_delivery import send_job
from agents.social.isluno_fulfillment import send_fulfillment,send_pending_email
from agents.social.mermaid_email_transport import EmailSendError
from shared.isluno_pricing import ItineraryError
from shared.isluno_config import JourneyScope
from shared.isluno_catalog import CatalogStore


class PaymentTests(unittest.TestCase):
    write_config=quote_tests.QuoteTests.write_config;write_profile=quote_tests.QuoteTests.write_profile;scope=quote_tests.QuoteTests.scope
    turn=quote_tests.QuoteTests.turn;active=quote_tests.QuoteTests.active;initial=quote_tests.QuoteTests.initial;summary=quote_tests.QuoteTests.summary
    send_quote=quote_tests.QuoteTests.send;send=quote_tests.QuoteTests.send;job=quote_tests.QuoteTests.job;tap=quote_tests.QuoteTests.tap;token=quote_tests.QuoteTests.token;sleep=quote_tests.QuoteTests.sleep

    def setUp(self):
        quote_tests.QuoteTests.setUp(self)
        self.payments=PaymentStore(self.store,'https://example.invalid/r/isluno/documents')

    def approved(self,multi=False):
        self.initial()
        if multi:self.turn(response('add',[{'product_id':'fixture-cruise','date':'2026-10-16'}],{'name':'Second Party'}))
        result,_=self.turn(response('summary'));summary=self.job(result);self.send_quote(summary['id'])
        quote=self.tap(summary);self.send_quote(quote['id']);approved=self.tap(quote);self.send_quote(approved['id'])
        return approved

    def pay(self,approved=None):
        approved=approved or self.approved()
        result,calls=self.turn(token=self.token(approved))
        self.assertEqual(calls,0);self.assertEqual(result['media']['type'],'isluno_fulfillment')
        return self.payments.job(result['media']['url'],self.scope().account_id,self.scope().conversation_id)

    def deliver(self,job,states=None):
        def post(scope,body,key):
            self.posts.append((scope,body,key))
            status=states.pop(0) if states else 'accepted'
            return {'status':status,'provider_id':'fake-'+str(len(self.posts)) if status=='accepted' else None}
        return send_job(self.scope().conversation_id,self.scope().account_id,job['id'],store=self.payments,
                        post=post,window=lambda *a:{'open':True},sleep=self.sleep)

    def test_actual_payment_receipt_and_unique_ticket_per_item(self):
        from pypdf import PdfReader
        job=self.pay(self.approved(multi=True));row=self.payments.records(self.scope())[0]
        self.assertEqual(self.active()['status'],'demo_paid')
        self.assertEqual(len(row['documents']),3)
        paid=row['snapshot'];self.assertFalse(paid['real_money_charged']);self.assertFalse(paid['supplier_booking_made'])
        self.assertEqual(paid['itinerary']['totals']['total_minor'],50000)
        self.assertEqual(len(set(paid['ticket_ids'].values())),2)
        for document in row['documents']:
            raw=self.payments.document(document['id'])
            text='\n'.join(p.extract_text() for p in PdfReader(BytesIO(raw)).pages)
            self.assertIn('No real money',text);self.assertIn('No supplier booking',text)
            self.assertIn('USD 500.00' if document['kind']=='receipt' else 'USD 250.00',text)
            if document['kind']=='ticket':
                self.assertIn(paid['ticket_ids'][document['item_id']],text)
                if paid['item_details'][document['item_id']]['guest_name']=='Calvin':
                    self.assertNotIn('Second Party',text)
        self.assertTrue(self.deliver(job));count=len(self.posts)
        self.assertTrue(self.deliver(job));self.assertEqual(count,len(self.posts))
        self.assertTrue(all(d['status']=='accepted' for d in self.payments.records(self.scope())[0]['deliveries']))

    def test_duplicate_and_concurrent_taps_commit_once(self):
        approved=self.approved();token=self.token(approved)
        def complete(index):return self.payments.complete(self.scope(),'concurrent-'+str(index),self.now.isoformat(),token,'button_reply')
        with ThreadPoolExecutor(max_workers=2) as pool:jobs=list(pool.map(complete,range(2)))
        self.assertEqual(jobs[0],jobs[1]);self.assertEqual(len(self.payments.records(self.scope())),1)
        self.assertEqual(complete(0),jobs[0])
        with self.payments.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM isluno_paid_documents').fetchone()[0],2)
        revision=self.active()['revision']
        self.payments.complete(self.scope(),'another-tap',self.now.isoformat(),token,'button_reply')
        self.assertEqual(revision,self.active()['revision'])

    def test_wrong_customer_plaintext_expiry_and_corrected_actions_fail_closed(self):
        approved=self.approved();token=self.token(approved)
        before=self.active()
        self.turn(response(question='I paid already'))
        self.assertEqual(self.active(),before)
        other=JourneyScope('mermaid',self.scope().account_id,self.scope().conversation_id,'wrong')
        for scope,kind in [(other,'button_reply'),(self.scope(),None)]:
            with self.assertRaises(ItineraryError):self.payments.complete(scope,'bad',self.now.isoformat(),token,kind)
        self.turn(response('update',[{'date':'2026-10-18'}]))
        with self.assertRaises(ItineraryError):self.payments.complete(self.scope(),'stale',self.now.isoformat(),token,'button_reply')
        self.assertEqual(self.payments.records(self.scope()),[])
        self.now+=timedelta(hours=2)
        with self.assertRaisesRegex(ItineraryError,'expired_payment_action'):
            self.payments.complete(self.scope(),'expired',self.now.isoformat(),token,'button_reply')

    def test_catalog_change_after_mint_blocks_payment(self):
        approved=self.approved();catalog=CatalogStore(self.catalog_path);snap=catalog.snapshot()
        catalog.publish([{'id':'fixture-cruise','changes':{'summary':'Changed'}}],snap['revision'])
        with self.assertRaisesRegex(ItineraryError,'stale_quote_catalog'):
            self.payments.complete(self.scope(),'stale',self.now.isoformat(),self.token(approved),'button_reply')
        self.assertEqual(self.payments.records(self.scope()),[])

    def test_restart_partial_delivery_skips_accepted_and_ambiguity_never_resends(self):
        job=self.pay(self.approved(multi=True));self.assertFalse(self.deliver(job,['accepted','accepted','ambiguous']))
        count=len(self.posts)
        restarted=PaymentStore(self.store,self.payments.public_base)
        self.now+=timedelta(days=1)
        resumed=restarted.resume(self.scope(),self.now.isoformat())
        self.assertFalse(send_job(self.scope().conversation_id,self.scope().account_id,resumed['id'],store=restarted,
            post=lambda *a:self.fail('ambiguous part was resent'),window=lambda *a:{'open':True},sleep=self.sleep))
        self.assertEqual(count,len(self.posts));self.assertEqual(len(restarted.records(self.scope())),1)
        self.assertFalse(send_fulfillment(self.scope().conversation_id,self.scope().account_id,job['id'],store=restarted,dispatch=lambda *a,**kw:False))
        self.assertEqual(self.store.reviews(self.scope())[0]['reason'],'demo_fulfillment_review')

    def test_restart_after_accepted_part_before_next_claim_resumes_remaining(self):
        job=self.pay(self.approved(multi=True))
        with self.payments.db() as db,db:
            db.execute("UPDATE isluno_quote_deliveries SET status='accepted',provider_id='already-accepted' WHERE job_id=? AND part=0",(job['id'],))
        self.payments=PaymentStore(self.store,self.payments.public_base)
        count=len(self.posts);self.assertTrue(self.deliver(self.payments.resume(self.scope(),self.now.isoformat())))
        self.assertEqual(len(self.posts)-count,len(job['parts'])-1)

    def test_renderer_failure_rolls_back_payment_and_itinerary_atomically(self):
        approved=self.approved();before=self.active()
        with patch('agents.social.isluno_payments.render_pdf',side_effect=RuntimeError('synthetic renderer crash')):
            with self.assertRaises(RuntimeError):self.payments.complete(self.scope(),'crash',self.now.isoformat(),self.token(approved),'button_reply')
        self.assertEqual(self.active(),before);self.assertEqual(self.payments.records(self.scope()),[])
        self.assertEqual(self.pay(approved)['stage'],'fulfilled')

    def test_paid_snapshot_immutable_and_post_payment_edits_route_to_operator(self):
        self.pay();before=self.payments.records(self.scope())[0]['snapshot']
        result,_=self.turn(response('update',[{'date':'2026-10-18'}]))
        self.assertIn('operator review',result['text'])
        self.assertEqual(self.payments.records(self.scope())[0]['snapshot'],before)
        with self.payments.db() as db,self.assertRaises(sqlite3.IntegrityError):
            db.execute("UPDATE isluno_payments SET snapshot='{}'")

    def email_proposal(self,address='guest@example.invalid'):
        decision=response('email');decision['booking']['email_address']=address
        result,_=self.turn(decision)
        return self.payments.job(result['media']['url'],self.scope().account_id,self.scope().conversation_id)

    def test_email_requires_exact_address_native_consent_and_cannot_block_whatsapp(self):
        job=self.pay();proposal=self.email_proposal()
        calls=[]
        self.assertTrue(send_pending_email(self.scope(),store=self.payments,transport=lambda *a,**k:calls.append(a),guard=lambda:True))
        self.assertEqual(calls,[])
        token=proposal['parts'][0]['buttons'][0]['payload']
        result,_=self.turn(token=token);result2,_=self.turn(token=token)
        self.assertEqual(result,result2)
        def failed(*a,**k):raise EmailSendError('delivery_uncertain',uncertain=True)
        self.assertFalse(send_pending_email(self.scope(),store=self.payments,transport=failed,guard=lambda:True))
        self.assertTrue(self.deliver(job))
        self.assertTrue(send_pending_email(self.scope(),store=self.payments,transport=lambda *a,**k:self.fail('ambiguous email resent'),guard=lambda:True))
        emails=self.payments.records(self.scope())[0]['emails'];self.assertEqual(len(emails),1);self.assertEqual(emails[0]['status'],'ambiguous')

    def test_email_address_change_revokes_old_consent_and_acceptance_is_idempotent(self):
        self.pay();old=self.email_proposal('old@example.invalid');new=self.email_proposal('new@example.invalid')
        with self.assertRaises(ItineraryError):
            self.payments.consent_email(self.scope(),'old',self.now.isoformat(),old['parts'][0]['buttons'][0]['payload'],'button_reply')
        self.turn(token=new['parts'][0]['buttons'][0]['payload'])
        captured=[]
        self.assertTrue(send_pending_email(self.scope(),store=self.payments,transport=lambda *a,**k:captured.append((a,k)),guard=lambda:True))
        self.assertEqual(captured[0][0][0],'new@example.invalid')
        self.assertEqual(captured[0][0][1]['sender_display_name'],'TRACY | Isluno')
        self.assertTrue(send_pending_email(self.scope(),store=self.payments,transport=lambda *a,**k:self.fail('accepted email resent'),guard=lambda:True))

    def test_actual_sender_public_paid_documents_and_operator_projection(self):
        from agents.social.senders.zernio import ZernioSender
        from agents.social import isluno_fulfillment
        from agents.social.isluno_quotes import build_public_router
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from dashboard.isluno_api import build_router
        job=self.pay()
        with patch.object(isluno_fulfillment,'PaymentStore',return_value=self.payments),patch.object(isluno_fulfillment,'send_job',side_effect=lambda *a,**kw:self.deliver(job)):
            self.assertTrue(ZernioSender.send(self.scope().conversation_id,self.scope().account_id,'',job['id'],'isluno_fulfillment'))
        app=FastAPI();app.include_router(build_public_router(lambda:self.quotes));app.include_router(build_router(lambda:True,lambda:self.itinerary,lambda:self.store))
        with TestClient(app) as client:
            row=self.payments.records(self.scope())[0]
            for document in row['documents']:
                self.assertEqual(client.get('/isluno/documents/'+document['id']+'.pdf').content,self.payments.document(document['id']))
            params={'account_id':self.scope().account_id,'conversation_id':self.scope().conversation_id,'customer_ref':self.scope().customer_ref}
            self.assertEqual(client.get('/isluno/payments',params=params).json(),{'payments':self.payments.records(self.scope())})

    def test_email_missing_address_replay_and_revoked_queued_recipient(self):
        self.pay()
        first,_=self.turn(response('email'),trigger='email-ask')
        second,_=self.turn(response('email'),trigger='email-ask')
        self.assertEqual(first,second);self.assertIn('provide the email address',first['text'])
        old=self.email_proposal('old@example.invalid')
        self.turn(token=old['parts'][0]['buttons'][0]['payload'])
        self.email_proposal('new@example.invalid')
        self.assertEqual(self.payments.records(self.scope())[0]['emails'][0]['status'],'cancelled')
        self.assertTrue(send_pending_email(self.scope(),store=self.payments,transport=lambda *a,**k:self.fail('revoked recipient sent'),guard=lambda:True))

    def test_smtp_mime_uses_isluno_brand_and_preserves_legacy_default(self):
        from agents.social import mermaid_email_transport as transport
        self.pay();row=self.payments.records(self.scope())[0]
        raw=self.payments.document(next(d['id'] for d in row['documents'] if d['kind']=='receipt'))
        content={'subject':'Isluno demo receipt','text':'DEMO - no real money or supplier booking','html':'<p>DEMO</p>','sender_display_name':'TRACY | Isluno'}
        message=transport._message('sender@example.invalid','guest@example.invalid',content,('receipt.pdf',raw),'<fixture@example.invalid>')
        self.assertIn('Isluno',str(message['From']));self.assertEqual(len(list(message.iter_attachments())),1)
        content.pop('sender_display_name')
        with patch.object(transport,'_display_name',return_value='Original Mermaid'):
            legacy=transport._message('sender@example.invalid','guest@example.invalid',content,('receipt.pdf',raw),'<fixture@example.invalid>')
        self.assertIn('Original Mermaid',str(legacy['From']))

    def test_claimed_email_after_restart_does_not_blind_retry(self):
        self.pay();proposal=self.email_proposal();self.turn(token=proposal['parts'][0]['buttons'][0]['payload'])
        with self.payments.db() as db,db:db.execute("UPDATE isluno_paid_emails SET status='claimed'")
        self.assertTrue(send_pending_email(self.scope(),store=PaymentStore(self.store,self.payments.public_base),transport=lambda *a,**k:self.fail('claimed email retried'),guard=lambda:True))
        self.assertEqual(self.payments.records(self.scope())[0]['emails'][0]['status'],'claimed')

    def test_takeover_during_payment_preparation_rolls_back_all_changes(self):
        approved=self.approved();before=self.active()
        with patch('agents.social.zernio_dm_client._provider_mutation_account_allowed',side_effect=[True,False]):
            with self.assertRaisesRegex(ItineraryError,'payment_automation_paused'):
                self.payments.complete(self.scope(),'paused',self.now.isoformat(),self.token(approved),'button_reply')
        self.assertEqual(self.active(),before);self.assertEqual(self.payments.records(self.scope()),[])

    def test_email_async_scheduling_does_not_delay_or_change_whatsapp_result(self):
        self.pay();proposal=self.email_proposal();result,_=self.turn(token=proposal['parts'][0]['buttons'][0]['payload'])
        scheduled=[]
        self.assertTrue(send_fulfillment(self.scope().conversation_id,self.scope().account_id,result['media']['url'],store=self.payments,
            dispatch=lambda *a,**kw:True,schedule_email=lambda scope:scheduled.append(scope)))
        self.assertEqual(scheduled,[self.scope()])
        self.assertEqual(self.payments.records(self.scope())[0]['emails'][0]['status'],'queued')

    def test_email_own_claim_uses_fresh_controls_after_inbound_lease_finishes(self):
        from agents.social.isluno_fulfillment import email_guard
        from agents.social import zernio_dm_client
        from shared import state_registry,icp_overrides
        self.pay();proposal=self.email_proposal();self.turn(token=proposal['parts'][0]['buttons'][0]['payload'])
        with patch.object(state_registry,'get_blocked',return_value=False),patch.object(state_registry,'get_ai_muted',return_value=False),patch.object(icp_overrides,'fetch_overrides_fresh',return_value={}),patch.object(icp_overrides,'auto_reply_state',return_value=True),patch.object(icp_overrides,'whatsapp_inbox_state',return_value=True),patch.object(zernio_dm_client,'_provider_account_allowed',return_value=True):
            # Finished inbound workers may not send; the separate email claim can.
            token=zernio_dm_client.set_provider_mutation_guard(lambda:False)
            try:
                self.assertTrue(email_guard(self.scope()))
                self.assertTrue(send_pending_email(self.scope(),store=self.payments,transport=lambda *a,**k:None))
            finally:zernio_dm_client.reset_provider_mutation_guard(token)
            with patch.object(state_registry,'get_ai_muted',return_value=True):
                self.assertFalse(email_guard(self.scope()))

    def test_ticket_ids_and_receipt_are_database_unique(self):
        self.pay()
        with self.payments.db() as db:
            row=db.execute("SELECT * FROM isluno_paid_documents WHERE kind='ticket'").fetchone()
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute('INSERT INTO isluno_paid_documents VALUES(?,?,?,?,?,?,?,?)',('other-document',row['payment_id'],'ticket','other-item',row['pdf'],row['sha256'],row['expires_at'],row['ticket_id']))

    def test_expired_native_payment_tap_refreshes_prompt_but_does_not_pay(self):
        approved=self.approved();self.now+=timedelta(hours=2)
        result,calls=self.turn(token=self.token(approved),trigger='expired-native')
        self.assertEqual(calls,0);self.assertEqual(result['media']['type'],'isluno_quote')
        refreshed=self.job(result)
        self.assertNotEqual(self.token(refreshed),self.token(approved))
        self.assertEqual(self.payments.records(self.scope()),[])
        again,_=self.turn(token=self.token(approved),trigger='expired-native')
        self.assertEqual(again,result)
        repeated,_=self.turn(token=self.token(approved),trigger='expired-native-again')
        self.assertEqual(repeated,result)
        self.send_quote(refreshed['id']);self.pay(refreshed)
        self.assertEqual(len(self.payments.records(self.scope())),1)
