"""Offline checks for issued quote buttons and their scoped decisions."""
import json
from unittest.mock import Mock
import pytest
from test_mermaid_quote_review import isolated,fields,decision
from agents.social import mermaid_document_cards as cards,mermaid_reservation_workflow as flow,mermaid_reservation_store as store
from shared import state_registry,mermaid_catalog


def quote(monkeypatch):
    r=store.confirm_reservation('guest',fields()|{'phase':'summary_confirmed'},idempotency_key='fixture',zernio_account_id='account')
    r=store.transition(r['public_id'],'quote_ready',idempotency_key='ready',actor='system',reason='fixture',updates={'quote_public_id':'doc'})
    prefix=f"mermaid-quote:doc:{r['revision']}:"
    monkeypatch.setattr(cards,'records',lambda *a:[{'status':'delivered','provider_message_id':'msg','payload_json':json.dumps({'buttons':[{'payload':prefix+'accept'},{'payload':prefix+'change'}]})}])
    state_registry.wa_save_booking_state('guest',{'mermaid_intake':fields()}, {})
    return r,prefix

def test_scoped_buttons(monkeypatch):
    r,p=quote(monkeypatch)
    msg={'from':'guest','_zernio_account_id':'account','_zernio_interactive_id':p+'accept'}
    assert cards.quote_button_choice(msg,r)=='accept'
    assert cards.quote_button_choice(msg|{'from':'other'},r)=='stale'
    assert cards.quote_button_choice(msg,r|{'revision':r['revision']+1})=='stale'
    assert cards.quote_button_choice(msg,r|{'state':'cancelled'})=='stale'

def test_change_prevents_payment(monkeypatch):
    r,p=quote(monkeypatch)
    base={'from':'guest','_zernio_account_id':'account'}
    reply=flow.handle_demo_message(base|{'message_id':'change','_zernio_interactive_id':p+'change'},True,use_model=True)
    assert 'wijzigen' in reply['text']
    reply=flow.handle_demo_message(base|{'message_id':'accept-after-change','_zernio_interactive_id':p+'accept'},True,use_model=True)
    assert '/pay/' not in reply['text'] and store.get_reservation(r['public_id'])['state']=='quote_ready'

def test_accept_sends_payment(monkeypatch):
    r,p=quote(monkeypatch)
    reply=flow.handle_demo_message({'from':'guest','_zernio_account_id':'account','message_id':'accept','_zernio_interactive_id':p+'accept'},True,use_model=True)
    assert '/pay/' in reply['text'] and store.get_reservation(r['public_id'])['state']=='demo_payment_pending'

def test_labels_fit():
    for l,c in mermaid_catalog.get_catalog()['guest_copy'].items():
        assert all(len(c['quote_buttons'][k])<=20 for k in ['open','accept','change'])
