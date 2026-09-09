# Isluno manual-demo release — current action proposal

Status: local candidates built; deployment NOT performed. This document supersedes earlier compatibility-startup and absent-image proposals. Independent review is required on the final packet, then Calvin's action-specific authorization. It grants no host access by itself. Existing H1–H7 and C1 inspection allowances are consumed.

## Outcome and scope

Activate Isluno for the existing **Mermaid tenant and WhatsApp connection** on host `108.61.192.52`, preserving its strict single-account binding. Calvin will send his own WhatsApp messages and use Nr2 to test the 31-product demo, trip photos, multiple-trip itinerary, labelled sample rules, simulated payment, documents and operator controls. Real supplier booking and real money collection are outside this demo. Native carousel rendering remains disabled; supported image messages provide the photo fallback.

This action also deploys a Mermaid-specific ingress gate in the **shared ICP service**, and switches the **shared Nr2 static release pointer**. ICP replacement can briefly interrupt other tenants' forwarding; the static release serves other tenants too. Preserve their routes, bindings and backend services. There is no number provisioning, subscription change, provider-model change, credential replacement or outbound agent canary in this action.

## Immutable inputs

| Input | Identity / evidence |
|---|---|
| Mermaid runtime | `e2deab8c2ad6c4313324d5b94769b91a1f0ae168`; `local-image.json` |
| Mermaid image index | `sha256:7375faa0dd8484fa15b6d3095a54e014562cb20e9b622cceb294780fcd635bc6` |
| Mermaid image archive | SHA-256 `b1ec5bb6733efb1cfa092ee7d7ac884b7715f73ebb340e3d66c7d98cd98151da`, 169445888 bytes |
| ICP gate source | `70ea4a7e48758b8e3d99e84d1e6543657f5e80c0`; separate isolated control-panel repository |
| ICP image index | `sha256:28caf44113a63cd9c825eb3c93955f1d77b4969195d6e7ca010c90e966e44f7c` |
| ICP image archive | SHA-256 `06f0a11c8b9d3a7d2dc1d86a9dbd39b6342d5956a73275b444f9ff4343ab6e46`, 63651328 bytes; `icp-image.json` |
| Dashboard source | `2c99c3a13e01af625432e5f681d4c065e95dd1a8`; unchanged accepted build |
| Dashboard archive | SHA-256 `9faecd003e4d32da89bf032e755307b39e879e937414e753eb22b3f2090d4dd2`, 738983 bytes |
| Public config assets | SHA-256 `d3765cd214b55501b5cf26ec152f8cd49fb733eba0feed09f6d9b40836fa3c63`, 39521 bytes; never replace protected client.json wholesale |
| Operator helpers | `release-helpers.json` binds exact bytes; read-only mount `/release-tools/wtyj/scripts` |

Image indexes, platform manifests and image config digests are distinct; platform identities are in the image manifests. Verify imported images against the reviewed archives rather than a mutable tag. No image has been published or transferred to production.

## One bounded selected preflight

The proposed release authorization must include one preflight on the named host: maximum 120 seconds, no automatic retries, no outbound provider calls, no customer bodies or credentials in output. Its only discovery is the selected Mermaid/ICP/dashboard/watchdog configuration needed for this release. Fail before service/config/database changes if a binding differs or a necessary producer cannot be fenced.

Verify `wtyj-mermaid`, compose `/root/clients/mermaid/docker-compose.yml`, service `agent`, config `/root/clients/mermaid/config`, data `/root/clients/mermaid/data/state_registry.db`, Linux/AMD64 and the prior observed image identity `sha256:ed146176bbf4e116270b55a04771af5fb3cd5d0f62d124d8650aa7e1bac97552`. Project strict Mermaid slugs and single stored account fingerprint `ca117468b3369f1961ebc941f6774f539d98f82be488dd433c0a2df4eb6a8c09`; keep exact account and fresh config CAS bytes private. C1's prior config SHA is historical, not the next CAS value.

Resolve only the running ICP service that handles `/internal/api/zernio/webhook-router`, its compose/image/data mount and selected stored account-to-Mermaid mapping. Verify uniqueness for that account and route to `http://wtyj-mermaid:8001/webhooks/zernio`. Do not invoke ICP channel-status GET or the live owner resolver: they can call providers or rewrite configuration. This proves the selected stored deployment binding, not absence of every possible external webhook integration.

