"""Persistent and context-local dispatch for ordinary Python imports."""

from __future__ import annotations

import builtins
import importlib
import importlib.abc
import importlib.machinery
import importlib.util
import sys
from collections.abc import Mapping, Sequence
from contextvars import ContextVar, Token
from dataclasses import dataclass
from threading import RLock
from types import MappingProxyType, ModuleType
from typing import Any, cast

from .errors import (
    DefaultImportConflictError,
    ImportDispatcherConflictError,
    ScopeModuleNotProvidedError,
)
from .runtime import BoundImporter, DepfixRuntime


@dataclass(frozen=True, slots=True)
class ModuleBinding:
    runtime: DepfixRuntime
    node_id: str
    distribution: str
    version: str
    artifact_id: str
    realm_id: str
    specifiers: tuple[str, ...]
    mode: str

    @property
    def fingerprint(self) -> tuple[str, str, str, str]:
        return self.distribution, self.version, self.artifact_id, self.realm_id


@dataclass(frozen=True, slots=True)
class ImportSelection:
    bindings: Mapping[str, ModuleBinding]
    specifiers: tuple[str, ...]
    normalized_specifiers: tuple[str, ...]
    mode: str
    source_file: str = ""
    source_line: int = 0

    @classmethod
    def create(
        cls,
        bindings: Mapping[str, ModuleBinding],
        specifiers: Sequence[str],
        normalized_specifiers: Sequence[str],
        mode: str,
        *,
        source_file: str = "",
        source_line: int = 0,
    ) -> ImportSelection:
        return cls(
            MappingProxyType(dict(bindings)),
            tuple(specifiers),
            tuple(normalized_specifiers),
            mode,
            source_file,
            source_line,
        )


_scope_stack: ContextVar[tuple[ImportSelection, ...]] = ContextVar("depfix_using_scopes", default=())
_ordinary_misses: ContextVar[set[str] | None] = ContextVar("depfix_ordinary_misses", default=None)
_defaults: dict[str, ModuleBinding] = {}
_managed_roots: set[str] = set()
_lock = RLock()
_installed = False
_fallback_enabled = False
_fallback_bindings: dict[str, ModuleBinding] = {}
_previous_import: Any = None
_previous_import_module: Any = None
_previous_find_spec: Any = None


class _OrdinaryMissProbe(importlib.abc.MetaPathFinder):
    """Record lookups that exhausted the ordinary finder chain."""

    def find_spec(
        self,
        fullname: str,
        path: Sequence[str] | None,
        target: ModuleType | None = None,
    ) -> None:
        misses = _ordinary_misses.get()
        if misses is not None:
            misses.add(fullname)
        return None


_ordinary_miss_probe = _OrdinaryMissProbe()


def ensure_dispatcher() -> None:
    """Install the process-wide lightweight dispatcher exactly once."""
    global _installed, _previous_find_spec, _previous_import, _previous_import_module
    with _lock:
        if _installed:
            if builtins.__import__ is not _dispatch_import:
                raise ImportDispatcherConflictError(
                    "Another library replaced builtins.__import__ after Depfix installed its dispatcher",
                    remediation="restore Depfix's dispatcher or initialize the other import hook before calling depfix",
                )
            return
        _previous_import = builtins.__import__
        _previous_import_module = importlib.import_module
        _previous_find_spec = importlib.util.find_spec
        builtins.__import__ = cast(Any, _dispatch_import)
        importlib.import_module = cast(Any, _dispatch_import_module)
        importlib.util.find_spec = cast(Any, _dispatch_find_spec)
        _installed = True


def register_default(selection: ImportSelection) -> None:
    with _lock:
        conflicts: list[str] = []
        for root, binding in selection.bindings.items():
            current = _defaults.get(root)
            if current is not None and current.fingerprint != binding.fingerprint:
                conflicts.append(
                    f"{root}: {current.distribution}=={current.version} vs {binding.distribution}=={binding.version}"
                )
        if conflicts:
            raise DefaultImportConflictError(
                "A default import name already has an incompatible Depfix selection",
                request=", ".join(selection.specifiers),
                candidates=tuple(conflicts),
                remediation="keep one default selection or use depfix.using(...) for the temporary version",
            )
        ensure_dispatcher()
        for root, binding in selection.bindings.items():
            _defaults.setdefault(root, binding)
            _managed_roots.add(root)


def enter_scope(selection: ImportSelection) -> Token[tuple[ImportSelection, ...]]:
    ensure_dispatcher()
    with _lock:
        _managed_roots.update(selection.bindings)
    return _scope_stack.set((*_scope_stack.get(), selection))


def exit_scope(token: Token[tuple[ImportSelection, ...]]) -> None:
    _scope_stack.reset(token)


def active_scopes() -> tuple[ImportSelection, ...]:
    return _scope_stack.get()


def dispatcher_installed() -> bool:
    return _installed and builtins.__import__ is _dispatch_import


