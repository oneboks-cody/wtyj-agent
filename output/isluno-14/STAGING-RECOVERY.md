# Proposed bounded archive recovery — calvin-isluno-staging-c

Status: local candidate only, NOT dispatched. Existing conditional service-deployment approval and successful preflight B remain valid; neither is being requested again. The new allowance is limited to recovering archive staging. Authentication still must be verified before any later service disruption.

## Diagnosis

The first staging phase created the private destination, then stopped during the first `scp` upload. Its retained exception was only `Rejected`: the original wrapper discarded the fixed failure reason and exit code. The exact argv uses the expected local archive, fixed root SSH destination, batch mode, strict host-key checking, one connection attempt and a 180-second transfer cap; no deterministic quoting or path error was found. The local dispatch/result timestamps span 182.621499 seconds. This is consistent with the cap, but does not prove why the process stopped.

One separately recorded read-only verification found a regular 0600 partial archive at `/root/backups/isluno-manual-e2e-20260909-a/archives/mermaid-image.tar`: **79,641,600 bytes**, SHA256 `859cbdccb54fea793d1a4b2ba45e747081bfaf90c5f3b34ce1a341ec7092a27a`. An offline hash proves it exactly matches that prefix of the reviewed 169,445,888-byte archive; 89,804,288 bytes are missing. The partial file is retained untouched. No image load, extraction, service restart, config/DB change, pointer switch, gate change or provider call has occurred.

## Exact proposed action

Run `wtyj/scripts/recover_isluno_archive_staging.py --execute-approved-recovery --approval-reference calvin-isluno-staging-c` once from the verified isolated checkout. Default invocation performs no host reads. Immutable runner/test hashes are in `staging-recovery.json`.

1. Reverify the five local original artifacts against their exact size/SHA manifests. Create a new local one-shot receipt guard; refuse reuse without rewriting prior receipts.
2. Through the fixed SSH wrapper, make a maximum 30-second selected read-only check of only the original first archive and its parent shapes. Bound streaming SHA by the expected full size and verify before/after file identity. An unsafe shape stops. Reuse the original only if its complete size and SHA match; otherwise leave it untouched.
3. Verify the existing private archive root; create **only** a new 0700 `/root/backups/isluno-manual-e2e-20260909-a/archives/recovery-c` directory. If that directory already exists, stop. Do not repeat the old root-absent preflight, overwrite any existing archive, delete partial data or append to it.
4. Upload each needed reviewed original artifact once, serially, into the new directory. Maximum **five uploads**, concurrency **one**, maximum **233,957,640 original artifact bytes** (64,511,752 if the complete first original is reused). Protocol overhead is not included in those artifact sizes. Transfer caps are **900 seconds** for Mermaid, **600 seconds** for ICP, and **60 seconds each** for config assets, frontend and helpers. All commands share one **2100-second overall cap**, with no retry loop. The first archive is sent afresh to avoid modifying the retained partial.
5. Verify all selected staged archives against the exact original size/SHA manifest in one bounded read-only step. Save their explicit verified paths in a private receipt. Do not load an image, extract files, switch services or publish the dashboard in this recovery runner.

Each command records start time, finite limit, elapsed time, exit status, stdout/stderr byte counts and fixed failure codes. Each generated SSH payload is also saved before dispatch in an exclusive 0600 file, with its exact SHA256 and byte count in the receipt. Raw stderr and secrets are never printed or persisted. A failed upload may leave a new partial in recovery-c; stop, preserve it and do not reuse this attempt automatically. Success supplies the verified-path manifest needed for the already-approved later staging sequence; it is not live readiness.

## Verification and unchanged scope

Seven focused offline checks cover default no-host behavior, exact complete-file reuse, measured exit/timeout/output errors, consumed-guard preservation and a no-follow immutable-file projector; they passed in 0.293 seconds. The reference-specific guard check passed after naming the final reference. No live transfer test or unchanged runtime/image suite was run.

Runtime, frontend, both image archives, config assets, the six-helper archive, approved ACTION and the passed preflight payload are unchanged. The first transfer receipt, original partial proof and all consumed inspection guards are preserved. Only this bounded staging recovery allowance is proposed; there is no blanket deployment, provider or authentication request in it.

The exact prior read-only verification payload was reconstructed against its recorded SHA256 `1bbf3054f181c3435cfe253feed57c3ec890e81c764f0a144a9af2a507153a5c`: the only later source change was the addition of two remote-alarm lines. The original receipt was not changed and no host call was made. See `first-archive-exact-dispatched-payload.py.txt` and `first-archive-payload-reconstruction.json`. The new payload-preservation check passed in 0.001 seconds (eight unique current recovery checks).

Receipt ownership correction: a consumed guard is checked before local artifact reads; failure evidence is written only when this invocation created the new receipt directory. Existing-guard checks, including an invalid local artifact, passed in 0.004 seconds (nine unique current recovery checks). Prior receipts remain untouched even when a new invocation fails before dispatch.
