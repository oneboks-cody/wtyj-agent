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