def patch_import() -> None:
    """Enable installed-store fallback for otherwise unresolved imports."""
    global _fallback_enabled
    ensure_dispatcher()
    with _lock:
        _fallback_enabled = True
        _ensure_ordinary_miss_probe_last()


def unpatch_import() -> None:
    """Disable installed-store fallback without disturbing other import hooks."""
    global _fallback_enabled, _installed, _previous_find_spec, _previous_import, _previous_import_module
    with _lock:
        _fallback_enabled = False
        _fallback_bindings.clear()
        _remove_ordinary_miss_probe()
        if _defaults or _managed_roots or _scope_stack.get() or not _installed:
            return
        if (
            builtins.__import__ is not _dispatch_import
            or importlib.import_module is not _dispatch_import_module
            or importlib.util.find_spec is not _dispatch_find_spec
        ):
            return
        if builtins.__import__ is _dispatch_import:
            builtins.__import__ = cast(Any, _previous_import)
        if importlib.import_module is _dispatch_import_module:
            importlib.import_module = cast(Any, _previous_import_module)
        if importlib.util.find_spec is _dispatch_find_spec:
            importlib.util.find_spec = cast(Any, _previous_find_spec)
        _installed = False
        _previous_import = None
        _previous_import_module = None
        _previous_find_spec = None


def reset_dispatcher_state() -> None:
    """Restore interpreter globals for deterministic tests."""
    global _fallback_enabled, _installed, _previous_find_spec, _previous_import, _previous_import_module
    with _lock:
        _defaults.clear()
        _managed_roots.clear()
        _fallback_bindings.clear()
        _fallback_enabled = False
        _scope_stack.set(())
        _ordinary_misses.set(None)
        _remove_ordinary_miss_probe()
        if _installed and builtins.__import__ is _dispatch_import:
            builtins.__import__ = cast(Any, _previous_import)
        if _installed and importlib.import_module is _dispatch_import_module:
            importlib.import_module = cast(Any, _previous_import_module)
        if _installed and importlib.util.find_spec is _dispatch_find_spec:
            importlib.util.find_spec = cast(Any, _previous_find_spec)
        _installed = False
        _previous_import = None
        _previous_import_module = None
        _previous_find_spec = None


def _binding(root: str) -> ModuleBinding | None:
    for selection in reversed(_scope_stack.get()):
        selected = selection.bindings.get(root)
        if selected is not None:
            return selected
    with _lock:
        return _defaults.get(root)


def _fallback_binding(root: str) -> ModuleBinding | None:
    with _lock:
        if not _fallback_enabled:
            return None
        cached = _fallback_bindings.get(root)
        if cached is not None:
            return cached
        from .manager import prepare_store_import

        selected = prepare_store_import(root)
        if selected is not None:
            _fallback_bindings[root] = selected
        return selected


def _ordinary_root_missing(error: ModuleNotFoundError, name: str, misses: set[str]) -> bool:
    """Return whether ordinary resolution failed before executing the requested root."""
    root = name.split(".", 1)[0]
    return error.name == root and root in misses


def _ensure_ordinary_miss_probe_last() -> None:
    """Keep the non-loading probe after every ordinary meta-path finder."""
    with _lock:
        sys.meta_path[:] = [finder for finder in sys.meta_path if finder is not _ordinary_miss_probe]
        sys.meta_path.append(_ordinary_miss_probe)


def _remove_ordinary_miss_probe() -> None:
    with _lock:
        sys.meta_path[:] = [finder for finder in sys.meta_path if finder is not _ordinary_miss_probe]


def _store_fallback_enabled() -> bool:
    with _lock:
        return _fallback_enabled


def _realm_importer(globals: dict[str, Any] | None) -> BoundImporter | None:
    if not globals:
        return None
    graph_id = globals.get("__depfix_graph_id__")
    node_id = globals.get("__depfix_node_id__")
    if not isinstance(graph_id, str) or not isinstance(node_id, str):
        return None
    allow_unsafe = globals.get("__depfix_allow_unsafe__", False)
    from .manager import runtime_for_graph

    runtime = runtime_for_graph(graph_id, node_id, allow_unsafe is True)
    logical_package = globals.get("__depfix_logical_package__", "")
    return BoundImporter(runtime, node_id, logical_package if isinstance(logical_package, str) else "")


