# First-install boundary — current accepted design

The Mermaid coordinator cannot protect its own installation. The source-reviewed ICP gate and its packaged CLI provide the admission boundary; see ICP source `70ea4a7e48758b8e3d99e84d1e6543657f5e80c0` and `icp-image.json`.

The gate admits only Mermaid `message.received` forwards when open, holds its shared forwarding lease through the upstream response, and rejects closed admission with 503/Retry-After. Close writes `closing` before draining existing forward leases and has a 15-second bounded wait. Timeout/crash does not reopen it. Other tenant/non-customer paths retain their existing behavior; unknown ownership branches are not claimed as retained delivery. The gate cannot prove upstream retry duration or no external integration exists.

For first installation, the old ICP process has no gate leases. Retire it, install the closed store in the verified shared mount, and establish the selected Mermaid app/writer/watchdog stop before creating the private snapshot. The accepted direct-seal helper imports the coordinator with an explicit database path, not the application runtime or state_registry. Its aggregate stops on unfinished, ambiguous or unknown old work. Waiting drafts are preserved and quarantined by the final transition.

The current path is ACTION.md and RELEASE-SEQUENCE.md. No compatibility application startup is required or authorized by this document. Source review, offline tests and image verification are complete evidence for their exact inputs; actual host bindings, retirement, quiescence, backup and activation remain release-time facts.
