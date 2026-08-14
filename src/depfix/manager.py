"""Live/prepared request coordinator shared by Python and CLI APIs."""

from __future__ import annotations

import hashlib
import json
import platform
import sys
from pathlib import Path
from threading import RLock, Thread
from types import ModuleType
from typing import TYPE_CHECKING

from packaging.markers import Marker, default_environment
from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from .cache import Cache
from .config import ImportDeclaration, ProjectConfig
from .dispatcher import ImportSelection, ModuleBinding, reset_dispatcher_state
from .errors import (
    CacheError,
    FrozenManifestError,
    InvalidUsingScopeError,
    ManifestError,
    ManifestMismatchError,
    NativeIsolationRequired,
    ResolutionError,
    StoreImportError,
    UnsafePackageError,
)
from .manifest import assert_compatible_environment, load_manifest, write_manifest
from .models import Alias, LockedGraph, resolved_realm_id
from .progress import ProgressReporter
from .resolver import Resolver
from .runtime import DepfixRuntime
from .settings import Settings, resolve_settings
from .sources import SourceInfo, parse_source
from .sync import sync_graph

if TYPE_CHECKING:
    from .handles import PackageHandle

_runtimes: dict[
    tuple[str, str, str, tuple[str, ...], bool, tuple[tuple[str, tuple[str, ...]], ...]], DepfixRuntime
] = {}
_active_runtimes: dict[tuple[str, str, str, bool], DepfixRuntime] = {}
_memory_requests: dict[str, tuple[DepfixRuntime, Alias]] = {}
_memory_groups: dict[str, ImportSelection] = {}
_request_locks: dict[str, RLock] = {}
_maintenance_roots: set[str] = set()
_guard = RLock()


def prepare_request(
    specifier: str,
    *,
    module: str | None,
    api: str,
    refresh: bool,
    isolation: str | None,
    settings: Settings,
    source_file: str = "",
    source_line: int = 0,
) -> tuple[DepfixRuntime, Alias]:
    selected_isolation = _normalize_isolation(isolation)
    if selected_isolation == "process":
        raise NativeIsolationRequired(
            "The process-isolation RPC backend is not available in this release",
            request=specifier,
            module=module,
            remediation="run the native package in an application-owned worker process",
        )
    source = parse_source(specifier)
    identity = _request_identity(
        source.normalized,
        module,
        api,
        selected_isolation,
        settings,
    )
    with _guard:
        if not refresh and identity in _memory_requests:
            return _memory_requests[identity]
        request_lock = _request_locks.setdefault(identity, RLock())
    cache = Cache(settings.cache_dir)
    cache.renewal_interval_seconds = settings.cache_renewal_seconds
    progress = ProgressReporter(settings.log_level)
    record_manifest: Path | None = settings.manifest
    with request_lock, cache.lock("resolution:" + identity):
        with _guard:
            if not refresh and identity in _memory_requests:
                return _memory_requests[identity]
        if settings.manifest is not None:
            graph = load_manifest(settings.manifest)
            assert_compatible_environment(graph, settings.manifest)
            alias = _match_manifest_request(
                graph,
                source.normalized,
                module,
                api,
                selected_isolation,
                settings,
            )
            _sync_with_reservation(graph, cache, offline=settings.offline, progress=progress)
            runtime = _runtime(
                graph,
                cache,
                settings.manifest,
                selected_isolation,
                allow_unsafe=settings.allow_unsafe,
                root_nodes=(alias.node,),
            )
        else:
            resolution_path = cache.root / "resolutions" / identity / "imports.lock"
            record_manifest = resolution_path
            if not refresh and resolution_path.is_file():
                graph = load_manifest(resolution_path)
                assert_compatible_environment(graph, resolution_path)
                alias = graph.aliases[0]
                # A warm resolution may refetch an evicted exact artifact, but
                # it never asks uv to resolve the request again.
                _sync_with_reservation(graph, cache, offline=settings.offline, progress=progress)
                runtime = _runtime(
                    graph,
                    cache,
                    resolution_path,
                    selected_isolation,
                    allow_unsafe=settings.allow_unsafe,
                    root_nodes=(alias.node,),
                )
            else:
                if settings.frozen:
                    raise FrozenManifestError(
                        "Frozen mode rejects a request that is not listed in a prepared manifest",
                        request=specifier,
                        normalized_request=source.normalized,
                        frozen=True,
                        remediation="set DEPFIX_MANIFEST or run `depfix export` and `depfix install`",
                    )
                declaration = ImportDeclaration(
                    name="live_" + identity[:16],
                    specifier=specifier,
                    module=module,
                    api=api,
                    source_file=source_file,
                    source_line=source_line,
                    assignment="",
                    base_dir=Path.cwd(),
                    isolation=selected_isolation,
                    allow_unsafe=settings.allow_unsafe,
                    prefer_newest=settings.prefer_newest,
                )
                graph = Resolver(cache, settings=settings, progress=progress).resolve(
                    ProjectConfig(
                        Path("<live>"),
                        (declaration,),
                        {
                            "mode": "live",
                            "isolation": selected_isolation,
                            "allow-unsafe": settings.allow_unsafe,
                            "prefer-newest": settings.prefer_newest,
                        },
                    )
                )
                alias = graph.aliases[0]
                _sync_with_reservation(graph, cache, offline=settings.offline, progress=progress)
                resolution_path.parent.mkdir(parents=True, exist_ok=True)
                write_manifest(graph, resolution_path)
                runtime = _runtime(
                    graph,
                    cache,
                    resolution_path,
                    selected_isolation,
                    allow_unsafe=settings.allow_unsafe,
                    root_nodes=(alias.node,),
                )
        cache.record_installation(
            graph,
            root_nodes=(alias.node,),
            kind="python-code",
            description=f"depfix.{api}({specifier!r})",
            source_file=source_file,
            source_line=source_line,
            manifest=str(record_manifest or ""),
        )
        _schedule_cache_cleanup(cache, settings, graph)
        with _guard:
            _memory_requests[identity] = (runtime, alias)
        root = graph.node_index[alias.node]
        progress.emit("ready", f"{root.distribution}=={root.version}")
    return runtime, alias


