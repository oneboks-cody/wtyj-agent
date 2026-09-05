"""Evidence-driven calendar, status and abuse regressions from baseline #342."""
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import Mock

import pytest
from agents.marina import marina_agent
from agents.social import mermaid_reservation_workflow as workflow
from agents.social import mermaid_response_policy as policy
from agents.social import mermaid_reservation_store as store
from agents.social import mermaid_model_recovery as recovery
from shared import config_loader, state_registry, mermaid_catalog


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config_loader, '_CONFIG_PATH', str(Path(__file__).resolve().parents[3] / 'clients/mermaid/config/client.json'))
    monkeypatch.setattr(config_loader, '_cache', {})
    monkeypatch.setattr(state_registry, 'DB_PATH', str(tmp_path / 'state.db'))
    monkeypatch.setattr(state_registry, '_alert_dispatcher', None)
    monkeypatch.setattr(state_registry, '_summary_dispatcher', None)
    monkeypatch.setenv('MERMAID_DOCUMENT_ROOT', str(tmp_path / 'documents'))


def model(monkeypatch, locale='en', **kw):
    output = dict(language=locale, mermaid_action='question', reply='UNSUPPORTED CLAIM', fields={},
                  has_open_question=True, guest_question_excerpt='question', requires_human=False,
                  calendar_request='none', status_request='none', security_event='none',
                  automation_loop=False)
    output.update(kw)
    stub = Mock(return_value=output)
    monkeypatch.setattr(marina_agent, 'process_message', stub)
    return stub


@pytest.mark.parametrize('locale', workflow.SUPPORTED_LOCALES)
def test_calendar_replaces_model_invented_dates(monkeypatch, locale):
    monkeypatch.setattr(policy, 'local_today', lambda: date(2026, 9, 4))
    model(monkeypatch, locale, calendar_request='this_week', fields={'trip_date':'2026-09-10'})
    result = workflow.process_model_turn({'from':'guest','message_id':'week','text':'question'}, None)
    assert result.text == policy.calendar_reply('this_week', locale)
    assert policy.date_label('2026-09-06', locale) in result.text
    assert policy.date_label('2026-09-10', locale) not in result.text
    assert 'trip_date' not in state_registry.wa_get_booking_state('guest')['fields']['mermaid_intake']


def test_calendar_week_boundaries_and_current_published_days():
    assert policy.local_today(datetime(2026, 9, 7, 2, tzinfo=timezone.utc)) == date(2026, 9, 6)
    assert policy.calendar_dates('this_week', today=date(2026, 9, 6)) == [date(2026, 9, 6)]
    weekend = policy.calendar_dates('weekend', today=date(2026, 9, 4))
    assert [(d.isoformat(), d.weekday()) for d in weekend] == [('2026-09-05',5),('2026-09-06',6)]
    catalog = mermaid_catalog.get_catalog(); catalog['service']['operating_weekdays']=['sunday']
    assert policy.calendar_dates('next_week', today=date(2026, 12, 31), catalog=catalog) == [date(2027,1,10)]


@pytest.mark.parametrize('locale', workflow.SUPPORTED_LOCALES)
def test_queued_review_is_not_claimed_active(monkeypatch, locale):
    state_registry.create_pending_notification('escalation','whatsapp','guest','Test Guest','Review','Review',mode='soft')
    model(monkeypatch, locale, status_request='handover')
    result = workflow.process_model_turn({'from':'guest','message_id':'status','text':'question'}, None)
    assert result.text == policy.copy('review_queued', locale)


@pytest.mark.parametrize('locale', workflow.SUPPORTED_LOCALES)
def test_payment_and_email_claims_require_recorded_state(monkeypatch, locale):
    stub = model(monkeypatch, locale, status_request='payment')
    result = workflow.process_model_turn({'from':'guest','message_id':'paid','text':'question'}, None)
    assert result.text == policy.copy('payment_none', locale)
    stub.return_value['status_request'] = 'delivery'
    result = workflow.process_model_turn({'from':'guest','message_id':'email','text':'question'}, None)
    assert result.text == policy.copy('delivery_none', locale)


