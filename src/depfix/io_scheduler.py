"""Deterministic size-weighted scheduling for Depfix-owned remote I/O."""

from __future__ import annotations

import concurrent.futures
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Generic, TypeVar

_T = TypeVar("_T")

IO_CAPACITY = 16
METADATA_WEIGHT = 4
_ONE_MIB = 1024 * 1024
_TEN_MIB = 10 * _ONE_MIB
_LARGE_ARTIFACT = 100 * _ONE_MIB


def artifact_io_weight(size: int | None, *, capacity: int = IO_CAPACITY) -> int:
    """Return a deterministic artifact slot weight within the configured capacity."""
    if size is None or size <= 0:
        base = capacity
    elif size < _ONE_MIB:
        base = 1
    elif size < _TEN_MIB:
        base = 2
    elif size < _LARGE_ARTIFACT:
        base = 4
    else:
        base = capacity
    return min(capacity, base)


@dataclass(frozen=True, slots=True)
class IOWork(Generic[_T]):
    """One immutable, stably positioned remote-I/O operation."""

    position: int
    size: int | None
    operation: Callable[[], _T]
    unknown_size_weight: int | None = None


def run_weighted_io(work: Sequence[IOWork[_T]], *, capacity: int) -> tuple[_T, ...]:
    """Run weighted work within one budget and return results in stable order."""
    if not work:
        return ()
    if capacity < 1 or capacity > 32:
        raise ValueError("max_io_workers must be between 1 and 32")
    ordered = tuple(sorted(work, key=lambda item: item.position))
    if capacity == 1:
        return tuple(item.operation() for item in ordered)
    results: dict[int, _T] = {}
    failures: dict[int, BaseException] = {}
    active: dict[concurrent.futures.Future[_T], tuple[int, int]] = {}
    remaining = list(ordered)
    available = capacity
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=min(capacity, len(ordered)), thread_name_prefix="depfix-io"
    ) as executor:
        while remaining or active:
            while remaining and not failures:
                item = remaining[0]
                weight = (
                    min(capacity, item.unknown_size_weight)
                    if item.size is None and item.unknown_size_weight is not None
                    else artifact_io_weight(item.size, capacity=capacity)
                )
                if weight > available and active:
                    break
                remaining.pop(0)
                active[executor.submit(item.operation)] = (item.position, weight)
                available -= weight
            if not active:
                break
            done, _pending = concurrent.futures.wait(active, return_when=concurrent.futures.FIRST_COMPLETED)
            for future in done:
                position, weight = active.pop(future)
                available += weight
                if future.cancelled():
                    continue
                try:
                    results[position] = future.result()
                except BaseException as exc:
                    failures[position] = exc
            if failures:
                remaining.clear()
                for future in active:
                    future.cancel()
    if failures:
        primary_position = min(failures)
        primary = failures[primary_position]
        secondary = sorted(position for position in failures if position != primary_position)
        if secondary:
            primary.add_note("Additional remote-I/O failures at stable positions: " + ", ".join(map(str, secondary)))
        raise primary
    return tuple(results[item.position] for item in ordered)
