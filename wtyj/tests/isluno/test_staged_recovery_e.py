"""Synthetic stopped-state recovery. No customer data, network or runtime workers."""
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from shared import config_loader
import test_old_work_projection as fixtures

SCRIPT = Path(__file__).resolve().parents[2] / 'scripts/recover_isluno_staged_cutover_e.py'
spec = importlib.util.spec_from_file_location('staged_recovery', SCRIPT)
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.ProjectionTests(); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        for i in range(71):
            self.f.insert('inbound_processing_events', (str(i), '', 'superseded' if i < 50 else 'replied', 'unresolved-synthetic', '', '', '', ''))
            self.f.insert('whatsapp_processed', (str(i),))
        self.root = self.f.path.parent / 'backup'; self.root.mkdir(mode=0o700)
        self.original = self.root / 'state_registry.sqlite3'
        with sqlite3.connect(self.f.path) as source, sqlite3.connect(self.original) as target: source.backup(target)
        self.original.chmod(0o600)
        self.config = self.f.path.parent / 'client.json'
        self.doc = {'slug': 'mermaid', 'business': {'slug': 'mermaid'},
                    'features': {'mermaid_cutover_coordination': True, 'isluno_itinerary_demo_v1': False, 'mermaid_reminders': False},
                    'channel_account_allowlist': {'mode': 'strict', 'zernio_accounts': ['synthetic']}}
        self.config.write_text(json.dumps(self.doc)); self.config.chmod(0o600)
        self.config.with_name('client.json.lock').touch(mode=0o600)
        for target, name, value in [(m, 'ROOT', self.root), (m, 'DB', self.f.path), (m, 'CONFIG', self.config),
                                    (m, 'ACCOUNT', hashlib.sha256(b'synthetic').hexdigest()),
                                    (m, 'REVIEWED_SNAPSHOT', m.file_digest(self.original)),
                                    (config_loader, '_CONFIG_PATH', str(self.config))]:
            p = patch.object(target, name, value); p.start(); self.addCleanup(p.stop)
        config_loader._invalidate_cache(); self.addCleanup(config_loader._invalidate_cache)

    def recover(self, **changes):
        args = {'expected_config': m.file_digest(self.config), 'expected_bundle': m.bundle(self.f.path), 'evidence': 'synthetic-quiescence'}
        args.update(changes); return m.recover(**args)

    def test_preserves_exact_rows_quarantines_unknown_without_reclassification(self):
        before = self.original.read_bytes()
        result = self.recover()
        self.assertEqual(result['status'], 'sealed'); self.assertEqual(result['quarantined_no_replay'], 71)
        self.assertEqual(result['reclassified_as_completed'], 0)
        self.assertEqual(self.original.read_bytes(), before)
        with sqlite3.connect(self.original) as old, sqlite3.connect(self.f.path) as live:
            m.same_manifest(m.read_manifest(old), m.read_manifest(live))
            self.assertEqual(live.execute('SELECT count(*) FROM isluno_legacy_quarantine').fetchone()[0], 71)
        self.assertEqual(m.project(str(self.original))['blocking_records'], 71)
        from agents.social import isluno_transition
        self.assertTrue(isluno_transition.quarantined_inbound(['0', '70'], self.f.path))
        self.assertFalse(isluno_transition.quarantined_inbound(['new'], self.f.path))

    def test_wal_source_recovery_closes_detached_backup_before_projection(self):
        db = sqlite3.connect(self.f.path)
        try: self.assertEqual(db.execute('PRAGMA journal_mode=WAL').fetchone()[0], 'wal')
        finally: db.close()
        result = self.recover()
        self.assertEqual(result['status'], 'sealed')
        self.assertFalse(Path(str(self.root/'state_registry.recovery-e.sqlite3')+'-wal').exists())
        self.assertFalse(Path(str(self.root/'state_registry.recovery-e.sqlite3')+'-shm').exists())

    def test_committed_uncheckpointed_wal_frames_preserved_until_quarantine(self):
        connection = sqlite3.connect(self.f.path)
        try:
            self.assertEqual(connection.execute('PRAGMA journal_mode=WAL').fetchone()[0], 'wal')
            connection.execute('PRAGMA wal_autocheckpoint=0')
            connection.execute("UPDATE inbound_processing_events SET reason='intermediate-committed-fixture' WHERE message_id='0'")
            connection.commit()
            connection.execute("UPDATE inbound_processing_events SET reason='unresolved-synthetic' WHERE message_id='0'")
            connection.commit()
            wal = Path(str(self.f.path)+'-wal')
            self.assertGreater(wal.stat().st_size, 32)
            physical = (self.f.path.read_bytes(), wal.read_bytes())
            original_quarantine = m.quarantine
            def check_physical_then_quarantine(db, manifest):
                self.assertEqual((self.f.path.read_bytes(), wal.read_bytes()), physical)
                return original_quarantine(db, manifest)
            with patch.object(m, 'quarantine', side_effect=check_physical_then_quarantine): result = self.recover()
            self.assertEqual(result['private_source_files'], 2)
            self.assertEqual(result['status'], 'sealed')
        finally: connection.close()

    def test_wrong_cas_or_binding_never_quarantines(self):
        for changes in ({'expected_config': '0'*64}, {'expected_bundle': '0'*64}):
            with self.assertRaises(Exception): self.recover(**changes)
        self.doc['channel_account_allowlist']['zernio_accounts'] = ['different']; self.config.write_text(json.dumps(self.doc))
        with self.assertRaises(Exception): self.recover()
        self.assertFalse((self.root/'state_registry.recovery-e.sqlite3').exists())

    def test_missing_extra_or_changed_rows_reject(self):
        for sql in ["DELETE FROM inbound_processing_events WHERE message_id='0'",
                    "UPDATE inbound_processing_events SET reason='changed' WHERE message_id='0'",
                    "UPDATE inbound_processing_events SET last_error=NULL WHERE message_id='0'",
                    "UPDATE inbound_processing_events SET status='processing' WHERE message_id='0'"]:
            with self.subTest(sql=sql):
                with sqlite3.connect(self.f.path) as db:
                    db.execute('BEGIN'); db.execute(sql)
                    with sqlite3.connect(self.original) as original:
                        with self.assertRaises(Exception): m.quarantine(db, m.read_manifest(original))
                    db.rollback()
                    self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='isluno_legacy_quarantine'").fetchone())

    def test_extra_row_and_existing_quarantine_reject(self):
        with sqlite3.connect(self.original) as old: manifest = m.read_manifest(old)
        with sqlite3.connect(self.f.path) as live:
            live.execute('BEGIN')
            live.execute("INSERT INTO inbound_processing_events SELECT 'extra',channel,status,reason,last_error,processing_token,lease_expires_at,outbound_attempted_at FROM inbound_processing_events LIMIT 1")
            with self.assertRaises(Exception): m.quarantine(live, manifest)
            live.rollback()
            live.execute(m.QUARANTINE_SCHEMA)
            with self.assertRaises(Exception): m.quarantine(live, manifest)

    def test_snapshot_drift_and_typed_payload_drift_reject(self):
        with self.original.open('ab') as out: out.write(b'drift')
        with self.assertRaises(Exception): self.recover()
        left = {'x': ('unused', {'reason': b'bytes'})}
        right = {'x': ('unused', {'reason': "b'bytes'"})}
        with self.assertRaises(Exception): m.same_manifest(left, right)
        self.assertFalse((self.root/'state_registry.recovery-e.sqlite3').exists())

    def test_reviewed_snapshot_replacement_between_projection_and_manifest_rejects(self):
        original_project = m.project
        def replace_after_projection(path):
            result = original_project(path)
            replacement = self.root / 'replacement.sqlite3'
            replacement.write_bytes(self.original.read_bytes()); replacement.chmod(0o600)
            with sqlite3.connect(replacement) as db:
                db.execute("UPDATE inbound_processing_events SET reason='unreviewed' WHERE message_id='0'")
            replacement.replace(self.original)
            return result
        with patch.object(m, 'project', side_effect=replace_after_projection):
            with self.assertRaises(Exception): self.recover()
        self.assertFalse((self.root/'state_registry.recovery-e.sqlite3').exists())

    def test_missing_dedup_rejects(self):
        with sqlite3.connect(self.f.path) as db:
            db.execute("DELETE FROM whatsapp_processed WHERE message_id='0'")
        with self.assertRaises(Exception): self.recover()

    def test_duplicate_dispatch_preserves_both_snapshots(self):
        self.recover(); before = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        with self.assertRaises(Exception): self.recover()
        self.assertEqual({str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}, before)

    def test_interrupted_transaction_keeps_original_rows_and_no_partial_quarantine(self):
        with sqlite3.connect(self.original) as old: manifest = m.read_manifest(old)
        with self.assertRaises(RuntimeError):
            with sqlite3.connect(self.f.path) as live:
                live.execute('BEGIN IMMEDIATE'); m.quarantine(live, manifest); raise RuntimeError('synthetic interruption')
        with sqlite3.connect(self.f.path) as live:
            self.assertIsNone(live.execute("SELECT 1 FROM sqlite_master WHERE name='isluno_legacy_quarantine'").fetchone())
            m.same_manifest(manifest, m.read_manifest(live))

    def test_expired_seal_never_accepts_activation(self):
        self.recover()
        from shared import mermaid_maintenance as maintenance
        with sqlite3.connect(self.f.path) as db:
            db.execute('UPDATE mermaid_maintenance SET deadline=0')
            with self.assertRaises(maintenance.Closed): maintenance.require_sealed(db, 1)

    def test_default_no_reads_no_writes(self):
        with patch.object(m, 'recover', side_effect=AssertionError), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(m.main([]), 0)
        self.assertEqual(json.loads(out.getvalue()), {'status': 'offline_default', 'mutations': False})

if __name__ == '__main__': unittest.main()
