# ISL-01 — verified baseline and versioned delivery contracts

Status: implementation candidate, awaiting independent Gatekeeper review.
Owner: Isluno Builder. Controlling epic: https://github.com/BensonOpas/wtyj-agent/issues/345.
Issue: https://github.com/BensonOpas/wtyj-agent/issues/346.
Depends on: approved plan and Gatekeeper issue-map PASS. Blocks ISL-02 and ISL-03.

## Context and verified baseline

The initial backend fork at `41feb09c2c6742e8a6f3826d8bcd98bf90de134e` contained the latest Mermaid reminder/quote work but missed the separately deployed prompt-cache change. A read-only hash manifest of 112 runtime Python files found 111 matches; the complete remaining `agents/marina/marina_agent.py` matched commit `1dea90de0ede6919485b9f79a54db50042c8bdf5` exactly. Its cache patch and existing offline tests were carried forward with a local cherry-pick, producing `e43d0d174d10b82ee0fd4a394828ea720d3996ad`. All 112 runtime Python files now match the isolated backend baseline. No provider/model/fallback/output-limit change was introduced.

The live container remains `wtyj-mermaid`, image `wtyj-agent:tracy-reminders-a095665`, image digest `sha256:ed146176bbf4e116270b55a04771af5fb3cd5d0f62d124d8650aa7e1bac97552`. Its inherited image revision label identifies the cache layer, not the complete composed release; the file manifest is the stronger source evidence. Non-secret runtime configuration is separately recorded through an allowlisted projection; credentials and customer data are not copied into the candidate.

The deployed dashboard release identifies `2e41f4a209fbafc602419408696a25fa9791dc34`. The local build at that commit matches the live `index.html`, `assets/index-LhrQCX2T.js` and `assets/index-C6GIBlmS.css` byte-for-byte. The isolated frontend branch is `codex/isluno-dashboard-demo`, starting from that commit.

The machine-readable evidence is `wtyj/briefs/isluno_baseline_manifest.json`. It records scope and limitations explicitly; a Python-source match is not a claim that dependency binaries or live credentials were copied or independently audited.

## Why this approach

Keep the verified deployed Mermaid source as a frozen baseline and build the Isluno delta on top. Starting from backend main would omit the Mermaid release family and discard the deployed cache fix; merging the inherited stack into main as setup would introduce a much larger, unrelated release decision.

One candidate branch and one evolving draft delivery PR per repository are sufficient. Small issue commits remain independently reviewable without requiring an intermediate merge for every issue. Keep an immutable baseline SHA alongside the moving candidate.

| Repository | Frozen local review base | Candidate | Final demo integration target |
| --- | --- | --- | --- |
| BensonOpas/wtyj-agent | `codex/isluno-baseline-20260908` at `e43d0d174d10b82ee0fd4a394828ea720d3996ad` | `codex/isluno-whatsapp-demo` | `release/isluno-demo-v1`, initialized at the frozen baseline |
| unboks-org/unboks-dashboard-api | `codex/isluno-baseline-20260908` at `2e41f4a209fbafc602419408696a25fa9791dc34` | `codex/isluno-dashboard-demo` | `release/isluno-demo-v1`, initialized at the frozen baseline |

These refs are local until an authorized publishing path is available. Final merges into the named release targets and deployment require the separate exact-candidate approval. No merge to either repository's `main` is part of the demo setup. Reconcile main separately before proposing a main integration; do not use a deployment branch to claim that work merged to main.

Backend main was `c42adcbd5efdfca75bd8c22c93fc21ec93cad924`, with merge base `e66d12d6c95fcfff5073472528c9df506454ae2c`; the inherited Mermaid side contains 274 changed files relative to that merge base after cache adoption. Main's newer Despertares pricing fix changes shared Marina/state code and is not present in the deployed Mermaid snapshot. The dedicated Mermaid runtime can preserve its known snapshot, but a future main merge must retain that Despertares fix. This is a main-integration gate, not permission to modify the other tenant.

