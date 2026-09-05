"""Regression coverage for Mermaid's ordinary wheelchair-assistance path."""

from pathlib import Path
from unittest.mock import Mock

import pytest

from agents.marina import marina_agent
from agents.social import mermaid_crew_assistance as assistance
from agents.social import mermaid_reservation_store as reservation_store
from agents.social import mermaid_reservation_workflow as workflow
from agents.social import mermaid_understanding
from shared import config_loader, state_registry


PAP_WHEELCHAIR_ACK = (
    "Sí, no tin problema. Nos ta prepará pa risibí bishitantenan ku ta usa stul di rueda. "
    "Mi a registrá un nota pa e tripulashon por prepará pa duna asistensia."
)


@pytest.fixture(autouse=True)
def isolated_mermaid(tmp_path, monkeypatch):
    config = Path(__file__).resolve().parents[3] / "clients/mermaid/config/client.json"
    monkeypatch.setattr(config_loader, "_CONFIG_PATH", str(config))
    monkeypatch.setattr(config_loader, "_cache", {})
    monkeypatch.setattr(state_registry, "DB_PATH", str(tmp_path / "state.db"))
    monkeypatch.setattr(state_registry, "_alert_dispatcher", None)
    monkeypatch.setattr(state_registry, "_summary_dispatcher", None)


def _wheelchair_result(locale="pap", **overrides):
    result = {
        "language": locale,
        "mermaid_action": "details",
        "fields": {
            "trip_date": "2026-09-06",
            "adults": 3,
            "children": 1,
            "infants": 1,
            "child_ages": [
                {"value": 9, "unit": "years"},
                {"value": 11, "unit": "months"},
            ],
            "accessibility_notes": "The guest's husband uses a wheelchair.",
            "wheelchair_relationship": "husband",
        },
        # The server must replace both this prose and this obsolete review flag.
        "reply": "The team must approve this request before booking can continue.",
        "confidence": "high",
        "requires_human": True,
        "has_open_question": True,
        "guest_question_excerpt": "Boso por yuda ku e stul?",
        "calendar_request": "none",
        "status_request": "none",
        "assistance_request": "wheelchair_note",
        "security_event": "none",
        "other_question_reply": "",
    }
    result.update(overrides)
    return result


def _pending_notification_count():
    conn = state_registry._get_conn()
    try:
        return conn.execute("SELECT COUNT(*) FROM pending_notifications").fetchone()[0]
    finally:
        conn.close()


@pytest.mark.parametrize(
    "locale,message",
    [
        ("en", "My husband uses a wheelchair."),
        ("nl", "Mijn man gebruikt een rolstoel."),
        ("de", "Mein Mann benutzt einen Rollstuhl."),
        ("es", "Mi esposo usa una silla de ruedas."),
        ("pap", "Mi kasá ta usa stul di rueda. Boso por yuda ku e stul?"),
        ("pt", "Meu marido usa uma cadeira de rodas."),
    ],
)
def test_ordinary_wheelchair_path_is_visible_and_non_escalating_in_every_locale(
    monkeypatch, locale, message
):
    phone = f"guest-wheelchair-{locale}"
    monkeypatch.setattr(
        marina_agent,
        "process_message",
        Mock(
            return_value=_wheelchair_result(
                locale=locale,
                fields={
                    "accessibility_notes": "A differently worded model note.",
                    "wheelchair_relationship": "husband",
                },
            )
        ),
    )

    result = workflow.process_model_turn(
        {
            "from": phone,
            "from_name": "Synthetic Guest",
            "message_id": f"wheelchair-{locale}-1",
            "text": message,
        },
        None,
    )

    assert workflow.WHEELCHAIR_COPY[locale] in result.text
    assert result.action is None
    assert result.phase == "collecting"
    assert assistance.for_conversation(phone)["note"] == (
        "The guest's husband uses a wheelchair."
    )
    assert state_registry.get_active_escalation_mode(phone) is None
    assert state_registry.get_ai_muted(phone) is False
    assert _pending_notification_count() == 0


def test_formal_papiamentu_wheelchair_reply_saves_note_and_continues_booking(monkeypatch):
    stub = Mock(return_value=_wheelchair_result())
    monkeypatch.setattr(marina_agent, "process_message", stub)

    result = workflow.process_model_turn(
        {
            "from": "guest-pap-wheelchair",
            "from_name": "Synthetic Guest",
            "message_id": "wheelchair-1",
            "text": "Mi kasá ta usa stul di rueda. Boso por yuda ku e stul?",
        },
        None,
    )

    assert workflow.WHEELCHAIR_COPY["pap"] == PAP_WHEELCHAIR_ACK
    assert all(
        term in workflow.WHEELCHAIR_COPY["pap"]
        for term in ("Sí", "risibí", "bishitantenan", "stul di rueda", "tripulashon")
    )
    assert "mas ku bon biní" not in workflow.WHEELCHAIR_COPY["pap"]
    assert "*SÍ*" in workflow.COPY["pap"]["confirm"]
    assert "sí" in workflow.YES["pap"]
    assert result.text == "\n\n".join(
        (
            workflow.WELCOME_COPY["pap"],
            PAP_WHEELCHAIR_ACK,
            "Mi a registrá boso grupo: 3 adulto · 1 mucha (9 aña) · 1 bebé (11 luna).",
            workflow.COPY["pap"]["name"],
        )
    )
    assert "aprob" not in result.text.casefold()
    assert result.action is None
    assert result.phase == "collecting"

    state = state_registry.wa_get_booking_state("guest-pap-wheelchair")
    intake = state["fields"]["mermaid_intake"]
    assert intake["trip_date"] == "2026-09-06"
    assert (intake["adults"], intake["children"], intake["infants"]) == (3, 1, 1)
    assert intake["child_ages"] == [
        {"value": 9, "unit": "years"},
        {"value": 11, "unit": "months"},
    ]
    assert intake["wheelchair_relationship"] == "husband"

    item = assistance.for_conversation("guest-pap-wheelchair")
    assert item is not None
    assert item["status"] == "unacknowledged"
    assert item["note"] == "The guest's husband uses a wheelchair."
    assert item["tripDate"] == "2026-09-06"
    assert item["customerName"] == "Synthetic Guest"
    assert state_registry.get_active_escalation_mode("guest-pap-wheelchair") is None
    assert state_registry.get_ai_muted("guest-pap-wheelchair") is False
    assert _pending_notification_count() == 0

    stub.return_value = _wheelchair_result(
        assistance_request="none",
        requires_human=False,
        has_open_question=False,
        guest_question_excerpt="",
        reply="Korekto.",
        fields={
            "customer_name": "Synthetic Guest",
            "contact_phone": "+599 9 000 0000",
            "pickup_preference": "pier",
        },
    )
    summary = workflow.process_model_turn(
        {
            "from": "guest-pap-wheelchair",
            "from_name": "Synthetic Guest",
            "message_id": "details-2",
            "text": "E reservashon ta na nòmber di Synthetic Guest. Mi number ta +599 9 000 0000. Nos ta bini e pier.",
        },
        None,
    )
    assert summary.phase == "awaiting_summary_confirmation"
    assert summary.action is None
    assert workflow.SUMMARY_COPY["pap"]["title"] in summary.text
    assert state_registry.get_active_escalation_mode("guest-pap-wheelchair") is None
    assert state_registry.get_ai_muted("guest-pap-wheelchair") is False
    assert _pending_notification_count() == 0
    assert assistance.for_conversation("guest-pap-wheelchair")["customerName"] == "Synthetic Guest"


