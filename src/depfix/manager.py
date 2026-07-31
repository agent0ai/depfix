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
from .errors import FrozenManifestError, InvalidUsingScopeError, ManifestMismatchError, NativeIsolationRequired
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

_runtimes: dict[tuple[str, str], DepfixRuntime] = {}
_active_runtimes: dict[str, DepfixRuntime] = {}
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
    selected_isolation = isolation or "inprocess"
    if selected_isolation not in {"inprocess", "process"}:
        raise ValueError("isolation must be 'inprocess' or 'process'")
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
            runtime = _runtime(graph, cache, settings.manifest)
        else:
            resolution_path = cache.root / "resolutions" / identity / "imports.lock"
            if not refresh and resolution_path.is_file():
                graph = load_manifest(resolution_path)
                assert_compatible_environment(graph, resolution_path)
                alias = graph.aliases[0]
                # A warm resolution may refetch an evicted exact artifact, but
                # it never asks uv to resolve the request again.
                sync_graph(graph, cache, offline=settings.offline, progress=progress)
                runtime = _runtime(graph, cache, resolution_path)
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
                )
                graph = Resolver(cache, settings=settings, progress=progress).resolve(
                    ProjectConfig(
                        Path("<live>"),
                        (declaration,),
                        {"mode": "live", "isolation": selected_isolation},
                    )
                )
                alias = graph.aliases[0]
                sync_graph(graph, cache, offline=settings.offline, progress=progress)
                resolution_path.parent.mkdir(parents=True, exist_ok=True)
                write_manifest(graph, resolution_path)
                runtime = _runtime(graph, cache, resolution_path)
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
    selected_isolation = isolation or "inprocess"
    if selected_isolation not in {"inprocess", "process"}:
        raise InvalidUsingScopeError("isolation must be 'inprocess' or 'process'", request=", ".join(specifiers))
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
            runtime = _runtime(graph, cache, settings.manifest)
        else:
            resolution_path = cache.root / "groups" / identity / "imports.lock"
            if not refresh and resolution_path.is_file():
                graph = load_manifest(resolution_path)
                assert_compatible_environment(graph, resolution_path)
                aliases = graph.aliases
                sync_graph(graph, cache, offline=settings.offline, progress=progress)
                runtime = _runtime(graph, cache, resolution_path)
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
                    )
                    for index, specifier in enumerate(originals)
                )
                graph = Resolver(cache, settings=settings, progress=progress).resolve(
                    ProjectConfig(
                        Path("<standard-import>"),
                        declarations,
                        {"mode": "standard-import", "isolation": selected_isolation},
                    )
                )
                aliases = graph.aliases
                sync_graph(graph, cache, offline=settings.offline, progress=progress)
                resolution_path.parent.mkdir(parents=True, exist_ok=True)
                write_manifest(graph, resolution_path)
                runtime = _runtime(graph, cache, resolution_path)
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
    return _runtime(graph, cache, path)


def load_generated_alias(name: str, identity: tuple[str, str, str, str]) -> ModuleType:
    graph_id, node_id, module, specifier = identity
    runtime = _runtime_for_generated(graph_id)
    request = runtime.graph.alias_index.get(name)
    if request is None or (graph_id, request.node, request.module, request.specifier) != identity:
        raise RuntimeError(f"generated alias {name!r} does not match the active manifest")
    return runtime.import_for_node(node_id, module)


def load_generated_package(name: str, identity: tuple[str, str, str, str]) -> PackageHandle:
    from .handles import PackageHandle

    graph_id, node_id, module, specifier = identity
    runtime = _runtime_for_generated(graph_id)
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


def runtime_for_graph(graph_id: str) -> DepfixRuntime:
    return _runtime_for_generated(graph_id)


def _runtime(graph: LockedGraph, cache: Cache, manifest: Path) -> DepfixRuntime:
    key = (graph.graph_id, str(cache.root.resolve()))
    with _guard:
        runtime = _runtimes.get(key)
        if runtime is None:
            runtime = DepfixRuntime(graph, cache, manifest=manifest).activate()
            _runtimes[key] = runtime
        _active_runtimes[graph.graph_id] = runtime
        return runtime


def _runtime_for_generated(graph_id: str) -> DepfixRuntime:
    with _guard:
        runtime = _active_runtimes.get(graph_id)
    if runtime is not None:
        return runtime
    settings = resolve_settings(discover=True)
    if settings.manifest is None:
        raise RuntimeError("The generated Depfix alias has no active or automatically discovered manifest")
    runtime = activate_manifest(settings.manifest, settings)
    if runtime.graph.graph_id != graph_id:
        raise RuntimeError("The generated Depfix alias does not match the discovered manifest")
    return runtime


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
        and request.isolation == isolation
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


def _group_identity(normalized: tuple[str, ...], mode: str, isolation: str, settings: Settings) -> str:
    payload = json.dumps(
        {
            "specifiers": normalized,
            "mode": mode,
            "isolation": isolation,
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
    for alias in aliases:
        node = graph.node_index[alias.node]
        binding = ModuleBinding(
            runtime,
            node.id,
            node.distribution,
            node.version,
            node.artifact,
            resolved_realm_id(graph, (node.id,)),
            specifiers,
            mode,
        )
        for provided in node.provided_modules:
            root = provided.split(".", 1)[0]
            current = bindings.get(root)
            if current is not None and current.fingerprint != binding.fingerprint:
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
        and group.isolation == isolation
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
