#!/usr/bin/env python3
"""Exercise basic Depfix behavior and print environment-friendly diagnostics."""

from __future__ import annotations

import argparse
import platform
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import depfix


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, help="use an explicit cache root, such as tmp/depfix-debug-cache")
    parser.add_argument("--manifest", type=Path, help="activate an existing .depfix/imports.lock")
    parser.add_argument("--offline", action="store_true", help="forbid network access; requires prepared cache state")
    parser.add_argument("--refresh", action="store_true", help="resolve live version ranges again")
    parser.add_argument(
        "--extended",
        action="store_true",
        help="also check standalone-module and resource-backed packages",
    )
    return parser


def _module_version(module: ModuleType) -> str:
    return str(getattr(module, "__depfix_version__", getattr(module, "__version__", "unknown")))


def _show_module(label: str, module: ModuleType) -> None:
    logical = getattr(module, "__depfix_logical_name__", module.__name__)
    print(
        f"{label}: version={_module_version(module)} logical={logical} "
        f"canonical={module.__name__} object_id={id(module)}"
    )


def _load_options(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "manifest": args.manifest,
        "offline": args.offline,
        "refresh": args.refresh,
    }


def _basic_checks(args: argparse.Namespace) -> None:
    options = _load_options(args)
    idna_2 = depfix.import_module("idna==2.10", **options)
    idna_3 = depfix.import_module("idna==3.10", **options)
    idna_3_again = depfix.import_module("idna==3.10", **options)

    assert _module_version(idna_2) == "2.10"
    assert _module_version(idna_3) == "3.10"
    assert idna_2 is not idna_3
    assert idna_3_again is idna_3

    _show_module("idna old", idna_2)
    _show_module("idna new", idna_3)
    print("warm identity: same module object")

    package = depfix.load_package("packaging==24.2", **options)
    packaging_module = package.only_module()
    assert package.version == "24.2"
    assert _module_version(packaging_module) == "24.2"
    print(
        f"package handle: name={package.name} version={package.version} modules={package.module_names} "
        f"native={package.metadata.native_classification} realm={package.realm_id}"
    )


def _extended_checks(args: argparse.Namespace) -> None:
    options = _load_options(args)
    six = depfix.import_module("six==1.16.0", **options)
    pytz = depfix.import_module("pytz==2024.2", **options)

    assert _module_version(six) == "1.16.0"
    assert _module_version(pytz) == "2024.2"
    assert six.ensure_text(b"depfix") == "depfix"
    assert str(pytz.timezone("UTC")) == "UTC"

    _show_module("six standalone module", six)
    _show_module("pytz package", pytz)
    print("pytz resource/submodule check: UTC loaded")


def main() -> int:
    args = _parser().parse_args()
    if args.cache_dir is not None:
        depfix.configure(cache_dir=args.cache_dir)

    print(f"depfix={depfix.__version__}")
    print(f"python={platform.python_version()} implementation={platform.python_implementation()}")
    print(f"executable={sys.executable}")
    print(f"platform={platform.platform()}")
    print(f"cache_override={args.cache_dir or 'platform default'}")
    print(f"manifest={args.manifest or 'live mode'} offline={args.offline} refresh={args.refresh}")

    try:
        _basic_checks(args)
        if args.extended:
            _extended_checks(args)
    except depfix.DepfixError as exc:
        print(f"Depfix failure: {type(exc).__name__}: {exc}", file=sys.stderr)
        print("Run `depfix --json doctor` and retain this traceback for debugging.", file=sys.stderr)
        raise

    print("all requested Depfix checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
