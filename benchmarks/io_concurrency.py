"""Reproducible bounded-concurrency probe for Depfix I/O phases."""

from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import json
import os
import resource
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from packaging.specifiers import SpecifierSet

import depfix.cache as cache_module
import depfix.resolver as resolver_module
from depfix.cache import Cache, _remove_path
from depfix.io_scheduler import IOWork, run_weighted_io
from depfix.manifest import load_manifest
from depfix.project import install_packages
from depfix.resolver import Resolver
from depfix.wheel import extract_wheel, inspect_wheel


class _Metrics:
    def __init__(self) -> None:
        self._guard = threading.Lock()
        self.transport_open_calls = 0
        self.redirect_requests = 0
        self.transport_wait_seconds = 0.0
        self.contended_lock_entries = 0
        self.lock_wait_seconds = 0.0
        self.subprocesses = 0
        self.subprocess_wall_seconds = 0.0

    def add(self, field: str, value: int | float) -> None:
        with self._guard:
            setattr(self, field, getattr(self, field) + value)


class _TimedResponse:
    def __init__(self, response: Any, metrics: _Metrics) -> None:
        self._response = response
        self._metrics = metrics

    def read(self, size: int = -1) -> bytes:
        started = time.monotonic()
        try:
            return self._response.read(size)
        finally:
            self._metrics.add("transport_wait_seconds", time.monotonic() - started)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._response, name)

    def __enter__(self) -> _TimedResponse:
        self._response.__enter__()
        return self

    def __exit__(self, *args: object) -> Any:
        return self._response.__exit__(*args)


@contextlib.contextmanager
def _instrument() -> Iterator[_Metrics]:
    metrics = _Metrics()
    original_open = cache_module._open_url
    original_resolver_open = resolver_module._open_url
    original_redirect = cache_module._PolicyRedirectHandler.redirect_request
    original_lock = Cache._artifact_lock
    original_run = subprocess.run

    def measured_open(*args: Any, **kwargs: Any) -> _TimedResponse:
        metrics.add("transport_open_calls", 1)
        started = time.monotonic()
        try:
            response = original_open(*args, **kwargs)
        finally:
            metrics.add("transport_wait_seconds", time.monotonic() - started)
        return _TimedResponse(response, metrics)

    def measured_redirect(self: Any, *args: Any, **kwargs: Any) -> Any:
        metrics.add("redirect_requests", 1)
        return original_redirect(self, *args, **kwargs)

    @contextlib.contextmanager
    def measured_lock(self: Cache, digest: str) -> Iterator[None]:
        lock = self.root / "locks" / f"{digest}.lock"
        contended = lock.exists()
        started = time.monotonic()
        with original_lock(self, digest):
            if contended:
                metrics.add("contended_lock_entries", 1)
                metrics.add("lock_wait_seconds", time.monotonic() - started)
            yield

    def measured_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        metrics.add("subprocesses", 1)
        started = time.monotonic()
        try:
            return original_run(*args, **kwargs)
        finally:
            metrics.add("subprocess_wall_seconds", time.monotonic() - started)

    cache_module._open_url = measured_open
    resolver_module._open_url = measured_open
    cache_module._PolicyRedirectHandler.redirect_request = measured_redirect
    Cache._artifact_lock = measured_lock
    subprocess.run = measured_run
    try:
        yield metrics
    finally:
        cache_module._open_url = original_open
        resolver_module._open_url = original_resolver_open
        cache_module._PolicyRedirectHandler.redirect_request = original_redirect
        Cache._artifact_lock = original_lock
        subprocess.run = original_run


def _items(trace: Path, state: str) -> list[dict[str, Any]]:
    payload = json.loads(trace.read_text(encoding="utf-8"))
    key = "largest_artifacts" if state == "agent-zero-large" else "artifacts"
    items = sorted(payload[key], key=lambda item: item["name"])
    if state == "small":
        items = [item for item in items if item["name"] in {"idna", "six"}]
    elif state == "partial":
        items = items[::2]
    return items


def _download(cache: Cache, item: dict[str, Any]) -> tuple[str, int]:
    started = time.monotonic_ns()
    blob = cache.fetch_url(
        item["url"],
        item["sha256"],
        expected_size=item["size"],
        allowed_hosts=("files.pythonhosted.org",),
    )
    inspection = inspect_wheel(blob, filename=item["filename"])
    if inspection.distribution != item["name"] or str(inspection.version) != item["version"]:
        raise AssertionError(f"identity mismatch for {item['filename']}")
    return item["sha256"], time.monotonic_ns() - started


