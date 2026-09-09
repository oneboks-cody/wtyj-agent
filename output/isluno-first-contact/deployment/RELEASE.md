# First-contact refinement release

Prepared, not dispatched. Pinned application `b0eef7a395d2ada58aa0e6e090b1b9dbfdbec420` has independent source PASS. It changes only two Python files on the live `0361641` image: welcome/emoji instructions and delivery-aware model context. No catalog/config/profile/frontend/number/provider/model changes.

Exact packet `2c6f4a1e87671fc68c12140ada7b2166ac439db184526ceeb66066c08b3062ac`; archive51200bytes, three regular members, SHA256 `1ca27770eba7430f0319b49744e276338274144ef5c58aaf762926d7d199ad63`. One upload60sec, one no-network build120sec, 900sec cumulative budget including verification;500sec beforedisruption/300beforeactivation/180rollbackreserve. No automatic retry. Same91protectedtables checked throughout.

The separate authorized cleanup has already completed and intake is open16/15. This release does not clear anything. A fresh read-only proof confirms38emptydemo/contexttables and182payload-free no-replay IDs: `df8b083ca12612f6ee288672b21f59f5b900ca127529418056763458424dd582`. Any new customer state invalidates the proof and stops deployment; there is no automatic reset/refresh. Standard maintenance seal/reopen now applies because no unreviewed ledgers remain; no unknown-ledger override. Expected runtime16→17sealed→18open; ingress15→16closed→17open. Catalog stays `b9733d8e45ea6525a11b476ce5e6cc85554211b7c9b6194bc380edeaa5a03a0b`.

Seven focused operator checks passed in network-disabled Docker1.048sec: standardseal/readiness/reopen/tombstonepreservation, changedguest/tombstone/staleproof rejection, abort/runtimeconfiguration and verificationdeadline guards. An initial synthetic fixture incorrectly omitted ledger status columns and was corrected; no operator source change was needed. Source checks:4focused+64affectedBuilder,28independentGatekeeper. No paid model or real customer sends.

Gatekeeper separately verified authenticated Nr2 after cleanup. Existing conditional deployment authority remains; require exact packet review and its fresh hostpreflight before execution. Canonical private directory `tmp/isluno-first-contact-rollout`; these are review copies, not runnable entrypoints.
