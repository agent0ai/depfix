# Release checklist

Publishing a versioned GitHub Release starts the production Trusted Publishing workflow. A push or tag alone never
publishes. The workflow builds and tests without OIDC permission, then the protected `pypi` environment authorizes the
separate upload job. PyPI already contains Depfix 0.1.0.

## Owner-controlled blockers

- [x] Add the owner-approved MIT `LICENSE` and SPDX PEP 621 license metadata.
- [x] Record `agent0ai` as project owner in package metadata.
- [x] Record the owner-specified canonical source, documentation, issue, and changelog URLs.
- [ ] Apply [the prepared GitHub About metadata](.github/REPOSITORY_METADATA.md) in repository settings.
- [x] Confirm normalized name `depfix` is owned by the published functional `0.1.0` Alpha release.
- [x] Configure the protected `pypi` repository environment with required reviewers and deployment tags restricted to
  `v*`; configure `testpypi` separately only when that staging workflow is wanted.
- [x] Add the production Trusted Publisher under PyPI project `depfix` → Publishing with these exact values:
  - Owner: `agent0ai`
  - Repository: `depfix`
  - Workflow filename: `publish-pypi.yml`
  - Environment: `pypi`
- [ ] Configure the optional TestPyPI publisher for `publish-testpypi.yml` and environment `testpypi`.
- [x] Add the owner-approved private security reporting contact to `SECURITY.md`.
- [x] Push the reviewed source and `.github/readme-banner.png` to the public canonical repository before uploading 0.2.0;
  the PyPI README loads its banner from that absolute GitHub URL.

## Candidate validation

- [x] Update `_version.py` and `CHANGELOG.md` for 0.2.1; confirm no unintended manifest format change.
- [x] Run `python scripts/release_check.py` on a clean connected host.
- [x] Review the printed wheel/sdist SHA-256 values and archive inventories.
- [x] Confirm the wheel is `py3-none-any`, contains `py.typed` and schemas, and contains no tests, caches, credentials,
  third-party packages, uv binaries, or project manifests.
- [x] Install the exact wheel locally and verify `import depfix`, `depfix --help`, `depfix --version`, uv discovery, one live
  import, and one export/install/offline run.
- [x] Confirm CI passes Windows, macOS, Linux, supported Python versions, minimum uv, current uv, build, and clean-wheel jobs.

## Published releases

### 0.2.1 — 2026-08-01

- PyPI: `https://pypi.org/project/depfix/0.2.1/`
- GitHub: `https://github.com/agent0ai/depfix/releases/tag/v0.2.1`
- Wheel SHA-256: `41e7ae9208bd488e398c574d4c15b0ebcd90b0a08f7383dd5087ab9242303067`
- Sdist SHA-256: `84ff65118d5de0c647fc6c91a18b62f300fb366b9c3e2b0e76d21a165fe038da`
- Published from commit `7bb120deb06a14fd77982fac19987e37d6006801` after both the branch and tag cross-platform
  CI matrices passed, including Windows 3.13, followed by the isolated Trusted Publishing workflow.
- Verified through a clean public-index installation, CLI version check, and live `idna==3.10` runtime import.

### 0.2.0 — 2026-08-01

- PyPI: `https://pypi.org/project/depfix/0.2.0/`
- GitHub: `https://github.com/agent0ai/depfix/releases/tag/v0.2.0`
- Wheel SHA-256: `e27edc6d1b9b9ce323d0356c623fbb1ce5828c3ff279b875be6aca103ee867ba`
- Sdist SHA-256: `453dff9dcc6284a21dfd68d55efda760d9bd81dda0ad4397a41de96dc4877dec`
- Published from commit `3781777608545b2d3985e550242e3076910d4a26` after the complete cross-platform CI matrix and
  isolated Trusted Publishing workflow passed.
- Verified through a clean public-index installation, CLI version check, and live `idna==3.10` runtime import.
- A separate tag-triggered CI rerun later exposed the transient Windows cache-lock race fixed by `0.2.1`; use `0.2.1` or
  later on Windows.

### 0.1.0 Alpha — 2026-07-31

- PyPI: `https://pypi.org/project/depfix/0.1.0/`
- Wheel SHA-256: `1c4a1a16923a66db7d5c716def504b3917cc04d392231a826c240ef7c2508bc3`
- Sdist SHA-256: `b409bf4725dc1cb9c9a7c5a6461c8365207a7cebb2d46822730e300d6f2b4a67`
- Published through an owner-authorized upload after the full local release gate; a clean public-index install and both
  public file hashes were verified.
- The package was published before this workspace gained Git history, so no matching tag or repository-host release
  accompanied the original upload.

## TestPyPI (explicit manual workflow)

- [ ] Invoke `Publish TestPyPI` manually for the reviewed commit.
- [ ] Install from TestPyPI while sourcing dependencies from PyPI, then repeat CLI/live/prepared checks.
- [ ] Verify rendered metadata, README, files, and dependency declarations.

## PyPI (GitHub Release workflow)

- [x] Confirm CI is green for the reviewed commit and create its deliberate signed/annotated `vX.Y.Z` tag.
- [x] Publish a GitHub Release for that tag; this starts the `Publish to PyPI` workflow from `publish-pypi.yml`.
- [x] Approve the protected `pypi` environment deployment after reviewing the build-and-test job.
- [x] Verify the PyPI page, artifact hashes, `pip install depfix`, `depfix --version`, uv installation, and a basic live import.