@pytest.mark.parametrize('locale', workflow.SUPPORTED_LOCALES)
def test_owner_approved_pickup_includes_return(monkeypatch, locale):
    model(monkeypatch, locale, status_request='pickup_coverage')
    result = workflow.process_model_turn({'from':'guest','message_id':'return','text':'question'}, None)
    assert result.text == policy.copy('pickup_round_trip', locale)


def test_confirmed_automation_loop_stops_before_reply_and_never_escalates(monkeypatch):
    intake = {
        'trip_date': '2026-09-12', 'adults': 2, 'children': 0, 'infants': 0,
        'customer_name': 'Saved Guest', 'contact_phone': '+12025550123',
        'pickup_preference': 'pier', 'language': 'en', 'phase': 'collecting',
    }
    state_registry.wa_save_booking_state('loop-guest', {'mermaid_intake': intake}, {})
    state_registry.wa_store_message('loop-guest', 'user', 'Automated peer reply')
    stub = model(
        monkeypatch,
        automation_loop=True,
        mermaid_action='request_human',
        requires_human=True,
        fields={'adults': 99},
        reply='THIS MUST NEVER BE SENT',
    )
    result = workflow.handle_demo_message({
        'from': 'loop-guest', '_zernio_sender_name': 'Unboks',
        'message_id': 'loop-one', 'text': 'Automated peer reply',
    }, include_media=True, use_model=True)

    assert result['text'] == '' and result['mermaid_action'] == 'loop_stopped'
    saved = state_registry.wa_get_booking_state('loop-guest')
    assert saved['fields']['mermaid_intake'] == intake
    assert saved['flags'][state_registry.MERMAID_LOOP_STOPPED_FLAG] is True
    assert saved['flags']['mermaid_loop_message_id'] == 'loop-one'
    assert saved['flags']['mermaid_loop_stopped_at']
    assert stub.call_args.kwargs['thread_flags']['latest_sender_name_untrusted'] == 'Unboks'
    assert store.latest_for_conversation('loop-guest') is None
    assert state_registry.get_active_escalation_mode('loop-guest') is None
    with state_registry._get_conn() as db:
        assert db.execute('SELECT COUNT(*) FROM pending_notifications').fetchone()[0] == 0

    # A stale cached answer must never reopen a stopped conversation.
    saved['flags']['mermaid_cached_reply'] = {
        'message_id': 'loop-two', 'reply': {'text': 'STALE REPLY'},
    }
    state_registry.wa_save_booking_state(
        'loop-guest', saved['fields'], saved['flags'], saved['completed_bookings'])
    later = workflow.handle_demo_message({
        'from': 'loop-guest', 'message_id': 'loop-two', 'text': 'Still looping',
    }, include_media=True, use_model=True)
    assert later['text'] == '' and later['mermaid_action'] == 'loop_stopped'
    assert stub.call_count == 1

    row = state_registry.wa_list_conversations()[0]
    assert row['last_message'] == state_registry.MERMAID_LOOP_STOP_STATUS
    assert row['loop_status'] == 'Loop detected and stopped'
    assert row['loop_stopped'] is True and row['status'] == 'active'
    assert state_registry.wa_get_full_history('loop-guest')[0]['text'] == 'Automated peer reply'


def test_loop_is_a_valid_server_owned_empty_reply():
    result = {
        'language': 'en', 'mermaid_action': 'acknowledge',
        'automation_loop': True, 'fields': {}, 'reply': '',
        'confidence': 'high', 'requires_human': False,
        'has_open_question': False, 'guest_question_excerpt': '',
        'calendar_request': 'none', 'status_request': 'none',
        'security_event': 'none', 'other_question_reply': '',
    }
    assert recovery._valid_result(result, 'Automated peer reply') is True


