# Resolution

Depfix turns a requirement-like request into an exact, target-specific graph before any realm code executes:

```text
request
  → normalized source identity
  → verified installed-inventory selection
  → bulk uv exact-version plan when remote resolution is needed
  → artifact fetch/build and inspection
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

Version ranges are resolved cache-first by default. Depfix first selects the newest complete installed artifact matching
the canonical distribution name, PEP 440 range, target tags, Python version, extras, and markers. It does not require the
active index to advertise that artifact or its hash. A verified custom-index or Git/VCS build therefore remains the same
installed distribution and version when a later ordinary request uses PyPI defaults. Exact hashes still own physical
identity, integrity verification, provenance, and manifest reproducibility; same-version variants remain distinct files.
Corrupt or incomplete installed targets are excluded from this inventory and must be repaired or acquired again.

An implicit index change does not override compatible installed reuse. `prefer_newest=True` explicitly asks live
resolution to reconsider remote versions, while explicit artifact URLs/hashes and exact prepared manifests retain their
own source and physical identity. Frozen/offline requests can reuse only complete compatible installed artifacts or their
exact available inputs. Refresh invalidates reusable graph results but keeps the ordinary cache-first ranking unless it is
combined with `prefer_newest`.

Grouped roots use stable greedy reuse. An artifact selected for one root can satisfy a later root with an overlapping
range, so `default("A", "B")`, `using("A", "B")`, or `depfix pip install A B` can converge on one compatible dependency
version. Depfix does not search every possible combination for a mathematically minimal graph. Separate requests can also
reuse compatible cached artifacts, while their dependency nodes remain parent-specific. `depfix pip install` keeps the
same parent-specific graphs in the shared store and therefore does not turn transitive conflicts into environment-wide
installation failures.

Compatible registry-only groups first use one `uv pip compile` operation to produce a conventional exact-version plan
without installing a duplicate closure into an ephemeral target. Depfix then acquires and verifies each selected artifact
through its own content-addressed store and reconstructs parent-specific dependencies, extras, markers, native metadata,
and import ownership. Cache-first groups expose verified installed versions and dependency metadata to that planning call
through temporary metadata-only wheels plus exact constraints. These planning stubs are never installed and never replace
the immutable hash/provenance identity of the real stored artifact. If the installed preference set makes planning fail,
Depfix first retries the complete root group without those optional cache constraints, so a stale transitive installation
is not classified as a root conflict. If the root group itself conflicts, Depfix may stably move roots whose verified
installed metadata proves a mismatch with an installed dependency version toward the split boundary. This hint uses no
network lookup or dependency-tree pre-resolution and leaves the original order unchanged when local evidence is absent.
Depfix then recursively bisects the roots and keeps every successful half as an independent conventional plan. Failed halves continue splitting until only singleton roots remain;
only those singletons use isolated resolution. This bounded process takes at most linear planning calls, does not search
for a mathematically largest cross-half cohort, and deliberately lets separate successful halves retain different
dependency versions in Depfix's parent-specific graphs. Unsupported groups retain the existing isolated resolver.

The resulting exact group plan is stored under an API-independent identity. A semantically equivalent later
`default()`/`default_requirements()` activation can consume a graph prepared by `depfix pip install` or
`project.install_packages()` without uv, index traversal, or graph reconstruction. Normalized roots, constraints,
effective indexes, cache preference, candidate-eligibility policy, unsafe policy, and the full target environment
participate in that identity; refresh replaces the reusable result, and consumer-specific import mode/isolation stays
outside the reusable resolution key.

`prefer_newest=True` on a loading API, `depfix.configure(prefer_newest=True)`, `[resolver] prefer-newest = true`, and the
CLI `--prefer-newest` option switch ranking back to newest-first. Live resolution entries and prepared manifests are exact
once written; refresh or export again when candidate selection should be reconsidered.
