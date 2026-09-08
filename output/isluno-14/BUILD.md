# Local artifact reproduction

These commands only build/package local files. They do not authorize release. Use the exact accepted source checkouts from ACTION.md. Review dirty state first; do not reset or overwrite another task's work.

Frontend static build recipe used in this preparation:

```sh
cd /Users/calvin/Projects/isluno-dashboard-demo-20260908/artifacts/unboks
mkdir -p tmp
cp /Users/calvin/Projects/isluno-whatsapp-demo-20260908/output/isluno-14/frontend-build.config.ts tmp/isluno-release-build.config.ts
env -i PATH=/opt/homebrew/bin:/usr/bin:/bin /opt/homebrew/bin/node node_modules/vite/bin/vite.js build --config tmp/isluno-release-build.config.ts
```

The wrapper imports the accepted Vite config, disables env-file loading and fixes static base `/`. No dependency installation or network service starts. The production `dist/public` output is the frontend artifact. The target's actual static mount and API route still need confirmation.

After verifying that build:

```sh
cd /Users/calvin/Projects/isluno-whatsapp-demo-20260908
python3 output/isluno-14/package-local-artifacts.py
```

The packager uses `git archive` at fixed accepted backend source and an explicit runtime/config allowlist. It excludes tests, live config and databases. It checks the frontend accepted HEAD and unchanged tracked source, then packages the static build in sorted order with fixed tar/gzip timestamps. It never builds or claims a backend OCI image. Generated archives live in ignored tmp storage, and their exact names/digests/member lists are in artifacts.json. Repository Git source is the reproducible archive source; no 60 MB binary duplicate is added to Git.

`local-validation.json` records archive hash/member and frontend entrypoint checks. `frontend-build.txt` records the fresh 3.17-second build. Previous acceptance tests and rendering remain the independently accepted ISL-13 evidence; no repeated matrix was needed for this preparation.
