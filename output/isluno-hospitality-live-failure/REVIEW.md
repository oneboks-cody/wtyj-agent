# Live introduction failure and narrow correction

Both actual fresh introductions reached model-response validation and failed with `invalid_reply_style` at 2026-09-09T16:18:17.684557Z and16:18:55.328936Z. Model output usage was876 and808tokens. The live image was the reviewed source3a5f524. Both failure acknowledgements were accepted by the provider; neither failed turn retained a model decision or outcome. The failure records remain operator_review and have not been cleaned, replayed or marked resolved.

The old predicate conflated nonstring text, a1800-character limit and any em/en dash. Existing logs did not capture tool input, stop reason or the failing subpredicate. Unicode punctuation is a reproduced possible cause, not a proven historical cause. No model output has been invented or presented as captured evidence.

A synthetic otherwise-valid dash-bearing response to the exact user-supplied introduction reproduced the same generic failure through the real SDK response extraction, contract validator and social handler. Earlier tests mocked the SDK to supply already-compliant structured decisions and prose, so they missed harmless formatting variation at that boundary.

The correction retains strict structure, size, consent, fact/result bindings and transaction gates. It splits type and length failure codes, logs bounded structural metadata/allowlisted stop reason for these failures, and converts em/en dashes to ASCII hyphens after binding expansion in final text and question. Numeric ranges and names retain their meaning; binding identifiers and consent evidence remain unchanged. No text truncation, model retry, extra understanding call or model/provider/token change.

Validation: the new six SDK/presentation cases plus four existing no-reply cases PASS (10tests,9.435s); all25 existing hospitality scenarios PASS (23.390s), with their old evidence-writer disabled to preserve the frozen original report. All runs used network-disabled Docker, synthetic model outputs and stub transports. Tests cover final accepted delivery history/question, unused branch punctuation, source text, no quote/payment/itinerary mutation, unsupported facts and malformed type/length rejection. Prepatch reproduction is retained.

This proves the narrow correction offline. It does not prove the uncaptured historical subcause or future live conversational quality. Deployment requires exact candidate review and a fresh preserving-data packet bound to all four retained turns and three accepted plans; the previous two-turn packet must not be reused.