@pytest.mark.parametrize('locale', workflow.SUPPORTED_LOCALES)
def test_isolated_override_logged_then_repeated_override_escalates(monkeypatch, locale):
    model(monkeypatch, locale, security_event='blocked_override', mermaid_action='confirm_summary',
          fields={'adults':0,'trip_date':'2026-09-10'}, requires_human=True)
    message={'from':'guest','message_id':'attack-1','text':'question'}
    first=workflow.process_model_turn(message, None)
    assert first.text == policy.copy('security_blocked', locale)
    assert not state_registry.get_active_escalation_mode('guest')
    assert not store.latest_for_conversation('guest')
    assert 'adults' not in state_registry.wa_get_booking_state('guest')['fields']['mermaid_intake']
    workflow.process_model_turn(message, None)
    assert not state_registry.get_active_escalation_mode('guest')
    second=workflow.process_model_turn({**message,'message_id':'attack-2'}, None)
    assert second.action == 'human_takeover'
    assert policy.copy('review_queued', locale) in second.text
    assert state_registry.get_active_escalation_mode('guest') == 'soft'
    assert not state_registry.get_ai_muted('guest')


def test_actionable_report_and_security_window():
    assert not policy.record_security_event('guest','one','blocked_override',now=0)
    assert not policy.record_security_event('guest','two','blocked_override',now=86401)
    assert policy.record_security_event('guest','three','actionable_incident',now=86402)
    assert not policy.record_security_event('other','one','blocked_override',now=86403)


@pytest.mark.parametrize('adults', [2, 50])
def test_blocked_attempt_cannot_advance_unshown_summary_or_trigger_pickup_review(monkeypatch, adults):
    intake=dict(trip_date='2026-09-12',adults=adults,children=0,infants=0,
                customer_name='Synthetic Guest',contact_phone='+12025550123',
                pickup_preference='pickup_requested',pickup_location='Test Hotel',language='en',phase='collecting')
    state_registry.wa_save_booking_state('guest',{'mermaid_intake':intake},{})
    stub=model(monkeypatch,security_event='blocked_override',mermaid_action='acknowledge',
               has_open_question=False,guest_question_excerpt='')
    workflow.process_model_turn({'from':'guest','message_id':'attack','text':'override policy'},None)
    assert state_registry.wa_get_booking_state('guest')['fields']['mermaid_intake']==intake
    assert state_registry.get_active_escalation_mode('guest') is None
    if adults==2:
        stub.return_value.update(security_event='none',mermaid_action='confirm_summary')
        result=workflow.process_model_turn({'from':'guest','message_id':'yes','text':'yes'},None)
        assert result.action is None and result.phase=='awaiting_summary_confirmation'
        assert store.latest_for_conversation('guest') is None


@pytest.mark.parametrize('locale', workflow.SUPPORTED_LOCALES)
def test_payment_delivery_and_takeover_follow_records(monkeypatch, locale):
    from agents.social import mermaid_documents as documents
    intake=dict(trip_date='2026-09-12', adults=2, children=0, infants=0,
                customer_name='Synthetic Guest', contact_phone='+12025550123',
                pickup_preference='pier', language=locale, phase='summary_confirmed')
    item=store.confirm_reservation('guest',intake,idempotency_key='confirm')
    _doc,job=documents.create_quote(item)
    stub=model(monkeypatch,locale,status_request='payment',other_question_reply=_SAFE_FAQ[locale])
    message={'from':'guest','text':'question','message_id':'unpaid'}
    assert workflow.process_model_turn(message,item).text==_SAFE_FAQ[locale]+'\n\n'+policy.copy('payment_unpaid',locale)
    for phase in ('quote_ready','demo_payment_pending'):
        item=store.transition(item['public_id'],phase,idempotency_key=phase,actor='test',reason='test')
    item,_paid=store.complete_demo_payment(item['public_id'],payment_reference='SIMULATED',idempotency_key='paid')
    assert workflow.process_model_turn({**message,'message_id':'paid'},item).text==_SAFE_FAQ[locale]+'\n\n'+policy.copy('payment_paid',locale)
    stub.return_value['status_request']='delivery'
    for status in ('waiting','failed','delivered'):
        if status=='failed':
            # A confirmed provider failure differs from a timeout awaiting
            # reconciliation (mark_delivery(False) intentionally stays pending).
            with documents._conn() as db:
                db.execute("UPDATE mermaid_delivery_jobs SET status='failed',last_error='provider confirmed failure' WHERE public_id=?",(job['public_id'],))
        elif status=='delivered':documents.mark_delivery(job['public_id'],True)
        assert workflow.process_model_turn({**message,'message_id':status},item).text==_SAFE_FAQ[locale]+'\n\n'+policy.copy('delivery_'+status,locale)
    state_registry.set_ai_muted('guest',True,channel='whatsapp')
    assert policy.state_context('guest',item)['review']=='active'