def _metadata(resolver: Resolver, item: dict[str, Any]) -> tuple[str, int]:
    started = time.monotonic_ns()
    payload = resolver._project_artifact_payload(item["name"], SpecifierSet(f"=={item['version']}"))
    files = payload.get("releases", {}).get(item["version"], [])
    if not any(entry.get("filename") == item["filename"] for entry in files):
        raise AssertionError(f"metadata omitted exact artifact {item['filename']}")
    return item["sha256"], time.monotonic_ns() - started


def _map(workers: int, function: Any, items: list[dict[str, Any]], *, weighted: bool = False) -> list[Any]:
    if workers == 1:
        return [function(item) for item in items]
    if weighted:
        return list(
            run_weighted_io(
                tuple(
                    IOWork(index, int(item["size"]), lambda item=item: function(item))
                    for index, item in enumerate(items)
                ),
                capacity=workers,
            )
        )
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        return list(executor.map(function, items))


def _usage_cpu(usage: resource.struct_rusage) -> float:
    return usage.ru_utime + usage.ru_stime


def _observation(
    *,
    state: str,
    phase: str,
    workers: int,
    items: list[dict[str, Any]],
    completed: list[Any],
    before_self: resource.struct_rusage,
    after_self: resource.struct_rusage,
    before_children: resource.struct_rusage,
    after_children: resource.struct_rusage,
    wall: float,
    metrics: _Metrics,
) -> dict[str, Any]:
    self_cpu = _usage_cpu(after_self) - _usage_cpu(before_self)
    child_cpu = _usage_cpu(after_children) - _usage_cpu(before_children)
    return {
        "state": state,
        "phase": phase,
        "workers": workers,
        "artifacts": len(items),
        "bytes": sum(item["size"] for item in items),
        "transport_open_calls": metrics.transport_open_calls,
        "redirect_requests": metrics.redirect_requests,
        "transport_wait_seconds_sum": metrics.transport_wait_seconds,
        "contended_lock_entries": metrics.contended_lock_entries,
        "lock_wait_seconds_sum": metrics.lock_wait_seconds,
        "subprocesses": metrics.subprocesses,
        "subprocess_wall_seconds": metrics.subprocess_wall_seconds,
        "wall_seconds": wall,
        "self_cpu_seconds": self_cpu,
        "child_cpu_seconds": child_cpu,
        "unattributed_wall_minus_cpu_seconds": max(0.0, wall - self_cpu - child_cpu),
        "peak_rss_mib": after_self.ru_maxrss / 1024,
        "child_peak_rss_mib": after_children.ru_maxrss / 1024,
        "disk_input_blocks": after_self.ru_inblock - before_self.ru_inblock,
        "disk_output_blocks": after_self.ru_oublock - before_self.ru_oublock,
        "child_disk_input_blocks": after_children.ru_inblock - before_children.ru_inblock,
        "child_disk_output_blocks": after_children.ru_oublock - before_children.ru_oublock,
        "verified_hashes": len({result[0] for result in completed}),
    }


