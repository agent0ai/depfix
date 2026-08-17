from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import zipfile
from collections.abc import Iterable
from pathlib import Path

import pytest


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_wheel(
    directory: Path,
    distribution: str,
    version: str,
    files: dict[str, str | bytes],
    *,
    requires: Iterable[str] = (),
    tag: str = "py3-none-any",
    metadata_version: str = "2.3",
    requires_python: str = "",
    import_names: Iterable[str] | None = None,
    import_namespaces: Iterable[str] = (),
) -> Path:
    normalized = distribution.replace("-", "_")
    filename = f"{normalized}-{version}-{tag}.whl"
    wheel = directory / filename
    dist_info = f"{normalized}-{version}.dist-info"
    metadata = (
        f"Metadata-Version: {metadata_version}\n"
        f"Name: {distribution}\n"
        f"Version: {version}\n"
        + (f"Requires-Python: {requires_python}\n" if requires_python else "")
        + "".join(f"Requires-Dist: {requirement}\n" for requirement in requires)
        + ("".join(f"Import-Name: {name}\n" for name in import_names) if import_names is not None else "")
        + "".join(f"Import-Namespace: {name}\n" for name in import_namespaces)
        + "\n"
    )
    wheel_metadata = f"Wheel-Version: 1.0\nGenerator: depfix-tests\nRoot-Is-Purelib: true\nTag: {tag}\n\n"
    members: dict[str, bytes] = {
        **{name: value.encode() if isinstance(value, str) else value for name, value in files.items()},
        f"{dist_info}/METADATA": metadata.encode(),
        f"{dist_info}/WHEEL": wheel_metadata.encode(),
    }
    rows: list[list[str]] = []
    for name, value in sorted(members.items()):
        encoded = base64.urlsafe_b64encode(hashlib.sha256(value).digest()).rstrip(b"=").decode()
        rows.append([name, f"sha256={encoded}", str(len(value))])
    record_name = f"{dist_info}/RECORD"
    rows.append([record_name, "", ""])
    stream = io.StringIO()
    csv.writer(stream, lineterminator="\n").writerows(rows)
    members[record_name] = stream.getvalue().encode()
    with zipfile.ZipFile(wheel, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, value in members.items():
            archive.writestr(name, value)
    return wheel


def file_spec(path: Path, *, kind: str = "wheel") -> str:
    del kind  # Local wheels and single modules share the public ``file:`` source form.
    return f"file:{path.resolve().as_posix()}#sha256={sha256(path)}"


def build_index(root: Path, wheels: Iterable[Path]) -> str:
    releases_by_project: dict[str, dict[str, list[dict[str, object]]]] = {}
    from packaging.utils import parse_wheel_filename

    for wheel in wheels:
        distribution, version, _build, _tags = parse_wheel_filename(wheel.name)
        project = str(distribution).replace("_", "-").lower()
        releases = releases_by_project.setdefault(project, {})
        releases.setdefault(str(version), []).append(
            {
                "filename": wheel.name,
                "packagetype": "bdist_wheel",
                "url": wheel.resolve().as_uri(),
                "size": wheel.stat().st_size,
                "digests": {"sha256": sha256(wheel)},
                "requires_python": ">=3.11",
                "yanked": False,
            }
        )
    for project, releases in releases_by_project.items():
        target = root / project
        target.mkdir(parents=True, exist_ok=True)
        (target / "json").write_text(json.dumps({"releases": releases}), encoding="utf-8")
    return root.resolve().as_uri()


@pytest.fixture
def wheel_factory(tmp_path: Path):
    def factory(distribution: str, version: str, files: dict[str, str | bytes], **kwargs: object) -> Path:
        return build_wheel(tmp_path, distribution, version, files, **kwargs)

    return factory
