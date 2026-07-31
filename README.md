<p align="center">
  <img src=".github/readme-banner.svg" alt="Depfix — isolated Python dependency realms" width="100%" />
</p>

<h3 align="center">Python dependencies solved. Use one package in multiple versions simultaneously. Install at runtime.</h3>

<p align="center">
  Depfix lets one Python process load multiple pure-Python package versions side by side—even when their transitive dependencies conflict.
</p>

<p align="center">
  <a href="https://pypi.org/project/depfix/"><img alt="PyPI" src="https://img.shields.io/pypi/v/depfix?style=for-the-badge&logo=pypi&logoColor=white" /></a>
  <a href="https://pypi.org/project/depfix/"><img alt="Python 3.11+" src="https://img.shields.io/pypi/pyversions/depfix?style=for-the-badge&logo=python&logoColor=white" /></a>
  <a href="https://github.com/agent0ai/depfix/actions/workflows/ci.yml"><img alt="CI" src="https://img.shields.io/github/actions/workflow/status/agent0ai/depfix/ci.yml?branch=main&style=for-the-badge&label=CI" /></a>
  <a href="https://github.com/sponsors/agent0ai"><img alt="Sponsor agent0ai" src="https://img.shields.io/badge/Sponsor-agent0ai-FF69B4?style=for-the-badge&logo=githubsponsors&logoColor=white" /></a>
</p>

<p align="center">
  <a href="#quick-start">Quick start</a> ·
  <a href="#why-depfix">Why Depfix</a> ·
  <a href="#from-live-imports-to-locked-deployments">Deployment</a> ·
  <a href="#documentation">Documentation</a> ·
  <a href="https://github.com/agent0ai/depfix/issues">Issues</a>
</p>

```python
from depfix import import_module

idna_2 = import_module("idna==2.10")
idna_3 = import_module("idna==3.10")

assert idna_2.__depfix_version__ == "2.10"
assert idna_3.__depfix_version__ == "3.10"
assert idna_2 is not idna_3
```

No virtual-environment switching. No `sys.path` swapping. No installation into the active `site-packages`.

## Why Depfix

| Capability | Why it matters |
| --- | --- |
| **Side-by-side versions** | Load different versions of the same pure-Python distribution in one process. |
| **Dependency realms** | Each root keeps parent-specific dependency edges, so one graph does not flatten into another. |
| **Zero-configuration start** | Call `import_module()` directly; the first request prepares an exact graph in the user cache. |
| **Reproducible deployments** | Export deterministic manifests, install frozen state, and build complete air-gap bundles. |
| **Real package sources** | Resolve PyPI requirements, Git refs, URLs, local projects, wheels, and standalone Python files. |
| **Honest isolation** | Pure-Python realm loading is supported; unsafe native-extension loading fails explicitly. |

