# Mermaid — Separate chat and document language
Status: local implementation; deployment requires separate approval.

The owner wants Tracy to converse in the customer's language where capable, explain in that language that her six fully supported languages are English, Dutch, German, Spanish, Portuguese and Papiamentu, and request one of those for documents. Use the existing single understanding call; no translation service, extra model pass or paid validation.

Persist chat_language and document_language independently in booking intake/customer history. Do not infer document consent from nationality or a language switch. Unsupported-language guests select through a six-row WhatsApp list or an explicit typed choice. Block initial quote/checkout until choice; preserve booking facts supplied alongside language requests. Existing supported-language bookings keep their normal flow. Ignore forged, stale and cross-account picker replies. Keep server-owned prices/status in reviewed copy; conversational model replies use chat_language. Existing documents are not rewritten.

Validate with mocked model/provider and synthetic local SQLite: foreign-language offer, selected-language continuity, confirmation gate, typed choice evidence, all six options, stale/foreign selection rejection and profile persistence. No deployment or live messages authorized in this implementation turn. Roll back only these preference/selector changes.

## Implementation and verification
Added the tenant-configured six-language list and a separate chat/document preference contract to the existing model call. WhatsApp list replies are bound to the pending conversation/account/token. Both clicked and evidenced typed choices preserve the prior chat language. The normal reply cache and delivery retry path carry the list. Preferences are captured in the guest profile and preserved across new bookings. Initial quote confirmation waits for an unsupported-language guest's choice; new booking snapshots and emails use that choice.

The Papiamentu reply validator now reads chat_language when available, so selecting Papiamentu documents does not reject Swedish conversation. Legacy responses without chat_language retain their existing validation behavior.

15 focused offline tests passed in approximately 1.4 seconds: six selections, continuity/profile storage, confirmation gate, typed evidence, invalid selections, native six-row payload/account guard, supported-language regression, selected reservation/email locale and chat/register separation. Network connections were denied and model/provider calls mocked. Changed integration files parse and git diff --check passes. No model/provider settings changed, no paid API evaluation, no deployment or live customer sends. Existing already-issued PDFs are not regenerated.

Provider contract checked against https://docs.zernio.com/messages/send-inbox-message: direct reply buttons are capped at three; native interactive list supports this six-option selector and returns interactiveId through the existing webhook.
