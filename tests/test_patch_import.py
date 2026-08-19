from __future__ import annotations

import importlib
import importlib.abc
import importlib.util
import os
import stat
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from conftest import file_spec

import depfix
from depfix import cache as cache_module
from depfix.cache import Cache
from depfix.dispatcher import dispatcher_installed
from depfix.errors import StoreImportError
from depfix.manager import reset_runtime_state
from depfix.manifest import load_manifest
from depfix.project import install_packages
from depfix.settings import reset_configuration


@pytest.fixture(autouse=True)
def _clean_import_state():
    reset_configuration()
    reset_runtime_state()
    yield
    reset_configuration()
    reset_runtime_state()


def _install(tmp_path: Path, *wheels: Path) -> None:
    for wheel in wheels:
        install_packages((file_spec(wheel),), cache_dir=tmp_path / "cache")
    depfix.configure(cache_dir=tmp_path / "cache", log_level="WARNING")


def test_patch_import_uses_recorded_module_names_and_is_explicit(tmp_path: Path, wheel_factory) -> None:
    wheel = wheel_factory(
        "distribution-name-does-not-match",
        "1.0.0",
        {
            "actual_import.py": "VALUE = 42\n",
            "second_import.py": "VALUE = 7\n",
        },
    )
    _install(tmp_path, wheel)

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import depfix; depfix.patch_import(); import actual_import; print(actual_import.VALUE)",
        ],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "DEPFIX_CACHE_DIR": str(tmp_path / "cache"), "DEPFIX_LOG_LEVEL": "WARNING"},
    )
    assert completed.stdout.strip() == "42"

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("actual_import")
    assert not dispatcher_installed()

    depfix.patch_import()
    assert importlib.import_module("actual_import").VALUE == 42
    assert importlib.import_module("second_import").VALUE == 7


@pytest.mark.parametrize("entrypoint", ["script", "module"])
def test_depfix_run_enables_the_installed_store_fallback_without_application_setup(
    tmp_path: Path, wheel_factory, entrypoint: str
) -> None:
    wheel = wheel_factory("runner-fallback", "1.0.0", {"runner_fallback.py": "VALUE = 'runner-ok'\n"})
    _install(tmp_path, wheel)
    application = tmp_path / "runner_application.py"
    application.write_text("import runner_fallback\nprint(runner_fallback.VALUE)\n", encoding="utf-8")
    command = [sys.executable, "-m", "depfix", "--cache-dir", str(tmp_path / "cache"), "run"]
    command.extend([str(application)] if entrypoint == "script" else ["-m", "runner_application"])

    payloads = [
        path
        for path in (tmp_path / "cache" / "v1" / "targets").rglob("*")
        if path.is_file() and path.name != ".complete"
    ]
    if os.name != "nt":
        for path in payloads:
            path.chmod(0o444)

    completed = subprocess.run(command, cwd=tmp_path, check=True, capture_output=True, text=True)

    assert completed.stdout.strip() == "runner-ok"
    if os.name != "nt":
        assert payloads and {stat.S_IMODE(path.stat().st_mode) for path in payloads} == {0o555}


