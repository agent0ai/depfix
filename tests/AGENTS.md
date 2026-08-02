# Test suite

## Purpose

- Verify public behavior, isolation invariants, resolution, deployment, security, and packaging-facing contracts.

## Ownership

- `conftest.py` owns deterministic artifact fixtures.
- Test modules group end-to-end, runtime/resolver, public-product, lock/cache, and standard-import scope behavior.

## Local Contracts

- Tests must not depend on the developer's ambient packages, cache, or credentials.
- Network-free fixtures are preferred; explicitly marked live checks belong in release or CI boundaries.
- Live PyPI checks require `DEPFIX_RUN_LIVE_TESTS=1`; CI owns their connected execution.
- Regressions should assert observable public behavior or a named safety invariant.
- Standard-import tests reset the dispatcher, persistent defaults, context scopes, runtimes, and configuration.
- Shared-mode tests must clean up process-global import roots and paths, and cover compatible reuse, public-owner conflicts,
  private-helper and compatibility-alias best effort, namespace contribution merging, and scoped first-use/reuse behavior.
- Unsafe-loading tests must cover per-request/global precedence, deny-by-default remediation, manifest persistence, and a
  real compiled-extension path when the interpreter provides a suitable test extension.
- Cache lifecycle tests must cover installation/use timestamps, total reclaimed targets, returning-graph reservations,
  active-runtime leases, retention configuration precedence, and equivalent Python/CLI list and removal behavior.

## Work Guidance

- Keep temporary artifacts under pytest-provided paths and reset process-global Depfix state between cases.

## Verification

- Run `python -m pytest -q`; use the closest test module during iteration.

## Child DOX Index
