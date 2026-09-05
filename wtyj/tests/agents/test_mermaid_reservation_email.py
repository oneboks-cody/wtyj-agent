"""Focused, fully offline checks for consented booking emails and delivery state."""

from datetime import datetime, timedelta
from email import policy
from email.parser import BytesParser
import hashlib
from pathlib import Path
import smtplib
import socket
import ssl
from unittest.mock import Mock

import anthropic
import pytest

from shared import config_loader, mermaid_customers, state_registry
from agents.marina import marina_agent
from agents.social import mermaid_date_changes as changes
from agents.social import mermaid_documents as docs
from agents.social import mermaid_email_template as template
from agents.social import mermaid_email_transport as transport
from agents.social import mermaid_reservation_email as emails
from agents.social import mermaid_reservation_store as store
from agents.social import mermaid_reservation_workflow as workflow


_SMTP_SEND = transport.send_email


@pytest.fixture(autouse=True)
def offline_email_state(tmp_path, monkeypatch):
    monkeypatch.setattr(config_loader, '_CONFIG_PATH', str(
        Path(__file__).resolve().parents[3] / 'clients/mermaid/config/client.json'))
    monkeypatch.setattr(config_loader, '_cache', {})
    monkeypatch.setattr(state_registry, 'DB_PATH', str(tmp_path / 'state.db'))
    monkeypatch.setattr(state_registry, '_alert_dispatcher', None)
    monkeypatch.setattr(state_registry, '_summary_dispatcher', None)
    monkeypatch.setenv('MERMAID_DOCUMENT_ROOT', str(tmp_path / 'documents'))
    monkeypatch.setenv('MERMAID_DEMO_SIGNING_SECRET', 'offline-test-only')
    monkeypatch.setenv('UNBOKS_PUBLIC_BASE_URL', 'https://example.test')
    monkeypatch.setenv('MERMAID_EMAIL_ADDRESS', 'hello@1boks.com')
    monkeypatch.setenv('MERMAID_EMAIL_PASSWORD', 'offline-password')
    monkeypatch.delenv('MERMAID_EMAIL_PASSWORD_FILE', raising=False)
    monkeypatch.setattr(anthropic, 'Anthropic', Mock(side_effect=AssertionError('Model calls are forbidden')))
    monkeypatch.setattr(socket, 'create_connection', Mock(side_effect=AssertionError('Network is forbidden')))
    monkeypatch.setattr(transport.smtplib, 'SMTP', Mock(side_effect=AssertionError('SMTP must be mocked')))
    monkeypatch.setattr(transport, 'ready', lambda: True)

    # PDF layout already has dedicated tests. Here the artifact boundary proves
    # which immutable receipt bytes were attached without rendering five PDFs.
    def receipt_bytes(reservation, payment, target):
        target.parent.mkdir(parents=True, exist_ok=True)
        data = ('%PDF-1.4\nOffline receipt: ' + reservation['intake']['trip_date']).encode()
        target.write_bytes(data)
        return hashlib.sha256(data).hexdigest()

    monkeypatch.setattr(docs, 'render_receipt_pdf', receipt_bytes)
    outgoing = Mock(return_value=None)
    monkeypatch.setattr(transport, 'send_email', outgoing)
    yield outgoing


def booking(*, paid=True, phone='guest'):
    day = datetime.now(changes.LOCAL).date() + timedelta(days=7)
    while day.weekday() != 1:
        day += timedelta(days=1)
    intake = {
        'trip_date': day.isoformat(), 'customer_name': 'Calvin & <Family>',
        'contact_phone': '+12025550123', 'adults': 4, 'children': 1, 'infants': 1,
        'child_ages': [{'value': 6, 'unit': 'years'}, {'value': 11, 'unit': 'months'}],
        'pickup_preference': 'pier', 'language': 'en', 'phase': 'summary_confirmed',
    }
    state_registry.wa_save_booking_state(phone, {'mermaid_intake': intake}, {}, [])
    reservation = store.confirm_reservation(
        phone, intake, idempotency_key=phone + ':book', zernio_account_id='account')
    if not paid:
        return reservation
    reservation = store.transition(reservation['public_id'], 'quote_ready', idempotency_key=phone + ':quote', actor='test', reason='test')
    reservation = store.transition(reservation['public_id'], 'demo_payment_pending', idempotency_key=phone + ':pay', actor='test', reason='test')
    reservation, payment = store.complete_demo_payment(
        reservation['public_id'], payment_reference='PAY-OFFLINE-' + phone, idempotency_key=phone + ':paid')
    document, _ = docs.create_receipt(reservation, payment)
    return store.attach_receipt(reservation['public_id'], document['public_id'])


