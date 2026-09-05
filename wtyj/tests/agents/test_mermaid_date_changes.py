from pathlib import Path
from datetime import datetime,timedelta
from unittest.mock import Mock
import json
import pytest
from pypdf import PdfReader
from shared import config_loader,state_registry,mermaid_customers
from agents.social import mermaid_date_changes as changes,mermaid_documents as docs,mermaid_reservation_store as store,mermaid_reservation_workflow as workflow
from agents.marina import marina_agent

@pytest.fixture(autouse=True)
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(config_loader,'_CONFIG_PATH',str(Path(__file__).resolve().parents[3]/'clients/mermaid/config/client.json'))
    monkeypatch.setattr(config_loader,'_cache',{})
    monkeypatch.setattr(state_registry,'DB_PATH',str(tmp_path/'state.db'))
    monkeypatch.setattr(state_registry,'_alert_dispatcher',None)
    monkeypatch.setattr(state_registry,'_summary_dispatcher',None)
    monkeypatch.setenv('MERMAID_DOCUMENT_ROOT',str(tmp_path/'documents'))
    monkeypatch.setenv('MERMAID_DEMO_SIGNING_SECRET','test')
    monkeypatch.setenv('UNBOKS_PUBLIC_BASE_URL','https://example.test')


def booked():
    day=datetime.now(changes.LOCAL).date()+timedelta(days=7)
    while day.weekday()!=1:day+=timedelta(days=1)
    fields={'trip_date':day.isoformat(),'customer_name':'Calvin','contact_phone':'+12025550123','adults':4,'children':1,'infants':1,'child_ages':[{'value':6,'unit':'years'},{'value':11,'unit':'months'}],'pickup_preference':'pier','language':'en','phase':'summary_confirmed'}
    state_registry.wa_save_booking_state('guest',{'mermaid_intake':fields},{},[])
    r=store.confirm_reservation('guest',fields,idempotency_key='book',zernio_account_id='account')
    r=store.transition(r['public_id'],'quote_ready',idempotency_key='quote',actor='test',reason='test')
    r=store.transition(r['public_id'],'demo_payment_pending',idempotency_key='pay',actor='test',reason='test')
    r,p=store.complete_demo_payment(r['public_id'],payment_reference='PAY-TEST',idempotency_key='payment')
    d,j=docs.create_receipt(r,p)
    r=store.attach_receipt(r['public_id'],d['public_id'])
    return r,p,(day+timedelta(days=1)).isoformat(),d


def message(mid='request'):
    return {'from':'guest','_zernio_account_id':'account','message_id':mid,'text':'Can we move to Wednesday?'}


def tap(token,choice='confirm',**kwargs):
    return {**message('tap'), '_zernio_interactive_id':changes.PREFIX+token+':'+choice,**kwargs}


def test_confirm_changes_only_date_and_records_history_and_versioned_pdf():
    r,p,new,old=booked();original=Path(old['path']).read_bytes()
    proposal=changes.propose(message(),r,new,'en');token=proposal['media']['url']
    assert store.get_reservation(r['public_id'])['intake']==r['intake']
    assert new in changes.pending('guest')['new_date']
    reply=changes.handle_button(tap(token));updated=store.get_reservation(r['public_id'])
    assert updated['intake']=={**r['intake'],'trip_date':new}
    assert updated['monetary_snapshot']==r['monetary_snapshot'] and updated['payment_reference']==r['payment_reference']
    documents=docs.documents_for_reservation(r['public_id']);assert len(documents)==2
    assert Path(old['path']).read_bytes()==original
    latest=next(d for d in documents if d['public_id']==updated['receipt_public_id'])
    c=docs._conn(); path=c.execute('SELECT path FROM mermaid_documents WHERE public_id=?',(latest['public_id'],)).fetchone()[0]; c.close()
    pdf=PdfReader(path);assert len(pdf.pages)==1 and len(pdf.pages[0].images)==1
    assert changes.guest.guest_date(new,'en') in pdf.pages[0].extract_text()
    assert len([e for e in store.events(r['public_id']) if e['event_type']=='date_changed'])==1
    account=mermaid_customers.account_id('guest')
    assert mermaid_customers.get_account(account)['details']['trip_date']==new
    assert any('HO note recorded' in m['text'] for m in mermaid_customers.history(account)['items'])
    assert state_registry.wa_get_booking_state('guest')['fields']['mermaid_intake']['trip_date']==new
    replay=changes.handle_button(tap(token,message_id='tap-again'))
    assert replay['mermaid_delivery_commit']==reply['mermaid_delivery_commit']
    assert len(docs.documents_for_reservation(r['public_id']))==2
    docs.mark_delivery(reply['mermaid_delivery_commit']['job_id'],True)
    assert changes.handle_button(tap(token))['duplicate']


