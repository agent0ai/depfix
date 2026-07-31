"""Safe wheel inspection and extraction without installing into site-packages."""

from __future__ import annotations

import base64
import csv
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import zipfile
from dataclasses import dataclass
from email.parser import BytesParser
from email.policy import compat32
from pathlib import Path, PurePosixPath

from packaging.utils import canonicalize_name, parse_wheel_filename

from .errors import CacheError, IntegrityError, ResolutionError

_NATIVE_SUFFIXES = (".so", ".pyd", ".dll", ".dylib")
_DRIVE = re.compile(r"^[A-Za-z]:")
_READ_ONLY_DIRECTORY_MODE = stat.S_IRUSR | stat.S_IXUSR | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH
_PROMOTABLE_DIRECTORY_MODE = _READ_ONLY_DIRECTORY_MODE | stat.S_IWUSR
_READ_ONLY_FILE_MODE = stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH


@dataclass(frozen=True, slots=True)
class WheelInspection:
    distribution: str
    version: str
    build_tag: str
    python_tag: str
    abi_tag: str
    platform_tag: str
    requires_python: str
    requires_dist: tuple[str, ...]
    provided_modules: tuple[str, ...]
    public_modules: tuple[str, ...]
    private_modules: tuple[str, ...]
    all_importable_modules: tuple[str, ...]
    namespace_contributions: tuple[str, ...]
    native_classification: str
    metadata_dir: str


def inspect_wheel(path: Path, *, filename: str | None = None) -> WheelInspection:
    wheel_filename = filename or path.name
    try:
        distribution, version, build, tags = parse_wheel_filename(wheel_filename)
    except Exception as exc:
        raise ResolutionError("Invalid wheel filename", remediation=f"{wheel_filename}: {exc}") from exc
    with zipfile.ZipFile(path) as archive:
        names = _validate_members(archive)
        metadata_names = [name for name in names if name.endswith(".dist-info/METADATA")]
        if len(metadata_names) != 1:
            raise ResolutionError("Wheel must contain exactly one .dist-info/METADATA file", remediation=wheel_filename)
        metadata_name = metadata_names[0]
        metadata = BytesParser(policy=compat32).parsebytes(archive.read(metadata_name))
        metadata_distribution = canonicalize_name(metadata.get("Name", ""))
        metadata_version = metadata.get("Version", "")
        if metadata_distribution != canonicalize_name(str(distribution)) or metadata_version != str(version):
            raise ResolutionError(
                "Wheel filename and Core Metadata identity disagree",
                remediation=f"filename={distribution} {version}; metadata={metadata_distribution} {metadata_version}",
            )
        root_files: set[str] = set()
        importable: set[str] = set()
        packages: set[str] = set()
        top_modules: set[str] = set()
        native_modules: set[str] = set()
        native = False
        metadata_dir = metadata_name.split("/", 1)[0]
        for name in names:
            logical = _installed_relative(name)
            if logical is None or ".dist-info/" in logical or ".egg-info/" in logical:
                continue
            parts = PurePosixPath(logical).parts
            if not parts or "__pycache__" in parts:
                continue
            logical_name = _logical_import_name(parts)
            if logical_name is not None:
                importable.add(logical_name)
                if parts[-1] in {"__init__.py", "__init__.pyi"}:
                    packages.add(logical_name)
                elif len(parts) == 1:
                    top_modules.add(logical_name)
                root_files.add(logical_name.split(".", 1)[0])
            if logical.lower().endswith(_NATIVE_SUFFIXES):
                native = True
                native_name = _native_import_name(parts)
                if native_name is not None:
                    importable.add(native_name)
                    native_modules.add(native_name)
                    root_files.add(native_name.split(".", 1)[0])
        namespaces = _namespace_contributions(importable, packages)
        metadata_import_names = metadata.get_all("Import-Name")
        metadata_namespaces = metadata.get_all("Import-Namespace") or []
        metadata_public: set[str] = set()
        metadata_private: set[str] = set()
        declared_namespaces: set[str] = set()
        for value in metadata_namespaces:
            name, private = _metadata_import_name(value, allow_empty=False)
            if name:
                declared_namespaces.add(name)
                if private:
                    metadata_private.add(name)
        if metadata_import_names is not None:
            for value in metadata_import_names:
                name, private = _metadata_import_name(value, allow_empty=True)
                if not name:
                    continue
                (metadata_private if private else metadata_public).add(name)
            overlap = (metadata_public | metadata_private) & declared_namespaces
            if overlap:
                raise ResolutionError(
                    "Core Metadata lists the same name as exclusive and namespace-owned",
                    candidates=tuple(sorted(overlap)),
                    remediation=wheel_filename,
                )
            missing = {
                name
                for name in metadata_public | metadata_private
                if not _artifact_provides(name, importable, namespaces | declared_namespaces)
            }
            if missing:
                raise ResolutionError(
                    "Core Metadata import names are inconsistent with the wheel contents",
                    candidates=tuple(sorted(missing)),
                    remediation=wheel_filename,
                )
            public_modules = _leaf_names(metadata_public)
            private_modules = metadata_private
        else:
            public_modules, private_modules = _derive_public_modules(
                root_files, top_modules, packages, importable, namespaces
            )
        namespaces |= declared_namespaces
        tag = sorted(tags, key=str)[0]
        return WheelInspection(
            distribution=metadata_distribution,
            version=metadata_version,
            build_tag=".".join(str(item) for item in build) if build else "",
            python_tag=tag.interpreter,
            abi_tag=tag.abi,
            platform_tag=tag.platform,
            requires_python=metadata.get("Requires-Python", ""),
            requires_dist=tuple(metadata.get_all("Requires-Dist", [])),
            provided_modules=tuple(sorted(root_files)),
            public_modules=tuple(sorted(public_modules)),
            private_modules=tuple(sorted(private_modules)),
            all_importable_modules=tuple(sorted(importable | namespaces | metadata_public | metadata_private)),
            namespace_contributions=tuple(sorted(namespaces)),
            native_classification=(
                "native-unknown" if native or tag.platform != "any" or tag.abi != "none" else "pure-python"
            ),
            metadata_dir=metadata_dir,
        )


