"""Four offline checks for useful recovery without extra model calls."""
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

import pytest

from agents.marina import marina_agent
from agents.social import mermaid_date_changes as changes
from agents.social import mermaid_reservation_store as store
from agents.social import mermaid_reservation_workflow as workflow
from agents.social import mermaid_response_policy as policy
from shared import config_loader, mermaid_catalog, state_registry


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config_loader, "_CONFIG_PATH", str(
        Path(__file__).resolve().parents[3] / "clients/mermaid/config/client.json"))
    monkeypatch.setattr(config_loader, "_cache", {})
    monkeypatch.setattr(state_registry, "DB_PATH", str(tmp_path / "state.db"))
    monkeypatch.setattr(state_registry, "_alert_dispatcher", None)
    monkeypatch.setattr(state_registry, "_summary_dispatcher", None)
    monkeypatch.setenv("MERMAID_DOCUMENT_ROOT", str(tmp_path / "documents"))
    monkeypatch.setattr(marina_agent.anthropic, "Anthropic", Mock(
        side_effect=AssertionError("These recovery tests must never call Anthropic")))


def future_tuesday():
    day = datetime.now(changes.LOCAL).date() + timedelta(days=7)
    return day + timedelta(days=(1 - day.weekday()) % 7)


def booked():
    fields = {
        "trip_date": future_tuesday().isoformat(), "customer_name": "Calvin",
        "contact_phone": "+12025550123", "adults": 2, "children": 0,
        "infants": 0, "pickup_preference": "pier", "language": "en",
        "phase": "summary_confirmed",
    }
    state_registry.wa_save_booking_state("guest", {"mermaid_intake": fields}, {}, [])
    reservation = store.confirm_reservation(
        "guest", fields, idempotency_key="book", zernio_account_id="account")
    for target, key in (("quote_ready", "quote"), ("demo_payment_pending", "payment")):
        reservation = store.transition(
            reservation["public_id"], target, idempotency_key=key,
            actor="test", reason="offline recovery check")
    reservation, _payment = store.complete_demo_payment(
        reservation["public_id"], payment_reference="PAY-TEST",
        idempotency_key="paid")
    return reservation


def message(text, mid="request"):
    return {"from": "guest", "_zernio_account_id": "account",
            "message_id": mid, "text": text}


def model(monkeypatch, *, fields=None, question="Is lunch included?", **overrides):
    result = {
        "language": "en", "mermaid_action": "question", "fields": fields or {},
        "reply": "Breakfast and BBQ lunch are included.", "requires_human": False,
        "has_open_question": True, "guest_question_excerpt": question,
        "calendar_request": "none", "status_request": "none", "security_event": "none",
        "other_question_topic": "food", "other_question_excerpt": question,
        "other_question_reply": "Breakfast and BBQ lunch are included.",
    }
    result.update(overrides)
    stub = Mock(return_value=result)
    monkeypatch.setattr(marina_agent, "process_message", stub)
    return stub


def assert_scheduled(alternatives, today, current_date=None):
    operating = mermaid_catalog.get_catalog()["service"]["operating_weekdays"]
    assert len(alternatives) == 2 and len(set(alternatives)) == 2
    for value in alternatives:
        day = date.fromisoformat(value)
        assert day > today
        assert policy.WEEKDAYS[day.weekday()] in operating
        assert value != current_date


def test_date_recovery_explains_reason_and_offers_readable_scheduled_choices():
    today = date(2026, 9, 4)
    current_date = "2026-09-09"
    cases = (("2026-09-10", "closed"), ("2026-08-31", "past"),
             ("2026-09-04", "today"))
    for target, reason in cases:
        recovery = policy.date_recovery(target, today=today, current_date=current_date)
        assert recovery["reason"] == reason and recovery["requested_date"] == target
        assert_scheduled(recovery["alternatives"], today, current_date)
        text = policy.date_recovery_reply(recovery, "en", current_date)
        assert target not in text and current_date not in text
        assert policy.date_label(current_date, "en") in text
        assert all(policy.date_label(value, "en") in text for value in recovery["alternatives"])
    closed = policy.date_recovery("2026-09-10", today=today, current_date=current_date)
    assert closed["alternatives"] == ["2026-09-11", "2026-09-12"]
    for locale in workflow.SUPPORTED_LOCALES:
        text = policy.date_recovery_reply(closed, locale, current_date)
        assert policy.policy()["weekdays"][locale][3] in text
        assert all(policy.date_label(value, locale) in text for value in closed["alternatives"])
    unclear = policy.date_recovery("not a date", today=today)
    assert unclear["reason"] == "unclear" and unclear["alternatives"] == []
    assert policy.date_recovery_reply(unclear, "en").strip()


