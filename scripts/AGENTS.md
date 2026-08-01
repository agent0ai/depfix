# Project scripts

## Purpose

- Automate release validation and owner-facing maintenance checks.

## Ownership

- `release_check.py` is the authoritative local distribution gate.

## Local Contracts

- Scripts must fail closed, redact credentials, and avoid publishing or reserving names.
- Release validation must inspect tracked source and built archives for repository junk or credential patterns, then test a
  clean installation.
- Clean-environment creation must work with both system and dynamically linked uv-managed CPython interpreters.

## Work Guidance

- Keep scripts runnable from the repository root with declared optional dependencies.

## Verification

- Run `python scripts/release_check.py` after packaging, metadata, or release-script changes.

## Child DOX Index
