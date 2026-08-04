from __future__ import annotations

import asyncio
import builtins
import importlib
import json
import os
import runpy
import subprocess
import sys
import tomllib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import ModuleType

import pytest
from conftest import build_index, file_spec
from jsonschema import Draft202012Validator

import depfix
from depfix.dispatcher import dispatcher_installed
from depfix.errors import (
    DefaultImportConflictError,
    FrozenManifestError,
    ImportDispatcherConflictError,
    ScopeModuleNotProvidedError,
    SharedImportConflictError,
)
from depfix.manager import reset_runtime_state
from depfix.manifest import load_manifest
from depfix.project import create_bundle, export_project, install_manifest
from depfix.scanner import scan_project
from depfix.settings import reset_configuration
from depfix.uv_backend import UvBackend


@pytest.fixture(autouse=True)
def _clean_standard_import_state():
    reset_configuration()
    reset_runtime_state()
    yield
    reset_configuration()
    reset_runtime_state()


def _versioned_wheels(tmp_path: Path, wheel_factory):
    old = wheel_factory(
        "standard-old",
        "1.0.0",
        {
            "scope_target/__init__.py": "from .child import VALUE\nVERSION = 'old'\n",
            "scope_target/child.py": "VALUE = 'old-child'\n",
        },
    )
    new = wheel_factory(
        "standard-new",
        "2.0.0",
        {
            "scope_target/__init__.py": "from .child import VALUE\nVERSION = 'new'\n",
            "scope_target/child.py": "VALUE = 'new-child'\n",
        },
    )
    depfix.configure(cache_dir=tmp_path / "cache", log_level="WARNING")
    return old, new


def test_importing_depfix_alone_does_not_install_dispatcher() -> None:
    assert not dispatcher_installed()
    assert "default" in depfix.__all__ and "using" in depfix.__all__
    assert {"RealmBoundaryError", "RealmInfo", "assert_same_realm", "enforce_same_realm", "realm_of"} <= set(
        depfix.__all__
    )
    assert "activate" not in depfix.__all__


def test_fresh_import_has_no_import_hook_or_subprocess_activity() -> None:
    environment = dict(os.environ)
    environment.pop("DEPFIX_MANIFEST", None)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import builtins, subprocess, sys; before = builtins.__import__; "
            "fail = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('subprocess')); "
            "subprocess.run = fail; subprocess.Popen = fail; import depfix; "
            "print(before is builtins.__import__, 'depfix.manager' not in sys.modules)",
        ],
        check=True,
        text=True,
        capture_output=True,
        env=environment,
    )
    assert result.stdout.strip() == "True True"


def test_persistent_default_and_multiple_roots(tmp_path: Path, wheel_factory) -> None:
    first = wheel_factory("default-first", "1.0.0", {"default_first.py": "VALUE = 1\n"})
    second = wheel_factory(
        "default-tools",
        "2.0.0",
        {"default_second.py": "VALUE = 2\n", "default_extra.py": "VALUE = 3\n"},
    )
    depfix.configure(cache_dir=tmp_path / "cache", log_level="WARNING")

    depfix.default(file_spec(first), file_spec(second))
    import default_extra
    import default_first
    import default_second

    assert (default_first.VALUE, default_second.VALUE, default_extra.VALUE) == (1, 2, 3)
    assert "default_first" not in sys.modules


