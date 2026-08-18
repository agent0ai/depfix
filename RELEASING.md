# Release checklist

Production releases start only when an owner manually dispatches `Publish to PyPI` from an existing annotated `vX.Y.Z`
tag. A push or tag alone never publishes. The workflow validates the tag at the current `main`, runs the complete reusable
CI and distribution gate without write or OIDC permission, and stages a hidden draft with the checked artifacts only after
every check passes. The protected `pypi` environment then authorizes a separate OIDC upload job. A clean public-index
verification makes the GitHub Release public. A failed upload removes the draft; a verification failure after a successful
upload preserves the exact checked draft so failed jobs can be retried safely. The records below are the publication
authority for reviewed artifacts.

## Owner-controlled blockers

- [x] Add the owner-approved MIT `LICENSE` and SPDX PEP 621 license metadata.
- [x] Record `agent0ai` as project owner in package metadata.
- [x] Record the owner-specified canonical source, documentation, issue, and changelog URLs.
- [ ] Apply [the prepared GitHub About metadata](.github/REPOSITORY_METADATA.md) in repository settings.
- [x] Confirm normalized name `depfix` is owned by the published functional `0.1.0` Alpha release.
- [x] Configure the protected `pypi` repository environment with deployment tags restricted to `v*`; publication remains
  manually dispatched from a checked annotated tag, so no additional required-reviewer confirmation is needed.
- [x] Add the production Trusted Publisher under PyPI project `depfix` → Publishing with these exact values:
  - Owner: `agent0ai`
  - Repository: `depfix`
  - Workflow filename: `publish-pypi.yml`
  - Environment: `pypi`
- [ ] Configure the optional TestPyPI publisher for `publish-testpypi.yml` and environment `testpypi`.
- [x] Add the owner-approved private security reporting contact to `SECURITY.md`.
- [x] Push the reviewed source and `.github/readme-banner.png` to the public canonical repository before publishing;
  the PyPI README loads its banner from that absolute GitHub URL.

## Candidate validation

- [ ] Update `_version.py` and add a dated `CHANGELOG.md` section for the exact stable `X.Y.Z` version.
- [ ] Run `python scripts/release_check.py` on a clean connected host.
- [ ] Review the printed wheel/sdist SHA-256 values and archive inventories.
- [ ] Confirm the wheel is `py3-none-any`, contains `py.typed` and schemas, and contains no tests, caches, credentials,
  third-party packages, uv binaries, or project manifests.
- [ ] Install the exact wheel locally and verify `import depfix`, `depfix --help`, `depfix --version`, uv discovery, one live
  import, and one export/install/offline run.
- [ ] Commit and push the reviewed source to `main`, then create and push an annotated `vX.Y.Z` tag at that exact commit.
- [ ] Confirm ordinary tag CI is green; the production workflow will independently rerun every required job.
- [ ] Run `python scripts/release.py X.Y.Z`. This read-only preflight verifies the clean checkout, local and remote
  `main`, local and remote annotated tag, source version, changelog, and unused GitHub/PyPI destinations.

## Published releases

### 0.11.4 — 2026-08-18

- PyPI: `https://pypi.org/project/depfix/0.11.4/`
- GitHub: `https://github.com/agent0ai/depfix/releases/tag/v0.11.4`
- Wheel SHA-256: `3db10454ea7a5b81773986d70b9f6d6bb119e89862e40861d90561b380bd4731`
- Sdist SHA-256: `f0fba8d84aaf1a1ddd0ae4c0496d1347c660de45babf1ab22ea2df95b221db0b`
- Published from commit `c83d4f9a5fa1809310bd6a2903a39f35e30544ac` by protected workflow run
  `https://github.com/agent0ai/depfix/actions/runs/32181142178` after its tag-bound request, complete quality and platform
  matrix, distribution, hidden-draft staging, OIDC publication, public-index verification, and release finalization gates
  passed on attempt 1.
