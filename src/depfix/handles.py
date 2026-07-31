"""Stable package-handle API with lazy module access."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from types import ModuleType
from typing import TYPE_CHECKING

from .errors import ModuleNotProvidedError, MultipleImportModulesError, NoImportModulesError
from .models import Alias, LockedGraph, Node
from .sources import SourceInfo, parse_source

if TYPE_CHECKING:
    from .runtime import DepfixRuntime


@dataclass(frozen=True, slots=True)
class PackageMetadata:
    name: str
    version: str
    requires_python: str
    native_classification: str
    public_modules: tuple[str, ...]
    private_modules: tuple[str, ...]
    namespace_contributions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DependencyNodeView:
    node_id: str
    name: str
    version: str


class DependencyGraphView(Mapping[str, DependencyNodeView]):
    def __init__(self, graph: LockedGraph, node: Node) -> None:
        nodes = graph.node_index
        self._items = {
            name: DependencyNodeView(child_id, nodes[child_id].distribution, nodes[child_id].version)
            for name, child_id in sorted(node.dependencies.items())
        }

    def __getitem__(self, key: str) -> DependencyNodeView:
        return self._items[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)


class PackageModules(Mapping[str, ModuleType]):
    def __init__(self, runtime: DepfixRuntime, node: Node, module_names: tuple[str, ...]) -> None:
        self._runtime = runtime
        self._node = node
        self._module_names = module_names
        self._loaded: dict[str, ModuleType] = {}

    def __getitem__(self, name: str) -> ModuleType:
        if name not in self._module_names:
            raise ModuleNotProvidedError(
                "The package does not provide the requested public module",
                module=name,
                realm=self._node.id,
                import_modules=self._module_names,
            )
        module = self._loaded.get(name)
        if module is None:
            module = self._runtime.import_for_node(self._node.id, name)
            self._loaded[name] = module
        return module

    def __getattr__(self, name: str) -> ModuleType:
        if name.startswith("_"):
            raise AttributeError(name)
        try:
            return self[name]
        except ModuleNotProvidedError as exc:
            raise AttributeError(name) from exc

    def __iter__(self) -> Iterator[str]:
        return iter(self._module_names)

    def __len__(self) -> int:
        return len(self._module_names)


class PackageHandle:
    """One prepared distribution node and its lazily imported public modules."""

    def __init__(self, runtime: DepfixRuntime, alias: Alias) -> None:
        self._runtime = runtime
        self._alias = alias
        self._graph = runtime.graph
        self._node = self._graph.node_index[alias.node]
        self._artifact = self._graph.artifact_index[self._node.artifact]
        self.requested_specifier = alias.specifier
        self.name = self._artifact.distribution
        self.normalized_name = self._artifact.distribution
        self.version = self._artifact.version
        try:
            parsed_source = parse_source(alias.specifier)
        except Exception:
            parsed_source = SourceInfo(alias.specifier, alias.normalized_specifier, self._artifact.source_kind)
        self.source = parsed_source
        self.artifact_hash = self._artifact.sha256
        self.module_names = self._node.public_modules
        self.modules = PackageModules(runtime, self._node, self.module_names)
        self.metadata = PackageMetadata(
            self.name,
            self.version,
            self._artifact.requires_python,
            self._node.native_classification,
            self._node.public_modules,
            self._node.private_modules,
            self._node.namespace_contributions,
        )
        self.dependencies = DependencyGraphView(self._graph, self._node)
        self.realm_id = self._node.id
        self.prepared = True

    def import_module(self, name: str) -> ModuleType:
        return self.modules[name]

    def only_module(self) -> ModuleType:
        if not self.module_names:
            raise NoImportModulesError(
                f"{self.name}=={self.version} exposes no public import modules",
                request=self.requested_specifier,
            )
        if len(self.module_names) > 1:
            raise MultipleImportModulesError(
                f"{self.name}=={self.version} exposes multiple public import modules",
                request=self.requested_specifier,
                import_modules=self.module_names,
                remediation="select one through package.modules[name] or package.import_module(name)",
            )
        return self.modules[self.module_names[0]]

    def __repr__(self) -> str:
        return f"PackageHandle({self.name!r}, version={self.version!r}, modules={self.module_names!r})"