def inbound(reservation, action, text, source, address=None):
    message = {'from': reservation['conversation_id'], '_zernio_account_id': 'account',
               'message_id': source, 'text': text}
    understood = {'email_action': action, 'email_request_excerpt': text}
    if address is not None:
        understood['email_address'] = address
    return emails.handle(message, reservation, understood, 'en')


def model_inbound(monkeypatch, reservation, action, text, source, address=None, **extra):
    message = {'from': reservation['conversation_id'], '_zernio_account_id': 'account',
               'message_id': source, 'text': text}
    understood = {'language': 'en', 'mermaid_action': 'acknowledge', 'fields': {},
                  'reply': 'Handled locally.', 'requires_human': False,
                  'email_action': action, 'email_request_excerpt': text, **extra}
    if address is not None:
        understood['email_address'] = address
    model = Mock(return_value=understood)
    monkeypatch.setattr(marina_agent, 'process_message', model)
    result = workflow.process_model_turn(message, reservation)
    assert model.call_count == 1
    assert result.generation_failure is None
    return result, model, message


def jobs(reservation):
    conn = emails._conn()
    try:
        return [dict(row) for row in conn.execute(
            'SELECT * FROM mermaid_reservation_emails WHERE reservation_public_id=? ORDER BY rowid',
            (reservation['public_id'],))]
    finally:
        conn.close()


def opt_in(reservation):
    assert emails.offer(reservation)
    reply = inbound(reservation, 'accept', 'Yes please', 'email-yes')
    assert 'email address' in reply.lower()
    assert emails.context(reservation)['phase'] == 'awaiting_address'


def test_paid_guest_opts_in_then_address_sends_once_and_records_profile_and_audit(offline_email_state, monkeypatch):
    reservation = booking()
    assert not offline_email_state.called
    status, model, _ = model_inbound(monkeypatch, reservation, 'status', 'Did you email it?', 'email-status')
    assert model.call_args.kwargs['thread_fields']['email_offer']['phase'] == 'not_offered'
    assert status.action == 'reservation_email' and status.text.endswith('?')
    assert emails.context(reservation)['phase'] == 'offered'
    assert emails.offer(reservation)
    accepted, model, _ = model_inbound(monkeypatch, reservation, 'accept', 'Yes please', 'email-yes')
    assert model.call_args.kwargs['thread_fields']['email_offer']['phase'] == 'offered'
    assert 'email address' in accepted.text.lower()
    assert emails.context(reservation)['phase'] == 'awaiting_address'
    assert not offline_email_state.called
    recipient = 'calvin@example.test'
    reply, model, message = model_inbound(monkeypatch, reservation, 'address', recipient, 'email-address', recipient)
    assert model.call_args.kwargs['thread_fields']['email_offer']['phase'] == 'awaiting_address'
    replay = workflow.process_model_turn(message, reservation)
    assert model.call_count == 1
    assert reply.text == replay.text and recipient in reply.text
    assert offline_email_state.call_count == 1
    delivered = jobs(reservation)
    assert len(delivered) == 1 and delivered[0]['status'] == 'accepted'
    assert delivered[0]['recipient'] == recipient
    assert delivered[0]['revision'] == reservation['revision']
    customer_id = mermaid_customers.account_id('guest')
    assert mermaid_customers.get_account(customer_id)['details']['email'] == recipient
    audit = mermaid_customers.history(customer_id)['items']
    assert any('Guest agreed' in item['text'] for item in audit)
    assert any(recipient in item['text'] and 'accepted' in item['text'] for item in audit)
    sent_to, content, attachment = offline_email_state.call_args.args
    assert sent_to == recipient and reservation['intake']['trip_date'].encode() in attachment[1]
    assert 'Calvin &amp; &lt;Family&gt;' in content['html']
    assert 'Calvin & <Family>' not in content['html']
    for url in template.settings()['policy_links'].values():
        assert url in content['text'] and 'href="' + url + '"' in content['html']
    assert '4 adults' in content['text'] and '11 months' in content['text']
    assert '<img ' in content['html'] and 'image/' in content['html']
    assert 'SMTP' not in content['text']


