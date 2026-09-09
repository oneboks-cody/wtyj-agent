# calvin-isluno-recovery-e — proposed continuation, NOT executed

## Why new approval is needed

The original cutover stopped on71 unclassified records. RecoveryD was subsequently approved and consumed, but stopped before quarantine: its SQLite connection context managers committed without closing the backup handles, so WAL sidecars prevented detached-backup verification. This was a recovery-script defect, not a successful activation. Read-only inspection confirms no quarantine/coordinator/cutover tables and unchanged staged config; D's backup exists and matches the original snapshot hash. D will not be retried. Config staging succeeded; Mermaid is stopped, ICP is running with Mermaid admission closed0, and temporary watchdog/provisioning-worker fences remain. There is no coordinator or cutover marker. All three existing SQLite backups, D's helper/dispatch/payload and the partial archive upload are preserved. The original Nr2 static pointer remains selected.

The proposed additional action is to preserve these exact71 unresolved records and explicitly quarantine them from automatic replay. This is not a claim of successful delivery or permission to replay them. Calvin approved that disposition for one D attempt, which is now consumed. The corrected E helper and its additional private source copies require a separate one-run approval under the explicit no-retry boundary. The original downstream conditional deployment approval remains valid for unchanged steps, subject to their checks.

## Fixed historical evidence

The approved source for the disposition is the existing detached `state_registry.sqlite3` under the private release backup root, SHA256 `269a45bead0fd1dc85ab651bc256202db3e3dc6c52653195dce4d431741cd349`,774144bytes. It contains exactly50superseded and21replied inbound rows with empty TEXT channel metadata. Known-enum comparison did not establish the reasons as known successful completion. Dedup entries exist and error/token/lease/attempt fields are exact-empty TEXT. All other projected work ledgers are empty.

The helper binds this hash, the manifest and the SQLite read to one held descriptor and verifies its identity/content metadata before and after use. It compares every typed field of the live71rows to that private manifest. No customer payload, identifier, account credential or raw unrecognized reason is published. Original records, statuses and both consumed backups remain unchanged.

## Exact additional scope

- One guarded SSH dispatch, carrying at most128KiB, including exactly one new helper file of at most16KiB. Exact source/payload hashes and actual byte counts are in `recovery-e/packet.json` once prepared.
- Fixed host `root@108.61.192.52`; fixed Mermaid and existing ICP services/data/config. No new phone number or account/provider changes.
- Maximum360seconds for the entire dispatch,330seconds remote alarm,45seconds in the recovery helper,15seconds backup. No automatic retries, fallback, extra file uploads or scope expansion. One new exclusive dispatch guard and receipt namespace.
- Before mutations, verify the existing stopped Mermaid, new ICP image/container/mounts, ingress closed0, config SHA and mode0600, original pointer, compose sources, selected stored account, maintenance marker, inactive worker/timer/service and canonical lifecycle/worker/watchdog locks. Stop on mismatch.
- Install/hash-check the reviewed helper at the new exclusive path under the existing private helper tree. Verify every previously reviewed helper hash. Retain the existing helper/image/archive artifacts.
- Create a new exclusive0700 `recovery-e-source/` directory and0600 byte copies of the bound main database and optional WAL (maximum256MiB each,512MiB total copied bytes). Verify stable file metadata and exact original/copied main-WAL bundle hashes before and after copying; do not copy SHM. Open only this private copy through SQLite for backup, so verification cannot create sidecars on the original database. Create one new exclusive `state_registry.recovery-e.sqlite3` backup, verify integrity and explicitly close source/target handles before detached projection. Preserve all three consumed backups. Fresh config/database CAS and exact manifest checks still precede quarantine. Record copy count/bytes/bundle hash.
- Add exactly71entries to the existing runtime quarantine schema with disposition `operator_review_no_automatic_replay`. Preserve all original rows and statuses. The normal projector and runtime code remain unchanged; no record becomes "completed".
- With all temporary fences held, initialize/close/seal the coordinator only after verifying exact quarantine coverage and no other unreviewed work. Activate with the existing canonical CAS writer and start the pinned Mermaid image within the new120-second seal. The helper starts no application workers itself and has no network.
- The controller returns with both admission gates closed. Its generic health/marker checks are startup evidence only. Continue the previously approved selected authenticated capabilities/catalog31/operator controls, static pointer/assets/routes, media/document checks, and exact-generation reopening only after readiness passes. No agent customer-message or paid-provider canary.

## Runtime guard evidence and checks

The independent Gatekeeper used exact functions extracted from the pinned image with synthetic data and network disabled. Retained terminal records reject duplicate ingress even after dedup expiry; terminal records are excluded from recovery while an eligible control is claimed; the quarantine lookup is channel independent. These are bounded function checks, not full live WhatsApp verification.

The recovery tests run in the pinned image with synthetic SQLite/config and network disabled. They cover unresolved-record preservation/quarantine, unchanged strict projector, config/database/binding mismatch, row deletion/change/extra row, missing dedup, null/type drift, existing quarantine, snapshot replacement/drift, interrupted transaction, consumed backup, expired seal and default-off behavior. The corrected tests additionally cover WAL without a sidecar and WAL with committed uncheckpointed frames, verifying physical original main/WAL bytes are unchanged until the authorized quarantine transaction. A separate fresh-process CLI fixture imports config_loader only after the actual network/process/thread restrictions are installed. The exact controller wrapper is tested to preserve fixed failure status/phase. Final source/results are bound by the E packet/evidence hashes and independent review.

## Failure behavior

The controller preserves the helper's fixed stopped-phase JSON even when the helper exits1; a stopped result never satisfies sealed/applied checks. No automatic retry, forced reopening, status relabel, uncertain-send replay, old-image rollback or database restore. Preserve any new backup/quarantine/receipt and stop dependent steps. A transport failure means remote state is uncertain until inspected; it does not justify rerunning the dispatch. New source changes invalidate the prepared packet hashes.

## Approval text after independent acceptance

“I approve calvin-isluno-recovery-e exactly as documented in output/isluno-14/RECOVERY-E.md and recovery-e/packet.json at reviewed commit <FULL_SHA>. Preserve the71 historical records unchanged and quarantine them as unresolved with no automatic replay. Authorize the single bounded helper transfer and continuation, within the documented limits, with no retries or expanded scope. My existing conditional deployment approval remains valid only after its required checks pass.”

This is a new E run, never permission to replay D. The placeholder must be replaced by the independently reviewed commit before presenting the approval request. This document itself is not authorization.
