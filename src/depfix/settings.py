"""Process configuration and manifest auto-discovery."""

from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path
from threading import RLock
from typing import Any

from platformdirs import user_cache_path

from .errors import FrozenManifestError, SpecifierError


@dataclass(frozen=True, slots=True)
class Settings:
    manifest: Path | None = None
    frozen: bool = False
    offline: bool = False
    allow_unsafe: bool = False
    prefer_newest: bool = False
    cache_dir: Path = user_cache_path("depfix")
    cache_retention_days: int = 30
    cache_auto_cleanup: bool = True
    uv: Path | None = None
    index_url: str | None = None
    extra_index_url: tuple[str, ...] = ()
    log_level: str = "INFO"


_configured: dict[str, Any] = {}
_lock = RLock()
_discovery_cache: dict[Path, Path | None] = {}


def configure(
    *,
    manifest: str | os.PathLike[str] | None = None,
    frozen: bool | None = None,
    offline: bool | None = None,
    allow_unsafe: bool | None = None,
    prefer_newest: bool | None = None,
    cache_dir: str | os.PathLike[str] | None = None,
    cache_retention_days: int | None = None,
    cache_auto_cleanup: bool | None = None,
    uv: str | os.PathLike[str] | None = None,
    index_url: str | None = None,
    extra_index_url: str | tuple[str, ...] | list[str] | None = None,
    log_level: str | None = None,
) -> Settings:
    """Set process-level defaults; per-call arguments still take precedence."""
    values: dict[str, Any] = {
        "manifest": Path(manifest).expanduser().resolve() if manifest is not None else None,
        "frozen": frozen,
        "offline": offline,
        "allow_unsafe": allow_unsafe,
        "prefer_newest": prefer_newest,
        "cache_dir": Path(cache_dir).expanduser().resolve() if cache_dir is not None else None,
        "cache_retention_days": _validate_retention_days(cache_retention_days),
        "cache_auto_cleanup": cache_auto_cleanup,
        "uv": Path(uv).expanduser().resolve() if uv is not None else None,
        "index_url": index_url,
        "extra_index_url": _split_indexes(extra_index_url) if extra_index_url is not None else None,
        "log_level": log_level,
    }
    with _lock:
        _configured.update({key: value for key, value in values.items() if value is not None})
    return resolve_settings(discover=False)


def reset_configuration() -> None:
    """Clear process configuration. Primarily useful for test isolation."""
    with _lock:
        _configured.clear()
        _discovery_cache.clear()


