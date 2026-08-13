# GitHub Actions workflows

## Purpose

- Define CI and explicitly authorized package publication.

## Ownership

- `ci.yml` owns reusable quality, test-matrix, uv-boundary, and distribution checks.
- `publish-testpypi.yml` owns manual TestPyPI publishing; `publish-pypi.yml` owns the manually dispatched, checked
  GitHub Release and PyPI pipeline.
- `recover-pypi-release.yml` owns manual completion of a retained GitHub draft after a successful PyPI upload.

## Local Contracts

- Every job starts from checked-out source and declared dependencies.
- Production publication requires manual dispatch from an existing annotated `vX.Y.Z` tag at the current `main` commit;
  version, changelog, release absence, and PyPI absence checks must pass before the full reusable CI workflow runs.
- A hidden draft is staged only after every reusable CI job and the authoritative distribution gate pass; it becomes a
  public GitHub Release only after PyPI publication and clean public-index verification succeed.
- Publishing jobs receive only `id-token: write`; other jobs remain read-only.
- Only draft staging, finalization, and cleanup receive `contents: write`; a failed upload removes the unpublished draft,
  while a post-upload verification failure preserves the exact draft assets so failed jobs can be retried safely.
- Build and test distributions without OIDC permission, then pass only that exact two-file artifact set to the protected
  `pypi` environment for publication and verify the public-index installation with bounded retries for PyPI JSON/simple
  index propagation.
- Recovery shares production concurrency, receives no OIDC token, and cannot upload or replace artifacts. It may publish
  only an existing draft whose exact filenames and SHA-256 digests match a clean-installable PyPI release.
- Ordinary CI and the authoritative release gate must run `scripts/validate_workflows.py` so trigger, permission, job-graph,
  draft-retention, and recovery contracts cannot drift silently.
- The latest-uv connected gate runs published-package import and cross-version object-boundary probes; ordinary matrix
  tests remain network-free.

## Work Guidance

- Pin maintained major versions of official actions and keep the local release check as the artifact authority.

## Verification

- Run `python scripts/validate_workflows.py` and `python scripts/release_check.py`, then inspect the workflow diff for
  permission or trigger expansion.

## Child DOX Index
