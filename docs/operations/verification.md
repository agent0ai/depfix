# Verification

Local gates:

```bash
ruff format --check .
ruff check .
mypy src/depfix
pytest
python scripts/release_check.py
python scripts/validate_workflows.py
```

The test suite covers source normalization, Core Metadata/artifact discovery, zero-project single modules, lazy package
handles, module ambiguity/absence, conflicting transitive realms, namespace provider sets, relative/circular imports,
resources/metadata facades, strict native rejection, automatic shared native loading, scoped native reuse and public-owner
conflicts, scanner safety, settings precedence, hash/cache concurrency and resumable truncation, spawn workers,
deterministic manifests/bundles, offline bundle install, ordinary prepared interpreter startup, generated stubs, uv
discovery outside `PATH`, deterministic provenance/boundary guards, and opt-in live cross-version object probes for
published pure-Python packages.

The full release check first rejects tracked credentials, `.env` files, and OS metadata. It then builds wheel and sdist,
runs metadata checks, rejects forbidden archive contents and credential patterns, installs the exact wheel in a clean
environment, verifies import/CLI/uv, exercises the private uv bootstrap, runs live and prepared-offline smoke tests,
bootstraps a second environment from the exact bundled wheel set with `--no-index`, and prints exact artifact hashes for
manual publication. It also runs the repository-owned workflow validator, which parses every workflow and fails when
manual-only release triggers, least-privilege permissions, protected OIDC publishing, draft retention, or recovery
contracts expand unexpectedly. CI repeats the appropriate gates by operating system, architecture, Python, and uv
version.

After a reviewed annotated tag is pushed, `python scripts/release.py X.Y.Z` performs a read-only release preflight against
the clean checkout, local and remote `main`, local and remote tag, source version, changelog, GitHub Releases, and PyPI.
Adding `--dispatch --confirmation release-depfix-X.Y.Z` submits the production workflow with the validated tag as its ref;
the helper never sources `.env` or prints its `GH_TOKEN`/`GITHUB_TOKEN`. See [`RELEASING.md`](../../RELEASING.md) for the
owner approval and retained-draft recovery procedures.
