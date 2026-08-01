# Verification

Local gates:

```bash
ruff format --check .
ruff check .
mypy src/depfix
pytest
python scripts/release_check.py
```

The test suite covers source normalization, Core Metadata/artifact discovery, zero-project single modules, lazy package
handles, module ambiguity/absence, conflicting transitive realms, namespace provider sets, relative/circular imports,
resources/metadata facades, native-module rejection, scanner safety, settings precedence, hash/cache concurrency, spawn
workers, deterministic manifests/bundles, offline bundle install, ordinary prepared interpreter startup, generated stubs,
and uv discovery outside `PATH`.

The full release check first rejects tracked credentials, `.env` files, and OS metadata. It then builds wheel and sdist,
runs metadata checks, rejects forbidden archive contents and credential patterns, installs the exact wheel in a clean
environment, verifies import/CLI/uv, exercises the private uv bootstrap, runs live and prepared-offline smoke tests,
bootstraps a second environment from the exact bundled wheel set with `--no-index`, and prints exact artifact hashes for
manual publication. CI repeats the appropriate gates by operating system, architecture, Python, and uv version.
