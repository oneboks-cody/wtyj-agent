"""Versioned quote snapshots, explicit native approvals and durable delivery jobs."""
from contextlib import contextmanager
from datetime import datetime, timedelta
import copy
import hashlib
import json
import secrets
from urllib.parse import urlsplit

from agents.social.isluno_itinerary import encoded
from agents.social.isluno_quote_documents import projection, text_sections, render_pdf, COPY
from shared.isluno_catalog import CatalogStore
from shared.isluno_config import require_scope, JourneyScope
from shared.isluno_pricing import check, ItineraryError
from shared import config_loader


def init_schema(db):
    db.executescript('''
        CREATE TABLE IF NOT EXISTS isluno_quote_versions (
            id TEXT PRIMARY KEY, scope_key TEXT NOT NULL, version INTEGER NOT NULL, snapshot TEXT NOT NULL,
            pdf BLOB NOT NULL, pdf_sha256 TEXT NOT NULL, fingerprint TEXT NOT NULL, expires_at TEXT NOT NULL,
            UNIQUE(scope_key,version));
        CREATE TRIGGER IF NOT EXISTS isluno_quote_no_update BEFORE UPDATE ON isluno_quote_versions
            BEGIN SELECT RAISE(ABORT, 'immutable quote'); END;
        CREATE TRIGGER IF NOT EXISTS isluno_quote_no_delete BEFORE DELETE ON isluno_quote_versions
            BEGIN SELECT RAISE(ABORT, 'immutable quote'); END;
        CREATE TABLE IF NOT EXISTS isluno_quote_state (
            quote_id TEXT PRIMARY KEY, scope_key TEXT NOT NULL, status TEXT NOT NULL,
            summary_confirmed_at TEXT, approved_at TEXT);
        CREATE TABLE IF NOT EXISTS isluno_quote_latest (scope_key TEXT PRIMARY KEY, quote_id TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS isluno_quote_actions (
            token TEXT PRIMARY KEY, quote_id TEXT NOT NULL, kind TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS isluno_quote_requests (
            scope_key TEXT NOT NULL, trigger_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
            job_id TEXT NOT NULL, PRIMARY KEY(scope_key,trigger_id));
        CREATE TABLE IF NOT EXISTS isluno_quote_jobs (
            id TEXT PRIMARY KEY, scope_key TEXT NOT NULL, quote_id TEXT NOT NULL, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS isluno_quote_deliveries (
            job_id TEXT NOT NULL, part INTEGER NOT NULL, status TEXT NOT NULL, provider_id TEXT,
            PRIMARY KEY(job_id,part));
        CREATE TABLE IF NOT EXISTS isluno_discovery_pacing (recipient TEXT PRIMARY KEY, last_send REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS isluno_discovery_latest (scope_key TEXT PRIMARY KEY, plan_id TEXT NOT NULL);
    ''')
    from agents.social.isluno_payments import init_schema as init_payments
    init_payments(db)


def material(session, itinerary):
    return {'itinerary': itinerary, 'guest': session['guest'],
            'item_details': {i['id']: session['item_details'].get(i['id'], {}) for i in itinerary['items']},
            'document_language': session['document_language'] or session['chat_language'],
            'pending': session['pending']}


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def invalidate_changed(db, scope, session, itinerary):
    """Runs inside the same transaction as every conversation correction."""
    row = db.execute('SELECT v.fingerprint,s.quote_id FROM isluno_quote_latest l JOIN isluno_quote_versions v ON v.id=l.quote_id JOIN isluno_quote_state s ON s.quote_id=v.id WHERE l.scope_key=?', (scope.key,)).fetchone()
    if row and (itinerary is None or digest(material(session, itinerary)) != row['fingerprint']):
        db.execute("UPDATE isluno_quote_state SET status='superseded' WHERE quote_id=?", (row['quote_id'],))
        session['quote_context'] = {'quote_id': row['quote_id'], 'stage': 'superseded'}
        # Delete only the latest pointer, never historical browsing or quote rows.
        db.execute('DELETE FROM isluno_discovery_latest WHERE scope_key=?', (scope.key,))


