"""Documented project APIs backing export, install, verify, and bundle CLI."""

from __future__ import annotations

import compileall
import contextlib
import hashlib
import json
import os
import shutil
import stat
import sys
import tempfile
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath

from packaging.markers import Marker, default_environment

from ._version import __version__
from .aliases import generate_aliases
from .cache import Cache
from .config import ImportDeclaration, ProjectConfig
from .errors import (
    BundleError,
    CacheError,
    DefaultImportConflictError,
    HashMismatchError,
    IntegrityError,
    ManifestError,
    OfflineArtifactMissingError,
    redact,
)
from .manifest import (
    assert_compatible_environment,
    canonical_resolution_identity,
    computed_graph_id,
    current_environment,
    dumps_manifest,
    load_manifest,
    write_manifest,
)
from .models import Alias, LockedGraph, RequestGroup, resolved_realm_id
from .progress import ProgressReporter
from .resolver import Resolver
from .scanner import DynamicRequest, ScanGroup, scan_project
from .settings import Settings, resolve_settings
from .sources import parse_source
from .sync import sync_graph
from .uv_backend import UvBackend

BUNDLE_FORMAT_VERSION = 1


@dataclass(frozen=True, slots=True)
class ExportResult:
    manifest: Path
    manifest_id: str
    requests: int
    artifacts: int
    dynamic_requests: tuple[DynamicRequest, ...]
    ide_path: Path


@dataclass(frozen=True, slots=True)
class InstallResult:
    manifest: Path
    manifest_id: str
    artifacts: int
    target: Path
    warm: bool


@dataclass(frozen=True, slots=True)
class PackageInstallResult:
    manifest: Path
    manifest_id: str
    requests: int
    artifacts: int
    packages: tuple[str, ...]
    store: Path
    warm: bool


@dataclass(frozen=True, slots=True)
class BundleResult:
    bundle: Path
    manifest_id: str
    files: int
    sha256: str


@dataclass(frozen=True, slots=True)
class VerificationResult:
    source: Path
    manifest_id: str
    artifacts: int
    complete: bool


def export_project(
    root: str | os.PathLike[str] = ".",
    *,
    output: str | os.PathLike[str] = ".depfix/imports.lock",
    include: Iterable[str] = (),
    exclude: Iterable[str] = (),
    index_url: str | None = None,
    extra_index_url: Iterable[str] = (),
    refresh: bool = False,
    prefer_newest: bool | None = None,
) -> ExportResult:
    project_root = Path(root).expanduser().resolve()
    result = scan_project(project_root, include=include, exclude=exclude)
    sites = list(result.requests)
    config_path = project_root / ".depfix" / "config.toml"
    if config_path.is_file():
        sites.extend(_dynamic_config_sites(config_path, project_root))
    if not sites:
        raise ManifestError(
            "No static Depfix requests were found",
            source=str(project_root),
            remediation="add import_module/load_package calls or pass include=[...]",
        )
    settings = resolve_settings(
        cache_dir=None,
        index_url=index_url,
        extra_index_url=tuple(extra_index_url),
        prefer_newest=prefer_newest,
        discover=True,
        discovery_start=project_root,
    )
    aliases = _unique_aliases(site.suggested_alias for site in sites)
    declarations = tuple(
        ImportDeclaration(
            alias,
            site.original_specifier,
            site.module,
            api="load_package" if site.group_id else site.api,
            source_file=site.source_file,
            source_line=site.line,
            source_column=site.column,
            assignment=site.assignment or "",
            base_dir=site.base_dir,
            group_id=site.group_id,
            mode=site.mode,
            enclosing_function=site.enclosing_function,
            isolation=site.isolation,
            allow_unsafe=settings.allow_unsafe if site.allow_unsafe is None else site.allow_unsafe,
            prefer_newest=settings.prefer_newest if site.prefer_newest is None else site.prefer_newest,
            index_url=site.index_url,
            extra_index_url=site.extra_index_url,
        )
        for alias, site in zip(aliases, sites, strict=True)
    )
    cache = Cache(settings.cache_dir)
    progress = ProgressReporter(settings.log_level)
    policy = _config_policy(config_path)
    policy.update(
        {
            "mode": "prepared",
            "frozen": True,
            "index": _sanitized_index(settings.index_url),
            "extra-indexes": tuple(_sanitized_index(item) for item in settings.extra_index_url),
            "allow-unsafe": settings.allow_unsafe,
            "prefer-newest": settings.prefer_newest,
        }
    )
    graph = Resolver(cache, settings=settings, progress=progress).resolve(
        ProjectConfig(config_path, declarations, policy)
    )
    diagnostics = [_format_dynamic(item) for item in result.dynamic_requests]
    graph, group_diagnostics = _attach_scan_groups(graph, result.groups)
    diagnostics.extend(group_diagnostics)
    graph = replace(graph, dynamic_diagnostics=tuple(diagnostics))
    graph = replace(graph, graph_id=computed_graph_id(graph))
    destination = Path(output)
    if not destination.is_absolute():
        destination = project_root / destination
    destination = destination.resolve()
    write_manifest(graph, destination)
    sync_graph(graph, cache, offline=False, progress=progress)
    _record_export_origins(cache, graph, destination)
    ide_path = cache.root / "ide" / graph.graph_id.removeprefix("sha256:")
    generate_aliases(graph, cache, ide_path)
    if destination.parent == project_root / ".depfix":
        _write_state_gitignore(destination.parent)
    return ExportResult(
        destination, graph.graph_id, len(graph.aliases), len(graph.artifacts), result.dynamic_requests, ide_path
    )


