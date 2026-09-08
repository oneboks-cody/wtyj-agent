# ISL-08: in-chat demo payment and fulfillment

Issue: [ISL-08 #353](https://github.com/BensonOpas/wtyj-agent/issues/353). Issue base: `c2f3c49f8b43c0827048160d2d60e997a5f2917e`, independently accepted ISL-07. This candidate awaits independent review. Seven of fourteen implementation issues are accepted in candidate; none are merged/verified. No release action is requested.

## Delivered behavior

The approved-quote job now offers a native **Complete demo pay** reply button (localized within the WhatsApp button-title limit). It requires no website, signing-secret setup, card entry or payment provider. Its random action token binds the exact customer scope, quote and current itinerary, and expires at the earlier of one hour or the quote expiry. Plain text cannot complete payment.

Minting revalidates the approved quote inside its transaction. Consumption revalidates the current approved quote, exact itinerary revision/material, source catalog revision, customer/account/journey, expiry, accepted payment-prompt delivery and open operator requests inside `BEGIN IMMEDIATE`. The existing final mutation guard is checked before preparing completion and again before commit, so a worker/takeover change rolls back the whole transition. A unique paid record per quote and per scoped itinerary, immutable records and durable event fingerprints serialize duplicate/concurrent taps. A renderer failure rolls back payment, itinerary, documents and fulfillment together.

The itinerary transitions through the existing immutable-version writer to `demo_paid`, with no repricing. The paid record retains the exact approved quote snapshot, the paid itinerary revision, time, mode `simulated`, `real_money_charged=false` and `supplier_booking_made=false`. It receives one combined receipt/itinerary and one independently identified demo ticket per paid item. Database uniqueness enforces ticket identifiers and one document per paid item, plus one combined receipt. Every PDF includes the exact quoted prices, per-item guests/pickup/source/sample rules, unique ticket identifiers and explicit no-real-money/no-supplier-booking notices. A ticket's header uses that item's guest rather than another item's party name.

The existing PDF renderer and canonical quote projection are reused with explicit receipt/ticket modes. The existing `isluno_quote_jobs` and per-part delivery ledger, generic file-attachment sender, provider account/window checks, final send guard and shared customer pacing are reused. Legacy Mermaid's web checkout and single-reservation tables are never invoked or migrated. The new guarded payment boundary and immutable paid snapshot are the seam for later adapters; no real payment or supplier adapter is present.

Implemented capability reporting now exposes itinerary booking and demo payment when the existing strict Isluno feature is explicitly active. The feature remains off in the unapplied patch; catalog editing remains false. No live configuration, number, routing, credentials or provider/model setting was changed.

## Recovery and truthful delivery

Each confirmation, receipt, ticket and optional-email prompt is a persisted fulfillment part before a send can occur. The real `isluno_fulfillment` sender delegates to the accepted one-attempt durable sender. It skips known accepted parts, resumes remaining queued parts and stops at rejected, ambiguous or crash-claimed parts without blind retries. Ambiguous WhatsApp fulfillment creates a scoped operator review request; successful completion resolves only that fulfillment request. Provider acceptance remains distinct from customer delivery/read evidence.

A structured `documents` request resumes the same paid job using the new verified inbound timestamp. This recovers an interrupted sequence after restart, including after the original payment-action expiry, without paying again or repeating accepted sends. A duplicate payment tap within its validity period returns the same job. Expired payment actions cannot create payment. A verified expired native tap can return a fresh prompt only after revalidating that same still-approved quote; a second explicit tap is required to complete. Repeated refresh requests reuse the fresh prompt. Expired or corrected quotes cannot be refreshed this way; they need a new quote review. The independent paid-document recovery path remains available. Paid PDFs have opaque 128-bit media capabilities with 30-day expiry, the existing private/no-store public document route, and current tenant/account identity checks. They retain their frozen paid content after later catalog changes. They are never public listing endpoints.

Restart recovery is driven by a verified customer document request or the existing sender retry; there is no new timer, startup sweep or provider polling loop. An ambiguous/claimed part cannot be safely inferred to be unsent and requires operator/provider reconciliation before any future resend. This issue deliberately fails closed rather than inventing delivered status or treating absence of evidence as a safe retry. General reminder/legacy recovery remains ISL-12.

## Consented optional email

The same single structured understanding call can extract `email` plus an address. The existing Mermaid email-address normalizer is reused. Missing/invalid addresses produce a request for the address, with no queued email. A valid address is displayed in WhatsApp with a separate native **Email receipt** confirmation. The random confirmation token binds the paid record, exact normalized recipient and customer scope, expires in one hour and requires a verified interactive event with a message ID. Text/address extraction alone is not consent.

A changed address invalidates unconsented old buttons and cancels an old address's still-queued email. Consent creates at most one email record per paid record/recipient, with its source confirmation and stable Message-ID. Duplicate confirmations return the same acknowledgement and do not enqueue another send. Accepted, failed, ambiguous and crash-claimed email attempts are not automatically resent. Explicit fresh consent can requeue an address that was cancelled before any attempt.

Email dispatch is independent of WhatsApp fulfillment. After the WhatsApp send attempt, the sender schedules a background worker only when a consented email is queued. The worker atomically claims one queued email. It owns that durable claim rather than the already-finished inbound worker's lease. Before claiming and again before SMTP it checks the current strict customer scope, fresh tenant automation/inbox controls, account ownership, block and human-mute controls. Therefore it can finish after the inbound reply is marked replied while still respecting current automation controls. A crash after claim fails closed on restart. Email success/failure never changes the paid record or suppresses WhatsApp documents.

The existing outbound-only SMTP transport sends the combined receipt PDF. A narrowly scoped optional, validated `sender_display_name` field gives Isluno messages `TRACY | Isluno`; callers that omit it retain the original Mermaid branding. No SMTP credential was inspected or changed. Accepted means SMTP accepted DATA, not inbox delivery. The operator payment projection includes recipient/status/error/Message-ID and WhatsApp per-part provider IDs.

`GET /isluno/payments` reuses the host dashboard authentication and exact customer scope with no-store headers; it returns the latest 20 immutable paid records, ordered document metadata, per-part delivery state and email state. Dashboard UI remains a later issue.

## Offline evidence

The full final network-denied suite passed **136 tests in 16.488 seconds**. It includes **19 payment/fulfillment checks**, all 19 quote checks, all 24 conversation checks and the existing identity/catalog/itinerary/discovery/media checks. A focused 19-check run passed in 2.468 seconds before the final fresh-prompt reuse assertion; the full final run covers that assertion. No legacy root pytest fixtures were loaded.

Coverage includes actual normalized handler-to-payment-to-document flow, real sender dispatch and public/operator routes with deterministic adapters; two concurrent payment taps; wrong-customer/plaintext/expired/corrected/catalog-changed actions; interruption after accepted delivery; ambiguity and crash claims; immutable paid data; renderer and takeover rollback; item-specific ticket guests; database-enforced uniqueness; exact-recipient consent, address changes, duplicate emails and SMTP ambiguity; MIME branding/legacy fallback; asynchronous scheduling and independent email controls after the inbound lease ends. No external service was called.

The six accepted ISL-07 quote PDFs were regenerated and all hashes matched exactly after the renderer extension. Earlier independent cache evidence remains separately attributed; shared Marina/model/cache implementation is unchanged. The existing 234 local-image conversions run in the full suite without refetching sources or images. Runtime dependencies and lockfiles are unchanged.

Reproduce tests with `tmp/isluno-test-venv/bin/python wtyj/scripts/verify_isluno_offline.py`. ReportLab 4.4.3 and pypdf 4.3.1 are existing root requirements. The runner denies external sockets and DNS. External test spend: **$0**. No WhatsApp, email, model/provider, supplier, real-money, live-config, SSH, merge or deployment action occurred.

## Rendered samples

`wtyj/scripts/render_isluno_paid_samples.py` runs the actual synthetic conversation/quote/payment pipeline with every external socket and DNS request denied. It exports six-language paid-record JSON, a hash/page manifest and **18 PDF review fixtures** under `output/pdf/isluno-08`: one six-trip combined receipt and two representative individually identified tickets for each of `en`, `nl`, `de`, `es`, `pt`, `pap`. The actual payment creates all six item tickets; the JSON inventories all of them. Each receipt totals **USD 1500.00**, and each displayed ticket totals **USD 250.00**.

The receipts have four pages each and the tickets one page each: **36 pages** total. All final pages were rendered with Poppler at 100 dpi and visually inspected in six complete contact sheets. Long accented names and product titles wrap correctly, ticket identifiers and prices are legible, and every page preserves the demo/no-money/no-supplier-booking notice; each item keeps its sample-rule label. The final regenerated PDF hashes match the inspected bytes. This verifies layout and deterministic routing, not native-language quality or live provider rendering/delivery.

## Rollback

Disable the existing unpublished Isluno feature; retain all paid snapshots, document bytes, consent and delivery ledgers. Incomplete or uncertain fulfillment remains visible to operators. Do not erase payments, reuse stale buttons, replay attempted sends or copy these synthetic records into a live database.
