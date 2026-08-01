# Compatibility boundaries

Supported release targets are CPython 3.11–3.13 on x64 Linux, macOS, and Windows, with Python 3.13 coverage on arm64 Linux,
macOS, and Windows. Manifests are specific to implementation, minor version, ABI, platform, and architecture.

Pure-Python wheels, modules, namespace packages, resources, relative/circular imports, `importlib.import_module`,
`importlib.util.find_spec`, `importlib.resources`, selected `importlib.metadata`, and `pkgutil.get_data` are realm aware.
Persistent defaults and temporary contexts use ordinary Python import syntax; temporary selections are isolated across
threads and asynchronous tasks through context-local scope state. Code that compares synthetic
`__name__` values to hard-coded logical names should instead use `__depfix_logical_name__`.

Mixed wheels may execute their pure-Python root. Any attempt to load an unknown native module is rejected; optional native
accelerators can therefore fall back to Python when the package handles `ImportError`. Required native extensions still
need an application-owned process. Arbitrary plugin discovery, binary ABI co-loading, and extension modules that require
their original initialization name are not claimed to work in-process.

Setuptools' `distutils` compatibility import is redirected to the selected package's own `setuptools._distutils`. Imports
of dependencies bundled under `setuptools._vendor` also stay inside that selected wheel, while an explicitly declared
dependency always wins. The compatibility adapter preserves synthetic module identity while satisfying setuptools'
logical module-prefix checks. A versioned setuptools import therefore does not fall back to ambient installation state.

Ambient third-party packages are intentionally invisible inside a realm unless declared by artifact metadata. Standard
library modules remain shared process modules. Conventional `depfix pip` environments are separate from realm resolution.

Editors do not execute Python's context-sensitive import dispatcher. Generated `depfix_imports` aliases provide exact
versioned stubs for imports inside scopes and decorated functions. A generated search-path overlay covers one persistent
default selection; arbitrary scope-sensitive inference still requires aliases or an IDE extension.