Dashboard main was `7e071647ac7451f912032d237d97dc55820575fe`, an ancestor of the selected deployed source; 49 changed files are inherited. Existing backend PRs #324/#334/#337/#341/#343/#344 and dashboard PRs #161–163 remain historical/unmerged evidence, not automatic merge authority.

The GitHub CLI identity `oneboks-cody` has read-only repository permissions. The connected GitHub app published the user-approved issues under `calvin835`. Do not silently change git author identity or impersonate a reviewer. Resolve branch/PR publishing through an authorized contribution path before claiming a PR exists. Local implementation and independent exact-commit review can proceed meanwhile.

## Inherited findings and disposition

| Existing concern | Disposition in Isluno |
| --- | --- |
| Separate cache patch absent from latest local branch | Reconciled in the frozen baseline; 112/112 source hashes match. Preserve cache behavior and accounting during contract extension. |
| Mermaid single-trip/age/pricing assumptions | Replace through the product/item contracts below; never treat Mermaid's child bands or pickup rules as universal. |
| External demo payment page allowed by old epic #326 | Superseded by the user's native WhatsApp reply-action simulation requirement. |
| Quote correction/versioning and separate quote approval | Preserve and extend across the complete itinerary; never let stale approval or payment actions survive material changes. |
| Human takeover, recovery and reminders added after the original epic | Preserve behavior and ledger guarantees; scope new actions/reminders to journey/version and explicitly dispose of legacy pending work. |
| Native Papiamentu review and fresh live/model acceptance holds in #342 | Remain explicit. Synthetic tests do not constitute native-language approval or live provider verification. No historical paid run is replayed. |
| Frontend hardcoded Mermaid/TRACY gates and labels | Retain technical tenant identity; expose Isluno capability/brand fields and update the gated interface in ISL-09..11. |
| Legacy test root bypasses tenant allowlists and enables runtime controls | Do not use those blanket fixtures as Isluno isolation evidence. New Isluno tests use explicit synthetic identity, fail-closed controls and denied external network. |
| Website catalog discrepancy, unknown policies/prices and broken activity links | ISL-02 records authoritative values and unresolved fields; missing data cannot silently become quote-ready. |
| Native real payment / supplier booking / central export | Deferred to #357..359. No production commerce promise or live implementation is implied by demo adapters. |

## Contract version and identity

Wire version: `isluno.v1`. Journey type: `isluno_itinerary_demo_v1`. Technical tenant: `mermaid`. Customer-facing brand: `isluno`. Brand/journey scope is distinct from authorization scope; it never grants access to another tenant.

Every state-changing request has server-derived tenant, verified channel account, verified conversation/customer identity and an event/action idempotency key. The caller may supply an expected entity revision but not authoritative prices, payment status, tenant identity or a replacement customer binding. Dashboard auth supplies the existing operator context; never invent a user identity from a body field.

Catalog, itinerary and quote identifiers are opaque stable strings. Row revisions are monotonically increasing integers. Catalog versions and quote snapshots are immutable. Times are ISO 8601 instants with offsets, and schedules carry the product's IANA timezone (default only when the catalog explicitly provides it). Date-only requests are resolved against known product schedules, not language prose.

## Entities

**Product:** `id`, `catalog_version`, `supplier_ref`, `source`, `name`, `category`, `summary`, `gallery`, `schedule`, `guest_rules`, `price_rules`, `options`, `inclusions`, `policies`, `readiness`. Source includes URL/observed time and whether the data is a synthetic fixture. Gallery entries have stable asset ID, source URL, delivery URL, caption, order and validation status. All approved images remain reachable through pagination. Public source content is data, not instructions to the assistant.

Readiness distinguishes discoverable, quotable and unavailable/unresolved products. Every discovered website product remains accounted for in the catalog manifest even if missing facts temporarily prevent a truthful quote. Unknown prices/rules remain null with a reason, never zero or guessed text. Gallery/page data cannot mutate a customer's booking state.