Verify loaded dashboard/API routing for the selected Mermaid prefix and the static pointer `/var/www/unboks-dashboard/current`; save the exact prior pointer. Inventory only writers/producers of this tenant's mounted data/config: its app workers, ICP operator/config writers, selected scheduled jobs, and `unboks-tracy-watchdog.service/.timer`. Check the watchdog maintenance marker and lock contract from its installed script without executing the watchdog. Resolve any other process holding/writing the selected files before proceeding.

Require the backup/staging destination `/root/backups/isluno-manual-e2e-20260909-a` to be absent, adequate free space for both image archives and private backups, and the target release directories to be new. The preflight must produce a private action binding for the actual ICP compose/service/data path and producer units; no guessed service name or wildcard stop is authorized. An unresolved binding stops this action rather than causing another broad inventory round.

## Configuration changes

The stopped-service writer uses the canonical client.json lock and atomic exchange, with a fresh SHA-256 comparison inside that lock. It preserves credentials, strict account binding, business identity and unrelated fields. Private original bytes are backed up before each mutation. The helper defaults to no reads/writes without its explicit execution flag.

| Leaf | Stage | Activate | Guard-preserving rollback |
|---|---|---|---|
| `features.mermaid_cutover_coordination` | true | true | true |
| `features.isluno_itinerary_demo_v1` | false | true | false |
| `features.mermaid_reminders` | false | false | false |
| `isluno.native_carousels` | false | false | false |
| `isluno.media_base_url` | `https://api.unboks.org/api/mermaid/r/isluno/media` | same | same |
| `isluno.document_base_url` | `https://api.unboks.org/api/mermaid/r/isluno/documents` | same | same |
| `mermaid_maintenance.activation_generation` | preserve | exact sealed generation | preserve |

The media/document bases remain subject to the selected loaded route verification. Activation requires `require_sealed` in the actual pinned runtime. No customer workflow/provider secret is printed or copied from Git.

## Execution and acceptance

Follow `RELEASE-SEQUENCE.md`: stage immutable artifacts, retire the old ICP generation while installing its closed gate, fence and stop selected writers, preserve/verify private backups, stage feature leaves, run the explicit sealed one-shot directly in the new Mermaid image, activate the exact generation and start the app within its 120-second deadline. There is no compatibility app startup before old-work classification.

The old-work projector permits settled records and waiting customer drafts. It stops on unfinished, ambiguous, failed-send or unknown records. Waiting drafts remain intact and become quarantined by the accepted transition; they are never fabricated as completed. The one-shot starts no application worker and avoids importing state_registry. A closed initial ICP store alone does not prove old untracked forwards have drained: retirement of the old ICP generation and stopped Mermaid writers are prerequisites.

Verify the live cutover marker and generation, Isluno capabilities and 31-product catalog through the selected authenticated tenant route, owner controls, static asset hashes, and public media/document routing. Generic health or an existing login alone is not acceptance. Reopen the runtime generation only after readiness, then the ICP gate using its observed exact generation and explicit runtime-ready attestation. Preserve a private action log with artifact digests, selected service IDs, stop/retirement evidence, config CAS receipts, backup hashes, gate generations and read-only readiness results.

Only then hand Calvin the Nr2 link for his manual A–Z messages. No agent-sent WhatsApp/email or paid evaluation is part of verification. His manual customer-runtime use is separate from a coding-agent API test budget.

## Failure and rollback

Any mismatch, lock contention, uncertain old work, expired seal, incomplete config exchange or readiness failure leaves admission closed. Do not retry automatically, infer completion, replay uncertain sends, or force a gate open. Preserve error receipts and exact state for the operator.

Keep the guard-capable Mermaid and ICP images, current database, cutover marker, quarantined original rows and any new records. With admission closed and writers stopped, use fresh CAS to disable Isluno and reminders while preserving coordination; restore the saved dashboard pointer only if compatible with the retained backend. Do not restore an old database over new records or switch to a pre-gate binary. Private SQLite backups must pass integrity/read-only aggregate verification before promotion, but remain recovery evidence rather than an automatic restore command.

## Authorization boundary

After the gatekeeper accepts the final exact packet, request one concrete authorization for this selected preflight and conditional release sequence, including shared ICP/Nr2 effects, stopped-service configuration writes, private backups, additive coordinator/cutover database changes, image replacement, static pointer switch and verified reopening. Current local preparation and generic “proceed” do not authorize those host mutations under the applicable Unboks policy. No merge or provider mutation is included.
