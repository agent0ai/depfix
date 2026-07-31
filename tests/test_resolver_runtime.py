from __future__ import annotations

import importlib
import importlib.resources
import sys
from pathlib import Path

import pytest
from conftest import build_index, file_spec

import depfix
from depfix.aliases import generate_aliases
from depfix.cache import Cache
from depfix.config import ImportDeclaration, ProjectConfig, load_config
from depfix.errors import NativeIsolationRequired, UndeclaredImportError
from depfix.manifest import write
from depfix.resolver import Resolver
from depfix.runtime import DepfixRuntime
from depfix.sync import sync_graph


def _project(tmp_path: Path, wheel_factory):
    shared1 = wheel_factory(
        "shared-lib",
        "1.5.0",
        {"shared/__init__.py": "__version__ = '1.5.0'\nAPI = 'old'\n"},
    )
    shared2 = wheel_factory(
        "shared-lib",
        "2.5.0",
        {"shared/__init__.py": "__version__ = '2.5.0'\nAPI = 'new'\n"},
    )
    appa = wheel_factory(
        "application-a",
        "1.0.0",
        {
            "appa/__init__.py": (
                "import shared\n"
                "from .helper import local_value\n"
                "import importlib\n"
                "dynamic_shared = importlib.import_module('shared')\n"
                "from importlib import metadata\n"
                "metadata_version = metadata.version('shared-lib')\n"
                "import importlib.resources\n"
                "resource_text = importlib.resources.files(__package__).joinpath('data.txt').read_text()\n"
                "import pkgutil\n"
                "pkg_data = pkgutil.get_data(__package__, 'data.txt').decode()\n"
                "__version__ = '1.0.0'\n"
            ),
            "appa/helper.py": "local_value = 'relative-ok'\n",
            "appa/data.txt": "resource-a",
        },
        requires=["shared-lib<2"],
    )
    appb = wheel_factory(
        "application-b",
        "1.0.0",
        {"appb/__init__.py": "import shared as dependency\n__version__ = '1.0.0'\n"},
        requires=["shared-lib>=2"],
    )
    example1 = wheel_factory(
        "example-one",
        "1.0.0",
        {
            "example/__init__.py": "from .api import *\n__version__ = '1.0.0'\n",
            "example/api.py": "__all__ = ['old_api']\ndef old_api() -> str: return 'old'\n",
            "example/__init__.pyi": "__version__: str\ndef old_api() -> str: ...\n",
        },
    )
    example2 = wheel_factory(
        "example-two",
        "2.0.0",
        {
            "example/__init__.py": "from .api import *\n__version__ = '2.0.0'\n",
            "example/api.py": "__all__ = ['new_api']\ndef new_api() -> int: return 2\n",
            "example/__init__.pyi": "__version__: str\ndef new_api() -> int: ...\n",
        },
    )
    circular = wheel_factory(
        "circular-demo",
        "1.0.0",
        {
            "circ/__init__.py": "from . import a\nready = True\n",
            "circ/a.py": "from . import b\nvalue = 'a'\n",
            "circ/b.py": "from . import a\nvalue = 'b'\n",
        },
    )
    ns_one1 = wheel_factory("ns-one", "1.0.0", {"acme/one.py": "VALUE = 'one-v1'\n"})
    ns_one2 = wheel_factory("ns-one", "2.0.0", {"acme/one.py": "VALUE = 'one-v2'\n"})
    ns_two = wheel_factory("ns-two", "1.0.0", {"acme/two.py": "VALUE = 'two'\n"})
    ns_consumer1 = wheel_factory(
        "ns-consumer-one",
        "1.0.0",
        {
            "consume1/__init__.py": (
                "import acme\nfrom acme import one, two\nVALUES = (one.VALUE, two.VALUE)\nNAMESPACE = acme\n"
            )
        },
        requires=["ns-one<2", "ns-two"],
    )
    ns_consumer2 = wheel_factory(
        "ns-consumer-two",
        "1.0.0",
        {"consume2/__init__.py": "import acme\nfrom acme import one\nVALUE = one.VALUE\nNAMESPACE = acme\n"},
        requires=["ns-one>=2"],
    )
    native = wheel_factory(
        "native-demo",
        "1.0.0",
        {"nativepkg/__init__.py": "ok = True\n", "nativepkg/accelerator.so": b"not really native"},
    )
    index = build_index(tmp_path / "index", [shared1, shared2, ns_one1, ns_one2, ns_two])
    declarations = {
        "appa": (appa, "appa"),
        "appb": (appb, "appb"),
        "example_v1": (example1, "example"),
        "example_v2": (example2, "example"),
        "circular": (circular, "circ"),
        "namespaces_v1": (ns_consumer1, "consume1"),
        "namespaces_v2": (ns_consumer2, "consume2"),
        "native": (native, "nativepkg"),
    }
    config = tmp_path / ".depfix" / "config.toml"
    config.parent.mkdir(parents=True, exist_ok=True)
    text = ["[policy]", 'resolution = "isolated"', "strict-imports = true", ""]
    for alias, (wheel, module) in declarations.items():
        text.extend([f"[imports.{alias}]", f'specifier = "{file_spec(wheel)}"', f'module = "{module}"', ""])
    config.write_text("\n".join(text), encoding="utf-8")
    cache = Cache(tmp_path / "cache")
    graph = Resolver(cache, index_url=index).resolve(load_config(config))
    lock_path = tmp_path / ".depfix" / "imports.lock"
    write(graph, lock_path)
    sync_graph(graph, cache, offline=True)
    generate_aliases(graph, cache, tmp_path / ".depfix" / "generated")
    return graph, lock_path, cache