def install_manifest(
    manifest: str | os.PathLike[str],
    *,
    frozen: bool = True,
    offline: bool = False,
    cached_only: bool = False,
    local: bool = False,
    target: str | os.PathLike[str] | None = None,
    compile_bytecode: bool = False,
    cache_dir: str | os.PathLike[str] | None = None,
    reason: str | None = None,
) -> InstallResult:
    source_file, source_line = _caller_location()
    if target is not None and not local:
        raise ValueError("target requires local=True so the selected artifacts are copied there")
    source = Path(manifest).expanduser().resolve()
    if source.suffix == ".depfixbundle":
        return _install_bundle(
            source,
            frozen=frozen,
            offline=True,
            local=local,
            target=target,
            cache_dir=cache_dir,
            reason=reason,
        )
    graph = load_manifest(source)
    assert_compatible_environment(graph, source)
    settings = resolve_settings(
        manifest=source, frozen=frozen, offline=offline or cached_only, cache_dir=cache_dir, discover=False
    )
    cache = Cache(settings.cache_dir)
    progress = ProgressReporter(settings.log_level)
    marker = cache.root / "manifests" / graph.graph_id.removeprefix("sha256:") / "installed.json"
    warm = marker.is_file()
    artifact_rebuilder = Resolver(cache, settings=settings)
    allowed_hosts = _policy_strings(graph.policy.get("allowed-hosts"))
    allow_insecure = bool(graph.policy.get("allow-insecure-transport", False))
    try:
        sync_graph(
            graph,
            cache,
            offline=offline or cached_only,
            progress=progress,
            artifact_rebuilder=lambda artifact: artifact_rebuilder.reacquire_built_artifact(
                artifact,
                allowed_hosts=allowed_hosts,
                allow_insecure=allow_insecure,
            ),
        )
    except IntegrityError:
        raise
    except CacheError as exc:
        if offline or cached_only:
            missing = next((item for item in graph.artifacts if not cache.has_package(item.sha256)), None)
            raise OfflineArtifactMissingError(
                "A manifest package is not materialized and its ephemeral input is unavailable offline",
                manifest=source,
                artifact_hash=missing.sha256 if missing is not None else "",
                offline=True,
                remediation="install from a complete bundle or prepare the exact manifest on a connected host",
            ) from exc
        raise
    cache.record_installation(
        graph,
        root_nodes=_graph_roots(graph),
        kind="command" if reason else "python-code",
        description=reason or "depfix.project.install_manifest(...)",
        command=reason or "",
        source_file="" if reason else source_file,
        source_line=0 if reason else source_line,
        manifest=str(source),
    )
    ide_path = cache.root / "ide" / graph.graph_id.removeprefix("sha256:")
    generate_aliases(graph, cache, ide_path)
    if compile_bytecode:
        _compile_graph_bytecode(graph, cache)
    destination = Path(target).expanduser().resolve() if target is not None else cache.root / "targets"
    if local:
        destination = source.parent / "runtime" if target is None else destination
        _materialize_local(graph, cache, destination)
        _write_state_gitignore(source.parent)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        json.dumps(
            {
                "manifest_id": graph.graph_id,
                "artifacts": len(graph.artifacts),
                "target": str(destination),
                "compile_bytecode": compile_bytecode,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    count = len(graph.artifacts)
    progress.emit("ready", f"{count} {'artifact' if count == 1 else 'artifacts'}")
    return InstallResult(source, graph.graph_id, len(graph.artifacts), destination, warm)


def install_packages(
    requirements: Iterable[str],
    *,
    constraints: Iterable[str] = (),
    refresh: bool = False,
    offline: bool | None = None,
    index_url: str | None = None,
    extra_index_url: Iterable[str] = (),
    prefer_newest: bool | None = None,
    cache_dir: str | os.PathLike[str] | None = None,
    base_dir: str | os.PathLike[str] | None = None,
    reason: str | None = None,
) -> PackageInstallResult:
    """Resolve package roots as one Depfix group and populate the shared store."""
    source_file, source_line = _caller_location()
    root = Path(base_dir or Path.cwd()).expanduser().resolve()
    selected: dict[str, str] = {}
    marker_environment = {key: str(value) for key, value in default_environment().items()}
    requested = (requirements,) if isinstance(requirements, str) else requirements
    constrained = (constraints,) if isinstance(constraints, str) else constraints
    for raw in requested:
        specifier = _install_specifier(raw, root)
        source = parse_source(specifier, base_dir=root)
        if source.marker and not Marker(source.marker).evaluate(marker_environment):
            continue
        selected.setdefault(source.normalized, specifier)
    if not selected:
        raise ValueError("pip install requires at least one active package requirement")
    normalized_constraints = tuple(sorted(dict.fromkeys(item.strip() for item in constrained if item.strip())))
    config_path = root / ".depfix" / "config.toml"
    settings = resolve_settings(
        offline=offline,
        index_url=index_url,
        extra_index_url=tuple(extra_index_url),
        prefer_newest=prefer_newest,
        cache_dir=cache_dir,
        discover=True,
        discovery_start=root,
    )
    policy = _config_policy(config_path)
    policy.update(
        {
            "mode": "package-install",
            "index": _sanitized_index(settings.index_url),
            "extra-indexes": tuple(_sanitized_index(item) for item in settings.extra_index_url),
            "allow-unsafe": settings.allow_unsafe,
            "prefer-newest": settings.prefer_newest,
            "constraints": normalized_constraints,
        }
    )
    ordered = tuple(selected[item] for item in sorted(selected))
    declarations = tuple(
        ImportDeclaration(
            f"package_{index:04d}",
            specifier,
            api="load_package",
            base_dir=root,
            mode="package-install",
            allow_unsafe=settings.allow_unsafe,
            prefer_newest=settings.prefer_newest,
        )
        for index, specifier in enumerate(ordered)
    )
    cache = Cache(settings.cache_dir)
    progress = ProgressReporter(settings.log_level)
    identity = _package_install_identity(tuple(sorted(selected)), normalized_constraints, policy, settings)
    manifest = cache.root / "installs" / identity / "imports.lock"
    canonical_identity = canonical_resolution_identity(
        tuple(sorted(selected)),
        normalized_constraints,
        index_url=settings.index_url,
        extra_index_url=settings.extra_index_url,
        prefer_newest=settings.prefer_newest,
        allow_unsafe=settings.allow_unsafe,
        resolution_policy=_canonical_resolution_policy(policy),
    )
    canonical_manifest = cache.root / "plans" / canonical_identity / "imports.lock"
    with cache.lock("package-install:" + identity):
        reusable_manifest = manifest if manifest.is_file() else canonical_manifest
        warm = reusable_manifest.is_file() and not refresh
        if warm:
            graph = load_manifest(reusable_manifest)
            assert_compatible_environment(graph, reusable_manifest)
        else:
            graph = Resolver(cache, settings=settings, progress=progress).resolve(
                ProjectConfig(config_path, declarations, policy)
            )
        cache.reserve_artifacts({artifact.sha256 for artifact in graph.artifacts})
        sync_graph(graph, cache, offline=settings.offline, progress=progress)
        if not manifest.is_file() or refresh:
            write_manifest(graph, manifest)
        with cache.lock("canonical-resolution:" + canonical_identity):
            if not canonical_manifest.is_file() or refresh:
                write_manifest(graph, canonical_manifest)
        command = reason or ""
        cache.record_installation(
            graph,
            root_nodes=tuple(alias.node for alias in graph.aliases),
            kind="command" if reason else "python-code",
            description=reason or "depfix.project.install_packages(...)",
            command=command,
            source_file="" if reason else source_file,
            source_line=0 if reason else source_line,
            manifest=str(manifest),
        )
    nodes = graph.node_index
    packages = tuple(
        sorted(f"{nodes[alias.node].distribution}=={nodes[alias.node].version}" for alias in graph.aliases)
    )
    progress.emit("ready", f"{len(packages)} package roots in the shared store")
    return PackageInstallResult(
        manifest,
        graph.graph_id,
        len(graph.aliases),
        len(graph.artifacts),
        packages,
        cache.root,
        warm,
    )


def _record_export_origins(cache: Cache, graph: LockedGraph, manifest: Path) -> None:
    grouped: dict[tuple[str, int, str, str], list[str]] = {}
    for alias in graph.aliases:
        api = "default" if alias.mode == "default" else "using" if alias.mode.startswith("using") else alias.api
        key = (alias.source_file, alias.source_line, api, alias.group)
        grouped.setdefault(key, []).append(alias.node)
    for (source_file, source_line, api, _group), roots in sorted(grouped.items()):
        cache.record_installation(
            graph,
            root_nodes=tuple(dict.fromkeys(roots)),
            kind="python-code",
            description=f"depfix.{api}(...) exported from {source_file or manifest}",
            source_file=source_file,
            source_line=source_line,
            manifest=str(manifest),
        )


def _graph_roots(graph: LockedGraph) -> tuple[str, ...]:
    aliases = tuple(dict.fromkeys(alias.node for alias in graph.aliases))
    if aliases:
        return aliases
    children = {child for node in graph.nodes for child in node.dependencies.values()}
    return tuple(node.id for node in graph.nodes if node.id not in children)


def _caller_location() -> tuple[str, int]:
    try:
        frame = sys._getframe(2)
    except (AttributeError, ValueError):
        return "", 0
    return frame.f_code.co_filename, frame.f_lineno


def verify_manifest(
    source: str | os.PathLike[str], *, cache_dir: str | os.PathLike[str] | None = None
) -> VerificationResult:
    path = Path(source).expanduser().resolve()
    if path.suffix == ".depfixbundle":
        graph, _metadata = _read_bundle(path, promote_to=None)
        return VerificationResult(path, graph.graph_id, len(graph.artifacts), True)
    graph = load_manifest(path)
    assert_compatible_environment(graph, path)
    cache = Cache(Path(cache_dir) if cache_dir is not None else None)
    for artifact in graph.artifacts:
        if not cache.has_package(artifact.sha256):
            raise CacheError(
                "A manifest package is not completely materialized",
                artifact_hash=artifact.sha256,
                remediation="install the manifest on a connected host or from a complete bundle",
            )
    return VerificationResult(path, graph.graph_id, len(graph.artifacts), True)


def create_bundle(
    manifest: str | os.PathLike[str],
    output: str | os.PathLike[str],
    *,
    include_depfix_runtime: bool = False,
    cache_dir: str | os.PathLike[str] | None = None,
) -> BundleResult:
    manifest_path = Path(manifest).expanduser().resolve()
    graph = load_manifest(manifest_path)
    cache = Cache(Path(cache_dir) if cache_dir is not None else None)
    runtime_wheels: list[Path] = []
    if include_depfix_runtime:
        runtime_wheels = _runtime_wheels(cache)
    destination = Path(output).expanduser().resolve()
    if destination.suffix != ".depfixbundle":
        raise BundleError(
            "Bundle outputs must use the .depfixbundle extension",
            source=str(destination),
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    manifest_bytes = dumps_manifest(graph).encode("utf-8")
    metadata = {
        "format_version": BUNDLE_FORMAT_VERSION,
        "manifest_id": graph.graph_id,
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "target": {
            "implementation": graph.environment.python_implementation,
            "python_version": graph.environment.python_version,
            "abi": graph.environment.abi,
            "platform": graph.environment.platform,
            "architecture": graph.environment.machine,
        },
        "artifacts": [artifact.sha256 for artifact in sorted(graph.artifacts, key=lambda item: item.sha256)],
        "runtime_wheels": [
            {
                "filename": path.name,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "size": path.stat().st_size,
            }
            for path in sorted(runtime_wheels, key=lambda item: item.name)
        ],
    }
    files = 2 + len(graph.artifacts) + len(runtime_wheels)
    rebuild_settings = resolve_settings(cache_dir=cache_dir, frozen=False, offline=False, discover=False)
    artifact_rebuilder = Resolver(cache, settings=rebuild_settings)
    allowed_hosts = _policy_strings(graph.policy.get("allowed-hosts"))
    allow_insecure = bool(graph.policy.get("allow-insecure-transport", False))
    with contextlib.ExitStack() as locks:
        archive_paths: dict[str, Path] = {}
        for artifact in sorted(graph.artifacts, key=lambda item: item.sha256):
            locks.enter_context(cache.lock("target:" + artifact.id))
            if not cache.has_blob(artifact.sha256) and artifact.build_backend:
                artifact_rebuilder.reacquire_built_artifact(
                    artifact,
                    allowed_hosts=allowed_hosts,
                    allow_insecure=allow_insecure,
                )
            locks.enter_context(cache._artifact_lock(artifact.sha256))
            archive_paths[artifact.sha256] = cache.fetch_artifact(
                artifact,
                verify=True,
                allowed_hosts=allowed_hosts,
                allow_insecure=allow_insecure,
                _lock_held=True,
            )
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            _write_zip_entry(
                archive, "bundle.json", json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode() + b"\n"
            )
            _write_zip_entry(archive, "manifest/imports.lock", manifest_bytes)
            for artifact in sorted(graph.artifacts, key=lambda item: item.sha256):
                _write_zip_entry(
                    archive, f"artifacts/sha256/{artifact.sha256}", archive_paths[artifact.sha256].read_bytes()
                )
            for wheel in sorted(runtime_wheels, key=lambda item: item.name):
                _write_zip_entry(archive, f"runtime/wheels/{wheel.name}", wheel.read_bytes())
            if runtime_wheels:
                bootstrap = (
                    b"Install all wheels in runtime/wheels without an index, then run:\n"
                    b"    depfix install manifest/imports.lock --offline --frozen\n"
                )
                _write_zip_entry(archive, "runtime/BOOTSTRAP.txt", bootstrap)
                files += 1
        for artifact in graph.artifacts:
            cache.discard_blob(artifact.sha256, _lock_held=True)
            if artifact.source_sha256 and artifact.source_sha256 != artifact.sha256:
                cache.discard_blob(artifact.source_sha256)
            shutil.rmtree(cache.root / "built-wheels" / artifact.sha256, ignore_errors=True)
    os.replace(temporary, destination)
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    for wheel in runtime_wheels:
        wheel_digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
        wheel.unlink(missing_ok=True)
        cache.discard_blob(wheel_digest)
    runtime_store = cache.root / "built-wheels" / "depfix-runtime"
    with contextlib.suppress(OSError):
        runtime_store.rmdir()
    return BundleResult(destination, graph.graph_id, files, digest)


def _install_bundle(
    bundle: Path,
    *,
    frozen: bool,
    offline: bool,
    local: bool,
    target: str | os.PathLike[str] | None,
    cache_dir: str | os.PathLike[str] | None,
    reason: str | None,
) -> InstallResult:
    cache = Cache(Path(cache_dir) if cache_dir is not None else None)
    graph, _metadata = _read_bundle(bundle, promote_to=cache)
    manifest_path = cache.root / "manifests" / graph.graph_id.removeprefix("sha256:") / "imports.lock"
    write_manifest(graph, manifest_path)
    return install_manifest(
        manifest_path,
        frozen=frozen,
        offline=True,
        local=local,
        target=target,
        cache_dir=cache_dir,
        reason=reason,
    )


def _read_bundle(bundle: Path, *, promote_to: Cache | None) -> tuple[LockedGraph, dict[str, object]]:
    if not bundle.is_file():
        raise BundleError("Bundle does not exist", source=str(bundle))
    with zipfile.ZipFile(bundle) as archive:
        infos = archive.infolist()
        if len(infos) > 50_000 or sum(info.file_size for info in infos) > 4 * 1024 * 1024 * 1024:
            raise BundleError("Bundle exceeds safety limits", source=str(bundle))
        seen: set[str] = set()
        folded: set[str] = set()
        for info in infos:
            path = PurePosixPath(info.filename)
            mode = info.external_attr >> 16
            if path.is_absolute() or ".." in path.parts or "\\" in info.filename or stat.S_ISLNK(mode):
                raise BundleError("Unsafe bundle member", source=info.filename)
            normalized = str(path)
            if normalized in seen or normalized.casefold() in folded:
                raise BundleError("Duplicate or case-folding-colliding bundle member", source=info.filename)
            seen.add(normalized)
            folded.add(normalized.casefold())
        try:
            metadata = json.loads(archive.read("bundle.json"))
            manifest_bytes = archive.read("manifest/imports.lock")
        except (KeyError, json.JSONDecodeError) as exc:
            raise BundleError("Bundle metadata or manifest is missing/malformed", source=str(bundle)) from exc
        if metadata.get("format_version") != BUNDLE_FORMAT_VERSION:
            raise BundleError("Unsupported bundle format", source=str(bundle))
        if hashlib.sha256(manifest_bytes).hexdigest() != metadata.get("manifest_sha256"):
            raise HashMismatchError("Bundled manifest hash mismatch", source=str(bundle))
        temporary_root = Path(tempfile.mkdtemp(prefix="depfix-bundle-manifest-"))
        try:
            manifest_path = temporary_root / "imports.lock"
            manifest_path.write_bytes(manifest_bytes)
            graph = load_manifest(manifest_path)
        finally:
            shutil.rmtree(temporary_root, ignore_errors=True)
        if graph.graph_id != metadata.get("manifest_id"):
            raise BundleError("Bundle and manifest identities disagree", source=str(bundle))
        assert_compatible_environment(graph, bundle)
        for artifact in graph.artifacts:
            member = f"artifacts/sha256/{artifact.sha256}"
            try:
                data = archive.read(member)
            except KeyError as exc:
                raise BundleError("Bundle is missing a required artifact", artifact_hash=artifact.sha256) from exc
            if len(data) != artifact.size or hashlib.sha256(data).hexdigest() != artifact.sha256:
                raise HashMismatchError("Bundled artifact failed size/hash verification", artifact_hash=artifact.sha256)
            if promote_to is not None:
                temporary = promote_to.root / "tmp" / artifact.sha256
                temporary.parent.mkdir(parents=True, exist_ok=True)
                temporary.write_bytes(data)
                promote_to.fetch_url(temporary.as_uri(), artifact.sha256, expected_size=artifact.size)
                temporary.unlink(missing_ok=True)
        runtime_entries = metadata.get("runtime_wheels", [])
        if not isinstance(runtime_entries, list):
            raise BundleError("Bundle runtime wheel metadata is malformed", source=str(bundle))
        for item in runtime_entries:
            if not isinstance(item, dict):
                raise BundleError("Bundle runtime wheel metadata is malformed", source=str(bundle))
            filename = str(item.get("filename", ""))
            if not filename or PurePosixPath(filename).name != filename:
                raise BundleError("Unsafe runtime wheel filename", source=filename)
            try:
                data = archive.read(f"runtime/wheels/{filename}")
            except KeyError as exc:
                raise BundleError("Bundle is missing a declared runtime wheel", source=filename) from exc
            if len(data) != item.get("size") or hashlib.sha256(data).hexdigest() != item.get("sha256"):
                raise HashMismatchError("Bundled runtime wheel failed size/hash verification", source=filename)
    return graph, metadata


def _runtime_wheels(cache: Cache) -> list[Path]:
    project_root = Path(__file__).resolve().parents[2]
    settings = resolve_settings(cache_dir=cache.root.parent, discover=False)
    runtime_store = cache.root / "built-wheels" / "depfix-runtime"
    runtime_store.mkdir(parents=True, exist_ok=True)
    output: Path | None = None
    root_wheels = sorted((project_root / "dist").glob(f"depfix-{__version__}-*.whl"))
    if len(root_wheels) > 1:
        raise BundleError(
            "Several Depfix release wheels match the current version",
            candidates=tuple(path.name for path in root_wheels),
            remediation="retain exactly one tested universal release wheel in dist/",
        )
    if root_wheels:
        selected = root_wheels[0]
        root_wheel = runtime_store / selected.name
        shutil.copy2(selected, root_wheel)
        specifier = f"file:{root_wheel}"
    elif (project_root / "pyproject.toml").is_file():
        backend = UvBackend(settings, cache)
        temporary_root = cache.root / "tmp"
        temporary_root.mkdir(parents=True, exist_ok=True)
        output = Path(tempfile.mkdtemp(prefix="depfix-runtime-wheels-", dir=temporary_root))
        selected = backend.build_wheel(project_root, output=output)
        root_wheel = runtime_store / selected.name
        shutil.copy2(selected, root_wheel)
        specifier = f"file:{root_wheel}"
    else:
        root_wheel = None
        specifier = f"depfix=={__version__}"
    # Resolve the exact root and dependency wheels. The source-checkout path
    # uses the already-tested dist wheel when available; installed releases
    # retrieve their own exact published version through the configured index.
    declaration = ImportDeclaration("depfix_runtime", specifier, api="load_package", base_dir=runtime_store)
    graph = Resolver(cache, settings=settings).resolve(ProjectConfig(Path("<bundle-runtime>"), (declaration,), {}))
    wheels: list[Path] = []
    for artifact in graph.artifacts:
        if not artifact.filename.endswith(".whl"):
            raise BundleError(
                "The Depfix air-gap runtime resolved a non-wheel artifact",
                source=artifact.filename,
                remediation="publish compatible wheels for Depfix and every runtime dependency",
            )
        source = cache.blob_path(artifact.sha256)
        target = runtime_store / artifact.filename
        if root_wheel is not None and artifact.sha256 == hashlib.sha256(root_wheel.read_bytes()).hexdigest():
            target = root_wheel
        elif not target.exists():
            shutil.copy2(source, target)
        wheels.append(target)
    if output is not None:
        shutil.rmtree(output, ignore_errors=True)
    return sorted(wheels, key=lambda path: path.name)


def _materialize_local(graph: LockedGraph, cache: Cache, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for artifact in graph.artifacts:
        source = cache.unpacked_path(artifact.id)
        target = destination / artifact.sha256[:16]
        if not target.exists():
            shutil.copytree(source, target)


def _compile_graph_bytecode(graph: LockedGraph, cache: Cache) -> None:
    for artifact in graph.artifacts:
        root = cache.unpacked_path(artifact.id)
        for directory in [root, *(path for path in root.rglob("*") if path.is_dir())]:
            try:
                directory.chmod(0o755)
            except OSError:
                pass
        if not compileall.compile_dir(root / "purelib", quiet=1, force=True):
            raise ManifestError(
                "Bytecode compilation failed for a prepared artifact",
                artifact_hash=artifact.sha256,
            )
        for path in sorted(root.rglob("*"), reverse=True):
            try:
                path.chmod(0o555 if path.is_dir() else 0o444)
            except OSError:
                pass
        try:
            root.chmod(0o555)
        except OSError:
            pass


def _write_zip_entry(archive: zipfile.ZipFile, name: str, data: bytes) -> None:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    archive.writestr(info, data)


def _write_state_gitignore(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / ".gitignore"
    if not path.exists():
        path.write_text("*\n!.gitignore\n!imports.lock\n!config.toml\n", encoding="utf-8")


def _attach_scan_groups(graph: LockedGraph, scanned: tuple[ScanGroup, ...]) -> tuple[LockedGraph, list[str]]:
    aliases = list(graph.aliases)
    used = {alias.name for alias in aliases}
    groups: list[RequestGroup] = []
    diagnostics: list[str] = []
    nodes = graph.node_index
    default_roots: dict[str, tuple[str, str]] = {}
    for group in scanned:
        roots = [alias for alias in aliases if alias.group == group.id and alias.api == "load_package"]
        providers: dict[str, list[Alias]] = {}
        provided_names: set[str] = set()
        for alias in roots:
            node = nodes[alias.node]
            provided_names.update(node.provided_modules)
            for logical in node.provided_modules:
                providers.setdefault(logical.split(".", 1)[0], []).append(alias)
        if group.mode == "default":
            for root, candidates in providers.items():
                for candidate in candidates:
                    node = nodes[candidate.node]
                    fingerprint = resolved_realm_id(graph, (node.id,))
                    current = default_roots.get(root)
                    if current is not None and current[0] != fingerprint:
                        raise DefaultImportConflictError(
                            "Persistent default declarations select incompatible providers",
                            module=root,
                            candidates=(current[1], f"{node.distribution}=={node.version}"),
                            source=f"{group.source_file}:{group.line}:{group.column}",
                            remediation="keep one default version or move the alternate selection into using()",
                        )
                    default_roots[root] = (fingerprint, f"{node.distribution}=={node.version}")
        generated: dict[str, str] = {}
        for logical, source_alias in group.module_aliases:
            root = logical.split(".", 1)[0]
            candidates = providers.get(root, [])
            if len(candidates) != 1:
                if not _standard_library(root):
                    diagnostics.append(
                        f"{group.source_file}:{group.line}:{group.column}: {group.mode} group {group.id} "
                        f"does not uniquely provide ordinary import {logical!r}; "
                        f"provided={sorted(provided_names)!r}"
                    )
                continue
            provider = candidates[0]
            name = _unique_generated_alias(source_alias, used)
            used.add(name)
            aliases.append(
                Alias(
                    name,
                    provider.node,
                    logical,
                    " | ".join(redact(value) for value in group.specifiers),
                    normalized_specifier=" | ".join(group.normalized_specifiers),
                    api="import_module",
                    source_file=group.source_file,
                    source_line=group.line,
                    source_column=group.column,
                    assignment=source_alias,
                    explicit_module=True,
                    isolation=provider.isolation,
                    allow_unsafe=provider.allow_unsafe,
                    index_identity=provider.index_identity,
                    source_policy=provider.source_policy,
                    group=group.id,
                    mode=group.mode,
                    enclosing_function=group.enclosing_function,
                )
            )
            generated[name] = logical
        realm_id = resolved_realm_id(graph, tuple(alias.node for alias in roots))
        options = {key: redact(value) for key, value in group.options}
        isolation = json.loads(options.get("isolation", '"auto"'))
        if not isinstance(isolation, str):
            isolation = "auto"
        rendered_allow_unsafe = options.get("allow_unsafe")
        if rendered_allow_unsafe is None:
            allow_unsafe = roots[0].allow_unsafe if roots else False
        else:
            try:
                allow_unsafe = json.loads(rendered_allow_unsafe)
            except json.JSONDecodeError:
                allow_unsafe = False
            if not isinstance(allow_unsafe, bool):
                allow_unsafe = False
        groups.append(
            RequestGroup(
                group.id,
                group.mode,
                tuple(redact(value) for value in group.specifiers),
                group.normalized_specifiers,
                tuple(alias.name for alias in roots),
                source_file=group.source_file,
                source_line=group.line,
                source_column=group.column,
                enclosing_function=group.enclosing_function,
                ordinary_imports=group.ordinary_imports,
                resolved_graph_ids=(realm_id,),
                provided_imports=tuple(sorted(provided_names)),
                module_aliases=generated,
                source_base_dir=PurePosixPath(group.source_file).parent.as_posix(),
                isolation=isolation,
                allow_unsafe=allow_unsafe,
                options=options,
            )
        )
    return replace(graph, aliases=tuple(sorted(aliases, key=lambda item: item.name)), groups=tuple(groups)), diagnostics


def _unique_generated_alias(value: str, used: set[str]) -> str:
    base = value if value.isidentifier() else "scoped_import"
    if base not in used:
        return base
    index = 2
    while f"{base}_{index}" in used:
        index += 1
    return f"{base}_{index}"


def _standard_library(root: str) -> bool:
    return root in sys.builtin_module_names or root in getattr(sys, "stdlib_module_names", set())


def _unique_aliases(values: Iterable[str]) -> list[str]:
    counts: dict[str, int] = {}
    result: list[str] = []
    for value in values:
        count = counts.get(value, 0) + 1
        counts[value] = count
        result.append(value if count == 1 else f"{value}_{count}")
    return result


def _format_dynamic(item: DynamicRequest) -> str:
    return f"{item.source_file}:{item.line}:{item.column}: {item.reason}: {item.expression}"


def _install_specifier(raw: str, base_dir: Path) -> str:
    value = raw.strip()
    if not value:
        raise ValueError("package requirements may not be empty")
    if value.startswith(("pypi:", "git:", "url:", "py:", "file:")) or " @ " in value:
        return value
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = base_dir / candidate
    if value.startswith((".", "/", "~")) or candidate.exists():
        return f"file:{candidate.resolve()}"
    return value


def _package_install_identity(
    normalized_requirements: tuple[str, ...],
    constraints: tuple[str, ...],
    policy: dict[str, object],
    settings: Settings,
) -> str:
    environment = current_environment()
    payload = {
        "requirements": normalized_requirements,
        "constraints": constraints,
        "policy": policy,
        "indexes": (settings.index_url, settings.extra_index_url),
        "prefer_newest": settings.prefer_newest,
        "environment": {
            "implementation": environment.python_implementation,
            "python": environment.python_version,
            "abi": environment.abi,
            "platform": environment.platform,
            "machine": environment.machine,
        },
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def _sanitized_index(value: str | None) -> str:
    if not value:
        return ""
    from urllib.parse import urlsplit, urlunsplit

    split = urlsplit(value)
    host = split.hostname or ""
    port = f":{split.port}" if split.port else ""
    return urlunsplit((split.scheme, host + port, split.path, "", ""))


def _policy_strings(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (tuple, list)) and all(isinstance(item, str) for item in value):
        return tuple(value)
    raise ManifestError("Network policy values must be strings or arrays of strings")


def _canonical_resolution_policy(policy: dict[str, object]) -> dict[str, object]:
    """Return project policy fields that can change candidate eligibility."""
    consumer_fields = {
        "mode",
        "isolation",
        "constraints",
        "index",
        "extra-indexes",
        "allow-unsafe",
        "prefer-newest",
        "frozen",
    }
    return {key: value for key, value in policy.items() if key not in consumer_fields}


def _config_policy(path: Path) -> dict[str, object]:
    if not path.is_file():
        return {}
    import tomllib

    try:
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ManifestError("Unable to read .depfix/config.toml", source=str(path), remediation=str(exc)) from exc
    policy = raw.get("policy", {})
    if not isinstance(policy, dict):
        raise ManifestError("[policy] in .depfix/config.toml must be a table", source=str(path))
    return {str(key): value for key, value in policy.items()}


def _dynamic_config_sites(path: Path, root: Path):  # type: ignore[no-untyped-def]
    import tomllib

    from .scanner import ScanSite, _suggest_alias
    from .sources import parse_source

    with path.open("rb") as handle:
        raw = tomllib.load(handle)
    result = []
    for index, item in enumerate(raw.get("dynamic", []), 1):
        specifier = item["specifier"]
        module = item.get("module")
        parsed = parse_source(specifier, base_dir=root)
        result.append(
            ScanSite(
                specifier,
                parsed.normalized,
                item.get("api", "import_module"),
                module,
                ".depfix/config.toml",
                index,
                0,
                None,
                root,
                "included",
                item.get("alias") or _suggest_alias(None, parsed.distribution or module or "package"),
                allow_unsafe=item.get("allow-unsafe"),
                prefer_newest=item.get("prefer-newest"),
            )
        )
    return result