def test_using_reuses_one_compatible_dependency_by_default_and_can_force_newest(
    tmp_path: Path,
    wheel_factory,
) -> None:
    dependency_old = wheel_factory(
        "scope-compatible-dependency",
        "1.0.0",
        {"scope_compatible_dependency.py": "VERSION = 'old'\n"},
    )
    dependency_new = wheel_factory(
        "scope-compatible-dependency",
        "2.0.0",
        {"scope_compatible_dependency.py": "VERSION = 'new'\n"},
    )
    package_a = wheel_factory(
        "scope-compatible-a",
        "1.0.0",
        {"scope_compatible_a.py": "VALUE = 'a'\n"},
        requires=["scope-compatible-dependency>=1,<2"],
    )
    package_b = wheel_factory(
        "scope-compatible-b",
        "1.0.0",
        {
            "scope_compatible_b.py": (
                "import scope_compatible_dependency\nDEPENDENCY_VERSION = scope_compatible_dependency.VERSION\n"
            )
        },
        requires=["scope-compatible-dependency>=1,<3"],
    )
    index = build_index(tmp_path / "index", [dependency_old, dependency_new])

    depfix.configure(cache_dir=tmp_path / "reuse-cache", index_url=index, log_level="WARNING")
    with depfix.using(file_spec(package_a), file_spec(package_b)):
        import scope_compatible_b as reused

    reset_runtime_state()
    reset_configuration()
    depfix.configure(cache_dir=tmp_path / "newest-cache", index_url=index, log_level="WARNING")
    with depfix.using(file_spec(package_a), file_spec(package_b), prefer_newest=True):
        import scope_compatible_b as newest

    assert reused.DEPENDENCY_VERSION == "old"
    assert newest.DEPENDENCY_VERSION == "new"


def test_native_auto_mode_supports_using_as_a_single_version_scope(tmp_path: Path, wheel_factory) -> None:
    first = wheel_factory(
        "shared-default-demo",
        "1.0.0",
        {"shared_default_demo.py": "VALUE = 7\n", "shared_default_accelerator.so": b"native marker"},
    )
    second = wheel_factory(
        "shared-default-demo",
        "2.0.0",
        {"shared_default_demo.py": "VALUE = 8\n", "shared_default_accelerator.so": b"native marker"},
    )
    depfix.configure(cache_dir=tmp_path / "cache", log_level="WARNING")

    with depfix.using(file_spec(first)):
        imported = importlib.import_module("shared_default_demo")
        assert imported.VALUE == 7
        assert imported.__name__ == "shared_default_demo"

    with pytest.raises(ScopeModuleNotProvidedError):
        importlib.import_module("shared_default_demo")

    with depfix.using(file_spec(first)):
        assert importlib.import_module("shared_default_demo") is imported

    with pytest.raises(SharedImportConflictError):
        with depfix.using(file_spec(second)):
            pass

    depfix.default(file_spec(first))
    assert importlib.import_module("shared_default_demo") is imported


def test_shared_defaults_merge_locked_namespace_contributions(tmp_path: Path, wheel_factory) -> None:
    first = wheel_factory(
        "shared-namespace-one",
        "1.0.0",
        {"acme/one.py": "VALUE = 'one'\n", "shared_namespace_one.so": b"native marker"},
    )
    second = wheel_factory(
        "shared-namespace-two",
        "1.0.0",
        {"acme/two.py": "VALUE = 'two'\n", "shared_namespace_two.so": b"native marker"},
    )
    depfix.configure(cache_dir=tmp_path / "cache", log_level="WARNING")

    depfix.default(file_spec(first), file_spec(second))
    from acme import one, two

    assert (one.VALUE, two.VALUE) == ("one", "two")
    assert len(sys.modules["acme"].__path__) == 2


def test_default_is_additive_idempotent_and_rejects_conflicts(tmp_path: Path, wheel_factory) -> None:
    old, new = _versioned_wheels(tmp_path, wheel_factory)
    extra = wheel_factory("additive-extra", "1.0.0", {"additive_extra.py": "VALUE = 4\n"})

    depfix.default(file_spec(old))
    depfix.default(file_spec(old))
    depfix.default(file_spec(extra))
    import additive_extra
    import scope_target

    assert scope_target.VERSION == "old"
    assert additive_extra.VALUE == 4
    with pytest.raises(DefaultImportConflictError):
        depfix.default(file_spec(new))


def test_separate_and_nested_using_scopes_restore_exactly(tmp_path: Path, wheel_factory) -> None:
    old, new = _versioned_wheels(tmp_path, wheel_factory)

    with depfix.using(file_spec(old)):
        import scope_target as outer
        from scope_target import child as outer_child

        with depfix.using(file_spec(new)):
            import scope_target as inner
            from scope_target.child import VALUE as inner_value

        import scope_target as restored

    assert outer.VERSION == "old"
    assert outer_child.VALUE == "old-child"
    assert inner.VERSION == "new"
    assert inner_value == "new-child"
    assert restored is outer
    assert inner is not outer
    assert "scope_target" not in sys.modules


