"""Atomic no-money completion and immutable, independently delivered fulfillment."""
from datetime import datetime, timedelta
import copy
import hashlib
import json
import secrets

from agents.social.isluno_quotes import QuoteStore, digest
from agents.social.isluno_itinerary import encoded
from agents.social.isluno_quote_documents import render_pdf
from agents.social.isluno_payment_copy import COPY
from shared.isluno_config import require_scope, JourneyScope
from shared.isluno_pricing import check, ItineraryError


def init_schema(db):
    db.executescript('''
        CREATE TABLE IF NOT EXISTS isluno_payment_actions (
            token TEXT PRIMARY KEY, scope_key TEXT NOT NULL, quote_id TEXT NOT NULL, expires_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS isluno_payments (
            id TEXT PRIMARY KEY, scope_key TEXT NOT NULL, itinerary_id TEXT NOT NULL, quote_id TEXT NOT NULL UNIQUE,
            snapshot TEXT NOT NULL, job_id TEXT NOT NULL, UNIQUE(scope_key,itinerary_id));
        CREATE TRIGGER IF NOT EXISTS isluno_payment_no_update BEFORE UPDATE ON isluno_payments
            BEGIN SELECT RAISE(ABORT,'immutable demo payment'); END;
        CREATE TRIGGER IF NOT EXISTS isluno_payment_no_delete BEFORE DELETE ON isluno_payments
            BEGIN SELECT RAISE(ABORT,'immutable demo payment'); END;
        CREATE TABLE IF NOT EXISTS isluno_paid_documents (
            id TEXT PRIMARY KEY, payment_id TEXT NOT NULL, kind TEXT NOT NULL, item_id TEXT, pdf BLOB NOT NULL,
            sha256 TEXT NOT NULL, expires_at TEXT NOT NULL, ticket_id TEXT UNIQUE, UNIQUE(payment_id,item_id));
        CREATE UNIQUE INDEX IF NOT EXISTS isluno_one_paid_receipt ON isluno_paid_documents(payment_id) WHERE kind='receipt';
        CREATE TRIGGER IF NOT EXISTS isluno_paid_document_no_update BEFORE UPDATE ON isluno_paid_documents
            BEGIN SELECT RAISE(ABORT,'immutable paid document'); END;
        CREATE TRIGGER IF NOT EXISTS isluno_paid_document_no_delete BEFORE DELETE ON isluno_paid_documents
            BEGIN SELECT RAISE(ABORT,'immutable paid document'); END;
        CREATE TABLE IF NOT EXISTS isluno_payment_events (
            scope_key TEXT NOT NULL, trigger_id TEXT NOT NULL, fingerprint TEXT NOT NULL, payment_id TEXT NOT NULL,
            PRIMARY KEY(scope_key,trigger_id));
        CREATE TABLE IF NOT EXISTS isluno_fulfillment_events (scope_key TEXT NOT NULL, event_key TEXT NOT NULL, job_id TEXT NOT NULL, PRIMARY KEY(scope_key,event_key));
        CREATE TABLE IF NOT EXISTS isluno_email_consents (
            token TEXT PRIMARY KEY, scope_key TEXT NOT NULL, payment_id TEXT NOT NULL, recipient TEXT NOT NULL,
            expires_at TEXT NOT NULL, consent_trigger TEXT);
        CREATE TABLE IF NOT EXISTS isluno_paid_emails (
            id TEXT PRIMARY KEY, payment_id TEXT NOT NULL, scope_key TEXT NOT NULL, recipient TEXT NOT NULL,
            consent_token TEXT NOT NULL, status TEXT NOT NULL, message_id TEXT NOT NULL, error TEXT,
            UNIQUE(payment_id,recipient));
    ''')


def mint_payment(store, db, scope, quote_id):
    snapshot, stage = store._valid(db,scope,quote_id)
    check(stage == 'approved','quote_not_approved')
    token='ip_'+secrets.token_hex(16)
    expiry=min(datetime.fromisoformat(db.execute('SELECT expires_at FROM isluno_quote_versions WHERE id=?',(quote_id,)).fetchone()[0]),store.clock()+timedelta(hours=1))
    db.execute('INSERT INTO isluno_payment_actions VALUES(?,?,?,?)',(token,scope.key,quote_id,expiry.isoformat()))
    return token