def test_multiversion_realms_import_semantics_and_aliases(tmp_path: Path, wheel_factory) -> None:
    graph, lock_path, cache = _project(tmp_path, wheel_factory)
    runtime = DepfixRuntime(graph, cache, lockfile=lock_path).activate()

    v1 = runtime.load_alias("example_v1")
    v2 = runtime.load_alias("example_v2")
    assert v1 is not v2
    assert v1.__version__ == "1.0.0" and v1.old_api() == "old"
    assert v2.__version__ == "2.0.0" and v2.new_api() == 2
    assert v1.__name__.startswith("_depfix.g_") and v2.__name__.startswith("_depfix.g_")
    assert v1.__name__ != v2.__name__
    assert Path(v1.__file__).is_file() and v1.__spec__.origin == v1.__file__
    assert runtime.load_alias("example_v1") is v1

    appa = runtime.load_alias("appa")
    appb = runtime.load_alias("appb")
    assert appa.shared.__version__ == "1.5.0"
    assert appb.dependency.__version__ == "2.5.0"
    assert appa.shared is appa.dynamic_shared
    assert appa.metadata_version == "1.5.0"
    assert appa.resource_text == "resource-a" and appa.pkg_data == "resource-a"
    assert appa.local_value == "relative-ok"
    assert importlib.resources.files(appa).joinpath("data.txt").read_text() == "resource-a"

    circular = runtime.load_alias("circular")
    assert circular.a.value == "a" and circular.a.b.value == "b"
    assert circular.a.b.a is circular.a

    before_path = list(sys.path)
    assert not any(
        cache.unpacked_path(artifact.id).as_posix() in entry for artifact in graph.artifacts for entry in sys.path
    )
    assert "example" not in sys.modules and "shared" not in sys.modules
    assert sys.path == before_path

    depfix.activate(lock_path, cache_dir=tmp_path / "cache")
    from depfix_imports import example_v1, example_v2

    assert example_v1 is v1 and example_v2 is v2
    direct = importlib.import_module("depfix_imports.example_v1")
    assert direct is v1


