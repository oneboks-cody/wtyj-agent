"""Quote review is a separate customer decision; all networking is denied."""
import socket
from pathlib import Path
from unittest.mock import Mock
import pytest
from agents.marina import marina_agent
from agents.social import mermaid_reservation_workflow as workflow
from agents.social import mermaid_documents as docs, mermaid_demo_payment as payment, mermaid_reservation_store as store
from shared import config_loader, state_registry

@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(socket.socket, 'connect', lambda *a, **k: pytest.fail('Network prohibited'))
    monkeypatch.setattr(config_loader, '_CONFIG_PATH', str(Path(__file__).resolve().parents[3]/'clients/mermaid/config/client.json'))
    monkeypatch.setattr(config_loader, '_cache', {})
    monkeypatch.setattr(state_registry, 'DB_PATH', str(tmp_path/'state.db'))
    monkeypatch.setattr(state_registry, '_alert_dispatcher', None)
    monkeypatch.setattr(state_registry, '_summary_dispatcher', None)
    monkeypatch.setenv('MERMAID_DEMO_SIGNING_SECRET', 'fixture-only')
    monkeypatch.setenv('UNBOKS_PUBLIC_BASE_URL', 'https://example.test')

def fields():
    return dict(trip_date='2027-09-08', adults=2, children=0, infants=0,
                customer_name='Fixture Guest',contact_phone='+12025550123',pickup_preference='pier',
                language='nl',phase='awaiting_summary_confirmation')

def decision(action='confirm_summary', **extra):
    return dict(language='nl',mermaid_action=action,fields={},reply='Antwoord',
                has_open_question=False,requires_human=False,confidence='high')|extra

def test_quote_first_then_explicit_approval(monkeypatch):
    state_registry.wa_save_booking_state('guest',{'mermaid_intake':fields()}, {})
    model=Mock(return_value=decision())
    monkeypatch.setattr(marina_agent,'process_message',model)
    monkeypatch.setattr(docs,'create_quote',lambda r: ({'public_id':'fixture-doc','filename':'quote.pdf'},{'public_id':'fixture-job'}))
    reply=workflow.handle_demo_message({'from':'guest','message_id':'details-ok','text':'Klopt'},True,use_model=True)
    r=store.latest_for_conversation('guest')
    assert r['state']=='quote_ready'
    assert reply['media']['filename']=='quote.pdf'
    assert '/pay/' not in reply['text'] and 'USD' not in reply['text']
    with pytest.raises(ValueError):payment.build_payment_url('https://example.test',r['public_id'],'fixture-only')
    model.return_value=decision('payment_status',status_request='payment',has_open_question=True)
    reply=workflow.handle_demo_message({'from':'guest','message_id':'payment-question','text':'Hoe betaal ik?'},True,use_model=True)
    assert '/pay/' not in reply['text'] and store.get_reservation(r['public_id'])['state']=='quote_ready'
    model.return_value=decision()
    original=payment.build_payment_url
    failures=[]
    def interrupted(*args, **kwargs):
        if not failures:
            failures.append(True)
            raise RuntimeError('fixture interruption after approval')
        return original(*args, **kwargs)
    monkeypatch.setattr(payment,'build_payment_url',interrupted)
    with pytest.raises(RuntimeError, match='fixture interruption'):
        workflow.handle_demo_message({'from':'guest','message_id':'quote-ok','text':'De offerte klopt'},True,use_model=True)
    reply=workflow.handle_demo_message({'from':'guest','message_id':'quote-ok','text':'De offerte klopt'},True,use_model=True)
    assert '/pay/' in reply['text'] and not reply['media']
    assert store.get_reservation(r['public_id'])['state']=='demo_payment_pending'
    again=workflow.handle_demo_message({'from':'guest','message_id':'quote-ok','text':'De offerte klopt'},True,use_model=True)
    assert again['text']==reply['text']

@pytest.mark.parametrize('language',['en','nl','de','es','pt','pap'])
def test_quote_card_is_only_review_instruction(language):
    text=docs.quote_message({'language':language})
    assert 'Open PDF' in text and '/pay/' not in text and 'USD' not in text
