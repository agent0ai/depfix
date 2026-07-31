# Contributing

Use CPython 3.11+ and install development dependencies:

```bash
python -m venv .venv
.venv/bin/python -m pip install -e '.[test,release]'
```

On Windows, use `.venv\Scripts\python.exe`. Run before submitting changes:

```bash
ruff format --check .
ruff check .
mypy src/depfix
pytest
python scripts/release_check.py --quick
```

Changes to source parsing, manifests, import identity, cache layout, native policy, or uv commands require focused tests and
documentation. Tests must not depend on ambient third-party imports. Never commit credentials, private package artifacts,
global cache content, `.depfix/runtime`, or generated build output.

Preserve the stable `import_module -> ModuleType` and `load_package -> PackageHandle` contracts. New native support must be
opt-in and evidence-based; do not weaken the unknown-native gate to make one package pass.

No contributor identity or certificate-of-origin policy has been selected. The owner should establish those governance
terms together with the license before accepting external contributions.
