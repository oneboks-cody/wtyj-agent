# Approved cutover stop — 9 September 2026

The approved staging recovery C succeeded: five serial uploads, 233,957,640 bytes, 467.566 seconds, all hashes verified. The earlier partial upload was preserved. Both reviewed images and the dashboard assets were loaded/staged without changing the dashboard pointer. Gatekeeper verified the authenticated selected Mermaid Nr2 session before disruption.

## Cutover result

One guarded cutover dispatch ran; it stopped and was not retried. The new guard-capable ICP image is running with Mermaid ingress closed at initial generation 0. The Mermaid application is stopped. The watchdog timer and host provisioning worker remain stopped; the Mermaid maintenance marker remains. The shared dashboard pointer still targets the original release. No agent provider calls, messages or paid tests were made.

Configuration staging succeeded (coordination enabled, Isluno disabled, reminders disabled). The initial interpretation that staging itself failed was corrected after read-only inspection: one private config backup exists, target is mode 0600, and no incomplete exchange file remains. The next sealed-start helper created its detached SQLite backup but did not return a seal receipt. No maintenance or cutover table exists, and activation was not attempted.

The detached backup projects 71 blocking inbound records: 50 superseded and 21 replied, all with a channel outside the reviewed exact `whatsapp` value and a reason outside the reviewed settled allowlist. All have dedup entries and exact-empty error, token, lease and attempt fields. Those facts do not establish successful delivery or make the records safe to replay. All other reviewed work ledgers contain zero records.

The zero-blocking-record prerequisite is therefore not satisfied. Records and private backups remain intact. Do not weaken the classifier, mark records completed, retry the first-install seal, reopen traffic or restore an old database/image automatically.

## Recovery work required

Independently review the legacy channel/reason semantics against the baseline runtime and preserved snapshot. Prepare an exact, bounded recovery packet that preserves these rows and prevents replay, with explicit recovery/continuation authority before further production mutation. A corrected continuation must use fresh config/database bindings and retain both guard-capable services; it must not replay the consumed first-install helper against its existing exclusive backup. Until then, the demo is unavailable for customer testing.

## Evidence

Private local receipts under `tmp/isluno-deploy-approved-b/`: `cutover-local-dispatch.json`, `cutover-exact-payload.py`, `cutover-result.json`, `stop-inspection.json`, `aggregate-inspection.json`, and `status-inspection.json`. These contain no public customer payloads; protected source config and full SQLite snapshots remain private on the host.

Configuration SHA-256 after staging: `dbb56a497007a7f52e6a31487803bd542bd7e01dfa0620ee5c5590af400d0fe1`.

Detached old-work snapshot SHA-256: `269a45bead0fd1dc85ab651bc256202db3e3dc6c52653195dce4d431741cd349`.

Exact dispatched cutover payload SHA-256: `68b65936547dc447d0c5186c1c4284e8acce6f37bebc2bd6f478edf0caebbe03`.