@pytest.mark.parametrize('locale', workflow.SUPPORTED_LOCALES)
def test_party_uses_singular_forms(locale):
    from agents.social import mermaid_guest_experience as guest
    text=guest.party_text({'adults':1,'children':1,'infants':1},locale)
    copy=guest.guest_copy(locale)['formal_party']
    assert text==' · '.join((copy['adult_one'].format(count=1), copy['children_unknown_one'].format(count=1), copy['infants_unknown_one'].format(count=1)))


@pytest.mark.parametrize('selector', ['payment', 'delivery', 'wildlife_guarantee'])
def test_cancellation_outcome_takes_priority_over_status_selector(monkeypatch, selector):
    intake=dict(trip_date='2026-09-12',adults=2,children=0,infants=0,
                customer_name='Synthetic Guest',contact_phone='+12025550123',
                pickup_preference='pier',language='en',phase='summary_confirmed')
    item=store.confirm_reservation('guest',intake,idempotency_key='confirm')
    for phase in ('quote_ready','demo_payment_pending'):
        item=store.transition(item['public_id'],phase,idempotency_key=phase,actor='test',reason='test')
    model(monkeypatch,mermaid_action='cancel',requires_human=True,status_request=selector)
    reply=workflow.handle_demo_message({'from':'guest','message_id':'cancel','text':'question'},include_media=True,use_model=True)
    assert reply['text']==workflow.COPY['en']['cancelled']
    assert store.get_reservation(item['public_id'])['state']=='cancelled'


_REVIEW_ACK = {
    'en': "Yes, all those details are correct.",
    'nl': "Ja, al die gegevens kloppen.",
    'de': "Ja, alle diese Angaben stimmen.",
    'es': "Sí, todos esos datos son correctos.",
    'pap': "Si, tur e datonan ta korekto.",
    'pt': "Sim, todos esses dados estão corretos.",
}
_WILDLIFE_CONDITION = {
    'en': "Yes, but only if you can guarantee we will see turtles.",
    'nl': "Ja, maar alleen als je kunt garanderen dat we schildpadden zien.",
    'de': "Ja, aber nur wenn Sie garantieren, dass wir Schildkröten sehen.",
    'es': "Sí, pero solo si pueden garantizar que veremos tortugas.",
    'pap': "Si, pero solamente si bo por garantisá ku nos lo mira turtuga.",
    'pt': "Sim, mas só se puderem garantir que veremos tartarugas.",
}
_FALSE_REVIEW_PROSE = {
    'en': "The team is reviewing your request and will contact you when it is approved.",
    'nl': "Het team beoordeelt je verzoek en neemt contact op zodra het is goedgekeurd.",
    'de': "Die Rollstuhlfrage wird noch vom Mermaid-Team geprüft. Sobald die Freigabe vorliegt, geht es mit der Buchung weiter.",
    'es': "El equipo está revisando tu solicitud y te avisará cuando esté aprobada.",
    'pap': "E tim ta revisá bo petishon i lo tuma kontakto ku bo ora e ta aprobá.",
    'pt': "A equipe está analisando seu pedido e entrará em contato quando estiver aprovado.",
}
_SAFE_FAQ = {
    'en': "Breakfast is included. Please arrive at the pier at 06:45.",
    'nl': "Ontbijt is inbegrepen. Zorg dat je om 06:45 bij de pier bent.",
    'de': "Frühstück ist enthalten. Bitte seien Sie um 06:45 Uhr am Pier.",
    'es': "El desayuno está incluido. Llega al muelle a las 06:45.",
    'pap': "Desayuno ta inkluí. Yega na e pier pa 06:45.",
    'pt': "O café da manhã está incluído. Chegue ao píer às 06:45.",
}
_FOOD_QUESTION = {
    'en': "Is breakfast included? When should we arrive?",
    'nl': "Is ontbijt inbegrepen? Hoe laat moeten we er zijn?",
    'de': "Ist Frühstück dabei? Wann müssen wir da sein?",
    'es': "¿Está incluido el desayuno? ¿A qué hora debemos llegar?",
    'pap': "Desayuno ta inkluí? Ki ora nos mester yega?",
    'pt': "O café da manhã está incluído? A que horas devemos chegar?",
}


