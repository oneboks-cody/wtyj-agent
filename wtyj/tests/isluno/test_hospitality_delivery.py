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
            self.assertEqual(reply,{'text':RESPONSE_FAILED['en'],'generation_failed':True})
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