def test_namespace_provider_sets_are_realm_scoped(tmp_path: Path, wheel_factory) -> None:
    graph, lock_path, cache = _project(tmp_path, wheel_factory)
    runtime = DepfixRuntime(graph, cache, lockfile=lock_path).activate()
    first = runtime.load_alias("namespaces_v1")
    second = runtime.load_alias("namespaces_v2")
    assert first.VALUES == ("one-v1", "two")
    assert second.VALUE == "one-v2"
    assert first.NAMESPACE is not second.NAMESPACE
    assert len(first.NAMESPACE.__path__) == 2
    assert len(second.NAMESPACE.__path__) == 1


def test_unknown_native_is_rejected_and_ambient_imports_do_not_leak(tmp_path: Path, wheel_factory) -> None:
    graph, lock_path, cache = _project(tmp_path, wheel_factory)
    runtime = DepfixRuntime(graph, cache, lockfile=lock_path).activate()
    assert graph.alias_index["native"].node in graph.node_index
    assert graph.node_index[graph.alias_index["native"].node].native_classification == "native-unknown"
    native = runtime.load_alias("native")
    assert native.ok is True
    with pytest.raises(NativeIsolationRequired):
        runtime.import_for_node(native.__depfix_node_id__, "nativepkg.accelerator")
    example = runtime.load_alias("example_v1")
    with pytest.raises(UndeclaredImportError):
        runtime.import_for_node(example.__depfix_node_id__, "pytest")


def test_dynamic_compatibility_submodules_and_missing_probes(tmp_path: Path, wheel_factory) -> None:
    provider = wheel_factory(
        "compat-provider",
        "1.0.0",
        {
            "compat_provider/__init__.py": (
                "import json\n"
                "import sys\n"
                "import types\n"
                "moves = types.ModuleType(__name__ + '.moves')\n"
                "moves.json = json\n"
                "sys.modules[moves.__name__] = moves\n"
                "synthetic_round_trip = __import__(moves.__name__, fromlist=('*',)) is moves\n"
            ),
            "compat_provider/broken.py": "import absent_nested_dependency\n",
        },
    )
    consumer = wheel_factory(
        "compat-consumer",
        "1.0.0",
        {
            "compat_consumer.py": (
                "try:\n"
                "    import absent_optional_dependency\n"
                "except ModuleNotFoundError:\n"
                "    optional_missing = True\n"
                "import compat_provider\n"
                "try:\n"
                "    from compat_provider import broken\n"
                "except ModuleNotFoundError:\n"
                "    nested_missing = True\n"
                "from compat_provider.moves import json\n"
                "encoded = json.dumps({'realm': 'ok'}, sort_keys=True)\n"
            )
        },
        requires=["compat-provider==1.0.0"],
    )
    index = build_index(tmp_path / "index", [provider])
    config = ProjectConfig(
        tmp_path / ".depfix" / "config.toml",
        (ImportDeclaration("consumer", file_spec(consumer), "compat_consumer"),),
        {},
    )
    cache = Cache(tmp_path / "cache")
    graph = Resolver(cache, index_url=index).resolve(config)
    sync_graph(graph, cache, offline=True)
    module = DepfixRuntime(graph, cache).activate().load_alias("consumer")

    assert module.optional_missing is True
    assert module.nested_missing is True
    assert module.compat_provider.synthetic_round_trip is True
    assert module.encoded == '{"realm": "ok"}'


def test_generated_stubs_keep_version_specific_apis(tmp_path: Path, wheel_factory) -> None:
    _graph, _lock_path, _cache = _project(tmp_path, wheel_factory)
    generated = tmp_path / ".depfix" / "generated" / "depfix_imports"
    first = (generated / "example_v1" / "__init__.pyi").read_text()
    second = (generated / "example_v2" / "__init__.pyi").read_text()
    assert "old_api" in first and "new_api" not in first
    assert "new_api" in second and "old_api" not in second
    assert (generated / "aliases.json").is_file()