def prepare_import_selection(
    specifiers: tuple[str, ...],
    *,
    constraints: tuple[str, ...] = (),
    declaration_origins: tuple[tuple[str, int], ...] = (),
    mode: str,
    refresh: bool,
    isolation: str | None,
    settings: Settings,
    base_dir: Path,
    source_file: str = "",
    source_line: int = 0,
) -> ImportSelection:
    if not specifiers:
        raise InvalidUsingScopeError(
            "A Depfix standard-import selection requires at least one package specifier",
            remediation="pass one or more supported package/source strings",
        )
    try:
        selected_isolation = _normalize_isolation(isolation)
    except ValueError as exc:
        raise InvalidUsingScopeError(str(exc), request=", ".join(specifiers)) from exc
    if selected_isolation == "process":
        raise NativeIsolationRequired(
            "The process-isolation RPC backend is not available in this release",
            request=", ".join(specifiers),
            remediation="run native packages in an application-owned worker process",
        )
    if declaration_origins and len(declaration_origins) != len(specifiers):
        raise ValueError("declaration origins must match the package declarations")
    input_origins = declaration_origins or tuple((source_file, source_line) for _ in specifiers)
    unique_sources: dict[str, tuple[SourceInfo, str, tuple[str, int]]] = {}
    marker_environment = {key: str(value) for key, value in default_environment().items()}
    for index, specifier in enumerate(specifiers):
        source = parse_source(specifier, base_dir=base_dir)
        if source.marker and not Marker(source.marker).evaluate(marker_environment):
            continue
        unique_sources.setdefault(source.normalized, (source, specifier, input_origins[index]))
    sources = sorted(unique_sources.values(), key=lambda item: item[0].normalized)
    normalized = tuple(source.normalized for source, _specifier, _origin in sources)
    originals = tuple(specifier for _source, specifier, _origin in sources)
    active_origins = tuple(origin for _source, _specifier, origin in sources)
    normalized_constraints = _constraint_identity(constraints)
    if not originals:
        return ImportSelection.create({}, (), (), mode, source_file=source_file, source_line=source_line)
    identity = _group_identity(normalized, normalized_constraints, mode, selected_isolation, settings)
    with _guard:
        if not refresh and identity in _memory_groups:
            cached = _memory_groups[identity]
            return ImportSelection.create(
                cached.bindings,
                originals,
                normalized,
                mode,
                source_file=source_file,
                source_line=source_line,
            )
        request_lock = _request_locks.setdefault("group:" + identity, RLock())
    cache = Cache(settings.cache_dir)
    cache.renewal_interval_seconds = settings.cache_renewal_seconds
    progress = ProgressReporter(settings.log_level)
    record_manifest: Path | None = settings.manifest
    with request_lock, cache.lock("group-resolution:" + identity):
        with _guard:
            prepared = _memory_groups.get(identity)
        if not refresh and prepared is not None:
            return ImportSelection.create(
                prepared.bindings,
                originals,
                normalized,
                mode,
                source_file=source_file,
                source_line=source_line,
            )
        if settings.manifest is not None:
            graph = load_manifest(settings.manifest)
            assert_compatible_environment(graph, settings.manifest)
            aliases = _match_manifest_group(
                graph,
                normalized,
                normalized_constraints,
                mode,
                selected_isolation,
                settings,
            )
            _sync_with_reservation(graph, cache, offline=settings.offline, progress=progress)
            runtime = _runtime(
                graph,
                cache,
                settings.manifest,
                selected_isolation,
                allow_unsafe=settings.allow_unsafe,
                root_nodes=tuple(alias.node for alias in aliases),
            )
        else:
            resolution_path = cache.root / "groups" / identity / "imports.lock"
            record_manifest = resolution_path
            if not refresh and resolution_path.is_file():
                graph = load_manifest(resolution_path)
                assert_compatible_environment(graph, resolution_path)
                aliases = graph.aliases
                _sync_with_reservation(graph, cache, offline=settings.offline, progress=progress)
                runtime = _runtime(
                    graph,
                    cache,
                    resolution_path,
                    selected_isolation,
                    allow_unsafe=settings.allow_unsafe,
                    root_nodes=tuple(alias.node for alias in aliases),
                )
            else:
                if settings.frozen:
                    raise FrozenManifestError(
                        "Frozen mode rejects an unlisted Depfix standard-import selection",
                        request=", ".join(originals),
                        frozen=True,
                        remediation="export and install this default()/using() declaration",
                    )
                declarations = tuple(
                    ImportDeclaration(
                        name=f"standard_{index}",
                        specifier=specifier,
                        module=None,
                        api="load_package",
                        source_file=active_origins[index][0],
                        source_line=active_origins[index][1],
                        base_dir=base_dir,
                        isolation=selected_isolation,
                        allow_unsafe=settings.allow_unsafe,
                        prefer_newest=settings.prefer_newest,
                    )
                    for index, specifier in enumerate(originals)
                )
                graph = Resolver(cache, settings=settings, progress=progress).resolve(
                    ProjectConfig(
                        Path("<standard-import>"),
                        declarations,
                        {
                            "mode": "standard-import",
                            "isolation": selected_isolation,
                            "allow-unsafe": settings.allow_unsafe,
                            "prefer-newest": settings.prefer_newest,
                            **({"constraints": normalized_constraints} if normalized_constraints else {}),
                        },
                    )
                )
                aliases = graph.aliases
                _sync_with_reservation(graph, cache, offline=settings.offline, progress=progress)
                resolution_path.parent.mkdir(parents=True, exist_ok=True)
                write_manifest(graph, resolution_path)
                runtime = _runtime(
                    graph,
                    cache,
                    resolution_path,
                    selected_isolation,
                    allow_unsafe=settings.allow_unsafe,
                    root_nodes=tuple(alias.node for alias in aliases),
                )
        public_api = "default" if mode == "default" else "using"
        cache.record_installation(
            graph,
            root_nodes=tuple(alias.node for alias in aliases),
            kind="python-code",
            description=f"depfix.{public_api}({', '.join(repr(item) for item in originals)})",
            source_file=source_file,
            source_line=source_line,
            manifest=str(record_manifest or ""),
        )
        _schedule_cache_cleanup(cache, settings, graph)
        selection = _selection_from_aliases(
            graph,
            runtime,
            tuple(aliases),
            originals,
            normalized,
            mode,
            source_file=source_file,
            source_line=source_line,
        )
        canonical = ImportSelection.create(selection.bindings, originals, normalized, "standard-import")
        with _guard:
            _memory_groups[identity] = canonical
        progress.emit("ready", f"{len(selection.bindings)} managed import roots")
        return selection


