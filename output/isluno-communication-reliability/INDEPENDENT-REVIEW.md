# Tracy communication audit, 9 September 2026

Deployed and independently verified at backend ab88343b325d3ee67a6dcdad5e15e1f6dd2d254f and frontend 25dfebfb8688f76ef357b97c60c893fdf1bb8791. Application, exact deployment packet, live source/runtime and authenticated Nr2 checks passed. Calvin can test the existing WhatsApp number. Real customer conversation and native-language quality acceptance remain pending.

Live source at investigation start: f001cc7a73aaaac0679ba39d12cb60935035f97c. Target: Isluno on technical tenant mermaid and the existing WhatsApp account. Builder alone performs scoped host inspection and deployment. Gatekeeper inspects source, evidence and offline reproductions. No paid engineering probes, agent-sent messages, replay or cleanup.

## Confirmed current failure

Both reported inbounds reached the backend at 16:40:53 and 16:42:02 UTC. Routing and admission passed; each invoked the model once, saved its decision and outcome, then failed outbound delivery. Plans are rejected, with no provider ID. Their text contains 1,122 and 1,085 characters and three top-level reply buttons. The ordinary 4,096-character application check did not account for the 1,024-character interactive body limit. Actual original HTTP/platform error details were discarded, so the exact original provider error code cannot be recovered. The oversized payload defect is established independently of that missing diagnostic.

Primary contract references: https://docs.zernio.com/messages/send-inbox-message and Meta-hosted https://whatsapp.github.io/WhatsApp-Nodejs-SDK/api-reference/types/InteractiveObject/#reply-button. The former converts top-level buttons to interactive WhatsApp messages; the latter states the 1,024-character body limit. Plain text, attachments, interactive controls and carousel messages have distinct wire requirements.

## Additional independently reproduced defects

- A guest taps their own older trip button after a newer discovery turn: the handler returns empty text and makes zero model calls. Invalid authority must never execute, but a stale legitimate control needs a clear next step.
- A valid reply is stopped by the customer-service-window check before dispatch: plan stays queued and the recovery audit lists no outbound failure or incident. There is no actual send, yet the operator view loses the reason.
- A later model result is generated but not sent: a previous operator_review incident becomes progress_resumed_new_turn while the new reply remains queued. Local state progress is being reported before communication progress is established.

Reproducer: reproduce.py and reproduced.jsonl in this directory. All data, model decisions and transport outcomes are synthetic, with Docker networking disabled.

The separate reproduce_wire.py sends a realistic 1,107-character button reply through actual Marina extraction, conversation logic, sender registry, ZernioSender and post_once. Only the model SDK, scope/window controls and HTTP adapter are simulated. A strict synthetic transport returns HTTP 400 and the actual application persists rejected. Its synthetic platform code is not the missing historical provider code.

## Operator visibility

Nr2 source at 2c99c3a13e01af625432e5f681d4c065e95dd1a8 does render recovery rows. A count-only diagnosis would be wrong. Its discovery failure rows expose only an opaque plan ID, status and provider reference. An incident links to a saved itinerary only when one exists. Discovery-only failures need a scoped conversation link and safe transport reason; there is no useful review target in the current rows.

## Additional draft review requirements

The first optional-branch fallback draft constructed a missing-field question differently from the existing renderer, so its removal could not match the renderer's colon/product/time text. That would produce duplicated prompts and expose slot IDs instead of human times. The shared authoritative missing-field presentation needs one question and a transcript test.

The first recovery correction guarded progress(), but reconcile_claims still inferred recovered communication from a larger session revision. Both paths need evidence that all required reply parts were accepted. Local state progress alone is insufficient.

The first draft formatter passed 54 independent boundary/Unicode/lossless-content/final-question/control-and-media-placement cases in check_wire_cases.py. This preliminary result is not a frozen candidate review or deployment clearance.

verify_wire_candidate.py now passes the same real-sender path that reproduced the oversized-body rejection: one synthetic model call, a 1,082-character plain prefix and a 25-character question/button part, two strict simulated HTTP 200 results, exact content preservation, both parts and aggregate accepted. Source was mounted read-only, Docker networking disabled, and runtime data/logs isolated in temporary filesystems. This remains a draft result pending the exact candidate.

