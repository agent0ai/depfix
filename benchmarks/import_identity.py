"""Measure repeated public import identity for an already installed manifest."""

from __future__ import annotations

import argparse
import timeit
from pathlib import Path

import depfix


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("specifier")
    parser.add_argument("--module")
    parser.add_argument("--number", type=int, default=100_000)
    args = parser.parse_args()

    def load():  # type: ignore[no-untyped-def]
        return depfix.import_module(
            args.specifier,
            module=args.module,
            manifest=args.manifest,
            frozen=True,
            offline=True,
        )

    module = load()
    assert load() is module
    elapsed = timeit.timeit(load, number=args.number)
    print(f"{args.number} canonical imports: {elapsed:.6f}s ({elapsed / args.number * 1e6:.3f} us/import)")


if __name__ == "__main__":
    main()
