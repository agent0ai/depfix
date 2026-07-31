"""Realm-scoped synthetic module runtime for pure-Python artifacts."""

from __future__ import annotations

import builtins
import hashlib
import importlib
import importlib.abc
import importlib.machinery
import importlib.metadata
import importlib.resources
import importlib.util
import io
import os
import pkgutil
import sys
import threading
import types
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType, ModuleType
from typing import Any

from .cache import Cache
from .errors import (
    AmbiguousMetadataError,
    CacheError,
    ImportOwnershipError,
    ModuleNotProvidedError,
    NativeIsolationRequired,
    RealmImportError,
    UndeclaredImportError,
)
from .models import Alias, Artifact, LockedGraph, Node

_FACADE_ROOTS = {"importlib", "pkgutil"}


@dataclass(frozen=True, slots=True)
class _Location:
    source: Path | None
    package_dir: Path | None
    namespace_paths: tuple[Path, ...] = ()

    @property
    def is_package(self) -> bool:
        return self.package_dir is not None or bool(self.namespace_paths)

    @property
    def is_namespace(self) -> bool:
        return self.source is None and bool(self.namespace_paths)


class DepfixResourceReader(importlib.resources.abc.TraversableResources):
    def __init__(self, root: Path) -> None:
        self.root = root

    def open_resource(self, resource: str) -> io.BufferedReader:
        return (self.root / resource).open("rb")

    def resource_path(self, resource: str) -> str:
        return str(self.root / resource)

    def is_resource(self, path: str) -> bool:
        return (self.root / path).is_file()

    def contents(self) -> Iterator[str]:
        return (item.name for item in self.root.iterdir())

    def files(self) -> Path:
        return self.root


class DepfixSourceLoader(importlib.abc.Loader):
    def __init__(self, runtime: DepfixRuntime, node: Node, logical_name: str, location: _Location) -> None:
        self.runtime = runtime
        self.node = node
        self.logical_name = logical_name
        self.location = location

    def create_module(self, spec: importlib.machinery.ModuleSpec) -> ModuleType | None:
        return None

    def exec_module(self, module: ModuleType) -> None:
        self.runtime._exec_source(module, self.node, self.logical_name, self.location)

    def get_filename(self, fullname: str) -> str:
        if self.location.source is None:
            raise ImportError(f"namespace module {fullname} has no source file")
        return str(self.location.source)

    def get_source(self, fullname: str) -> str | None:
        if self.location.source is None:
            return None
        return self.location.source.read_text(encoding="utf-8")

    def get_code(self, fullname: str) -> types.CodeType | None:
        source = self.get_source(fullname)
        return None if source is None else compile(source, self.get_filename(fullname), "exec", dont_inherit=True)

    def get_data(self, path: str) -> bytes:
        return Path(path).read_bytes()

    def get_resource_reader(self, fullname: str) -> DepfixResourceReader | None:
        return DepfixResourceReader(self.location.package_dir) if self.location.package_dir is not None else None


class BoundImporter:
    def __init__(self, runtime: DepfixRuntime, node_id: str, logical_package: str) -> None:
        self.runtime = runtime
        self.node_id = node_id
        self.logical_package = logical_package

    def __call__(
        self,
        name: str,
        globals: dict[str, Any] | None = None,
        locals: dict[str, Any] | None = None,
        fromlist: tuple[str, ...] | list[str] = (),
        level: int = 0,
    ) -> ModuleType:
        if level:
            if not self.logical_package:
                raise ImportError("attempted relative import with no known parent package")
            logical_name = importlib.util.resolve_name("." * level + name, self.logical_package)
        else:
            logical_name = name
        if not level and logical_name.startswith("_depfix."):
            return builtins.__import__(name, globals, locals, fromlist, level)
        root = logical_name.split(".", 1)[0]
        if not level and root in _FACADE_ROOTS:
            requested = self.runtime.facade(self.node_id, logical_name, self.logical_package)
            if fromlist:
                return requested
            return self.runtime.facade(self.node_id, root, self.logical_package)
        if not level and self.runtime.is_standard_library(root):
            return builtins.__import__(name, globals, locals, fromlist, level)
        module = self.runtime.import_for_node(self.node_id, logical_name)
        if fromlist:
            for member in fromlist:
                if member == "*" or hasattr(module, member):
                    continue
                child_name = f"{logical_name}.{member}" if logical_name else member
                try:
                    child = self.runtime.import_for_node(self.node_id, child_name)
                except RealmImportError as exc:
                    if exc.module == child_name:
                        continue
                    raise
                setattr(module, member, child)
            return module
        return self.runtime.import_for_node(self.node_id, root)


