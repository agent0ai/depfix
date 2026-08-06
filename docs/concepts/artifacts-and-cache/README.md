# Artifacts and cache

Depfix never installs realm packages into active `site-packages`. It stores exact bytes in a content-addressed cache, then
materializes verified, environment-specific targets for the import runtime.

```text
source or index
  → size-bounded artifact download with bounded resume/retry
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

Mutable lifecycle and provenance records are separate from immutable content. Depfix records an artifact's first
installation time once, then updates a coalesced usage marker after successful package imports. After a graph is
successfully synchronized, it also records the retained roots, dependency edges, exact artifact hashes, and a
secret-redacted reason: a canonical Depfix command or the calling script path and line. Equivalent graph/origin records
share one identity, so repeated runs do not grow provenance indefinitely.

`depfix cache list` combines those records with the blob, materialized targets, retained build output, and source-archive
footprint. Its duplicate view groups physical artifacts by normalized distribution; exact SHA-256 content cannot be
duplicated, while different versions and distinct artifacts for the same version can coexist. Its tree view reconstructs
currently retained installation roots and dependency edges without requiring the original project or requirements file
to remain present. `depfix.inspect_cache()` exposes the same flat, duplicate, and tree structures to Python. Legacy
installed targets without lifecycle/provenance metadata remain visible as `unknown` artifacts, use their filesystem
modification time as the conservative installation time, and simply have no recorded reason/tree.

Automatic retention defaults to 30 unused days. A missing maintenance clock is initialized without scanning, and later
daily checks run in a daemon thread so ordinary imports only pay a constant-time timestamp check. The current graph is
reserved before synchronization, which prevents a returning application from evicting and refetching its own packages.
Active runtimes hold per-artifact, cross-process leases; cleanup skips live leases and clears stale process markers.
Removal takes the same target and artifact mutation locks as installation before deleting the blob, every environment
target, lifecycle metadata, retained built wheel, and an unshared source archive.

Explicit `cleanup_cache()` / `depfix cache cleanup` operations use the same retention and lease rules. Exact package
removal uses `remove_cached_package()` / `depfix cache remove`; dry-run mode reports the selection and reclaimable bytes
without deleting it. Full `depfix cache clean` remains the deliberate operation for deleting the complete cache root.

Extracted files and child directories are hardened before promotion. The staging root remains owner-writable until the
atomic rename because Darwin rejects renaming a write-disabled directory; Depfix hardens the completed root immediately
after promotion while the cache mutation lock is still held.

Offline mode rejects absent content instead of fetching it. Integrity failures never promote partial state. See the
[threat model](../../operations/threat-model.md) for hostile-input assumptions and
[deployment modes](../deployment/) for cache preparation and bundles.

The default per-artifact download limit and per-wheel expanded-size limit are each 1 GiB. A truncated transfer may resume
up to the bounded attempt count, but only exact expected size and SHA-256 content are promoted.
