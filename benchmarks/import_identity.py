"""Measure cached alias lookup overhead for an already installed manifest."""

from __future__ import annotations

import argparse
import timeit
from pathlib import Path

import depfix


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("alias")
    parser.add_argument("--number", type=int, default=100_000)
    args = parser.parse_args()
    runtime = depfix.activate(args.manifest)
    elapsed = timeit.timeit(lambda: runtime.load_alias(args.alias), number=args.number)
    print(f"{args.number} canonical alias loads: {elapsed:.6f}s ({elapsed / args.number * 1e6:.3f} us/load)")


if __name__ == "__main__":
    main()