def _queued_accessibility_review(locale):
    intake = dict(trip_date='2026-09-12', adults=2, children=1, infants=0,
                  customer_name='Nadia Croes', contact_phone='+12025550045',
                  pickup_preference='pier', language=locale, phase='human_takeover',
                  accessibility_notes='Guest uses a wheelchair and asks about safe boarding')
    state_registry.wa_save_booking_state('guest', {'mermaid_intake': intake}, {})
    state_registry.create_pending_notification(
        'escalation', 'whatsapp', 'guest', 'Nadia Croes',
        'Mermaid reservation: human review', 'Accessible boarding needs review.', mode='soft')
    return intake


def _assert_review_preserved(intake):
    assert state_registry.wa_get_booking_state('guest')['fields']['mermaid_intake'] == intake
    assert state_registry.get_active_escalation_mode('guest') == 'soft'
    assert state_registry.get_ai_muted('guest') is False
    assert store.latest_for_conversation('guest') is None
    with state_registry._get_conn() as db:
        assert db.execute('SELECT COUNT(*) FROM pending_notifications').fetchone()[0] == 1


@pytest.mark.parametrize('locale', workflow.SUPPORTED_LOCALES)
@pytest.mark.parametrize('action', ['acknowledge', 'question', 'details'])
def test_plain_review_acknowledgement_uses_queue_records_without_model_status_selector(monkeypatch, locale, action):
    intake = _queued_accessibility_review(locale)
    stub = model(monkeypatch, locale, mermaid_action=action,
                 reply=_FALSE_REVIEW_PROSE[locale], has_open_question=False,
                 guest_question_excerpt='')
    result = workflow.process_model_turn(
        {'from': 'guest', 'message_id': 'review-ack', 'text': _REVIEW_ACK[locale]}, None)
    assert result.text == policy.copy('review_queued', locale)
    assert result.action is None
    assert stub.call_args.kwargs['thread_fields']['recorded_status']['review'] == 'queued'
    _assert_review_preserved(intake)


@pytest.mark.parametrize('locale', workflow.SUPPORTED_LOCALES)
@pytest.mark.parametrize('action', ['acknowledge', 'question', 'details'])
@pytest.mark.parametrize('requires_human', [False, True])
def test_review_faq_uses_supported_evidenced_body_despite_generic_label(monkeypatch, locale, action, requires_human):
    intake = _queued_accessibility_review(locale)
    model(monkeypatch, locale, mermaid_action=action,
          reply=_SAFE_FAQ[locale] + ' ' + _FALSE_REVIEW_PROSE[locale],
          other_question_reply=_SAFE_FAQ[locale], status_request='none',
          other_question_topic='food', other_question_excerpt=_FOOD_QUESTION[locale],
          guest_question_excerpt='', has_open_question=True, requires_human=requires_human)
    result = workflow.process_model_turn(
        {'from': 'guest', 'message_id': 'review-faq', 'text': _FOOD_QUESTION[locale]}, None)
    assert result.text == _SAFE_FAQ[locale]
    assert result.action == ('human_takeover' if requires_human else None)
    _assert_review_preserved(intake)


