# Distributable source

## Purpose

- Contain Python packages shipped in Depfix distributions.

## Ownership

- Only installable runtime source and packaged runtime data belong here.

## Local Contracts

- Package discovery is rooted at `src/` and declared by `pyproject.toml`.
- Generated packaging metadata is not source and does not receive DOX ownership.

## Work Guidance

- Keep package boundaries importable in editable and wheel installations.

## Verification

- Run strict type checking, unit tests, and a clean wheel-install smoke test.

## Child DOX Index

- [`depfix/AGENTS.md`](depfix/AGENTS.md) — the public package and runtime implementation.
