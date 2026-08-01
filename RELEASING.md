# Release checklist

No command or workflow in this repository publishes automatically. PyPI already contains Depfix 0.1.0; every later release
requires an explicit owner-run workflow. Configure the protected-environment approvals listed below before using it.

## Owner-controlled blockers

- [x] Add the owner-approved MIT `LICENSE` and SPDX PEP 621 license metadata.
- [x] Record `agent0ai` as project owner in package metadata.
- [x] Record the owner-specified canonical source, documentation, issue, and changelog URLs.
- [ ] Apply [the prepared GitHub About metadata](.github/REPOSITORY_METADATA.md) in repository settings.
- [x] Confirm normalized name `depfix` is owned by the published functional `0.1.0` Alpha release.
- [ ] Configure protected `testpypi` and `pypi` repository environments with required reviewers.
- [ ] Configure matching TestPyPI/PyPI trusted publishers for the workflow and environment names.
- [x] Add the owner-approved private security reporting contact to `SECURITY.md`.
- [ ] Push the reviewed source and `.github/readme-banner.png` to the public canonical repository before uploading 0.2.0;
  the PyPI README loads its banner from that absolute GitHub URL.

## Candidate validation

- [x] Update `_version.py` and `CHANGELOG.md` for 0.2.0; confirm no unintended manifest format change.
- [x] Run `python scripts/release_check.py` on a clean connected host.
- [x] Review the printed wheel/sdist SHA-256 values and archive inventories.
- [x] Confirm the wheel is `py3-none-any`, contains `py.typed` and schemas, and contains no tests, caches, credentials,
  third-party packages, uv binaries, or project manifests.
- [x] Install the exact wheel locally and verify `import depfix`, `depfix --help`, `depfix --version`, uv discovery, one live
  import, and one export/install/offline run.
- [ ] Confirm CI passes Windows, macOS, Linux, supported Python versions, minimum uv, current uv, build, and clean-wheel jobs.

## Published releases

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

## PyPI (explicit manual workflow)

- [ ] Create and push the deliberate signed/annotated release tag according to owner policy.
- [ ] Invoke `Publish PyPI` manually with that tag and pass the protected-environment approval.
- [ ] Verify the PyPI page, artifact hashes, `pip install depfix`, `depfix --version`, uv installation, and a basic live import.
- [ ] Create the repository-host release from the same tag and changelog entry.
