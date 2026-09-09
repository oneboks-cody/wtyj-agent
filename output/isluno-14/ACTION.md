**Latest runtime candidate includes maintenance coordination changes. The source/config/static archive records below predate this backend delta; the old backend source archive is NOT the current release candidate. Repackage the reviewed runtime and pin its image before any release approval.**

# ISL-14 proposed release action — target binding still incomplete

**Preparation only. Not approved for execution.** ISL-13 passed independent review. Current counts are 13/14 accepted in candidate, 0/14 merged/verified, three deferred. This packet pins local release material and identifies exact outstanding target facts. Do not approve a blanket deployment against placeholders; complete the target binding and obtain Calvin's final action-specific authorization first.

## Pinned material

| Component | Proposed version / artifact | SHA-256 |
|---|---|---|
| Backend source | Accepted `4f62147871ada343edb7360c66fe8baae89eae90`; `isluno-backend-source.tar.gz`, 60,630,992 bytes | `f680d43f740101b428131e1a70dfabd81736b41d1990e45cfe6d0834951ccbce` |
| Tenant-owned default-off config | Five reviewed Isluno JSON files at that backend version; `isluno-config-default-off.tar.gz`, 39,521 bytes | `d3765cd214b55501b5cf26ec152f8cd49fb733eba0feed09f6d9b40836fa3c63` |
| Frontend static build | `2c99c3a13e01af625432e5f681d4c065e95dd1a8`; `isluno-frontend-static.tar.gz`, 738,983 bytes | `9faecd003e4d32da89bf032e755307b39e879e937414e753eb22b3f2090d4dd2` |
| Backend OCI image | **Not built/pinned**: verified target platform, immutable base and dependency image are missing | No digest; source archive is not an image |

Archives are retained locally at `/Users/calvin/Projects/isluno-whatsapp-demo-20260908/tmp/isluno-release-artifacts/`. `artifacts.json` records every packaged member and all 66 frontend file hashes. The backend source archive contains 364 approved tracked runtime/source/assets/template/build-input files, excluding tests, briefs, live config, databases and secrets. It contains a reviewed full source payload, not evidence that an arbitrary existing base image is compatible.

The frontend was built locally from unchanged accepted source in a cleared process environment, with Vite envDir disabled and base `/`. Build passed in 3.17 seconds. Each archive hash and member path was checked; frontend HTML references resolve to packaged assets. No node API server is packaged or proposed for deployment. The source README identifies `artifacts/unboks` static files and canonical Python tenant APIs; `artifacts/api-server` is explicitly not the production backend.

The accepted ISL-13 manifest/evidence remains in `../isluno-13`. Current preparation adds the output/isluno-14 packet, its brief and the isolated H1–H7 inspection script/tests; it does not alter the accepted customer-facing application sources or configuration. Future OCI packaging must pin its immutable base/platform/dependencies, verify the packaged source and run warranted offline image checks before the execution packet can be approved.

## Exact proposed changes

**Tenant and route:** technical tenant `mermaid`, brand Isluno, journey `isluno_itinerary_demo_v1`, intended existing number +1 223 276 0075. The exact current account ID, ownership and sole consumer are **unknown**, not derived from a historical allowlist. No second bot/consumer, number reconnect, account transfer or provider display-name mutation is proposed. Customer-visible native profile-name behavior therefore remains unverified.

**Backend:** replace only the verified Mermaid service's runtime artifact with the approved OCI digest once available. Preserve volume bindings and other tenant containers. Repository deploy code documents service `agent`, container `wtyj-mermaid`, live directory `/root/clients/mermaid` and health `127.0.0.1:8102`; these are recheck candidates, not asserted current values. The top-level supervisor also starts email-poller/hold-reaper/ali-quote-recovery; current enabled processes must be inventoried before choosing the runtime image or drain procedure.

**Frontend:** stage the pinned 66-file static build in a new release directory on the verified frontend host, then atomically switch only the verified existing frontend pointer after backend readiness. Preserve the previous static release and API routing. Repository README documents `/var/www/unboks-dashboard/releases/*`; GitHub currently documents pointer `/var/www/unboks-dashboard/current` on `dashboard.unboks.org`; the September 8 baseline records release `2e41f4a209fbafc602419408696a25fa9791dc34`. These are sourced recheck candidates; current server state is not verified. No Nginx tenant-routing rewrite or TypeScript API deployment is proposed. Compare the actual deployed source with the accepted baseline before replacing it, so intervening changes cannot be silently lost.