def _single(
    trace: Path,
    workers: int,
    state: str,
    phase: str,
    seed_dir: Path | None,
    uv: Path | None,
    weighted: bool = False,
) -> dict[str, Any]:
    payload = json.loads(trace.read_text(encoding="utf-8"))
    items = _items(trace, state)
    with tempfile.TemporaryDirectory(prefix="depfix-io-probe-") as temporary:
        cache = Cache(Path(temporary) / "cache")
        if phase in {"materialize", "warm-verify"}:
            if seed_dir is None:
                raise ValueError("local phases require --seed-dir")
            for item in items:
                destination = cache.blob_path(item["sha256"])
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(seed_dir / item["sha256"], destination)
        if phase == "warm-verify":
            for item in items:
                extract_wheel(cache.blob_path(item["sha256"]), cache.unpacked_path(item["sha256"]))
        destinations = {item["sha256"]: cache.unpacked_path(item["sha256"]) for item in items}
        before_self = resource.getrusage(resource.RUSAGE_SELF)
        before_children = resource.getrusage(resource.RUSAGE_CHILDREN)
        wall_start = time.monotonic()
        with _instrument() as metrics:
            if phase == "metadata":
                resolver = Resolver(cache)
                completed = _map(workers, lambda item: _metadata(resolver, item), items)
            elif phase == "acquire":
                completed = _map(workers, lambda item: _download(cache, item), items, weighted=weighted)
                for item in items:
                    cache.verify_blob(item["sha256"], size=item["size"])
            elif phase == "materialize":
                completed = _map(
                    workers,
                    lambda item: (
                        item["sha256"],
                        extract_wheel(cache.blob_path(item["sha256"]), destinations[item["sha256"]]),
                    ),
                    items,
                )
                if not all(cache.has_package(item["sha256"]) for item in items):
                    raise AssertionError("materialized target failed validation")
            elif phase == "warm-verify":
                completed = _map(
                    workers,
                    lambda item: (item["sha256"], cache.has_package(item["sha256"])),
                    items,
                )
                if not all(valid for _digest, valid in completed):
                    raise AssertionError("warm target failed validation")
            else:
                if uv is None:
                    raise ValueError("source build requires --uv")
                items = payload["source_build_artifacts"]

                def build(item: dict[str, Any]) -> tuple[str, bool]:
                    source_blob = cache.fetch_url(
                        item["url"],
                        item["sha256"],
                        expected_size=item["size"],
                        allowed_hosts=("files.pythonhosted.org",),
                    )
                    build_root = Path(temporary) / item["name"]
                    build_root.mkdir()
                    source = build_root / item["filename"]
                    shutil.copyfile(source_blob, source)
                    output = build_root / "wheel-output"
                    subprocess.run(
                        [
                            str(uv),
                            "build",
                            "--wheel",
                            "--out-dir",
                            str(output),
                            "--no-config",
                            "--no-python-downloads",
                            "--cache-dir",
                            str(build_root / "uv-cache"),
                            str(source),
                        ],
                        check=True,
                        capture_output=True,
                        text=True,
                    )
                    wheels = list(output.glob("*.whl"))
                    if len(wheels) != 1:
                        raise AssertionError("source build did not produce exactly one wheel")
                    inspection = inspect_wheel(wheels[0], filename=wheels[0].name)
                    if inspection.distribution != item["name"] or str(inspection.version) != item["version"]:
                        raise AssertionError("source build identity mismatch")
                    return item["sha256"], True

                completed = _map(workers, build, items)
        wall = time.monotonic() - wall_start
        after_self = resource.getrusage(resource.RUSAGE_SELF)
        after_children = resource.getrusage(resource.RUSAGE_CHILDREN)
    return _observation(
        state=state,
        phase=phase,
        workers=workers,
        items=items,
        completed=completed,
        before_self=before_self,
        after_self=after_self,
        before_children=before_children,
        after_children=after_children,
        wall=wall,
        metrics=metrics,
    ) | {"policy": "weighted" if weighted else "uniform"}


REQUIREMENTS = (
    "requests==2.32.3",
    "flask==3.1.2",
    "pydantic==2.11.7",
    "boto3==1.40.15",
    "markdown==3.8.2",
    "beautifulsoup4==4.13.5",
)


def _setup_end_to_end(cache_dir: Path, base_dir: Path, state: str) -> None:
    result = install_packages(REQUIREMENTS, cache_dir=cache_dir, base_dir=base_dir)
    if state == "partial":
        graph = load_manifest(result.manifest)
        cache = Cache(cache_dir)
        for artifact in sorted(graph.artifacts, key=lambda value: value.sha256)[::2]:
            _remove_path(cache.unpacked_path(artifact.id))


def _end_to_end_single(cache_dir: Path, base_dir: Path, state: str) -> dict[str, Any]:
    before_self = resource.getrusage(resource.RUSAGE_SELF)
    before_children = resource.getrusage(resource.RUSAGE_CHILDREN)
    wall_start = time.monotonic()
    with _instrument() as metrics:
        result = install_packages(REQUIREMENTS, cache_dir=cache_dir, base_dir=base_dir)
    wall = time.monotonic() - wall_start
    after_self = resource.getrusage(resource.RUSAGE_SELF)
    after_children = resource.getrusage(resource.RUSAGE_CHILDREN)
    return _observation(
        state=state,
        phase="end-to-end",
        workers=1,
        items=[],
        completed=[(str(index), True) for index in range(result.artifacts)],
        before_self=before_self,
        after_self=after_self,
        before_children=before_children,
        after_children=after_children,
        wall=wall,
        metrics=metrics,
    ) | {"artifacts": result.artifacts, "bytes": None}


