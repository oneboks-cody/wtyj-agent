# BRIEF — Confirmed self-service reservation date changes
**Status:** Draft | **Files:** date-change workflow, understanding, reservation workflow, documents, Zernio sender, tenant copy, tests | **Depends on:** customer-copy release | **Blocks:** none

## Context
A paid guest requested moving Tuesday 8 September to Wednesday 9 September. Existing rules escalated instead of obtaining confirmation. The owner authorizes Tracy to change the date after a WhatsApp confirmation button, update the dashboard/customer file, leave an HO note and send a revised PDF. A separate gluten-free enquiry failed three times with provider_failure; no evidence yet of a dietary-classification defect.

## Why This Approach
Use a scoped, expiring proposal bound to the guest, tenant account, old date and reservation revision. Taps are authenticated workflow commands, not language guesses. Confirmed changes and the audit/customer snapshots commit in one SQLite transaction. Preserve amounts, payment records and immutable prior PDF versions. Reject mutating on the initial request, treating a stale tap as approval for a newer proposal, rewriting old PDFs, and suppressing unrelated operator holds. Claude still owns language understanding in one call.

## Instructions
Add typed change_date action and context for the pending proposal. Present old/new dates with Yes, change date and Keep date buttons. Validate future published operating dates and the policy time window; do not invent live inventory checks. On confirmation recheck the bound revision, preserve all passenger/transport/payment details, create a versioned receipt, update the reservation pointer and customer timeline, and record an HO audit note. Route the new PDF through the existing signed card/delivery path. Preserve manual operator pauses; resolve only the owner's identified obsolete date-change escalation, separately from runtime logic.

## Tests
Cover request without mutation, authenticated confirmation, duplicate and stale taps, wrong guest/account, unsupported/past date, concurrent revision change, money unchanged, customer history/HO note, preserved previous PDF and revised date, delivery retry, and provider failure behavior. Check exact-image integration with mocked sends, plus real-model understanding for the reported date and dietary questions.

## Success Condition
An approved date change appears in the dashboard/customer history with an HO note and a revised PDF, without an automatic escalation or duplicate change.

## Rollback
Restore the preceding image and tenant copy. Additive proposal data can remain; document-version migration is backward readable, with old code seeing historical receipts. Do not revert confirmed customer date changes without an explicit operational decision.

## Verification before release
201 date/payment/card/wheelchair checks passed; after the food-adapter fix, 224 date/model-recovery checks passed (409 distinct tests across these suites). Exact-image offline integration exercised the sender registry, two confirmation buttons, a confirmed date update, the fourth outbound card, signed revised-PDF download, one-page rendering, preserved original PDF/payment, HO audit, and duplicate tap suppression. No provider network was enabled in that integration.

The dietary failure was traced further: historical logs show successful API token usage followed by claude_empty_reply on all three attempts. A real-model canary reproduced it. A supported FAQ answer was in other_question_reply while reply was empty. The Mermaid-only adapter now copies that answer only with an allowed FAQ topic and exact guest evidence, then runs the ordinary validation. Malformed/unevidenced replies still fail. A second real-model canary successfully handled both the date request and gluten-free question.

The owner's obsolete date-only soft escalation is notification 3, content revision 1, conversation 6a997cbb837438e2a7862522. Its source model turn is 6a9b7ee7627131d1bf5f7efd. Resolve it using the existing revision-checked release API after deployment; keep the current date unchanged pending the customer's confirmation. A concurrent manual takeover must block this cleanup.

## Live release
Deployed commit 0dc35b0 as wtyj-agent:tracy-date-changes, digest sha256:0a6afc1279239378cbf55f3de252d31ea208d51f86cf0551f2800fae3200db45. Health 200 and watchdog healthy. Existing messages/customer/payment/document records preserved, six peer containers unchanged. Backup: /root/backups/tracy-date-changes-verified.

After verifying no manual mute/takeover and the exact notification revision, released obsolete date-change escalation 3 with release_mermaid_escalation(expected_content_revision=1). Added a system note to the customer history. Verified the reservation still says 2026-09-08, monetary snapshot unchanged, Tracy active, and no active escalation. No customer date change or external message was performed during rollout; the new confirmation flow runs on the customer's next request.
