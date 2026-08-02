# Compatibility boundaries

Supported release targets are CPython 3.11–3.13 on x64 Linux, macOS, and Windows, with Python 3.13 coverage on arm64 Linux,
macOS, and Windows. Manifests are specific to implementation, minor version, ABI, platform, and architecture.

`auto` selects a mode from the requested node and its dependency closure. It uses a synthetic in-process realm when every
artifact is pure Python and guarded shared imports when any artifact is native or platform specific. Selection is based on
wheel inspection and tags, not package-name allowlists.

In-process realms support pure-Python wheels, modules, namespace packages, resources, relative/circular imports,
`importlib.import_module`, `importlib.util.find_spec`, `importlib.resources`, selected `importlib.metadata`, and
`pkgutil.get_data`. Persistent defaults and temporary contexts use ordinary Python syntax; temporary selections are
isolated across threads and asynchronous tasks through context-local state. Code that compares synthetic `__name__`
values to hard-coded logical names should instead use `__depfix_logical_name__`.

Explicit `inprocess` mode is strict by default. Mixed wheels may execute a pure-Python root, and optional accelerators may
fall back when the package handles `ImportError`, but loading a required native extension raises
`NativeIsolationRequired`. A trusted caller may explicitly accept reduced isolation with `allow_unsafe=True`; Depfix then
attempts the extension under its synthetic identity, and package/ABI limitations remain the caller's risk.

Shared mode imports verified artifacts under their ordinary logical names through process-global `sys.path` and
`sys.modules` behavior. This supports the common Deno-style use case where one native version is selected for the process.
Repeated compatible requests reuse the canonical module. Depfix rejects an incompatible second public owner instead of
silently returning the previously loaded version. Public and explicitly requested roots are checked exactly; private
top-level helpers use conventional best effort because Python environments may preload them before Depfix runs.

Shared mode does not claim parent-specific dependency realms, simultaneous native versions, reliable unloading, or
context-local version switching. `using()` can expose the first compatible native selection as scoped syntax sugar, but
the loaded modules keep process ownership after scope exit. Reusing that version is safe; selecting an incompatible one
requires a fresh application-owned worker. Package ABI requirements and external system libraries remain
package/platform compatibility boundaries.

Packages classified as `native-known-unsafe` are rejected with `UnsafePackageError` unless the request or process enables
`allow_unsafe`. That override is limited to the execution/isolation policy. It does not permit incompatible shared owners,
unverified artifacts, forbidden network access, or unavailable process isolation.

Setuptools' `distutils` compatibility import is redirected to the selected package's own `setuptools._distutils`. Imports
of dependencies bundled under `setuptools._vendor` also stay inside that selected wheel, while an explicitly declared
dependency always wins. The compatibility adapter preserves synthetic module identity while satisfying setuptools'
logical module-prefix checks. A versioned setuptools import therefore does not fall back to ambient installation state.

Ambient third-party packages are intentionally invisible inside an in-process realm unless declared by artifact metadata.
Standard library modules remain shared process modules. Shared mode instead follows conventional logical imports from
verified targets, subject to the public-owner checks above. Conventional `depfix pip` environments are separate from both
runtime modes.

Editors do not execute Python's context-sensitive import dispatcher. Generated `depfix_imports` aliases provide exact
versioned stubs for imports inside scopes and decorated functions. A generated search-path overlay covers one persistent
default selection; arbitrary scope-sensitive inference still requires aliases or an IDE extension.