@pytest.mark.parametrize('locale', workflow.SUPPORTED_LOCALES)
@pytest.mark.parametrize('other', [None, '', '   '])
@pytest.mark.parametrize('requires_human', [False, True])
def test_missing_review_faq_never_falls_back_to_raw_active_staff_prose(monkeypatch, locale, other, requires_human):
    intake = _queued_accessibility_review(locale)
    additions = {} if other is None else {'other_question_reply': other}
    model(monkeypatch, locale, mermaid_action='question',
          reply=_SAFE_FAQ[locale] + ' ' + _FALSE_REVIEW_PROSE[locale],
          status_request='none', guest_question_excerpt='', has_open_question=False, requires_human=requires_human,
          **additions)
    result = workflow.process_model_turn(
        {'from': 'guest', 'message_id': 'missing-faq', 'text': 'Question?'}, None)
    # This is truthful status, not a successful answer to the missing FAQ.
    assert result.text == policy.copy('review_queued', locale)
    assert result.action == ('human_takeover' if requires_human else None)
    _assert_review_preserved(intake)


@pytest.mark.parametrize('boundary', ['security', 'first_review', 'request_human', 'cancel', 'confirm_summary', 'new_booking', 'pickup_review'])
def test_repeated_review_faq_cannot_override_primary_review_reason(monkeypatch, boundary):
    if boundary != 'first_review':
        intake = _queued_accessibility_review('en')
        if boundary == 'pickup_review':
            intake.update(adults=50, pickup_preference='pickup_requested', pickup_location='Test Hotel')
            state_registry.wa_save_booking_state('guest', {'mermaid_intake': intake}, {})
    changes = {'requires_human': True, 'mermaid_action': 'question'}
    if boundary == 'security':
        changes['security_event'] = 'blocked_override'
    elif boundary in {'request_human', 'cancel', 'confirm_summary', 'new_booking'}:
        changes['mermaid_action'] = boundary
    model(monkeypatch, reply='UNVERIFIED RAW STAFF CLAIM', other_question_reply='SUPPRESSED FAQ', **changes)
    result = workflow.process_model_turn(
        {'from': 'guest', 'message_id': 'protected-review', 'text': 'question'}, None)
    assert 'SUPPRESSED FAQ' not in result.text and 'UNVERIFIED' not in result.text
    if boundary == 'security':
        assert result.text == policy.copy('security_blocked', 'en')
    else:
        assert result.action == 'human_takeover' and result.text == policy.copy('review_queued', 'en')
    assert store.latest_for_conversation('guest') is None
    assert state_registry.get_ai_muted('guest') is False
    with state_registry._get_conn() as db:
        assert db.execute('SELECT COUNT(*) FROM pending_notifications').fetchone()[0] == 1


@pytest.mark.parametrize('locale', workflow.SUPPORTED_LOCALES)
def test_ordinary_faq_outside_review_keeps_raw_answer(monkeypatch, locale):
    model(monkeypatch, locale, reply=_SAFE_FAQ[locale],
          other_question_reply='UNUSED', guest_question_excerpt='Question?')
    result = workflow.process_model_turn(
        {'from': 'guest', 'message_id': 'ordinary-faq', 'text': 'Question?'}, None)
    assert result.text == _SAFE_FAQ[locale]
    assert state_registry.get_active_escalation_mode('guest') is None
    assert store.latest_for_conversation('guest') is None


