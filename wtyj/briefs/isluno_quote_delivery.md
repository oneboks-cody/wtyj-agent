# ISL-07: versioned itinerary quotes

Issue: [ISL-07 #352](https://github.com/BensonOpas/wtyj-agent/issues/352). Issue base: `a9d316cb0a2a53d67b3b0fae7cf3880d036882d1`, the accepted ISL-06 candidate. This issue remains pending independent review. Six of fourteen implementation issues were accepted before this handoff; none were merged/verified. No release action is requested.

## Customer and operator behavior

The actual WhatsApp conversation handler supports a structured `summary` request through the existing single Marina understanding call. A typed approval requests the review flow; it cannot approve anything. Completed drafts receive a complete multipart summary, followed by a native **Confirm details** reply button. Confirming that button advances only summary confirmation. The next job sends the stored PDF and then a separate **Approve quote** native reply button. Approving records quote approval, with explicit no-payment/no-real-reservation text. The itinerary stays draft/editable until the later simulated-payment issue.

Quotes freeze the exact immutable itinerary revision, per-item prices/rules/source claims and catalog revisions, guest data, each item's guest name/ages and pickup details, document language, currency and totals. Document language defaults to the current chat language when the first review is requested, then remains separate from later chat-language changes. The application validates every line multiplication, item sum and grand total. Summary text, PDF and authenticated operator projection use the same canonical projection; business facts never come from generated prose.

All quote snapshots and PDF bytes are immutable SQLite records, with SHA-256 hashes and update/delete triggers. Mutable stage and delivery records are separate. Whole-flow approval stages are `summary`, `quote`, `approved` and `superseded`. Exact customer scope includes tenant, account, conversation, customer and journey. Random native action tokens bind a single quote/version and stage, expire after 24 hours and require a verified interactive event. No external approval or booking URLs are used.

Any material correction invalidates the prior quote inside the conversation transaction. A complete correction produces a new summary version. Incomplete corrections invalidate approval while retaining the correctable intake. Catalog publication, expiry, a different current itinerary or source, cancellation, and wrong-customer actions fail closed. Native stage transitions also advance the conversation revision so an older in-flight understanding result cannot overwrite them. Old discovery Add/Help/gallery contexts are revoked on quote stages. Replayed correction events return their original quote job; repeated button taps do not create another approval or another send.

A combined correction and question first preserves the source-backed answer or unavailable-answer/operator-help interaction. Its new quote is persisted as queued and the customer is told that it is ready for review; asking to review sends that same prepared summary. Questions cannot confirm or approve it. Post-booking operator requests retain the existing ISL-06 behavior.

Authenticated `GET /isluno/quotes` uses the host dashboard's existing auth dependency, exact customer scope, no-store headers and bounded pagination (default 20, maximum 100). It exposes immutable snapshots/projections, stage timestamps, validity, PDF hash and per-part delivery state. `delivered` is null: no delivered/read claim is inferred from acceptance. Dashboard UI is still the later dashboard issue.

## Actual delivery boundary

`ZernioSender` dispatches the new `isluno_quote` envelope to the quote delivery module. It reuses Zernio's existing `buttons`/postback shape and `attachmentUrl`/`attachmentType=file` fields. The sender checks the bound account/conversation and current quote before each part, shares discovery's six-second customer pacing, checks the customer-service window again after waiting and uses the existing final provider/account mutation guard. Each part has a unique idempotency key and is durably claimed before one POST. Accepted parts are skipped on replay; rejected, ambiguous or crash-claimed parts are never blindly resent. The entire prior-stage job must be accepted before the next stage can advance, so a failed PDF cannot authorize the approval button.

`accepted` requires an explicit provider message ID; it is not evidence of visual rendering, customer delivery or reading. Missing document deployment configuration or a closed window before dispatch leaves the part queued, with no send or success claim. Ambiguous/partial delivery recovery belongs to ISL-12.

The PDF is served through `/r/isluno/documents/{random-128-bit-quote-id}.pdf`, with no listing, a 24-hour expiry, current-scope/quote checks and private/no-store/nosniff/no-referrer headers. This is a bearer media capability for provider attachment fetching, not a customer booking page. It only serves confirmed/current quotes and is revoked by corrections. As with any delivered document, revocation cannot erase a copy already downloaded by the customer/provider.

`isluno.document_base_url` is added only as null in the **unapplied** feature patch. Feature activation, public routing and this base URL require the separate release packet. No actual config, number, credentials, provider/model, live database or legacy Mermaid documents were changed.

ISL-08 must use `QuoteStore.approved_snapshot` and revalidate the same current quote binding at its own payment transaction/dispatch boundary. No payment actions exist yet, so these tests establish current/stale approval guards, not a completed payment implementation.

## Offline evidence

- Full final suite: **117 tests passed in 13.912 seconds**, using `tmp/isluno-test-venv/bin/python wtyj/scripts/verify_isluno_offline.py`. The runner denies external sockets and DNS and excludes the legacy root pytest fixtures.
- The quote module has **19 focused checks**; an 18-check focused run passed in 1.722 seconds before final read-interface/pagination additions and the targeted guest-name correction case. The full final run includes those additions.
- Actual normalized inbound handler, native action path, sender dispatch, public document route and authenticated operator router are exercised with deterministic fixtures. Coverage includes distinct confirmation/approval, accepted-stage requirements, duplicate taps and correction replay, wrong customer, plaintext/expired actions, stale catalog, correction during send, in-flight understanding conflicts, retained per-item guests, separate chat/document languages, invalid totals, immutable PDF bytes, missing document URL, rejected/ambiguous/claimed delivery and mixed-question operator help.
- The full suite also retains all 24 conversation checks, itinerary/catalog/discovery tests and conversion of the existing 234 local images. No source or image URLs were fetched. The earlier independent seven-check cache evidence remains unchanged; no shared Marina/model/cache implementation was edited here.
- No external API validation, customer messages, email, supplier bookings, payments, phone/config activation, SSH, merge or deployment. External test spend: **$0**.

## Rendered evidence and inspection

Run `tmp/isluno-test-venv/bin/python wtyj/scripts/render_isluno_quote_samples.py` to reproduce the six synthetic artifacts in `output/pdf/isluno-07`. The generator denies every external socket and DNS request, uses the actual conversation/pricing/quote pipeline with synthetic rules, and renders through the production PDF function. ReportLab 4.4.3 and pypdf 4.3.1 are existing root requirements; no dependency or lockfile changed. Fonts are the licensed Vera fonts bundled with ReportLab.

Each language (`en`, `nl`, `de`, `es`, `pt`, `pap`) has six dated trips, long accented guest names, long product names, age-banded line items and exactly **USD 1500.00** total. Each PDF has **four pages**, **24 pages** overall. Companion JSON contains the exact snapshot, projection and full summary. `manifest.json` records file hashes, byte counts and page counts.

All 24 final pages were rendered with Poppler at 100 dpi and visually inspected in six complete contact sheets. An initial layout had separated source notices and a narrow Portuguese quantity column; the final renderer keeps ordinary item sections together and gives quantities enough width. Final inspection found no clipped totals, overlapping text, lost accents, broken quantity headings or missing demo/sample notices. Every page has an Isluno header, localized demo footer and page number; each item retains its explicit sample-rule notice. Source product/option names and the original source rule label remain recognizable, accompanied by localized headings, age labels and demo/sample notices. Additional offline stress renders cover 20 items and long names in every language.

The visual/layout checks and deterministic language routing are not native-speaker approval. Translation quality, particularly Papiamentu, and live provider acceptance/rendering remain separate release evidence.

## Rollback

Disable the unpublished Isluno feature to restore the unchanged legacy routing. Preserve all new quote versions, stage history and delivery evidence. Do not erase records or replay previously attempted messages as a rollback.
