# GitHub Actions workflows

## Purpose

- Define CI and explicitly authorized package publication.

## Ownership

- `ci.yml` owns reusable quality, test-matrix, uv-boundary, and distribution checks.
- `publish-testpypi.yml` owns manual TestPyPI publishing; `publish-pypi.yml` owns the manually dispatched, checked
  GitHub Release and PyPI pipeline.

## Local Contracts

- Every job starts from checked-out source and declared dependencies.
- Production publication requires manual dispatch from an existing annotated `vX.Y.Z` tag at the current `main` commit;
  version, changelog, release absence, and PyPI absence checks must pass before the full reusable CI workflow runs.
- A hidden draft is staged only after every reusable CI job and the authoritative distribution gate pass; it becomes a
  public GitHub Release only after PyPI publication and clean public-index verification succeed.
- Publishing jobs receive only `id-token: write`; other jobs remain read-only.
- Only draft staging, finalization, and cleanup receive `contents: write`; failed publication removes the unpublished
  draft.
- Build and test distributions without OIDC permission, then pass only that exact two-file artifact set to the protected
  `pypi` environment for publication and verify the public-index installation.

## Work Guidance

- Pin maintained major versions of official actions and keep the local release check as the artifact authority.

## Verification

- Run `python scripts/release_check.py`, validate workflow YAML, and inspect the workflow diff for permission or trigger
  expansion.

## Child DOX Index