@pytest.mark.parametrize('case',['wrong_guest','wrong_account','expired','superseded','revision','keep','held'])
def test_invalid_or_declined_confirmation_does_not_change_reservation(case):
    r,p,new,old=booked();proposal=changes.propose(message(),r,new,'en');token=proposal['media']['url'];m=tap(token)
    if case=='wrong_guest':m['from']='stranger'
    if case=='wrong_account':m['_zernio_account_id']='other'
    if case=='keep':m=tap(token,'keep')
    if case=='held':store.freeze_for_human(r['public_id'])
    if case in {'expired','superseded','revision'}:
        c=changes._conn()
        with c:
            if case=='expired':c.execute('UPDATE mermaid_date_changes SET expires_at=0')
            if case=='superseded':c.execute("UPDATE mermaid_date_changes SET status='superseded'")
            if case=='revision':c.execute('UPDATE mermaid_reservations SET revision=revision+1')
        c.close()
    reply=changes.handle_button(m)
    assert not reply.get('mermaid_delivery_commit')
    assert store.get_reservation(r['public_id'])['intake']['trip_date']==r['intake']['trip_date']
    assert len(docs.documents_for_reservation(r['public_id']))==1


def test_model_date_request_returns_confirmation_buttons_without_escalation(monkeypatch):
    r,p,new,old=booked()
    model=Mock(return_value={'language':'en','mermaid_action':'change_date','fields':{'trip_date':new},'reply':'','requires_human':False,'has_open_question':True,'guest_question_excerpt':'Can we move to Wednesday?'})
    monkeypatch.setattr(marina_agent,'process_message',model)
    reply=workflow.handle_demo_message(message(),include_media=True,use_model=True)
    assert reply['media']['type']==changes.MEDIA_TYPE and reply['mermaid_action']=='date_change'
    assert state_registry.get_active_escalation_mode('guest') is None
    result=workflow.handle_demo_message(tap(reply['media']['url']),include_media=True,use_model=True)
    assert result.get('mermaid_delivery_commit') and model.call_count==1


def test_provider_confirmation_uses_two_bound_reply_buttons(monkeypatch):
    from agents.social import zernio_dm_client as provider
    r,p,new,old=booked();token=changes.propose(message(),r,new,'en')['media']['url'];posts=[]
    monkeypatch.setattr(provider,'_provider_mutation_account_allowed',lambda *a:True)
    monkeypatch.setattr(provider,'_recommendation_session_open',lambda *a:(True,[]))
    monkeypatch.setattr(provider,'_post_recommendation_message',lambda *a:posts.append(a[2]) or ('sent',200,'provider-id'))
    monkeypatch.setattr(provider,'_confirm_recommendation_status',lambda *a,**k:'sent')
    assert changes.send_confirmation('guest','account',token)
    assert changes.send_confirmation('guest','account',token)
    assert len(posts)==1 and len(posts[0]['buttons'])==2
    assert 'text' not in posts[0]
    assert changes.guest.guest_date(r['intake']['trip_date'],'en') in posts[0]['message']
    assert changes.guest.guest_date(new,'en') in posts[0]['message']
    assert posts[0]['message'].endswith('Is that correct?')
    assert posts[0]['buttons'][0]['payload']==changes.PREFIX+token+':confirm'


@pytest.mark.parametrize('target',['2020-01-01','not a date'])
def test_invalid_date_never_creates_proposal(target):
    r,p,new,old=booked()
    assert changes.propose(message(),r,target,'en')['media'] is None
    assert changes.pending('guest') is None