def test_wheelchair_note_deduplicates_provider_replay_and_semantic_repeat(monkeypatch):
    stub = Mock(return_value=_wheelchair_result(locale="en"))
    monkeypatch.setattr(marina_agent, "process_message", stub)
    message = {
        "from": "guest-wheelchair-replay",
        "message_id": "provider-1",
        "text": "My husband uses a wheelchair. Can you help with the wheelchair?",
    }

    first = workflow.process_model_turn(message, None)
    replay = workflow.process_model_turn(message, None)
    repeated = workflow.process_model_turn(
        {**message, "message_id": "provider-2"}, None
    )

    assert workflow.WHEELCHAIR_COPY["en"] in first.text
    assert replay.duplicate is True
    assert replay.text == ""
    assert workflow.WHEELCHAIR_COPY["en"] in repeated.text
    assert stub.call_count == 2
    items = assistance.list_items()
    assert len(items) == 1
    assert items[0]["revision"] == 1
    assert [event["event_type"] for event in assistance.events(items[0]["id"])] == [
        "created"
    ]
    assert _pending_notification_count() == 0


@pytest.mark.parametrize(
    "prior_role,expects_welcome",
    [("user", True), ("assistant", False), ("operator", False)],
)
def test_welcome_uses_visible_assistant_or_operator_history(
    monkeypatch, prior_role, expects_welcome
):
    phone = f"guest-history-{prior_role}"
    state_registry.dm_store_message(
        phone, "whatsapp", prior_role, "Earlier visible message", sender_name="Synthetic"
    )
    monkeypatch.setattr(
        marina_agent,
        "process_message",
        Mock(
            return_value=_wheelchair_result(
                fields={
                    "accessibility_notes": "A guest uses a wheelchair.",
                    "wheelchair_relationship": "unspecified",
                }
            )
        ),
    )

    result = workflow.process_model_turn(
        {
            "from": phone,
            "message_id": "wheelchair-history",
            "text": "Un hende den nos grupo ta usa stul di rueda. Boso por yuda ku e stul?",
        },
        None,
    )

    assert (workflow.WELCOME_COPY["pap"] in result.text) is expects_welcome
    assert PAP_WHEELCHAIR_ACK in result.text
    assert result.text.endswith(workflow.COPY["pap"]["trip_date"])


def test_saved_claim_is_not_returned_until_private_note_write_succeeds(monkeypatch):
    phone = "guest-wheelchair-write-failure"
    stub = Mock(return_value=_wheelchair_result())
    monkeypatch.setattr(marina_agent, "process_message", stub)
    original_record = assistance.record_wheelchair_note
    monkeypatch.setattr(
        assistance,
        "record_wheelchair_note",
        Mock(side_effect=RuntimeError("simulated durable-write failure")),
    )
    message = {
        "from": phone,
        "message_id": "provider-failure-1",
        "text": "Mi kasá ta usa stul di rueda. Boso por yuda ku e stul?",
    }

    with pytest.raises(RuntimeError, match="durable-write failure"):
        workflow.process_model_turn(message, None)

    state = state_registry.wa_get_booking_state(phone)
    assert "provider-failure-1" not in state["flags"].get(
        "mermaid_seen_message_ids", []
    )
    assert assistance.for_conversation(phone) is None
    assert _pending_notification_count() == 0

    monkeypatch.setattr(assistance, "record_wheelchair_note", original_record)
    retry = workflow.process_model_turn(message, None)
    assert retry.text.startswith(workflow.WELCOME_COPY["pap"])
    assert PAP_WHEELCHAIR_ACK in retry.text
    assert assistance.for_conversation(phone) is not None
    assert "provider-failure-1" in state_registry.wa_get_booking_state(phone)["flags"][
        "mermaid_seen_message_ids"
    ]
    # The generated understanding is replayed from the durable model cache.
    assert stub.call_count == 1


def test_specific_unconfirmed_wheelchair_equipment_still_requires_review(monkeypatch):
    monkeypatch.setattr(
        marina_agent,
        "process_message",
        Mock(
            return_value=_wheelchair_result(
                mermaid_action="question",
                assistance_request="other_review",
                requires_human=False,
                fields={},
                reply="I need Mermaid's team to confirm whether a lift is available.",
            )
        ),
    )

    result = workflow.process_model_turn(
        {
            "from": "guest-wheelchair-equipment",
            "from_name": "Synthetic Guest",
            "message_id": "wheelchair-lift-1",
            "text": "Do you have a wheelchair lift and can you guarantee the transfer?",
        },
        None,
    )

    assert result.action == "human_takeover"
    assert result.phase == "human_takeover"
    assert assistance.for_conversation("guest-wheelchair-equipment") is None
    assert _pending_notification_count() == 1


@pytest.mark.parametrize(
    "message",
    [
        "Do you have a wheelchair we can borrow?",
        "Can your crew lift him onto the boat in his wheelchair?",
        "Can you guarantee that his wheelchair will fit aboard?",
        "My husband uses a wheelchair and carries an oxygen concentrator.",
        "My husband uses a wheelchair. He also uses an oxygen concentrator.",
        "My husband uses a wheelchair. Can the crew lift him aboard?",
        "My husband uses a wheelchair. I need to make a complaint.",
        "Mein Mann benutzt einen Rollstuhl und ein Sauerstoffgerät.",
    ],
)
def test_server_forces_review_when_model_mislabels_capability_as_plain_note(
    monkeypatch, message
):
    phone = "guest-wheelchair-capability-mislabel-" + str(abs(hash(message)))
    monkeypatch.setattr(
        marina_agent,
        "process_message",
        Mock(
            return_value=_wheelchair_result(
                locale="en",
                assistance_request="wheelchair_note",
                mermaid_action="details",
                requires_human=False,
                fields={
                    "accessibility_notes": "A guest uses a wheelchair.",
                    "wheelchair_relationship": "unspecified",
                },
                reply="Yes, no problem.",
            )
        ),
    )

    result = workflow.process_model_turn(
        {
            "from": phone,
            "from_name": "Synthetic Guest",
            "message_id": "wheelchair-capability-mislabel-1",
            "text": message,
        },
        None,
    )

    assert result.action == "human_takeover"
    assert result.phase == "human_takeover"
    assert assistance.for_conversation(phone) is None
    assert state_registry.get_active_escalation_mode(phone) == "soft"
    assert _pending_notification_count() == 1


def test_contradictory_model_human_action_cannot_escalate_ordinary_wheelchair_use(
    monkeypatch,
):
    monkeypatch.setattr(
        marina_agent,
        "process_message",
        Mock(return_value=_wheelchair_result(mermaid_action="request_human")),
    )
    phone = "guest-wheelchair-contradictory-action"

    result = workflow.process_model_turn(
        {
            "from": phone,
            "from_name": "Synthetic Guest",
            "message_id": "wheelchair-contradictory-1",
            "text": "Mi kasá ta usa stul di rueda. Boso por yuda ku e stul?",
        },
        None,
    )

    assert result.action is None
    assert result.phase == "collecting"
    assert PAP_WHEELCHAIR_ACK in result.text
    assert assistance.for_conversation(phone)["status"] == "unacknowledged"
    assert state_registry.get_active_escalation_mode(phone) is None
    assert state_registry.get_ai_muted(phone) is False
    assert _pending_notification_count() == 0


