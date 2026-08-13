"""Documented uv executable adapter and private bootstrap fallback."""

from __future__ import annotations

import contextlib
import os
import platform
import re
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import time
import venv
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from email.parser import BytesParser
from email.policy import compat32
from pathlib import Path

from packaging.utils import canonicalize_name
from packaging.version import Version

from .cache import Cache
from .errors import (
    UnsupportedUvVersionError,
    UvBackendError,
    UvBootstrapError,
    UvNotFoundError,
    redact,
)
from .progress import ProgressReporter
from .settings import Settings

MINIMUM_UV_VERSION = Version("0.11.0")
BOOTSTRAP_UV_VERSION = Version("0.11.0")
_VERSION = re.compile(r"^uv\s+(\d+(?:\.\d+){2}(?:[^\s]*)?)")


@dataclass(frozen=True, slots=True)
class UvExecutable:
    path: Path
    version: Version
    source: str


@dataclass(frozen=True, slots=True)
class PreparedEnvironment:
    target: Path
    distributions: dict[str, str]


class UvBackend:
    def __init__(self, settings: Settings, cache: Cache, *, progress: ProgressReporter | None = None) -> None:
        self.settings = settings
        self.cache = cache
        self.progress = progress or ProgressReporter(settings.log_level)
        self._executable: UvExecutable | None = None
        self.invocation_count = 0

    def ensure_available(self, *, allow_bootstrap: bool = True) -> UvExecutable:
        if self._executable is not None:
            return self._executable
        rejected: list[str] = []
        for path, source in self._candidate_paths():
            try:
                executable = self._validate(path, source)
            except UvBackendError as exc:
                rejected.append(str(exc))
                continue
            self._executable = executable
            return executable
        if allow_bootstrap and not self.settings.frozen and not self.settings.offline:
            self._executable = self._bootstrap()
            return self._executable
        raise UvNotFoundError(
            "No compatible uv executable is available",
            candidates=tuple(rejected),
            offline=self.settings.offline,
            frozen=self.settings.frozen,
            remediation=f"install uv>={MINIMUM_UV_VERSION} or set DEPFIX_UV to a compatible executable",
        )

    def version(self) -> str:
        return str(self.ensure_available().version)

    def run(
        self,
        arguments: Sequence[str],
        *,
        cwd: Path | None = None,
        check: bool = True,
        extra_env: dict[str, str] | None = None,
        forward_output: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        executable = self.ensure_available()
        command = [str(executable.path), *arguments]
        self.cache.reconcile_intermediates()
        temporary_root = self.cache.root / "tmp"
        temporary_root.mkdir(parents=True, exist_ok=True)
        uv_cache = Path(tempfile.mkdtemp(prefix=f"uv-cache-{os.getpid()}-", dir=temporary_root))
        environment = os.environ.copy()
        environment.update(
            {
                "UV_NO_PROGRESS": "1",
                "UV_NO_PYTHON_DOWNLOADS": "1",
                "UV_NO_CONFIG": "1",
                "UV_INDEX_STRATEGY": "first-index",
                "UV_COLOR": "never",
            }
        )
        if self.settings.offline:
            environment["UV_OFFLINE"] = "1"
        if self.settings.index_url:
            environment["UV_DEFAULT_INDEX"] = self.settings.index_url
        if self.settings.extra_index_url:
            environment["UV_INDEX"] = " ".join(self.settings.extra_index_url)
        if extra_env:
            environment.update(extra_env)
        # Never let Depfix package operations populate uv's user/global cache. This
        # process-owned directory is removed after the invocation and reclaimed by
        # cache reconciliation if the process is interrupted.
        environment["UV_CACHE_DIR"] = str(uv_cache)
        self.invocation_count += 1
        try:
            result = subprocess.run(
                command,
                cwd=cwd,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
        finally:
            shutil.rmtree(uv_cache, ignore_errors=True)
        if forward_output and result.returncode == 0:
            self.progress.forward_uv(result.stdout, result.stderr)
        if check and result.returncode != 0:
            raise UvBackendError(
                "uv command failed",
                uv_command=command,
                uv_version=str(executable.version),
                rejections=(redact(result.stderr.strip()),),
                offline=self.settings.offline,
                frozen=self.settings.frozen,
                remediation="rerun with --verbose after verifying the requirement and index policy",
            )
        return result

    def prepare_requirement(self, requirement: str, *, target: Path, no_deps: bool = False) -> PreparedEnvironment:
        target.parent.mkdir(parents=True, exist_ok=True)
        arguments = [
            "pip",
            "install",
            "--target",
            str(target),
            "--python",
            sys.executable,
            "--no-python-downloads",
            "--no-config",
            "--color",
            "never",
        ]
        if self.settings.offline:
            arguments.append("--offline")
        if no_deps:
            arguments.append("--no-deps")
        if self.settings.index_url:
            arguments.extend(["--default-index", self.settings.index_url])
        for index in self.settings.extra_index_url:
            arguments.extend(["--index", index])
        arguments.append(requirement)
        self.run(arguments, forward_output=True)
        distributions = _installed_distributions(target)
        if not distributions:
            raise UvBackendError("uv completed without preparing a distribution", request=requirement)
        return PreparedEnvironment(target, distributions)

    def resolve_root_version(self, requirement: str, distribution: str) -> str:
        temporary_root = self.cache.root / "tmp"
        temporary_root.mkdir(parents=True, exist_ok=True)
        target = Path(tempfile.mkdtemp(prefix=f"uv-resolve-{os.getpid()}-", dir=temporary_root))
        try:
            prepared = self.prepare_requirement(requirement, target=target)
            normalized = str(canonicalize_name(distribution))
            try:
                return prepared.distributions[normalized]
            except KeyError as exc:
                raise UvBackendError(
                    "uv resolution did not contain the requested root distribution",
                    request=requirement,
                    candidates=tuple(f"{name}=={version}" for name, version in sorted(prepared.distributions.items())),
                ) from exc
        finally:
            shutil.rmtree(target, ignore_errors=True)

    def build_wheel(self, source: Path, *, output: Path, offline: bool | None = None) -> Path:
        output.mkdir(parents=True, exist_ok=True)
        arguments = ["build", "--wheel", "--out-dir", str(output), "--no-python-downloads", str(source)]
        if offline if offline is not None else self.settings.offline:
            arguments.append("--offline")
        self.run(arguments, cwd=source if source.is_dir() else source.parent, forward_output=True)
        wheels = sorted(output.glob("*.whl"), key=lambda path: path.name)
        if len(wheels) != 1:
            raise UvBackendError(
                "uv build must produce exactly one wheel",
                source=str(source),
                candidates=tuple(path.name for path in wheels),
            )
        return wheels[0]

    def _candidate_paths(self) -> Iterator[tuple[Path, str]]:
        seen: set[str] = set()

        def emit(path: Path | None, source: str) -> Iterator[tuple[Path, str]]:
            if path is None:
                return
            normalized = os.path.normcase(str(path.resolve()))
            if normalized not in seen:
                seen.add(normalized)
                yield path, source

        yield from emit(self.settings.uv, "explicit configuration")
        executable_name = "uv.exe" if os.name == "nt" else "uv"
        # The runtime dependency's script is normally next to this interpreter,
        # even when that directory is intentionally absent from PATH.
        yield from emit(Path(sys.executable).absolute().parent / executable_name, "Depfix runtime dependency")
        scripts = sysconfig.get_path("scripts")
        yield from emit(Path(scripts) / executable_name if scripts else None, "current Python scripts directory")
        located = shutil.which("uv")
        yield from emit(Path(located) if located else None, "PATH")
        yield from emit(self._managed_executable(), "Depfix private tool cache")

    def _validate(self, path: Path, source: str) -> UvExecutable:
        if not path.is_file():
            raise UvNotFoundError("uv candidate does not exist", source=source, cache_path=path)
        try:
            result = subprocess.run(
                [str(path), "--version"],
                text=True,
                capture_output=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise UvNotFoundError(
                "uv candidate could not be executed", source=source, cache_path=path, remediation=str(exc)
            ) from exc
        match = _VERSION.match(result.stdout.strip())
        if result.returncode != 0 or match is None:
            raise UvNotFoundError("uv candidate returned an invalid version response", source=source, cache_path=path)
        version = Version(match.group(1))
        if version < MINIMUM_UV_VERSION:
            raise UnsupportedUvVersionError(
                "uv is older than the minimum tested backend",
                source=source,
                cache_path=path,
                uv_version=str(version),
                remediation=f"install uv>={MINIMUM_UV_VERSION}",
            )
        return UvExecutable(path.resolve(), version, source)

    def _managed_executable(self) -> Path:
        root = self._managed_root()
        return root / ("Scripts/uv.exe" if os.name == "nt" else "bin/uv")

    def _managed_root(self) -> Path:
        platform_key = f"{sys.platform}-{platform.machine().lower()}-py{sys.version_info.major}{sys.version_info.minor}"
        return self.cache.root / "tools" / "uv" / str(BOOTSTRAP_UV_VERSION) / platform_key

    def _bootstrap(self) -> UvExecutable:
        destination = self._managed_root()
        executable = self._managed_executable()
        if executable.is_file():
            return self._validate(executable, "Depfix private tool cache")
        lock = self.cache.root / "locks" / "uv-bootstrap.lock"
        with _directory_lock(lock, timeout=120):
            if executable.is_file():
                return self._validate(executable, "Depfix private tool cache")
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = Path(tempfile.mkdtemp(prefix="uv-bootstrap-", dir=destination.parent))
            try:
                venv.EnvBuilder(with_pip=True, clear=True, symlinks=os.name != "nt").create(temporary)
                python = temporary / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
                command = [
                    str(python),
                    "-m",
                    "pip",
                    "install",
                    "--disable-pip-version-check",
                    f"uv=={BOOTSTRAP_UV_VERSION}",
                ]
                result = subprocess.run(
                    command,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                if result.returncode != 0:
                    raise UvBootstrapError(
                        "Unable to bootstrap a private uv tool environment",
                        uv_command=command,
                        rejections=(redact(result.stderr.strip()),),
                        cache_path=destination,
                        remediation=f"install uv>={MINIMUM_UV_VERSION} in the application environment",
                    )
                os.replace(temporary, destination)
            except BaseException:
                shutil.rmtree(temporary, ignore_errors=True)
                raise
        return self._validate(executable, "Depfix private bootstrap")


def _installed_distributions(target: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for metadata_path in sorted(target.glob("*.dist-info/METADATA")):
        metadata = BytesParser(policy=compat32).parsebytes(metadata_path.read_bytes())
        name = metadata.get("Name")
        version = metadata.get("Version")
        if name and version:
            result[str(canonicalize_name(name))] = version
    return result


@contextlib.contextmanager
def _directory_lock(path: Path, *, timeout: float) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    while True:
        try:
            path.mkdir()
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise UvBootstrapError("Timed out waiting for the uv bootstrap lock", cache_path=path) from None
            time.sleep(0.05)
    try:
        yield
    finally:
        with contextlib.suppress(OSError):
            path.rmdir()
