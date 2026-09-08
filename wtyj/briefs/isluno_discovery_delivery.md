# ISL-05 trip discovery and complete WhatsApp galleries

Issue: https://github.com/BensonOpas/wtyj-agent/issues/350
Accepted base: 148491e02a031941cea9ec4c768abdea2998620d (ISL-04).
Repository BensonOpas/wtyj-agent; technical tenant mermaid; isolated local candidate. Builder-only implementation, no new worker.

## Delivered integration

The real WhatsApp orchestrator routes explicitly enabled Isluno before reading/resetting legacy Mermaid intake. Existing ignore/moderation and webhook takeover/account/worker controls remain in place. Discovery has a separate context and retains the existing 50-turn/hour bound. The existing WhatsAppZernioChannel supplies customer, conversation, account, signed inbound timestamp and interactive metadata; typed action tokens are not accepted as button replies.

Marina's existing process_message entry point selects up to three catalog products and source fact keys under an Isluno tool contract. Model/provider/max-output settings remain claude-sonnet-4-6/2048 and the existing zero-retry bounded SDK mode is reused. Customer-facing trip facts are rendered from the selected catalog fields, not generated model prose. Source text and long information remain accessible in successive More info replies. The model receives the last selected product context for follow-up questions. Original source copy remains in its catalog language; translating that copy and the full booking conversation are subsequent integration work. UI controls/demo notices cover six configured languages; Papiamentu remains pending native review.

Durable plans bind the exact tenant/account/conversation/customer/journey/schema scope and catalog revision. Opaque 20-character actions select a product, request more photos/info, or emit a scoped add_trip intent for ISL-06. A newer view, edited catalog, expired action, foreign customer or ordinary typed token fails closed. Replayed inbound IDs return the recorded plan. Choose/Add/More photos use native reply payloads and no external booking URL. Browsing creates no itinerary/payment rows; an Add tap records selection intent, not a booking. Human intent is retained and surfaced to the downstream workflow without claiming staff have acted.

The existing ZernioSender dispatches the stored plan using attachment_type=isluno_discovery. Plans retain send state, recipient pacing and provider IDs. Every POST uses a stable phase-specific idempotency key and is attempted once. Final account/worker guards and current-plan checks run before dispatch. Timeouts, partial responses, ignored-field warnings, missing message IDs and interrupted claimed sends stay unresolved and are not blindly retried. Remote rejections do not trigger automatic fallback: the documented error envelope does not establish a definitive no-send media failure. Ordinary-image mode is selected before dispatch, and locally invalid media uses the bounded valid-image/text path. Accepted is provider acceptance, not delivered/read or verified visual rendering; late reconciliation/recovery is ISL-12.

## Gallery and media matrix

| Gallery size | Native candidate pages | Ordinary-image mode |
| --- | --- | --- |
| 1 | one ordinary image | 1 image |
| 2 | 2-card page | 2 successive image replies |
| 10 | 10-card page | 10 successive image replies |
| 11 | 10-card page + ordinary image | 11 successive image replies |
| 21 | 10-card + 10-card + ordinary image | 21 successive image replies |

Only the requested page is sent. More photos retains the same product and advances through every gallery entry. Invalid/missing entries are reported and never replaced with another trip. Ordinary-image mode sends one valid image per requested page. No automatic post-rejection resend is attempted; there is no burst of fallback sends. All cards on a native page have matching reply-button layout. Per-recipient sends are spaced by at least six seconds across plans; window checks happen before and after pacing. A closed/unavailable window does not trigger a template or other channel.

MediaLibrary verifies the existing catalog digest/length/dimensions and converts approved originals to JPEG in memory, bounded to 1600 pixels and 5 MB. No original is edited or redownloaded. GET /r/isluno/media/{sha256}.jpg serves only catalog-listed verified assets while the strict Isluno profile is active. Public assets contain no customer data; unknown IDs/path traversal fail. The unapplied feature fragment now includes isluno.media_base_url=null and native_carousels=false. Release configuration must supply the exact public URL ending in /r/isluno/media under the Mermaid API prefix. No live URL/config was set. Missing base configuration produces the explicit unavailable-media path.

