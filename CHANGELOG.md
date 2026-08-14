# Changelog

All notable changes use this file. The project follows semantic versioning after its first public release.

## 0.10.2 - 2026-08-14

- Added `depfix.default_requirements(path)` for transactional grouped activation of requirements files, including nested
  requirements and constraints, environment markers, file-relative sources, scoped indexes, explicit contextual parser
  errors, and warm/offline reuse through the same standard-import runtime as `default()`.

- Added explicit, reversible `depfix.patch_import()` installed-store fallback for ordinary imports, with normal importer
  precedence, exact module metadata, newest-compatible deterministic selection, ambiguity diagnostics, no implicit
  network resolution, unchanged exact-manifest/`default()`/`using()` priority, and automatic activation for `depfix run`
  applications.

- Added `depfix uninstall` and the matching Python API for normalized names, exact versions, and PEP 440 ranges, with
  dry-run and JSON reporting, non-cascading removal, and lock-safe protection for preparation and active runtimes.

- Preserved absolute Windows drive paths in named `file:` requirements and gave routine cross-process usage writes a
  bounded contention window while retaining the short fail-closed bound for first runtime activation.

## 0.10.1 - 2026-08-14 (superseded before PyPI publication)

- The hosted matrix showed that one shared SQLite timeout could not both merge routine concurrent usage writes reliably
  and preserve bounded first-activation failure. Validation failed before distribution staging, OIDC, or PyPI
  publication; the separate timeout paths are released as 0.10.2 without moving the immutable tag.

## 0.10.0 - 2026-08-14 (superseded before PyPI publication)

- The hosted Windows matrix exposed direct `file:C:/...` requirements-path parsing and concurrent SQLite usage-write
  failures. Validation failed before draft staging, OIDC, or PyPI publication; the immutable tag is superseded by 0.10.1.

## 0.9.5 - 2026-08-13

- Made `depfix list` and `depfix tree` the primary installed-package inventory and provenance-tree commands, added
  metadata-rich cached live-resolution inspection under `depfix cache resolutions`, and retained explicit manifest
  inspection through `--manifest` with migration guidance for positional compatibility forms.
- Retained `depfix cache list` as a deprecated migration alias and added active-runtime state to structured and human
  installed-package inventory.

## 0.9.4 - 2026-08-13

- Fixed grouped custom-index resolution when an otherwise compatible dependency has no advertised artifact SHA-256:
  live resolution now downloads that selected artifact once, binds its observed SHA-256 and size into the exact graph,
  and retains strict hash verification for materialization, prepared/offline reuse, and later downloads.
- Kept malformed and conflicting advertised hashes as hard integrity failures, with regression coverage for the public
  PyTorch CPU `torch`, `torchvision`, and `setuptools>=77.0.3` dependency edge and ephemeral archive cleanup.

## 0.9.3 - 2026-08-13

- Corrected the Simple HTML custom-index regression fixture to decode local wheel URLs with platform-native file URL
  rules on Windows; runtime behavior is unchanged from the fully gated 0.9.2 candidate.

## 0.9.2 - 2026-08-13

- Made incomplete-target recovery restore write permissions across the complete hardened tree before removal, including
  Python 3.13 non-root runners.

## 0.9.1 - 2026-08-13

- Restored owner write permission before deleting hardened ephemeral artifacts, preserving self-cleaning behavior on
  Windows while keeping completed package targets read-only.

## 0.9.0 - 2026-08-13

- Added standards-compatible Simple HTML custom-index discovery alongside PEP 691 JSON, preserving redirected-link
  resolution, SHA-256, `Requires-Python`, yanked, size, transport-policy, and grouped index-isolation checks.
- Made downloaded wheels, source archives, locally built wheels, and Depfix-owned uv caches ephemeral after verified
  materialization, with lock-safe cleanup of dead-owner crash leftovers while preserving complete unpacked targets and
  never touching user-owned uv caches.
- Hardened installed-package completeness checks and exact online/bundle repair so missing payloads are detected and
  source-derived artifacts are reproducibly rebuilt and hash-verified when their temporary wheels no longer exist.

