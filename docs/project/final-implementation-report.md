# Final implementation report

## Architecture

Depfix separates immutable artifacts, parent-specific dependency nodes, public requests, and logical import modules. A
uv-backed resolver produces an exact graph; a content-addressed global cache verifies and prepares artifacts; the runtime
maps logical names into `_depfix.<graph>.<node>` identities without modifying `sys.path` or active `site-packages`.
Prepared manifests and `.depfixbundle` archives reuse the same graph/runtime implementation as zero-configuration calls.

The standard and explicit import contracts are:

```python
import depfix

depfix.default("requests>=2.31,<3")
import requests

with depfix.using("requests==2.31.0"):
    import requests as legacy_requests

dynamic_module = depfix.import_module("idna==3.10")
package = depfix.load_package("charset-normalizer==3.4.2")
```

`default()` maintains persistent ordinary-import selections. `using()` provides nested context-local selections and
function execution decorators. `import_module()` returns exactly one module or raises a typed discovery error;
`load_package()` returns a lazy `PackageHandle`.

## Production-phase file inventory

Package and schemas:

- `src/depfix/__init__.py`, `__main__.py`, `_version.py`, `aliases.py`, `cache.py`, `cli.py`, `config.py`, `dispatcher.py`,
  `errors.py`, `handles.py`, `manager.py`, `manifest.py`, `models.py`, `progress.py`, `project.py`, `resolver.py`, `runtime.py`,
  `scanner.py`, `scopes.py`, `settings.py`, `sources.py`, `specifiers.py`, `sync.py`, `uv_backend.py`, `wheel.py`, and
  `py.typed`;
- `src/depfix/schemas/depfix-manifest-v1.schema.json` and `schemas/depfix-manifest-v1.schema.json`.

Packaging, release, and automation:

- `pyproject.toml`, `MANIFEST.in`, `.gitignore`, `LICENSE`, and `scripts/release_check.py`;
- `.github/readme-banner.png`, `.github/REPOSITORY_METADATA.md`, and workflows `ci.yml`, `publish-testpypi.yml`, and
  `publish-pypi.yml`;
- `README.md`, `CHANGELOG.md`, `SECURITY.md`, `CONTRIBUTING.md`, and `RELEASING.md`.

Documentation and examples:

- the project-wide `AGENTS.md` hierarchy and `docs/README.md` documentation map;
- concept boundaries under `docs/concepts/` for artifacts/cache, deployment, import realms, manifests, and resolution;
- task guides under `docs/guides/`, interfaces under `docs/reference/`, operations under `docs/operations/`, research under
  `docs/research/`, and implementation records under `docs/project/`;
- runnable examples under `examples/conflicting_botocore_versions/`, `container/`, `debug_basic/`, and
  `two_idna_versions/`.

Verification:

- `tests/conftest.py`, `test_end_to_end.py`, `test_public_product.py`, `test_resolver_runtime.py`,
  `test_specifiers_lock_cache.py`, and `test_standard_imports.py`;
- `benchmarks/import_identity.py`.

The previous provisional package tree, generated prototype metadata, and obsolete repository configuration were removed.
Only narrow parser/invalidation checks for retired prototype formats remain, so users receive a migration error instead of
silent misinterpretation.

## Public workflows

Runtime and project API:

```python
import depfix
from depfix.project import create_bundle, export_project, install_manifest, scan_project, verify_manifest

depfix.configure(cache_dir="/var/cache/depfix")
depfix.default("requests>=2.31,<3")
with depfix.using("idna==3.10"):
    import idna
module = depfix.import_module("pypi:idna==3.10")
result = export_project(".", output=".depfix/imports.lock")
install_manifest(result.manifest, frozen=True)
create_bundle(result.manifest, "dist/application.depfixbundle", include_depfix_runtime=True)
```

CLI:

```bash
depfix export . -o .depfix/imports.lock
depfix install .depfix/imports.lock --frozen
depfix bundle .depfix/imports.lock --include-depfix-runtime -o dist/application.depfixbundle
depfix install dist/application.depfixbundle --offline --frozen
python application.py
```

Other supported surfaces include `prepare`, `scan`, `fetch`, `run`, `verify`, `check`, `tree`, `show`, `why`, `list`,
`doctor`, `migrate`, per-realm requirements export, IDE generation/attachment, cache operations, and `depfix pip` uv
passthrough.

## Cache and manifest

The default cache is `platformdirs.user_cache_path("depfix")/v1`; override its parent with `DEPFIX_CACHE_DIR` or
`depfix.configure(cache_dir=...)`. Important subtrees are `artifacts/sha256`, `targets`, `resolutions`, `manifests`,
`groups`, `metadata`, `ide`, `locks`, `tools/uv`, and `built-wheels`. Project state is `.depfix/imports.lock`; live loading
does not create project files.

Representative manifest fields:

