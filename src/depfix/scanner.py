"""Safe AST scanner for statically discoverable Depfix requests."""

from __future__ import annotations

import ast
import fnmatch
import hashlib
import json
import keyword
import re
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

import pathspec
from pathspec.pattern import Pattern

from .errors import redact
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
    group_id: str = ""
    mode: str = "explicit"
    enclosing_function: str = ""
    isolation: str = "auto"
    allow_unsafe: bool | None = None
    prefer_newest: bool | None = None
    index_url: str | None = None
    extra_index_url: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class ScanGroup:
    id: str
    mode: str
    specifiers: tuple[str, ...]
    normalized_specifiers: tuple[str, ...]
    source_file: str
    line: int
    column: int
    enclosing_function: str
    ordinary_imports: tuple[str, ...]
    module_aliases: tuple[tuple[str, str], ...]
    base_dir: Path
    options: tuple[tuple[str, str], ...] = ()


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
    groups: tuple[ScanGroup, ...] = ()


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
    groups: list[ScanGroup] = []
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
        groups.extend(visitor.groups)
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
        tuple(sorted(groups, key=lambda item: (item.source_file, item.line, item.column, item.id))),
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
        self.groups: list[ScanGroup] = []
        self._assignment: str | None = None
        self._function_stack: list[str] = []
        self._handled_groups: set[int] = set()

    def visit_Module(self, node: ast.Module) -> None:
        for index, statement in enumerate(node.body):
            call = _expression_call(statement)
            if call is not None and self._api(call.func) == "default":
                following: list[ast.stmt] = []
                for candidate in node.body[index + 1 :]:
                    next_call = _expression_call(candidate)
                    if next_call is not None and self._api(next_call.func) == "default":
                        break
                    following.append(candidate)
                ordinary = _direct_ordinary_imports(following)
                self._record_group(call, "default", ordinary, "")
            self.visit(statement)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module == "depfix":
            for item in node.names:
                if item.name in {"default", "import_module", "load_package", "using"}:
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
        if id(node) in self._handled_groups:
            return
        api = self._api(node.func)
        if api is None:
            self.generic_visit(node)
            return
        if api == "default":
            self._record_group(node, "default", (), self._enclosing_function)
            return
        if api == "using":
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
        isolation = "auto"
        allow_unsafe: bool | None = None
        prefer_newest: bool | None = None
        index_url: str | None = None
        extra_index_url: tuple[str, ...] | None = None
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
            if keyword_arg.arg == "isolation":
                isolation = self._constant(keyword_arg.value) or ""
                if not isolation:
                    self.dynamic.append(
                        DynamicRequest(
                            self.relative, node.lineno, node.col_offset, expression, "dynamic isolation= override"
                        )
                    )
                    return
            if keyword_arg.arg == "allow_unsafe":
                allow_unsafe = self._constant_bool(keyword_arg.value)
                if allow_unsafe is None:
                    self.dynamic.append(
                        DynamicRequest(
                            self.relative,
                            node.lineno,
                            node.col_offset,
                            expression,
                            "dynamic allow_unsafe= override",
                        )
                    )
                    return
            if keyword_arg.arg == "prefer_newest":
                prefer_newest = self._constant_bool(keyword_arg.value)
                if prefer_newest is None:
                    self.dynamic.append(
                        DynamicRequest(
                            self.relative,
                            node.lineno,
                            node.col_offset,
                            expression,
                            "dynamic prefer_newest= override",
                        )
                    )
                    return
            if keyword_arg.arg == "index_url":
                index_url = self._constant(keyword_arg.value)
                if index_url is None:
                    self.dynamic.append(
                        DynamicRequest(
                            self.relative, node.lineno, node.col_offset, expression, "dynamic index_url= override"
                        )
                    )
                    return
            if keyword_arg.arg == "extra_index_url":
                extra_index_url = self._constant_strings(keyword_arg.value)
                if extra_index_url is None:
                    self.dynamic.append(
                        DynamicRequest(
                            self.relative,
                            node.lineno,
                            node.col_offset,
                            expression,
                            "dynamic extra_index_url= override",
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
                isolation=isolation,
                allow_unsafe=allow_unsafe,
                prefer_newest=prefer_newest,
                index_url=index_url,
                extra_index_url=extra_index_url,
            )
        )

    def _api(self, node: ast.expr) -> str | None:
        if isinstance(node, ast.Name):
            return self.functions.get(node.id)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id in self.modules and node.attr in {"default", "import_module", "load_package", "using"}:
                return node.attr
        return None

    def visit_With(self, node: ast.With) -> None:
        ordinary = _ordinary_imports(node.body, self._is_using_call)
        for item in node.items:
            if isinstance(item.context_expr, ast.Call) and self._api(item.context_expr.func) == "using":
                self._record_group(item.context_expr, "using-context", ordinary, self._enclosing_function)
        self.generic_visit(node)

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        self.visit_With(node)  # type: ignore[arg-type]

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        name = ".".join((*self._function_stack, node.name))
        ordinary = _ordinary_imports(node.body, self._is_using_call)
        for decorator in node.decorator_list:
            if isinstance(decorator, ast.Call) and self._api(decorator.func) == "using":
                self._record_group(decorator, "using-decorator", ordinary, name)
        for decorator in node.decorator_list:
            self.visit(decorator)
        self._function_stack.append(node.name)
        for index, statement in enumerate(node.body):
            call = _expression_call(statement)
            if call is not None and self._api(call.func) == "default":
                following: list[ast.stmt] = []
                for candidate in node.body[index + 1 :]:
                    next_call = _expression_call(candidate)
                    if next_call is not None and self._api(next_call.func) == "default":
                        break
                    following.append(candidate)
                self._record_group(call, "default", _direct_ordinary_imports(following), name)
            self.visit(statement)
        self._function_stack.pop()

    @property
    def _enclosing_function(self) -> str:
        return ".".join(self._function_stack)

    def _is_using_call(self, node: ast.expr) -> bool:
        return isinstance(node, ast.Call) and self._api(node.func) == "using"

    def _record_group(
        self,
        node: ast.Call,
        mode: str,
        ordinary: tuple[tuple[str, str], ...],
        enclosing_function: str,
    ) -> None:
        if id(node) in self._handled_groups:
            return
        self._handled_groups.add(id(node))
        expression = ast.get_source_segment(self.text, node) or "<call>"
        if not node.args:
            self.dynamic.append(
                DynamicRequest(self.relative, node.lineno, node.col_offset, expression, "missing specifier arguments")
            )
            return
        values: list[str] = []
        normalized: list[str] = []
        seen: set[str] = set()
        for argument in node.args:
            value = self._constant(argument)
            if value is None:
                self.dynamic.append(
                    DynamicRequest(
                        self.relative,
                        node.lineno,
                        node.col_offset,
                        expression,
                        "specifier group contains a dynamic expression",
                    )
                )
                return
            parsed = parse_source(value, base_dir=self.source.parent)
            if parsed.normalized in seen:
                continue
            seen.add(parsed.normalized)
            values.append(value)
            normalized.append(parsed.normalized)
        options: list[tuple[str, str]] = []
        for option in node.keywords:
            if option.arg is None:
                self.dynamic.append(
                    DynamicRequest(self.relative, node.lineno, node.col_offset, expression, "dynamic keyword options")
                )
                return
            rendered = _literal_option(option.value)
            if rendered is None:
                self.dynamic.append(
                    DynamicRequest(
                        self.relative,
                        node.lineno,
                        node.col_offset,
                        expression,
                        f"dynamic {option.arg}= option",
                    )
                )
                return
            options.append((option.arg, redact(rendered)))
        options.sort()
        payload = json.dumps(
            {
                "file": self.relative,
                "line": node.lineno,
                "column": node.col_offset,
                "mode": mode,
                "specifiers": sorted(normalized),
                "options": options,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        group_id = "group_" + hashlib.sha256(payload.encode()).hexdigest()[:20]
        imports = tuple(dict.fromkeys(name for name, _alias in ordinary if name != "depfix"))
        aliases = tuple((name, alias) for name, alias in ordinary if name != "depfix")
        self.groups.append(
            ScanGroup(
                group_id,
                mode,
                tuple(redact(value) for value in values),
                tuple(normalized),
                self.relative,
                node.lineno,
                node.col_offset,
                enclosing_function,
                imports,
                aliases,
                self.source.parent,
                tuple(options),
            )
        )
        for value, normalized_value in zip(values, normalized, strict=True):
            parsed = parse_source(value, base_dir=self.source.parent)
            alias = _suggest_alias(None, parsed.distribution or "package")
            rendered_isolation = dict(options).get("isolation", '"auto"')
            try:
                isolation = json.loads(rendered_isolation)
            except json.JSONDecodeError:
                isolation = "auto"
            if not isinstance(isolation, str):
                isolation = "auto"
            rendered_allow_unsafe = dict(options).get("allow_unsafe", "null")
            try:
                allow_unsafe = json.loads(rendered_allow_unsafe)
            except json.JSONDecodeError:
                allow_unsafe = None
            if not isinstance(allow_unsafe, bool):
                allow_unsafe = None
            rendered_prefer_newest = dict(options).get("prefer_newest", "null")
            try:
                prefer_newest = json.loads(rendered_prefer_newest)
            except json.JSONDecodeError:
                prefer_newest = None
            if not isinstance(prefer_newest, bool):
                prefer_newest = None
            index_url = self._literal_string_option(node, "index_url")
            extra_index_url = self._literal_string_sequence_option(node, "extra_index_url")
            self.requests.append(
                ScanSite(
                    value,
                    normalized_value,
                    mode,
                    None,
                    self.relative,
                    node.lineno,
                    node.col_offset,
                    None,
                    self.source.parent,
                    suggested_alias=alias,
                    group_id=group_id,
                    mode=mode,
                    enclosing_function=enclosing_function,
                    isolation=isolation,
                    allow_unsafe=allow_unsafe,
                    prefer_newest=prefer_newest,
                    index_url=index_url,
                    extra_index_url=extra_index_url,
                )
            )

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

    @staticmethod
    def _constant_bool(node: ast.expr) -> bool | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, bool):
            return node.value
        return None

    @staticmethod
    def _constant_strings(node: ast.expr) -> tuple[str, ...] | None:
        try:
            value = ast.literal_eval(node)
        except (ValueError, TypeError):
            return None
        if isinstance(value, str):
            return (value,)
        if isinstance(value, (tuple, list)) and all(isinstance(item, str) for item in value):
            return tuple(value)
        return None

    def _literal_string_option(self, call: ast.Call, name: str) -> str | None:
        option = next((item for item in call.keywords if item.arg == name), None)
        return self._constant(option.value) if option is not None else None

    def _literal_string_sequence_option(self, call: ast.Call, name: str) -> tuple[str, ...] | None:
        option = next((item for item in call.keywords if item.arg == name), None)
        return self._constant_strings(option.value) if option is not None else None


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


def _expression_call(statement: ast.stmt) -> ast.Call | None:
    return statement.value if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call) else None


