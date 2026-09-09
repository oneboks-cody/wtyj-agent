# Isluno invalid model response must not silently discard a guest turn

Calvin’s 9 September 09:45 personal test reached the live tenant and completed one model call. Logs record `invalid_discovery_facts`; no model decision/outcome was stored and no outbound send was attempted. The actual invalid field value was not retained, so its type/count cannot be reconstructed. Current discovery validation checks array type, at most five entries, and string members. Marina turns validation exceptions into generation failure; the Isluno caller discarded that response and returned empty text.

Keep validation and source-fact enforcement strict. Clarify the fact-key schema/prompt, record only safe validator codes and field shape, and return one localized processing-failure acknowledgement for the already-claimed failed turn. Preserve itinerary/guest state and the recovery incident. Duplicate turns must neither re-call the model nor resend an acknowledgement; only a fresh customer message may proceed. This is the documented model/API-failure exception, not a normal business reply template. No second model call, automatic replay, provider setting change, schema migration, or live agent test.

Tests: synthetic malformed fact-key variants through actual Marina and inbound handler; one model call, nonempty failure reply, unchanged progress and failed-turn evidence; duplicate no-call/no-reply; fresh valid turn succeeds; unknown source keys remain rejected. Run targeted suites with network disabled.

Release remains separately governed by the organizer’s current conditional deployment authority. Rollback uses the prior pinned runtime image; retain all current customer records. No production change is made by this source patch.
