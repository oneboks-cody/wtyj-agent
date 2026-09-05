# Mermaid — Neutral child wording and compound ages
**Status:** Prepared locally; live deployment pending authorization | **Files:** clients/mermaid/config/client.json, clients/mermaid/config/reservation_catalog.json | **Depends on:** existing age extraction | **Blocks:** none

## Context
The owner reports repeated unsolicited affectionate labels for children. The existing booking_age_guidance explicitly encourages this wording, and English catalog fallback labels repeat it. The supplied example also shows a compound age interpreted as two children.

## Why This Approach
Correct the tenant's model guidance and existing fallback labels. Keep the single understanding call, structured routing and existing age representation. Reject a reply-text replacement filter or a Python language classifier: those hide the prompting error and violate the architecture.

## Instructions
Use neutral age-appropriate labels; mirror affectionate wording only when the guest introduces it and then sparingly. Preserve professional summaries. Treat a compound years-and-months age as one person, represented by total months; do not increase passenger counts from the two numeric components. Clarify genuinely ambiguous party size. This does not rewrite existing guest records.

## Validation
Parse both JSON files and inspect the exact changed keys. No provider calls or simulated customer conversations. Future authorized behavior evaluation should cover neutral wording without guest precedent, restrained mirroring with precedent, and a single compound-age child with unchanged party count. Static validation does not establish live model behavior.

## Success Condition
Tracy uses neutral child terminology unless mirroring the guest and does not split one compound age into multiple passengers.

## Rollback
Restore only the prior booking_age_guidance and two English catalog labels; preserve unrelated tenant configuration and customer state. Deployment requires action-specific authorization under the current Unboks policy.
