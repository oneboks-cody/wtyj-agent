# Immutable image recipe gap review — offline only

Candidate note after C1 preparation PASS at 0d50899. No build, pull, publish, package installation, network query or host inspection is authorized or performed here. C1 runner/helper/payload identities remain unchanged; its approval is pending. This note grants no build or release authority.

## Bound source evidence

Accepted runtime source is e2deab8c2ad6c4313324d5b94769b91a1f0ae168. Its existing 365-member source archive SHA-256 is d9841ef3070fed9cde8840a353ad7a9917400f7facfbd13d3ab50472c9b68c51; current-artifacts.json binds every member. That archive is unchanged and is not an OCI image. Target platform observed by the separately approved H1–H7 run is linux/amd64.

The following Git sources were compared with that accepted commit and are unchanged. Only source and existing manifests were read; no archive, image or dependency installation was repeated.

| Input | SHA-256 | What the source establishes |
| --- | --- | --- |
| Dockerfile | 378f3e89c0565c2061238eaa46e5a4d1502b7e7187e124b849e90be751809948 | Python 3.12-slim tag; live apt resolution; curl/ca-certificates; versioned gws download; pip install; app and supervisor copy |
| requirements.txt | 13f873c191dba61fcc6f42954d3ad898ec39f785a583f6a9a0210e8fa89abf71 | Explicit listed package versions, including provider SDKs and PDF dependencies; no artifact hashes or complete transitive resolution record |
| supervisord.conf | c9f8641eb6d9198b94157426e2e4e13a687f43a8b0869ebc72e737a215e2e686 | Webhook server plus email-poller, hold-reaper and Ali quote-recovery autostart; port 8001; mounted runtime log paths |
| .dockerignore | d9792aaf4b27388741f874a5c4ce1faa2ebb103191a31882826260f0cd43d8d1 | Excludes config/data/logs/client trees and also wtyj/templates; not included in the accepted source archive |
| Dockerfile.mermaid-pdf | a0f03191e3d169422d13bae3e2e6d22954604a880b964ca2b0f49080fff19fd2 | Historical partial overlay on mutable Mermaid image tag; neither full Isluno recipe nor approved immutable base |
| wtyj/tests/isluno/requirements.txt | 9c3e8584733e7e4585f3b39883dc03f829d686637237439a1b78881be4bff536 | Import/verification tools only; not a complete production lock |

Observed running image identity sha256:ed146176bbf4e116270b55a04771af5fb3cd5d0f62d124d8650aa7e1bac97552 came from Docker container image metadata. It does not by itself establish a pullable repository@manifest-digest reference, base lineage, installed dependency inventory, Python patch version or complete filesystem provenance. Keep approved_candidate_base_image_digest and final OCI identity unresolved. The previous 112 selected source hashes cannot fill those gaps.

## Minimal proposed recipe: retain an approved dependency image

Prefer this route only if an authorized artifact owner can provide a compatible, immutable existing dependency image and its provenance. This reduces dependency churn; it is not permission to use the observed ID as FROM.

1. Bind a retrievable OCI repository reference and platform-specific manifest digest (and index mapping, if applicable), linux/amd64, exact Python version/ABI, dependency/OS inventory, entrypoint/user/workdir/environment metadata and evidence that no protected tenant config, credentials or customer data are baked into layers. Supply the reviewed artifact or provenance without dumping live environment/config. Inventory must include installed provider SDKs, PDF/font/image support and the gws binary version/hash. Differences from accepted requirements need explicit review, not silent upgrades or assumptions.
2. Prepare a separately reviewed recipe using only that immutable base and the existing accepted archive as the application input. No apt, pip, curl or provider operation in this overlay build. Map archive wtyj/agents, shared, dashboard, assets and templates to the corresponding /app directories; map supervisord.conf to /etc/supervisord.conf. Record Dockerfile and requirements as build provenance; they are not substitutes for dependency attestation.
3. Review inherited application files before deciding replacement semantics. Blind COPY can leave obsolete base files; blindly deleting /app can remove dependencies/tools or mounted-path assumptions. Establish exactly which application directories are replaced, which immutable dependency paths remain and which stale application files must be absent. No operational deletion is proposed by this note.
4. Construct a fresh allowlisted staging context from the reviewed archive manifest, never this full working checkout. The archive already contains one templates member and no scripts. Do not accidentally apply the repository .dockerignore rule that drops templates; define and hash the staging context and its dedicated exclusions explicitly. C1 inspection scripts and private outputs have no runtime image role.
5. Preserve the reviewed launch contract explicitly rather than inheriting unknown base defaults. The candidate supervisor file has four autostart programs; their inclusion does not prove safe old-runtime drain or bootstrap coverage. Reconcile these with I1 before deployment. Tenant flags, credentials and model/provider choices remain runtime inputs; never bake them into build arguments, layers or labels.
6. Pin builder/toolchain identity, recipe/context hashes, source archive identity and platform in build provenance. Record the resulting platform manifest/config IDs separately, with any index identity and immutable artifact destination. A recipe alone does not promise byte-identical image output; timestamps and builder metadata also need controlled reproduction settings if byte reproducibility is required.