class _DistributionView(importlib.metadata.Distribution):
    def __init__(self, artifact: Artifact) -> None:
        self.artifact = artifact

    @property
    def version(self) -> str:
        return self.artifact.version

    @property
    def metadata(self) -> importlib.metadata.PackageMetadata:
        return {"Name": self.artifact.distribution, "Version": self.artifact.version}  # type: ignore[return-value]

    def read_text(self, filename: str) -> str | None:
        return None

    def locate_file(self, path: str | os.PathLike[str]) -> Path:
        return Path(path)


class AliasLoader(importlib.abc.Loader):
    def __init__(self, runtime: DepfixRuntime, alias: Alias) -> None:
        self.runtime = runtime
        self.alias = alias

    def create_module(self, spec: importlib.machinery.ModuleSpec) -> ModuleType:
        return self.runtime.load_alias(self.alias.name)

    def exec_module(self, module: ModuleType) -> None:
        return None


class AliasRootLoader(importlib.abc.Loader):
    """Expose the runtime alias namespace without mutating ``sys.path``."""

    def __init__(self, runtime: DepfixRuntime) -> None:
        self.runtime = runtime

    def create_module(self, spec: importlib.machinery.ModuleSpec) -> ModuleType | None:
        return None

    def exec_module(self, module: ModuleType) -> None:
        runtime = self.runtime

        def load(name: str) -> object:
            alias = runtime.graph.alias_index.get(name)
            if alias is None:
                raise AttributeError(name)
            if alias.api == "load_package":
                from .handles import PackageHandle

                value: object = PackageHandle(runtime, alias)
            else:
                value = runtime.load_alias(name)
            module.__dict__[name] = value
            return value

        module.__dict__.update(
            {
                "__all__": sorted(runtime.graph.alias_index),
                "__getattr__": load,
                "__path__": [],
            }
        )


class AliasFinder(importlib.abc.MetaPathFinder):
    def __init__(self, runtime: DepfixRuntime) -> None:
        self.runtime = runtime

    def find_spec(
        self,
        fullname: str,
        path: Iterable[str] | None,
        target: ModuleType | None = None,
    ) -> importlib.machinery.ModuleSpec | None:
        prefix = "depfix_imports."
        if fullname == "depfix_imports":
            return importlib.util.spec_from_loader(fullname, AliasRootLoader(self.runtime), is_package=True)
        if not fullname.startswith(prefix):
            return None
        alias_name = fullname[len(prefix) :]
        if "." in alias_name:
            return None
        alias = self.runtime.graph.alias_index.get(alias_name)
        if alias is None or not alias.module:
            return None
        return importlib.util.spec_from_loader(fullname, AliasLoader(self.runtime, alias))


