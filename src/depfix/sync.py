"""Materialize locked artifacts into the private cache."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
from collections.abc import Callable
from pathlib import Path

from .cache import Cache
from .models import Artifact, LockedGraph
from .progress import ProgressReporter
from .wheel import extract_wheel


def sync_graph(
    graph: LockedGraph,
    cache: Cache,
    *,
    offline: bool = False,
    verify: bool = True,
    progress: ProgressReporter | None = None,
    artifact_rebuilder: Callable[[Artifact], Path] | None = None,
) -> None:
    cache.reconcile_intermediates()
    allowed_hosts = _policy_strings(graph.policy.get("allowed-hosts"))
    allow_insecure = bool(graph.policy.get("allow-insecure-transport", False))
    nodes_by_artifact: dict[str, list[str]] = {}
    for node in graph.nodes:
        nodes_by_artifact.setdefault(node.artifact, []).extend(node.provided_modules)
    pending = [artifact for artifact in graph.artifacts if not cache.has_package(artifact.sha256)]
    if progress is not None and pending:
        count = len(pending)
        progress.emit("prepare", f"{count} {'artifact' if count == 1 else 'artifacts'}")
    for artifact in graph.artifacts:
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
        cache.blob_path(artifact.sha256).unlink(missing_ok=True)
        if artifact.source_sha256 and artifact.source_sha256 != artifact.sha256:
            cache.blob_path(artifact.source_sha256).unlink(missing_ok=True)
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
        for path in temporary.rglob("*"):
            try:
                if path.is_dir():
                    path.chmod(0o555)
                else:
                    path.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
            except OSError:
                pass
        try:
            os.replace(temporary, destination)
        except OSError:
            if not destination.is_dir():
                raise
    finally:
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)


def _remove_incomplete_target(path: Path) -> None:
    def make_writable_and_retry(function, value, _error):  # type: ignore[no-untyped-def]
        Path(value).chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        function(value)

    if path.is_dir():
        shutil.rmtree(path, onerror=make_writable_and_retry)
    else:
        path.unlink()


def _policy_strings(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (tuple, list)) and all(isinstance(item, str) for item in value):
        return tuple(value)
    raise ValueError("network policy values must be strings or arrays of strings")
