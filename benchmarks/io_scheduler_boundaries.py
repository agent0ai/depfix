"""Deterministic threshold and mixed-order probe for weighted I/O admission."""

from __future__ import annotations

import argparse
import json
import os
import resource
import tempfile
import threading
import time
from pathlib import Path

from depfix.io_scheduler import IOWork, run_weighted_io

_MIB = 1024 * 1024


def _measure(name: str, sizes: tuple[int | None, ...], *, capacity: int) -> dict[str, object]:
    guard = threading.Lock()
    active = 0
    peak_active = 0
    peak_temporary_files = 0
    starts: list[int] = []
    finishes: list[int] = []
    with tempfile.TemporaryDirectory(prefix="depfix-io-admission-") as temporary:
        root = Path(temporary)

        def operation(position: int) -> int:
            nonlocal active, peak_active, peak_temporary_files
            part = root / f"{position}.part"
            part.write_bytes(b"pending")
            with guard:
                active += 1
                peak_active = max(peak_active, active)
                peak_temporary_files = max(peak_temporary_files, len(tuple(root.glob("*.part"))))
                starts.append(position)
            time.sleep(0.03)
            with guard:
                finishes.append(position)
                active -= 1
            part.unlink()
            return position

        before = resource.getrusage(resource.RUSAGE_SELF)
        started = time.monotonic()
        results = run_weighted_io(
            tuple(
                IOWork(position, size, lambda position=position: operation(position))
                for position, size in enumerate(sizes)
            ),
            capacity=capacity,
        )
        wall = time.monotonic() - started
        after = resource.getrusage(resource.RUSAGE_SELF)
        if results != tuple(range(len(sizes))):
            raise AssertionError(f"{name}: stable results changed")
        if tuple(root.iterdir()):
            raise AssertionError(f"{name}: temporary files leaked")
    return {
        "case": name,
        "capacity": capacity,
        "operations": len(sizes),
        "bytes_advertised": sum(size for size in sizes if size is not None),
        "missing_size_operations": sum(size is None for size in sizes),
        "peak_active_connections": peak_active,
        "peak_temporary_files": peak_temporary_files,
        "temporary_files_after": 0,
        "wall_seconds": wall,
        "self_cpu_seconds": (after.ru_utime + after.ru_stime) - (before.ru_utime + before.ru_stime),
        "start_order": starts,
        "finish_order": finishes,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capacity", type=int, default=16)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    capacity = args.capacity
    tiny = 64 * 1024
    large = 100 * _MIB
    cases = (
        ("below-1-mib", (1 * _MIB - 1,) * 16, 16),
        ("at-1-mib", (1 * _MIB,) * 16, 8),
        ("below-10-mib", (10 * _MIB - 1,) * 16, 8),
        ("at-10-mib", (10 * _MIB,) * 16, 4),
        ("below-100-mib", (100 * _MIB - 1,) * 16, 4),
        ("at-100-mib", (large,) * 4, 1),
        ("missing-size", (None,) * 4, 1),
        ("large-then-tiny", (large, *((tiny,) * 16)), 16),
        ("tiny-then-large", (*((tiny,) * 16), large), 16),
        ("tiny-large-tiny", (*((tiny,) * 8), large, *((tiny,) * 8)), 8),
        ("missing-then-tiny", (None, *((tiny,) * 16)), 16),
    )
    runs = []
    for name, sizes, expected_peak in cases:
        observation = _measure(name, sizes, capacity=capacity)
        if observation["peak_active_connections"] != min(capacity, expected_peak):
            raise AssertionError(f"{name}: unexpected peak {observation['peak_active_connections']}")
        runs.append(observation)
    payload = {
        "schema": 1,
        "command": "python benchmarks/io_scheduler_boundaries.py --capacity 16",
        "python": os.sys.version.split()[0],
        "platform": os.sys.platform,
        "cpu_count": os.cpu_count(),
        "method": (
            "deterministic admission probe with equal-duration local operations and one part file per active operation"
        ),
        "runs": runs,
    }
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(rendered, end="")
    else:
        args.output.write_text(rendered, encoding="utf-8")


if __name__ == "__main__":
    main()
