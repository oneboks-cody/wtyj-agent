"""Customer files retain enquiries, corrections, conversations and bookings."""
from contextlib import closing
from pathlib import Path
import json
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from dashboard import api
from shared import config_loader, state_registry as state, mermaid_customers as customers
from agents.social import mermaid_reservation_store as store


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config_loader, '_CONFIG_PATH', str(Path(__file__).resolve().parents[3] / 'clients/mermaid/config/client.json'))
    monkeypatch.setattr(config_loader, '_cache', {})
    monkeypatch.setattr(state, 'DB_PATH', str(tmp_path/'state.db'))
    monkeypatch.setattr(api, '_current_tenant_slug', lambda: 'mermaid')
    monkeypatch.setattr(state, '_alert_dispatcher', None)
    with closing(store._conn()): pass
    app = FastAPI(); app.include_router(api.router)
    return TestClient(app, headers={'Authorization':f'Bearer {api._SESSION_TOKEN}'})


def save(cid='conversation-a', **updates):
    details=dict(customer_name='Test Guest', contact_phone='+12025550123', trip_date='2026-09-06',
                 adults=2, children=0, infants=0, pickup_preference='pier', language='en', phase='collecting')
    details.update(updates)
    state.wa_save_booking_state(cid, {'mermaid_intake':details}, {})
    return details


def test_enquiry_visible_before_booking_and_corrected_details_survive_reset(client):
    state.dm_store_inbound_message('conversation-a','whatsapp','Hello','Test Guest',['one'])
    listing=client.get('/dashboard/api/mermaid-customers').json()
    account=listing['items'][0]; cid=account['id']
    assert account['reservationCount']==0 and account['messageCount']==1
    save(); save(contact_phone='+12025550999', pickup_location='Westpunt')
    save(contact_phone='+12025550999', pickup_location='Westpunt')
    state.wa_save_booking_state('conversation-a', {}, {})
    assert customers.get_account(cid)['details']['contact_phone']=='+12025550999'
    revisions=customers.history(cid, changes=True)['items']
    assert len(revisions)==2
    assert revisions[-1]['details']['contact_phone']=='+12025550123'
    assert client.get('/dashboard/api/mermaid-customers?query=2025550999').json()['items'][0]['id']==cid
    assert client.get('/dashboard/api/mermaid-customers?query=nobody').json()['items']==[]


def test_no_callback_merging_and_booking_values_unchanged_on_backfill(client):
    details=save(phase='summary_confirmed')
    reservation=store.confirm_reservation('conversation-a',details,idempotency_key='one')
    save('conversation-b', customer_name='Another Guest')
    before=store.get_reservation(reservation['public_id'])
    assert customers.backfill()==2
    assert customers.backfill()==2
    assert len(customers.list_accounts()['items'])==2
    cid=customers.account_id('conversation-a')
    response=client.get(f'/dashboard/api/mermaid-customers/{cid}')
    assert response.headers['x-unboks-tenant']=='mermaid'
    assert response.headers['cache-control'].startswith('no-store')
    assert response.json()['reservations'][0]['publicId']==reservation['public_id']
    assert store.get_reservation(reservation['public_id'])==before
    assert len(customers.history(cid,changes=True)['items'])==1


def test_email_persists_through_booking_updates_without_merging_guest_profiles(client):
    original = save()
    save('conversation-b', customer_name='Another Guest')
    first = customers.account_id('conversation-a')
    second = customers.account_id('conversation-b')
    with closing(state._get_conn()) as conn, conn:
        # A shared family inbox is contact information, not account identity.
        assert customers.set_email(conn, 'conversation-a', 'family@example.com') == first
        assert customers.set_email(conn, 'conversation-b', 'family@example.com') == second
        customers.set_email(conn, 'conversation-a', 'family@example.com')
    assert first != second
    assert customers.get_account(first)['customerName'] == original['customer_name']
    assert len(customers.history(first, changes=True)['items']) == 2
    save(trip_date='2026-09-09', phase='booked')
    with closing(state._get_conn()) as conn, conn:
        customers.set_email(conn, 'conversation-a', 'updated@example.com')
    # Reservation snapshots contain no email and may later be recaptured.
    save(trip_date='2026-09-11', phase='booked')
    account = client.get(f'/dashboard/api/mermaid-customers/{first}').json()
    assert account['details']['email'] == 'updated@example.com'
    assert account['details']['trip_date'] == '2026-09-11'
    assert account['details']['contact_phone'] == original['contact_phone']
    assert account['customerName'] == original['customer_name']
    assert customers.get_account(second)['details']['email'] == 'family@example.com'
    history = customers.history(first, changes=True)['items']
    assert [item['details'].get('email') for item in history] == [
        'updated@example.com', 'updated@example.com', 'family@example.com', 'family@example.com', None,
    ]
    with closing(state._get_conn()) as conn:
        conn.execute('BEGIN IMMEDIATE')
        customers.set_email(conn, 'conversation-a', 'rolled-back@example.com')
        conn.rollback()
    assert customers.get_account(first)['details']['email'] == 'updated@example.com'
    assert client.get('/dashboard/api/mermaid-customers?query=updated%40example.com').json()['items'][0]['id'] == first


