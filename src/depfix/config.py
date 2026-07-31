"""Project configuration loading."""

from __future__ import annotations

import keyword
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import LockError


@dataclass(frozen=True, slots=True)
class ImportDeclaration:
    name: str
    specifier: str
    module: str | None = None
    api: str = "import_module"
    source_file: str = ""
    source_line: int = 0
    source_column: int = 0
    assignment: str = ""
    base_dir: Path | None = None


@dataclass(frozen=True, slots=True)
class ProjectConfig:
    path: Path
    imports: tuple[ImportDeclaration, ...]
    policy: dict[str, Any]


def load_config(path: Path) -> ProjectConfig:
    try:
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise LockError("Unable to read Depfix import configuration", manifest=path, remediation=str(exc)) from exc
    declarations: list[ImportDeclaration] = []
    try:
        imports = raw.get("imports", {})
        if not isinstance(imports, dict) or not imports:
            raise ValueError("[imports] must declare at least one alias table")
        for name, declaration in sorted(imports.items()):
            if not isinstance(name, str) or not name.isidentifier() or keyword.iskeyword(name):
                raise ValueError(f"alias {name!r} is not a valid Python identifier")
            module = declaration.get("module")
            if module is not None and (
                not isinstance(module, str) or not module or not all(part.isidentifier() for part in module.split("."))
            ):
                raise ValueError(f"module {module!r} for alias {name!r} is not a dotted Python name")
            declarations.append(ImportDeclaration(name, declaration["specifier"], module))
        policy = dict(raw.get("policy", {}))
    except (KeyError, TypeError, ValueError) as exc:
        raise LockError("Malformed Depfix project configuration", manifest=path, remediation=str(exc)) from exc
    return ProjectConfig(path, tuple(declarations), policy)
