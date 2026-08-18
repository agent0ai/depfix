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
- `default()` and `default_requirements()` own persistent ordinary-import selections; context-local `using()` scopes and
  decorators own temporary ones. Requirements-file activation uses the canonical CLI parser, filters inactive markers,
  preserves nested constraints and scoped indexes, and registers the prepared group transactionally.
- Install the narrow standard-import dispatcher only when a `default()`, `default_requirements()`, or `using()` selection
  needs it, preserve caller realms before application scopes/defaults, and delegate unmanaged imports to the prior importer.
- `patch_import()` explicitly enables a reversible, process-local fallback after ordinary import resolution. It uses only
  complete compatible graphs already recorded in the shared store, never performs resolution or network installation,
  keeps explicit scopes/defaults ahead of deterministic installed-store selection, and preserves an exact configured
  manifest's provider before considering unrelated stored graphs.
- `depfix run` enables the installed-store fallback after applying its process configuration and optional prepared state,
  before executing either a script or module; the launched application does not need its own Depfix setup call.
- Resolved artifacts are hash-pinned and materialized outside ambient `site-packages`.
- Pinned downloads retry and resume bounded transient truncation, but exact size and SHA-256 verification remain mandatory
  before atomic target promotion. Downloads and build inputs are ephemeral, remain protected through materialization,
  and are removed afterward; completed unpacked targets are the cross-project cache. Completion metadata verifies the
  installed payload rather than trusting a marker alone, while legacy targets retain a conservative file-count check.
- Cross-process cache locks retry transient Windows permission races without masking permanent permission failures on
  other platforms.
- Cache lifecycle metadata preserves first installation time and coalesces successful-import usage writes. Activated
  runtimes share one process-wide daemon that renews complete closures into one transactional, artifact-keyed usage store
  without process or runtime identities. Initial activation fails safely after a short bounded wait when another process
  holds the usage writer, rather than activating without durable first-use evidence. Runtime lease establishment and target
  validation serialize with removal under the target/artifact mutation locks, with filesystem locks acquired before the
  process-global usage-registration lock. Automatic retention records candidates before a configurable deletion grace and
  serializes final usage revalidation and removal with the usage transaction under artifact/target locks; explicit cleanup
  and removal remain immediate under mutation locks while protecting preparation reservations and process-leased active
  runtimes.
- Install and cache-maintenance passes reconcile obsolete retained blobs, abandoned process-owned download parts, stale
  extraction targets, and stale built-wheel staging without deleting active work. Dry-run reconciliation is observational;
  offline reuse requires a complete target or an exact bundle-provided artifact.
- Bundle creation rebuilds source-derived ephemeral wheels from recorded provenance when necessary and accepts them only
  when their size and SHA-256 exactly match the locked artifact.
- Successful graph synchronization records deduplicated, secret-redacted installation origins and enough root/node data
  to inspect flat packages, distribution-level duplicate footprint, and top-down dependency trees without the original
  project. Exact artifact hashes remain single physical entries; same-version variants require distinct hashes.
- Top-level `list` and `tree` inspect installed shared-store artifacts and provenance trees. Manifest inspection requires
  explicit `--manifest` syntax (with migration guidance for positional compatibility), while `cache resolutions` owns
  live-resolution record inspection and `cache list` remains only a deprecated compatibility alias.
- Top-level `uninstall` parses one or more bare names or PEP 440 constraints, deduplicates physical artifacts, never
  cascades into dependencies, and protects preparation reservations and process-leased active runtimes. Exact manifests
  remain immutable while installed inventory filters removed targets; `cache remove` remains the advanced artifact-hash
  compatibility path with the same protection.
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
  complete source-agnostic custom-index and VCS artifacts even when the active index does not advertise their hashes, and
  artifacts selected earlier in a grouped resolution. Every loading API accepts `prefer_newest`; process, environment,
  project, export, and CLI configuration inherit through `settings.py`, and resolution identities separate both modes.
  Each resolution verifies the installed inventory once and uses that deterministic snapshot for direct roots, transitive
  edges, and bulk-plan preferences; repeated dependency edges must not rehash every installed target.
- Every loading API accepts request-scoped primary and extra index overrides. A scoped primary suppresses inherited extra
  indexes unless the same call explicitly supplies them; index policy is part of request and graph identity, never mutates
  process configuration, and is rejected when an exact prepared manifest is active.
- Custom indexes prefer PEP 691 JSON but also accept standards-compatible Simple HTML media types. Dispatch by the
  response Content-Type, resolve HTML links against the final project URL, and preserve SHA-256, Requires-Python, yanked,
  size, index-isolation, and transport policy checks without falling back after valid Simple discovery. A selected live
  artifact without an advertised SHA-256 is downloaded once and bound to its observed SHA-256 and size before inspection;
  malformed or conflicting advertised hashes remain hard failures, and prepared/offline graphs remain exact.
- Legacy index and wheel `Requires-Python` metadata may normalize numeric ordering wildcards to their exact release-prefix
  interval bounds. Valid PEP 440 remains unchanged, and unrelated or ambiguous malformed specifiers remain hard failures;
  graph and inspection-cache records use the canonical repaired bound.
- `depfix pip install` and `project.install_packages()` resolve package/requirement-file roots as one store-only group,
  persist an exact cache manifest, materialize verified targets, and never invoke environment installation or import
  activation. Requirement constraints apply to matching roots and dependency edges across every selected graph. The CLI
  renders one compact, identity-deduplicated store summary by default and retains the complete result under `--json`.
- Compatible multi-root registry groups use one `uv pip compile` exact-version plan without installing a duplicate closure;
  Depfix seeds cache-first planning with verified installed metadata, ingests artifacts through its own verified store, and
  recursively bisects a conflicting group in stable order. Every successful half keeps its own exact plan and may retain
  dependency versions different from another successful half; only failed singleton roots use isolated resolution. The
  split tree is linear in the root count and does not search for a mathematically largest cross-half cohort. Before
  splitting, verified installed root metadata may stably move roots with locally proven installed-version mismatches to
  the split boundary; this hint performs no network lookup or dependency pre-resolution and leaves unscored roots in
  input order. Optional
  installed constraints are removed and the full group is retried before splitting when those cache preferences make the
  initial plan unsatisfiable. Graph reconstruction treats each successful exact plan as authoritative: installed reuse is
  eligible only at the version selected by that cohort, and inspected metadata that contradicts the plan fails explicitly.
- Package-install and standard-import consumers share an exact canonical group plan when normalized roots, constraints,
  effective candidate-eligibility policy, indexes, and target environment match; refresh replaces the canonical plan,
  while consumer mode and isolation remain runtime bindings.
- Exact `install_manifest()` preparation never resolves or changes locked selections; online repair may rebuild a missing
  source-derived artifact only when its recorded provenance reproduces the locked size and SHA-256. Live grouped package
  installation may resolve and build before writing its exact stored manifest.
- Public failures use typed, credential-redacted `DepfixError` subclasses; requirements parse failures include their
  resolved filename and logical starting line.

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
