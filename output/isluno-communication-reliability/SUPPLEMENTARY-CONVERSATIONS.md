# Changed communication cases

Synthetic fixtures through the actual handler. No live model evaluation or customer sends.

## test_optional_unused_outcome_branches_are_not_required

Guest: Add this trip

Reply: Happy to help.

Could you share the guest name?

Model calls: 1. Actual handler output from scripted SDK; delivery only asserted by named test.

## test_stale_button_returns_operational_notice_without_model_or_mutation

Guest: [Earlier Add trip button]

Reply: That earlier choice is no longer current. What would you like to check or change in your itinerary?

Model calls: 0. Durable operational notice prepared; no live send.

## test_fresh_hi_after_rejected_reply_is_a_new_turn_without_replay

Guest: hi

Reply: Hello! I can help you plan your visit.

What do you enjoy doing on holiday?

Model calls: 1. Actual handler output from scripted SDK; delivery only asserted by named test.
