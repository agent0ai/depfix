from __future__ import annotations

import multiprocessing
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from conftest import file_spec

import depfix
from depfix.cache import Cache
from depfix.config import ImportDeclaration, ProjectConfig
from depfix.errors import UndeclaredImportError
from depfix.manager import reset_runtime_state
from depfix.manifest import write
from depfix.resolver import Resolver
from depfix.runtime import DepfixRuntime
from depfix.settings import reset_configuration
from depfix.sync import sync_graph


def _spawn_load(lock_path: str, cache_root: str, queue: multiprocessing.Queue) -> None:
    runtime = depfix.activate(lock_path, cache_dir=cache_root)
    module = runtime.load_alias("spawned")
    queue.put((module.VALUE, module.__depfix_graph_id__, module.__name__))


def test_direct_single_file_api_is_integrity_pinned_and_isolated(tmp_path: Path) -> None:
    source = tmp_path / "remote_math.py"
    source.write_text("import math\nanswer = math.factorial(5)\n", encoding="utf-8")
    specifier = file_spec(source, kind="py")
    depfix.configure(cache_dir=tmp_path / "cache")
    module = depfix.import_module(specifier, module="remote_math")
    assert module.answer == 120
    assert depfix.import_module(specifier, module="remote_math") is module
    assert "remote_math" not in sys.modules

    neighbor = tmp_path / "neighbor.py"
    neighbor.write_text("VALUE = 'ambient-neighbor'\n", encoding="utf-8")
    isolated = tmp_path / "isolated.py"
    isolated.write_text("import neighbor\n", encoding="utf-8")
    try:
        depfix.configure(cache_dir=tmp_path / "cache2")
        with pytest.raises(UndeclaredImportError):
            depfix.import_module(file_spec(isolated, kind="py"), module="isolated", refresh=True)
    finally:
        reset_configuration()


@pytest.mark.live
@pytest.mark.skipif(
    os.environ.get("DEPFIX_RUN_LIVE_TESTS") != "1",
    reason="set DEPFIX_RUN_LIVE_TESTS=1 to exercise published PyPI artifacts",
)
def test_openai_0_7_and_0_28_load_side_by_side(tmp_path: Path) -> None:
    depfix.configure(cache_dir=tmp_path / "cache")
    try:
        openai_0_7 = depfix.import_module("openai==0.7.0")
        openai_0_28 = depfix.import_module("openai==0.28.1")

        assert openai_0_7.__depfix_version__ == "0.7.0"
        assert openai_0_28.__depfix_version__ == "0.28.1"
        assert openai_0_7 is not openai_0_28
        assert openai_0_7.__name__ != openai_0_28.__name__
        assert hasattr(openai_0_7, "Completion")
        assert not hasattr(openai_0_7, "ChatCompletion")
        assert hasattr(openai_0_28, "ChatCompletion")
        assert "openai" not in sys.modules
    finally:
        reset_runtime_state()
        reset_configuration()


@pytest.mark.live
@pytest.mark.skipif(
    os.environ.get("DEPFIX_RUN_LIVE_TESTS") != "1",
    reason="set DEPFIX_RUN_LIVE_TESTS=1 to exercise published PyPI artifacts",
)
def test_setuptools_75_imports_both_public_modules_explicitly_and_by_default(tmp_path: Path) -> None:
    depfix.configure(cache_dir=tmp_path / "cache", log_level="WARNING")
    try:
        setuptools = depfix.import_module("setuptools==75.0.0", module="setuptools")
        with pytest.warns(DeprecationWarning, match="pkg_resources is deprecated"):
            pkg_resources = depfix.import_module("setuptools==75.0.0", module="pkg_resources")

        assert setuptools.__depfix_version__ == "75.0.0"
        assert pkg_resources.__depfix_version__ == "75.0.0"
        assert setuptools is not pkg_resources

        depfix.default("setuptools==75.0.0")
        import setuptools as default_setuptools

        with pytest.warns(DeprecationWarning, match="pkg_resources is deprecated"):
            import pkg_resources as default_pkg_resources

        assert default_setuptools.__depfix_version__ == "75.0.0"
        assert default_pkg_resources.__depfix_version__ == "75.0.0"
        assert default_setuptools is not default_pkg_resources
    finally:
        reset_runtime_state()
        reset_configuration()


def test_reload_failure_cleanup_and_thread_identity(tmp_path: Path, wheel_factory) -> None:
    good = wheel_factory("reload-demo", "1.0.0", {"reloadable/__init__.py": "VALUE = 1\n"})
    bad = wheel_factory("failure-demo", "1.0.0", {"failure/__init__.py": "raise RuntimeError('boom')\n"})
    config = ProjectConfig(
        tmp_path / ".depfix" / "config.toml",
        (
            ImportDeclaration("reloadable", file_spec(good), "reloadable"),
            ImportDeclaration("failure", file_spec(bad), "failure"),
        ),
        {},
    )
    cache = Cache(tmp_path / "cache")
    graph = Resolver(cache).resolve(config)
    sync_graph(graph, cache, offline=True)
    runtime = DepfixRuntime(graph, cache).activate()
    with ThreadPoolExecutor(max_workers=12) as pool:
        modules = list(pool.map(lambda _: runtime.load_alias("reloadable"), range(100)))
    assert len({id(module) for module in modules}) == 1
    module = modules[0]
    module.VALUE = 99
    assert runtime.reload(module).VALUE == 1

    failed_alias = graph.alias_index["failure"]
    canonical = runtime.canonical_name(failed_alias.node, "failure")
    with pytest.raises(RuntimeError, match="boom"):
        runtime.load_alias("failure")
    assert canonical not in sys.modules