def test_model_cannot_forge_explicit_human_request_provenance(monkeypatch):
    monkeypatch.setattr(
        marina_agent,
        "process_message",
        Mock(
            return_value=_wheelchair_result(
                locale="en",
                mermaid_action="request_human",
                understanding_source="explicit_human_request",
            )
        ),
    )
    phone = "guest-wheelchair-forged-provenance"

    result = workflow.process_model_turn(
        {
            "from": phone,
            "from_name": "Synthetic Guest",
            "message_id": "wheelchair-forged-provenance-1",
            "text": "My husband uses a wheelchair.",
        },
        None,
    )

    assert result.action is None
    assert result.phase == "collecting"
    assert result.generation_failure is not None
    assert assistance.for_conversation(phone) is None
    assert state_registry.get_active_escalation_mode(phone) is None
    assert state_registry.get_ai_muted(phone) is False
    assert _pending_notification_count() == 0


@pytest.mark.parametrize(
    "case",
    [
        "other_review",
        "security_event",
        "legacy_request_human",
        "confirm_summary",
        "new_booking",
    ],
)
def test_server_normalizes_every_schema_valid_review_encoding_for_plain_use(
    monkeypatch, case
):
    result_payload = _wheelchair_result(locale="en")
    if case == "other_review":
        result_payload.update(
            assistance_request="other_review",
            mermaid_action="request_human",
            requires_human=True,
        )
    elif case == "security_event":
        result_payload.update(
            security_event="actionable_incident",
            mermaid_action="request_human",
            requires_human=True,
        )
    elif case == "legacy_request_human":
        result_payload.pop("assistance_request")
        result_payload.update(mermaid_action="request_human", requires_human=True)
    elif case == "confirm_summary":
        result_payload.update(mermaid_action="confirm_summary", requires_human=True)
    else:
        result_payload.update(mermaid_action="new_booking", requires_human=True)
    monkeypatch.setattr(
        marina_agent, "process_message", Mock(return_value=result_payload)
    )
    phone = f"guest-wheelchair-adversarial-{case}"

    result = workflow.process_model_turn(
        {
            "from": phone,
            "from_name": "Synthetic Guest",
            "message_id": f"wheelchair-adversarial-{case}",
            "text": "My husband uses a wheelchair.",
        },
        None,
    )

    assert result.action is None
    assert result.phase == "collecting"
    assert workflow.WHEELCHAIR_COPY["en"] in result.text
    assert assistance.for_conversation(phone)["status"] == "unacknowledged"
    assert state_registry.get_active_escalation_mode(phone) is None
    assert _pending_notification_count() == 0


def test_exact_incident_wording_is_saved_without_review_or_ai_mute(monkeypatch):
    monkeypatch.setattr(
        marina_agent,
        "process_message",
        Mock(
            return_value=_wheelchair_result(
                locale="en",
                assistance_request="other_review",
                mermaid_action="request_human",
                requires_human=True,
                fields={},
            )
        ),
    )
    phone = "guest-exact-incident-wording"

    result = workflow.process_model_turn(
        {
            "from": phone,
            "from_name": "Synthetic Guest",
            "message_id": "exact-incident-1",
            "text": "myhusband is handicapped and need special attention on and off board, can u help?",
        },
        None,
    )

    assert result.action is None
    assert workflow.BOARDING_ASSISTANCE_COPY["en"] in result.text
    item = assistance.for_conversation(phone)
    assert item["kind"] == "boarding_assistance"
    assert item["relationship"] == "husband"
    assert item["note"] == (
        "The guest's husband requested extra assistance when boarding "
        "and disembarking."
    )
    intake = state_registry.wa_get_booking_state(phone)["fields"]["mermaid_intake"]
    assert "accessibility_notes" not in intake
    assert "wheelchair_relationship" not in intake
    assert state_registry.get_active_escalation_mode(phone) is None
    assert state_registry.get_ai_muted(phone) is False
    assert _pending_notification_count() == 0


def test_mixed_explicit_person_request_wins_even_when_model_returns_details(
    monkeypatch,
):
    monkeypatch.setattr(
        marina_agent,
        "process_message",
        Mock(
            return_value=_wheelchair_result(
                locale="en", mermaid_action="details", requires_human=False
            )
        ),
    )
    phone = "guest-wheelchair-explicit-person"

    result = workflow.process_model_turn(
        {
            "from": phone,
            "from_name": "Synthetic Guest",
            "message_id": "wheelchair-person-1",
            "text": "My husband uses a wheelchair; connect me with the team.",
        },
        None,
    )

    assert result.action == "human_takeover"
    assert result.phase == "human_takeover"
    assert assistance.for_conversation(phone)["status"] == "unacknowledged"
    assert state_registry.get_active_escalation_mode(phone) == "soft"
    assert _pending_notification_count() == 1


def test_no_existing_wheelchair_note_gets_truthful_nonempty_acknowledgement(
    monkeypatch,
):
    phone = "guest-no-existing-wheelchair-note"
    monkeypatch.setattr(
        marina_agent,
        "process_message",
        Mock(
            return_value=_wheelchair_result(
                locale="en",
                assistance_request="wheelchair_note",
                mermaid_action="request_human",
                requires_human=True,
                fields={},
            )
        ),
    )

    result = workflow.process_model_turn(
        {
            "from": phone,
            "from_name": "Synthetic Guest",
            "message_id": "no-note-correction",
            "text": "My husband does not use a wheelchair.",
        },
        None,
    )

    assert result.text
    assert workflow.NO_WHEELCHAIR_NOTE_COPY["en"] in result.text
    assert assistance.for_conversation(phone) is None
    assert state_registry.get_active_escalation_mode(phone) is None
    assert state_registry.get_ai_muted(phone) is False


@pytest.mark.parametrize(
    "message",
    [
        "Do you have a wheelchair we can borrow?",
        "Can Mermaid provide a wheelchair?",
        "Is there a wheelchair available?",
        "My husband uses a wheelchair; can your crew lift him onto the boat?",
        "Do you have room for it on the boat?",
        "My husband uses a wheelchair and has a severe peanut allergy.",
        "My husband does not use an electric wheelchair; he uses a normal foldable wheelchair.",
        "There is no wheelchair ramp, but my husband uses a wheelchair.",
        "I need a wheelchair.",
    ],
)
def test_deterministic_plain_use_guard_rejects_capability_and_mixed_intents(message):
    assert workflow._ordinary_wheelchair_message(message) is False


@pytest.mark.parametrize(
    "message,is_withdrawal",
    [
        ("My daughter does not use a wheelchair.", True),
        ("One guest no longer uses a wheelchair.", True),
        ("Our guest is not a wheelchair user.", True),
        ("What is your wheelchair policy?", False),
        ("I saw a wheelchair on the beach.", False),
    ],
)
def test_negated_and_informational_wheelchair_mentions_are_not_ordinary_notes(
    message, is_withdrawal
):
    assert workflow._wheelchair_withdrawal_message(message) is is_withdrawal
    assert workflow._ordinary_wheelchair_message(message) is False
    assert workflow._wheelchair_capability_requires_review(message) is False


