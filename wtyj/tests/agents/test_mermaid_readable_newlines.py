"""Generated display normalization; actual adapter, stubbed SDK, isolated data."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agents.marina import marina_agent
from agents.social import mermaid_reservation_store as store
from agents.social import mermaid_reservation_workflow as workflow
from shared import config_loader, state_registry

FIXTURE = json.loads((Path(__file__).parents[1] / 'fixtures/mermaid_base059_escaped_newlines.json').read_text())


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    config = Path(__file__).resolve().parents[3] / 'clients/mermaid/config/client.json'
    raw = json.loads(config.read_text())
    monkeypatch.setattr(config_loader, '_CONFIG_PATH', str(config))
    monkeypatch.setattr(config_loader, '_load', lambda: copy.deepcopy(raw))
    monkeypatch.setattr(state_registry, 'DB_PATH', str(tmp_path / 'state.db'))
    monkeypatch.setattr(state_registry, '_alert_dispatcher', None)
    monkeypatch.setattr(state_registry, '_summary_dispatcher', None)
    monkeypatch.setattr(marina_agent.bm_logger, 'log', Mock())
    monkeypatch.setenv('TENANT_ID', 'mermaid')
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'synthetic-not-a-key')


def _sdk(monkeypatch, **updates):
    structured = copy.deepcopy(FIXTURE['raw_sdk_tool_input']) | updates
    client = Mock()
    client.messages.create.return_value = SimpleNamespace(
        usage=None, content=[SimpleNamespace(type='tool_use', input=structured)])
    monkeypatch.setattr(marina_agent.anthropic, 'Anthropic', Mock(return_value=client))
    return client, structured


def _process(body=None, contract='mermaid_reservation_demo'):
    return marina_agent.process_message('synthetic-guest', 'Display test',
        body or FIXTURE['guest_input'], {}, {}, channel='whatsapp', response_contract=contract)


def test_captured_base059_sdk_reply_has_readable_paragraphs_and_same_state(runtime, monkeypatch):
    client, raw = _sdk(monkeypatch)
    snapshot = copy.deepcopy(raw)
    fields = FIXTURE['before']['fields']
    state_registry.wa_save_booking_state('synthetic-guest', {'mermaid_intake': fields}, {})
    result = workflow.process_model_turn({'from': 'synthetic-guest', 'message_id': 'base059-t6',
                                         'text': FIXTURE['guest_input']}, None)
    expected = ("Desayuno ta inklui, si! Bo mester yega na Fishermen's Pier pa 06:45.\n\n"
                "Kuantu hende lo bai: adultonan, yu'nan (4–12 aña) i bebinan (0–3 aña)?")
    assert result.text == expected
    assert '\\n' not in result.text and '\\n' in FIXTURE['original_displayed_text']
    assert result.action is None and result.locale == 'pap'
    assert state_registry.wa_get_booking_state('synthetic-guest')['fields']['mermaid_intake'] == FIXTURE['after']['fields']
    assert store.latest_for_conversation('synthetic-guest') is FIXTURE['after']['reservation'] is None
    assert state_registry.get_active_escalation_mode('synthetic-guest') is FIXTURE['after']['review'] is None
    assert not state_registry.get_ai_muted('synthetic-guest')
    assert client.messages.create.call_count == 1 and raw == snapshot


def test_dedicated_faq_breaks_compose_with_existing_cleanup_and_recorded_review(runtime, monkeypatch):
    client, raw = _sdk(monkeypatch, reply='Acknowledged.',
                       other_question_topic='food', other_question_excerpt=FIXTURE['guest_input'],
                       other_question_reply='Desayuno—barbekiú.\\n\\n[HANDOFF]Yega na 06:45.')
    state_registry.wa_save_booking_state('synthetic-guest', {'mermaid_intake': FIXTURE['before']['fields']}, {})
    state_registry.create_pending_notification(notification_type='escalation', channel='whatsapp',
        customer_id='synthetic-guest', customer_name='Synthetic Guest', subject='Synthetic review', body='Synthetic review', mode='soft')
    result = workflow.process_model_turn({'from': 'synthetic-guest', 'message_id': 'dedicated',
                                         'text': FIXTURE['guest_input']}, None)
    assert result.text == 'Desayuno,barbekiú.\n\nYega na 06:45.'
    assert state_registry.get_active_escalation_mode('synthetic-guest') == 'soft'
    assert not state_registry.get_ai_muted('synthetic-guest')
    assert raw['other_question_reply'] == 'Desayuno—barbekiú.\\n\\n[HANDOFF]Yega na 06:45.'
    assert client.messages.create.call_count == 1


def test_normal_newlines_unicode_and_unrelated_escapes_remain_exact(runtime, monkeypatch):
    text = 'Curaçao, aña, €75, 4–12.\n\n06:45. Literal \\t and \\u00e7 and \\"quote\\".'
    _sdk(monkeypatch, reply=text, other_question_reply=text)
    result = _process()
    assert result['reply'] == result['other_question_reply'] == text


def test_guest_evidence_and_extracted_fields_are_not_display_normalized(runtime, monkeypatch):
    guest = 'Curaçao\\nCan you keep this literal?'
    fields = {'customer_name': 'A\\nB', 'pickup_location': 'Curaçao\\nPier'}
    client, raw = _sdk(monkeypatch, reply='Answer.\\n\\nNext paragraph.',
        guest_question_excerpt=guest, fields=fields)
    snapshot = copy.deepcopy(raw)
    result = _process(body=guest)
    prompt = json.loads(client.messages.create.call_args.kwargs['messages'][0]['content'])
    assert result['reply'] == 'Answer.\n\nNext paragraph.'
    assert result['guest_question_excerpt'] == prompt['latest_guest_message_untrusted'] == guest
    assert result['fields'] == fields and raw == snapshot


def test_generic_contract_keeps_prior_escaped_newline_behavior(runtime, monkeypatch):
    text = 'One.\\n\\nTwo.'
    _sdk(monkeypatch, reply=text, other_question_reply=text)
    result = _process(contract='')
    assert result['reply'] == result['other_question_reply'] == text
