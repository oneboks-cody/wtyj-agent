"""Regression scenarios from Calvin's real booking, without customer sends."""
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import Mock
import pytest
from pypdf import PdfReader
from agents.marina import marina_agent
from agents.social import mermaid_reservation_workflow as workflow
from agents.social import mermaid_documents as docs, mermaid_demo_payment as payment
from agents.social import mermaid_reservation_store as store
from agents.social import mermaid_delivery_reconciliation as reconcile
from agents.social import mermaid_guest_experience as guest
from agents.social import mermaid_response_policy as response_policy
from shared import config_loader, mermaid_catalog, state_registry


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    config = Path(__file__).resolve().parents[3] / 'clients/mermaid/config/client.json'
    monkeypatch.setattr(config_loader, '_CONFIG_PATH', str(config))
    monkeypatch.setattr(config_loader, '_cache', {})
    monkeypatch.setattr(state_registry, 'DB_PATH', str(tmp_path/'state.db'))
    monkeypatch.setenv('MERMAID_DOCUMENT_ROOT', str(tmp_path/'documents'))
    monkeypatch.setenv('MERMAID_DEMO_SIGNING_SECRET', 'test-only')
    monkeypatch.setenv('UNBOKS_PUBLIC_BASE_URL', 'https://demo.example/api/mermaid')
    monkeypatch.setenv('LATE_API_KEY', 'test-only')


def fields(locale='en'):
    return dict(trip_date='2026-09-06', adults=3, children=0, infants=0,
                customer_name='Test Guest', contact_phone='+12025550123', pickup_preference='pickup_requested',
                pickup_location='Piscadera Bay Resort', language=locale, phase='summary_confirmed')


def test_mixed_price_question_is_answered_without_forcing_confirmation(monkeypatch):
    initial=fields(); initial.pop('pickup_location'); initial.pop('pickup_preference'); initial['phase']='collecting'
    state_registry.wa_save_booking_state('guest', {'mermaid_intake': initial}, {})
    answer="Pickup costs extra, but I don't have a confirmed price for your resort. Would you prefer to keep it pending?"
    model=Mock(return_value=dict(language='en', mermaid_action='details', has_open_question=True,
        fields={'pickup_preference':'pier','pickup_location':'Piscadera Bay Resort'}, reply=answer))
    monkeypatch.setattr(marina_agent, 'process_message', model)
    result=workflow.process_model_turn({'from':'guest','text':'We have a car. Does pickup cost extra?', 'message_id':'one'}, None)
    assert result.text==answer and result.phase=='collecting'
    assert model.call_args.kwargs['thread_fields']['authoritative_pricing']['total']==450
    assert not store.latest_for_conversation('guest')
    model.return_value=dict(language='en',mermaid_action='confirm_summary',has_open_question=True, fields={},reply='The price is still pending.')
    result=workflow.process_model_turn({'from':'guest','text':'yes but how much is pickup?', 'message_id':'two'},None)
    assert result.action is None


def test_one_summary_price_and_natural_approval(monkeypatch):
    initial=fields(); initial['phase']='collecting'
    state_registry.wa_save_booking_state('guest', {'mermaid_intake':initial},{})
    model=Mock(return_value=dict(language='en',mermaid_action='details',fields={},reply='Noted.',has_open_question=False))
    monkeypatch.setattr(marina_agent,'process_message',model)
    result=workflow.process_model_turn({'from':'guest','message_id':'summary','text':'Keep pickup pending'},None)
    assert result.text.count('Here is what I have')==1
    assert model.call_args.kwargs['thread_fields']['pickup_status']=='included'
    assert 'USD 525.00' in result.text and 'USD 75.00' in result.text
    assert 'Piscadera Bay Resort' in result.text and '05:45' in result.text
    assert '0 children' not in result.text and '*YES*' not in result.text
    model.return_value=dict(language='en',mermaid_action='question',has_open_question=True,fields={},reply='Breakfast and lunch are included.')
    result=workflow.process_model_turn({'from':'guest','message_id':'question','text':'Is lunch included?'},None)
    assert result.text=='Breakfast and lunch are included.'
    model.return_value=dict(language='en',mermaid_action='confirm_summary',has_open_question=False,fields={},reply='Thanks.')
    result=workflow.process_model_turn({'from':'guest','message_id':'yes','text':'yesz'},None)
    assert result.action=='summary_confirmed'