def test_using_accepts_one_consistent_multi_package_group(tmp_path: Path, wheel_factory) -> None:
    first = wheel_factory("scope-first", "1.0.0", {"scope_first.py": "VALUE = 1\n"})
    second = wheel_factory("scope-second", "1.0.0", {"scope_second.py": "VALUE = 2\n"})
    depfix.configure(cache_dir=tmp_path / "cache", log_level="WARNING")

    with depfix.using(file_spec(first), file_spec(second)):
        import scope_first
        import scope_second

    assert scope_first.VALUE + scope_second.VALUE == 3


def test_using_restores_after_exception_and_modules_remain_usable(tmp_path: Path, wheel_factory) -> None:
    old, _new = _versioned_wheels(tmp_path, wheel_factory)

    with pytest.raises(RuntimeError):
        with depfix.using(file_spec(old)):
            import scope_target as retained

            raise RuntimeError("stop")

    assert retained.VERSION == "old"
    assert retained.child.VALUE == "old-child"


def test_using_sync_and_async_decorators_preserve_metadata(tmp_path: Path, wheel_factory) -> None:
    old, new = _versioned_wheels(tmp_path, wheel_factory)

    @depfix.using(file_spec(old))
    def sync_operation() -> ModuleType:
        """sync marker"""
        import scope_target

        return scope_target

    @depfix.using(file_spec(new))
    async def async_operation() -> ModuleType:
        """async marker"""
        await asyncio.sleep(0)
        import scope_target

        return scope_target

    assert sync_operation.__name__ == "sync_operation"
    assert sync_operation.__doc__ == "sync marker"
    sync_module = sync_operation()
    assert sync_module.VERSION == "old"
    assert sync_operation() is sync_module
    assert asyncio.run(async_operation()).VERSION == "new"


def test_async_tasks_and_threads_have_isolated_scopes(tmp_path: Path, wheel_factory) -> None:
    old, new = _versioned_wheels(tmp_path, wheel_factory)

    async def run_tasks() -> tuple[str, str]:
        ready = asyncio.Event()

        async def selected(specifier: str, wait: bool) -> str:
            with depfix.using(specifier):
                if wait:
                    ready.set()
                    await asyncio.sleep(0)
                else:
                    await ready.wait()
                import scope_target

                return scope_target.VERSION

        return tuple(await asyncio.gather(selected(file_spec(old), True), selected(file_spec(new), False)))

    assert asyncio.run(run_tasks()) == ("old", "new")

    def selected(specifier: str) -> str:
        with depfix.using(specifier):
            import scope_target

            return scope_target.VERSION

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(selected, (file_spec(old), file_spec(new))))
    assert results == ("old", "new")


def test_package_dependencies_keep_their_realm_after_scope_exit(tmp_path: Path, wheel_factory) -> None:
    dependency_old = wheel_factory("realm-dependency", "1.0.0", {"realm_dependency.py": "VERSION = 'old'\n"})
    dependency_new = wheel_factory("realm-dependency", "2.0.0", {"realm_dependency.py": "VERSION = 'new'\n"})
    client_old = wheel_factory(
        "realm-client-old",
        "1.0.0",
        {"realm_client.py": "def dependency_version():\n import realm_dependency\n return realm_dependency.VERSION\n"},
        requires=["realm-dependency==1.0.0"],
    )
    client_new = wheel_factory(
        "realm-client-new",
        "1.0.0",
        {"realm_client.py": "def dependency_version():\n import realm_dependency\n return realm_dependency.VERSION\n"},
        requires=["realm-dependency==2.0.0"],
    )
    index = build_index(tmp_path / "index", [dependency_old, dependency_new])
    depfix.configure(cache_dir=tmp_path / "cache", index_url=index, log_level="WARNING")

    with depfix.using(file_spec(client_old)):
        import realm_client as old_client
    with depfix.using(file_spec(client_new)):
        import realm_client as new_client

        assert old_client.dependency_version() == "old"

    assert old_client.dependency_version() == "old"
    assert new_client.dependency_version() == "new"


