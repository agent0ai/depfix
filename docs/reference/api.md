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
) -> PackageHandle
```

The return value is always `PackageHandle`. `module_names`, `metadata`, and `dependencies` are inspection-only;
`modules[name]`, `modules.name`, `import_module(name)`, and `only_module()` import lazily.

The async wrappers run blocking preparation in a worker thread and share the synchronous canonical module identity:
`import_module_async` and `load_package_async`.

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
parameters. It accepts `manifest`, `frozen`, `offline`, `allow_unsafe`, `cache_dir`, `cache_retention_days`,
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
```

Unsafe loading can be enabled process-wide with `depfix.configure(allow_unsafe=True)`, persistently with
`[settings] allow-unsafe = true`, or through `DEPFIX_ALLOW_UNSAFE=1`. Keep it disabled unless the process is intended to
accept the reduced isolation guarantee.

`log_level` defaults to `INFO`. At `INFO` or `DEBUG`, cold preparation writes secret-redacted resolution, uv summary,
download, materialization, and ready lines to stderr. `WARNING`, `ERROR`, `CRITICAL`, and `OFF` suppress progress.

Supported variables are `DEPFIX_MANIFEST`, `DEPFIX_FROZEN`, `DEPFIX_OFFLINE`, `DEPFIX_ALLOW_UNSAFE`, `DEPFIX_CACHE_DIR`,
`DEPFIX_CACHE_RETENTION_DAYS`, `DEPFIX_CACHE_AUTO_CLEANUP`, `DEPFIX_UV`, `DEPFIX_INDEX_URL`,
`DEPFIX_EXTRA_INDEX_URL`, and `DEPFIX_LOG_LEVEL`.

`multiprocessing_initializer(manifest, cache_dir)` is a spawn-safe worker initializer for prepared graphs.

## Cache API

```python
depfix.list_cached_packages(*, cache_dir=None) -> tuple[CachedPackage, ...]
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
UTC `last_used_at`, and total `size_bytes`. Size includes the immutable blob, all materialized environment targets, and
associated retained build/source data. `cleanup_cache()` uses the configured 30-day retention when `days` is omitted;
zero selects every inactive installed artifact. `remove_cached_package()` matches one normalized distribution and can be
narrowed by version or SHA-256.

`CacheCleanupResult.removed` contains the selected entries (the would-remove entries for `dry_run=True`),
`skipped_active` contains matching artifacts protected by preparation reservations or live runtimes, and
`reclaimed_bytes` reports the removed or would-remove package footprint. Automatic cleanup uses the same policy once
daily in the background. It protects the graph currently being
prepared and every live runtime lease; it can be disabled with `cache_auto_cleanup=False` without disabling explicit
cleanup.

## Project API

`depfix.project` exports `scan_project`, `export_project`, `install_manifest`, `create_bundle`, and `verify_manifest`.
These are the implementations called by the CLI; they return immutable result dataclasses. `install_manifest()` never
resolves or builds. Passing `target=` requires `local=True` so verified package trees are actually copied to that location.

## Exceptions

All expected failures derive from `DepfixError`. Public branches cover specifiers/sources, resolution, artifacts and hash
mismatches, module discovery/provision, default conflicts, invalid scopes, dispatcher replacement, manifests, uv, bundles,
offline misses, unsafe package policy, native isolation, and shared-owner conflicts. Exception attributes
carry structured request, source, module, manifest, artifact, policy, candidate, and remediation context. String rendering
redacts URL credentials, token-like query values, and credential-bearing CLI flags.
