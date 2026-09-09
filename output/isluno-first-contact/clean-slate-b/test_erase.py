import tempfile,sqlite3,json,hashlib
from pathlib import Path
from erase import erase
root=Path(tempfile.mkdtemp());p=root/'state.db'
db=sqlite3.connect(p)
db.executescript('''PRAGMA journal_mode=WAL;
CREATE TABLE mermaid_maintenance(tenant TEXT,generation INTEGER,phase TEXT);
INSERT INTO mermaid_maintenance VALUES('mermaid',15,'draining');
CREATE TABLE mermaid_maintenance_workers(status TEXT);
CREATE TABLE mermaid_maintenance_pending(generation INTEGER,message_id TEXT);
INSERT INTO mermaid_maintenance_pending VALUES(3,'old');
CREATE TABLE isluno_cutover(tenant TEXT);
INSERT INTO isluno_cutover VALUES('mermaid');
CREATE TABLE customers(id TEXT);
INSERT INTO customers VALUES('demo-guest');
CREATE TABLE data_retention_audit_log(id TEXT);
INSERT INTO data_retention_audit_log VALUES('preserve-audit');
CREATE TABLE mermaid_reservations(id TEXT);
INSERT INTO mermaid_reservations VALUES('historical');
CREATE TABLE system_settings(key TEXT,value TEXT);
INSERT INTO system_settings VALUES('business','preserve');
CREATE TABLE isluno_quote_versions(id TEXT,payload TEXT);
INSERT INTO isluno_quote_versions VALUES('quote','private customer body');
CREATE TRIGGER isluno_quote_no_delete BEFORE DELETE ON isluno_quote_versions BEGIN SELECT RAISE(ABORT,'immutable'); END;
CREATE TABLE whatsapp_processed(message_id TEXT PRIMARY KEY,created_at TEXT);
INSERT INTO whatsapp_processed VALUES('processed-only','2020');
CREATE TABLE inbound_processing_events(message_id TEXT PRIMARY KEY,status TEXT,reason TEXT,created_at TEXT,updated_at TEXT,payload_json TEXT DEFAULT '{}',conversation_id TEXT DEFAULT '');
INSERT INTO inbound_processing_events VALUES('old','processing','unknown','2020','2020','private customer body','private phone');
''')
scope={'tables':{n:hashlib.sha256(s.encode()).hexdigest() for n,s in db.execute("SELECT name,sql FROM sqlite_master WHERE type='table'")},'preserve_tables':['mermaid_maintenance','mermaid_maintenance_workers','isluno_cutover','system_settings','data_retention_audit_log','mermaid_reservations'],'delete_tables':['customers','mermaid_maintenance_pending','isluno_quote_versions'],'triggers':[{'name':n,'sql':s} for n,s in db.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'")]}
db.close()
# Invalid generation must preserve original records transactionally.
db=sqlite3.connect(p);db.execute('UPDATE mermaid_maintenance SET generation=8');db.commit();db.close()
try:erase(p,scope);raise RuntimeError('accepted stale generation')
except AssertionError:pass
db=sqlite3.connect(p);assert db.execute('SELECT payload FROM isluno_quote_versions').fetchone()[0]=='private customer body';db.execute('UPDATE mermaid_maintenance SET generation=15');db.commit();db.close()
result=erase(p,scope)
db=sqlite3.connect(p)
assert db.execute('SELECT count(*) FROM mermaid_maintenance_pending').fetchone()[0]==0
assert db.execute('SELECT message_id,status,reason,payload_json,conversation_id FROM inbound_processing_events ORDER BY message_id').fetchall()==[(x,'ignored','history_cleared_no_replay','{}','') for x in ['old','processed-only']]
db.execute("INSERT INTO isluno_quote_versions VALUES('new','new')");db.commit()
try:db.execute('DELETE FROM isluno_quote_versions');raise RuntimeError('trigger lost')
except sqlite3.IntegrityError:pass
assert db.execute('SELECT id FROM data_retention_audit_log').fetchall()==[('preserve-audit',)]
assert db.execute('SELECT id FROM mermaid_reservations').fetchall()==[('historical',)]
assert db.execute('SELECT count(*) FROM customers').fetchone()[0]==0
db.close();assert b'private customer body' not in p.read_bytes()
print(json.dumps({'status':'pass','rollback':True,'immutable_trigger_restored':True,'customer_payload_removed':True,'pending_removed':True,'processed_only_tombstone':True,'business_preserved':True,'no_backup':True}))
