# BRIEF — Helpful recovery and consistent Tracy voice
**Status:** Live | **Files:** agents/social/mermaid_date_changes.py, mermaid_response_policy.py, mermaid_reply_planning.py, mermaid_reservation_workflow.py, mermaid_understanding.py; clients/mermaid/config/response_policy.json and client.json; focused offline tests | **Depends on:** live confirmation-summary repair | **Blocks:** none

## Context
A requested move from Wednesday 9 September to Thursday 10 September received only “Please choose a future date from our published sailing schedule.” The server merged closed-day, past-date and malformed-date failures into one message and discarded the model reply. Other protected routes override the warm voice, and pending reviews append status text to unrelated questions.

## Why This Approach
Use one shared catalog-backed date assessment and recovery planner for intake and reservation changes. Explain the actual constraint, offer two valid nearby sailing dates, and preserve the saved booking until confirmation. Improve the single-call conversational contract and stop appending unrelated handover status to supported FAQs. Rejected: another model call to rewrite each server result (adds latency/cost), or a Thursday-only canned patch (misses the general failure). Critical facts and writes remain authoritative; localized recovery copy follows the already approved protected-response pattern.

## Instructions
1. Refactor date validation and alternatives in mermaid_response_policy.py and consume it from mermaid_date_changes.py and intake in mermaid_reservation_workflow.py.
2. Keep readable date lists and recovery copy in tenant configuration. Never imply live inventory was checked.
3. Preserve independent supported FAQ answers, confirmation summaries/buttons, and existing transaction/audit/document behavior. Supersede a prior pending proposal when a new explicit date request replaces it, even if that new date is invalid, so an old button cannot execute an unwanted change.
4. Give mermaid_understanding.py a general answer/reason/next-step standard; resolve short date-option replies from saved structured choices. Do not add a model call or text classifiers.
5. Deploy a scoped Mermaid-only overlay after local checks; preserve tenant credentials, all customer data and peer containers.

## Tests
A few offline behavior checks: closed/past dates yield valid alternatives, rejecting a replacement invalidates stale buttons without changing the booking, intake preserves supplied facts and the separate FAQ, and an ordinary FAQ during review omits unrelated status. No Anthropic API calls or live customer messages for testing.

## Success Condition
A Thursday request explains the closed day, offers real scheduled alternatives with readable dates, and keeps the existing reservation until a valid replacement is confirmed; unrelated answers remain focused.

## Rollback
Restore the previous Mermaid image and backed-up configuration; no reservation migration is required. Pending proposal supersession deliberately prevents obsolete buttons and can be replaced by a fresh customer request.

## Verification and release
Four focused offline tests passed, including follow-up selection receiving the saved alternatives and creating a new confirmation without committing a change. Two existing confirmation/PDF checks passed: provider payload carries the summary plus bound buttons, and confirmed changes preserve payment and produce one versioned, one-page receipt with the image and audit trail. All model/provider calls in these checks were mocked. No Anthropic test calls or manual WhatsApp test messages were sent. The related existing calendar/FAQ tests were updated to the new presentation contract without running a broad suite.

Independent review caught and resolved two issues before deployment: date validation could discard a separate FAQ, and cutoff precedence could offer dates that the policy would subsequently reject. The single-call contract now puts mixed date/FAQ answers in the separate answer field, and existing-booking cutoff takes precedence over offering alternatives. Existing initial-intake same-day eligibility remains unchanged; replacements and recovery alternatives use future dates.

Deployed Mermaid-only image `wtyj-agent:tracy-helpful-recovery`, digest `sha256:b5b6cc5385f6a1dee79ac1339d2b569f618a30b977dfb3758d5ecd1c9bc10ad3`. Backup `/root/backups/tracy-helpful-recovery-verified`. Five module hashes and targeted configuration changes verified. Health 200 and watchdog healthy. Six peer containers unchanged. Database integrity OK; all 74 chat rows, 2 booking states, 2 customers, 14 intake records, 1 reservation, 1 payment, 4 PDFs and 4 delivery jobs preserved. A read-only live check confirmed the current reservation remained booked for 11 September 2026, as it was at deployment. A pure local-policy render inside the live container reproduced the screenshot scenario with the Thursday explanation and valid Friday/Saturday alternatives, without changing or messaging that reservation.

Six-language recovery wording is supplied; new Papiamentu wording follows the existing glossary but has not received a new native-speaker review. No claim of universal model behavior verification is made; this release verifies the deterministic response and booking boundaries without paid model evaluation.