- PyPI and GitHub expose exactly the wheel and source archive byte for byte. Both PyPI attestations bind their artifact
  subjects and digests to `agent0ai/depfix`, `publish-pypi.yml`, annotated `v0.11.4`, the release commit, the protected
  `pypi` environment, and workflow attempt 1. A credential-free no-cache public-index installation verified 0.11.4,
  fetched the exact `liteparse==2.0.3` AArch64 wheel with SHA-256
  `1dd3816c7fadd20f77a7697e8511707033caadceb13d9452cf784e9b0626a2cc`, materialized its omitted 7,644,440-byte
  `liteparse/libpdfium.so`, and reverified the complete 12-file Depfix-owned payload manifest.

### 0.11.3 — 2026-08-18

- PyPI: `https://pypi.org/project/depfix/0.11.3/`
- GitHub: `https://github.com/agent0ai/depfix/releases/tag/v0.11.3`
- Wheel SHA-256: `65d1010aa0aec5f612170fd4a57311a2f3f8b5cb8727aee479cbd7a9b74add53`
- Sdist SHA-256: `92c51ad112fcdcea566c227f9ba60d2b6280cf8ac14e8f9fecd350e06c0a7257`
- Published from commit `c6f2a4b19eac7a1101677a63a6327d29562f9bd3` by protected workflow run
  `https://github.com/agent0ai/depfix/actions/runs/32165460875` after its tag-bound request, complete quality and platform
  matrix, distribution, hidden-draft staging, OIDC publication, public-index verification, and release finalization gates
  passed on attempt 1.
- PyPI and GitHub expose exactly the wheel and source archive byte for byte. Both PyPI attestations bind their artifact
  subjects and digests to `agent0ai/depfix`, `publish-pypi.yml`, annotated `v0.11.3`, the release commit, the protected
  `pypi` environment, and workflow attempt 1. A credential-free no-cache public-index installation verified 0.11.3,
  built and imported `tinysegmenter==0.3`, and found no filesystem links in the fresh Depfix cache.

### 0.11.2 — 2026-08-18 (superseded before PyPI publication)

- Tag CI exposed Windows-specific ZIP backslash-name normalization and a POSIX-only executable-bit assertion. Validation
  failed before distribution staging, OIDC, or PyPI publication; 0.11.3 contains the correction without moving the
  immutable tag.

### 0.11.1 — 2026-08-18

- PyPI: `https://pypi.org/project/depfix/0.11.1/`
- GitHub: `https://github.com/agent0ai/depfix/releases/tag/v0.11.1`
- Wheel SHA-256: `0f7ddbbb8fc56b59e5ca28426bf3e5e54bbd4d587dc899ff5f4635102cd6aaf4`
- Sdist SHA-256: `7309782d250045702735f6845b5b09e5d6e2df781bea793706ad87fb2633ec64`
- Published from commit `3f91c3cf09e3d6b4b7ffb737e4e8550644a0d8dc` by protected workflow run
  `https://github.com/agent0ai/depfix/actions/runs/32147276913` after its tag-bound request, complete quality and platform
  matrix, distribution, hidden-draft staging, OIDC publication, public-index verification, and release finalization gates
  passed on attempt 1.
- PyPI and GitHub expose exactly the wheel and source archive byte for byte. Both PyPI attestations bind their artifact
  subjects and digests to `agent0ai/depfix`, `publish-pypi.yml`, annotated `v0.11.1`, the release commit, the protected
  `pypi` environment, and the workflow attempt. A credential-free no-cache public-index installation verified version and
  import identity, then loaded Rich 15's dynamic Unicode table and returned `cell_len("👍") == 2`.

### 0.11.0 — 2026-08-18

- PyPI: `https://pypi.org/project/depfix/0.11.0/`
- GitHub: `https://github.com/agent0ai/depfix/releases/tag/v0.11.0`
- Wheel SHA-256: `8e5dbabf441dc7b58198d99f89cf260b930a5005c2ae044a9ca4c95ef01d6df6`
- Sdist SHA-256: `5a0dc2c20289c1c0241d29767cb1909f2205b747555dccc74bb1970eecbd494d`
- Published from commit `52e7644bc25b336565c1e184c50b3709eff99607` by protected workflow run
  `https://github.com/agent0ai/depfix/actions/runs/32126539524` after its tag-bound request, complete quality and platform
  matrix, distribution, hidden-draft staging, OIDC publication, public-index verification, and release finalization gates
  passed. One Windows 3.11 concurrency-test timeout was rerun successfully before distribution staging; no publication
  state existed during that retry.
