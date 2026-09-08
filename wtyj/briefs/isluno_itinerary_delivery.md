# ISL-04 durable itineraries and itemized pricing

Issue: https://github.com/BensonOpas/wtyj-agent/issues/349
Accepted base: 762d1f335b773ef6ee24b6c6646037a4ad575b5e (ISL-03).
Repository BensonOpas/wtyj-agent; technical tenant mermaid; isolated local demo candidate. Builder-only implementation in this issue, no additional worker.

## Contract and behavior

ItineraryStore creates, reads and revises a draft itinerary under the verified six-part JourneyScope. Every mutation supplies an opaque request ID and expected revision. A repeated identical request returns its original response, including after later edits; reusing that ID with a different body conflicts. SQLite BEGIN IMMEDIATE serializes edits; one competing expected revision wins. Invalid selections and conflicting schedules roll back without consuming a request ID, so the customer can correct them.

Each item selection explicitly supplies item_id, product_id, date, slot_id, guest_ages, options (option-ID-to-quantity mapping) and pickup (boolean). Pricing retains the catalog revision/version, original source/claims, selected rule snapshot and sample label. Add/update prices that item from the current validated catalog; other items retain their snapshots. Removing an item does not remove old revisions. Parent/item payloads are stored together as an atomic JSON document with indexed parent identity/revision; separate immutable revision and request tables provide historical reads and replay. No legacy tables are read or migrated. SQL triggers reject updates/deletions of historical revisions.

All money uses integer minor units and an explicit currency exponent. One itinerary supports one currency; conversion and unverified taxes fail closed. Age bands, adult-accompaniment requirements, per-booking capacity, required/optional extras, and included/priced/meeting-point pickup rules are applied. Option quantities mean people for per_person, zero/one for per_booking, units for per_unit; required per-person extras and selected per-person pickup cover the entire party. No guessed adult age is introduced.

Departure date/day/slot, past departure/check-in and age constraints return stable correctable error codes. Known occupancy intervals include published or sampled check-in through trip end, compared across timezone-aware instants. Overlap rejects the edit. No unverified travel buffer is invented; downstream UX must not promise physical transfer feasibility merely because known intervals do not overlap. Ambiguous/missing DST departure times reject. Availability is always demo_assumed; no supplier reservation or money movement is claimed. Limits of 50 items and 1,000 guests are defensive storage/input bounds, not advertised supplier capacity.

## Dashboard read projection

Existing authenticated Isluno router now exposes:

- GET /dashboard/api/isluno/itineraries with account_id, conversation_id, customer_ref, limit (1–100, default 50), offset (default 0).
- GET /dashboard/api/isluno/itineraries/{itinerary_id} with the same scope and optional positive revision.

Both require the existing dashboard authentication dependency, revalidate the configured account and runtime tenant, and return Cache-Control: no-store. Different conversation/customer keys cannot read an itinerary; unauthorized account/disabled feature returns 403, missing scoped itinerary/revision returns 404. List results carry ID, revision, status, item count, totals and update time. Detail returns frozen product/pricing/source evidence and itemized lines. These are operator reads; write interaction and customer routing remain later issues. Capability itinerary_booking remains false until customer integration exists.

## Representative exact examples

Synthetic cruise: ages 35, 34, 8 at 10,000 adult / 5,000 child minor units totals 25,000 USD cents. Two such trips total 50,000 cents. After a catalog adult-price update to 12,000, adding a second trip leaves the first at 25,000 and prices the second at 29,000: total 54,000.

Synthetic ages 35, 8, 2: base 15,000 + booking pickup 2,500 + required gear 3 × 500 + photos 2 × 1,000 = 21,000 USD cents. Included pickup adds zero; a per-booking product priced at 30,000 remains 30,000 for its permitted party.

Actual imported Jungle Tour demo: ages 3, 4, 11, 12 preserve published bands (free, 1,000, 1,000, 2,000), totaling 4,000 USD cents. The snapshot still labels the complete rule envelope as demo_sample because other operational/currency/tax assumptions remain samples.

## Evidence and limits

python3 wtyj/scripts/verify_isluno_offline.py: 52 tests passed in 0.337 seconds, sockets/DNS denied, synthetic configuration and temporary databases, no legacy pytest root fixtures. Includes all prior 36 checks plus single/multi-trip reconciliation, restart, duplicate/concurrent requests, immutable history, catalog edits, source-aligned Jungle ages, all 31 sample products, invalid/overlapping dates, child/option/pickup/capacity rules, mixed currency/unverified tax rejection, tenant/customer binding, additive legacy coexistence, disabled access/replay and actual router HTTP projections/authentication. git diff --check passed.

Historical revisions are the immutable pricing input for ISL-07 quote approval, not a claim that quote approval/payment already exists. No messaging/model calls, email, customer data access, supplier reservations, deployment, provider/number/live configuration or dependency changes. Gatekeeper acceptance of this exact issue range remains required before advancing.
