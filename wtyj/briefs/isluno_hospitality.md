# Isluno hospitality conversation rewrite
Status: Source 3a5f524 deployed on 2026-09-09. Two live introduction failures under investigation; typography correction in progress. Issues: #351, #356. Existing draft PR360.

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
Source 3a5f524 is live; the preserving-data deployment receipt is in output/isluno-hospitality/deployment/RECEIPT.md. Any correction requires independent exact-candidate review and a freshly bound preserving-data operator packet. Existing authority applies only within its verified scope. Preserve fresh failures and customer records; no replay, cleanup or database restore. Deployment does not prove live model quality.

## Live incident: invalid_reply_style (2026-09-09)
Both fresh introduction turns failed at hospitality.validate before normal rendering, with invalid_reply_style at16:18:17UTC and16:18:55UTC. Output usage was876 and808tokens. No tool input/stop reason or style subpredicate was retained. The failing predicate combines nonstrings, overlength text and Unicode dash punctuation; the exact live subcause cannot be recovered from that evidence.

The offline SDK-boundary regression reproduces the same failure from a synthetic otherwise-valid dash-bearing welcome. This is a tested variant of the known boundary, not a claim that model output was captured. Existing scripted fixtures supplied compliant text and did not test cosmetic deviations.

Corrective scope: keep type/size/authority/fact/result validation, split type and length diagnostic codes, and mechanically normalize spaced em/en sentence breaks to commas and preserve numeric ranges/compound names with ASCII hyphens only after binding expansion in final presentation. This preserves range/name meaning and binding identifiers. Do not truncate text or add model calls. Persist/send the normalized final text and question. Log bounded structural counts and an allowlisted SDK stop reason for structural failures without guest/model/source content. Add SDK-boundary checks for normal/unused-branch punctuation, final delivery history, source bindings, no booking/payment mutation and malformed data still failing safely. Provider/model/token limits stay unchanged; tests run network-disabled with mocked SDK and transport.