@pytest.mark.parametrize(
    "message,assistance_request",
    [
        ("What is your wheelchair policy?", "wheelchair_note"),
        ("I saw a wheelchair on the beach.", "other_review"),
    ],
)
def test_model_cannot_turn_neutral_wheelchair_mentions_into_notes_or_reviews(
    monkeypatch, message, assistance_request
):
    phone = "guest-neutral-wheelchair-" + str(abs(hash(message)))
    monkeypatch.setattr(
        marina_agent,
        "process_message",
        Mock(
            return_value=_wheelchair_result(
                locale="en",
                mermaid_action="request_human",
                assistance_request=assistance_request,
                requires_human=True,
                security_event="actionable_incident",
                fields={
                    "accessibility_notes": "A guest uses a wheelchair.",
                    "wheelchair_relationship": "unspecified",
                    "special_requests": "Wheelchair assistance requested.",
                },
                reply="Here is the general information you requested.",
            )
        ),
    )

    result = workflow.process_model_turn(
        {
            "from": phone,
            "from_name": "Synthetic Guest",
            "message_id": "neutral-wheelchair-1",
            "text": message,
        },
        None,
    )

    assert result.action is None
    assert assistance.for_conversation(phone) is None
    intake = state_registry.wa_get_booking_state(phone)["fields"]["mermaid_intake"]
    assert "accessibility_notes" not in intake
    assert "wheelchair_relationship" not in intake
    assert "special_requests" not in intake
    assert state_registry.get_active_escalation_mode(phone) is None
    assert state_registry.get_ai_muted(phone) is False
    assert _pending_notification_count() == 0


@pytest.mark.parametrize(
    "message",
    [
        "My husband uses a wheelchair. Can we bring towels aboard?",
        "My husband uses a wheelchair. Can I bring my own mask aboard?",
        "My husband uses a wheelchair. Is lunch included?",
        "My husband uses a wheelchair. Can we buy beer aboard?",
    ],
)
def test_unrelated_faq_after_wheelchair_note_does_not_trigger_review(message):
    assert workflow._ordinary_wheelchair_message(message) is True
    assert workflow._wheelchair_capability_requires_review(message) is False


@pytest.mark.parametrize(
    "message",
    [
        "Cancel my reservation; my husband uses a wheelchair.",
        "Please make another booking. My husband uses a wheelchair.",
        "Annuleer mijn reservering; mijn man gebruikt een rolstoel.",
        "Stornieren Sie meine Reservierung; mein Mann benutzt einen Rollstuhl.",
        "Cancela mi reserva; mi esposo usa una silla de ruedas.",
        "Kanselá mi reservashon; mi kasá ta usa stul di rueda.",
        "Cancele minha reserva; meu marido usa uma cadeira de rodas.",
    ],
)
def test_independent_reservation_action_is_not_misclassified_as_capability_review(message):
    assert workflow._ordinary_wheelchair_message(message) is True
    assert workflow._wheelchair_capability_requires_review(message) is False


@pytest.mark.parametrize(
    "message",
    [
        "Do you have a wheel chair we can borrow?",
        "Hebben jullie een rolstoel?",
        "Habt ihr einen Rollstuhl?",
        "¿Tienen una silla de ruedas?",
        "Tin stul di rueda pa nos usa?",
        "Vocês têm cadeira de rodas?",
    ],
)
def test_wheelchair_equipment_availability_requires_review_in_every_locale(message):
    assert workflow._ordinary_wheelchair_message(message) is False
    assert workflow._wheelchair_capability_requires_review(message) is True


@pytest.mark.parametrize(
    "action,message",
    [
        ("cancel", "Cancel my reservation; my husband uses a wheelchair."),
        ("new_booking", "Please make another booking. My husband uses a wheelchair."),
    ],
)
def test_independent_cancel_or_new_booking_is_preserved_without_wheelchair_escalation(
    monkeypatch, action, message
):
    phone = f"guest-independent-{action}"
    monkeypatch.setattr(
        marina_agent,
        "process_message",
        Mock(
            return_value=_wheelchair_result(
                locale="en",
                mermaid_action=action,
                requires_human=True,
            )
        ),
    )

    result = workflow.process_model_turn(
        {
            "from": phone,
            "from_name": "Synthetic Guest",
            "message_id": f"independent-{action}",
            "text": message,
        },
        None,
    )

    assert result.action != "human_takeover"
    assert state_registry.get_active_escalation_mode(phone) is None
    assert state_registry.get_ai_muted(phone) is False
    assert _pending_notification_count() == 0
    if action == "cancel":
        assert result.action == "cancel"
    else:
        assert assistance.for_conversation(phone)["status"] == "unacknowledged"


def test_new_booking_wheelchair_note_does_not_attach_to_old_terminal_trip(
    monkeypatch,
):
    phone = "guest-new-booking-wheelchair-link"
    old_intake = {
        "trip_date": "2026-09-06",
        "adults": 2,
        "children": 0,
        "infants": 0,
        "customer_name": "Synthetic Guest",
        "contact_phone": "+5999000000",
        "pickup_preference": "pier",
        "language": "en",
        "phase": "summary_confirmed",
    }
    old_reservation = reservation_store.confirm_reservation(
        phone, old_intake, idempotency_key="old-reservation"
    )
    reservation_store.transition(
        old_reservation["public_id"],
        "quote_ready",
        idempotency_key="old-quote",
        actor="test",
        reason="test setup",
    )
    reservation_store.transition(
        old_reservation["public_id"],
        "demo_payment_pending",
        idempotency_key="old-checkout",
        actor="test",
        reason="test setup",
    )
    reservation_store.complete_demo_payment(
        old_reservation["public_id"],
        payment_reference="PAY-OLD-TRIP",
        idempotency_key="old-payment",
    )
    monkeypatch.setattr(
        marina_agent,
        "process_message",
        Mock(
            return_value=_wheelchair_result(
                locale="en",
                mermaid_action="new_booking",
                requires_human=True,
            )
        ),
    )

    result = workflow.process_model_turn(
        {
            "from": phone,
            "from_name": "Synthetic Guest",
            "message_id": "new-trip-wheelchair",
            "text": "Please make another booking. My husband uses a wheelchair.",
        },
        reservation_store.get_reservation(old_reservation["public_id"]),
    )

    assert result.action != "human_takeover"
    assert assistance.for_reservation(old_reservation["public_id"]) is None
    current = assistance.for_conversation(phone)
    assert current["kind"] == "wheelchair"
    assert current["reservationPublicId"] is None
    assert state_registry.wa_get_booking_state(phone)["flags"].get(
        "mermaid_session_started_at"
    )


def test_existing_reservation_restatement_keeps_acknowledged_wheelchair_lifecycle(
    monkeypatch,
):
    phone = "guest-existing-reservation-wheelchair-restatement"
    note, _ = assistance.record_wheelchair_note(
        phone,
        note="The guest's husband uses a wheelchair.",
        relationship="husband",
        trip_date="2026-09-06",
        source_message_id="existing-reservation-original-note",
    )
    intake = {
        "trip_date": "2026-09-06",
        "adults": 2,
        "children": 0,
        "infants": 0,
        "customer_name": "Synthetic Guest",
        "contact_phone": "+5999000000",
        "pickup_preference": "pier",
        "language": "en",
        "phase": "summary_confirmed",
        "accessibility_notes": "The guest's husband uses a wheelchair.",
        "wheelchair_relationship": "husband",
    }
    reservation = reservation_store.confirm_reservation(
        phone, intake, idempotency_key="existing-reservation-confirm"
    )
    assistance.acknowledge(
        note["id"], expected_revision=1, acknowledged_by="Crew Member"
    )
    state_registry.wa_save_booking_state(
        phone, {"mermaid_intake": intake}, {}, []
    )
    monkeypatch.setattr(
        marina_agent,
        "process_message",
        Mock(
            return_value=_wheelchair_result(
                locale="en",
                fields={
                    "accessibility_notes": "The guest's husband uses a wheelchair.",
                    "wheelchair_relationship": "husband",
                },
            )
        ),
    )

    workflow.process_model_turn(
        {
            "from": phone,
            "from_name": "Synthetic Guest",
            "message_id": "existing-reservation-restatement",
            "text": "My husband uses a wheelchair.",
        },
        reservation,
    )

    current = assistance.for_conversation(phone, kind="wheelchair")
    assert current["reservationPublicId"] == reservation["public_id"]
    assert current["revision"] == 1
    assert current["status"] == "acknowledged"
    linked = assistance.for_reservation(reservation["public_id"], kind="wheelchair")
    assert linked["revision"] == 1
    assert linked["status"] == "acknowledged"


