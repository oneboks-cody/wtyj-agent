"""Fresh contact versus delivery-proven continuation through real callers, no network."""
import copy
import unittest
from unittest.mock import patch
from agents.marina import marina_agent
from agents.social import isluno_conversation_understanding as understanding
from agents.social import isluno_hospitality
from test_communication_wire import CommunicationWireTests
from test_conversation import response
from hospitality_fixtures import hospitality

class FirstContactTests(unittest.TestCase):
    def setUp(self):
        self.w=CommunicationWireTests('test_observed_lengths_complete_through_real_sender')
        self.w.setUp();self.addCleanup(self.w.doCleanups)
        self.t=self.w.t

    def call(self,trigger='fresh',text='Hello, I will be on Curaçao for a month.'):
        decision=response(products=[],fact_keys=[])
        decision['hospitality']=hospitality('Welcome! A month on Curaçao gives you time to explore at your own pace. 🌴',
                                            'Who are you travelling with?',stage='welcome')
        with patch.object(marina_agent,'process_message',wraps=marina_agent.process_message) as real:
            reply,calls,_=self.w.h.call(decision,trigger,text=text)
            inputs=copy.deepcopy(real.call_args.kwargs)
        self.assertEqual(calls,1);self.assertNotIn('generation_failed',reply)
        return reply,inputs

    def test_fresh_context_warm_question_and_optional_emoji_reach_wire(self):
        reply,inputs=self.call()
        self.assertEqual(inputs['thread_fields']['delivery_context'],{'confirmed_message_ids':[],'prior_guest_turns':0})
        self.assertEqual(inputs['messages'],[])
        self.assertTrue(self.w.send(reply))
        sent=''.join(x['message'] for x in self.w.requests)
        self.assertIn('🌴',sent);self.assertEqual(sent.count('?'),1)
        self.assertNotIn('—',sent);self.assertNotIn('–',sent)
        self.assertFalse(self.t.store.session(self.t.scope())['active_itinerary_id'])
        self.assertIn('Ask activity preferences on the next turn',isluno_hospitality.PROMPT)

    def test_accepted_is_not_delivered_and_foreign_callback_is_not_proof(self):
        first,_=self.call('one');self.assertTrue(self.w.send(first))
        self.assertFalse(self.w.callback('message.delivered','provider-1',account='different-account'))
        _,inputs=self.call('two','Hello again')
        self.assertEqual(inputs['thread_fields']['delivery_context']['confirmed_message_ids'],[])
        self.assertTrue(any(h.get('delivery_status')=='accepted' for h in inputs['messages']))
        self.assertIn('acceptance without a delivered/read callback',isluno_hospitality.PROMPT)

    def test_confirmed_continuation_and_late_failure_remove_sharing_authority(self):
        first,_=self.call('one');self.assertTrue(self.w.send(first))
        self.assertTrue(self.w.callback('message.delivered','provider-1'))
        _,inputs=self.call('two','Hello again')
        confirmed=inputs['thread_fields']['delivery_context']['confirmed_message_ids']
        self.assertEqual(len(confirmed),1)
        actual=next(h for h in inputs['messages'] if h.get('delivery_id')==confirmed[0])
        self.assertEqual(actual['provider_delivery_status'],'delivered');self.assertIn('Welcome!',actual['content'])
        self.assertTrue(self.w.callback('message.failed','provider-1'))
        _,after=self.call('three','Please try again')
        self.assertEqual(after['thread_fields']['delivery_context']['confirmed_message_ids'],[])
        failed=next(h for h in after['messages'] if h.get('delivery_id')==confirmed[0])
        self.assertEqual(failed['delivery_status'],'provider_failed');self.assertNotIn('content',failed)

    def test_rejected_content_and_legacy_plans_never_become_dialogue(self):
        first,_=self.call('one');self.assertFalse(self.w.send(first,[(400,{'code':'invalid_body'})]))
        _,inputs=self.call('two','Hello again')
        self.assertEqual(inputs['thread_fields']['delivery_context']['confirmed_message_ids'],[])
        rejected=[h for h in inputs['messages'] if h.get('role')=='assistant']
        self.assertTrue(rejected);self.assertTrue(all('content' not in h and 'media' not in h for h in rejected))
        raw={'history':[{'role':'assistant','content':'Unsent ideas','media':['private']},
                        {'role':'assistant','content':'Maybe delivered','delivery_status':'ambiguous','delivery_id':'uncertain'},
                        {'role':'user','content':'I like snorkelling'}]}
        self.assertEqual(understanding.delivery_context(raw)['confirmed_message_ids'],[])
        projected=understanding.prompt_history(raw)
        self.assertNotIn('content',projected[0]);self.assertNotIn('content',projected[1])
        self.assertEqual(projected[2]['content'],'I like snorkelling')
        self.assertEqual(raw['history'][0]['content'],'Unsent ideas')
