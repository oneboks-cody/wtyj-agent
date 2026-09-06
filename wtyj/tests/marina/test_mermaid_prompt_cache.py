"""Offline SDK-boundary tests. Only synthetic tenant and guest data is used."""
import json

import httpx
import pytest

from agents.marina import marina_agent as agent
from agents.social import mermaid_understanding as understanding
from shared import config_loader


@pytest.fixture
def boundary(monkeypatch):
    raw = {"slug": "mermaid", "features": {"mermaid_reservation_demo": True},
           "password": "synthetic-secret"}
    monkeypatch.setattr(config_loader, "get_raw", lambda: raw)
    monkeypatch.setattr(config_loader, "get_agent_signature", lambda: "Tracy")
    monkeypatch.setattr(understanding, "system_prompt", lambda: "Stable policy synthetic-secret")
    monkeypatch.setattr(agent, "_build_system_prompt", lambda *a, **kw: "Legacy policy")
    monkeypatch.setattr(agent, "_build_user_prompt", lambda *a, **kw: "Legacy guest")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "offline-dummy-key")
    events, requests = [], []
    monkeypatch.setattr(agent.bm_logger, "log", lambda event, **kw: events.append((event, kw)))
    usage = {"input_tokens": 120, "output_tokens": 30}
    status = [200]

    def handle(request):
        requests.append(json.loads(request.content))
        if status[0] != 200:
            return httpx.Response(status[0], json={"type": "error", "error": {
                "type": "authentication_error", "message": "Synthetic failure"}})
        return httpx.Response(200, json={
            "id": "msg_offline", "type": "message", "role": "assistant",
            "model": "claude-sonnet-4-6", "stop_reason": "tool_use", "stop_sequence": None,
            "usage": usage, "content": [{"type": "tool_use", "id": "tool_offline",
                "name": "marina_response", "input": {"reply": "Breakfast is included.",
                "fields": {"adults": 2}, "confidence": "high", "requires_human": False}}],
        })

    sdk = agent.anthropic.Anthropic
    monkeypatch.setattr(agent.anthropic, "Anthropic", lambda **kw: sdk(
        **kw, http_client=httpx.Client(transport=httpx.MockTransport(handle))))
    return raw, requests, events, usage, status


def call(contract="mermaid_reservation_demo", body="Two adults; is breakfast included?"):
    return agent.process_message("synthetic-guest", "", body, {"customer_name": "Test Guest"}, {},
        channel="whatsapp", messages=[{"role": "assistant", "text": "How many adults?"}],
        response_contract=contract)


@pytest.mark.parametrize("created,read", [(9000, 0), (0, 9000), (0, 0)])
def test_cache_preserves_full_request_and_reply_and_records_usage(boundary, created, read):
    _, requests, events, usage, _ = boundary
    usage.update(cache_creation_input_tokens=created, cache_read_input_tokens=read)
    result = call()
    assert result["reply"] == "Breakfast is included."
    assert result["fields"] == {"adults": 2}
    assert len(requests) == 1
    request = requests[0]
    expected_text = agent.redact_config_credentials(understanding.system_prompt(), config_loader.get_raw())
    assert request["system"] == [{"type": "text", "text": expected_text,
        "cache_control": {"type": "ephemeral", "ttl": "5m"}}]
    assert "synthetic-secret" not in json.dumps(request)
    assert request["tools"] == [understanding.MERMAID_TOOL]
    assert request["tool_choice"] == {"type": "tool", "name": "marina_response"}
    assert request["model"] == "claude-sonnet-4-6" and request["max_tokens"] == 2048
    expected_user = understanding.user_prompt("synthetic-guest", "", "Two adults; is breakfast included?",
        {"customer_name": "Test Guest"}, {}, channel="whatsapp",
        messages=[{"role": "assistant", "text": "How many adults?"}])
    assert request["messages"] == [{"role": "user", "content": expected_user}]
    assert "cache_control" not in request
    recorded = next(fields for event, fields in events if event == "api_usage")
    assert recorded["input_tokens"] == 120
    assert recorded["cache_creation_input_tokens"] == created
    assert recorded["cache_read_input_tokens"] == read
    assert recorded["total_input_tokens"] == 120 + created + read


def test_different_guests_share_only_stable_prefix(boundary):
    _, requests, _, _, _ = boundary
    call(body="Synthetic guest A")
    call(body="Synthetic guest B")
    assert requests[0]["system"] == requests[1]["system"]
    assert requests[0]["messages"] != requests[1]["messages"]
    assert "Synthetic guest" not in json.dumps(requests[0]["system"])


def test_legacy_contract_and_absent_cache_usage_remain_supported(boundary):
    _, requests, events, _, _ = boundary
    assert call(contract="")["reply"] == "Breakfast is included."
    assert requests[0]["system"] == "Legacy policy"
    assert "cache_control" not in json.dumps(requests[0])
    recorded = next(fields for event, fields in events if event == "api_usage")
    assert recorded["total_input_tokens"] == 120
    assert recorded["cache_read_input_tokens"] == recorded["cache_creation_input_tokens"] == 0


def test_other_tenant_cannot_enter_mermaid_contract(boundary):
    raw, requests, _, _, _ = boundary
    raw["slug"] = "other-tenant"
    call()
    assert requests == []


def test_provider_failure_has_no_cache_retry_or_fallback_call(boundary):
    _, requests, _, _, status = boundary
    status[0] = 401
    result = call()
    assert "model_error" in result
    assert len(requests) == 1
