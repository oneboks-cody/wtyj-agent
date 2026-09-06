# Mermaid — Approved handover wording
**Status:** Deployed | **Files:** clients/mermaid/config/response_policy.json | **Depends on:** existing recorded handover status | **Blocks:** none

## Context and approach
The owner approved a warmer two-paragraph replacement for the English review_queued message and explicitly requested deployment. Change that existing copy value only; preserve status routing and all other languages. A workflow redesign or additional model call is unnecessary.

## Validation and success
Validate JSON, verify the one-field diff and read the loaded production copy after deployment. Success means the existing queued-review route loads the exact approved text. No paid model calls or test messages.

## Rollback
Restore the backed-up review_queued value only, preserving other tenant configuration and guest records.

## Deployment evidence
Changed one English configuration value; verified exact loaded copy and health 200 without restart or customer sends. Backup: /root/backups/mermaid-warm-handover-20260906T125002Z. Other configuration and customer data untouched.
