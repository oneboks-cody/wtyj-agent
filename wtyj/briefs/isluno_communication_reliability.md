# Isluno communication and conversation reliability

Scope: Calvin’s 2026-09-09 request to investigate the full communication/conversation path, implement and deploy a consolidated correction, then invite customer testing. Builder owns source/host execution; existing Gatekeeper independently audits. No new agents, paid probes, agent messages, provider/model/token changes, cleanup or replay. Preserve all live records.

## Observed failure, not inferred from health

The12:40 introduction and12:42 hi reached backend16:40:53/16:42:02UTC, passed account/gate controls, entered debounce, each made one model call (831/824outputtokens), and saved a valid decision/outcome. Their outgoing plans were rejected; inbound status became send_failed/provider_send_failed. Bodies contained1122/1085characters and three reply buttons, no media. The sender discarded the original HTTP/platform error codes. Unbound message.delivered callbacks later appeared; the handler discarded them, so they cannot establish either reply’s delivery.

Zernio’s top-level buttons convert to WhatsApp interactive reply buttons: https://docs.zernio.com/messages/send-inbox-message . Meta-hosted reply-button specification limits body text to1024characters: https://whatsapp.github.io/WhatsApp-Nodejs-SDK/api-reference/types/InteractiveObject/#reply-button . The stored payloads violate this limit. Exact historical HTTPreason is unavailable; never invent it.

Prior failures were the separate invalid_reply_style defect. Its narrow correction passed offline but never tested realistic rendered length through the actual HTTP sender. Existing scripted model responses and stub successful sends left the final wire contract untested. A /health200 and image hashes establish deployment/runtime availability, not a working conversation.

## Full path and required corrections

| Boundary | Evidence / defect | Required behavior |
|---|---|---|
| External ingress/account | Both newest messages arrived, account/gates allowed | Preserve signed tenant/account routing; never persist foreign payloads |
| Durable acceptance/debounce | Both claimed/buffered once | Actual signed wire-shape tests through route, buffer, handler and sender; duplicates do not call/send again |
| Model context | Full catalog/rules plus duplicated history and persisted internals | Keep complete facts and rule IDs; one history copy, compact relevant state, one model call |
| Structured decision | Up to six speculative outcome branches under fixed output budget | Neutral common answer plus authoritative outcome; optional unused branches; strict consent/fact/type/size rules |
| Native controls | Own older control can become silent error | Explicit stale-control notice without mutation; foreign/unknown-scope controls remain safe |
| Handler error boundary | Business exceptions collapsed to empty strings | Distinguish duplicate/security suppression from verified customer operational failures; durable notice/incident when possible |
| Presentation/wire | Oversized button payloads were posted | Lossless bounded parts; final question/controls/media preserved, UTF16-safe limits validated before dispatch |
| Dispatch/pacing/window | Boolean failure hides boundary; initial window failures leave queued | Typed persistent status/reason, bounded pacing, no blind retry, never leave predispatch failure invisible |
| Required-part acceptance | One logical reply can have multiple parts | Per-part immutable content/idempotency/status/provider IDs; aggregate accepted only after all required parts |
| Failure/recovery | New delivery failures not linked to incidents; model progress falsely implies recovered communication | Operator-visible failures/queued holds; communication progress only after required parts accepted |
| Provider callbacks | delivered/read ignored; failed handler legacy-focused | Exact tenant/account/provider-ID correlation for Isluno discovery/quote/fulfillment; no guessed matches, no replay |
| History | Planned or rejected answer must not appear received | Store actual attempted parts and acceptance/delivery/read state separately; truthful last accepted question |
| Verification | Sender was bypassed | Mock HTTP boundary, not application/sender; vary real model output, media/buttons/long facts, failures and mixed booking intent |

## Design

Use existing JSON payloads for message parts and transport metadata; do not add a database schema. Keep model-authored prose and server-authoritative booking outcomes distinct. Reuse source-backed operations for missing optional branch rendering, without claiming success or inventing a next question. Keep quotes/payments dependent on actual required-part delivery.

Plan parts before dispatch. Split at presentation boundaries without truncating content or silently dropping controls. Respect text4096, interactive/caption1024 and control/card limits conservatively in UTF16 units. Claim and record each part before its one post; preserve accepted prefixes, stop on any rejected/unknown result, never automatically repeat uncertain work. Persist safe HTTP/platform codes and exact provider IDs without logging response prose or credentials.

A provider outage cannot guarantee a WhatsApp error message; do not claim otherwise. The system must retain a truthful operator-visible status when sending safely is impossible. Verified customer local failures should use a bounded native notice where the existing durable ownership permits it. Security, takeover, paused controls and duplicate suppression remain intentional and separately observable.

## Validation and release

Network-disabled fixtures only, using actual route/parser/adapter/buffer/Marina/renderer/sender with mocked SDK and HTTP. Include the two observed lengths and wire shape, realistic Unicode, long source expansion, multi-part acceptance/rejection/crash, late callbacks, stale buttons, window/control changes, mixed questions and booking consent. Reuse relevant existing booking/quote/payment/media tests. Publish exact evidence and unresolved limits.

One consolidated candidate and concrete bounded preserving-data rollout require Gatekeeper technical review before execution under Calvin’s explicit deployment instruction. Fresh proof must cover all current turns/plans/incidents, including failures, and preserve all customer/business tables. No historical rollout packet or stale proof reuse. Rollback is image-only with compatible existing JSON and no database restore. Do not invite another test until the deployed candidate’s wire-path and runtime checks pass; real model/customer quality remains distinct from offline proof.