Independent check_quote_hold_race.py found a draft concurrency regression: while one quote sender waits on the initial service-window check, a second sender accepts all four parts. The first sender then receives a closed-window result and overwrites part zero's accepted status/provider reference. The correction must condition each preclaim hold on the still-queued row and retain an owning claim for post-dispatch writes.

Native discovery controls require accepted prerequisite content, not merely a generated current plan and a valid opaque token. Scope, latest catalogue and expiry checks remain necessary but do not establish that the associated reply was accepted. Unaccepted or partly rejected controls must not authorize a mutation.

Compact model state must preserve real item identifiers, selections, human timing and authoritative item pricing fields. The initial projection used outer item_id/totals although stored items use id/total_minor/currency/lines. The nested selection.item_id was retained, so the identifier was not completely lost; the projection should nevertheless match the authoritative shape explicitly.

## Must improve before release

1. Validate every final wire body after binding expansion. Preserve full text, media associations and actionable controls in bounded, ordered parts; never silently truncate the guest's answer.
2. Keep durable per-part claims, provider outcomes and history. A partly accepted reply is not fully delivered. Stop on ambiguous results; no blind retries. Do not offer or remember controls/questions as accepted until their specific part is accepted.
3. Preserve safe, bounded failure metadata and show failed or abandoned reply work in the operator view, including discovery before an itinerary exists. Distinguish security/ownership suppression from operational failure.
4. Give verified guests clear handling for expired controls and safe processing failures. Preserve their selections and all approval/payment boundaries.
5. Reduce duplicated model context and unnecessary speculative output branches while preserving complete source facts, rule identifiers, consent and transaction validation. One existing model call per inbound remains.
6. Test the complete HTTP ingress, buffer, model extraction, application, sender, transport contract and persistence path with realistic payload variation and faults. Mocking send_reply=True bypassed the boundary that failed here.
7. Correlate late provider outcomes by exact tenant/account/message ID. Unmatched callbacks cannot establish delivery. Preserve acceptance versus delivery versus read distinctions.
8. Recheck conversational behavior across the complete hospitality scenarios, including mixed requests, memory corrections, photos, hesitation, direct booking, stale actions, conflicts, missing facts, human help and returning guests.

## Frozen application verification

All confirmed source defects and draft regressions described above are corrected in the frozen candidate. The draft findings are retained as audit history, not outstanding release blockers.

- Gatekeeper independently ran 71 backend regression tests in 133.857 seconds, covering communication wire handling, hospitality delivery, recovery and payments. All passed using a cached Docker image with networking disabled. Log: frozen-tests.log.
- Gatekeeper independently reran 54 boundary, Unicode, lossless splitting, final-question, controls and media cases. All passed.
- Gatekeeper independently reran the realistic oversized-body reproduction through Marina, the conversation handler, sender registry, ZernioSender and post_once. One scripted model call became two valid synthetic HTTP requests, with all content and the final question preserved. Both parts and the aggregate were accepted.
- Gatekeeper independently reran the stale-control, closed-window, unsent-progress and quote-sender concurrency reproductions. Each passed. In the concurrency case, the stale sender made zero posts and could not overwrite four already accepted parts or their provider references. Results: frozen-reproductions.jsonl.
- Gatekeeper independently ran 10 frontend recovery/workspace tests at the exact frontend source, with sandbox-exec denying networking. All passed in 1.85 seconds.
- Backend diff whitespace validation passed.
- Builder separately reports the full 175-test backend suite passing in 210.926 seconds at the same source, recorded in output/isluno-communication-reliability/TESTS.json. This is Builder evidence, distinct from Gatekeeper's independent runs.
- The refreshed export preserves all customer-facing text and relevant turn state from the previously reviewed 24 complete scripted conversations, 71 turns, plus one non-dispatched adversarial scenario. The existing six-dimension scores and reasons carry only for unchanged scripted prose. They do not establish live model behavior or native-language quality. The original independently reviewed export remains preserved at SHA-256 19b0db5ee142d4836ccd5e9fe9519fe573752ab24939a15a53b55477d189690a.

The user has signed in and Gatekeeper verified the authenticated Isluno Today page at https://dashboard.unboks.org/today. That currently shows the old frontend. Post-swap verification must check the newly built assets and changed failure panel.

## Deployment package review

