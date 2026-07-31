# Changelog

All notable changes use this file. The project follows semantic versioning after its first public release.

## Unreleased

- Adopted the MIT License with Agent Zero ownership and contact metadata.
- Fixed wheel target promotion on macOS by keeping the staging root owner-writable until `os.replace`, then restoring the
  final read-only cache invariant after atomic promotion.

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

Published to PyPI as the initial Alpha release. At publication, owner follow-up included license selection, a private
security contact, and protected trusted-publisher environments for future releases.
