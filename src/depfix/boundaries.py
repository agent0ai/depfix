"""Opt-in diagnostics for objects crossing managed package-version boundaries."""

from __future__ import annotations

import inspect
import sys
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from functools import wraps
from types import ModuleType
from typing import Any, ParamSpec, TypeVar, cast

from .errors import RealmBoundaryError

P = ParamSpec("P")
R = TypeVar("R")


@dataclass(frozen=True, slots=True)
class RealmInfo:
    """Immutable provenance for one Depfix-managed module identity."""

    graph_id: str
    node_id: str
    distribution: str
    version: str
    module: str
    artifact_id: str

    @property
    def package(self) -> str:
        """Return the selected distribution and version."""
        return f"{self.distribution}=={self.version}"

    @property
    def realm_id(self) -> str:
        """Return the exact graph/node identity that owns the class."""
        return f"{self.graph_id}:{self.node_id}"


def realm_of(value: object) -> RealmInfo | None:
    """Return managed provenance for a module, class, function, or instance."""
    module = _owning_module(value)
    if module is None:
        return None
    metadata = vars(module)
    fields = {
        "graph_id": metadata.get("__depfix_graph_id__"),
        "node_id": metadata.get("__depfix_node_id__"),
        "distribution": metadata.get("__depfix_distribution__"),
        "version": metadata.get("__depfix_version__"),
        "module": metadata.get("__depfix_logical_name__"),
        "artifact_id": metadata.get("__depfix_artifact_id__"),
    }
    if not all(isinstance(item, str) for item in fields.values()):
        return None
    return RealmInfo(**cast(dict[str, str], fields))


def assert_same_realm(
    consumer: object | RealmInfo,
    *values: object,
    recursive: bool = True,
) -> None:
    """Raise when a managed value is not owned by the consumer's exact realm."""
    consumer_info = _consumer_info(consumer)
    for index, value in enumerate(values):
        _assert_value(consumer_info, value, f"values[{index}]", recursive=recursive)


def enforce_same_realm(
    consumer: object | RealmInfo,
    *,
    parameters: Iterable[str] | None = None,
    recursive: bool = True,
    check_return: bool = False,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Guard selected function arguments against foreign managed values."""
    consumer_info = _consumer_info(consumer)
    parameter_names = (parameters,) if isinstance(parameters, str) else parameters
    selected_parameters = None if parameter_names is None else tuple(dict.fromkeys(parameter_names))

    def decorate(function: Callable[P, R]) -> Callable[P, R]:
        signature = inspect.signature(function)
        if selected_parameters is not None:
            unknown = tuple(name for name in selected_parameters if name not in signature.parameters)
            if unknown:
                joined = ", ".join(unknown)
                raise ValueError(f"Unknown guarded parameter(s) for {function.__qualname__}: {joined}")

        def check_arguments(args: tuple[object, ...], kwargs: dict[str, object]) -> None:
            if selected_parameters is None:
                for index, value in enumerate(args):
                    _assert_value(consumer_info, value, f"args[{index}]", recursive=recursive)
                for name, value in kwargs.items():
                    _assert_value(consumer_info, value, f"kwargs[{name!r}]", recursive=recursive)
                return
            bound = signature.bind_partial(*args, **kwargs)
            for name in selected_parameters:
                if name in bound.arguments:
                    _assert_value(consumer_info, bound.arguments[name], f"parameter[{name!r}]", recursive=recursive)

        if inspect.iscoroutinefunction(function):

            @wraps(function)
            async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> Any:
                check_arguments(cast(tuple[object, ...], args), cast(dict[str, object], kwargs))
                result = await cast(Callable[P, Any], function)(*args, **kwargs)
                if check_return:
                    _assert_value(consumer_info, result, "return", recursive=recursive)
                return result

            return cast(Callable[P, R], async_wrapper)

        @wraps(function)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            check_arguments(cast(tuple[object, ...], args), cast(dict[str, object], kwargs))
            result = function(*args, **kwargs)
            if check_return:
                _assert_value(consumer_info, result, "return", recursive=recursive)
            return result

        return wrapper

    return decorate


def _consumer_info(consumer: object | RealmInfo) -> RealmInfo:
    if isinstance(consumer, RealmInfo):
        return consumer
    info = realm_of(consumer)
    if info is None:
        raise RealmBoundaryError(
            "The guarded consumer has no Depfix realm provenance",
            consumer=_object_label(consumer),
            remediation="pass a module or value loaded by Depfix as the guarded consumer",
        )
    return info


def _assert_value(consumer: RealmInfo, value: object, path: str, *, recursive: bool) -> None:
    for nested_path, producer in _managed_values(value, path, recursive=recursive, seen=set()):
        if producer.graph_id == consumer.graph_id and producer.node_id == consumer.node_id:
            continue
        raise RealmBoundaryError(
            "A value from another Depfix package realm crossed a guarded boundary",
            consumer=f"{consumer.package} ({consumer.module})",
            producer=f"{producer.package} ({producer.module})",
            consumer_realm=consumer.realm_id,
            producer_realm=producer.realm_id,
            value_path=nested_path,
            remediation=(
                "keep creation and consumption in one package realm, or reconstruct the value from an "
                "application-owned primitive representation"
            ),
        )


def _managed_values(
    value: object,
    path: str,
    *,
    recursive: bool,
    seen: set[int],
) -> Iterator[tuple[str, RealmInfo]]:
    info = realm_of(value)
    if info is not None:
        yield path, info
        return
    if not recursive or type(value) not in {dict, list, tuple, set, frozenset}:
        return
    identity = id(value)
    if identity in seen:
        return
    seen.add(identity)
    if type(value) is dict:
        for index, (key, item) in enumerate(cast(dict[object, object], value).items()):
            yield from _managed_values(key, f"{path}.keys[{index}]", recursive=True, seen=seen)
            yield from _managed_values(item, f"{path}.values[{index}]", recursive=True, seen=seen)
        return
    for index, item in enumerate(cast(Iterable[object], value)):
        yield from _managed_values(item, f"{path}[{index}]", recursive=True, seen=seen)


def _owning_module(value: object) -> ModuleType | None:
    module_name: object
    if isinstance(value, ModuleType):
        return value
    if isinstance(value, type):
        module_name = value.__module__
    elif inspect.isfunction(value) or inspect.ismethod(value) or inspect.isbuiltin(value):
        module_name = getattr(value, "__module__", None)
    else:
        module_name = type(value).__module__
    if not isinstance(module_name, str):
        return None
    module = sys.modules.get(module_name)
    return module if isinstance(module, ModuleType) else None


def _object_label(value: object) -> str:
    if isinstance(value, ModuleType):
        return value.__name__
    if isinstance(value, type):
        return f"{value.__module__}.{value.__qualname__}"
    value_type = type(value)
    return f"{value_type.__module__}.{value_type.__qualname__}"
