# ISL-11 — Brand, navigation, Today and automation clarity

Issue https://github.com/unboks-org/unboks-dashboard-api/issues/166. Backend issue base `020be6bd6da1e6010904bda80f3b38d5bfce2416`; dashboard issue base `bdfda333671e3f2166ec545c49cf199a0f7a3d06`. Ten of fourteen implementation issues accepted before this review; zero merged/verified. Both evolving draft PRs target `release/isluno-demo-v1`. This issue awaits independent Gatekeeper acceptance.

## Operator behavior

The actual existing DashboardShell/RentalDashboardShell reads the authenticated Isluno brand profile and itinerary capability. Enabled Mermaid technical workspaces display the approved brand and assistant name, Guests, Itineraries and Products & pricing navigation, with a matching catalog settings category. Technical tenant, session, provider and number configuration do not change. Capability loading/error is explicit. Disabled capability retains Mermaid operations; other tenants retain their own navigation and brand. Historical Mermaid route/query entries force original branding and legacy list links, with an explicit return to current itineraries.

An authenticated GET `/isluno/operations/today` projects the same scoped persisted itinerary data: journey/item/demo-paid totals, incomplete or failed outbound parts, current quote review, pending/active operator requests, consented email states and trip selections scheduled today in each trip timezone. Attention counts are counts of recorded actions/parts, not unique customers. Quote version/status and part context remain visible in attention labels, including superseded failures. No queued/accepted send is represented as delivered. Dates/availability are explicitly demo assumptions; payment remains simulated and no supplier reservation or charge is asserted. Empty states are shown only after successful reads; failed refreshes retain records with a stale-data warning and suppress current counts/badges. The projection currently scans demo records and builds full details before returning; large-dataset performance is not claimed.

Automation uses the existing authenticated `/agent/status` GET/PUT contract. Missing/error/unknown state cannot enable controls. A failed mutation now invalidates the shell's displayed certainty until an explicit status refresh succeeds. No optimistic active/paused transition is introduced. Mutation responses retain their original tenant scope and cannot populate another tenant's cache after a switch. The global reply setting is explicitly distinguished from conversation takeover. Existing inbox/takeover links and controls remain the existing implementation; a pending operator request is not presented as active takeover.

## Verification

- Backend: 165 tests passed in 20.735s with external sockets/DNS denied. Three new Today checks cover pending/ambiguous/queued/handover projections, demo totals, auth/feature guards, read immutability, verified empty state and local trip date boundaries. Existing catalog, party corrections, quote/payment/documents/email/isolation checks are carried forward.
- Frontend: 45 tests across seven files passed in 1.91s; typecheck passed; production build passed in 2.97s. Eight new workspace checks mount actual shell/hooks/API wrappers and cover brand/navigation, legacy return, capability off, unknown/error states, existing PUT/server-returned state, mutation failure/recovery, late tenant response isolation and unavailable attention data. Existing rental/Mermaid navigation, legacy Today, itinerary and tenant tests pass. Their original fixtures explicitly retain disabled Isluno capability behavior. No runtime dependency/lockfile changes.
- Browser used the actual DashboardShell, RentalDashboardShell, Today, reservation list and Settings/catalog pages, unlike the earlier outer-chrome substitutes. Login is supplied by an explicit synthetic AuthContext; no login, session-renewal, live provider or live takeover verification is claimed. Other-tenant browser evidence mounts the actual rental shell with a labelled fixture body; it is not a full Ali workflow test.
- Loopback FastAPI runs real conversation/payment/quote stores, operations/catalog routers and the exact host dashboard authentication and agent-status handler functions loaded from source. Only the underlying control bridge is replaced with an in-memory synthetic envelope/write adapter. The successful browser Pause action generated exactly one synthetic control write and read back paused. Injected PUT failure showed unknown/disabled controls plus an error; explicit GET refresh restored verified active state. Unknown envelopes and 503 GET failures stayed unavailable. No real operator, customer or provider state was mutated.
- Browser navigated from Today into Products & pricing in actual Settings, then itineraries, original Mermaid history and back. Capability-off retained original operations. Another-tenant entry showed Ali Car Rental / Nick / Fleet navigation with no Isluno content or reconnect requirement. Mobile document and viewport widths both measured 390; menu and six-item navigation remained usable. A local aborted Today refresh retained records, showed unavailable/stale warning, hid current counts and removed the attention badge. No browser exceptions or Vite overlay were observed. Missing unrelated settings/order/appointment endpoints deliberately return 404 in this bounded fixture; synthetic 502/503 and aborted requests are fault evidence, not successful operations.
- Thirteen screenshots and SHA256 manifest are in dashboard `output/isluno-11`. Paused Today, mobile Today, final failed-control state, stale Today and other-tenant menu were visually inspected. The failed-control screenshot was replaced after the final certainty/recovery correction. Owned fixture servers and browser were closed after verification. There were no asset/PDF/source refetches; earlier accepted artifact evidence is unchanged.

## Reproduce locally

Backend:

```sh
tmp/isluno-test-venv/bin/python wtyj/scripts/verify_isluno_offline.py
tmp/isluno-test-venv/bin/python wtyj/scripts/serve_isluno_operations_fixture.py --workspace --manifest tmp/isluno-11-fixture.json
```

The fixture listens on 127.0.0.1:8788, creates disposable synthetic records and denies outbound dispatch before imports. Existing test-local dependencies include uvicorn 0.41.0. `--workspace` installs only test-owned routes from `scripts/isluno_workspace_fixture.py`, including authenticated `/__fixture/state` controls for synthetic availability/feature/failure modes. These are never mounted by production routers. No real environment secrets or customer records are loaded. Stop the process after checking.

Dashboard:

```sh
pnpm --filter @workspace/unboks test src/components/isluno/IslunoWorkspace.test.tsx src/components/rental/RentalDashboardShell.test.tsx src/components/rental/MermaidNavigation.test.tsx src/components/inbox/RentalNavigation.test.tsx src/pages/MermaidToday.test.tsx src/components/isluno/IslunoOperations.test.tsx src/lib/tenant-isolation.test.ts
pnpm --filter @workspace/unboks typecheck
pnpm --filter @workspace/unboks build
PORT=5180 BASE_PATH=/ pnpm --filter @workspace/unboks exec vite --config tests/isluno-workspace.config.ts
agent-browser --session isluno11 open http://127.0.0.1:5180/tests/isluno-workspace.html
```

The test Vite config disables dotenv loading, proxies only to loopback and does not replace the application shell. Add `?tenant=ali-car-rental` to the fixture entry for the bounded other-tenant shell case. Synthetic records in `tests/isluno-today.fixture.json` are captured from the real local Today endpoint. Local logs: backend `tmp/isluno-11-full.txt`, dashboard `tmp/isluno-11-ui-tests.txt`, `tmp/isluno-11-typecheck.txt`, `tmp/isluno-11-build.txt`, `tmp/isluno-11-browser-network.txt` and `tmp/isluno-11-browser-errors.txt`.

## Boundaries and rollback

External test spend $0. No live model/send/email/payment/supplier call, production-data access, number/provider/credential/config activation, merge or deployment. Tenant brand/capability changes are display-only in this candidate; disable the gated workspace capability to retain legacy operations without deleting any records. Existing authenticated global automation contracts and permissions remain unchanged. Restart/recovery/reminder migration stays ISL-12. Actual login and live takeover/provider claims remain limitations to track explicitly for ISL-13 integration/release evidence.
