# Basic environment debugger

This example exercises Depfix from the same interpreter and environment you want to diagnose. It prints runtime identity,
loads two `idna` versions side by side, checks warm module identity, and inspects a lazy `PackageHandle` for `packaging`.

From the repository root:

```bash
python -m pip install -e .
python examples/debug_basic/application.py --cache-dir tmp/depfix-debug-cache
```

Add `--extended` to exercise a standalone `six` module plus `pytz` package/submodule and timezone-resource access:

```bash
python examples/debug_basic/application.py --extended --cache-dir tmp/depfix-debug-cache
```

Useful variations:

```bash
# Force fresh live resolution.
python examples/debug_basic/application.py --refresh --cache-dir tmp/depfix-debug-cache

# Confirm the prepared cache is sufficient without network access.
python examples/debug_basic/application.py --offline --cache-dir tmp/depfix-debug-cache

# Exercise an exact prepared graph.
python examples/debug_basic/application.py --manifest .depfix/imports.lock --offline
```

If a check fails, keep the traceback and add the redacted diagnostics from:

```bash
depfix --json doctor
```

The root `tmp/` directory is ignored by Git, pruned from source distributions, and rejected by the release archive check.
