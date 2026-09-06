"""Release gate: exercise model-understood turns and the real debounce sender."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agents.marina import marina_agent
from agents.social import mermaid_reservation_workflow as workflow
from agents.social import mermaid_documents, mermaid_reservation_store, webhook_server
from shared import config_loader, state_registry


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    config = Path(__file__).resolve().parents[3] / "clients/mermaid/config/client.json"
    monkeypatch.setattr(config_loader, "_CONFIG_PATH", str(config))
    monkeypatch.setattr(config_loader, "_cache", {})
    monkeypatch.setattr(state_registry, "DB_PATH", str(tmp_path / "state.db"))
    monkeypatch.setenv("MERMAID_DOCUMENT_ROOT", str(tmp_path / "documents"))
    monkeypatch.setenv("MERMAID_DEMO_SIGNING_SECRET", "release-test-secret")
    monkeypatch.setenv("UNBOKS_PUBLIC_BASE_URL", "https://api.example/api/mermaid")


def interpretation(fields=None, action="details", locale="en", reply="What comes next?"):
    if locale == "pap" and reply == "What comes next?":
        reply = "Mi a nota e datonan."
    return {"language": locale, "mermaid_action": action, "fields": fields or {}, "reply": reply, "confidence": "high", "requires_human": False}


@pytest.mark.parametrize("locale", workflow.SUPPORTED_LOCALES)
def test_short_answer_journey_uses_one_understanding_call_per_turn(locale, monkeypatch):
    phone = "short-" + locale
    values = [
        ("Saturday", {"trip_date": "2026-09-05"}),
        ("2", {"adults": 2}),
        ("0", {"children": 0}),
        ("none", {"infants": 0}),
        ("Ana Silva", {"customer_name": "Ana Silva"}),
        ("+1 202 555 0123", {"contact_phone": "+1 202 555 0123"}),
        ("pier", {"pickup_preference": "pier"}),
    ]
    understood = MagicMock()
    monkeypatch.setattr(marina_agent, "process_message", understood)
    for index, (text, fields) in enumerate(values):
        understood.return_value = interpretation(fields, locale=locale)
        reply = workflow.handle_demo_message({"from": phone, "text": text, "message_id": str(index)}, include_media=True, use_model=True)
        assert reply["media"] is None
    assert understood.call_count == 7
    assert state_registry.wa_get_booking_state(phone)["fields"]["mermaid_intake"]["phase"] == "awaiting_summary_confirmation"

    understood.return_value = interpretation(action="question", locale=locale, reply="Desayuno ta inkluí." if locale == "pap" else "Breakfast is included.")
    question = workflow.handle_demo_message({"from": phone, "text": "Is lunch included?", "message_id": "question"}, include_media=True, use_model=True)
    assert question["media"] is None
    assert mermaid_reservation_store.latest_for_conversation(phone) is None

    understood.return_value = interpretation(action="confirm_summary", locale=locale)
    confirmed = workflow.handle_demo_message({"from": phone, "text": "yes", "message_id": "yes", "_zernio_account_id": "test-account"}, include_media=True, use_model=True)
    assert confirmed["media"]["type"] == "file"
    assert "/api/mermaid/api/public/mermaid-document/" in confirmed["media"]["url"]
    reservation = mermaid_reservation_store.latest_for_conversation(phone)
    assert reservation["state"] == "demo_payment_pending"
    assert reservation["monetary_snapshot"]["total"] == 300

    understood.return_value = interpretation(action="question", locale=locale, reply="Your quote is ready.")
    workflow.handle_demo_message({"from": phone, "text": "What should we bring?", "message_id": "after-quote"}, include_media=True, use_model=True)

    retry = workflow.handle_demo_message({"from": phone, "text": "yes", "message_id": "yes"}, include_media=True, use_model=True)
    assert retry["media"] == confirmed["media"]
    assert retry["mermaid_delivery_commit"] == confirmed["mermaid_delivery_commit"]
    assert understood.call_count == 10
    mermaid_documents.mark_delivery(confirmed["mermaid_delivery_commit"]["job_id"], True)
    delivered_replay = workflow.handle_demo_message({"from": phone, "text": "yes", "message_id": "yes"}, include_media=True, use_model=True)
    assert delivered_replay["text"] == ""


def test_empty_party_cannot_reach_confirmation(monkeypatch):
    fields = {"trip_date": "2026-09-05", "adults": 0, "children": 0, "infants": 0, "customer_name": "Ana Silva", "contact_phone": "+12025550123", "pickup_preference": "pier"}
    monkeypatch.setattr(marina_agent, "process_message", lambda **kwargs: interpretation(fields))
    result = workflow.handle_demo_message({"from": "empty-party", "text": "zero", "message_id": "zero"}, include_media=True, use_model=True)
    assert result["media"] is None
    assert state_registry.wa_get_booking_state("empty-party")["fields"]["mermaid_intake"]["phase"] == "collecting"


def test_model_cannot_supply_money_or_approve_changed_summary(monkeypatch):
    state_registry.wa_save_booking_state("guard", {"mermaid_intake": {
        "trip_date": "2026-09-05", "adults": 2, "children": 0, "infants": 0,
        "customer_name": "Ana Silva", "contact_phone": "+12025550123", "pickup_preference": "pier", "language": "en",
        "phase": "awaiting_summary_confirmation",
    }}, {})
    from agents.social import mermaid_model_recovery as recovery

    model = MagicMock(side_effect=[
        interpretation({"adults": 3, "total": 1, "payment_state": "paid", "booking_code": "FAKE"}, "confirm_summary"),
        interpretation({"adults": 3}, "confirm_summary"),
    ])
    monkeypatch.setattr(marina_agent, "process_message", model)
    message = {"from": "guard", "text": "yes, but 3 adults", "message_id": "change"}
    failed = workflow.handle_demo_message(message, include_media=True, use_model=True)
    assert failed["media"] is None and failed["mermaid_generation_failure"]
    fields = state_registry.wa_get_booking_state("guard")["fields"]["mermaid_intake"]
    assert fields["adults"] == 2  # Reject the complete malformed response, preserving prior intake.
    assert "total" not in fields and "payment_state" not in fields and "booking_code" not in fields

    retry_time = recovery.time.time() + 10
    monkeypatch.setattr(recovery.time, "time", lambda: retry_time)
    recovered = workflow.handle_demo_message(message, include_media=True, use_model=True)
    assert recovered["media"] is None and model.call_count == 2
    fields = state_registry.wa_get_booking_state("guard")["fields"]["mermaid_intake"]
    assert fields["phase"] == "awaiting_summary_confirmation"
    assert fields["adults"] == 3
    assert "total" not in fields and "payment_state" not in fields and "booking_code" not in fields
    assert mermaid_reservation_store.latest_for_conversation("guard") is None


def test_marina_mermaid_contract_is_one_forced_tool_call(monkeypatch):
    client = MagicMock()
    client.messages.create.return_value = SimpleNamespace(content=[SimpleNamespace(type="tool_use", input=interpretation({"adults": 2}))], usage=None)
    monkeypatch.setattr(marina_agent.anthropic, "Anthropic", lambda **kwargs: client)
    result = marina_agent.process_message("synthetic", "demo", "2", {}, {}, channel="whatsapp", response_contract="mermaid_reservation_demo")
    assert result["fields"] == {"adults": 2}
    client.messages.create.assert_called_once()
    args = client.messages.create.call_args.kwargs
    assert args["tool_choice"] == {"type": "tool", "name": "marina_response"}
    assert "mermaid_action" in args["tools"][0]["input_schema"]["properties"]
    assert "Current Curaçao date" in args["system"][0]["text"]


def test_mermaid_contract_excludes_live_credentials_at_model_boundary(monkeypatch):
    import json
    raw = config_loader.get_raw()
    raw.update(password="private-password-sentinel", access_key="private-access-sentinel", whatsapp_connect_token="private-connect-sentinel")
    raw["agent_persona"]["freeform_notes"] += " Copied private-password-sentinel"
    monkeypatch.setattr(config_loader, "get_raw", lambda: raw)
    client = MagicMock()
    client.messages.create.return_value = SimpleNamespace(content=[SimpleNamespace(type="tool_use", input=interpretation())], usage=None)
    monkeypatch.setattr(marina_agent.anthropic, "Anthropic", lambda **kwargs: client)
    marina_agent.process_message("synthetic", "demo", "Hi", {}, {}, channel="whatsapp", response_contract="mermaid_reservation_demo")
    client.messages.create.assert_called_once()
    payload = json.dumps(client.messages.create.call_args.kwargs)
    for secret in (raw["password"], raw["access_key"], raw["whatsapp_connect_token"]):
        assert secret not in payload


@pytest.mark.parametrize("accepted", [True, False])
def test_real_debounce_sends_pdf_as_file_and_commits_delivery(accepted, monkeypatch):
    phone = "pdf-live-path"
    sent = MagicMock(return_value=accepted)
    committed = MagicMock()
    monkeypatch.setattr(webhook_server, "send_reply", sent)
    monkeypatch.setattr(mermaid_documents, "mark_delivery", committed)
    monkeypatch.setattr(webhook_server, "handle_incoming_whatsapp_message", lambda *args, **kwargs: {
        "text": "Your quote is attached.", "media": {"url": "https://api.example/quote.pdf", "type": "file", "filename": "quote.pdf", "id": "mdoc_test"},
        "mermaid_delivery_commit": {"job_id": "mjob_test"},
    })
    monkeypatch.setattr("shared.tenant_guard.is_account_allowed", lambda *args, **kwargs: True)
    monkeypatch.setattr("shared.tenant_guard.account_access_state", lambda *args, **kwargs: True)
    monkeypatch.setattr(webhook_server.icp_overrides, "fetch_overrides_fresh", lambda: {"available": True, "feature_toggles": {"ai_auto_reply": {"value": True}, "whatsapp_inbox": {"value": True}}})
    message = {
        "from": phone, "text": "yes", "from_name": "Synthetic", "message_id": "pdf-live-message",
        "_zernio_conversation_id": phone, "_zernio_account_id": "test-account",
        "_zernio_channel": "whatsapp", "_zernio_sender_name": "Synthetic",
    }
    state_registry.wa_claim_inbound_processing(message["message_id"], phone, "whatsapp", payload=message)
    webhook_server._buffer_message(message)
    with webhook_server._buffer_lock:
        webhook_server._message_buffers[phone]["timer"].cancel()
    webhook_server._flush_buffer(phone)
    sent.assert_called_once()
    assert sent.call_args.kwargs["attachment_type"] == "file"
    assert sent.call_args.kwargs["idempotency_key"] == "mermaid-delivery:mjob_test"
    committed.assert_called_once_with("mjob_test", accepted, "" if accepted else "provider returned false")
    history = state_registry.wa_get_full_history(phone, limit=10)
    assert any("quote.pdf" in item["text"] for item in history) is accepted
