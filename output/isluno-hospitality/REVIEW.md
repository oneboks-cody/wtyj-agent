# Hospitality implementation and offline review packet

Implementation is prepared and independently reviewed offline in draft PR360 for conversation issue #351 and release
issue #356. It is not deployed. Live source remains c04512c. No new external API,
WhatsApp, email, payment, supplier or production database operation was performed.

Candidate source: `3a5f5240f9d51e71fb47c47fbce8a16d1688f78e`. Final affected regression: **97 tests passed**, network disabled (74.524 seconds). Earlier broader regression: 140 passed at d8d361e; the changed paths were retested on the candidate. See TESTS.json for exact scope and hashes.

## Design and evidence

The complete controlling brief and design are versioned under
`wtyj/briefs/isluno_hospitality_user_brief.md` and `isluno_hospitality.md`.
Read `CONVERSATIONS.md` for every actual attempted outbound part. `conversations.json`
contains the corresponding browsing state, pending item counts, delivery status,
actual history entries and last accepted question. Sources, model responses and
provider outcomes are synthetic fixtures. These are orchestration and presentation
checks, not evidence that a live model will choose those words or classifications.

| Brief sections | Implementation | Conversation evidence |
| --- | --- | --- |
| 1–4, 10 | Configured brand voice, expressive single-call replies, separate browsing memory | T01–04, T16 languages |
| 5, 9, 11 | Product/fact references, reasoned prose, preference and budget memory; read-only estimate | T02–04, T06, T08, T12 |
| 6 | Product-bound gallery, explicit paging, accepted/ambiguous asset exclusion, no booking consent from photos | T07, T16, T20; existing complete-gallery tests |
| 7–8 | Stage plus independent booking state, intent/consent evidence and action gates | T05–06, T15, T18, T21 |
| 12 | Existing price/eligibility/conflict engine, quote approval and simulated payment; default provenance | T04–05, T09, T15, T21 |
| 13 | Outcome-conditioned answers and composed existing quote/fulfillment jobs | T10, T15 repeated review/lunch, T19; existing mixed document/email checks |
| 14 | Provider-result history, actual text/media/button meanings; old unverified history labelled | Every dispatched turn, T07, T15, T20 |
| 15 | Durable failure acknowledgement, action uncertainty copy, actual human review record | T13–14, T16, T19–20 |
| 16 | Typed contract, source/result validation, no second model call or reply classifier | T17; all SDK assertions and seven targeted regression suites |
| 17–18 | Full scripted conversations plus independent Gatekeeper scoring | CONVERSATIONS.md; [Gatekeeper PASS for implementation and scripted offline review](GATEKEEPER-REVIEW.md) |
| 19 | Preserving-data release sequence and explicit release gate | Deployment plan below |

## Independent review result

Gatekeeper independently passed 29 network-disabled checks and both reproduced failure cases. Its [per-conversation report](GATEKEEPER-REVIEW.md) scores all 24 complete conversations (71 turns) at least 4/5 across all six dimensions. The 25th record is the separate nondispatched adversarial example. Implementation/scripted offline review passed; deployment, live model quality and native-language certification remain separate.

## Builder's provisional quality assessment

These are builder judgments about the scripted examples only. Gatekeeper's independent scores above are authoritative for this offline review; these builder scores are not acceptance.

| Dimension | Provisional score | Reasons and examples |
| --- | --- | --- |
| Warmth | 4/5 | T01 welcomes Calvin and connects his month to water, adventure and slower days. T14 acknowledges preference for a person. Error paths remain concise and can still feel formal. |
| Listening | 4/5 | T03 retains party/children/holiday/no pickup together; T08 changes pace. T15 preserves paid history and T21 accepts the requested name/age answer without redundant permission. |
| Guidance | 4/5 | T02 and T08 connect a named recommendation to the guest's pace; T04 asks missing pricing details before calculating. Broader real-catalog comparisons still need customer-quality review. |
| Commercial progress | 4/5 | T06 distinguishes interest from selection, T05 collects the missing date, T15 follows review/approval/payment, T12 accepts decline. No unsupported urgency or discounts. |
| Accuracy | 4/5 | Referenced facts and calculated totals use the fixture catalog; time conflicts and past dates have precise outcomes. T17 explicitly demonstrates that structural validity cannot certify arbitrary accompanying prose. |
| Ease | 4/5 | One next question in the scripted natural replies, known details reused, Add trip suppressed during booking-result replies. Canonical review and error details remain more formal than discovery prose. |

## Known limits

- The single model call interprets intent and writes rapport/recommendation prose.
  Structured validation rejects unknown sources, contradictory structured authority,
  unavailable bindings and missing outcome branches. It cannot prove free prose is
  semantically factual. T17 includes an invented-transfer counterexample that is
  structurally valid, deliberately not dispatched, and explicitly fails human quality
  review. No second evaluator or reply-language classifier is disguised here.
- Each natural-language action carries a quotation from the latest guest message;
  exact quotation matching does not prove the model interpreted consent correctly.
  Existing native quote/payment/email approval gates remain separate and mandatory.
- Provider acceptance is not proof of guest receipt or reading. Rejected, claimed
  and ambiguous sends are not silently replayed. If persistence itself is unavailable,
  the bounded webhook failure reply cannot manufacture durable conversation history.
- Native carousels remain disabled in the current configuration. The supported
  single-photo flow pages through real galleries with More photos; it does not send
  the entire gallery in an unsolicited burst. No actual image/provider test occurred.
- Pricing estimates require complete supported selection details. They are demo
  estimates, not real availability or supplier reservations. Sample-rule labels remain
  visible where applicable; all payments here are simulated.
- NL/DE/ES/PT/PAP examples use scripted prose and translations. They are not native-
  speaker certification. No live-model language quality has been established.

## Deployment plan for independent review

1. Freeze the source and evidence SHA. Gatekeeper inspects the diff, complete
   conversations, exact test evidence and six quality dimensions. Any hard blocker
   returns the candidate to implementation.
2. Build a concrete bounded release packet for that reviewed candidate. Include the
   changed Python files and public profile, image/base/source hashes, upload limits,
   overall duration, one-attempt markers and rollback image. No dependency, provider,
   model, account, billing, secret or schema mutation is part of this rewrite.
3. Obtain situational deployment authority for the exact reviewed packet. The old
   no-reply deployment authorization was completed and does not cover this rewrite.
4. Before disruption, verify current runtime/ICP identity and revisions, account
   binding, fresh Nr2 session where required, health and active workers. Capture
   preserving-data evidence from current state. Do not restore deleted history or
   erase fresh messages, itineraries, payments or operator requests.
5. Close both ingress gates and drain through the reviewed maintenance procedure.
   The old single-failed-turn proof is historical and cannot be reused for current
   customer data. If ordinary maintenance cannot prove quiescence with fresh records,
   stop before disruption until that specific preserving-data procedure is reviewed.
6. Apply only the bounded reviewed image/profile update. Verify source hashes,
   effective service configuration, health and all data invariants before reopening.
   Roll back to the prior image on failed required checks, preserving compatible JSON
   and customer records; never restore a historical database to hide a failure.
7. Reopen only after both runtime and ingress checks pass. Publish the exact release
   receipt. Mark deployment separately from real-conversation verification. Calvin's
   own WhatsApp conversation can supply live quality evidence; agent-run provider
   evaluations or sends need separate explicit bounded authorization.

The high-level sequence is ready for review. A new executable release packet and
fresh maintenance/session evidence are intentionally not claimed to exist yet.
