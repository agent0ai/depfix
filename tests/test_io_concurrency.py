from __future__ import annotations

import io
import json
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest
from conftest import build_index

from depfix.cache import Cache
from depfix.config import ImportDeclaration, ProjectConfig
from depfix.errors import IntegrityError
from depfix.io_scheduler import IOWork, artifact_io_weight, run_weighted_io
from depfix.progress import ProgressReporter
from depfix.resolver import Resolver
from depfix.settings import resolve_settings
from depfix.sync import sync_graph
from depfix.uv_backend import ResolutionPlan


def test_size_weights_use_sixteen_slot_budget() -> None:
    assert artifact_io_weight(1) == 1
    assert artifact_io_weight(1024 * 1024) == 2
    assert artifact_io_weight(10 * 1024 * 1024) == 4
    assert artifact_io_weight(None) == 16
    assert artifact_io_weight(0) == 16
    assert artifact_io_weight(100 * 1024 * 1024) == 16
    assert artifact_io_weight(1, capacity=32) == 1
    assert artifact_io_weight(1024 * 1024, capacity=32) == 2
    assert artifact_io_weight(10 * 1024 * 1024, capacity=32) == 4
    assert artifact_io_weight(None, capacity=32) == 32
    assert artifact_io_weight(100 * 1024 * 1024, capacity=32) == 32


def test_metadata_weight_is_explicit_and_unknown_artifacts_are_exclusive() -> None:
    guard = threading.Lock()
    active = 0
    artifact_active = 0
    metadata_peak = 0
    artifact_peak = 0

    def operation(kind: str) -> str:
        nonlocal active, artifact_active, metadata_peak, artifact_peak
        with guard:
            active += 1
            if kind == "artifact":
                artifact_active += 1
            metadata_peak = max(metadata_peak, active)
            artifact_peak = max(artifact_peak, artifact_active)
        time.sleep(0.02)
        with guard:
            active -= 1
            if kind == "artifact":
                artifact_active -= 1
        return kind

    metadata = tuple(IOWork(index, None, lambda: operation("metadata"), unknown_size_weight=4) for index in range(8))
    assert run_weighted_io(metadata, capacity=16) == ("metadata",) * 8
    assert metadata_peak == 4

    unknown_artifacts = tuple(IOWork(index, None, lambda: operation("artifact")) for index in range(4))
    assert run_weighted_io(unknown_artifacts, capacity=16) == ("artifact",) * 4
    assert artifact_peak == 1


