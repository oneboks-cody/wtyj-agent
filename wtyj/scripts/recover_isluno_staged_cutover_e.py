"""Default-off operator recovery: preserve and quarantine the approved legacy snapshot."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import sqlite3
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent))
from inspect_isluno_target import require, safe_open
from prepare_isluno_sealed_start import file_digest, bundle, ACCOUNT
from project_isluno_old_work import project

ROOT = Path('/release-backup')
DB = Path('/app/data/state_registry.db')
CONFIG = Path('/app/config/client.json')
REVIEWED_SNAPSHOT = '269a45bead0fd1dc85ab651bc256202db3e3dc6c52653195dce4d431741cd349'
QUARANTINE_SCHEMA = '''CREATE TABLE isluno_legacy_quarantine(
 source TEXT NOT NULL,reference TEXT NOT NULL,conversation_id TEXT,
 original_status TEXT,sha256 TEXT NOT NULL,disposition TEXT NOT NULL,
 quarantined_at TEXT NOT NULL,PRIMARY KEY(source,reference))'''
PHASE = 'not_started'


def row_digest(row):
    return hashlib.sha256(json.dumps(dict(row), sort_keys=True, default=str).encode()).hexdigest()


def read_manifest(connection):
    """Private in-memory rows; never log identities, reasons or payloads."""
    connection.row_factory = sqlite3.Row
    rows = connection.execute('SELECT * FROM inbound_processing_events LIMIT 72').fetchall()
    require(len(rows) == 71)
    counts = {'superseded': 0, 'replied': 0}
    manifest = {}
    for row in rows:
        row = dict(row)
        identity = row['message_id']
        require(type(identity) is str and bool(identity) and identity not in manifest)
        require(row['channel'] == '' and row['status'] in counts)
        require(all(type(row[k]) is str and row[k] == '' for k in
                    ('last_error', 'processing_token', 'lease_expires_at', 'outbound_attempted_at')))
        require(connection.execute('SELECT 1 FROM whatsapp_processed WHERE message_id=?', (identity,)).fetchone() is not None)
        counts[row['status']] += 1
        manifest[identity] = (row_digest(row), row)
    require(counts == {'superseded': 50, 'replied': 21})
    return manifest


def same_manifest(left, right):
    require(set(left) == set(right))
    for identity in left:
        old, new = left[identity][1], right[identity][1]
        require(set(old) == set(new))
        require(all(type(old[key]) is type(new[key]) and old[key] == new[key] for key in old))


def quarantine(connection, reviewed):
    """Caller owns transaction; unknown rows are preserved, never called completed."""
    same_manifest(reviewed, read_manifest(connection))
    require(connection.execute("SELECT 1 FROM sqlite_master WHERE name IN ('isluno_cutover','isluno_legacy_quarantine','mermaid_maintenance')").fetchone() is None)
    connection.execute(QUARANTINE_SCHEMA)
    at = datetime.now(timezone.utc).isoformat()
    for identity, (digest, row) in reviewed.items():
        connection.execute('INSERT INTO isluno_legacy_quarantine VALUES(?,?,?,?,?,?,?)',
                           ('inbound_processing_events', identity, row.get('conversation_id'), row['status'],
                            digest, 'operator_review_no_automatic_replay', at))
    same_manifest(reviewed, read_manifest(connection))
    observed = {row[0]: row[1] for row in connection.execute(
        "SELECT reference,sha256 FROM isluno_legacy_quarantine WHERE source='inbound_processing_events' AND disposition='operator_review_no_automatic_replay'")}
    require(observed == {k: v[0] for k, v in reviewed.items()})


def reviewed_manifest(original):
    """Bind the approved snapshot hash and manifest to one held, stable inode."""
    import stat
    fd = safe_open(str(original))
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_uid == os.geteuid()
                and stat.S_IMODE(before.st_mode) == 0o600 and 0 < before.st_size <= 268435456)
        sha = hashlib.sha256(); size = 0
        while chunk := os.read(fd, 65536):
            size += len(chunk); require(size <= 268435456); sha.update(chunk)
        require(size == before.st_size and sha.hexdigest() == REVIEWED_SNAPSHOT)
        aggregate = project(str(original))
        require(aggregate['snapshot_sha256'] == REVIEWED_SNAPSHOT)
        with sqlite3.connect('file:/proc/self/fd/' + str(fd) + '?mode=ro&immutable=1', uri=True, timeout=0) as source:
            source.execute('PRAGMA query_only=ON')
            manifest = read_manifest(source)
        def signature(value):
            return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns,
                    value.st_mode, value.st_uid, value.st_nlink)
        require(signature(os.fstat(fd)) == signature(before))
        current = safe_open(str(original))
        try: require(signature(os.fstat(current)) == signature(before))
        finally: os.close(current)
        return aggregate, manifest
    finally:
        os.close(fd)


def private_source_copy(source, target):
    """Copy bound SQLite bytes without opening the production DB through SQLite."""
    import stat
    descriptor = safe_open(str(source))
    try:
        before = os.fstat(descriptor)
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size <= 268435456)
        output = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(output, 'wb') as destination:
            size = 0
            while chunk := os.read(descriptor, 65536):
                size += len(chunk); require(size <= 268435456); destination.write(chunk)
            destination.flush(); os.fsync(destination.fileno())
        after = os.fstat(descriptor)
        require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                and size == before.st_size)
    finally:
        os.close(descriptor)


def recover(expected_config, expected_bundle, evidence):
    global PHASE
    import fcntl, re, stat
    require(re.fullmatch('[0-9a-f]{64}', expected_config or '') and re.fullmatch('[0-9a-f]{64}', expected_bundle or ''))
    require(re.fullmatch('[A-Za-z0-9_-]{1,100}', evidence or ''))
    lock = safe_open(str(CONFIG) + '.lock')
    try:
        info = os.fstat(lock)
        require(info.st_nlink == 1 and info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o600)
        fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        require(file_digest(CONFIG) == expected_config and bundle(DB) == expected_bundle)
        fd = safe_open(str(CONFIG))
        try:
            require(os.fstat(fd).st_size <= 1048576)
            raw = os.read(fd, 1048577)
        finally:
            os.close(fd)
        require(hashlib.sha256(raw).hexdigest() == expected_config)
        def pairs(items):
            result = {}
            for key, value in items:
                require(key not in result); result[key] = value
            return result
        config = json.loads(raw, object_pairs_hook=pairs)
        require(config.get('slug') == 'mermaid' and config.get('business', {}).get('slug') == 'mermaid')
        binding = config.get('channel_account_allowlist', {})
        require(binding.get('mode') == 'strict' and type(binding.get('zernio_accounts')) is list and len(binding['zernio_accounts']) == 1)
        require(hashlib.sha256(binding['zernio_accounts'][0].encode()).hexdigest() == ACCOUNT)
        features = config.get('features', {})
        require(features.get('mermaid_cutover_coordination') is True and features.get('isluno_itinerary_demo_v1') is False and features.get('mermaid_reminders') is False)
        original = ROOT / 'state_registry.sqlite3'
        aggregate, reviewed = reviewed_manifest(original)
        require(aggregate['blocking_records'] == 71)
        require(aggregate['counts']['inbound_processing_events']['unknown'] == 71)
        require(all(sum(v.values()) == 0 for k, v in aggregate['counts'].items() if k != 'inbound_processing_events'))
        parent = safe_open(str(ROOT), directory=True)
        try:
            info = os.fstat(parent)
            require(stat.S_IMODE(info.st_mode) == 0o700 and info.st_uid == os.geteuid())
            fd = os.open('state_registry.recovery-e.sqlite3', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
            os.close(fd)
        finally:
            os.close(parent)
        PHASE = 'new_backup_created'
        snapshot = ROOT / 'state_registry.recovery-e.sqlite3'
        copied = ROOT / 'recovery-e-source'
        copied.mkdir(mode=0o700)
        private_source_copy(DB, copied / 'state_registry.db')
        wal = Path(str(DB) + '-wal')
        if wal.exists(): private_source_copy(wal, copied / 'state_registry.db-wal')
        require(bundle(DB) == expected_bundle and bundle(copied / 'state_registry.db') == expected_bundle)
        copied_files = [copied / 'state_registry.db'] + ([copied / 'state_registry.db-wal'] if wal.exists() else [])
        copied_bytes = sum(path.stat().st_size for path in copied_files)
        require(copied_bytes <= 536870912)
        before = time.monotonic()
        source = sqlite3.connect('file:' + str(copied / 'state_registry.db') + '?mode=ro', uri=True, timeout=0)
        target = sqlite3.connect(snapshot, timeout=0)
        try:
            source.execute('PRAGMA query_only=ON')
            def progress(*_): require(time.monotonic() - before < 15)
            source.backup(target, pages=256, progress=progress, sleep=.01)
            target.set_progress_handler(lambda: int(time.monotonic() - before > 20), 1000)
            require(target.execute('PRAGMA integrity_check').fetchall() == [('ok',)])
            same_manifest(reviewed, read_manifest(target))
        finally:
            target.close()
            source.close()
        current_aggregate = project(str(snapshot))
        require(current_aggregate['counts'] == aggregate['counts'] and current_aggregate['not_initialized'] == aggregate['not_initialized'])
        require(file_digest(CONFIG) == expected_config and bundle(DB) == expected_bundle)
        PHASE = 'snapshot_and_bindings_verified'
        with sqlite3.connect(DB, timeout=0) as live:
            live.execute('BEGIN IMMEDIATE')
            quarantine(live, reviewed)
        PHASE = 'exact_legacy_rows_quarantined'
        require('shared.state_registry' not in sys.modules)
        from shared import config_loader, mermaid_maintenance as maintenance
        require(Path(config_loader._CONFIG_PATH).resolve() == CONFIG.resolve() and maintenance.requested())
        require(file_digest(CONFIG) == expected_config)
        maintenance.initialize(db_path=str(DB))
        generation = maintenance.close(0, seconds=120, coverage=maintenance.COVERAGE, evidence=evidence, db_path=str(DB))
        PHASE = 'coordinator_closed'
        maintenance.seal(generation, db_path=str(DB))
        with sqlite3.connect('file:' + str(DB) + '?mode=ro', uri=True, timeout=0) as live:
            maintenance.require_sealed(live, generation)
            same_manifest(reviewed, read_manifest(live))
            deadline = live.execute("SELECT deadline FROM mermaid_maintenance WHERE tenant='mermaid'").fetchone()[0]
        require('shared.state_registry' not in sys.modules)
        PHASE = 'sealed'
        return {'status': 'sealed', 'generation': generation, 'deadline_epoch': deadline,
                'preserved_unresolved_records': 71, 'quarantined_no_replay': 71,
                'reclassified_as_completed': 0, 'snapshot_sha256': current_aggregate['snapshot_sha256'],
                'application_workers_started': False, 'automatic_retry': False,
                'private_source_bundle_sha256': expected_bundle, 'private_source_files': len(copied_files),
                'private_source_bytes': copied_bytes}
    finally:
        os.close(lock)


def main(argv=None):
    try:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument('--execute-approved-recovery-e', action='store_true')
        parser.add_argument('--approve-preserved-unresolved-quarantine-71', action='store_true')
        parser.add_argument('--expected-config-sha256')
        parser.add_argument('--expected-db-bundle-sha256')
        parser.add_argument('--verified-quiescence-reference')
        args = parser.parse_args(argv)
        if not args.execute_approved_recovery_e:
            print('{"status":"offline_default","mutations":false}'); return 0
        require(args.approve_preserved_unresolved_quarantine_71)
        import socket, subprocess, threading
        def denied(*args, **kwargs): raise ValueError('disabled')
        socket.socket.connect = denied; socket.socket.connect_ex = denied; socket.socket.sendto = denied
        socket.create_connection = denied; socket.getaddrinfo = denied
        subprocess.Popen = denied; threading.Thread.start = denied
        signal.signal(signal.SIGALRM, denied); signal.setitimer(signal.ITIMER_REAL, 45)
        print(json.dumps(recover(args.expected_config_sha256, args.expected_db_bundle_sha256,
                                 args.verified_quiescence_reference), sort_keys=True)); return 0
    except Exception:
        print(json.dumps({'status': 'stopped', 'phase': PHASE, 'automatic_retry': False})); return 1
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)


if __name__ == '__main__':
    raise SystemExit(main())
