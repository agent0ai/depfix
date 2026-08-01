"""Immutable, content-addressed artifact cache."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import platform
import stat
import sys
import sysconfig
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from platformdirs import user_cache_path

from ._file_urls import file_url_to_path
from .errors import CacheError, IntegrityError, redact
from .models import Artifact


class Cache:
    def __init__(
        self,
        root: Path | None = None,
        *,
        max_artifact_size: int = 256 * 1024 * 1024,
        timeout: float = 30.0,
    ) -> None:
        configured = os.environ.get("DEPFIX_CACHE_DIR")
        self.root = (root or (Path(configured) if configured else user_cache_path("depfix"))) / "v1"
        self.max_artifact_size = max_artifact_size
        self.timeout = timeout

    def blob_path(self, sha256: str) -> Path:
        return self.root / "artifacts" / "sha256" / sha256[:2] / sha256

    def unpacked_path(self, artifact_id: str) -> Path:
        digest = artifact_id.removeprefix("sha256:")
        return self.root / "targets" / digest / _environment_key()

    def has_blob(self, sha256: str) -> bool:
        return self.blob_path(sha256).is_file()

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
        )

    def fetch_url(
        self,
        url: str,
        sha256: str,
        *,
        expected_size: int | None = None,
        allowed_hosts: tuple[str, ...] = (),
        allow_insecure: bool = False,
    ) -> Path:
        path, _final_url = self.fetch_url_with_final(
            url,
            sha256,
            expected_size=expected_size,
            allowed_hosts=allowed_hosts,
            allow_insecure=allow_insecure,
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
    ) -> tuple[Path, str]:
        _validate_network_url(url, allowed_hosts=allowed_hosts, allow_insecure=allow_insecure)
        destination = self.blob_path(sha256)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.is_file():
            return self.verify_blob(sha256, size=expected_size), self._recorded_final_url(sha256, url)
        with self._artifact_lock(sha256):
            if destination.is_file():
                return self.verify_blob(sha256, size=expected_size), self._recorded_final_url(sha256, url)
            temporary_dir = self.root / "temp"
            temporary_dir.mkdir(parents=True, exist_ok=True)
            fd, temporary_name = tempfile.mkstemp(prefix="download-", dir=temporary_dir)
            temporary = Path(temporary_name)
            final_url = url
            try:
                digest = hashlib.sha256()
                total = 0
                with os.fdopen(fd, "wb") as output:
                    with _open_url(
                        url,
                        timeout=self.timeout,
                        allowed_hosts=allowed_hosts,
                        allow_insecure=allow_insecure,
                    ) as response:
                        final_url = response.geturl()
                        declared = response.headers.get("Content-Length")
                        if declared and int(declared) > self.max_artifact_size:
                            raise CacheError("Artifact exceeds configured download limit", artifact_hash=sha256)
                        while True:
                            chunk = response.read(1024 * 1024)
                            if not chunk:
                                break
                            total += len(chunk)
                            if total > self.max_artifact_size:
                                raise CacheError("Artifact exceeds configured download limit", artifact_hash=sha256)
                            digest.update(chunk)
                            output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
                if expected_size is not None and total != expected_size:
                    raise IntegrityError(
                        "Downloaded artifact has the wrong size",
                        artifact_hash=sha256,
                        remediation=f"expected {expected_size} bytes but received {total}",
                    )
                actual = digest.hexdigest()
                if actual != sha256:
                    raise IntegrityError(
                        "Downloaded artifact hash mismatch",
                        artifact_hash=sha256,
                        remediation=f"expected {sha256}, received {actual}",
                    )
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
        fd, temporary_name = tempfile.mkstemp(prefix="live-download-", dir=temporary_dir)
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
                break
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise CacheError("Timed out waiting for another cache writer", artifact_hash=digest) from None
                time.sleep(0.025)
        try:
            yield
        finally:
            with contextlib.suppress(OSError):
                lock.rmdir()

    def list_blobs(self) -> list[Path]:
        root = self.root / "artifacts" / "sha256"
        return sorted(path for path in root.glob("*/*") if path.is_file()) if root.exists() else []

    def prune(self, referenced_hashes: set[str]) -> list[Path]:
        removed: list[Path] = []
        for path in self.list_blobs():
            if path.name not in referenced_hashes:
                path.unlink()
                (self.root / "metadata" / "origins" / f"{path.name}.json").unlink(missing_ok=True)
                removed.append(path)
        return removed

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


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