def test_resolver_serializes_simple_index_artifacts_without_advertised_sizes(
    tmp_path: Path,
    wheel_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wheels = tuple(
        wheel_factory(f"unknown-size-{index}", "1.0.0", {f"unknown_size_{index}.py": f"VALUE = {index}\n"})
        for index in range(4)
    )
    index_url = build_index(tmp_path / "missing-size-index", wheels)
    for metadata in (tmp_path / "missing-size-index").glob("*/json"):
        payload = json.loads(metadata.read_text(encoding="utf-8"))
        for releases in payload["releases"].values():
            for artifact in releases:
                # Simple HTML and PEP 691 records without ``size`` normalize to zero.
                artifact["size"] = 0
        metadata.write_text(json.dumps(payload), encoding="utf-8")

    class Backend:
        def version(self) -> str:
            return "test"

        def resolve_requirements_plan(self, requirements, *, constraints=(), preferences=()):  # type: ignore[no-untyped-def]
            del constraints, preferences
            return ResolutionPlan({requirement: "1.0.0" for requirement in requirements})

    real_fetch = Cache.fetch_url_with_final
    guard = threading.Lock()
    active = 0
    peak = 0

    def measured_fetch(self, url, sha256, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal active, peak
        assert kwargs.get("expected_size") is None
        with guard:
            active += 1
            peak = max(peak, active)
        try:
            time.sleep(0.01)
            return real_fetch(self, url, sha256, **kwargs)
        finally:
            with guard:
                active -= 1

    monkeypatch.setattr(Cache, "fetch_url_with_final", measured_fetch)
    graph = Resolver(
        Cache(tmp_path / "missing-size-cache"),
        index_url=index_url,
        backend=Backend(),
    ).resolve(
        ProjectConfig(
            tmp_path / "missing-size.toml",
            tuple(
                ImportDeclaration(f"item_{index}", f"unknown-size-{index}", api="load_package") for index in range(4)
            ),
            {},
        )
    )

    assert peak == 1
    assert len(graph.artifacts) == 4
    assert all(artifact.size > 0 for artifact in graph.artifacts)
    assert not any((tmp_path / "missing-size-cache").rglob("*.part"))


@pytest.mark.parametrize(
    ("size", "expected_peak"),
    [(1, 16), (1024 * 1024, 8), (10 * 1024 * 1024, 4), (100 * 1024 * 1024, 1)],
)
def test_weighted_scheduler_enforces_active_budget(size: int, expected_peak: int) -> None:
    guard = threading.Lock()
    release = threading.Event()
    active = 0
    peak = 0

    def operation() -> int:
        nonlocal active, peak
        with guard:
            active += 1
            peak = max(peak, active)
            if peak == expected_peak:
                release.set()
        release.wait(timeout=1)
        time.sleep(0.005)
        with guard:
            active -= 1
        return 1

    results = run_weighted_io(tuple(IOWork(index, size, operation) for index in range(16)), capacity=16)
    assert results == (1,) * 16
    assert peak == expected_peak


def test_weighted_scheduler_returns_stable_results_and_primary_error() -> None:
    assert run_weighted_io(
        (
            IOWork(2, 1, lambda: "third"),
            IOWork(0, 1, lambda: "first"),
            IOWork(1, 1, lambda: "second"),
        ),
        capacity=16,
    ) == ("first", "second", "third")

    def fail(message: str, delay: float) -> None:
        time.sleep(delay)
        raise RuntimeError(message)

    with pytest.raises(RuntimeError, match="stable-first"):
        run_weighted_io(
            (
                IOWork(0, 1, lambda: fail("stable-first", 0.02)),
                IOWork(1, 1, lambda: fail("completed-first", 0)),
            ),
            capacity=16,
        )


def test_max_io_workers_configuration_bounds(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("DEPFIX_MAX_IO_WORKERS", "1")
    assert resolve_settings(cache_dir=tmp_path, discover=False).max_io_workers == 1
    monkeypatch.setenv("DEPFIX_MAX_IO_WORKERS", "33")
    with pytest.raises(Exception, match="between 1 and 32"):
        resolve_settings(cache_dir=tmp_path, discover=False)


def test_resolver_and_sync_outputs_are_identical_across_capacities(tmp_path: Path, wheel_factory) -> None:
    alpha = wheel_factory(
        "weighted-alpha",
        "1.0.0",
        {"weighted_alpha.py": "VALUE = 'alpha'\n"},
        requires=["weighted-shared==1.0.0"],
    )
    beta = wheel_factory("weighted-beta", "1.0.0", {"weighted_beta.py": "VALUE = 'beta'\n"})
    shared = wheel_factory("weighted-shared", "1.0.0", {"weighted_shared.py": "VALUE = 'shared'\n"})
    index = build_index(tmp_path / "index", (alpha, beta, shared))
    config = ProjectConfig(
        tmp_path / "weighted.toml",
        (
            ImportDeclaration("beta", "weighted-beta", api="load_package"),
            ImportDeclaration("alpha", "weighted-alpha", api="load_package"),
        ),
        {},
    )

    class Backend:
        def version(self) -> str:
            return "test"

        def resolve_requirements_plan(self, requirements, *, constraints=(), preferences=()):  # type: ignore[no-untyped-def]
            del requirements, constraints, preferences
            return ResolutionPlan(
                {
                    "weighted-alpha": "1.0.0",
                    "weighted-beta": "1.0.0",
                    "weighted-shared": "1.0.0",
                }
            )

    graphs = []
    resolver_progress = []
    sync_progress = []
    target_snapshots = []
    for capacity in (1, 4, 8, 16):
        resolve_stream = io.StringIO()
        settings = resolve_settings(
            cache_dir=tmp_path / f"resolve-{capacity}",
            index_url=index,
            max_io_workers=capacity,
            log_level="INFO",
            discover=False,
        )
        graph = Resolver(
            Cache(settings.cache_dir / "packages"),
            settings=settings,
            backend=Backend(),
            progress=ProgressReporter("INFO", stream=resolve_stream),
        ).resolve(config)
        graphs.append(graph)
        resolver_progress.append(resolve_stream.getvalue())

        sync_stream = io.StringIO()
        sync_cache = Cache(tmp_path / f"sync-{capacity}")
        sync_graph(
            graph,
            sync_cache,
            progress=ProgressReporter("INFO", stream=sync_stream),
            max_io_workers=capacity,
        )
        sync_progress.append(sync_stream.getvalue())
        target_snapshots.append(
            tuple(
                (
                    path.relative_to(sync_cache.root).as_posix(),
                    path.read_bytes(),
                    path.stat().st_mode & 0o777,
                )
                for path in sorted(sync_cache.root.rglob("*"))
                if path.is_file() and "metadata" not in path.parts
            )
        )

    assert graphs[1:] == graphs[:-1]
    assert resolver_progress[1:] == resolver_progress[:-1]
    assert sync_progress[1:] == sync_progress[:-1]
    assert target_snapshots[1:] == target_snapshots[:-1]


@pytest.mark.parametrize("capacity", [1, 16])
def test_sync_prefetch_selects_stable_integrity_error_and_cleans_parts(
    tmp_path: Path,
    wheel_factory,
    capacity: int,
) -> None:
    first_wheel = wheel_factory("integrity-first", "1.0.0", {"integrity_first.py": "VALUE = 1\n"})
    second_wheel = wheel_factory("integrity-second", "1.0.0", {"integrity_second.py": "VALUE = 2\n"})
    index = build_index(tmp_path / "integrity-index", (first_wheel, second_wheel))

    class Backend:
        def version(self) -> str:
            return "test"

        def resolve_root_version(self, requirement: str, distribution: str) -> str:
            del requirement, distribution
            return "1.0.0"

    graph = Resolver(Cache(tmp_path / "resolved"), index_url=index, backend=Backend()).resolve(
        ProjectConfig(
            tmp_path / "integrity.toml",
            (
                ImportDeclaration("first", "integrity-first", api="load_package"),
                ImportDeclaration("second", "integrity-second", api="load_package"),
            ),
            {},
        )
    )
    broken = replace(
        graph,
        artifacts=tuple(replace(artifact, size=artifact.size + 1) for artifact in graph.artifacts),
    )
    expected_primary = broken.artifacts[0].sha256
    cache = Cache(tmp_path / f"broken-{capacity}")

    with pytest.raises(IntegrityError) as captured:
        sync_graph(broken, cache, max_io_workers=capacity)

    assert captured.value.artifact_hash == expected_primary
    assert not any(path.is_file() for path in cache.root.glob("artifacts/**/*"))
    assert not any(cache.root.rglob("*.part"))
    assert not any(cache.root.glob("unpacked/*"))


def test_concurrent_sync_retries_transient_downloads_without_leaks(
    tmp_path: Path,
    wheel_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wheels = tuple(
        wheel_factory(f"retry-{index}", "1.0.0", {f"retry_{index}.py": f"VALUE = {index}\n"}) for index in range(4)
    )
    index_url = build_index(tmp_path / "retry-index", wheels)

    class Backend:
        def version(self) -> str:
            return "test"

        def resolve_requirements_plan(self, requirements, *, constraints=(), preferences=()):  # type: ignore[no-untyped-def]
            del constraints, preferences
            return ResolutionPlan({requirement: "1.0.0" for requirement in requirements})

    graph = Resolver(Cache(tmp_path / "retry-resolved"), index_url=index_url, backend=Backend()).resolve(
        ProjectConfig(
            tmp_path / "retry.toml",
            tuple(ImportDeclaration(f"retry_{index}", f"retry-{index}", api="load_package") for index in range(4)),
            {},
        )
    )

    import depfix.cache as cache_module

    real_open = cache_module._open_url
    calls: dict[str, int] = {}
    guard = threading.Lock()

    def flaky_open(request, **kwargs):  # type: ignore[no-untyped-def]
        url = request.full_url if hasattr(request, "full_url") else str(request)
        with guard:
            calls[url] = calls.get(url, 0) + 1
            fail = calls[url] == 1
        if fail:
            raise OSError("transient test interruption")
        return real_open(request, **kwargs)

    monkeypatch.setattr(cache_module, "_open_url", flaky_open)
    cache = Cache(tmp_path / "retry-sync")
    sync_graph(graph, cache, max_io_workers=16)

    assert all(calls[artifact.url] == 2 for artifact in graph.artifacts)
    assert all(cache.has_package(artifact.sha256) for artifact in graph.artifacts)
    assert not any(cache.root.rglob("*.part"))
