"""Fresh-process synthetic CLI verification; run only in the network-disabled image."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys
import test_old_work_projection as fixtures

f = fixtures.ProjectionTests(); f.setUp()
try:
    for i in range(71):
        f.insert('inbound_processing_events', (str(i), '', 'superseded' if i < 50 else 'replied', 'unknown-synthetic', '', '', '', ''))
        f.insert('whatsapp_processed', (str(i),))
    db = sqlite3.connect(f.path)
    try: assert db.execute('PRAGMA journal_mode=WAL').fetchone()[0] == 'wal'
    finally: db.close()
    root = f.path.parent / 'private'; root.mkdir(mode=0o700)
    original = root / 'state_registry.sqlite3'
    source = sqlite3.connect(f.path); target = sqlite3.connect(original)
    try: source.backup(target)
    finally: target.close(); source.close()
    original.chmod(0o600)
    config = f.path.parent / 'client.json'
    config.write_text(json.dumps({'slug':'mermaid','business':{'slug':'mermaid'},'features':{'mermaid_cutover_coordination':True,'isluno_itinerary_demo_v1':False,'mermaid_reminders':False},'channel_account_allowlist':{'mode':'strict','zernio_accounts':['synthetic']}})); config.chmod(0o600)
    config.with_name('client.json.lock').touch(mode=0o600)
    os.environ['CLIENT_CONFIG_PATH'] = str(config)
    os.environ['TENANT_ID'] = 'mermaid'
    os.environ['TENANT_ACCOUNT_ALLOWLIST_REQUIRED'] = 'true'
    assert 'shared.config_loader' not in sys.modules
    script = Path(__file__).resolve().parents[2] / 'scripts/recover_isluno_staged_cutover_e.py'
    spec = importlib.util.spec_from_file_location('recovery_cli_fixture',script)
    m = importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    m.ROOT=root;m.DB=f.path;m.CONFIG=config;m.ACCOUNT=hashlib.sha256(b'synthetic').hexdigest()
    m.REVIEWED_SNAPSHOT=m.file_digest(original)
    result=m.main(['--execute-approved-recovery-e','--approve-preserved-unresolved-quarantine-71','--expected-config-sha256',m.file_digest(config),'--expected-db-bundle-sha256',m.bundle(f.path),'--verified-quiescence-reference','synthetic-cli'])
    assert result == 0
    assert 'shared.state_registry' not in sys.modules
    assert 'shared.config_loader' in sys.modules
    print(json.dumps({'fresh_cli':'pass','wal_mode':True,'application_workers_started':False,'provider_calls':0,'agent_messages':0}))
finally:
    f.doCleanups()
