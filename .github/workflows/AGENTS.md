# GitHub Actions workflows

## Purpose

- Define CI and explicitly authorized package publication.

## Ownership

- `ci.yml` owns quality, test-matrix, uv-boundary, and distribution checks.
- `publish-testpypi.yml` owns manual TestPyPI publishing; `publish-pypi.yml` owns GitHub Release-driven PyPI publishing.

## Local Contracts

- Every job starts from checked-out source and declared dependencies.
- Production publication requires a published GitHub Release whose `v` tag exactly matches the package version.
- Publishing jobs receive only `id-token: write`; other jobs remain read-only.
- Build and test distributions without OIDC permission, then pass only the resulting artifact to the protected `pypi`
  environment for publication.

## Work Guidance

- Pin maintained major versions of official actions and keep the local release check as the artifact authority.

## Verification

- Run `python scripts/release_check.py` and inspect the workflow diff for permission or trigger expansion.

## Child DOX Index
