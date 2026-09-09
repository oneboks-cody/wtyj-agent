import hashlib,sqlite3,unittest
from datetime import timedelta
from unittest.mock import patch
import retained_history as operator
import test_conversation as conversation
from shared import state_registry,mermaid_maintenance as m
from agents.social.isluno_recovery import RecoveryStore
from agents.social.isluno_delivery import send_plan

class OperatorTests(unittest.TestCase):
 def setUp(self):
  self.t=conversation.ConversationTests('test_conversation_sdk_uses_single_existing_model_request');self.t.setUp();self.addCleanup(self.t.doCleanups)
  self.path=str(self.t.itinerary.db_path)
  p=patch.object(state_registry,'DB_PATH',self.path);p.start();self.addCleanup(p.stop)
  state_registry._get_conn().close()
  c=sqlite3.connect(self.path);c.executescript(m.SCHEMA+"CREATE TABLE isluno_cutover(tenant TEXT PRIMARY KEY,activated_at TEXT NOT NULL); INSERT INTO isluno_cutover VALUES('mermaid','fixture'); INSERT INTO mermaid_maintenance VALUES('mermaid',8,'open',0,'[]','');");c.close()
  self.t.store.reserve(self.t.scope(),'failed')
  recovery=RecoveryStore(self.t.store);recovery.observe(self.t.scope(),'failed',conversation.NOW.isoformat());recovery.incident(self.t.scope(),'failed','understanding_failure','ItineraryError')
  for trigger in ('failed','complete'):state_registry.wa_claim_inbound_processing(trigger,self.t.scope().conversation_id,'whatsapp',payload={'text':'synthetic'})
  state_registry.inbound_processing_bulk_update(['failed'],'ignored',reason='no_reply_returned')
  result,_=self.t.turn(conversation.response(products=[],fact_keys=[]),trigger='complete')
  c=sqlite3.connect(self.path);plan=c.execute('SELECT id FROM isluno_discovery_plans').fetchone()[0];c.close()
  self.assertTrue(send_plan(self.t.scope().conversation_id,self.t.scope().account_id,plan,store=self.t.discovery,post=lambda *a:{'status':'accepted','provider_id':'fixture'},window=lambda *a:{'open':True},sleep=lambda *a:None))
  state_registry.inbound_processing_bulk_update(['complete'],'replied',reason='provider_send_ok')
  from agents.social.isluno_conversation import failure_reply
  failure_ids=('style-one','style-two')
  patcher=patch.object(operator,'FAILURE_HASHES',{hashlib.sha256(t.encode()).hexdigest() for t in failure_ids});patcher.start();self.addCleanup(patcher.stop)
  for index,trigger in enumerate(failure_ids,1):
   self.t.discovery.clock=lambda index=index:conversation.NOW+timedelta(seconds=index*10)
   state_registry.wa_claim_inbound_processing(trigger,self.t.scope().conversation_id,'whatsapp',payload={'text':'synthetic'})
   self.t.store.reserve(self.t.scope(),trigger)
   recovery.observe(self.t.scope(),trigger,conversation.NOW.isoformat());recovery.incident(self.t.scope(),trigger,'understanding_failure','invalid_reply_style')
   reply=failure_reply(self.t.store,self.t.discovery,self.t.scope(),trigger,{'text':'Synthetic failed greeting','_zernio_sent_at':conversation.NOW.isoformat()},understanding_failed=True)
   self.assertTrue(send_plan(self.t.scope().conversation_id,self.t.scope().account_id,reply['media']['url'],store=self.t.discovery,post=lambda *a:{'status':'accepted','provider_id':'fixture-'+trigger},window=lambda *a:{'open':True},sleep=lambda *a:None))
   state_registry.inbound_processing_bulk_update([trigger],'replied',reason='provider_send_ok')
 def proof(self):
  c=sqlite3.connect(self.path)
  try:return operator.review(c)
  finally:c.close()
 def drain(self):m.close(8,seconds=120,coverage=m.COVERAGE,evidence='fixture',db_path=self.path)
 def test_seal_readiness_reopen_preserve_rows(self):
  before=self.proof();self.drain()
  with self.assertRaises(m.Closed):m.seal(9,db_path=self.path)
  operator.seal(self.path,9,before['sha256']);self.assertEqual(operator.readiness(self.path,9,before['sha256']),before)
  operator.reopen(self.path,9,before['sha256']);self.assertEqual(self.proof(),before)
  self.assertEqual((m.status(self.path)['phase'],m.status(self.path)['generation']),('open',10))
  self.assertFalse(state_registry.wa_claim_inbound_processing('complete',self.t.scope().conversation_id,'whatsapp',payload={'text':'duplicate'}))
 def test_stale_hash_does_not_seal(self):
  self.drain()
  with self.assertRaisesRegex(ValueError,'stale_history_proof'):operator.seal(self.path,9,'0'*64)
  self.assertEqual(m.status(self.path)['phase'],'draining')
 def test_uncertainty_active_work_wrong_binding_rejected(self):
  mutations=["UPDATE isluno_discovery_plans SET status='ambiguous'", "UPDATE isluno_discovery_plans SET trigger_id='unrelated' WHERE rowid=(SELECT min(rowid) FROM isluno_discovery_plans)", "UPDATE inbound_processing_events SET processing_token='active' WHERE message_id='complete'", "UPDATE isluno_recovery_incidents SET status='open'", "INSERT INTO mermaid_maintenance_workers VALUES('worker',8,'inbound','active',1,NULL,'')", "UPDATE isluno_booking_sessions SET payload=json_set(payload,'$.active_itinerary_id','unknown')"]
  for sql in mutations:
   with self.subTest(sql=sql):
    c=sqlite3.connect(self.path);c.execute('BEGIN')
    try:
     c.execute(sql)
     with self.assertRaises(ValueError):operator.review(c)
    finally:c.rollback();c.close()
 def test_abort_unsealed_drain_preserves_unfinished_work(self):
  before=self.proof();self.drain()
  c=sqlite3.connect(self.path);c.execute("INSERT INTO mermaid_maintenance_workers VALUES('worker',9,'inbound','active',1,NULL,'')");c.commit();c.close()
  operator.abort_drain(self.path,9,'fixture-packet')
  self.assertEqual(m.status(self.path)['phase'],'open')
  c=sqlite3.connect(self.path);self.assertEqual(c.execute("SELECT count(*) FROM mermaid_maintenance_workers WHERE id='worker'").fetchone()[0],1);c.execute("DELETE FROM mermaid_maintenance_workers WHERE id='worker'");c.commit();c.close()
  self.assertEqual(self.proof(),before)
 def test_abort_cannot_reopen_sealed_or_wrong_generation(self):
  proof=self.proof();self.drain()
  with self.assertRaises(ValueError):operator.abort_drain(self.path,8,'fixture')
  operator.seal(self.path,9,proof['sha256'])
  with self.assertRaises(ValueError):operator.abort_drain(self.path,9,'fixture')
  self.assertEqual(m.status(self.path)['phase'],'sealed')
if __name__=='__main__':unittest.main()
