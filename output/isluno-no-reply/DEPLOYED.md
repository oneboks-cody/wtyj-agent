# Isluno no-reply correction deployed — 9 September 2026

Calvin’s personal 09:45 message reached Isluno and completed a model call, then failed `invalid_discovery_facts`; the prior handler returned silence. The exact malformed field value was not retained. Source commit `c04512cfc84ccae329c5d8de881fe9a010882e59` keeps strict validation, clarifies the fact-key array contract, records content-free error diagnostics, and returns one processing-failure acknowledgement without a second model call, booking mutation or duplicate resend.

Calvin explicitly authorized “deploy, go live”; the organizer independently reviewed source, offline tests and the bounded operator continuation. Eight focused source checks passed with networking disabled, including actual webhook processing with mocked SDK/sender. Three operator-proof tests passed, including atomic seal/reopen, fingerprint rejection, and preserved replay guards.

Deployed image: `sha256:8d63e4d77862d9832892e788ceafc0f4d425aacf6ac594b7ee034aa1547068e3`.
Container: `5acae4259714c41aeca3e310e6466cca27cfda19f0724d6439675c02e5c624b7`.
All five packaged source hashes were verified. Health returned 200. Runtime admission is open generation 6 and ICP intake is open generation 5; provisioning worker/watchdog restored and temporary marker removed.

Current customer/business data across 91 tables matched the privately saved continuation fingerprint manifest. The original failed-turn and incident proof remained unchanged. No customer backup, reset, replay, provider-setting change or agent-sent test message occurred.

The initial replacement and image-only rollback stopped on an overspecific raw HostConfig comparison. Original raw ordering was not retained, so an ordering-only cause is not claimed. Current effective settings were verified against unchanged Compose/defaults; a reviewed continuation normalized unordered bind/capability lists, deployed the same verified candidate and completed readiness/reopening. Both consumed attempts remain recorded and were not automatically replayed. The continuation retained the original cumulative 720-second limit.

The operator-only seal classifies exactly the proven failed turn as retained operator review, not completed work. Normal coordinator checks and runtime replay guards remain unchanged. Browser login had expired; frontend/auth code was not changed. Calvin can now send a fresh WhatsApp message. A successful live customer reply/booking is not yet claimed.

Evidence: `DEPLOYED.json`, `VERIFIED-OPEN.json`, `files.json`, `TESTS.json`. The organizer owns the GitHub acceptance record.
