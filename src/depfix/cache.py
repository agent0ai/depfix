"""Immutable, content-addressed artifact cache."""

from __future__ import annotations

import atexit
import contextlib
import hashlib
import http.client
import json
import os
import platform
import re
import shutil
import stat
import sys
import sysconfig
import tempfile
import threading
import time
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from packaging.utils import canonicalize_name
from platformdirs import user_cache_path

from ._file_urls import file_url_to_path
from .errors import CacheError, IntegrityError, redact
from .models import Artifact, LockedGraph

_IS_WINDOWS = os.name == "nt"
_DEFAULT_MAX_ARTIFACT_SIZE = 1024 * 1024 * 1024
_DOWNLOAD_ATTEMPTS = 3
_AUTOMATIC_CLEANUP_INTERVAL_SECONDS = 24 * 60 * 60
_RESERVATION_GRACE_SECONDS = 60 * 60
_USAGE_WRITE_INTERVAL_SECONDS = 60 * 60
_STALE_INTERMEDIATE_SECONDS = 60 * 60
_LEGACY_STALE_INTERMEDIATE_SECONDS = 24 * 60 * 60


@dataclass(frozen=True, slots=True)
class PackageInstallReason:
    """One durable explanation for why a package entered the shared store."""

    kind: str
    description: str
    recorded_at: datetime
    command: str = ""
    source_file: str = ""
    source_line: int = 0
    manifest: str = ""


@dataclass(frozen=True, slots=True)
class CachedPackage:
    """One installed, content-addressed package artifact."""

    distribution: str
    version: str
    artifact_hash: str
    filename: str
    installed_at: datetime
    last_used_at: datetime | None
    size_bytes: int
    reasons: tuple[PackageInstallReason, ...] = ()


@dataclass(frozen=True, slots=True)
class CachedDuplicate:
    """Multiple physical artifacts retained for one normalized distribution."""

    distribution: str
    versions: tuple[str, ...]
    same_version_variants: tuple[str, ...]
    occurrences: int
    total_size_bytes: int
    additional_size_bytes: int
    packages: tuple[CachedPackage, ...]


@dataclass(frozen=True, slots=True)
class CachedPackageNode:
    """One package in an installation-root dependency tree."""

    package: CachedPackage
    dependencies: tuple[CachedPackageNode, ...] = ()
    repeated: bool = False


@dataclass(frozen=True, slots=True)
class CachedInstallation:
    """One successful installation origin and its currently retained roots."""

    id: str
    reason: PackageInstallReason
    total_size_bytes: int
    roots: tuple[CachedPackageNode, ...]


@dataclass(frozen=True, slots=True)
class CacheInventory:
    """All supported views over the current shared package store."""

    total_size_bytes: int
    packages: tuple[CachedPackage, ...]
    duplicates: tuple[CachedDuplicate, ...]
    installations: tuple[CachedInstallation, ...]


@dataclass(frozen=True, slots=True)
class CacheCleanupResult:
    """Packages selected by a cleanup/removal operation."""

    removed: tuple[CachedPackage, ...]
    skipped_active: tuple[CachedPackage, ...]
    reclaimed_bytes: int
    dry_run: bool = False


class CacheLease:
    """Cross-process marker keeping active runtime artifacts out of cleanup."""

    def __init__(self, cache: Cache, artifact_hashes: set[str]) -> None:
        self._cache = cache
        self._paths: list[Path] = []
        token = f"{os.getpid()}-{uuid.uuid4().hex}.lease"
        try:
            for digest in sorted(artifact_hashes):
                with cache._artifact_lock(digest):
                    path = cache._lease_root(digest) / token
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(str(os.getpid()) + "\n", encoding="ascii")
                    self._paths.append(path)
        except BaseException:
            self.close()
            raise
        atexit.register(self.close)

    def close(self) -> None:
        paths, self._paths = self._paths, []
        for path in paths:
            with contextlib.suppress(OSError):
                path.unlink()
            with contextlib.suppress(OSError):
                path.parent.rmdir()


