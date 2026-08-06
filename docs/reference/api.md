# Python API

## Standard imports

```python
depfix.default(
    *specifiers,
    refresh=False,
    manifest=None,
    frozen=None,
    offline=None,
    isolation=None,
    allow_unsafe=None,
    prefer_newest=None,
) -> None
```

`default()` adds persistent distribution selections for ordinary imports. Identical calls are idempotent; later calls may
add new import roots but cannot silently replace an existing root with another artifact. One distribution can provide
several roots, such as `setuptools` and `pkg_resources`.

```python
depfix.using(
    *specifiers,
    refresh=False,
    manifest=None,
    frozen=None,
    offline=None,
    isolation=None,
    allow_unsafe=None,
    prefer_newest=None,
) -> ContextDecorator
```

`using()` prepares one consistent temporary import map. It supports nested `with` blocks and synchronous or asynchronous
function decorators. Scope state uses context-local storage; leaving a scope restores the prior selection while modules
already loaded from it remain usable. It is not a class decorator.

Both functions accept one or more bare/PyPI requirements, `pypi:`, `git:`, `url:`, `file:`, `py:`, or standard PEP 508
direct references. A multi-argument call is resolved and exported as one grouped top-level selection.

The ordinary-import dispatcher is installed once, on the first `default()` or `using()` call. It resolves a Depfix-loaded
caller's permanent realm first, then the active scope, then defaults, and delegates unmanaged imports unchanged. Merely
importing `depfix` has no import-hook, resolver, subprocess, cache, or network side effect.

## Explicit dynamic loading

```python
depfix.import_module(
    specifier,
    *,
    module=None,
    refresh=False,
    manifest=None,
    frozen=None,
    offline=None,
    isolation=None,
    allow_unsafe=None,
    prefer_newest=None,
) -> ModuleType
```

The return value is always one module. Automatic discovery must produce one public candidate; otherwise
`NoImportModulesError` or `MultipleImportModulesError` is raised. `module=` is validated against the exact artifact.

```python
depfix.load_package(
    specifier,
    *,
    refresh=False,
    manifest=None,
    frozen=None,
    offline=None,
    isolation=None,
    allow_unsafe=None,
    prefer_newest=None,
) -> PackageHandle
```

The return value is always `PackageHandle`. `module_names`, `metadata`, and `dependencies` are inspection-only;
`modules[name]`, `modules.name`, `import_module(name)`, and `only_module()` import lazily.

The async wrappers run blocking preparation in a worker thread and share the synchronous canonical module identity:
`import_module_async` and `load_package_async`.

## Object-boundary diagnostics

```python
depfix.realm_of(value: object) -> RealmInfo | None
```

`realm_of()` reports the managed module that owns a module, class, function, or instance type. `RealmInfo` contains
`graph_id`, `node_id`, normalized `distribution`, `version`, logical `module`, and `artifact_id`. Its `package` property is
the `distribution==version` display form; `realm_id` is the exact `graph_id:node_id` identity. Unmanaged values return
`None`.

```python
depfix.assert_same_realm(
    consumer: object | RealmInfo,
    *values: object,
    recursive: bool = True,
) -> None
```

`assert_same_realm()` requires the consumer to have managed provenance and accepts unmanaged values. Every managed value
must have the same graph and node identity as the consumer. Recursive checks inspect nested builtin `dict`, `list`,
`tuple`, `set`, and `frozenset` values without traversing arbitrary application objects. A mismatch raises
`RealmBoundaryError` with `consumer`, `producer`, `consumer_realm`, `producer_realm`, and `value_path` attributes.

```python
@depfix.enforce_same_realm(
    consumer,
    *,
    parameters: Iterable[str] | None = None,
    recursive: bool = True,
    check_return: bool = False,
)
```

`enforce_same_realm()` applies the same check to all supplied arguments or only the named `parameters`; one parameter may
also be passed as a string. It preserves sync and async callables. `check_return=True` also checks the direct return value
after a successful call. Unknown parameter names fail when the decorator is applied.

These APIs are opt-in diagnostics, not translators. Provenance cannot be inferred when a managed library decorates or
generates a class that remains owned by an application module. See the
[object-boundary guide](../guides/object-boundaries.md) for adapters and limitations.

## Version selection policy

Every loading API accepts `prefer_newest=`. Omitting it inherits configuration; the effective default is `False`.
Depfix then ranks compatible artifacts already present in its shared content store ahead of uncached artifacts and selects
the newest cached match. If no cached artifact satisfies the requirement, it selects the newest compatible version
normally. Only artifacts still published by the configured index, valid for the interpreter, permitted by policy, and
inside every declared version constraint are eligible.

