"""Depfix: version-resolved imports with per-package dependency realms.

Importing this package performs no network, resolver, or subprocess work. Cold
preparation begins only when a load API is called.
"""

from __future__ import annotations

import os
import warnings
from types import ModuleType
from typing import TYPE_CHECKING, Any

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
    SharedImportConflictError,
    SourceError,
    SpecifierError,
    UnsafePackageError,
    UnsupportedManifestVersionError,
    UnsupportedUvVersionError,
    UvBackendError,
    UvBootstrapError,
    UvNotFoundError,
)

if TYPE_CHECKING:
    from .cache import CacheCleanupResult, CachedPackage
    from .handles import PackageHandle
    from .scopes import default, using
    from .settings import Settings


def configure(
    *,
    manifest: str | os.PathLike[str] | None = None,
    frozen: bool | None = None,
    offline: bool | None = None,
    allow_unsafe: bool | None = None,
    cache_dir: str | os.PathLike[str] | None = None,
    cache_retention_days: int | None = None,
    cache_auto_cleanup: bool | None = None,
    uv: str | os.PathLike[str] | None = None,
    index_url: str | None = None,
    extra_index_url: str | tuple[str, ...] | list[str] | None = None,
    log_level: str | None = None,
) -> Settings:
    """Set process-level defaults without loading the resolver until needed."""
    from .settings import configure as configure_settings

    return configure_settings(
        manifest=manifest,
        frozen=frozen,
        offline=offline,
        allow_unsafe=allow_unsafe,
        cache_dir=cache_dir,
        cache_retention_days=cache_retention_days,
        cache_auto_cleanup=cache_auto_cleanup,
        uv=uv,
        index_url=index_url,
        extra_index_url=extra_index_url,
        log_level=log_level,
    )


def import_module(
    specifier: str,
    *,
    module: str | None = None,
    refresh: bool = False,
    manifest: str | os.PathLike[str] | None = None,
    frozen: bool | None = None,
    offline: bool | None = None,
    isolation: str | None = None,
    allow_unsafe: bool | None = None,
) -> ModuleType:
    """Return exactly one canonical module or raise a typed discovery error."""
    from .manager import prepare_request
    from .settings import resolve_settings

    settings = resolve_settings(
        manifest=manifest,
        frozen=frozen,
        offline=offline,
        allow_unsafe=allow_unsafe,
    )
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
    allow_unsafe: bool | None = None,
) -> PackageHandle:
    """Return one package handle without eagerly importing its root modules."""
    from .handles import PackageHandle
    from .manager import prepare_request
    from .settings import resolve_settings

    settings = resolve_settings(
        manifest=manifest,
        frozen=frozen,
        offline=offline,
        allow_unsafe=allow_unsafe,
    )
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
    allow_unsafe: bool | None = None,
) -> ModuleType:
    """Async-friendly wrapper sharing canonical identity with `import_module`."""
    import asyncio

    return await asyncio.to_thread(
        import_module,
        specifier,
        module=module,
        refresh=refresh,
        manifest=manifest,
        frozen=frozen,
        offline=offline,
        isolation=isolation,
        allow_unsafe=allow_unsafe,
    )


async def load_package_async(
    specifier: str,
    *,
    refresh: bool = False,
    manifest: str | os.PathLike[str] | None = None,
    frozen: bool | None = None,
    offline: bool | None = None,
    isolation: str | None = None,
    allow_unsafe: bool | None = None,
) -> PackageHandle:
    import asyncio

    return await asyncio.to_thread(
        load_package,
        specifier,
        refresh=refresh,
        manifest=manifest,
        frozen=frozen,
        offline=offline,
        isolation=isolation,
        allow_unsafe=allow_unsafe,
    )


def list_cached_packages(*, cache_dir: str | os.PathLike[str] | None = None) -> tuple[CachedPackage, ...]:
    """List installed package artifacts in the shared Depfix cache."""
    from .cache import Cache
    from .settings import resolve_settings

    settings = resolve_settings(cache_dir=cache_dir, discover=True)
    return Cache(settings.cache_dir).list_packages()


def cleanup_cache(
    *,
    days: int | None = None,
    cache_dir: str | os.PathLike[str] | None = None,
    dry_run: bool = False,
) -> CacheCleanupResult:
    """Remove package artifacts unused beyond the configured retention window."""
    from .cache import Cache
    from .settings import resolve_settings

    settings = resolve_settings(cache_dir=cache_dir, discover=True)
    retention = settings.cache_retention_days if days is None else days
    return Cache(settings.cache_dir).cleanup(retention, dry_run=dry_run)


def remove_cached_package(
    distribution: str,
    *,
    version: str | None = None,
    artifact_hash: str | None = None,
    cache_dir: str | os.PathLike[str] | None = None,
    dry_run: bool = False,
) -> CacheCleanupResult:
    """Remove one distribution selection from the shared Depfix cache."""
    from .cache import Cache
    from .settings import resolve_settings

    settings = resolve_settings(cache_dir=cache_dir, discover=True)
    return Cache(settings.cache_dir).remove_package(
        distribution,
        version=version,
        artifact_hash=artifact_hash,
        dry_run=dry_run,
    )


def activate(
    manifest: str | os.PathLike[str] = ".depfix/imports.lock",
    *,
    cache_dir: str | os.PathLike[str] | None = None,
) -> object:
    """Deprecated prototype alias for prepared-manifest activation."""
    from .manager import activate_manifest
    from .settings import resolve_settings

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
    from .manager import activate_manifest
    from .settings import resolve_settings

    settings = resolve_settings(manifest=manifest, cache_dir=cache_dir, frozen=True, discover=False)
    assert settings.manifest is not None
    activate_manifest(settings.manifest, settings)


def __getattr__(name: str) -> Any:
    """Load public API objects only when code asks for them."""
    if name in {"default", "using"}:
        from .scopes import default, using

        value = default if name == "default" else using
        globals()[name] = value
        return value
    if name == "PackageHandle":
        from .handles import PackageHandle

        globals()[name] = PackageHandle
        return PackageHandle
    if name in {"CachedPackage", "CacheCleanupResult"}:
        from .cache import CacheCleanupResult, CachedPackage

        cache_type = CachedPackage if name == "CachedPackage" else CacheCleanupResult
        globals()[name] = cache_type
        return cache_type
    if name == "Settings":
        from .settings import Settings

        globals()[name] = Settings
        return Settings
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "ArtifactError",
    "BundleError",
    "CacheCleanupResult",
    "CachedPackage",
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
    "SharedImportConflictError",
    "Settings",
    "SourceError",
    "SpecifierError",
    "UnsupportedManifestVersionError",
    "UnsupportedUvVersionError",
    "UvBackendError",
    "UvBootstrapError",
    "UvNotFoundError",
    "UnsafePackageError",
    "cleanup_cache",
    "configure",
    "default",
    "import_module",
    "import_module_async",
    "load_package",
    "load_package_async",
    "list_cached_packages",
    "multiprocessing_initializer",
    "remove_cached_package",
    "using",
    "__version__",
]
