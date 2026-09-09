# Isluno no-reply correction: bounded rollout

Calvin explicitly instructed “deploy, go live”. Final technical review covers the operator-only retained-failure proof described below; no additional user approval phrase is required.

Source candidate: `c04512cfc84ccae329c5d8de881fe9a010882e59`.

The 09:45 personal test reached Isluno and completed one model call. Its response failed `invalid_discovery_facts`; the old caller returned empty text. The invalid fact field itself was not retained, so its exact type/count is unknown. This patch keeps strict validation, clarifies the array contract, logs content-free shape diagnostics, and sends one processing-failure acknowledgement for a verified claimed failed turn. Duplicate turns do not recall the model or resend; itinerary state and the incident stay preserved. A fresh valid turn remains eligible.

Eight focused tests passed with Docker networking disabled, including real Marina/social/webhook processing with a mocked SDK and sender, malformed shapes, source/tenant rejection, unchanged itinerary, duplicate suppression and a fresh valid turn. Runtime model, token limit and retry settings are unchanged.

## Exact scope

- Target: Mermaid technical tenant hosting Isluno at `108.61.192.52`, existing number and config.
- Existing image config ID: `sha256:2be4255cecfce5a7355e7868570cbfb7bddfec119bdb7d8f70391c7990df8fb9`.
- Upload one source bundle, 194560 bytes, SHA-256 `66c7df2eceafe53e808c10e423a61c79dfd9dcf1ec950c1e4de4f445dd2c483c`; six fixed regular files (Dockerfile plus five Python files), enumerated and hashed in `files.json`. A read-only coordinator preflight runs first. One transfer, maximum 60 seconds, no automatic retry. Stage outside live mounts under `/root/isluno-no-reply-c04512c`.
- Build a derived image from the verified existing local image, networking disabled, no pull or dependency install, maximum 120 seconds. Verify five packaged file hashes and unchanged base filesystem layers before service disruption. Image ID is recorded and used directly for activation.
- Verify selected runtime/image, exact config, account/tenant binding, current gates (runtime open 4, ICP open 3), unchanged Compose configuration, idle provisioning queue and writer boundaries. Reuse existing selected tenant guard, maintenance marker and watchdog/provision-worker locks; preserve other tenants.
- Close ICP 3→4 and runtime 4→5, drain existing workers, then seal within 120 seconds through the reviewed operator-only extension. The normal coordinator remains unchanged: its two unreviewed tables are explicitly classified as one retained failed test and matching operator-review incident using fingerprint `eff3f83c68b7f706298d456d6d85f1a967916ab1e0d0b340892859fac577ff86`. Under one SQLite write transaction the extension requires zero active/pending work, the exact single terminal failed inbound, no outbound attempt/lease/token/heartbeat/retry, valid tenant/account links and unchanged customer bytes; it writes only the coordinator phase and a proof-bearing audit action. Unknown/extra/stale records reject. The same proof and audit are verified for readiness and atomic reopening. No customer record is marked complete or replayable. Never reinterpret unresolved messages as completed, erase records, or force a seal.
- Replace only `agent` with the verified derived image via a dedicated Compose override; no pull, rebuild, dependencies or other service changes. Preserve config, data, logs, session login, catalog, provider bindings, customer test records and recovery incident. No database migration/reset/replay, no provider tests or agent-sent messages.
- Check health, effective configuration/mounts, packaged hashes, capabilities/catalog31, and preservation of prior inbound/incident records. The browser session has expired; this backend-only patch preserves auth data, and authenticated visual confirmation is deferred to Calvin’s next sign-in. Reopen runtime 5→6 and ICP 4→5 only after readiness; restore temporary worker/watchdog fences. The runner requires at least 460 seconds remaining before disruption, 300 seconds before activation, and 180 seconds for the single rollback. A slow preflight/build fails before service disruption. Overall operation at most 720 seconds; stop on failure, no automatic retries.
- Rollback is image-only to the existing pinned image while retaining current data, under the same gates/readiness checks. Never restore an old customer database. If state is uncertain, keep admission closed and report the exact stopped phase.

## Execution and timing

Default offline preparation: `python3 tmp/isluno-no-reply/dispatch.py`. Execution requires `--execute-approved <generated-packet-sha256>` and the organizer’s technical clearance. Scripts are `helpers.py`, `preflight.py`, `stage.py`, `retained_review.py`, `rollout.py`, and `dispatch.py` in that directory. No retry of a consumed dispatch.

The 720-second cumulative bound covers preflight20s, source transfer60s, build120s, metadata checks, gate closure/drain, readiness, and one image-only rollback if required. Enforce 460s remaining before disruption, 300s before replacement and 180s before rollback; slow preparation stops before disrupting service. Coordinator one-shot commands are capped at10s, runtime inspect5s, SQL customer fingerprint work5s, image switch40s, health wait15s. The gate’s original120-second sealing window is not extended. Customer data is never restored from a backup.

This deploys the reviewed correction, not a paid test or proof of a successful live booking. Calvin will send a fresh WhatsApp message after deployment readiness is confirmed.
