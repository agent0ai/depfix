# Test suite

## Purpose

- Verify public behavior, isolation invariants, resolution, deployment, security, and packaging-facing contracts.

## Ownership

- `conftest.py` owns deterministic artifact fixtures.
- Test modules group end-to-end, runtime/resolver, public-product, lock/cache, standard-import scope, and live cross-version
  object-boundary behavior.
- `test_simple_index.py` owns network-free Simple JSON/HTML negotiation, metadata translation, redirect, media-type,
  missing-size, and grouped custom-index isolation regressions.
- `test_release_tooling.py` owns network-free immutable-tag and tag-safe dispatch regressions for the owner release helper.

## Local Contracts

- Tests must not depend on the developer's ambient packages, cache, or credentials.
- Network-free fixtures are preferred; explicitly marked live checks belong in release or CI boundaries.
- Live PyPI checks require `DEPFIX_RUN_LIVE_TESTS=1`; CI owns their connected execution.
- Source-archive regressions cover real `tinysegmenter==0.3`, contained tar/ZIP links and hard links, forward/nested/chained
  references, executable targets, portable namespace collisions, unsafe targets and special members, and expanded-size
  accounting without persisting filesystem links.
- Wheel regressions cover archive-derived manifests for safe unlisted Python, native, signature, and supported `.data`
  members; missing claims and safe stale rows; malformed, duplicate, insecure, and mismatched RECORD claims; namespace
  collisions; and warm-reuse rejection of mutation, omission, unmanifested payload files or directories, links, and
  special entries. Wheel-owned bytecode remains manifested; source-backed interpreter bytecode is accepted only when its
  complete body matches deterministic recompilation, with forged-header bytecode and trailing bytes rejected.
- Regressions should assert observable public behavior or a named safety invariant.
- Remote-I/O scheduler regressions assert stable result/error ordering, 16-way tiny-file activity, fixed medium weights,
  exclusive 100 MB and missing-size artifacts, explicit four-slot metadata work, raised-capacity tiny slots, bounded configuration rollback, capacity-invariant resolver/sync
  outputs and progress, and concurrent integrity cleanup and transient retry behavior at the real prefetch boundaries.
- Runtime-target regressions cover uniform non-writable executable POSIX payloads, non-executable and writable rejection,
  locked repair of legacy non-writable targets without a retained artifact blob, refusal to repair writable manifested or
  authenticated derived payloads or writable directories, verified rematerialization of writable cache and local targets,
  rollback-safe warm local replacement, warm local legacy-mode repair, closure-scoped installed-store repair, and Windows
  native read-only semantics without POSIX execute-mode assumptions.
- Standard-import tests reset the dispatcher, persistent defaults, context scopes, runtimes, and configuration.
- Requirements-default tests cover canonical nested parsing, constraints, markers, relative paths, contextual rejection,
  idempotent offline reuse, and atomic default-conflict rollback.
- Installed-store fallback tests cover passive opt-in, recorded module-name mismatches, normal finder precedence,
  deterministic/ambiguous selection, namespaces, native mode, import semantics, concurrency, and reversible hooks without
  network resolution, exact-manifest precedence, plus automatic script/module activation through `depfix run` without
  application-side setup.
- Shared-mode tests must clean up process-global import roots and paths, and cover compatible reuse, public-owner conflicts,
  private-helper and compatibility-alias best effort, namespace contribution merging, and scoped first-use/reuse behavior.
- Unsafe-loading tests must cover per-request/global precedence, deny-by-default remediation, manifest persistence, and a
  real compiled-extension path when the interpreter provides a suitable test extension.
- Cache lifecycle tests must cover installation/use timestamps, total reclaimed targets, returning-graph reservations,
  transactional artifact-keyed full-graph renewal without per-artifact writes, periodic cadence, renewal/deletion races,
  bounded activation failure under a competing process's usage-store writer, interrupted candidate persistence, two-phase
  automatic deletion, read-only image-origin renewal and tree removal, retention configuration precedence,
  same-version artifact variants, dependency trees, installed-payload corruption, ephemeral archive reconciliation,
  abandoned download ownership, stale extraction/build staging, dry-run immutability, top-level installed inventory/tree
  commands, explicit manifest inspection, live-resolution inspection, compatibility migration, and equivalent Python/CLI
  removal behavior. uv-boundary tests must prove Depfix uses and promptly removes its own subprocess cache,
  preserves ambient user-owned uv caches, and reclaims only dead-owner crash leftovers.
- Uninstall tests must cover PEP 440 name/specifier validation, normalization, overlapping selection deduplication,
  dry-run purity, no-match and JSON reporting, preparation/runtime protection, non-cascading shared dependencies, and
  actual materialization/removal plus runtime-activation/removal under both shared-target lock orderings.
- Bundle and online manifest-repair tests must cover exact hash-verified reacquisition of source-built artifacts after
  their ephemeral wheels are removed; offline repair must still fail clearly when no complete target or bundle exists.
- Resolution tests must cover compatible cache reuse across separate and grouped roots, newest-first overrides, cached
  top-level requests, public loading signatures, scanner preservation, and configuration precedence.
- Scoped-index tests must cover primary-only precedence, grouped and async loading, concurrent isolation, graph identity,
  exact-manifest rejection, scanner preservation, native-package handling, refresh, and credential redaction.
- `depfix pip install` tests must prove package arguments and nested requirement/constraint files use grouped Depfix
  resolution, preserve incompatible transitive versions, populate only the shared store, and leave the environment and
  `sys.path` unchanged.
- Live cross-version object-boundary probes must use immutable published releases, distinguish immediate, silent, and
  delayed failures, and avoid presenting a deliberately selected package set as an ecosystem frequency estimate.
- Boundary API tests must cover module/class/instance provenance, unmanaged values, exact producer/consumer diagnostics,
  selected parameters, builtin-container recursion, return checking, async preservation, and documented detection gaps.
- Release-tooling tests must use local temporary Git repositories and must not read credentials, call GitHub, or query
  package indexes.

## Work Guidance

- Keep temporary artifacts under pytest-provided paths and reset process-global Depfix state between cases.

## Verification

- Run `python -m pytest -q`; use the closest test module during iteration.

## Child DOX Index
