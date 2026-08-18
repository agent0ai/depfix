# Resolution

## Purpose

- Document how user requests become exact artifacts, import names, and dependency graphs.

## Ownership

- `README.md` owns the resolution overview.
- `source-grammar.md`, `module-discovery.md`, and `uv-backend.md` own their named boundaries.

## Local Contracts

- Source normalization preserves identity and removes credentials from durable output.
- Exact artifact inspection, not distribution-name guessing, determines provided import modules.
- uv is an executable backend; Depfix does not import uv internals.
- Depfix uv subprocesses use only Depfix-owned ephemeral cache directories; user-owned uv caches are outside cleanup scope.
- Compatible selection prefers the newest complete artifact already present in the shared cache unless `prefer_newest` is
  enabled; installed custom-index and VCS artifacts are source-agnostic and need not be advertised by the active index.
  One resolution uses one verified installed-inventory snapshot for roots, transitive edges, and bulk preferences so
  repeated graph edges do not repeatedly rescan or hash the shared store.
- Compatible registry groups use a bulk uv compile plan without duplicate closure installation; conflicting roots retain
  isolated fallback while compatible cohorts keep shared plans. A failed group may first stably move roots with locally
  proven installed-version mismatches to the split boundary using verified installed metadata only, then recursively
  splits in stable halves; each
  successful half retains an independent exact plan, failed singletons use isolated resolution, and no global
  maximum-subset search combines roots back across halves.
  Cache-first compilation uses exact installed-version constraints and ephemeral metadata-only overrides without changing
  stored artifact identity; when the optional installed set is collectively incompatible, the complete root group is
  retried without those preferences before any roots are classified as conflicting.
- Exact canonical group plans are reusable across package-install and standard-import callers when roots, constraints,
  candidate-eligibility policy, indexes, and target environment are semantically identical; refresh atomically replaces
  the reusable plan.
- Grouped store installation preserves parent-specific dependency graphs and never requires incompatible roots to share
  one environment-wide dependency version.
- Per-loading-request index policies use first-index selection, participate in resolution identity, and remain isolated
  from process configuration and concurrent requests. Prefer one scoped primary index over extra indexes when possible.
- Legacy numeric ordering wildcards in index or wheel `Requires-Python` metadata normalize to exact release-prefix
  interval bounds; valid PEP 440 and unrelated malformed syntax retain strict behavior.

## Work Guidance

- Keep selection, fetching/building, artifact inspection, and runtime loading conceptually separate.

## Verification

- Use source parser, resolver, module-discovery, policy, uv-boundary, and exact-artifact tests.

## Child DOX Index