class Cache:
    def __init__(
        self,
        root: Path | None = None,
        *,
        max_artifact_size: int = _DEFAULT_MAX_ARTIFACT_SIZE,
        timeout: float = 30.0,
    ) -> None:
        configured = os.environ.get("DEPFIX_CACHE_DIR")
        self.root = (root or (Path(configured) if configured else user_cache_path("depfix"))) / "v1"
        self.max_artifact_size = max_artifact_size
        self.timeout = timeout
        self._usage_updates: dict[str, float] = {}
        self._usage_guard = threading.Lock()

    def blob_path(self, sha256: str) -> Path:
        return self.root / "artifacts" / "sha256" / sha256[:2] / sha256

    def unpacked_path(self, artifact_id: str) -> Path:
        digest = artifact_id.removeprefix("sha256:")
        return self.root / "targets" / digest / _environment_key()

    def has_blob(self, sha256: str) -> bool:
        return self.blob_path(sha256).is_file()

    def discard_blob(self, sha256: str, *, _lock_held: bool = False) -> None:
        """Remove an ephemeral artifact after restoring owner write permission."""
        lock_context = contextlib.nullcontext() if _lock_held else self._artifact_lock(sha256)
        with lock_context:
            _remove_path(self.blob_path(sha256))

    def has_package(self, sha256: str) -> bool:
        """Return whether the current environment has a complete unpacked package."""
        target = self.unpacked_path("sha256:" + sha256.removeprefix("sha256:"))
        return self._target_is_complete(target, sha256.removeprefix("sha256:"))

    def _target_is_complete(self, target: Path, digest: str) -> bool:
        """Validate one unpacked target without relying on its directory name."""
        marker = target / ".complete"
        try:
            data = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, TypeError, json.JSONDecodeError):
            return False
        if data.get("format_version") != 1 or data.get("artifact_sha256") != digest:
            return False
        purelib = target / "purelib"
        if not purelib.is_dir():
            return False
        file_hashes = data.get("file_hashes")
        if isinstance(file_hashes, dict) and file_hashes:
            for relative, expected in file_hashes.items():
                if not isinstance(relative, str) or not isinstance(expected, str):
                    return False
                path = target / relative
                if not path.is_file() or _hash_file(path) != expected:
                    return False
            return True
        installed_files = data.get("installed_files")
        if not isinstance(installed_files, int) or installed_files < 1:
            return False
        present = sum(
            1
            for path in target.rglob("*")
            if path.is_file() and path.name != ".complete" and path.suffix != ".pyc" and "__pycache__" not in path.parts
        )
        return present >= installed_files

    def verify_blob(self, sha256: str, *, size: int | None = None) -> Path:
        path = self.blob_path(sha256)
        if not path.is_file():
            raise CacheError("Artifact is absent from the cache", artifact_hash=sha256)
        actual_size = path.stat().st_size
        if size is not None and actual_size != size:
            raise IntegrityError(
                "Cached artifact has the wrong size",
                artifact_hash=sha256,
                remediation=f"remove the corrupt blob {path} and fetch again",
            )
        digest = _hash_file(path)
        if digest != sha256:
            raise IntegrityError(
                "Cached artifact hash mismatch",
                artifact_hash=sha256,
                remediation=f"remove the corrupt blob {path} and fetch again",
            )
        return path

    def fetch_artifact(
        self,
        artifact: Artifact,
        *,
        offline: bool = False,
        verify: bool = True,
        allowed_hosts: tuple[str, ...] = (),
        allow_insecure: bool = False,
        _lock_held: bool = False,
    ) -> Path:
        existing = self.blob_path(artifact.sha256)
        if existing.is_file():
            return self.verify_blob(artifact.sha256, size=artifact.size) if verify else existing
        if offline:
            raise CacheError(
                "Artifact is not cached and offline mode forbids fetching",
                artifact_hash=artifact.sha256,
                remediation="run depfix fetch before entering offline mode",
            )
        return self.fetch_url(
            artifact.url,
            artifact.sha256,
            expected_size=artifact.size,
            allowed_hosts=allowed_hosts,
            allow_insecure=allow_insecure,
            _lock_held=_lock_held,
        )

    def fetch_url(
        self,
        url: str,
        sha256: str,
        *,
        expected_size: int | None = None,
        allowed_hosts: tuple[str, ...] = (),
        allow_insecure: bool = False,
        _lock_held: bool = False,
    ) -> Path:
        path, _final_url = self.fetch_url_with_final(
            url,
            sha256,
            expected_size=expected_size,
            allowed_hosts=allowed_hosts,
            allow_insecure=allow_insecure,
            _lock_held=_lock_held,
        )
        return path

    def fetch_url_with_final(
        self,
        url: str,
        sha256: str,
        *,
        expected_size: int | None = None,
        allowed_hosts: tuple[str, ...] = (),
        allow_insecure: bool = False,
        _lock_held: bool = False,
    ) -> tuple[Path, str]:
        _validate_network_url(url, allowed_hosts=allowed_hosts, allow_insecure=allow_insecure)
        if expected_size is not None and expected_size > self.max_artifact_size:
            raise CacheError("Artifact exceeds configured download limit", artifact_hash=sha256)
        destination = self.blob_path(sha256)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.is_file():
            return self.verify_blob(sha256, size=expected_size), self._recorded_final_url(sha256, url)
        lock_context = contextlib.nullcontext() if _lock_held else self._artifact_lock(sha256)
        with lock_context:
            if destination.is_file():
                return self.verify_blob(sha256, size=expected_size), self._recorded_final_url(sha256, url)
            temporary_dir = self.root / "temp"
            temporary_dir.mkdir(parents=True, exist_ok=True)
            fd, temporary_name = tempfile.mkstemp(prefix=f"download-{os.getpid()}-", suffix=".part", dir=temporary_dir)
            temporary = Path(temporary_name)
            final_url = url
            try:
                digest = hashlib.sha256()
                total = 0
                with os.fdopen(fd, "w+b") as output:
                    for attempt in range(_DOWNLOAD_ATTEMPTS):
                        resume_from = total
                        request: str | urllib.request.Request = url
                        if resume_from:
                            request = urllib.request.Request(
                                url,
                                headers={
                                    "User-Agent": "depfix/0.1",
                                    "Range": f"bytes={resume_from}-",
                                },
                            )
                        response_total: int | None = None
                        try:
                            with _open_url(
                                request,
                                timeout=self.timeout,
                                allowed_hosts=allowed_hosts,
                                allow_insecure=allow_insecure,
                            ) as response:
                                final_url = response.geturl()
                                content_range = response.headers.get("Content-Range")
                                if resume_from and _content_range_start(content_range) != resume_from:
                                    output.seek(0)
                                    output.truncate()
                                    digest = hashlib.sha256()
                                    total = 0
                                    resume_from = 0
                                declared = _header_integer(response.headers.get("Content-Length"))
                                range_total = _content_range_total(content_range)
                                response_total = range_total or (
                                    resume_from + declared if declared is not None else None
                                )
                                if response_total is not None and response_total > self.max_artifact_size:
                                    raise CacheError(
                                        "Artifact exceeds configured download limit",
                                        artifact_hash=sha256,
                                    )
                                while True:
                                    chunk = response.read(1024 * 1024)
                                    if not chunk:
                                        break
                                    total += len(chunk)
                                    if total > self.max_artifact_size:
                                        raise CacheError(
                                            "Artifact exceeds configured download limit",
                                            artifact_hash=sha256,
                                        )
                                    digest.update(chunk)
                                    output.write(chunk)
                        except (OSError, http.client.HTTPException):
                            if attempt + 1 == _DOWNLOAD_ATTEMPTS:
                                raise CacheError(
                                    "Artifact download failed after bounded retries",
                                    artifact_hash=sha256,
                                ) from None
                            continue

                        required_size = expected_size if expected_size is not None else response_total
                        if required_size is not None and total < required_size:
                            if attempt + 1 < _DOWNLOAD_ATTEMPTS:
                                continue
                            raise IntegrityError(
                                "Downloaded artifact has the wrong size",
                                artifact_hash=sha256,
                                remediation=f"expected {required_size} bytes but received {total}",
                            )
                        if expected_size is not None and total > expected_size:
                            raise IntegrityError(
                                "Downloaded artifact has the wrong size",
                                artifact_hash=sha256,
                                remediation=f"expected {expected_size} bytes but received {total}",
                            )
                        actual = digest.hexdigest()
                        if actual == sha256:
                            break
                        if attempt + 1 < _DOWNLOAD_ATTEMPTS:
                            output.seek(0)
                            output.truncate()
                            digest = hashlib.sha256()
                            total = 0
                            continue
                        raise IntegrityError(
                            "Downloaded artifact hash mismatch",
                            artifact_hash=sha256,
                            remediation=f"expected {sha256}, received {actual}",
                        )
                    output.flush()
                    os.fsync(output.fileno())
                try:
                    os.chmod(temporary, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
                except OSError:
                    pass
                os.replace(temporary, destination)
                self._write_origin(sha256, url, final_url)
            finally:
                temporary.unlink(missing_ok=True)
        return destination, redact(final_url)

    def fetch_unpinned(
        self,
        url: str,
        *,
        allowed_hosts: tuple[str, ...] = (),
        allow_insecure: bool = False,
    ) -> tuple[Path, str, str]:
        """Fetch mutable live content once and promote it by its observed hash."""
        temporary_dir = self.root / "tmp"
        temporary_dir.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(prefix=f"live-download-{os.getpid()}-", suffix=".part", dir=temporary_dir)
        temporary = Path(temporary_name)
        digest = hashlib.sha256()
        total = 0
        final_url = url
        try:
            with os.fdopen(fd, "wb") as output:
                with _open_url(
                    url,
                    timeout=self.timeout,
                    allowed_hosts=allowed_hosts,
                    allow_insecure=allow_insecure,
                ) as response:
                    final_url = response.geturl()
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > self.max_artifact_size:
                            raise CacheError("Artifact exceeds configured download limit", source=final_url)
                        digest.update(chunk)
                        output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            observed = digest.hexdigest()
            destination = self.blob_path(observed)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with self._artifact_lock(observed):
                if destination.is_file():
                    self.verify_blob(observed, size=total)
                else:
                    os.replace(temporary, destination)
            return destination, observed, final_url
        finally:
            temporary.unlink(missing_ok=True)

    @contextlib.contextmanager
    def _artifact_lock(self, digest: str):  # type: ignore[no-untyped-def]
        lock = self.root / "locks" / f"{digest}.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                lock.mkdir()
                (lock / "owner").write_text(str(os.getpid()) + "\n", encoding="ascii")
                break
            except OSError as error:
                if not isinstance(error, FileExistsError) and not (_IS_WINDOWS and isinstance(error, PermissionError)):
                    raise
                if self._recover_abandoned_lock(lock):
                    continue
                if time.monotonic() >= deadline:
                    raise CacheError("Timed out waiting for another cache writer", artifact_hash=digest) from None
                time.sleep(0.025)
        try:
            yield
        finally:
            shutil.rmtree(lock, ignore_errors=True)

    def _recover_abandoned_lock(self, lock: Path) -> bool:
        """Remove a lock only when its recorded process is gone and it is old enough."""
        try:
            pid = int((lock / "owner").read_text(encoding="ascii").strip())
            age = time.time() - lock.stat().st_mtime
        except (OSError, ValueError):
            return False
        if age < 1.0 or _pid_is_running(pid):
            return False
        abandoned = lock.with_name(lock.name + f".abandoned-{uuid.uuid4().hex}")
        try:
            os.replace(lock, abandoned)
        except OSError:
            return False
        shutil.rmtree(abandoned, ignore_errors=True)
        return True

    def list_blobs(self) -> list[Path]:
        root = self.root / "artifacts" / "sha256"
        return sorted(path for path in root.glob("*/*") if path.is_file()) if root.exists() else []

    def prune(self, referenced_hashes: set[str]) -> list[Path]:
        self.reconcile_intermediates()
        removed: list[Path] = []
        for path in self.list_blobs():
            digest = path.name
            if digest in referenced_hashes:
                continue
            with self.lock("target:sha256:" + digest), self._artifact_lock(digest):
                if digest in referenced_hashes or not path.is_file():
                    continue
                path.unlink()
                (self.root / "metadata" / "origins" / f"{digest}.json").unlink(missing_ok=True)
                removed.append(path)
        return removed

    def verify_packages(self) -> int:
        """Validate every retained unpacked target, including corrupt targets omitted from inventory."""
        targets = self.root / "targets"
        digests = (
            sorted(path.name for path in targets.iterdir() if path.is_dir() and _is_sha256(path.name))
            if targets.is_dir()
            else []
        )
        verified = 0
        for digest in digests:
            with self.lock("target:sha256:" + digest), self._artifact_lock(digest):
                digest_root = targets / digest
                retained = tuple(path for path in digest_root.iterdir() if path.is_dir())
                if not retained or any(not self._target_is_complete(path, digest) for path in retained):
                    raise CacheError("Cached package target is incomplete", artifact_hash=digest)
                verified += 1
        return verified

    def discard_download(self, sha256: str) -> None:
        """Discard a materialized package's source archive under its mutation locks."""
        digest = sha256.removeprefix("sha256:")
        with self.lock("target:sha256:" + digest), self._artifact_lock(digest):
            if self.has_package(digest):
                _remove_path(self.blob_path(digest))

    def reconcile_intermediates(
        self,
        *,
        stale_after: float = _STALE_INTERMEDIATE_SECONDS,
        dry_run: bool = False,
    ) -> list[Path]:
        """Remove obsolete blobs and abandoned download parts without touching active work."""
        removed: list[Path] = []
        now = time.time()
        for path in self.list_blobs():
            digest = path.name
            complete = self.has_package(digest)
            try:
                stale_orphan = not complete and now - path.stat().st_mtime >= _LEGACY_STALE_INTERMEDIATE_SECONDS
            except OSError:
                continue
            if not complete and not stale_orphan:
                continue
            with self.lock("target:sha256:" + digest), self._artifact_lock(digest):
                try:
                    stale_orphan = (
                        not self.has_package(digest)
                        and now - path.stat().st_mtime >= _LEGACY_STALE_INTERMEDIATE_SECONDS
                    )
                except OSError:
                    continue
                if path.is_file() and (self.has_package(digest) or stale_orphan):
                    if not dry_run:
                        _remove_path(path)
                    removed.append(path)
        temporary_root = self.root / "temp"
        if temporary_root.is_dir():
            for path in temporary_root.glob("download-*"):
                try:
                    age = now - path.stat().st_mtime
                except OSError:
                    continue
                try:
                    pid = int(path.name.split("-", 2)[1])
                except (ValueError, IndexError):
                    pid = None
                abandoned = pid is not None and not _pid_is_running(pid) and age >= stale_after
                legacy_stale = pid is None and age >= _LEGACY_STALE_INTERMEDIATE_SECONDS
                if abandoned or legacy_stale:
                    if not dry_run:
                        _remove_path(path)
                    removed.append(path)
        work_root = self.root / "tmp"
        prefixes = (
            "live-download-",
            "depfix-build-",
            "depfix-source-",
            "git-",
            "git-wheel-",
            "uv-cache-",
            "uv-resolve-",
        )
        if work_root.is_dir():
            for path in work_root.iterdir():
                if not path.name.startswith(prefixes):
                    continue
                try:
                    age = now - path.stat().st_mtime
                except OSError:
                    continue
                match = re.search(r"-(\d+)-", path.name)
                pid = int(match.group(1)) if match is not None else None
                abandoned = pid is not None and not _pid_is_running(pid) and age >= stale_after
                legacy_stale = pid is None and age >= _LEGACY_STALE_INTERMEDIATE_SECONDS
                if abandoned or legacy_stale:
                    if not dry_run:
                        _remove_path(path)
                    removed.append(path)
        targets_root = self.root / "targets"
        if targets_root.is_dir():
            for digest_root in targets_root.iterdir():
                if not digest_root.is_dir() or not _is_sha256(digest_root.name):
                    continue
                digest = digest_root.name
                for path in digest_root.iterdir():
                    if self._target_is_complete(path, digest) or not _is_stale(path, now):
                        continue
                    with self.lock("target:sha256:" + digest), self._artifact_lock(digest):
                        if path.exists() and not self._target_is_complete(path, digest) and _is_stale(path, now):
                            if not dry_run:
                                _remove_path(path)
                            removed.append(path)
        built_root = self.root / "built-wheels"
        if built_root.is_dir():
            for path in built_root.iterdir():
                if not path.is_dir() or not _is_sha256(path.name) or not _is_stale(path, now):
                    continue
                digest = path.name
                with self.lock("target:sha256:" + digest), self._artifact_lock(digest):
                    if path.exists() and _is_stale(path, now):
                        if not dry_run:
                            _remove_path(path)
                        removed.append(path)
        return removed

    def record_artifact(self, artifact: Artifact) -> None:
        """Persist immutable installation identity without resetting its age."""
        path = self._package_metadata_path(artifact.sha256)
        if path.is_file():
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._artifact_lock("metadata-" + artifact.sha256):
            if not path.is_file():
                data = {
                    "format_version": 1,
                    "sha256": artifact.sha256,
                    "distribution": artifact.distribution,
                    "version": artifact.version,
                    "filename": artifact.filename,
                    "installed_at": time.time(),
                    "source_sha256": artifact.source_sha256,
                }
                temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
                try:
                    temporary.write_text(json.dumps(data, sort_keys=True) + "\n", encoding="utf-8")
                    os.replace(temporary, path)
                finally:
                    temporary.unlink(missing_ok=True)
        self._initialize_cleanup_clock()

    def record_installation(
        self,
        graph: LockedGraph,
        *,
        root_nodes: tuple[str, ...],
        kind: str,
        description: str,
        command: str = "",
        source_file: str = "",
        source_line: int = 0,
        manifest: str = "",
    ) -> None:
        """Record one successful graph preparation without duplicating equivalent origins."""
        node_index = graph.node_index
        artifact_index = graph.artifact_index
        selected: set[str] = set()

        def visit(node_id: str) -> None:
            if node_id in selected or node_id not in node_index:
                return
            selected.add(node_id)
            for child_id in node_index[node_id].dependencies.values():
                visit(child_id)

        for root in root_nodes:
            visit(root)
        if not selected:
            return
        nodes: list[dict[str, object]] = []
        for node_id in sorted(selected):
            node = node_index[node_id]
            artifact = artifact_index[node.artifact]
            nodes.append(
                {
                    "id": node.id,
                    "artifact_hash": artifact.sha256,
                    "distribution": node.distribution,
                    "version": node.version,
                    "dependencies": {
                        name: child for name, child in sorted(node.dependencies.items()) if child in selected
                    },
                }
            )
        origin = {
            "kind": kind,
            "description": redact(description),
            "command": redact(command),
            "source_file": source_file,
            "source_line": max(source_line, 0),
            "manifest": manifest,
        }
        identity_payload = {
            "graph_id": graph.graph_id,
            "roots": sorted(root for root in root_nodes if root in selected),
            "origin": origin,
        }
        encoded = json.dumps(identity_payload, sort_keys=True, separators=(",", ":")).encode()
        identity = hashlib.sha256(encoded).hexdigest()
        path = self._installation_metadata_path(identity)
        if path.is_file():
            return
        data = {
            "format_version": 1,
            "id": identity,
            "graph_id": graph.graph_id,
            "recorded_at": time.time(),
            "roots": identity_payload["roots"],
            "origin": origin,
            "nodes": nodes,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._artifact_lock("installation-" + identity):
            if path.is_file():
                return
            temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
            try:
                temporary.write_text(json.dumps(data, sort_keys=True) + "\n", encoding="utf-8")
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)

    def mark_used(self, artifact: Artifact) -> None:
        """Record successful package use, coalescing writes within one process."""
        self.record_artifact(artifact)
        now = time.time()
        with self._usage_guard:
            previous = self._usage_updates.get(artifact.sha256)
            if previous is not None and now - previous < _USAGE_WRITE_INTERVAL_SECONDS:
                return
            self._usage_updates[artifact.sha256] = now
        path = self._usage_path(artifact.sha256)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)
        os.utime(path, (now, now))

    def reserve_artifacts(self, artifact_hashes: set[str]) -> None:
        """Briefly protect a graph that is about to synchronize and activate."""
        now = time.time()
        root = self.root / "metadata" / "reservations"
        root.mkdir(parents=True, exist_ok=True)
        for digest in artifact_hashes:
            path = root / f"{digest}.touch"
            path.touch(exist_ok=True)
            os.utime(path, (now, now))

    def lease(self, artifact_hashes: set[str]) -> CacheLease:
        return CacheLease(self, artifact_hashes)

    def list_packages(self) -> tuple[CachedPackage, ...]:
        """Return installed artifacts with lifecycle and total footprint data."""
        digests: set[str] = set()
        targets = self.root / "targets"
        if targets.is_dir():
            digests.update(path.name for path in targets.iterdir() if path.is_dir() and _is_sha256(path.name))
        metadata = self.root / "metadata" / "packages"
        if metadata.is_dir():
            digests.update(path.stem for path in metadata.glob("*.json") if _is_sha256(path.stem))
        reasons = self._reasons_by_artifact()
        entries = [
            entry
            for digest in digests
            if (entry := self._package_entry(digest, reasons=reasons.get(digest, ()))) is not None
        ]
        return tuple(sorted(entries, key=lambda item: (item.distribution, item.version, item.artifact_hash)))

    def inventory(self) -> CacheInventory:
        """Return flat, duplicate, and installation-tree views of the shared store."""
        packages = self.list_packages()
        package_index = {package.artifact_hash: package for package in packages}
        grouped: dict[str, list[CachedPackage]] = {}
        for package in packages:
            grouped.setdefault(package.distribution, []).append(package)
        duplicates: list[CachedDuplicate] = []
        for distribution, entries in sorted(grouped.items()):
            if len(entries) < 2:
                continue
            versions: dict[str, int] = {}
            for entry in entries:
                versions[entry.version] = versions.get(entry.version, 0) + 1
            total = sum(entry.size_bytes for entry in entries)
            duplicates.append(
                CachedDuplicate(
                    distribution=distribution,
                    versions=tuple(sorted(versions)),
                    same_version_variants=tuple(sorted(version for version, count in versions.items() if count > 1)),
                    occurrences=len(entries),
                    total_size_bytes=total,
                    additional_size_bytes=total - max(entry.size_bytes for entry in entries),
                    packages=tuple(entries),
                )
            )
        duplicates.sort(key=lambda item: (-item.additional_size_bytes, item.distribution))
        installations = self._installation_inventory(package_index)
        return CacheInventory(
            total_size_bytes=sum(package.size_bytes for package in packages),
            packages=packages,
            duplicates=tuple(duplicates),
            installations=installations,
        )

    def cleanup(
        self,
        days: int,
        *,
        protected_hashes: set[str] | None = None,
        dry_run: bool = False,
    ) -> CacheCleanupResult:
        """Remove artifacts unused for at least ``days`` while preserving active leases."""
        self.reconcile_intermediates(dry_run=dry_run)
        if isinstance(days, bool) or not isinstance(days, int) or days < 0:
            raise ValueError("cache cleanup days must be a non-negative integer")
        now = time.time()
        cutoff = now - days * 24 * 60 * 60
        protected = protected_hashes or set()
        candidates = [
            entry
            for entry in self.list_packages()
            if entry.artifact_hash not in protected and self._last_relevant_time(entry, now) <= cutoff
        ]
        result = self._remove_entries(
            candidates,
            protected_hashes=protected,
            dry_run=dry_run,
            eligible_before=cutoff,
        )
        if not dry_run:
            self._touch_cleanup_clock()
        return result

    def remove_package(
        self,
        distribution: str,
        *,
        version: str | None = None,
        artifact_hash: str | None = None,
        dry_run: bool = False,
    ) -> CacheCleanupResult:
        """Remove cached artifacts matching one normalized distribution selection."""
        selected = str(canonicalize_name(distribution))
        entries = tuple(
            entry
            for entry in self.list_packages()
            if entry.distribution == selected
            and (version is None or entry.version == version)
            and (artifact_hash is None or entry.artifact_hash == artifact_hash.removeprefix("sha256:"))
        )
        return self._remove_entries(entries, protected_hashes=set(), dry_run=dry_run)

    def automatic_cleanup_due(self, *, interval_seconds: int = _AUTOMATIC_CLEANUP_INTERVAL_SECONDS) -> bool:
        """Cheaply report whether the background retention sweep is due."""
        marker = self._cleanup_clock_path()
        if not marker.exists():
            self._initialize_cleanup_clock()
            return False
        try:
            return time.time() - marker.stat().st_mtime >= interval_seconds
        except OSError:
            return False

    def automatic_cleanup(self, days: int, *, protected_hashes: set[str]) -> CacheCleanupResult | None:
        """Run one cross-process daily sweep, or return when another process already did."""
        with self.lock("automatic-cleanup"):
            if not self.automatic_cleanup_due():
                return None
            return self.cleanup(days, protected_hashes=protected_hashes)

    @contextlib.contextmanager
    def lock(self, key: str):  # type: ignore[no-untyped-def]
        """Serialize a cache mutation across threads/processes using a bounded key."""
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        with self._artifact_lock("operation-" + digest):
            yield

    def _recorded_final_url(self, digest: str, fallback: str) -> str:
        path = self.root / "metadata" / "origins" / f"{digest}.json"
        try:
            value = json.loads(path.read_text(encoding="utf-8"))["final_url"]
        except (OSError, KeyError, TypeError, json.JSONDecodeError):
            return redact(fallback)
        return str(value)

    def _write_origin(self, digest: str, requested_url: str, final_url: str) -> None:
        path = self.root / "metadata" / "origins" / f"{digest}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                {"requested_url": redact(requested_url), "final_url": redact(final_url)},
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)

    def _package_entry(
        self,
        digest: str,
        *,
        reasons: tuple[PackageInstallReason, ...] = (),
    ) -> CachedPackage | None:
        target = self.root / "targets" / digest
        if not target.is_dir() or not any(
            self._target_is_complete(path, digest) for path in target.iterdir() if path.is_dir()
        ):
            return None
        data: dict[str, object] = {}
        try:
            loaded = json.loads(self._package_metadata_path(digest).read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, TypeError, json.JSONDecodeError):
            pass
        installed = _positive_timestamp(data.get("installed_at"))
        if installed is None:
            installed = min(self._artifact_mtimes(digest), default=time.time())
        usage = self._usage_path(digest)
        try:
            last_used = usage.stat().st_mtime
        except OSError:
            last_used = None
        distribution = str(data.get("distribution") or "unknown")
        if distribution != "unknown":
            distribution = str(canonicalize_name(distribution))
        source_digest = str(data.get("source_sha256") or "")
        source_blob = (
            self.blob_path(source_digest)
            if _is_sha256(source_digest)
            and source_digest != digest
            and self._source_is_exclusive(source_digest, owner_digest=digest)
            else None
        )
        return CachedPackage(
            distribution=distribution,
            version=str(data.get("version") or ""),
            artifact_hash=digest,
            filename=str(data.get("filename") or digest),
            installed_at=datetime.fromtimestamp(installed, UTC),
            last_used_at=datetime.fromtimestamp(last_used, UTC) if last_used is not None else None,
            size_bytes=(
                _directory_size(target)
                + (source_blob.stat().st_size if source_blob is not None and source_blob.is_file() else 0)
            ),
            reasons=reasons,
        )

    def _reasons_by_artifact(self) -> dict[str, tuple[PackageInstallReason, ...]]:
        reasons: dict[str, list[PackageInstallReason]] = {}
        for record in self._installation_records():
            reason = self._reason_from_record(record)
            if reason is None:
                continue
            for node in _record_nodes(record).values():
                digest = str(node.get("artifact_hash") or "")
                if _is_sha256(digest) and reason not in reasons.setdefault(digest, []):
                    reasons[digest].append(reason)
        return {
            digest: tuple(sorted(items, key=lambda item: item.recorded_at, reverse=True))
            for digest, items in reasons.items()
        }

    def _installation_inventory(
        self,
        packages: dict[str, CachedPackage],
    ) -> tuple[CachedInstallation, ...]:
        installations: list[CachedInstallation] = []
        for record in self._installation_records():
            reason = self._reason_from_record(record)
            if reason is None:
                continue
            nodes = _record_nodes(record)
            seen: set[str] = set()

            def build(
                node_id: str,
                active: frozenset[str],
                record_nodes: dict[str, dict[str, object]] = nodes,
                visited: set[str] = seen,
            ) -> CachedPackageNode | None:
                raw = record_nodes.get(node_id)
                if raw is None:
                    return None
                digest = str(raw.get("artifact_hash") or "")
                package = packages.get(digest)
                if package is None:
                    return None
                repeated = node_id in visited or node_id in active
                if repeated:
                    return CachedPackageNode(package, repeated=True)
                visited.add(node_id)
                dependencies = raw.get("dependencies")
                child_ids = (
                    [str(value) for _name, value in sorted(dependencies.items())]
                    if isinstance(dependencies, dict)
                    else []
                )
                lineage = active | {node_id}
                children = tuple(child for child_id in child_ids if (child := build(child_id, lineage)) is not None)
                return CachedPackageNode(package, children)

            raw_roots = record.get("roots")
            roots = (
                tuple(
                    root
                    for node_id in raw_roots
                    if isinstance(node_id, str) and (root := build(node_id, frozenset())) is not None
                )
                if isinstance(raw_roots, list)
                else ()
            )
            if not roots:
                continue
            retained = {
                str(node.get("artifact_hash")) for node in nodes.values() if str(node.get("artifact_hash")) in packages
            }
            installations.append(
                CachedInstallation(
                    id=str(record.get("id") or ""),
                    reason=reason,
                    total_size_bytes=sum(packages[digest].size_bytes for digest in retained),
                    roots=roots,
                )
            )
        return tuple(
            sorted(
                installations,
                key=lambda item: (item.reason.recorded_at, item.id),
                reverse=True,
            )
        )

    def _installation_records(self) -> tuple[dict[str, object], ...]:
        root = self.root / "metadata" / "installations"
        if not root.is_dir():
            return ()
        records: list[dict[str, object]] = []
        for path in sorted(root.glob("*.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, TypeError, json.JSONDecodeError):
                continue
            if isinstance(raw, dict) and raw.get("format_version") == 1:
                records.append(raw)
        return tuple(records)

    def _prune_empty_installation_records(self) -> None:
        root = self.root / "metadata" / "installations"
        if not root.is_dir():
            return
        for path in root.glob("*.json"):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, TypeError, json.JSONDecodeError):
                continue
            if not isinstance(raw, dict):
                continue
            retained = any(
                self._package_entry(str(node.get("artifact_hash") or "")) is not None
                for node in _record_nodes(raw).values()
            )
            if not retained:
                path.unlink(missing_ok=True)

    def _reason_from_record(self, record: dict[str, object]) -> PackageInstallReason | None:
        origin = record.get("origin")
        recorded_at = _positive_timestamp(record.get("recorded_at"))
        if not isinstance(origin, dict) or recorded_at is None:
            return None
        try:
            source_line = int(origin.get("source_line") or 0)
        except (TypeError, ValueError):
            source_line = 0
        return PackageInstallReason(
            kind=str(origin.get("kind") or "unknown"),
            description=str(origin.get("description") or "unknown installation"),
            recorded_at=datetime.fromtimestamp(recorded_at, UTC),
            command=str(origin.get("command") or ""),
            source_file=str(origin.get("source_file") or ""),
            source_line=source_line,
            manifest=str(origin.get("manifest") or ""),
        )

    def _last_relevant_time(self, entry: CachedPackage, now: float) -> float:
        relevant = entry.last_used_at.timestamp() if entry.last_used_at is not None else entry.installed_at.timestamp()
        reservation = self.root / "metadata" / "reservations" / f"{entry.artifact_hash}.touch"
        try:
            reserved_at = reservation.stat().st_mtime
        except OSError:
            return relevant
        if now - reserved_at <= _RESERVATION_GRACE_SECONDS:
            relevant = max(relevant, reserved_at)
        return relevant

    def _has_live_reservation(self, digest: str, now: float) -> bool:
        path = self.root / "metadata" / "reservations" / f"{digest}.touch"
        try:
            return now - path.stat().st_mtime <= _RESERVATION_GRACE_SECONDS
        except OSError:
            return False

    def _remove_entries(
        self,
        entries: tuple[CachedPackage, ...] | list[CachedPackage],
        *,
        protected_hashes: set[str],
        dry_run: bool,
        eligible_before: float | None = None,
    ) -> CacheCleanupResult:
        removed: list[CachedPackage] = []
        skipped: list[CachedPackage] = []
        reclaimed = 0
        for original in entries:
            digest = original.artifact_hash
            if digest in protected_hashes:
                skipped.append(original)
                continue
            with self.lock("target:sha256:" + digest), self._artifact_lock(digest):
                current = self._package_entry(digest, reasons=original.reasons)
                if current is None:
                    continue
                if eligible_before is not None and self._last_relevant_time(current, time.time()) > eligible_before:
                    continue
                if self._has_live_reservation(digest, time.time()) or self._has_live_lease(digest):
                    skipped.append(current)
                    continue
                removed.append(current)
                reclaimed += current.size_bytes
                if not dry_run:
                    self._delete_artifact(digest)
        if removed and not dry_run:
            self._prune_empty_installation_records()
        return CacheCleanupResult(tuple(removed), tuple(skipped), reclaimed, dry_run)

    def _delete_artifact(self, digest: str) -> None:
        source_digest = self._source_digest(digest)
        _remove_path(self.blob_path(digest))
        _remove_path(self.root / "targets" / digest)
        _remove_path(self.root / "built-wheels" / digest)
        for path in (
            self._package_metadata_path(digest),
            self._usage_path(digest),
            self.root / "metadata" / "reservations" / f"{digest}.touch",
            self.root / "metadata" / "origins" / f"{digest}.json",
            self.root / "metadata" / "imports" / f"{digest}.json",
        ):
            _remove_path(path)
        _remove_path(self._lease_root(digest))
        if source_digest is not None and not self._source_is_referenced(source_digest):
            _remove_path(self.blob_path(source_digest))
            _remove_path(self.root / "metadata" / "origins" / f"{source_digest}.json")

    def _has_live_lease(self, digest: str) -> bool:
        root = self._lease_root(digest)
        if not root.is_dir():
            return False
        live = False
        for path in root.glob("*.lease"):
            try:
                pid = int(path.name.split("-", 1)[0])
            except (ValueError, IndexError):
                path.unlink(missing_ok=True)
                continue
            if _pid_is_running(pid):
                live = True
            else:
                path.unlink(missing_ok=True)
        if not live:
            with contextlib.suppress(OSError):
                root.rmdir()
        return live

    def _artifact_mtimes(self, digest: str) -> list[float]:
        paths = (
            self.blob_path(digest),
            self.root / "targets" / digest,
            self.root / "built-wheels" / digest,
        )
        result: list[float] = []
        for path in paths:
            try:
                result.append(path.stat().st_mtime)
            except OSError:
                pass
        return result

    def _source_digest(self, digest: str) -> str | None:
        try:
            data = json.loads(self._package_metadata_path(digest).read_text(encoding="utf-8"))
        except (OSError, TypeError, json.JSONDecodeError):
            return None
        value = str(data.get("source_sha256") or "") if isinstance(data, dict) else ""
        return value if _is_sha256(value) and value != digest else None

    def _source_is_referenced(self, source_digest: str) -> bool:
        metadata = self.root / "metadata" / "packages"
        if not metadata.is_dir():
            return False
        if self._package_metadata_path(source_digest).is_file():
            return True
        return any(self._source_digest(path.stem) == source_digest for path in metadata.glob("*.json"))

    def _source_is_exclusive(self, source_digest: str, *, owner_digest: str) -> bool:
        metadata = self.root / "metadata" / "packages"
        if self._package_metadata_path(source_digest).is_file():
            return False
        if not metadata.is_dir():
            return True
        return not any(
            path.stem != owner_digest and self._source_digest(path.stem) == source_digest
            for path in metadata.glob("*.json")
        )

    def _package_metadata_path(self, digest: str) -> Path:
        return self.root / "metadata" / "packages" / f"{digest}.json"

    def _installation_metadata_path(self, identity: str) -> Path:
        return self.root / "metadata" / "installations" / f"{identity}.json"

    def _usage_path(self, digest: str) -> Path:
        return self.root / "metadata" / "usage" / f"{digest}.touch"

    def _lease_root(self, digest: str) -> Path:
        return self.root / "metadata" / "leases" / digest

    def _cleanup_clock_path(self) -> Path:
        return self.root / "metadata" / "cleanup.touch"

    def _initialize_cleanup_clock(self) -> None:
        marker = self._cleanup_clock_path()
        marker.parent.mkdir(parents=True, exist_ok=True)
        try:
            marker.touch(exist_ok=False)
        except FileExistsError:
            pass

    def _touch_cleanup_clock(self) -> None:
        marker = self._cleanup_clock_path()
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch(exist_ok=True)
        os.utime(marker, None)


def _positive_timestamp(value: object) -> float | None:
    try:
        timestamp = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return timestamp if timestamp > 0 else None


def _record_nodes(record: dict[str, object]) -> dict[str, dict[str, object]]:
    raw = record.get("nodes")
    if not isinstance(raw, list):
        return {}
    result: dict[str, dict[str, object]] = {}
    for node in raw:
        if isinstance(node, dict) and isinstance(node.get("id"), str):
            result[str(node["id"])] = node
    return result


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value.lower())


