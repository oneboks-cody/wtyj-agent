# ISL-02 — complete public catalog, gallery library and demo rules

Issue: https://github.com/BensonOpas/wtyj-agent/issues/347. Epic: #345.
Dependency: ISL-01 accepted in candidate at `cfeb101e5fc1fc91dec9e70e39409c766ef04dfb`.
Status: implementation candidate; independent review pending. No release or live writes.

## Scope and completion check

One active implementation issue: import every distinct published product and its actual gallery; retain stable IDs/source evidence and all approved images; validate per-product rules; publish catalog edits with revision conflicts and immutable history; prove those contracts through focused offline tests. No customer journey, API route, provider send or dashboard implementation is claimed by this issue.

Completion requires the inventory/asset counts to reconcile, unknown supplier facts to remain explicit, separate demo rules to be labelled and versioned, source and historical snapshots to survive edits, and the actual catalog store/asset validation tests to pass without external network access.

## User clarification accepted during this issue

On 8 September 2026 Calvin answered **“Allow labelled sample rules for the demo”** when asked how to handle missing child prices, departure times and extras. Gatekeeper recorded this in #347 and #345. This permits fully exercising the demo without pretending that missing supplier facts have been verified.

Each product keeps `price_rules`, `guest_rules`, `schedule`, `options`, `pickup` and `policies` unresolved at the supplier/source level. A separate `demo_rules` envelope contains its own version, `user_approved_demo_sample` authority, approval reference, explicit label and `real_booking_eligible: false`. `quote_rules(..., mode="demo")` selects the effective rules with their version and provenance. A real-mode request is rejected. Quote/itinerary/document implementation must persist that selected snapshot and expose sample status; the catalog alone cannot prove downstream display.

Known published times, minimum ages, meeting points, included pickup and age fares are retained in `source_claims.published_facts` with provenance and reflected in the effective demo rules. For example, Afternoon Explorer starts at 13:00 from Playa Daaibooi, Aquafari retains its age-12 minimum, and Jungle Tour retains its published 0–3 / 4–11 / 12+ price bands. Remaining gaps use sample USD starting fares, half-price child bands where unspecified, explicit sample departure slots and a USD 25 sample transfer only where pickup is not already published as included. Each rule envelope lists source keys reused and remaining assumptions. These sample values are not supplier safety requirements, tax advice or confirmed transport. Operators can edit them in the later gated dashboard.

## Inventory and asset result

- **31 distinct product pages**, reconciling the historical count of 31 with no count delta. The source set is the unfiltered Explore page plus homepage links at capture time. Unpublished owner/supplier inventory is not claimed.
- **290 distinct gallery source URLs**, **287 product/gallery associations** after three same-content duplicates within a product were accounted for separately.
- **234 unique image files**, 56,823,688 bytes. Every imported gallery asset passed supported-image decode/type/dimension/size checks. **Zero missing assets.** Repeated thumbnails, recommendations and customer-review content are excluded.
- Original WebP/JPEG/PNG bytes are stored by SHA-256 under `wtyj/assets/isluno/`. Public source association is retained without expiring Google query signatures. Delivery URLs remain unset until stable application serving exists; this issue does not claim WhatsApp delivery or image-format compatibility at that boundary.
- All 31 products are discoverable and quote-eligible **for the labelled sample demo**. All retain source-level unresolved facts and have no verified exact supplier-price set.

The public source manifest is `clients/mermaid/config/isluno_inventory_report.json`; the single effective catalog is adjacent `isluno_catalog.json`. The report records the original source import before the sample overlay and the later text-only correction separately. Its initial per-product readiness is a historical import observation, not current effective readiness; the catalog records current source facts and effective demo readiness.

Website discrepancies remain explicit: the public taxonomy places paddleboarding and flyboarding under scuba diving and skydiving under off-road; provider mapping is unverified; starting prices are not complete guest/extra/currency rules; the generic guarantee/deposit copy is not treated as verified provider policy. None silently enables real booking.

## Implementation and validation

