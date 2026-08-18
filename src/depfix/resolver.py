"""Realm-aware resolver with uv-backed root resolution and exact artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass, fields, replace
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urldefrag, urljoin, urlsplit

from packaging.markers import default_environment
from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.tags import sys_tags
from packaging.utils import canonicalize_name, parse_sdist_filename, parse_wheel_filename
from packaging.version import InvalidVersion, Version

from ._version import __version__
from .cache import Cache, CachedPackage, _host_matches, _open_url
from .config import ImportDeclaration, ProjectConfig
from .errors import (
    DepfixError,
    HashMismatchError,
    ModuleNotProvidedError,
    MultipleImportModulesError,
    NoImportModulesError,
    ResolutionError,
    SourceError,
    redact,
)
from .manifest import computed_graph_id, current_environment
from .models import Alias, Artifact, LockedGraph, Node
from .progress import ProgressReporter
from .settings import Settings, resolve_settings
from .sources import SourceInfo, hash_local_source, parse_source
from .uv_backend import PlanPreference, ResolutionPlan, UvBackend
from .wheel import WheelInspection, inspect_wheel

_SIMPLE_JSON = "application/vnd.pypi.simple.v1+json"
_SIMPLE_HTML = {"application/vnd.pypi.simple.v1+html", "text/html"}
_SIMPLE_ACCEPT = "application/vnd.pypi.simple.v1+json, application/vnd.pypi.simple.v1+html;q=0.9, text/html;q=0.8"
_WILDCARD_COMPARISON = re.compile(r"^(?P<operator><=|>=|<|>)(?P<release>\d+(?:\.\d+)*)\.\*$")
_WINDOWS_DRIVE_PATH = re.compile(r"^[A-Za-z]:")
_SOURCE_ARCHIVE_MAX_FILES = 50_000
_SOURCE_ARCHIVE_MAX_SIZE = 2 * 1024 * 1024 * 1024


class _SimpleHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.files: list[dict[str, object]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        values = {key.lower(): value for key, value in attrs}
        href = values.get("href")
        if not href:
            return
        yanked = "data-yanked" in values
        self.files.append(
            {
                "url": href,
                "requires-python": values.get("data-requires-python") or "",
                "yanked": yanked,
                "yanked-reason": (values.get("data-yanked") or "") if yanked else "",
            }
        )


class _Candidate:
    def __init__(
        self,
        distribution: str,
        version: str,
        url: str,
        filename: str,
        size: int,
        sha256: str,
        requires_python: str = "",
        yanked: bool = False,
        yanked_reason: str = "",
        source: SourceInfo | None = None,
        built: bool = False,
        source_url: str = "",
        source_final_url: str = "",
        source_sha256: str = "",
        source_size: int = 0,
        installed_artifact: Artifact | None = None,
    ) -> None:
        self.distribution = distribution
        self.version = version
        self.url = url
        self.filename = filename
        self.size = size
        self.sha256 = sha256
        self.requires_python = requires_python
        self.yanked = yanked
        self.yanked_reason = yanked_reason
        self.source = source
        self.built = built
        self.source_url = source_url
        self.source_final_url = source_final_url
        self.source_sha256 = source_sha256
        self.source_size = source_size
        self.installed_artifact = installed_artifact


class Resolver:
    def __init__(
        self,
        cache: Cache,
        *,
        settings: Settings | None = None,
        backend: UvBackend | None = None,
        progress: ProgressReporter | None = None,
        index_url: str | None = None,
        allow_yanked: bool = False,
    ) -> None:
        self.cache = cache
        self.settings = settings or resolve_settings(cache_dir=cache.root.parent, discover=False)
        if index_url is not None:
            self.settings = replace(self.settings, index_url=index_url, extra_index_url=())
        self.progress = progress or ProgressReporter(self.settings.log_level)
        self.backend = backend or UvBackend(self.settings, cache, progress=self.progress)
        self._backend_supplied = backend is not None
        self._base_settings = self.settings
        # The JSON endpoint is used only to turn uv-selected versions into exact
        # compatible wheel records. A custom endpoint remains injectable for
        # deterministic local-index tests.
        configured_index = self.settings.index_url
        self.index_url = (configured_index or "https://pypi.org/pypi").rstrip("/")
        self._custom_index = configured_index is not None or bool(self.settings.extra_index_url)
        configured_indexes = self.settings.extra_index_url
        self._project_indexes = tuple(dict.fromkeys((*configured_indexes, self.index_url)))
        self.allow_yanked = allow_yanked
        self._artifacts: dict[str, Artifact] = {}
        self._nodes: dict[str, Node] = {}
        self._candidate_cache: dict[tuple[str, str, bool], _Candidate] = {}
        self._uv_version = "not-required"
        self._policy: dict[str, object] = {}
        self._allowed_hosts: tuple[str, ...] = ()
        self._allow_insecure = False
        self._prefer_newest = self.settings.prefer_newest
        self._constraints: dict[str, SpecifierSet] = {}
        self._active_plan: dict[str, str] = {}
        self._installed_inventory: tuple[CachedPackage, ...] | None = None

    def resolve(self, config: ProjectConfig) -> LockedGraph:
        self._policy = dict(config.policy)
        configured_preference = self._policy.get("prefer-newest", self.settings.prefer_newest)
        if not isinstance(configured_preference, bool):
            raise ResolutionError("Policy 'prefer-newest' must be boolean")
        self._prefer_newest = configured_preference
        self._policy["prefer-newest"] = configured_preference
        self._constraints = self._parse_constraints(self._policy.get("constraints"))
        if self._constraints:
            self._policy["constraints"] = tuple(
                f"{distribution}{constraint}" for distribution, constraint in sorted(self._constraints.items())
            )
        self._allowed_hosts = _policy_strings(self._policy.get("allowed-hosts"))
        self._allow_insecure = bool(self._policy.get("allow-insecure-transport", False))
        self._validate_index_policy()
        bulk_plans = self._bulk_plan(config)
        aliases: list[Alias] = []
        for declaration in config.imports:
            self._active_plan = bulk_plans.get(declaration.name, {})
            self._apply_declaration_indexes(declaration)
            prefer_newest = self._prefer_newest if declaration.prefer_newest is None else declaration.prefer_newest
            if not isinstance(prefer_newest, bool):
                raise ResolutionError(f"prefer-newest for alias {declaration.name!r} must be boolean")
            source = parse_source(declaration.specifier, base_dir=declaration.base_dir or config.path.parent)
            self.progress.emit("resolve", source.normalized)
            try:
                node = self._resolve_declaration(
                    declaration,
                    source,
                    path=f"request:{declaration.name}",
                    ancestors={},
                    prefer_newest=prefer_newest,
                )
                selected_module = self._select_module(declaration, source, node)
            except DepfixError as exc:
                if declaration.source_file and exc.referrer is None:
                    exc.referrer = f"{declaration.source_file}:{declaration.source_line}"
                raise
            aliases.append(
                Alias(
                    declaration.name,
                    node.id,
                    selected_module or "",
                    redact(declaration.specifier),
                    normalized_specifier=source.normalized,
                    api=declaration.api,
                    source_file=declaration.source_file,
                    source_line=declaration.source_line,
                    source_column=declaration.source_column,
                    assignment=declaration.assignment,
                    explicit_module=declaration.module is not None,
                    isolation=declaration.isolation or str(config.policy.get("isolation", "auto")),
                    allow_unsafe=(
                        declaration.allow_unsafe
                        if declaration.allow_unsafe is not None
                        else bool(config.policy.get("allow-unsafe", False))
                    ),
                    index_identity=_index_policy_identity(self._project_indexes),
                    source_policy=str(config.policy.get("source-policy", "default")),
                    group=declaration.group_id,
                    mode=declaration.mode,
                    enclosing_function=declaration.enclosing_function,
                )
            )
        environment = current_environment()
        graph = LockedGraph(
            format_version=1,
            graph_id="",
            created_by=f"depfix {__version__}",
            environment=environment,
            artifacts=tuple(sorted(self._artifacts.values(), key=lambda value: value.id)),
            nodes=tuple(sorted(self._nodes.values(), key=lambda value: value.id)),
            aliases=tuple(sorted(aliases, key=lambda value: value.name)),
            policy=self._policy,
            resolver_backend="uv",
            resolver_version=self._uv_version,
        )
        return replace(graph, graph_id=computed_graph_id(graph))

    def _bulk_plan(self, config: ProjectConfig) -> dict[str, dict[str, str]]:
        """Plan registry roots together, recursively splitting failed cohorts."""
        registry: list[tuple[ImportDeclaration, SourceInfo, str]] = []
        for declaration in config.imports:
            source = parse_source(declaration.specifier, base_dir=declaration.base_dir or config.path.parent)
            if source.kind != "pypi" or source.requirement is None:
                continue
            if declaration.index_url is not None or declaration.extra_index_url is not None:
                continue
            requested = Requirement(source.requirement)
            constraint = self._constrained_specifier(source.distribution or requested.name, requested.specifier)
            extras = f"[{','.join(sorted(requested.extras))}]" if requested.extras else ""
            registry.append((declaration, source, f"{source.distribution}{extras}{constraint}"))
        if len(registry) < 2 or not hasattr(self.backend, "resolve_requirements_plan"):
            return {}

        preferences = self._bulk_plan_preferences(registry)
        explicit_constraints = tuple(f"{name}{value}" for name, value in sorted(self._constraints.items()))
        preferred_constraints = explicit_constraints + tuple(
            f"{item.distribution}=={item.version}" for item in preferences
        )
        requirements = tuple(item[2] for item in registry)
        self.progress.emit("plan", f"bulk resolving {len(requirements)} package roots")

        plans: dict[str, dict[str, str]] = {}

        def record(indexes: tuple[int, ...], plan: ResolutionPlan) -> None:
            for index in indexes:
                plans[registry[index][0].name] = plan.distributions

        all_indexes = tuple(range(len(registry)))
        try:
            plan = self.backend.resolve_requirements_plan(
                requirements,
                constraints=preferred_constraints,
                preferences=preferences,
            )
        except DepfixError:
            if preferences:
                self.progress.emit(
                    "plan",
                    "installed preferences conflict with the bulk plan; retrying the full root group",
                )
                try:
                    plan = self.backend.resolve_requirements_plan(
                        requirements,
                        constraints=explicit_constraints,
                        preferences=(),
                    )
                except DepfixError:
                    plan = None
            else:
                plan = None
        if plan is not None:
            record(all_indexes, plan)
            self._uv_version = self.backend.version()
            return plans

        split_indexes = self._bulk_split_order(registry, preferences)
        cohorts: list[tuple[int, ...]] = [split_indexes]
        successful_cohorts = 0
        while cohorts:
            indexes = cohorts.pop()
            if len(indexes) < 2:
                continue
            midpoint = len(indexes) // 2
            for part in (indexes[:midpoint], indexes[midpoint:]):
                try:
                    cohort_plan = self.backend.resolve_requirements_plan(
                        tuple(registry[index][2] for index in part),
                        constraints=explicit_constraints,
                        preferences=(),
                    )
                except DepfixError:
                    if len(part) > 1:
                        self.progress.emit("plan", f"splitting conflicting cohort of {len(part)} roots")
                        cohorts.append(part)
                    continue
                record(part, cohort_plan)
                successful_cohorts += 1

        isolated = [source for declaration, source, _requirement in registry if declaration.name not in plans]
        if plans:
            self._uv_version = self.backend.version()
            self.progress.emit("plan", f"retained {successful_cohorts} compatible bulk cohorts")
        if isolated:
            self.progress.emit(
                "fallback",
                f"isolating {len(isolated)} conflicting roots: {', '.join(item.normalized for item in isolated)}",
            )
        return plans

    def _bulk_split_order(
        self,
        registry: list[tuple[ImportDeclaration, SourceInfo, str]],
        preferences: tuple[PlanPreference, ...],
    ) -> tuple[int, ...]:
        """Move roots with locally proven installed-version mismatches to the split boundary."""
        by_distribution = {item.distribution: item for item in preferences}
        scored: list[tuple[int, int, int]] = []
        signaled = 0
        for index, (_declaration, source, requirement_text) in enumerate(registry):
            requested = Requirement(requirement_text)
            preference = by_distribution.get(source.distribution or "")
            mismatches = 0
            strictness = sum(item.operator in {"==", "===", "~=", "<", "<="} for item in requested.specifier)
            if preference is not None:
                try:
                    if Version(preference.version) not in requested.specifier:
                        mismatches += 1
                except InvalidVersion:
                    pass
                for raw_dependency in preference.requires_dist:
                    try:
                        dependency = Requirement(raw_dependency)
                    except InvalidRequirement:
                        continue
                    # Marker and extra evaluation can depend on the selected
                    # plan. Use only unconditional local metadata as a safe
                    # ordering hint rather than pre-resolving dependency trees.
                    if dependency.marker is not None:
                        continue
                    installed_dependency = by_distribution.get(str(canonicalize_name(dependency.name)))
                    if installed_dependency is None:
                        continue
                    constraint = self._constrained_specifier(dependency.name, dependency.specifier)
                    try:
                        if Version(installed_dependency.version) not in constraint:
                            mismatches += 1
                    except InvalidVersion:
                        continue
            if mismatches:
                signaled += 1
            scored.append((mismatches, strictness if mismatches else 0, index))
        if not signaled:
            return tuple(range(len(registry)))
        ordered = tuple(item[2] for item in sorted(scored))
        if ordered != tuple(range(len(registry))):
            self.progress.emit("plan", f"moving {signaled} locally signaled roots to the split boundary")
        return ordered

    def _bulk_plan_preferences(
        self,
        registry: list[tuple[ImportDeclaration, SourceInfo, str]],
    ) -> tuple[PlanPreference, ...]:
        """Return verified installed selections for a uniformly cache-first group."""
        if any(
            self._prefer_newest if declaration.prefer_newest is None else declaration.prefer_newest
            for declaration, _source, _requirement in registry
        ):
            return ()
        preferences: list[PlanPreference] = []
        distributions = sorted({package.distribution for package in self._installed_packages()})
        for distribution in distributions:
            constraint = self._constraints.get(distribution, SpecifierSet())
            candidate = self._select_installed(distribution, constraint, emit_progress=False)
            if candidate is None:
                continue
            inspection = self._inspect_installed(candidate)
            extras: set[str] = set()
            for requirement in inspection.requires_dist:
                extras.update(re.findall(r"\bextra\s*==\s*['\"]([^'\"]+)['\"]", requirement))
                extras.update(re.findall(r"['\"]([^'\"]+)['\"]\s*==\s*extra\b", requirement))
            preferences.append(
                PlanPreference(
                    candidate.distribution,
                    candidate.version,
                    inspection.requires_python,
                    inspection.requires_dist,
                    tuple(sorted(extras)),
                )
            )
        if preferences:
            self.progress.emit("reuse", f"seeding bulk plan with {len(preferences)} installed selections")
        return tuple(preferences)

    def reacquire_built_artifact(
        self,
        artifact: Artifact,
        *,
        allowed_hosts: tuple[str, ...] = (),
        allow_insecure: bool = False,
    ) -> Path:
        """Rebuild a source-derived wheel and accept only the exact locked bytes."""
        if not artifact.build_backend or not artifact.source_url:
            raise SourceError(
                "The ephemeral artifact cannot be rebuilt from recorded provenance",
                artifact_hash=artifact.sha256,
                remediation="re-export the project while the original source is available",
            )
        source = parse_source(artifact.source_url)
        if source.kind == "git":
            source = replace(
                source,
                commit=artifact.vcs_commit or source.commit,
                requested_ref=artifact.requested_ref or source.requested_ref,
                subdirectory=artifact.subdirectory or source.subdirectory,
                mutable=False,
            )
            wheel, _observed = self._build_git(source)
            candidate_path = wheel
        else:
            if source.path is not None:
                if not source.path.exists():
                    raise SourceError(
                        "The recorded source for an ephemeral built artifact is unavailable",
                        source=artifact.source_url,
                        artifact_hash=artifact.sha256,
                        remediation="restore the exact source tree or re-export the project",
                    )
                expected_source = artifact.local_source_hash or artifact.source_sha256
                actual_source = hash_local_source(source.path)
                if expected_source and actual_source != expected_source:
                    raise HashMismatchError(
                        "The recorded source changed and cannot reproduce the locked artifact",
                        source=artifact.source_url,
                        artifact_hash=artifact.sha256,
                        remediation="restore the exact source or explicitly refresh the manifest",
                    )
                source_path = source.path
            else:
                assert source.url is not None
                if not artifact.source_sha256:
                    raise SourceError(
                        "The remote source lacks the hash required to rebuild an ephemeral artifact",
                        source=artifact.source_url,
                        artifact_hash=artifact.sha256,
                        remediation="re-export the project with exact source provenance",
                    )
                source_path, final_url = self.cache.fetch_url_with_final(
                    source.url,
                    artifact.source_sha256,
                    expected_size=artifact.source_size or None,
                    allowed_hosts=allowed_hosts,
                    allow_insecure=allow_insecure,
                )
                source = replace(source, sha256=artifact.source_sha256, final_url=final_url, mutable=False)
            candidate = self._build_source_candidate(source_path, source)
            candidate_path = self.cache.blob_path(candidate.sha256)
        actual = hashlib.sha256(candidate_path.read_bytes()).hexdigest()
        if actual != artifact.sha256 or candidate_path.stat().st_size != artifact.size:
            self.cache.blob_path(actual).unlink(missing_ok=True)
            shutil.rmtree(self.cache.root / "built-wheels" / actual, ignore_errors=True)
            raise HashMismatchError(
                "Rebuilt source artifact does not match the exact locked wheel",
                source=artifact.source_url,
                artifact_hash=artifact.sha256,
                remediation="re-export and bundle in a reproducible build environment",
            )
        return self.cache.fetch_url(candidate_path.as_uri(), artifact.sha256, expected_size=artifact.size)

    def _apply_declaration_indexes(self, declaration: ImportDeclaration) -> None:
        if declaration.index_url is None and declaration.extra_index_url is None:
            selected = self._base_settings
        else:
            primary = declaration.index_url or self._base_settings.index_url
            if declaration.extra_index_url is not None:
                extras = declaration.extra_index_url
            elif declaration.index_url is not None:
                extras = ()
            else:
                extras = self._base_settings.extra_index_url
            selected = replace(self._base_settings, index_url=primary, extra_index_url=extras)
        policy_key = (selected.index_url, selected.extra_index_url)
        current_key = (self.settings.index_url, self.settings.extra_index_url)
        self.settings = selected
        if policy_key != current_key:
            self._candidate_cache.clear()
        if not self._backend_supplied:
            self.backend = UvBackend(selected, self.cache, progress=self.progress)
        configured_index = selected.index_url
        self.index_url = (configured_index or "https://pypi.org/pypi").rstrip("/")
        self._custom_index = configured_index is not None or bool(selected.extra_index_url)
        self._project_indexes = tuple(dict.fromkeys((*selected.extra_index_url, self.index_url)))
        self._validate_index_policy()

    def _select_module(self, declaration: ImportDeclaration, source: SourceInfo, node: Node) -> str | None:
        if declaration.module is not None:
            if (
                declaration.module not in node.all_importable_modules
                and declaration.module not in node.public_modules
                and declaration.module not in node.namespace_contributions
            ):
                raise ModuleNotProvidedError(
                    "The selected artifact does not provide the requested import module",
                    request=declaration.specifier,
                    normalized_request=source.normalized,
                    module=declaration.module,
                    import_modules=node.all_importable_modules,
                    remediation="choose one of the import modules discovered in the exact artifact",
                )
            return declaration.module
        if declaration.api == "load_package":
            return None
        if not node.public_modules:
            raise NoImportModulesError(
                f"{node.distribution}=={node.version} exposes no public import modules",
                request=declaration.specifier,
                normalized_request=source.normalized,
                remediation=f'use load_package("{declaration.specifier}") to inspect distribution metadata',
            )
        if len(node.public_modules) > 1:
            choices = "\n".join(f"- {name}" for name in node.public_modules)
            raise MultipleImportModulesError(
                f"{node.distribution}=={node.version} exposes multiple public import modules:\n\n{choices}",
                request=declaration.specifier,
                normalized_request=source.normalized,
                import_modules=node.public_modules,
                remediation=(
                    f'use load_package("{declaration.specifier}") or pass module='
                    + " / ".join(repr(name) for name in node.public_modules)
                ),
            )
        return node.public_modules[0]

    def _resolve_declaration(
        self,
        declaration: ImportDeclaration,
        source: SourceInfo,
        *,
        path: str,
        ancestors: dict[str, Node],
        prefer_newest: bool,
    ) -> Node:
        self._validate_source_policy(source)
        if source.kind == "py":
            return self._resolve_single_file(declaration, source, path=path)
        if source.kind == "pypi":
            assert source.distribution is not None and source.requirement is not None
            requested = Requirement(source.requirement)
            requested_constraint = self._constrained_specifier(source.distribution, requested.specifier)
            extras = f"[{','.join(sorted(requested.extras))}]" if requested.extras else ""
            constrained_requirement = f"{source.distribution}{extras}{requested_constraint}"
            planned = self._planned_version(source.distribution, requested_constraint)
            selection_constraint = SpecifierSet(f"=={planned}") if planned is not None else requested_constraint
            cached_candidate = (
                self._select_installed(source.distribution, selection_constraint) if not prefer_newest else None
            )
            exact_version = (
                planned
                or (cached_candidate.version if cached_candidate is not None else None)
                or self.backend.resolve_root_version(constrained_requirement, source.distribution)
            )
            self.progress.emit("fetch", f"{source.distribution}=={exact_version} dependency graph")
            candidate = cached_candidate or self._select_pypi(
                source.distribution,
                SpecifierSet(f"=={exact_version}"),
                prefer_newest=prefer_newest,
            )
            candidate.source = source
            candidate = self._prepare_selected_candidate(candidate)
            return self._resolve_candidate(
                candidate,
                extras=source.extras,
                path=path,
                ancestors=ancestors,
                prefer_newest=prefer_newest,
            )
        if source.kind == "git":
            wheel, exact_source = self._build_git(source)
            candidate = self._candidate_from_local_wheel(wheel, exact_source)
            return self._resolve_candidate(
                candidate,
                extras=source.extras,
                path=path,
                ancestors=ancestors,
                prefer_newest=prefer_newest,
            )
        if source.path is not None:
            if not source.path.exists():
                raise SourceError("Local source does not exist", request=source.original, source=str(source.path))
            if source.path.suffix.lower() == ".py":
                return self._resolve_single_file(declaration, source, path=path)
            if source.path.suffix.lower() == ".whl":
                candidate = self._candidate_from_local_wheel(source.path, source)
            else:
                candidate = self._build_source_candidate(source.path, source)
            return self._resolve_candidate(
                candidate,
                extras=source.extras,
                path=path,
                ancestors=ancestors,
                prefer_newest=prefer_newest,
            )
        if source.kind == "url":
            assert source.url is not None
            blob, observed_source = self._fetch_source(source)
            if source.url.lower().split("#", 1)[0].endswith(".whl"):
                candidate = self._candidate_from_blob(blob, Path(urlsplit(source.url).path).name, observed_source)
            else:
                candidate = self._build_source_candidate(blob, observed_source)
            return self._resolve_candidate(
                candidate,
                extras=source.extras,
                path=path,
                ancestors=ancestors,
                prefer_newest=prefer_newest,
            )
        raise SourceError("Unsupported normalized source", request=source.original, source=source.kind)

    def _resolve_single_file(self, declaration: ImportDeclaration, source: SourceInfo, *, path: str) -> Node:
        module = declaration.module
        if source.path is not None:
            if not source.path.is_file():
                raise SourceError("Single-file source does not exist", request=source.original, source=str(source.path))
            stem = source.path.stem
            digest = hashlib.sha256(source.path.read_bytes()).hexdigest()
            if source.sha256 and source.sha256 != digest:
                raise HashMismatchError(
                    "Local single-file hash mismatch",
                    request=source.original,
                    artifact_hash=digest,
                )
            blob = self.cache.fetch_url(source.path.as_uri(), digest, expected_size=source.path.stat().st_size)
            origin = source.path.as_uri()
            filename = source.path.name
        else:
            assert source.url is not None
            blob, observed = self._fetch_source(source)
            digest = observed.sha256 or hashlib.sha256(blob.read_bytes()).hexdigest()
            origin = observed.final_url or observed.url or source.url
            filename = Path(urlsplit(source.url).path).name
            stem = Path(filename).stem
        if module is None:
            if not stem.isidentifier():
                raise NoImportModulesError(
                    "The single-file name is not a valid Python import identifier",
                    request=source.original,
                    remediation="pass module= with a valid top-level import name",
                )
            module = stem
        if "." in module:
            raise ResolutionError(
                "A single-file artifact must be a top-level module",
                request=source.original,
                module=module,
                remediation="package related files as a wheel",
            )
        distribution = str(canonicalize_name(source.distribution or module))
        version = f"0+{digest[:12]}"
        self._validate_constraint(distribution, version, source)
        artifact = Artifact(
            id=f"sha256:{digest}",
            distribution=distribution,
            version=version,
            url=redact(origin),
            filename=filename,
            size=blob.stat().st_size,
            sha256=digest,
            source_kind=source.kind,
            final_url=redact(origin),
            local_source_hash=digest if source.path else "",
            source_url=redact(source.url or (source.path.as_uri() if source.path else "")),
            source_final_url=redact(source.final_url or ""),
            source_sha256=digest,
            source_size=blob.stat().st_size,
        )
        self._artifacts.setdefault(artifact.id, artifact)
        node = Node(
            id=_node_id(path, artifact.id),
            artifact=artifact.id,
            distribution=artifact.distribution,
            version=artifact.version,
            provided_modules=(module,),
            public_modules=(module,),
            all_importable_modules=(module,),
        )
        self._nodes[node.id] = node
        return node

    def _resolve_candidate(
        self,
        candidate: _Candidate,
        *,
        extras: tuple[str, ...],
        path: str,
        ancestors: dict[str, Node],
        prefer_newest: bool,
    ) -> Node:
        if candidate.installed_artifact is not None:
            self.progress.emit("inspect", f"{candidate.distribution}=={candidate.version} installed metadata")
            inspection = self._inspect_installed(candidate)
            final_url = candidate.installed_artifact.final_url or candidate.url
        elif self.cache.has_package(candidate.sha256):
            self._ensure_candidate_size(candidate)
            blob = self.cache.blob_path(candidate.sha256)
            final_url = candidate.url
            self.progress.emit("inspect", f"{candidate.distribution}=={candidate.version} stored metadata")
            inspection = self._inspect_artifact(blob, candidate)
        else:
            with self.cache._artifact_lock(candidate.sha256):
                if not self.cache.has_blob(candidate.sha256):
                    self.progress.emit("acquire", f"{candidate.distribution}=={candidate.version}")
                blob, final_url = self.cache.fetch_url_with_final(
                    candidate.url,
                    candidate.sha256,
                    expected_size=candidate.size or None,
                    allowed_hosts=self._allowed_hosts,
                    allow_insecure=self._allow_insecure,
                    _lock_held=True,
                )
                candidate.size = blob.stat().st_size
                self.progress.emit("inspect", f"{candidate.distribution}=={candidate.version} artifact metadata")
                inspection = self._inspect_artifact(blob, candidate)
        if inspection.distribution != candidate.distribution or not _versions_equivalent(
            inspection.version, candidate.version
        ):
            raise ResolutionError(
                "Repository record and downloaded wheel metadata disagree",
                artifact_hash=candidate.sha256,
                remediation=(
                    f"record={candidate.distribution} {candidate.version}; "
                    f"wheel={inspection.distribution} {inspection.version}"
                ),
            )
        self._validate_constraint(candidate.distribution, candidate.version, candidate.source)
        source = candidate.source
        artifact = candidate.installed_artifact or Artifact(
            id=f"sha256:{candidate.sha256}",
            distribution=candidate.distribution,
            version=candidate.version,
            url=redact(candidate.url),
            filename=candidate.filename,
            size=candidate.size,
            sha256=candidate.sha256,
            python_tag=inspection.python_tag,
            abi_tag=inspection.abi_tag,
            platform_tag=inspection.platform_tag,
            build_tag=inspection.build_tag,
            requires_python=inspection.requires_python,
            yanked=candidate.yanked,
            yanked_reason=candidate.yanked_reason,
            source_kind=source.kind if source else "pypi",
            final_url=redact((source.final_url or final_url) if source else final_url),
            vcs_repository=redact(source.url or "") if source and source.kind == "git" else "",
            vcs_commit=(source.commit or "") if source else "",
            requested_ref=(source.requested_ref or "") if source else "",
            subdirectory=(source.subdirectory or "") if source else "",
            local_source_hash=hash_local_source(source.path) if source and source.path and source.path.exists() else "",
            build_backend="uv" if candidate.built else "",
            source_url=redact(
                candidate.source_url
                or (source.url if source and source.url else source.path.as_uri() if source and source.path else "")
            ),
            source_final_url=redact(candidate.source_final_url or (source.final_url if source else "") or ""),
            source_sha256=candidate.source_sha256 or (source.sha256 if source and source.sha256 else ""),
            source_size=candidate.source_size,
        )
        self._artifacts.setdefault(artifact.id, artifact)
        provisional = Node(
            id=_node_id(path, artifact.id),
            artifact=artifact.id,
            distribution=artifact.distribution,
            version=artifact.version,
            extras=extras,
            provided_modules=inspection.provided_modules,
            public_modules=inspection.public_modules,
            private_modules=inspection.private_modules,
            all_importable_modules=inspection.all_importable_modules,
            namespace_contributions=inspection.namespace_contributions,
            native_classification=inspection.native_classification,
        )
        self._nodes[provisional.id] = provisional
        lineage = dict(ancestors)
        lineage[artifact.distribution] = provisional
        grouped: dict[str, list[Requirement]] = {}
        evaluated: list[str] = []
        marker_environment = {key: str(value) for key, value in default_environment().items()}
        for raw in inspection.requires_dist:
            requirement = Requirement(raw)
            active_extras = ("", *extras)
            if requirement.marker is not None and not any(
                requirement.marker.evaluate({**marker_environment, "extra": extra}) for extra in active_extras
            ):
                continue
            dependency_name = str(canonicalize_name(requirement.name))
            grouped.setdefault(dependency_name, []).append(requirement)
            evaluated.append(raw)
        dependencies: dict[str, str] = {}
        for name, requirements in sorted(grouped.items()):
            constraints = self._constrained_specifier(
                name,
                SpecifierSet(",".join(str(req.specifier) for req in requirements if str(req.specifier))),
            )
            ancestor = lineage.get(name)
            if ancestor is not None and Version(ancestor.version) in constraints:
                dependencies[name] = ancestor.id
                continue
            selected_extras = tuple(sorted({extra for req in requirements for extra in req.extras}))
            planned = self._planned_version(name, constraints)
            selection_constraint = SpecifierSet(f"=={planned}") if planned is not None else constraints
            child_candidate = self._select_installed(name, selection_constraint) if not prefer_newest else None
            if child_candidate is None:
                child_candidate = self._select_pypi(
                    name,
                    selection_constraint,
                    prefer_newest=prefer_newest,
                )
            child_candidate.source = SourceInfo(
                original=str(requirements[0]),
                normalized=f"{name}{constraints}",
                kind="pypi",
                distribution=name,
                requirement=f"{name}{constraints}",
                extras=selected_extras,
            )
            child_candidate = self._prepare_selected_candidate(child_candidate)
            child = self._resolve_candidate(
                child_candidate,
                extras=selected_extras,
                path=f"{path}/{name}",
                ancestors=lineage,
                prefer_newest=prefer_newest,
            )
            dependencies[name] = child.id
        node = replace(provisional, dependencies=dependencies, evaluated_markers=tuple(sorted(evaluated)))
        self._nodes[node.id] = node
        return node

    def _planned_version(self, distribution: str, constraint: SpecifierSet) -> str | None:
        """Return the active cohort's exact compatible version, if one exists."""
        planned = self._active_plan.get(distribution)
        if planned is None:
            return None
        try:
            compatible = Version(planned) in constraint
        except InvalidVersion as exc:
            raise ResolutionError(
                "Bulk resolution plan contains an invalid version",
                request=f"{distribution}=={planned}",
            ) from exc
        if not compatible:
            raise ResolutionError(
                "Bulk resolution plan conflicts with inspected dependency metadata",
                request=f"{distribution}{constraint}",
                remediation=f"planned {distribution}=={planned}",
            )
        return planned

    def _select_installed(
        self,
        distribution: str,
        constraint: SpecifierSet,
        *,
        emit_progress: bool = True,
    ) -> _Candidate | None:
        """Select a complete compatible installed artifact without consulting an index."""
        normalized = str(canonicalize_name(distribution))
        compatible: list[tuple[Version, str, _Candidate]] = []
        artifact_fields = {item.name for item in fields(Artifact)}
        current_python = Version(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
        for package in self._installed_packages():
            if package.distribution != normalized:
                continue
            try:
                version = Version(package.version)
            except InvalidVersion:
                continue
            if version not in constraint:
                continue
            metadata_path = self.cache.root / "metadata" / "packages" / f"{package.artifact_hash}.json"
            imports_path = self.cache.root / "metadata" / "imports" / f"{package.artifact_hash}.json"
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                inspection = json.loads(imports_path.read_text(encoding="utf-8"))
                filename = str(metadata["filename"])
                requires_python = _normalize_requires_python(str(inspection.get("requires_python") or ""))
                if requires_python and current_python not in SpecifierSet(requires_python):
                    continue
                if filename.endswith(".whl"):
                    _name, _version, _build, tags = parse_wheel_filename(filename)
                    if not tags & set(sys_tags()):
                        continue
                raw_artifact = metadata.get("artifact")
                if not isinstance(raw_artifact, dict):
                    # Legacy entries become reusable after any exact graph
                    # synchronizes them through Cache.record_artifact. Until
                    # then, their source identity is insufficient for safe
                    # reacquisition after target eviction.
                    continue
                values = {key: value for key, value in raw_artifact.items() if key in artifact_fields}
                artifact = Artifact(**values)
                if (
                    artifact.id != f"sha256:{package.artifact_hash}"
                    or artifact.sha256 != package.artifact_hash
                    or str(canonicalize_name(artifact.distribution)) != normalized
                    or not _versions_equivalent(artifact.version, package.version)
                    or artifact.filename != filename
                    or not isinstance(artifact.size, int)
                    or isinstance(artifact.size, bool)
                    or artifact.size < 0
                ):
                    continue
            except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
            candidate = _Candidate(
                normalized,
                package.version,
                artifact.url,
                artifact.filename,
                artifact.size,
                artifact.sha256,
                requires_python=artifact.requires_python,
                installed_artifact=artifact,
            )
            compatible.append((version, package.artifact_hash, candidate))
        if not compatible:
            return None
        compatible.sort(key=lambda item: (item[0], item[1]), reverse=True)
        selected = compatible[0][2]
        if emit_progress:
            self.progress.emit("reuse", f"{selected.distribution}=={selected.version} from shared store")
        return selected

    def _installed_packages(self) -> tuple[CachedPackage, ...]:
        """Return one verified installed-inventory snapshot for this resolution."""
        if self._installed_inventory is None:
            self._installed_inventory = self.cache.list_packages()
        return self._installed_inventory

    def _inspect_installed(self, candidate: _Candidate) -> WheelInspection:
        metadata_path = self.cache.root / "metadata" / "imports" / f"{candidate.sha256}.json"
        try:
            raw = json.loads(metadata_path.read_text(encoding="utf-8"))
            return WheelInspection(
                distribution=raw["distribution"],
                version=raw["version"],
                build_tag=raw["build_tag"],
                python_tag=raw["python_tag"],
                abi_tag=raw["abi_tag"],
                platform_tag=raw["platform_tag"],
                requires_python=raw["requires_python"],
                requires_dist=tuple(raw["requires_dist"]),
                provided_modules=tuple(raw["provided_modules"]),
                public_modules=tuple(raw["public_modules"]),
                private_modules=tuple(raw["private_modules"]),
                all_importable_modules=tuple(raw["all_importable_modules"]),
                namespace_contributions=tuple(raw["namespace_contributions"]),
                native_classification=raw["native_classification"],
                metadata_dir=raw["metadata_dir"],
            )
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ResolutionError(
                "Installed artifact metadata is incomplete",
                artifact_hash=candidate.sha256,
                remediation="repair the artifact from an exact manifest or refresh the request",
            ) from exc

    def _parse_constraints(self, value: object) -> dict[str, SpecifierSet]:
        raw_constraints = _policy_strings(value)
        grouped: dict[str, list[str]] = {}
        for raw in raw_constraints:
            try:
                requirement = Requirement(raw)
            except InvalidRequirement as exc:
                raise ResolutionError("Invalid package constraint", request=raw, remediation=str(exc)) from exc
            if requirement.url or requirement.extras or requirement.marker:
                raise ResolutionError(
                    "Package constraints must contain only a distribution name and version specifier",
                    request=raw,
                )
            distribution = str(canonicalize_name(requirement.name))
            grouped.setdefault(distribution, []).append(str(requirement.specifier))
        return {
            distribution: SpecifierSet(",".join(item for item in specifiers if item))
            for distribution, specifiers in grouped.items()
        }

    def _constrained_specifier(self, distribution: str, specifier: SpecifierSet) -> SpecifierSet:
        constrained = self._constraints.get(str(canonicalize_name(distribution)))
        if constrained is None:
            return specifier
        values = [value for value in (str(specifier), str(constrained)) if value]
        return SpecifierSet(",".join(values))

    def _validate_constraint(self, distribution: str, version: str, source: SourceInfo | None) -> None:
        constraint = self._constraints.get(str(canonicalize_name(distribution)))
        if constraint is not None and Version(version) not in constraint:
            raise ResolutionError(
                "Selected source does not satisfy the requirements constraint",
                request=source.original if source else distribution,
                candidates=(f"{distribution}=={version}",),
                remediation=f"select a source version matching {distribution}{constraint}",
            )

    def _inspect_artifact(self, blob: Path, candidate: _Candidate) -> WheelInspection:
        metadata_path = self.cache.root / "metadata" / "imports" / f"{candidate.sha256}.json"
        try:
            raw = json.loads(metadata_path.read_text(encoding="utf-8"))
            if raw.get("format_version") != 1 or raw.get("filename") != candidate.filename:
                raise ValueError("metadata cache version/filename mismatch")
            inspection = WheelInspection(
                distribution=raw["distribution"],
                version=raw["version"],
                build_tag=raw["build_tag"],
                python_tag=raw["python_tag"],
                abi_tag=raw["abi_tag"],
                platform_tag=raw["platform_tag"],
                requires_python=raw["requires_python"],
                requires_dist=tuple(raw["requires_dist"]),
                provided_modules=tuple(raw["provided_modules"]),
                public_modules=tuple(raw["public_modules"]),
                private_modules=tuple(raw["private_modules"]),
                all_importable_modules=tuple(raw["all_importable_modules"]),
                namespace_contributions=tuple(raw["namespace_contributions"]),
                native_classification=raw["native_classification"],
                metadata_dir=raw["metadata_dir"],
            )
            names = (
                *inspection.provided_modules,
                *inspection.public_modules,
                *inspection.private_modules,
                *inspection.all_importable_modules,
                *inspection.namespace_contributions,
            )
            if any(
                not isinstance(name, str) or not name or not all(part.isidentifier() for part in name.split("."))
                for name in names
            ):
                raise ValueError("invalid import name in metadata cache")
            normalized_requires_python = _normalize_requires_python(inspection.requires_python)
            if normalized_requires_python != inspection.requires_python:
                raise ValueError("noncanonical Requires-Python in metadata cache")
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            inspection = inspect_wheel(blob, filename=candidate.filename)
            inspection = replace(
                inspection,
                requires_python=_normalize_requires_python(inspection.requires_python),
            )
            metadata_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "format_version": 1,
                "filename": candidate.filename,
                **{name: getattr(inspection, name) for name in inspection.__dataclass_fields__},
            }
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=metadata_path.name + ".",
                dir=metadata_path.parent,
            )
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    handle.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
                os.replace(temporary_name, metadata_path)
            finally:
                Path(temporary_name).unlink(missing_ok=True)
        return inspection

    def _candidate_from_local_wheel(self, path: Path, source: SourceInfo) -> _Candidate:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if source.sha256 and source.sha256 != digest:
            raise HashMismatchError(
                "Local wheel hash mismatch",
                request=source.original,
                artifact_hash=digest,
            )
        blob = self.cache.fetch_url(path.resolve().as_uri(), digest, expected_size=path.stat().st_size)
        observed = replace(source, sha256=digest, final_url=path.resolve().as_uri())
        return self._candidate_from_blob(blob, path.name, observed)

    def _candidate_from_blob(self, blob: Path, filename: str, source: SourceInfo) -> _Candidate:
        try:
            distribution, version, _build, tags = parse_wheel_filename(filename)
        except Exception as exc:
            raise ResolutionError("Invalid wheel filename", request=source.original, remediation=str(exc)) from exc
        if not tags & set(sys_tags()):
            raise ResolutionError(
                "Wheel is incompatible with this interpreter",
                request=source.original,
                candidates=tuple(str(tag) for tag in sorted(tags, key=str)),
            )
        digest = source.sha256 or hashlib.sha256(blob.read_bytes()).hexdigest()
        if source.path is not None and source.path.suffix.lower() == ".whl":
            artifact_url = source.path.resolve().as_uri()
        else:
            artifact_url = source.final_url or source.url or blob.as_uri()
        return _Candidate(
            str(canonicalize_name(str(distribution))),
            str(version),
            artifact_url,
            filename,
            blob.stat().st_size,
            digest,
            source=source,
        )

    def _build_source_candidate(self, source_path: Path, source: SourceInfo) -> _Candidate:
        self._uv_version = self.backend.version()
        temporary_root = self.cache.root / "tmp"
        temporary_root.mkdir(parents=True, exist_ok=True)
        output = Path(tempfile.mkdtemp(prefix=f"depfix-build-{os.getpid()}-", dir=temporary_root))
        extracted: Path | None = None
        try:
            source_url = source.url or (source.path.as_uri() if source.path is not None else "")
            source_final_url = source.final_url or source_url
            source_digest = source.sha256 or hash_local_source(source_path)
            source_size = source_path.stat().st_size if source_path.is_file() else 0
            build_root = source_path
            if source_path.is_file():
                extracted = Path(tempfile.mkdtemp(prefix=f"depfix-source-{os.getpid()}-", dir=temporary_root))
                _extract_source_archive(source_path, extracted)
                build_root = _source_project_root(extracted)
            wheel = self.backend.build_wheel(build_root, output=output)
            digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
            permanent = self.cache.root / "built-wheels" / digest / wheel.name
            permanent.parent.mkdir(parents=True, exist_ok=True)
            if not permanent.exists():
                shutil.copy2(wheel, permanent)
            candidate = self._candidate_from_local_wheel(
                permanent,
                replace(source, final_url=permanent.as_uri(), sha256=None),
            )
            candidate.built = True
            candidate.source_url = source_url
            candidate.source_final_url = source_final_url
            candidate.source_sha256 = source_digest
            candidate.source_size = source_size
            return candidate
        finally:
            shutil.rmtree(output, ignore_errors=True)
            if extracted is not None:
                shutil.rmtree(extracted, ignore_errors=True)

    def _prepare_selected_candidate(self, candidate: _Candidate) -> _Candidate:
        if not candidate.sha256:
            blob, digest, final_url = self.cache.fetch_unpinned(
                candidate.url,
                allowed_hosts=self._allowed_hosts,
                allow_insecure=self._allow_insecure,
            )
            candidate.sha256 = digest
            candidate.size = blob.stat().st_size
            source = candidate.source
            if source is not None:
                candidate.source = replace(
                    source,
                    url=candidate.url,
                    final_url=final_url,
                    sha256=digest,
                    mutable=False,
                )
        if candidate.filename.endswith(".whl"):
            return candidate
        if not bool(self._policy.get("allow-build", True)):
            raise ResolutionError(
                "The selected release requires a source build that policy forbids",
                request=f"{candidate.distribution}=={candidate.version}",
                remediation="enable controlled builds during live/export or require a wheel-only release",
            )
        source = candidate.source or SourceInfo(
            original=f"{candidate.distribution}=={candidate.version}",
            normalized=f"{candidate.distribution}=={candidate.version}",
            kind="pypi",
            distribution=candidate.distribution,
        )
        blob, final_url = self.cache.fetch_url_with_final(
            candidate.url,
            candidate.sha256,
            expected_size=candidate.size or None,
            allowed_hosts=self._allowed_hosts,
            allow_insecure=self._allow_insecure,
        )
        candidate.size = blob.stat().st_size
        observed = replace(
            source,
            url=candidate.url,
            final_url=final_url,
            sha256=candidate.sha256,
            mutable=False,
        )
        return self._build_source_candidate(blob, observed)

    def _ensure_candidate_size(self, candidate: _Candidate) -> None:
        if candidate.size > 0:
            return
        blob = self.cache.blob_path(candidate.sha256)
        if blob.is_file():
            candidate.size = blob.stat().st_size
            return
        metadata_path = self.cache.root / "metadata" / "packages" / f"{candidate.sha256}.json"
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            artifact = metadata["artifact"]
            recorded_size = artifact["size"]
            if (
                not isinstance(artifact, dict)
                or artifact.get("sha256") != candidate.sha256
                or not isinstance(recorded_size, int)
                or isinstance(recorded_size, bool)
                or recorded_size <= 0
            ):
                raise ValueError("invalid cached artifact size")
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            blob, _final_url = self.cache.fetch_url_with_final(
                candidate.url,
                candidate.sha256,
                expected_size=None,
                allowed_hosts=self._allowed_hosts,
                allow_insecure=self._allow_insecure,
            )
            candidate.size = blob.stat().st_size
        else:
            candidate.size = recorded_size

    def _build_git(self, source: SourceInfo) -> tuple[Path, SourceInfo]:
        assert source.url is not None
        root = self.cache.root / "tmp"
        root.mkdir(parents=True, exist_ok=True)
        checkout = Path(tempfile.mkdtemp(prefix=f"git-{os.getpid()}-", dir=root))
        output = Path(tempfile.mkdtemp(prefix=f"git-wheel-{os.getpid()}-", dir=root))
        try:
            clone = subprocess.run(
                ["git", "clone", "--no-checkout", source.url, str(checkout)],
                text=True,
                capture_output=True,
                check=False,
            )
            if clone.returncode != 0:
                raise SourceError("Git clone failed", request=source.original, rejections=(clone.stderr.strip(),))
            ref = source.requested_ref or "HEAD"
            checkout_result = subprocess.run(
                ["git", "-C", str(checkout), "checkout", "--detach", ref],
                text=True,
                capture_output=True,
                check=False,
            )
            if checkout_result.returncode != 0:
                raise SourceError(
                    "Git ref checkout failed", request=source.original, rejections=(checkout_result.stderr.strip(),)
                )
            commit_result = subprocess.run(
                ["git", "-C", str(checkout), "rev-parse", "HEAD"],
                text=True,
                capture_output=True,
                check=True,
            )
            commit = commit_result.stdout.strip().lower()
            build_root = checkout / source.subdirectory if source.subdirectory else checkout
            self._uv_version = self.backend.version()
            wheel = self.backend.build_wheel(build_root, output=output)
            permanent = self.cache.root / "built-wheels" / hashlib.sha256(wheel.read_bytes()).hexdigest() / wheel.name
            permanent.parent.mkdir(parents=True, exist_ok=True)
            if not permanent.exists():
                shutil.copy2(wheel, permanent)
            return permanent, replace(
                source,
                commit=commit,
                mutable=False,
                final_url=permanent.as_uri(),
            )
        finally:
            shutil.rmtree(checkout, ignore_errors=True)
            shutil.rmtree(output, ignore_errors=True)

    def _fetch_source(self, source: SourceInfo) -> tuple[Path, SourceInfo]:
        assert source.url is not None
        if source.sha256:
            blob, final_url = self.cache.fetch_url_with_final(
                source.url,
                source.sha256,
                allowed_hosts=self._allowed_hosts,
                allow_insecure=self._allow_insecure,
            )
            return blob, replace(source, final_url=final_url)
        if self.settings.frozen:
            raise SourceError(
                "Frozen remote artifacts require an exact SHA-256",
                request=source.original,
                frozen=True,
                remediation="add #sha256=<digest> and export again",
            )
        blob, digest, final_url = self.cache.fetch_unpinned(
            source.url,
            allowed_hosts=self._allowed_hosts,
            allow_insecure=self._allow_insecure,
        )
        return blob, replace(source, sha256=digest, final_url=final_url, mutable=False)

    def _validate_index_policy(self) -> None:
        allowed_indexes = {_index_identity(value) for value in _policy_strings(self._policy.get("allowed-indexes"))}
        for index in self._project_indexes:
            identity = _index_identity(index)
            if allowed_indexes and identity not in allowed_indexes:
                raise ResolutionError("Package index is not permitted by policy", source=identity)
            split = urlsplit(index)
            if split.scheme == "file":
                continue
            if split.scheme != "https" and not (self._allow_insecure and split.scheme == "http"):
                raise ResolutionError(
                    "Package indexes require HTTPS",
                    source=identity,
                    remediation="use HTTPS or set allow-insecure-transport only for a controlled development index",
                )
            host = (split.hostname or "").lower().rstrip(".")
            if self._allowed_hosts and not any(_host_matches(host, value) for value in self._allowed_hosts):
                raise ResolutionError("Package index host is not permitted by policy", source=identity)

    def _validate_source_policy(self, source: SourceInfo) -> None:
        if source.url is None:
            return
        clean = source.url.removeprefix("git+")
        split = urlsplit(clean)
        if split.scheme in {"http", "https"}:
            if split.scheme != "https" and not self._allow_insecure:
                raise SourceError(
                    "Remote sources require HTTPS",
                    request=source.original,
                    source=redact(source.url),
                )
            host = (split.hostname or "").lower().rstrip(".")
        elif split.scheme == "ssh":
            host = (split.hostname or "").lower().rstrip(".")
        elif split.scheme == "git":
            if not self._allow_insecure:
                raise SourceError(
                    "The unauthenticated git transport is disabled by policy",
                    request=source.original,
                    source=redact(source.url),
                )
            host = (split.hostname or "").lower().rstrip(".")
        elif split.scheme:
            return
        else:
            match = re.match(r"^[^@\s]+@([^:\s]+):", clean)
            host = match.group(1).lower().rstrip(".") if match else ""
        if self._allowed_hosts and not any(_host_matches(host, value) for value in self._allowed_hosts):
            raise SourceError(
                "Source host is not permitted by policy",
                request=source.original,
                source=redact(source.url),
            )

    def _select_pypi(
        self,
        distribution: str,
        constraint: SpecifierSet,
        *,
        prefer_newest: bool,
    ) -> _Candidate:
        key = (distribution, str(constraint), prefer_newest)
        if key in self._candidate_cache:
            return self._candidate_cache[key]
        payload = self._project_artifact_payload(distribution, constraint)
        tag_rank = {tag: index for index, tag in enumerate(sys_tags())}
        candidates: list[tuple[int, Version, int, int, int, _Candidate]] = []
        rejections: list[str] = []
        exact_pin = any(
            specifier.operator in {"==", "==="} and not specifier.version.endswith(".*") for specifier in constraint
        )
        for raw_version, files in payload.get("releases", {}).items():
            try:
                version = Version(raw_version)
            except InvalidVersion:
                continue
            if version not in constraint:
                continue
            for item in files:
                filename = item.get("filename", "")
                if item.get("yanked", False) and not self.allow_yanked and not exact_pin:
                    rejections.append(f"{filename}: yanked ({item.get('yanked_reason') or 'no reason'})")
                    continue
                requires_python = _normalize_requires_python(str(item.get("requires_python") or ""))
                current_python = Version(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
                if requires_python and current_python not in SpecifierSet(requires_python):
                    rejections.append(f"{filename}: Requires-Python {requires_python}")
                    continue
                digest = item.get("digests", {}).get("sha256")
                if digest is not None and not re.fullmatch(r"[0-9a-fA-F]{64}", str(digest)):
                    rejections.append(f"{filename}: malformed SHA-256")
                    continue
                candidate = _Candidate(
                    str(canonicalize_name(distribution)),
                    raw_version,
                    item["url"],
                    filename,
                    int(item["size"]),
                    str(digest).lower() if digest is not None else "",
                    requires_python,
                    bool(item.get("yanked", False)),
                    item.get("yanked_reason") or "",
                )
                cached = int(
                    bool(candidate.sha256)
                    and (self.cache.has_package(candidate.sha256) or f"sha256:{candidate.sha256}" in self._artifacts)
                )
                if item.get("packagetype") == "bdist_wheel" and filename.endswith(".whl"):
                    try:
                        _name, _version, _build, wheel_tags = parse_wheel_filename(filename)
                    except Exception:
                        continue
                    ranks = [tag_rank[tag] for tag in wheel_tags if tag in tag_rank]
                    if not ranks:
                        rejections.append(f"{filename}: incompatible wheel tags")
                        continue
                    pure_python = int(all(tag.abi == "none" and tag.platform == "any" for tag in wheel_tags))
                    candidates.append((cached, version, 1, pure_python, -min(ranks), candidate))
                    continue
                if item.get("packagetype") == "sdist" and bool(self._policy.get("allow-build", True)):
                    try:
                        _sdist_name, sdist_version = parse_sdist_filename(filename)
                    except Exception:
                        continue
                    if sdist_version == version:
                        candidates.append((cached, version, 0, 0, 0, candidate))
        if not candidates:
            raise ResolutionError(
                "No compatible artifact satisfies the dependency edge",
                request=f"{distribution}{constraint}",
                rejections=tuple(rejections[-20:]),
                remediation="allow a controlled source build or select a compatible wheel target",
            )
        if prefer_newest:
            candidates.sort(
                key=lambda entry: (entry[1], entry[2], entry[3], entry[4], entry[5].filename),
                reverse=True,
            )
        else:
            candidates.sort(
                key=lambda entry: (entry[0], entry[1], entry[2], entry[3], entry[4], entry[5].filename),
                reverse=True,
            )
        selected = candidates[0][5]
        self._candidate_cache[key] = selected
        return selected

    def _project_artifact_payload(
        self,
        distribution: str,
        constraint: SpecifierSet,
    ) -> dict[str, Any]:
        errors: list[str] = []
        indexes = self._project_indexes if self._custom_index else (self.index_url,)
        for index in indexes:
            if index == "https://pypi.org/pypi" and not self._custom_index:
                continue
            simple_url = f"{index.rstrip('/')}/{distribution}/"
            request = urllib.request.Request(
                simple_url,
                headers={
                    "Accept": _SIMPLE_ACCEPT,
                    "User-Agent": "depfix/0.1",
                },
            )
            try:
                with _open_url(
                    request,
                    timeout=self.cache.timeout,
                    allowed_hosts=self._allowed_hosts,
                    allow_insecure=self._allow_insecure,
                ) as response:
                    media_type = self._response_media_type(response)
                    final_url = response.geturl()
                    if media_type == _SIMPLE_JSON:
                        simple = json.load(response)
                        files = simple.get("files", []) if isinstance(simple, dict) else []
                    elif media_type in _SIMPLE_HTML:
                        parser = _SimpleHTMLParser()
                        parser.feed(response.read().decode("utf-8"))
                        parser.close()
                        files = parser.files
                    else:
                        raise ResolutionError(
                            "Package index returned an unsupported Simple API media type",
                            source=redact(final_url),
                            rejections=(f"received {media_type or 'missing Content-Type'}",),
                        )
                releases = self._simple_releases(files, final_url)
                if releases:
                    return {"releases": releases}
                errors.append(f"Simple API {redact(final_url)}: response contained no supported artifacts")
            except Exception as exc:
                errors.append(f"Simple API {redact(simple_url)}: {redact(str(exc))}")
        for index in indexes:
            legacy_url = f"{index.rstrip('/')}/{distribution}/json"
            request = urllib.request.Request(
                legacy_url,
                headers={"Accept": "application/json", "User-Agent": "depfix/0.1"},
            )
            try:
                with _open_url(
                    request,
                    timeout=self.cache.timeout,
                    allowed_hosts=self._allowed_hosts,
                    allow_insecure=self._allow_insecure,
                ) as response:
                    payload = json.load(response)
                if isinstance(payload, dict) and payload.get("releases"):
                    return payload
                errors.append(f"JSON API {redact(legacy_url)}: response contained no releases")
            except Exception as exc:
                errors.append(f"JSON API {redact(legacy_url)}: {redact(str(exc))}")
        raise ResolutionError(
            "Unable to query package artifact metadata",
            request=f"{distribution}{constraint}",
            source=_index_policy_identity(indexes),
            rejections=tuple(errors),
            remediation="verify the PyPI-compatible index URL and its PEP 691 or project JSON support",
        )

    @staticmethod
    def _response_media_type(response: Any) -> str:
        content_type = response.headers.get("Content-Type", "")
        return str(content_type).partition(";")[0].strip().lower()

    def _simple_releases(self, files: Any, base_url: str) -> dict[str, list[dict[str, object]]]:
        releases: dict[str, list[dict[str, object]]] = {}
        if not isinstance(files, list):
            return releases
        for item in files:
            if not isinstance(item, dict):
                continue
            file_url = urljoin(base_url, str(item.get("url", "")))
            file_url, fragment = urldefrag(file_url)
            filename = str(item.get("filename") or unquote(PurePosixPath(urlsplit(file_url).path).name))
            try:
                if filename.endswith(".whl"):
                    _name, version, _build, _tags = parse_wheel_filename(filename)
                    package_type = "bdist_wheel"
                else:
                    _name, version = parse_sdist_filename(filename)
                    package_type = "sdist"
            except Exception:
                continue
            raw_hashes = item.get("hashes", {})
            hashes = dict(raw_hashes) if isinstance(raw_hashes, dict) else {}
            if "sha256" not in hashes:
                for field in fragment.split("&"):
                    algorithm, separator, digest = field.partition("=")
                    if separator and algorithm.lower() == "sha256":
                        hashes["sha256"] = digest
                        break
            size = item.get("size")
            yanked_value = item.get("yanked", False)
            yanked_reason = item.get("yanked-reason")
            if yanked_reason is None and isinstance(yanked_value, str):
                yanked_reason = yanked_value
            releases.setdefault(str(version), []).append(
                {
                    "filename": filename,
                    "packagetype": package_type,
                    "url": file_url,
                    "size": int(size) if size is not None else 0,
                    "digests": hashes,
                    "requires_python": item.get("requires-python") or "",
                    "yanked": bool(yanked_value),
                    "yanked_reason": str(yanked_reason or ""),
                }
            )
        return releases


def _node_id(path: str, artifact_id: str) -> str:
    return "node_" + hashlib.sha256(f"{path}\0{artifact_id}".encode()).hexdigest()[:24]


def _normalize_requires_python(value: str) -> str:
    """Canonicalize safely inferable comparison wildcards in Requires-Python."""
    try:
        SpecifierSet(value)
    except InvalidSpecifier as original_error:
        normalized: list[str] = []
        changed = False
        for raw_part in value.split(","):
            part = raw_part.strip()
            match = _WILDCARD_COMPARISON.fullmatch(part)
            if match is None:
                normalized.append(part)
                continue
            operator = match.group("operator")
            release = [int(component) for component in match.group("release").split(".")]
            if operator in {">", "<="}:
                release[-1] += 1
            boundary = ".".join(str(component) for component in release)
            normalized.append((">=" if operator == ">" else "<" if operator == "<=" else operator) + boundary)
            changed = True
        if not changed:
            raise original_error
        return str(SpecifierSet(",".join(normalized)))
    return value


def _versions_equivalent(left: str, right: str) -> bool:
    try:
        return Version(left) == Version(right)
    except InvalidVersion:
        return left == right


def _index_identity(value: str) -> str:
    split = urlsplit(redact(value))
    host = split.hostname or ""
    port = f":{split.port}" if split.port else ""
    return f"{split.scheme}://{host}{port}{split.path}".rstrip("/")


def _index_policy_identity(values: tuple[str, ...]) -> str:
    return "first-index:" + ",".join(_index_identity(value) for value in values)


def _policy_strings(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (tuple, list)) and all(isinstance(item, str) for item in value):
        return tuple(value)
    raise ResolutionError("Policy values must be strings or arrays of strings")


@dataclass(frozen=True)
class _SourceArchiveEntry:
    path: PurePosixPath
    kind: str
    size: int = 0
    link_target: PurePosixPath | None = None
    executable: bool = False


def _extract_source_archive(path: Path, destination: Path) -> None:
    max_files = _SOURCE_ARCHIVE_MAX_FILES
    max_size = _SOURCE_ARCHIVE_MAX_SIZE
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if len(infos) > max_files or sum(item.file_size for item in infos) > max_size:
                raise SourceError("Source archive exceeds extraction safety limits", source=str(path))
            entries: list[_SourceArchiveEntry] = []
            info_by_path: dict[PurePosixPath, zipfile.ZipInfo] = {}
            seen_paths: set[str] = set()
            for info in infos:
                raw_name = info.orig_filename
                relative = _safe_archive_path(raw_name, path)
                _reject_archive_path_collision(relative, raw_name, seen_paths)
                mode = info.external_attr >> 16
                if info.is_dir():
                    entry = _SourceArchiveEntry(relative, "directory")
                elif stat.S_IFMT(mode) == stat.S_IFLNK:
                    try:
                        linkname = archive.read(info).decode("utf-8")
                    except UnicodeDecodeError as exc:
                        raise SourceError("Source archive link target is not UTF-8", source=info.filename) from exc
                    entry = _SourceArchiveEntry(
                        relative,
                        "link",
                        link_target=_safe_link_target(relative, linkname, path, relative_to_parent=True),
                    )
                elif stat.S_IFMT(mode) in {0, stat.S_IFREG}:
                    entry = _SourceArchiveEntry(relative, "file", size=info.file_size, executable=bool(mode & 0o111))
                else:
                    raise SourceError("Source archives may not contain special files", source=info.filename)
                entries.append(entry)
                info_by_path[relative] = info
            resolved_links = _validate_source_archive_plan(entries, path, max_size=max_size)
            for entry in entries:
                if entry.kind == "directory":
                    destination.joinpath(*entry.path.parts).mkdir(parents=True, exist_ok=True)
            for entry in entries:
                if entry.kind != "file":
                    continue
                target = destination.joinpath(*entry.path.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info_by_path[entry.path]) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
                _make_archive_file_executable(target, entry)
            _materialize_archive_links(destination, entries, resolved_links)
        return
    try:
        tar_archive = tarfile.open(path, mode="r:*")
    except tarfile.TarError as exc:
        raise SourceError("Unsupported or malformed source archive", source=str(path)) from exc
    with tar_archive:
        members = tar_archive.getmembers()
        if len(members) > max_files:
            raise SourceError("Source archive exceeds extraction safety limits", source=str(path))
        entries = []
        member_by_path: dict[PurePosixPath, tarfile.TarInfo] = {}
        tar_seen_paths: set[str] = set()
        for member in members:
            relative = _safe_archive_path(member.name, path)
            _reject_archive_path_collision(relative, member.name, tar_seen_paths)
            if member.isdir():
                entry = _SourceArchiveEntry(relative, "directory")
            elif member.isfile():
                entry = _SourceArchiveEntry(
                    relative,
                    "file",
                    size=member.size,
                    executable=bool(member.mode & 0o111),
                )
            elif member.issym() or member.islnk():
                entry = _SourceArchiveEntry(
                    relative,
                    "link",
                    link_target=_safe_link_target(
                        relative,
                        member.linkname,
                        path,
                        relative_to_parent=member.issym(),
                    ),
                )
            else:
                raise SourceError("Source archives may not contain special files", source=member.name)
            entries.append(entry)
            member_by_path[relative] = member
        resolved_links = _validate_source_archive_plan(entries, path, max_size=max_size)
        for entry in entries:
            if entry.kind == "directory":
                destination.joinpath(*entry.path.parts).mkdir(parents=True, exist_ok=True)
        for entry in entries:
            if entry.kind != "file":
                continue
            target = destination.joinpath(*entry.path.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            tar_source = tar_archive.extractfile(member_by_path[entry.path])
            if tar_source is None:
                raise SourceError("Unable to read source archive member", source=entry.path.as_posix())
            with tar_source, target.open("wb") as output:
                shutil.copyfileobj(tar_source, output, length=1024 * 1024)
            _make_archive_file_executable(target, entry)
        _materialize_archive_links(destination, entries, resolved_links)


def _safe_archive_path(name: str, archive: Path) -> PurePosixPath:
    relative = PurePosixPath(name)
    if (
        not name
        or "\x00" in name
        or "\\" in name
        or _WINDOWS_DRIVE_PATH.match(name)
        or relative.is_absolute()
        or not relative.parts
        or ".." in relative.parts
    ):
        raise SourceError("Unsafe path in source archive", source=str(archive), remediation=name)
    return relative


def _safe_link_target(
    link_path: PurePosixPath,
    linkname: str,
    archive: Path,
    *,
    relative_to_parent: bool,
) -> PurePosixPath:
    raw_target = PurePosixPath(linkname)
    if (
        not linkname
        or "\x00" in linkname
        or "\\" in linkname
        or _WINDOWS_DRIVE_PATH.match(linkname)
        or raw_target.is_absolute()
    ):
        raise SourceError("Unsafe link target in source archive", source=str(archive), remediation=linkname)
    parts = list(link_path.parent.parts) if relative_to_parent else []
    for part in raw_target.parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if not parts:
                raise SourceError("Unsafe link target in source archive", source=str(archive), remediation=linkname)
            parts.pop()
            continue
        parts.append(part)
    if not parts:
        raise SourceError("Unsafe link target in source archive", source=str(archive), remediation=linkname)
    return PurePosixPath(*parts)


def _validate_source_archive_plan(
    entries: list[_SourceArchiveEntry],
    archive: Path,
    *,
    max_size: int,
) -> dict[PurePosixPath, PurePosixPath]:
    by_path = {entry.path: entry for entry in entries}
    namespace: dict[str, tuple[str, str]] = {}
    for entry in entries:
        for index in range(1, len(entry.path.parts) + 1):
            part_path = PurePosixPath(*entry.path.parts[:index]).as_posix()
            kind = entry.kind if index == len(entry.path.parts) else "directory"
            folded = part_path.casefold()
            previous = namespace.get(folded)
            if previous is not None and (previous[0] != part_path or previous[1] != kind):
                raise SourceError("Source archive contains a colliding path namespace", source=entry.path.as_posix())
            namespace[folded] = (part_path, kind)

    resolved: dict[PurePosixPath, PurePosixPath] = {}

    def resolve(link: _SourceArchiveEntry) -> PurePosixPath:
        existing = resolved.get(link.path)
        if existing is not None:
            return existing
        chain: list[_SourceArchiveEntry] = []
        chain_paths: set[PurePosixPath] = set()
        current = link
        while True:
            existing = resolved.get(current.path)
            if existing is not None:
                final = existing
                break
            if current.path in chain_paths:
                raise SourceError("Source archive contains a cyclic link", source=current.path.as_posix())
            chain.append(current)
            chain_paths.add(current.path)
            target_path = current.link_target
            target = by_path.get(target_path) if target_path is not None else None
            if target is None:
                raise SourceError("Source archive contains a dangling link", source=current.path.as_posix())
            if target.kind == "directory":
                raise SourceError("Source archive links may not target directories", source=current.path.as_posix())
            if target.kind == "file":
                final = target.path
                break
            current = target
        for item in chain:
            resolved[item.path] = final
        return final

    total_size = sum(entry.size for entry in entries if entry.kind == "file")
    for entry in entries:
        if entry.kind != "link":
            continue
        final = resolve(entry)
        total_size += by_path[final].size
        if total_size > max_size:
            raise SourceError("Source archive exceeds extraction safety limits", source=str(archive))
    if total_size > max_size:
        raise SourceError("Source archive exceeds extraction safety limits", source=str(archive))
    return resolved


def _materialize_archive_links(
    destination: Path,
    entries: list[_SourceArchiveEntry],
    resolved_links: dict[PurePosixPath, PurePosixPath],
) -> None:
    by_path = {entry.path: entry for entry in entries}
    for entry in entries:
        if entry.kind != "link":
            continue
        source = destination.joinpath(*resolved_links[entry.path].parts)
        target = destination.joinpath(*entry.path.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        with source.open("rb") as input_file, target.open("wb") as output:
            shutil.copyfileobj(input_file, output, length=1024 * 1024)
        _make_archive_file_executable(target, by_path[resolved_links[entry.path]])


def _make_archive_file_executable(path: Path, entry: _SourceArchiveEntry) -> None:
    if entry.executable:
        path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _reject_archive_path_collision(relative: PurePosixPath, original: str, seen_paths: set[str]) -> None:
    normalized = relative.as_posix().casefold()
    if normalized in seen_paths:
        raise SourceError(
            "Source archive contains duplicate or case-colliding paths",
            source=original,
        )
    seen_paths.add(normalized)


def _source_project_root(destination: Path) -> Path:
    if (destination / "pyproject.toml").is_file() or (destination / "setup.py").is_file():
        return destination
    children = [item for item in destination.iterdir() if item.is_dir()]
    files = [item for item in destination.iterdir() if item.is_file()]
    if len(children) == 1 and not files:
        return children[0]
    candidates = [
        item.parent for item in destination.rglob("pyproject.toml") if len(item.relative_to(destination).parts) <= 2
    ]
    if len(candidates) == 1:
        return candidates[0]
    raise SourceError(
        "Unable to identify one build project in the source archive",
        source=str(destination),
        candidates=tuple(str(item) for item in candidates),
    )