def _directory_size(path: Path) -> int:
    if not path.is_dir():
        return 0
    total = 0
    for item in path.rglob("*"):
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:
            continue
    return total


def _remove_path(path: Path) -> None:
    if not path.exists():
        return

    def make_writable_and_retry(function, value, _error):  # type: ignore[no-untyped-def]
        candidate = Path(value)
        with contextlib.suppress(OSError):
            candidate.parent.chmod(stat.S_IRWXU)
        with contextlib.suppress(OSError):
            candidate.chmod(stat.S_IRUSR | stat.S_IWUSR | (stat.S_IXUSR if candidate.is_dir() else 0))
        function(value)

    if path.is_dir():
        for directory, child_directories, files in os.walk(path, topdown=True, followlinks=False):
            current = Path(directory)
            with contextlib.suppress(OSError):
                current.chmod(stat.S_IRWXU)
            for name in child_directories:
                candidate = current / name
                if not candidate.is_symlink():
                    with contextlib.suppress(OSError):
                        candidate.chmod(stat.S_IRWXU)
            for name in files:
                candidate = current / name
                if not candidate.is_symlink():
                    with contextlib.suppress(OSError):
                        candidate.chmod(stat.S_IRUSR | stat.S_IWUSR)
        shutil.rmtree(path, onerror=make_writable_and_retry)
    else:
        with contextlib.suppress(OSError):
            path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        path.unlink(missing_ok=True)


