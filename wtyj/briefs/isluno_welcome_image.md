# Isluno first welcome image

**Status:** Implemented | **Files:** `clients/mermaid/config/isluno_profile.json`, `wtyj/assets/isluno/brand/`, `wtyj/shared/isluno_config.py`, `wtyj/shared/isluno_media.py`, `wtyj/agents/social/isluno_conversation.py`, `wtyj/agents/social/isluno_discovery.py`, `wtyj/agents/social/isluno_wire.py`, `wtyj/tests/isluno/test_welcome_image.py` | **Depends on:** sunny welcome source `1741a2de` | **Blocks:** customer verification

## Context

Calvin supplied the Isluno wordmark and requested it above Tracy's first welcome text. The existing trip-media path only admits catalog gallery images, and the wire splitter moves rich media to the final part when text exceeds WhatsApp's interactive limit.

## Why This Approach

The logo is a profile-approved brand asset with exact hash, size and dimensions. It is not inserted into a tour gallery. The existing public media endpoint may serve only that exact profile asset or a verified catalog asset. First-welcome eligibility uses conversation delivery state and stage, while the wire layer explicitly preserves media-first order. A general static-file route was rejected because it would broaden public file access.

## Instructions

1. Validate the exact brand asset metadata in the active Isluno profile.
2. Serve the exact approved asset through the bounded JPEG media path.
3. Attach it only when there is no confirmed prior assistant delivery, the saved stage is welcome, no booking action is authorized and no product was selected.
4. Keep the welcome image on the first wire part when long text is split.
5. Preserve ordinary trip-card ordering, delivery idempotency and customer data.

## Tests

Verify exact brand allowlisting and rejection, one-part and long media-first ordering, first-welcome plan attachment, and absence on follow-up or transactional/product flows. Tests use local fixtures with network disabled.

## Success Condition

The first successful broad welcome displays the supplied Isluno image above its text exactly once, without changing trip galleries or booking behavior.

## Rollback

Revert this issue commit and redeploy the previous verified image. Existing conversation data requires no migration.
