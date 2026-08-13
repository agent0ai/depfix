# Project scripts

## Purpose

- Automate release validation and owner-facing maintenance checks.

## Ownership

- `release_check.py` is the authoritative local distribution gate.
- `release.py` owns read-only production preflight and tag-safe workflow dispatch.
- `validate_workflows.py` owns repository-specific GitHub Actions syntax and security-contract validation.

## Local Contracts

- Scripts must fail closed, redact credentials, and avoid publishing or reserving names.
- Release validation must inspect tracked source and built archives for repository junk or credential patterns, then test a
  clean installation.
- Clean-environment creation must work with both system and dynamically linked uv-managed CPython interpreters.
- Release dispatch must fail closed unless the clean local checkout, local and remote `main`, local and remote annotated
  tag, version, changelog, and unused publication destinations agree. It must never source, persist, or print credentials.
- Workflow validation must run inside ordinary CI and the authoritative release gate, and protect manual-only triggers,
  least-privilege permissions, isolated OIDC publication, checked-draft retention, and no-OIDC recovery.

## Work Guidance

- Keep scripts runnable from the repository root with declared optional dependencies.

## Verification

- Run `python scripts/validate_workflows.py` after workflow changes and `python scripts/release_check.py` after packaging,
  metadata, workflow-contract, or release-script changes.

## Child DOX Index