- PyPI and GitHub expose the protected run's wheel and source archive byte for byte. Their PyPI attestations bind both
  artifact digests to the release workflow, annotated `v0.11.0` tag, and published commit. A clean credential-free,
  no-cache public-index installation verified version/help and a live `idna==3.10` Depfix import after regional Simple
  API propagation completed.

### 0.10.3 — 2026-08-17

- PyPI: `https://pypi.org/project/depfix/0.10.3/`
- GitHub: `https://github.com/agent0ai/depfix/releases/tag/v0.10.3`
- Wheel SHA-256: `5f876399873bac31d0649bc5f3e28748c4e2df86169cc34876b16b4ea0b87887`
- Sdist SHA-256: `3ddc5c30757a8ea5c77075ab67d2233c6ad9593ea94a27a150060b22375618fc`
- Published from commit `4626a894bb25d8942ab3cd9ac71d1a1fd3a3efaa` by protected workflow run
  `https://github.com/agent0ai/depfix/actions/runs/32042472924` after its request, complete quality and platform matrix,
  distribution, hidden-draft staging, OIDC publication, public-index verification, and release finalization gates passed.
- PyPI and GitHub expose the protected run's retained wheel and source archive byte for byte. Their inventories exclude
  tests, caches, credentials, uv binaries, and DOX files. A clean credential-free public-index installation verified
  version/help, canonical normalization of the historical `>3.4.*, <4` metadata form, and preparation of the complete
  70-artifact `fastmcp==3.2.4` graph. The unrelated build-progress limitation remains unchanged.

### 0.10.2 — 2026-08-14

- PyPI: `https://pypi.org/project/depfix/0.10.2/`
- GitHub: `https://github.com/agent0ai/depfix/releases/tag/v0.10.2`
- Wheel SHA-256: `d23b5d98467e3cf9729d63a116280b3f72a04c54d57c9a836be59d0fb0a36f66`
- Sdist SHA-256: `443604260efb3ad8594d880da2015a1a8ce1a2950453cdda22ca2866fa6d2805`
- Published from commit `5534ddf12ebb2379eb459edc6f02af06ae0037bc` by protected workflow run
  `https://github.com/agent0ai/depfix/actions/runs/31795984090` after the complete quality, Linux, macOS, Windows,
  x64, arm64, Python 3.11–3.13, uv, distribution, draft-staging, OIDC publication, public-index, and finalization gates
  passed.
- PyPI and GitHub expose the exact same wheel and source archive byte for byte. Their inventories exclude tests, caches,
  credentials, uv binaries, and DOX files. A clean credential-free public-index installation verified version/help,
  requirements-file defaults from outside a project, patch/unpatch, automatic `depfix run` fallback, PEP 440 uninstall,
  and installed list/tree behavior. Release notes explicitly leave the uv compile/resolver optimization as research-only.

### 0.10.1 — 2026-08-14 (superseded before PyPI publication)

- Tag: `https://github.com/agent0ai/depfix/tree/v0.10.1`
- Hosted validation showed that one shared SQLite timeout could not both merge routine concurrent usage writes reliably
  and preserve bounded first-activation failure. Validation failed before distribution staging, OIDC, or PyPI
  publication; separate contention bounds are released as `0.10.2` without moving the immutable tag.

### 0.10.0 — 2026-08-14 (superseded before PyPI publication)

- Tag: `https://github.com/agent0ai/depfix/tree/v0.10.0`
- Hosted Windows validation exposed direct `file:C:/...` requirements-path parsing and concurrent SQLite usage-write
  failures. Validation failed before distribution staging, OIDC, or PyPI publication; the corrections are released as
  `0.10.1` without moving the immutable tag.

### 0.9.2 — 2026-08-13 (superseded before PyPI publication)

- Tag: `https://github.com/agent0ai/depfix/tree/v0.9.2`
- Linux, macOS, uv, quality, and distribution gates passed, including the non-root Python 3.13 recovery. The Windows
  matrix then exposed a test-fixture-only conversion of `file:///C:/...` into an invalid path. Validation failed before
  draft staging, OIDC, or PyPI publication; the platform-native fixture correction is released as `0.9.3` without moving
  the immutable tag.

