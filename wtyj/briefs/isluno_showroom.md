# Show trips when requested

Calvin requests implementation, deployment and a fresh test slate. Explicit requests
to see the range override intake. The single understanding call selects up to three
source-backed products and cards, then one preference question. Align product/card
schema limits at three. Ordinary greetings and qualification remain short.

Observed production failures: `unsupported_card_fact`, and a carousel send rejected
with HTTP 400 after the introduction was accepted. The stored response has no provider
error code, so the exact provider rejection cause remains unknown. Do not claim native
carousels work. Configure ordinary photo galleries for new replies; never replay a
previous rejected/ambiguous send. Show three product images for the three-option
showroom and up to three images when exploring one product, within the six-part cap.

Normalize an exact existing same-product card fact into fact_keys when the model
omits the duplicate key. Unknown and cross-product bindings remain rejected; the
selection is revalidated and translations remain required. Do not infer source facts.

Tests cover three distinct illustrated options, a single next question, no booking,
exact fact-key normalization, invalid bindings, short intake and scoped gallery actions.
External API test budget remains zero. Calvin will test customer-facing output.

Use the authorized tenant reset to discard the failed test session before rollout;
retain payload-free replay-prevention IDs. Deploy the scoped source and profile,
verify health and empty session state, and keep source rollback available.