def test_natural_approval_plus_payment_question_creates_quote_for_review(monkeypatch):
    initial=fields();initial['phase']='awaiting_summary_confirmation'
    state_registry.wa_save_booking_state('guest',{'mermaid_intake':initial},{})
    model=Mock(return_value=dict(
        language='en',mermaid_action='payment_status',fields={},reply='I can help with that.',
        confidence='high',requires_human=False,has_open_question=True,
        guest_question_excerpt='where and when do I pay?',calendar_request='none',
        status_request='payment',security_event='none',other_question_reply=''))
    monkeypatch.setattr(marina_agent,'process_message',model)
    result=workflow.handle_demo_message(
        {'from':'guest','text':'Yes, looks good, where and when do I pay?',
         'message_id':'natural-payment-approval'},True,use_model=True)
    reservation=store.latest_for_conversation('guest')
    assert reservation and reservation['state']=='quote_ready'
    assert result['media'] and result['media']['type']=='file'
    assert '/pay/' not in result['text']
    assert response_policy.copy('payment_none','en') not in result['text']


def test_payment_question_without_approval_explains_next_step(monkeypatch):
    initial=fields();initial['phase']='awaiting_summary_confirmation'
    state_registry.wa_save_booking_state('guest',{'mermaid_intake':initial},{})
    monkeypatch.setattr(marina_agent,'process_message',Mock(return_value=dict(
        language='en',mermaid_action='payment_status',fields={},reply='I can help with that.',
        confidence='high',requires_human=False,has_open_question=True,
        guest_question_excerpt='Where do I pay?',calendar_request='none',
        status_request='payment',security_event='none',other_question_reply='')))
    result=workflow.handle_demo_message(
        {'from':'guest','text':'Where do I pay?','message_id':'payment-question'},
        True,use_model=True)
    assert result['media'] is None
    assert store.latest_for_conversation('guest') is None
    assert result['text']==response_policy.copy('payment_none','en')
    assert "I’ll send your quote PDF" in result['text']


@pytest.mark.parametrize('locale',workflow.SUPPORTED_LOCALES)
def test_pickup_is_consistent_in_quote_receipt_checkout_and_confirmation(locale, tmp_path):
    item=store.confirm_reservation('guest',fields(locale),idempotency_key='confirm',zernio_account_id='owned-account')
    record={'currency':'USD','amount':525,'payment_reference':'PAY-DEMO-test','paid_at':'2026-09-03T23:48:43+00:00'}
    quote=tmp_path/'quote.pdf'; receipt=tmp_path/'receipt.pdf'
    docs.render_quote_pdf(item,quote); docs.render_receipt_pdf(item,record,receipt)
    for p in (quote,receipt):
        text=' '.join(page.extract_text() for page in PdfReader(p).pages)
        assert 'Piscadera Bay Resort' in text and '525.00' in text and '75.00' in text
        assert '05:45' in text and '06:45' not in text
        assert item['catalog_version'] not in text
    text=payment.success_message(item,record)
    assert 'Piscadera Bay Resort' in text and '05:45' in text and '06:45' not in text
    import time
    expires=int(time.time())+3600
    signature=payment.sign_payment(item['public_id'],expires,'test-only')
    page=payment.checkout_page(item['public_id'],expires,signature).body.decode()
    assert 'Piscadera Bay Resort' in page and '05:45' in page
    if locale=='en':
        assert 'Total incl. pickup' in page and 'USD 75.00' in page
        assert '05:45' in page


def receipt_job():
    item=store.confirm_reservation('guest',fields(),idempotency_key='confirm',zernio_account_id='owned-account')
    doc,job=docs.create_receipt(item,{'currency':'USD','amount':525,'payment_reference':'DEMO','paid_at':'2026-09-03T23:48:43+00:00'})
    docs.mark_delivery(job['public_id'],False,'timeout')
    return doc,job