class DepfixRuntime:
    def __init__(
        self, graph: LockedGraph, cache: Cache, *, manifest: Path | None = None, lockfile: Path | None = None
    ) -> None:
        self.graph = graph
        self.cache = cache
        self.manifest = manifest or lockfile
        self._nodes = graph.node_index
        self._artifacts = graph.artifact_index
        self._locks: dict[tuple[str, str], threading.RLock] = {}
        self._locks_guard = threading.Lock()
        self._locations: dict[str, tuple[Node, str, _Location]] = {}
        self._facades: dict[tuple[str, str, str], ModuleType] = {}
        self._finder = AliasFinder(self)

    def activate(self) -> DepfixRuntime:
        for artifact in self.graph.artifacts:
            root = self.cache.unpacked_path(artifact.id)
            if not (root / ".complete").is_file():
                raise CacheError(
                    "Locked artifact has not been synchronized",
                    manifest=self.manifest,
                    artifact_hash=artifact.sha256,
                    remediation="run `depfix install <manifest> --frozen` before activating it",
                )
        if not any(finder is self._finder for finder in sys.meta_path):
            sys.meta_path.insert(0, self._finder)
        return self

    def deactivate(self) -> None:
        """Remove this runtime's public alias finder; loaded realm modules remain valid."""
        sys.meta_path[:] = [finder for finder in sys.meta_path if finder is not self._finder]

    def load_alias(self, alias_name: str) -> ModuleType:
        alias = self.graph.alias_index.get(alias_name)
        if alias is None:
            raise RealmImportError("Unknown manifest alias", module=alias_name, manifest=self.manifest)
        return self.import_for_node(alias.node, alias.module)

    def import_for_node(self, caller_node_id: str, logical_name: str) -> ModuleType:
        if not logical_name or not all(part.isidentifier() for part in logical_name.split(".")):
            raise RealmImportError(
                "Logical module name is not a valid dotted Python name",
                module=logical_name,
                referrer=caller_node_id,
                realm=caller_node_id,
                manifest=self.manifest,
            )
        caller = self._nodes[caller_node_id]
        providers = self._provider_nodes(caller, logical_name)
        if not providers:
            raise UndeclaredImportError(
                "No declared provider exposes this import in the caller's realm",
                module=logical_name,
                referrer=caller.id,
                realm=caller.id,
                manifest=self.manifest,
                remediation=(
                    "declare the dependency in package metadata; ambient site-packages are intentionally ignored"
                ),
            )
        root = logical_name.split(".", 1)[0]
        if len(providers) > 1:
            if not all(root in node.namespace_contributions for node in providers):
                raise ImportOwnershipError(
                    "Several direct dependencies provide the same non-namespace import root",
                    module=logical_name,
                    referrer=caller.id,
                    realm=caller.id,
                    candidates=tuple(f"{node.distribution}=={node.version}" for node in providers),
                )
            if logical_name == root or all(self._locate(node, logical_name).is_namespace for node in providers):
                return self._load_namespace(caller, logical_name, providers)
            concrete = [node for node in providers if self._location_exists(self._locate(node, logical_name))]
            if len(concrete) != 1:
                raise ImportOwnershipError(
                    "Namespace submodule ownership is ambiguous",
                    module=logical_name,
                    referrer=caller.id,
                    realm=caller.id,
                    candidates=tuple(f"{node.distribution}=={node.version}" for node in concrete),
                )
            provider = concrete[0]
        else:
            provider = providers[0]
        return self._load_from_provider(provider, logical_name)

    def _provider_nodes(self, caller: Node, logical_name: str) -> list[Node]:
        root = logical_name.split(".", 1)[0]
        if root in caller.provided_modules:
            providers = [caller]
            if root in caller.namespace_contributions:
                providers.extend(
                    child for child in self._direct_dependencies(caller) if root in child.namespace_contributions
                )
            return providers
        return [child for child in self._direct_dependencies(caller) if root in child.provided_modules]

    def _direct_dependencies(self, node: Node) -> list[Node]:
        return [self._nodes[node_id] for _name, node_id in sorted(node.dependencies.items())]

    def _load_from_provider(self, node: Node, logical_name: str) -> ModuleType:
        if "." in logical_name:
            parent_name = logical_name.rpartition(".")[0]
            parent = self._load_from_provider(node, parent_name)
        else:
            parent = None
        canonical = self.canonical_name(node.id, logical_name)
        lock = self._module_lock(node.id, logical_name)
        with lock:
            existing = sys.modules.get(canonical)
            if existing is not None:
                return existing
            if parent is not None:
                member = logical_name.rsplit(".", 1)[1]
                dynamic = vars(parent).get(member)
                if dynamic is None and parent.__name__ not in self._locations:
                    dynamic = getattr(parent, member, None)
                if isinstance(dynamic, ModuleType):
                    dynamic_name = dynamic.__name__
                    if dynamic_name == canonical:
                        sys.modules[canonical] = dynamic
                        return dynamic
                    if self.is_standard_library(dynamic_name.split(".", 1)[0]):
                        return dynamic
            location = self._locate(node, logical_name)
            if not self._location_exists(location):
                raise RealmImportError(
                    "Provider does not contain the requested module",
                    module=logical_name,
                    referrer=node.id,
                    realm=node.id,
                    manifest=self.manifest,
                    artifact_hash=self._artifacts[node.artifact].sha256,
                )
            if location.source is not None and location.source.suffix.lower() in {
                ".so",
                ".pyd",
                ".dll",
                ".dylib",
            }:
                raise NativeIsolationRequired(
                    "Native module loading is not safe in an in-process Depfix realm",
                    module=logical_name,
                    referrer=node.id,
                    realm=node.id,
                    manifest=self.manifest,
                    artifact_hash=self._artifacts[node.artifact].sha256,
                    remediation="run this package in an application-owned worker process",
                )
            if location.is_namespace:
                module = self._create_namespace(
                    node, logical_name, (location.package_dir,) if location.package_dir else location.namespace_paths
                )
            else:
                module = self._create_source_module(node, logical_name, location)
            if parent is not None:
                setattr(parent, logical_name.rsplit(".", 1)[1], module)
            return module

    def _create_source_module(self, node: Node, logical_name: str, location: _Location) -> ModuleType:
        canonical = self.canonical_name(node.id, logical_name)
        self._ensure_synthetic_parents(canonical)
        loader = DepfixSourceLoader(self, node, logical_name, location)
        spec = importlib.util.spec_from_loader(
            canonical, loader, origin=str(location.source), is_package=location.is_package
        )
        if spec is None:
            raise RealmImportError("Unable to construct module spec", module=logical_name, realm=node.id)
        spec.has_location = True
        spec.loader_state = self._loader_state(node, logical_name)
        if location.is_package:
            spec.submodule_search_locations = [str(location.package_dir)] if location.package_dir else []
        module = importlib.util.module_from_spec(spec)
        sys.modules[canonical] = module
        self._locations[canonical] = (node, logical_name, location)
        try:
            loader.exec_module(module)
        except BaseException:
            if sys.modules.get(canonical) is module:
                del sys.modules[canonical]
            self._locations.pop(canonical, None)
            raise
        return module

    def _exec_source(self, module: ModuleType, node: Node, logical_name: str, location: _Location) -> None:
        if location.source is None:
            return
        logical_package = logical_name if location.is_package else logical_name.rpartition(".")[0]
        module.__dict__.update(self._metadata(node, logical_name))
        module.__dict__["__depfix_logical_package__"] = logical_package
        realm_builtins = dict(vars(builtins))
        realm_builtins["__import__"] = BoundImporter(self, node.id, logical_package)
        module.__dict__["__builtins__"] = realm_builtins
        source = location.source.read_bytes()
        code = compile(source, str(location.source), "exec", dont_inherit=True)
        exec(code, module.__dict__)

    def _load_namespace(self, caller: Node, logical_name: str, providers: list[Node]) -> ModuleType:
        canonical = self.namespace_name(caller.id, logical_name)
        existing = sys.modules.get(canonical)
        if existing is not None:
            return existing
        paths = tuple(
            location.package_dir
            for node in providers
            if (location := self._locate(node, logical_name)).package_dir is not None
        )
        if not paths:
            raise RealmImportError(
                "Namespace package has no physical contributions", module=logical_name, realm=caller.id
            )
        module = self._create_namespace(caller, logical_name, paths, canonical=canonical)
        if "." in logical_name:
            parent = self.import_for_node(caller.id, logical_name.rpartition(".")[0])
            setattr(parent, logical_name.rsplit(".", 1)[1], module)
        return module

    def _create_namespace(
        self,
        node: Node,
        logical_name: str,
        paths: tuple[Path, ...],
        *,
        canonical: str | None = None,
    ) -> ModuleType:
        canonical = canonical or self.canonical_name(node.id, logical_name)
        self._ensure_synthetic_parents(canonical)
        spec = importlib.machinery.ModuleSpec(canonical, loader=None, is_package=True)
        spec.submodule_search_locations = [str(path) for path in paths]
        spec.loader_state = self._loader_state(node, logical_name)
        module = ModuleType(canonical)
        module.__spec__ = spec
        module.__loader__ = None
        module.__package__ = canonical
        module.__path__ = list(spec.submodule_search_locations)
        module.__dict__.update(self._metadata(node, logical_name))
        module.__dict__["__depfix_logical_package__"] = logical_name
        sys.modules[canonical] = module
        return module

    def _locate(self, node: Node, logical_name: str) -> _Location:
        root = self.cache.unpacked_path(node.artifact) / "purelib"
        relative = Path(*logical_name.split("."))
        package_dir = root / relative
        initializer = package_dir / "__init__.py"
        if initializer.is_file():
            return _Location(initializer, package_dir)
        module_file = (root / relative).with_suffix(".py")
        if module_file.is_file():
            return _Location(module_file, None)
        native_stem = root / relative
        native_candidates = sorted(
            (
                *native_stem.parent.glob(native_stem.name + ".*.so"),
                *native_stem.parent.glob(native_stem.name + ".*.pyd"),
                native_stem.with_suffix(".so"),
                native_stem.with_suffix(".pyd"),
                *native_stem.parent.glob(native_stem.name + ".dll"),
                *native_stem.parent.glob(native_stem.name + ".dylib"),
            ),
            key=lambda item: item.name,
        )
        native_candidates = [item for item in native_candidates if item.is_file()]
        if native_candidates:
            return _Location(native_candidates[0], None)
        if package_dir.is_dir():
            return _Location(None, package_dir, (package_dir,))
        return _Location(None, None)

    @staticmethod
    def _location_exists(location: _Location) -> bool:
        return location.source is not None or location.package_dir is not None or bool(location.namespace_paths)

    def _module_lock(self, node_id: str, logical_name: str) -> threading.RLock:
        key = (node_id, logical_name)
        with self._locks_guard:
            return self._locks.setdefault(key, threading.RLock())

    def canonical_name(self, node_id: str, logical_name: str) -> str:
        graph_token = hashlib.sha256(self.graph.graph_id.encode()).hexdigest()[:24]
        node_token = node_id.removeprefix("node_").removeprefix("n_")
        return f"_depfix.g_{graph_token}.n_{node_token}.{logical_name}"

    def namespace_name(self, realm_id: str, logical_name: str) -> str:
        graph_token = hashlib.sha256(self.graph.graph_id.encode()).hexdigest()[:24]
        realm_token = realm_id.removeprefix("node_").removeprefix("n_")
        return f"_depfix.g_{graph_token}.r_{realm_token}.{logical_name}"

    def _ensure_synthetic_parents(self, canonical: str) -> None:
        parts = canonical.split(".")
        for index in range(1, len(parts)):
            name = ".".join(parts[:index])
            if name in sys.modules:
                continue
            package = ModuleType(name)
            package.__path__ = []
            package.__package__ = name
            package.__spec__ = importlib.machinery.ModuleSpec(name, loader=None, is_package=True)
            package.__spec__.submodule_search_locations = []
            sys.modules[name] = package
            if index > 1:
                setattr(sys.modules[".".join(parts[: index - 1])], parts[index - 1], package)

    def _loader_state(self, node: Node, logical_name: str) -> MappingProxyType[str, object]:
        artifact = self._artifacts[node.artifact]
        return MappingProxyType(
            {
                "graph_id": self.graph.graph_id,
                "node_id": node.id,
                "logical_name": logical_name,
                "distribution": node.distribution,
                "version": node.version,
                "artifact_id": artifact.id,
                "dependency_map": MappingProxyType(dict(node.dependencies)),
            }
        )

    def _metadata(self, node: Node, logical_name: str) -> dict[str, object]:
        artifact = self._artifacts[node.artifact]
        aliases = [alias for alias in self.graph.aliases if alias.node == node.id]
        return {
            "__depfix_graph_id__": self.graph.graph_id,
            "__depfix_node_id__": node.id,
            "__depfix_logical_name__": logical_name,
            "__depfix_distribution__": node.distribution,
            "__depfix_version__": node.version,
            "__depfix_artifact_id__": artifact.id,
            "__depfix_specifier__": aliases[0].specifier if aliases else None,
            "__depfix_dependency_map__": MappingProxyType(dict(node.dependencies)),
        }

    def is_standard_library(self, root: str) -> bool:
        return root in sys.builtin_module_names or root in getattr(sys, "stdlib_module_names", set())

    def facade(self, node_id: str, requested: str, logical_package: str) -> ModuleType:
        key = (node_id, requested, logical_package)
        if key in self._facades:
            return self._facades[key]
        if requested == "importlib":
            facade = _copy_module(importlib)
            setattr(  # noqa: B010
                facade,
                "import_module",
                lambda name, package=None: self._facade_import(node_id, name, package, logical_package),
            )
            setattr(facade, "reload", self.reload)  # noqa: B010
            setattr(  # noqa: B010
                facade,
                "resources",
                self.facade(node_id, "importlib.resources", logical_package),
            )
            setattr(  # noqa: B010
                facade,
                "metadata",
                self.facade(node_id, "importlib.metadata", logical_package),
            )
            setattr(  # noqa: B010
                facade,
                "util",
                self.facade(node_id, "importlib.util", logical_package),
            )
        elif requested == "importlib.resources":
            facade = _copy_module(importlib.resources)
            setattr(  # noqa: B010
                facade,
                "files",
                lambda package: importlib.resources.files(self._module_argument(node_id, package)),
            )
        elif requested == "importlib.metadata":
            facade = _copy_module(importlib.metadata)
            setattr(  # noqa: B010
                facade,
                "version",
                lambda distribution: self.distribution_for_node(node_id, distribution).version,
            )
            setattr(  # noqa: B010
                facade,
                "distribution",
                lambda distribution: self.distribution_for_node(node_id, distribution),
            )
        elif requested == "importlib.util":
            facade = _copy_module(importlib.util)
            setattr(  # noqa: B010
                facade,
                "find_spec",
                lambda name, package=None: self._facade_find_spec(node_id, name, package, logical_package),
            )
        elif requested == "pkgutil":
            facade = _copy_module(pkgutil)
            setattr(  # noqa: B010
                facade,
                "get_data",
                lambda package, resource: self._pkgutil_get_data(node_id, package, resource),
            )
        else:
            raise ImportError(requested)
        self._facades[key] = facade
        return facade

    def _facade_import(self, node_id: str, name: str, package: str | None, logical_package: str) -> ModuleType:
        if name.startswith("."):
            base = package or logical_package
            if base.startswith("_depfix."):
                module = sys.modules.get(base)
                base = str(getattr(module, "__depfix_logical_name__", logical_package))
            name = importlib.util.resolve_name(name, base)
        if self.is_standard_library(name.split(".", 1)[0]):
            return importlib.import_module(name)
        return self.import_for_node(node_id, name)

    def _facade_find_spec(
        self,
        node_id: str,
        name: str,
        package: str | None,
        logical_package: str,
    ) -> importlib.machinery.ModuleSpec | None:
        try:
            return self._facade_import(node_id, name, package, logical_package).__spec__
        except ModuleNotProvidedError:
            return None

    def _module_argument(self, node_id: str, package: str | ModuleType) -> ModuleType:
        if isinstance(package, ModuleType):
            return package
        if package.startswith("_depfix.") and package in sys.modules:
            return sys.modules[package]
        return self.import_for_node(node_id, package)

    def _pkgutil_get_data(self, node_id: str, package: str, resource: str) -> bytes | None:
        module = self._module_argument(node_id, package)
        if not hasattr(module, "__path__") or module.__spec__ is None or module.__spec__.loader is None:
            return None
        loader = module.__spec__.loader
        if not hasattr(loader, "get_data"):
            return None
        module_file = getattr(module, "__file__", None)
        if not isinstance(module_file, str):
            return None
        path = str(Path(module_file).parent / resource)
        data: bytes = loader.get_data(path)
        return data

    def distribution_for_node(self, node_id: str, distribution: str) -> _DistributionView:
        from packaging.utils import canonicalize_name

        normalized = canonicalize_name(distribution)
        node = self._nodes[node_id]
        matches = [
            candidate for candidate in [node, *self._direct_dependencies(node)] if candidate.distribution == normalized
        ]
        if len(matches) != 1:
            raise AmbiguousMetadataError(
                "Distribution query is absent or ambiguous in this realm",
                module=distribution,
                referrer=node_id,
                realm=node_id,
                candidates=tuple(f"{item.distribution}=={item.version}" for item in matches),
            )
        return _DistributionView(self._artifacts[matches[0].artifact])

    def reload(self, module: ModuleType) -> ModuleType:
        canonical = module.__name__
        if canonical not in self._locations:
            return importlib.reload(module)
        node, logical_name, location = self._locations[canonical]
        spec = module.__spec__
        loader = spec.loader if spec else None
        if not isinstance(loader, DepfixSourceLoader):
            raise ImportError(f"cannot reload namespace module {canonical}")
        preserved = {key: module.__dict__[key] for key in list(module.__dict__) if key.startswith("__depfix_")}
        module.__dict__.clear()
        module.__dict__.update(
            {
                "__name__": canonical,
                "__loader__": loader,
                "__package__": spec.parent if spec else canonical.rpartition(".")[0],
                "__spec__": spec,
                "__file__": str(location.source),
                **preserved,
            }
        )
        if location.is_package:
            module.__path__ = [str(location.package_dir)]
        self._exec_source(module, node, logical_name, location)
        return module


def _copy_module(source: ModuleType) -> ModuleType:
    target = ModuleType(source.__name__)
    target.__dict__.update(source.__dict__)
    return target
