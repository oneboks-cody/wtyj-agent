# Mermaid — Natural Dutch documents and email
**Status:** Local revision | **Files:** Dutch reservation email/catalog copy, mermaid_email_template.py, mermaid_documents.py | **Depends on:** formal guest address | **Blocks:** none

## Context and approach
The owner identified translated-sounding headings, dense pickup prose, incorrect passenger plurals and unwanted vocabulary in the Dutch reservation communication. Rewrite the complete relevant Dutch email/PDF sections with u/uw, concise headings and clear paragraphs. Use the owner's preferred terms vruchtensappen, snorkels and zwemvinnen. Preserve services, prices, policy links and booking facts. Reject runtime word substitution or new model calls.

## Implementation
Use Datum and Uw bezoek aan Klein Curaçao. Separate pickup place/time, vehicle and included return journey. Use quantity-aware passenger labels in the email and PDF price table, with existing labels as the unchanged fallback for other languages. Preserve paragraph breaks through escaped HTML and PDF transport rendering. Existing sent artifacts and live guest records are not modified.

## Validation and success
Render synthetic Dutch quote/receipt and email offline with network disabled. Verify one-page PDFs visually, safe escaped content, preserved monetary totals and singular/plural labels. Success is clear, natural Dutch across the reviewed document sections; preview is not a live booking or a sent email.

## Rollback
Restore only this revision's copy and rendering changes. Deployment requires separate action-specific authorization.

## Verification result
Synthetic quote and receipt each render to one page. The Dutch receipt banner is reduced from 58 to 48 mm to accommodate readable transport lines and all three policy links. Offline checks passed for email singular/plural labels, paragraph breaks, HTML escaping and preferred vocabulary. Configuration comparison confirms only Dutch copy changed; other locales, prices and policy settings are preserved. No provider calls, customer sends or deployment performed.

## Extension — other supported languages
The owner requested the same editorial standard for English, German, Spanish, Portuguese and Papiamentu. Review existing PDF/email copy and shared reservation labels; replace literal translations and dense transport paragraphs, use appropriate passenger plurals and preserve respectful address. Keep existing business facts, policy meanings/URLs, amounts and runtime routing unchanged. Validate synthetic quote/receipt/email rendering for each changed locale offline, including page counts, paragraphs, labels and escaped content. No deployment or customer delivery is included.

Visual review also identified English-style date syntax in German, Spanish and Portuguese. The existing date-label renderer now supports tenant-configured date patterns with the previous behavior as fallback; only those three locale patterns are configured. Calendar selection and booking dates are unchanged.

### Extension verification
- Offline synthetic 4-adult/2-child reservation: English, German, Spanish, Portuguese and Papiamentu quotes and receipts render to one page (10 PDFs); all preserve USD 800.00 and all three policy links.
- Email render checks cover singular adults, plural adults/children, escaped guest input and transport line breaks. Existing interpolation fields are preserved.
- Configuration comparisons preserve Dutch copy, service/pricing configuration and policy meaning; policy summary edits only add paragraph spacing.
- Focused date-label checks verify German/Spanish/Portuguese syntax and existing English/Dutch fallback, including dates without weekdays.
- PDF layouts visually reviewed; no external provider calls, delivery, live state changes or deployment.

## Authorized live deployment and owner review
Owner explicitly requested fixing/deploying the complete language revision and sending one WhatsApp review message. Deployed `wtyj-agent:tracy-language-918e7c4`, built from the verified live prompt-cache image to preserve its newer changes. Three runtime source hashes and 205 individually compared configuration edits verified; six peer containers unchanged. Health 200. Backup: `/root/backups/tracy-language-918e7c4-20260906T132131Z`.

The first attempt rolled back after legitimate inbound WhatsApp activity changed runtime records during restart. No database restore was performed. The completed attempt verified all 58 database tables unchanged while stopped, then verified integrity after startup; ordinary customer processing remained permitted. Protected backup from the first attempt remains available at `/root/backups/tracy-language-918e7c4-20260906T132026Z`.

One Dutch message built from the deployed email/transport copy and the owner's recorded reservation was sent to the verified Calvin WhatsApp conversation ending 003. The adapter confirmed send status and the exact message was recorded in dashboard history. Enforced maximum one message POST, with idempotency key `tracy-language-918e7c4-owner-review`; no model calls or fallback sends. This delivery was explicitly requested by the owner, not an automated paid evaluation. No PDF or email was sent in this review message.

## Follow-up — generated conversation wording
Owner reported that chat still used the rejected Dutch terms and a long translated overview. Read-only inspection of the live assembled system prompt confirmed the old English overview example was included and vruchtensappen was absent. The earlier release changed document/email copy, not this independent chat-style source. Existing prompt checks did not cover that connection.

Remove the stale long English exemplar, replace the 120-180 word target with concise information-first guidance, and add a compact six-language editorial vocabulary to the tenant persona. Feed that vocabulary into the actual Mermaid understanding prompt. Explicitly distinguish information enquiries from booking intent; allow at most one relevant optional information follow-up, never an unsolicited date/name request. Preserve one model call, facts, protected routing, existing register and Papiamentu glossary. No runtime text replacement or extra language-classification pass.

Offline regressions inspect the assembled production prompt and compare vocabulary against the already-reviewed email copy, so future edits cannot silently leave conversation guidance disconnected again. Live output quality is not claimed verified without an authorized model call. This revision is local pending separate deployment approval.

Follow-up validation: two focused offline tests passed in 0.08 seconds. The actual assembled prompt contains all six editorial guides and the existing formal register; drink/equipment/facility terms agree with approved email copy. The stale English exemplar and 120-180 word target are absent; information-first pacing and one-question guidance are present. git diff --check passed. No model calls, live writes, sends or deployment.
