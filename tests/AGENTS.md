# Test suite

## Purpose

- Verify public behavior, isolation invariants, resolution, deployment, security, and packaging-facing contracts.

## Ownership

- `conftest.py` owns deterministic artifact fixtures.
- Test modules group end-to-end, runtime/resolver, public-product, and lock/cache behavior.

## Local Contracts

- Tests must not depend on the developer's ambient packages, cache, or credentials.
- Network-free fixtures are preferred; explicitly marked live checks belong in release or CI boundaries.
- Regressions should assert observable public behavior or a named safety invariant.

## Work Guidance

- Keep temporary artifacts under pytest-provided paths and reset process-global Depfix state between cases.

## Verification

- Run `python -m pytest -q`; use the closest test module during iteration.

## Child DOX Index
