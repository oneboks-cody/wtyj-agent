"""Default-off Mermaid admission/drain barrier. Never an inbox/AI-owner control.

Arming and sealing are operator actions requiring separate release authority.
No expired/crashed claim is ever inferred complete. Uncertainty remains visible.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import json
from pathlib import Path
import sqlite3
import time
import uuid

from shared import config_loader

CURRENT = ContextVar('mermaid_maintenance_worker', default=None)
# Explicit operator inventory must cover these producers AND exclude old/external writers.
COVERAGE = frozenset({'inbound', 'recovery', 'scheduled', 'delivery', 'transport', 'operator_writers', 'runtime_inventory'})
SCHEMA = '''
CREATE TABLE IF NOT EXISTS mermaid_maintenance (
 tenant TEXT PRIMARY KEY CHECK(tenant='mermaid'), generation INTEGER NOT NULL,
 phase TEXT NOT NULL, deadline REAL NOT NULL, coverage TEXT NOT NULL, evidence TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS mermaid_maintenance_workers (
 id TEXT PRIMARY KEY, generation INTEGER NOT NULL, role TEXT NOT NULL,
 status TEXT NOT NULL, started REAL NOT NULL, ended REAL, disposition TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS mermaid_maintenance_pending (
 generation INTEGER NOT NULL, message_id TEXT NOT NULL, PRIMARY KEY(generation,message_id));
CREATE TABLE IF NOT EXISTS mermaid_maintenance_audit (
 id INTEGER PRIMARY KEY, generation INTEGER NOT NULL, action TEXT NOT NULL, at REAL NOT NULL);
'''
# Only active execution/uncertainty, not drafts awaiting a guest response, blocks seal.
LEDGERS = {
 'inbound_processing_events': ('status', ('received','processing','recovering','paused','processing_failed')),
 'mermaid_abandoned_reminders': ('status', ('sending','uncertain','claimed')),
 'mermaid_delivery_jobs': ('status', ('sending','uncertain','claimed','ambiguous','pending')),
 'mermaid_reservation_emails': ('status', ('sending','uncertain')),
 'isluno_reminders': ('status', ('claimed','uncertain','ambiguous')),
}


class Closed(RuntimeError):
    pass


def database():
    from shared import state_registry
    return state_registry.DB_PATH


def applies():
    return (config_loader.get_raw() or {}).get('slug') == 'mermaid'


def state(db):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='mermaid_maintenance'").fetchone():
        return None
    row=db.execute("SELECT generation,phase,deadline,coverage,evidence FROM mermaid_maintenance WHERE tenant='mermaid'").fetchone()
    return dict(zip(('generation','phase','deadline','coverage','evidence'),row)) if row else None


def requested():
    return applies() and (config_loader.get_raw().get('features') or {}).get('mermaid_cutover_coordination') is True


def initialize(db_path=None):
    """Explicit default-off bootstrap; no arm/closure or automatic proof creation."""
    if not requested():
        return
    with sqlite3.connect(db_path or database()) as db:
        db.executescript(SCHEMA)
        db.execute("INSERT OR IGNORE INTO mermaid_maintenance VALUES('mermaid',0,'open',0,'[]','')")


def _enabled(db):
    if not applies():
        return None
    value=state(db)
    if requested() and value is None:
        raise Closed('coordination_not_initialized')
    return value  # durable state cannot be bypassed by switching the feature off


def admit(db, message_id):
    """Caller owns BEGIN IMMEDIATE, shared with dedup/payload insertion."""
    value=_enabled(db)
    if not value or value['phase']=='open':
        return
    # Existing fully accepted duplicates remain acknowledgable; no new marker is consumed.
    accepted=db.execute('SELECT 1 FROM inbound_processing_events WHERE message_id=?',(message_id,)).fetchone()
    marked=db.execute('SELECT 1 FROM whatsapp_processed WHERE message_id=?',(message_id,)).fetchone()
    if accepted and marked:
        return
    raise Closed('admission_closed')


def close(expected_generation, *, seconds, coverage, evidence, db_path=None, now=None):
    """Dedicated operator command; evidence is a reviewed inventory reference, not authority."""
    if not applies() or not (1 <= seconds <= 120):
        raise Closed('invalid_scope_or_deadline')
    if set(coverage)!=COVERAGE or not isinstance(evidence,str) or not evidence.strip():
        raise Closed('coverage_unverified')
    now=time.time() if now is None else now
    with sqlite3.connect(db_path or database()) as db:
        db.execute('BEGIN IMMEDIATE');s=_enabled(db)
        if not s or s['phase']!='open' or s['generation']!=expected_generation:
            raise Closed('stale_generation')
        generation=expected_generation+1
        db.execute("UPDATE mermaid_maintenance SET generation=?,phase='draining',deadline=?,coverage=?,evidence=? WHERE tenant='mermaid'",
                   (generation,now+seconds,json.dumps(sorted(coverage)),evidence))
        db.execute('INSERT INTO mermaid_maintenance_pending SELECT ?,message_id FROM inbound_processing_events WHERE status IN (\'received\',\'processing\',\'recovering\')',(generation,))
        db.execute('INSERT INTO mermaid_maintenance_audit(generation,action,at) VALUES(?,?,?)',(generation,'close',now))
        return generation


@contextmanager
def worker(role, message_ids=(), db_path=None):
    """Durably claim every participating entry; nested work inherits a still-active claim."""
    if not applies():
        yield;return
    path=db_path or database()
    if not requested() and not Path(path).exists():
        yield;return
    token=None;parent=CURRENT.get()
    with sqlite3.connect(path) as db:
        db.execute('BEGIN IMMEDIATE');s=_enabled(db)
        if s:
            if role not in COVERAGE:
                raise Closed('unknown_producer')
            nested=False
            if parent and parent[0]==str(path):
                nested=bool(db.execute("SELECT 1 FROM mermaid_maintenance_workers WHERE id=? AND status='active'",(parent[1],)).fetchone())
                if not nested:raise Closed('stale_worker')
            accepted=False
            if role=='inbound' and message_ids:
                accepted=all(db.execute('SELECT 1 FROM mermaid_maintenance_pending WHERE generation=? AND message_id=?',(s['generation'],m)).fetchone() for m in message_ids)
            if s['phase']!='open' and not (s['phase']=='draining' and (nested or accepted or role=='recovery')):
                raise Closed('worker_admission_closed')
            if s['phase']=='draining' and time.time()>=s['deadline']:
                raise Closed('drain_deadline')
            token=uuid.uuid4().hex
            db.execute('INSERT INTO mermaid_maintenance_workers VALUES(?,?,?,?,?,NULL,\'\')',(token,s['generation'],role,'active',time.time()))
    if not token:
        yield;return
    context=CURRENT.set((str(path),token));status='operator_review';reason='interrupted_outcome_unknown'
    try:
        yield
        status='finished';reason='returned_normally'
    finally:
        CURRENT.reset(context)
        with sqlite3.connect(path) as db:
            db.execute("UPDATE mermaid_maintenance_workers SET status=?,ended=?,disposition=? WHERE id=? AND status='active'",(status,time.time(),reason,token))


def participating(role, ids=None, when=None):
    def decorate(fn):
        @wraps(fn)
        def wrapped(*args,**kwargs):
            if when is not None and not when(*args,**kwargs):
                return fn(*args,**kwargs)
            message_ids=ids(*args,**kwargs) if ids else ()
            with worker(role,message_ids):
                return fn(*args,**kwargs)
        return wrapped
    return decorate


def _snapshot(db):
    s=_enabled(db)
    if not s:return {'phase':'inactive','ready':False}
    incidents=[dict(zip(('id','role','status','started','ended','disposition'),row)) for row in db.execute("SELECT id,role,status,started,ended,disposition FROM mermaid_maintenance_workers WHERE status!='finished' ORDER BY started LIMIT 50")]
    counts={k:v for k,v in db.execute("SELECT status,COUNT(*) FROM mermaid_maintenance_workers WHERE status!='finished' GROUP BY status")}
    tables={x[0] for x in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    pending={};unknown=[]
    for table,(column,states) in LEDGERS.items():
        if table not in tables:
            if table=='inbound_processing_events':unknown.append(table)
            continue
        columns={x[1] for x in db.execute('PRAGMA table_info('+table+')')}
        if column not in columns:unknown.append(table);continue
        if table=='inbound_processing_events':
            count=db.execute('SELECT COUNT(*) FROM inbound_processing_events WHERE message_id IN (SELECT message_id FROM mermaid_maintenance_pending WHERE generation=?) AND status IN ('+','.join('?' for _ in states)+')',(s['generation'],*states)).fetchone()[0]
        else:
            count=db.execute('SELECT COUNT(*) FROM '+table+' WHERE '+column+' IN ('+','.join('?' for _ in states)+')',states).fetchone()[0]
        if count:pending[table]=count
    # Any new Isluno send ledger not explicitly reviewed is a coverage stop, never ignored.
    for table in tables:
        if table.startswith('isluno_') and table not in LEDGERS:
            columns={x[1] for x in db.execute('PRAGMA table_info('+table+')')}
            if any(x in columns for x in ('status','decision','outcome')):
                if db.execute('SELECT 1 FROM '+table+' LIMIT 1').fetchone():unknown.append(table)
    return {'phase':s['phase'],'generation':s['generation'],'deadline':s['deadline'],
            'coverage_complete':set(json.loads(s['coverage']))==COVERAGE and bool(s['evidence']),
            'workers':counts,'incidents':incidents,'pending':pending,'unreviewed_ledgers':sorted(unknown),'ready':False}


def status(db_path=None):
    if not applies():return {'phase':'not_applicable','ready':False}
    path=db_path or database()
    if not requested() and not Path(path).exists():return {'phase':'inactive','ready':False}
    with sqlite3.connect(path) as db:
        return _snapshot(db)


def seal(expected_generation, db_path=None, now=None):
    """Atomic final no-new-work boundary; readiness cannot be inferred from status counts."""
    now=time.time() if now is None else now
    with sqlite3.connect(db_path or database()) as db:
        db.execute('BEGIN IMMEDIATE');s=_snapshot(db)
        if s.get('generation')!=expected_generation or s['phase']!='draining':raise Closed('stale_generation')
        if now>=s['deadline']:raise Closed('drain_deadline')
        if not s['coverage_complete'] or s['workers'] or s['pending'] or s['unreviewed_ledgers']:raise Closed('work_not_drained')
        db.execute("UPDATE mermaid_maintenance SET phase='sealed' WHERE tenant='mermaid'")
        db.execute('INSERT INTO mermaid_maintenance_audit(generation,action,at) VALUES(?,?,?)',(expected_generation,'seal',now))
        return expected_generation


def require_sealed(db, expected_generation=None):
    if not _enabled(db):return
    s=_snapshot(db)
    if (s['phase']!='sealed' or type(expected_generation) is not int or s['generation']!=expected_generation
            or time.time()>=s['deadline'] or not s['coverage_complete'] or s['workers'] or s['pending'] or s['unreviewed_ledgers']):
        raise Closed('cutover_requires_current_sealed_generation')


def reopen(expected_generation, db_path=None):
    """Separate operator action after runtime identity/readiness verification, never automatic."""
    with sqlite3.connect(db_path or database()) as db:
        db.execute('BEGIN IMMEDIATE');s=_snapshot(db)
        if s.get('generation')!=expected_generation or s['phase']!='sealed' or s['workers']:raise Closed('stale_or_busy')
        db.execute("UPDATE mermaid_maintenance SET phase='open',generation=generation+1,coverage='[]',evidence='' WHERE tenant='mermaid'")
        db.execute('INSERT INTO mermaid_maintenance_audit(generation,action,at) VALUES(?,?,?)',(expected_generation,'reopen',time.time()))
