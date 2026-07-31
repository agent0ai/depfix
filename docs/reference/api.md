# Python API

## Runtime loading

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

`activate(manifest)` validates and activates an installed graph. `multiprocessing_initializer(manifest, cache_dir)` is a
spawn-safe worker initializer.

## Project API

`depfix.project` exports `scan_project`, `export_project`, `install_manifest`, `create_bundle`, and `verify_manifest`.
These are the implementations called by the CLI; they return immutable result dataclasses.

## Exceptions

All expected failures derive from `DepfixError`. Public branches cover specifiers/sources, resolution, artifacts and hash
mismatches, module discovery/provision, manifests, uv, bundles, offline misses, and native isolation. Exception attributes
carry structured request, source, module, manifest, artifact, policy, candidate, and remediation context. String rendering
redacts URL credentials, token-like query values, and credential-bearing CLI flags.