@pytest.mark.parametrize('route', ['calendar', 'wildlife_guarantee', 'pickup_coverage', 'pickup_pricing'])
def test_review_faq_fallback_keeps_protected_fact_routes(monkeypatch, route):
    intake = _queued_accessibility_review('en')
    selector = {'calendar_request': 'operating_days'} if route == 'calendar' else {'status_request': route}
    model(monkeypatch, reply=_FALSE_REVIEW_PROSE['en'],
          other_question_reply=_SAFE_FAQ['en'], guest_question_excerpt='', **selector)
    result = workflow.process_model_turn(
        {'from': 'guest', 'message_id': 'protected-facts', 'text': 'Question?'}, None)
    if route == 'calendar':
        expected = _SAFE_FAQ['en'] + '\n\n' + policy.calendar_reply('operating_days', 'en')
    elif route == 'wildlife_guarantee':
        expected = policy.wildlife_guarantee_reply('en', {'review': 'queued'})
    elif route == 'pickup_coverage':
        expected = _SAFE_FAQ['en'] + '\n\n' + policy.pickup_coverage_reply('en')
    else:
        expected = policy.pickup_pricing_reply('en', intake) + '\n\n' + _SAFE_FAQ['en']
    assert result.text == expected
    _assert_review_preserved(intake)


@pytest.mark.parametrize('locale', workflow.SUPPORTED_LOCALES)
def test_wildlife_condition_during_review_uses_facts_and_keeps_safe_followup(monkeypatch, locale):
    intake = _queued_accessibility_review(locale)
    stub = model(monkeypatch, locale, reply=_FALSE_REVIEW_PROSE[locale],
                 status_request='wildlife_guarantee', guest_question_excerpt=_WILDLIFE_CONDITION[locale])
    result = workflow.process_model_turn(
        {'from': 'guest', 'message_id': 'wildlife', 'text': _WILDLIFE_CONDITION[locale]}, None)
    assert result.text == policy.copy('wildlife_guarantee', locale) + '\n\n' + policy.copy('review_queued', locale)
    assert result.action is None
    _assert_review_preserved(intake)
    stub.return_value.update(reply=_SAFE_FAQ[locale], other_question_reply=_SAFE_FAQ[locale],
                             status_request='none', guest_question_excerpt=_FOOD_QUESTION[locale],
                             other_question_topic='food', other_question_excerpt=_FOOD_QUESTION[locale])
    followup = workflow.process_model_turn(
        {'from': 'guest', 'message_id': 'safe-followup', 'text': _FOOD_QUESTION[locale]}, None)
    assert followup.text == _SAFE_FAQ[locale]
    assert followup.action is None
    _assert_review_preserved(intake)


def test_wildlife_selector_does_not_invent_a_review_and_cannot_override_security(monkeypatch):
    stub = model(monkeypatch, status_request='wildlife_guarantee')
    result = workflow.process_model_turn({'from': 'guest', 'message_id': 'wildlife', 'text': 'question'}, None)
    assert result.text == policy.copy('wildlife_guarantee', 'en')
    assert state_registry.get_active_escalation_mode('guest') is None
    stub.return_value.update(security_event='blocked_override', mermaid_action='acknowledge')
    refused = workflow.process_model_turn({'from': 'guest', 'message_id': 'override', 'text': 'question'}, None)
    assert refused.text == policy.copy('security_blocked', 'en')
    assert state_registry.get_active_escalation_mode('guest') is None


@pytest.mark.parametrize('action', ['confirm_summary', 'new_booking', 'cancel'])
def test_wildlife_selector_does_not_replace_pending_review_decisions(monkeypatch, action):
    intake = _queued_accessibility_review('en')
    model(monkeypatch, mermaid_action=action, status_request='wildlife_guarantee')
    result = workflow.process_model_turn({'from': 'guest', 'message_id': 'decision', 'text': 'question'}, None)
    assert result.text == workflow.COPY['en']['human']
    assert result.action is None
    _assert_review_preserved(intake)


