"""One-shot uses real accepted coordinator with synthetic DB/config, no application workers."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import threading
import unittest
from unittest.mock import patch
import test_old_work_projection as fixtures
from shared import config_loader

SCRIPT=Path(__file__).resolve().parents[2]/'scripts/prepare_isluno_sealed_start.py'
spec=importlib.util.spec_from_file_location('sealed_start',SCRIPT);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


class SealedTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.ProjectionTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.config=self.f.path.parent/'client.json'
        self.doc={'slug':'mermaid','business':{'slug':'mermaid'},'features':{'mermaid_cutover_coordination':True,'isluno_itinerary_demo_v1':False},'channel_account_allowlist':{'mode':'strict','zernio_accounts':['synthetic-account']}}
        self.config.write_text(json.dumps(self.doc));self.config.chmod(0o600)
        self.config.with_name('client.json.lock').touch(mode=0o600)
        self.backup=self.f.path.parent/'backup';self.backup.mkdir(mode=0o700)
        self.patch=patch.object(config_loader,'_CONFIG_PATH',str(self.config));self.patch.start();self.addCleanup(self.patch.stop)
        config_loader._invalidate_cache();self.addCleanup(config_loader._invalidate_cache)
        self.thread=patch.object(threading.Thread,'start',side_effect=AssertionError('worker started'));self.thread.start();self.addCleanup(self.thread.stop)

    def run_seal(self,**overrides):
        args={'expected_config':m.file_digest(self.config),'expected_bundle':m.bundle(self.f.path),'quiescence_reference':'synthetic-quiescence',
              'db':self.f.path,'config':self.config,'backup':self.backup,'account':hashlib.sha256(b'synthetic-account').hexdigest()}
        args.update(overrides);return m.prepare(**args)

    def test_real_coordinator_seals_without_state_registry_or_workers(self):
        self.f.insert('mermaid_reservations',('draft','mermaid','quote_ready',None))
        result=self.run_seal()
        self.assertEqual(result['generation'],1);self.assertEqual(result['status'],'sealed')
        self.assertNotIn('shared.state_registry',sys.modules)
        self.assertFalse(result['application_workers_started'])
        with sqlite3.connect(self.f.path) as db:
            self.assertEqual(db.execute('SELECT phase FROM mermaid_maintenance').fetchone()[0],'sealed')
            self.assertEqual(db.execute('SELECT state FROM mermaid_reservations').fetchone()[0],'quote_ready')
        self.assertEqual(result['aggregate']['counts']['mermaid_reservations']['customer_waiting'],1)
        from shared import mermaid_maintenance
        with sqlite3.connect(self.f.path) as db:mermaid_maintenance.require_sealed(db,1)

    def test_direct_activation_retains_waiting_draft_under_same_seal(self):
        self.f.insert('mermaid_reservations',('draft','mermaid','quote_ready',None))
        result=self.run_seal()
        profile=Path(__file__).resolve().parents[3]/'clients/mermaid/config/isluno_profile.json'
        self.config.with_name('isluno_profile.json').write_bytes(profile.read_bytes())
        self.doc['features']['isluno_itinerary_demo_v1']=True
        self.doc['mermaid_maintenance']={'activation_generation':result['generation']}
        self.config.write_text(json.dumps(self.doc))
        with patch.dict(os.environ,{'TENANT_ID':'mermaid','TENANT_ACCOUNT_ALLOWLIST_REQUIRED':'true'}):
            from agents.social import isluno_transition
            isluno_transition.ensure(db_path=self.f.path)
        with sqlite3.connect(self.f.path) as db:
            self.assertEqual(db.execute('SELECT tenant FROM isluno_cutover').fetchone()[0],'mermaid')
            self.assertEqual(db.execute('SELECT original_status,disposition FROM isluno_legacy_quarantine').fetchone(),('quote_ready','operator_review_no_automatic_replay'))
            self.assertEqual(db.execute('SELECT state FROM mermaid_reservations').fetchone()[0],'quote_ready')
        self.assertNotIn('shared.state_registry',sys.modules)

    def test_unresolved_card_or_send_stops_before_coordinator_write(self):
        self.f.insert('mermaid_reservations',('r','mermaid','quote_ready',None))
        self.f.insert('mermaid_documents',('d','mermaid','r'))
        self.f.insert('mermaid_card_deliveries',('d','pending','possible-provider-id'))
        with self.assertRaises(Exception):self.run_seal()
        with sqlite3.connect(self.f.path) as db:
            self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='mermaid_maintenance'").fetchone())
        self.assertTrue((self.backup/'state_registry.sqlite3').exists())

    def test_wrong_config_or_db_cas_no_backup_no_schema(self):
        for changed in ({'expected_config':'0'*64},{'expected_bundle':'0'*64},{'account':'0'*64}):
            with self.assertRaises(Exception):self.run_seal(**changed)
        self.assertFalse(list(self.backup.iterdir()))

    def test_existing_backup_or_gate_no_overwrite(self):
        self.run_seal();before=(self.backup/'state_registry.sqlite3').read_bytes()
        with self.assertRaises(Exception):self.run_seal()
        self.assertEqual((self.backup/'state_registry.sqlite3').read_bytes(),before)

    def test_no_activation_during_preparation(self):
        self.doc['features']['isluno_itinerary_demo_v1']=True;self.config.write_text(json.dumps(self.doc))
        with self.assertRaises(Exception):self.run_seal()
        self.assertFalse(list(self.backup.iterdir()))

    def test_default_no_mutations(self):
        with patch.object(m,'prepare',side_effect=AssertionError),contextlib.redirect_stdout(io.StringIO()) as out:self.assertEqual(m.main([]),0)
        self.assertEqual(json.loads(out.getvalue()),{'status':'offline_default','mutations':False})


if __name__=='__main__':unittest.main()