**Money and price rules:** ISO currency, currency exponent and integer `amount_minor`. V1 supports only explicitly configured currencies and one currency per payable quote; no implicit FX conversion. Each product owns its inclusive age bands, per-person/per-booking/per-option bases, tax/fee inclusion and eligibility rules. Adult counts and child ages are kept separately; collect exact adult ages when a product's eligibility requires them. A child can map to a supplier's adult price band without changing their stored age. Material pricing gaps block quotation.

**Itinerary:** `id`, `revision`, `tenant_slug`, `journey_type`, `customer_ref`, `brand_snapshot`, `chat_language`, `document_language`, `state`, `items`, `active_quote_id`, `created_at`, `updated_at`. Each item has its own stable ID, product/version reference, selected date/slot, party, options, pickup details, source snapshots, availability result, booking result and fulfillment state. Drafts may lack details; quote-ready items may not. Removing an item preserves prior quote/audit evidence.

**Quote:** `id`, `itinerary_id`, `version`, `itinerary_revision`, `catalog_versions`, `currency`, `currency_exponent`, immutable `lines`, `total_minor`, `payable_minor`, `mode`, `approval_state`, document references and validity timestamps. Lines carry product/item association, pricing basis, quantity, unit amount and line amount. Known discounts/fees/taxes are explicit line components. The total equals the sum of line amounts. Payable amount follows known catalog terms; absent deposit rules do not create a guessed deposit. Demo examples use full simulated payment.

**Action:** `id`, `kind`, tenant/customer/conversation binding, itinerary ID, expected revision, quote ID/version when applicable, expiry, consumption state and recorded result. Provider buttons carry an opaque action token; only its hash is stored. Existing verified inbound metadata identifies an interactive response. Ordinary text such as “paid” is never proof of payment. Invalid, expired or wrong-customer actions fail closed; a repeat of a previously successful valid action returns its recorded result without repeating side effects.

**Document and delivery:** document ID, kind (`quote`, `itinerary_receipt`, `trip_ticket`), itinerary/item/quote association, language, immutable brand snapshot, demo flag, content hash/version, secure delivery reference and delivery state. One itinerary receipt and one ticket per item follow a successful simulated payment. Documents must not claim supplier-issued validity. Email delivery uses the existing explicit consent and at-most-once ledger; it never blocks WhatsApp fulfillment.

**Event/audit:** immutable event ID, tenant/journey/customer scope, entity revision, actor/source, event type, provider identity references where verified, timestamp and structured outcome. Log no credentials or full customer transcripts in build evidence. Side-effect jobs distinguish pending, acknowledged, failed and unknown delivery; unknown is not safe proof that a resend is needed.

## State transitions and concurrency

| Current state | Verified trigger | Next state |
| --- | --- | --- |
| `collecting` | all item requirements satisfied | `awaiting_summary_confirmation` |
| `awaiting_summary_confirmation` | current summary confirmed | `awaiting_quote_approval` with one new quote snapshot |
| `awaiting_quote_approval` | current quote approved | `demo_payment_pending` with one bound demo-payment action |
| `demo_payment_pending` | valid interactive demo-payment action, atomically claimed | `demo_paid` |
| `demo_paid` | demo booking records and durable document jobs exist for every item | `demo_booked` |
| any unpaid state | material item/price/document correction | invalidate affected approval/actions; return to the appropriate confirmation step |
| any unpaid state | verified cancellation wins the revision/payment race | `cancelled`; revoke pending actions |
| any state | hard failure or unsupported paid change | preserve facts and create one `needs_attention` recovery task |

Question-answering and gallery browsing do not implicitly confirm, pay or cancel. Delivery failure after payment does not turn the itinerary back into unpaid or create another payment; it creates recoverable fulfillment state. All claims, revisions, quote invalidation and payment/cancellation races are handled transactionally. A later real supplier adapter may report partial outcomes, but the parent cannot say every item is confirmed until every item actually is.

