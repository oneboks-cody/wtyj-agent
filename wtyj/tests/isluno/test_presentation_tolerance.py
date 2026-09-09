"""Synthetic variants of the captured invalid_reply_style boundary, not captured model prose."""
import copy
import json
import unittest
from test_no_reply import NoReplyTests
from test_conversation import response
from hospitality_fixtures import hospitality

INTRO='Hi , im calvin and interested in activity in curacao, we are arriving next week and staying for a month'

class PresentationToleranceTests(unittest.TestCase):
 def setUp(self):
  self.h=NoReplyTests('test_unknown_source_fact_remains_rejected_and_never_rendered');self.h.setUp();self.addCleanup(self.h.doCleanups)
 def decision(self,text='Welcome, Calvin — happy to help you explore.'):
  d=response(products=[],fact_keys=[],guest={'name':'Calvin'})
  d['hospitality']=hospitality(text,'Would you prefer a relaxed day – or something active?',stage='welcome',memory={'holiday':'Arriving next week, staying for a month'})
  return d
 def test_unicode_punctuation_at_actual_sdk_boundary_keeps_intro_browsing(self):
  decision=self.decision();original=copy.deepcopy(decision)
  reply,calls,logs=self.h.call(decision,'intro-punctuation',text=INTRO)
  self.assertNotIn('generation_failed',reply);self.assertEqual(calls,1)
  self.assertIn('Welcome, Calvin',reply['text']);self.assertNotIn('—',reply['text']);self.assertNotIn('–',reply['text'])
  session=self.h.t.store.session(self.h.t.scope())
  self.assertIsNone(session['active_itinerary_id']);self.assertFalse(session['pending'])
  self.assertEqual(session['browsing']['holiday'],'Arriving next week, staying for a month')
  self.assertEqual(decision,original)
  from agents.social.isluno_delivery import send_plan
  scope=self.h.t.scope();bodies=[]
  def post(scope,body,key):
   bodies.append(body);return {'status':'accepted','provider_id':'synthetic-punctuation'}
  self.assertTrue(send_plan(scope.conversation_id,scope.account_id,reply['media']['url'],store=self.h.t.discovery,post=post,window=lambda *a:{'open':True},sleep=lambda _:None))
  session=self.h.t.store.session(scope)
  self.assertEqual(session['history'][-1]['content'],reply['text'])
  self.assertIn(' - ',session['last_accepted_question'])
  with self.h.t.store.db() as db:
   for table in ('isluno_itineraries','isluno_quote_jobs','isluno_payments'):
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone():
     self.assertEqual(db.execute('SELECT count(*) FROM '+table).fetchone()[0],0)
  self.assertFalse(any(c.args==('isluno_model_contract_failed',) for c in logs))
 def test_unused_branch_punctuation_does_not_reject_valid_browsing(self):
  d=self.decision('Welcome, happy to help.')
  d['hospitality']['replies']['error']={'paragraphs':['That change failed — {operation}'],'question':''}
  reply,calls,_=self.h.call(d,'unused-branch',text=INTRO)
  self.assertNotIn('generation_failed',reply);self.assertNotIn('failed',reply['text']);self.assertEqual(calls,1)
 def test_bound_source_typography_is_normalized_after_identifier_resolution(self):
  catalog=json.loads(self.h.t.catalog_path.read_text());product=catalog['products'][0]
  product['name']='Harbour–Bay';product['summary']='Explore the harbour — a relaxed trip.'
  self.h.t.catalog_path.write_text(json.dumps(catalog))
  d=self.decision('Try {name:fixture-cruise}. {fact:fixture-cruise:summary}')
  d['product_ids']=['fixture-cruise'];d['fact_keys']=['summary']
  reply,calls,_=self.h.call(d,'source-typography',text=INTRO)
  self.assertNotIn('generation_failed',reply);self.assertEqual(calls,1)
  self.assertIn('Harbour-Bay',reply['text']);self.assertIn('harbour - a relaxed trip',reply['text'])
 def test_unsupported_fact_still_fails_with_cosmetic_punctuation(self):
  d=self.decision('Welcome — {fact:invented:summary}')
  reply,calls,logs=self.h.call(d,'unsafe-fact',text=INTRO)
  self.assertTrue(reply['generation_failed']);self.assertEqual(calls,1)
  self.assertTrue(any(c.kwargs.get('code')=='unsupported_reply_fact' for c in logs))
 def test_type_and_size_remain_distinct_fatal_contract_errors(self):
  for index,(value,code) in enumerate([(None,'invalid_reply_text_type'),('x'*1801,'invalid_reply_text_length')]):
   with self.subTest(code=code):
    d=self.decision();d['hospitality']['replies']['browsing']['paragraphs']=[value]
    reply,calls,logs=self.h.call(d,'structural-'+str(index),text=INTRO)
    self.assertTrue(reply['generation_failed']);self.assertEqual(calls,1)
    diagnostic=next(c.kwargs for c in logs if c.args==('isluno_model_contract_failed',))
    self.assertEqual(diagnostic['code'],code)
    self.assertEqual(diagnostic['stop_reason'],'unavailable')
    self.assertEqual(diagnostic['reply_nonstring_count'],int(value is None))
    self.assertNotIn('Calvin',str(diagnostic));self.assertNotIn('xxx',str(diagnostic))
 def test_typography_preserves_range_and_name_meaning(self):
  from agents.social.isluno_hospitality import presentation_text
  self.assertEqual(presentation_text('Hours 10–12; Harbour–Bay — welcome.'), 'Hours 10-12; Harbour-Bay - welcome.')
if __name__=='__main__':unittest.main()
