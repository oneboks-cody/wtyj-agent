"""Independent failure/gating paths with local stores and stubbed transports."""
import copy
import sqlite3
import unittest
from unittest.mock import patch

import test_conversation as fixtures
from agents.social.isluno_conversation import failure_reply
from agents.social.isluno_quotes import QuoteStore
from agents.social.isluno_quote_delivery import send_job
from shared.isluno_pricing import ItineraryError


class HospitalityDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.t=fixtures.ConversationTests('test_conversation_sdk_uses_single_existing_model_request')
        self.t.setUp();self.addCleanup(self.t.doCleanups)
        self.now=fixtures.NOW
        self.t.itinerary.clock=lambda:self.now
        self.t.discovery.clock=lambda:self.now

    def test_storage_failure_returns_acknowledgement_without_scope_bypass(self):
        from dataclasses import replace
        from agents.social.isluno_recovery_copy import RESPONSE_FAILED
        with patch.object(self.t.store,'db',side_effect=sqlite3.OperationalError('synthetic storage unavailable')):
            reply=failure_reply(self.t.store,self.t.discovery,self.t.scope(),'claimed-message',{'text':'Synthetic','_zernio_sent_at':self.now.isoformat()})
            self.assertEqual(reply['text'],RESPONSE_FAILED['en']);self.assertTrue(reply['generation_failed'])
            self.assertEqual(reply['media']['type'],'isluno_discovery')
            plan,_=self.t.discovery.delivery_plan(reply['media']['url'],self.t.scope().account_id,self.t.scope().conversation_id)
            self.assertTrue(plan['operational_notice'])
            with self.assertRaises(PermissionError):
                failure_reply(self.t.store,self.t.discovery,replace(self.t.scope(),account_id='wrong-account'),'claimed-message',{})

    def test_incident_failure_does_not_mask_claimed_understanding_failure(self):
        from test_no_reply import NoReplyTests
        from agents.social.isluno_recovery import RecoveryStore
        runner=NoReplyTests('test_unknown_source_fact_remains_rejected_and_never_rendered')
        runner.t=self.t
        decision=fixtures.response();decision['fact_keys']={}
        with patch.object(RecoveryStore,'incident',side_effect=sqlite3.OperationalError('synthetic incident failure')):
            reply,calls,_=runner.call(decision,'claimed-failed-model')
        self.assertEqual(calls,1);self.assertTrue(reply['text']);self.assertTrue(reply['generation_failed'])
        duplicate,calls,_=runner.call(fixtures.response(),'claimed-failed-model')
        self.assertEqual(calls,0);self.assertEqual(duplicate,'')

    def test_partial_original_quote_never_replays_or_unlocks_approval(self):
        from datetime import timedelta
        self.t.initial()
        scope=self.t.scope();quotes=QuoteStore(self.t.store,'https://example.invalid/documents')
        original=quotes.prepare(scope,'initial-review',self.now.isoformat(),expected_session_revision=self.t.store.session(scope)['revision'])
        self.assertGreaterEqual(len(original['parts']),2)
        base={'language':'en','product_ids':[],'fact_keys':[],'intent':'discover','question':''}
        def answer(trigger):
            return self.t.discovery.plan(scope,trigger,self.now.isoformat(),base,response_text='Synthetic grounded answer.')
        first=quotes.compose_answer(scope,'review-one',self.now.isoformat(),original,answer('answer-one'))
        calls=[];states=['accepted','accepted','ambiguous']
        def post(actual,body,key):
            self.assertEqual(actual,scope);calls.append(key)
            state=states.pop(0)
            return {'status':state,'provider_id':'synthetic' if state=='accepted' else None}
        def sleep(seconds):self.now+=timedelta(seconds=seconds)
        self.assertFalse(send_job(scope.conversation_id,scope.account_id,first['id'],store=quotes,post=post,window=lambda *a:{'open':True},sleep=sleep))
        token=original['parts'][-1]['buttons'][0]['payload']
        with self.assertRaisesRegex(ItineraryError,'quote_not_fully_accepted'):
            quotes.act(scope,'premature-approval',self.now.isoformat(),token,'button_reply')
        second=quotes.compose_answer(scope,'review-two',self.now.isoformat(),first,answer('answer-two'))
        self.assertEqual(second['followup_job_id'],original['id'])
        count=len(calls);states[:]=['accepted']
        self.assertFalse(send_job(scope.conversation_id,scope.account_id,second['id'],store=quotes,post=post,window=lambda *a:{'open':True},sleep=sleep))
        self.assertEqual(len(calls)-count,1)  # Only the new answer, never the ambiguous original.
        with self.assertRaisesRegex(ItineraryError,'quote_not_fully_accepted'):
            quotes.act(scope,'still-no-approval',self.now.isoformat(),token,'button_reply')
        with quotes.db() as db:
            rows=db.execute('SELECT status FROM isluno_quote_deliveries WHERE job_id=? ORDER BY part',(original['id'],)).fetchall()
            self.assertEqual([r[0] for r in rows[:2]],['accepted','ambiguous'])

    def test_accepted_statement_preserves_last_actual_question(self):
        from agents.social.isluno_hospitality import record_delivery
        self.t.initial();scope=self.t.scope()
        with self.t.store.db() as db,db:
            record_delivery(db,scope,'question',{'message':'Which day suits you?'},'accepted',question='Which day suits you?')
            record_delivery(db,scope,'statement',{'message':'Synthetic detail.'},'accepted')
            record_delivery(db,scope,'uncertain-question',{'message':'A different question?'},'ambiguous',question='A different question?')
        session=self.t.store.session(scope)
        self.assertEqual(session['last_accepted_question'],'Which day suits you?')
        self.assertEqual(session['history'][-1]['delivery_status'],'ambiguous')
        self.assertFalse(session['history'][-1]['guest_receipt_verified'])

    def test_late_failure_of_answer_blocks_composed_quote_approval(self):
        from datetime import timedelta
        from agents.social.isluno_callbacks import accept
        self.t.initial();scope=self.t.scope();quotes=QuoteStore(self.t.store,'https://example.invalid/documents')
        original=quotes.prepare(scope,'review',self.now.isoformat(),expected_session_revision=self.t.store.session(scope)['revision'])
        base={'language':'en','product_ids':[],'fact_keys':[],'intent':'discover','question':''}
        answer=self.t.discovery.plan(scope,'answer',self.now.isoformat(),base,response_text='A source-backed answer.')
        composed=quotes.compose_answer(scope,'composition',self.now.isoformat(),original,answer)
        calls=[]
        def post(*args):
            calls.append(args);return {'status':'accepted','provider_id':'composed-'+str(len(calls))}
        self.assertTrue(send_job(scope.conversation_id,scope.account_id,composed['id'],store=quotes,post=post,window=lambda *a:{'open':True},sleep=lambda seconds:setattr(self,'now',self.now+timedelta(seconds=seconds))))
        with patch('shared.tenant_guard.account_access_state',return_value=True):
            self.assertTrue(accept({'event':'message.failed','message':{'id':'composed-1','accountId':scope.account_id,'conversationId':scope.conversation_id}},store=self.t.store))
        token=original['parts'][-1]['buttons'][0]['payload']
        with self.assertRaisesRegex(ItineraryError,'quote_not_fully_accepted'):
            quotes.act(scope,'no-approval-after-failed-answer',self.now.isoformat(),token,'button_reply')

    def test_stale_quote_hold_does_not_erase_an_accepted_part(self):
        from agents.social.isluno_quote_delivery import _save
        self.t.initial();scope=self.t.scope();quotes=QuoteStore(self.t.store,'https://example.invalid/documents')
        job=quotes.prepare(scope,'race-review',self.now.isoformat(),expected_session_revision=self.t.store.session(scope)['revision'])
        with quotes.db() as db,db:
            db.execute("UPDATE isluno_quote_deliveries SET status='accepted',provider_id='owned-send' WHERE job_id=? AND part=0",(job['id'],))
        self.assertEqual(_save(quotes,scope,job,0,{'status':'window_closed','dispatched':False}),'accepted')
        with quotes.db() as db:
            row=db.execute('SELECT status,provider_id FROM isluno_quote_deliveries WHERE job_id=? AND part=0',(job['id'],)).fetchone()
            self.assertEqual(tuple(row),('accepted','owned-send'))