def _logical_import_name(parts: tuple[str, ...]) -> str | None:
    filename = parts[-1]
    if not filename.endswith((".py", ".pyi")):
        return None
    stem = filename.rsplit(".", 1)[0]
    components = list(parts[:-1])
    if stem != "__init__":
        components.append(stem)
    if not components or not all(part.isidentifier() for part in components):
        return None
    return ".".join(components)


def _native_import_name(parts: tuple[str, ...]) -> str | None:
    filename = parts[-1]
    stem = filename.split(".", 1)[0]
    components = [*parts[:-1], stem]
    return ".".join(components) if components and all(part.isidentifier() for part in components) else None


def _metadata_import_name(value: str, *, allow_empty: bool) -> tuple[str, bool]:
    name, separator, qualifier = value.partition(";")
    name = name.strip()
    private = separator == ";" and qualifier.strip() == "private"
    if separator and not private:
        raise ResolutionError("Invalid Import-Name/Import-Namespace qualifier", remediation=value)
    if not name:
        if allow_empty:
            return "", private
        raise ResolutionError("Import-Namespace may not be empty")
    if not all(part.isidentifier() for part in name.split(".")):
        raise ResolutionError("Invalid dotted import name in Core Metadata", remediation=value)
    return name, private


def _namespace_contributions(importable: set[str], packages: set[str]) -> set[str]:
    namespaces: set[str] = set()
    for name in importable:
        parts = name.split(".")
        for index in range(1, len(parts)):
            parent = ".".join(parts[:index])
            if parent not in packages:
                namespaces.add(parent)
    return namespaces


def _artifact_provides(name: str, importable: set[str], namespaces: set[str]) -> bool:
    return name in importable or name in namespaces or any(item.startswith(name + ".") for item in importable)


def _leaf_names(names: set[str]) -> set[str]:
    return {name for name in names if not any(other != name and other.startswith(name + ".") for other in names)}


def _derive_public_modules(
    roots: set[str],
    top_modules: set[str],
    packages: set[str],
    importable: set[str],
    namespaces: set[str],
) -> tuple[set[str], set[str]]:
    candidates: set[str] = set(top_modules)
    for root in roots:
        if root in packages:
            candidates.add(root)
            continue
        concrete = {name for name in importable if name.startswith(root + ".") and name not in namespaces}
        # Prefer the outermost concrete package/module under a namespace. This
        # maps google/cloud/storage/client.py to google.cloud.storage, while a
        # single acme/plugin.py contribution maps to acme.plugin.
        candidates.update(name for name in concrete if not any(parent in packages for parent in _parents(name)))
    private = {name for name in candidates if any(part.startswith("_") for part in name.split("."))}
    return _leaf_names(candidates - private), private


def _parents(name: str) -> list[str]:
    parts = name.split(".")
    return [".".join(parts[:index]) for index in range(1, len(parts))]


