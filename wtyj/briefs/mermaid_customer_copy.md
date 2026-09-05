# BRIEF — Natural customer wording across Tracy’s booking flow
**Status:** Tested | **Files:** Mermaid guest copy, workflow, prompt, cards, PDFs, checkout, reference generation and tenant copy config | **Depends on:** repeated-assistance release | **Blocks:** none

## Context
Customer messages repeat demo/simulation disclaimers in closing, paid labels, references and cancellation answers. The operator explicitly requested removing all customer-facing demo references.

## Why This Approach
Change authored copy and public reference presentation, retaining the existing no-money execution, signed callbacks, idempotency and protected tenant allowlists. Do not globally replace internal state names or migrate saved booking records. Reject merely stripping words from the old cancellation policy: the public cancellation page states a qualified 24-hour policy for online-paid tickets, while the existing 48-hour policy was a placeholder. Supply verified public policy facts to the model, and link the official conditions/privacy/cancellation pages in both PDFs. Direct questions about actual charges still receive truthful answers.

## Instructions
Update six-language copy. New references omit environment prefixes; legacy display aliases keep the same unique suffix without altering stored identifiers. Preserve the tropical images and island emoji, one-page documents, monetary snapshots, callback authentication and payment state handling. Patch only changed config leaves into live tenant files. Existing delivered messages and immutable documents are historical artifacts and are not rewritten.

## Tests
185 payment/card/wheelchair regressions passed. Twelve rendered PDFs cover quote and receipt in six languages: one page, one tropical image, three valid policy links and no demo wording. Check the isolated complete flow with provider sends stubbed, then a real model cancellation answer before release.

## Success Condition
New guest messages, cards, checkout and PDFs use ordinary booking/payment wording without repetitive demo labels, with unchanged payment execution and customer records.

## Rollback
Restore the prior image wtyj-agent:tracy-repeated-assistance and backed-up tenant config files; recreate only the Mermaid agent. No data migration.