def test_address_alone_needs_confirmation_and_decline_invalid_or_unpaid_never_send(offline_email_state):
    reservation = booking()
    recipient = 'calvin@example.test'
    reply = inbound(reservation, 'address', recipient, 'address-alone', recipient)
    assert recipient in reply and reply.endswith('?')
    assert emails.context(reservation)['phase'] == 'awaiting_confirmation'
    inbound(reservation, 'decline', 'No thanks', 'email-no')
    assert emails.context(reservation)['phase'] == 'declined'
    invalid = inbound(reservation, 'address', 'calvin@', 'invalid-email', 'calvin@')
    assert 'check' in invalid.lower()
    guessed = inbound(reservation, 'address', 'Use my email', 'invented-email', 'someone@example.test')
    assert 'check' in guessed.lower()
    unpaid = booking(paid=False, phone='unpaid-guest')
    assert emails.offer(unpaid) == ''
    reply = inbound(unpaid, 'request', 'Email me at other@example.test', 'unpaid-email', 'other@example.test')
    assert 'payment' in reply.lower()
    assert not offline_email_state.called
    assert jobs(reservation) == [] and jobs(unpaid) == []
    customer_id = mermaid_customers.account_id('guest')
    assert mermaid_customers.get_account(customer_id)['details']['email'] == recipient


def test_uncertain_send_is_not_retried_without_explicit_resend(offline_email_state):
    reservation = booking()
    opt_in(reservation)
    recipient = 'calvin@example.test'
    offline_email_state.side_effect = [transport.EmailSendError('delivery_uncertain', uncertain=True), None]
    reply = inbound(reservation, 'address', recipient, 'first-send', recipient)
    assert 'check your inbox' in reply.lower()
    assert jobs(reservation)[0]['status'] == 'uncertain'
    inbound(reservation, 'address', recipient, 'first-send', recipient)
    inbound(reservation, 'address', recipient, 'new-message-same-address', recipient)
    assert offline_email_state.call_count == 1
    reply = inbound(reservation, 'resend', 'Please resend it', 'explicit-resend')
    assert recipient in reply
    assert offline_email_state.call_count == 2
    assert [row['status'] for row in jobs(reservation)] == ['uncertain', 'accepted']
    assert len({row['message_id'] for row in jobs(reservation)}) == 2


def test_requested_resend_uses_latest_confirmed_date_and_receipt_without_losing_email(offline_email_state):
    reservation = booking()
    opt_in(reservation)
    recipient = 'calvin@example.test'
    inbound(reservation, 'address', recipient, 'original-email', recipient)
    first_attachment = offline_email_state.call_args.args[2]
    new_date = (datetime.fromisoformat(reservation['intake']['trip_date']).date() + timedelta(days=1)).isoformat()
    proposal = changes.propose(
        {'from': 'guest', '_zernio_account_id': 'account', 'message_id': 'date-request', 'text': 'Move to Wednesday'},
        reservation, new_date, 'en')
    changes.handle_button({
        'from': 'guest', '_zernio_account_id': 'account', 'message_id': 'date-confirm', 'text': 'Yes, change date',
        '_zernio_interactive_id': changes.PREFIX + proposal['media']['url'] + ':confirm',
    })
    updated = store.get_reservation(reservation['public_id'])
    assert updated['receipt_public_id'] != reservation['receipt_public_id']
    customer_id = mermaid_customers.account_id('guest')
    assert mermaid_customers.get_account(customer_id)['details']['email'] == recipient
    # A stale in-memory reservation must never cause the old receipt to be sent.
    inbound(reservation, 'resend', 'Email me the updated copy', 'updated-email')
    assert offline_email_state.call_count == 2
    _, content, attachment = offline_email_state.call_args.args
    assert new_date.encode() in attachment[1] and attachment[1] != first_attachment[1]
    assert changes.guest.guest_date(new_date, 'en') in content['text']
    latest = jobs(reservation)[-1]
    assert latest['document_public_id'] == updated['receipt_public_id']
    assert latest['revision'] == updated['revision']