def test_unmanaged_and_existing_bare_modules_do_not_leak(tmp_path: Path, wheel_factory) -> None:
    old, _new = _versioned_wheels(tmp_path, wheel_factory)
    ambient = ModuleType("scope_target")
    ambient.VERSION = "ambient"  # type: ignore[attr-defined]
    sys.modules["scope_target"] = ambient
    try:
        with depfix.using(file_spec(old)):
            import xml.etree.ElementTree

            import scope_target

            assert scope_target.VERSION == "old"
            assert xml.etree.ElementTree is not None
        assert sys.modules["scope_target"] is ambient
    finally:
        sys.modules.pop("scope_target", None)


def test_importlib_compatibility_uses_active_selection(tmp_path: Path, wheel_factory) -> None:
    old, _new = _versioned_wheels(tmp_path, wheel_factory)

    with depfix.using(file_spec(old)):
        selected = importlib.import_module("scope_target.child")
        spec = importlib.util.find_spec("scope_target")

    assert selected.VALUE == "old-child"
    assert spec is not None and spec.name.startswith("_depfix.")


def test_realm_standard_library_and_metadata_facade_with_dispatcher(tmp_path: Path, wheel_factory) -> None:
    wheel = wheel_factory(
        "realm-stdlib",
        "4.2.0",
        {
            "realm_stdlib.py": "import importlib.metadata\n"
            "import json\n"
            "VALUE = json.loads('\\\"ok\\\"')\n"
            "DIST_VERSION = importlib.metadata.version('realm-stdlib')\n"
        },
    )
    depfix.configure(cache_dir=tmp_path / "cache", log_level="WARNING")

    with depfix.using(file_spec(wheel)):
        import realm_stdlib

    assert realm_stdlib.VALUE == "ok"
    assert realm_stdlib.DIST_VERSION == "4.2.0"


def test_known_managed_import_missing_from_scope_is_typed(tmp_path: Path, wheel_factory) -> None:
    old, _new = _versioned_wheels(tmp_path, wheel_factory)
    other = wheel_factory("only-other", "1.0.0", {"only_other.py": "VALUE = 1\n"})

    with depfix.using(file_spec(old)):
        import scope_target as selected

    assert selected.VERSION == "old"
    with depfix.using(file_spec(other)), pytest.raises(ScopeModuleNotProvidedError):
        import scope_target  # noqa: F401
    with depfix.using(file_spec(other)), pytest.raises(ScopeModuleNotProvidedError) as captured:
        import packaging  # noqa: F401

    message = str(captured.value)
    assert "using-context" in message
    assert "only_other" in message


def test_replacing_dispatcher_after_installation_is_diagnosed(tmp_path: Path, wheel_factory) -> None:
    wheel = wheel_factory("dispatcher-demo", "1.0.0", {"dispatcher_demo.py": "VALUE = 1\n"})
    depfix.configure(cache_dir=tmp_path / "cache", log_level="WARNING")
    depfix.default(file_spec(wheel))
    dispatcher = builtins.__import__

    def replacement(*args, **kwargs):  # type: ignore[no-untyped-def]
        return dispatcher(*args, **kwargs)

    builtins.__import__ = replacement
    try:
        with pytest.raises(ImportDispatcherConflictError):
            depfix.default(file_spec(wheel))
    finally:
        builtins.__import__ = dispatcher