def prepare_store_import(root: str) -> ModuleBinding | None:
    """Select one exact, compatible installed graph for an unresolved import root."""
    settings = resolve_settings(discover=True)
    cache = Cache(settings.cache_dir)
    cache.renewal_interval_seconds = settings.cache_renewal_seconds
    candidates: list[tuple[tuple[tuple[str, Version], ...], str, Path, LockedGraph, tuple[str, ...]]] = []
    rejected: list[str] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    manifest_paths = _store_manifest_paths(cache, settings.manifest)
    if settings.manifest is not None and settings.manifest.is_file():
        configured = settings.manifest.resolve()
        try:
            configured_graph = load_manifest(configured)
        except (ManifestError, OSError, ValueError):
            pass
        else:
            if any(
                root in {name.split(".", 1)[0] for name in node.provided_modules} for node in configured_graph.nodes
            ):
                manifest_paths = (configured,)
    for path in manifest_paths:
        try:
            graph = load_manifest(path)
        except (ManifestError, OSError, ValueError):
            continue
        providers = tuple(
            sorted(
                (node for node in graph.nodes if root in {name.split(".", 1)[0] for name in node.provided_modules}),
                key=lambda node: (node.distribution, node.version, node.artifact),
            )
        )
        if not providers:
            continue
        if len(providers) > 1 and not all(root in node.namespace_contributions for node in providers):
            rejected.append(
                "multiple non-namespace providers: "
                + ", ".join(sorted(f"{node.distribution}=={node.version}" for node in providers))
            )
            continue
        provider_ids = tuple(node.id for node in providers)
        identity = (graph.graph_id, provider_ids)
        if identity in seen:
            continue
        seen.add(identity)
        try:
            assert_compatible_environment(graph, path)
            versions = tuple(sorted((node.distribution, Version(node.version)) for node in providers))
        except (ManifestMismatchError, InvalidVersion) as exc:
            rejected.append(f"{', '.join(sorted(f'{node.distribution}=={node.version}' for node in providers))}: {exc}")
            continue
        closure = _node_closure(graph, provider_ids)
        missing = [
            graph.artifact_index[graph.node_index[node_id].artifact].sha256
            for node_id in closure
            if not cache.has_package(graph.artifact_index[graph.node_index[node_id].artifact].sha256)
        ]
        if missing:
            rejected.append(
                f"{', '.join(sorted(f'{node.distribution}=={node.version}' for node in providers))}: "
                "installed graph is incomplete"
            )
            continue
        candidates.append((versions, _closure_fingerprint(graph, provider_ids), path, graph, provider_ids))
    if not candidates:
        if rejected:
            raise StoreImportError(
                "Depfix found an installed provider, but none is compatible with this process",
                module=root,
                rejections=tuple(sorted(set(rejected))),
                remediation="install a compatible artifact or select an exact package with depfix.default(...)",
            )
        return None
    provider_sets = {
        tuple(distribution for distribution, _version in versions)
        for versions, _fingerprint, _path, _graph, _node_ids in candidates
    }
    maximal_provider_sets = {
        providers for providers in provider_sets if not any(set(providers) < set(other) for other in provider_sets)
    }
    if len(maximal_provider_sets) > 1:
        raise StoreImportError(
            "Several installed distributions provide this import name",
            module=root,
            candidates=tuple(
                sorted(
                    ", ".join(f"{distribution}=={version}" for distribution, version in versions)
                    for versions, _fingerprint, _path, _graph, _node_ids in candidates
                )
            ),
            remediation="choose the intended distribution and version with depfix.default(...)",
        )
    selected_provider_set = next(iter(maximal_provider_sets))
    comparable = [
        item
        for item in candidates
        if tuple(distribution for distribution, _version in item[0]) == selected_provider_set
    ]
    version_sets = {item[0] for item in comparable}
    newest_sets = {
        versions
        for versions in version_sets
        if not any(
            all(
                other_version >= version
                for (_distribution, version), (_other, other_version) in zip(versions, other, strict=True)
            )
            and any(
                other_version > version
                for (_distribution, version), (_other, other_version) in zip(versions, other, strict=True)
            )
            for other in version_sets
        )
    }
    if len(newest_sets) > 1:
        raise StoreImportError(
            "No single installed namespace graph has the newest version of every provider",
            module=root,
            candidates=tuple(
                sorted(
                    ", ".join(f"{distribution}=={version}" for distribution, version in versions)
                    for versions in newest_sets
                )
            ),
            remediation="choose one compatible namespace package set with depfix.default(...)",
        )
    newest = next(iter(newest_sets))
    newest_candidates = [item for item in comparable if item[0] == newest]
    fingerprints = {item[1] for item in newest_candidates}
    if len(fingerprints) > 1:
        raise StoreImportError(
            "The newest installed version has several incompatible dependency graphs or artifacts",
            module=root,
            candidates=tuple(
                sorted(
                    ", ".join(
                        f"{graph.node_index[node_id].distribution}=={graph.node_index[node_id].version} "
                        f"({graph.node_index[node_id].artifact})"
                        for node_id in node_ids
                    )
                    for _versions, _fingerprint, _path, graph, node_ids in newest_candidates
                )
            ),
            remediation="choose an exact package source or version with depfix.default(...)",
        )
    _versions, _fingerprint, path, graph, node_ids = min(newest_candidates, key=lambda item: str(item[2]))
    namespace_group = len(node_ids) > 1 and all(
        root in graph.node_index[node_id].namespace_contributions for node_id in node_ids
    )
    runtime = _runtime(
        graph,
        cache,
        path,
        "auto",
        allow_unsafe=settings.allow_unsafe,
        root_nodes=node_ids,
        namespace_groups={root: node_ids} if namespace_group else None,
    )
    node_id = node_ids[0]
    node = graph.node_index[node_id]
    return ModuleBinding(
        runtime,
        node.id,
        node.distribution,
        node.version,
        node.artifact,
        f"shared:{node.artifact}" if runtime.shared else resolved_realm_id(graph, (node.id,)),
        (f"{node.distribution}=={node.version}",),
        "store-fallback",
    )


