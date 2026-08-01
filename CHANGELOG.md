# Changelog

All notable changes use this file. The project follows semantic versioning after its first public release.

## Unreleased

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
- Fixed wheel identity inspection and runtime compatibility for packages such as setuptools that include nested vendored
  metadata, bundled dependencies, logical module-prefix checks, and several public import roots.
- Reworked the public README around ordinary imports, runtime installation, multiversion packages, production preparation,
  and a GitHub/PyPI-compatible project banner.
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
