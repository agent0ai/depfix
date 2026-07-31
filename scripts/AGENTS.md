# Project scripts

## Purpose

- Automate release validation and owner-facing maintenance checks.

## Ownership

- `release_check.py` is the authoritative local distribution gate.
- `name_preflight.py` performs non-reserving package-index availability checks.

## Local Contracts

- Scripts must fail closed, redact credentials, and avoid publishing or reserving names.
- Release validation must inspect built archives and test a clean installation.

## Work Guidance

- Keep scripts runnable from the repository root with declared optional dependencies.

## Verification

- Run `python scripts/release_check.py` after packaging, metadata, or release-script changes.

## Child DOX Index