@pytest.mark.parametrize(
    ("kind", "message"),
    [
        (
            assistance.KIND_WHEELCHAIR,
            "Please make another booking. My husband uses a wheelchair.",
        ),
        (
            assistance.KIND_BOARDING_ASSISTANCE,
            "Please make another booking. I need extra help boarding.",
        ),
    ],
)
def test_unseen_new_booking_assistance_retry_reuses_generation_and_links(
    monkeypatch, kind, message
):
    phone = f"guest-new-booking-retry-{kind}"
    old_intake = {
        "trip_date": "2026-09-06",
        "adults": 2,
        "children": 0,
        "infants": 0,
        "customer_name": "First Party",
        "contact_phone": "+5999000000",
        "pickup_preference": "pier",
        "language": "en",
        "phase": "summary_confirmed",
    }
    old_reservation = reservation_store.confirm_reservation(
        phone, old_intake, idempotency_key=f"retry-old-{kind}"
    )
    old_reservation = reservation_store.cancel(
        old_reservation["public_id"],
        idempotency_key=f"retry-old-cancel-{kind}",
    )
    result_payload = _wheelchair_result(
        locale="en", mermaid_action="new_booking", requires_human=False
    )
    if kind == assistance.KIND_BOARDING_ASSISTANCE:
        monkeypatch.setattr(
            workflow, "_general_boarding_assistance_message", lambda _text: True
        )
        result_payload["fields"] = {
            key: value
            for key, value in result_payload["fields"].items()
            if key not in {"accessibility_notes", "wheelchair_relationship"}
        }
        result_payload["assistance_request"] = "none"
    monkeypatch.setattr(
        marina_agent, "process_message", Mock(return_value=result_payload)
    )
    provider_message = {
        "from": phone,
        "from_name": "Second Party",
        "message_id": f"retry-new-booking-{kind}",
        "text": message,
    }

    workflow.process_model_turn(
        provider_message, old_reservation, defer_seen=True
    )
    first_flags = state_registry.wa_get_booking_state(phone)["flags"]
    first_generation = first_flags["mermaid_session_started_at"]
    assert first_flags["mermaid_session_source_message_id"] == provider_message[
        "message_id"
    ]
    workflow.process_model_turn(
        provider_message, old_reservation, defer_seen=True
    )
    second_generation = state_registry.wa_get_booking_state(phone)["flags"][
        "mermaid_session_started_at"
    ]
    assert second_generation == first_generation

    new_reservation = reservation_store.confirm_reservation(
        phone,
        {
            **old_intake,
            "adults": 3,
            "customer_name": "Second Party",
        },
        idempotency_key=f"retry-new-confirm-{kind}",
        assistance_session_owned=True,
    )
    linked = assistance.for_reservation(
        new_reservation["public_id"], kind=kind
    )
    assert linked is not None
    assert linked["reservationPublicId"] == new_reservation["public_id"]


def test_workflow_trip_correction_syncs_owned_boarding_assistance(monkeypatch):
    phone = "guest-owned-boarding-workflow-correction"
    intake = {
        "trip_date": "2026-09-06",
        "adults": 2,
        "children": 0,
        "infants": 0,
        "customer_name": "Synthetic Guest",
        "contact_phone": "+5999000000",
        "pickup_preference": "pier",
        "language": "en",
        "phase": "summary_confirmed",
    }
    state_registry.wa_save_booking_state(
        phone,
        {"mermaid_intake": intake},
        {"mermaid_session_started_at": "workflow-owned-generation"},
        [],
    )
    boarding, _ = assistance.record_boarding_assistance_note(
        phone,
        note="A guest in this party requested extra boarding assistance.",
        trip_date="2026-09-06",
        source_message_id="workflow-owned-boarding",
    )
    reservation = reservation_store.confirm_reservation(
        phone,
        intake,
        idempotency_key="workflow-owned-boarding-first",
        assistance_session_owned=True,
    )
    result_payload = _wheelchair_result(
        locale="en",
        mermaid_action="details",
        requires_human=False,
        assistance_request="none",
        fields={"trip_date": "2026-09-13"},
    )
    monkeypatch.setattr(
        marina_agent, "process_message", Mock(return_value=result_payload)
    )

    workflow.process_model_turn(
        {
            "from": phone,
            "from_name": "Synthetic Guest",
            "message_id": "workflow-owned-date-correction",
            "text": "Please change my trip date to September 13.",
        },
        reservation,
    )

    corrected = assistance.for_conversation(
        phone, kind=assistance.KIND_BOARDING_ASSISTANCE
    )
    assert corrected["id"] == boarding["id"]
    assert corrected["tripDate"] == "2026-09-13"
    assert corrected["revision"] == 2
    assert corrected["reservationPublicId"] is None


@pytest.mark.parametrize(
    "message",
    [
        "My husband is in a wheelchair.",
        "We have a wheelchair user in our party.",
        "My spouse travels in a wheelchair.",
    ],
)
def test_common_plain_wheelchair_paraphrases_use_the_ordinary_path(message):
    assert workflow._ordinary_wheelchair_message(message) is True


def test_plain_boat_context_is_not_mistaken_for_a_transfer_request():
    message = "My husband uses a wheelchair on boat trips."
    assert workflow._ordinary_wheelchair_message(message) is True
    assert workflow._wheelchair_capability_requires_review(message) is False


def test_mixed_oxygen_equipment_requires_review_even_when_wheelchair_use_is_plain():
    message = "My husband uses a wheelchair and carries an oxygen concentrator."
    assert workflow._ordinary_wheelchair_message(message) is False
    assert workflow._wheelchair_capability_requires_review(message) is True


@pytest.mark.parametrize(
    "message",
    [
        "Mi esposo usa una silla de ruedas en viajes en barco.",
        "Mi kasa ta usa stul di rueda ora nos ta biaha ku boto.",
        "Meu marido usa cadeira de rodas em passeios de barco.",
    ],
)
def test_localized_plain_boat_context_remains_ordinary(message):
    assert workflow._ordinary_wheelchair_message(message) is True
    assert workflow._wheelchair_capability_requires_review(message) is False


@pytest.mark.parametrize(
    "message",
    [
        "Can his wheelchair go on board?",
        "Does your boat accept his wheelchair?",
        "Can he remain in his wheelchair during the crossing?",
        "His manual wheelchair is 90 cm wide. Will that work?",
        "¿Puede permanecer en su silla de ruedas a bordo?",
        "E por keda den su stul di rueda abordo?",
        "Ele pode permanecer na cadeira de rodas a bordo?",
    ],
)
def test_specific_fit_and_crossing_questions_require_review(message):
    assert workflow._ordinary_wheelchair_message(message) is False
    assert workflow._wheelchair_capability_requires_review(message) is True


