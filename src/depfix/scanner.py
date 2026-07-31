"""Safe AST scanner for statically discoverable Depfix requests."""

from __future__ import annotations

import ast
import fnmatch
import keyword
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

import pathspec
from pathspec.pattern import Pattern

from .sources import parse_source

DEFAULT_EXCLUDED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".depfix",
    ".venv",
    "venv",
    "env",
    ".tox",
    ".nox",
    "build",
    "dist",
    "site-packages",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".pyright",
    ".ruff_cache",
}


@dataclass(frozen=True, slots=True)
class ScanSite:
    original_specifier: str
    normalized_specifier: str
    api: str
    module: str | None
    source_file: str
    line: int
    column: int
    assignment: str | None
    base_dir: Path
    origin: str = "static"
    suggested_alias: str = ""


@dataclass(frozen=True, slots=True)
class DynamicRequest:
    source_file: str
    line: int
    column: int
    expression: str
    reason: str


@dataclass(frozen=True, slots=True)
class ScanResult:
    root: Path
    requests: tuple[ScanSite, ...]
    dynamic_requests: tuple[DynamicRequest, ...]
    files_scanned: int


def scan_project(
    root: str | Path = ".",
    *,
    include: Iterable[str] = (),
    exclude: Iterable[str] = (),
) -> ScanResult:
    project = Path(root).expanduser().resolve()
    matcher = _gitignore(project)
    requests: list[ScanSite] = []
    dynamic: list[DynamicRequest] = []
    files = 0
    for source in _source_files(project, matcher, tuple(exclude)):
        files += 1
        text = source.read_text(encoding="utf-8")
        relative = source.relative_to(project).as_posix()
        try:
            tree = ast.parse(text, filename=str(source), type_comments=True)
        except SyntaxError as exc:
            dynamic.append(DynamicRequest(relative, exc.lineno or 0, exc.offset or 0, "<syntax error>", str(exc)))
            continue
        visitor = _Visitor(project, source, relative, text)
        visitor.visit(tree)
        requests.extend(visitor.requests)
        dynamic.extend(visitor.dynamic)
    for value in include:
        parsed = parse_source(value, base_dir=project)
        alias = _suggest_alias(None, parsed.distribution or "package")
        requests.append(
            ScanSite(
                value, parsed.normalized, "import_module", None, "<explicit>", 0, 0, None, project, "included", alias
            )
        )
    return ScanResult(
        project,
        tuple(sorted(requests, key=lambda item: (item.source_file, item.line, item.column, item.normalized_specifier))),
        tuple(sorted(dynamic, key=lambda item: (item.source_file, item.line, item.column))),
        files,
    )


class _Visitor(ast.NodeVisitor):
    def __init__(self, root: Path, source: Path, relative: str, text: str) -> None:
        self.root = root
        self.source = source
        self.relative = relative
        self.text = text
        self.functions: dict[str, str] = {}
        self.modules: set[str] = set()
        self.constants: dict[str, str] = {}
        self.requests: list[ScanSite] = []
        self.dynamic: list[DynamicRequest] = []
        self._assignment: str | None = None

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module == "depfix":
            for item in node.names:
                if item.name in {"import_module", "load_package"}:
                    self.functions[item.asname or item.name] = item.name
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for item in node.names:
            if item.name == "depfix":
                self.modules.add(item.asname or "depfix")
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            value = self._constant(node.value)
            if value is not None:
                self.constants[node.targets[0].id] = value
            else:
                self.constants.pop(node.targets[0].id, None)
            previous = self._assignment
            self._assignment = node.targets[0].id
            self.visit(node.value)
            self._assignment = previous
            return
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if isinstance(node.target, ast.Name) and node.value is not None:
            value = self._constant(node.value)
            if value is not None:
                self.constants[node.target.id] = value
            else:
                self.constants.pop(node.target.id, None)
            previous = self._assignment
            self._assignment = node.target.id
            self.visit(node.value)
            self._assignment = previous
            return
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        api = self._api(node.func)
        if api is None:
            self.generic_visit(node)
            return
        expression = ast.get_source_segment(self.text, node) or "<call>"
        if not node.args:
            self.dynamic.append(
                DynamicRequest(self.relative, node.lineno, node.col_offset, expression, "missing specifier argument")
            )
            return
        value = self._constant(node.args[0])
        module = None
        for keyword_arg in node.keywords:
            if keyword_arg.arg == "module":
                module = self._constant(keyword_arg.value)
                if module is None:
                    self.dynamic.append(
                        DynamicRequest(
                            self.relative, node.lineno, node.col_offset, expression, "dynamic module= override"
                        )
                    )
                    return
        if value is None:
            self.dynamic.append(
                DynamicRequest(
                    self.relative,
                    node.lineno,
                    node.col_offset,
                    expression,
                    "specifier is not a safe static string constant",
                )
            )
            return
        parsed = parse_source(value, base_dir=self.source.parent)
        alias = _suggest_alias(self._assignment, parsed.distribution or (module or "package"))
        self.requests.append(
            ScanSite(
                value,
                parsed.normalized,
                api,
                module,
                self.relative,
                node.lineno,
                node.col_offset,
                self._assignment,
                self.source.parent,
                suggested_alias=alias,
            )
        )

    def _api(self, node: ast.expr) -> str | None:
        if isinstance(node, ast.Name):
            return self.functions.get(node.id)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id in self.modules and node.attr in {"import_module", "load_package"}:
                return node.attr
        return None

    def _constant(self, node: ast.expr) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Name):
            return self.constants.get(node.id)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left = self._constant(node.left)
            right = self._constant(node.right)
            return left + right if left is not None and right is not None else None
        if isinstance(node, ast.JoinedStr) and all(
            isinstance(item, ast.Constant) and isinstance(item.value, str) for item in node.values
        ):
            values = [
                item.value for item in node.values if isinstance(item, ast.Constant) and isinstance(item.value, str)
            ]
            return "".join(values)
        return None


def _source_files(root: Path, matcher: pathspec.PathSpec[Pattern] | None, excludes: tuple[str, ...]) -> Iterator[Path]:
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(root)
        if any(part in DEFAULT_EXCLUDED_DIRS for part in relative.parts):
            continue
        relative_text = relative.as_posix()
        if matcher is not None and matcher.match_file(relative_text):
            continue
        if any(fnmatch.fnmatch(relative_text, pattern) for pattern in excludes):
            continue
        if path.is_file() and path.suffix in {".py", ".pyi"}:
            yield path


def _gitignore(root: Path) -> pathspec.PathSpec[Pattern] | None:
    path = root / ".gitignore"
    if not path.is_file():
        return None
    return pathspec.PathSpec.from_lines("gitwildmatch", path.read_text(encoding="utf-8").splitlines())


def _suggest_alias(assignment: str | None, distribution: str) -> str:
    if assignment and assignment.isidentifier() and not keyword.iskeyword(assignment):
        return assignment
    value = re.sub(r"\W+", "_", distribution.replace("-", "_")).strip("_") or "package"
    if value[0].isdigit():
        value = "package_" + value
    return value