`shared/isluno_catalog.py` validates identity, stable IDs, ordered/deduplicated assets, supported currencies and integer minor units, age-band overlaps/gaps, timezone/slot data, options, pickup and policies. It returns defensive copies and derives readiness rather than trusting caller-supplied flags. Runtime/API callers must still enforce tenant/capability/operator authentication; this storage module does not infer authority from catalog text.

`CatalogStore.publish` locks across processes, checks the prior content revision, restricts editable fields, saves an immutable prior version and atomically replaces/fsyncs the new catalog. Gallery editors can reorder/caption/remove known assets but cannot inject URLs or change imported asset identity. Catalog edits never touch reservation, quote or payment stores. Sample edits generate a new rules version; older selected snapshots remain unchanged.

The import script is an explicit public-GET maintenance tool, never invoked by runtime reads. It enforces host/path allowlists, rejects all redirects before body reads or follow-up dispatch, and uses four-worker image concurrency, per-response and aggregate byte limits, and a request ceiling. It never calls BRIC, model or messaging APIs. Import-only dependencies are BeautifulSoup and Pillow; the runtime catalog uses the standard library.

One initial import downloaded the assets but failed while constructing the final report because it read readiness from pre-validation objects. This was corrected; one repeat completed (323 GETs in the successful invocation). Both were public static-content reads, not paid-provider tests. The report counts the successful invocation, not the earlier attempt or exploratory reads. No repeated live supplier/provider validation occurred.

Offline reproduction: `python3 wtyj/scripts/verify_isluno_offline.py`. It denies sockets/DNS, uses a synthetic config and does not load the legacy pytest root fixtures. Tests exercise validation, source/sample separation, real-mode rejection, concurrent publication (one winner), stale edits, history/snapshot preservation, media injection rejection, parsing, inventory coverage and every retained asset hash/length. Result at this candidate: **19 tests passed**; every retained asset hash/length matched. Tool-only dependencies are pinned in `wtyj/tests/isluno/requirements.txt`. The baseline verifier remains separately available; no live model, WhatsApp, email, supplier or payment test is implied.

## Next handoff and rollback

Submit the exact `cfeb101..candidate` commit range, this brief, source inventory and offline output to Gatekeeper. ISL-03 is the next eligible build issue only after this handoff is accepted under the one-issue workflow. ISL-04/05 depend on both ISL-02 and ISL-03.

Rollback restores a prior catalog revision or removes only this unpublished candidate. Retain imported assets used by historical snapshots and version history. The existing Mermaid catalog/runtime/number and customer records were not modified.

## Independent-review corrections

Gatekeeper requested changes at `4adb3d5`: all summaries were empty because the importer assumed paragraph children, and urllib redirects could bypass the fetch request/body ceilings. Neither finding was waived.

The parser now reads the actual `pages-tour-section-content` DOM and fails on unexpectedly empty overviews. A text-only refresh fetched 31 product pages and reused every original image. All 31 descriptions were restored as concise factual paraphrases; available timing, location, age, pickup, optional-price and cancellation claims are retained with source URL and observation time. Sample rules were aligned with those known facts. Published facts are no longer classified as missing merely because the parser lost them. The website/legacy Mermaid operating-day conflict remains explicit.

The redirect handler now closes and rejects every 301/302/303/307/308 response before urllib can read its body or recursively dispatch. An entirely in-memory HTTPS regression proves that a one-request ceiling cannot produce a second hop and a 10,000-byte redirect body is never read. Actual-DOM extraction, empty-description rejection and committed-description/published-time checks cover the content regression. No supplier/provider call or repeated image download was needed for these fixes.

The second review accepted both original fixes and identified one narrow follow-up: optional group-size/adult-accompaniment/check-in fields also needed schema validation. These fields now reject invalid types/ranges, unknown guest/schedule fields and inconsistent check-in/slot associations. Publication regressions verify twelve malformed edits leave the exact active bytes/revision unchanged and create no history; valid constraints remain accepted. Existing catalog data and image blobs were unchanged by this validation-only correction.