```toml
format-version = 1
manifest-id = "sha256:<full graph digest>"

[resolver]
backend = "uv"
backend-version = "<executing compatible uv version>"

[[artifacts]]
id = "sha256:<wheel digest>"
distribution = "requests"
version = "2.32.5"
sha256 = "<wheel digest>"
size = 64738
source-kind = "pypi"
source-url = ""
source-final-url = ""
source-sha256 = ""
source-size = 0

[[nodes]]
id = "node_<lock-scoped identity>"
realm-id = "node_<lock-scoped identity>"
artifact = "sha256:<wheel digest>"
native-classification = "pure-python"
dependencies = { "idna" = "node_<child identity>" }

[[requests]]
alias = "requests"
specifier = "requests>=2.31,<3"
normalized-specifier = "requests<3,>=2.31"
api = "import_module"
module = "requests"
index-identity = "first-index:https://pypi.org/pypi"

[[groups]]
id = "group_<declaration identity>"
mode = "using-context"
specifiers = ["requests==2.31.0", "PyYAML==6.0.2"]
aliases = ["requests", "pyyaml"]
ordinary-imports = ["requests", "yaml"]
resolved-graph-ids = ["realm_<resolved identity>"]
```

Serialization is deterministic for the same graph. Parsing validates the full identity, target, hashes, references,
modules, policies, native classification, and absence of serialized credentials.

## Support matrix and uv

- CPython 3.11–3.13: x64 Linux, macOS, and Windows.
- CPython 3.13: arm64 Linux, macOS, and Windows CI runners.
- uv minimum: 0.11.0. CI and the release gate exercise both 0.11.0 and the current compatible release.
- uv is a mandatory distribution dependency, is found beside the active interpreter even when absent from `PATH`, and can
  be repaired into `.../v1/tools/uv/0.11.0/<platform>/` when policy allows network/bootstrap work.

Manifests are target-specific. The current CLI rejects cross-interpreter, cross-platform, and cross-architecture install
requests instead of pretending they are safe.

## Verification and artifacts

Commands used:

```bash
python -m ruff format --check .
python -m ruff check .
python -m mypy src/depfix
python -m pytest -q
python scripts/release_check.py
```

The full release check performs a clean PEP 517 build, Twine metadata validation, archive/credential inspection, clean-wheel
installation, uv discovery, live resolution, prepared offline startup, private uv bootstrap, and a fully air-gapped
`--no-index` runtime installation using the exact tested wheel. It prints the review hashes after every clean build and
never uploads.

Generated artifacts:

- `dist/depfix-0.2.0-py3-none-any.whl`
- `dist/depfix-0.2.0.tar.gz`

## Native and other known limitations

- Unknown/unsafe native modules are not made multiversion-safe. A mixed wheel's pure-Python code may fall back after
  `ImportError`, but loading its unknown native module raises `NativeIsolationRequired`.
- `isolation="process"` is reserved but a general RPC process backend is not implemented. Applications must own worker
  process boundaries for required native packages.
- One manifest currently describes one concrete host target; multi-target pure-Python bundles are not implemented.
- Generic analyzers cannot infer arbitrary scope-sensitive imports. Generated `depfix_imports` aliases/stubs are the exact
  scoped bridge, and one persistent default selection can use the generated type-search overlay; there is no custom
  language server.
- Source builds execute the selected build backend in a uv subprocess, not a security sandbox. Prefer exact wheels.
- Built Git/local/sdist outputs retain source and resulting-wheel provenance, but portable fresh-host deployment of those
  locally built wheels requires a bundle (or another accessible wheel store).
- Private authenticated indexes work best with a prepared cache or bundle. A fresh manifest-only host must have an
  externally usable artifact URL/authentication path; credentials are deliberately never serialized.
- Depfix enforces configured host/index policies on its own index, artifact, source, and redirect I/O. uv is constrained to
  configured indexes with `first-index` semantics, but uv's internal metadata transport remains governed by uv.
- SBOM generation and vulnerability-scanner adapters are not included in 0.2.0.

## Published alpha and owner follow-up

PyPI accepted `depfix 0.1.0` as an Alpha release on 2026-07-31. A clean environment installed it from the public index,
and PyPI reports the reviewed wheel SHA-256
`1c4a1a16923a66db7d5c716def504b3917cc04d392231a826c240ef7c2508bc3` and sdist SHA-256
`b409bf4725dc1cb9c9a7c5a6461c8365207a7cebb2d46822730e300d6f2b4a67`.

The public metadata records `agent0ai` as owner and `https://github.com/agent0ai/depfix` as the canonical repository. Depfix
is MIT licensed, and `pr@agent-zero.ai` is the private security contact. Remaining owner operations include GitHub About
metadata, protected trusted publishing, and release/tag administration.

The workflows still have no push/tag publication trigger. Future workflow publication requires explicit manual dispatch,
confirmation text, trusted-publisher configuration, and environment approval.
