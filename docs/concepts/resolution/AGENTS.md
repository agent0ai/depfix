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
- Compatible selection prefers the newest artifact already present in the shared cache unless `prefer_newest` is enabled;
  grouped reuse is stable and greedy rather than a global minimum-graph solver.
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
