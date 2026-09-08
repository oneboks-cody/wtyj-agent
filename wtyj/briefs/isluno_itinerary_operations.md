# ISL-10 — Itinerary and guest operations

Issue: https://github.com/unboks-org/unboks-dashboard-api/issues/165. Backend issue base: `90b80c263f7d1961dc85144569bc955d05759aba`. Dashboard issue base: `a7bdc86f48c16e2a29c03450801bf193cb887e12`. Nine of fourteen implementation issues were accepted before this candidate; zero merged/verified. This candidate awaits independent Gatekeeper review.

## Behavior

The existing reservation and guest entries select the Isluno workspace through authenticated capabilities. Operators can search itinerary parents, inspect each trip and its guest, price and server-owned stage, open immutable quote revisions, inspect simulated payment and per-trip tickets, and trace recorded events and individual document-send outcomes. Guest history groups the exact conversation/customer scope, not matching display names. Search includes guest/customer, itinerary, quote/payment references and product names.

Authenticated GET-only projections enforce the configured Mermaid tenant, allowed channel account and Isluno journey/schema. The client scopes cache keys and rejects late responses or PDFs after a tenant switch. PDF links read stored quote/receipt/ticket bytes, including superseded quotes, through operator authentication; public customer-link expiry does not remove authorized operator history. Opening uses the browser PDF viewer and its existing print facility. No new booking/payment transition is offered. Inbox/takeover links retain the existing conversation route.

Legacy Mermaid lists and guest pages remain available with an explicit original-brand notice and retained legacy search links. Disabled/older capabilities retain the legacy entry. Broader navigation/branding remains ISL-11.

Provider acceptance is labelled acceptance, never guest delivery. Ambiguous, queued and rejected parts remain distinct. Where existing ledgers lack send/action timestamps, the API returns null and the UI says not recorded; no timestamps or delivery receipts are invented. Payment is explicitly simulated and no supplier booking is asserted. Refresh failures retain cached details with an error. Collection pagination is exposed, but the demo projection currently computes matching summaries before slicing; this is not a large-dataset performance claim.

## Narrow guest correction included in this issue

The real two-party fixture reproduced a handler bug: after adding a second trip for Second Party, changing only the first trip's date replaced its guest Calvin with the current session name Second Party. The operator UI faithfully exposed the incorrect corrected quote. The item write now preserves its prior guest name unless that turn explicitly supplies a replacement; new items use the current intended session name. Regression coverage exercises real conversation actions, corrected quotes and operator readback, explicit replacement, new-item inheritance, preservation of the other item and immutable earlier quote snapshots. Gatekeeper authorized this narrow correction within ISL-10; no separate candidate or business-rule change was introduced.

## Validation

- Backend: 158 tests passed in 19.622s, with external sockets and DNS denied. Five new operations tests cover authenticated projections, one/multiple trips, corrected/historical snapshots, paid documents, truthful partial delivery, wrong account/scope/token, disabled capabilities and read immutability, including the guest regression.
- Frontend: 32 tests passed across five focused files in 1.42s. Six new component tests use the real API wrappers with deterministic fetch responses captured from the actual synthetic backend. They cover list/detail/history, search/loading/empty states, partial failure, legacy selection and late tenant responses. Existing reservation/customer and tenant-isolation checks pass. Typecheck and production build pass (build 3.10s). No dependency or lockfile changes.
- Actual loopback browser → authenticated FastAPI → conversation/quote/payment stores used three synthetic journeys seeded through real handlers: a single draft, a two-trip paid itinerary with two quote revisions and partial delivery, and a single-trip paid itinerary. Outbound dispatch was disabled. The fixture executes the host auth function with a synthetic token and mounts real application pages/routes/API wrappers; only surrounding dashboard chrome is replaced. Outer login/shell, real inbox/takeover mutation and live provider behavior are not claimed verified.
- Browser checked parent/detail totals, both per-item guest names, quote corrections, payment/tickets, ledger states, history, search and empty search, guest history, and an aborted refresh that retained cached details. A tenant change to another tenant then opening a journey showed only “Isluno itinerary workspace is unavailable.” No prior guest/trip data remained. Mobile viewport and document widths both measured 390, with no error overlay.
- Loading was checked against the actual local backend by temporarily stopping its owned process, navigating to guests, observing “Refreshing…” / “Loading guests…”, then resuming it and observing all three guest cards. A screenshot attempt for that particular state failed because the browser daemon resolved a relative path; no loading screenshot is claimed.
- Opening a historical quote produced a native blob PDF tab. Authenticated backend tests fetch every stored quote/receipt/ticket PDF and verify PDF bytes and no-store headers. No physical printer was used.
- Twelve screenshots and SHA-256 manifest are in dashboard `output/isluno-10`. List, multi-trip, quote corrections, partial delivery, refresh failure, mobile trips and guest history were visually inspected. Fixture names/references and products are synthetic. The browser error log was empty; injected request failures are deliberate fault evidence. Owned fixture servers/browser were closed after checks.

## Reproduce ($0 external test budget)

Backend:

```sh
tmp/isluno-test-venv/bin/python wtyj/scripts/verify_isluno_offline.py
tmp/isluno-test-venv/bin/python wtyj/scripts/serve_isluno_operations_fixture.py --manifest tmp/isluno-10-fixture.json
```

The second command uses existing test-local dependencies including uvicorn 0.41.0, creates a disposable synthetic database, denies outbound socket dispatch and listens only on 127.0.0.1:8788. Stop it after verification.

Dashboard:

```sh
pnpm --filter @workspace/unboks test src/components/isluno/IslunoOperations.test.tsx src/pages/MermaidReservations.test.tsx src/pages/MermaidReservationWorkspace.test.tsx src/pages/MermaidCustomerAccount.test.tsx src/lib/tenant-isolation.test.ts
pnpm --filter @workspace/unboks typecheck
pnpm --filter @workspace/unboks build
PORT=5180 BASE_PATH=/ pnpm --filter @workspace/unboks exec vite --config tests/isluno-operations.config.ts
agent-browser --session isluno10 open http://127.0.0.1:5180/tests/isluno-operations.html
```

The test-only Vite config disables dotenv loading and proxies only to loopback. Synthetic session data is supplied only by the explicit test entry. Production proxy/auth configuration is unchanged. Backend log: `tmp/isluno-10-full.txt`; frontend logs: `tmp/isluno-10-ui-tests.txt`, `tmp/isluno-10-typecheck.txt`, `tmp/isluno-10-build.txt`. Temporary logs are local evidence, not committed credentials or customer data.

## Boundaries

External test spend $0. No live model, WhatsApp/email, supplier/payment calls, source/image refetch, tenant/number/provider changes, feature activation, merge or deployment. Existing immutable catalog, quote and fulfillment evidence is preserved. Both evolving draft PRs target `release/isluno-demo-v1`. Disable the itinerary workspace capability to restore the legacy entry without deleting records; authenticated records remain available for operator recovery.
