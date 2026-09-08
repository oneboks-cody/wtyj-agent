# Isluno V1 candidate and cutover review packet

**Prepared for review only. Do not execute deployment or activation from this packet without ISL-13 PASS and Calvin's separate approval of the concrete release action.** ISL-14 owns execution. Current ledger: 12/14 accepted in candidate, 0/14 merged/verified, three integrations deferred. ISL-13 review is pending.

## Candidate identity and intended experience

Backend verified source: `6a7970a10610c7df58bdc933238a1066a73e5983`, descendant of accepted ISL-12 `1305cbcabbe3d4c56d1c4eba331554f34a505636`; frozen base `e43d0d174d10b82ee0fd4a394828ea720d3996ad`. Dashboard unchanged: `2c99c3a13e01af625432e5f681d4c065e95dd1a8`; frozen base `2e41f4a209fbafc602419408696a25fa9791dc34`. Final evidence-only head is supplied in the PR review handoff. Verify its ancestry and every source/config digest in candidate-manifest.json; do not interpret a source SHA as a deployed version.

Draft backend https://github.com/BensonOpas/wtyj-agent/pull/360 and dashboard https://github.com/unboks-org/unboks-dashboard-api/pull/167 target `release/isluno-demo-v1`. Keep the original checkouts and other tenants unchanged. Technical tenant stays `mermaid`, brand Isluno, journey `isluno_itinerary_demo_v1`, schema `isluno.v1`. Reuse of +1 223 276 0075 is intended, subject to current ownership/consumer verification; no new phone number is proposed.

Guests discover all 31 products, browse complete galleries through ordinary in-chat image pages, build one or several trips, confirm a summary, approve the quote, simulate payment using native reply buttons and receive a combined receipt and per-trip tickets. Optional email requires exact-address consent. Availability is assumed and payment is simulated: no supplier reservation or money movement. Native payments, live inventory and central export are deferred.

## Present defaults and release stops

The supplied feature patch is unapplied and default-off. Native carousels are false, media/document public base URLs are null, and reminders are disabled. No merge, infrastructure, credentials, provider, display-name, phone or configuration activation occurred. Missing media/document deployment configuration must not be bypassed by dropping PDFs or silently changing delivery mode.

The packet is not executable until the release owner records all of these concrete values and evidence:

1. Exact approved backend/frontend release heads and deployment artifact digests matching this manifest; independent ISL-13 verdict and any remaining conditions.
2. Verified target host/project, tenant database location, service/process identities and current deployment SHA. These are deliberately **not inferred from historical reports**.
3. Current number/account ownership and exactly one intended webhook consumer; matching strict account allowlist and verified tenant. No reconnect, transfer or new number without its own approved action.
4. Secure additive database backup/checkpoint procedure and tested restore location with retention/access controls. Do not publish the database, credentials or guest records as release evidence.
5. Valid media/document base URLs and allowlisted static assets; exact feature patch and display-name behavior to be approved. Native carousels and reminders remain off unless separately scoped and approved.
6. Current outer-login/session/tenant isolation and live control/takeover evidence; approved native-language copy review. Separate any unverified language/rendering claims.
7. If live verification is requested, explicit recipients/tenant/action, provider/model (where relevant), total dollar ceiling and maximum messages/requests with enforceable pre-dispatch guards. Default budget remains $0; this packet grants no live-test allowance.

## Proposed execution sequence after those gates

The release operator first verifies the reviewed source and build artifacts against the manifest and records a run ID. Retain the current service and rollback reference until the approved switch; never run a second consumer on the same number. Complete the approved backup and additive-schema rehearsal on an isolated copy before the live switch. Do not drop or rewrite legacy quote/payment/document histories.

Deploy the approved guard-capable backend with the feature off, verify tenant-bound health and the paired frontend capability-off behavior using the authorized environment checks. Apply only the reviewed tenant feature/media/document configuration during the approved change window. On startup with the feature enabled, the cutover journal/fence must commit before accepting new inbound work or resuming legacy recovery. Inspect the explicit outstanding-work dispositions, including date changes and emails; ambiguous/attempted work requires operator reconciliation. Do not manually mark it accepted or replay it.

After the single approved consumer serves the Isluno journey, perform only the approved bounded verification. Distinguish enqueued, provider-accepted, delivered and read states in the release record. Stop on wrong scope, missing immutable history, misleading payment status, duplicate side effects, invalid credentials or a spending refusal. Record exact deployed versions and outstanding limits. ISL-14 may only be marked complete on its independent evidence, not on this offline packet.

## Defined rollback and preservation

Rejecting this undeployed candidate changes no live service. For a later authorized activation, rollback means restoring the prior feature configuration **while retaining guard-capable code, the durable cutover fence and all new ledgers**. It does not mean rolling to a pre-guard binary, clearing the marker, restoring an old database over post-cutover records or replaying uncertain actions. Keep the same single-consumer ownership discipline.

Disable the new capability through the approved configuration path. The recovery loop suppresses queued Isluno reminders with reason rollback; legacy automated sends/booking links remain fenced. Preserve current new journey, quote, payment, PDF, consent and recovery rows, plus original Mermaid business history. Maintain historical operator access and use the existing manual inbox for reviewed follow-up. Compare read-only record counts/hashes and audit dispositions against the authorized pre-switch capture. Reactivation must not revive accepted, claimed, ambiguous or suppressed work. Unclear external outcomes require manual provider reconciliation under separate authority.

The integrated rehearsal creates one- and two-trip paid demos, seven PDF artifacts, two consented synthetic email acceptances and a recovery incident through real callers. It hashes all 31 existing Isluno ledger tables and six seeded legacy tables before feature rollback, verifies their preservation afterward, restores the feature and verifies zero extra synthetic sends. Queued reminder suppression and legacy inbound replay are covered by separate scoped recovery tests. No production backup/restore or runtime cutover is claimed.

## Reproduction and evidence

From the isolated backend worktree, using the existing offline venv:

```sh
tmp/isluno-test-venv/bin/python wtyj/scripts/verify_isluno_offline.py
tmp/isluno-test-venv/bin/python wtyj/scripts/verify_isluno_candidate.py --output tmp/isluno-candidate-rerun
python3 wtyj/scripts/seal_isluno_candidate.py --dashboard /Users/calvin/Projects/isluno-dashboard-demo-20260908 --output tmp/isluno-candidate-rerun
```

Use a fresh output directory for a rerun. Synthetic IDs/PDF identifiers may differ; assertions and source/data digests are the reproducibility contract. Existing artifacts identify the reviewed run. `offline-tests.txt`: **183 tests passed, 25.636 seconds**, sockets/DNS denied. `integrated.json` records handler events, fixture transport states, native action call counts, operator data, preservation hashes and PDF hashes. `requirements.json`/`requirements.md` map all 68 literal child acceptance criteria. `boundaries.md` lists every substituted segment. `visual-review.md` and artifact manifests distinguish new PDF review from carried six-language/UI evidence.

Frontend unchanged: the accepted 292-test run, typecheck, 2.98-second build and actual-shell desktop/mobile evidence remain applicable by exact source/artifact hashes; they are not a new ISL-13 browser run. Current code changes add verification/manifest scripts and tests only. No unresolved integration defect was observed in these offline checks; the Gatekeeper must independently assess that conclusion and the live-only gates.
