"""Materialize locked artifacts into the private cache."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from collections.abc import Callable
from functools import partial
from pathlib import Path

from .cache import Cache, _remove_path
from .io_scheduler import IOWork, run_weighted_io
from .models import Artifact, LockedGraph
from .progress import ProgressReporter
from .target_permissions import harden_runtime_target, runtime_target_modes_safely_repairable
from .wheel import extract_wheel


def sync_graph(
    graph: LockedGraph,
    cache: Cache,
    *,
    offline: bool = False,
    verify: bool = True,
    progress: ProgressReporter | None = None,
    artifact_rebuilder: Callable[[Artifact], Path] | None = None,
    artifact_ids: frozenset[str] | None = None,
    max_io_workers: int = 16,
) -> None:
    cache.reconcile_intermediates()
    allowed_hosts = _policy_strings(graph.policy.get("allowed-hosts"))
    allow_insecure = bool(graph.policy.get("allow-insecure-transport", False))
    nodes_by_artifact: dict[str, list[str]] = {}
    for node in graph.nodes:
        nodes_by_artifact.setdefault(node.artifact, []).extend(node.provided_modules)
    artifacts = tuple(artifact for artifact in graph.artifacts if artifact_ids is None or artifact.id in artifact_ids)
    pending = [artifact for artifact in artifacts if not cache.has_package(artifact.sha256)]
    if progress is not None and pending:
        count = len(pending)
        progress.emit("prepare", f"{count} {'artifact' if count == 1 else 'artifacts'}")
    _prefetch_artifacts(
        pending,
        cache,
        offline=offline,
        verify=verify,
        allowed_hosts=allowed_hosts,
        allow_insecure=allow_insecure,
        progress=progress,
        max_io_workers=max_io_workers,
    )
    for artifact in artifacts:
        destination = cache.unpacked_path(artifact.id)
        with cache.lock("target:" + artifact.id):
            if (
                not offline
                and artifact_rebuilder is not None
                and artifact.build_backend
                and not cache.has_package(artifact.sha256)
                and not cache.has_blob(artifact.sha256)
            ):
                artifact_rebuilder(artifact)
            with cache._artifact_lock(artifact.sha256):
                _sync_artifact(
                    artifact,
                    destination,
                    cache,
                    nodes_by_artifact,
                    offline=offline,
                    verify=verify,
                    allowed_hosts=allowed_hosts,
                    allow_insecure=allow_insecure,
                    progress=progress,
                )


def _prefetch_artifacts(
    artifacts: list[Artifact],
    cache: Cache,
    *,
    offline: bool,
    verify: bool,
    allowed_hosts: tuple[str, ...],
    allow_insecure: bool,
    progress: ProgressReporter | None,
    max_io_workers: int,
) -> None:
    """Acquire exact remote blobs concurrently; materialization remains ordered."""
    if offline:
        return
    pending = [artifact for artifact in artifacts if not artifact.build_backend and not cache.has_blob(artifact.sha256)]
    for artifact in pending:
        if progress is not None:
            progress.emit("download", f"{artifact.distribution}=={artifact.version}")
    run_weighted_io(
        tuple(
            IOWork(
                position,
                artifact.size,
                partial(
                    cache.fetch_artifact,
                    artifact,
                    offline=False,
                    verify=verify,
                    allowed_hosts=allowed_hosts,
                    allow_insecure=allow_insecure,
                ),
            )
            for position, artifact in enumerate(pending)
        ),
        capacity=max_io_workers,
    )


def _sync_artifact(
    artifact: Artifact,
    destination: Path,
    cache: Cache,
    nodes_by_artifact: dict[str, list[str]],
    *,
    offline: bool,
    verify: bool,
    allowed_hosts: tuple[str, ...],
    allow_insecure: bool,
    progress: ProgressReporter | None,
) -> None:
    """Materialize one artifact while its target and artifact locks are held."""
    for abandoned in destination.parent.glob(destination.name + ".*"):
        _remove_incomplete_target(abandoned)
    if (
        destination.is_dir()
        and cache._target_contents_are_complete(destination, artifact.sha256)
        and runtime_target_modes_safely_repairable(destination)
    ):
        harden_runtime_target(destination)
    if not cache.has_package(artifact.sha256):
        if progress is not None and not cache.has_blob(artifact.sha256):
            progress.emit("download", f"{artifact.distribution}=={artifact.version}")
        blob = cache.fetch_artifact(
            artifact,
            offline=offline,
            verify=verify,
            allowed_hosts=allowed_hosts,
            allow_insecure=allow_insecure,
            _lock_held=True,
        )
        if destination.exists():
            _remove_incomplete_target(destination)
        if artifact.filename.lower().endswith(".whl"):
            extract_wheel(blob, destination)
        elif artifact.filename.lower().endswith(".py"):
            roots = sorted(set(nodes_by_artifact.get(artifact.id, ())))
            if len(roots) != 1:
                raise ValueError(f"single-file artifact {artifact.id} must expose exactly one root")
            _materialize_python_file(blob, destination, roots[0], artifact.sha256)
        else:
            raise ValueError(f"unsupported locked artifact type: {artifact.filename}")
    cache.record_artifact(artifact)
    if cache.has_package(artifact.sha256):
        cache.discard_blob(artifact.sha256, _lock_held=True)
        if artifact.source_sha256 and artifact.source_sha256 != artifact.sha256:
            cache.discard_blob(artifact.source_sha256)
        built = cache.root / "built-wheels" / artifact.sha256
        if built.exists():
            _remove_incomplete_target(built)


def _materialize_python_file(
    blob: Path,
    destination: Path,
    module_root: str,
    artifact_sha256: str,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=destination.name + ".", dir=destination.parent))
    try:
        purelib = temporary / "purelib"
        purelib.mkdir()
        target = purelib / f"{module_root}.py"
        shutil.copyfile(blob, target)
        target_hash = hashlib.sha256(target.read_bytes()).hexdigest()
        (temporary / ".complete").write_text(
            json.dumps(
                {
                    "format_version": 1,
                    "kind": "python-file",
                    "artifact_sha256": artifact_sha256,
                    "module": module_root,
                    "installed_files": 1,
                    "file_hashes": {f"purelib/{module_root}.py": target_hash},
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        harden_runtime_target(temporary, writable_root=True)
        try:
            os.replace(temporary, destination)
        except OSError:
            if not destination.is_dir():
                raise
        harden_runtime_target(destination)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)


def _remove_incomplete_target(path: Path) -> None:
    _remove_path(path)


def _policy_strings(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (tuple, list)) and all(isinstance(item, str) for item in value):
        return tuple(value)
    raise ValueError("network policy values must be strings or arrays of strings")
