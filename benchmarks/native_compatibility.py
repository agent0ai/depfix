"""Exercise representative native-package APIs in fresh Python processes."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import depfix


def _pydantic(module: ModuleType) -> None:
    record = module.create_model("Record", count=(int, ...))
    assert record(count="2").count == 2


def _orjson(module: ModuleType) -> None:
    encoded = module.dumps({"compatible": True})
    assert module.loads(encoded) == {"compatible": True}


def _numpy(module: ModuleType) -> None:
    values = module.array([1, 2, 3])
    assert int((values * 2).sum()) == 12


def _pillow(module: ModuleType) -> None:
    image = module.new("RGB", (2, 2), "red")
    assert image.size == (2, 2) and image.getpixel((0, 0)) == (255, 0, 0)


def _psutil(module: ModuleType) -> None:
    assert isinstance(module.cpu_count(), int) and module.cpu_count() >= 1


def _cryptography(module: ModuleType) -> None:
    key = module.Fernet.generate_key()
    cipher = module.Fernet(key)
    assert cipher.decrypt(cipher.encrypt(b"depfix")) == b"depfix"


def _torch(module: ModuleType) -> None:
    assert int(module.tensor([1, 2]).sum().item()) == 3


CASES: dict[str, tuple[str, str, Callable[[ModuleType], None]]] = {
    "pydantic": ("pydantic==2.13.4", "pydantic", _pydantic),
    "orjson": ("orjson==3.11.9", "orjson", _orjson),
    "numpy": ("numpy==2.4.6", "numpy", _numpy),
    "pillow": ("Pillow==12.3.0", "PIL.Image", _pillow),
    "psutil": ("psutil==7.2.2", "psutil", _psutil),
    "cryptography": ("cryptography==50.0.0", "cryptography.fernet", _cryptography),
    "torch": ("torch==2.13.0", "torch", _torch),
}


def _run_case(name: str, cache_dir: Path | None, offline: bool) -> dict[str, object]:
    specifier, logical_module, operation = CASES[name]
    options = {"cache_dir": cache_dir, "log_level": "WARNING"} if cache_dir is not None else {"log_level": "WARNING"}
    depfix.configure(**options)
    started = time.perf_counter()
    module = depfix.import_module(specifier, module=logical_module, offline=offline)
    operation(module)
    elapsed = time.perf_counter() - started
    assert module.__name__ == logical_module
    return {
        "case": name,
        "specifier": specifier,
        "module": logical_module,
        "mode": "shared",
        "seconds": round(elapsed, 6),
        "status": "passed",
    }


def _run_parent(names: list[str], cache_dir: Path | None, offline: bool) -> int:
    results: list[dict[str, object]] = []
    failed = False
    for name in names:
        command = [sys.executable, str(Path(__file__).resolve()), "--case", name]
        if cache_dir is not None:
            command.extend(("--cache-dir", str(cache_dir)))
        if offline:
            command.append("--offline")
        completed = subprocess.run(command, text=True, capture_output=True)
        if completed.returncode == 0:
            results.append(json.loads(completed.stdout))
            continue
        failed = True
        results.append(
            {
                "case": name,
                "status": "failed",
                "returncode": completed.returncode,
                "stderr": completed.stderr.strip(),
            }
        )
    print(json.dumps(results, indent=2, sort_keys=True))
    return int(failed)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=tuple(CASES), help=argparse.SUPPRESS)
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument(
        "--include-torch",
        action="store_true",
        help="also exercise the multi-gigabyte Torch dependency graph",
    )
    args = parser.parse_args()
    cache_dir = args.cache_dir.expanduser().resolve() if args.cache_dir is not None else None
    if args.case is not None:
        print(json.dumps(_run_case(args.case, cache_dir, args.offline), sort_keys=True))
        return 0
    names = [name for name in CASES if name != "torch"]
    if args.include_torch:
        names.append("torch")
    return _run_parent(names, cache_dir, args.offline)


if __name__ == "__main__":
    raise SystemExit(main())