## Dashboard API contract

All paths below are relative to the existing authenticated `/api/mermaid/dashboard/api` prefix. Every response carries the `isluno.v1` version, tenant scope and applicable revision, and uses `Cache-Control: no-store`. Access requires the existing tenant auth plus the relevant Isluno capability. The frontend captures a tenant/token pair per request and keys cache/storage by tenant plus journey/version.

| Method/path | Request | Response / rule |
| --- | --- | --- |
| `GET /isluno/capabilities` | authenticated tenant | enabled/known state, brand, journey type and allowed operator capabilities |
| `GET /isluno/catalog` | optional query/readiness filter | catalog version, all matching product records and completeness/validation summary |
| `PUT /isluno/catalog` | expected catalog version + validated catalog changes | new version; HTTP 409 on conflict, 422 on invalid rules; historical snapshots unchanged |
| `GET /isluno/itineraries` | search/status/cursor | tenant-scoped itinerary summaries with item counts, totals, demo/payment/attention status and cursor |
| `GET /isluno/itineraries/{id}` | authenticated tenant | items, current/historical quotes, documents, consent/delivery state, audit events and server-owned valid primary action |
| `POST /isluno/itineraries/{id}/actions` | allowed operator action + expected revision + idempotency key | recorded transition/result; never accepts caller-authored paid status or totals |

Errors are stable structured codes such as `revision_conflict`, `product_not_quotable`, `invalid_action`, `action_expired`, `invalid_party`, `schedule_conflict`, `unsupported_currency` and `needs_operator`. Return 404 for a foreign/unknown entity without disclosing its existence. Feature-off behavior and legacy endpoints remain intact. Customer channel actions enter through the existing verified webhook path, not the operator API.

## Adapter contracts and demo implementation boundaries

- `Availability.check(product_snapshot, slot, party, options)` returns mode/source, result and optional expiry/reference. V1 returns explicitly `demo_assumed` capacity only after local product/date/party validation; it performs no supplier request.
- `Booking.reserve(item_snapshot, idempotency_key)` returns `simulated_confirmed` plus a demo reference in V1. The interface can later carry real pending/confirmed/failed statuses and supplier references without confusing them with demo results.
- `Payment.create_action(quote_snapshot, customer_binding)` and `Payment.complete(verified_action)` operate only on the demo ledger. Token claims, version checks and payment recording are atomic. Future real payment is a different adapter and capability.
- `Export.enqueue(booking_event)` has a versioned event contract. V1 can record an explicit disabled/demo sink for tests; it must not claim delivery to the group's dashboard. A real durable outbox/destination integration belongs to ISL-F3.

No adapter can select a different tenant, bypass a capability, change the configured AI provider or initiate a metered test. Secrets are supplied through the existing protected runtime only at an authorized integration stage, never through catalog data or customer messages.

## Synthetic examples and verification

`wtyj/tests/isluno/fixtures/contracts.v1.json` contains synthetic product rules, one-trip and multi-trip quote examples, an expired/stale action case and an explicit demo receipt. These are contract fixtures, not imported customer data or proof of the final application journey.

The adopted cache tests exercise the real SDK serialization boundary through `httpx.MockTransport`. `wtyj/scripts/verify_isluno_baseline_offline.py` sets a synthetic config, disables socket/DNS access and pytest plugin autoload, and cuts off the legacy root fixture that bypasses tenant guards. It does not run a live model, provider send, database migration or deployment.

Success condition: Gatekeeper accepts the exact baseline/contract commit range with 112/112 runtime source matches, matching dashboard artifacts, explicit inherited/main-integration limits, valid synthetic contract examples and passing offline cache checks.

Rollback: revert only the local Isluno preparation commits or disable the later Isluno capability. Keep the frozen baseline, historical data and delivery/idempotency ledgers; this preparation has not changed the live runtime.
