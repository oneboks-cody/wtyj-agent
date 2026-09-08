# ISL-06 natural itinerary conversation

Issue: https://github.com/BensonOpas/wtyj-agent/issues/351
Accepted base: a1b3e316657eaafffacbe916db3e82f821a11058 (ISL-05).
Repository BensonOpas/wtyj-agent; tenant mermaid; isolated local candidate. Builder owns all changes; no additional worker or model override.

## Application behavior

The actual social orchestrator now enters the itinerary conversation handler before legacy Mermaid intake. One structured Marina response covers discovery, explicit booking changes, shared guest details, document-language preferences and source-bound translations. The model sees the saved session, current item IDs, effective catalog booking rules and current time/timezone. Python uses the structured action/field contract; there are no keyword or reply-text intent classifiers. A native reply action uses zero understanding calls. SDK model, maximum output and zero-retry mode remain unchanged.

An Add trip reply now starts a scoped draft and retains an incomplete selection rather than losing the intent at the webhook boundary. The application asks for the next missing name, ages, date, slot, pickup choice/location or required extras. Available slot times are shown inside chat. Explicit ages are reused for new items; adult ages are not invented from a count. Per-item updates preserve other dates, guests and extras. Options are patched by ID, so removing one extra does not delete another. Pickup consent applies the catalog's known per-booking/per-person fee; unknown unit quantities still need input. Pickup location and guest-name details are retained per item for subsequent document snapshots.

Add/new/update/remove/cancel use the same scoped itinerary service and immutable revision records. The service can participate in the conversation's SQLite transaction without committing it independently. A turn durably saves guest/intake state together with successful item changes. Invalid pricing/date combinations retain correctable pending selections and guest data while rolling back that turn's item updates. An explicit initial add may retain an empty draft while intake is incomplete. Questions do not apply pending selections. Ambiguous/missing item targets and unmatched product requests get corrective prompts rather than a silent dropped reply.

Unpaid cancellation marks the draft cancelled while retaining its history. Non-draft/post-payment changes are recorded for operator review without changing the itinerary or claiming a refund. Repeated open requests for the same scoped itinerary/reason are deduplicated. Explicit Ask the team replies are consumed by the application and reach that durable queue. GET /dashboard/api/isluno/operator-requests exposes its scoped id/itinerary/reason/status under the existing dashboard authentication dependency; it is no-store and does not expose another customer's requests. No staff action or external alert delivery is claimed.

## Replay, language and source boundaries

A turn is reserved before understanding. Its validated decision and transaction outcome are durable. Duplicate inbound IDs reuse the outcome without another model call or item insertion. A reply-persistence interruption after commit can rebuild the same reply from that outcome. A claimed turn without a decision fails closed; automatic model retry/recovery is not introduced here and remains ISL-12. Session revision checks prevent overwriting a newer conversation state.

Six supported chat languages use canonical operation/price prompts. Document language is a distinct nullable preference; a chat-language switch does not change it. Source answers use fact-keyed translations from the same understanding response, validated against the selected catalog products/facts. Missing required non-English translations fail closed. Proper names, rules, monetary totals and saved state are not rewritten by translation. Exact source/catalog references and sample pricing snapshots remain retained. Canonical summaries label sample demo rules, including while asking about an incomplete sample selection.

Deterministic translation fixtures prove language routing/state and source association, not linguistic quality or clinical/supplier accuracy. Native-language review remains pending, especially Papiamentu; model translation faithfulness requires later authorized evaluation. No extra translation model request is made.

Quote approval and simulated payment remain ISL-07/08. Explicit approval currently reports that the quote approval stage is unavailable and performs no approval/payment transition. The conversation builds and edits a demo draft; it does not claim a real supplier booking or real money movement. Native carousel rendering remains unverified/default-off, with the accepted ordinary-image reply pagination and conservative no-resend behavior unchanged.

## Verification

Network-denied deterministic fixtures run through the actual WhatsApp channel normalization, social orchestrator and conversation handler. Coverage includes one trip then a second, targeted date changes/removal, missing-field intake with retained guests, questions versus approval, invalid corrections, multi-item rollback, all six language switches and separate document preferences, pickup location/fee retention, unrelated-extra preservation, explicit sample labels, native Add/Help consumption, replay after commit, customer isolation, and authenticated operator queue reads. The actual Marina SDK boundary is mocked and asserted to make one request with the existing model/output settings.

The runner now supports --pattern for focused unittest development while always retaining socket/DNS denial and excluding legacy root fixtures. The existing isolated test environment supplies the repository-pinned reportlab dependency needed by the real orchestrator import. No dependency/lockfile change was made.

The inherited SDK cache/baseline runner passed all 7 tests in 0.36 seconds with network denied; the frozen 112-file baseline manifest still matches. Full-suite results for the exact integrated candidate are recorded below. No paid/model/provider validation calls, WhatsApp/email sends, supplier bookings, real payments, live config/phone changes, merges or deployments occurred.

Final combined verification: `tmp/isluno-test-venv/bin/python wtyj/scripts/verify_isluno_offline.py` passed **92 tests in 12.055 seconds**, with external sockets/DNS denied. `git diff --check` passed. All 234 media conversions were included in the unchanged full media test; no source assets were fetched or modified.