class QuoteStore:
    def __init__(self, conversation=None, public_base=None):
        if conversation is None:
            from agents.social.isluno_conversation import ConversationStore
            conversation = ConversationStore()
        self.conversation = conversation
        self.itinerary = conversation.itinerary
        self.clock = self.itinerary.clock
        self.public_base = public_base if public_base is not None else (config_loader.get_raw().get('isluno') or {}).get('document_base_url', '')

    @contextmanager
    def db(self):
        with self.conversation.db() as db:
            yield db

    def _current(self, db, scope):
        row = db.execute('SELECT payload FROM isluno_booking_sessions WHERE scope_key=?', (scope.key,)).fetchone()
        check(row is not None, 'no_active_itinerary')
        session = json.loads(row[0])
        check(bool(session['active_itinerary_id']), 'no_active_itinerary')
        return session, self.itinerary._current(db, scope, session['active_itinerary_id'])

    def _valid(self, db, scope, quote_id):
        require_scope(scope)
        row = db.execute('SELECT v.*,s.status,l.quote_id AS latest FROM isluno_quote_versions v JOIN isluno_quote_state s ON s.quote_id=v.id JOIN isluno_quote_latest l ON l.scope_key=v.scope_key WHERE v.id=? AND v.scope_key=?', (quote_id, scope.key)).fetchone()
        check(row is not None, 'quote_not_found')
        check(row['latest'] == quote_id and row['status'] != 'superseded' and datetime.fromisoformat(row['expires_at']) > self.clock(), 'stale_quote_action')
        session, itinerary = self._current(db, scope)
        check(itinerary['status'] == 'draft' and not session['pending'] and digest(material(session, itinerary)) == row['fingerprint'], 'stale_quote_action')
        snapshot = json.loads(row['snapshot'])
        check(snapshot['catalog_revision'] == CatalogStore(self.itinerary.catalog_path).snapshot()['revision'], 'stale_quote_catalog')
        return snapshot, row['status']

    def _job(self, db, scope, snapshot, stage, sent_at):
        words = COPY[snapshot['chat_language']]
        job_id = secrets.token_hex(16)
        parts = []
        if stage == 'summary':
            # All lines are sent, split before provider length limits, never truncated.
            for section in text_sections(snapshot, snapshot['chat_language']):
                for start in range(0, len(section), 3500):
                    parts.append({'accountId': scope.account_id, 'message': section[start:start + 3500]})
        elif stage == 'quote':
            parts.append({'accountId': scope.account_id, 'message': words[15], 'document_quote_id': snapshot['id']})
        else:
            parts.append({'accountId': scope.account_id, 'message': words[16]})
        if stage in {'summary', 'quote'}:
            kind = 'confirm_summary' if stage == 'summary' else 'approve_quote'
            token = 'iq_' + secrets.token_hex(16)
            db.execute('INSERT INTO isluno_quote_actions VALUES(?,?,?)', (token, snapshot['id'], kind))
            parts.append({'accountId': scope.account_id, 'message': words[21] if stage == 'summary' else words[15],
                          'buttons': [{'type': 'postback', 'payload': token, 'title': words[13 if stage == 'summary' else 14]}]})
        if stage == 'approved':
            from agents.social.isluno_payments import mint_payment
            from agents.social.isluno_payment_copy import COPY as PAYMENT_COPY
            token = mint_payment(self, db, scope, snapshot['id'])
            parts[-1]['buttons'] = [{'type':'postback','payload':token,'title':PAYMENT_COPY[snapshot['chat_language']][3]}]
        payload = {'id': job_id, 'quote_id': snapshot['id'], 'scope': json.loads(self.itinerary._scope(scope)),
                   'stage': stage, 'trigger_sent_at': sent_at, 'parts': parts}
        db.execute('INSERT INTO isluno_quote_jobs VALUES(?,?,?,?)', (job_id, scope.key, snapshot['id'], encoded(payload)))
        db.executemany("INSERT INTO isluno_quote_deliveries VALUES(?,?,'queued',NULL)", [(job_id, i) for i in range(len(parts))])
        # Quote stages supersede all prior Add/Help/gallery action contexts.
        db.execute('DELETE FROM isluno_discovery_latest WHERE scope_key=?', (scope.key,))
        return payload

    def _timestamp(self, sent_at):
        try:
            sent = datetime.fromisoformat(sent_at.replace('Z', '+00:00'))
            check(sent.tzinfo is not None and timedelta(0) <= self.clock() - sent < timedelta(hours=24), 'invalid_inbound_time')
        except (ValueError, AttributeError, TypeError) as exc:
            raise ItineraryError('invalid_inbound_time') from exc

    def prepare(self, scope, trigger, sent_at, *, expected_session_revision=None):
        require_scope(scope)
        self._timestamp(sent_at)
        fingerprint = digest(['prepare', expected_session_revision])
        with self.db() as db, db:
            db.execute('BEGIN IMMEDIATE')
            old = self._replay(db, scope, trigger, fingerprint)
            if old: return old
            session, itinerary = self._current(db, scope)
            check(expected_session_revision is None or session['revision'] == expected_session_revision, 'conversation_revision_changed')
            check(itinerary['status'] == 'draft' and itinerary['items'] and not session['pending'], 'quote_intake_incomplete')
            catalog_revision = CatalogStore(self.itinerary.catalog_path).snapshot()['revision']
            # Current turn's source must still be current; never bless stale understanding.
            check(session.get('catalog_revision') == catalog_revision, 'stale_quote_catalog')
            value = material(session, itinerary)
            check(bool(value['guest'].get('name')), 'quote_guest_missing')
            previous = db.execute('SELECT v.*,s.status FROM isluno_quote_latest l JOIN isluno_quote_versions v ON v.id=l.quote_id JOIN isluno_quote_state s ON s.quote_id=v.id WHERE l.scope_key=?', (scope.key,)).fetchone()
            if previous and previous['fingerprint'] == digest(value) and previous['status'] != 'superseded' and datetime.fromisoformat(previous['expires_at']) > self.clock() and json.loads(previous['snapshot'])['catalog_revision'] == catalog_revision:
                snapshot = json.loads(previous['snapshot'])
                # Reuse the current stage's existing durable job, never duplicate sends.
                row = db.execute('SELECT payload FROM isluno_quote_jobs WHERE quote_id=? ORDER BY rowid DESC LIMIT 1', (snapshot['id'],)).fetchone()
                job = json.loads(row[0])
            else:
                if previous:
                    db.execute("UPDATE isluno_quote_state SET status='superseded' WHERE quote_id=?", (previous['id'],))
                version = db.execute('SELECT COALESCE(MAX(version),0)+1 FROM isluno_quote_versions WHERE scope_key=?', (scope.key,)).fetchone()[0]
                snapshot = {**copy.deepcopy(value), 'id': secrets.token_hex(16), 'version': version,
                    'catalog_revision': catalog_revision, 'chat_language': session['chat_language'], 'created_at': self.clock().isoformat(),
                    'demo_only': True, 'real_booking_eligible': False}
                session['stage'] = 'review_approval'
                session['quote_context'] = {'quote_id': snapshot['id'], 'version': version, 'stage': 'summary'}
                db.execute('UPDATE isluno_booking_sessions SET payload=? WHERE scope_key=?', (encoded(session), scope.key))
                raw = render_pdf(snapshot)
                db.execute('INSERT INTO isluno_quote_versions VALUES(?,?,?,?,?,?,?,?)', (snapshot['id'], scope.key, version, encoded(snapshot), raw, hashlib.sha256(raw).hexdigest(), digest(value), (self.clock()+timedelta(hours=24)).isoformat()))
                db.execute("INSERT INTO isluno_quote_state VALUES(?,?,'summary',NULL,NULL)", (snapshot['id'],scope.key))
                db.execute('INSERT INTO isluno_quote_latest VALUES(?,?) ON CONFLICT(scope_key) DO UPDATE SET quote_id=excluded.quote_id', (scope.key,snapshot['id']))
                job = self._job(db, scope, snapshot, 'summary', sent_at)
            self._record(db, scope, trigger, fingerprint, job['id'])
            return job

    def _replay(self, db, scope, trigger, fingerprint):
        check(isinstance(trigger, str) and 0 < len(trigger) <= 512, 'missing_verified_message_id')
        row = db.execute('SELECT * FROM isluno_quote_requests WHERE scope_key=? AND trigger_id=?', (scope.key,trigger)).fetchone()
        if row:
            check(row['fingerprint'] == fingerprint, 'quote_request_conflict')
            job = json.loads(db.execute('SELECT payload FROM isluno_quote_jobs WHERE id=?', (row['job_id'],)).fetchone()[0])
            self._valid(db, scope, job['quote_id'])
            return job

    def _record(self, db, scope, trigger, fingerprint, job_id):
        db.execute('INSERT INTO isluno_quote_requests VALUES(?,?,?,?)', (scope.key,trigger,fingerprint,job_id))

    def fully_accepted(self,db,scope,job_id,seen=None):
        require_scope(scope);seen=set() if seen is None else seen
        if job_id in seen or len(seen)>=50:return False
        seen.add(job_id)
        row=db.execute('SELECT payload FROM isluno_quote_jobs WHERE id=? AND scope_key=?',(job_id,scope.key)).fetchone()
        if not row:return False
        job=json.loads(row[0]);rows=db.execute('SELECT status FROM isluno_quote_deliveries WHERE job_id=?',(job_id,)).fetchall()
        if not rows or any(row[0]!='accepted' for row in rows):return False
        if job.get('answer_plan_id'):
            if not db.execute("SELECT 1 FROM isluno_discovery_plans WHERE id=? AND scope_key=? AND status='accepted'",(job['answer_plan_id'],scope.key)).fetchone():return False
        followup=job.get('followup_job_id') or job.get('fulfillment_job_id')
        return not followup or self.fully_accepted(db,scope,followup,seen)

    def act(self, scope, trigger, sent_at, token, interactive_type):
        require_scope(scope)
        self._timestamp(sent_at)
        check(interactive_type in {'button_reply', 'list_reply'}, 'unverified_quote_action')
        fingerprint = digest(['action', token, interactive_type])
        with self.db() as db, db:
            db.execute('BEGIN IMMEDIATE')
            old = self._replay(db, scope, trigger, fingerprint)
            if old: return old
            row = db.execute('SELECT a.* FROM isluno_quote_actions a JOIN isluno_quote_versions v ON v.id=a.quote_id WHERE a.token=? AND v.scope_key=?', (token,scope.key)).fetchone()
            check(row is not None, 'invalid_quote_action')
            snapshot, stage = self._valid(db, scope, row['quote_id'])
            required, target = ('summary', 'quote') if row['kind'] == 'confirm_summary' else ('quote', 'approved')
            check(stage in {required, target} or row['kind'] == 'confirm_summary' and stage == 'approved', 'wrong_quote_stage')
            if stage == required:
                latest = db.execute('SELECT id FROM isluno_quote_jobs WHERE quote_id=? ORDER BY rowid DESC LIMIT 1', (snapshot['id'],)).fetchone()[0]
                check(self.fully_accepted(db,scope,latest), 'quote_not_fully_accepted')
                session, _ = self._current(db, scope)
                session['revision'] += 1
                session['stage'] = 'payment_confirmation' if target == 'approved' else 'review_approval'
                session['quote_context'] = {'quote_id': snapshot['id'], 'version': snapshot['version'], 'stage': target}
                session['history'] = (session['history'] + [{'role': 'user', 'content': '[Verified WhatsApp action: ' + row['kind'] + ']'}])[-100:]
                db.execute('UPDATE isluno_booking_sessions SET payload=? WHERE scope_key=?', (encoded(session), scope.key))
                field = 'summary_confirmed_at' if target == 'quote' else 'approved_at'
                db.execute(f'UPDATE isluno_quote_state SET status=?,{field}=? WHERE quote_id=?', (target,self.clock().isoformat(),snapshot['id']))
                job = self._job(db, scope, snapshot, target, sent_at)
            else:
                job = json.loads(db.execute('SELECT payload FROM isluno_quote_jobs WHERE quote_id=? ORDER BY rowid DESC LIMIT 1', (snapshot['id'],)).fetchone()[0])
            self._record(db,scope,trigger,fingerprint,job['id'])
            return job

    def job(self, job_id, account_id, conversation_id):
        with self.db() as db:
            row = db.execute('SELECT payload FROM isluno_quote_jobs WHERE id=?', (job_id,)).fetchone()
            check(row is not None, 'quote_job_not_found')
            job = json.loads(row[0])
            scope = JourneyScope(**job['scope'])
            require_scope(scope)
            check(scope.account_id == account_id and scope.conversation_id == conversation_id, 'wrong_quote_recipient')
            snapshot, stage = self._valid(db,scope,job['quote_id'])
            check(job['stage'] == stage, 'stale_quote_job')
            return job

    def compose_answer(self, scope, trigger, sent_at, fulfillment, answer):
        """A new answer references the original quote ledger; never clones sends."""
        require_scope(scope)
        check(fulfillment['scope'] == answer['scope'] == scope.__dict__, 'wrong_quote_recipient')
        ident = hashlib.sha256(encoded([scope.key, trigger, 'hospitality-answer']).encode()).hexdigest()[:32]
        with self.db() as db, db:
            old = db.execute('SELECT payload FROM isluno_quote_jobs WHERE id=? AND scope_key=?', (ident, scope.key)).fetchone()
            if old:
                return json.loads(old[0])
            # Repeated review questions refer to the original quote job, not a
            # chain of old answer plans whose discovery controls are now stale.
            visited = set()
            while fulfillment.get('followup_job_id'):
                target = fulfillment['followup_job_id']
                check(target not in visited and len(visited) < 50, 'invalid_quote_composition')
                visited.add(target)
                row = db.execute('SELECT payload FROM isluno_quote_jobs WHERE id=? AND scope_key=? AND quote_id=?',
                                 (target, scope.key, fulfillment['quote_id'])).fetchone()
                check(row is not None, 'invalid_quote_composition')
                fulfillment = json.loads(row[0])
            job = {**fulfillment, 'id':ident, 'parts':[{'message':answer['body'].get('message') or answer['body']['interactive']['body']['text']}],
                   'answer_plan_id':answer['id'], 'followup_job_id':fulfillment['id'], 'trigger_sent_at':sent_at}
            db.execute('INSERT INTO isluno_quote_jobs VALUES(?,?,?,?)', (ident, scope.key, fulfillment['quote_id'], encoded(job)))
            db.execute("INSERT INTO isluno_quote_deliveries VALUES(?,0,'queued',NULL)", (ident,))
            return job

    def document_url(self, quote_id):
        base = urlsplit(self.public_base) if isinstance(self.public_base,str) else None
        check(base and base.scheme == 'https' and base.hostname and not (base.username or base.password or base.query or base.fragment), 'document_delivery_base_unconfigured')
        return self.public_base.rstrip('/') + '/' + quote_id + '.pdf'

    def document(self, quote_id):
        # Random 128-bit capability, short expiry, revoked on correction. No indexes.
        with self.db() as db:
            row = db.execute('SELECT v.pdf,v.snapshot,j.payload FROM isluno_quote_versions v JOIN isluno_quote_jobs j ON j.quote_id=v.id WHERE v.id=? LIMIT 1', (quote_id,)).fetchone()
            if row is None:
                from agents.social.isluno_payments import PaymentStore
                return PaymentStore(self.conversation).document(quote_id)
            scope = JourneyScope(**json.loads(row['payload'])['scope'])
            _, stage = self._valid(db,scope,quote_id)
            check(stage in {'quote','approved'}, 'quote_not_confirmed')
            return bytes(row['pdf'])

    def approved_snapshot(self, scope, quote_id):
        """ISL-08 must revalidate this binding before minting any payment action."""
        require_scope(scope)
        with self.db() as db:
            snapshot, stage = self._valid(db, scope, quote_id)
            check(stage == 'approved', 'quote_not_approved')
            return snapshot

    def list(self, scope, limit=20, offset=0):
        require_scope(scope)
        check(type(limit) is int and 1 <= limit <= 100 and type(offset) is int and offset >= 0, 'invalid_pagination')
        with self.db() as db:
            rows = db.execute('SELECT v.snapshot,v.pdf_sha256,v.expires_at,s.* FROM isluno_quote_versions v JOIN isluno_quote_state s ON s.quote_id=v.id WHERE v.scope_key=? ORDER BY v.version DESC LIMIT ? OFFSET ?', (scope.key,limit,offset)).fetchall()
            result = []
            for row in rows:
                deliveries = [dict(r) for r in db.execute('SELECT d.* FROM isluno_quote_deliveries d JOIN isluno_quote_jobs j ON j.id=d.job_id WHERE j.quote_id=? ORDER BY j.rowid,d.part', (row['quote_id'],))]
                try:
                    self._valid(db,scope,row['quote_id'])
                    valid = True
                except ItineraryError:
                    valid = False
                result.append({'snapshot': json.loads(row['snapshot']), 'projection': projection(json.loads(row['snapshot'])),
                    'status': row['status'], 'valid': valid, 'summary_confirmed_at': row['summary_confirmed_at'], 'approved_at': row['approved_at'],
                    'expires_at': row['expires_at'], 'pdf_sha256': row['pdf_sha256'], 'deliveries': deliveries,
                    'delivered': None, 'delivery_note': 'accepted means provider acceptance only; no delivered/read evidence'})
            return result


def envelope(job):
    return {'text': job['parts'][0].get('message','Isluno'), 'media': {'url': job['id'], 'type': 'isluno_quote', 'caption': 'Isluno'}}


def build_public_router(store_factory=QuoteStore):
    from fastapi import APIRouter, HTTPException, Response
    from shared.isluno_config import IslunoUnavailable
    import re
    router = APIRouter(prefix='/isluno/documents')

    @router.get('/{quote_id}.pdf')
    def document(quote_id: str):
        try:
            check(re.fullmatch('[a-f0-9]{32}',quote_id) is not None, 'quote_not_found')
            return Response(store_factory().document(quote_id),media_type='application/pdf',
                headers={'Cache-Control':'private, no-store', 'X-Content-Type-Options':'nosniff',
                         'Referrer-Policy':'no-referrer', 'Content-Disposition':'inline; filename="Isluno-quote.pdf"'})
        except (ItineraryError, IslunoUnavailable):
            raise HTTPException(404,detail='Document unavailable')
    return router
