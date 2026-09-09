import importlib.util,json,sqlite3,unittest
from pathlib import Path
from unittest.mock import patch
import retained_history as operator
from test_conversation import ConversationTests
from shared import state_registry,mermaid_maintenance as maintenance

class EmptyOperatorTests(unittest.TestCase):
 def setUp(self):
  self.t=ConversationTests('test_conversation_sdk_uses_single_existing_model_request');self.t.setUp();self.addCleanup(self.t.doCleanups)
  self.path=str(self.t.itinerary.db_path)
  p=patch.object(state_registry,'DB_PATH',self.path);p.start();self.addCleanup(p.stop);state_registry._get_conn().close()
  db=sqlite3.connect(self.path);db.executescript(maintenance.SCHEMA+"CREATE TABLE isluno_cutover(tenant TEXT PRIMARY KEY,activated_at TEXT NOT NULL); INSERT INTO isluno_cutover VALUES('mermaid','fixture'); INSERT INTO mermaid_maintenance VALUES('mermaid',16,'open',0,'[]','');")
  tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
  # Lazy tables need not exist until used in the fixture, unlike the pinned live schema.
  for table in set(operator.TABLES)|set(operator.EMPTY_TABLES):
   if table not in tables:
    column=maintenance.LEDGERS.get(table,('fixture',()))[0]
    db.execute('CREATE TABLE '+table+'('+column+' TEXT)')
  db.executemany("INSERT INTO inbound_processing_events(message_id,status,reason,created_at,updated_at) VALUES(?,'ignored','history_cleared_no_replay','fixture','fixture')",[(str(i),) for i in range(182)])
  db.commit();db.close()
 def proof(self):
  db=sqlite3.connect(self.path)
  try:return operator.review(db)
  finally:db.close()
 def test_standard_seal_reopen_retains_empty_context_and_tombstones(self):
  before=self.proof();self.assertEqual(before['turns'],0)
  maintenance.close(16,seconds=120,coverage=maintenance.COVERAGE,evidence='fixture',db_path=self.path)
  operator.seal(self.path,17,before['sha256']);self.assertEqual(operator.readiness(self.path,17,before['sha256']),before)
  operator.reopen(self.path,17,before['sha256']);self.assertEqual(self.proof(),before)
  self.assertEqual(maintenance.status(self.path)['generation'],18)
  self.assertFalse(state_registry.wa_claim_inbound_processing('0','synthetic','whatsapp',payload={'text':'old'}))
 def test_new_guest_or_changed_tombstone_stops_before_seal(self):
  db=sqlite3.connect(self.path);db.execute("UPDATE inbound_processing_events SET payload_json='secret' WHERE message_id='0'");db.commit();db.close()
  with self.assertRaisesRegex(ValueError,'tombstone_payload_or_status'):self.proof()
  db=sqlite3.connect(self.path);db.execute("UPDATE inbound_processing_events SET payload_json='{}' WHERE message_id='0'");db.execute("INSERT INTO isluno_operator_requests(fixture) VALUES('new')");db.commit();db.close()
  with self.assertRaisesRegex(ValueError,'new_customer_state'):self.proof()
 def test_stale_proof_and_invalid_generation_do_not_reopen(self):
  with self.assertRaisesRegex(ValueError,'stale_history_proof'):operator.proof_at(self.path,'stale')
  with self.assertRaisesRegex(ValueError,'not_sealed'):operator.reopen(self.path,17,self.proof()['sha256'])
  self.assertEqual(maintenance.status(self.path)['generation'],16)
