"""Atomic admission/drain tests use only synthetic SQLite and mocked external work."""
import json
import asyncio
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch, MagicMock
from shared import mermaid_maintenance as gate, state_registry


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'state.db'
        self.raw={'slug':'mermaid','features':{'mermaid_cutover_coordination':True}}
        for target,name,value in [(gate.config_loader,'get_raw',lambda:self.raw),(state_registry,'DB_PATH',str(self.path))]:
            p=patch.object(target,name,value);p.start();self.addCleanup(p.stop)
        # Actual registry initialization/claim uses a synthetic tenant DB only.
        c=state_registry._get_conn();c.close();gate.initialize()

    def claim(self,mid):
        return state_registry.wa_claim_inbound_processing(mid,'fixture-guest','whatsapp',{'platform':'whatsapp','account_id':'fixture-account','text':'synthetic'})

    def close(self):
        return gate.close(0,seconds=120,coverage=gate.COVERAGE,evidence='synthetic-complete-inventory')

    def terminal(self,mid):
        # Preserve the terminal metadata produced by the real completion API.
        self.assertTrue(state_registry.inbound_processing_update(mid,'replied',reason='provider_send_ok'))

    def test_accepted_commit_survives_crash_and_duplicate(self):
        self.assertTrue(self.claim('one'))  # Simulated crash before ack: no background work runs.
        epoch=self.close()
        self.assertFalse(self.claim('one'))
        with self.assertRaises(gate.Closed):self.claim('new')
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM whatsapp_processed WHERE message_id='new'").fetchone()[0],0)
            self.assertIn('synthetic',db.execute("SELECT payload_json FROM inbound_processing_events WHERE message_id='one'").fetchone()[0])
        with gate.worker('inbound',['one']):
            with gate.worker('transport'):self.terminal('one')
        gate.seal(epoch)
        with self.assertRaises(gate.Closed):
            with gate.worker('recovery'):pass
        self.assertFalse(self.claim('one'))
        with self.assertRaises(gate.Closed):self.claim('new')

    def test_atomic_closure_acceptance_race(self):
        barrier=threading.Barrier(2);out=[]
        def accept():
            barrier.wait()
            try:out.append(self.claim('race'))
            except gate.Closed:out.append('closed')
        t=threading.Thread(target=accept);t.start();barrier.wait();epoch=self.close();t.join(5);self.assertFalse(t.is_alive())
        with sqlite3.connect(self.path) as db:
            count=db.execute("SELECT COUNT(*) FROM whatsapp_processed WHERE message_id='race'").fetchone()[0]
            pending=db.execute("SELECT COUNT(*) FROM mermaid_maintenance_pending WHERE message_id='race' AND generation=?",(epoch,)).fetchone()[0]
        self.assertEqual((count,pending),(1,1) if out==[True] else (0,0));self.assertIn(out,[[True],['closed']])

    def test_active_worker_blocks_seal_and_nested_completion(self):
        with gate.worker('scheduled'):
            epoch=self.close()
            with self.assertRaises(gate.Closed):gate.seal(epoch)
            with gate.worker('transport'):pass  # Original participating job can finish.
        gate.seal(epoch)
        gate.reopen(epoch)
        with self.assertRaises(gate.Closed):gate.reopen(epoch)
        self.assertTrue(self.claim('after'))

    def test_unknown_crash_and_ambiguous_send_remain_visible(self):
        with self.assertRaises(RuntimeError):
            with gate.worker('transport'):raise RuntimeError('synthetic ambiguous acceptance')
        epoch=self.close()
        self.assertEqual(gate.status()['workers'],{'operator_review':1})
        with self.assertRaises(gate.Closed):gate.seal(epoch)
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO mermaid_maintenance_workers VALUES('crash',0,'delivery','active',0,NULL,'')")
        self.assertEqual(gate.status()['workers'],{'active':1,'operator_review':1})
        self.assertFalse(gate.status()['ready'])

    def test_deadline_and_stale_generation(self):
        epoch=gate.close(0,seconds=1,coverage=gate.COVERAGE,evidence='fixture',now=0)
        with self.assertRaises(gate.Closed):gate.seal(epoch,now=2)
        with self.assertRaises(gate.Closed):
            with gate.worker('recovery'):pass
        with self.assertRaises(gate.Closed):gate.seal(epoch+1,now=.5)
        self.assertEqual(gate.status()['phase'],'draining')

    def test_incomplete_coverage_and_unknown_producer(self):
        with self.assertRaises(gate.Closed):gate.close(0,seconds=20,coverage={'inbound'},evidence='fixture')
        with self.assertRaises(gate.Closed):
            with gate.worker('not-covered'):pass
        self.assertEqual(gate.status()['phase'],'open')

    def test_pending_failed_and_unknown_ledger_prevent_false_ready(self):
        self.claim('one');epoch=self.close()
        with sqlite3.connect(self.path) as db:db.execute("UPDATE inbound_processing_events SET status='processing_failed' WHERE message_id='one'")
        with self.assertRaises(gate.Closed):gate.seal(epoch)
        with sqlite3.connect(self.path) as db:db.execute("UPDATE inbound_processing_events SET status='received' WHERE message_id='one'")
        self.terminal('one')
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TABLE isluno_unreviewed_sender(status TEXT)');db.execute("INSERT INTO isluno_unreviewed_sender VALUES('claimed')")
        self.assertIn('isluno_unreviewed_sender',gate.status()['unreviewed_ledgers'])
        with self.assertRaises(gate.Closed):gate.seal(epoch)

    def test_scheduled_and_outbound_roots_blocked_before_side_effect(self):
        calls=[]
        @gate.participating('scheduled')
        def job():calls.append('job')
        @gate.participating('transport')
        def send():calls.append('send')
        self.close()
        for fn in (job,send):
            with self.assertRaises(gate.Closed):fn()
        self.assertEqual(calls,[])

    def test_inactive_and_other_tenant_behavior(self):
        self.raw={'slug':'other','features':{'mermaid_cutover_coordination':True}}
        with gate.worker('unknown-role'):pass
        self.assertTrue(self.claim('other'))
        self.assertEqual(gate.status()['phase'],'not_applicable')
        self.raw={'slug':'mermaid','features':{}}
        with sqlite3.connect(self.path) as db:db.execute('DELETE FROM mermaid_maintenance')
        with gate.worker('transport'):pass
        self.assertTrue(self.claim('inactive'))
        self.assertEqual(gate.status()['phase'],'inactive')

    def test_flag_off_does_not_remove_existing_fence(self):
        self.close();self.raw['features']={}
        with self.assertRaises(gate.Closed):self.claim('cannot-bypass')

    def test_first_cutover_requires_sealed_when_gate_installed(self):
        with sqlite3.connect(self.path) as db:
            with self.assertRaises(gate.Closed):gate.require_sealed(db)
        epoch=self.close();gate.seal(epoch)
        with sqlite3.connect(self.path) as db:gate.require_sealed(db,epoch)

    def test_noncustomer_event_semantics(self):
        calls=[]
        @gate.participating('inbound',when=lambda event:event=='message.received')
        def callback(event):calls.append(event)
        self.close();callback('message.sent');callback('message.failed')
        self.assertEqual(calls,['message.sent','message.failed'])

    def test_actual_zernio_ack_boundary(self):
        from agents.social import webhook_server as web
        from starlette.background import BackgroundTasks
        from shared import tenant_guard
        class Request:
            headers={}
            async def body(self):return b'{"event":"message.received"}'
        msg={'message_id':'ack','conversation_id':'fixture-guest','channel':'whatsapp','platform':'whatsapp','account_id':'fixture-account'}
        with patch.object(web,'verify_webhook_signature',return_value=True),patch.object(web,'parse_zernio_webhook',return_value=msg),patch.object(tenant_guard,'account_access_state',return_value=True),patch.object(web.icp_overrides,'fetch_overrides',return_value={}),patch.object(web.icp_overrides,'whatsapp_inbox_state',return_value=True):
            first=BackgroundTasks();response=asyncio.run(web.receive_zernio_webhook(Request(),first))
            self.assertEqual(response.status_code,200);self.assertEqual(len(first.tasks),1)
            self.close()  # Crash after200/before the background task; never execute it here.
            duplicate=BackgroundTasks();response=asyncio.run(web.receive_zernio_webhook(Request(),duplicate))
            self.assertEqual(response.status_code,200);self.assertEqual(duplicate.tasks,[])
            msg['message_id']='rejected';pending=BackgroundTasks();response=asyncio.run(web.receive_zernio_webhook(Request(),pending))
            self.assertEqual(response.status_code,503);self.assertEqual(pending.tasks,[])
        with sqlite3.connect(self.path) as db:
            self.assertIsNone(db.execute("SELECT 1 FROM whatsapp_processed WHERE message_id='rejected'").fetchone())

    def test_returned_send_with_ambiguous_business_ledger_blocks_seal(self):
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS mermaid_reservation_emails(status TEXT)')
        with gate.worker('delivery'):
            epoch=self.close()
            with gate.worker('transport'):
                with sqlite3.connect(self.path) as db:db.execute("INSERT INTO mermaid_reservation_emails(status) VALUES('uncertain')")
        self.assertEqual(gate.status()['workers'],{})
        self.assertEqual(gate.status()['pending']['mermaid_reservation_emails'],1)
        with self.assertRaises(gate.Closed):gate.seal(epoch)

    def test_real_email_transport_denied_before_credentials_or_smtp(self):
        from agents.social import mermaid_email_transport as email
        self.close()
        with patch.object(email,'_password',side_effect=AssertionError('credential read forbidden')):
            with self.assertRaises(gate.Closed):email.send_email('fixture@example.invalid',{},('fixture.pdf',b''),message_id='fixture')

    def test_sealed_generation_and_deadline_rechecked_at_activation(self):
        epoch=self.close();gate.seal(epoch)
        with sqlite3.connect(self.path) as db:
            with self.assertRaises(gate.Closed):gate.require_sealed(db,epoch+1)
            with patch.object(gate.time,'time',return_value=time.time()+200):
                with self.assertRaises(gate.Closed):gate.require_sealed(db,epoch)
            gate.require_sealed(db,epoch)

    def test_actual_unconfirmed_delivery_transition_blocks_seal(self):
        from shared import tenant_guard
        self.claim('failed')
        batch=state_registry.inbound_processing_join_batch('failed')
        token=state_registry.inbound_processing_begin_batch(['failed'],batch_id=batch)
        self.assertTrue(token);epoch=self.close()
        with patch.object(tenant_guard,'account_access_state',return_value=True):
            notice=state_registry.inbound_processing_commit_delivery_failure(['failed'],batch,token,
                account_id='fixture-account',notification={'channel':'whatsapp','customer_id':'fixture-guest','customer_name':'Fixture',
                'subject':'Synthetic unconfirmed delivery','body':'Synthetic operator review required'})
        self.assertIsNotNone(notice)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("SELECT status,reason,last_error FROM inbound_processing_events WHERE message_id='failed'").fetchone(),
                             ('send_failed','provider_send_failed','provider_delivery_unconfirmed'))
        self.assertEqual(gate.status()['pending']['inbound_processing_events'],1)
        with self.assertRaises(gate.Closed):gate.seal(epoch)

    def test_missing_unknown_and_superseded_snapshot_members_block(self):
        for mid in ('missing','unknown','superseded','bare-replied'):self.claim(mid)
        epoch=self.close()
        with sqlite3.connect(self.path) as db:
            db.execute("DELETE FROM inbound_processing_events WHERE message_id='missing'")
            db.execute("UPDATE inbound_processing_events SET status='future_status' WHERE message_id='unknown'")
            db.execute("UPDATE inbound_processing_events SET status='superseded',reason='newer_outbound_exists' WHERE message_id='superseded'")
            db.execute("UPDATE inbound_processing_events SET status='replied',reason='unreviewed_reason' WHERE message_id='bare-replied'")
        summary=gate.status()
        self.assertEqual(summary['pending']['inbound_processing_events'],4)
        self.assertEqual(summary['inbound_dispositions']['missing_row'],1)
        self.assertEqual(summary['inbound_dispositions']['superseded_requires_review'],1)
        with self.assertRaises(gate.Closed):gate.seal(epoch)

    def test_reviewed_completed_and_owner_dispositions_can_seal(self):
        for mid in ('reply','ignore','handoff'):self.claim(mid)
        epoch=self.close();self.terminal('reply')
        self.assertTrue(state_registry.inbound_processing_update('ignore','ignored',reason='ignored_contact'))
        self.assertTrue(state_registry.inbound_processing_update('handoff','escalated',reason='human_takeover_ai_muted'))
        self.assertEqual(gate.status()['inbound_dispositions'],{'completed_or_disposed':3})
        gate.seal(epoch)

    def test_sdk_text_denied_before_credentials_or_client_in_draining_and_sealed(self):
        from agents.social import zernio_dm_client as client
        epoch=self.close()
        for phase in ('draining','sealed'):
            if phase=='sealed':gate.seal(epoch)
            with patch.object(client.os.environ,'get',side_effect=AssertionError('credential lookup forbidden')),patch.object(client,'_get_client') as factory:
                with self.assertRaises(gate.Closed):client.send_dm_reply('fixture','fixture-account','synthetic')
                factory.assert_not_called()

    def test_actual_sdk_text_open_nested_default_off_and_other_tenant(self):
        from agents.social import zernio_dm_client as client
        sdk=MagicMock()
        with patch.dict(client.os.environ,{'LATE_API_KEY':'offline-dummy-key'}),patch.object(client,'_get_client',return_value=sdk),patch.object(client,'_provider_mutation_account_allowed',return_value=True) as account:
            self.assertTrue(client.send_dm_reply('fixture','fixture-account','open'))
            with gate.worker('inbound'):
                epoch=self.close()
                self.assertTrue(client.send_dm_reply('fixture','fixture-account','nested original work'))
            gate.seal(epoch);gate.reopen(epoch)
            self.raw={'slug':'other','features':{}}
            self.assertTrue(client.send_dm_reply('fixture','fixture-account','other'))
            self.raw={'slug':'mermaid','features':{}}
            with sqlite3.connect(self.path) as db:db.execute('DELETE FROM mermaid_maintenance')
            self.assertTrue(client.send_dm_reply('fixture','fixture-account','inactive'))
            self.assertEqual(sdk.inbox.send_inbox_message.call_count,4)
            self.assertEqual(account.call_count,4)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM mermaid_maintenance_workers WHERE role='transport' AND status='finished'").fetchone()[0],2)

    def test_actual_supersession_is_not_causal_resolution(self):
        self.claim('superseded');epoch=self.close()
        state_registry.wa_store_message('fixture-guest','operator','Synthetic unrelated newer note')
        self.assertEqual(state_registry.inbound_processing_claim_recoverable(max_age_seconds=0),[])
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("SELECT status,reason FROM inbound_processing_events WHERE message_id='superseded'").fetchone(),
                             ('superseded','newer_outbound_exists'))
        self.assertEqual(gate.status()['inbound_dispositions'],{'superseded_requires_review':1})
        with self.assertRaises(gate.Closed):gate.seal(epoch)

    def test_caught_sdk_exception_retains_unknown_outbound_claim(self):
        from agents.social import zernio_dm_client as client
        sdk=MagicMock();sdk.inbox.send_inbox_message.side_effect=RuntimeError('synthetic response lost')
        with patch.dict(client.os.environ,{'LATE_API_KEY':'offline-dummy-key'}),patch.object(client,'_get_client',return_value=sdk),patch.object(client,'_provider_mutation_account_allowed',return_value=True):
            self.assertFalse(client.send_dm_reply('fixture','fixture-account','uncertain'))
        epoch=self.close();self.assertEqual(gate.status()['workers'],{'operator_review':1})
        self.assertEqual(gate.status()['incidents'][0]['disposition'],'provider_outcome_unknown')
        with self.assertRaises(gate.Closed):gate.seal(epoch)
