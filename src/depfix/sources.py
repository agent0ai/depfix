"""Unified PEP 508 and Depfix source parser."""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from urllib.parse import parse_qs, unquote, urlsplit, urlunsplit

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name, parse_sdist_filename, parse_wheel_filename

from .errors import SourceError, SpecifierError, redact

_HASH = re.compile(r"^[0-9a-fA-F]{64}$")
_COMMIT = re.compile(r"^[0-9a-fA-F]{40,64}$")
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")
_LEGACY_PREFIXES = ("wheel+", "py+https:", "py+file:")


@dataclass(frozen=True, slots=True)
class SourceInfo:
    original: str
    normalized: str
    kind: str
    distribution: str | None = None
    requirement: str | None = None
    extras: tuple[str, ...] = ()
    marker: str | None = None
    url: str | None = None
    final_url: str | None = None
    path: Path | None = None
    sha256: str | None = None
    vcs: str | None = None
    requested_ref: str | None = None
    commit: str | None = None
    subdirectory: str | None = None
    mutable: bool = False

    @property
    def is_remote(self) -> bool:
        return self.url is not None and urlsplit(self.url).scheme not in {"", "file"}

    @property
    def uv_requirement(self) -> str:
        if self.kind == "pypi":
            assert self.requirement is not None
            return self.requirement
        if self.kind == "git":
            assert self.url is not None
            ref = self.commit or self.requested_ref
            url = self.url if self.url.startswith("git+") else "git+" + self.url
            value = url + (f"@{ref}" if ref else "")
            if self.subdirectory:
                value += f"#subdirectory={self.subdirectory}"
            return f"{self.distribution} @ {value}" if self.distribution else value
        if self.path is not None:
            value = self.path.as_uri()
        else:
            assert self.url is not None
            value = self.url
        return f"{self.distribution} @ {value}" if self.distribution else value


def parse_source(specifier: str, *, base_dir: str | os.PathLike[str] | None = None) -> SourceInfo:
    original = specifier.strip()
    if not original:
        raise SpecifierError("Requirement may not be empty", request=specifier)
    if original.startswith(_LEGACY_PREFIXES) or original.startswith("path:"):
        raise SpecifierError(
            "This provisional source syntax is no longer supported",
            request=original,
            remediation="use bare/pypi:, git:, url:, file:, or py: syntax and export a new manifest",
        )
    base = Path(base_dir).expanduser().resolve() if base_dir is not None else Path.cwd().resolve()
    if original.startswith("pypi:"):
        return _requirement_source(original, original[5:])
    if original.startswith("git:"):
        return _git_source(original, original[4:])
    if original.startswith("url:"):
        return _url_source(original, original[4:], kind="url")
    if original.startswith("py:"):
        return _url_source(original, original[3:], kind="py")
    if original.startswith("file:") or _WINDOWS_DRIVE.match(original):
        value = original[5:] if original.startswith("file:") else original
        return _file_source(original, value, base)
    try:
        requirement = Requirement(original)
    except InvalidRequirement as exc:
        raise SpecifierError("Invalid Python requirement", request=original, remediation=str(exc)) from exc
    if requirement.url:
        return _direct_reference(original, requirement, base)
    return _requirement_source(original, original)


def normalized_request(specifier: str, *, base_dir: str | os.PathLike[str] | None = None) -> str:
    return parse_source(specifier, base_dir=base_dir).normalized


def _requirement_source(original: str, value: str) -> SourceInfo:
    try:
        requirement = Requirement(value)
    except InvalidRequirement as exc:
        raise SpecifierError("Invalid Python requirement", request=original, remediation=str(exc)) from exc
    if requirement.url:
        return _direct_reference(original, requirement, Path.cwd())
    distribution = str(canonicalize_name(requirement.name))
    extras = tuple(sorted(requirement.extras))
    normalized = distribution
    if extras:
        normalized += "[" + ",".join(extras) + "]"
    normalized += str(requirement.specifier)
    if requirement.marker:
        normalized += f"; {requirement.marker}"
    return SourceInfo(
        original=original,
        normalized=normalized,
        kind="pypi",
        distribution=distribution,
        requirement=normalized,
        extras=extras,
        marker=str(requirement.marker) if requirement.marker else None,
    )


def _direct_reference(original: str, requirement: Requirement, base: Path) -> SourceInfo:
    assert requirement.url is not None
    url = requirement.url
    distribution = str(canonicalize_name(requirement.name))
    if url.startswith(("git+", "git://", "ssh://")) or (".git" in url and "@" in url):
        source = _git_source(original, url)
    elif url.startswith("file:"):
        source = _file_source(original, url, base)
    else:
        source = _url_source(original, url, kind="py" if urlsplit(url).path.endswith(".py") else "url")
    requested_name = distribution
    if requirement.extras:
        requested_name += "[" + ",".join(sorted(requirement.extras)) + "]"
    normalized = f"{requested_name} @ {source.normalized}"
    if requirement.marker:
        normalized += f"; {requirement.marker}"
    return SourceInfo(
        **{
            field: getattr(source, field)
            for field in source.__dataclass_fields__
            if field not in {"original", "normalized", "distribution", "requirement", "extras", "marker"}
        },
        original=original,
        normalized=normalized,
        distribution=distribution,
        requirement=str(requirement),
        extras=tuple(sorted(requirement.extras)),
        marker=str(requirement.marker) if requirement.marker else None,
    )