class PaymentStore(QuoteStore):
    def _paid(self, db, scope, payment_id=None):
        require_scope(scope)
        row = (db.execute('SELECT * FROM isluno_payments WHERE id=? AND scope_key=?',(payment_id,scope.key)).fetchone() if payment_id else
               db.execute('SELECT * FROM isluno_payments WHERE scope_key=? ORDER BY rowid DESC LIMIT 1',(scope.key,)).fetchone())
        check(row is not None,'demo_payment_not_found')
        return row

    def _job_row(self, db, row):
        return json.loads(db.execute('SELECT payload FROM isluno_quote_jobs WHERE id=?',(row['job_id'],)).fetchone()[0])

    def complete(self, scope, trigger, sent_at, token, interactive_type):
        require_scope(scope);self._timestamp(sent_at)
        check(interactive_type in {'button_reply','list_reply'},'unverified_payment_action')
        check(isinstance(trigger,str) and 0<len(trigger)<=512,'missing_verified_message_id')
        fingerprint=digest([token,interactive_type])
        with self.db() as db,db:
            db.execute('BEGIN IMMEDIATE')
            action=db.execute('SELECT * FROM isluno_payment_actions WHERE token=? AND scope_key=?',(token,scope.key)).fetchone()
            check(action is not None,'invalid_payment_action')
            check(datetime.fromisoformat(action['expires_at'])>self.clock(),'expired_payment_action')
            event=db.execute('SELECT * FROM isluno_payment_events WHERE scope_key=? AND trigger_id=?',(scope.key,trigger)).fetchone()
            if event:
                check(event['fingerprint']==fingerprint,'payment_request_conflict')
                return self._job_row(db,self._paid(db,scope,event['payment_id']))
            paid=db.execute('SELECT * FROM isluno_payments WHERE scope_key=? AND quote_id=?',(scope.key,action['quote_id'])).fetchone()
            if paid:
                db.execute('INSERT INTO isluno_payment_events VALUES(?,?,?,?)',(scope.key,trigger,fingerprint,paid['id']))
                return self._job_row(db,paid)
            # No earlier read authorizes this transition: revalidate under BEGIN IMMEDIATE.
            from agents.social.zernio_dm_client import _provider_mutation_account_allowed
            check(_provider_mutation_account_allowed(scope.account_id,'isluno_demo_payment'),'payment_automation_paused')
            quote, stage=self._valid(db,scope,action['quote_id'])
            check(stage=='approved','quote_not_approved')
            latest=db.execute('SELECT id FROM isluno_quote_jobs WHERE quote_id=? ORDER BY rowid DESC LIMIT 1',(quote['id'],)).fetchone()[0]
            check(self.fully_accepted(db,scope,latest),'payment_prompt_not_accepted')
            check(not db.execute("SELECT 1 FROM isluno_operator_requests WHERE scope_key=? AND status IN ('pending','active')",(scope.key,)).fetchone(),'operator_review_active')
            payment_id=secrets.token_hex(16);job_id=secrets.token_hex(16)
            snapshot=copy.deepcopy(quote)
            snapshot.update(payment_id=payment_id,paid_at=self.clock().isoformat(),payment_mode='simulated',real_money_charged=False,supplier_booking_made=False)
            snapshot['ticket_ids']={i['id']:'ISL-DEMO-'+secrets.token_hex(8).upper() for i in quote['itinerary']['items']}
            paid_itinerary=self.itinerary._apply(scope,quote['itinerary']['id'],'payment-'+payment_id,quote['itinerary']['revision'],{'action':'demo_pay'},connection=db)
            snapshot['paid_itinerary_revision']=paid_itinerary['revision']
            db.execute('INSERT INTO isluno_payments VALUES(?,?,?,?,?,?)',(payment_id,scope.key,quote['itinerary']['id'],quote['id'],encoded(snapshot),job_id))
            parts=[{'accountId':scope.account_id,'message':COPY[quote['chat_language']][2]}]
            for kind,item_id in [('receipt',None)]+[('ticket',i['id']) for i in quote['itinerary']['items']]:
                doc_id=secrets.token_hex(16)
                reference=payment_id if kind=='receipt' else snapshot['ticket_ids'][item_id]
                raw=render_pdf(snapshot,kind=kind,reference=reference,item_id=item_id)
                db.execute('INSERT INTO isluno_paid_documents VALUES(?,?,?,?,?,?,?,?)',(doc_id,payment_id,kind,item_id,raw,hashlib.sha256(raw).hexdigest(),(self.clock()+timedelta(days=30)).isoformat(),snapshot['ticket_ids'][item_id] if item_id else None))
                parts.append({'accountId':scope.account_id,'message':COPY[quote['chat_language']][0 if kind=='receipt' else 1]+': '+reference,
                              'document_quote_id':doc_id,'attachmentName':'Isluno-'+kind+'-'+reference+'.pdf'})
            parts.append({'accountId':scope.account_id,'message':COPY[quote['chat_language']][4]+' - '+COPY[quote['chat_language']][8]})
            job={'id':job_id,'quote_id':quote['id'],'payment_id':payment_id,'scope':json.loads(self.itinerary._scope(scope)),
                 'stage':'fulfilled','trigger_sent_at':sent_at,'parts':parts}
            db.execute('INSERT INTO isluno_quote_jobs VALUES(?,?,?,?)',(job_id,scope.key,quote['id'],encoded(job)))
            db.executemany("INSERT INTO isluno_quote_deliveries VALUES(?,?,'queued',NULL)",[(job_id,i) for i in range(len(parts))])
            db.execute('INSERT INTO isluno_payment_events VALUES(?,?,?,?)',(scope.key,trigger,fingerprint,payment_id))
            session,_=self._current(db,scope)
            session['revision']+=1;session['payment_context']={'payment_id':payment_id,'status':'demo_paid'}
            session['quote_context']={'quote_id':quote['id'],'stage':'demo_paid'}
            session['stage']='after_booking'
            session['history']=(session['history']+[{'role':'user','content':'[Verified Complete demo payment action]'}])[-100:]
            db.execute('UPDATE isluno_booking_sessions SET payload=? WHERE scope_key=?',(encoded(session),scope.key))
            db.execute('DELETE FROM isluno_discovery_latest WHERE scope_key=?',(scope.key,))
            check(_provider_mutation_account_allowed(scope.account_id,'isluno_demo_payment'),'payment_automation_paused')
            return job

    def refresh_pending(self, scope, trigger, sent_at, old_token):
        require_scope(scope); self._timestamp(sent_at)
        request_id = 'payment-refresh-' + digest(trigger)
        fingerprint = digest(old_token)
        with self.db() as db, db:
            db.execute('BEGIN IMMEDIATE')
            replay = self._replay(db, scope, request_id, fingerprint)
            if replay: return replay
            action = db.execute('SELECT quote_id FROM isluno_payment_actions WHERE token=? AND scope_key=?', (old_token, scope.key)).fetchone()
            check(action is not None, 'invalid_payment_action')
            snapshot, stage = self._valid(db, scope, action['quote_id'])
            check(stage == 'approved', 'quote_not_approved')
            active = db.execute('SELECT 1 FROM isluno_payment_actions WHERE scope_key=? AND quote_id=? AND expires_at>?', (scope.key, snapshot['id'], self.clock().isoformat())).fetchone()
            if active:
                job = json.loads(db.execute('SELECT payload FROM isluno_quote_jobs WHERE quote_id=? ORDER BY rowid DESC LIMIT 1', (snapshot['id'],)).fetchone()[0])
            else:
                job = self._job(db, scope, snapshot, 'approved', sent_at)
            self._record(db, scope, request_id, fingerprint, job['id'])
            return job

    def job(self, job_id, account_id, conversation_id):
        with self.db() as db:
            row=db.execute('SELECT payload FROM isluno_quote_jobs WHERE id=?',(job_id,)).fetchone()
            check(row is not None,'quote_job_not_found');job=json.loads(row[0]);scope=JourneyScope(**job['scope'])
            require_scope(scope)
            check(scope.account_id==account_id and scope.conversation_id==conversation_id,'wrong_quote_recipient')
            self._paid(db,scope,job['payment_id'])
            return job

    def resume(self, scope, sent_at):
        require_scope(scope);self._timestamp(sent_at)
        with self.db() as db,db:
            db.execute('BEGIN IMMEDIATE');row=self._paid(db,scope);job=self._job_row(db,row)
            job['trigger_sent_at']=sent_at
            db.execute('UPDATE isluno_quote_jobs SET payload=? WHERE id=?',(encoded(job),job['id']))
            return job

    def document(self, document_id):
        with self.db() as db:
            row=db.execute('SELECT d.*,p.job_id FROM isluno_paid_documents d JOIN isluno_payments p ON p.id=d.payment_id WHERE d.id=?',(document_id,)).fetchone()
            check(row is not None and datetime.fromisoformat(row['expires_at'])>self.clock(),'paid_document_unavailable')
            job=json.loads(db.execute('SELECT payload FROM isluno_quote_jobs WHERE id=?',(row['job_id'],)).fetchone()[0])
            require_scope(JourneyScope(**job['scope']))
            return bytes(row['pdf'])

    def records(self, scope):
        require_scope(scope)
        with self.db() as db:
            rows=db.execute('SELECT * FROM isluno_payments WHERE scope_key=? ORDER BY rowid DESC LIMIT 20',(scope.key,)).fetchall()
            return [{'snapshot':json.loads(row['snapshot']),
                     'documents':[dict(r) for r in db.execute('SELECT id,kind,item_id,ticket_id,sha256,expires_at FROM isluno_paid_documents WHERE payment_id=? ORDER BY rowid',(row['id'],))],
                     'deliveries':[dict(r) for r in db.execute('SELECT * FROM isluno_quote_deliveries WHERE job_id=?',(row['job_id'],))],
                     'emails':[dict(r) for r in db.execute('SELECT id,recipient,status,error,message_id FROM isluno_paid_emails WHERE payment_id=?',(row['id'],))]} for row in rows]

    def compose(self, scope, trigger, sent_at, fulfillment, answer):
        """Reference existing ledgers; never copy accepted document parts into a new send."""
        require_scope(scope); self._timestamp(sent_at)
        check(fulfillment['scope'] == answer['scope'] == json.loads(self.itinerary._scope(scope)), 'wrong_quote_recipient')
        event_key = 'composed:' + trigger
        with self.db() as db, db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT job_id FROM isluno_fulfillment_events WHERE scope_key=? AND event_key=?', (scope.key, event_key)).fetchone()
            if old:
                return json.loads(db.execute('SELECT payload FROM isluno_quote_jobs WHERE id=?', (old[0],)).fetchone()[0])
            job_id = secrets.token_hex(16)
            text = answer['body'].get('message') or answer['body']['interactive']['body']['text']
            job = {'id': job_id, 'quote_id': fulfillment['quote_id'], 'payment_id': fulfillment['payment_id'],
                   'scope': fulfillment['scope'], 'stage': 'composed', 'trigger_sent_at': sent_at,
                   'answer_plan_id': answer['id'], 'fulfillment_job_id': fulfillment['id'],
                   'parts': [{'message': text + '\n\n' + fulfillment['parts'][0].get('message', '')}]}
            db.execute('INSERT INTO isluno_quote_jobs VALUES(?,?,?,?)', (job_id, scope.key, job['quote_id'], encoded(job)))
            db.execute('INSERT INTO isluno_fulfillment_events VALUES(?,?,?)', (scope.key, event_key, job_id))
            return job

    def propose_email(self, scope, trigger, sent_at, recipient, *, replace=False):
        from agents.social.mermaid_reservation_email import normalize_email
        check(type(replace) is bool, 'invalid_email_correction')
        supplied = bool(recipient)
        fingerprint = digest([recipient, replace])
        recipient=normalize_email(recipient) if recipient else None
        check(isinstance(trigger,str) and 0<len(trigger)<=512,'missing_verified_message_id')
        require_scope(scope);self._timestamp(sent_at)
        with self.db() as db,db:
            db.execute('BEGIN IMMEDIATE');paid=self._paid(db,scope);snapshot=json.loads(paid['snapshot'])
            event_key='email-proposal:'+trigger
            old=db.execute('SELECT job_id FROM isluno_fulfillment_events WHERE scope_key=? AND event_key=?',(scope.key,event_key)).fetchone()
            if old:
                job=json.loads(db.execute('SELECT payload FROM isluno_quote_jobs WHERE id=?',(old[0],)).fetchone()[0])
                check(job.get('email_proposal_fingerprint')==fingerprint,'email_proposal_conflict')
                return job
            if supplied or replace:
                # Revoke the rejected destination before asking for a usable replacement.
                db.execute("UPDATE isluno_paid_emails SET status='cancelled',error='address_changed' WHERE payment_id=? AND status='queued' AND (? OR ? IS NULL OR recipient!=?)", (paid['id'], replace, recipient, recipient))
                db.execute('DELETE FROM isluno_email_consents WHERE scope_key=? AND consent_trigger IS NULL', (scope.key,))
            if recipient is None:
                job=self._notice(db,scope,paid,sent_at,COPY[snapshot['chat_language']][8])
                job['email_proposal_fingerprint'] = fingerprint
                db.execute('UPDATE isluno_quote_jobs SET payload=? WHERE id=?', (encoded(job), job['id']))
                db.execute('INSERT INTO isluno_fulfillment_events VALUES(?,?,?)',(scope.key,event_key,job['id']))
                return job
            token='ie_'+secrets.token_hex(16)
            db.execute('INSERT INTO isluno_email_consents VALUES(?,?,?,?,?,NULL)',(token,scope.key,paid['id'],recipient,(self.clock()+timedelta(hours=1)).isoformat()))
            job=self._notice(db,scope,paid,sent_at,COPY[snapshot['chat_language']][6]+' '+recipient,
                             [{'type':'postback','payload':token,'title':COPY[snapshot['chat_language']][7]}])
            job['email_recipient']=recipient
            job['email_proposal_fingerprint']=fingerprint
            db.execute('UPDATE isluno_quote_jobs SET payload=? WHERE id=?',(encoded(job),job['id']))
            db.execute('INSERT INTO isluno_fulfillment_events VALUES(?,?,?)',(scope.key,event_key,job['id']))
            return job

    def _notice(self, db, scope, paid, sent_at, text, buttons=None):
        job_id=secrets.token_hex(16)
        job={'id':job_id,'quote_id':paid['quote_id'],'payment_id':paid['id'],'scope':json.loads(self.itinerary._scope(scope)),
             'stage':'email_notice','trigger_sent_at':sent_at,'parts':[{'accountId':scope.account_id,'message':text,'buttons':buttons or []}]}
        db.execute('INSERT INTO isluno_quote_jobs VALUES(?,?,?,?)',(job_id,scope.key,paid['quote_id'],encoded(job)))
        db.execute("INSERT INTO isluno_quote_deliveries VALUES(?,0,'queued',NULL)",(job_id,))
        return job

    def consent_email(self, scope, trigger, sent_at, token, interactive_type):
        require_scope(scope);self._timestamp(sent_at)
        check(interactive_type in {'button_reply','list_reply'},'unverified_email_consent')
        check(isinstance(trigger,str) and 0<len(trigger)<=512,'missing_verified_message_id')
        with self.db() as db,db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM isluno_email_consents WHERE token=? AND scope_key=?',(token,scope.key)).fetchone()
            check(row is not None and datetime.fromisoformat(row['expires_at'])>self.clock(),'invalid_email_consent')
            paid=self._paid(db,scope,row['payment_id']);snapshot=json.loads(paid['snapshot'])
            event_key='email-consent:'+token
            old=db.execute('SELECT job_id FROM isluno_fulfillment_events WHERE scope_key=? AND event_key=?',(scope.key,event_key)).fetchone()
            if old: return json.loads(db.execute('SELECT payload FROM isluno_quote_jobs WHERE id=?',(old[0],)).fetchone()[0])
            db.execute('UPDATE isluno_email_consents SET consent_trigger=COALESCE(consent_trigger,?) WHERE token=?',(trigger,token))
            email_id=secrets.token_hex(16)
            db.execute("INSERT OR IGNORE INTO isluno_paid_emails VALUES(?,?,?,?,?,'queued',?,NULL)",(email_id,paid['id'],scope.key,row['recipient'],token,'<isluno.'+email_id+'@demo.isluno.com>'))
            db.execute("UPDATE isluno_paid_emails SET status='queued',consent_token=?,error=NULL WHERE payment_id=? AND recipient=? AND status='cancelled'",(token,paid['id'],row['recipient']))
            job=self._notice(db,scope,paid,sent_at,COPY[snapshot['chat_language']][5])
            db.execute('INSERT INTO isluno_fulfillment_events VALUES(?,?,?)',(scope.key,event_key,job['id']))
            return job


def envelope(job):
    return {'text':job['parts'][0].get('message','Isluno'),'media':{'url':job['id'],'type':'isluno_fulfillment','caption':'Isluno'}}