See the runnable [AWS CLI and Boto3 dependency-conflict example](https://github.com/agent0ai/depfix/tree/main/examples/conflicting_botocore_versions)
for two incompatible Botocore graphs operating in one Python process.

## Quick start

Install Depfix from PyPI:

```bash
python -m pip install depfix
```

Then ask for the package version your code needs:

```python
from depfix import import_module, load_package

requests = import_module("requests>=2.31,<3")
tools = load_package("setuptools==75.0.0")

print(requests.__depfix_version__)
print(tools.name, tools.version, tools.module_names)
setuptools = tools.modules.setuptools
```

`import_module()` returns exactly one canonical module. If a distribution exposes zero or several public roots, Depfix
raises a typed discovery error instead of guessing. Use `module=` to select a known root or `load_package()` to inspect a
lazy package handle.

Importing `depfix` itself performs no resolution, network, cache, or subprocess work. Those begin only when a load or
preparation API is called.

## How it works

```text
requirement or source
  → exact uv-backed resolution
  → hash-pinned artifact graph
  → verified, content-addressed cache
  → parent-specific dependency realm
  → canonical synthetic module identity
```

Realm modules live under graph- and node-qualified internal names. Their logical imports are resolved through declared
dependency edges, not the process's ambient third-party packages. Repeated calls for the same graph and logical module
return the same module object.

Depfix invokes uv through its documented executable interface. It does not import uv internals, vendor a uv binary, alter
the active environment, or add prepared package trees to global `sys.path`.

## Sources

```python
import_module("requests>=2.31,<3")
import_module("pypi:requests[socks]~=2.32")
import_module("git:https://github.com/acme/sdk.git@v2.4.0")
import_module("url:https://packages.example/acme_sdk-2.4.0-py3-none-any.whl#sha256=<digest>")
import_module("file:../acme-sdk")
import_module("file:./helpers.py")
import_module("py:https://modules.example/utilities.py#sha256=<digest>")
```

Standard PEP 508 direct references work too. Mutable Git refs are pinned to commits during export. Credentials remain in
external uv, index, keyring, or Git configuration and are never serialized into manifests.

## From live imports to locked deployments

Live mode is ideal for exploration: it resolves into the platform cache and creates no project files. When the graph must
be reproducible, prepare it explicitly:

```bash
depfix export . --output .depfix/imports.lock
depfix install .depfix/imports.lock --frozen
DEPFIX_FROZEN=1 python application.py
```

`export` scans static `import_module()` and `load_package()` calls without executing application code. The manifest records
exact artifacts, hashes, target identity, import ownership, source provenance, policy, and parent-specific edges.

For disconnected targets:

```bash
depfix bundle .depfix/imports.lock --output dist/application.depfixbundle --include-depfix-runtime
depfix install dist/application.depfixbundle --offline --frozen
```

| Mode | Resolution | Network | Project state |
| --- | --- | --- | --- |
| **Live** | On the first request | Allowed by policy | None |
| **Prepared** | During export | Optional during install | Deterministic manifest |
| **Air-gapped** | On the connected build host | Forbidden on target | Manifest + exact bundle |

## IDE support

```bash
depfix ide sync .depfix/imports.lock
depfix ide configure .depfix/imports.lock
```

Depfix generates a physical `depfix_imports` package with graph-specific stubs, editor snippets, and source maps. Distinct
aliases keep distinct version-specific APIs while runtime loading continues to use canonical realm identities.

## Boundaries worth knowing

Depfix is an alpha release focused on CPython 3.11–3.13. Its isolation boundary prevents dependency-graph collisions; it
is not a sandbox for untrusted Python code.

Pure-Python wheels, namespace packages, resources, relative and circular imports, selected metadata access, threads, and
spawn workers are covered. Native extensions can carry process-global ABI and library state that cannot be made safe by
renaming a module. Depfix rejects unknown native loading with `NativeIsolationRequired`; use an application-owned worker
process when native code is required.

## Documentation

| I want to… | Start here |
| --- | --- |
| Get a project running | [Getting started](https://github.com/agent0ai/depfix/blob/main/docs/guides/getting-started.md) |
| Understand dependency isolation | [Import realms](https://github.com/agent0ai/depfix/tree/main/docs/concepts/import-realms) |
| Follow resolution and package discovery | [Resolution](https://github.com/agent0ai/depfix/tree/main/docs/concepts/resolution) |
| Prepare containers or offline systems | [Deployment](https://github.com/agent0ai/depfix/tree/main/docs/concepts/deployment) |
| Inspect the Python surface | [Python API](https://github.com/agent0ai/depfix/blob/main/docs/reference/api.md) |
| Inspect every command | [CLI reference](https://github.com/agent0ai/depfix/blob/main/docs/reference/cli.md) |
| Review security assumptions | [Threat model](https://github.com/agent0ai/depfix/blob/main/docs/operations/threat-model.md) |
| Diagnose a failure | [Troubleshooting](https://github.com/agent0ai/depfix/blob/main/docs/guides/troubleshooting.md) |

Browse the complete [documentation map](https://github.com/agent0ai/depfix/tree/main/docs).

## Created by Agent Zero

Depfix is an open-source project by [agent0ai](https://github.com/agent0ai), creator of
[Agent Zero](https://github.com/agent0ai/agent-zero), [Space Agent](https://github.com/agent0ai/space-agent), and
[DOX](https://github.com/agent0ai/dox).

- Found a bug or compatibility gap? [Open an issue](https://github.com/agent0ai/depfix/issues).
- Want to contribute? Read [CONTRIBUTING.md](https://github.com/agent0ai/depfix/blob/main/CONTRIBUTING.md).
- Preparing a release? Follow [RELEASING.md](https://github.com/agent0ai/depfix/blob/main/RELEASING.md).
- Want to support agent0ai's work? [Sponsor on GitHub](https://github.com/sponsors/agent0ai).

## License

Depfix is released under the [MIT License](https://github.com/agent0ai/depfix/blob/main/LICENSE).