def resolve_settings(
    *,
    manifest: str | os.PathLike[str] | None = None,
    frozen: bool | None = None,
    offline: bool | None = None,
    allow_unsafe: bool | None = None,
    prefer_newest: bool | None = None,
    cache_dir: str | os.PathLike[str] | None = None,
    cache_retention_days: int | None = None,
    cache_auto_cleanup: bool | None = None,
    uv: str | os.PathLike[str] | None = None,
    index_url: str | None = None,
    extra_index_url: str | tuple[str, ...] | list[str] | None = None,
    log_level: str | None = None,
    discover: bool = True,
    discovery_start: str | os.PathLike[str] | None = None,
) -> Settings:
    """Apply per-call, configured, environment, discovery, default precedence."""
    with _lock:
        configured = dict(_configured)
    environment: dict[str, Any] = {
        "manifest": _env_path("DEPFIX_MANIFEST"),
        "frozen": _env_bool("DEPFIX_FROZEN"),
        "offline": _env_bool("DEPFIX_OFFLINE"),
        "allow_unsafe": _env_bool("DEPFIX_ALLOW_UNSAFE"),
        "prefer_newest": _env_bool("DEPFIX_PREFER_NEWEST"),
        "cache_dir": _env_path("DEPFIX_CACHE_DIR"),
        "cache_retention_days": _env_nonnegative_int("DEPFIX_CACHE_RETENTION_DAYS"),
        "cache_auto_cleanup": _env_bool("DEPFIX_CACHE_AUTO_CLEANUP"),
        "uv": _env_path("DEPFIX_UV"),
        "index_url": os.environ.get("DEPFIX_INDEX_URL"),
        "extra_index_url": _split_indexes(os.environ.get("DEPFIX_EXTRA_INDEX_URL")),
        "log_level": os.environ.get("DEPFIX_LOG_LEVEL"),
    }
    explicit: dict[str, Any] = {
        "manifest": Path(manifest).expanduser().resolve() if manifest is not None else None,
        "frozen": frozen,
        "offline": offline,
        "allow_unsafe": allow_unsafe,
        "prefer_newest": prefer_newest,
        "cache_dir": Path(cache_dir).expanduser().resolve() if cache_dir is not None else None,
        "cache_retention_days": _validate_retention_days(cache_retention_days),
        "cache_auto_cleanup": cache_auto_cleanup,
        "uv": Path(uv).expanduser().resolve() if uv is not None else None,
        "index_url": index_url,
        "extra_index_url": _split_indexes(extra_index_url) if extra_index_url is not None else None,
        "log_level": log_level,
    }
    defaults = Settings()
    start = Path(discovery_start).resolve() if discovery_start is not None else None
    project_config = _project_config_values(start) if discover else {}

    def choose(name: str) -> Any:
        for layer in (explicit, configured, environment, project_config):
            value = layer.get(name)
            if value is not None and value != ():
                return value
        return getattr(defaults, name)

    selected_manifest = choose("manifest")
    if selected_manifest is None and discover:
        selected_manifest = discover_manifest(start)
    return Settings(
        manifest=selected_manifest,
        frozen=bool(choose("frozen")),
        offline=bool(choose("offline")),
        allow_unsafe=bool(choose("allow_unsafe")),
        prefer_newest=bool(choose("prefer_newest")),
        cache_dir=Path(choose("cache_dir")),
        cache_retention_days=int(choose("cache_retention_days")),
        cache_auto_cleanup=bool(choose("cache_auto_cleanup")),
        uv=Path(choose("uv")) if choose("uv") is not None else None,
        index_url=choose("index_url"),
        extra_index_url=tuple(choose("extra_index_url")),
        log_level=str(choose("log_level")),
    )


def resolve_loading_settings(
    *,
    index_url: str | None = None,
    extra_index_url: str | tuple[str, ...] | list[str] | None = None,
    **overrides: Any,
) -> Settings:
    """Resolve a loading call's settings without mutating process configuration."""
    settings = resolve_settings(index_url=index_url, extra_index_url=extra_index_url, **overrides)
    if settings.manifest is not None and (index_url is not None or extra_index_url is not None):
        raise FrozenManifestError(
            "A prepared manifest is exact and does not accept live package-index overrides",
            manifest=settings.manifest,
            frozen=settings.frozen,
            remediation="omit index_url/extra_index_url or omit the prepared manifest for live resolution",
        )
    if extra_index_url is not None:
        settings = replace(settings, extra_index_url=_split_indexes(extra_index_url))
    elif index_url is not None:
        # A scoped primary index is complete by default. Inheriting unrelated
        # extra indexes here would make first-index selection surprising and
        # could reintroduce dependency-confusion exposure.
        settings = replace(settings, extra_index_url=())
    return settings


def discover_manifest(start: Path | None = None) -> Path | None:
    """Find `.depfix/imports.lock` without searching beyond a project boundary."""
    origin = (start or _application_start()).expanduser().resolve()
    if origin.is_file():
        origin = origin.parent
    with _lock:
        if origin in _discovery_cache:
            return _discovery_cache[origin]
    current = origin
    found: Path | None = None
    home = Path.home().resolve()
    for _depth in range(32):
        candidate = current / ".depfix" / "imports.lock"
        if candidate.is_file():
            found = candidate
            break
        boundary = any((current / marker).exists() for marker in (".git", ".hg", ".svn", "pyproject.toml"))
        parent = current.parent
        if boundary or parent == current or current == home:
            break
        current = parent
    with _lock:
        if found is not None:
            _discovery_cache[origin] = found
    return found