def _store_manifest_paths(cache: Cache, configured: Path | None) -> tuple[Path, ...]:
    paths: set[Path] = set()
    if configured is not None and configured.is_file():
        paths.add(configured.resolve())
    for directory in ("installs", "groups", "resolutions"):
        root = cache.root / directory
        if root.is_dir():
            paths.update(path.resolve() for path in root.rglob("imports.lock") if path.is_file())
    return tuple(sorted(paths))


def _closure_fingerprint(graph: LockedGraph, roots: tuple[str, ...]) -> str:
    nodes = graph.node_index

    def describe(node_id: str, active: frozenset[str]) -> object:
        if node_id in active:
            return ("cycle", nodes[node_id].distribution)
        node = nodes[node_id]
        return (
            node.distribution,
            node.version,
            node.artifact,
            tuple((name, describe(child, active | {node_id})) for name, child in sorted(node.dependencies.items())),
        )

    return json.dumps(tuple(describe(root, frozenset()) for root in roots), sort_keys=True, separators=(",", ":"))


def activate_manifest(path: Path, settings: Settings) -> DepfixRuntime:
    graph = load_manifest(path)
    assert_compatible_environment(graph, path)
    cache = Cache(settings.cache_dir)
    cache.renewal_interval_seconds = settings.cache_renewal_seconds
    cache.reserve_artifacts({artifact.sha256 for artifact in graph.artifacts})
    for artifact in graph.artifacts:
        if not cache.has_package(artifact.sha256):
            raise CacheError(
                "Prepared package is not materialized in the shared store",
                artifact_hash=artifact.sha256,
                remediation="install the exact manifest online or from a complete bundle",
            )
    sync_graph(graph, cache, offline=True)
    runtime = _runtime(
        graph,
        cache,
        path,
        "inprocess",
        allow_unsafe=True,
        enforce_unsafe=False,
        register_active=False,
    )
    runtime.enable_alias_mode_dispatch()
    _schedule_cache_cleanup(cache, settings, graph)
    return runtime


