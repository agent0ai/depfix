# Two-version example

## Purpose

- Show two pure-Python versions of the same distribution loaded side by side.

## Ownership

- `application.py` is the canonical smallest multiversion example.

## Local Contracts

- The example must use ordinary imports in separate `using()` scopes and assert distinct versions and module identities.

## Work Guidance

- Keep the example self-contained and readable before adding ancillary configuration.

## Verification

- Run `python examples/two_idna_versions/application.py` with network access or a warm cache.

## Child DOX Index
