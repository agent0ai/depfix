# Troubleshooting

- `MultipleImportModulesError`: use `load_package(specifier)` or pass one candidate through `module=`.
- `NoImportModulesError`: the distribution may be command-only or metadata may explicitly declare no import roots; inspect a
  `PackageHandle`.
- `ManifestMismatchError`: export/install with the same CPython minor, ABI, platform, architecture, request API/module, and
  isolation policy.
- `FrozenManifestError`: the normalized call is not declared. Export current source or correct `DEPFIX_MANIFEST`.
- `OfflineArtifactMissingError`: install a complete `.depfixbundle` or fetch/install on a connected host first.
- `HashMismatchError`: do not update the recorded hash to match unexpected content. Remove the corrupt cache entry only
  after verifying the trusted source, then fetch again.
- `UvNotFoundError`/`UnsupportedUvVersionError`: install the package with dependencies or set `DEPFIX_UV` to uv 0.11.0+.
- `UnsafePackageError`: the selected graph is classified as unsafe. If the package is trusted and reduced isolation is
  acceptable, pass `allow_unsafe=True` on that request, call `depfix.configure(allow_unsafe=True)` process-wide, or set
  `DEPFIX_ALLOW_UNSAFE=1`. This never bypasses integrity, network, or incompatible-owner checks.
- `NativeIsolationRequired`: `inprocess` reached a required native extension or the reserved `process` mode was requested.
  Let `auto` use shared mode, pass `allow_unsafe=True` only when deliberate in-process native loading is acceptable, or
  use an application-owned worker. `allow_unsafe` cannot enable the unavailable `process` backend.
- `SharedImportConflictError`: an incompatible public import root already belongs to this process. Reuse the loaded
  version, choose a compatible requirement, or start a fresh worker; this also applies after a native `using()` scope
  exits because native modules cannot be safely unloaded.
- editor cannot resolve `depfix_imports`: run `depfix ide sync` and apply the path from `depfix ide configure`.
- generated alias mismatch: regenerate IDE data from the exact installed manifest and detach stale `.pth` files.
- stale cache operation after an unclean process exit: inspect `depfix cache dir`; a lock timeout reports its exact path.
- macOS `PermissionError` at `wheel.py: os.replace(...)` with Depfix 0.1.0: upgrade to 0.2.0 or later. Clearing the cache
  alone does not fix the 0.1.0 promotion bug.

Run `depfix doctor --json` to report uv, cache, manifest, and native classifications. Remove private paths if needed before
sharing diagnostics; secrets are redacted automatically but diagnostics should still be reviewed.