@pytest.mark.parametrize('status,expected',[('sent','pending'),('delivered','delivered'),('read','delivered'),('failed','failed')])
def test_late_delivery_matches_document_with_changed_signature_once(status, expected, monkeypatch):
    doc,job=receipt_job()
    message=dict(id='provider-123',direction='outgoing',deliveryStatus=status,message='Actual provider wording.',
        createdAt=datetime.now(timezone.utc).isoformat(),attachments=[{'url':f"https://demo.example/api/mermaid/api/public/mermaid-document/{doc['public_id']}?expires=old&signature=old"}])
    response=Mock(status_code=200);response.json.return_value={'messages':[message]}
    read=Mock(return_value=response)
    monkeypatch.setattr(reconcile.provider,'_provider_account_get',read)
    monkeypatch.setattr(reconcile.provider,'_provider_history_still_owned',lambda *a:True)
    assert reconcile.reconcile_job(job['public_id'])==expected
    reconcile.reconcile_job(job['public_id'])
    assert docs.delivery_job(job['public_id'])['status']==expected
    history=state_registry.dm_get_history('guest','whatsapp',limit=20)
    assert sum(m['text']=='Actual provider wording.' for m in history)==(1 if expected=='delivered' else 0)
    assert read.call_args.kwargs['params']['accountId']=='owned-account'


@pytest.mark.parametrize('mismatch',['host','document','ownership','incoming'])
def test_reconciliation_rejects_unrelated_or_no_longer_owned_evidence(mismatch,monkeypatch):
    doc,job=receipt_job()
    url=f"https://demo.example/api/mermaid/api/public/mermaid-document/{doc['public_id']}"
    if mismatch=='host':url=url.replace('demo.example','evil.example')
    if mismatch=='document':url+='-other'
    message=dict(id='provider-123',direction='incoming' if mismatch=='incoming' else 'outgoing',deliveryStatus='delivered',message='Wrong',attachments=[{'url':url}])
    response=Mock(status_code=200);response.json.return_value={'messages':[message]}
    monkeypatch.setattr(reconcile.provider,'_provider_account_get',lambda *a,**k:response)
    monkeypatch.setattr(reconcile.provider,'_provider_history_still_owned',lambda *a:mismatch!='ownership')
    assert reconcile.reconcile_job(job['public_id'])=='unknown'
    assert docs.delivery_job(job['public_id'])['status']=='pending'


def test_repeated_payment_callback_reconciles_instead_of_resending(monkeypatch):
    item=store.confirm_reservation('guest',fields(),idempotency_key='confirm',zernio_account_id='owned-account')
    for state in ['quote_ready','demo_payment_pending']:
        item=store.transition(item['public_id'],state,idempotency_key=state,actor='test',reason='test')
    sender=Mock(return_value=False);monkeypatch.setattr(payment,'send_reply',sender)
    monkeypatch.setattr(payment.icp_overrides,'fetch_overrides_fresh',lambda:{})
    monkeypatch.setattr(payment.icp_overrides,'whatsapp_inbox_state',lambda x:True)
    monkeypatch.setattr(payment.icp_overrides,'auto_reply_state',lambda x:True)
    monkeypatch.setattr(reconcile,'reconcile_job',lambda x:'pending')
    import time
    expires=int(time.time())+3600;signature=payment.sign_payment(item['public_id'],expires,'test-only')
    for _ in range(3):assert payment.complete_checkout(item['public_id'],expires,signature,'success').status_code==200
    sender.assert_called_once()
    conn=docs._conn()
    try:job=dict(conn.execute("SELECT * FROM mermaid_delivery_jobs WHERE kind='receipt'").fetchone())
    finally:conn.close()
    assert job['status']=='pending' and job['attempts']==1
    assert docs.claim_initial_delivery(job['public_id']) is False