### 0.9.1 — 2026-08-13 (superseded before PyPI publication)

- Tag: `https://github.com/agent0ai/depfix/tree/v0.9.1`
- The hosted non-root Python 3.13 gate exposed a second permission boundary when replacing an intentionally corrupted
  hardened target. Validation failed before draft staging, OIDC, or PyPI publication; complete tree permission recovery
  is released as `0.9.2` without moving the immutable tag.

### 0.9.0 — 2026-08-13 (superseded before PyPI publication)

- Tag: `https://github.com/agent0ai/depfix/tree/v0.9.0`
- The complete hosted gate exposed permission failures when removing hardened ephemeral inputs on Windows and when test
  fixtures intentionally corrupted read-only targets on non-root runners. Validation failed before draft staging, OIDC,
  or PyPI publication; the permission-safe correction is released as `0.9.1` without moving the immutable tag.

### 0.7.0 — 2026-08-06

- PyPI: `https://pypi.org/project/depfix/0.7.0/`
- GitHub: `https://github.com/agent0ai/depfix/releases/tag/v0.7.0`
- Wheel SHA-256: `e1503fe28f744a1b98e2db1d479efcd725b0c22a9fa7f7e92fe5b92ca4e38e40`
- Sdist SHA-256: `8c5cc54c38d5a09f14933a442975686daf02ba1283f9ef9a22d687ce46ee635e`
- Published from commit `6b308be97dad6b899c40e90ee3cdf4aad7989ed0` after the complete quality, distribution,
  Linux, macOS, Windows, x64, arm64, Python 3.11–3.13, and uv gates passed, including Windows 3.13 package-store
  provenance and dependency-tree coverage.
- Published through the protected `pypi` environment with OIDC Trusted Publishing. PyPI JSON exposed the exact files
  before its simple index had propagated; an independent clean install then verified `0.7.0`, and the GitHub Release was
  restored from the exact PyPI bytes after the workflow correctly withheld its draft. The follow-up workflow hardening
  retries simple-index propagation and preserves checked drafts after successful uploads.

### 0.6.0 — 2026-08-04

- PyPI: `https://pypi.org/project/depfix/0.6.0/`
- GitHub: `https://github.com/agent0ai/depfix/releases/tag/v0.6.0`
- Wheel SHA-256: `5df0c1a057786a397912952c98f2923c9f8bd0ff1d4419767584a75a1369837f`
- Sdist SHA-256: `fd9e48012f326805fd1edd1dd7697d6c3735f61fd0bb9eb341fce8c51f1e0b9c`
- Published from commit `75b82292656fbea1a74373b0f35a1d96d1d93420` after branch, annotated-tag, and production
  gates passed the complete quality, distribution, Linux, macOS, Windows, x64, arm64, Python 3.11–3.13, uv, live
  cross-version object-boundary, prepared/offline, and air-gap checks.
- Published through the protected `pypi` environment with OIDC Trusted Publishing. The workflow and an independent clean
  public-index install verified version `0.6.0`, the new boundary APIs, the exact two-file artifact set, matching public
  hashes, and the public GitHub Release.

### 0.5.0 — 2026-08-03

- PyPI: `https://pypi.org/project/depfix/0.5.0/`
- GitHub: `https://github.com/agent0ai/depfix/releases/tag/v0.5.0`
- Wheel SHA-256: `1e87bd728b90594471593ae856699ec29b763e92a0eea9ec1aebca10bea721a6`
- Sdist SHA-256: `d8c4a6e015b40eb3e50d6060c9ac6e317ad4a67ca693e302e60178b9ff47f49c`
- Published from commit `1b703a1c68e5ce9f6744f96e381728d647d262ac` after the complete hosted quality,
  distribution, Linux, macOS, Windows, x64, arm64, Python 3.11–3.13, and uv compatibility gates passed.
- Published through the protected `pypi` environment and verified by matching public artifact hashes and a clean CPython
  3.13 public-index install. GitHub visibility was completed through the API after the final CLI job lacked repository
  context; the follow-up workflow fix checks out source before future finalization and cleanup jobs.

### 0.4.1 — 2026-08-02

