# Source grammar

Bare strings are PEP 508 requirements and default to PyPI. Depfix uses `packaging` for names, extras, markers, PEP 440
versions, and direct references.

| Form | Meaning |
| --- | --- |
| `requests>=2.31,<3` | PyPI requirement |
| `pypi:requests[socks]~=2.32` | Explicit PyPI shorthand |
| `git:https://host/org/repo.git@tag` | Git repository/ref |
| `git:ssh://git@host/org/repo.git#ref=main&subdirectory=python` | Git with fragment options |
| `url:https://host/package.whl#sha256=...` | Remote wheel or source archive |
| `file:../project`, `file:./wheel.whl`, `file:./module.py` | Local project/artifact/module |
| `py:https://host/module.py#sha256=...` | One remote Python module |

Standard `name @ git+https://...`, `name @ https://...`, and `name @ file:///...` references normalize into the same
source model. Git parsing separates a suffix ref from HTTPS/SSH authentication without splitting at the first `@`.

Relative `file:` values discovered by export are based on the containing source file. Direct runtime calls use the current
working directory. Local projects and source archives are built through uv in a temporary directory and promoted by the
resulting wheel hash. Source archives reject traversal, links/devices, duplicate paths, and extraction-limit violations.

Remote frozen artifacts require SHA-256. HTTPS is the supported remote artifact transport. Redirect origins are recorded
without credentials. Single-file sources cannot fetch siblings; use a wheel for relative imports.

Index credentials do not belong in specifiers. Configure indices through Python, CLI, environment, `.depfix/config.toml`,
and uv-compatible authentication. Manifests retain sanitized index/source provenance only.

Depfix keeps valid PEP 440 `Requires-Python` metadata unchanged. For legacy index or wheel metadata only, it repairs a
numeric release-prefix wildcard used with an ordering operator by translating the prefix interval to its exact bound:
`>3.4.*` becomes `>=3.5`, `>=3.4.*` becomes `>=3.4`, `<3.4.*` becomes `<3.4`, and `<=3.4.*` becomes `<3.5`.
Valid `==3.4.*` and `!=3.4.*` remain unchanged. Non-numeric, compatible-release, local-version, or otherwise ambiguous
malformed specifiers remain errors.

Optional export policy is configured in `.depfix/config.toml`:

```toml
[policy]
allowed-hosts = ["pypi.org", "files.pythonhosted.org", "packages.example.com"]
allowed-indexes = ["https://pypi.org/pypi"]
allow-insecure-transport = false
```

Host patterns may be exact names or `*.example.com`. The policy is checked for configured indexes, source URLs, artifact
URLs, and HTTP redirects and is serialized into the manifest. Depfix configures uv with `first-index` semantics; uv's own
metadata requests remain governed by uv and the configured index endpoints.
