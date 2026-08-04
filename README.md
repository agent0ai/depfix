<p align="center">
  <img src="https://raw.githubusercontent.com/agent0ai/depfix/main/.github/readme-banner.png" alt="Depfix — install and import any Python package version" width="100%" />
</p>


<p align="center">
  <a href="https://pypi.org/project/depfix/"><img alt="PyPI" src="https://img.shields.io/pypi/v/depfix?style=for-the-badge&logo=pypi&logoColor=white" /></a>
  <a href="https://pypi.org/project/depfix/"><img alt="Python 3.11+" src="https://img.shields.io/pypi/pyversions/depfix?style=for-the-badge&logo=python&logoColor=white" /></a>
  <a href="https://github.com/sponsors/agent0ai"><img alt="Sponsor agent0ai" src="https://img.shields.io/badge/Sponsor-agent0ai-FF69B4?style=for-the-badge&logo=githubsponsors&logoColor=white" /></a>
</p>

<h1>No more Python dependency pain!</h1>

<table>
  <thead>
    <tr>
      <th>The win</th>
      <th>What it means</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td><strong>🧩 No dependency conflicts</strong></td>
      <td>Every package keeps the dependency versions it needs.</td>
    </tr>
    <tr>
      <td><strong>🔀 Multiple versions together</strong></td>
      <td>Import two versions of the same package in one Python process.</td>
    </tr>
    <tr>
      <td><strong>⚡ Install at runtime</strong></td>
      <td>Packages are downloaded when your code first needs them, then cached.</td>
    </tr>
    <tr>
      <td><strong>🧹 No dependency files</strong></td>
      <td>No <code>requirements.txt</code> or dependency list in <code>pyproject.toml</code> is required.</td>
    </tr>
    <tr>
      <td><strong>🐍 Normal Python imports</strong></td>
      <td>Select a version, then keep writing ordinary <code>import package</code>.</td>
    </tr>
    <tr>
      <td><strong>🌱 Start with one line</strong></td>
      <td>No environment redesign or separate installation workflow.</td>
    </tr>
    <tr>
      <td><strong>🚀 Production ready</strong></td>
      <td>Built-in CLI and Python APIs let you preinstall or vendor packages for predictable deployments.</td>
    </tr>
  </tbody>
</table>

```python
import depfix

with depfix.using("requests==2.31.0"):
    import requests as requests_old

with depfix.using("requests==2.32.3"):
    import requests as requests_new

assert requests_old is not requests_new
```

That is the idea: put the version next to the import and let Depfix handle the rest.

No `requirements.txt` needed. No dependency list in `pyproject.toml`. No virtual-environment juggling because two packages
want different versions of the same dependency. Depfix downloads packages when they are first used, keeps them cached, and
makes sure each package continues using the dependencies it was installed with.

Your imports become the source of truth. Dependency conflicts stop being a project-wide problem.

## Your imports are the package manager

Install Depfix once:

```bash
python -m pip install depfix
```

Existing dependency files can populate the same shared store without touching the active environment:

```bash
depfix pip install requests==2.32.3 PyYAML==6.0.2
depfix pip install -r requirements.txt
```

This is Depfix installation, not an alias for `pip` or `uv pip`. The listed packages are resolved as one group, exact
artifacts are materialized in the Depfix store, and incompatible transitive versions can coexist. Requirement files may
include nested `-r` files, `-c` constraints, indexes, hashes, and local `-e` paths. Add `--prefer-newest` or `-U` when
newest-first selection matters more than compatible cache reuse.

Then choose whichever import style fits your code.

### Set a default version

Use `default()` when the rest of the file or application should import selected versions normally. You can select one or
several packages together:

```python
import depfix

depfix.default(
    "requests==2.32.3",
    "PyYAML==6.0.2",
)

import requests
import yaml

response = requests.get("https://raw.githubusercontent.com/pypa/pip/main/.pre-commit-config.yaml")
workflow = yaml.safe_load(response.text)
```

There is no separate install step. The first `default()` call prepares the requested packages, and later runs reuse the
cache. When dependency ranges overlap, Depfix prefers the newest compatible version already in that shared cache, so
packages selected together can reuse one copy. Pass `prefer_newest=True` to `default()`, `using()`, `import_module()`, or
`load_package()` when you explicitly want newest-first resolution instead.

### Use a version temporarily

Use `using()` when one part of your program needs a specific version:

```python
import depfix

with depfix.using("openai==0.7.0"):
    import openai as openai_0_7

with depfix.using("openai==0.28.1"):
    import openai as openai_0_28
```

The imported objects keep working after the block ends:

```python
with depfix.using("requests==2.31.0"):
    import requests as legacy_requests

response = legacy_requests.get("https://example.com")
```

`using()` also works as a function decorator:

```python
import depfix


@depfix.using("requests==2.31.0")
def fetch_with_legacy_requests(url: str):
    import requests

    return requests.get(url)
```

The selected version is active every time the function runs. Async functions work too.

### Import a package directly

Use `import_module()` when you want the module returned immediately:

```python
import depfix

requests = depfix.import_module("requests==2.32.3")
```

This is especially convenient for dynamic code:

```python
version = "2.32.3"
requests = depfix.import_module(f"requests=={version}")
```

Most packages expose one obvious import. If a package exposes several and you already know which ones you need, select
each with `module=`:

```python
import depfix

setuptools = depfix.import_module(
    "setuptools==75.0.0",
    module="setuptools",
)

pkg_resources = depfix.import_module(
    "setuptools==75.0.0",
    module="pkg_resources",
)
```

Use `load_package()` when you want to inspect package metadata or discover its available module names before importing:

```python
package = depfix.load_package("setuptools==75.0.0")

print(package.name, package.version)
print(package.module_names)
print(package.dependencies)
```

## Dependency conflicts just work

Imagine two packages that cannot be installed together conventionally:

```text
awscli==1.32.0  needs botocore==1.34.0
boto3==1.36.0   needs botocore>=1.36,<1.37
```

With Depfix, import both:

```python
import depfix

with depfix.using("awscli==1.32.0", "boto3==1.36.0"):
    import awscli.clidriver
    import boto3
```

Each package receives the Botocore version it needs. You do not have to pin the shared dependency, split the application,
or create another environment. See the runnable
[AWS CLI and Boto3 example](https://github.com/agent0ai/depfix/tree/main/examples/conflicting_botocore_versions).

## More than PyPI

The same APIs accept version ranges and common Python package sources:

```python
requests = depfix.import_module("requests>=2.31,<3")
requests_with_socks = depfix.import_module("pypi:requests[socks]~=2.32")
sdk = depfix.import_module("git:https://github.com/acme/sdk.git@v2.4.0")
local_package = depfix.import_module("file:../my-local-package")
helpers = depfix.import_module("file:./helpers.py")

module = depfix.import_module("url:https://packages.example/acme_sdk-2.4.0-py3-none-any.whl#sha256=<digest>")
```

Standard PEP 508 direct references work as well.

## Start simple, lock it later

For local development, just run your Python file. Depfix installs and caches packages as the code reaches them. It does
not create project files.

When you want a repeatable deployment, Depfix can scan the same imports and prepare everything in advance:

```bash
depfix export . -o .depfix/imports.lock
depfix install .depfix/imports.lock --frozen
python application.py
```

This is optional. You can start with one import and add deployment controls only when you need them. Offline bundles,
containers, and generated IDE aliases are also available.

## The shared cache cleans itself

Depfix reuses one package store across projects, repositories, and working directories. It records when each exact package
artifact was installed and when Depfix last imported it. Once per day, a lightweight background check removes packages
that have gone unused for 30 days. The graph being prepared and packages held by active Depfix runtimes are always
protected, so returning to an older project does not delete and immediately reinstall its own dependencies.

Inspect or clean the store explicitly from the CLI:

```bash
depfix cache list
depfix cache cleanup --days 30
depfix cache remove requests --version 2.31.0
```

The same operations are available as `depfix.list_cached_packages()`, `depfix.cleanup_cache()`, and
`depfix.remove_cached_package()`. Change the retention window or disable automatic cleanup centrally:

```python
depfix.configure(cache_retention_days=60, cache_auto_cleanup=False)
```

## Good to know

- Importing `depfix` alone does nothing expensive. Installation starts only when you call a loading function.
- Automatic cache cleanup defaults to packages unused for 30 days and runs off the import path in a background sweep.
- Package preparation is shown on stderr, so you can see what is happening. Set `DEPFIX_LOG_LEVEL=WARNING` for quiet mode.
- Depfix supports pure-Python and native wheel packages on CPython 3.11–3.13. Automatic mode isolates pure dependency
  graphs and loads native graphs through guarded, conventional Python imports.
- Isolated versions keep [separate class identities](https://github.com/agent0ai/depfix/blob/main/docs/concepts/import-realms/compatibility.md#objects-crossing-version-boundaries).
  Keep library-owned objects within their version, or translate them through an agreed primitive or serialized boundary
  before another version consumes them. Use `realm_of()`, `assert_same_realm()`, or `enforce_same_realm()` to make known
  crossings fail immediately with producer and consumer versions.
- A native package can own one compatible public import version per process. Reusing it is idempotent; requesting an
  incompatible second owner raises a clear error instead of silently returning the wrong version.
- `using()` works as scoped syntax sugar for the first compatible native version. That version remains loaded as the
  process owner after the scope exits; use a worker when you need to switch or overlap native versions.
- Unsafe package classifications and strict in-process native loading are denied by default. Trusted callers can opt in
  per request with `allow_unsafe=True` or process-wide with `depfix.configure(allow_unsafe=True)`.
- Depfix isolates pure dependency versions; it is not a sandbox for untrusted code.

## Documentation

- [Getting started](https://github.com/agent0ai/depfix/blob/main/docs/guides/getting-started.md)
- [Guard objects between versions](https://github.com/agent0ai/depfix/blob/main/docs/guides/object-boundaries.md)
- [Python API](https://github.com/agent0ai/depfix/blob/main/docs/reference/api.md)
- [CLI reference](https://github.com/agent0ai/depfix/blob/main/docs/reference/cli.md)
- [Deployment](https://github.com/agent0ai/depfix/tree/main/docs/concepts/deployment)
- [Troubleshooting](https://github.com/agent0ai/depfix/blob/main/docs/guides/troubleshooting.md)

## Created by Agent Zero

Depfix is an open-source project by [agent0ai](https://github.com/agent0ai), creator of
[Agent Zero](https://github.com/agent0ai/agent-zero), [Space Agent](https://github.com/agent0ai/space-agent), and
[DOX](https://github.com/agent0ai/dox).

- Found a bug or compatibility gap? [Open an issue](https://github.com/agent0ai/depfix/issues).
- Want to contribute? Read [CONTRIBUTING.md](https://github.com/agent0ai/depfix/blob/main/CONTRIBUTING.md).
- Want to support agent0ai's work? [Sponsor on GitHub](https://github.com/sponsors/agent0ai).

## License

Depfix is released under the [MIT License](https://github.com/agent0ai/depfix/blob/main/LICENSE).
