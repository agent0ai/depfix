"""Generate a bounded, reproducible Agent Zero artifact trace from uv pylock output."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
import tomllib
from pathlib import Path
from typing import Any

from packaging.tags import sys_tags
from packaging.utils import parse_wheel_filename


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _compatible_wheel(package: dict[str, Any], ranks: dict[Any, int]) -> dict[str, Any] | None:
    candidates: list[tuple[int, str, dict[str, Any]]] = []
    for wheel in package.get("wheels", []):
        filename = wheel["url"].rsplit("/", 1)[-1]
        try:
            _name, _version, _build, tags = parse_wheel_filename(filename)
        except Exception:
            continue
        compatible = [ranks[tag] for tag in tags if tag in ranks]
        if compatible:
            candidates.append((min(compatible), filename, wheel))
    return min(candidates, default=(0, "", None))[2]


def _artifact(package: dict[str, Any], source: dict[str, Any], *, kind: str) -> dict[str, Any]:
    url = source["url"]
    return {
        "name": package["name"],
        "version": package["version"],
        "filename": url.rsplit("/", 1)[-1],
        "url": url,
        "size": source["size"],
        "sha256": source["hashes"]["sha256"],
        "kind": kind,
    }


def _bounded_sample(wheels: list[dict[str, Any]], *, count: int = 32) -> list[dict[str, Any]]:
    ordered = sorted(wheels, key=lambda item: (item["size"], item["name"]))
    sample: list[dict[str, Any]] = []
    for index in range(count):
        position = round((len(ordered) - 1) * 0.95 * index / (count - 1))
        item = dict(ordered[position])
        item["sample_reason"] = f"stratum-{index + 1:02d}-of-{count}"
        sample.append(item)
    return sample


def _source_sample(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(sources, key=lambda item: (item["size"], item["name"]))
    positions = (0, len(ordered) // 2, len(ordered) - 1)
    labels = ("smallest", "median", "largest")
    sample = []
    for label, position in zip(labels, positions, strict=True):
        item = dict(ordered[position])
        item["sample_reason"] = f"{label}-source-only-build"
        sample.append(item)
    return sample


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--requirements", type=Path, required=True)
    parser.add_argument("--uv", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="depfix-agent-zero-trace-") as temporary:
        root = Path(temporary)
        pylock = root / "pylock.toml"
        command = [
            str(args.uv),
            "pip",
            "compile",
            str(args.requirements),
            "--format",
            "pylock.toml",
            "--generate-hashes",
            "--python-version",
            "3.11",
            "--python-platform",
            "aarch64-manylinux_2_28",
            "--no-config",
            "--no-python-downloads",
            "--cache-dir",
            str(root / "cache"),
            "-o",
            str(pylock),
        ]
        subprocess.run(command, check=True, capture_output=True, text=True)
        plan = tomllib.loads(pylock.read_text(encoding="utf-8"))
    ranks = {tag: index for index, tag in enumerate(sys_tags())}
    wheels: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    for package in plan["packages"]:
        wheel = _compatible_wheel(package, ranks)
        if wheel is not None:
            wheels.append(_artifact(package, wheel, kind="wheel"))
        elif package.get("sdist"):
            sources.append(_artifact(package, package["sdist"], kind="sdist"))
    payload = {
        "schema": 2,
        "description": "Agent Zero exact-plan 32-stratum wheel and three-stratum source-build replay.",
        "agent_zero_commit": "baadd0dd0b09fa769a1027c183b964be85d5c8cc",
        "requirements_sha256": _hash(args.requirements),
        "planner": "uv 0.12.5 pylock.toml; CPython 3.11; aarch64-manylinux_2_28",
        "distributions": len(plan["packages"]),
        "wheel_distributions": len(wheels),
        "source_only_artifacts": sorted(sources, key=lambda item: item["name"]),
        "selected_wheel_bytes": sum(item["size"] for item in wheels),
        "wheel_size_bytes": [item["size"] for item in sorted(wheels, key=lambda item: item["size"])],
        "artifacts": _bounded_sample(wheels),
        "largest_artifacts": sorted(wheels, key=lambda item: item["size"], reverse=True)[:3],
        "source_build_artifacts": _source_sample(sources),
    }
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
