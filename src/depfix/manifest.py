"""Deterministic, realm-aware Depfix import manifest."""

from __future__ import annotations

import hashlib
import json
import keyword
import os
import platform
import re
import sys
import sysconfig
import tempfile
import tomllib
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .errors import (
    ManifestError,
    ManifestMismatchError,
    ManifestNotFoundError,
    UnsupportedManifestVersionError,
)
from .models import Alias, Artifact, Environment, LockedGraph, Node


def current_environment() -> Environment:
    return Environment(
        python_implementation=platform.python_implementation().lower(),
        python_version=f"{sys.version_info.major}.{sys.version_info.minor}",
        abi=sysconfig.get_config_var("SOABI") or "none",
        platform=sys.platform,
        machine=platform.machine().lower(),
    )


def graph_digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def computed_graph_id(graph: LockedGraph) -> str:
    def artifact_payload(item: Artifact) -> dict[str, Any]:
        return {name: getattr(item, name) for name in item.__dataclass_fields__}

    def node_payload(item: Node) -> dict[str, Any]:
        payload = {name: getattr(item, name) for name in item.__dataclass_fields__}
        payload["dependencies"] = dict(sorted(item.dependencies.items()))
        return payload

    payload = {
        "environment": {
            "python_implementation": graph.environment.python_implementation,
            "python_version": graph.environment.python_version,
            "abi": graph.environment.abi,
            "platform": graph.environment.platform,
            "machine": graph.environment.machine,
        },
        "resolver": {"backend": graph.resolver_backend, "version": graph.resolver_version},
        "artifacts": [artifact_payload(item) for item in sorted(graph.artifacts, key=lambda value: value.id)],
        "nodes": [node_payload(item) for item in sorted(graph.nodes, key=lambda value: value.id)],
        "requests": [
            {name: getattr(item, name) for name in item.__dataclass_fields__}
            for item in sorted(graph.aliases, key=lambda value: value.name)
        ],
        "policy": dict(sorted(graph.policy.items())),
        "dynamic_diagnostics": sorted(graph.dynamic_diagnostics),
    }
    return graph_digest(payload)