def _git_source(original: str, value: str) -> SourceInfo:
    raw = value.strip()
    if raw.startswith("git:") and not raw.startswith("git://"):
        raw = raw[4:]
    clean, fragment = _split_fragment(raw)
    params = parse_qs(fragment, keep_blank_values=False)
    ref = next(iter(params.get("ref", ())), None)
    subdirectory = next(iter(params.get("subdirectory", ())), None)
    # Split only a suffix ref. Authentication `@` occurs before the final path
    # component, while a VCS ref follows `.git` or the repository path.
    if ref is None:
        clean, ref = _git_suffix_ref(clean)
    if clean.startswith("git+"):
        clean = clean[4:]
    scheme = urlsplit(clean).scheme
    if scheme not in {"https", "ssh", "git"} and not re.match(r"^[^@\s]+@[^:\s]+:.+", clean):
        raise SourceError(
            "Git sources require HTTPS, SSH, git, or SCP-style URLs", request=original, source=redact(clean)
        )
    commit = ref.lower() if ref and _COMMIT.fullmatch(ref) else None
    mutable = commit is None
    normalized_url = redact(clean)
    normalized = f"git:{normalized_url}"
    if ref:
        normalized += f"@{ref}"
    if subdirectory:
        normalized += f"#subdirectory={subdirectory}"
    return SourceInfo(
        original=original,
        normalized=normalized,
        kind="git",
        url=clean,
        vcs="git",
        requested_ref=ref,
        commit=commit,
        subdirectory=subdirectory,
        mutable=mutable,
    )


def _git_suffix_ref(value: str) -> tuple[str, str | None]:
    """Split a repository suffix ref without mistaking URL authentication for it."""
    candidate_url, separator, candidate_ref = value.rpartition("@")
    if not separator or not candidate_ref:
        return value, None
    without_git_prefix = value[4:] if value.startswith("git+") else value
    split = urlsplit(without_git_prefix)
    if split.scheme:
        path_start = without_git_prefix.find(split.path)
        at_position = without_git_prefix.rfind("@")
        if not split.path or at_position < path_start:
            return value, None
    else:
        # SCP-style URLs have their authentication separator before the host
        # colon, while a requested ref follows the repository path.
        path_start = value.find(":")
        if path_start < 0 or value.rfind("@") < path_start:
            return value, None
    return candidate_url, candidate_ref


def _url_source(original: str, value: str, *, kind: str) -> SourceInfo:
    url, digest, _fragment = _artifact_url(value)
    split = urlsplit(url)
    if split.scheme not in {"https", "http"}:
        raise SourceError("Remote sources require an HTTP(S) URL", request=original, source=redact(url))
    if kind == "py" and not split.path.lower().endswith(".py"):
        raise SourceError("py: sources must identify one .py file", request=original, source=redact(url))
    distribution = _distribution_from_filename(Path(unquote(split.path)).name)
    normalized = f"{kind}:{redact(url)}" + (f"#sha256={digest}" if digest else "")
    return SourceInfo(
        original, normalized, kind, distribution=distribution, url=url, sha256=digest, mutable=digest is None
    )


def _file_source(original: str, value: str, base: Path) -> SourceInfo:
    clean, digest, _fragment = _artifact_url(value)
    if clean.startswith("file://"):
        split = urlsplit(clean)
        if split.netloc not in {"", "localhost"}:
            # UNC paths are retained on Windows; other platforms cannot safely
            # reinterpret the authority as a local path.
            if os.name != "nt":
                raise SourceError("UNC file URLs are supported only on Windows", request=original)
            path = Path(f"//{split.netloc}{unquote(split.path)}")
        else:
            path = Path(unquote(split.path))
    elif _WINDOWS_DRIVE.match(clean):
        path = Path(PureWindowsPath(clean))
    else:
        path = Path(unquote(clean))
        if not path.is_absolute():
            path = base / path
    path = path.expanduser().resolve()
    kind = "py" if path.suffix.lower() == ".py" else "file"
    distribution = _distribution_from_filename(path.name)
    normalized = f"file:{path.as_posix()}" + (f"#sha256={digest}" if digest else "")
    return SourceInfo(
        original, normalized, kind, distribution=distribution, path=path, sha256=digest, mutable=path.is_dir()
    )


def _artifact_url(value: str) -> tuple[str, str | None, str]:
    split = urlsplit(value)
    params = parse_qs(split.fragment, keep_blank_values=False)
    hashes = params.get("sha256", [])
    if len(hashes) > 1 or (hashes and not _HASH.fullmatch(hashes[0])):
        raise SpecifierError("sha256 must be one 64-hex digest", request=value)
    digest = hashes[0].lower() if hashes else None
    remaining = "&".join(f"{key}={item}" for key, items in sorted(params.items()) if key != "sha256" for item in items)
    clean = urlunsplit((split.scheme, split.netloc, split.path, split.query, remaining))
    return clean, digest, remaining


def _split_fragment(value: str) -> tuple[str, str]:
    before, separator, fragment = value.partition("#")
    return before, fragment if separator else ""


def _distribution_from_filename(filename: str) -> str | None:
    try:
        if filename.endswith(".whl"):
            distribution, _version, _build, _tags = parse_wheel_filename(filename)
            return str(canonicalize_name(str(distribution)))
        distribution, _version = parse_sdist_filename(filename)
        return str(canonicalize_name(str(distribution)))
    except Exception:
        return None


def hash_local_source(path: Path) -> str:
    """Return a deterministic content identity for a file or project tree."""
    digest = hashlib.sha256()
    if path.is_file():
        digest.update(path.read_bytes())
        return digest.hexdigest()
    if not path.is_dir():
        raise SourceError("Local source does not exist", source=str(path))
    excluded = {".git", ".hg", ".svn", ".depfix", ".venv", "__pycache__", "build", "dist"}
    for child in sorted(path.rglob("*"), key=lambda item: item.as_posix()):
        relative = child.relative_to(path)
        if any(part in excluded for part in relative.parts) or not child.is_file():
            continue
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(child.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()
