# One dashboard tenant check — OFFLINE candidate, not execution authority

Runner `wtyj/scripts/check_isluno_dashboard_tenant.py` SHA-256 `c1334a61950dbed21bbad55b606a3b44b6395028938cc8e20813d56b6263a937`. Synthetic verification:10 tests in dashboard-tenant-tests.txt; source identities and limits in dashboard-tenant-evidence.json. No live request, credential read, provider call or runtime change occurred. C1 remains consumed and untouched. This script is an inspection tool, excluded from the accepted runtime source archive.

## Exact request and output

ONE HTTPS GET https://api.unboks.org/api/mermaid/dashboard/api/client/profile with no body/query, existing explicitly supplied bearer credential, application/json Accept and Connection:close. Default transport OFF. Standard verified TLS/hostname validation; direct http.client connection (no environment proxy discovery, redirects, cookie jar, login, auth refresh or HTTP retry). The stdlib/platform trust store and DNS/TLS machinery are used normally; this does not discover application credentials. No ICP page/status endpoint, webhook POST or provider route is contacted.

A ten-second real-time signal deadline covers protected input, DNS/TLS/connect, request, response and parsing; per-connection timeout is also bounded by remaining time. Response cap8KiB counts status/header/framing/body plaintext through an unbuffered socket file wrapper, not only JSON. Requests with unsupported/ambiguous framing stop; compressed bodies are rejected. No body is decompressed or persisted. TLS/OS internal buffering is distinct from the bounded application HTTP reader. Unknown status/redirect401/invalid tenant or schema/oversize/timeout produces only a fixed stopped result. No retry after failure. Socket/response close occurs on all post-construction exit paths.

Success prints only status=observed, http_status=200, tenant_matches=true and UTC observed_at. Display names, arbitrary fields, raw response/error text and bearer credential are never output or written. This proves only that this selected dashboard request reached a runtime identifying Mermaid at that instant. It does not prove complete Nginx routing, inbound ownership/sole consumer, source/image identity, config-file hash, retained-work disposition or deployment readiness.

## Protected credential input

Only descriptor0 backed by an explicitly supplied pipe/socket adapter is accepted. Regular-file redirection, terminal input and directories are rejected. Input must be the existing bare bearer token, ASCII b64token syntax,1–4096bytes, NO trailing newline. The producer must close the pipe after writing. No credential argument, environment variable, config file, browser-storage extraction or login is supported. Do not type the token into a command, chat, approval message or issue, or use shell echo/printf with a token literal.

The authorized operator supplies their existing credential through a trusted protected-input adapter that writes only to stdin and does not log/echo it or refresh authentication. The runner does not implement or assume an available adapter. If that protected credential supply is unavailable, stop before execution; a401 is not permission to discover another credential. Any adapter needs review for this exact read-only use before connecting it. No credential is needed for offline tests.

## Reviewable one-run approval text — do not request execution until review and protected supply are ready

“Approve ONE dashboard tenant check using runner SHA-256 `c1334a61950dbed21bbad55b606a3b44b6395028938cc8e20813d56b6263a937`, reference calvin-dashboard-tenant-once, to the exact HTTPS GET URL above. Permit an existing bearer credential explicitly supplied by the operator through reviewed protected standard input only. Ten seconds total,8KiB response cap, no redirect/retry/login/refresh/credential discovery. Output selected status/time/tenant equality only. No provider messaging/API, config/DB/host discovery, runtime change or deployment is approved; external provider calls$0.”

The post-approval entry point is:

```text
python3 wtyj/scripts/check_isluno_dashboard_tenant.py --execute-authorized-request --authorization-reference calvin-dashboard-tenant-once
```

This invocation is incomplete without the reviewed protected stdin adapter; it must not be run interactively to prompt for a token. Without execution flags it prints offline_default and reads no credential or network. The reference is audit metadata, not authority or a cross-process run-count guard. The operator/orchestrator must record start/end, run commit, runner digest, exit/status and the sanitized result once; successful or failed dispatch consumes the approved one-run scope. Do not launch again under the same permission.

## Verification and retained gates

Synthetic HTTP socket/connection and credential adapters cover fixed request/TLS verification, wrong tenant/schema/duplicate JSON, redirect statuses/no retry,401 and transport errors, body+header combined cap, chunk framing/short reads/ambiguous lengths, signal installation/restoration and timeout failure, offline/preflight no dispatch, pipe-only credentials and header-injection rejection, and response/credential/error redaction. Network/DNS are denied by the offline harness. Initial fixture run exposed a missing read-wrapper flush method required by Python3.14 HTTPResponse.close; corrected, then final10 tests pass. This is local runner evidence, not container/runtime verification.

Current ICP subscription/store/deployment binding, inbound route, watchdog/bootstrap/old-work disposition, image provenance, media/document URLs, backup/CAS/window and release/canary authority remain separate. No expanded host or provider collector is included.