## Provider evidence and precise gap

Read-only official provider documentation inspected 8 September 2026:

- https://docs.zernio.com/messages/send-inbox-message and its OpenAPI at https://docs.zernio.com/api/openapi: session-window restriction, media carousel support without a commerce catalog, 2–10 cards, reply-button metadata, image-with-buttons, per-recipient pacing, idempotency and ambiguous failure semantics.
- https://docs.360dialog.com/docs/messaging/message-types/interactive/media-carousel.md: matching card button layouts, media headers and carousel text bounds.

Zernio documents pass-through quick-reply media cards but does not fully enumerate their nested card schema. The candidate uses card type button with quick_reply action entries. Exact provider acceptance/rendering of that nested native shape remains unverified; Meta's direct documentation fetch returned 429. Native mode therefore remains default-off in the unapplied configuration fragment, with complete ordinary-image/reply pagination available. Synthetic payload checks are not a claim of native rendering. No website/URL-button substitute is introduced.

## Offline evidence

Original reviewed suite: 72 tests passing; corrective results are recorded below. This includes the prior 52 checks, gallery sizes 1/2/10/11/21, single-image mode, missing media, stale/foreign/typed/expired actions, unchanged product association, no booking rows, actual channel normalization and sender/orchestrator routing, follow-up product context, model SDK contract, window/pacing/replay, ordinary-image mode, unresolved/crashed/superseded delivery, and public media identity gating. All 234 unique originals were actually decoded, converted and decoded again as bounded JPEGs.

Because the full social orchestrator imports the existing quote renderer, local verification needed the repository's already-pinned reportlab==4.4.3. It was installed only into tmp/isluno-test-venv using existing system packages; no runtime dependency/lockfile change. Reproduce with that environment or a normal requirements.txt environment:

    tmp/isluno-test-venv/bin/python wtyj/scripts/verify_isluno_offline.py

The inherited Mermaid SDK-boundary cache regression runner also passed all 7 tests (0.38 seconds), with network denied and legacy root pytest fixtures excluded. The frozen baseline manifest still matches 112 source hashes. No provider/model/WhatsApp/email test calls, real reservations, money movement, production data/config/phone changes, merges or deployments occurred.


## Gatekeeper corrective review

Corrective base: 2feccc332947dc299555ffa68044e68e4e9da74d. All three findings were retained and corrected within ISL-05:

1. Plans now store product_fact_keys per recommended product. A Choose reply uses that product's own fact set; if none were selected, it explicitly opens that product's summary. The actual channel/handler regression covers one product with inclusion_0 and another without inclusions. The second offered choice succeeds and never displays the first product's inclusion.
2. Empty fact keys plus a clarification signal produce a localized, server-owned unavailable-answer prompt instead of unrelated summary/photos. An Ask the team reply records explicit human intent without claiming staff have acted or creating a booking. No-product results ask the guest to describe the unmatched activity. Arbitrary generated question prose is not rendered as business-fact authority. Synthetic supplier/safety questions, explicit help replies and no-match clarification are covered.
3. The response parser and send_plan are tested together with mocked HTTP responses matching documented top-level error/code/platformError envelopes, nested legacy errors, partial failures and 5xx. Each attempt is made once; repeating the plan sends nothing. Automatic fallback after remote rejection is unsupported because available evidence cannot safely establish no-send status, so the earlier inferred media-error mapping and resend branch were removed. Default-off native mode and complete ordinary-image/reply pagination remain. This supersedes earlier claims of validated automatic post-rejection fallback; no native rendering claim is made.

The original 72-check and 7-cache-test evidence remains valid for that original SHA, subject to these findings. Corrective verification adds heterogeneous action integration, safe clarifications/help replies and parser-through-send-plan error coverage. No source/image download or live provider test was repeated.

Corrective result: `tmp/isluno-test-venv/bin/python wtyj/scripts/verify_isluno_offline.py` passed **74 tests in 12.451 seconds**, with sockets/DNS denied. `git diff --check` passed. The Marina SDK/cache code was unchanged by this correction; the prior 7-test result is retained rather than represented as a fresh run.