def test_cli_frozen_offline_run_and_doctor(tmp_path: Path, wheel_factory) -> None:
    wheel = wheel_factory("cli-demo", "1.0.0", {"clidemo/__init__.py": "VALUE = 'cli-ok'\n"})
    application = tmp_path / "application.py"
    application.write_text(
        "from depfix import import_module\n"
        f'demo = import_module("{file_spec(wheel)}", module="clidemo")\n'
        "print(demo.VALUE)\n",
        encoding="utf-8",
    )
    cache_root = tmp_path / "cache"
    manifest = tmp_path / ".depfix" / "imports.lock"
    base = [sys.executable, "-m", "depfix", "--cache-dir", str(cache_root)]
    exported = subprocess.run(
        [*base, "export", str(tmp_path), "--output", str(manifest)], text=True, capture_output=True
    )
    assert exported.returncode == 0, exported.stderr
    installed = subprocess.run(
        [*base, "install", str(manifest), "--frozen", "--offline", "--no-build"], text=True, capture_output=True
    )
    assert installed.returncode == 0, installed.stderr
    environment = dict(os.environ)
    environment.update({"DEPFIX_CACHE_DIR": str(cache_root), "DEPFIX_FROZEN": "1", "DEPFIX_OFFLINE": "1"})
    run = subprocess.run(
        [sys.executable, application.name], cwd=tmp_path, env=environment, text=True, capture_output=True
    )
    assert run.returncode == 0, run.stderr
    assert run.stdout.strip() == "cli-ok"
    doctor = subprocess.run([*base, "doctor"], cwd=tmp_path, text=True, capture_output=True)
    assert doctor.returncode == 0, doctor.stdout + doctor.stderr


def test_spawn_initializer_restores_locked_graph(tmp_path: Path, wheel_factory) -> None:
    wheel = wheel_factory("spawn-demo", "1.0.0", {"spawned/__init__.py": "VALUE = 'spawn-ok'\n"})
    config = ProjectConfig(
        tmp_path / ".depfix" / "config.toml",
        (ImportDeclaration("spawned", file_spec(wheel), "spawned"),),
        {},
    )
    cache_root = tmp_path / "cache"
    cache = Cache(cache_root)
    graph = Resolver(cache).resolve(config)
    lock_path = tmp_path / ".depfix" / "imports.lock"
    write(graph, lock_path)
    sync_graph(graph, cache, offline=True)
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    process = context.Process(target=_spawn_load, args=(str(lock_path), str(cache_root), queue))
    process.start()
    process.join(15)
    assert process.exitcode == 0
    value, graph_id, synthetic_name = queue.get(timeout=2)
    assert value == "spawn-ok" and graph_id == graph.graph_id
    assert synthetic_name.startswith("_depfix.g_")


def test_static_analyzers_distinguish_generated_version_apis(tmp_path: Path, wheel_factory) -> None:
    from test_resolver_runtime import _project

    _graph, _lock, _cache = _project(tmp_path, wheel_factory)
    source = tmp_path / "types_demo.py"
    source.write_text(
        "from depfix_imports import example_v1, example_v2\n"
        "old: str = example_v1.old_api()\n"
        "new: int = example_v2.new_api()\n"
        "example_v1.new_api()\n"
        "example_v2.old_api()\n",
        encoding="utf-8",
    )
    environment = dict(os.environ)
    environment["MYPYPATH"] = str(tmp_path / ".depfix" / "generated")
    result = subprocess.run(
        [sys.executable, "-m", "mypy", str(source)], env=environment, text=True, capture_output=True
    )
    assert result.returncode == 1
    assert 'Module has no attribute "new_api"' in result.stdout
    assert 'Module has no attribute "old_api"' in result.stdout

    config = tmp_path / "pyrightconfig.json"
    config.write_text(
        "{\n"
        f'  "include": ["{source.name}"],\n'
        '  "typeCheckingMode": "strict",\n'
        '  "extraPaths": [".depfix/generated"],\n'
        '  "stubPath": ".depfix/generated"\n'
        "}\n",
        encoding="utf-8",
    )
    pyright = subprocess.run(
        [sys.executable, "-m", "pyright", "-p", str(config)],
        cwd=tmp_path,
        text=True,
        capture_output=True,
    )
    assert pyright.returncode == 1
    assert '"new_api" is not a known attribute' in pyright.stdout
    assert '"old_api" is not a known attribute' in pyright.stdout