def _pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if _IS_WINDOWS:
        return _windows_pid_is_running(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _windows_pid_is_running(pid: int) -> bool:
    """Query process state without using ``os.kill(pid, 0)``, which terminates on Windows."""
    import ctypes

    windll = getattr(ctypes, "windll", None)
    if windll is None:
        return False
    kernel32 = windll.kernel32
    process_query_limited_information = 0x1000
    still_active = 259
    kernel32.OpenProcess.argtypes = (ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong)
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.GetExitCodeProcess.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong))
    kernel32.GetExitCodeProcess.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
    kernel32.CloseHandle.restype = ctypes.c_int
    kernel32.GetLastError.restype = ctypes.c_ulong
    handle = kernel32.OpenProcess(process_query_limited_information, 0, pid)
    if not handle:
        return int(kernel32.GetLastError()) == 5
    try:
        exit_code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == still_active
    finally:
        kernel32.CloseHandle(handle)


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_stale(path: Path, now: float) -> bool:
    """Require an entire intermediate tree to be older than the legacy grace period."""
    try:
        newest = path.stat().st_mtime
        if path.is_dir():
            newest = max((item.stat().st_mtime for item in path.rglob("*")), default=newest)
    except OSError:
        return False
    return now - newest >= _LEGACY_STALE_INTERMEDIATE_SECONDS