def _dispatch_import(
    name: str,
    globals: dict[str, Any] | None = None,
    locals: dict[str, Any] | None = None,
    fromlist: Sequence[str] | None = (),
    level: int = 0,
) -> ModuleType:
    if not level and name.startswith("_depfix."):
        return cast(ModuleType, _previous_import(name, globals, locals, fromlist, level))
    realm_importer = _realm_importer(globals)
    if realm_importer is not None:
        root = name.split(".", 1)[0]
        if not level and _is_standard_library(root) and root not in {"importlib", "pkgutil"}:
            return cast(ModuleType, _previous_import(name, globals, locals, fromlist, level))
        return realm_importer(name, globals, locals, list(fromlist or ()), level)
    if level or not name:
        return cast(ModuleType, _previous_import(name, globals, locals, fromlist, level))
    root = name.split(".", 1)[0]
    if _is_standard_library(root):
        return cast(ModuleType, _previous_import(name, globals, locals, fromlist, level))
    selected = _binding(root)
    if selected is None:
        caller_package = globals.get("__package__") if globals else None
        if isinstance(caller_package, str) and (caller_package == "depfix" or caller_package.startswith("depfix.")):
            return cast(ModuleType, _previous_import(name, globals, locals, fromlist, level))
        scopes = _scope_stack.get()
        active = scopes[-1] if scopes else None
        if (active is not None and not _is_application_module(root)) or root in _managed_roots:
            _raise_scope_not_provided(name, active)
        misses: set[str] = set()
        token: Token[set[str] | None] | None = None
        if _store_fallback_enabled():
            _ensure_ordinary_miss_probe_last()
            token = _ordinary_misses.set(misses)
        try:
            return cast(ModuleType, _previous_import(name, globals, locals, fromlist, level))
        except ModuleNotFoundError as exc:
            if not _ordinary_root_missing(exc, name, misses):
                raise
            selected = _fallback_binding(root)
            if selected is None:
                raise
        finally:
            if token is not None:
                _ordinary_misses.reset(token)
    importer = BoundImporter(selected.runtime, selected.node_id, "")
    return importer(name, globals, locals, list(fromlist or ()), level)


def _dispatch_import_module(name: str, package: str | None = None) -> ModuleType:
    if name.startswith("."):
        return cast(ModuleType, _previous_import_module(name, package))
    root = name.split(".", 1)[0]
    selected = _binding(root)
    if selected is None:
        scopes = _scope_stack.get()
        active = scopes[-1] if scopes else None
        if (active is not None and not _is_application_module(root)) or root in _managed_roots:
            _raise_scope_not_provided(name, active)
        misses: set[str] = set()
        token: Token[set[str] | None] | None = None
        if _store_fallback_enabled():
            _ensure_ordinary_miss_probe_last()
            token = _ordinary_misses.set(misses)
        try:
            return cast(ModuleType, _previous_import_module(name, package))
        except ModuleNotFoundError as exc:
            if not _ordinary_root_missing(exc, name, misses):
                raise
            selected = _fallback_binding(root)
            if selected is None:
                raise
        finally:
            if token is not None:
                _ordinary_misses.reset(token)
    return selected.runtime.import_for_node(selected.node_id, name)


def _dispatch_find_spec(name: str, package: str | None = None) -> importlib.machinery.ModuleSpec | None:
    if name.startswith("."):
        return cast(importlib.machinery.ModuleSpec | None, _previous_find_spec(name, package))
    root = name.split(".", 1)[0]
    selected = _binding(root)
    if selected is None:
        ordinary = cast(importlib.machinery.ModuleSpec | None, _previous_find_spec(name, package))
        if ordinary is not None:
            return ordinary
        if "." in name and _previous_find_spec(root) is not None:
            return None
        selected = _fallback_binding(root)
        if selected is None:
            return None
    return selected.runtime.import_for_node(selected.node_id, name).__spec__


def _is_standard_library(root: str) -> bool:
    return root in sys.builtin_module_names or root in getattr(sys, "stdlib_module_names", set())


def _is_application_module(root: str) -> bool:
    try:
        spec = _previous_find_spec(root)
    except (ImportError, AttributeError, ValueError):
        return False
    locations: list[str] = []
    if spec is not None and isinstance(spec.origin, str):
        locations.append(spec.origin)
    if spec is not None and spec.submodule_search_locations:
        locations.extend(str(item) for item in spec.submodule_search_locations)
    if not locations:
        return False
    return all("site-packages" not in item and "dist-packages" not in item for item in locations)


def _raise_scope_not_provided(name: str, active: ImportSelection | None) -> None:
    mode = active.mode if active else "inactive"
    declared = ", ".join(active.specifiers) if active else "no active selection"
    bindings = tuple(active.bindings.values()) if active else ()
    manifest = next((binding.runtime.manifest for binding in bindings if binding.runtime.manifest), None)
    frozen = next(
        (
            bool(binding.runtime.graph.policy.get("frozen"))
            for binding in bindings
            if "frozen" in binding.runtime.graph.policy
        ),
        None,
    )
    raise ScopeModuleNotProvidedError(
        f'The active Depfix {mode} selection contains {declared}, but none of its packages provides "{name}"',
        module=name,
        request=", ".join(active.specifiers) if active else None,
        source=f"{active.source_file}:{active.source_line}" if active and active.source_file else None,
        candidates=tuple(sorted(f"{binding.distribution}=={binding.version}" for binding in bindings)),
        import_modules=tuple(sorted(active.bindings)) if active else (),
        manifest=manifest,
        frozen=frozen,
        remediation="declare this package in the active scope/default map or import it outside that scope",
    )
