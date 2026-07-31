# Getting started

Install Depfix and call it directly:

```python
from depfix import import_module, load_package

http = import_module("requests>=2.31,<3")
tools = load_package("setuptools>=75,<76")
```

Live mode is sufficient for exploration. For tests or deployment:

```bash
depfix scan .
depfix export . --output .depfix/imports.lock
depfix check . --manifest .depfix/imports.lock
depfix install .depfix/imports.lock --frozen
DEPFIX_FROZEN=1 python application.py
```

Dynamic calls can be declared with `--include` or `[[dynamic]]` tables in `.depfix/config.toml`. Configure private indices
outside specifiers and keep authentication in uv/keyring/Git facilities. Use `depfix bundle` for an air-gapped target.

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
