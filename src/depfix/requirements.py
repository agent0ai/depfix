"""Canonical parsing for pip-style requirements and constraint files."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path

from packaging.requirements import InvalidRequirement, Requirement

from .errors import RequirementsFileError
from .sources import parse_source


@dataclass(slots=True)
class RequirementCollection:
    requirements: list[str] = field(default_factory=list)
    requirement_origins: list[tuple[Path, int]] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    index_url: str | None = None
    extra_index_urls: list[str] = field(default_factory=list)

    def merge(self, other: RequirementCollection, *, source: Path, line: int) -> None:
        self.requirements.extend(other.requirements)
        self.requirement_origins.extend(other.requirement_origins)
        self.constraints.extend(other.constraints)
        if other.index_url is not None:
            if self.index_url is not None and self.index_url != other.index_url:
                raise _error("requirements files declare conflicting primary indexes", source, line)
            self.index_url = other.index_url
        self.extra_index_urls.extend(other.extra_index_urls)


def read_requirements(
    path: str | Path,
    *,
    constraints_only: bool = False,
    _stack: tuple[Path, ...] = (),
) -> RequirementCollection:
    """Parse one requirements tree using Depfix's supported pip-file grammar."""
    source = Path(path).expanduser().resolve()
    if source in _stack:
        chain = " -> ".join(item.name for item in (*_stack, source))
        raise _error(f"recursive requirements include detected ({chain})", source, 1)
    try:
        logical_lines = _logical_lines(source)
    except OSError as exc:
        raise _error("requirements file could not be read", source, 1, remediation=str(exc)) from exc

    result = RequirementCollection()
    stack = (*_stack, source)
    for line, value in logical_lines:
        cleaned = re.sub(r"\s+#.*$", "", value).strip()
        if not cleaned:
            continue
        try:
            tokens = shlex.split(cleaned, comments=False)
        except ValueError as exc:
            raise _error("malformed requirements line", source, line, remediation=str(exc)) from exc
        if not tokens:
            continue
        option, option_value = _file_option(tokens, source, line)
        if option in {"requirement", "constraint"}:
            assert option_value is not None
            included = Path(option_value)
            if not included.is_absolute():
                included = source.parent / included
            nested = read_requirements(
                included,
                constraints_only=constraints_only or option == "constraint",
                _stack=stack,
            )
            result.merge(nested, source=source, line=line)
            continue
        if option == "index-url":
            assert option_value is not None
            result.merge(RequirementCollection(index_url=option_value), source=source, line=line)
            continue
        if option == "extra-index-url":
            assert option_value is not None
            result.extra_index_urls.append(option_value)
            continue
        if option == "editable":
            if constraints_only:
                raise _error("editable entries are not valid constraints", source, line)
            assert option_value is not None
            result.requirements.append(_editable_specifier(option_value, source, line))
            result.requirement_origins.append((source, line))
            continue
        if option == "hash" or any(token == "--hash" or token.startswith("--hash=") for token in tokens[1:]):
            raise _error(
                "pip --hash entries are not supported by Depfix requirements activation",
                source,
                line,
                remediation="use a PEP 508 direct URL with a #sha256= fragment or a prepared Depfix manifest",
            )
        if option is not None or any(token.startswith("-") for token in tokens[1:]):
            unsupported = (
                tokens[0] if option is not None else next(token for token in tokens[1:] if token.startswith("-"))
            )
            raise _error(f"unsupported requirements directive {unsupported!r}", source, line)
        value = _requirement_specifier(cleaned, source, line)
        if constraints_only:
            _validate_constraint(value, source, line)
            result.constraints.append(value)
        else:
            result.requirements.append(value)
            result.requirement_origins.append((source, line))
    return result


def _logical_lines(source: Path) -> tuple[tuple[int, str], ...]:
    pending = ""
    start = 0
    result: list[tuple[int, str]] = []
    for number, physical in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
        value = physical.strip()
        if not pending and (not value or value.startswith("#")):
            continue
        if not pending:
            start = number
        pending += value[:-1].rstrip() + " " if value.endswith("\\") else value
        if not value.endswith("\\"):
            result.append((start, pending.strip()))
            pending = ""
    if pending:
        raise _error("unterminated requirements line continuation", source, start)
    return tuple(result)


def _file_option(tokens: list[str], source: Path, line: int) -> tuple[str | None, str | None]:
    names = {
        "-r": "requirement",
        "--requirement": "requirement",
        "-c": "constraint",
        "--constraint": "constraint",
        "-e": "editable",
        "--editable": "editable",
        "--index-url": "index-url",
        "--extra-index-url": "extra-index-url",
        "--hash": "hash",
    }
    head = tokens[0]
    if head in names:
        if len(tokens) != 2:
            raise _error(f"requirements directive {head!r} requires exactly one value", source, line)
        return names[head], tokens[1]
    for prefix, name in (
        ("--requirement=", "requirement"),
        ("--constraint=", "constraint"),
        ("--editable=", "editable"),
        ("--index-url=", "index-url"),
        ("--extra-index-url=", "extra-index-url"),
        ("--hash=", "hash"),
    ):
        if head.startswith(prefix):
            if len(tokens) != 1 or not head[len(prefix) :]:
                raise _error(f"requirements directive {prefix[:-1]!r} requires exactly one value", source, line)
            return name, head[len(prefix) :]
    if head.startswith("-r") and len(head) > 2 and len(tokens) == 1:
        return "requirement", head[2:]
    if head.startswith("-c") and len(head) > 2 and len(tokens) == 1:
        return "constraint", head[2:]
    return (head, None) if head.startswith("-") else (None, None)


def _requirement_specifier(value: str, source: Path, line: int) -> str:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = source.parent / candidate
    if value.startswith((".", "/", "~")) or candidate.exists():
        value = f"file:{candidate.resolve()}"
    try:
        return parse_source(value, base_dir=source.parent).original
    except Exception as exc:
        raise _error("invalid requirement", source, line, remediation=str(exc)) from exc


def _editable_specifier(value: str, source: Path, line: int) -> str:
    specifier = _requirement_specifier(value, source, line)
    parsed = parse_source(specifier, base_dir=source.parent)
    if parsed.kind != "file":
        raise _error(
            "Depfix accepts editable syntax only for a local file or project path",
            source,
            line,
        )
    return parsed.normalized


def _validate_constraint(value: str, source: Path, line: int) -> None:
    try:
        requirement = Requirement(value)
    except InvalidRequirement as exc:
        raise _error("invalid package constraint", source, line, remediation=str(exc)) from exc
    if requirement.url or requirement.extras or requirement.marker:
        raise _error("constraints may contain only a distribution name and version specifier", source, line)


def _error(message: str, source: Path, line: int, *, remediation: str | None = None) -> RequirementsFileError:
    return RequirementsFileError(message, source=f"{source}:{line}", remediation=remediation)
