# BRIEF — One wheelchair acknowledgement per reply
**Status:** Tested | **Files:** mermaid_understanding.py, mermaid_reservation_workflow.py, test_mermaid_wheelchair_flow.py | **Depends on:** loop-stop release | **Blocks:** none

## Context
The live opening enquiry combined wheelchair eligibility and next-week dates. The model supplied a paraphrased wheelchair acknowledgement in other_question_reply; exact-copy removal missed it and Python added its own saved-note acknowledgement.

## Why This Approach
Require a structured ordinary FAQ topic plus exact guest evidence before appending an answer on assistance routes. Reject string/keyword deduplication because phrasing and language vary. Missing legacy topic fails closed for this optional answer. Keep one model call and existing authoritative crew-note persistence. This guards composition but still depends on correct model topic classification.

## Instructions
Extend the structured contract in wtyj/agents/social/mermaid_understanding.py. Replace exact acknowledgement removal in wtyj/agents/social/mermaid_reservation_workflow.py with topic/evidence eligibility. Preserve genuine mixed FAQ answers and the existing calendar next question. Deploy a two-module overlay on the current live image, preserving config and customer data.

## Tests
154 wheelchair flow tests pass, covering six languages, paraphrased duplicate with missing/none/accessibility/protected topic, separate food answer preservation, absent evidence rejection and persisted crew note.

## Success Condition
The combined wheelchair/calendar enquiry produces one assistance acknowledgement and one next question, while a distinct FAQ survives.

## Rollback
Restore compose image wtyj-agent:tracy-loop-stop-f1aefc5 and recreate only the Mermaid agent; no schema or data migration.