def _child(command: list[str]) -> dict[str, Any]:
    completed = subprocess.run(command, check=True, capture_output=True, text=True)
    return json.loads(completed.stdout)


def _run_matrix(
    trace: Path,
    agent_zero_trace: Path,
    uv: Path,
    workers: list[int],
    repetitions: int,
    output: Path,
    baseline_label: str,
) -> None:
    runs: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="depfix-io-seed-") as seed_name:
        seed_dir = Path(seed_name)
        seed_cache = Cache(seed_dir / "cache")
        seed_items = _items(trace, "cold")
        _map(min(8, len(seed_items)), lambda item: _download(seed_cache, item), seed_items)
        for item in seed_items:
            shutil.copyfile(seed_cache.blob_path(item["sha256"]), seed_dir / item["sha256"])
        phases = (
            (trace, "metadata", "cold"),
            (trace, "acquire", "small"),
            (trace, "acquire", "cold"),
            (trace, "acquire", "partial"),
            (trace, "materialize", "cold"),
            (trace, "warm-verify", "cold"),
            (agent_zero_trace, "acquire", "agent-zero-sample"),
        )
        for phase_trace, phase, state in phases:
            for repetition in range(repetitions):
                order = workers if repetition % 2 == 0 else list(reversed(workers))
                for bound in order:
                    command = [
                        sys.executable,
                        str(Path(__file__).resolve()),
                        "--trace",
                        str(phase_trace),
                        "--single",
                        "--state",
                        state,
                        "--phase",
                        phase,
                        "--workers",
                        str(bound),
                    ]
                    if phase in {"materialize", "warm-verify"}:
                        command.extend(("--seed-dir", str(seed_dir)))
                    observation = _child(command)
                    observation["repetition"] = repetition + 1
                    runs.append(observation)
        large_bounds = [bound for bound in workers if bound in {1, 4}]
        for bound in large_bounds:
            observation = _child(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--trace",
                    str(agent_zero_trace),
                    "--single",
                    "--state",
                    "agent-zero-large",
                    "--phase",
                    "acquire",
                    "--workers",
                    str(bound),
                ]
            )
            observation["repetition"] = 1
            runs.append(observation)
        for repetition in range(repetitions):
            order = workers if repetition % 2 == 0 else list(reversed(workers))
            for bound in order:
                command = [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--trace",
                    str(agent_zero_trace),
                    "--single",
                    "--state",
                    "agent-zero-source",
                    "--phase",
                    "source-build",
                    "--workers",
                    str(bound),
                    "--uv",
                    str(uv),
                ]
                observation = _child(command)
                observation["repetition"] = repetition + 1
                runs.append(observation)
        for repetition in range(repetitions):
            for state in ("cold", "partial", "warm"):
                with tempfile.TemporaryDirectory(prefix=f"depfix-io-e2e-{state}-") as temporary:
                    root = Path(temporary)
                    cache_dir = root / "cache"
                    if state != "cold":
                        subprocess.run(
                            [
                                sys.executable,
                                str(Path(__file__).resolve()),
                                "--end-to-end-setup",
                                "--state",
                                state,
                                "--cache-dir",
                                str(cache_dir),
                                "--base-dir",
                                str(root),
                            ],
                            check=True,
                            capture_output=True,
                            text=True,
                        )
                    observation = _child(
                        [
                            sys.executable,
                            str(Path(__file__).resolve()),
                            "--end-to-end-single",
                            "--state",
                            state,
                            "--cache-dir",
                            str(cache_dir),
                            "--base-dir",
                            str(root),
                        ]
                    )
                    observation["repetition"] = repetition + 1
                    runs.append(observation)
    payload = {
        "schema": 2,
        "command": (
            "python benchmarks/io_concurrency.py --trace benchmarks/data/io-concurrency-medium.json "
            "--agent-zero-trace benchmarks/data/io-concurrency-agent-zero-summary.json "
            "--uv .venv/bin/uv --workers 1 2 4 8 --repetitions 3 "
            "--output docs/research/io-concurrency-evidence.json"
        ),
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "cpu_count": os.cpu_count(),
        "baseline": baseline_label,
        "trace": str(trace),
        "agent_zero_trace": str(agent_zero_trace),
        "runs": runs,
    }
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _run_weighted_matrix(
    trace: Path,
    agent_zero_trace: Path,
    workers: list[int],
    repetitions: int,
    output: Path,
    baseline_label: str,
    include_large: bool,
) -> None:
    """Measure the shipped weighted scheduler without rerunning local phases."""
    runs: list[dict[str, Any]] = []
    phases = (
        (trace, "small"),
        (trace, "cold"),
        (trace, "partial"),
        (agent_zero_trace, "agent-zero-sample"),
    )
    for phase_trace, state in phases:
        for repetition in range(repetitions):
            order = workers if repetition % 2 == 0 else list(reversed(workers))
            for bound in order:
                observation = _child(
                    [
                        sys.executable,
                        str(Path(__file__).resolve()),
                        "--trace",
                        str(phase_trace),
                        "--single",
                        "--weighted",
                        "--state",
                        state,
                        "--phase",
                        "acquire",
                        "--workers",
                        str(bound),
                    ]
                )
                observation["repetition"] = repetition + 1
                runs.append(observation)
    if include_large:
        observation = _child(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--trace",
                str(agent_zero_trace),
                "--single",
                "--weighted",
                "--state",
                "agent-zero-large",
                "--phase",
                "acquire",
                "--workers",
                str(max(workers)),
            ]
        )
        observation["repetition"] = 1
        runs.append(observation)
    payload = {
        "schema": 1,
        "command": "python benchmarks/io_concurrency.py --weighted-matrix --workers 1 4 8 16",
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "cpu_count": os.cpu_count(),
        "baseline": baseline_label,
        "trace": str(trace),
        "agent_zero_trace": str(agent_zero_trace),
        "runs": runs,
    }
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trace", type=Path)
    parser.add_argument("--agent-zero-trace", type=Path)
    parser.add_argument("--uv", type=Path)
    parser.add_argument("--workers", type=int, nargs="+", default=[1, 2, 4, 8])
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--baseline-label", default="active import path")
    parser.add_argument("--single", action="store_true")
    parser.add_argument("--weighted", action="store_true")
    parser.add_argument("--weighted-matrix", action="store_true")
    parser.add_argument("--include-large", action="store_true")
    parser.add_argument("--end-to-end-single", action="store_true")
    parser.add_argument("--end-to-end-setup", action="store_true")
    parser.add_argument("--state", default="cold")
    parser.add_argument(
        "--phase", choices=("metadata", "acquire", "materialize", "warm-verify", "source-build"), default="acquire"
    )
    parser.add_argument("--seed-dir", type=Path)
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--base-dir", type=Path)
    args = parser.parse_args()
    if args.end_to_end_setup:
        _setup_end_to_end(args.cache_dir, args.base_dir, args.state)
        return
    if args.end_to_end_single:
        print(json.dumps(_end_to_end_single(args.cache_dir, args.base_dir, args.state), sort_keys=True))
        return
    if args.trace is None:
        parser.error("--trace is required")
    if args.weighted_matrix:
        if args.output is None or args.agent_zero_trace is None:
            parser.error("weighted matrix runs require --output and --agent-zero-trace")
        _run_weighted_matrix(
            args.trace,
            args.agent_zero_trace,
            args.workers,
            args.repetitions,
            args.output,
            args.baseline_label,
            args.include_large,
        )
        return
    if args.single:
        print(
            json.dumps(
                _single(
                    args.trace,
                    args.workers[0],
                    args.state,
                    args.phase,
                    args.seed_dir,
                    args.uv,
                    weighted=args.weighted,
                ),
                sort_keys=True,
            )
        )
        return
    if args.output is None or args.agent_zero_trace is None or args.uv is None:
        parser.error("matrix runs require --output, --agent-zero-trace, and --uv")
    _run_matrix(
        args.trace, args.agent_zero_trace, args.uv, args.workers, args.repetitions, args.output, args.baseline_label
    )


if __name__ == "__main__":
    main()
