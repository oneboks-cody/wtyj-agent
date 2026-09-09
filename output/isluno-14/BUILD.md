# Current backend source reproduction

Accepted runtime source: `e2deab8c2ad6c4313324d5b94769b91a1f0ae168`. Current source manifest: `current-artifacts.json`; checks: `current-package-checks.json`.

From this verified isolated backend checkout, the local-only reproduction entry point is:

```sh
python3 output/isluno-14/package-current-backend.py
```

The artifact has already been produced and verified. Do not rerun merely for confirmation. The writer refuses to overwrite `tmp/isluno-release-artifacts/e2deab8c2ad6c4313324d5b94769b91a1f0ae168/isluno-backend-source.tar.gz`; a separately chosen scratch checkout/output is required for an independent reproduction. Never delete or overwrite accepted/historical evidence to make a rerun succeed.

The script packages only pinned Git runtime/build-input/assets/template paths, uses `tar.umask=0022`, gzip level9 and mtime0, and compares every member's content-derived Git blob identity and mode with `git ls-tree`. It requires exactly the previously accepted364 runtime files plus the new coordinator, includes all17 affected runtime files and rejects protected-data paths, symlinks, submodules or unexpected membership. Archive digest/member SHA-256 identities are fixed in the current manifest. It neither reads env files nor installs dependencies/builds/pulls/publishes an OCI image.

The first local packaging attempt stopped before writing any artifact because Git's default tar mode differed from the explicit source-mode check. Pinning `tar.umask=0022` made the recipe deterministic with respect to permissions; the corrected attempt passed all365 checks. No old archive was opened for writing. This was a local packaging check, not a runtime/test/provider failure.

## Carried config and frontend evidence

The original five default-off config files are byte-identical at the current runtime commit, verified by an empty Git diff for those exact paths. Their existing archive is carried unchanged with provenance in `current-artifacts.json`.

The66-file frontend build remains at accepted dashboard source `2c99c3a13e01af625432e5f681d4c065e95dd1a8`. Its prior3.17-second build used a cleared environment, Vite envDir=false and static base `/`; `frontend-build.config.ts`, `frontend-build.txt` and historical `artifacts.json` retain the recipe/output hashes. No frontend files or build artifacts were changed during this refresh. Actual public API/media/document routing remains separately unverified.

`package-local-artifacts.py` and `local-validation.json` describe the earlier4f62147 backend/config/static preparation. They are historical, not the current backend writer or validation result. The current package includes only source; immutable compatible dependencies and a final OCI image remain outstanding. See ACTION.md for exact release gates.
