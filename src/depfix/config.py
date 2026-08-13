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
    group_id: str = ""
    mode: str = "explicit"
    enclosing_function: str = ""
    isolation: str = "auto"
    allow_unsafe: bool | None = None
    prefer_newest: bool | None = None
    index_url: str | None = None
    extra_index_url: tuple[str, ...] | None = None


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
        policy = dict(raw.get("policy", {}))
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
            isolation = declaration.get("isolation", policy.get("isolation", "auto"))
            if isolation not in {"auto", "inprocess", "shared", "process"}:
                raise ValueError(f"isolation {isolation!r} for alias {name!r} is unsupported")
            allow_unsafe = declaration.get("allow-unsafe", policy.get("allow-unsafe", False))
            if not isinstance(allow_unsafe, bool):
                raise ValueError(f"allow-unsafe for alias {name!r} must be boolean")
            prefer_newest = declaration.get("prefer-newest", policy.get("prefer-newest"))
            if prefer_newest is not None and not isinstance(prefer_newest, bool):
                raise ValueError(f"prefer-newest for alias {name!r} must be boolean")
            declarations.append(
                ImportDeclaration(
                    name,
                    declaration["specifier"],
                    module,
                    isolation=isolation,
                    allow_unsafe=allow_unsafe,
                    prefer_newest=prefer_newest,
                )
            )
    except (KeyError, TypeError, ValueError) as exc:
        raise LockError("Malformed Depfix project configuration", manifest=path, remediation=str(exc)) from exc
    return ProjectConfig(path, tuple(declarations), policy)