Gatekeeper independently rebuilt frontend 25dfebfb8688f76ef357b97c60c893fdf1bb8791 with networking denied. The build passed in 3.49 seconds and all 66 output files were byte-identical to the proposed archive. All 16 Python members matched the frozen backend source. The archive contains 83 verified members including its Dockerfile. Gatekeeper independently passed 10 operator/runner tests in 35.673 seconds. An earlier test harness attempt failed during temporary fixture creation on a read-only mount; the corrected harness uses Gatekeeper's writable fixture tree and no networking.

The preserving runner has been read in full. It bounds the overall action to 900 seconds, transfers once, builds without networking from the exact existing image, binds tenant/config/runtime/ingress identities, fences producers, seals only the reviewed retained history, compares every protected table around activation, verifies packaged source and served frontend hashes, and reserves time for authenticated UI review and a single pointer rollback. No host call has been made by Gatekeeper.

The latest retained-history proof also binds the actual two pending soft escalation notices and their dedup links. These are delivery review records, not hard takeover. The verified conversation has ai_muted=false, blocked=false and an empty mute source. The existing Isluno transition fence suppresses legacy startup escalation reconciliation.

Final supplementary review identified one narrow presentation correction before dispatch: when the model omitted an unused pending branch, the fallback still combined a friendly sentence with "Demo itinerary saved" and "Please provide: guest name?". Commit ab88343 corrects that incomplete-intake fallback. The question stays model-authored through the existing missing-field binding; Python binds the actual field/product/time and suppresses unused preparation wording after a complete selection. No new schema field, static question table, model call or transaction authority was added. Corrected fixture: "Happy to help. Could you share the guest name?"

Gatekeeper independently passed 24 communication/delivery tests in 18.397 seconds and 35 hospitality/presentation tests in 33.946 seconds at exact ab88343. The full 175-test result above remains explicitly bound to its parent e8aead, not falsely relabelled as a new full-suite run. The final independently generated export contains 24 complete conversations, 71 turns, plus the non-dispatched adversarial scenario; all previously scored guest-facing text and relevant turn state remain unchanged. Additional muted/blocked guard injections both rejected unsafe controls while preserving fixture rows.

Final packet 6f0a71adb17f5fd40ff9a55ed79d41188078ce8aeb339754cdd541fa14130bfe binds archive d5c383222a336bba68af31105179af16b8e9f6a1c1405e306851fdec7ff62b5a and retained proof c20ab63aa42f4b705033ac0a59c1ee6b9334fa01d53c9d805499f844b5c9f6cc. Every archive member and generated payload hash was independently reverified. Gatekeeper cleared exactly one 900-second preserving deployment and the reviewed conditional rollback, with Builder as sole host executor. Paid testing, agent messages, replay and configuration changes remain outside the action. Authenticated UI verification is still required before claiming frontend readiness.

## Remaining verification limits

## Deployment and authenticated verification result

Builder executed the cleared packet once in 22.635 seconds. Backend image sha256:8ea85eb29ae6bea3ff64012cb276970a2daef15f5e0958c743df2c9bca5ac11d is running with the exact 16 packaged source files. Health returned 200; the catalog has 31 products; runtime generation 12 and ingress generation 11 are open; producers are restored. All 91 customer/business tables were preserved through activation, and the once-only post-deployment query-only proof matched c20ab63aa42f4b705033ac0a59c1ee6b9334fa01d53c9d805499f844b5c9f6cc. No retry or rollback occurred.

Gatekeeper independently reloaded the signed-in Isluno Today page after activation. Counts remain 0 itineraries, 0 items, 0 demo-paid and 2 pending/review. Three historical incident labels now explicitly say delivery is unverified. Two rejected replies have review guidance and scoped conversation links; all five links match the retained conversation. The main asset matches the independently rebuilt frontend and browser errors/warnings were empty. Gatekeeper expanded a delivery reference but did not open a customer conversation. The historical provider IDs and part/error metadata are correctly unknown rather than fabricated. See AUTHENTICATED-UI-RECEIPT.json.

No agent-sent customer message, paid engineering request, replay, cleanup or configuration change occurred. The next step is Calvin's manual WhatsApp conversation, progressing from introduction through discovery, photos, explicit trip selection, review and simulated payment. Remaining supplier, payment and native-language limits below still apply.

## Evidence limits after deployment

A full offline path with a strict fake transport can establish application handling and wire conformity. It cannot prove future model prose, network delivery or native-language quality. No claim that every possible external failure has been eliminated is supportable. The engineering goal is to prevent these defect classes and make remaining failures explicit and recoverable without unauthorized actions.