def test_legacy_default_path_does_not_treat_the_word_person_as_human_request():
    phone = "guest-wheelchair-legacy-default"

    result = workflow.process_intake_turn(
        phone,
        "A person in our party uses a wheelchair.",
        message_id="legacy-wheelchair-1",
        from_name="Synthetic Guest",
    )

    assert result.action is None
    assert workflow.WHEELCHAIR_COPY["en"] in result.text
    assert assistance.for_conversation(phone)["status"] == "unacknowledged"
    assert state_registry.get_active_escalation_mode(phone) is None


def test_live_prompt_has_one_non_conflicting_ordinary_wheelchair_rule():
    prompt = mermaid_understanding.system_prompt()

    assert "Mermaid's team must confirm transfer and trip suitability" not in prompt
    assert "Guests who use a wheelchair are welcome" in prompt
    assert "Ordinary wheelchair use is governed by the separate assistance rule" in prompt
    assert "specific unconfirmed capability" in prompt


def test_failed_date_note_sync_is_retried_from_cached_understanding(monkeypatch):
    phone = "guest-wheelchair-date-retry"
    assistance.record_wheelchair_note(
        phone,
        note="A guest uses a wheelchair.",
        relationship="unspecified",
        trip_date="2026-09-06",
        source_message_id="wheelchair-original",
    )
    state_registry.wa_save_booking_state(
        phone,
        {
            "mermaid_intake": {
                "language": "en",
                "phase": "collecting",
                "trip_date": "2026-09-06",
                "accessibility_notes": "A guest uses a wheelchair.",
                "wheelchair_relationship": "unspecified",
            }
        },
        {},
    )
    stub = Mock(
        return_value=_wheelchair_result(
            locale="en",
            assistance_request="none",
            requires_human=False,
            has_open_question=False,
            guest_question_excerpt="",
            fields={"trip_date": "2026-09-13"},
            reply="I updated the trip date.",
        )
    )
    monkeypatch.setattr(marina_agent, "process_message", stub)
    original_sync = assistance.sync_existing
    monkeypatch.setattr(
        assistance,
        "sync_existing",
        Mock(side_effect=RuntimeError("simulated note-sync failure")),
    )
    message = {
        "from": phone,
        "message_id": "date-correction-1",
        "text": "Please change the trip to 13 September.",
    }

    with pytest.raises(RuntimeError, match="note-sync failure"):
        workflow.process_model_turn(message, None)
    assert assistance.for_conversation(phone)["tripDate"] == "2026-09-06"

    monkeypatch.setattr(assistance, "sync_existing", original_sync)
    workflow.process_model_turn(message, None)

    assert assistance.for_conversation(phone)["tripDate"] == "2026-09-13"
    assert "date-correction-1" in state_registry.wa_get_booking_state(phone)["flags"][
        "mermaid_seen_message_ids"
    ]
    assert stub.call_count == 1


def test_new_session_does_not_carry_old_wheelchair_note_into_unrelated_booking(
    monkeypatch,
):
    phone = "guest-wheelchair-old-session"
    assistance.record_wheelchair_note(
        phone,
        note="A guest in this party uses a wheelchair.",
        relationship="unspecified",
        trip_date="2026-09-06",
        source_message_id="old-session-note",
    )
    # This is the state shape after the generic 24-hour Mermaid reset.
    state_registry.wa_save_booking_state(phone, {}, {}, [])
    monkeypatch.setattr(
        marina_agent,
        "process_message",
        Mock(
            return_value=_wheelchair_result(
                locale="en",
                assistance_request="none",
                requires_human=False,
                fields={"trip_date": "2026-09-20"},
                reply="I saved the new date.",
            )
        ),
    )

    workflow.process_model_turn(
        {
            "from": phone,
            "message_id": "new-session-date",
            "text": "We would like to travel on 20 September.",
        },
        None,
    )

    old_attention = assistance.for_conversation(phone)
    assert old_attention["tripDate"] == "2026-09-06"
    assert old_attention["revision"] == 1
    intake = state_registry.wa_get_booking_state(phone)["fields"]["mermaid_intake"]
    assert intake["trip_date"] == "2026-09-20"
    assert "accessibility_notes" not in intake


def test_explicit_no_wheelchair_correction_withdraws_task_and_clears_intake(
    monkeypatch,
):
    phone = "guest-wheelchair-withdrawal"
    assistance.record_wheelchair_note(
        phone,
        note="A guest in this party uses a wheelchair.",
        relationship="unspecified",
        trip_date="2026-09-13",
        source_message_id="wheelchair-original",
    )
    state_registry.wa_save_booking_state(
        phone,
        {
            "mermaid_intake": {
                "language": "en",
                "phase": "collecting",
                "accessibility_notes": "A guest in this party uses a wheelchair.",
                "wheelchair_relationship": "unspecified",
            }
        },
        {},
    )
    monkeypatch.setattr(
        marina_agent,
        "process_message",
        Mock(
            return_value=_wheelchair_result(
                locale="en",
                mermaid_action="details",
                assistance_request="wheelchair_withdrawal",
                requires_human=False,
                fields={},
                reply="I removed it.",
            )
        ),
    )

    result = workflow.process_model_turn(
        {
            "from": phone,
            "message_id": "wheelchair-withdrawal-1",
            "text": "Nobody in our party uses a wheelchair anymore.",
        },
        None,
    )

    assert workflow.WHEELCHAIR_WITHDRAWAL_COPY["en"] in result.text
    intake = state_registry.wa_get_booking_state(phone)["fields"]["mermaid_intake"]
    assert "accessibility_notes" not in intake
    assert "wheelchair_relationship" not in intake
    withdrawn = assistance.for_conversation(phone)
    assert withdrawn["status"] == "withdrawn"
    assert withdrawn["note"] == "Wheelchair note withdrawn after a guest correction."
    assert assistance.list_items() == []

    reopened, outcome = assistance.record_wheelchair_note(
        phone,
        note="A guest in this party uses a wheelchair.",
        relationship="unspecified",
        source_message_id="wheelchair-reintroduced",
    )
    assert outcome == "updated"
    assert reopened["status"] == "unacknowledged"


@pytest.mark.parametrize(
    "message",
    [
        "My husband does not use a wheelchair.",
        "Doesn't use a wheelchair.",
        "We no longer need a wheelchair.",
        "The wheelchair note has been removed from my reservation.",
        "Mi kasá no ta usa stul di rueda mas.",
        "Nò, mi kasá no ta usa stul di rueda mas.",
        "No ta usa stul di rueda mas.",
        "Nos no tin mester di e stul di rueda mas.",
        "Kita e nota tokante e stul di rueda for di mi reservashon, por fabor.",
        "Mijn man gebruikt geen rolstoel meer.",
        "Gebruikt geen rolstoel meer.",
        "Wij hebben geen rolstoel meer nodig.",
        "De rolstoelnotitie is uit mijn reservering verwijderd.",
        "Mein Mann benutzt keinen Rollstuhl mehr.",
        "Benutzt keinen Rollstuhl mehr.",
        "Wir brauchen keinen Rollstuhl mehr.",
        "Der Rollstuhlhinweis wurde aus meiner Reservierung entfernt.",
        "Mi esposo no usa una silla de ruedas.",
        "No usa una silla de ruedas.",
        "Ya no necesitamos una silla de ruedas.",
        "La nota sobre la silla de ruedas fue eliminada de mi reserva.",
        "Meu marido não usa uma cadeira de rodas.",
        "Não usa uma cadeira de rodas.",
        "Nós não precisamos mais de uma cadeira de rodas.",
        "A observação sobre a cadeira de rodas foi removida da minha reserva.",
    ],
)
def test_direct_negation_no_longer_needed_and_removed_forms_are_withdrawals(message):
    assert workflow._wheelchair_withdrawal_message(message) is True
    assert workflow._ordinary_wheelchair_message(message) is False


