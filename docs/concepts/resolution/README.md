# Resolution

Depfix turns a requirement-like request into an exact, target-specific graph before any realm code executes:

```text
request
  → normalized source identity
  → compatible version and artifact selection
  → uv fetch/build in an isolated target
  → hash and package-metadata inspection
  → parent-specific dependency nodes
  → deterministic manifest or live resolution entry
```

The [source grammar](source-grammar.md) covers PyPI, Git, URLs, files, single Python modules, and PEP 508 direct references.
The [uv backend](uv-backend.md) defines the supported subprocess boundary. [Module discovery](module-discovery.md) explains
how Core Metadata and exact wheel contents become public import candidates.

Resolution does not install into the running interpreter. Its output feeds the
[artifact cache](../artifacts-and-cache/), [manifest model](../manifests/), and finally the
[import runtime](../import-realms/).
