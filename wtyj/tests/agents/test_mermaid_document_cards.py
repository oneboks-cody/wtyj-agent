import hashlib
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from agents.social import mermaid_document_cards as cards, mermaid_documents as docs
from agents.social import mermaid_demo_payment as payment, mermaid_reservation_store as store
from agents.social import mermaid_reservation_workflow as workflow, mermaid_delivery_reconciliation as reconcile
from agents.social import zernio_dm_client as provider
from agents.social import mermaid_guest_experience as guest
from agents.social.senders import send_reply
from shared import config_loader, mermaid_catalog, state_registry, tenant_guard

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config_loader, "_CONFIG_PATH", str(ROOT / "clients/mermaid/config/client.json"))
    monkeypatch.setattr(config_loader, "_cache", {})
    monkeypatch.setattr(state_registry, "DB_PATH", str(tmp_path / "state.db"))
    monkeypatch.setenv("MERMAID_DOCUMENT_ROOT", str(tmp_path / "documents"))
    monkeypatch.setenv("MERMAID_DEMO_SIGNING_SECRET", "card-test-secret")
    monkeypatch.setenv("UNBOKS_PUBLIC_BASE_URL", "https://example.test/api/mermaid")
    monkeypatch.setenv("LATE_API_KEY", "fake")
    monkeypatch.setattr(tenant_guard, "is_account_allowed", lambda *a, **k: True)
    monkeypatch.setattr(provider, "_provider_mutation_account_allowed", lambda *a: True)
    monkeypatch.setattr(provider, "_recommendation_session_open", lambda *a: (True, []))
    monkeypatch.setattr(provider, "_provider_history_still_owned", lambda *a: True)
    def render(reservation, target):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"%PDF-test-document")
        return hashlib.sha256(target.read_bytes()).hexdigest()
    monkeypatch.setattr(docs, "render_quote_pdf", render)


def sample(locale="en"):
    return store.confirm_reservation("card-test", {
        "customer_name": "Calvin test", "trip_date": "2026-09-13", "adults": 3,
        "children": 0, "infants": 1, "child_ages": [{"value": 9, "unit": "months"}],
        "pickup_preference": "pickup_requested", "pickup_location": "President Kennedy Boulevard 12, Willemstad",
        "contact_phone": "+12025550123", "language": locale, "phase": "summary_confirmed",
    }, idempotency_key="sample-"+locale, zernio_account_id="card-account")


def send(reservation, document, text="Your demo quote is ready.", key="card-test-action"):
    url = docs.build_signed_url("https://example.test/api/mermaid", document["public_id"], "card-test-secret")
    return send_reply("whatsapp", reservation["conversation_id"], "card-account", text,
                      attachment_url=url, attachment_type="file", attachment_name=document["filename"],
                      confirm_delivery=True, idempotency_key=key)


def test_automatic_sender_makes_one_image_button_card_and_reuses_payload(monkeypatch):
    r = sample(); document, job = docs.create_quote(r)
    posts = []
    monkeypatch.setattr(provider, "_post_recommendation_message", lambda url, headers, body: posts.append((headers.copy(), body)) or ("sent", 200, "provider-1"))
    monkeypatch.setattr(provider, "_confirm_recommendation_status", lambda *a, **k: "sent")
    from agents.social.senders import zernio
    monkeypatch.setattr(zernio, "send_dm_reply", lambda *a, **k: pytest.fail("bare file fallback"))
    assert send(r, document)
    assert send(r, document, "A retry with regenerated text must not send again.")
    assert len(posts) == 1
    headers, payload = posts[0]
    interactive = payload["interactive"]
    assert interactive["type"] == "cta_url" and "☀️🏝️" in interactive["body"]["text"]
    assert interactive["header"]["type"] == "image"
    assert "/api/mermaid/api/public/mermaid-card-image/" in interactive["header"]["image"]["link"]
    assert interactive["action"]["parameters"]["display_text"] == "Open PDF"
    assert "card=true" in interactive["action"]["parameters"]["url"]
    assert cards.records(document["public_id"], "card-test", "card-account")[0]["status"] == "delivered"


def test_ambiguous_send_retry_uses_identical_body_and_key(monkeypatch):
    r = sample(); document, _ = docs.create_quote(r)
    posts = []
    def post(url, headers, body):
        posts.append((headers["Idempotency-Key"], json.dumps(body, sort_keys=True)))
        return ("ambiguous", None, "") if len(posts) == 1 else ("sent", 200, "provider-retry")
    monkeypatch.setattr(provider, "_post_recommendation_message", post)
    monkeypatch.setattr(provider, "_confirm_recommendation_status", lambda *a, **k: "sent")
    assert not send(r, document)
    assert send(r, document, "Changed text")
    assert posts[0] == posts[1]


def test_pending_card_is_reconciled_by_provider_id_without_attachment(monkeypatch):
    r = sample(); document, job = docs.create_quote(r)
    monkeypatch.setattr(provider, "_post_recommendation_message", lambda *a: ("sent", 200, "provider-delayed"))
    monkeypatch.setattr(provider, "_confirm_recommendation_status", lambda *a, **k: "ambiguous")
    assert not send(r, document)
    docs.mark_delivery(job["public_id"], False)
    class Reply:
        status_code = 200
        def json(self):
            return {"messages": [{"id": "provider-delayed", "direction": "outgoing", "status": "delivered", "text": "Your trip ☀️🏝️", "accountId": "card-account", "conversationId": "card-test"}]}
    monkeypatch.setattr(provider, "_provider_account_get", lambda *a, **k: Reply())
    assert reconcile.reconcile_job(job["public_id"]) == "delivered"
    assert docs.delivery_job(job["public_id"])["status"] == "delivered"


