# Import realms

```text
public request / scanned request
  -> unified PEP 508 + source model
  -> uv-backed exact version/fetch/build boundary
  -> artifact hash + wheel/Core Metadata inspection
  -> lock-scoped realm graph with parent-specific edges
  -> global immutable cache and materialized targets
  -> caller-bound synthetic import runtime
```

Artifacts, nodes, and requests are distinct. An artifact is immutable content. A node is one artifact plus extras,
dependency edges, module ownership, namespace contributions, and native policy in a particular realm. A request points to a
node and retains its normalized identity, API contract, source site, alias, and policy.

Canonical modules use `_depfix.g_<manifest>.n_<node>.<logical-name>`. They live in `sys.modules` only under synthetic names;
logical roots and materialized target paths are not added to global import state. Each module executes with a caller-bound
`__import__` that resolves logical names through that node's declared dependency edges. Relative imports, circular imports,
namespace packages, resource readers, `pkgutil`, and realm-scoped `importlib.metadata` use the same binding.

The public `depfix_imports` alias root is a lightweight finder at runtime and a physical generated stub package for editors.
The two layers share graph/node/module/specifier identities.

uv resolves/fetches/builds at an executable boundary. Depfix retains graph topology, artifact selection and validation,
module ownership, native policy, manifests, caching, and import identity. Active `site-packages` is never a preparation
target.

Global cache paths are content or request addressed beneath `depfix/v1`; target keys include interpreter/ABI/platform
identity. Mutations use locks, temporary paths, fsync where relevant, and atomic replacement. Completed targets carry an
artifact marker and are validated before reuse.