## 0.8.1 - 2026-08-13

- Replaced the default `depfix pip install` JSON block with one compact line reporting distinct requested packages,
  transitive dependencies, the complete shared-store inventory, and its path; exact warm installs report reuse, while
  `--json` retains the complete structured result.

## 0.8.0 - 2026-08-13

- Hardened production publication against PyPI CDN propagation by retrying the clean simple-index install after the exact
  JSON artifact set appears, and preserving the checked GitHub draft when post-upload verification needs to be retried.
- Added a fail-closed release launcher that verifies the clean local/remote main commit, annotated tag, version,
  changelog, and unused publication destinations before dispatching the production workflow from the tag; CI now
  validates workflow syntax and security contracts, and a no-OIDC recovery workflow can publish a retained draft only
  after its assets match the verified PyPI files byte for byte.
- Added request-scoped primary and extra package-index selection to every synchronous, asynchronous, and standard-import
  loading API. Scoped primary indexes suppress inherited extras, remain isolated across concurrent calls, participate in
  graph/cache identity, and are rejected when an exact prepared manifest makes live resolution inapplicable.

## 0.7.0 - 2026-08-06

- Added package-store inspection through `depfix cache list` and `depfix.inspect_cache()`, with package, duplicate, and
  top-down installation-tree views that expose artifact sizes, installation and last-use timestamps, dependency
  relationships, and repeated shared nodes without double-counting them.
- Recorded durable installation provenance for grouped CLI installs and runtime loading APIs, including canonical
  commands, requirement manifests, and application source paths and line numbers, while keeping legacy cache entries
  inspectable when no historical reason is available.
- Extended cache cleanup to prune provenance records whose artifacts have been removed, preserving an accurate shared
  store inventory over time.

## 0.6.0 - 2026-08-04

- Added opt-in `realm_of()`, `assert_same_realm()`, and `enforce_same_realm()` APIs with immutable `RealmInfo` provenance,
  nested builtin-container checks, named sync/async function boundaries, optional return enforcement, and structured
  `RealmBoundaryError` producer, consumer, realm, value-path, and remediation diagnostics.
- Added live object-interoperability probes for packaging, attrs, PyJWT, and urllib3, documenting silent, immediate,
  directional, nominal, and delayed cross-version failures alongside application-owned primitive adapter patterns and
  explicit detection limits.

## 0.5.0 - 2026-08-03

- Reworked `depfix pip install` from an environment-mutating uv passthrough into grouped Depfix installation: package
  arguments and requirement/constraint files now populate the shared store, preserve incompatible transitive versions,
  reuse compatible cached artifacts, and never modify `site-packages` or `sys.path`.
- Added compatible cache-first dependency selection across separate and grouped requests, with per-loading-call,
  process, environment, project, export, and CLI `prefer_newest` overrides for explicit newest-first resolution.
- Hardened production release automation so a manual annotated-tag dispatch must pass version, changelog, current-main,
  PyPI-absence, complete cross-platform CI, and distribution checks before staging a hidden release draft or enabling
  OIDC publication of the exact checked artifacts; the GitHub Release becomes public only after PyPI verification.
- Fixed cache cleanup on Windows by restoring owner write permission before deleting read-only artifact and metadata
  files, matching the existing recursive materialization cleanup behavior.

## 0.4.1 - 2026-08-02

- Fixed cleanup of read-only cache targets for non-root accounts by restoring owner write permissions across the target
  tree before recursive removal.

## 0.4.0 - 2026-08-02

- Added shared-cache lifecycle management with installation and last-use timestamps, total package footprint inventory,
  cross-process active-runtime leases, returning-graph protection, configurable 30-day background retention, and matched
  Python/CLI list, cleanup, dry-run, and exact-removal operations.

## 0.3.0 - 2026-08-02

- Added request-scoped `auto`, `inprocess`, and `shared` import modes. Pure graphs retain synthetic multiversion realms;
  native graphs use guarded logical imports, compatible requests are idempotent, and incompatible public-root replacement
  raises `SharedImportConflictError` without misclassifying deliberate compatibility aliases below an owned root.
