"""Integrated normalized inbound → native actions → sender → operator → rollback.

Only catalog/legacy history is seeded. New booking stages and acceptance ledgers
are written exclusively by application callers. All external boundaries are fake.
"""
import copy
import hashlib
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import test_operations as operations
from test_conversation import response, NOW
from shared import isluno_config, state_registry
from agents.social import isluno_delivery, isluno_quote_delivery, isluno_fulfillment, zernio_dm_client
from agents.social import mermaid_email_transport, mermaid_date_changes, mermaid_reservation_email
from agents.social import isluno_transition
from agents.social.senders import send_reply
from scripts.isluno_recovery_fixture import before as seed_legacy


class InlineThread:
    def __init__(self, target, args=(), kwargs=None, **unused):self.target=target;self.args=args;self.kwargs=kwargs or {}
    def start(self):self.target(*self.args,**self.kwargs)


class IntegratedTests(unittest.TestCase):
    def setUp(self):
        self.t=operations.OperationsTests();self.t.setUp();self.addCleanup(self.t.doCleanups)
        catalog=json.loads(self.t.catalog_path.read_text());product=catalog['products'][0]
        keys=('guest_rules','price_rules','schedule','options','pickup','policies')
        product['demo_rules']={'authority':'user_approved_demo_sample','version':'integrated-synthetic-v1','real_booking_eligible':False,'label':'DEMO SAMPLE — not supplier confirmed','approval_ref':'Synthetic verification fixture','rules':{key:copy.deepcopy(product[key]) for key in keys}}
        product['price_rules']=None;product['schedule']=None
        self.t.catalog_path.write_text(json.dumps(catalog))
        seed_legacy(self.t)
        with mermaid_date_changes._conn() as db:
            db.execute("INSERT INTO mermaid_date_changes(token,reservation_public_id,conversation_id,account_id,source_message_id,old_date,new_date,expected_revision,locale,status,expires_at,created_at) VALUES('legacy-date','legacy-fixture','legacy-fixture-guest','synthetic-account','legacy-date-turn','2026-10-15','2026-10-16',1,'en','pending',9999999999,?)",(NOW.isoformat(),))
        with mermaid_reservation_email._conn() as db:
            db.execute("INSERT INTO mermaid_reservation_emails(public_id,reservation_public_id,conversation_id,source_message_id,recipient,revision,document_public_id,status,message_id,created_at,updated_at) VALUES('legacy-email','legacy-fixture','legacy-fixture-guest','legacy-email-turn','legacy@example.invalid',1,'legacy-doc','sending','legacy-email-message',?,?)",(NOW.isoformat(),NOW.isoformat()))
        self.legacy_tables=['mermaid_reservations','mermaid_checkout_links','mermaid_delivery_jobs','mermaid_abandoned_reminders','mermaid_date_changes','mermaid_reservation_emails']
        self.legacy_before=self.digests(self.legacy_tables)
        self.transport=[];self.email=[];self.events=[];self.states=[]
        for module,name,value in [
            (isluno_delivery,'DiscoveryStore',lambda *a,**k:self.t.discovery),
            (isluno_quote_delivery,'QuoteStore',lambda *a,**k:self.t.quotes),
            (isluno_fulfillment,'PaymentStore',lambda *a,**k:self.t.payments),
            (isluno_delivery,'post_once',self.post),
            (isluno_quote_delivery,'post_once',self.post),
            (zernio_dm_client,'whatsapp_customer_service_window',lambda *a:{'open':True}),
            (isluno_fulfillment,'email_guard',lambda scope:True),
            (mermaid_email_transport,'send_email',self.email_post),
            (isluno_fulfillment,'threading',SimpleNamespace(Thread=InlineThread)),
        ]:self.enterContext(patch.object(module,name,value))
        self.enterContext(patch.object(isluno_delivery.time,'sleep',side_effect=self.t.sleep))
        self.documents={};self.evidence={}
    def post(self,scope,body,key,**kwargs):
        if kwargs.get('guard'):self.assertTrue(kwargs['guard']())
        self.assertLess(len(self.transport),100,'Bounded synthetic dispatch ceiling')
        status=self.states.pop(0) if self.states else 'accepted'
        self.transport.append({'scope':scope.key,'key':key,'body':body,'status':status})
        return {'status':status,'provider_id':'synthetic-'+str(len(self.transport)) if status=='accepted' else None}
    def email_post(self,recipient,content,attachment,**kwargs):
        self.assertLess(len(self.email),4);self.assertTrue(recipient.endswith('@example.invalid'))
        self.email.append({'recipient':recipient,'message_id':kwargs['message_id'],'attachment_sha256':hashlib.sha256(attachment[1]).hexdigest()})
    def digests(self,tables):
        with self.t.store.db() as db:
            return {table:{'rows':len(rows),'sha256':hashlib.sha256(json.dumps(rows,sort_keys=True,default=str).encode()).hexdigest()} for table in tables for rows in [[dict(r) for r in db.execute('SELECT * FROM '+table+' ORDER BY rowid')]]}
    def send(self,result):
        media=result['media']
        return send_reply('whatsapp',self.t.scope().conversation_id,self.t.scope().account_id,result['text'],media['url'],media['type'])
    def turn(self,decision=None,token=None,trigger=None):
        result,calls=self.t.turn(decision,token=token,trigger=trigger)
        self.events.append({'scope':self.t.scope().key,'trigger':trigger or 'turn-'+str(self.t.serial),'model_fixture_calls':calls,'result_type':result['media']['type'] if isinstance(result,dict) else 'suppressed'})
        return result
    def test_new_journeys_and_ledgers_survive_cutover_rollback_without_replay(self):
        saved_jobs=[];details=[]
        for count in (1,2):
            self.t.scope=lambda count=count:isluno_config.verified_scope(account_id='synthetic-account',conversation_id='integrated-'+str(count),customer_ref='integrated-guest-'+str(count))
            self.t.now=NOW
            for index in range(count):
                result=self.turn(response('add',[{'product_id':'fixture-cruise','date':f'2026-10-{15+index}'}],{'name':'Synthetic Guest','ages':[35,34,8]}))
                self.assertTrue(self.send(result))
            result=self.turn(response('summary'));self.assertTrue(self.send(result));summary=self.t.job(result)
            result=self.turn(token=self.t.token(summary));self.assertTrue(self.send(result));quote=self.t.job(result)
            result=self.turn(token=self.t.token(quote));self.assertTrue(self.send(result));approved=self.t.job(result)
            result=self.turn(token=self.t.token(approved),trigger='native-pay-'+str(count))
            job=self.t.payments.job(result['media']['url'],self.t.scope().account_id,self.t.scope().conversation_id)
            self.assertEqual(self.t.active()['status'],'demo_paid');self.assertEqual(len(self.t.active()['items']),count)
            saved_jobs.append((self.t.scope(),result))
            if count==2:self.states=['accepted','ambiguous']
            self.assertEqual(self.send(result),count==1)
            sends=len(self.transport)
            replay=self.turn(token=self.t.token(approved),trigger='native-pay-'+str(count))
            self.assertEqual(replay,result);self.send(replay);self.assertEqual(len(self.transport),sends)
            decision=response('email');decision['booking']['email_address']='guest@example.invalid'
            proposal=self.turn(decision);self.assertTrue(self.send(proposal));self.assertEqual(len(self.email),count-1)
            email_job=self.t.payments.job(proposal['media']['url'],self.t.scope().account_id,self.t.scope().conversation_id)
            consent=self.turn(token=self.t.token(email_job),trigger='native-consent-'+str(count));self.assertTrue(self.send(consent))
            self.assertEqual(len(self.email),count);self.send(consent);self.assertEqual(len(self.email),count)
            identifier=self.t.scope().key+'.'+self.t.active()['id']
            detail=self.t.get('journeys/'+identifier);self.assertEqual(detail.status_code,200);detail=detail.json()
            self.assertFalse(detail['real_money_charged']);self.assertFalse(detail['supplier_booking_made'])
            self.assertEqual(sum(d['kind']=='ticket' for d in detail['documents']),count)
            for document in detail['documents']:
                pdf=self.t.get('journeys/'+identifier+'/documents/'+document['id']+'.pdf');self.assertEqual(pdf.status_code,200);self.assertTrue(pdf.content.startswith(b'%PDF'))
                self.documents[str(count)+'-'+document['id']+'.pdf']=pdf.content
            details.append(detail)
        from agents.social.isluno_recovery import RecoveryStore,run_once
        recovery=RecoveryStore(self.t.store)
        # Actual failure on a new scoped draft creates a durable recovery record.
        self.t.scope=lambda:isluno_config.verified_scope(account_id='synthetic-account',conversation_id='integrated-failure',customer_ref='integrated-failure')
        self.turn(response('add',[{'product_id':'fixture-cruise','date':'2026-10-18'}],{'name':'Recovery Guest','ages':[35]}))
        def failed():raise RuntimeError('Synthetic understanding outage')
        result,calls=self.t.turn(response('update',[{'date':'2026-10-19'}]),before_response=failed)
        self.assertEqual((result,calls),('',1))
        self.assertTrue(self.t.get('today').json()['recovery']['incidents'])
        with self.t.store.db() as db:
            tables=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'isluno_%' ORDER BY name")]
        before=self.digests(tables);self.assertGreater(before['isluno_payments']['rows'],0);self.assertGreater(before['isluno_email_consents']['rows'],0)
        audit=isluno_transition.audit(self.t.itinerary.db_path)
        self.assertTrue(set(self.legacy_tables).issubset({r['source'] for r in audit['quarantined']}))
        configuration=copy.deepcopy(self.t.config);disabled=copy.deepcopy(configuration);disabled['features']={};self.t.write_config(disabled)
        sends=len(self.transport);emails=len(self.email)
        self.assertEqual(run_once(store=recovery),0);self.assertTrue(isluno_transition.blocked())
        self.assertFalse(send_reply('whatsapp','legacy-fixture-guest','synthetic-account','Legacy action'))
        self.assertEqual(self.digests(tables),before);self.assertEqual(self.digests(self.legacy_tables),self.legacy_before)
        self.t.write_config(configuration)
        for scope,result in saved_jobs:
            self.t.scope=lambda scope=scope:scope
            self.send(result)
        self.assertEqual(len(self.transport),sends);self.assertEqual(len(self.email),emails)
        self.assertEqual(self.digests(tables),before);self.assertEqual(self.digests(self.legacy_tables),self.legacy_before)
        self.evidence={'fixture_only':True,'one_and_multi_trip':True,'real_money_charged':False,'supplier_booking_made':False,'native_action_model_calls':sum(e['model_fixture_calls'] for e in self.events if e['trigger'].startswith(('native-pay','native-consent'))),'external_calls':0,'synthetic_transport_calls':len(self.transport),'synthetic_email_calls':len(self.email),'legacy_preserved':self.legacy_before,'post_cutover_preserved':before,'quarantine':audit,'events':self.events,'operator_details':details,'transport':self.transport,'email':self.email,'rollback_reactivation_extra_sends':0}