def test_backfill_existing_legacy_data_without_inventing_phone(client):
    with closing(state._get_conn()) as conn, conn:
        conn.execute("INSERT INTO whatsapp_threads(phone,role,text,created_at,sender_name,channel) VALUES(?,?,?,?,?,?)",
                     ('legacy','user','hello','2026-01-01T00:00:00+00:00','Guest','whatsapp'))
        conn.execute("INSERT INTO whatsapp_booking_state(phone,fields_json,flags_json,completed_bookings_json,last_activity,created_at) VALUES(?,?,?,?,?,?)",
                     ('legacy',json.dumps({'mermaid_intake':{'customer_name':'Guest','adults':4}}),'{}','[]','2026-01-02','2026-01-01'))
    assert customers.backfill()==1
    cid=customers.account_id('legacy')
    assert 'contact_phone' not in customers.get_account(cid)['details']
    assert customers.get_account(cid)['firstSeen']=='2026-01-01T00:00:00+00:00'
    assert customers.backfill()==1


def test_history_pagination_keeps_all_messages_and_mermaid_retention(client,monkeypatch):
    for i in range(205):
        state.dm_store_message('guest','whatsapp','user',str(i),created_at='2026-01-01T00:00:00+00:00')
    cid=customers.account_id('guest')
    first=customers.history(cid,limit=100)
    # A newer message must not shift the cursor or duplicate earlier pages.
    state.dm_store_message('guest','whatsapp','assistant','new')
    second=customers.history(cid,first['nextBefore'],100)
    third=customers.history(cid,second['nextBefore'],100)
    assert [m['text'] for m in third['items']+second['items']+first['items']]==[str(i) for i in range(205)]
    assert third['nextBefore'] is None
    assert state.wa_cleanup_stale_data()['threads_cleaned']==0
    monkeypatch.setattr(config_loader,'get_raw',lambda:{'slug':'other','features':{'mermaid_customer_accounts':True}})
    assert state.wa_cleanup_stale_data()['threads_cleaned']==205


def test_account_write_is_atomic_and_concurrent_identity_is_unique(client):
    with closing(state._get_conn()) as conn:
        conn.execute('BEGIN IMMEDIATE')
        customers.capture(conn,'rollback',intake={'customer_name':'No save'})
        conn.rollback()
    assert customers.account_id('rollback') is None
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda i: state.dm_store_inbound_message('same','whatsapp','hi','Guest',[str(i)]),range(8)))
    assert len(customers.list_accounts()['items'])==1
    assert customers.list_accounts()['items'][0]['messageCount']==8


def test_api_auth_tenant_and_bounds(client,monkeypatch):
    assert client.get('/dashboard/api/mermaid-customers',headers={'Authorization':''}).status_code==401
    assert client.get('/dashboard/api/mermaid-customers/999').status_code==404
    assert client.get('/dashboard/api/mermaid-customers/999/history').status_code==404
    assert client.get('/dashboard/api/mermaid-customers?offset=-1').status_code==422
    assert client.get('/dashboard/api/mermaid-customers?limit=1000').status_code==422
    monkeypatch.setattr(api,'_current_tenant_slug',lambda:'ali-car-rental')
    assert client.get('/dashboard/api/mermaid-customers').status_code==404


def test_pdf_download_is_private_and_bound_to_customer(client, tmp_path, monkeypatch):
    from agents.social import mermaid_documents as docs
    monkeypatch.setattr(docs, '_root', lambda: tmp_path/'documents')
    details=save(phase='summary_confirmed')
    reservation=store.confirm_reservation('conversation-a',details,idempotency_key='one')
    document,_=docs.create_quote(reservation)
    save('other')
    cid=customers.account_id('conversation-a')
    other=customers.account_id('other')
    path=f"/dashboard/api/mermaid-customers/{cid}/documents/{document['public_id']}"
    response=client.get(path)
    assert response.status_code==200 and response.content.startswith(b'%PDF')
    assert response.headers['x-unboks-tenant']=='mermaid'
    assert 'no-store' in response.headers['cache-control']
    assert client.get(path,headers={'Authorization':''}).status_code==401
    assert client.get(path.replace(f'/{cid}/',f'/{other}/')).status_code==404
    Path(document['path']).write_bytes(b'changed')
    assert client.get(path).status_code==404