def test_invalid_replacement_supersedes_old_button_and_preserves_paid_booking(monkeypatch):
    reservation = booked()
    tuesday = date.fromisoformat(reservation["intake"]["trip_date"])
    wednesday = (tuesday + timedelta(days=1)).isoformat()
    thursday = (tuesday + timedelta(days=2)).isoformat()
    proposal = changes.propose(message("Can we move to Wednesday?"), reservation, wednesday, "en")
    old_token = proposal["media"]["url"]
    reply = changes.propose(
        message("Sorry, I meant Thursday.", "replacement"), reservation, thursday, "en")
    assert reply["media"] is None and changes.pending("guest") is None
    saved = state_registry.wa_get_booking_state("guest")
    recovery = saved["flags"]["mermaid_date_recovery"]
    assert recovery["reason"] == "closed" and recovery["requested_date"] == thursday
    assert recovery["reservation_public_id"] == reservation["public_id"]
    assert_scheduled(recovery["alternatives"], policy.local_today(), tuesday.isoformat())
    assert all(policy.date_label(value, "en") in reply["text"] for value in recovery["alternatives"])
    assert policy.date_label(tuesday, "en") in reply["text"]
    stale = changes.handle_button({
        **message("Yes, change date", "old-button"),
        "_zernio_interactive_id": changes.PREFIX + old_token + ":confirm",
    })
    assert not stale.get("mermaid_delivery_commit")
    updated = store.get_reservation(reservation["public_id"])
    for key in ("intake", "monetary_snapshot", "payment_reference", "revision", "state"):
        assert updated[key] == reservation[key]
    assert not any(event["event_type"] == "date_changed" for event in store.events(reservation["public_id"]))
    chosen = recovery["alternatives"][0]
    stub = model(
        monkeypatch, fields={"trip_date": chosen}, mermaid_action="change_date",
        reply="", has_open_question=False, guest_question_excerpt="",
        other_question_topic="none", other_question_excerpt="", other_question_reply="")
    next_reply = workflow.process_model_turn(
        message("The first option works.", "choose-offered-date"), reservation).as_reply()
    assert stub.call_count == 1
    assert stub.call_args.kwargs["thread_fields"]["date_recovery"] == recovery
    assert next_reply["media"]["type"] == changes.MEDIA_TYPE
    assert next_reply["media"]["url"] != old_token
    assert not next_reply.get("mermaid_delivery_commit")
    assert changes.pending("guest")["new_date"] == chosen
    assert "mermaid_date_recovery" not in state_registry.wa_get_booking_state("guest")["flags"]
    updated = store.get_reservation(reservation["public_id"])
    assert updated["intake"] == reservation["intake"]
    assert updated["revision"] == reservation["revision"]


def test_invalid_intake_date_keeps_party_and_food_answer_with_one_model_call(monkeypatch):
    thursday = (future_tuesday() + timedelta(days=2)).isoformat()
    stub = model(monkeypatch, fields={
        "trip_date": thursday, "adults": 3, "children": 0, "infants": 0,
        "customer_name": "Calvin", "dietary_requirements": "Vegetarian meal requested",
    }, mermaid_action="details", reply="")
    result = workflow.process_model_turn(message(
        "We are 3 adults for Thursday. My name is Calvin and I would like a vegetarian meal. Is lunch included?"), None)
    saved = state_registry.wa_get_booking_state("guest")
    fields = saved["fields"]["mermaid_intake"]
    assert (fields["adults"], fields["children"], fields["infants"]) == (3, 0, 0)
    assert fields["customer_name"] == "Calvin"
    assert fields["dietary_requirements"] == "Vegetarian meal requested"
    assert "trip_date" not in fields
    assert result.text.count("Breakfast and BBQ lunch are included.") == 1
    assert "Thursday" in result.text
    recovery = saved["flags"]["mermaid_date_recovery"]
    assert recovery["requested_date"] == thursday
    assert_scheduled(recovery["alternatives"], policy.local_today())
    assert all(policy.date_label(value, "en") in result.text for value in recovery["alternatives"])
    assert state_registry.get_active_escalation_mode("guest") is None
    assert stub.call_count == 1 and not result.generation_failure


def test_food_followup_during_review_answers_question_without_repeating_queue(monkeypatch):
    reservation = booked()
    state_registry.create_pending_notification(
        "escalation", "whatsapp", "guest", "Calvin", "Review", "Review", mode="soft")
    stub = model(monkeypatch)
    result = workflow.process_model_turn(message("Is lunch included?"), reservation)
    assert result.text == "Breakfast and BBQ lunch are included."
    assert policy.copy("review_queued", "en") not in result.text
    assert state_registry.get_active_escalation_mode("guest") == "soft"
    updated = store.get_reservation(reservation["public_id"])
    assert updated["intake"] == reservation["intake"]
    assert updated["revision"] == reservation["revision"]
    assert stub.call_count == 1 and not result.generation_failure