@pytest.mark.parametrize('locale', workflow.SUPPORTED_LOCALES)
@pytest.mark.parametrize('selector,key', [('handover','review_queued'),('payment','payment_none'),('delivery','delivery_none')])
def test_recorded_status_keeps_dedicated_food_and_arrival_answer(monkeypatch, locale, selector, key):
    intake=_queued_accessibility_review(locale)
    model(monkeypatch,locale,status_request=selector,
          reply='UNVERIFIED: staff are working, paid, email delivered.',
          other_question_reply=_SAFE_FAQ[locale], guest_question_excerpt='Question?')
    result=workflow.process_model_turn({'from':'guest','message_id':'faq-status','text':'Question?'},None)
    assert result.text==_SAFE_FAQ[locale]+'\n\n'+policy.copy(key,locale)
    assert result.action is None and 'UNVERIFIED' not in result.text
    _assert_review_preserved(intake)


def test_followup_base045_t6_keeps_exact_faq_in_dedicated_field(monkeypatch):
    intake=_queued_accessibility_review('de')
    question='Ist Frühstück dabei? Wann müssen wir da sein?'
    answer="Ja, Frühstück ist inklusive, genauso wie Softdrinks, Säfte und ein BBQ-Mittagessen.\n\nIhr Treffpunkt am Fishermen's Pier ist um 06:45 Uhr zum Check-in."
    model(monkeypatch,'de',status_request='handover',reply=answer,
          other_question_reply=answer, guest_question_excerpt=question)
    result=workflow.process_model_turn({'from':'guest','message_id':'base045-t6','text':question},None)
    assert result.text==answer+'\n\n'+policy.copy('review_queued','de')
    _assert_review_preserved(intake)


@pytest.mark.parametrize('selector,key', [('handover','review_queued'),('payment','payment_none'),('delivery','delivery_none')])
@pytest.mark.parametrize('other', [None, '', '   '])
def test_empty_or_legacy_omitted_faq_never_reuses_raw_status_prose(monkeypatch, selector, key, other):
    _queued_accessibility_review('en')
    additions={} if other is None else {'other_question_reply':other}
    model(monkeypatch,status_request=selector,reply='UNVERIFIED RAW REPLY',**additions)
    result=workflow.process_model_turn({'from':'guest','message_id':'empty','text':'question'},None)
    assert result.text==policy.copy(key,'en')


@pytest.mark.parametrize('selector', ['handover','payment','delivery'])
@pytest.mark.parametrize('action', ['confirm_summary','new_booking','cancel'])
def test_status_faq_cannot_override_review_blocked_decision(monkeypatch, selector, action):
    intake=_queued_accessibility_review('en')
    model(monkeypatch,mermaid_action=action,status_request=selector,
          reply='UNVERIFIED',other_question_reply=_SAFE_FAQ['en'])
    result=workflow.process_model_turn({'from':'guest','message_id':'blocked','text':'question'},None)
    assert result.text==workflow.COPY['en']['human'] and result.action is None
    _assert_review_preserved(intake)


@pytest.mark.parametrize('kind', ['security','human','cancel'])
def test_dedicated_status_faq_does_not_leak_past_primary_action(monkeypatch, kind):
    changes=({'security_event':'blocked_override'} if kind=='security' else
             {'mermaid_action':'request_human'} if kind=='human' else {'mermaid_action':'cancel'})
    model(monkeypatch,status_request='handover',reply='UNVERIFIED',
          other_question_reply='SHOULD NOT APPEAR',**changes)
    result=workflow.process_model_turn({'from':'guest','message_id':'primary','text':'question'},None)
    assert 'SHOULD NOT APPEAR' not in result.text and 'UNVERIFIED' not in result.text
    if kind=='security':assert result.text==policy.copy('security_blocked','en')
    elif kind=='human':assert result.action=='human_takeover'
    else:assert result.action=='cancel' and result.text==''