def test_scanner_groups_defaults_contexts_decorators_and_aliases(tmp_path: Path) -> None:
    (tmp_path / "application.py").write_text(
        "import depfix as dependencies\n"
        "from depfix import default as select_defaults\n"
        "from depfix import using as selected\n"
        "BASE = 'requests=='\n"
        "VERSION = '2.31.0'\n"
        "select_defaults(BASE + VERSION, 'PyYAML==6.0.2', allow_unsafe=True, prefer_newest=True)\n"
        "import requests\n"
        "import yaml as configuration\n"
        "with selected('requests==2.32.3', allow_unsafe=False):\n"
        "    import requests as current_requests\n"
        "@dependencies.using('requests==2.31.0')\n"
        "async def operation():\n"
        "    import requests as decorated_requests\n",
        encoding="utf-8",
    )

    result = scan_project(tmp_path)

    assert [group.mode for group in result.groups] == ["default", "using-context", "using-decorator"]
    assert all(site.isolation == "auto" for site in result.requests)
    default_group, context_group, decorator_group = result.groups
    assert dict(default_group.options)["allow_unsafe"] == "true"
    assert dict(default_group.options)["prefer_newest"] == "true"
    assert dict(context_group.options)["allow_unsafe"] == "false"
    assert "allow_unsafe" not in dict(decorator_group.options)
    assert default_group.normalized_specifiers == ("requests==2.31.0", "pyyaml==6.0.2")
    assert ("yaml", "configuration") in default_group.module_aliases
    assert context_group.ordinary_imports == ("requests",)
    assert ("requests", "current_requests") in context_group.module_aliases
    assert decorator_group.enclosing_function == "operation"
    assert ("requests", "decorated_requests") in decorator_group.module_aliases


def test_scanner_reports_dynamic_default_and_using_groups(tmp_path: Path) -> None:
    (tmp_path / "application.py").write_text(
        "import depfix\n"
        "version = read_version()\n"
        "depfix.default(f'requests=={version}')\n"
        "with depfix.using('requests==' + version):\n"
        "    import requests\n",
        encoding="utf-8",
    )

    result = scan_project(tmp_path)

    assert result.groups == ()
    assert len(result.dynamic_requests) == 2
    assert all("dynamic" in item.reason for item in result.dynamic_requests)


