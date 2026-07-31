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
) -> PackageHandle
```

The return value is always `PackageHandle`. `module_names`, `metadata`, and `dependencies` are inspection-only;
`modules[name]`, `modules.name`, `import_module(name)`, and `only_module()` import lazily.

The async wrappers run blocking preparation in a worker thread and share the synchronous canonical module identity:
`import_module_async` and `load_package_async`.

## Configuration

`depfix.configure(...)` accepts `manifest`, `frozen`, `offline`, `cache_dir`, `uv`, `index_url`, `extra_index_url`, and
`log_level`. Precedence is per-call, `configure`, environment, optional project config/manifest discovery, defaults.

`log_level` defaults to `INFO`. At `INFO` or `DEBUG`, cold preparation writes secret-redacted resolution, uv summary,
download, materialization, and ready lines to stderr. `WARNING`, `ERROR`, `CRITICAL`, and `OFF` suppress progress.

Supported variables are `DEPFIX_MANIFEST`, `DEPFIX_FROZEN`, `DEPFIX_OFFLINE`, `DEPFIX_CACHE_DIR`, `DEPFIX_UV`,
`DEPFIX_INDEX_URL`, `DEPFIX_EXTRA_INDEX_URL`, and `DEPFIX_LOG_LEVEL`.

`multiprocessing_initializer(manifest, cache_dir)` is a spawn-safe worker initializer for prepared graphs.

## Project API

`depfix.project` exports `scan_project`, `export_project`, `install_manifest`, `create_bundle`, and `verify_manifest`.
These are the implementations called by the CLI; they return immutable result dataclasses.

## Exceptions

All expected failures derive from `DepfixError`. Public branches cover specifiers/sources, resolution, artifacts and hash
mismatches, module discovery/provision, default conflicts, invalid scopes, dispatcher replacement, manifests, uv, bundles,
offline misses, and native isolation. Exception attributes
carry structured request, source, module, manifest, artifact, policy, candidate, and remediation context. String rendering
redacts URL credentials, token-like query values, and credential-bearing CLI flags.