def load_generated_alias(name: str, identity: tuple[str, str, str, str]) -> ModuleType:
    graph_id, node_id, module, specifier = identity
    runtime = _runtime_for_generated(graph_id, node_id, name)
    request = runtime.graph.alias_index.get(name)
    if request is None or (graph_id, request.node, request.module, request.specifier) != identity:
        raise RuntimeError(f"generated alias {name!r} does not match the active manifest")
    return runtime.import_for_node(node_id, module)


def load_generated_package(name: str, identity: tuple[str, str, str, str]) -> PackageHandle:
    from .handles import PackageHandle

    graph_id, node_id, module, specifier = identity
    runtime = _runtime_for_generated(graph_id, node_id, name)
    request = runtime.graph.alias_index.get(name)
    if request is None or request.node != node_id or request.specifier != specifier or request.api != "load_package":
        raise RuntimeError(f"generated package alias {name!r} does not match the active manifest")
    return PackageHandle(runtime, request)


def reset_runtime_state() -> None:
    with _guard:
        for runtime in set(_runtimes.values()):
            runtime.deactivate()
        _runtimes.clear()
        _active_runtimes.clear()
        _memory_requests.clear()
        _memory_groups.clear()
        _request_locks.clear()
    reset_dispatcher_state()


def _sync_with_reservation(
    graph: LockedGraph,
    cache: Cache,
    *,
    offline: bool,
    progress: ProgressReporter | None,
) -> None:
    cache.reserve_artifacts({artifact.sha256 for artifact in graph.artifacts})
    sync_graph(graph, cache, offline=offline, progress=progress)


