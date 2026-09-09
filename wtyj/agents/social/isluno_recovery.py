"""Durable reminder eligibility and operator recovery, without blind retries."""
from shared.mermaid_maintenance import participating as _maintenance_participating
import hashlib
import json
from datetime import datetime,timedelta
from pathlib import Path
from agents.social.isluno_conversation import ConversationStore
from shared import isluno_config
from shared.isluno_pricing import ItineraryError


def runtime_guard(scope):
    from shared import state_registry,icp_overrides
    from agents.social.zernio_dm_client import _provider_account_allowed
    try:
        isluno_config.require_scope(scope)
        if state_registry.get_blocked(scope.conversation_id) or state_registry.get_ai_muted(scope.conversation_id):return False
        if state_registry.get_active_escalation_mode(scope.conversation_id) in {'soft','hard'}:return False
        flags=(state_registry.wa_get_booking_state(scope.conversation_id) or {}).get('flags') or {}
        if flags.get('mermaid_reminders_opt_out') or flags.get(state_registry.MERMAID_LOOP_STOPPED_FLAG):return False
        import sqlite3
        with sqlite3.connect(state_registry.DB_PATH) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='inbound_processing_events'").fetchone() and db.execute("SELECT 1 FROM inbound_processing_events WHERE conversation_id=? AND status IN ('received','processing','recovering')",(scope.conversation_id,)).fetchone():return False
        controls=icp_overrides.fetch_overrides_fresh()
        if icp_overrides.auto_reply_state(controls) is not True or icp_overrides.whatsapp_inbox_state(controls) is not True:return False
        return _provider_account_allowed(scope.account_id,'isluno_recovery',boundary='mutation') is True
    except Exception:return False


