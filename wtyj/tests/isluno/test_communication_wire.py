"""Actual model/handler/sender path; only model SDK and provider adapters replaced."""
import copy
import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch
from contextlib import ExitStack
import unittest
from test_no_reply import NoReplyTests
from test_conversation import response,NOW
from hospitality_fixtures import hospitality
from agents.social import senders,zernio_dm_client as client
from agents.social.isluno_wire import validate_body,text_of,units,messages
from agents.social.isluno_recovery import RecoveryStore
from agents.social.isluno_callbacks import accept


class CommunicationWireTests(unittest.TestCase):
    def setUp(self):
        self.h=NoReplyTests('test_unknown_source_fact_remains_rejected_and_never_rendered');self.h.setUp();self.addCleanup(self.h.doCleanups)
        self.t=self.h.t;self.now=NOW
        self.t.itinerary.clock=lambda:self.now;self.t.discovery.clock=lambda:self.now
        self.requests=[]
    def reply(self,n=1122,trigger='intro'):
        decision=response();decision['hospitality']=hospitality('A'*(n-27),'Which day would suit you?',photo='initial')
        reply,calls,_=self.h.call(decision,trigger,text="Good morning, we are arriving next week and would love some ideas for trips with our family.")
        self.assertEqual(calls,1);self.assertNotIn('generation_failed',reply)
        return reply
    def send(self,reply,results=None,window=True,callback_before_response=False):
        def http(url,**kw):
            body=kw['json'];validate_body(body);self.requests.append(copy.deepcopy(body))
            index=len(self.requests);data={'success':True,'data':{'messageId':'provider-'+str(index)}};code=200
            if results:
                selected=results[index-1]
                if isinstance(selected,Exception):raise selected
                code,data=selected
            if callback_before_response and index==1:self.callback('message.failed','provider-1')
            return SimpleNamespace(status_code=code,json=lambda:data,text=json.dumps(data))
        def sleep(seconds):self.now+=timedelta(seconds=seconds)
        with ExitStack() as stack:
            stack.enter_context(patch.dict('os.environ',{'LATE_API_KEY':'synthetic-no-network'}))
            stack.enter_context(patch.object(client,'_provider_mutation_account_allowed',return_value=True))
            stack.enter_context(patch.object(client,'whatsapp_customer_service_window',return_value={'open':window}))
            stack.enter_context(patch.object(client.http_requests,'post',side_effect=http))
            stack.enter_context(patch('agents.social.isluno_delivery.DiscoveryStore',return_value=self.t.discovery))
            stack.enter_context(patch('agents.social.isluno_delivery.time.sleep',side_effect=sleep))
            return senders.send_reply('whatsapp',self.t.scope().conversation_id,self.t.scope().account_id,reply['text'],
                attachment_url=reply['media']['url'],attachment_type=reply['media']['type'],confirm_delivery=True)
    def callback(self,event,identifier,account=None):
        scope=self.t.scope()
        payload={'event':event,'message':{'id':identifier,'accountId':account or scope.account_id,'conversationId':scope.conversation_id}}
        with patch('shared.tenant_guard.account_access_state',return_value=True):return accept(payload,store=self.t.store)
    def plan(self,reply):
        return self.t.discovery.delivery_plan(reply['media']['url'],self.t.scope().account_id,self.t.scope().conversation_id)[0]
    def test_observed_lengths_complete_through_real_sender(self):
        for n in (1122,1085):
            reply=self.reply(n,str(n));before=len(self.requests)
            self.assertTrue(self.send(reply))
            parts=self.requests[before:];self.assertGreaterEqual(len(parts),2)
            self.assertEqual(''.join(map(text_of,parts)),reply['text'])
            self.assertTrue(parts[-1].get('buttons') or parts[-1].get('attachmentUrl'))
            self.assertTrue(all('buttons' not in part and 'attachmentUrl' not in part for part in parts[:-1]))
            self.assertLessEqual(units(text_of(parts[-1])),1024)
            self.assertTrue(all(p['status']=='accepted' for p in self.plan(reply)['parts']))
    def test_accepted_prefix_rejected_controls_never_replayed(self):
        reply=self.reply()
        self.assertFalse(self.send(reply,[(200,{'success':True,'data':{'messageId':'prefix'}}),(400,{'code':'invalid_body','platformError':{'code':100}})]))
        count=len(self.requests);self.assertFalse(self.send(reply));self.assertEqual(count,len(self.requests))
        plan=self.plan(reply);self.assertEqual([p['status'] for p in plan['parts']],['accepted','rejected'])
        recovery=RecoveryStore(self.t.store).audit();failure=next(r for r in recovery['outbound_failures'] if r['id']==plan['id'])
        self.assertEqual(failure['http_status'],400);self.assertEqual(failure['reason'],'invalid_body');self.assertTrue(failure['inbox_path'].startswith('/conversations?c='))
        self.assertEqual(self.t.store.session(self.t.scope()).get('last_accepted_question',''),'')
    def test_exception_after_prefix_is_ambiguous(self):
        reply=self.reply();self.assertFalse(self.send(reply,[(200,{'success':True,'data':{'messageId':'prefix'}}),RuntimeError('synthetic transport interruption')]))
        self.assertEqual(self.plan(reply)['parts'][1]['status'],'ambiguous')
        self.assertNotIn('synthetic transport interruption',json.dumps(self.plan(reply)))
        self.assertFalse(self.send(reply));self.assertEqual(len(self.requests),2)
    def test_closed_window_is_visible_without_post(self):
        reply=self.reply();self.assertFalse(self.send(reply,window=False));self.assertEqual(self.requests,[])
        failure=RecoveryStore(self.t.store).audit()['outbound_failures'][0]
        self.assertEqual(failure['status'],'window_closed');self.assertEqual(failure['parts'][0]['status'],'window_closed')
    def test_partial_warning_keeps_provider_id_without_retry(self):
        reply=self.reply(500)
        self.assertFalse(self.send(reply,[(200,{'success':True,'data':{'messageId':'partial','messageIds':['partial'],'partialFailure':True},'warnings':['private provider prose']})]))
        plan=self.plan(reply);self.assertEqual(plan['parts'][0]['result']['provider_ids'],['partial'])
        self.assertNotIn('private provider prose',json.dumps(plan))
        before=len(self.requests);self.assertFalse(self.send(reply));self.assertEqual(len(self.requests),before)
    def test_callback_before_http_result_correlates_and_stops_remaining_parts(self):
        reply=self.reply();self.assertFalse(self.send(reply,callback_before_response=True));self.assertEqual(len(self.requests),1)
        self.assertEqual(self.plan(reply)['parts'][0]['status'],'provider_failed')
        session=self.t.store.session(self.t.scope());self.assertTrue(session['provider_events'][0]['matched'])
        self.assertEqual(session['history'][-1]['delivery_status'],'provider_failed')
    def test_delivered_read_and_late_failure_are_distinct(self):
        reply=self.reply(500);self.assertTrue(self.send(reply))
        self.assertTrue(self.callback('message.delivered','provider-1'));self.assertTrue(self.callback('message.read','provider-1'))
        self.assertEqual(self.plan(reply)['parts'][0]['provider_delivery_status'],'read')
        self.assertTrue(self.callback('message.delivered','provider-1'));self.assertEqual(self.plan(reply)['parts'][0]['provider_delivery_status'],'read')
        self.assertTrue(self.callback('message.failed','provider-1'));self.assertTrue(self.callback('message.delivered','provider-1'))
        self.assertEqual(self.plan(reply)['parts'][0]['status'],'provider_failed')
        before=len(self.requests);self.assertFalse(self.send(reply));self.assertEqual(len(self.requests),before)
    def test_wrong_account_or_unknown_id_never_changes_send(self):
        reply=self.reply(500);self.assertTrue(self.send(reply))
        self.assertFalse(self.callback('message.failed','provider-1','foreign'))
        self.assertTrue(self.callback('message.failed','unknown'))
        self.assertEqual(self.plan(reply)['parts'][0]['status'],'accepted')
    def test_unicode_lossless_controls_final(self):
        for text in ['😀'*2050,'a '*2048,'x'*1122]:
            body={'accountId':'fixture','message':text+'\nQuestion?','buttons':[{'type':'postback','title':'Select','payload':'a'}]}
            parts=messages(body,'Question?');self.assertEqual(''.join(map(text_of,parts)),body['message'])
            self.assertTrue(parts[-1]['message'].endswith('Question?'))
            for part in parts:validate_body(part)
    def test_optional_unused_outcome_branches_are_not_required(self):
        decision=response('add',[{'product_id':'fixture-cruise'}]);decision['hospitality']=hospitality('Happy to help.',action='add',evidence='Add this trip',replies={'error':{'paragraphs':['{operation}'],'question':''}})
        reply,calls,_=self.h.call(decision,'missing-fields',text='Add this trip')
        self.assertEqual(calls,1);self.assertNotIn('generation_failed',reply)
        self.assertEqual(reply['text'], 'Happy to help.\n\nCould you share the guest name?')
        self.assertEqual(reply['text'].count('?'),1)
        self.assertNotIn('saved',reply['text'])
        self.assertNotIn('slot_',reply['text'])
    def test_preparation_binding_is_question_only(self):
        decision=response('add',[{'product_id':'fixture-cruise'}])
        value=hospitality('Happy to help.',action='add',evidence='Add this trip',replies={'error':{'paragraphs':['{operation}'],'question':''}})
        # Actual handler validation rejects a transactional binding in shared prose.
        bad=copy.deepcopy(decision);bad['hospitality']=copy.deepcopy(value)
        bad['hospitality']['replies']['browsing']['paragraphs']=['The {missing_field}']
        reply,calls,_=self.h.call(bad,'bad-preparation',text='Add this trip')
        self.assertTrue(reply.get('generation_failed'));self.assertEqual(calls,1)

    def test_stale_button_returns_operational_notice_without_model_or_mutation(self):
        first=self.reply(500,'one');self.assertTrue(self.send(first));self.reply(500,'two')
        token=next(b['payload'] for part in self.plan(first)['parts'] for b in part['body'].get('buttons',[]) if self.plan(first)['button_meanings'][b['payload']]['kind']=='add');before=self.t.store.session(self.t.scope())['revision']
        reply,calls=self.t.turn(token=token,trigger='stale')
        self.assertEqual(calls,0);self.assertTrue(reply['text']);self.assertTrue(reply['generation_failed'])
        self.assertEqual(self.t.store.session(self.t.scope())['revision'],before)
    def test_unaccepted_native_control_cannot_apply_an_action(self):
        first=self.reply(500,'unsent')
        token=next(b['payload'] for part in self.plan(first)['parts'] for b in part['body'].get('buttons',[]) if self.plan(first)['button_meanings'][b['payload']]['kind']=='add')
        reply,calls=self.t.turn(token=token,trigger='unsent-click')
        self.assertEqual(calls,0);self.assertTrue(reply['generation_failed'])
        self.assertIsNone(self.t.store.session(self.t.scope())['active_itinerary_id'])
        self.assertIn('no longer current',reply['text'])

    def test_generated_but_unsent_turn_does_not_resolve_incident(self):
        bad=response();bad['fact_keys']={};self.h.call(bad,'failed')
        self.reply(500,'new')
        self.assertTrue(all(i['status']=='operator_review' for i in RecoveryStore(self.t.store).audit()['incidents']))

    def test_signed_webhook_burst_dedup_and_real_window_sender(self):
        import hashlib,hmac
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from agents.social import webhook_server as web,social_agent,isluno_conversation
        from agents.marina import marina_agent
        from shared import state_registry
        from datetime import datetime,timezone
        class Clock(datetime):
            @classmethod
            def now(cls,tz=None):return self.now
        decision=response(products=[],fact_keys=[])
        decision['hospitality']=hospitality('Welcome! I can help you plan a lovely day.','What kind of activities do you enjoy?')
        controls={'available':True,'feature_toggles':{'ai_auto_reply':{'value':True},'whatsapp_inbox':{'value':True}}}
        app=FastAPI();app.post('/webhook/zernio')(web.receive_zernio_webhook)
        model=SimpleNamespace(content=[SimpleNamespace(type='tool_use',input=decision)],usage=None)
        scope=self.t.scope();posts=[];gets=[]
        def http_get(url,**kw):
            gets.append(kw['params']);return SimpleNamespace(status_code=200,json=lambda:{'success':True,'data':{'messages':[{'direction':'incoming','createdAt':NOW.isoformat()}]}})
        def http_post(url,**kw):
            validate_body(kw['json']);posts.append(kw['json']);data={'success':True,'data':{'messageId':'webhook-'+str(len(posts))}}
            return SimpleNamespace(status_code=200,json=lambda:data,text=json.dumps(data))
        with ExitStack() as stack:
            stack.enter_context(patch.dict('os.environ',{'ZERNIO_WEBHOOK_SECRET':'synthetic-secret','LATE_API_KEY':'synthetic-no-network'}))
            for obj,name,val in [(isluno_conversation,'ConversationStore',self.t.store),(isluno_conversation,'DiscoveryStore',self.t.discovery),
                (social_agent.state_registry,'match_ignored_contact',None),(social_agent.auto_block,'evaluate_inbound',{})]:
                stack.enter_context(patch.object(obj,name,return_value=val))
            stack.enter_context(patch('agents.social.isluno_delivery.DiscoveryStore',return_value=self.t.discovery))
            stack.enter_context(patch('agents.social.isluno_recovery.ConversationStore',return_value=self.t.store))
            stack.enter_context(patch.object(state_registry,'DB_PATH',str(self.t.itinerary.db_path)))
            stack.enter_context(patch('shared.tenant_guard.account_access_state',return_value=True))
            stack.enter_context(patch.object(client,'_provider_account_allowed',return_value=True))
            stack.enter_context(patch.object(client,'datetime',Clock))
            stack.enter_context(patch.object(client.http_requests,'get',side_effect=http_get))
            stack.enter_context(patch.object(client.http_requests,'post',side_effect=http_post))
            stack.enter_context(patch.object(web.icp_overrides,'fetch_overrides',return_value=controls))
            stack.enter_context(patch.object(web.icp_overrides,'fetch_overrides_fresh',return_value=controls))
            stack.enter_context(patch.object(web,'send_typing_indicator'))
            logs=stack.enter_context(patch.object(web,'log'))
            sdk=stack.enter_context(patch.object(marina_agent.anthropic,'Anthropic'));sdk.return_value.messages.create.return_value=model
            from agents.social.isluno_transition import ensure
            ensure(self.t.itinerary.db_path,self.now)
            api=TestClient(app)
            for identifier,text in [('signed-intro','Good morning, my family arrives next week.'),('signed-hi','hi')]:
                payload={'event':'message.received','message':{'id':identifier,'conversationId':scope.conversation_id,'accountId':scope.account_id,
                    'sender':{'id':scope.customer_ref},'platform':'whatsapp','text':text,'sentAt':NOW.isoformat()}}
                raw=json.dumps(payload).encode();sig=hmac.new(b'synthetic-secret',raw,hashlib.sha256).hexdigest()
                self.assertEqual(api.post('/webhook/zernio',content=raw,headers={'X-Zernio-Signature':'invalid'}).status_code,403)
                self.assertEqual(api.post('/webhook/zernio',content=raw,headers={'X-Zernio-Signature':sig}).status_code,200)
                self.assertEqual(api.post('/webhook/zernio',content=raw,headers={'X-Zernio-Signature':sig}).status_code,200)
                with web._buffer_lock:web._message_buffers[scope.conversation_id]['timer'].cancel()
            web._flush_buffer(scope.conversation_id)
            self.assertEqual(sdk.return_value.messages.create.call_count,1,str(logs.call_args_list))
            self.assertEqual(len(posts),1);self.assertTrue(gets)
            self.assertTrue(all(item['accountId']==scope.account_id for item in gets))
            with self.t.store.db() as db:
                rows=db.execute("SELECT status,reason FROM inbound_processing_events WHERE message_id IN ('signed-intro','signed-hi')").fetchall()
                self.assertEqual([tuple(row) for row in rows],[('replied','provider_send_ok')]*2)
            session=self.t.store.session(scope)
            self.assertIn('communication_progress',session)

    def test_compact_state_preserves_authoritative_items_and_prices(self):
        from agents.social.isluno_conversation_understanding import prompt_state
        self.t.initial();itinerary=self.t.active();state={**self.t.store.session(self.t.scope()),'itinerary':itinerary}
        projected=prompt_state(state)
        self.assertNotIn('history',projected)
        for original,item in zip(itinerary['items'],projected['itinerary']['items']):
            for key in ('id','selection','total_minor','currency','currency_exponent','lines'):
                self.assertEqual(item[key],original[key])

    def test_fresh_hi_after_rejected_reply_is_a_new_turn_without_replay(self):
        first=self.reply(500,'rejected-intro')
        self.assertFalse(self.send(first,[(400,{'code':'invalid_body'})]))
        decision=response(products=[],fact_keys=[])
        decision['hospitality']=hospitality('Hello! I can help you plan your visit.','What do you enjoy doing on holiday?')
        reply,calls,_=self.h.call(decision,'fresh-hi',text='hi')
        self.assertEqual(calls,1);self.assertTrue(self.send(reply));self.assertEqual(len(self.requests),2)
        statuses=[h['delivery_status'] for h in self.t.store.session(self.t.scope())['history'] if h['role']=='assistant']
        self.assertEqual(statuses,['rejected','accepted'])

    def test_old_progress_record_is_not_presented_as_verified_delivery(self):
        bad=response();bad['fact_keys']={};self.h.call(bad,'historic-failure')
        with self.t.store.db() as db,db:
            db.execute("UPDATE isluno_recovery_incidents SET status='progress_resumed_new_turn'")
        audit=RecoveryStore(self.t.store).audit()
        self.assertFalse(audit['incidents'][0]['delivery_progress_verified'])
        with self.t.store.db() as db:
            self.assertEqual(db.execute('SELECT status FROM isluno_recovery_incidents').fetchone()[0],'progress_resumed_new_turn')
