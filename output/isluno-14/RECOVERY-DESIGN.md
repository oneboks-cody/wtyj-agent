# Recovery design — preparation only, not execution authority

The consumed cutover is stopped after successful configuration staging and the creation of a detached old-work backup. Its reviewed classifier reports 71 unknown inbound records. Existing production mutation authority does not authorize accepting those records, retrying the seal or reopening traffic.

## Chosen direction

Preserve historical records as unresolved and prevent automatic replay. Do not make their status appear completed or treat dedup/newer outbound as proof of delivery. Retain both existing backups, the partial upload, staged assets, closed ICP gate and pinned images.

A possible minimal continuation is a separately reviewed operator quarantine action against an exact private manifest of the 71 rows, using the existing `isluno_legacy_quarantine` schema and runtime guard. This is a design candidate, not yet proven safe or approved. The source guards must be verified to cover every admission/recovery/send path for these legacy channels before adopting it. If the pinned runtime cannot enforce that quarantine, a reviewed runtime change and new image are prerequisites; do not silently substitute an image.

## Evidence and source constraints

- Detached snapshot: SHA256 `269a45bead0fd1dc85ab651bc256202db3e3dc6c52653195dce4d431741cd349`, 774144 bytes. Original preserved.
- Staged config: SHA256 `dbb56a497007a7f52e6a31487803bd542bd7e01dfa0620ee5c5590af400d0fe1`, mode0600. Fresh CAS still required at action time.
- Status projection: 50 superseded,21 replied; channel differs from exact whatsapp; reasons outside reviewed safe list; dedup present; exact-empty error/token/lease/attempt. A subsequent bounded known-enum projection proves the channel is exact empty TEXT for all71. Reasons match none of empty TEXT, newer_outbound_exists, provider_send_ok or reservation_v2_structural_gate; raw unrecognized strings were not exposed. Terminal provenance remains unproven.
- `state_registry.py` supersession writer proves a newer outbound exists, not causal reconciliation of the older turn.
- `mermaid_maintenance._inbound_dispositions` explicitly rejects superseded as requiring review. Its close snapshot initially selects received/processing/recovering; that narrower selection does not override the independent old-work gate.
- `isluno_transition.ensure` skips inbound rows outside channel whatsapp. The existing transition therefore cannot be cited as proof it will quarantine these 71 rows automatically.

## Required packet before requesting continuation

1. Resolve only code-defined legacy channel/reason enums and private row fingerprints from the preserved snapshot, without customer bodies/IDs in public output or provider calls. Reject any different/unexplained row.
2. Independently verify all relevant pinned-runtime replay guards. Document whether the existing quarantine table can enforce a manual no-replay disposition for these exact rows, including non-whatsapp channel metadata.
3. Implement a default-off, single-use recovery helper with fixed target, strict tenant/account/config/database CAS, exact 71-row manifest, bounded execution and no network/application workers. Preserve source rows unchanged and record any explicitly approved quarantine separately. Unknown/new rows still stop; the normal classifier remains strict.
4. Use a new exclusive backup/receipt path; preserve both consumed backups. No first-install helper replay. The new helper must prove integrity, exact row preservation, quarantine coverage and zero remaining unreviewed work before creating a fresh coordinator seal.
5. Focused offline tests: stale/missing/extra row, row-content drift, binding/config mismatch, absent or mismatched quarantine, uncertain outcome preservation, interrupted transaction, expired seal, duplicate dispatch and snapshot collision. No live provider test.
6. Independent exact-source/hash review, then a concrete action-specific continuation request naming the unresolved-row disposition, helper/image hashes, request/time limits and unchanged downstream deployment scope. No production action before that approval.
7. Approved continuation would activate/start within a fresh120-second seal, verify authenticated selected capabilities/catalog31/controls/media/doc routes, switch and verify static assets, then reopen runtime and ICP exact generations and restore only appropriate temporary fences.

Current operational state: Mermaid stopped; ICP running closed0 for Mermaid; watchdog timer and host provisioning worker stopped; original dashboard pointer retained. This preparation does not reopen service or grant release authority.
