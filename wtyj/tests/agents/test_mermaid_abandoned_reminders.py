"""Offline clock and delivery tests; all sends are mocked."""
import json,socket
from datetime import datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import Mock
import pytest
from shared import state_registry,config_loader,tenant_guard
from agents.social import mermaid_abandoned_reminders as reminders,mermaid_reservation_store as store,zernio_dm_client as provider,senders

BASE=datetime(2027,1,1,10,tzinfo=timezone.utc)
@pytest.fixture
def setup(tmp_path,monkeypatch):
 monkeypatch.setattr(socket.socket,'connect',lambda *a,**k: pytest.fail('Network prohibited'))
 monkeypatch.setattr(config_loader,'_CONFIG_PATH',str(Path(__file__).resolve().parents[3]/'clients/mermaid/config/client.json'))
 monkeypatch.setattr(config_loader,'_cache',{})
 monkeypatch.setattr(state_registry,'DB_PATH',str(tmp_path/'state.db'))
 monkeypatch.setattr(state_registry,'get_ai_muted',lambda p:False)
 monkeypatch.setattr(state_registry,'get_active_escalation_mode',lambda p:None)
 monkeypatch.setattr(tenant_guard,'is_account_allowed',lambda *a,**k:True)
 state_registry._get_conn().close();store._conn().close()
 state_registry.wa_save_booking_state('guest',{'mermaid_intake':{'adults':2,'language':'nl','phase':'collecting'}},{})
 reminders.connection(BASE-timedelta(minutes=1)).close()
 incoming(BASE,'one')
 send=Mock(return_value=True);monkeypatch.setattr(senders,'send_reply',send)
 monkeypatch.setattr(provider,'whatsapp_customer_service_window',Mock(return_value={'open':True}))
 return send

def incoming(at,key):
 c=reminders.connection(at)
 with c:
  c.execute("INSERT INTO whatsapp_threads (phone,role,text,created_at,channel,source_message_key) VALUES ('guest','user','fixture',?,'whatsapp',?)",(at.isoformat(),key))
  c.execute("INSERT INTO whatsapp_threads (phone,role,text,created_at,channel,source_message_key) VALUES ('guest','assistant','fixture',?,'whatsapp',?)",((at+timedelta(seconds=1)).isoformat(),key+'reply'))
  c.execute("INSERT INTO inbound_processing_events (message_id,conversation_id,channel,status,created_at,updated_at,payload_json) VALUES (?,'guest','whatsapp','completed',?,?,?)",(key,at.isoformat(),at.isoformat(),json.dumps({'account_id':'account','sent_at':at.isoformat()})))
 c.close()

def test_two_milestones_no_duplicates(setup):
 assert reminders.run_once(BASE+timedelta(hours=5,minutes=59))==0
 assert reminders.run_once(BASE+timedelta(hours=6))==1
 assert reminders.run_once(BASE+timedelta(hours=7))==0
 assert reminders.run_once(BASE+timedelta(hours=18))==1
 assert reminders.run_once(BASE+timedelta(hours=19))==0
 assert setup.call_count==2
 assert 'laatste herinnering' in setup.call_args.args[3]

def test_new_customer_message_restarts_clock(setup):
 reminders.run_once(BASE+timedelta(hours=6))
 incoming(BASE+timedelta(hours=7),'two')
 assert reminders.run_once(BASE+timedelta(hours=12))==0
 assert reminders.run_once(BASE+timedelta(hours=13))==1
 assert setup.call_count==2

@pytest.mark.parametrize('reason',['optout','booked','window','tenant'])
def test_suppression(setup,monkeypatch,reason):
 if reason=='optout':state_registry.wa_save_booking_state('guest',{'mermaid_intake':{'adults':2,'phase':'collecting'}},{'mermaid_reminders_opt_out':True})
 if reason=='booked':state_registry.wa_save_booking_state('guest',{'mermaid_intake':{'adults':2,'phase':'booked'}},{})
 if reason=='window':monkeypatch.setattr(provider,'whatsapp_customer_service_window',lambda *a:{'open':False})
 if reason=='tenant':monkeypatch.setattr(tenant_guard,'is_account_allowed',lambda *a,**k:False)
 reminders.run_once(BASE+timedelta(hours=6));assert not setup.called

def test_downtime_does_not_send_two_at_once(setup):
 reminders.run_once(BASE+timedelta(hours=19));reminders.run_once(BASE+timedelta(hours=20))
 assert setup.call_count==1

def test_expired_window_and_no_failure_retry(setup):
 assert reminders.run_once(BASE+timedelta(hours=24))==0
 setup.return_value=False
 reminders.run_once(BASE+timedelta(hours=6));reminders.run_once(BASE+timedelta(hours=7))
 assert setup.call_count==1