In a multi-package `default()` or `using()` call, roots are processed in a stable order. Artifacts fetched for an earlier
root can therefore satisfy a later root, reducing duplicate versions when their ranges overlap. This is greedy compatible
reuse, not a global minimum-package optimization pass; incompatible ranges still produce distinct nodes and artifacts.

Pass `prefer_newest=True` to a loading call to ignore cache presence when ranking compatible candidates. Set it globally
with `depfix.configure(prefer_newest=True)`, `DEPFIX_PREFER_NEWEST=1`, or `[resolver] prefer-newest = true`. A completed
live resolution is an exact cached graph; use `refresh=True` to reconsider candidates for the same request after new
releases appear. Prepared manifests are always exact and remain unchanged until exported again.

## Isolation modes

Every loading API accepts `isolation=`. `None` and `"auto"` are the default:

- `auto` uses an in-process realm when the selected request and its dependency closure are pure Python. If that closure
  contains a native or platform-specific artifact, it uses shared mode.
- `inprocess` forces synthetic realm identities and parent-specific dependency edges. A required native extension raises
  `NativeIsolationRequired` unless unsafe loading is explicitly enabled; packages with a working pure-Python fallback may
  still load. `isolated` is retained as an alias for this mode.
- `shared` prepends only verified cache targets and imports under normal logical names. Public and explicitly requested
  roots have one compatible owner per process. An incompatible loaded or Depfix-owned root raises
  `SharedImportConflictError`; private top-level helpers follow conventional best-effort behavior.
- `process` is reserved for a future RPC backend and currently raises `NativeIsolationRequired`.

Shared imports are process-global and cannot provide multiversion realm isolation. Repeating the same selection is safe
and idempotent. `default()` persists a shared selection. `using()` may expose the first compatible shared selection only
inside its lexical scope, but scope exit does not unload native modules or release their process ownership. A later
incompatible version raises `SharedImportConflictError`. Use `isolation="inprocess"` only when the selected package does
not actually require its native extension, or use an application-owned worker to switch or overlap native versions.

## Unsafe-loading policy

Every loading API accepts `allow_unsafe=`. Omitting it inherits the configured value; the effective default is `False`.
Pass `allow_unsafe=True` on one `import_module()`, `load_package()`, `default()`, or `using()` request to opt that request
into known-unsafe package classifications and deliberate native-extension loading in an `inprocess` realm. The async
wrappers expose the same option.

This override does not disable hashes, artifact validation, network policy, frozen/offline rules, shared-owner conflicts,
or the unavailable `process` backend. Those checks protect correctness and provenance rather than the unsafe-execution
decision. Loaded package code still has the normal authority of the Python process.

## Configuration

`depfix.configure(...)` is the single process-wide Python configuration entry point, including for future global
parameters. It accepts `manifest`, `frozen`, `offline`, `allow_unsafe`, `prefer_newest`, `cache_dir`, `cache_retention_days`,
`cache_auto_cleanup`, `uv`, `index_url`, `extra_index_url`, and `log_level`. Precedence is per-call, `configure`,
environment, optional project config/manifest discovery, defaults. An explicit per-call `False` therefore overrides a
process-wide `True`.

Persistent project defaults live together in `.depfix/config.toml`. For example:

```toml
[settings]
allow-unsafe = false
offline = false
cache-retention-days = 30
cache-auto-cleanup = true

[resolver]
prefer-newest = false
```

Unsafe loading can be enabled process-wide with `depfix.configure(allow_unsafe=True)`, persistently with
`[settings] allow-unsafe = true`, or through `DEPFIX_ALLOW_UNSAFE=1`. Keep it disabled unless the process is intended to
accept the reduced isolation guarantee.

`log_level` defaults to `INFO`. At `INFO` or `DEBUG`, cold preparation writes secret-redacted resolution, uv summary,
download, materialization, and ready lines to stderr. `WARNING`, `ERROR`, `CRITICAL`, and `OFF` suppress progress.

Supported variables are `DEPFIX_MANIFEST`, `DEPFIX_FROZEN`, `DEPFIX_OFFLINE`, `DEPFIX_ALLOW_UNSAFE`,
`DEPFIX_PREFER_NEWEST`, `DEPFIX_CACHE_DIR`, `DEPFIX_CACHE_RETENTION_DAYS`, `DEPFIX_CACHE_AUTO_CLEANUP`, `DEPFIX_UV`, `DEPFIX_INDEX_URL`,
`DEPFIX_EXTRA_INDEX_URL`, and `DEPFIX_LOG_LEVEL`.