- PyPI: `https://pypi.org/project/depfix/0.4.1/`
- GitHub: `https://github.com/agent0ai/depfix/releases/tag/v0.4.1`
- Wheel SHA-256: `69f10ac5a7b62883c06edab5efa00ef8a1a9d9b372bad70c41ff5ebacdcc6123`
- Sdist SHA-256: `aa2eec7c9ef21de5347e78a58d3319f3cb8abfdc50c8d169800309100fb7b1c2`
- Published from commit `d906f280b3f257b43768798b1d3cde613199ceb7` after the hosted Python 3.13 gate caught
  and the patch fixed non-root removal of read-only cache targets.
- Verified through the protected `pypi` environment, PyPI release metadata, and a clean CPython 3.13 public-index install.

### 0.4.0 — 2026-08-02 (superseded before PyPI publication)

- GitHub: `https://github.com/agent0ai/depfix/releases/tag/v0.4.0`
- The pre-upload hosted gate exposed the non-root cache cleanup issue fixed in `0.4.1`; the upload job was skipped and
  PyPI never received `0.4.0`.

### 0.3.0 — 2026-08-02

- PyPI: `https://pypi.org/project/depfix/0.3.0/`
- GitHub: `https://github.com/agent0ai/depfix/releases/tag/v0.3.0`
- Wheel SHA-256: `002bb3c08a77ef1adef0af2005bb05d2d69b80a806268538b2a5e1cbbebb1179`
- Sdist SHA-256: `15419d58dcb677d82a350bb5c4cf791b3742f91dcbdb5c87de58bc0b6ec10116`
- Published from commit `2d45ad8c66810ff31f3fdc046ff809cdc6b6b15d` after branch and annotated-tag CI passed all
  quality, uv, distribution, Windows, macOS, Linux, x64, and arm64 jobs, including Windows 3.13 and live incompatible
  OpenAI imports.
- Published through the protected `pypi` environment after the isolated Trusted Publishing build-and-test job passed.
- Verified through PyPI release metadata and artifact digests, a clean public-index installation, CLI version check, and a
  live `idna==3.10` runtime import.

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

## PyPI (checked manual workflow)

- [ ] Expose a workflow-capable `GH_TOKEN` or `GITHUB_TOKEN` to the release process from the owner's secret manager. The
  helper does not read `.env`, write credentials, or print the token.
- [ ] Dispatch through the checked launcher:
  `python scripts/release.py X.Y.Z --dispatch --confirmation release-depfix-X.Y.Z`. The launcher can only submit the
  validated `vX.Y.Z` tag as the workflow ref.
- [ ] Open the resulting `Publish to PyPI` run and confirm its ref is `vX.Y.Z`, never `main`.
- [ ] Confirm request validation and the complete reusable CI matrix pass; any failure must leave GitHub Releases and PyPI
  unchanged.
- [ ] Confirm the workflow stages a hidden draft with only the checked wheel and sdist attached.
- [ ] Confirm the protected `pypi` deployment starts only after the completed checks and exact draft assets are staged.
- [ ] Confirm OIDC publishing and the clean public-index installation job pass, then confirm the GitHub Release becomes
  public. A failed upload must remove its unpublished draft; a post-upload verification failure must retain the checked
  draft for a failed-job retry.
- [ ] Record the PyPI/GitHub URLs, public artifact hashes, tagged commit, and verification result above.

## Recovery after a successful PyPI upload

Use recovery only when `Publish checked distributions to PyPI` succeeded but public-index verification or GitHub Release
finalization did not. The production workflow retains its checked draft in this state. Retry the failed jobs first; if the
original run cannot be resumed, dispatch the recovery workflow from the same annotated tag:

```bash
gh workflow run recover-pypi-release.yml --ref vX.Y.Z \
  -f version=X.Y.Z -f confirmation=recover-depfix-X.Y.Z
```

`Recover PyPI release` has no OIDC permission and cannot upload to PyPI or create replacement artifacts. It requires the
exact two expected files on PyPI, waits for a clean simple-index install, downloads the retained GitHub draft, compares
both filenames and SHA-256 digests byte for byte, and only then makes the draft public. It fails closed when the draft is
missing, already public, or different from PyPI. Do not use it after a pre-upload or upload failure; fix the candidate and
make a new version instead.
