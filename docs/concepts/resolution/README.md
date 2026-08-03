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

## Compatible cache reuse

Version ranges are resolved cache-first by default. Among artifacts that remain available from the configured index and
meet the requirement, target tags, Python version, and policy, Depfix selects the newest artifact already in its shared
content store. When there is no cached match, resolution selects the newest compatible version. Exact hashes still own
artifact identity; this preference never treats different builds or versions as interchangeable.

Grouped roots use stable greedy reuse. An artifact selected for one root can satisfy a later root with an overlapping
range, so `default("A", "B")`, `using("A", "B")`, or `depfix pip install A B` can converge on one compatible dependency
version. Depfix does not search every possible combination for a mathematically minimal graph. Separate requests can also
reuse compatible cached artifacts, while their dependency nodes remain parent-specific. `depfix pip install` keeps the
same parent-specific graphs in the shared store and therefore does not turn transitive conflicts into environment-wide
installation failures.

`prefer_newest=True` on a loading API, `depfix.configure(prefer_newest=True)`, `[resolver] prefer-newest = true`, and the
CLI `--prefer-newest` option switch ranking back to newest-first. Live resolution entries and prepared manifests are exact
once written; refresh or export again when candidate selection should be reconsidered.
