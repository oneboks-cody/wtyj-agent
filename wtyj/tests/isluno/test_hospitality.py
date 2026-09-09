"""Complete synthetic conversations through Marina, orchestration and durable sends.
Model prose/decisions and provider outcomes are scripted, never live-quality proof.
"""
import copy
from contextlib import ExitStack
from datetime import timedelta
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_conversation as fixtures
from hospitality_fixtures import hospitality, operation_replies
from agents.social import isluno_hospitality as contract
from agents.social.isluno_delivery import send_plan
from shared.isluno_catalog import CatalogStore

ROOT = Path(__file__).resolve().parents[3]


class HospitalityTests(unittest.TestCase):
    records = []

    @classmethod
    def tearDownClass(cls):
        path = ROOT / 'output/isluno-hospitality/conversations.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'evidence': 'Synthetic catalog, scripted model responses, stub provider; no live model or guest receipt proof.',
                                   'conversations': cls.records}, ensure_ascii=False, indent=2))

    def setUp(self):
        self.t = fixtures.ConversationTests('test_conversation_sdk_uses_single_existing_model_request')
        self.t.setUp(); self.addCleanup(self.t.doCleanups)
        self.now = fixtures.NOW
        self.t.itinerary.clock = lambda: self.now
        self.t.discovery.clock = lambda: self.now
        catalog = json.loads(self.t.catalog_path.read_text())
        p = catalog['products'][0]
        p['summary'] = 'A relaxed three-hour cruise around the harbour.'
        p['inclusions'] = ['Lunch is included.']
        asset = json.loads((ROOT / 'clients/mermaid/config/isluno_catalog.json').read_text())['products'][0]['gallery'][0]
        p['gallery'] = [dict(copy.deepcopy(asset), id='cruise-photo-' + str(i), order=i) for i in range(4)]
        other = copy.deepcopy(p)
        other.update(id='fixture-walk', name='Synthetic Walk', summary='A two-hour active guided walk.', gallery=[])
        other['schedule']['slots'][0]['duration_minutes'] = 120
        other['inclusions'] = ['A guide is included.']
        catalog['products'].append(other)
        self.t.catalog_path.write_text(json.dumps(catalog))
        self.t.config['isluno'] = {'native_carousels':False, 'document_base_url':'https://example.invalid/documents'}
        self.t.write_config(self.t.config)
        self.transcript = {'scenario': self._testMethodName, 'turns': []}
        self.records.append(self.transcript)
        self.serial = 0

    def decision(self, text, question='', *, action='none', evidence='', products=None, facts=None,
                 updates=None, guest=None, memory=None, photo='none', stage='exploration', language='en', replies=None):
        decision = fixtures.response(action, updates, guest, language=language,
                                     products=[] if products is None else products, fact_keys=[] if facts is None else facts)
        decision['hospitality'] = hospitality(text, question, action=action, evidence=evidence,
                memory=memory, photo=photo, stage=stage, replies=replies or (operation_replies() if action != 'none' else {}))
        return decision

    def turn(self, text, decision=None, *, token='', status='accepted'):
        from agents.marina import marina_agent
        from agents.social import social_agent, isluno_conversation
        from agents.social.channels.whatsapp_zernio import WhatsAppZernioChannel
        self.serial += 1
        self.now += timedelta(seconds=10)
        scope = self.t.scope()
        trigger = 'hospitality-' + str(self.serial)
        msg = WhatsAppZernioChannel.from_zernio({'conversation_id':scope.conversation_id, 'account_id':scope.account_id,
            'sender_id':scope.customer_ref, 'message_id':trigger, 'channel':'whatsapp', 'text':text,
            'sent_at':self.now.isoformat(), 'interactive_id':token, 'interactive_type':'button_reply' if token else ''})
        before = copy.deepcopy(self.t.store.session(scope))
        with ExitStack() as stack:
            for obj, name, value in [(social_agent.state_registry,'match_ignored_contact',None),
                                      (social_agent.auto_block,'evaluate_inbound',{}),
                                      (isluno_conversation,'ConversationStore',self.t.store),
                                      (isluno_conversation,'DiscoveryStore',self.t.discovery)]:
                stack.enter_context(patch.object(obj,name,return_value=value))
            stack.enter_context(patch.dict(os.environ, {'ANTHROPIC_API_KEY':'synthetic-no-network'}))
            stack.enter_context(patch.object(marina_agent.bm_logger,'log'))
            sdk = stack.enter_context(patch.object(marina_agent.anthropic,'Anthropic'))
            if isinstance(decision, Exception):
                sdk.return_value.messages.create.side_effect = decision
            else:
                sdk.return_value.messages.create.return_value = SimpleNamespace(content=[SimpleNamespace(type='tool_use',input=copy.deepcopy(decision))],usage=None)
            result = social_agent.handle_incoming_whatsapp_message(msg,include_media=True)
            calls = sdk.return_value.messages.create.call_count
            self.assertEqual(calls,0 if token else 1)
            if calls:
                self.last_model_request = sdk.return_value.messages.create.call_args.kwargs
                self.assertEqual(self.last_model_request['model'],'claude-sonnet-4-6')
                self.assertEqual(self.last_model_request['max_tokens'],2048)
        self.assertIsInstance(result,dict)
        self.assertTrue(result['text'])
        outgoing = []
        if result.get('media',{}).get('type') == 'isluno_discovery':
            def post(actual_scope, body, key):
                self.assertEqual(actual_scope,scope)
                outgoing.append(copy.deepcopy(body))
                return {'status':status,'provider_id':'synthetic-'+trigger if status=='accepted' else None}
            ok = send_plan(scope.conversation_id,scope.account_id,result['media']['url'],store=self.t.discovery,
                           post=post,window=lambda *args:{'open':True},sleep=lambda _:None)
            self.assertEqual(ok,status=='accepted')
            self.assertEqual(len(outgoing),1)
            history = self.t.store.session(scope)['history']
            self.assertEqual(history[-1]['content'],result['text'])
            self.assertEqual(history[-1]['delivery_status'],status)
            self.assertFalse(history[-1]['guest_receipt_verified'])
        elif result.get('media',{}).get('type') in {'isluno_quote','isluno_fulfillment'}:
            from agents.social.isluno_quotes import QuoteStore
            from agents.social.isluno_payments import PaymentStore
            from agents.social.isluno_quote_delivery import send_job
            from agents.social.isluno_fulfillment import send_fulfillment
            store = (PaymentStore if result['media']['type']=='isluno_fulfillment' else QuoteStore)(self.t.store)
            def post(actual_scope, body, key):
                self.assertEqual(actual_scope,scope)
                outgoing.append(copy.deepcopy(body))
                return {'status':status,'provider_id':'synthetic-'+trigger+'-'+str(len(outgoing)) if status=='accepted' else None}
            def sleep(seconds):
                self.now += timedelta(seconds=seconds)
            def dispatch(conversation, account, ident, **kw):
                return send_job(conversation,account,ident,store=store,post=post,window=lambda *a:{'open':True},sleep=sleep)
            if result['media']['type']=='isluno_quote':
                self.assertTrue(dispatch(scope.conversation_id,scope.account_id,result['media']['url']))
            else:
                self.assertTrue(send_fulfillment(scope.conversation_id,scope.account_id,result['media']['url'],store=store,
                    dispatch=dispatch,schedule_email=lambda *_:None,
                    send_question=lambda conv,account,plan:send_plan(conv,account,plan,store=self.t.discovery,post=post,window=lambda *a:{'open':True},sleep=sleep)))
            self.last_job=store.job(result['media']['url'],scope.account_id,scope.conversation_id)
        after = self.t.store.session(scope)
        self.transcript['turns'].append({'guest':text or '[Native action]', 'reply':result['text'], 'provider_outcome':status if outgoing else 'not dispatched by this harness',
            'model_calls':calls, 'browsing':after.get('browsing',{}), 'stage':after.get('stage'),
            'itinerary_id':after['active_itinerary_id'], 'pending_items':len(after['pending']),
            'outbound':outgoing, 'last_accepted_question':after.get('last_accepted_question'), 'history':after['history'][-2:]})
        if decision and not isinstance(decision,Exception) and decision['booking']['action']=='none':
            self.assertEqual(after['active_itinerary_id'],before['active_itinerary_id'])
            self.assertEqual(after['pending'],before['pending'])
            self.assertEqual(after['item_details'],before['item_details'])
        return result

    def no_bookings(self):
        self.assertIsNone(self.t.store.session(self.t.scope())['active_itinerary_id'])
        with self.t.store.db() as db:
            for table in ['isluno_itineraries','isluno_quote_versions','isluno_payments']:
                if db.execute("SELECT 1 FROM sqlite_master WHERE name=?",(table,)).fetchone():
                    self.assertEqual(db.execute('SELECT count(*) FROM '+table).fetchone()[0],0)

    def book(self, date='2026-10-15'):
        text='Please add the cruise for '+date+'. I am Calvin, age 35.'
        return self.turn(text,self.decision('Happy to help.',action='add',evidence=text,products=['fixture-cruise'],
            updates=[{'product_id':'fixture-cruise','date':date}],guest={'name':'Calvin','ages':[35]},stage='booking_preparation'))

    def test_01_calvin_introduction_and_month(self):
        intro="Hi, I’m Calvin and interested in activities in Curaçao. We’re arriving next week and staying for a month."
        self.turn(intro,self.decision("Hi Calvin! Lovely to hear you're coming to Curaçao.\n\nA month gives you time for days on the water, a little adventure and slower days to enjoy the island at your own pace.",
            "Who's coming with you?",guest={'name':'Calvin'},memory={'holiday':'Arriving next week, staying a month'},stage='welcome'))
        self.turn('My partner and I, just the two of us.',self.decision("Lovely, we can find something that suits you both.",
            'Would you prefer a relaxed outing or something active?',memory={'party':'Two adults, partners'}))
        saved=self.t.store.session(self.t.scope())
        self.assertEqual(saved['browsing']['guest']['name'],'Calvin')
        self.assertIn('month',saved['browsing']['holiday']);self.no_bookings()
        self.assertIn("Who's coming with you?",json.dumps(self.last_model_request))
        self.assertNotIn('Demo itinerary saved',json.dumps(saved['history']))

    def test_02_relaxed_couple(self):
        self.turn('We are a couple looking for a relaxed day.',self.decision("For the slower pace you're after, I'd start with {name:fixture-cruise}. {fact:fixture-cruise:summary}",
            'Would you like to see a few photos?',products=['fixture-cruise'],facts=['summary'],memory={'party':'Couple','pace':'Relaxed'}))
        self.turn('Yes please.',self.decision('Here is a photo of {name:fixture-cruise}.','Would you like more photos or trip details?',
            products=['fixture-cruise'],photo='initial'))
        self.no_bookings()

    def test_03_family_volunteers_details(self):
        self.turn('We are two adults and two children, 6 and 10, here for a week. We like relaxed outings and need no pickup.',
            self.decision("Thanks, that helps narrow things down for your family. {fact:fixture-cruise:summary}",
                'Would you like to explore this option?',products=['fixture-cruise'],facts=['summary'],
                memory={'party':'Two adults, children 6 and 10','holiday':'One week','pace':'Relaxed','requirements':'No pickup'}))
        self.turn('Do they include lunch?',self.decision('{fact:fixture-cruise:inclusion_0}', 'Would you like to see the cruise photos?',
            products=['fixture-cruise'],facts=['inclusion_0']))
        self.no_bookings();self.assertNotIn('ages',self.t.store.session(self.t.scope())['guest'])

    def test_04_specific_budget(self):
        self.turn('We have USD 250 for two people. How much is the cruise for us?',self.decision(
            "I'll keep your total budget in mind. To work out the right price for your party, I need your ages first.",
            'How old are both guests?',products=['fixture-cruise'],memory={'budget':'USD 250 total','party':'Two people'}))
        estimate=self.decision('The demo estimate for your party is {estimate_total}.', 'Would you like to add this cruise to your itinerary?',
            products=['fixture-cruise'],guest={'ages':[35,34]},replies={'error':operation_replies()['error']})
        estimate['hospitality']['estimate']={'product_id':'fixture-cruise','date':'2026-10-15','slot_id':'morning','guest_ages':[35,34],'options':{},'pickup':False}
        self.turn('We are 35 and 34, October 15 morning, no pickup or extras.',estimate)
        self.assertIn('USD 200.00',self.transcript['turns'][-1]['reply'])
        self.no_bookings()
        self.turn('We need time to think.',self.decision("Of course, take your time. I've kept your preferences for when you're ready."))
        self.no_bookings();self.assertEqual(self.t.store.session(self.t.scope())['browsing']['budget'],'USD 250 total')

    def test_05_direct_booking_known_trip(self):
        self.turn('Please add the cruise. I am Calvin, age 35.',self.decision('Happy to help.',action='add',evidence='Please add the cruise.',
            products=['fixture-cruise'],updates=[{'product_id':'fixture-cruise'}],guest={'name':'Calvin','ages':[35]},stage='booking_preparation'))
        self.turn('October 15 please.',self.decision('Happy to help.',action='update',evidence='October 15 please.',
            products=['fixture-cruise'],updates=[{'date':'2026-10-15'}],stage='booking_preparation'))
        self.assertEqual(self.t.active()['totals']['total_minor'],10000)
        self.assertEqual(self.t.store.session(self.t.scope())['pending'],{})

    def test_06_interest_is_not_consent(self):
        self.turn('Tell me about the cruise.',self.decision('{fact:fixture-cruise:summary}', 'Does that pace suit you?',products=['fixture-cruise'],facts=['summary']))
        self.turn('That looks nice.',self.decision("It sounds like it appeals to you.",'Would you like to add this cruise to your itinerary?',products=['fixture-cruise']))
        self.no_bookings()

    def test_07_photos_more_and_ambiguous(self):
        first=self.turn('Show cruise photos.',self.decision('Here is {name:fixture-cruise}.','Would you like another photo?',products=['fixture-cruise'],photo='initial'))
        second=self.turn('More photos please.',self.decision('Here is another view of {name:fixture-cruise}.','Would you like to keep browsing?',products=['fixture-cruise'],photo='more'))
        self.assertNotEqual(self.transcript['turns'][0]['outbound'][0]['attachmentUrl'],self.transcript['turns'][1]['outbound'][0]['attachmentUrl'])
        self.turn('Another please.',self.decision('Another view of {name:fixture-cruise}.',products=['fixture-cruise'],photo='more'),status='ambiguous')
        self.turn('Show the next photo.',self.decision('Here is the next view of {name:fixture-cruise}.',products=['fixture-cruise'],photo='more'))
        urls=[t['outbound'][0]['attachmentUrl'] for t in self.transcript['turns']]
        self.assertEqual(len(set(urls)),4);self.no_bookings()
        self.assertIn('ambiguous',json.dumps(self.last_model_request))

    def test_08_changed_preference(self):
        self.turn('We want something active.',self.decision('{fact:fixture-walk:summary}', 'Does a walk appeal to you?',products=['fixture-walk'],facts=['summary'],memory={'pace':'Active'}))
        self.turn('Actually, a relaxed outing would suit us better.',self.decision("Let's switch the pace. {fact:fixture-cruise:summary}",
            'Would you like the cruise details?',products=['fixture-cruise'],facts=['summary'],memory={'pace':'Relaxed'}))
        self.assertEqual(self.t.store.session(self.t.scope())['browsing']['pace'],'Relaxed');self.no_bookings()

    def test_09_conflicting_trips(self):
        self.book()
        before=self.t.active()
        self.turn('Add the walk on the same date, October 15.',self.decision('Happy to help.',action='add',evidence='Add the walk on the same date, October 15.',
            products=['fixture-walk'],updates=[{'product_id':'fixture-walk','date':'2026-10-15'}],stage='booking_preparation'))
        self.assertEqual(self.t.active(),before)
        self.assertEqual(len(self.t.store.session(self.t.scope())['pending']),1)
        self.turn('Move the walk to October 16.',self.decision('Happy to help.',action='update',evidence='Move the walk to October 16.',products=['fixture-walk'],
            updates=[{'item_id':next(iter(self.t.store.session(self.t.scope())['pending'])),'date':'2026-10-16'}],stage='booking_preparation'))
        self.assertEqual(len(self.t.active()['items']),2)

    def test_10_correction_and_question(self):
        self.book()
        replies=operation_replies()
        replies['saved']['paragraphs'].insert(0,'{fact:fixture-cruise:inclusion_0}')
        replies['error']['paragraphs'].insert(0,'{fact:fixture-cruise:inclusion_0}')
        self.turn('Move the cruise to October 16, and is lunch included?',self.decision('Happy to help.',action='update',
            evidence='Move the cruise to October 16',products=['fixture-cruise'],facts=['inclusion_0'],updates=[{'date':'2026-10-16'}],replies=replies))
        self.assertIn('Lunch is included.',self.transcript['turns'][-1]['reply'])
        self.assertEqual(self.t.active()['items'][0]['selection']['date'],'2026-10-16')

    def test_11_missing_supplier_information(self):
        d=self.decision("I don't have confirmed accessibility details for this cruise.", 'Would you like help from the team?',products=['fixture-cruise'])
        d['question']='Unconfirmed wheelchair access'
        self.turn('Is the cruise wheelchair accessible?',d)
        self.turn('No, just show me the photos for now.',self.decision('Here is {name:fixture-cruise}.',products=['fixture-cruise'],photo='initial'))
        self.assertEqual(self.t.store.reviews(self.t.scope()),[]);self.no_bookings()

    def test_12_declined_recommendation(self):
        self.turn('What is the cruise like?',self.decision('{fact:fixture-cruise:summary}', 'Would that suit your plans?',products=['fixture-cruise'],facts=['summary']))
        self.turn('No thanks, we do not want a boat trip.',self.decision("Thanks for telling me. We can leave boat trips out.",
            'Would you prefer an activity on land?',memory={'interests':'Land activities, no boat trips'}))
        self.no_bookings()

    def test_13_processing_failure(self):
        self.book();before=self.t.active()
        d=self.decision('Invalid model shape');d['fact_keys']={}
        self.turn('Move it to October 16.',d)
        self.assertEqual(self.t.active(),before)
        self.turn('What is the current cruise description?',self.decision('{fact:fixture-cruise:summary}',products=['fixture-cruise'],facts=['summary']))
        self.assertEqual(self.t.active(),before)

    def test_14_explicit_human(self):
        self.turn('I would like to speak to a person.',self.decision('Happy to help.',action='human',evidence='speak to a person',replies=operation_replies()))
        self.assertEqual(len(self.t.store.reviews(self.t.scope())),1)
        self.turn('Thank you.',self.decision("You're welcome."))
        self.assertEqual(len(self.t.store.reviews(self.t.scope())),1)
        self.assertNotIn('notified',self.transcript['turns'][0]['reply'])

    def test_15_returning_guest_pending_and_booked(self):
        self.book()
        before=self.t.active()
        self.turn('Hi again, Calvin here.',self.decision('Welcome back, Calvin.','What would you like help with?',guest={'name':'Calvin'},stage='after_booking'))
        self.assertEqual(self.t.active(),before);self.assertEqual(self.t.store.reviews(self.t.scope()),[])
        from agents.social.isluno_quotes import QuoteStore
        from agents.social.isluno_payments import PaymentStore
        self.turn('Please show the itinerary for review.',self.decision('Happy to help.',action='summary',evidence='show the itinerary for review',products=['fixture-cruise']))
        quotes=QuoteStore(self.t.store)
        job=self.last_job
        before_quote=copy.deepcopy(job)
        self.turn('My name is Calvin, just checking in.',self.decision('Good to hear from you.','What would you like to check?',guest={'name':'Calvin'}))
        self.assertEqual(quotes.job(job['id'],self.t.scope().account_id,self.t.scope().conversation_id),before_quote)
        for label in ['Confirm summary','Approve quote','Complete demo payment']:
            if job.get('followup_job_id'):
                job=quotes.job(job['followup_job_id'],self.t.scope().account_id,self.t.scope().conversation_id)
            token=job['parts'][-1]['buttons'][0]['payload']
            self.turn('[Native: '+label+']',token=token)
            job=self.last_job
        paid=copy.deepcopy(PaymentStore(self.t.store).records(self.t.scope()))
        self.turn('Hi, Calvin here again.',self.decision('Welcome back, Calvin.','What can I help you with?',guest={'name':'Calvin'},stage='after_booking'))
        self.assertEqual(PaymentStore(self.t.store).records(self.t.scope()),paid)
        self.assertEqual(self.t.store.reviews(self.t.scope()),[])

    def language_conversation(self, language):
        samples={
          'nl':("Hoi, ik ben Calvin. We blijven een maand.","Hoi Calvin! Fijn dat jullie een maand de tijd hebben om het eiland te ontdekken.","Met wie kom je?","We zijn met twee volwassenen.","Dank je, dan zoeken we iets dat bij jullie past.","Willen jullie iets rustigs of actiefs?"),
          'de':("Hallo, ich bin Calvin. Wir bleiben einen Monat.","Hallo Calvin! Mit einem ganzen Monat könnt ihr die Insel in eurem Tempo entdecken.","Wer kommt mit?","Wir sind zwei Erwachsene.","Danke, dann suchen wir etwas Passendes für euch beide.","Möchtet ihr etwas Ruhiges oder Aktives?"),
          'es':("Hola, soy Calvin. Nos quedamos un mes.","¡Hola, Calvin! Con un mes podéis descubrir la isla a vuestro ritmo.","¿Con quién vienes?","Somos dos adultos.","Gracias, podemos buscar algo que os guste a los dos.","¿Preferís algo tranquilo o activo?"),
          'pt':("Olá, sou o Calvin. Ficamos um mês.","Olá, Calvin! Com um mês podem descobrir a ilha ao vosso ritmo.","Quem vem contigo?","Somos dois adultos.","Obrigado, podemos encontrar algo que agrade aos dois.","Preferem algo tranquilo ou ativo?"),
          'pap':("Bon dia, mi ta Calvin. Nos ta keda un luna.","Bon dia Calvin! Ku un luna boso tin tempu pa eksplorá e isla na boso ritmo.","Ku ken bo ta bini?","Nos ta dos adulto.","Danki, nos por buska algu ku ta pas ku boso dos.","Boso ta preferá algu trankil òf aktivo?")}
        for lang,(guest,answer,question,second,reply,next_question) in [(language, samples[language])]:
            self.turn(guest,self.decision(answer,question,language=lang,guest={'name':'Calvin'},memory={'holiday':'One month'},stage='welcome'))
            self.turn(second,self.decision(reply,next_question,language=lang,memory={'party':'Two adults'}))
        self.no_bookings()

    def test_17_structural_rejection_and_semantic_limit(self):
        catalog=__import__('agents.social.isluno_understanding',fromlist=['context']).context(CatalogStore(self.t.catalog_path).snapshot())
        good=self.decision('{fact:fixture-cruise:summary}',products=['fixture-cruise'],facts=['summary'])
        bad=copy.deepcopy(good);bad['hospitality']['replies']['browsing']['paragraphs']=['{fact:fixture-cruise:invented_luxury_transfer}']
        with self.assertRaisesRegex(ValueError,'unsupported_reply_fact'):
            contract.validate(bad['hospitality'],bad,catalog)
        bad=copy.deepcopy(good);bad['hospitality']['replies']['browsing']['paragraphs']=['Your booking is {operation}.']
        with self.assertRaisesRegex(ValueError,'transaction_claim_in_browsing'):
            contract.validate(bad['hospitality'],bad,catalog)
        bad=copy.deepcopy(good);bad['hospitality']['replies']['browsing']['paragraphs']=['{fact:fixture-cruise:summary} A luxury transfer is included.']
        # Honest residual-risk evidence: structural validation cannot certify free prose.
        contract.validate(bad['hospitality'],bad,catalog)
        self.transcript['adversarial_not_dispatched']={'text':bad['hospitality']['replies']['browsing']['paragraphs'][0],
            'result':'Structurally valid, semantically unsupported; must fail human conversation review. NOT sent by this harness.'}
        action=self.decision('Happy to help.',action='add',evidence='Please book',products=['fixture-cruise'],updates=[{'product_id':'fixture-cruise'}])
        with self.assertRaisesRegex(ValueError,'unbound_action_authority'):
            contract.authorize(action['hospitality'],'That looks nice.')
        self.no_bookings()

    def test_18_pending_intake_greeting_never_completes_it(self):
        self.turn('Add the cruise for October 15.',self.decision('Happy to help.',action='add',evidence='Add the cruise for October 15.',
            products=['fixture-cruise'],updates=[{'product_id':'fixture-cruise','date':'2026-10-15'}]))
        before=copy.deepcopy(self.t.active())
        pending=copy.deepcopy(self.t.store.session(self.t.scope())['pending'])
        self.turn('Hi, Calvin here, I am 35.',self.decision('Hi Calvin, good to hear from you.','Would you like to continue arranging the cruise?',guest={'name':'Calvin','ages':[35]}))
        self.assertEqual(self.t.active(),before)
        self.assertEqual(self.t.store.session(self.t.scope())['pending'],pending)
        self.assertEqual(self.t.store.reviews(self.t.scope()),[])

    def test_19_question_survives_failed_correction(self):
        self.book()
        replies=operation_replies()
        replies['error']['paragraphs'].insert(0,'{fact:fixture-cruise:inclusion_0}')
        before=copy.deepcopy(self.t.active())
        self.turn('Move the cruise to yesterday, and is lunch included?',self.decision('Happy to help.',action='update',
            evidence='Move the cruise to yesterday',products=['fixture-cruise'],facts=['inclusion_0'],updates=[{'date':'2026-09-01'}],replies=replies))
        self.assertIn('Lunch is included.',self.transcript['turns'][-1]['reply'])
        self.assertEqual(self.t.active(),before)

    def test_20_partial_delivery_and_duplicate_no_resend(self):
        self.turn('Photos of the cruise.',self.decision('Here is {name:fixture-cruise}.','Would you like more?',products=['fixture-cruise'],photo='initial'))
        scope=self.t.scope()
        result=self.turn('Another photo.',self.decision('Another view of {name:fixture-cruise}.','Shall I show the next?',products=['fixture-cruise'],photo='more'),status='rejected')
        before=copy.deepcopy(self.t.store.session(scope))
        self.assertFalse(send_plan(scope.conversation_id,scope.account_id,result['media']['url'],store=self.t.discovery,
            post=lambda *a:self.fail('Rejected part was retried'),window=lambda *a:{'open':True}))
        self.assertEqual(self.t.store.session(scope),before)
        self.assertEqual(before['last_accepted_question'],'Would you like more?')
        self.no_bookings()

    def test_16_nl(self): self.language_conversation('nl')
    def test_16_de(self): self.language_conversation('de')
    def test_16_es(self): self.language_conversation('es')
    def test_16_pt(self): self.language_conversation('pt')
    def test_16_pap(self): self.language_conversation('pap')