def _header_integer(value: object) -> int | None:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _content_range_start(value: object) -> int | None:
    parsed = _parse_content_range(value)
    return parsed[0] if parsed is not None else None


def _content_range_total(value: object) -> int | None:
    parsed = _parse_content_range(value)
    return parsed[1] if parsed is not None else None


def _parse_content_range(value: object) -> tuple[int, int | None] | None:
    if not isinstance(value, str) or not value.startswith("bytes "):
        return None
    byte_range, separator, total_text = value[6:].partition("/")
    start_text, dash, _end_text = byte_range.partition("-")
    if separator != "/" or dash != "-":
        return None
    start = _header_integer(start_text)
    total = None if total_text == "*" else _header_integer(total_text)
    if start is None or (total_text != "*" and total is None):
        return None
    return start, total


class _LocalResponse:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._handle = path.open("rb")
        self.headers = {"Content-Length": str(path.stat().st_size)}

    def read(self, size: int = -1) -> bytes:
        return self._handle.read(size)

    def geturl(self) -> str:
        return self._path.resolve().as_uri()

    def __enter__(self) -> _LocalResponse:
        return self

    def __exit__(self, *args: object) -> None:
        self._handle.close()


def _open_url(
    url: str | urllib.request.Request,
    *,
    timeout: float,
    allowed_hosts: tuple[str, ...] = (),
    allow_insecure: bool = False,
) -> Any:
    raw_url = url.full_url if isinstance(url, urllib.request.Request) else url
    _validate_network_url(raw_url, allowed_hosts=allowed_hosts, allow_insecure=allow_insecure)
    split = urlsplit(raw_url)
    if split.scheme == "file":
        if split.netloc not in {"", "localhost"}:
            raise CacheError("Remote file URL authorities are not permitted")
        return _LocalResponse(file_url_to_path(raw_url))
    request = (
        url
        if isinstance(url, urllib.request.Request)
        else urllib.request.Request(raw_url, headers={"User-Agent": "depfix/0.1"})
    )
    opener = urllib.request.build_opener(_PolicyRedirectHandler(allowed_hosts, allow_insecure))
    return opener.open(request, timeout=timeout)  # noqa: S310


