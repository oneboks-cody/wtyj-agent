"""Focused offline preference and selector regressions; no live provider calls."""
import json
from pathlib import Path
from unittest.mock import Mock
import pytest
from shared import config_loader, state_registry, mermaid_customers
from agents.marina import marina_agent
from agents.social import mermaid_document_language as language
from agents.social import mermaid_reservation_workflow as workflow

@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config_loader, '_CONFIG_PATH', str(Path(__file__).resolve().parents[3] / 'clients/mermaid/config/client.json'))
    monkeypatch.setattr(config_loader, '_cache', {})
    monkeypatch.setattr(state_registry, 'DB_PATH', str(tmp_path/'state.db'))
    monkeypatch.setattr(state_registry, '_alert_dispatcher', None)
    import socket
    monkeypatch.setattr(socket.socket, 'connect', lambda *a, **k: pytest.fail('Network forbidden'))
    monkeypatch.setattr(socket, 'create_connection', lambda *a, **k: pytest.fail('Network forbidden'))
    monkeypatch.setattr(marina_agent, 'process_message', Mock(side_effect=AssertionError('Model must be explicitly mocked')))

def answer(**kw):
    result=dict(language='en',chat_language='sv',mermaid_action='details',fields={},reply='Tack!',confidence='high',requires_human=False,has_open_question=False,security_event='none',calendar_request='none',status_request='none',automation_loop=False)
    result.update(kw)
    return result

def message(mid='one', text='Jag talar svenska', **kw):
    return dict({'from':'synthetic-language-guest','text':text,'message_id':mid,'_zernio_account_id':'synthetic-account'},**kw)

def saved():
    return state_registry.wa_get_booking_state('synthetic-language-guest')

def test_foreign_language_offer_keeps_details_and_blocks_confirmation(monkeypatch):
    fields=dict(trip_date='2099-09-19',adults=2,children=0,infants=0,customer_name='Sample Guest',contact_phone='+46123456789',pickup_preference='pier')
    model=Mock(return_value=answer(fields=fields,document_language_prompt='Jag kan hjälpa er på svenska. Vilket språk vill ni ha dokumenten på?',document_language_button='Välj språk'))
    monkeypatch.setattr(marina_agent,'process_message',model)
    result=workflow.handle_demo_message(message(),include_media=True,use_model=True)
    assert result['media']['type']==language.MEDIA_TYPE
    data=saved()['fields']['mermaid_intake']
    assert data['customer_name']=='Sample Guest' and data['chat_language']=='sv'
    assert data['phase']=='collecting' and not data.get('document_language')
    assert model.call_count==1
    model.return_value=answer(mermaid_action='confirm_summary',document_language_prompt='Vilket språk vill ni ha dokumenten på?',document_language_button='Välj språk')
    result=workflow.handle_demo_message(message('two','Ja'),include_media=True,use_model=True)
    assert not str(result.get('mermaid_action')).startswith('reservation:')
    assert result['media']['type']==language.MEDIA_TYPE
    assert model.call_count==2

@pytest.mark.parametrize('choice',['en','nl','de','es','pt','pap'])
def test_bound_choice_preserves_chat_and_profile(choice,monkeypatch):
    fields={'language':'en','chat_language':'sv','phase':'collecting'}
    flags={language.FLAG:{'token':'token','account':'synthetic-account','text':'Välj språk','button':'Språk'}}
    state_registry.wa_save_booking_state('synthetic-language-guest',{'mermaid_intake':fields},flags)
    model=Mock(return_value=answer(reply='Vilket datum vill ni åka?'))
    monkeypatch.setattr(marina_agent,'process_message',model)
    result=workflow.handle_demo_message(message(text=language.options()[choice],_zernio_interactive_id=language.PREFIX+'token:'+choice),include_media=True,use_model=True)
    data=saved()['fields']['mermaid_intake']
    assert data['document_language']==choice and data['language']==choice and data['chat_language']=='sv'
    assert language.FLAG not in saved()['flags'] and result['media'] is None
    assert model.call_args.kwargs['thread_fields']['verified_document_language_choice']==choice
    cid=mermaid_customers.account_id('synthetic-language-guest')
    account=mermaid_customers.get_account(cid)
    assert account is not None
    with state_registry._get_conn() as conn:
        latest=json.loads(conn.execute('SELECT intake_json FROM mermaid_customer_intakes ORDER BY id DESC LIMIT 1').fetchone()[0])
    assert latest['document_language']==choice and latest['chat_language']=='sv'
    model.return_value=answer(reply='Tack, jag har noterat namnet.',fields={'customer_name':'Example'})
    result=workflow.handle_demo_message(message('two','Example'),include_media=True,use_model=True)
    assert saved()['fields']['mermaid_intake']['language']==choice and result['media'] is None
    assert not result.get('mermaid_generation_failure') and 'Tack' in result['text']
    assert model.call_count==2

