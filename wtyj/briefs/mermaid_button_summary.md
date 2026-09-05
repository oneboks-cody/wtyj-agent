# BRIEF — Include the date summary above WhatsApp buttons
**Status:** In progress | **Files:** mermaid_date_changes.py, date-change tests | **Depends on:** date-change release | **Blocks:** none

## Context
The delivered card displayed “Please select an option” despite the correct summary existing locally. The sender used top-level text instead of the provider API's documented message field. Earlier mocks accepted an invalid provider field.

## Why This Approach
Correct the API payload, not the booking logic. Reject adding a separate text message, which could split the summary from its buttons. Pending cards sent with the old payload must be superseded so the next request creates a fresh proposal/key; never reuse an idempotency key with changed content.

## Instructions
Send accountId, message and buttons. Assert the actual provider body carries both dates and the confirmation question, and lacks the unsupported text field. Deploy only the changed sender module. Supersede pending old-format proposals after backup; preserve reservation dates/payments. Existing delivered WhatsApp messages cannot be edited by this change.

## Tests
Run date-change regression suite, including actual body fields, mixed FAQ, duplicate handling and authenticated confirmation. Verify live module hash, health and preserved customer data.

## Success Condition
New date-change button messages show the old/new date summary directly above the choices.

## Rollback
Restore image wtyj-agent:tracy-date-changes. Superseded proposals remain historical and a new request creates fresh choices.