def _q(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _array(values: Iterable[str]) -> str:
    return "[" + ", ".join(_q(value) for value in values) + "]"


def _inline_table(value: dict[str, str]) -> str:
    return "{ " + ", ".join(f"{_q(key)} = {_q(item)}" for key, item in sorted(value.items())) + " }"


def dumps_manifest(graph: LockedGraph) -> str:
    lines = [
        f"format-version = {graph.format_version}",
        f"manifest-id = {_q(graph.graph_id)}",
        f"created-by = {_q(graph.created_by)}",
        f"dynamic-diagnostics = {_array(sorted(graph.dynamic_diagnostics))}",
        "",
        "[resolver]",
        f"backend = {_q(graph.resolver_backend)}",
        f"backend-version = {_q(graph.resolver_version)}",
        f"depfix-version = {_q(graph.created_by.rsplit(' ', 1)[-1])}",
        "",
        "[[targets]]",
        f"id = {_q(_target_id(graph.environment))}",
        f"implementation = {_q(graph.environment.python_implementation)}",
        f"python-version = {_q(graph.environment.python_version)}",
        f"abi = {_q(graph.environment.abi)}",
        f"platform = {_q(graph.environment.platform)}",
        f"architecture = {_q(graph.environment.machine)}",
    ]
    if graph.policy:
        lines.extend(["", "[policy]"])
        for key, value in sorted(graph.policy.items()):
            if isinstance(value, bool):
                rendered = str(value).lower()
            elif isinstance(value, int):
                rendered = str(value)
            elif isinstance(value, (tuple, list)):
                rendered = _array(str(item) for item in value)
            else:
                rendered = _q(str(value))
            lines.append(f"{key} = {rendered}")
    for artifact in sorted(graph.artifacts, key=lambda item: item.id):
        lines.extend(
            [
                "",
                "[[artifacts]]",
                f"id = {_q(artifact.id)}",
                f"distribution = {_q(artifact.distribution)}",
                f"version = {_q(artifact.version)}",
                f"url = {_q(artifact.url)}",
                f"final-url = {_q(artifact.final_url)}",
                f"filename = {_q(artifact.filename)}",
                f"size = {artifact.size}",
                f"sha256 = {_q(artifact.sha256)}",
                f"source-kind = {_q(artifact.source_kind)}",
                f"python-tag = {_q(artifact.python_tag)}",
                f"abi-tag = {_q(artifact.abi_tag)}",
                f"platform-tag = {_q(artifact.platform_tag)}",
                f"build-tag = {_q(artifact.build_tag)}",
                f"requires-python = {_q(artifact.requires_python)}",
                f"yanked = {str(artifact.yanked).lower()}",
                f"yanked-reason = {_q(artifact.yanked_reason)}",
                f"vcs-repository = {_q(artifact.vcs_repository)}",
                f"vcs-commit = {_q(artifact.vcs_commit)}",
                f"requested-ref = {_q(artifact.requested_ref)}",
                f"subdirectory = {_q(artifact.subdirectory)}",
                f"local-source-hash = {_q(artifact.local_source_hash)}",
                f"build-backend = {_q(artifact.build_backend)}",
                f"source-url = {_q(artifact.source_url)}",
                f"source-final-url = {_q(artifact.source_final_url)}",
                f"source-sha256 = {_q(artifact.source_sha256)}",
                f"source-size = {artifact.source_size}",
            ]
        )
    for node in sorted(graph.nodes, key=lambda item: item.id):
        lines.extend(
            [
                "",
                "[[nodes]]",
                f"id = {_q(node.id)}",
                f"realm-id = {_q(node.id)}",
                f"artifact = {_q(node.artifact)}",
                f"distribution = {_q(node.distribution)}",
                f"version = {_q(node.version)}",
                f"extras = {_array(node.extras)}",
                f"provided-roots = {_array(node.provided_modules)}",
                f"module-names = {_array(node.public_modules)}",
                f"private-module-names = {_array(node.private_modules)}",
                f"all-importable-names = {_array(node.all_importable_modules)}",
                f"namespace-contributions = {_array(node.namespace_contributions)}",
                f"native-classification = {_q(node.native_classification)}",
                f"dependencies = {_inline_table(dict(node.dependencies))}",
                f"evaluated-markers = {_array(node.evaluated_markers)}",
            ]
        )
    for request in sorted(graph.aliases, key=lambda item: item.name):
        request_id = (
            "request_" + hashlib.sha256((request.normalized_specifier + request.name).encode()).hexdigest()[:20]
        )
        lines.extend(
            [
                "",
                "[[requests]]",
                f"id = {_q(request_id)}",
                f"alias = {_q(request.name)}",
                f"node = {_q(request.node)}",
                f"module = {_q(request.module)}",
                f"specifier = {_q(request.specifier)}",
                f"normalized-specifier = {_q(request.normalized_specifier)}",
                f"api = {_q(request.api)}",
                f"source-file = {_q(request.source_file)}",
                f"source-line = {request.source_line}",
                f"source-column = {request.source_column}",
                f"assignment = {_q(request.assignment)}",
                f"explicit-module = {str(request.explicit_module).lower()}",
                f"isolation = {_q(request.isolation)}",
                f"index-identity = {_q(request.index_identity)}",
                f"source-policy = {_q(request.source_policy)}",
            ]
        )
    return "\n".join(lines) + "\n"


def write_manifest(graph: LockedGraph, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = dumps_manifest(graph).encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass


def load_manifest(path: Path) -> LockedGraph:
    try:
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
    except FileNotFoundError as exc:
        raise ManifestNotFoundError("Depfix manifest was not found", manifest=path) from exc
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ManifestError("Unable to read Depfix manifest", manifest=path, remediation=str(exc)) from exc
    if "graph-id" in raw and "manifest-id" not in raw:
        raise UnsupportedManifestVersionError(
            "The phase-one prototype manifest is not valid for permanent Depfix releases",
            manifest=path,
            remediation="run `depfix export` to create .depfix/imports.lock",
        )
    try:
        if raw["format-version"] != 1:
            raise UnsupportedManifestVersionError(
                f"Unsupported manifest format {raw['format-version']!r}",
                manifest=path,
                remediation="upgrade Depfix or export with a supported release",
            )
        if len(raw["targets"]) != 1:
            raise ValueError("this manifest format requires exactly one target")
        target = raw["targets"][0]
        environment = Environment(
            target["implementation"],
            target["python-version"],
            target["abi"],
            target["platform"],
            target["architecture"],
        )
        artifacts = tuple(
            Artifact(
                id=item["id"],
                distribution=item["distribution"],
                version=item["version"],
                url=item["url"],
                filename=item["filename"],
                size=item["size"],
                sha256=item["sha256"],
                python_tag=item.get("python-tag", "py3"),
                abi_tag=item.get("abi-tag", "none"),
                platform_tag=item.get("platform-tag", "any"),
                build_tag=item.get("build-tag", ""),
                requires_python=item.get("requires-python", ""),
                yanked=item.get("yanked", False),
                yanked_reason=item.get("yanked-reason", ""),
                source_kind=item.get("source-kind", "pypi"),
                final_url=item.get("final-url", ""),
                vcs_repository=item.get("vcs-repository", ""),
                vcs_commit=item.get("vcs-commit", ""),
                requested_ref=item.get("requested-ref", ""),
                subdirectory=item.get("subdirectory", ""),
                local_source_hash=item.get("local-source-hash", ""),
                build_backend=item.get("build-backend", ""),
                source_url=item.get("source-url", ""),
                source_final_url=item.get("source-final-url", ""),
                source_sha256=item.get("source-sha256", ""),
                source_size=item.get("source-size", 0),
            )
            for item in raw.get("artifacts", [])
        )
        nodes = tuple(
            Node(
                id=item["id"],
                artifact=item["artifact"],
                distribution=item["distribution"],
                version=item["version"],
                extras=tuple(item.get("extras", [])),
                provided_modules=tuple(item.get("provided-roots", [])),
                public_modules=tuple(item.get("module-names", [])),
                private_modules=tuple(item.get("private-module-names", [])),
                all_importable_modules=tuple(item.get("all-importable-names", [])),
                namespace_contributions=tuple(item.get("namespace-contributions", [])),
                native_classification=item.get("native-classification", "pure-python"),
                dependencies=dict(item.get("dependencies", {})),
                evaluated_markers=tuple(item.get("evaluated-markers", [])),
            )
            for item in raw.get("nodes", [])
        )
        requests = tuple(
            Alias(
                item["alias"],
                item["node"],
                item.get("module", ""),
                item["specifier"],
                normalized_specifier=item.get("normalized-specifier", ""),
                api=item.get("api", "import_module"),
                source_file=item.get("source-file", ""),
                source_line=item.get("source-line", 0),
                source_column=item.get("source-column", 0),
                assignment=item.get("assignment", ""),
                explicit_module=item.get("explicit-module", False),
                isolation=item.get("isolation", "inprocess"),
                index_identity=item.get("index-identity", ""),
                source_policy=item.get("source-policy", "default"),
            )
            for item in raw.get("requests", [])
        )
        resolver = raw.get("resolver", {})
        graph = LockedGraph(
            raw["format-version"],
            raw["manifest-id"],
            raw["created-by"],
            environment,
            artifacts,
            nodes,
            requests,
            raw.get("policy", {}),
            resolver_backend=resolver.get("backend", "uv"),
            resolver_version=resolver.get("backend-version", "unknown"),
            dynamic_diagnostics=tuple(raw.get("dynamic-diagnostics", [])),
        )
    except UnsupportedManifestVersionError:
        raise
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise ManifestError("Malformed Depfix manifest", manifest=path, remediation=str(exc)) from exc
    _validate(graph, path)
    return graph


def _validate(graph: LockedGraph, path: Path) -> None:
    artifacts = graph.artifact_index
    nodes = graph.node_index
    if (
        len(artifacts) != len(graph.artifacts)
        or len(nodes) != len(graph.nodes)
        or len(graph.alias_index) != len(graph.aliases)
    ):
        raise ManifestError("Duplicate artifacts, nodes, or aliases", manifest=path)
    digest_pattern = re.compile(r"^[0-9a-f]{64}$")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", graph.graph_id):
        raise ManifestError("Manifest identity is not a full SHA-256 value", manifest=path)
    for policy_name in ("allowed-hosts", "allowed-indexes"):
        policy_value = graph.policy.get(policy_name, ())
        if not isinstance(policy_value, (str, tuple, list)) or (
            not isinstance(policy_value, str) and not all(isinstance(item, str) for item in policy_value)
        ):
            raise ManifestError(f"Policy {policy_name!r} must contain strings", manifest=path)
        values = (policy_value,) if isinstance(policy_value, str) else policy_value
        if any(_contains_secret(item) for item in values):
            raise ManifestError(f"Policy {policy_name!r} contains serialized credentials", manifest=path)
    if not isinstance(graph.policy.get("allow-insecure-transport", False), bool):
        raise ManifestError("Policy 'allow-insecure-transport' must be boolean", manifest=path)
    for artifact in graph.artifacts:
        if artifact.id != f"sha256:{artifact.sha256}" or not digest_pattern.fullmatch(artifact.sha256):
            raise ManifestError(f"Artifact {artifact.id!r} has an invalid content identity", manifest=path)
        if (
            _contains_secret(artifact.url)
            or _contains_secret(artifact.final_url)
            or _contains_secret(artifact.vcs_repository)
            or _contains_secret(artifact.source_url)
            or _contains_secret(artifact.source_final_url)
        ):
            raise ManifestError(
                f"Artifact {artifact.id!r} contains credentials in serialized provenance",
                manifest=path,
                remediation="use external uv/index authentication and export again",
            )
        if artifact.source_sha256 and not digest_pattern.fullmatch(artifact.source_sha256):
            raise ManifestError(f"Artifact {artifact.id!r} has invalid source provenance", manifest=path)
        if artifact.source_size < 0:
            raise ManifestError(f"Artifact {artifact.id!r} has invalid source size", manifest=path)
    for node in graph.nodes:
        if node.artifact not in artifacts:
            raise ManifestError(f"Node {node.id} references a missing artifact", manifest=path)
        missing = set(node.dependencies.values()) - set(nodes)
        if missing:
            raise ManifestError(f"Node {node.id} references missing dependencies {sorted(missing)}", manifest=path)
        if node.native_classification not in {
            "pure-python",
            "native-certified-safe",
            "native-unknown",
            "native-known-unsafe",
        }:
            raise ManifestError(f"Node {node.id} has an unsupported native classification", manifest=path)
        for name in (
            *node.provided_modules,
            *node.public_modules,
            *node.private_modules,
            *node.all_importable_modules,
            *node.namespace_contributions,
        ):
            if not name or not all(part.isidentifier() for part in name.split(".")):
                raise ManifestError(f"Node {node.id} contains an invalid import name {name!r}", manifest=path)
    for request in graph.aliases:
        if request.node not in nodes:
            raise ManifestError(f"Request alias {request.name} references a missing node", manifest=path)
        if not request.name.isidentifier() or keyword.iskeyword(request.name):
            raise ManifestError(f"Alias {request.name!r} is not a Python identifier", manifest=path)
        if request.module and not all(part.isidentifier() for part in request.module.split(".")):
            raise ManifestError(f"Alias {request.name!r} has an invalid logical module", manifest=path)
        if not request.module and request.api != "load_package":
            raise ManifestError(f"Import request {request.name!r} has no selected module", manifest=path)
        if request.isolation not in {"inprocess", "process"}:
            raise ManifestError(f"Request {request.name!r} has an unsupported isolation policy", manifest=path)
        if _contains_secret(request.specifier) or _contains_secret(request.index_identity):
            raise ManifestError(f"Request {request.name!r} contains serialized credentials", manifest=path)
    expected = computed_graph_id(graph)
    if graph.graph_id != expected:
        raise ManifestMismatchError(
            "Manifest identity does not match its canonical contents",
            manifest=path,
            remediation=f"expected manifest-id {expected}; export again",
        )


def assert_compatible_environment(graph: LockedGraph, path: Path) -> None:
    actual = current_environment()
    if graph.environment != actual:
        raise ManifestMismatchError(
            "The manifest target does not match this interpreter",
            manifest=path,
            remediation=f"export/install a matching target; manifest={graph.environment!r}, actual={actual!r}",
        )


def _target_id(environment: Environment) -> str:
    return (
        f"{environment.python_implementation}-{environment.python_version}-{environment.platform}-{environment.machine}"
    )


def _contains_secret(value: str) -> bool:
    if not value:
        return False
    if re.search(r"[a-zA-Z][a-zA-Z0-9+.-]*://(?!<redacted>@)[^/@\s]+@", value):
        return True
    return bool(
        re.search(
            r"(?i)[?&](?:access[_-]?token|api[_-]?key|auth|credential|password|secret|token)=(?!<redacted>)",
            value,
        )
    )


# Internal aliases keep the phase-one implementation incrementally testable;
# public documentation and project APIs use the manifest names above.
dumps = dumps_manifest
write = write_manifest
load = load_manifest
