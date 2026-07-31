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

## Work Guidance

- Keep selection, fetching/building, artifact inspection, and runtime loading conceptually separate.

## Verification

- Use source parser, resolver, module-discovery, policy, uv-boundary, and exact-artifact tests.

## Child DOX Index
