# Getting started

Install Depfix and select packages for ordinary imports:

```python
import depfix

depfix.default("requests>=2.31,<3")
import requests

with depfix.using("requests==2.31.0"):
    import requests as legacy_requests
```

Use `depfix.import_module(...)` for an explicit dynamic module object and `depfix.load_package(...)` to inspect package
metadata or discover the import roots provided by a distribution.

Live mode is sufficient for exploration. For tests or deployment:

```bash
depfix scan .
depfix export . --output .depfix/imports.lock
depfix check . --manifest .depfix/imports.lock
depfix install .depfix/imports.lock --frozen
DEPFIX_FROZEN=1 python application.py
```

The scanner recognizes literal `default()` and `using()` calls, imported aliases, nested contexts, and decorated sync or
async functions. Dynamic calls can be declared with `--include` or `[[dynamic]]` tables in `.depfix/config.toml`.
Configure private indices outside specifiers and keep authentication in uv/keyring/Git facilities. Use `depfix bundle` for
an air-gapped target.

For multiprocessing spawn workers:

```python
from multiprocessing import get_context
from depfix import multiprocessing_initializer

pool = get_context("spawn").Pool(
    initializer=multiprocessing_initializer,
    initargs=("/app/.depfix/imports.lock",),
)
```

Commit application source and the deterministic manifest. Do not commit `.depfix/runtime`, global cache content, index
credentials, or generated editor attachments.
