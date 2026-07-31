"""Lock graph data model.

The model deliberately separates artifacts, distribution nodes, aliases and
logical modules.  A node is lock-scoped: the same artifact may occur in more
than one node when its dependency view differs.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class Environment:
    python_implementation: str
    python_version: str
    abi: str
    platform: str
    machine: str


@dataclass(frozen=True, slots=True)
class Artifact:
    id: str
    distribution: str
    version: str
    url: str
    filename: str
    size: int
    sha256: str
    python_tag: str = "py3"
    abi_tag: str = "none"
    platform_tag: str = "any"
    build_tag: str = ""
    requires_python: str = ""
    yanked: bool = False
    yanked_reason: str = ""
    source_kind: str = "pypi"
    final_url: str = ""
    vcs_repository: str = ""
    vcs_commit: str = ""
    requested_ref: str = ""
    subdirectory: str = ""
    local_source_hash: str = ""
    build_backend: str = ""
    source_url: str = ""
    source_final_url: str = ""
    source_sha256: str = ""
    source_size: int = 0


@dataclass(frozen=True, slots=True)
class Node:
    id: str
    artifact: str
    distribution: str
    version: str
    extras: tuple[str, ...] = ()
    provided_modules: tuple[str, ...] = ()
    public_modules: tuple[str, ...] = ()
    private_modules: tuple[str, ...] = ()
    all_importable_modules: tuple[str, ...] = ()
    namespace_contributions: tuple[str, ...] = ()
    native_classification: str = "pure-python"
    dependencies: Mapping[str, str] = field(default_factory=dict)
    evaluated_markers: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Alias:
    name: str
    node: str
    module: str
    specifier: str
    normalized_specifier: str = ""
    api: str = "import_module"
    source_file: str = ""
    source_line: int = 0
    source_column: int = 0
    assignment: str = ""
    explicit_module: bool = False
    isolation: str = "inprocess"
    index_identity: str = ""
    source_policy: str = "default"
    group: str = ""
    mode: str = "explicit"
    enclosing_function: str = ""


@dataclass(frozen=True, slots=True)
class RequestGroup:
    id: str
    mode: str
    specifiers: tuple[str, ...]
    normalized_specifiers: tuple[str, ...]
    aliases: tuple[str, ...]
    source_file: str = ""
    source_line: int = 0
    source_column: int = 0
    enclosing_function: str = ""
    ordinary_imports: tuple[str, ...] = ()
    resolved_graph_ids: tuple[str, ...] = ()
    provided_imports: tuple[str, ...] = ()
    module_aliases: Mapping[str, str] = field(default_factory=dict)
    source_base_dir: str = ""
    isolation: str = "inprocess"
    options: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LockedGraph:
    format_version: int
    graph_id: str
    created_by: str
    environment: Environment
    artifacts: tuple[Artifact, ...]
    nodes: tuple[Node, ...]
    aliases: tuple[Alias, ...]
    policy: Mapping[str, Any] = field(default_factory=dict)
    resolver_backend: str = "uv"
    resolver_version: str = "unknown"
    dynamic_diagnostics: tuple[str, ...] = ()
    groups: tuple[RequestGroup, ...] = ()

    @property
    def artifact_index(self) -> dict[str, Artifact]:
        return {artifact.id: artifact for artifact in self.artifacts}

    @property
    def node_index(self) -> dict[str, Node]:
        return {node.id: node for node in self.nodes}

    @property
    def alias_index(self) -> dict[str, Alias]:
        return {alias.name: alias for alias in self.aliases}

    @property
    def group_index(self) -> dict[str, RequestGroup]:
        return {group.id: group for group in self.groups}


def resolved_realm_id(graph: LockedGraph, node_ids: tuple[str, ...]) -> str:
    nodes = graph.node_index

    def visit(node_id: str, active: set[str]) -> dict[str, Any]:
        if node_id in active:
            return {"cycle": nodes[node_id].distribution}
        node = nodes[node_id]
        lineage = {*active, node_id}
        return {
            "distribution": node.distribution,
            "version": node.version,
            "artifact": node.artifact,
            "extras": node.extras,
            "dependencies": {name: visit(child_id, lineage) for name, child_id in sorted(node.dependencies.items())},
        }

    payload = [visit(node_id, set()) for node_id in sorted(node_ids)]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return "realm_" + hashlib.sha256(encoded).hexdigest()[:24]
