# GitHub Actions workflows

## Purpose

- Define CI and explicitly authorized package publication.

## Ownership

- `ci.yml` owns quality, test-matrix, uv-boundary, and distribution checks.
- `publish-testpypi.yml` and `publish-pypi.yml` own manual trusted-publishing flows.

## Local Contracts

- Every job starts from checked-out source and declared dependencies.
- Production publication requires a matching `v` tag and the exact confirmation input.
- Publishing jobs receive only `id-token: write`; other jobs remain read-only.

## Work Guidance

- Pin maintained major versions of official actions and keep the local release check as the artifact authority.

## Verification

- Run `python scripts/release_check.py` and inspect the workflow diff for permission or trigger expansion.

## Child DOX Index
