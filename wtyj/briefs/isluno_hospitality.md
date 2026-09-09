# Isluno hospitality conversation rewrite
Status: Building. Issues: #351, #356. Existing draft PR360.

## Context
The controlling 19-section user brief is preserved in isluno_hospitality_user_brief.md.
Name capture currently selects an administrative reply. Guest-only changes can
complete pending selections or request post-booking review. Preliminary render()
is stored before the actual response and media are assembled.

## Design and trust boundary
One existing Marina call returns separate browsing memory, explicit action authority,
and conditional natural presentation. Python applies authorized actions, selects
presentation branches by actual outcome, and substitutes versioned source facts and
calculated result bindings. Browsing never completes a selection or changes a quote.
No second model call, provider/model change, schema change or live test is needed.

Natural prose is model-authored. Structural validation can reject invalid source
references and unsupported structured claims, but cannot prove arbitrary prose is
semantically free of invented facts. Prompt grounding and independent conversation
review are therefore explicit boundaries, not a mathematical factuality guarantee.
A second model evaluator and fixed administrative templates were rejected: the first
adds calls and does not prove correctness; the second violates the hospitality goal.

Session JSON stores browsing preferences separately. Assistant history is recorded
with final attempted body/media/button meaning and durable provider outcome at the
same transaction as dispatch result. Accepted means accepted by provider, not read or
received by the guest. Failed and ambiguous attempts remain labelled; neither is
replayed. Only accepted photos advance the known-shown set; ambiguous photos remain
excluded from automatic resend. A new explicit resend remains a new guest action.

## Files / section map
- isluno_hospitality.py: typed presentation, memory, consent and delivery history (1–5,7–11,14,16).
- isluno_conversation_understanding.py: single-call contract/grounding (1–5,7–13,16).
- isluno_conversation.py: mutation separation, state, mixed outcomes, failure (3,7,8,12–16).
- isluno_discovery.py: product-associated photos, pagination and natural bodies (5,6,14).
- isluno_delivery.py and isluno_quote_delivery.py: final durable delivery history (14,15).
- tests/isluno/test_hospitality.py and offline evidence: complete conversations (17,18).

## Tests / success
Use real Marina orchestration with a mocked SDK and stub transports in a network-
disabled local container. Verify each of the 15 mandatory multi-turn scenarios,
representative supported languages, negative consent/fact cases, media progression,
actual outgoing history, duplicates, mixed outcomes and preserved transaction state.
Publish complete synthetic transcripts, six reasoned quality dimensions and limitations.
Gatekeeper independently reviews the exact candidate, not builder scores alone.

## Release and rollback (section 19)
Current live source c04512c stays in place. The previous no-reply deployment approval
is consumed; this rewrite requires an exact reviewed candidate and new situational
release authorization. Before any deployment, verify current tenant/account/image,
Nr2 session where applicable, both ingress gates, worker drain and live data preserving
maintenance capability. Old single-turn preservation proofs must not be reused against
fresh conversations. Build/upload bounds, image hashes, operational rollback and
maintenance evidence must be reviewed as a concrete packet. Preserve all fresh data.
Rollback uses the prior verified image and compatible session JSON, never a database
restore. Deployment does not itself prove real customer conversation quality.