**Configuration:** install exactly `isluno_profile.json`, `isluno_catalog.json`, `isluno_inventory_report.json`, `isluno_feature_patch.json` and `isluno_recovery.json` from the pinned package in the verified Mermaid config directory. Keep feature false while staging. The later approved activation may change only these client configuration leaves: `/features/isluno_itinerary_demo_v1` to true, `/isluno/native_carousels` to false, `/isluno/media_base_url` to the verified approved HTTPS base, and `/isluno/document_base_url` to the verified approved HTTPS base. Preserve slug, the exact current strict account allowlist, all credentials, owner controls and unrelated tenant/operator settings. Reminders remain disabled. Null URLs are a stop, not permission to omit document delivery. The exact merged diff and compare-and-swap source digest cannot be generated until the current sanitized projection and protected change mechanism are identified.

**Database:** apply only the accepted additive runtime initialization on the existing verified tenant database: Isluno tables/indexes/immutable-ledger guards and nullable `claimed_at` migration where needed, followed by cutover quarantine. Keep original Mermaid business rows and every post-cutover ledger. No schema drop, stage rewrite, manual acceptance/payment flag, old-DB restore or data export is proposed. Store any approved backup privately on the target, not in Git or this packet.

**Provider / live testing:** no provider mutation or live canary is proposed. Local implementation does not prove current ownership, route, rendering, active controls or delivery. Any later canary is a separate action with exact recipient/tenant/message purpose/provider/model, cumulative request/dollar ceiling and enforceable pre-dispatch guards; default is $0.

## Ordered non-executing command plan

`commands.review.txt` contains command forms and hard prerequisites. It is documentation, not a shell script or authority to run. Known local artifact checks are pinned; remote variables stay unbound until current facts are supplied. The protected config apply and admission/drain commands are explicitly **unavailable**, because inventing them would risk losing new inbound work or overriding owner state.

1. Verify release source ancestry, source/build hashes and current-target inventory. The GitHub deployments endpoints for both repositories returned empty lists; that establishes no usable current deployment record, not an absence of running services.
2. Bind the target host/platform/image/base/dependencies; produce and verify the candidate OCI digest locally or in the approved build environment. Confirm actual runtime files and mounted data/config paths match the approved baseline or review the intervening delta. No execution approval is possible while this image digest is absent.
3. Bind the account/number and one consumer; verify the ingress admission/drain mechanism and durable buffering/retry behavior before any service stop. Incoming messages must remain retained. A blind stop/recreate or a second consumer is not an acceptable substitute.
4. Generate the exact protected configuration compare-and-swap diff and install destinations, backup plan and restore verification. Supply the missing public bases. Stage files without activating; review permissions and per-tenant ownership without publishing sensitive contents.
5. Obtain Calvin's approval naming exact digest/host/service/config diff/database action/frontend pointer/window/rollback. Obtain any distinct live-test authorization separately.
6. During that approved window only: capture peer/container/pointer identity; close admission using the verified mechanism; drain/record outstanding work; stop only the verified service; create the approved consistent private backup; install the guard-capable runtime and config; start one consumer with the approved feature. Cutover journal must commit before new turns and old workers resume. No unrelated peer operation.
7. Verify permitted health, source/image/config digests, strict account and sole-consumer routing, durable inbound continuity, quarantine dispositions and operator reads. Switch the static frontend pointer, check assets/API routing, and reopen admission only through the verified mechanism. Never equate health or fixture acceptance with provider delivery.
8. Record exact post-release identities, data preservation and unresolved limits. Any mismatch stops promotion and invokes only the approved guard-preserving rollback. Do not claim ISL-14 complete from this preparation.

## Rollback restrictions and proposed action

**Do not invoke `wtyj/scripts/deploy_mermaid_release.sh` unchanged for this release.** Its failure trap recreates the exact previous image, and its preparer stages unrelated historical Mermaid configuration files. A previous image may predate the Isluno fence; that rollback conflicts with the accepted contract. The script is useful documentation for peer snapshots, locking and private backups, not an approved Isluno executor.

After a future authorized activation, retain the verified guard-capable candidate runtime and current database. Restore only the approved prior feature/client leaf values under compare-and-swap protection; keep the cutover marker and new ledgers. Stop/restart or admission operations, if required, use the exact same approved service and sole-consumer procedure. Restore the previous static pointer only if that compatible UI has been verified for retained history; otherwise leave the guard-capable UI. Never restore an old database over new bookings or return to a pre-guard binary. Automatic legacy work stays fenced; uncertain/claimed/accepted sends do not replay. Operators retain historical reads and manual follow-up.

The exact config CAS/restart/pointer rollback commands remain bound to missing current paths/digests and mechanism evidence. The offline rehearsal already establishes ledger preservation and no replay; it is not a live backup restoration or approval for service operation.