def test_mixed_date_and_email_waits_for_confirm_or_keep_and_status_preserves_consent(offline_email_state, monkeypatch):
    for index, choice in enumerate(('confirm', 'keep')):
        phone = 'deferred-' + choice
        reservation = booking(phone=phone)
        recipient = 'calvin-' + choice + '@example.test'
        original_date = reservation['intake']['trip_date']
        new_date = (datetime.fromisoformat(original_date).date() + timedelta(days=1)).isoformat()
        result, _, _ = model_inbound(
            monkeypatch, reservation, 'request',
            'Move my reservation to Wednesday, and email the updated receipt to ' + recipient,
            phone + ':request', recipient, mermaid_action='change_date', fields={'trip_date': new_date})
        assert result.action == 'date_change'
        token = result.as_reply()['media']['url']
        assert changes.pending(phone)['token'] == token
        preference = emails.context(reservation)
        assert preference['phase'] == 'awaiting_date_confirmation'
        assert preference['proposed_email'] == recipient
        assert jobs(reservation) == [] and offline_email_state.call_count == index
        status, _, _ = model_inbound(
            monkeypatch, reservation, 'status', 'Did you email it?', phone + ':status')
        assert 'date change' in status.text.lower()
        assert emails.context(reservation)['phase'] == 'awaiting_date_confirmation'
        assert emails.context(reservation)['proposed_email'] == recipient
        button = {'from': phone, '_zernio_account_id': 'account', 'message_id': phone + ':decision',
                  'text': 'Yes, change date' if choice == 'confirm' else 'Keep date',
                  '_zernio_interactive_id': changes.PREFIX + token + ':' + choice}
        final_reply = changes.handle_button(button)
        current = store.get_reservation(reservation['public_id'])
        expected_date = new_date if choice == 'confirm' else original_date
        assert current['intake']['trip_date'] == expected_date
        assert offline_email_state.call_count == index + 1 and recipient in final_reply['text']
        sent_to, content, attachment = offline_email_state.call_args.args
        assert sent_to == recipient and expected_date.encode() in attachment[1]
        assert changes.guest.guest_date(expected_date, 'en') in content['text']
        sent_jobs = jobs(reservation)
        assert len(sent_jobs) == 1 and sent_jobs[0]['status'] == 'accepted'
        assert sent_jobs[0]['document_public_id'] == current['receipt_public_id']
        assert sent_jobs[0]['revision'] == current['revision']
        changes.handle_button({**button, 'message_id': phone + ':decision-replay'})
        assert offline_email_state.call_count == index + 1


def test_smtp_acceptance_survives_quit_failure_and_only_sends_after_tls_and_login(monkeypatch):
    events = []
    captured = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            assert (host, port, timeout) == ('smtp.gmail.com', 587, 20)
            events.append('connect')

        def ehlo(self):
            events.append('ehlo')
            return 250, b'hello'

        def starttls(self, *, context):
            assert isinstance(context, ssl.SSLContext)
            assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
            events.append('tls')

        def login(self, sender, password):
            assert sender == 'hello@1boks.com' and password == 'offline-password'
            assert events[-2:] == ['tls', 'ehlo']
            events.append('login')

        def mail(self, sender):
            assert events[-1] == 'login' and sender == 'hello@1boks.com'
            events.append('mail')
            return 250, b'ok'

        def rcpt(self, recipient):
            assert recipient == 'guest@example.test'
            events.append('rcpt')
            return 250, b'ok'

        def data(self, data):
            assert events[-2:] == ['mail', 'rcpt']
            captured['message'] = BytesParser(policy=policy.default).parsebytes(data)
            events.append('data')
            return 250, b'accepted'

        def quit(self):
            events.append('quit')
            raise smtplib.SMTPServerDisconnected('after acceptance')

        def close(self):
            events.append('close')

    monkeypatch.setattr(transport.smtplib, 'SMTP', FakeSMTP)
    result = _SMTP_SEND(
        'guest@example.test', {'subject': 'Your reservation ☀️', 'text': 'Receipt attached.', 'html': '<p>Receipt attached.</p>'},
        ('Receipt.pdf', b'%PDF-1.4\nOffline'), message_id='<offline-reservation@1boks.com>')
    assert result is None and events[-3:] == ['data', 'quit', 'close']
    message = captured['message']
    assert message['From'].addresses[0].display_name == 'Tracy | Mermaid Boat Trips'
    assert message['Message-ID'] == '<offline-reservation@1boks.com>'
    assert message.get_body(preferencelist=('plain',)).get_content().strip() == 'Receipt attached.'
    assert '<p>Receipt attached.</p>' in message.get_body(preferencelist=('html',)).get_content()
    attachments = list(message.iter_attachments())
    assert len(attachments) == 1 and attachments[0].get_filename() == 'Receipt.pdf'
    assert attachments[0].get_payload(decode=True) == b'%PDF-1.4\nOffline'
