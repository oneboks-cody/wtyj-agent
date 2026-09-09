"""Readable output captured from actual handlers and strict provider-boundary fixtures."""
import copy,json
from pathlib import Path
from test_visual_discovery import VisualDiscoveryTests
from test_conversation import response
from hospitality_fixtures import hospitality
from shared.isluno_media import MediaLibrary
records=[]
names=['test_warm_introduction_no_forced_products_or_booking','test_two_cards_real_http_and_exact_history','test_sparse_dutch_details_use_one_bound_call_then_native_pages','test_recommendation_photo_plan_and_intake_are_separate_authorities','test_missing_local_media_and_unverified_location_are_truthful_text','test_explicit_question_keeps_full_requested_answer','test_fresh_text_fallback_after_rejection_does_not_replay_media']
for name in names+['actual_catalog_recommendations']:
 t=VisualDiscoveryTests(names[0] if name=='actual_catalog_recommendations' else name);t.setUp();events=[]
 original=t.h.call
 def call(decision,trigger,*args,**kwargs):
  result=original(decision,trigger,*args,**kwargs);events.append({'guest':kwargs.get('text','Synthetic request'),'model_calls':result[1],'prepared_reply':result[0].get('text'),'generation_failed':bool(result[0].get('generation_failed'))});return result
 t.h.call=call
 original_turn=t.t.turn
 def turn(*args,**kwargs):
  result=original_turn(*args,**kwargs);events.append({'guest':'[Plan this trip]' if kwargs.get('token') else 'Synthetic turn','model_calls':result[1],'prepared_reply':result[0].get('text')});return result
 t.t.turn=turn
 original_click=t.click_visual
 def click(plan,product,kind,trigger):
  result=original_click(plan,product,kind,trigger);events.append({'guest':'['+kind+' for '+product+']','model_calls':0,'prepared_reply':result['body']['message']});return result
 t.click_visual=click
 original_send=t.send
 def send(reply,*args,**kwargs):
  before=len(t.requests);result=original_send(reply,*args,**kwargs);plan=t.plan(reply);bodies=[]
  for body in t.requests[before:]:
   body=copy.deepcopy(body);body.pop('accountId',None)
   for b in body.get('buttons',[]):b['meaning']=plan['button_meanings'].get(b.pop('payload'),{})
   bodies.append(body)
  events.append({'actual_HTTP_payloads':bodies,'complete_provider_acceptance':result,'parts':[{'status':p['status'],'products':p.get('product_ids',[]),'assets':p.get('assets',[])} for p in plan['parts']]});return result
 t.send=send
 try:
  if name=='actual_catalog_recommendations':
   catalog=Path('clients/mermaid/config/isluno_catalog.json');t.t.catalog_path.write_bytes(catalog.read_bytes())
   t.t.discovery.media=MediaLibrary(t.t.catalog_path,Path('wtyj/assets/isluno'),'https://api.unboks.org/api/mermaid/r/isluno/media')
   ids=['klein-curacao-catamaran-day-trip','snorkel-and-beach-adventures'];d=response(products=ids,fact_keys=['summary','location_context']);d['intent']='discover'
   d['hospitality']=hospitality('For a day on the water or a mix of beaches and sightseeing, these are two options.','Which sounds more like your kind of day?',photo='initial',stage='recommendation')
   d['hospitality']['cards']=[{'product_id':ids[0],'paragraphs':['{fact:'+ids[0]+':summary}']},{'product_id':ids[1],'paragraphs':['{fact:'+ids[1]+':summary}']}]
   result=t.visual('real-catalog',d);assert t.send(result)
  else:getattr(t,name)()
  session=t.t.store.session(t.t.scope());records.append({'scenario':name,'events':events,'final_state':{'pending_products':[p.get('product_id') for p in session['pending'].values()],'active_itinerary_present':bool(session['active_itinerary_id'])},'passed':True})
 finally:t.doCleanups()
out=Path('output/isluno-visual-discovery')
(out/'VISUAL-CONVERSATIONS.json').write_text(json.dumps({'application_source':'0361641575552e5a78a671efbc1dd59b9d5196ba','evidence':'Scripted SDK, strict fake HTTP, network-disabled Docker. Actual catalog case uses verified stored gallery bytes and public delivery URLs but makes no provider request. No live model/native-language quality claim.','scenarios':records},indent=2,ensure_ascii=False)+'\n')
lines=['# Progressive visual WhatsApp examples','','Actual renderer and sender payloads with scripted model output and network denial. Product prices/rules are not changed. No engineering customer send.','']
for record in records:
 lines+=['## '+record['scenario'],'']
 for event in record['events']:
  if 'guest' in event:lines+=['Guest: '+event['guest'],'','Model calls: '+str(event['model_calls']),'']
  else:
   for body in event['actual_HTTP_payloads']:
    if body.get('attachmentUrl'):lines+=['Image: '+body['attachmentUrl'],'']
    lines+=[body['message'],'']
    if body.get('buttons'):lines+=['Actions: '+', '.join(b['title']+' → '+b['meaning'].get('product_id','') for b in body['buttons']),'']
   lines+=['Complete provider acceptance (fixture): '+str(event['complete_provider_acceptance']),'']
 lines+=['Final state: '+json.dumps(record['final_state']),'']
(out/'VISUAL-CONVERSATIONS.md').write_text('\n'.join(lines))
print(json.dumps({'scenarios':len(records),'passed':True}))