- Let `using()` expose a first compatible shared/native selection as scoped syntax sugar. Native modules remain loaded and
  keep process ownership after scope exit, so later incompatible versions fail explicitly instead of being swapped.
- Added deny-by-default unsafe package handling with per-request `allow_unsafe` options on every loading API, centralized
  process/project/environment configuration, manifest persistence, typed remediation, and deliberate in-process extension
  loading for trusted callers.
- Raised the verified artifact limit to 1 GiB, added bounded resumable downloads without weakening exact size/SHA-256
  promotion, and accepted PEP 440-equivalent registry/wheel version spellings.
- Added an offline compatibility probe covering Pydantic, orjson, NumPy, Pillow, psutil, cryptography, and optional Torch.

## 0.2.1 - 2026-08-01

- Fixed a transient Windows cache-lock race where concurrent artifact writers could receive `PermissionError` while the
  winning process removed its lock directory.

## 0.2.0 - 2026-08-01

- Added version-aware standard imports through persistent `default()` selections and context-local `using()` scopes and
  function decorators, with narrow import dispatch, grouped scanning/export, frozen installation, offline bundles,
  typed diagnostics, and generated scoped/default IDE artifacts.
- Added default stderr progress for live resolution and installation, including uv package summaries, artifact downloads,
  preparation, and a quiet `WARNING`-level mode.
- Added an AWS CLI/Boto3 example that runs mutually exclusive Botocore versions together, plus runtime support for
  `ModuleNotFoundError` probes, dynamic compatibility submodules, and internal synthetic imports used by those packages.
- Added a live regression that imports OpenAI 0.7.0 and 0.28.1 side by side, including fixes for optional `find_spec()`
  probes and source-built wheel hash provenance exposed by those published artifacts.
- Adopted the MIT License with Agent Zero ownership and contact metadata.
- Fixed wheel target promotion on macOS by keeping the staging root owner-writable until `os.replace`, then restoring the
  final read-only cache invariant after atomic promotion.
- Fixed Windows local module and wheel sources by preserving drive letters in `file:///C:/...` URLs, accepting the
  platform `Scripts` location for `uv.exe`, and keeping resolver imports lazy until a Depfix API is used.
- Fixed wheel identity inspection and runtime compatibility for packages such as setuptools that include nested vendored
  metadata, bundled dependencies, logical module-prefix checks, and several public import roots.
- Reworked the public README around ordinary imports, runtime installation, multiversion packages, production preparation,
  and a GitHub/PyPI-compatible project banner.
- Added OIDC Trusted Publishing from version-matched GitHub Releases, with package builds isolated from the protected PyPI
  upload job and no stored PyPI token.
- Removed ignored install CLI options, made custom install targets require explicit local materialization, modernized SPDX
  license metadata, removed the obsolete pre-registration name check, and expanded release checks for repository hygiene,
  credential patterns, and uv-managed interpreters.

## 0.1.0 - 2026-07-31

- Established the permanent `depfix` distribution, import, CLI, environment, cache, bundle, generated-package, and
  synthetic-namespace identities.
- Added zero-configuration ranged imports and lazy `PackageHandle` loading.
- Added unified PyPI, Git, URL, file, Python-module, and PEP 508 direct-reference sources.
- Added Core Metadata/artifact module discovery and four-state native classification with mixed-wheel Python fallback.
- Added mandatory uv resolution/build backend, executable discovery, and private repair bootstrap.
- Added deterministic manifests, AST export, frozen/offline install, air-gap bundles, IDE stubs/source maps, and uv pip
  passthrough.
- Preserved parent-specific multiversion realms, namespace provider sets, canonical identity, resource/metadata facades,
  concurrency, and spawn-worker behavior.
- Added release validation, cross-platform CI, and manual trusted-publishing workflow templates.
- Added a project-wide DOX hierarchy, concept-owned documentation folders, a GitHub/PyPI README, and canonical
  `agent0ai` project metadata.

Published to PyPI as the initial Alpha release. License selection, a private security contact, and protected
trusted-publisher environments remained owner follow-up at publication.
