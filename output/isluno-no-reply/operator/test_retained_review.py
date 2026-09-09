import hashlib,sqlite3,sys,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,'/workspace/tmp/isluno-no-reply')
import retained_review as operator
import test_conversation as conversation
NOW=conversation.NOW
from shared import state_registry,mermaid_maintenance as m
from agents.social.isluno_recovery import RecoveryStore

class RetainedReviewTests(unittest.TestCase):
 def setUp(self):
  self.t=conversation.ConversationTests('test_conversation_sdk_uses_single_existing_model_request');self.t.setUp();self.addCleanup(self.t.doCleanups)
  self.path=str(self.t.itinerary.db_path);self.trigger='synthetic-failed-turn';self.sha=hashlib.sha256(self.trigger.encode()).hexdigest()
  p=patch.object(state_registry,'DB_PATH',self.path);p.start();self.addCleanup(p.stop)
  c=state_registry._get_conn();c.close()
  self.t.store.reserve(self.t.scope(),self.trigger)
  recovery=RecoveryStore(self.t.store);recovery.observe(self.t.scope(),self.trigger,NOW.isoformat());recovery.incident(self.t.scope(),self.trigger,'understanding_failure','ItineraryError')
  c=sqlite3.connect(self.path);c.executescript(m.SCHEMA+"CREATE TABLE isluno_cutover(tenant TEXT PRIMARY KEY,activated_at TEXT NOT NULL); INSERT INTO isluno_cutover VALUES('mermaid','fixture'); INSERT INTO mermaid_maintenance VALUES('mermaid',4,'open',0,'[]','');");c.close()
  state_registry.wa_claim_inbound_processing(self.trigger,self.t.scope().conversation_id,'whatsapp',payload={'text':'synthetic'})
  state_registry.inbound_processing_bulk_update([self.trigger],'ignored',reason='no_reply_returned')
 def proof(self):
  c=sqlite3.connect(self.path)
  try:return operator.review(c,self.sha)
  finally:c.close()
 def test_atomic_seal_preserves_customer_rows_and_normal_gate_still_rejects(self):
  before=self.proof();m.close(4,seconds=120,coverage=m.COVERAGE,evidence='synthetic',db_path=self.path)
  with self.assertRaises(m.Closed):m.seal(5,db_path=self.path)
  operator.seal(self.path,5,self.sha,before['sha256'])
  self.assertEqual(operator.readiness(self.path,5,self.sha,before['sha256']),before)
  self.assertEqual(self.proof(),before)
  self.assertEqual(m.status(self.path)['unreviewed_ledgers'],operator.UNKNOWN)
  operator.reopen(self.path,5,self.sha,before['sha256'])
  self.assertEqual((m.status(self.path)['phase'],m.status(self.path)['generation']),('open',6))
  self.assertEqual(self.proof(),before)
  self.assertFalse(state_registry.wa_claim_inbound_processing(self.trigger,self.t.scope().conversation_id,'whatsapp',payload={'text':'duplicate'}))
 def test_stale_fingerprint_rolls_back_without_sealing(self):
  proof=self.proof();m.close(4,seconds=120,coverage=m.COVERAGE,evidence='synthetic',db_path=self.path)
  c=sqlite3.connect(self.path);c.execute("UPDATE inbound_processing_events SET payload_json='{}'");c.commit();c.close()
  with self.assertRaisesRegex(ValueError,'stale_retained_proof'):operator.seal(self.path,5,self.sha,proof['sha256'])
  self.assertEqual(m.status(self.path)['phase'],'draining')
 def test_extra_rows_active_workers_and_outbound_uncertainty_reject(self):
  mutations=["UPDATE inbound_processing_events SET outbound_attempted_at='attempt'", "UPDATE inbound_processing_events SET processing_token='active'", "UPDATE isluno_recovery_incidents SET kind='understanding_stalled'", "INSERT INTO mermaid_maintenance_workers VALUES('worker',4,'inbound','active',1,NULL,'')"]
  for sql in mutations:
   c=sqlite3.connect(self.path);c.execute('BEGIN');c.execute(sql)
   try:
    with self.assertRaises(ValueError):operator.review(c,self.sha)
   finally:c.rollback();c.close()
  with self.assertRaisesRegex(ValueError,'different_test_turn'):
   c=sqlite3.connect(self.path)
   try:operator.review(c,'0'*64)
   finally:c.close()

if __name__=='__main__':unittest.main()