`multiprocessing_initializer(manifest, cache_dir)` is a spawn-safe worker initializer for prepared graphs.

## Cache API

```python
depfix.list_cached_packages(*, cache_dir=None) -> tuple[CachedPackage, ...]
depfix.inspect_cache(*, cache_dir=None) -> CacheInventory
depfix.cleanup_cache(*, days=None, cache_dir=None, dry_run=False) -> CacheCleanupResult
depfix.remove_cached_package(
    distribution,
    *,
    version=None,
    artifact_hash=None,
    cache_dir=None,
    dry_run=False,
) -> CacheCleanupResult
```

`CachedPackage` reports normalized `distribution`, `version`, `artifact_hash`, `filename`, UTC `installed_at`, optional
UTC `last_used_at`, total `size_bytes`, and zero or more `reasons`. Size includes the immutable blob, all materialized
environment targets, and associated retained build/source data. Each `PackageInstallReason` includes a kind,
secret-redacted description, UTC `recorded_at`, and, when available, the originating command or Python source path and
line plus the exact manifest path.

`inspect_cache()` returns one immutable `CacheInventory` with:

- `packages`: the same flat artifact entries as `list_cached_packages()`;
- `duplicates`: `CachedDuplicate` groups for distributions with more than one physical artifact, ranked by additional
  footprint. `same_version_variants` identifies distinct artifact hashes carrying the same distribution version;
- `installations`: `CachedInstallation` origins with top-down `CachedPackageNode` dependency trees;
- `total_size_bytes`: the current physical package footprint.

The cache is content-addressed, so the same SHA-256 appears physically once even when several graphs use it. A duplicate
group can therefore mean different versions or distinct builds/artifacts of the same version. `additional_size_bytes` is
the group's footprint beyond its largest member; it is an inspection metric, not a claim that the bytes are safe to
remove.

`cleanup_cache()` uses the configured 30-day retention when `days` is omitted; zero selects every inactive installed
artifact. `remove_cached_package()` matches one normalized distribution and can be narrowed by version or SHA-256.

`CacheCleanupResult.removed` contains the selected entries (the would-remove entries for `dry_run=True`),
`skipped_active` contains matching artifacts protected by preparation reservations or live runtimes, and
`reclaimed_bytes` reports the removed or would-remove package footprint. Automatic cleanup uses the same policy once
daily in the background. It protects the graph currently being
prepared and every live runtime lease; it can be disabled with `cache_auto_cleanup=False` without disabling explicit
cleanup.

## Project API

`depfix.project` exports `scan_project`, `export_project`, `install_packages`, `install_manifest`, `create_bundle`, and
`verify_manifest`.
These are the implementations called by the CLI; they return immutable result dataclasses. `install_manifest()` never
resolves or builds. Passing `target=` requires `local=True` so verified package trees are actually copied to that location.
`export_project(..., prefer_newest=None)` uses the same inherited cache-reuse policy and accepts an explicit override.

```python
install_packages(
    requirements,
    *,
    constraints=(),
    refresh=False,
    offline=None,
    index_url=None,
    extra_index_url=(),
    prefer_newest=None,
    cache_dir=None,
    base_dir=None,
    reason=None,
) -> PackageInstallResult
```

`install_packages()` performs the store-only grouped operation behind `depfix pip install`. Each root retains its own
dependency graph, compatible cached dependencies may be reused across roots, and incompatible dependency ranges may
select multiple versions. It writes a reusable exact manifest beneath the shared store, materializes verified targets,
and does not activate a runtime or alter `site-packages`/`sys.path`. Constraints apply to matching root and transitive
distributions in every selected graph. `PackageInstallResult` reports the manifest, identity, request/artifact counts,
selected root package versions, store path, and whether the exact install graph was already warm. Python calls record the
caller path and line automatically. `reason=` may provide an explicit command-style reason; the CLI uses it to retain the
canonical `depfix pip install ...` invocation without index credentials.

## Exceptions

All expected failures derive from `DepfixError`. Public branches cover specifiers/sources, resolution, artifacts and hash
mismatches, module discovery/provision, default conflicts, invalid scopes, dispatcher replacement, realm boundaries,
manifests, uv, bundles, offline misses, unsafe package policy, native isolation, and shared-owner conflicts. Exception
attributes carry structured request, source, module, manifest, artifact, policy, candidate, producer/consumer, and
remediation context. String rendering redacts URL credentials, token-like query values, and credential-bearing CLI flags.
