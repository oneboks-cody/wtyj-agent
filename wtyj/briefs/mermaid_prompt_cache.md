# Mermaid prompt caching — 2026-09-06

Calvin authorized deployment of stable-context caching while preserving reply quality.
Scope: Mermaid/Tracy only; customer runtime remains autonomous. Paid test budget: $0.

Evidence: live marina_agent.py SHA256
1b0ccbb65f6210eb0565d585bc82b5010531596d1586c47c7a5e4a70c5d81d1b
matches committed e12ad36 source; live image tracy-formal-address-91827a4 uses SDK 0.84.0.
This isolated branch follows the live source, not current origin/main. Deploy only
marina_agent.py as an overlay on that exact live image; retain all other live files.

Approach: wrap the existing redacted Mermaid system string in a text block with
an explicit five-minute cache breakpoint. The prefix includes its unchanged tool
schema. Guest history and latest messages remain outside the breakpoint, unchanged.
No changes to model, output allowance, instructions, routing, retries or customer
records. Tenant validation precedes caching. Daily date/config changes invalidate
the prefix naturally. No automatic breakpoint on the dynamic user payload.

Record cache creation/read tokens separately from uncached input; preserve existing
input_tokens meaning and add total_input_tokens for accurate cost comparisons.
Anthropic Sonnet 4.6 rates per million: uncached $3; 5m write $3.75; read $0.30;
output $15. Source: https://platform.claude.com/docs/en/build-with-claude/prompt-caching
Writes cost extra; sparse traffic can cost more. No guaranteed percentage saving.

Sep 5 Curaçao day runtime sample: 34 calls, 507745 input and 16541 output tokens;
estimated pre-cache $1.771350. Timestamp replay gives at most 29 potential hits
and 5 writes with 5m caching, assuming stable prefix and completed prior requests.
At an assumed cacheable fraction of 70–80% of input this implies roughly 44–50%
off Mermaid usage ($0.78–$0.89/day for this sample). Prefix token share and real
hits are unmeasured until normal traffic; this is a scenario, not invoice evidence
or a promise about the full multi-project account bill.

Validation: network-denied pytest with synthetic fixtures and real SDK MockTransport
checks exact prompt preservation, fixed model/tool/output settings, redaction,
one call per turn, tenant guard, guest separation, legacy requests, cache hit/write
accounting, absent usage fields and no retries on credentials failure. Existing
forced-tool/redaction tests receive the new system block shape. No live AI calls.

Success: focused checks pass; the pinned overlay runs healthy in Mermaid only;
peer containers unchanged; source digest verified. Actual cache hits/savings remain
pending ordinary authorized traffic. Rollback: restore previous compose image and
recreate only Mermaid's agent service; no config/database migrations are involved.