@pytest.mark.parametrize(
    "locale,message",
    [
        ("en", "My husband doesn't use a wheelchair anymore."),
        ("en", "My daughter does not use a wheelchair."),
        ("en", "One guest no longer uses a wheelchair."),
        ("en", "Our guest is not a wheelchair user."),
        ("pap", "Mi kasá no ta usa stul di rueda mas."),
        ("nl", "Mijn man gebruikt geen rolstoel meer."),
        ("de", "Mein Mann benutzt keinen Rollstuhl mehr."),
        ("es", "Mi esposo no usa una silla de ruedas."),
        ("pt", "Meu marido não usa mais uma cadeira de rodas."),
    ],
)
def test_multilingual_direct_negation_withdraws_without_escalation(
    monkeypatch, locale, message
):
    phone = f"guest-wheelchair-direct-withdrawal-{locale}"
    note = "The guest's husband uses a wheelchair."
    assistance.record_wheelchair_note(
        phone,
        note=note,
        relationship="husband",
        trip_date="2026-09-13",
        source_message_id=f"wheelchair-original-{locale}",
    )
    state_registry.wa_save_booking_state(
        phone,
        {
            "mermaid_intake": {
                "language": locale,
                "phase": "collecting",
                "accessibility_notes": note,
                "wheelchair_relationship": "husband",
            }
        },
        {},
    )
    monkeypatch.setattr(
        marina_agent,
        "process_message",
        Mock(
            return_value=_wheelchair_result(
                locale=locale,
                mermaid_action="request_human",
                assistance_request="other_review",
                requires_human=True,
                security_event="actionable_incident",
                fields={
                    "accessibility_notes": note,
                    "wheelchair_relationship": "husband",
                },
                reply="The team must review this.",
            )
        ),
    )

    result = workflow.process_model_turn(
        {
            "from": phone,
            "from_name": "Synthetic Guest",
            "message_id": f"wheelchair-direct-withdrawal-{locale}",
            "text": message,
        },
        None,
    )

    assert result.text == "\n\n".join(
        (
            workflow.WHEELCHAIR_WITHDRAWAL_COPY[locale],
            workflow.COPY[locale]["trip_date"],
        )
    )
    assert result.action is None
    assert result.phase == "collecting"
    intake = state_registry.wa_get_booking_state(phone)["fields"]["mermaid_intake"]
    assert "accessibility_notes" not in intake
    assert "wheelchair_relationship" not in intake
    assert assistance.for_conversation(phone)["status"] == "withdrawn"
    assert state_registry.get_active_escalation_mode(phone) is None
    assert state_registry.get_ai_muted(phone) is False
    assert _pending_notification_count() == 0


def test_legacy_direct_path_withdraws_formal_papiamentu_correction():
    phone = "guest-wheelchair-direct-withdrawal-legacy-pap"
    note = "The guest's husband uses a wheelchair."
    assistance.record_wheelchair_note(
        phone,
        note=note,
        relationship="husband",
        trip_date="2026-09-13",
        source_message_id="wheelchair-original-legacy-pap",
    )
    state_registry.wa_save_booking_state(
        phone,
        {
            "mermaid_intake": {
                "language": "pap",
                "phase": "collecting",
                "accessibility_notes": note,
                "wheelchair_relationship": "husband",
            }
        },
        {},
    )

    result = workflow.process_intake_turn(
        phone,
        "Mi kasá no ta usa stul di rueda mas.",
        message_id="wheelchair-direct-withdrawal-legacy-pap",
    )

    assert result.text == "\n\n".join(
        (
            workflow.WHEELCHAIR_WITHDRAWAL_COPY["pap"],
            workflow.COPY["pap"]["trip_date"],
        )
    )
    assert result.action is None
    assert result.phase == "collecting"
    intake = state_registry.wa_get_booking_state(phone)["fields"]["mermaid_intake"]
    assert "accessibility_notes" not in intake
    assert "wheelchair_relationship" not in intake
    assert assistance.for_conversation(phone)["status"] == "withdrawn"
    assert state_registry.get_active_escalation_mode(phone) is None
    assert state_registry.get_ai_muted(phone) is False
    assert _pending_notification_count() == 0


@pytest.mark.parametrize(
    "message",
    [
        "My husband does not use an electric wheelchair; he uses a normal foldable wheelchair.",
        "There is no wheelchair ramp, but my husband uses a wheelchair.",
        "Mijn man gebruikt geen elektrische rolstoel; hij gebruikt een gewone rolstoel.",
        "Mein Mann benutzt keinen elektrischen Rollstuhl; er benutzt einen normalen Rollstuhl.",
        "Mi esposo no usa una silla de ruedas eléctrica; usa una silla manual.",
        "Meu marido não usa uma cadeira de rodas elétrica; usa uma cadeira manual.",
        "Mi kasá no ta usa un stul di rueda elektriko; e ta usa un stul manual.",
    ],
)
def test_withdrawal_guard_rejects_negated_capability_sentences(message):
    assert workflow._wheelchair_withdrawal_message(message) is False


def test_withdrawal_after_quote_does_not_restore_private_intake_fields(monkeypatch):
    phone = "guest-wheelchair-withdrawal-quote"
    original_intake = {
        "language": "en",
        "phase": "demo_payment_pending",
        "trip_date": "2026-09-13",
        "adults": 2,
        "children": 0,
        "infants": 0,
        "customer_name": "Synthetic Guest",
        "contact_phone": "+5999000000",
        "pickup_preference": "pier",
        "accessibility_notes": "A guest in this party uses a wheelchair.",
        "wheelchair_relationship": "unspecified",
    }
    state_registry.wa_save_booking_state(
        phone, {"mermaid_intake": original_intake}, {}
    )
    assistance.record_wheelchair_note(
        phone,
        note=original_intake["accessibility_notes"],
        relationship="unspecified",
        trip_date=original_intake["trip_date"],
        source_message_id="wheelchair-before-quote",
    )
    reservation = {
        "public_id": "mer_quote_withdrawal",
        "state": "demo_payment_pending",
        "human_takeover": False,
        "intake": {
            key: value
            for key, value in original_intake.items()
            if key not in {"accessibility_notes", "wheelchair_relationship"}
        },
        "monetary_snapshot": {
            "currency": "USD",
            "items": [],
            "total": 300,
            "pickup_amount": None,
            "pickup_plan": None,
        },
        "booking_code": "MER-DEMO-TEST",
    }
    monkeypatch.setattr(
        marina_agent,
        "process_message",
        Mock(
            return_value=_wheelchair_result(
                locale="en",
                assistance_request="wheelchair_withdrawal",
                requires_human=False,
                fields={},
                reply="Corrected.",
            )
        ),
    )

    result = workflow.process_model_turn(
        {
            "from": phone,
            "message_id": "wheelchair-withdrawal-quote-1",
            "text": "Nobody in our party uses a wheelchair anymore.",
        },
        reservation,
    )

    intake = state_registry.wa_get_booking_state(phone)["fields"]["mermaid_intake"]
    assert "accessibility_notes" not in intake
    assert "wheelchair_relationship" not in intake
    assert assistance.for_conversation(phone)["status"] == "withdrawn"
    assert workflow.WHEELCHAIR_WITHDRAWAL_COPY["en"] in result.text