def _literal_option(node: ast.expr) -> str | None:
    try:
        value = ast.literal_eval(node)
    except (ValueError, TypeError):
        return None
    if value is None or isinstance(value, (str, bool, int, float)):
        return json.dumps(value, sort_keys=True)
    if isinstance(value, (tuple, list)) and all(isinstance(item, str) for item in value):
        return json.dumps(value, sort_keys=True)
    return None


def _ordinary_imports(
    statements: Iterable[ast.stmt],
    is_using_call: Callable[[ast.expr], bool],
) -> tuple[tuple[str, str], ...]:
    result: list[tuple[str, str]] = []

    class Collector(ast.NodeVisitor):
        def visit_Import(self, node: ast.Import) -> None:
            for item in node.names:
                result.append((item.name, item.asname or item.name.split(".", 1)[0]))

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            if node.level == 0 and node.module:
                result.append((node.module, node.module.split(".", 1)[0]))

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            return None

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            return None

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            return None

        def visit_With(self, node: ast.With) -> None:
            if any(is_using_call(item.context_expr) for item in node.items):
                return None
            self.generic_visit(node)

        def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
            self.visit_With(node)  # type: ignore[arg-type]

    collector = Collector()
    for statement in statements:
        collector.visit(statement)
    return tuple(result)


def _direct_ordinary_imports(statements: Iterable[ast.stmt]) -> tuple[tuple[str, str], ...]:
    result: list[tuple[str, str]] = []
    for statement in statements:
        if isinstance(statement, ast.Import):
            result.extend((item.name, item.asname or item.name.split(".", 1)[0]) for item in statement.names)
        elif isinstance(statement, ast.ImportFrom) and statement.level == 0 and statement.module:
            result.append((statement.module, statement.module.split(".", 1)[0]))
    return tuple(result)
