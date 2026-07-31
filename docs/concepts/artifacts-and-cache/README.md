# Artifacts and cache

Depfix never installs realm packages into active `site-packages`. It stores exact bytes in a content-addressed cache, then
materializes verified, environment-specific targets for the import runtime.

```text
source or index
  → size-bounded artifact download
  → SHA-256 blob
  → wheel identity, metadata, path, and RECORD checks
  → temporary extraction
  → atomic target promotion
  → read-only completed target
```

Artifacts and realm nodes are different. One artifact is one immutable wheel or Python file; a node adds evaluated extras,
parent-specific dependency edges, module ownership, namespace contributions, and native policy. Multiple nodes may reuse
one artifact without merging their dependency contexts.

The default root comes from `platformdirs.user_cache_path("depfix")`, with format-versioned data under `v1`. Important
areas include `artifacts/sha256`, `targets`, `resolutions`, `manifests`, `metadata`, `ide`, `locks`, `tools/uv`, and
`built-wheels`. `DEPFIX_CACHE_DIR` or `depfix.configure(cache_dir=...)` changes the parent location.

Extracted files and child directories are hardened before promotion. The staging root remains owner-writable until the
atomic rename because Darwin rejects renaming a write-disabled directory; Depfix hardens the completed root immediately
after promotion while the cache mutation lock is still held.

Offline mode rejects absent content instead of fetching it. Integrity failures never promote partial state. See the
[threat model](../../operations/threat-model.md) for hostile-input assumptions and
[deployment modes](../deployment/) for cache preparation and bundles.
