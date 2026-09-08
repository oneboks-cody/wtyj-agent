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

Sample policy: displayed starting amounts seed sample USD adult prices; sample child rates are half, with ages 0–2 free where child participation is enabled. Nine specified motorized/diving/skydiving products use adult-only sample eligibility. Sample schedules use the listed duration with explicit daily departure slots; a sample hotel transfer costs USD 25 per booking. These are illustrative values, not inferred supplier rules, actual safety requirements, tax advice or confirmed transport. Operators can edit them in the later gated dashboard.

## Inventory and asset result

- **31 distinct product pages**, reconciling the historical count of 31 with no count delta. The source set is the unfiltered Explore page plus homepage links at capture time. Unpublished owner/supplier inventory is not claimed.
- **290 distinct gallery source URLs**, **287 product/gallery associations** after three same-content duplicates within a product were accounted for separately.
- **234 unique image files**, 56,823,688 bytes. Every imported gallery asset passed supported-image decode/type/dimension/size checks. **Zero missing assets.** Repeated thumbnails, recommendations and customer-review content are excluded.
- Original WebP/JPEG/PNG bytes are stored by SHA-256 under `wtyj/assets/isluno/`. Public source association is retained without expiring Google query signatures. Delivery URLs remain unset until stable application serving exists; this issue does not claim WhatsApp delivery or image-format compatibility at that boundary.
- All 31 products are discoverable and quote-eligible **for the labelled sample demo**. All retain source-level unresolved facts and have no verified exact supplier-price set.

The public source manifest is `clients/mermaid/config/isluno_inventory_report.json`; the single effective catalog is adjacent `isluno_catalog.json`. The report records the source import before the separately approved sample overlay, so its per-product readiness describes original supplier facts. The catalog records the current effective demo readiness.

Website discrepancies remain explicit: the public taxonomy places paddleboarding and flyboarding under scuba diving and skydiving under off-road; provider mapping is unverified; starting prices are not complete guest/extra/currency rules; the generic guarantee/deposit copy is not treated as verified provider policy. None silently enables real booking.

## Implementation and validation

`shared/isluno_catalog.py` validates identity, stable IDs, ordered/deduplicated assets, supported currencies and integer minor units, age-band overlaps/gaps, timezone/slot data, options, pickup and policies. It returns defensive copies and derives readiness rather than trusting caller-supplied flags. Runtime/API callers must still enforce tenant/capability/operator authentication; this storage module does not infer authority from catalog text.

`CatalogStore.publish` locks across processes, checks the prior content revision, restricts editable fields, saves an immutable prior version and atomically replaces/fsyncs the new catalog. Gallery editors can reorder/caption/remove known assets but cannot inject URLs or change imported asset identity. Catalog edits never touch reservation, quote or payment stores. Sample edits generate a new rules version; older selected snapshots remain unchanged.

The import script is an explicit public-GET maintenance tool, never invoked by runtime reads. It enforces host/path allowlists before redirects, four-worker image concurrency, per-response and aggregate byte limits, and a request ceiling. It never calls BRIC, model or messaging APIs. Import-only dependencies are BeautifulSoup and Pillow; the runtime catalog uses the standard library.

One initial import downloaded the assets but failed while constructing the final report because it read readiness from pre-validation objects. This was corrected; one repeat completed (323 GETs in the successful invocation). Both were public static-content reads, not paid-provider tests. The report counts the successful invocation, not the earlier attempt or exploratory reads. No repeated live supplier/provider validation occurred.

Offline reproduction: `python3 wtyj/scripts/verify_isluno_offline.py`. It denies sockets/DNS, uses a synthetic config and does not load the legacy pytest root fixtures. Tests exercise validation, source/sample separation, real-mode rejection, concurrent publication (one winner), stale edits, history/snapshot preservation, media injection rejection, parsing, inventory coverage and every retained asset hash/length. Result at this candidate: **14 tests passed**; every retained asset hash/length matched. Tool-only dependencies are pinned in `wtyj/tests/isluno/requirements.txt`. The baseline verifier remains separately available; no live model, WhatsApp, email, supplier or payment test is implied.

## Next handoff and rollback

Submit the exact `cfeb101..candidate` commit range, this brief, source inventory and offline output to Gatekeeper. ISL-03 is the next eligible build issue only after this handoff is accepted under the one-issue workflow. ISL-04/05 depend on both ISL-02 and ISL-03.

Rollback restores a prior catalog revision or removes only this unpublished candidate. Retain imported assets used by historical snapshots and version history. The existing Mermaid catalog/runtime/number and customer records were not modified.
