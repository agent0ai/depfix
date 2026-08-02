"""Public persistent and temporary standard-import selection APIs."""

from __future__ import annotations

import functools
import inspect
import os
import sys
from collections.abc import Callable
from contextvars import Token
from pathlib import Path
from types import TracebackType
from typing import Any, TypeVar, cast

from .dispatcher import ImportSelection, ensure_dispatcher, enter_scope, exit_scope, register_default
from .errors import InvalidUsingScopeError
from .manager import prepare_import_selection
from .settings import resolve_settings

_F = TypeVar("_F", bound=Callable[..., Any])


def default(
    *specifiers: str,
    refresh: bool = False,
    manifest: str | os.PathLike[str] | None = None,
    frozen: bool | None = None,
    offline: bool | None = None,
    isolation: str | None = None,
    allow_unsafe: bool | None = None,
) -> None:
    """Persist package selections for subsequent ordinary imports."""
    ensure_dispatcher()
    source_file, source_line, base_dir = _caller_location(1)
    settings = resolve_settings(
        manifest=manifest,
        frozen=frozen,
        offline=offline,
        allow_unsafe=allow_unsafe,
    )
    selection = prepare_import_selection(
        _validate_specifiers(specifiers, "default"),
        mode="default",
        refresh=refresh,
        isolation=isolation,
        settings=settings,
        base_dir=base_dir,
        source_file=source_file,
        source_line=source_line,
    )
    register_default(selection)


def using(
    *specifiers: str,
    refresh: bool = False,
    manifest: str | os.PathLike[str] | None = None,
    frozen: bool | None = None,
    offline: bool | None = None,
    isolation: str | None = None,
    allow_unsafe: bool | None = None,
) -> _Using:
    """Create a context manager/decorator for temporary ordinary imports."""
    ensure_dispatcher()
    selected = _validate_specifiers(specifiers, "using")
    source_file, source_line, base_dir = _caller_location(1)
    return _Using(
        selected,
        refresh=refresh,
        manifest=manifest,
        frozen=frozen,
        offline=offline,
        isolation=isolation,
        allow_unsafe=allow_unsafe,
        source_file=source_file,
        source_line=source_line,
        base_dir=base_dir,
    )


class _Using:
    def __init__(
        self,
        specifiers: tuple[str, ...],
        *,
        refresh: bool,
        manifest: str | os.PathLike[str] | None,
        frozen: bool | None,
        offline: bool | None,
        isolation: str | None,
        allow_unsafe: bool | None,
        source_file: str,
        source_line: int,
        base_dir: Path,
        mode: str = "using-context",
    ) -> None:
        self.specifiers = specifiers
        self.refresh = refresh
        self.manifest = manifest
        self.frozen = frozen
        self.offline = offline
        self.isolation = isolation
        self.allow_unsafe = allow_unsafe
        self.source_file = source_file
        self.source_line = source_line
        self.base_dir = base_dir
        self.mode = mode
        self._token: Token[tuple[ImportSelection, ...]] | None = None

    def __enter__(self) -> _Using:
        if self._token is not None:
            raise InvalidUsingScopeError("The same depfix.using() context manager is already active")
        settings = resolve_settings(
            manifest=self.manifest,
            frozen=self.frozen,
            offline=self.offline,
            allow_unsafe=self.allow_unsafe,
        )
        selection = prepare_import_selection(
            self.specifiers,
            mode=self.mode,
            refresh=self.refresh,
            isolation=self.isolation,
            settings=settings,
            base_dir=self.base_dir,
            source_file=self.source_file,
            source_line=self.source_line,
        )
        self._token = enter_scope(selection)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc, traceback
        token = self._token
        if token is None:
            raise InvalidUsingScopeError("The depfix.using() context manager is not active")
        self._token = None
        exit_scope(token)

    def __call__(self, function: _F) -> _F:
        if isinstance(function, type):
            raise InvalidUsingScopeError(
                "depfix.using() does not support class decorators",
                remediation="place the scope inside methods or decorate individual functions",
            )
        if inspect.isasyncgenfunction(function):

            @functools.wraps(function)
            async def async_generator_wrapper(*args: Any, **kwargs: Any):  # type: ignore[no-untyped-def]
                with self._invocation():
                    async for item in function(*args, **kwargs):
                        yield item

            return cast(_F, async_generator_wrapper)
        if inspect.iscoroutinefunction(function):

            @functools.wraps(function)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                with self._invocation():
                    return await function(*args, **kwargs)

            return cast(_F, async_wrapper)

        if inspect.isgeneratorfunction(function):

            @functools.wraps(function)
            def generator_wrapper(*args: Any, **kwargs: Any):  # type: ignore[no-untyped-def]
                with self._invocation():
                    yield from function(*args, **kwargs)

            return cast(_F, generator_wrapper)

        @functools.wraps(function)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            with self._invocation():
                return function(*args, **kwargs)

        return cast(_F, wrapper)

    def _invocation(self) -> _Using:
        return _Using(
            self.specifiers,
            refresh=self.refresh,
            manifest=self.manifest,
            frozen=self.frozen,
            offline=self.offline,
            isolation=self.isolation,
            allow_unsafe=self.allow_unsafe,
            source_file=self.source_file,
            source_line=self.source_line,
            base_dir=self.base_dir,
            mode="using-decorator",
        )


def _validate_specifiers(specifiers: tuple[str, ...], api: str) -> tuple[str, ...]:
    if not specifiers:
        raise InvalidUsingScopeError(
            f"depfix.{api}() requires at least one package specifier",
            remediation="pass one or more supported package/source strings",
        )
    if any(not isinstance(specifier, str) or not specifier.strip() for specifier in specifiers):
        raise InvalidUsingScopeError(f"depfix.{api}() specifiers must be non-empty strings")
    return specifiers


def _caller_location(depth: int) -> tuple[str, int, Path]:
    try:
        frame = sys._getframe(depth + 1)
    except (AttributeError, ValueError):
        return "", 0, Path.cwd()
    source_file = frame.f_globals.get("__file__")
    if not isinstance(source_file, str):
        return "", frame.f_lineno, Path.cwd()
    path = Path(source_file).expanduser().resolve()
    return str(path), frame.f_lineno, path.parent
