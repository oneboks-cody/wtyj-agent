"""Disposable legacy cutover and recovery records for operator UI verification."""
import json
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

def before(fixture):
    from shared import state_registry
    from agents.social import mermaid_reservation_store as legacy,mermaid_documents as documents,mermaid_abandoned_reminders as reminders
    fixture.enterContext(patch.object(state_registry,'DB_PATH',str(fixture.itinerary.db_path)))
    at=fixture.now.isoformat()
    with legacy._conn() as db:
        db.execute("INSERT INTO mermaid_reservations(public_id,tenant_slug,conversation_id,zernio_account_id,summary_version,customer_name,language,intake_json,catalog_version,monetary_snapshot_json,state,availability_source,booking_code,created_at,updated_at) VALUES('legacy-fixture','mermaid','legacy-fixture-guest','synthetic-account','legacy-fixture-version','Synthetic Legacy','en','{}','legacy-fixture','{}','quote_sent','demo_assumed','LEGACY-FIXTURE',?,?)",(at,at))
        db.execute("INSERT INTO mermaid_checkout_links VALUES('legacy-fixture-token','mermaid','legacy-fixture',9999999999,1)")
    with documents._conn() as db:
        db.execute("INSERT INTO mermaid_delivery_jobs VALUES('legacy-fixture-job','mermaid','legacy-fixture','legacy-fixture-document','legacy-fixture-guest','quote','pending','legacy-fixture-key',1,'unknown',?,?)",(at,at))
    with reminders.connection(fixture.now) as db:
        db.execute("INSERT INTO mermaid_abandoned_reminders VALUES('legacy-fixture-reminder','mermaid','legacy-fixture-guest',1,6,'sending',?,?)",(at,at))

def after(fixture):
    from shared import isluno_config
    from agents.social.isluno_recovery import RecoveryStore
    from agents.social.isluno_delivery import send_plan
    from test_conversation import response
    source=Path(__file__).resolve().parents[2]/'clients/mermaid/config/isluno_recovery.json'
    policy=json.loads(source.read_text());policy['enabled']=True
    target=fixture.directory/'isluno_recovery.json';target.write_text(json.dumps(policy))
    fixture.scope=lambda:isluno_config.verified_scope(account_id='synthetic-account',conversation_id='fixture-recovery',customer_ref='fixture-recovery-guest')
    result=fixture.initial()
    send_plan(fixture.scope().conversation_id,fixture.scope().account_id,result['media']['url'],store=fixture.discovery,post=lambda *a:{'status':'accepted','provider_id':'synthetic-original'},window=lambda *a:{'open':True})
    recovery=RecoveryStore(fixture.store)
    with recovery.db() as db:identifier=db.execute('SELECT id FROM isluno_reminders ORDER BY due_at LIMIT 1').fetchone()[0]
    fixture.now+=timedelta(hours=6)
    recovery.dispatch(identifier,post=lambda *a:{'status':'ambiguous'},guard=lambda scope:True,window=lambda *a:{'open':True})
    def failed_model():raise RuntimeError('Synthetic model outage')
    fixture.turn(response('update',[{'date':'2026-10-20'}]),before_response=failed_model)
    policy['enabled']=False;target.write_text(json.dumps(policy))
