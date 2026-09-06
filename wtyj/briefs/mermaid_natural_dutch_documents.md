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