def _schedule_cache_cleanup(cache: Cache, settings: Settings, graph: LockedGraph) -> None:
    if not settings.cache_auto_cleanup or not cache.automatic_cleanup_due():
        return
    root = str(cache.root.resolve())
    with _guard:
        if root in _maintenance_roots:
            return
        _maintenance_roots.add(root)
        protected = {artifact.sha256 for artifact in graph.artifacts}
        for runtime in set(_runtimes.values()):
            if runtime.cache.root.resolve() == cache.root.resolve():
                protected.update(runtime.artifact_hashes)

    def maintain() -> None:
        try:
            cache.automatic_cleanup(
                settings.cache_retention_days,
                protected_hashes=protected,
                grace_hours=settings.cache_deletion_grace_hours,
            )
        except (CacheError, OSError, ValueError):
            # Retention is opportunistic; explicit cache commands surface errors.
            pass
        finally:
            with _guard:
                _maintenance_roots.discard(root)

    Thread(target=maintain, name="depfix-cache-cleanup", daemon=True).start()


def runtime_for_graph(graph_id: str, node_id: str, allow_unsafe: bool = False) -> DepfixRuntime:
    with _guard:
        runtime = _active_runtimes.get((graph_id, node_id, "inprocess", allow_unsafe))
        if runtime is None:
            runtime = _active_runtimes.get((graph_id, node_id, "shared", allow_unsafe))
    if runtime is None:
        raise RuntimeError(f"No active runtime owns node {node_id!r} in graph {graph_id!r}")
    return runtime


def runtime_for_alias(graph_id: str, node_id: str, alias_name: str) -> DepfixRuntime:
    return _runtime_for_generated(graph_id, node_id, alias_name)


def _runtime(
    graph: LockedGraph,
    cache: Cache,
    manifest: Path,
    isolation: str,
    *,
    allow_unsafe: bool,
    root_nodes: tuple[str, ...] | None = None,
    namespace_groups: dict[str, tuple[str, ...]] | None = None,
    enforce_unsafe: bool = True,
    register_active: bool = True,
) -> DepfixRuntime:
    active_nodes = _node_closure(graph, root_nodes or tuple(node.id for node in graph.nodes))
    import_mode = _effective_import_mode(graph, isolation, active_nodes)
    if enforce_unsafe:
        _assert_unsafe_allowed(graph, active_nodes, allow_unsafe, manifest)
    runtime_nodes = active_nodes if import_mode == "shared" else tuple(node.id for node in graph.nodes)
    namespace_identity = tuple(sorted((root, node_ids) for root, node_ids in (namespace_groups or {}).items()))
    key = (graph.graph_id, str(cache.root.resolve()), import_mode, runtime_nodes, allow_unsafe, namespace_identity)
    with _guard:
        runtime = _runtimes.get(key)
        if runtime is None:
            runtime = DepfixRuntime(
                graph,
                cache,
                manifest=manifest,
                import_mode=import_mode,
                active_node_ids=runtime_nodes,
                namespace_groups=namespace_groups,
                allow_unsafe=allow_unsafe,
            ).activate()
            _runtimes[key] = runtime
        if register_active:
            for node_id in runtime_nodes:
                _active_runtimes[(graph.graph_id, node_id, import_mode, allow_unsafe)] = runtime
        return runtime


def _normalize_isolation(value: str | None) -> str:
    selected = value or "auto"
    if selected == "isolated":
        selected = "inprocess"
    if selected not in {"auto", "inprocess", "shared", "process"}:
        raise ValueError("isolation must be 'auto', 'inprocess', 'shared', or 'process'")
    return selected


def _effective_import_mode(graph: LockedGraph, isolation: str, active_nodes: tuple[str, ...]) -> str:
    selected = _normalize_isolation(isolation)
    if selected == "shared":
        return "shared"
    if selected == "auto" and any(
        graph.node_index[node_id].native_classification != "pure-python" for node_id in active_nodes
    ):
        return "shared"
    return "inprocess"


def _node_closure(graph: LockedGraph, roots: tuple[str, ...]) -> tuple[str, ...]:
    nodes = graph.node_index
    selected: list[str] = []

    def include(node_id: str) -> None:
        if node_id in selected:
            return
        if node_id not in nodes:
            raise RuntimeError(f"Graph {graph.graph_id!r} has no node {node_id!r}")
        selected.append(node_id)
        for _distribution, dependency_id in sorted(nodes[node_id].dependencies.items()):
            include(dependency_id)

    for root in roots:
        include(root)
    return tuple(selected)


def _assert_unsafe_allowed(
    graph: LockedGraph,
    active_nodes: tuple[str, ...],
    allow_unsafe: bool,
    manifest: Path,
) -> None:
    unsafe = [
        graph.node_index[node_id]
        for node_id in active_nodes
        if graph.node_index[node_id].native_classification == "native-known-unsafe"
    ]
    if unsafe and not allow_unsafe:
        raise UnsafePackageError(
            "The selected dependency graph contains a package classified as unsafe",
            candidates=tuple(sorted(f"{node.distribution}=={node.version}" for node in unsafe)),
            manifest=manifest,
            remediation=(
                "pass allow_unsafe=True for this request, call depfix.configure(allow_unsafe=True) process-wide, "
                "or set DEPFIX_ALLOW_UNSAFE=1"
            ),
        )


