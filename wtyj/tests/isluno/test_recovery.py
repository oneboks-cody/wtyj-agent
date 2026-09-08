"""Actual-handler recovery, clock-driven reminders and legacy rollback rehearsal."""
import copy
import json
import sqlite3
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
import test_operations as operations
from test_conversation import response,NOW
from agents.social.isluno_recovery import RecoveryStore,run_once
from agents.social.isluno_delivery import send_plan
from agents.social import isluno_transition as transition
from shared import state_registry

class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.t=operations.OperationsTests('test_reads_do_not_modify_snapshots_or_create_delivery');self.t.setUp();self.addCleanup(self.t.doCleanups)
        self.store=RecoveryStore(self.t.store)
        policy=json.loads((Path(__file__).resolve().parents[3]/'clients/mermaid/config/isluno_recovery.json').read_text());policy['enabled']=True
        (self.t.directory/'isluno_recovery.json').write_text(json.dumps(policy))
        self.addCleanup(patch.stopall)
        patch.object(state_registry,'DB_PATH',str(self.t.itinerary.db_path)).start()
        self.calls=[]
    def plan(self):
        result=self.t.initial();self.assertTrue(send_plan(self.t.scope().conversation_id,self.t.scope().account_id,result['media']['url'],store=self.t.discovery,post=lambda *a:{'status':'accepted','provider_id':'original-reply'},window=lambda *a:{'open':True}))
        with self.store.db() as db:return [dict(r) for r in db.execute('SELECT * FROM isluno_reminders ORDER BY due_at')]
    def send(self,identifier,**kwargs):
        def post(*args):self.calls.append(args);return {'status':'accepted','provider_id':'reminder-fixture'}
        return self.store.dispatch(identifier,post=kwargs.pop('post',post),guard=kwargs.pop('guard',lambda scope:True),window=kwargs.pop('window',lambda *a:{'open':True}),**kwargs)
    def test_due_once_restart_and_later_slot_do_not_replay(self):
        rows=self.plan();self.assertEqual(len(rows),2);self.assertFalse(self.send(rows[0]['id']))
        self.t.now+=timedelta(hours=6);self.assertTrue(self.send(rows[0]['id']));self.assertEqual(len(self.calls),1)
        self.store=RecoveryStore(self.t.store);self.assertTrue(self.send(rows[0]['id']));self.assertEqual(len(self.calls),1)
        self.t.now+=timedelta(hours=12);self.assertTrue(self.send(rows[1]['id']));self.assertEqual(len(self.calls),2)
    def test_latest_due_slot_avoids_restart_catchup_burst(self):
        rows=self.plan();self.t.now+=timedelta(hours=19)
        self.assertFalse(self.send(rows[0]['id']));self.assertTrue(self.send(rows[1]['id']));self.assertEqual(len(self.calls),1)
    def test_reply_opt_out_takeover_completion_and_window_suppress(self):
        for condition in ('reply','opt_out','human','completion','window','control'):
            with self.subTest(condition=condition):
                self.t.now=NOW
                # Each condition owns its own durable customer scope.
                self.t.scope=lambda condition=condition:__import__('shared.isluno_config',fromlist=['verified_scope']).verified_scope(account_id='synthetic-account',conversation_id='recovery-'+condition,customer_ref='guest-'+condition)
                rows=self.plan();identifier=rows[0]['id']
                if condition=='reply':self.t.turn(response(question='Details?'))
                if condition=='opt_out':self.t.turn(response('stop_reminders'))
                if condition=='human':self.t.turn(response('human'))
                if condition=='completion':
                    active=self.t.active();self.t.itinerary._apply(self.t.scope(),active['id'],'cancel-for-test',active['revision'],{'action':'cancel'})
                self.t.now+=timedelta(hours=6)
                self.assertFalse(self.send(identifier,guard=lambda scope:condition!='control',window=lambda *a:{'open':condition!='window'}))
        self.assertEqual(self.calls,[])
    def test_ambiguous_exception_and_crash_never_retry(self):
        rows=self.plan();self.t.now+=timedelta(hours=6)
        def fail(*args):self.calls.append(args);raise RuntimeError('Synthetic provider timeout')
        self.assertFalse(self.send(rows[0]['id'],post=fail));self.assertFalse(self.send(rows[0]['id']));self.assertEqual(len(self.calls),1)
        self.t.now+=timedelta(hours=12)
        class Crash(BaseException):pass
        def crash(*args):raise Crash()
        with self.assertRaises(Crash):self.send(rows[1]['id'],post=crash)
        self.store=RecoveryStore(self.t.store);self.assertFalse(self.send(rows[1]['id']));self.assertEqual(len(self.calls),1)
    def test_model_failure_preserves_progress_and_duplicate_does_not_recall_model(self):
        self.t.initial();before=self.t.active()
        def failure():raise RuntimeError('Synthetic model failure')
        result,calls=self.t.turn(response('update',[{'date':'2026-10-20'}]),trigger='failed-model',before_response=failure)
        self.assertEqual(result,'');self.assertEqual(calls,1);self.assertEqual(self.t.active(),before)
        result,calls=self.t.turn(response('update',[{'date':'2026-10-20'}]),trigger='failed-model')
        self.assertEqual(result,'');self.assertEqual(calls,0)
        with self.store.db() as db:
            incident=db.execute('SELECT * FROM isluno_recovery_incidents').fetchone();self.assertEqual(incident['kind'],'understanding_failure');self.assertEqual(incident['status'],'operator_review')
        self.t.turn(response('update',[{'date':'2026-10-20'}]));self.assertEqual(self.t.active()['items'][0]['selection']['date'],'2026-10-20')
    def test_native_opt_out_acknowledgement_and_policy_off(self):
        self.plan();before=self.t.active();result,_=self.t.turn(response('stop_reminders'))
        self.assertIn('Reminders are stopped',result['text']);self.assertEqual(self.t.active(),before)
        self.t.turn(response('none'));self.t.now+=timedelta(hours=20)
        self.assertEqual(run_once(store=self.store,post=lambda *a:self.calls.append(a),guard=lambda s:True,window=lambda *a:{'open':True}),0)
        (self.t.directory/'isluno_recovery.json').unlink();self.assertEqual(self.store.policy(),{})

    def test_cutover_and_rollback_quarantine_legacy_callers_without_changing_history(self):
        from agents.social import mermaid_reservation_store as legacy,mermaid_documents as documents,mermaid_abandoned_reminders as reminders,mermaid_delivery_reconciliation as reconciliation,senders
        # Initialize the actual legacy schemas, then insert only synthetic rows.
        with legacy._conn() as db:
            db.execute("INSERT INTO mermaid_reservations(public_id,tenant_slug,conversation_id,zernio_account_id,summary_version,customer_name,language,intake_json,catalog_version,monetary_snapshot_json,state,availability_source,booking_code,created_at,updated_at) VALUES('old','mermaid','legacy-guest','synthetic-account','old-version','Synthetic Legacy','en','{}','legacy-fixture','{}','quote_sent','demo_assumed','OLD',?,?)",(NOW.isoformat(),NOW.isoformat()))
            db.execute("INSERT INTO mermaid_checkout_links VALUES('old-token','mermaid','old',9999999999,1)")
        with documents._conn() as db:
            db.execute("INSERT INTO mermaid_delivery_jobs VALUES('old-job','mermaid','old','old-document','legacy-guest','quote','pending','old-delivery-key',1,'unknown',?,?)",(NOW.isoformat(),NOW.isoformat()))
        with reminders.connection(NOW) as db:
            db.execute("INSERT INTO mermaid_abandoned_reminders VALUES('old-reminder','mermaid','legacy-guest',1,6,'sending',?,?)",(NOW.isoformat(),NOW.isoformat()))
        with sqlite3.connect(self.t.itinerary.db_path) as db:
            db.execute('CREATE TABLE inbound_processing_events(message_id TEXT PRIMARY KEY,conversation_id TEXT,channel TEXT,status TEXT,payload_json TEXT)')
            db.execute('INSERT INTO inbound_processing_events VALUES(?,?,?,?,?)',('old-inbound','legacy-guest','whatsapp','processing',json.dumps({'account_id':'synthetic-account'})))
            before={table:list(db.execute('SELECT * FROM '+table)) for table in ('mermaid_reservations','mermaid_checkout_links','mermaid_delivery_jobs','mermaid_abandoned_reminders','inbound_processing_events')}
        transition.ensure(self.t.itinerary.db_path,NOW)
        audit=transition.audit(self.t.itinerary.db_path);self.assertEqual(len(audit['quarantined']),5)
        self.assertEqual({r['disposition'] for r in audit['quarantined']},{'operator_review_no_automatic_replay','uncertain_outcome_reconcile_no_replay'})
        self.assertTrue(transition.quarantined_inbound(['old-inbound']))
        # Execute the exact recovery caller with a synthetic claimed legacy batch.
        import ast,hashlib
        source=ast.parse((Path(__file__).resolve().parents[2]/'agents/social/webhook_server.py').read_text())
        function=next(n for n in source.body if isinstance(n,ast.FunctionDef) and n.name=='_recover_stale_ali_inbound_once')
        namespace={'state_registry':state_registry,'hashlib':hashlib,'log':lambda *a,**k:None}
        exec(compile(ast.Module(body=[function],type_ignores=[]),'webhook_server.py','exec'),namespace)
        with patch.object(state_registry,'inbound_processing_claim_recoverable',return_value=[{'message_id':'old-inbound','conversation_id':'legacy-guest','processing_token':'fixture-claim'}]),patch.object(state_registry,'inbound_processing_bulk_update') as disposition:
            self.assertEqual(namespace['_recover_stale_ali_inbound_once'](),0)
            self.assertEqual(disposition.call_args.kwargs['reason'],'isluno_legacy_quarantine')

        for rollback in (False,True):
            if rollback:
                config=copy.deepcopy(self.t.config);config['features']={};self.t.write_config(config)
            self.assertTrue(transition.blocked())
            with self.assertRaises(legacy.MermaidReservationError):legacy.confirm_reservation('legacy-guest',{},idempotency_key='old-button')
            with self.assertRaises(legacy.MermaidReservationError):legacy.complete_demo_payment('old',payment_reference='old-pay',idempotency_key='old-pay')
            self.assertEqual(reminders.run_once(NOW+timedelta(hours=6)),0)
            self.assertEqual(reconciliation.reconcile_job('old-job'),'quarantined')
            self.assertFalse(documents.claim_initial_delivery('old-job'))
            from agents.social import mermaid_demo_payment,mermaid_reservation_email,mermaid_date_changes,mermaid_document_language
            self.assertEqual(mermaid_demo_payment.short_checkout_page('old-token').status_code,410)
            self.assertEqual(mermaid_demo_payment.complete_short_checkout('old-token','success').status_code,410)
            self.assertEqual(mermaid_reservation_email._send({},'demo@example.invalid','old-email','en'),'')
            self.assertFalse(mermaid_date_changes.send_confirmation('legacy-guest','synthetic-account','old-token'))
            self.assertFalse(mermaid_document_language.send_picker('legacy-guest','synthetic-account','old-token'))
            with patch.object(senders.ZernioSender,'send') as provider:
                self.assertFalse(senders.send_reply('whatsapp','legacy-guest','synthetic-account','Legacy reply'));provider.assert_not_called()
        with sqlite3.connect(self.t.itinerary.db_path) as db:
            self.assertEqual({table:list(db.execute('SELECT * FROM '+table)) for table in before},before)
        self.assertEqual(transition.audit(self.t.itinerary.db_path),audit)
        self.rehearsal_evidence={'fixture_only':True,'legacy_tables_unchanged':True,'cutover':audit,'rollback_fence_retained':True,'legacy_checkout_http_status':410,'quarantined_inbound_recovery_replayed':False,'provider_calls':0}
        config=copy.deepcopy(self.t.config);config['slug']='ali-car-rental';config['business']['slug']='ali-car-rental';config['features']={};self.t.write_config(config)
        self.assertFalse(transition.blocked());self.assertFalse(transition.quarantined_inbound(['old-inbound']))
        with patch.object(senders.ZernioSender,'send',return_value=True) as provider:
            self.assertTrue(senders.send_reply('whatsapp','other','other-account','Other tenant'));provider.assert_called_once()

    def test_rollback_suppresses_queued_isluno_reminders_after_reactivation(self):
        rows=self.plan();config=copy.deepcopy(self.t.config);config['features']={};self.t.write_config(config)
        self.assertEqual(run_once(store=self.store),0)
        self.t.write_config(self.t.config);self.t.now+=timedelta(hours=19)
        self.assertFalse(self.send(rows[0]['id']));self.assertFalse(self.send(rows[1]['id']));self.assertEqual(self.calls,[])
        with self.store.db() as db:self.assertEqual({r[0] for r in db.execute('SELECT reason FROM isluno_reminders')},{'rollback'})
    def test_recheck_after_claim_suppresses_new_reply_without_dispatch(self):
        rows=self.plan();self.t.now+=timedelta(hours=6);checks=[]
        def guard(scope):
            checks.append(1)
            if len(checks)==1:self.store.observe(scope,'reply-during-claim',self.t.now.isoformat())
            return True
        self.assertFalse(self.send(rows[0]['id'],guard=guard));self.assertEqual(self.calls,[])
    def test_runtime_guard_preserves_verified_control_and_legacy_opt_out(self):
        from agents.social.isluno_recovery import runtime_guard
        from shared import icp_overrides
        from agents.social import zernio_dm_client
        with patch.object(state_registry,'get_blocked',return_value=False),patch.object(state_registry,'get_ai_muted',return_value=False),patch.object(state_registry,'get_active_escalation_mode',return_value=None),patch.object(state_registry,'wa_get_booking_state',return_value={'flags':{}}),patch.object(icp_overrides,'fetch_overrides_fresh',return_value={}),patch.object(icp_overrides,'auto_reply_state',return_value=True),patch.object(icp_overrides,'whatsapp_inbox_state',return_value=True),patch.object(zernio_dm_client,'_provider_account_allowed',return_value=True):
            self.assertTrue(runtime_guard(self.t.scope()))
            with patch.object(state_registry,'get_ai_muted',return_value=True):self.assertFalse(runtime_guard(self.t.scope()))
            with patch.object(state_registry,'get_active_escalation_mode',return_value='hard'):self.assertFalse(runtime_guard(self.t.scope()))
            with patch.object(state_registry,'wa_get_booking_state',return_value={'flags':{'mermaid_reminders_opt_out':True}}):self.assertFalse(runtime_guard(self.t.scope()))
            with patch.object(icp_overrides,'auto_reply_state',return_value=None):self.assertFalse(runtime_guard(self.t.scope()))
            with self.store.db() as db,db:
                db.execute("CREATE TABLE inbound_processing_events(conversation_id TEXT,status TEXT)")
                db.execute("INSERT INTO inbound_processing_events VALUES(?, 'received')",(self.t.scope().conversation_id,))
            self.assertFalse(runtime_guard(self.t.scope()))
            with self.store.db() as db,db:db.execute("UPDATE inbound_processing_events SET status='processed'")
            self.assertTrue(runtime_guard(self.t.scope()))


    def test_scheduler_claims_one_due_reminder_and_enforces_window_age(self):
        rows=self.plan();self.t.now+=timedelta(hours=6)
        def post(*args):self.calls.append(args);return {'status':'accepted','provider_id':'scheduler-fixture'}
        self.assertEqual(run_once(store=self.store,post=post,guard=lambda s:True,window=lambda *a:{'open':True}),1)
        self.assertEqual(run_once(store=self.store,post=post,guard=lambda s:True,window=lambda *a:{'open':True}),0)
        self.t.now+=timedelta(hours=19);self.assertFalse(self.send(rows[1]['id']));self.assertEqual(len(self.calls),1)
    def test_operator_projection_exposes_failure_and_resumed_progress(self):
        self.t.initial()
        def failure():raise RuntimeError('Synthetic failure')
        self.t.turn(response('none'),before_response=failure)
        data=self.t.get('today').json();self.assertEqual(data['recovery']['incidents'][0]['status'],'operator_review')
        self.assertGreaterEqual(data['counts']['attention'],1)
        self.t.turn(response('update',[{'date':'2026-10-20'}]))
        self.assertEqual(self.t.get('today').json()['recovery']['incidents'][0]['status'],'progress_resumed_new_turn')
