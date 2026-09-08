# ISL-03 branding and isolated context candidate

Issue: https://github.com/BensonOpas/wtyj-agent/issues/348
Base: dfff99f1c6025465ed2f31762e16ae113baae904 (ISL-02 accepted candidate).
Repository: BensonOpas/wtyj-agent. Technical tenant: mermaid. Environment: isolated local candidate, synthetic offline fixtures.

Adds a six-language public Isluno profile, an unapplied disabled feature fragment, fail-closed identity/capability checks, additive SQLite conversation contexts, and authenticated dashboard capability discovery. The host dashboard's existing authentication dependency protects the new route. No client.json, phone binding, provider/model selection, old intake table or existing messaging route is changed.

Context keys bind tenant, allowed account, conversation, customer, journey and schema. Creation snapshots branding; subsequent profile changes do not rewrite existing contexts. Revision checks serialize conflicting edits. Disabled features and foreign scopes fail before database side effects. The capabilities endpoint advertises only profile/context foundations; booking, payment and editor remain false pending their implementation issues. This is not customer chat integration, which belongs to ISL-06.

## Bounded parallel pilot evidence

User authority is attributed to the Builder relay from coordinating task 01a0811e-f378-76a1-af2e-3ad4548c2516: “Calvin explicitly approves and activates the following parallel-agent workflow. Apply it to the current work without restarting completed work. This authorizes bounded parallel subagents, not deployment, live provider calls, or bypassing any existing project-plan approval requirement. Preserve current model settings.” Gatekeeper is not the source of this authority.

One test-only agent, isl03_identity_tests, worked from fb00df0300b50c43fd507882ba754f2459c9dc12 in the separate isluno-03-identity-tests-20260908 worktree on codex/isluno-03-identity-tests. Its sole allowed file was wtyj/tests/isluno/test_journey_config.py. Builder retained configuration, schema, contracts, routing and integration ownership. Baseline: 19 network-denied tests passed, configuration module compiled, no baseline failures. Agent submission 6fd2ca7097dabfab4757b2d60df803cf3deebf46 reported 29 checks passed, no blockers, no sends/spend/push. Builder inspected the complete diff and verified exactly one added allowed file with no deletions or unrelated edits, then sequentially cherry-picked it as cc36074. No shared-file collisions or contract changes were required.

## Verification

python3 wtyj/scripts/verify_isluno_offline.py: 36 passed, 0.242 seconds. Sockets and DNS denied; synthetic configuration, temporary SQLite databases and real tenant guard; legacy pytest root fixtures excluded. Coverage includes disabled/malformed configuration and profile, strict account/tenant boundaries, safe public projection, unchanged synthetic provider/number configuration, immutable brand snapshots, legacy-row preservation, context persistence and distinct identities, concurrent edit conflict, no writes on invalid scopes or disabled access, state validation, and HTTP authentication/cache headers/capability revocation.

HTTP verification uses the actual router factory with an injected synthetic authentication dependency. Host wiring was inspected; this does not claim full deployed application verification. No live runtime changes, external provider tests, customer sends, supplier reservations, payment or deployment occurred. Papiamentu copy remains marked for native review in the profile.
