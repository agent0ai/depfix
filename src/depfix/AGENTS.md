# Depfix package

## Purpose

- Resolve Python dependency requests and load pure packages into isolated realms or native graphs through a guarded
  process-shared import mode.

## Ownership

- Public Python and CLI entry points, source parsing, resolution, locking, caching, materialization, runtime import isolation, project preparation, and typed errors live in this package.

## Local Contracts

- Importing `depfix` performs no network, resolver, cache, or subprocess work.
- Keep resolver and scope implementation imports lazy at the public package boundary so the import-only contract also
  holds on Windows.
- `import_module` returns exactly one selected module; `load_package` returns a lazy package handle.
- `default()` owns persistent ordinary-import selections; context-local `using()` scopes and decorators own temporary ones.
- Install the narrow standard-import dispatcher only on the first `default()` or `using()` call, preserve caller realms
  before application scopes/defaults, and delegate unmanaged imports to the prior importer.
- Resolved artifacts are hash-pinned and materialized outside ambient `site-packages`.
- Pinned downloads retry and resume bounded transient truncation, but exact size and SHA-256 verification remain mandatory
  before atomic target promotion. Downloads and build inputs are ephemeral, remain protected through materialization,
  and are removed afterward; completed unpacked targets are the cross-project cache. Completion metadata verifies the
  installed payload rather than trusting a marker alone, while legacy targets retain a conservative file-count check.
- Cross-process cache locks retry transient Windows permission races without masking permanent permission failures on
  other platforms.
- Cache lifecycle metadata preserves first installation time and coalesces successful-import usage writes. Automatic
  retention uses a daily background sweep, protects the graph before synchronization, and skips cross-process leases held
  by active runtimes; explicit inventory, cleanup, dry-run, and removal APIs use the same artifact/target lock boundary and
  repair owner write permissions before deleting read-only files or materialization trees.
- Install and cache-maintenance passes reconcile obsolete retained blobs, abandoned process-owned download parts, stale
  extraction targets, and stale built-wheel staging without deleting active work. Dry-run reconciliation is observational;
  offline reuse requires a complete target or an exact bundle-provided artifact.
- Bundle creation rebuilds source-derived ephemeral wheels from recorded provenance when necessary and accepts them only
  when their size and SHA-256 exactly match the locked artifact.
- Successful graph synchronization records deduplicated, secret-redacted installation origins and enough root/node data
  to inspect flat packages, distribution-level duplicate footprint, and top-down dependency trees without the original
  project. Exact artifact hashes remain single physical entries; same-version variants require distinct hashes.
- Decode local `file:` URLs with platform-native rules, including Windows drive-letter and UNC path forms.
- Realm imports preserve module identity and prevent undeclared cross-realm leakage.
- `boundaries.py` owns opt-in provenance inspection, exact graph/node assertions, and sync/async boundary decorators.
  Guards inspect direct managed types and nested builtin containers, report typed producer/consumer diagnostics, accept
  unmanaged values, and never claim automatic conversion or arbitrary object-graph coverage.
- Package compatibility fallbacks may use modules embedded in the same selected artifact; declared dependency providers
  always take precedence.
- Cold package preparation reports secret-safe progress on stderr by default; warning and higher log levels remain quiet.
- Prepared installation never resolves or changes locked selections; connected repair may reproducibly rebuild an exact
  source-derived wheel from recorded provenance. A custom materialization target is valid only with explicit local copying.
- Private uv repair must preserve dynamically linked uv-managed CPython layouts when it creates a temporary environment.
- Every Depfix uv prepare, resolve, and build invocation uses a process-owned cache beneath the Depfix temporary root,
  removes it after the subprocess returns, and reclaims only dead-owner crash leftovers after the age grace. Never use or
  delete the uv cache owned by direct user invocations.
- `auto` selects in-process isolation for pure request closures and shared logical imports for closures containing native
  artifacts; mode selection is request-scoped inside mixed manifests and does not use package-name allowlists.
- Shared mode owns public and explicitly requested roots exactly, tolerates process-global private helpers and deliberate
  compatibility aliases below an owned root as conventional best effort, and raises `SharedImportConflictError` rather
  than replacing an incompatible public owner.
- `using()` may expose the first compatible shared/native selection as scoped syntax sugar. Its native modules remain the
  process owner after scope exit, so a later incompatible version raises `SharedImportConflictError`. Explicit `inprocess`
  mode rejects a required native extension with `NativeIsolationRequired`; `process` remains reserved.
- `settings.py` owns the precedence and representation of all process-wide parameters exposed by `depfix.configure()`.
  Every loading API accepts a per-request `allow_unsafe` override; its effective default is false, and enabling it may
  relax only unsafe-classification and strict in-process native-loading guards.
- Resolver candidate ranking defaults to the newest compatible artifact already present in the shared cache, including
  artifacts selected earlier in a grouped resolution. Every loading API accepts `prefer_newest`; process, environment,
  project, export, and CLI configuration inherit through `settings.py`, and resolution identities separate both modes.
- Every loading API accepts request-scoped primary and extra index overrides. A scoped primary suppresses inherited extra
  indexes unless the same call explicitly supplies them; index policy is part of request and graph identity, never mutates
  process configuration, and is rejected when an exact prepared manifest is active.
- Custom indexes prefer PEP 691 JSON but also accept standards-compatible Simple HTML media types. Dispatch by the
  response Content-Type, resolve HTML links against the final project URL, and preserve SHA-256, Requires-Python, yanked,
  size, index-isolation, and transport policy checks without falling back after valid Simple discovery.
- `depfix pip install` and `project.install_packages()` resolve package/requirement-file roots as one store-only group,
  persist an exact cache manifest, materialize verified targets, and never invoke environment installation or import
  activation. Requirement constraints apply to matching roots and dependency edges across every selected graph. The CLI
  renders one compact, identity-deduplicated store summary by default and retains the complete result under `--json`.
- Exact `install_manifest()` preparation never resolves or changes locked selections; online repair may rebuild a missing
  source-derived artifact only when its recorded provenance reproduces the locked size and SHA-256. Live grouped package
  installation may resolve and build before writing its exact stored manifest.
- Public failures use typed, credential-redacted `DepfixError` subclasses.

## Work Guidance

- Preserve the public imports re-exported by `depfix.__init__` and compatibility imports in `specifiers.py`; do not restore
  the prototype `activate()` function to the preferred or documented API.
- Keep resolver, cache, and runtime side effects explicit and testable.
- Prefer independently owned concept subpackages when a code area becomes a stable boundary; avoid circular dependencies across those boundaries.

## Verification

- Run `python -m ruff format --check .`, `python -m ruff check .`, `python -m mypy src/depfix`, and `python -m pytest -q`.

## Child DOX Index

- [`schemas/AGENTS.md`](schemas/AGENTS.md) — schema data embedded in the installed package.

The current Python modules remain a cohesive package-level graph; concept-specific ownership is documented in the documentation tree until a source move can preserve public imports without compatibility risk.