@pytest.mark.parametrize('locale', ['en','nl','de','es','pap','pt'])
def test_supplied_group_wheelchair_and_undecided_date_are_all_preserved(monkeypatch, locale):
    from agents.social.mermaid_reply_planning import DATE_COPY
    text = "i want to book a trip , we are 6 of us , 1 baby of 11 months , kid of 13 and 4 adults , my husband is in wheelchair , we dont know when we wanna go"
    fields = {'adults':5,'children':0,'infants':1,'child_ages':[{'value':13,'unit':'years'},{'value':11,'unit':'months'}], 'wheelchair_relationship':'husband'}
    model = Mock(return_value=_wheelchair_result(locale, fields=fields, date_request='undecided', date_request_excerpt='we dont know when we wanna go'))
    monkeypatch.setattr(marina_agent,'process_message',model)
    result=workflow.process_model_turn({'from':'undecided','message_id':'first','text':text},None)
    saved=state_registry.wa_get_booking_state('undecided')['fields']['mermaid_intake']
    assert saved['child_ages']==fields['child_ages'] and sum(saved[k] for k in ('adults','children','infants'))==6
    assert DATE_COPY[locale][0] in result.text
    assert workflow.COPY[locale]['trip_date'] not in result.text
    assert '13' in result.text and '11' in result.text and result.text.count('?')==1
    assert saved['date_selection']=='undecided' and 'trip_date' not in saved
    assert not state_registry.get_ai_muted('undecided') and result.action is None
    if locale=='en':
        assert '4 adults · 1 child (13 years) · 1 infant (11 months)' in result.text


def test_repeated_date_uncertainty_moves_on_without_inventing_or_confirming_date(monkeypatch):
    model=Mock(return_value=_wheelchair_result('en', fields={'adults':2,'children':0,'infants':0}, assistance_request='none', requires_human=False, date_request='undecided', date_request_excerpt="don't know yet"))
    monkeypatch.setattr(marina_agent,'process_message',model)
    for index in range(2):
        result=workflow.process_model_turn({'from':'later','message_id':str(index),'text':"We don't know yet"},None)
    assert "choose the date later" in result.text and workflow.COPY['en']['name'] in result.text
    assert result.phase=='collecting' and result.action is None
    assert state_registry.wa_get_booking_state('later')['fields']['mermaid_intake']['date_selection']=='deferred'
    model.return_value=_wheelchair_result('en', fields={'trip_date':'2026-09-13'}, assistance_request='none', requires_human=False, has_open_question=False, guest_question_excerpt='',reply='What name should I put on the reservation?')
    result=workflow.process_model_turn({'from':'later','message_id':'date','text':'September 13'},None)
    saved=state_registry.wa_get_booking_state('later')['fields']['mermaid_intake']
    assert saved['trip_date']=='2026-09-13' and 'date_selection' not in saved


@pytest.mark.parametrize('selector,value', [('calendar_request','next_week'),('status_request','wildlife_guarantee'),('status_request','pickup_coverage'),('assistance_request','wheelchair_note'),('assistance_request','other_review')])
def test_protected_reply_keeps_separate_food_answer(monkeypatch,selector,value):
    model=Mock(return_value=_wheelchair_result('en',fields={},assistance_request='none',requires_human=False,other_question_reply='Breakfast and a BBQ lunch are included.',other_question_excerpt='Is breakfast included?',other_question_topic='food'))
    model.return_value[selector]=value
    monkeypatch.setattr(marina_agent,'process_message',model)
    result=workflow.process_model_turn({'from':'mixed','message_id':value,'text':'Is breakfast included? And my other question?'},None)
    assert result.text.count('Breakfast and a BBQ lunch are included.')==1


def test_calendar_pickup_wildlife_and_food_questions_all_get_answers(monkeypatch):
    model=Mock(return_value=_wheelchair_result('en',fields={'adults':5,'children':0,'infants':1},assistance_request='none',requires_human=False,calendar_request='next_week',status_request='pickup_pricing',additional_status_requests=['wildlife_guarantee'],other_question_reply='Breakfast and a BBQ lunch are included.',other_question_excerpt='Is food included?'))
    monkeypatch.setattr(marina_agent,'process_message',model)
    result=workflow.process_model_turn({'from':'many','message_id':'many','text':'Which days next week? Pickup cost for six? Are turtles guaranteed? Is food included?'},None)
    assert '125' in result.text and 'guarantee' in result.text and 'Breakfast and a BBQ lunch' in result.text
    assert '2026-09-' in result.text
    assert 'pickup_preference' not in state_registry.wa_get_booking_state('many')['fields']['mermaid_intake']


def test_old_captured_assistance_copy_is_not_appended_twice(monkeypatch):
    model=Mock(return_value=_wheelchair_result('pap',fields={'adults':2,'children':0,'infants':0},other_question_reply=workflow.WHEELCHAIR_COPY['pap']))
    monkeypatch.setattr(marina_agent,'process_message',model)
    result=workflow.process_model_turn({'from':'copy','message_id':'copy','text':'Mi kasa ta usa stul di rueda.'},None)
    assert result.text.count(workflow.WHEELCHAIR_COPY['pap'])==1


def test_old_captured_secondary_wildlife_answer_cannot_duplicate_protected_fact(monkeypatch):
    from agents.social import mermaid_response_policy as policy
    model=Mock(return_value=_wheelchair_result('en',fields={'adults':5,'children':0,'infants':1},assistance_request='none',requires_human=False,status_request='pickup_pricing',additional_status_requests=['wildlife_guarantee'],other_question_reply="Turtle sightings do happen, but they're never guaranteed."))
    monkeypatch.setattr(marina_agent,'process_message',model)
    result=workflow.process_model_turn({'from':'copy','message_id':'copy','text':'How much is pickup? Are turtles guaranteed?'},None)
    assert "Turtle sightings do happen" not in result.text
    assert result.text.count(policy.copy('wildlife_guarantee','en'))==1


@pytest.mark.parametrize("topic", [None, "none", "accessibility", "protected"])
def test_paraphrased_assistance_is_not_a_separate_faq(monkeypatch, topic):
    duplicate = "Guests who use a wheelchair are welcome on the trip. I've added a note for the crew so they can be ready to assist."
    model = _wheelchair_result("en", fields={"wheelchair_relationship": "husband"},
        calendar_request="next_week", other_question_reply=duplicate,
        other_question_excerpt="My husband is in a wheelchair, can he join too ?")
    if topic is not None:
        model["other_question_topic"] = topic
    monkeypatch.setattr(marina_agent, "process_message", Mock(return_value=model))
    result = workflow.process_model_turn({"from": "repeat", "message_id": "repeat",
        "text": "My husband is in a wheelchair, can he join too ? We want next week."}, None)
    assert duplicate not in result.text
    assert result.text.count(workflow.WHEELCHAIR_COPY["en"]) == 1
    assert result.text.count("?") == 1
    assert assistance.for_conversation("repeat") is not None


def test_assistance_rejects_faq_without_guest_evidence(monkeypatch):
    model = _wheelchair_result("en", fields={}, other_question_topic="food",
        other_question_reply="Breakfast is included.", other_question_excerpt="Is breakfast included?")
    monkeypatch.setattr(marina_agent, "process_message", Mock(return_value=model))
    result = workflow.process_model_turn({"from": "no-faq", "message_id": "one",
        "text": "My husband uses a wheelchair."}, None)
    assert "Breakfast is included." not in result.text
