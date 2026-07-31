"""Typed public exceptions and secret-safe diagnostics for Depfix."""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path

_CREDENTIAL = re.compile(r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.-]*://)(?P<userinfo>[^/@\s]+)@")
_TOKEN_FLAGS = re.compile(r"(?i)(--(?:index-url|extra-index-url|token|password)\s+)(\S+)")
_QUERY_SECRET = re.compile(r"(?i)([?&](?:access[_-]?token|api[_-]?key|auth|credential|password|secret|token)=)[^&#\s]+")


def redact(value: str) -> str:
    """Remove common URL and CLI credential forms from diagnostics."""
    value = _CREDENTIAL.sub(r"\g<scheme><redacted>@", value)
    value = _QUERY_SECRET.sub(r"\1<redacted>", value)
    return _TOKEN_FLAGS.sub(r"\1<redacted>", value)


class DepfixError(Exception):
    """Base class for every expected, user-actionable Depfix failure."""

    def __init__(
        self,
        message: str,
        *,
        request: str | None = None,
        normalized_request: str | None = None,
        source: str | None = None,
        module: str | None = None,
        referrer: str | None = None,
        realm: str | None = None,
        candidates: Sequence[str] = (),
        import_modules: Sequence[str] = (),
        rejections: Sequence[str] = (),
        uv_command: Sequence[str] = (),
        uv_version: str | None = None,
        manifest: str | Path | None = None,
        artifact_hash: str | None = None,
        cache_path: str | Path | None = None,
        offline: bool | None = None,
        frozen: bool | None = None,
        remediation: str | None = None,
        # Phase-one internal call-site compatibility. These aliases are not
        # documented public arguments and are normalized immediately.
        specifier: str | None = None,
        lockfile: str | Path | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.request = request if request is not None else specifier
        self.normalized_request = normalized_request
        self.source = source
        self.module = module
        self.referrer = referrer
        self.realm = realm
        self.candidates = tuple(candidates)
        self.import_modules = tuple(import_modules)
        self.rejections = tuple(rejections)
        self.uv_command = tuple(redact(part) for part in uv_command)
        self.uv_version = uv_version
        selected_manifest = manifest if manifest is not None else lockfile
        self.manifest = Path(selected_manifest).resolve() if selected_manifest is not None else None
        self.artifact_hash = artifact_hash
        self.cache_path = Path(cache_path).resolve() if cache_path is not None else None
        self.offline = offline
        self.frozen = frozen
        self.remediation = remediation

    def __str__(self) -> str:
        rows = [self.message]
        for label, value in (
            ("request", self.request),
            ("normalized request", self.normalized_request),
            ("source", self.source),
            ("module", self.module),
            ("referrer", self.referrer),
            ("realm", self.realm),
            ("manifest", self.manifest),
            ("artifact", self.artifact_hash),
            ("cache", self.cache_path),
            ("uv version", self.uv_version),
        ):
            if value is not None:
                rows.append(f"  {label}: {redact(str(value))}")
        if self.offline is not None:
            rows.append(f"  offline: {str(self.offline).lower()}")
        if self.frozen is not None:
            rows.append(f"  frozen: {str(self.frozen).lower()}")
        if self.import_modules:
            rows.append("  import modules: " + ", ".join(self.import_modules))
        if self.candidates:
            rows.append("  candidates: " + ", ".join(self.candidates))
        rows.extend(f"  rejected: {redact(reason)}" for reason in self.rejections)
        if self.uv_command:
            rows.append("  uv command: " + " ".join(self.uv_command))
        if self.remediation:
            rows.append(f"  remediation: {redact(self.remediation)}")
        return "\n".join(rows)


class SpecifierError(DepfixError):
    pass


class SourceError(DepfixError):
    pass


class ResolutionError(DepfixError):
    pass


class ArtifactError(DepfixError):
    pass


class HashMismatchError(ArtifactError):
    pass


class ModuleDiscoveryError(DepfixError):
    pass


class NoImportModulesError(ModuleDiscoveryError):
    pass


class MultipleImportModulesError(ModuleDiscoveryError):
    pass


class ModuleNotProvidedError(ModuleDiscoveryError, ModuleNotFoundError):
    pass


class ManifestError(DepfixError):
    pass


class ManifestNotFoundError(ManifestError):
    pass


class ManifestMismatchError(ManifestError):
    pass


class FrozenManifestError(ManifestError):
    pass


class UnsupportedManifestVersionError(ManifestError):
    pass


class OfflineArtifactMissingError(ArtifactError):
    pass


class UvBackendError(DepfixError):
    pass


class UvNotFoundError(UvBackendError):
    pass


class UvBootstrapError(UvBackendError):
    pass


class UnsupportedUvVersionError(UvBackendError):
    pass


class NativeIsolationRequired(ModuleNotProvidedError):
    pass


class BundleError(DepfixError):
    pass


# Internal-specialized errors retained for the realm implementation. They all
# remain inside the documented public hierarchy.
class CacheError(ArtifactError):
    pass


class IntegrityError(HashMismatchError):
    pass


class LockError(ManifestError):
    pass


class RealmImportError(ModuleNotProvidedError):
    pass


class UndeclaredImportError(RealmImportError):
    pass


class ImportOwnershipError(RealmImportError):
    pass


class DefaultImportConflictError(DepfixError, ImportError):
    pass


class InvalidUsingScopeError(DepfixError):
    pass


class ScopeModuleNotProvidedError(ModuleNotProvidedError):
    pass


class ImportDispatcherConflictError(DepfixError, ImportError):
    pass


class AmbiguousMetadataError(ModuleDiscoveryError):
    pass
