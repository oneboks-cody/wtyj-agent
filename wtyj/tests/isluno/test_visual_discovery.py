"""Images-first actual renderer/sender boundary, synthetic SDK and HTTP only."""
import copy,json
from unittest.mock import patch
from test_communication_wire import CommunicationWireTests
from test_conversation import response,NOW
from hospitality_fixtures import hospitality
from test_discovery import FakeMedia
from agents.social.isluno_discovery import DiscoveryStore
from agents.social.isluno_conversation import envelope
from agents.social.isluno_wire import validate_body,units


class VisualDiscoveryTests(CommunicationWireTests):
    # Reuse the strict real sender harness, not inherited unrelated test cases.
    def setUp(self):
        super().setUp()
        catalog=json.loads(self.t.catalog_path.read_text())
        first=catalog['products'][0]
        template=json.loads(__import__('pathlib').Path('clients/mermaid/config/isluno_catalog.json').read_text())['products'][0]['gallery'][0]
        first['gallery']=[{**template,'id':'cruise-'+str(i),'order':i} for i in range(5)]
        first['summary']='A relaxed coastal cruise.'
        second=copy.deepcopy(first);second.update(id='fixture-beach',name='Beach adventure',summary='Explore beaches with a guide.')
        second['gallery']=[{**a,'id':'beach-'+str(i)} for i,a in enumerate(first['gallery'])]
        catalog['products'].append(second);self.t.catalog_path.write_text(json.dumps(catalog))
        self.t.discovery.media=FakeMedia()

    def decision(self,photo='initial'):
        d=response(products=['fixture-cruise','fixture-beach']);d['intent']='discover'
        d['hospitality']=hospitality('These two could suit your relaxed family holiday.','Which appeals to you?',photo=photo,stage='recommendation')
        d['hospitality']['photo_opt_out']=photo=='none'
        d['hospitality']['cards']=[{'product_id':p,'paragraphs':['{fact:'+p+':summary}']} for p in d['product_ids']]
        return d

    def visual(self,trigger='visual',decision=None,*,text='We are a family visiting Curaçao and enjoy relaxed boat trips and beaches.'):
        reply,calls,_=self.h.call(decision or self.decision(),trigger,text=text)
        self.assertEqual(calls,1);self.assertNotIn('generation_failed',reply)
        return reply

    def click_visual(self,plan,product,kind,trigger):
        token=next(t for t,a in plan['button_meanings'].items() if a['kind']==kind and a['product_id']==product)
        return self.t.discovery.plan(self.t.scope(),trigger,self.now.isoformat(),action_token=token,interactive_type='button_reply')

    def test_two_cards_real_http_and_exact_history(self):
        reply=self.visual();self.assertTrue(self.send(reply));plan=self.plan(reply)
        self.assertEqual(len(self.requests),3)
        cards=self.requests[1:]
        self.assertEqual([b['attachmentUrl'] for b in cards],['https://example.invalid/cruise-0.jpg','https://example.invalid/beach-0.jpg'])
        for body in cards:
            self.assertEqual([b['title'] for b in body['buttons']],['More photos','Trip details','Plan this trip'])
            self.assertNotIn('interactive',body);validate_body(body)
        self.assertTrue(cards[-1]['message'].endswith('Which appeals to you?'))
        self.assertEqual(sum(b['message'].count('?') for b in self.requests),1)
        history=self.t.store.session(self.t.scope())['history'][-3:]
        self.assertEqual(history[1]['media'],[{'product_id':'fixture-cruise','asset_id':'cruise-0'}])
        self.assertEqual(history[2]['media'],[{'product_id':'fixture-beach','asset_id':'beach-0'}])
        self.assertEqual({a['product_id'] for a in history[1]['buttons'].values()},{'fixture-cruise'})
        self.assertEqual({a['product_id'] for a in history[2]['buttons'].values()},{'fixture-beach'})
        self.assertIsNone(self.t.store.session(self.t.scope())['active_itinerary_id'])
        self.assertTrue(self.send(reply));self.assertEqual(len(self.requests),3)
        # First card remains valid after second card: both belong to one latest plan.
        gallery=self.click_visual(plan,'fixture-cruise','photos','gallery')
        self.assertEqual(gallery['asset_ids'],['cruise-1','cruise-2'])
        self.assertEqual(gallery['product_ids'],['fixture-cruise'])
        self.assertTrue(self.send(envelope(gallery)))
        self.assertEqual(len(self.requests),5)
        self.assertIsNone(self.t.store.session(self.t.scope())['active_itinerary_id'])

    def test_warm_introduction_no_forced_products_or_booking(self):
        d=response(products=[],fact_keys=[])
        d['hospitality']=hospitality('Welcome! A month on Curaçao gives you time to explore at your own pace.','Who are you travelling with?',stage='welcome')
        reply=self.visual('welcome',d,text='Hello, I will be on holiday in Curaçao for a month.');self.assertTrue(self.send(reply))
        self.assertEqual(len(self.requests),1);self.assertNotIn('attachmentUrl',self.requests[0]);self.assertNotIn('buttons',self.requests[0])
        self.assertIsNone(self.t.store.session(self.t.scope())['active_itinerary_id'])

    def test_missing_local_media_and_unverified_location_are_truthful_text(self):
        self.t.discovery.media=FakeMedia(missing=['cruise-0','beach-0'])
        reply=self.visual();self.assertTrue(self.send(reply))
        self.assertTrue(all('attachmentUrl' not in b for b in self.requests))
        self.assertEqual(sum('Photos unavailable' in b['message'] for b in self.requests),2)
        d=self.decision();d['hospitality']['photo_location']='unverified-stop'
        reply=self.visual('location',d);before=len(self.requests);self.assertTrue(self.send(reply))
        self.assertTrue(all('attachmentUrl' not in b for b in self.requests[before:]))

    def test_explicit_photo_none_stays_text_only(self):
        reply=self.visual(decision=self.decision('none'));self.assertTrue(self.send(reply))
        self.assertTrue(all('attachmentUrl' not in b for b in self.requests))

    def test_native_details_include_trip_image(self):
        reply=self.visual();self.assertTrue(self.send(reply))
        details=self.click_visual(self.plan(reply),'fixture-cruise','info','details-image')
        images=[p for p in details['parts'] if p['body'].get('attachmentUrl')]
        self.assertEqual(len(images),1)
        self.assertEqual(images[0]['product_ids'],['fixture-cruise'])

    def test_carousel_sender_tracks_all_images_and_scoped_buttons(self):
        self.t.profile['gallery_mode']='carousel';self.t.write_profile(self.t.profile)
        reply=self.visual();self.assertTrue(self.send(reply));plan=self.plan(reply)
        carousel=[p for p in plan['parts'] if p['body'].get('interactive')]
        self.assertEqual(len(carousel),2)
        for part in carousel:
            self.assertEqual(len(part['assets']),3)
            self.assertEqual(len(part['body']['interactive']['action']['cards']),3)
            self.assertTrue(part['button_meanings'])
            self.assertTrue(all(a['product_id']==part['product_ids'][0] for a in part['button_meanings'].values()))
        details=self.click_visual(plan,'fixture-cruise','info','carousel-details')
        self.assertEqual(details['product_ids'],['fixture-cruise'])
        self.assertTrue(self.send(envelope(details)))

    def test_carousel_rejection_never_replays_or_enables_buttons(self):
        self.t.profile['gallery_mode']='carousel';self.t.write_profile(self.t.profile)
        reply=self.visual()
        self.assertFalse(self.send(reply,[(200,{'success':True,'data':{'messageId':'intro'}}),(400,{'code':'INVALID_MEDIA'})]))
        before=len(self.requests);self.assertFalse(self.send(reply));self.assertEqual(len(self.requests),before)
        self.assertEqual(self.plan(reply)['parts'][1]['status'],'rejected')

    def test_recommendation_cannot_accidentally_omit_images(self):
        d=self.decision('none');d['hospitality']['photo_opt_out']=False
        reply=self.visual(decision=d);self.assertTrue(self.send(reply))
        self.assertEqual(sum(bool(b.get('attachmentUrl')) for b in self.requests),2)

    def test_repeated_pitch_keeps_image_when_gallery_exhausted(self):
        catalog=json.loads(self.t.catalog_path.read_text())
        for p in catalog['products']:p['gallery']=p['gallery'][:1]
        self.t.catalog_path.write_text(json.dumps(catalog))
        self.assertTrue(self.send(self.visual()));before=len(self.requests)
        self.assertTrue(self.send(self.visual('repeat-pitch')))
        self.assertEqual(sum(bool(b.get('attachmentUrl')) for b in self.requests[before:]),2)

    def test_native_details_respect_text_only_preference(self):
        reply=self.visual(decision=self.decision('none'));self.assertTrue(self.send(reply))
        details=self.click_visual(self.plan(reply),'fixture-cruise','info','details-no-image')
        self.assertTrue(all('attachmentUrl' not in p['body'] for p in details['parts']))

    def test_second_card_rejection_preserves_first_and_blocks_actions(self):
        reply=self.visual();self.assertFalse(self.send(reply,[(200,{'success':True,'data':{'messageId':'intro'}}),(200,{'success':True,'data':{'messageId':'first'}}),(400,{'code':'INVALID_MEDIA'})]))
        plan=self.plan(reply);self.assertEqual([p['status'] for p in plan['parts']],['accepted','accepted','rejected'])
        before=len(self.requests);self.assertFalse(self.send(reply));self.assertEqual(len(self.requests),before)
        token=next(t for t,a in plan['button_meanings'].items() if a['product_id']=='fixture-cruise' and a['kind']=='add')
        result,calls=self.t.turn(token=token,trigger='partial-add');self.assertTrue(result['generation_failed']);self.assertEqual(calls,0)
        self.assertIsNone(self.t.store.session(self.t.scope())['active_itinerary_id'])

    def test_ambiguous_media_never_blindly_falls_back_or_retries(self):
        reply=self.visual();self.assertFalse(self.send(reply,[(200,{'success':True,'data':{'messageId':'intro'}}),TimeoutError('uncertain')]))
        self.assertEqual(len(self.requests),2);self.assertEqual(self.plan(reply)['parts'][1]['status'],'ambiguous')
        self.assertFalse(self.send(reply));self.assertEqual(len(self.requests),2)

    def test_card_cross_product_binding_is_rejected_before_send(self):
        d=self.decision();d['hospitality']['cards'][0]['paragraphs']=['{fact:fixture-beach:summary}']
        reply,calls,_=self.h.call(d,'wrong-card');self.assertTrue(reply['generation_failed']);self.assertEqual(calls,1)
        self.assertIsNone(self.t.store.session(self.t.scope())['active_itinerary_id'])

    def test_long_bound_card_uses_summary_and_details_keep_full_source(self):
        catalog=json.loads(self.t.catalog_path.read_text())
        detail='A verified source sentence describing the outing. '*50
        catalog['products'][0].setdefault('source_claims',{})['additional_information']=detail
        self.t.catalog_path.write_text(json.dumps(catalog))
        d=self.decision();d['fact_keys']=['summary','additional_information']
        d['hospitality']['cards'][0]['paragraphs']=['{fact:fixture-cruise:additional_information}']
        reply=self.visual(decision=d);self.assertTrue(self.send(reply));plan=self.plan(reply)
        self.assertTrue(all(units(b['message'])<700 for b in self.requests))
        self.assertIn('A relaxed coastal cruise.',self.requests[1]['message'])
        details=self.click_visual(plan,'fixture-cruise','info','long-details')
        self.assertIn('A verified source sentence',details['body']['message'])
        self.assertTrue(any(a['kind']=='info' and a['info_offset']>0 for a in details['button_meanings'].values()))
        snapshot=self.t.discovery.source_snapshot(self.t.scope(),details)
        self.assertEqual(snapshot['catalog']['products'][0]['source_claims']['additional_information'],detail)

    def test_empty_and_exhausted_gallery_explain_missing_photos(self):
        catalog=json.loads(self.t.catalog_path.read_text())
        for p in catalog['products']:p['gallery']=[]
        self.t.catalog_path.write_text(json.dumps(catalog))
        reply=self.visual();self.assertTrue(self.send(reply))
        self.assertEqual(sum('Photos unavailable' in b['message'] for b in self.requests),2)
        self.assertTrue(all('attachmentUrl' not in b for b in self.requests))

    def test_sparse_dutch_details_use_one_bound_call_then_native_pages(self):
        catalog=json.loads(self.t.catalog_path.read_text())
        catalog['products'][0]['inclusions']=['A guide is included.']
        catalog['products'][0]['source_claims']={}
        self.t.catalog_path.write_text(json.dumps(catalog))
        d=response(language='nl',products=['fixture-cruise']);d['intent']='discover'
        d['translations']={'fixture-cruise':{'summary':'Een rustige vaartocht langs de kust.'}}
        d['hospitality']=hospitality('Dit past bij jullie rustige vakantie.','Lijkt dit jullie leuk?',photo='initial',stage='recommendation')
        d['hospitality']['cards']=[{'product_id':'fixture-cruise','paragraphs':['{fact:fixture-cruise:summary}']}]
        first=self.visual('dutch',d,text='Wij zijn op vakantie met het gezin en houden van rustige boottochten en stranden.');self.assertTrue(self.send(first));plan=self.plan(first)
        token=next(t for t,a in plan['button_meanings'].items() if a['kind']=='info')
        detail=response(language='nl',products=['fixture-cruise'],fact_keys=['summary','inclusion_0'])
        detail['translations']={'fixture-cruise':{'summary':'Een rustige vaartocht langs de kust.','inclusion_0':'Een gids is inbegrepen.'}}
        detail['hospitality']=hospitality('Hier zijn de reisdetails.')
        result,calls,_=self.h.call(detail,'dutch-detail',text='Reisdetails',interactive_id=token)
        self.assertEqual(calls,1);self.assertNotIn('generation_failed',result);self.assertTrue(self.send(result))
        self.assertIn('Een gids is inbegrepen.',result['text'])
        self.assertIsNone(self.t.store.session(self.t.scope())['active_itinerary_id'])
        again,calls,_=self.h.call(detail,'dutch-detail',text='Reisdetails',interactive_id=token)
        self.assertEqual(calls,0);before=len(self.requests);self.assertTrue(self.send(again));self.assertEqual(len(self.requests),before)
        latest=self.plan(result);next_plan=self.click_visual(latest,'fixture-cruise','info','dutch-native')
        self.assertFalse(next_plan.get('detail_translation_required'))
        self.assertIn('Een gids is inbegrepen.',next_plan['body']['message'])
        self.assertTrue(self.send(envelope(next_plan)))

    def test_recommendation_photo_plan_and_intake_are_separate_authorities(self):
        first=self.visual();self.assertTrue(self.send(first));plan=self.plan(first)
        gallery=self.click_visual(plan,'fixture-cruise','photos','journey-photos');self.assertTrue(self.send(envelope(gallery)))
        token=next(t for t,a in gallery['button_meanings'].items() if a['kind']=='add')
        result,calls=self.t.turn(token=token,trigger='journey-plan')
        self.assertEqual(calls,0);self.assertNotIn('generation_failed',result)
        session=self.t.store.session(self.t.scope());self.assertTrue(session['pending'])
        self.assertEqual({p['product_id'] for p in session['pending'].values()},{'fixture-cruise'})
        self.assertFalse(self.t.itinerary.get(self.t.scope(),session['active_itinerary_id'])['items'])
        self.assertTrue(self.send(result))
        self.assertNotIn('Demo itinerary saved',result['text'])
        pending_id=next(iter(session['pending']))
        d=response('update',updates=[{'item_id':pending_id}],guest={'name':'Alex'},products=['fixture-cruise'])
        d['hospitality']=hospitality('Thanks, Alex.','Could you share the {missing_field}?',action='update',evidence='My name is Alex',replies={'error':{'paragraphs':['{operation}'],'question':''}})
        updated,calls,_=self.h.call(d,'journey-intake',text='My name is Alex')
        self.assertEqual(calls,1);self.assertNotIn('generation_failed',updated)
        self.assertEqual(self.t.store.session(self.t.scope())['guest']['name'],'Alex')
        self.assertIn('ages',updated['text'])
        self.assertTrue(self.send(updated))

    def test_fresh_text_fallback_after_rejection_does_not_replay_media(self):
        first=self.visual();self.assertFalse(self.send(first,[(400,{'code':'INVALID_MEDIA'})]))
        d=self.decision('none');d['hospitality']['replies']['browsing']['paragraphs']=['The earlier reply could not be sent. Here are the trip details in text.']
        next_reply=self.visual('fresh-text',d,text='Please send the trip details as text.');before=len(self.requests);self.assertTrue(self.send(next_reply))
        self.assertTrue(all('attachmentUrl' not in body for body in self.requests[before:]))
        self.assertEqual(self.plan(first)['parts'][0]['status'],'rejected')

    def test_explicit_question_keeps_full_requested_answer(self):
        catalog=json.loads(self.t.catalog_path.read_text())
        answer='Verified answer about the requested arrangements. '*30
        catalog['products'][0].setdefault('source_claims',{})['additional_information']=answer
        self.t.catalog_path.write_text(json.dumps(catalog))
        d=response(products=['fixture-cruise'],fact_keys=['additional_information'])
        d['hospitality']=hospitality('{fact:fixture-cruise:additional_information}',photo='none')
        result=self.visual('direct-question',d,text='Please tell me the full arrangements for this cruise.');self.assertTrue(self.send(result))
        self.assertIn(answer,''.join(b['message'] for b in self.requests))
        self.assertTrue(any('attachmentUrl' in b for b in self.requests))

    def test_predispatch_hold_does_not_consume_an_image(self):
        first=self.visual();self.assertFalse(self.send(first,window=False));self.assertEqual(self.requests,[])
        second=self.visual('after-window');self.assertTrue(self.send(second))
        self.assertEqual([b['attachmentUrl'] for b in self.requests if b.get('attachmentUrl')],['https://example.invalid/cruise-0.jpg','https://example.invalid/beach-0.jpg'])

    def test_verified_place_context_inline_without_claiming_photo_location(self):
        catalog=json.loads(self.t.catalog_path.read_text())
        catalog['products'][0].setdefault('source_claims',{})['location_context']={'text':'Harbour Island is an island reached by boat.','source_url':'https://example.invalid/verified-listing'}
        self.t.catalog_path.write_text(json.dumps(catalog))
        result=self.visual('place');self.assertTrue(self.send(result))
        self.assertIn('Harbour Island is an island reached by boat.',self.requests[1]['message'])
        self.assertEqual(self.plan(result)['parts'][1]['assets'],[{'product_id':'fixture-cruise','asset_id':'cruise-0'}])

    def test_media_callback_before_http_result_stops_remaining_card(self):
        d=self.decision();d['hospitality']['replies']['browsing']['paragraphs']=['']
        result=self.visual('early-media-callback',d)
        self.assertFalse(self.send(result,callback_before_response=True))
        self.assertEqual(len(self.requests),1)
        plan=self.plan(result);self.assertEqual(plan['parts'][0]['status'],'provider_failed')
        self.assertEqual(plan['parts'][1]['status'],'queued')
        history=self.t.store.session(self.t.scope())['history'][-1]
        self.assertEqual(history['media'],[{'product_id':'fixture-cruise','asset_id':'cruise-0'}])
        self.assertEqual(history['delivery_status'],'provider_failed')

    def test_foreign_and_stale_card_buttons_do_not_apply(self):
        from dataclasses import replace
        from shared.isluno_pricing import ItineraryError
        first=self.visual();self.assertTrue(self.send(first));plan=self.plan(first)
        token=next(t for t,a in plan['button_meanings'].items() if a['kind']=='add')
        with self.assertRaises(ItineraryError):
            self.t.discovery.plan(replace(self.t.scope(),customer_ref='another-guest'),'foreign',self.now.isoformat(),action_token=token,interactive_type='button_reply')
        self.visual('new-context')
        result,calls=self.t.turn(token=token,trigger='stale-card')
        self.assertEqual(calls,0);self.assertTrue(result['generation_failed'])
        self.assertIsNone(self.t.store.session(self.t.scope())['active_itinerary_id'])

    def test_exhausted_gallery_has_no_false_new_photo_claim(self):
        catalog=json.loads(self.t.catalog_path.read_text())
        for p in catalog['products']:p['gallery']=p['gallery'][:1]
        self.t.catalog_path.write_text(json.dumps(catalog))
        first=self.visual();self.assertTrue(self.send(first));before=len(self.requests)
        d=self.decision('more');d['hospitality']['replies']['browsing']['paragraphs']=['Here are the available trip details.']
        second=self.visual('exhausted',d);self.assertTrue(self.send(second))
        self.assertTrue(all('attachmentUrl' not in b for b in self.requests[before:]))
        self.assertEqual(sum('Photos unavailable' in b['message'] for b in self.requests[before:]),2)

# unittest would otherwise inherit the whole communication suite for every visual run.
for _name in dir(CommunicationWireTests):
    if _name.startswith('test_') and _name not in VisualDiscoveryTests.__dict__:
        setattr(VisualDiscoveryTests,_name,None)
