# Depfix package

## Purpose

- Resolve Python dependency requests and load pure-Python packages into isolated, version-aware import realms.

## Ownership

- Public Python and CLI entry points, source parsing, resolution, locking, caching, materialization, runtime import isolation, project preparation, and typed errors live in this package.

## Local Contracts

- Importing `depfix` performs no network, resolver, cache, or subprocess work.
- Keep resolver and scope implementation imports lazy at the public package boundary so the import-only contract also
  holds on Windows.
- `import_module` returns exactly one selected module; `load_package` returns a lazy package handle.
- `default()` owns persistent ordinary-import selections; context-local `using()` scopes and decorators own temporary ones.
- Install the narrow standard-import dispatcher only on the first `default()` or `using()` call, preserve caller realms
  before application scopes/defaults, and delegate unmanaged imports to the prior importer.
- Resolved artifacts are hash-pinned and materialized outside ambient `site-packages`.
- Decode local `file:` URLs with platform-native rules, including Windows drive-letter and UNC path forms.
- Realm imports preserve module identity and prevent undeclared cross-realm leakage.
- Package compatibility fallbacks may use modules embedded in the same selected artifact; declared dependency providers
  always take precedence.
- Cold package preparation reports secret-safe progress on stderr by default; warning and higher log levels remain quiet.
- Prepared installation never resolves or builds; a custom materialization target is valid only with explicit local copying.
- Private uv repair must preserve dynamically linked uv-managed CPython layouts when it creates a temporary environment.
- Native extensions fail with a typed isolation error unless a future backend provides honest isolation.
- Public failures use typed, credential-redacted `DepfixError` subclasses.

## Work Guidance

- Preserve the public imports re-exported by `depfix.__init__` and compatibility imports in `specifiers.py`; do not restore
  the prototype `activate()` function to the preferred or documented API.
- Keep resolver, cache, and runtime side effects explicit and testable.
- Prefer independently owned concept subpackages when a code area becomes a stable boundary; avoid circular dependencies across those boundaries.

## Verification

- Run `python -m ruff format --check .`, `python -m ruff check .`, `python -m mypy src/depfix`, and `python -m pytest -q`.

## Child DOX Index

- [`schemas/AGENTS.md`](schemas/AGENTS.md) — schema data embedded in the installed package.

The current Python modules remain a cohesive package-level graph; concept-specific ownership is documented in the documentation tree until a source move can preserve public imports without compatibility risk.
