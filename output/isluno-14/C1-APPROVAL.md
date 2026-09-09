# C1 protected-config inspection candidate — NOT AUTHORIZED TO EXECUTE

Prepared offline after packaging PASS at 57552cc. This tool is separate from the accepted runtime e2deab8 and its 365-member archive; those artifacts are unchanged. Both H1–H7 approvals are consumed. No C1 read, host connection, provider access or release operation has occurred.

## Exact artifacts

- Runner: `wtyj/scripts/project_isluno_config.py`, SHA-256 `79ba19aeb59255193e104b7a9b12a7c47ba8fcce2ed11ab368c04776de2d0296`.
- Reused transport/path helpers: `wtyj/scripts/inspect_isluno_target.py`, SHA-256 `9a19b292999a1c20641f93bbaad9c7e7adbd984c355ea6325234651ceda4b60f` (unchanged reviewed helper).
- Generated stdlib-only remote payload: SHA-256 `93e07609a784f2ef5ada797478c7d70a4c426f7d2eb8503c9d1ff3b32c7717f8`, 5420 bytes. It contains only C1 functions, not the H1–H7 collector or application imports.
- Synthetic fixture evidence: `c1-tests.txt`, 13 tests pass with sockets/DNS denied. Transport dispatch is mocked; no subprocess/SSH is launched by these tests. The unchanged transport retains its separately reviewed timeout, combined-output and orphan-process cleanup regressions.

## Canonical locking and precise limitation

Source inspected: `sync_mermaid_config_fields.py:_canonical_client_json_lock` and `shared/config_loader.py` canonical write transaction. Both use `<client.json>.lock` with exclusive `flock`; writers may create/chmod that file. Calling either writer helper would therefore violate a purely read-only inspection.

C1 instead opens the EXISTING exact sidecar read-only and acquires `LOCK_SH | LOCK_NB`, which excludes canonical exclusive writers. It does not create, chmod, write, rename or delete any remote file. A missing/busy/unsafe lock causes immediate failure; no substitute lock is invented. This relies on the actual deployed writers cooperating with this documented protocol; it does not prove every external writer cooperates. Reads can affect filesystem access-time metadata under host mount policy.

Every path component is walked with directory descriptors and no-follow flags. Both files must be regular and singly linked. Config is capped before reading, read once into memory (plus one-byte growth probe), parsed with duplicate-key rejection, and hashed from those same bytes. Descriptor and newly reopened full-path signatures are compared after projection for config and lock (device, inode, mode, owner, link count, size, mtime and ctime). This detects replacement/in-place changes during observation; it is not a future CAS or protection against privileged adversarial writers bypassing locks. No inode lock survives the observation.

## Fixed read, identity, time and output scope

One SSH connection to `root@108.61.192.52`; strict host-key checking, batch mode, one connection attempt, ten-second connect timeout and existing operator SSH identity configuration. The runner does not inspect credential files. Exact remote paths: `/root/clients/mermaid/config/client.json` (at most 1 MiB) and its existing `.lock` (metadata/flock only; no lock content read). Parent directories are traversed only to bind those paths. No Docker, Nginx, process, environment, DB, log, source inventory or provider read is included.

Remote deadline 15 seconds; full transport deadline 30 seconds including reviewed owned-process-group cleanup, with a combined stdout/stderr cap of 8 KiB before buffering. No retries. Failures return fixed C1 stopped status, no partial fields, raw parser errors or command output. A flag/reference cannot grant permission.

Identity must be exact matching `mermaid` top-level/business slugs, strict account mode, and exactly one nonempty account ID of 1–256 ASCII letters/digits/underscore/hyphen; unexpected account shapes stop, never normalize silently. Public fields: byte count, same-byte SHA-256, selected slugs/mode/count, SHA-256 of the exact UTF-8 account ID, observation UTC time, the four named feature boolean/missing/invalid states, native-carousel boolean/missing/invalid, media/document-base presence booleans/missing/invalid, and activation generation integer 1 through 2^63−1 or missing/invalid. No maps other than the four fixed feature keys, actual URLs, prompts or secret values cross the public boundary.

The selected exact account ID travels only inside the bounded SSH result and is saved privately alongside that projection to a newly created local `tmp/isluno-c1-calvin-c1-once/binding.json` (directory 0700, file 0600). It is never stdout, Git or an issue. Existing output directories are rejected before connection; never overwrite or reuse a previous run. Public fingerprint is only an equality aid and proves neither number ownership nor sole delivery.

## Reviewable one-run approval text

“Approve ONE C1 inspection with runner SHA-256 `79ba19aeb59255193e104b7a9b12a7c47ba8fcce2ed11ab368c04776de2d0296`, transport helper SHA-256 `9a19b292999a1c20641f93bbaad9c7e7adbd984c355ea6325234651ceda4b60f`, and remote payload SHA-256 `93e07609a784f2ef5ada797478c7d70a4c426f7d2eb8503c9d1ff3b32c7717f8` against root@108.61.192.52. Permit only the fixed client.json/lock read scope, identity checks, limits and private-account output described in this artifact. No retry, other host discovery, provider call, deployment, config mutation, DB access, service action or live message is approved. Budget $0 provider calls. Authorization reference: calvin-c1-once.”

After explicit approval and digest verification only, from this verified backend checkout:

```text
python3 wtyj/scripts/project_isluno_config.py --execute-authorized-host --authorization-reference calvin-c1-once
```

Running without flags prints offline_default and makes no connection. Missing/busy lock or any other failure consumes this one-run scope; do not retry automatically. Store run timestamps/outcome and publish only the selected projection after review. Do not fill required-input values before a successful authorized observation.

## Still separate: R1 / P1 / I1 / I2

R1 requires selected loaded public API/webhook routes, upstream/tenant binding, revision provenance and absence of a second selected consumer; C1 neither enumerates routes nor proves loaded worker configuration. P1 requires current provider-owned number/account connection, matching private account fingerprint, exact selected webhook destination without query secrets, subscription scope and consumer count from an authorized administrator. No provider access is included.

I1 still requires actual old-runtime admission/retention and worker-disposition evidence, external producer inventory, bootstrap coverage and a supported bounded drain condition; the accepted local coordinator does not establish that deployment evidence. I2 still requires the actual subscription/retry/retention configuration and deployed deduplication binding in addition to the already cited public contract. These facts remain unresolved and separately scoped. Approved media/document URLs, immutable OCI/dependency recipe, fresh protected-write CAS, verified consistent backup/retained-data rollback, release window and explicit release/live-canary authority also remain required. C1 is no release approval.