class _PolicyRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, allowed_hosts: tuple[str, ...], allow_insecure: bool) -> None:
        self.allowed_hosts = allowed_hosts
        self.allow_insecure = allow_insecure

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        _validate_network_url(
            newurl,
            allowed_hosts=self.allowed_hosts,
            allow_insecure=self.allow_insecure,
        )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _validate_network_url(url: str, *, allowed_hosts: tuple[str, ...], allow_insecure: bool) -> None:
    split = urlsplit(url)
    if split.scheme == "file":
        return
    if split.scheme != "https" and not (allow_insecure and split.scheme == "http"):
        raise CacheError("Only HTTPS artifact downloads are permitted", source=redact(url))
    host = (split.hostname or "").lower().rstrip(".")
    if allowed_hosts and not any(_host_matches(host, pattern) for pattern in allowed_hosts):
        raise CacheError("Artifact host is not permitted by policy", source=redact(url))


def _host_matches(host: str, pattern: str) -> bool:
    candidate = pattern.strip().lower().rstrip(".")
    if candidate.startswith("*."):
        suffix = candidate[1:]
        return host.endswith(suffix) and host != suffix[1:]
    return host == candidate


def _environment_key() -> str:
    value = "-".join(
        (
            platform.python_implementation().lower(),
            f"{sys.version_info.major}{sys.version_info.minor}",
            str(sysconfig.get_config_var("SOABI") or "none"),
            sys.platform,
            platform.machine().lower(),
        )
    )
    return hashlib.sha256(value.encode()).hexdigest()[:16]