## Required inputs — the narrowly blocked portion

`required-inputs.json` now separates unbound execution inputs from sourced `discovered_candidates`. `target-discovery.json` records the September 8 backend image/source and frontend release, a recently corroborated historical host, deployment paths, current GitHub workflow blob, and limits of CI metadata. The backend baseline matched all 112 runtime Python files to the frozen source. These findings replace the earlier generic inventory request; no further generic inventory is requested.

Current GitHub environments and deployments are empty for both repos; repository variables returned 403. The latest inspected backend CI deployment jobs were skipped. The successful dashboard workflow predates the September 8 baseline and its control-repository SHA is not the dashboard build SHA. The current GitHub workflow differs from this candidate's workflow; selected release paths agree. The Mermaid deploy script is absent at its inspected default-branch path, so its documented paths are branch evidence only.

The remaining step is a narrowly scoped, separately authorized sanitized target inspection and ownership/routing evidence, followed by exact OCI/config/backup/window binding. Historical image and config hashes must not be used as fresh compare-and-swap values. Host access, provider inspection and execution are not granted by this discovery. Public media/document bases, ingress retention/drain, current exclusive number routing and the compatible immutable image recipe remain unresolved. No application rebuild or unchanged acceptance suite was repeated.

Native-language sign-off, outer login/webhook, live takeover and provider rendering/delivery remain separate evidence gaps, with no invented pass. No production SSH, database/config reads, Docker service operations, deploy, merge, provider call, feature activation or live message was performed during preparation.


## Bounded inspection proposal

`INSPECTION.md` extends command-plan step 1 with exact proposed target checks, selected output fields, execution limits, operator-only protected projections and provider-owned evidence. The H1–H7 runner is now implemented with offline/default transport disabled; see `runner-evidence.json` and `runner-tests.txt`. It remains preparation awaiting independent runner review and separate exact access authorization. No host/provider inspection has occurred.


## Authorized inspection attempt

Calvin approved one H1–H7 inspection using runner digest `6e2741055558f8075aafcc3f583fe0a82926be751501d6fc2d0e0b6390d1292d` at code `80e6c86cacfc793ebe724e0a377553ce46818776`. The single attempt stopped at H4 and returned no partial checks. See `inspection-attempt.json` for sanitized status/time/scope; detailed runner output remains private locally. No current binding can be promoted from this result and the specific failure cause is not established. No retry, alternate access or broader inspection was attempted. Earlier no-access statements describe preparation before this explicitly approved attempt; no provider access, protected-data read or mutation occurred.


## Additional authorized H1–H7 outcome

The separately approved corrected-runner attempt completed on 2026-09-09 at 02:05:54 UTC in approximately 2.55 seconds. `inspection-repeat-summary.json` records sanitized comparisons only; full selected metadata remains private. Linux/AMD64 and the expected three bind mounts were verified. Image/start identity matches the September 8 baseline; all 112 inspected Python files match, with none missing or different. The existing frontend release pointer and three checked asset hashes also match. Final container identity validation passed. This does not retroactively establish the cause of the first H4 stop.

`required-inputs.json` now binds ten directly observed metadata fields with timestamps and scope; the original failed attempt remains unchanged. Backend/frontend source SHA fields remain unbound: the selected comparisons support the historical baseline but are not complete source/dependency/build attestation. Protected config/account projection, public API/number/consumer routing, admission/drain/retention, media/document bases, compatible immutable candidate image, backup/config diff/window and exact release authorization remain unresolved. No additional host access, protected/provider reads, sends or mutations are authorized by this successful inspection.


## I1/I2 offline cutover disposition

`INGRESS-CUTOVER.md` and `ingress-evidence.json` trace the three matched baseline Python files and candidate quarantine changes. Durable pre-acknowledgement acceptance/recovery exists, but no coordinated admission/drain boundary is established; inbox-disable acknowledges without retaining new messages. Public Zernio retry documentation is conditional evidence, not configured-subscription proof. The minimal proposed correction is reviewed Mermaid-only coordination in the existing path; no runtime change or operational switch was implemented. Exact next step is design disposition by Gatekeeper, with R1/P1/I2 protected/provider facts still separately gated.


## Implemented maintenance candidate

`MAINTENANCE.md`, `maintenance-coverage.json` and `maintenance-evidence.json` describe the inactive-by-default coordinator,29 worker entry points, verified offline behavior and first-install external-boundary gap.23 focused and221 full backend tests passed; no host/provider access or activation occurred. This runtime delta awaits independent review. Prior archive/evidence identities are retained as historical records, not relabeled as this code.
