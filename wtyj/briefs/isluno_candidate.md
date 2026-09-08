# ISL-13 integrated candidate

Issue #355. Backend source `6a7970a10610c7df58bdc933238a1066a73e5983`; unchanged dashboard `2c99c3a13e01af625432e5f681d4c065e95dd1a8`. Final evidence-only head is supplied in the review handoff; manifest source hashes bind the exact tested runtime/config/test files. No release or activation.

The new integrated test uses actual normalized inbound/social/conversation handling, native quote/payment/consent actions, send_reply/ZernioSender and actual durable senders, authenticated operator routes, and additive cutover/rollback. It seeds only catalog/legacy records and substitutes external understanding/transport/window/email scheduling boundaries. New business stages and acceptance ledgers are produced by application callers.

One- and two-trip flows complete in chat; a deliberate ambiguous multipart outcome cannot replay accepted parts. Exact-address consent leads to two synthetic email acceptances. All 31 existing Isluno tables and six legacy tables retain their row counts/digests through defined rollback and reactivation; no extra messages are attempted. Separate accepted recovery tests cover queued reminder suppression and old inbound quarantine. The production reminder policy remains disabled.

Packet in `output/isluno-13`:
- `release-packet.md`: exact candidate identity, present defaults, unresolved release inputs, proposed cutover/rollback steps, commands and limits.
- `requirements.md` and `requirements.json`: all 68 literal child acceptance criteria and user-scope mapping.
- `candidate-manifest.json`: backend/frontend source/config digests, 31-product inventory with 287 gallery associations / 234 unique media files, carried and new artifact hashes.
- `integrated.json`: actual handler events, fixture transport outcomes, native action model-call counts, operator data, preserved tables and PDF hashes.
- `offline-tests.txt`: 183 tests passed in 25.636 seconds, external sockets/DNS denied.
- `boundaries.md`: exact executed and substituted entrypoint/auth/model/transport segments.
- `visual-review.md`, `pdf-checks.json`, `rendered/`, `document-contact-sheet.png`: seven new PDFs/eight pages and visual inspection; demo notices on every page.
- `carried-hash-checks.json`: 63 existing PDF/UI artifacts match their recorded manifests. Existing six-language and actual desktop/mobile browser evidence is carried, not rerun or promoted into live/native approval.
- `runtime-versions.json` and `reference-checks.json`: fixture dependency versions and local evidence/source validation.

Code changes add verification and manifest scripts/tests only. No application runtime changes, source refetch, customer data/secrets, provider calls, phone/config activation, merge or deploy. External tests $0. ISL-13 independent review pending; ISL-14 requires separate concrete release authorization. Counts remain 12/14 accepted in candidate, 0/14 merged/verified, three deferred.