def _runtime_for_generated(graph_id: str, node_id: str, alias_name: str) -> DepfixRuntime:
    with _guard:
        candidates = [
            runtime
            for runtime in set(_runtimes.values())
            if runtime.graph.graph_id == graph_id and node_id in runtime.graph.node_index
        ]
    graph = candidates[0].graph if candidates else None
    settings = resolve_settings(discover=True)
    if graph is None and settings.manifest is None:
        raise RuntimeError("The generated Depfix alias has no active or automatically discovered manifest")
    if graph is None:
        assert settings.manifest is not None
        graph = load_manifest(settings.manifest)
        assert_compatible_environment(graph, settings.manifest)
    if graph.graph_id != graph_id:
        raise RuntimeError("The generated Depfix alias does not match the discovered manifest")
    alias = graph.alias_index.get(alias_name)
    if alias is None or alias.node != node_id:
        raise RuntimeError(f"Generated alias {alias_name!r} is absent from graph {graph_id!r}")
    manifest = next(
        (candidate.manifest for candidate in candidates if candidate.manifest is not None), settings.manifest
    )
    if manifest is None:
        raise RuntimeError("The generated Depfix alias has no prepared manifest path")
    cache = candidates[0].cache if candidates else Cache(settings.cache_dir)
    return _runtime(
        graph,
        cache,
        manifest,
        alias.isolation,
        allow_unsafe=alias.allow_unsafe,
        root_nodes=(node_id,),
    )


def _match_manifest_request(
    graph: LockedGraph,
    normalized: str,
    module: str | None,
    api: str,
    isolation: str,
    settings: Settings,
) -> Alias:
    matches = [
        request
        for request in graph.aliases
        if request.normalized_specifier == normalized
        and request.api == api
        and _isolation_matches(request.isolation, isolation)
        and request.allow_unsafe == settings.allow_unsafe
        and ((module is None and not request.explicit_module) or (module is not None and request.module == module))
    ]
    if len(matches) != 1:
        if settings.frozen:
            raise FrozenManifestError(
                "The normalized runtime request is not declared exactly once in the frozen manifest",
                normalized_request=normalized,
                module=module,
                manifest=settings.manifest,
                frozen=True,
                candidates=tuple(
                    f"{request.api}:{request.normalized_specifier}:module={request.module or '<package>'}"
                    for request in graph.aliases
                ),
                remediation="export the current source declarations into the deployment manifest",
            )
        raise ManifestMismatchError(
            "The prepared manifest does not contain this exact normalized request",
            normalized_request=normalized,
            module=module,
            manifest=settings.manifest,
            remediation="run `depfix export` or omit the manifest for live resolution",
        )
    return matches[0]


