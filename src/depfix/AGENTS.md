# Depfix package

## Purpose

- Resolve Python dependency requests and load pure-Python packages into isolated, version-aware import realms.

## Ownership

- Public Python and CLI entry points, source parsing, resolution, locking, caching, materialization, runtime import isolation, project preparation, and typed errors live in this package.

## Local Contracts

- Importing `depfix` performs no network, resolver, cache, or subprocess work.
- `import_module` returns exactly one selected module; `load_package` returns a lazy package handle.
- Resolved artifacts are hash-pinned and materialized outside ambient `site-packages`.
- Realm imports preserve module identity and prevent undeclared cross-realm leakage.
- Native extensions fail with a typed isolation error unless a future backend provides honest isolation.
- Public failures use typed, credential-redacted `DepfixError` subclasses.

## Work Guidance

- Preserve the public imports re-exported by `depfix.__init__` and compatibility imports in `specifiers.py`.
- Keep resolver, cache, and runtime side effects explicit and testable.
- Prefer independently owned concept subpackages when a code area becomes a stable boundary; avoid circular dependencies across those boundaries.

## Verification

- Run `python -m ruff format --check .`, `python -m ruff check .`, `python -m mypy src/depfix`, and `python -m pytest -q`.

## Child DOX Index

- [`schemas/AGENTS.md`](schemas/AGENTS.md) — schema data embedded in the installed package.

The current Python modules remain a cohesive package-level graph; concept-specific ownership is documented in the documentation tree until a source move can preserve public imports without compatibility risk.
