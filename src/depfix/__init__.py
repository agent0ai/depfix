"""Depfix: version-resolved imports with per-package dependency realms.

Importing this package performs no network, resolver, or subprocess work. Cold
preparation begins only when a load API is called.
"""

from __future__ import annotations

import asyncio
import os
import warnings
from types import ModuleType

from ._version import __version__
from .errors import (
    ArtifactError,
    BundleError,
    DefaultImportConflictError,
    DepfixError,
    FrozenManifestError,
    HashMismatchError,
    ImportDispatcherConflictError,
    InvalidUsingScopeError,
    ManifestError,
    ManifestMismatchError,
    ManifestNotFoundError,
    ModuleDiscoveryError,
    ModuleNotProvidedError,
    MultipleImportModulesError,
    NativeIsolationRequired,
    NoImportModulesError,
    OfflineArtifactMissingError,
    ResolutionError,
    ScopeModuleNotProvidedError,
    SourceError,
    SpecifierError,
    UnsupportedManifestVersionError,
    UnsupportedUvVersionError,
    UvBackendError,
    UvBootstrapError,
    UvNotFoundError,
)
from .handles import PackageHandle
from .manager import activate_manifest, prepare_request
from .scopes import default, using
from .settings import Settings, configure, resolve_settings


def import_module(
    specifier: str,
    *,
    module: str | None = None,
    refresh: bool = False,
    manifest: str | os.PathLike[str] | None = None,
    frozen: bool | None = None,
    offline: bool | None = None,
    isolation: str | None = None,
) -> ModuleType:
    """Return exactly one canonical module or raise a typed discovery error."""
    settings = resolve_settings(manifest=manifest, frozen=frozen, offline=offline)
    runtime, request = prepare_request(
        specifier,
        module=module,
        api="import_module",
        refresh=refresh,
        isolation=isolation,
        settings=settings,
    )
    if not request.module:
        raise NoImportModulesError("The resolved request has no selected import module", request=specifier)
    return runtime.import_for_node(request.node, request.module)


def load_package(
    specifier: str,
    *,
    refresh: bool = False,
    manifest: str | os.PathLike[str] | None = None,
    frozen: bool | None = None,
    offline: bool | None = None,
    isolation: str | None = None,
) -> PackageHandle:
    """Return one package handle without eagerly importing its root modules."""
    settings = resolve_settings(manifest=manifest, frozen=frozen, offline=offline)
    runtime, request = prepare_request(
        specifier,
        module=None,
        api="load_package",
        refresh=refresh,
        isolation=isolation,
        settings=settings,
    )
    return PackageHandle(runtime, request)


async def import_module_async(
    specifier: str,
    *,
    module: str | None = None,
    refresh: bool = False,
    manifest: str | os.PathLike[str] | None = None,
    frozen: bool | None = None,
    offline: bool | None = None,
    isolation: str | None = None,
) -> ModuleType:
    """Async-friendly wrapper sharing canonical identity with `import_module`."""
    return await asyncio.to_thread(
        import_module,
        specifier,
        module=module,
        refresh=refresh,
        manifest=manifest,
        frozen=frozen,
        offline=offline,
        isolation=isolation,
    )


async def load_package_async(
    specifier: str,
    *,
    refresh: bool = False,
    manifest: str | os.PathLike[str] | None = None,
    frozen: bool | None = None,
    offline: bool | None = None,
    isolation: str | None = None,
) -> PackageHandle:
    return await asyncio.to_thread(
        load_package,
        specifier,
        refresh=refresh,
        manifest=manifest,
        frozen=frozen,
        offline=offline,
        isolation=isolation,
    )


def activate(
    manifest: str | os.PathLike[str] = ".depfix/imports.lock",
    *,
    cache_dir: str | os.PathLike[str] | None = None,
) -> object:
    """Deprecated prototype alias for prepared-manifest activation."""
    warnings.warn(
        "depfix.activate() is deprecated; standard imports use depfix.default(), while prepared aliases auto-activate",
        DeprecationWarning,
        stacklevel=2,
    )
    settings = resolve_settings(manifest=manifest, cache_dir=cache_dir, frozen=True, discover=False)
    assert settings.manifest is not None
    return activate_manifest(settings.manifest, settings)


def _load_alias(name: str, identity: tuple[str, str, str, str]) -> ModuleType:
    from .manager import load_generated_alias

    return load_generated_alias(name, identity)


def _load_package_alias(name: str, identity: tuple[str, str, str, str]) -> PackageHandle:
    from .manager import load_generated_package

    return load_generated_package(name, identity)


def multiprocessing_initializer(
    manifest: str | os.PathLike[str],
    cache_dir: str | os.PathLike[str] | None = None,
) -> None:
    settings = resolve_settings(manifest=manifest, cache_dir=cache_dir, frozen=True, discover=False)
    assert settings.manifest is not None
    activate_manifest(settings.manifest, settings)


__all__ = [
    "ArtifactError",
    "BundleError",
    "DefaultImportConflictError",
    "DepfixError",
    "FrozenManifestError",
    "HashMismatchError",
    "ImportDispatcherConflictError",
    "InvalidUsingScopeError",
    "ManifestError",
    "ManifestMismatchError",
    "ManifestNotFoundError",
    "ModuleDiscoveryError",
    "ModuleNotProvidedError",
    "MultipleImportModulesError",
    "NativeIsolationRequired",
    "NoImportModulesError",
    "OfflineArtifactMissingError",
    "PackageHandle",
    "ResolutionError",
    "ScopeModuleNotProvidedError",
    "Settings",
    "SourceError",
    "SpecifierError",
    "UnsupportedManifestVersionError",
    "UnsupportedUvVersionError",
    "UvBackendError",
    "UvBootstrapError",
    "UvNotFoundError",
    "configure",
    "default",
    "import_module",
    "import_module_async",
    "load_package",
    "load_package_async",
    "multiprocessing_initializer",
    "using",
    "__version__",
]
