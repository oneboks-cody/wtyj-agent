# Mermaid-only release sequence for Calvin's manual test

Customer journey frozen at e2deab8c2ad6c4313324d5b94769b91a1f0ae168; frontend2c99c3a13e01af625432e5f681d4c065e95dd1a8. Calvin will send WhatsApp messages himself. Existing signed-in Nr2 workspace was found by the gatekeeper; no login reset, new number or agent-paid test is needed merely to support his manual test. This is preparation, not an executable deployment approval.

## Concrete material being prepared locally

The local Colima UNIX Docker engine is Linux/aarch64 and advertises linux/amd64 support through BuildKit. Build uses exactly the365-file accepted source archive in an isolated context and Dockerfile.release, preserving direct requirements. Official Python3.12 amd64 manifest and gws0.8.0 archive are pinned; local build evidence records final status/image identity separately. No host engine, runtime mounts or credentials enter the build.

Use the existing five-file default-off config archive and accepted static archive. No feature expansion or reminder activation accompanies this release. The following exact config leaves form the proposed patch, applied by a canonical-lock/CAS helper to the existing protected document, preserving account/slug/secrets and all unrelated values:

| Phase | Selected leaves |
| --- | --- |
| Compatibility installation | features.isluno_itinerary_demo_v1=false; features.mermaid_cutover_coordination=true; isluno.native_carousels=false. Stage the five approved Isluno files; keep isluno_recovery.json enabled=false. Do not set activation_generation until a real seal exists. |
| Activation | features.isluno_itinerary_demo_v1=true; features.mermaid_cutover_coordination=true; features.mermaid_reminders=false; mermaid_maintenance.activation_generation=the exact just-sealed generation. Proposed media_base_url=https://api.unboks.org/api/mermaid/r/isluno/media and document_base_url=https://api.unboks.org/api/mermaid/r/isluno/documents match source router composition and require selected route verification/approval before binding. native_carousels=false remains the accepted delivery mode. |
| Guard-preserving rollback | features.isluno_itinerary_demo_v1=false; keep coordination installed and legacy cutover/quarantine records; keep legacy/Isluno reminders disabled pending explicit reconciliation. Retain current DB, generated documents and new booking/payment/outbound ledgers. Restore only reviewed compatible static pointer/config leaves under fresh CAS. |

The C1 digest is historical observation, never a future compare-and-swap value. Prepare a fresh same-lock read, exact expected-account private equality, minimal diff and same-protection write for the separately authorized release. Never replace full client.json with the tracked template. No merged protected document or write has been prepared from private data in this step.

## Required order of the actual coordinated action

1. Resolve the one missing bootstrap mechanism before stopping anything. The existing control-plane router forwards directly to wtyj-mermaid:8001; a dashboard Nginx rule alone does not close that path. The watchdog marker alone does not close it either. Need a supported, reviewed Mermaid-only retryable forwarding boundary that leaves acknowledged work retained and control callbacks available. If no such action exists, a small deployment-control change is needed; it is not a customer-flow feature. Do not use inbox-disable, stale ownership/deletion or exclusive lifecycle locking: those can ACK/drop or deadlock callbacks.
2. Once an exact boundary exists and is approved, coordinate the named unboks-tracy-watchdog service/timer and any in-flight recovery before setting /root/clients/mermaid/.maintenance. Its status.lock is a possible coordination point that must be verified rather than guessed. The marker's double check still has an in-flight restart race. Keep ownership/number/subscription unchanged, and account for Docker restart policy.
3. Close new Mermaid admission through that boundary while preserving the actual provider retry/retention contract. Finish accepted background/scheduled work or approve explicit retained no-replay dispositions for failures/ambiguity. Review fixed aggregate categories and producer coverage; do not infer zero work from one stopped process. No complete old-runtime work surface is currently established. An exact aggregate/reconciliation step is still needed before this action is executable.
4. Under external protection and after the above disposition, take a consistent private backup of the tenant DB with WAL handled correctly plus protected config/static/image metadata; record digest and restore verification without copying customer data into Git. Install only the pinned compatibility image and staged config into service agent with existing mounts. This first installation cannot be protected retroactively by its own new coordinator.
5. Verify the compatibility runtime and all participating producer identities, confirm initialization of the open coordinator generation, and only then transfer protection from the external boundary to the installed coordinator. No uninstrumented producer may remain active. Use the existing authenticated status surface for selected aggregate observations, with no customer payload output.
6. Invoke existing close(expected_generation, seconds<=120, full COVERAGE, evidence) only with actual verified producer/disposition evidence. Drain, then seal the same generation. Apply the exact activation patch and perform startup/cutover transaction within that deadline. require_sealed rechecks the real generation and work state; unknown or failed work stops promotion. No fabricated coverage reference, force-ready, expiry extension or row deletion.
7. Verify guarded activation, identity and selected media/document routes, then explicitly reopen the sealed generation. Restore watchdog operation only after runtime/protection state is verified. Calvin can then run the manual flow in the same WhatsApp number and inspect the same Nr2 workspace. A deployment pause remains in effect if any prior step fails.

## Failure handling

Before activation, keep external protection and watchdog coordination until the failure is understood; do not leave an unreviewed partial restart unattended. After activation, preserve the guard-capable image and current data. Disable the demo using only reviewed leaves; uncertain outbound/payment/business actions remain operator-review/no-replay. Do not restore a pre-fence binary or an old DB over new records. A backup is recovery material, not permission to erase records created since it. Reopen only after explicit readiness verification.

## Exact remaining physical prerequisites

- A completed compatible local image with recorded resolved packages and passing synthetic image checks; local-image.json will state the actual result.
- A real Mermaid-only forwarding/admission boundary for first installation, coordinated with the existing watchdog, plus an exact old-work aggregate/disposition step. Recovered GitHub source exposes the path and hazard but does not provide a complete existing maintenance API. This is the principal deployment-control gap.
- Current binding of the recovered ICP runtime/subscription/tenant store and callback destination, selected media/document routing, and a concrete consistent backup/CAS destination/window. Known host/admin/number/account intent need not be rediscovered; only those selected current bindings/actions remain.
- Review and approval of the final image/config/service/backup/boundary action as one concrete release, then Calvin's own manual WhatsApp test. No agent live-send budget is being requested.

No production command has been executed by this sequence. No broad inventory or additional dashboard-runner iteration is proposed.