def extract_wheel(
    path: Path,
    destination: Path,
    *,
    max_files: int = 20_000,
    max_extracted_size: int = 1024 * 1024 * 1024,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=destination.name + ".", dir=destination.parent))
    try:
        with zipfile.ZipFile(path) as archive:
            names = _validate_members(archive, max_files=max_files, max_extracted_size=max_extracted_size)
            _verify_record(archive, names)
            targets: set[str] = set()
            folded: set[str] = set()
            for info in archive.infolist():
                relative = _installed_relative(info.filename)
                if relative is None or info.is_dir():
                    continue
                category = _category(info.filename)
                target_relative = f"{category}/{relative}"
                if target_relative in targets or target_relative.casefold() in folded:
                    raise CacheError(
                        "Wheel contains duplicate or case-folding-colliding installed paths",
                        remediation=target_relative,
                    )
                targets.add(target_relative)
                folded.add(target_relative.casefold())
                target = temporary / Path(*PurePosixPath(target_relative).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
        marker = temporary / ".complete"
        marker.write_text(
            json.dumps(
                {
                    "format_version": 1,
                    "kind": "wheel",
                    "artifact_sha256": _hash_file(path),
                    "installed_files": len(targets),
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        _make_read_only(temporary, writable_root=True)
        promoted = False
        try:
            os.replace(temporary, destination)
            promoted = True
        except OSError:
            if not destination.is_dir():
                raise
        if promoted:
            _make_root_read_only(destination)
    finally:
        if temporary.exists():
            _remove_staging_tree(temporary)


def _validate_members(
    archive: zipfile.ZipFile,
    *,
    max_files: int = 20_000,
    max_extracted_size: int = 1024 * 1024 * 1024,
) -> list[str]:
    infos = archive.infolist()
    if len(infos) > max_files:
        raise CacheError("Wheel exceeds configured file-count limit")
    if sum(info.file_size for info in infos) > max_extracted_size:
        raise CacheError("Wheel exceeds configured extracted-size limit")
    names: list[str] = []
    seen: set[str] = set()
    folded: set[str] = set()
    for info in infos:
        name = info.filename
        path = PurePosixPath(name)
        if (
            not name
            or "\\" in name
            or path.is_absolute()
            or ".." in path.parts
            or _DRIVE.match(name)
            or any(":" in part for part in path.parts)
        ):
            raise CacheError("Unsafe path in wheel", remediation=name)
        mode = info.external_attr >> 16
        if stat.S_ISLNK(mode):
            raise CacheError("Symbolic links are not accepted in wheels", remediation=name)
        normalized = str(path)
        if normalized in seen or normalized.casefold() in folded:
            raise CacheError("Duplicate or case-folding-colliding wheel member", remediation=name)
        seen.add(normalized)
        folded.add(normalized.casefold())
        names.append(normalized)
    return names


def _verify_record(archive: zipfile.ZipFile, names: list[str]) -> None:
    records = [name for name in names if name.endswith(".dist-info/RECORD")]
    if len(records) != 1:
        raise IntegrityError("Wheel must contain exactly one RECORD file")
    rows = csv.reader(archive.read(records[0]).decode("utf-8").splitlines())
    record: dict[str, tuple[str, str]] = {}
    for row in rows:
        if len(row) != 3:
            raise IntegrityError("Malformed wheel RECORD row")
        if row[0] in record:
            raise IntegrityError("Duplicate wheel RECORD path", remediation=row[0])
        record[row[0]] = (row[1], row[2])
    directories = {str(PurePosixPath(info.filename)) for info in archive.infolist() if info.is_dir()}
    for name in names:
        if name in directories:
            continue
        if name not in record:
            raise IntegrityError("Wheel member is absent from RECORD", remediation=name)
        hash_field, size_field = record[name]
        data = archive.read(name)
        if size_field and int(size_field) != len(data):
            raise IntegrityError("Wheel RECORD size mismatch", remediation=name)
        if hash_field:
            algorithm, separator, encoded = hash_field.partition("=")
            if separator != "=" or algorithm != "sha256":
                raise IntegrityError("Only SHA-256 wheel RECORD hashes are supported", remediation=name)
            actual = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode("ascii")
            if actual != encoded:
                raise IntegrityError("Wheel RECORD hash mismatch", remediation=name)


def _installed_relative(name: str) -> str | None:
    parts = PurePosixPath(name).parts
    if not parts:
        return None
    if len(parts) >= 3 and parts[0].endswith(".data"):
        scheme = parts[1]
        if scheme in {"purelib", "platlib", "data"}:
            return str(PurePosixPath(*parts[2:]))
        return None
    return str(PurePosixPath(*parts))


def _category(name: str) -> str:
    parts = PurePosixPath(name).parts
    if len(parts) >= 3 and parts[0].endswith(".data"):
        return parts[1] if parts[1] in {"purelib", "platlib", "data"} else "data"
    return "purelib"


def _make_read_only(root: Path, *, writable_root: bool = False) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        try:
            if path.is_dir():
                path.chmod(_READ_ONLY_DIRECTORY_MODE)
            else:
                path.chmod(_READ_ONLY_FILE_MODE)
        except OSError:
            pass
    try:
        root.chmod(_PROMOTABLE_DIRECTORY_MODE if writable_root else _READ_ONLY_DIRECTORY_MODE)
    except OSError:
        pass


def _make_root_read_only(root: Path) -> None:
    try:
        root.chmod(_READ_ONLY_DIRECTORY_MODE)
    except OSError:
        pass


def _remove_staging_tree(root: Path) -> None:
    for path in sorted(root.rglob("*")):
        if path.is_dir():
            try:
                path.chmod(stat.S_IRWXU)
            except OSError:
                pass
    try:
        root.chmod(stat.S_IRWXU)
    except OSError:
        pass
    shutil.rmtree(root, ignore_errors=True)


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
