# ISL-05 — Native trip carousel with separate WhatsApp actions
**Status:** Candidate | **Files:** `wtyj/agents/social/isluno_visual.py`, `isluno_discovery.py`, `isluno_wire.py`; focused discovery/visual/catalog tests | **Depends on:** existing ISL-05 candidate and sunny showroom application 9621b5c | **Blocks:** native carousel activation

## Context
The previous native branch combined a CTA-URL card type with quick-reply card actions; the legacy gallery branch also embedded quick replies. The current live configuration uses ordinary galleries. A historical HTTP400 rejection did not include sufficient diagnostic detail to prove the provider's exact reason. The local shape is inconsistent with Ali's established carousel contract and must be corrected before activation.

## Why This Approach
Reuse Ali's URL-card plus separate native-selection pattern. A URL opens optional trip information; it cannot select, add, approve or pay. Every product retains its complete source-bound description and exact-product More photos / Trip details / Plan this trip postbacks in WhatsApp. Renaming a URL button as a booking action was rejected because it supplies no selection event. Remote-error fallback/retry was rejected because rejection or timeout does not prove no delivery.

## Instructions
1. `wtyj/agents/social/isluno_visual.py:11` builds cards with `{card_index, type:"cta_url", header:{type:"image",image:{link}}, body:{text}, action:{name:"cta_url",parameters:{display_text,url}}}`. Required URL actions use catalog source links, or the verified image URL if a source page is absent. UI labels distinguish trip page/photo viewing from native selection.
2. `wtyj/agents/social/isluno_visual.py:101` groups a single trip's first three images, or one image for each of two/three trips, into one carousel. Separate required control parts retain complete source-bound descriptions, opaque per-product actions, and one final question. At most six wire parts. Fewer than two resolvable images selects ordinary media/text before dispatch.
3. `wtyj/agents/social/isluno_discovery.py:236` applies the same card/control contract to legacy pagination without changing its continuation renderer or action scoping. Track exact media/control associations; do not give URL cards postback meanings. Keep caller reply overrides and selection suppression.
4. `wtyj/agents/social/isluno_wire.py:50` rejects mixed URL/quick-reply cards and conflicting outer controls/media. Preserve two-to-ten cards and existing text/button limits. Retain only safe provider error codes, never free-form provider/customer prose.
5. Activation requires the combined rollout owner to change only `gallery_mode` from `gallery` to `carousel` in the live Mermaid Isluno profile, preserving all other profile fields, facts/media, customer history and deployment prerequisites. No activation or operational commands are included in this commit. No model/provider/token changes, new understanding calls, engineering sends, merges, resets or replay.

## Tests
- Actual handler/sender with synthetic SDK/HTTP: grouped carousel and separate exact-product controls; third-trip planning; second-trip full gallery pagination; no booking on browsing.
- Galleries of 1/2/10/11/21 images, missing-image fallback before dispatch, scoped/stale actions, full legacy source text, recipient pacing and window enforcement.
- Rejected carousel, accepted carousel followed by rejected control, and ambiguous transport remain terminal without replay or implicit add; photo opt-out remains text-only.
- Reject mixed card schemas and preserve only numeric/safe error metadata; source-bound text and final question remain lossless and within six parts.
- All 31 real catalog descriptions and locally verified galleries in both ordinary/carousel modes; welcome-image regressions. Network-disabled Docker, synthetic credentials only.

## Success Condition
Offline actual-adapter checks pass and independent exact-candidate review accepts the contract; activation is separately recorded by the combined rollout owner, with real provider rendering explicitly unverified until observed.

## Rollback
The authorized rollout owner may restore the prior image and profile gallery mode through the reviewed data-preserving release procedure. Preserve all conversations, delivery dispositions, bookings and deduplication records; never replay a failed plan or restore/reset customer data as a shortcut.
