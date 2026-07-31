"""Materialize locked artifacts into the private cache."""

from __future__ import annotations

import json
import os
import shutil
import stat
import tempfile
from pathlib import Path

from .cache import Cache
from .models import LockedGraph
from .wheel import extract_wheel


def sync_graph(graph: LockedGraph, cache: Cache, *, offline: bool = False, verify: bool = True) -> None:
    allowed_hosts = _policy_strings(graph.policy.get("allowed-hosts"))
    allow_insecure = bool(graph.policy.get("allow-insecure-transport", False))
    nodes_by_artifact: dict[str, list[str]] = {}
    for node in graph.nodes:
        nodes_by_artifact.setdefault(node.artifact, []).extend(node.provided_modules)
    for artifact in graph.artifacts:
        blob = cache.fetch_artifact(
            artifact,
            offline=offline,
            verify=verify,
            allowed_hosts=allowed_hosts,
            allow_insecure=allow_insecure,
        )
        destination = cache.unpacked_path(artifact.id)
        with cache.lock("target:" + artifact.id):
            if _valid_target(destination, artifact.sha256):
                continue
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
        (temporary / ".complete").write_text(
            json.dumps(
                {
                    "format_version": 1,
                    "kind": "python-file",
                    "artifact_sha256": artifact_sha256,
                    "module": module_root,
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


def _valid_target(destination: Path, artifact_sha256: str) -> bool:
    marker = destination / ".complete"
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, TypeError, json.JSONDecodeError):
        return False
    return (
        data.get("format_version") == 1
        and data.get("artifact_sha256") == artifact_sha256
        and (destination / "purelib").is_dir()
    )


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
