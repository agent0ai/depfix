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

Dynamic `importlib.import_module()` lookups may address packaged children whose filename components are not valid Python
identifiers, such as `package.unicode17-0-0`. These names cannot appear in an `import` statement but CPython supports them
through `importlib`. Depfix resolves them only below an identifier import root in the selected, hash-verified artifact;
empty components, path separators, NULs, absolute paths, and traversal syntax remain invalid.

## Objects crossing version boundaries

Import isolation does not make library-defined objects interoperable. A class loaded in one realm has a different Python
identity from the same class loaded in another realm, even when the source and object layout did not change. A consumer
may reject the object with `isinstance`, silently choose a fallback path, or read fields that its own release added.

Live characterization tests use public APIs from immutable PyPI releases:

| Package pair | Cross-version boundary | Observed result | Risk shape |
| --- | --- | --- | --- |
| `packaging` 21.3 / 24.2 | Compare equal `Version("1.0")` values | Equality is false; ordering raises `TypeError` | Silent wrong result or immediate failure |
| `attrs` 21.4.0 / 24.2.0 | New `evolve()` consumes an old attrs instance | `Attribute.alias`, added after 21.4, is absent; the reverse direction succeeds | Directional representation change; immediate failure |
| `PyJWT` 2.10.0 / 2.10.1 | New `encode()` consumes an old `PyJWK` | The nominal type check misses and key preparation raises | Patch-version identity failure in an authentication path |
| `urllib3` 2.0.7 / 2.2.3 | New `Retry.from_int()` consumes an old `Retry` | The old object is stored as `total`; arithmetic fails later | Accepted malformed state; delayed failure |

The package implementations explain these outcomes:
[`packaging` comparisons require their own private base class](https://github.com/pypa/packaging/blob/24.2/src/packaging/version.py#L69-L110),
[`attrs.evolve()` reads `Attribute.alias`](https://github.com/python-attrs/attrs/blob/24.2.0/src/attr/_funcs.py#L438-L448),
PyJWT unwraps a key only after
[`isinstance(key, PyJWK)`](https://github.com/jpadilla/pyjwt/blob/2.10.1/jwt/api_jws.py#L164-L171), and urllib3's
[`Retry.from_int()` nominal check](https://github.com/urllib3/urllib3/blob/2.2.3/src/urllib3/util/retry.py#L270-L287)
otherwise accepts the value as a retry count. The executable evidence is in
[`tests/test_cross_version_objects.py`](../../../tests/test_cross_version_objects.py).

These four packages were deliberately selected to find failure modes; four failures out of four is not an ecosystem
failure-rate estimate. Severity is high when an unadapted boundary exists because the result can be silently wrong or
fail far from the handoff. Conditional likelihood depends on application architecture:

- no library object crosses between versioned graphs: not exposed;
- graphs exchange only agreed strings, bytes, numbers, standard-library values, or validated dictionaries: low;
- callbacks, plugin hooks, middleware, caches, or registries carry library-owned objects across graphs: medium to high;
- a known producer and consumer use different versions of the same library-owned class without an adapter: high.

Depfix provides opt-in diagnostics for the nominal cases it can observe. `realm_of(value)` reports the managed module that
owns a value's class. `assert_same_realm(consumer, value)` raises `RealmBoundaryError` before a foreign value reaches the
consumer. `enforce_same_realm()` applies that check to selected function parameters and optionally the return value,
including for async functions and nested builtin containers. The
[object-boundary guide](../../guides/object-boundaries.md) shows the adapter workflow.

Detection is intentionally conservative. Depfix does not traverse arbitrary object internals or infer provenance for an
application-owned class produced by a library decorator. A successful check proves only matching class provenance, not
semantic compatibility or safe data. Automatic conversion would need package-specific knowledge and could hide the same
invariant failures these checks are meant to expose.

Keep creation and consumption of library-owned objects in the same realm. When communication is required, define an
application-owned boundary and reconstruct the object in the receiving realm from a documented primitive form. Process
separation strengthens runtime isolation but still needs the same serialization contract; arbitrary pickle data is not a
safe compatibility or trust boundary.

Rich `Segment` values illustrate two separate boundaries. In Rich 15, `Console.render(Segment("hello"))` raises
`NotRenderableError` even in one ordinary environment: a segment is rendering output, not an accepted input renderable
(the exception text mentioning `Segment` is misleading). Pass a string or an object implementing `__rich_console__`, or
consume a segment sequence through Rich's segment/output APIs. Rich objects from different Depfix graphs remain a second,
independent nominal boundary even when both graphs select the exact same Rich wheel. Recreate the receiving Rich object
from application-owned text/style data instead of passing it between scopes; selecting the same version does not merge
realm identities.

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
verified targets, subject to the public-owner checks above. `depfix pip install` only prepares those verified targets in
the shared store; it does not activate either runtime mode or create a conventional environment installation.

Editors do not execute Python's context-sensitive import dispatcher. Generated `depfix_imports` aliases provide exact
versioned stubs for imports inside scopes and decorated functions. A generated search-path overlay covers one persistent
default selection; arbitrary scope-sensitive inference still requires aliases or an IDE extension.
