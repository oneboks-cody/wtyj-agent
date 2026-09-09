"""Synthetic detached SQLite snapshots; no runtime/customer data or network."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

SCRIPT=Path(__file__).resolve().parents[2]/'scripts/project_isluno_old_work.py'
spec=importlib.util.spec_from_file_location('old_work',SCRIPT);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


class ProjectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name).resolve()/'snapshot.sqlite3'
        with sqlite3.connect(self.path) as db:
            for name,cols in m.TABLES.items():
                fields=[c+(' INTEGER' if c in ('attempts','expires_at') else ' TEXT') for c in cols]
                db.execute('CREATE TABLE '+name+' ('+','.join(fields)+')')
        self.path.chmod(0o600)

    def insert(self,table,values):
        with sqlite3.connect(self.path) as db:db.execute('INSERT INTO '+table+' VALUES ('+','.join('?' for _ in values)+')',values)

    def test_empty_projection_readonly_same_byte_hash(self):
        before=self.path.read_bytes();result=m.project(str(self.path))
        self.assertEqual(result['blocking_records'],0)
        self.assertFalse(result['activation_authorized']);self.assertFalse(result['quiescence_proven_by_this_tool'])
        self.assertEqual(result['snapshot_sha256'],hashlib.sha256(before).hexdigest())
        self.assertEqual(self.path.read_bytes(),before)
        self.assertEqual(set(p.name for p in self.path.parent.iterdir()),{'snapshot.sqlite3'})

    def test_failed_send_not_terminal_and_draft_not_completed(self):
        self.insert('inbound_processing_events',('PRIVATE-ID','whatsapp','send_failed','provider_send_failed','CANARY','','','attempted'))
        self.insert('whatsapp_processed',('PRIVATE-ID',))
        self.insert('mermaid_reservations',('PRIVATE-RES','mermaid','quote_ready',None))
        result=m.project(str(self.path))
        self.assertEqual(result['counts']['inbound_processing_events']['uncertain'],1)
        self.assertEqual(result['counts']['mermaid_reservations']['customer_waiting'],1)
        self.assertEqual(result['blocking_records'],1)
        self.assertNotIn('PRIVATE',json.dumps(result));self.assertNotIn('CANARY',json.dumps(result))

    def test_settled_inbound_needs_dedup_and_exact_terminal_metadata(self):
        self.insert('inbound_processing_events',('id','whatsapp','replied','provider_send_ok','','','','attempt'))
        self.assertEqual(m.project(str(self.path))['blocking_records'],1)
        self.insert('whatsapp_processed',('id',))
        self.assertEqual(m.project(str(self.path))['blocking_records'],0)
        with sqlite3.connect(self.path) as db:db.execute("UPDATE inbound_processing_events SET processing_token='active'")
        self.assertEqual(m.project(str(self.path))['counts']['inbound_processing_events']['uncertain'],1)

    def test_business_cross_record_consistency(self):
        self.insert('mermaid_reservations',('r','mermaid','booked','payref'))
        self.assertEqual(m.project(str(self.path))['blocking_records'],1)
        self.insert('mermaid_demo_payments',('r','mermaid','simulated_success'))
        self.assertEqual(m.project(str(self.path))['blocking_records'],0)
        with sqlite3.connect(self.path) as db:db.execute("UPDATE mermaid_reservations SET state='cancelled'")
        self.assertEqual(m.project(str(self.path))['blocking_records'],2)

    def test_status_mapping_all_sources(self):
        cases=[('mermaid_abandoned_reminders',('mermaid','failed'),'uncertain'),
               ('mermaid_abandoned_reminders',('mermaid','skipped_window'),'settled_record'),
               ('mermaid_delivery_jobs',('mermaid','pending',1,False),'uncertain'),
               ('mermaid_delivery_jobs',('mermaid','pending',0,False),'unfinished'),
               ('mermaid_delivery_jobs',('mermaid','delivered',1,False),'settled_record'),
               ('mermaid_reservation_emails',('accepted',False,True),'settled_record'),
               ('mermaid_reservation_emails',('uncertain',True,True),'uncertain'),
               ('mermaid_date_changes',('confirmed','pending',True),'unfinished'),
               ('mermaid_date_changes',('pending','pending',True),'customer_waiting'),
               ('mermaid_email_preferences',('consented',True),'unfinished'),
               ('mermaid_email_preferences',('awaiting_confirmation',True),'customer_waiting'),
               ('mermaid_checkout_links',('mermaid',True,10),'customer_waiting'),
               ('mermaid_reservations',('mermaid','demo_paid',True,True),'uncertain')]
        for table,row,want in cases:self.assertEqual(m.classify(table,row),want)
        self.assertEqual(m.classify('mermaid_abandoned_reminders',('other','sent')),'unknown')
        self.assertEqual(m.classify('mermaid_reservation_emails',('CANARY',False,True)),'unknown')

    def test_missing_schema_and_row_cap_fail_no_partial(self):
        with patch.object(m,'MAX_ROWS',1):
            self.insert('whatsapp_processed',('one',));self.insert('whatsapp_processed',('two',))
            with self.assertRaises(Exception):m.project(str(self.path))
        with sqlite3.connect(self.path) as db:db.execute('DROP TABLE inbound_processing_events')
        with self.assertRaises(Exception):m.project(str(self.path))

    def test_live_wal_permissions_symlink_nonregular_and_size_rejected(self):
        wal=Path(str(self.path)+'-wal');wal.write_bytes(b'')
        with self.assertRaises(Exception):m.project(str(self.path))
        wal.unlink();self.path.chmod(0o644)
        with self.assertRaises(Exception):m.project(str(self.path))
        self.path.chmod(0o600)
        with patch.object(m,'MAX_BYTES',1),self.assertRaises(Exception):m.project(str(self.path))
        link=self.path.with_name('link');link.symlink_to(self.path)
        with self.assertRaises(Exception):m.project(str(link))
        link.unlink();os.mkfifo(link,0o600)
        with self.assertRaises(Exception):m.project(str(link))

    def test_snapshot_changes_rejected(self):
        self.insert('mermaid_abandoned_reminders',('mermaid','sent'));original=m.classify
        def change(table,row):
            with self.path.open('ab') as file:file.write(b'x')
            return original(table,row)
        with patch.object(m,'classify',side_effect=change),self.assertRaises(Exception):m.project(str(self.path))

    def test_lazy_tables_absent_and_card_uncertainty(self):
        for state,pid,want in [('prepared',0,'uncertain'),('pending',1,'uncertain'),('failed',0,'uncertain'),('delivered',1,'settled_record'),('delivered',0,'unknown')]:
            self.assertEqual(m.classify('mermaid_card_deliveries',(state,pid,1)),want)
        with sqlite3.connect(self.path) as db:
            for table in m.TABLES:
                if table not in ('inbound_processing_events','whatsapp_processed'):db.execute('DROP TABLE '+table)
        result=m.project(str(self.path))
        self.assertEqual(result['blocking_records'],0)
        self.assertIn('mermaid_card_deliveries',result['not_initialized'])
        with sqlite3.connect(self.path) as db:db.execute('CREATE TABLE mermaid_card_deliveries (status TEXT)')
        with self.assertRaises(Exception):m.project(str(self.path))

    def test_null_terminal_metadata_and_deadline_do_not_pass(self):
        self.assertEqual(m.classify('inbound_processing_events',('whatsapp','replied','provider_send_ok',None,0,0,0,1)),'unknown')
        self.insert('inbound_processing_events',('id','whatsapp','replied','provider_send_ok',None,'','',''))
        self.insert('whatsapp_processed',('id',))
        self.assertEqual(m.project(str(self.path))['blocking_records'],1)
        with sqlite3.connect(self.path) as db:db.execute("UPDATE inbound_processing_events SET last_error=?",(b'',))
        self.assertEqual(m.project(str(self.path))['blocking_records'],1)
        with patch.object(m.time,'monotonic',side_effect=[0,11]),self.assertRaises(Exception):m.project(str(self.path))

    def test_default_and_errors_redacted(self):
        with patch.object(m,'project',side_effect=AssertionError),contextlib.redirect_stdout(io.StringIO()) as out:self.assertEqual(m.main([]),0)
        self.assertEqual(json.loads(out.getvalue())['snapshot_read'],False)
        with patch.object(m,'project',side_effect=ValueError('PRIVATE CANARY')),contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(m.main(['--approved-detached-snapshot',str(self.path)]),1)
        self.assertEqual(json.loads(out.getvalue()),{'status':'stopped','check':'old_work'})


if __name__=='__main__':unittest.main()
