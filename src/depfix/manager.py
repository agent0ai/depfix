"""Live/prepared request coordinator shared by Python and CLI APIs."""

from __future__ import annotations

import hashlib
import json
import platform
import sys
from pathlib import Path
from threading import RLock
from types import ModuleType
from typing import TYPE_CHECKING

from .cache import Cache
from .config import ImportDeclaration, ProjectConfig
from .dispatcher import ImportSelection, ModuleBinding, reset_dispatcher_state
from .errors import (
    FrozenManifestError,
    InvalidUsingScopeError,
    ManifestMismatchError,
    NativeIsolationRequired,
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

_runtimes: dict[tuple[str, str, str, tuple[str, ...], bool], DepfixRuntime] = {}
_active_runtimes: dict[tuple[str, str, str, bool], DepfixRuntime] = {}
_memory_requests: dict[str, tuple[DepfixRuntime, Alias]] = {}
_memory_groups: dict[str, ImportSelection] = {}
_request_locks: dict[str, RLock] = {}
_guard = RLock()


def prepare_request(
    specifier: str,
    *,
    module: str | None,
    api: str,
    refresh: bool,
    isolation: str | None,
    settings: Settings,
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
    progress = ProgressReporter(settings.log_level)
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
            sync_graph(graph, cache, offline=settings.offline, progress=progress)
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
            if not refresh and resolution_path.is_file():
                graph = load_manifest(resolution_path)
                assert_compatible_environment(graph, resolution_path)
                alias = graph.aliases[0]
                # A warm resolution may refetch an evicted exact artifact, but
                # it never asks uv to resolve the request again.
                sync_graph(graph, cache, offline=settings.offline, progress=progress)
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
                    assignment="",
                    base_dir=Path.cwd(),
                    isolation=selected_isolation,
                    allow_unsafe=settings.allow_unsafe,
                )
                graph = Resolver(cache, settings=settings, progress=progress).resolve(
                    ProjectConfig(
                        Path("<live>"),
                        (declaration,),
                        {
                            "mode": "live",
                            "isolation": selected_isolation,
                            "allow-unsafe": settings.allow_unsafe,
                        },
                    )
                )
                alias = graph.aliases[0]
                sync_graph(graph, cache, offline=settings.offline, progress=progress)
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
        with _guard:
            _memory_requests[identity] = (runtime, alias)
        root = graph.node_index[alias.node]
        progress.emit("ready", f"{root.distribution}=={root.version}")
    return runtime, alias


def prepare_import_selection(
    specifiers: tuple[str, ...],
    *,
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
    unique_sources: dict[str, tuple[SourceInfo, str]] = {}
    for specifier in specifiers:
        source = parse_source(specifier, base_dir=base_dir)
        unique_sources.setdefault(source.normalized, (source, specifier))
    sources = sorted(unique_sources.values(), key=lambda item: item[0].normalized)
    normalized = tuple(source.normalized for source, _specifier in sources)
    originals = tuple(specifier for _source, specifier in sources)
    identity = _group_identity(normalized, mode, selected_isolation, settings)
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
    progress = ProgressReporter(settings.log_level)
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
            aliases = _match_manifest_group(graph, normalized, mode, selected_isolation, settings)
            sync_graph(graph, cache, offline=settings.offline, progress=progress)
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
            if not refresh and resolution_path.is_file():
                graph = load_manifest(resolution_path)
                assert_compatible_environment(graph, resolution_path)
                aliases = graph.aliases
                sync_graph(graph, cache, offline=settings.offline, progress=progress)
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
                        base_dir=base_dir,
                        isolation=selected_isolation,
                        allow_unsafe=settings.allow_unsafe,
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
                        },
                    )
                )
                aliases = graph.aliases
                sync_graph(graph, cache, offline=settings.offline, progress=progress)
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


def activate_manifest(path: Path, settings: Settings) -> DepfixRuntime:
    graph = load_manifest(path)
    assert_compatible_environment(graph, path)
    cache = Cache(settings.cache_dir)
    for artifact in graph.artifacts:
        cache.verify_blob(artifact.sha256, size=artifact.size)
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


def runtime_for_graph(graph_id: str, node_id: str, allow_unsafe: bool = False) -> DepfixRuntime:
    with _guard:
        runtime = _active_runtimes.get((graph_id, node_id, "inprocess", allow_unsafe))
    if runtime is None:
        raise RuntimeError(f"No active in-process runtime owns node {node_id!r} in graph {graph_id!r}")
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
    enforce_unsafe: bool = True,
    register_active: bool = True,
) -> DepfixRuntime:
    active_nodes = _node_closure(graph, root_nodes or tuple(node.id for node in graph.nodes))
    import_mode = _effective_import_mode(graph, isolation, active_nodes)
    if enforce_unsafe:
        _assert_unsafe_allowed(graph, active_nodes, allow_unsafe, manifest)
    runtime_nodes = active_nodes if import_mode == "shared" else tuple(node.id for node in graph.nodes)
    key = (graph.graph_id, str(cache.root.resolve()), import_mode, runtime_nodes, allow_unsafe)
    with _guard:
        runtime = _runtimes.get(key)
        if runtime is None:
            runtime = DepfixRuntime(
                graph,
                cache,
                manifest=manifest,
                import_mode=import_mode,
                active_node_ids=runtime_nodes,
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


def _group_identity(normalized: tuple[str, ...], mode: str, isolation: str, settings: Settings) -> str:
    payload = json.dumps(
        {
            "specifiers": normalized,
            "mode": mode,
            "isolation": isolation,
            "allow_unsafe": settings.allow_unsafe,
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
    mode: str,
    isolation: str,
    settings: Settings,
) -> tuple[Alias, ...]:
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