def _application_start() -> Path:
    main = sys.modules.get("__main__")
    main_file = getattr(main, "__file__", None)
    if not isinstance(main_file, str):
        return Path.cwd()
    candidate = Path(main_file).resolve()
    prefix = Path(sys.prefix).resolve()
    if candidate.is_relative_to(prefix) or (candidate.name == "__main__.py" and candidate.parent.name == "depfix"):
        return Path.cwd()
    return candidate


def _env_path(name: str) -> Path | None:
    value = os.environ.get(name)
    return Path(value).expanduser().resolve() if value else None


def _env_bool(name: str) -> bool | None:
    value = os.environ.get(name)
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise SpecifierError(f"{name} must be a boolean value", source="environment")


def _env_nonnegative_int(name: str) -> int | None:
    value = os.environ.get(name)
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError:
        raise SpecifierError(f"{name} must be a non-negative integer", source="environment") from None
    if parsed < 0:
        raise SpecifierError(f"{name} must be a non-negative integer", source="environment")
    return parsed


def _validate_retention_days(value: int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("cache_retention_days must be a non-negative integer")
    return value


def _split_indexes(value: str | tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return tuple(item.strip() for item in value.split(",") if item.strip())
    return tuple(str(item) for item in value)


def _project_config_values(start: Path | None = None) -> dict[str, Any]:
    path = _find_project_file(Path(".depfix/config.toml"), start=start)
    if path is None:
        return {}
    try:
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise SpecifierError(
            "Unable to read .depfix/config.toml",
            source=str(path),
            remediation=str(exc),
        ) from exc
    selected: dict[str, Any] = {}
    for table_name in ("settings", "resolver", "index"):
        table = raw.get(table_name, {})
        if not isinstance(table, dict):
            raise SpecifierError(
                f"[{table_name}] in .depfix/config.toml must be a table",
                source=str(path),
            )
        selected.update(table)
    aliases = {
        "index-url": "index_url",
        "extra-index-url": "extra_index_url",
        "cache-dir": "cache_dir",
        "cache-retention-days": "cache_retention_days",
        "cache-auto-cleanup": "cache_auto_cleanup",
        "log-level": "log_level",
    }
    values = {aliases.get(key, key.replace("-", "_")): value for key, value in selected.items()}
    result: dict[str, Any] = {}
    for key in ("frozen", "offline", "allow_unsafe", "prefer_newest", "cache_auto_cleanup"):
        if key in values:
            if not isinstance(values[key], bool):
                raise SpecifierError(
                    f"{key.replace('_', '-')} in .depfix/config.toml must be a boolean",
                    source=str(path),
                )
            result[key] = values[key]
    if "cache_retention_days" in values:
        value = values["cache_retention_days"]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise SpecifierError(
                "cache-retention-days in .depfix/config.toml must be a non-negative integer",
                source=str(path),
            )
        result["cache_retention_days"] = value
    for key in ("index_url", "log_level"):
        if key in values:
            if not isinstance(values[key], str):
                raise SpecifierError(
                    f"{key.replace('_', '-')} in .depfix/config.toml must be a string",
                    source=str(path),
                )
            result[key] = values[key]
    if "extra_index_url" in values:
        result["extra_index_url"] = _split_indexes(values["extra_index_url"])
    for key in ("cache_dir", "uv"):
        if key in values:
            configured_path = Path(str(values[key])).expanduser()
            if not configured_path.is_absolute():
                configured_path = path.parent / configured_path
            result[key] = configured_path.resolve()
    return result


def _find_project_file(relative: Path, *, start: Path | None = None) -> Path | None:
    current = (start or _application_start()).expanduser().resolve()
    if current.is_file():
        current = current.parent
    home = Path.home().resolve()
    for _depth in range(32):
        candidate = current / relative
        if candidate.is_file():
            return candidate
        boundary = any((current / marker).exists() for marker in (".git", ".hg", ".svn", "pyproject.toml"))
        parent = current.parent
        if boundary or parent == current or current == home:
            break
        current = parent
    return None
