"""Network-disabled scripted real-handler checks for the scoped operator proof."""
import copy
import importlib.util
from pathlib import Path
import sqlite3
import time
import unittest
from unittest.mock import patch

from test_first_contact import FirstContactTests

spec = importlib.util.spec_from_file_location('sunny_history', Path(__file__).with_name('history.py'))
history = importlib.util.module_from_spec(spec)
spec.loader.exec_module(history)


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.f = FirstContactTests('test_fresh_context_warm_question_and_optional_emoji_reach_wire')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        reply, _ = self.f.call()
        self.assertTrue(self.f.w.send(reply))
        self.path = str(self.f.t.itinerary.db_path)
        with sqlite3.connect(self.path) as db:
            db.executescript(history.maintenance.SCHEMA)
            db.execute("INSERT OR REPLACE INTO mermaid_maintenance VALUES ('mermaid',19,'draining',?,?,?)",
                       (time.time()+120, '[]', 'synthetic'))
            db.execute('CREATE TABLE IF NOT EXISTS isluno_cutover (tenant TEXT)')
            if not db.execute('SELECT 1 FROM isluno_cutover').fetchone():
                db.execute("INSERT INTO isluno_cutover (tenant) VALUES ('mermaid')")
            # The direct handler harness bypasses the real webhook inbound ledger.
            db.execute('CREATE TABLE IF NOT EXISTS inbound_processing_events (message_id TEXT,reason TEXT,conversation_id TEXT,channel TEXT,status TEXT,processing_token TEXT,lease_expires_at TEXT,last_error TEXT)')
            db.execute('INSERT INTO inbound_processing_events (message_id,reason,conversation_id,channel,status,processing_token,lease_expires_at,last_error) VALUES (?,?,?,?,?,?,?,?)',
                       ('fresh','provider_send_ok',self.f.t.scope().conversation_id,'whatsapp','replied','','',''))
            db.execute('CREATE TABLE IF NOT EXISTS whatsapp_processed (message_id TEXT)')
            db.execute("INSERT INTO whatsapp_processed (message_id) VALUES ('fresh')")
        self.state = dict(workers={}, pending={}, incidents=[], unreviewed_ledgers=history.UNKNOWN,
                          generation=19, phase='draining', coverage_complete=True, deadline=time.time()+120)
        self.mock = patch.object(history.maintenance, '_snapshot', side_effect=lambda _: copy.deepcopy(self.state))
        self.mock.start()
        self.addCleanup(self.mock.stop)

    def op(self, action, expected=None):
        return history.operate(action,19,expected,path=self.path)

    def test_completed_history_seals_and_reopens_without_customer_changes(self):
        proof=self.op('review')
        self.assertEqual(self.op('seal',proof['sha256']),proof)
        self.state['phase']='sealed'
        self.assertEqual(self.op('ready',proof['sha256']),proof)
        self.assertEqual(self.op('reopen',proof['sha256']),proof)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT generation,phase FROM mermaid_maintenance').fetchone(),(20,'open'))
            self.assertEqual(history.fingerprint(db),proof)

    def test_unknown_ledger_and_pending_execution_block(self):
        for key,value in [('unreviewed_ledgers',history.UNKNOWN+['isluno_new_jobs']),('pending',{'inbound':1}),('workers',{'running':1})]:
            old=self.state[key];self.state[key]=value
            with self.assertRaises(ValueError):self.op('review')
            self.state[key]=old

    def test_unfinished_turn_and_ambiguous_transport_block(self):
        with sqlite3.connect(self.path) as db:db.execute("UPDATE isluno_discovery_plans SET status='ambiguous'")
        with self.assertRaisesRegex(ValueError,'plan_not_accepted'):self.op('review')
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE isluno_discovery_plans SET status='accepted'")
            db.execute('UPDATE isluno_conversation_turns SET outcome=NULL')
        with self.assertRaisesRegex(ValueError,'unfinished_turn'):self.op('review')

    def test_changed_customer_data_and_expired_deadline_block(self):
        proof=self.op('review')
        with sqlite3.connect(self.path) as db:db.execute("UPDATE isluno_booking_sessions SET payload=payload || ' '")
        with self.assertRaisesRegex(ValueError,'customer_history_changed'):self.op('seal',proof['sha256'])
        self.state['deadline']=0
        with self.assertRaisesRegex(ValueError,'drain_deadline'):self.op('seal',self.op('review')['sha256'])


if __name__ == '__main__':unittest.main()