def test_card_download_scope_expiry_revocation_and_public_image(monkeypatch):
    r = sample(); document, _ = docs.create_quote(r)
    url = cards.download_url("https://example.test", document, r, now=1000)
    query = parse_qs(urlsplit(url).query)
    expires, signature = int(query["expires"][0]), query["signature"][0]
    monkeypatch.setattr(cards.time, "time", lambda: 1000 + 86400)
    assert cards.download_response(document["public_id"], expires, signature).status_code == 200
    assert docs.document_response(document["public_id"], expires, signature).status_code == 404
    assert cards.download_response(document["public_id"], expires, "bad").status_code == 404
    assert cards.download_response("mdoc_"+"0"*24, expires, signature).status_code == 404
    digest = hashlib.sha256(docs.HERO_IMAGE.read_bytes()).hexdigest()
    assert cards.image_response(digest).status_code == 200
    assert cards.image_response("bad").status_code == 404
    monkeypatch.setattr(cards.time, "time", lambda: expires + 1)
    assert cards.download_response(document["public_id"], expires, signature).status_code == 404
    monkeypatch.setattr(cards.time, "time", lambda: 1000)
    conn = docs._conn();conn.execute("DELETE FROM mermaid_documents WHERE public_id=?", (document["public_id"],));conn.commit();conn.close()
    assert cards.download_response(document["public_id"], expires, signature).status_code == 404


def test_ownership_and_closed_window_cannot_send(monkeypatch):
    r = sample(); document, _ = docs.create_quote(r)
    monkeypatch.setattr(provider, "_post_recommendation_message", lambda *a: pytest.fail("must not send"))
    url = docs.build_signed_url("https://example.test/api/mermaid", document["public_id"], "card-test-secret")
    assert cards.try_send("other-conversation", "card-account", "x", url, "other") is False
    assert cards.try_send("card-test", "other-account", "x", url, "other") is False
    monkeypatch.setattr(provider, "_recommendation_session_open", lambda *a: (False, []))
    assert not send(r, document)


@pytest.mark.parametrize("locale", ["en", "nl", "de", "es", "pap", "pt"])
def test_localized_dense_quote_and_warm_receipt_fit_one_card(locale):
    r = sample(locale)
    r["booking_code"] = "MER-DEMO-ABCDEFGH"
    r["customer_name"] = ("Alexandra Maria van der Meer " * 6)[:160]
    r["intake"]["pickup_location"] = ("Piscadera Bay Resort, bungalow 342, reception entrance beside the blue gate, " * 3)[:160]
    r["intake"].update(adults=1, children=4, infants=4, child_ages=[{"value": i+4,"unit":"years"} for i in range(4)]+[{"value":i+6,"unit":"months"} for i in range(4)])
    r["monetary_snapshot"] = store._money_snapshot(r["intake"], mermaid_catalog.get_catalog())
    copy = cards.copy_for(locale)
    receipt = payment.success_message(r, {"currency":"USD", "amount":r["monetary_snapshot"]["total"]})
    text = docs.quote_message(r)+"\n\n"+workflow.PAYMENT_COPY[locale][0]+"\n\n"+guest.guest_copy(locale)["checkout_link"]+"\nhttps://unboks.org/mermaid/pay/1234567890123456789012"
    for caption in (receipt,text):
        assert len((copy["title"]+"\n\n"+caption).encode("utf-16-le"))//2 <= 1024
        assert r["intake"]["pickup_location"] in caption and "05:45" in caption
    assert copy["closing"] in receipt and "demo" not in receipt.casefold()
    assert "\n\n" in receipt and guest.display_reference(r["booking_code"]) in receipt


def test_quote_then_real_checkout_dispatches_two_cards_without_replay(monkeypatch):
    from shared import icp_overrides
    monkeypatch.setattr(icp_overrides, "fetch_overrides_fresh", lambda: {})
    monkeypatch.setattr(icp_overrides, "whatsapp_inbox_state", lambda _: True)
    monkeypatch.setattr(icp_overrides, "auto_reply_state", lambda _: True)
    posts = []
    def post(url, headers, body):
        posts.append(body)
        return "sent", 200, "flow-provider-"+str(len(posts))
    monkeypatch.setattr(provider, "_post_recommendation_message", post)
    monkeypatch.setattr(provider, "_confirm_recommendation_status", lambda *a, **k: "sent")
    r = sample(); document, job = docs.create_quote(r)
    r = store.transition(r["public_id"], "quote_ready", idempotency_key="flow-quote", actor="system", reason="test")
    r = store.transition(r["public_id"], "demo_payment_pending", idempotency_key="flow-pending", actor="system", reason="test")
    pay_url = payment.build_payment_url("https://example.test/api/mermaid",r["public_id"],"card-test-secret")
    assert send(r, document, docs.quote_message(r)+"\n\n"+guest.guest_copy('en')['checkout_link']+"\n"+pay_url)
    docs.mark_delivery(job["public_id"], True)
    expires = int(payment.time.time()) + 3600
    signature = payment.sign_payment(r["public_id"], expires, "card-test-secret")
    assert payment.complete_checkout(r["public_id"], expires, signature, "success").status_code == 200
    assert payment.complete_checkout(r["public_id"], expires, signature, "success").status_code == 200
    assert len(posts) == 2
    assert all(body['interactive']['type']=='cta_url' for body in posts)
    assert pay_url in posts[0]['interactive']['body']['text']
    closing = posts[1]['interactive']['body']['text']
    assert "☀️🏝️" in closing and "1 infant (9 months)" in closing and "USD 525.00" in closing
    assert closing.endswith(cards.copy_for('en')['closing'])
    assert all(doc['delivery_status']=='delivered' for doc in docs.documents_for_reservation(r['public_id']))