def test_pdf_failure_rolls_back_date_and_audit(monkeypatch):
    r,p,new,old=booked();token=changes.propose(message(),r,new,'en')['media']['url']
    monkeypatch.setattr(docs,'render_receipt_pdf',Mock(side_effect=RuntimeError('renderer unavailable')))
    with pytest.raises(RuntimeError):changes.handle_button(tap(token))
    assert store.get_reservation(r['public_id'])['intake']==r['intake']
    assert not any(e['event_type']=='date_changed' for e in store.events(r['public_id']))
    assert changes.pending('guest')['token']==token


def test_two_simultaneous_taps_commit_one_change():
    from concurrent.futures import ThreadPoolExecutor
    r,p,new,old=booked();token=changes.propose(message(),r,new,'en')['media']['url']
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda n:changes.handle_button(tap(token,message_id=str(n))),range(2)))
    assert results[0]['mermaid_delivery_commit']==results[1]['mermaid_delivery_commit']
    assert sum(e['event_type']=='date_changed' for e in store.events(r['public_id']))==1


def test_natural_approval_uses_the_current_proposal(monkeypatch):
    r,p,new,old=booked();changes.propose(message(),r,new,'en')
    monkeypatch.setattr(marina_agent,'process_message',Mock(return_value={'language':'en','mermaid_action':'confirm_date_change','fields':{},'reply':'','requires_human':False,'has_open_question':False,'guest_question_excerpt':''}))
    reply=workflow.handle_demo_message({**message('natural-confirm'),'text':'yes that is correct'},include_media=True,use_model=True)
    assert reply.get('mermaid_delivery_commit')
    assert store.get_reservation(r['public_id'])['intake']['trip_date']==new


def test_separate_food_answer_is_in_the_actual_button_payload(monkeypatch):
    from agents.social import zernio_dm_client as provider
    r,p,new,old=booked()
    model=Mock(return_value={'language':'en','mermaid_action':'change_date','fields':{'trip_date':new},'reply':'','requires_human':False,'other_question_topic':'food','other_question_excerpt':'Is lunch included?','other_question_reply':'Breakfast and BBQ lunch are included.'})
    monkeypatch.setattr(marina_agent,'process_message',model)
    reply=workflow.handle_demo_message({**message(),'text':'Can we move to Wednesday? Is lunch included?'},include_media=True,use_model=True)
    assert 'BBQ lunch' in reply['text']
    posts=[]
    monkeypatch.setattr(provider,'_provider_mutation_account_allowed',lambda *a:True)
    monkeypatch.setattr(provider,'_recommendation_session_open',lambda *a:(True,[]))
    monkeypatch.setattr(provider,'_post_recommendation_message',lambda *a:posts.append(a[2]) or ('sent',200,'p'))
    monkeypatch.setattr(provider,'_confirm_recommendation_status',lambda *a,**k:'sent')
    assert changes.send_confirmation('guest','account',reply['media']['url'])
    assert posts[0]['message']==reply['text']


@pytest.mark.parametrize('evidence_valid',[True,False])
def test_food_answer_in_separate_field_is_not_discarded_by_model_adapter(monkeypatch,evidence_valid):
    from types import SimpleNamespace
    text='My son is gluten intolerant. What can he eat?'
    answer='The chicken and most salads are listed as gluten-free. Bring gluten-free bread for breakfast.'
    raw={'language':'en','mermaid_action':'question','fields':{},'reply':'','requires_human':False,'other_question_topic':'food','other_question_excerpt':'What can he eat?' if evidence_valid else 'an invented question','other_question_reply':answer}
    client=Mock();client.messages.create.return_value=SimpleNamespace(content=[SimpleNamespace(type='tool_use',input=raw)],usage=None)
    monkeypatch.setenv('ANTHROPIC_API_KEY','fake-key')
    monkeypatch.setattr(marina_agent.anthropic,'Anthropic',Mock(return_value=client))
    result=marina_agent.process_message('guest','Question',text,{}, {},channel='whatsapp',response_contract='mermaid_reservation_demo')
    assert client.messages.create.call_count==1
    if evidence_valid:assert result['reply']==answer and not result.get('generation_failed')
    else:assert result.get('generation_failed')
