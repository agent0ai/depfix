# Phase-one baseline inventory

Recorded before the second implementation phase. The pre-change gates were:

```text
14 tests passed
strict mypy: no issues in 14 source files
```

## Public surface before phase two

- Dynamic `import_module(specifier, module=...)` and explicit lock activation.
- Configuration file containing aliases, followed by `lock`, `sync`, and `run`.
- Diagnostic graph, dependency explanation, doctor, fetch, and cache commands.
- Generated runtime aliases and exact-artifact stub trees.

## Architecture retained

- Artifact, distribution-node, realm, alias, and logical-module identities are
  distinct.
- Canonical pure-Python modules use graph/node-qualified synthetic names.
- Each loaded module receives a realm-bound import function rather than a
  process-global import monkeypatch.
- Dependency roots resolve through parent-specific edges; unrelated realms do
  not merge.
- Artifact trees remain outside active `site-packages` and global `sys.path`.
- Hash-verified content storage, safe wheel extraction, atomic population, and
  typed native rejection are already implemented.
- Relative, absolute, circular, dynamic, namespace, resource, metadata,
  threaded, and spawn-child behavior has automated coverage.

## Pre-change cache and resolver

- Cache: user cache application directory, schema `v1`, SHA-256 blobs and
  read-only extracted trees.
- Resolver: direct wheels/files plus PyPI JSON, isolated dependency edges,
  newest compatible candidates using `packaging`.
- Limitations: wheel-only distribution preparation, no uv subprocess backend,
  explicit import-module roots, no package handle, no repository scanner,
  prototype lock/install vocabulary, no air-gap bundle.

## Migration boundary

The phase-one manifests and cache schema have provisional public identities and
must not be consumed silently after the permanent product rename. Phase two
will either import their exact graph through an explicit migration operation or
raise a clear invalidation error instructing the user to export again.