def _request_identity(
    normalized: str,
    module: str | None,
    api: str,
    isolation: str,
    settings: Settings,
) -> str:
    payload = json.dumps(
        {
            "request": normalized,
            "module": module,
            "api": api,
            "isolation": isolation,
            "allow_unsafe": settings.allow_unsafe,
            "prefer_newest": settings.prefer_newest,
            "manifest": str(settings.manifest.resolve()) if settings.manifest else None,
            "cache": str(settings.cache_dir.resolve()),
            "index": settings.index_url,
            "extra_indexes": settings.extra_index_url,
            "environment": {
                "implementation": platform.python_implementation().lower(),
                "python": f"{sys.version_info.major}.{sys.version_info.minor}",
            },
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _isolation_matches(prepared: str, requested: str) -> bool:
    return prepared == requested or (requested == "auto" and prepared == "inprocess")


def _group_identity(
    normalized: tuple[str, ...],
    constraints: tuple[str, ...],
    mode: str,
    isolation: str,
    settings: Settings,
) -> str:
    payload = json.dumps(
        {
            "specifiers": normalized,
            **({"constraints": constraints} if constraints else {}),
            "mode": mode,
            "isolation": isolation,
            "allow_unsafe": settings.allow_unsafe,
            "prefer_newest": settings.prefer_newest,
            "index": settings.index_url,
            "extra_indexes": settings.extra_index_url,
            "manifest": str(settings.manifest.resolve()) if settings.manifest else None,
            "cache": str(settings.cache_dir.resolve()),
            "environment": {
                "implementation": platform.python_implementation().lower(),
                "python": f"{sys.version_info.major}.{sys.version_info.minor}",
            },
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _selection_from_aliases(
    graph: LockedGraph,
    runtime: DepfixRuntime,
    aliases: tuple[Alias, ...],
    specifiers: tuple[str, ...],
    normalized: tuple[str, ...],
    mode: str,
    *,
    source_file: str,
    source_line: int,
) -> ImportSelection:
    bindings: dict[str, ModuleBinding] = {}
    conflicts: list[str] = []
    selected_nodes: list[str] = []

    def include(node_id: str) -> None:
        if node_id in selected_nodes:
            return
        selected_nodes.append(node_id)
        if runtime.shared:
            for dependency_id in graph.node_index[node_id].dependencies.values():
                include(dependency_id)

    for alias in aliases:
        include(alias.node)
    for node_id in selected_nodes:
        node = graph.node_index[node_id]
        binding = ModuleBinding(
            runtime,
            node.id,
            node.distribution,
            node.version,
            node.artifact,
            f"shared:{node.artifact}" if runtime.shared else resolved_realm_id(graph, (node.id,)),
            specifiers,
            mode,
        )
        for provided in node.provided_modules:
            root = provided.split(".", 1)[0]
            current = bindings.get(root)
            if current is not None and current.fingerprint != binding.fingerprint:
                current_node = graph.node_index[current.node_id]
                if (
                    runtime.shared
                    and root in current_node.namespace_contributions
                    and root in node.namespace_contributions
                ):
                    continue
                conflicts.append(
                    f"{root}: {current.distribution}=={current.version} vs {binding.distribution}=={binding.version}"
                )
            else:
                bindings[root] = binding
    if conflicts:
        raise InvalidUsingScopeError(
            "Several selected packages provide the same import root",
            request=", ".join(specifiers),
            candidates=tuple(conflicts),
            remediation="split the conflicting packages into separate depfix.using(...) scopes",
        )
    return ImportSelection.create(
        bindings,
        specifiers,
        normalized,
        mode,
        source_file=source_file,
        source_line=source_line,
    )


def _match_manifest_group(
    graph: LockedGraph,
    normalized: tuple[str, ...],
    constraints: tuple[str, ...],
    mode: str,
    isolation: str,
    settings: Settings,
) -> tuple[Alias, ...]:
    prepared_constraints = tuple(sorted(str(item) for item in graph.policy.get("constraints", ())))
    if prepared_constraints != constraints:
        error = FrozenManifestError if settings.frozen else ManifestMismatchError
        raise error(
            "The prepared manifest does not contain this exact requirements constraint set",
            normalized_request=", ".join(normalized),
            manifest=settings.manifest,
            frozen=settings.frozen,
            candidates=prepared_constraints,
            remediation="export and install the requirements file with its current constraints",
        )
    groups = [
        group
        for group in graph.groups
        if tuple(sorted(group.normalized_specifiers)) == normalized
        and group.mode == mode
        and _isolation_matches(group.isolation, isolation)
        and group.allow_unsafe == settings.allow_unsafe
    ]
    if groups:
        group = groups[0]
        aliases = tuple(graph.alias_index[name] for name in group.aliases)
        if aliases:
            return aliases
    included = tuple(
        alias
        for alias in graph.aliases
        if alias.source_file in {"<explicit>", ".depfix/config.toml"} and alias.normalized_specifier in normalized
    )
    if (
        len(included) == len(normalized)
        and tuple(sorted(alias.normalized_specifier for alias in included)) == normalized
    ):
        return included
    error = FrozenManifestError if settings.frozen else ManifestMismatchError
    raise error(
        "The prepared manifest does not contain this exact standard-import specifier group",
        normalized_request=", ".join(normalized),
        manifest=settings.manifest,
        frozen=settings.frozen,
        candidates=tuple(
            f"{group.mode}:{group.isolation}:{' | '.join(group.normalized_specifiers)}" for group in graph.groups
        ),
        remediation="export and install the current default()/using() declarations",
    )


def _constraint_identity(values: tuple[str, ...]) -> tuple[str, ...]:
    normalized: set[str] = set()
    for value in values:
        if not value.strip():
            continue
        try:
            requirement = Requirement(value)
        except InvalidRequirement as exc:
            raise ResolutionError("Invalid requirements constraint", request=value, remediation=str(exc)) from exc
        if requirement.url or requirement.extras or requirement.marker:
            raise ResolutionError(
                "Requirements constraints may contain only a distribution name and version specifier",
                request=value,
            )
        normalized.add(f"{canonicalize_name(requirement.name)}{requirement.specifier}")
    return tuple(sorted(normalized))