def test_exported_groups_drive_frozen_runtime_without_uv(tmp_path: Path, wheel_factory, monkeypatch) -> None:
    persistent = wheel_factory(
        "persistent-selection",
        "1.0.0",
        {"persistent_selection.py": "VALUE: str = 'persistent'\n"},
    )
    old, new = _versioned_wheels(tmp_path, wheel_factory)
    application = tmp_path / "application.py"
    application.write_text(
        "import depfix\n"
        f"depfix.default({file_spec(persistent)!r})\n"
        "import persistent_selection\n"
        f"with depfix.using({file_spec(old)!r}):\n"
        "    import scope_target as old_target\n"
        f"@depfix.using({file_spec(new)!r})\n"
        "def selected():\n"
        "    import scope_target as new_target\n"
        "    return new_target\n",
        encoding="utf-8",
    )
    cache = tmp_path / "cache"
    depfix.configure(cache_dir=cache, log_level="WARNING")
    exported = export_project(tmp_path)
    graph = load_manifest(exported.manifest)
    schema = json.loads(
        (Path(__file__).parents[1] / "schemas" / "depfix-manifest-v1.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator(schema).validate(tomllib.loads(exported.manifest.read_text(encoding="utf-8")))

    groups = {group.mode: group for group in graph.groups}
    assert set(groups) == {"default", "using-context", "using-decorator"}
    assert all(group.resolved_graph_ids for group in graph.groups)
    assert groups["using-context"].ordinary_imports == ("scope_target",)
    assert "old_target" in groups["using-context"].module_aliases
    assert "new_target" in groups["using-decorator"].module_aliases
    assert (exported.ide_path / "depfix_imports" / "old_target" / "__init__.pyi").is_file()
    assert (exported.ide_path / "depfix_imports" / "new_target" / "__init__.pyi").is_file()
    assert (exported.ide_path / "default_imports" / "persistent_selection" / "__init__.pyi").is_file()

    install_manifest(exported.manifest, frozen=True, offline=True, cache_dir=cache)
    reset_runtime_state()
    reset_configuration()
    depfix.configure(
        cache_dir=cache,
        manifest=exported.manifest,
        frozen=True,
        offline=True,
        log_level="WARNING",
    )

    def reject_uv(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("frozen standard imports must not invoke uv")

    monkeypatch.setattr(UvBackend, "run", reject_uv)
    depfix.default(file_spec(persistent))
    import persistent_selection

    with depfix.using(file_spec(old)):
        import scope_target as old_target
    with depfix.using(file_spec(old)):
        import scope_target as old_target_again

    @depfix.using(file_spec(new))
    def load_new() -> ModuleType:
        import scope_target as new_target

        return new_target

    new_target = load_new()

    assert persistent_selection.VALUE == "persistent"
    assert old_target_again is old_target
    assert old_target.VERSION == "old"
    assert new_target.VERSION == "new"

    unlisted = wheel_factory("unlisted-selection", "1.0.0", {"unlisted_selection.py": "VALUE = 1\n"})
    with pytest.raises(FrozenManifestError):
        with depfix.using(file_spec(unlisted)):
            pass
    with pytest.raises(FrozenManifestError):
        depfix.default(file_spec(unlisted))

    bundle = create_bundle(
        exported.manifest,
        tmp_path / "standard-imports.depfixbundle",
        cache_dir=cache,
    )
    bundle_cache = tmp_path / "bundle-cache"
    bundled = install_manifest(bundle.bundle, frozen=True, offline=True, cache_dir=bundle_cache)
    reset_runtime_state()
    reset_configuration()
    depfix.configure(
        cache_dir=bundle_cache,
        manifest=bundled.manifest,
        frozen=True,
        offline=True,
        log_level="WARNING",
    )
    with depfix.using(file_spec(old)):
        import scope_target as bundled_old

    assert bundled_old.VERSION == "old"


def test_frozen_relative_file_default_uses_declaration_base(tmp_path: Path, wheel_factory, monkeypatch) -> None:
    wheel = wheel_factory("relative-default", "1.0.0", {"relative_default.py": "VALUE = 'relative'\n"})
    relative = f"file:{wheel.name}#sha256={file_spec(wheel).rsplit('=', 1)[1]}"
    application = tmp_path / "relative_application.py"
    application.write_text(
        f"import depfix\ndepfix.default({relative!r})\nimport relative_default\nRESULT = relative_default.VALUE\n",
        encoding="utf-8",
    )
    cache = tmp_path / "cache"
    depfix.configure(cache_dir=cache, log_level="WARNING")
    exported = export_project(tmp_path)
    install_manifest(exported.manifest, frozen=True, offline=True, cache_dir=cache)
    reset_runtime_state()
    reset_configuration()
    depfix.configure(
        cache_dir=cache,
        manifest=exported.manifest,
        frozen=True,
        offline=True,
        log_level="WARNING",
    )

    def reject_uv(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("relative frozen defaults must not invoke uv")

    monkeypatch.setattr(UvBackend, "run", reject_uv)
    namespace = runpy.run_path(str(application))
    assert namespace["RESULT"] == "relative"


def test_dynamic_default_is_prepared_by_explicit_include(tmp_path: Path, wheel_factory, monkeypatch) -> None:
    wheel = wheel_factory("dynamic-include", "1.0.0", {"dynamic_include.py": "VALUE = 'included'\n"})
    specifier = file_spec(wheel)
    application = tmp_path / "dynamic_application.py"
    application.write_text(
        "import os\n"
        "import depfix\n"
        "specifier = os.environ['DYNAMIC_DEPFIX_SPEC']\n"
        "depfix.default(specifier)\n"
        "import dynamic_include\n"
        "RESULT = dynamic_include.VALUE\n",
        encoding="utf-8",
    )
    cache = tmp_path / "cache"
    depfix.configure(cache_dir=cache, log_level="WARNING")
    exported = export_project(tmp_path, include=(specifier,))
    graph = load_manifest(exported.manifest)
    assert graph.dynamic_diagnostics
    assert any(alias.source_file == "<explicit>" for alias in graph.aliases)
    install_manifest(exported.manifest, frozen=True, offline=True, cache_dir=cache)
    reset_runtime_state()
    reset_configuration()
    depfix.configure(
        cache_dir=cache,
        manifest=exported.manifest,
        frozen=True,
        offline=True,
        log_level="WARNING",
    )

    def reject_uv(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("explicit dynamic includes must not invoke uv")

    monkeypatch.setattr(UvBackend, "run", reject_uv)
    monkeypatch.setenv("DYNAMIC_DEPFIX_SPEC", specifier)
    namespace = runpy.run_path(str(application))
    assert namespace["RESULT"] == "included"
