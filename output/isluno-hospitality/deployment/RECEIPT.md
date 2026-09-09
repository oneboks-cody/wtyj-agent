# Hospitality rewrite deployed and open

Deployed on 2026-09-09 under Calvin’s explicit go-live authority and Gatekeeper clearance for packet `8ae84786388fa987f0ae22c4e3cb8e06efd3dad9a148afd0df4709fa16cc08c3` (operator commit `eb77b65f223d36177a66cabf44fe6dc89418be83`).

- Application source: `3a5f5240f9d51e71fb47c47fbce8a16d1688f78e`.
- Image: `sha256:f80fefc3b9e49f2580f0e1b0c5b7affc643b129d80a54d32c8c4abd64538f7ac`.
- Container: `8bc4006c9fa3d5f83eef6d3c7f275e241bfcfc2ee7532b6e3d436a0d8123d21c`.
- One successful dispatch, 15.944 seconds of 900 allowed; one 296960-byte source transfer. No retry or rollback needed.
- All 12 archive members verified; all 10 application file hashes verified in both packaged and live images.
- All 91 protected customer/business table hashes matched before stop, after stop and after activation. No cleanup, database restore or replay.
- Fresh post-reopen retained-history fingerprint remains `aed5c33dfde9c432316d3dfb325626919ace9f88d285e71995a52075dae2fd34`: both retained turns and the accepted plan are unchanged.
- Runtime admission open at generation 8; ICP ingress open at generation 7. Health HTTP 200; provisioning worker and watchdog timer active; maintenance marker absent.
- Scoped Isluno capabilities and 31 catalogue products verified. Dashboard pointer/index unchanged; existing account/number, client settings, effective environment, provider/model and session-token bytes preserved.
- Only the two reviewed hospitality profile keys were merged. Current profile SHA256: `5c90d1979c7599e9bdd82a809a6cebe07db150692ed949b7a88e1070b16616b3`.
- Agent-originated customer messages: 0. Paid provider tests: 0.

The state flags in result.json record completed rollout steps (including temporary stops and sealing). They do not mean the service remains stopped: verification.json independently records the fresh open/restored state.

Authenticated dashboard visual checks remain pending because the checked Nr2 session redirected to login. Offline scripted conversation checks passed; live model/customer quality is not claimed. The existing demo-payment, supplier-availability and media/carousel limitations remain as documented in the application review. PR360 remains draft; this host deployment did not merge it or change the shared main branch.