@pytest.mark.parametrize('location,adults,fee', [('Piscadera Bay Resort', 3, 75), ('Westpunt', 2, 75), ('Jan Thiel', 7, 125)])
def test_vehicle_pickup_charge_is_independent_of_location(location, adults, fee):
    intake=fields(); intake.update(pickup_location=location, adults=adults)
    money=store._money_snapshot(intake,mermaid_catalog.get_catalog())
    assert money['pickup_amount']==fee
    assert '05:45' in guest.transport_text(intake, 'en', money)
    assert money['total']==adults*150+fee
    assert [i for i in money['items'] if i['key']=='pickup']==[
        {'key':'pickup','label':'Pickup','quantity':1,'unit_amount':fee,'line_total':fee}]
    intake['pickup_preference']='pier'
    own=store._money_snapshot(intake,mermaid_catalog.get_catalog())
    assert own['pickup_amount'] is None and own['total']==adults*150
    assert '06:45' in guest.transport_text(intake, 'en', own)
    assert '05:45' not in guest.transport_text(intake, 'en', own)
    assert not any(i['key']=='pickup' for i in own['items'])


def test_current_fee_does_not_reprice_historical_reservation(monkeypatch,tmp_path):
    old=mermaid_catalog.get_catalog(); old['pricing'].pop('pickup_vehicles'); old['pricing']['pickup_price']=None; old['version']='historical-demo'
    with monkeypatch.context() as m:
        m.setattr(mermaid_catalog,'get_catalog',lambda:old)
        item=store.confirm_reservation('old-guest',fields(),idempotency_key='old')
    assert mermaid_catalog.get_catalog()['pricing']['pickup_vehicles'][0]['price']==75
    assert store.get_reservation(item['public_id'])['monetary_snapshot']['total']==450
    record={'currency':'USD','amount':450,'payment_reference':'OLD','paid_at':'2026-09-03T23:48:43+00:00'}
    for kind,render in [('quote',lambda p:docs.render_quote_pdf(item,p)),('receipt',lambda p:docs.render_receipt_pdf(item,record,p))]:
        p=tmp_path/f'{kind}.pdf'; render(p)
        text=' '.join(page.extract_text() for page in PdfReader(p).pages)
        assert '450.00' in text and 'pickup excluded' in text
        assert '525.00' not in text and '75.00' not in text and '05:45' not in text
    assert 'USD 75.00' not in payment.success_message(item,record)
    import time
    expires=int(time.time())+3600
    page=payment.checkout_page(item['public_id'],expires,payment.sign_payment(item['public_id'],expires,'test-only')).body.decode()
    assert 'USD 450.00' in page and 'pickup excluded' in page and '75.00' not in page


def test_pickup_never_invents_currency_conversion():
    intake=fields(); intake['currency']='EUR'
    with pytest.raises(store.MermaidReservationError,match='no conversion'):
        store._money_snapshot(intake,mermaid_catalog.get_catalog())


@pytest.mark.parametrize('amount', [-1, True, '75', 75.5])
def test_invalid_pickup_price_is_rejected(amount):
    catalog=mermaid_catalog.get_catalog();catalog['pricing']['pickup_vehicles'][0]['price']=amount
    with pytest.raises(mermaid_catalog.MermaidCatalogError,match='pickup price'):
        mermaid_catalog.validate_catalog(catalog)


@pytest.mark.parametrize('minutes,expected', [(60, '05:45'), (30, '06:15'), (90, '05:15')])
def test_pickup_time_uses_configured_lead_time(minutes, expected, monkeypatch):
    catalog=mermaid_catalog.get_catalog()
    catalog['service']['pickup_minutes_before_arrival']=minutes
    mermaid_catalog.validate_catalog(catalog)
    monkeypatch.setattr(mermaid_catalog,'get_catalog',lambda:catalog)
    assert mermaid_catalog.pickup_time()==expected
    assert expected in guest.transport_text(fields(),'en')


@pytest.mark.parametrize('minutes', [None, -1, 0, True, '60', 60.5, 406])
def test_invalid_pickup_lead_time_is_rejected(minutes):
    catalog=mermaid_catalog.get_catalog()
    catalog['service']['pickup_minutes_before_arrival']=minutes
    with pytest.raises(mermaid_catalog.MermaidCatalogError,match='pickup lead time'):
        mermaid_catalog.validate_catalog(catalog)
