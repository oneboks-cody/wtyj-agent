"""One mocked model request through the real Marina and social caller; no network."""
import copy
from contextlib import ExitStack
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_conversation import ConversationTests, response, NOW
from agents.social.channels.whatsapp_zernio import WhatsAppZernioChannel
from shared.isluno_pricing import ItineraryError


class NoReplyTests(unittest.TestCase):
    def setUp(self):
        self.t=ConversationTests('test_conversation_sdk_uses_single_existing_model_request')
        self.t.setUp();self.addCleanup(self.t.doCleanups)

    def call(self, decision, trigger, *, scope=None, webhook=False, text="Synthetic request", interactive_id=""):
        from agents.marina import marina_agent
        from agents.social import social_agent,isluno_conversation
        scope=scope or self.t.scope()
        from hospitality_fixtures import hospitality, operation_replies
        decision = copy.deepcopy(decision)
        decision.setdefault('hospitality', hospitality('Happy to help you explore.', action=decision['booking']['action'],
                            evidence='Synthetic request' if decision['booking']['action'] != 'none' else '', replies=operation_replies()))
        message=WhatsAppZernioChannel.from_zernio({'conversation_id':scope.conversation_id,
            'account_id':scope.account_id,'sender_id':scope.customer_ref,'message_id':trigger,
            'channel':'whatsapp','text':text,'sent_at':NOW.isoformat(),'interactive_id':interactive_id,'interactive_type':'button_reply' if interactive_id else ''})
        with ExitStack() as stack:
            for obj,name,value in [(social_agent.state_registry,'match_ignored_contact',None),
                                    (social_agent.auto_block,'evaluate_inbound',{}),
                                    (isluno_conversation,'ConversationStore',self.t.store),
                                    (isluno_conversation,'DiscoveryStore',self.t.discovery)]:
                stack.enter_context(patch.object(obj,name,return_value=value))
            stack.enter_context(patch.dict(os.environ,{'ANTHROPIC_API_KEY':'synthetic-no-network'}))
            logs=stack.enter_context(patch.object(marina_agent.bm_logger,'log'))
            sdk=stack.enter_context(patch.object(marina_agent.anthropic,'Anthropic'))
            sdk.return_value.messages.create.return_value=SimpleNamespace(content=[SimpleNamespace(type='tool_use',input=copy.deepcopy(decision))],usage=None)
            if webhook:
                from agents.social import webhook_server
                from shared import state_registry
                stack.enter_context(patch.object(state_registry,'DB_PATH',str(self.t.itinerary.db_path)))
                stack.enter_context(patch.object(webhook_server.icp_overrides,'fetch_overrides_fresh',return_value={
                    'available':True,'feature_toggles':{'ai_auto_reply':{'value':True},'whatsapp_inbox':{'value':True}}}))
                sent=stack.enter_context(patch.object(webhook_server,'send_reply',return_value=True))
                self.assertTrue(state_registry.wa_claim_inbound_processing(trigger,scope.conversation_id,'whatsapp',payload=message))
                webhook_server._buffer_message(message)
                with webhook_server._buffer_lock:
                    webhook_server._message_buffers[scope.conversation_id]['timer'].cancel()
                webhook_server._flush_buffer(scope.conversation_id)
                self.assertFalse(state_registry.wa_claim_inbound_processing(trigger,scope.conversation_id,'whatsapp',payload=message))
                self.assertEqual(sent.call_count,1)
                result=sent.call_args
            else:
                result=social_agent.handle_incoming_whatsapp_message(message,include_media=True)
            return result,sdk.return_value.messages.create.call_count,logs.call_args_list

    def test_invalid_fact_shapes_acknowledged_once_without_applying_or_retrying(self):
        from agents.social.isluno_recovery_copy import PROCESSING_FAILED
        self.t.initial();before=self.t.active()
        for index,keys in enumerate([None,{},'summary',[{}],['summary']*6]):
            with self.subTest(shape=type(keys).__name__):
                decision=response('update',[{'date':'2026-10-20'}]);decision['fact_keys']=keys
                trigger='invalid-'+str(index)
                reply,calls,logs=self.call(decision,trigger)
                self.assertEqual(reply['text'],PROCESSING_FAILED['en']);self.assertTrue(reply['generation_failed'])
                self.assertEqual(calls,1);self.assertEqual(self.t.active(),before)
                diagnostic=next(c.kwargs for c in logs if c.args==('isluno_model_contract_failed',))
                self.assertEqual(diagnostic['code'],'invalid_discovery_facts')
                self.assertEqual(diagnostic['fact_keys_type'],type(keys).__name__)
                self.assertLessEqual(set(diagnostic),{'code','channel','fact_keys_type','fact_keys_count','fact_keys_nonstring_count'})
                duplicate,calls,_=self.call(response(),trigger)
                self.assertEqual(duplicate,'');self.assertEqual(calls,0)
                with self.t.store.db() as db:
                    self.assertEqual(tuple(db.execute('SELECT decision,outcome FROM isluno_conversation_turns WHERE trigger_id=?',(trigger,)).fetchone()),(None,None))
                    incident=db.execute('SELECT status,code FROM isluno_recovery_incidents WHERE trigger_id=?',(trigger,)).fetchone()
                    self.assertEqual(tuple(incident),('operator_review','invalid_discovery_facts'))
        fresh,calls,_=self.call(response(),'fresh-valid')
        self.assertTrue(fresh['text']);self.assertNotIn('generation_failed',fresh);self.assertEqual(calls,1)

    def test_unknown_source_fact_remains_rejected_and_never_rendered(self):
        reply,calls,logs=self.call(response(fact_keys=['invented-secret-price']),'unknown-fact')
        self.assertTrue(reply['generation_failed']);self.assertNotIn('invented',reply['text']);self.assertEqual(calls,1)
        self.assertTrue(any(c.kwargs.get('code')=='unknown_discovery_fact' for c in logs))

    def test_scope_rejection_does_not_send_failure_ack_or_call_model(self):
        from dataclasses import replace
        reply,calls,_=self.call(response(),'wrong-account',scope=replace(self.t.scope(),account_id='other-account'))
        self.assertEqual(reply,'');self.assertEqual(calls,0)

    def test_real_webhook_sends_failure_text_once_and_deduplicates_inbound(self):
        from agents.social.isluno_recovery_copy import PROCESSING_FAILED
        decision=response();decision['fact_keys']={}
        sent,calls,_=self.call(decision,'webhook-failure',webhook=True)
        self.assertEqual(calls,1)
        self.assertIn(PROCESSING_FAILED['en'],str(sent))