def test_patch_import_preserves_ordinary_precedence_and_internal_failures(
    tmp_path: Path, wheel_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    stored = wheel_factory("precedence-demo", "2.0.0", {"precedence_demo.py": "VALUE = 'store'\n"})
    broken = wheel_factory("broken-demo", "1.0.0", {"broken_demo.py": "import absent_inside_package\n"})
    _install(tmp_path, stored, broken)
    local = tmp_path / "project"
    local.mkdir()
    (local / "precedence_demo.py").write_text("VALUE = 'project'\n", encoding="utf-8")
    monkeypatch.syspath_prepend(local)

    depfix.patch_import()
    assert importlib.import_module("precedence_demo").VALUE == "project"
    with pytest.raises(ModuleNotFoundError) as captured:
        importlib.import_module("broken_demo")
    assert captured.value.name == "absent_inside_package"


def test_patch_import_preserves_stdlib_and_third_party_finder_precedence(tmp_path: Path, wheel_factory) -> None:
    wheel = wheel_factory(
        "shadowed-modules",
        "1.0.0",
        {"fractions.py": "ORIGIN = 'store'\n", "hook_wins.py": "ORIGIN = 'store'\n"},
    )
    _install(tmp_path, wheel)

    class HookLoader(importlib.abc.Loader):
        def create_module(self, spec):  # type: ignore[no-untyped-def]
            return None

        def exec_module(self, module):  # type: ignore[no-untyped-def]
            module.ORIGIN = "hook"

    class HookFinder(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):  # type: ignore[no-untyped-def]
            if fullname == "hook_wins":
                return importlib.util.spec_from_loader(fullname, HookLoader())
            return None

    finder = HookFinder()
    sys.meta_path.insert(0, finder)
    try:
        depfix.patch_import()
        assert importlib.import_module("fractions").__name__ == "fractions"
        assert importlib.import_module("hook_wins").ORIGIN == "hook"
    finally:
        sys.meta_path[:] = [item for item in sys.meta_path if item is not finder]
        sys.modules.pop("hook_wins", None)


def test_patch_import_preserves_an_ordinary_loader_module_not_found_error(
    tmp_path: Path, wheel_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    wheel = wheel_factory("ordinary-probe", "1.0.0", {"ordinary_probe.py": "ORIGIN = 'store'\n"})
    _install(tmp_path, wheel)

    class FailingLoader(importlib.abc.Loader):
        def exec_module(self, module):  # type: ignore[no-untyped-def]
            raise ModuleNotFoundError("ordinary loader failed", name="ordinary_probe")

    class FailingFinder(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):  # type: ignore[no-untyped-def]
            if fullname == "ordinary_probe":
                return importlib.util.spec_from_loader(fullname, FailingLoader())
            return None

    def forbidden(_root: str):
        raise AssertionError("fallback incorrectly consulted")

    finder = FailingFinder()
    sys.meta_path.insert(0, finder)
    monkeypatch.setattr("depfix.manager.prepare_store_import", forbidden)
    try:
        depfix.patch_import()
        with pytest.raises(ModuleNotFoundError, match="ordinary loader failed") as captured:
            importlib.import_module("ordinary_probe")
        assert captured.value.name == "ordinary_probe"
    finally:
        sys.meta_path[:] = [item for item in sys.meta_path if item is not finder]
        sys.modules.pop("ordinary_probe", None)


@pytest.mark.parametrize("entrypoint", ["builtins", "import_module"])
def test_patch_import_calls_a_stateful_ordinary_finder_once(tmp_path: Path, wheel_factory, entrypoint: str) -> None:
    wheel = wheel_factory("oneshot-provider", "1.0.0", {"oneshot_provider.py": "ORIGIN = 'store'\n"})
    _install(tmp_path, wheel)

    class HookLoader(importlib.abc.Loader):
        def exec_module(self, module):  # type: ignore[no-untyped-def]
            module.ORIGIN = "hook"

    class OneShotFinder(importlib.abc.MetaPathFinder):
        calls = 0

        def find_spec(self, fullname, path=None, target=None):  # type: ignore[no-untyped-def]
            if fullname != "oneshot_provider":
                return None
            self.calls += 1
            if self.calls == 1:
                return importlib.util.spec_from_loader(fullname, HookLoader())
            return None

    finder = OneShotFinder()
    sys.meta_path.insert(0, finder)
    try:
        depfix.patch_import()
        module = (
            __import__("oneshot_provider") if entrypoint == "builtins" else importlib.import_module("oneshot_provider")
        )
        assert module.ORIGIN == "hook"
        assert finder.calls == 1
    finally:
        sys.meta_path[:] = [item for item in sys.meta_path if item is not finder]
        sys.modules.pop("oneshot_provider", None)


def test_patch_import_selects_newest_installed_version_and_explicit_scope_wins(tmp_path: Path, wheel_factory) -> None:
    old = wheel_factory("fallback-version", "1.0.0", {"fallback_version.py": "VERSION = 'old'\n"})
    new = wheel_factory("fallback-version", "2.0.0", {"fallback_version.py": "VERSION = 'new'\n"})
    _install(tmp_path, old, new)

    depfix.patch_import()
    assert importlib.import_module("fallback_version").VERSION == "new"

    reset_runtime_state()
    depfix.configure(cache_dir=tmp_path / "cache", log_level="WARNING")
    depfix.patch_import()
    with depfix.using(file_spec(old)):
        assert importlib.import_module("fallback_version").VERSION == "old"

    reset_runtime_state()
    depfix.configure(cache_dir=tmp_path / "cache", log_level="WARNING")
    depfix.patch_import()
    depfix.default(file_spec(old))
    assert importlib.import_module("fallback_version").VERSION == "old"


@pytest.mark.skipif(os.name == "nt", reason="legacy POSIX payload modes do not apply on Windows")
def test_patch_import_repairs_requested_closure_when_unrelated_artifact_is_missing(
    tmp_path: Path, wheel_factory
) -> None:
    unrelated = wheel_factory("absent-unrelated", "1.0.0", {"absent_unrelated.py": "VALUE = 1\n"})
    requested = wheel_factory("requested-repair", "1.0.0", {"requested_repair.py": "VALUE = 42\n"})
    result = install_packages((file_spec(unrelated), file_spec(requested)), cache_dir=tmp_path / "cache")
    graph = load_manifest(result.manifest)
    cache = Cache(tmp_path / "cache")
    artifacts = {node.distribution: graph.artifact_index[node.artifact] for node in graph.nodes}
    cache_module._remove_path(cache.unpacked_path(artifacts["absent-unrelated"].id))
    requested_target = cache.unpacked_path(artifacts["requested-repair"].id)
    requested_payload = requested_target / "purelib" / "requested_repair.py"
    requested_payload.chmod(0o444)
    assert not cache.has_package(artifacts["requested-repair"].sha256)

    depfix.configure(cache_dir=tmp_path / "cache", log_level="WARNING")
    depfix.patch_import()

    assert importlib.import_module("requested_repair").VALUE == 42
    assert stat.S_IMODE(requested_payload.stat().st_mode) == 0o555
    assert not cache.unpacked_path(artifacts["absent-unrelated"].id).exists()


@pytest.mark.parametrize("entrypoint", ["api", "run"])
def test_explicit_manifest_wins_over_newer_installed_fallback(tmp_path: Path, wheel_factory, entrypoint: str) -> None:
    old = wheel_factory("manifest-priority", "1.0.0", {"manifest_priority.py": "VERSION = 'old'\n"})
    old_install = install_packages((file_spec(old),), cache_dir=tmp_path / "cache")
    new = wheel_factory("manifest-priority", "2.0.0", {"manifest_priority.py": "VERSION = 'new'\n"})
    install_packages((file_spec(new),), cache_dir=tmp_path / "cache")

    if entrypoint == "api":
        depfix.configure(
            manifest=old_install.manifest,
            frozen=True,
            offline=True,
            cache_dir=tmp_path / "cache",
            log_level="WARNING",
        )
        depfix.patch_import()
        assert importlib.import_module("manifest_priority").VERSION == "old"
        return

    application = tmp_path / "manifest_application.py"
    application.write_text("import manifest_priority\nprint(manifest_priority.VERSION)\n", encoding="utf-8")
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "depfix",
            "--cache-dir",
            str(tmp_path / "cache"),
            "run",
            "--manifest",
            str(old_install.manifest),
            "--frozen",
            "--offline",
            str(application),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert completed.stdout.strip() == "old"


def test_patch_import_supports_namespaces_submodules_reload_and_concurrency(tmp_path: Path, wheel_factory) -> None:
    wheel = wheel_factory(
        "fallback-namespace",
        "1.0.0",
        {
            "fallback_ns/plugin.py": "VALUE = 11\n",
            "threaded_fallback/__init__.py": "from .child import VALUE\n",
            "threaded_fallback/child.py": "VALUE = 13\n",
        },
    )
    _install(tmp_path, wheel)
    depfix.patch_import()

    from fallback_ns import plugin

    assert plugin.VALUE == 11
    module = importlib.import_module("threaded_fallback.child")
    assert module.VALUE == 13
    assert importlib.reload(module).VALUE == 13
    with ThreadPoolExecutor(max_workers=8) as executor:
        values = tuple(executor.map(lambda _item: importlib.import_module("threaded_fallback").VALUE, range(24)))
    assert values == (13,) * 24


def test_patch_import_merges_namespace_contributors_from_one_exact_graph(tmp_path: Path, wheel_factory) -> None:
    first = wheel_factory(
        "fallback-namespace-one",
        "1.0.0",
        {"joined_ns/one.py": "import joined_ns.two\nVALUE = joined_ns.two.VALUE\n"},
    )
    second = wheel_factory("fallback-namespace-two", "1.0.0", {"joined_ns/two.py": "VALUE = 'two'\n"})
    install_packages((file_spec(first), file_spec(second)), cache_dir=tmp_path / "cache")
    depfix.configure(cache_dir=tmp_path / "cache", log_level="WARNING")

    depfix.patch_import()
    namespace = importlib.import_module("joined_ns")
    from joined_ns import one, two

    assert (one.VALUE, two.VALUE) == ("two", "two")
    assert len(namespace.__path__) == 2


def test_patch_import_preserves_circular_imports_and_native_auto_mode(tmp_path: Path, wheel_factory) -> None:
    extension = importlib.util.find_spec("_testcapi")
    if extension is None or extension.origin is None or not Path(extension.origin).is_file():
        pytest.skip("this interpreter does not provide the _testcapi extension")
    extension_path = Path(extension.origin)
    circular = wheel_factory(
        "fallback-cycle",
        "1.0.0",
        {
            "fallback_cycle/__init__.py": "from . import child\nVALUE = child.VALUE\n",
            "fallback_cycle/child.py": "import fallback_cycle\nVALUE = 17\n",
        },
    )
    native = wheel_factory(
        "fallback-native",
        "1.0.0",
        {
            "fallback_native/__init__.py": "from . import _testcapi\nVALUE = _testcapi.INT_MAX\n",
            f"fallback_native/{extension_path.name}": extension_path.read_bytes(),
        },
    )
    _install(tmp_path, circular, native)
    depfix.patch_import()

    assert importlib.import_module("fallback_cycle").VALUE == 17
    loaded_native = importlib.import_module("fallback_native")
    assert loaded_native.VALUE > 0
    assert callable(loaded_native._testcapi.parse_tuple_and_keywords)
    assert Path(loaded_native._testcapi.__file__).is_file()
    assert loaded_native.__name__ == "fallback_native"


def test_patch_import_rejects_ambiguous_same_version_artifacts(tmp_path: Path, wheel_factory) -> None:
    first = wheel_factory("ambiguous-fallback", "1.0.0", {"ambiguous_fallback.py": "VALUE = 1\n"})
    _install(tmp_path, first)
    second = wheel_factory("ambiguous-fallback", "1.0.0", {"ambiguous_fallback.py": "VALUE = 2\n"})
    _install(tmp_path, second)
    depfix.patch_import()

    with pytest.raises(StoreImportError, match="several incompatible dependency graphs or artifacts"):
        importlib.import_module("ambiguous_fallback")


def test_patch_and_unpatch_are_idempotent_and_preserve_unrelated_hooks(tmp_path: Path, wheel_factory) -> None:
    wheel = wheel_factory("unpatch-demo", "1.0.0", {"unpatch_one.py": "VALUE = 1\n", "unpatch_two.py": "VALUE = 2\n"})
    _install(tmp_path, wheel)

    class UnrelatedFinder:
        def find_spec(self, fullname, path=None, target=None):  # type: ignore[no-untyped-def]
            return None

    unrelated = UnrelatedFinder()
    sys.meta_path.append(unrelated)
    try:
        depfix.patch_import()
        depfix.patch_import()
        assert importlib.import_module("unpatch_one").VALUE == 1
        depfix.unpatch_import()
        depfix.unpatch_import()
        assert unrelated in sys.meta_path
        assert not dispatcher_installed()
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("unpatch_two")
    finally:
        sys.meta_path[:] = [finder for finder in sys.meta_path if finder is not unrelated]


def test_unknown_import_does_not_resolve_or_install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    depfix.configure(cache_dir=tmp_path / "cache", log_level="WARNING")

    def forbidden(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("patch_import must not invoke the resolver")

    monkeypatch.setattr("depfix.resolver.Resolver.resolve", forbidden)
    depfix.patch_import()
    with pytest.raises(ModuleNotFoundError) as captured:
        importlib.import_module("definitely_not_installed_by_depfix")
    assert captured.value.name == "definitely_not_installed_by_depfix"