No executable Dockerfile is supplied: the immutable base reference, its dependency/filesystem evidence and approved replacement semantics are missing. Inserting placeholders into a runnable build command would not resolve them.

## Alternative only if a compatible retained base cannot be established

A clean build from the root Dockerfile needs additional reviewed inputs before execution:

- Resolve python:3.12-slim to an approved linux/amd64 platform manifest and record Python patch/ABI and OS release. No tag or digest is invented here.
- Bind an immutable OS package source/snapshot, package versions and artifact integrity for curl, ca-certificates and dependencies; apt-get update against mutable repositories is not a pinned recipe.
- Verify the gws v0.8.0 x86_64 archive checksum and provenance before extraction. The existing curl-to-tar command has no explicit checksum verification. Record binary and any required native-library compatibility; a versioned URL alone does not establish content identity.
- Produce a complete Python 3.12/linux-amd64 dependency closure retaining the reviewed direct versions, with wheel hashes and approved artifact source. Use an immutable wheelhouse with no-index/hash-required installation where possible. If an sdist is unavoidable, pin its build requirements/toolchain and resulting wheel as well. The existing requirements file is not such a lock. Do not reuse the local Python 3.14 environment as dependency evidence.
- Review any unavoidable compatibility change separately; do not upgrade/downgrade packages, change providers/models or add a fallback to make resolution succeed silently. This route changes more of the runtime substrate and needs corresponding review.

## Exact outstanding inputs and warranted later checks

Missing inputs: approved immutable base locator/platform digest and provenance; exact Python/OS/dependency/binary inventory; image layer safety evidence; application replacement map; controlled staging context/recipe; builder identity and artifact destination; separately scoped build/access authority. The clean-build alternative additionally needs immutable OS and complete Python artifact pins plus gws checksum. No values above have been populated into required-inputs.json.

After a separately authorized build, verify only what that new image makes uncertain:

- Check OCI platform, manifest/config mapping, provenance, entrypoint/user/workdir and exact installed versions; compare final application files/modes with the accepted source manifest and approved absence list. Check layers as well as merged filesystem for forbidden protected material. Do not dump runtime secrets or use production mounts.
- Run dependency consistency and import checks in the actual Python 3.12 linux/amd64 image with an overridden non-serving entrypoint, synthetic config and no network. Then run the relevant existing offline booking/payment/document, strict tenant-account and maintenance/drain regressions in that image using a separately hashed read-only test fixture mount. Tests are absent from the runtime archive by design. Confirm PDF generation and required assets/templates. Local Python 3.14 passes remain local evidence, not container proof.
- Exercise lifecycle/background-worker startup only within an isolated synthetic environment with outbound traffic denied and no real credential/config/data/log mounts. Review supervisor child behavior explicitly; starting the normal image against production inputs is not an offline test. Image compatibility checks do not establish provider acceptance, live routing or a safe cutover.

Do not rerun the unchanged frontend build, source archive packaging or prior acceptance matrix merely to increase counts. R1/P1/I1/I2, approved media/document URLs, protected config CAS, consistent backup/retained-data rollback, release window and explicit deployment/canary authority remain independent gates. Counts remain 13/14 accepted, 0/14 merged/verified, 3 deferred.