def test_typed_selection_requires_latest_evidence():
    fields={'chat_language':'sv'};flags={}
    language.apply_understanding(fields,flags,answer(document_language='nl',document_language_excerpt='Dutch'),message(text='Hello'))
    assert 'document_language' not in fields
    language.apply_understanding(fields,flags,answer(chat_language='nl',document_language='nl',document_language_excerpt='Dutch'),message(text='Dutch please'))
    assert fields['document_language']=='nl' and fields['chat_language']=='sv'

@pytest.mark.parametrize('token,account',[('old','synthetic-account'),('token','another-account')])
def test_invalid_picker_never_calls_model(token,account,monkeypatch):
    state_registry.wa_save_booking_state('synthetic-language-guest',{'mermaid_intake':{'language':'en','chat_language':'sv'}},{language.FLAG:{'token':'token','account':'synthetic-account'}})
    result=workflow.process_model_turn(message(_zernio_account_id=account,_zernio_interactive_id=language.PREFIX+token+':nl'),None)
    assert result.duplicate and not result.text
    marina_agent.process_message.assert_not_called()

def test_selector_native_payload_and_account_guard(monkeypatch):
    from agents.social import zernio_dm_client as provider
    fields={'language':'en','chat_language':'sv'}
    state_registry.wa_save_booking_state('synthetic-language-guest',{'mermaid_intake':fields},{language.FLAG:{'token':'token','account':'synthetic-account','text':'Välj dokumentens språk','button':'Välj språk'}})
    monkeypatch.setattr(provider,'_provider_mutation_account_allowed',lambda *a:True)
    monkeypatch.setattr(provider,'_recommendation_session_open',lambda *a:(True,[]))
    post=Mock(return_value=('sent',200,'synthetic-message'))
    monkeypatch.setattr(provider,'_post_recommendation_message',post)
    monkeypatch.setattr(provider,'_confirm_recommendation_status',lambda *a,**k:'sent')
    assert not language.send_picker('synthetic-language-guest','other','token')
    post.assert_not_called()
    assert language.send_picker('synthetic-language-guest','synthetic-account','token')
    payload=post.call_args.args[2]
    assert payload['interactive']['type']=='list'
    rows=payload['interactive']['action']['sections'][0]['rows']
    assert [r['title'] for r in rows]==list(language.options().values())
    assert len(rows)==6 and len(set(r['id'] for r in rows))==6


def test_selected_document_locale_reaches_reservation_and_email():
    from agents.social import mermaid_reservation_store as store, mermaid_email_template as email
    fields=dict(phase='summary_confirmed',language='nl',document_language='nl',chat_language='sv',trip_date='2099-09-19',adults=2,children=0,infants=0,customer_name='Sample Guest',contact_phone='+46123456789',pickup_preference='pier')
    reservation=store.confirm_reservation('synthetic-language-guest',fields,idempotency_key='synthetic-confirm')
    assert reservation['language']=='nl' and reservation['intake']['chat_language']=='sv'
    payment={'currency':'USD','amount':300,'payment_reference':'SAMPLE'}
    rendered=email.render_email(reservation,payment,'sample@example.com',receipt_url='https://example.com/receipt',hero_url='https://example.com/hero')
    assert 'Uw bezoek aan Klein Curaçao' in rendered['text']
    assert reservation['monetary_snapshot']['total']==300


def test_doc_language_does_not_trigger_chat_register_rejection():
    from agents.social.mermaid_model_recovery import _validation_error
    assert _validation_error(answer(language='pap',reply='Vilket datum vill ni åka?'),'Example',expected_locale='pap') is None


def test_supported_chat_keeps_normal_summary_flow(monkeypatch):
    fields=dict(trip_date='2099-09-19',adults=2,children=0,infants=0,customer_name='Sample Guest',contact_phone='+46123456789',pickup_preference='pier')
    monkeypatch.setattr(marina_agent,'process_message',Mock(return_value=answer(chat_language='en',fields=fields)))
    result=workflow.handle_demo_message(message(text='Our booking details'),include_media=True,use_model=True)
    assert saved()['fields']['mermaid_intake']['phase']=='awaiting_summary_confirmation'
    assert result['media'] is None and language.FLAG not in saved()['flags']


def test_button_cannot_switch_chat_language():
    fields={'chat_language':'sv'}
    flags={language.FLAG:{'token':'token','account':'synthetic-account'}}
    language.apply_understanding(fields,flags,answer(chat_language='nl'),message(_zernio_interactive_id=language.PREFIX+'token:nl'))
    assert fields['chat_language']=='sv' and fields['document_language']=='nl'