class RecoveryStore:
    def __init__(self,conversation=None):self.conversation=conversation or ConversationStore();self.clock=self.conversation.itinerary.clock
    def db(self):
        return self.conversation.db()
    def initialize(self):
        with self.db() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS isluno_recovery_contacts(scope_key TEXT PRIMARY KEY,scope_json TEXT NOT NULL,last_trigger TEXT NOT NULL,last_at TEXT NOT NULL,opt_out INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS isluno_recovery_inbound(scope_key TEXT NOT NULL,trigger_id TEXT NOT NULL,PRIMARY KEY(scope_key,trigger_id));
            CREATE TABLE IF NOT EXISTS isluno_reminders(id TEXT PRIMARY KEY,scope_key TEXT NOT NULL,itinerary_id TEXT NOT NULL,session_revision INTEGER NOT NULL,
                trigger_id TEXT NOT NULL,anchor_at TEXT NOT NULL,due_at TEXT NOT NULL,reply_kind TEXT NOT NULL,reply_id TEXT NOT NULL,message TEXT NOT NULL,
                status TEXT NOT NULL,provider_id TEXT,reason TEXT);
            CREATE TABLE IF NOT EXISTS isluno_recovery_incidents(id TEXT PRIMARY KEY,scope_key TEXT NOT NULL,itinerary_id TEXT,trigger_id TEXT NOT NULL,
                kind TEXT NOT NULL,status TEXT NOT NULL,code TEXT NOT NULL,created_at TEXT NOT NULL);''')
    def observe(self,scope,trigger,sent_at):
        isluno_config.require_scope(scope);self.initialize()
        with self.db() as db,db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('INSERT OR IGNORE INTO isluno_recovery_inbound VALUES(?,?)',(scope.key,trigger)).rowcount:return
            db.execute("UPDATE isluno_reminders SET status='suppressed',reason='customer_reply' WHERE scope_key=? AND status='queued'",(scope.key,))
            db.execute('INSERT INTO isluno_recovery_contacts VALUES(?,?,?,?,0) ON CONFLICT(scope_key) DO UPDATE SET last_trigger=excluded.last_trigger,last_at=excluded.last_at',(scope.key,json.dumps(scope.__dict__),trigger,sent_at))
    def opt_out(self,scope):
        isluno_config.require_scope(scope);self.initialize()
        with self.db() as db,db:
            db.execute('UPDATE isluno_recovery_contacts SET opt_out=1 WHERE scope_key=?',(scope.key,))
            db.execute("UPDATE isluno_reminders SET status='suppressed',reason='opt_out' WHERE scope_key=? AND status='queued'",(scope.key,))
    def progress(self,scope,trigger):
        self.initialize();isluno_config.require_scope(scope)
        with self.db() as db,db:
            db.execute('BEGIN IMMEDIATE')
            completed=db.execute('SELECT outcome FROM isluno_conversation_turns WHERE scope_key=? AND trigger_id=?',(scope.key,trigger)).fetchone()
            if not completed or not completed[0]:return
            applied_revision=json.loads(completed[0])['session']['revision']
            rows=db.execute("SELECT i.id,t.baseline FROM isluno_recovery_incidents i JOIN isluno_conversation_turns t ON t.scope_key=i.scope_key AND t.trigger_id=i.trigger_id WHERE i.scope_key=? AND i.trigger_id!=? AND i.kind IN ('understanding_failure','understanding_stalled') AND i.status='operator_review'",(scope.key,trigger)).fetchall()
            for row in rows:
                # Replaying an older outcome is not evidence of later progress.
                if applied_revision>json.loads(row['baseline'])['revision']:
                    db.execute("UPDATE isluno_recovery_incidents SET status='progress_resumed_new_turn' WHERE id=?",(row['id'],))
    def incident(self,scope,trigger,kind,code):
        self.initialize();isluno_config.require_scope(scope)
        session=self.conversation.session(scope);identifier=hashlib.sha256((scope.key+trigger+kind).encode()).hexdigest()
        with self.db() as db,db:
            db.execute('INSERT OR IGNORE INTO isluno_recovery_incidents VALUES(?,?,?,?,?,?,?,?)',(identifier,scope.key,session['active_itinerary_id'],trigger,kind,'operator_review',code,self.clock().isoformat()))
    def reconcile_claims(self):
        """Surface unfinished claims older than 15 minutes; never infer failure or retry.

        Recent claims remain in flight. Pre-migration rows start their observation
        clock on first scan; a missing timestamp is not evidence of abandonment.
        """
        self.initialize()
        cutoff=(self.clock()-timedelta(minutes=15)).isoformat()
        with self.db() as db,db:
            rows=db.execute("SELECT t.*,c.scope_json FROM isluno_conversation_turns t JOIN isluno_recovery_contacts c ON c.scope_key=t.scope_key WHERE t.decision IS NULL AND t.outcome IS NULL AND (t.claimed_at IS NULL OR t.claimed_at<=?)",(cutoff,)).fetchall()
        for row in rows:
            scope=isluno_config.JourneyScope(**json.loads(row['scope_json']))
            try:isluno_config.require_scope(scope)
            except isluno_config.IslunoUnavailable:continue
            from agents.social.isluno_conversation import understanding_inflight
            if understanding_inflight(self.conversation,scope,row['trigger_id']):continue
            with self.db() as db,db:
                db.execute('BEGIN IMMEDIATE')
                pending=db.execute('SELECT decision,outcome,claimed_at FROM isluno_conversation_turns WHERE scope_key=? AND trigger_id=?',(scope.key,row['trigger_id'])).fetchone()
                if not pending or pending['decision'] is not None or pending['outcome'] is not None:continue
                if pending['claimed_at'] is None:
                    db.execute('UPDATE isluno_conversation_turns SET claimed_at=? WHERE scope_key=? AND trigger_id=?',(self.clock().isoformat(),scope.key,row['trigger_id']))
                    continue
                if pending['claimed_at']>cutoff:continue
                if db.execute("SELECT 1 FROM isluno_recovery_incidents WHERE scope_key=? AND trigger_id=? AND kind IN ('understanding_failure','understanding_stalled')",(scope.key,row['trigger_id'])).fetchone():continue
                baseline=json.loads(row['baseline'])
                saved=db.execute('SELECT payload FROM isluno_booking_sessions WHERE scope_key=?',(scope.key,)).fetchone()
                resumed=saved and json.loads(saved[0])['revision']>baseline['revision']
                identifier=hashlib.sha256((scope.key+row['trigger_id']+'understanding_stalled').encode()).hexdigest()
                db.execute('INSERT OR IGNORE INTO isluno_recovery_incidents VALUES(?,?,?,?,?,?,?,?)',(identifier,scope.key,baseline['active_itinerary_id'],row['trigger_id'],'understanding_stalled','progress_resumed_new_turn' if resumed else 'operator_review','stale_claim_outcome_unknown',self.clock().isoformat()))

    def audit(self):
        isluno_config.active_profile();self.reconcile_claims()
        from shared import config_loader
        allowed=config_loader.get_raw()['channel_account_allowlist']['zernio_accounts'][0]
        with self.db() as db:
            scopes=[r[0] for r in db.execute("SELECT scope_key FROM isluno_recovery_contacts WHERE json_extract(scope_json,'$.tenant_slug')=? AND json_extract(scope_json,'$.account_id')=? AND json_extract(scope_json,'$.journey_type')=? AND json_extract(scope_json,'$.schema_version')=?",('mermaid',allowed,isluno_config.JOURNEY,isluno_config.SCHEMA_VERSION))]
            incidents=[];reminders=[];outbound=[]
            for scope in scopes:
                outbound.extend(dict(r) for r in db.execute("SELECT id,status,provider_id FROM isluno_discovery_plans WHERE scope_key=? AND status NOT IN ('queued','accepted','consumed_action')",(scope,)))
                incidents.extend(dict(r) for r in db.execute('SELECT id,scope_key,itinerary_id,trigger_id,kind,status,code,created_at FROM isluno_recovery_incidents WHERE scope_key=?',(scope,)))
                reminders.extend(dict(r) for r in db.execute('SELECT id,itinerary_id,due_at,status,provider_id,reason FROM isluno_reminders WHERE scope_key=?',(scope,)))
        from agents.social.isluno_transition import audit
        return {'outbound_failures':outbound,'reminders_enabled':bool(self.policy()),'incidents':incidents,'reminders':reminders,'legacy':audit(self.conversation.itinerary.db_path)}

    def policy(self):
        from shared import config_loader
        path=Path(config_loader._CONFIG_PATH).with_name('isluno_recovery.json')
        if not path.exists():return {}
        data=json.loads(path.read_text())
        if data.get('enabled') is not True or data.get('tenant_slug')!='mermaid' or data.get('journey_type')!=isluno_config.JOURNEY:return {}
        if data.get('hours')!=[6,18] or not isinstance(data.get('messages'),dict):return {}
        return data
    def schedule(self,scope,trigger,result):
        self.initialize();policy=self.policy()
        if not policy:return
        session=self.conversation.session(scope)
        if not session['active_itinerary_id']:return
        itinerary=self.conversation.itinerary.get(scope,session['active_itinerary_id'])
        if itinerary['status']!='draft':return
        media=result.get('media') or {};message=policy['messages'].get(session['chat_language'])
        if media.get('type') not in {'isluno_discovery','isluno_quote'} or not isinstance(message,str) or not 0<len(message)<=4096:return
        with self.db() as db,db:
            row=db.execute('SELECT * FROM isluno_recovery_contacts WHERE scope_key=?',(scope.key,)).fetchone()
            if not row or row['opt_out'] or row['last_trigger']!=trigger:return
            try:anchor=datetime.fromisoformat(row['last_at'].replace('Z','+00:00'))
            except (ValueError,TypeError):return
            if anchor.tzinfo is None or anchor>self.clock():return
            for hour in policy['hours']:
                identifier=hashlib.sha256((scope.key+trigger+str(session['revision'])+str(hour)).encode()).hexdigest()
                db.execute('INSERT OR IGNORE INTO isluno_reminders VALUES(?,?,?,?,?,?,?,?,?,?,\'queued\',NULL,NULL)',(identifier,scope.key,itinerary['id'],session['revision'],trigger,row['last_at'],(anchor+timedelta(hours=hour)).isoformat(),media['type'],media['url'],message))
    def reason(self,scope,row):
        isluno_config.require_scope(scope)
        if not self.policy():return 'policy_disabled'
        with self.db() as db:
            contact=db.execute('SELECT * FROM isluno_recovery_contacts WHERE scope_key=?',(scope.key,)).fetchone()
            if not contact or contact['last_trigger']!=row['trigger_id']:return 'customer_reply'
            if contact['opt_out']:return 'opt_out'
            session_row=db.execute('SELECT payload FROM isluno_booking_sessions WHERE scope_key=?',(scope.key,)).fetchone()
            session=json.loads(session_row[0]) if session_row else {}
            if session.get('active_itinerary_id')!=row['itinerary_id'] or session.get('revision')!=row['session_revision']:return 'journey_changed'
            current=db.execute('SELECT payload_json FROM isluno_itineraries WHERE scope_key=? AND itinerary_id=?',(scope.key,row['itinerary_id'])).fetchone()
            if not current or json.loads(current[0])['status']!='draft':return 'completed'
            if db.execute("SELECT 1 FROM isluno_operator_requests WHERE scope_key=? AND status IN ('pending','active') AND reason!='demo_fulfillment_review'",(scope.key,)).fetchone():return 'takeover'
            if row['reply_kind']=='isluno_discovery':
                accepted=db.execute("SELECT 1 FROM isluno_discovery_plans WHERE id=? AND scope_key=? AND status='accepted'",(row['reply_id'],scope.key)).fetchone()
                if not accepted:return 'reply_not_accepted'
            else:
                from agents.social.isluno_quotes import QuoteStore
                try:QuoteStore(self.conversation).job(row['reply_id'],scope.account_id,scope.conversation_id)
                except ItineraryError:return 'quote_changed'
                if db.execute("SELECT 1 FROM isluno_quote_deliveries WHERE job_id=? AND status!='accepted'",(row['reply_id'],)).fetchone():return 'reply_not_accepted'
        return None
    def dispatch(self,identifier,*,post=None,guard=None,window=None):
        from agents.social.isluno_delivery import post_once
        from agents.social.zernio_dm_client import whatsapp_customer_service_window
        guard=guard or runtime_guard;window=window or whatsapp_customer_service_window
        self.initialize()
        with self.db() as db:
            row=db.execute('SELECT * FROM isluno_reminders WHERE id=?',(identifier,)).fetchone()
            if row is None:return False
            row=dict(row)
            contact=db.execute('SELECT scope_json FROM isluno_recovery_contacts WHERE scope_key=?',(row['scope_key'],)).fetchone()
        if row['status']=='accepted':return True
        if row['status']!='queued' or datetime.fromisoformat(row['due_at'])>self.clock():return False
        scope=isluno_config.JourneyScope(**json.loads(contact[0]))
        def suppression():
            try:reason=self.reason(scope,row)
            except (isluno_config.IslunoUnavailable,ItineraryError):return 'scope_unavailable'
            if reason:return reason
            try:age=(self.clock()-datetime.fromisoformat(row['anchor_at'].replace('Z','+00:00'))).total_seconds()
            except (ValueError,TypeError):return 'window_unavailable'
            if age<0 or age>=86400:return 'window_closed'
            if not guard(scope):return 'automation_or_takeover_unavailable'
            if window(scope.conversation_id,scope.account_id,row['anchor_at']).get('open') is not True:return 'window_closed'
            return None
        reason=suppression()
        with self.db() as db,db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM isluno_reminders WHERE scope_key=? AND trigger_id=? AND due_at>? AND due_at<=?',(scope.key,row['trigger_id'],row['due_at'],self.clock().isoformat())).fetchone():reason='superseded_reminder'
            recipient=hashlib.sha256(json.dumps([scope.tenant_slug,scope.account_id,scope.customer_ref],separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
            pacing=db.execute('SELECT last_send FROM isluno_discovery_pacing WHERE recipient=?',(recipient,)).fetchone()
            if not reason and pacing and self.clock().timestamp()-pacing[0]<6:return False
            changed=db.execute("UPDATE isluno_reminders SET status=?,reason=? WHERE id=? AND status='queued'",('suppressed' if reason else 'claimed',reason,identifier)).rowcount
            if changed and not reason:db.execute('INSERT INTO isluno_discovery_pacing VALUES(?,?) ON CONFLICT(recipient) DO UPDATE SET last_send=excluded.last_send',(recipient,self.clock().timestamp()))
        if not changed or reason:return False
        reason=suppression()
        if reason:result={'status':'suppressed','reason':reason}
        else:
            try:result=(post or (lambda scope,body,key:post_once(scope,body,key,guard=lambda:suppression() is None)))(scope,{'accountId':scope.account_id,'message':row['message']},'isluno-reminder-'+identifier)
            except Exception:result={'status':'ambiguous','reason':'provider_exception'}
        status=result.get('status','ambiguous')
        if status=='accepted' and not result.get('provider_id'):status='ambiguous'
        if status not in {'accepted','rejected','ambiguous','suppressed','blocked','window_closed'}:status='ambiguous'
        with self.db() as db,db:
            db.execute('UPDATE isluno_reminders SET status=?,provider_id=?,reason=? WHERE id=?',(status,result.get('provider_id'),result.get('reason'),identifier))
        return status=='accepted'


@_maintenance_participating('scheduled')
def run_once(*,store=None,post=None,guard=None,window=None):
    from agents.social.isluno_transition import requested,ensure
    if not requested():
        from agents.social.isluno_transition import blocked,rollback
        target=store.conversation.itinerary.db_path if store else None
        if blocked(target):rollback(target,store.clock() if store else None)
        return 0
    store=store or RecoveryStore();ensure(store.conversation.itinerary.db_path,store.clock());store.initialize();store.reconcile_claims()
    if not store.policy():return 0
    with store.db() as db:
        rows=db.execute("SELECT id FROM isluno_reminders WHERE status='queued' AND due_at<=? ORDER BY due_at LIMIT 10",(store.clock().isoformat(),)).fetchall()
    return sum(store.dispatch(row[0],post=post,guard=guard,window=window) for row in rows)
